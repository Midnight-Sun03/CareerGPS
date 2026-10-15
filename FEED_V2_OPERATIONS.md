# CareerGPS feed-v2 operation

The repository uses the existing database-backed evaluation queue. It does not
embed a scheduler or introduce a broker.

## Queue investigation

Before Block 3, legacy tasks were created after clean-revision activation and by
profile/policy signals. Those legacy write hooks are now retired. Historical
rows remain for audit and explicit reader rollback only; the normal worker does
not process them and `--legacy` refuses new materialization. No cron, service,
or deployment scheduler definition is present in this repository. That absence
does not establish how a production host is configured.

The legacy processor selects a batch and then locks each task. Its state check
prevents two workers from completing the same row, and a process death inside
the outer transaction normally rolls the task back to `PENDING`. It has no
visible lease, no stale-running recovery, no automatic bounded retry, and no
database uniqueness guarantee for concurrent enqueue checks. A caught build
failure becomes permanently `FAILED`. Legacy materialization also marks current
evaluations stale before replacement work is complete, and its evaluations do
not belong to the generation row. Those facts explain how a pending rebuild can
produce an empty feed.

Feed-v2 tasks add a compare-and-swap claim token, expiry lease, stale-claim
recovery, dependency-key duplicate suppression, bounded retry, and diagnostics.
Generation rows own all v2 evaluations. A BUILDING generation is invisible;
only a complete validated generation is atomically marked ACTIVE.

Before enabling the v2 reader, deployment must run this command periodically
(recommended: once per minute) under a single or multiple process-safe workers:

```
.\.venv\Scripts\python.exe manage.py process_evaluation_queue --limit 100 --worker-id <stable-production-worker-name>
```

## Recurring execution on this host

The v2 worker runs on Windows Task Scheduler. Two scripts own it:

* `scripts\run_v2_worker.ps1` performs one bounded pass and appends its result
  to `logs\v2-worker.log`.
* `scripts\install_v2_worker_task.ps1` installs, inspects, disables or removes
  the scheduled task.

Install, inspect and remove:

```
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\install_v2_worker_task.ps1
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\install_v2_worker_task.ps1 -Action Inspect
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\install_v2_worker_task.ps1 -Action Disable
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\install_v2_worker_task.ps1 -Action Remove
```

The task runs every 5 minutes with a batch limit of 25, as the signed-in
user, with `MultipleInstances = IgnoreNew` so a slow pass can never overlap the
next trigger. `-IntervalMinutes` and `-BatchLimit` change the cadence.

`IgnoreNew` only governs instances the scheduler itself launches. `run_v2_worker.ps1`
therefore also takes a machine-wide named mutex, `Global\CareerGPS.v2.EvaluationWorker`,
for the duration of the worker invocation, so a manual drain or any other launcher
cannot start a second worker beside a running one. That matters beyond wasted CPU:
a second worker reclaims a running task whose lease has expired and discards the
first worker's completed build. A pass that finds the mutex held starts nothing,
logs `pass SKIPPED`, and exits **3** (a skip, not a failure). Windows releases an
abandoned mutex when its owning process dies, so a killed worker cannot leave a
stale lock.

A full generation rebuild for a profile with several target roles takes roughly
15-20 minutes, which is far longer than the 5-minute interval. That is safe: the
trigger is skipped while a pass runs, the mutex rejects any other launcher, and
the batch limit keeps a quiet tick short. Do not shorten `ExecutionTimeLimit`
below the longest expected build.

`LogonType` selects the logon mode. The default `Interactive` installs without
elevation but only fires while the user is signed in, so the worker stops
running when the machine is left unattended. For a genuinely unattended host,
install it once from an Administrator prompt:

```
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\install_v2_worker_task.ps1 -LogonType S4U
```

`S4U` runs whether or not anyone is signed in and does not store a password.
Creating it requires elevation, which is why it is not the default.

A recurring pass deliberately does **not** pass `--all-profiles`. That flag asks
for a full rebuild of every profile, which is a backfill action. The write hooks
in `core/services/integrity.py` already enqueue work for target edits, profile
evidence edits, source-version changes and clean-revision activation, so a
recurring pass only drains what those hooks left behind, and a quiet tick costs
one short command that reports `Processed 0`.

Two traps cost real debugging time here and are recorded so they are not
reintroduced. `-NoNewWindow` and `-Wait` are `Start-Process` parameters, not
`powershell.exe` ones. Passing either to `powershell.exe -File` makes the
interpreter fail or hang without ever running the runner, which looks exactly
like a broken task. The action therefore invokes the script directly and does
not detach, so the recorded last result is the worker's real exit status.

If a pass is killed mid-flight, its claimed tasks stay `RUNNING` until
`CAREERGPS_V2_TASK_LEASE_SECONDS` (900s) expires, and the next pass reclaims
them. A task that exhausts `CAREERGPS_V2_TASK_MAX_ATTEMPTS` becomes `FAILED`
rather than retrying forever, so a repeatedly failing profile is visible instead
of silently looping.

Set `CAREERGPS_V2_WORKER_SCHEDULED=True` only after that deployment-owned job is
installed and observed completing work. Then run:

```
.\.venv\Scripts\python.exe manage.py verify_v2_feed --strict
```

Only after it passes should `CAREERGPS_FEED_READ_VERSION=v2` be deployed. Failed
builds retain the prior active generation. Expired task leases are reclaimed;
retries are bounded by `CAREERGPS_V2_TASK_MAX_ATTEMPTS`.

For local development, a manual invocation with a stable worker ID is sufficient
to verify queue operation and may be recorded with
`CAREERGPS_V2_WORKER_SCHEDULED=True` in the local `.env`. This means only that
the local v2 worker was verified. A production deployment still requires a
persistent scheduler or service before carrying either activation flag into
production.

Legacy pending tasks remain auditable. After successful cutover, classify and
supersede the obsolete legacy tasks explicitly with:

```
.\.venv\Scripts\python.exe manage.py reconcile_legacy_evaluation_tasks --supersede
```

The `legacy` read version remains an explicit, temporary rollback switch. It
does not change Clean-Job revisions, taxonomy facts, Block 1 resolution evidence,
or v2 generations. Remove that reader only after the production v2 scheduler has
been observed through a full deployment rollback window and no operational or
audit consumer requires historical legacy evaluations.

## Refreshing stored occupational intelligence

`RELATIONSHIP_AGGREGATION_VERSION` is part of the assessment dependency hash, so
changing the aggregation deliberately makes every previously stored verdict
stale. Assessments written by an older version are not upgraded in place: until
they are rebuilt, both pages disclose the missing or stale coverage rather than
presenting it as current.

The refresh order matters. Assessments are the input to the feed, so the
occupational backfill runs first and the feed is rebuilt from the result:

```
.\.venv\Scripts\python.exe manage.py backfill_occupational_intelligence --verify-determinism
.\.venv\Scripts\python.exe manage.py process_evaluation_queue --all-profiles --limit 200 --worker-id <stable-worker-name>
.\.venv\Scripts\python.exe manage.py verify_v2_feed --strict
```

`--all-profiles` enqueues one rebuild task per profile and then drains the
queue; repeat the command until it reports `Processed 0 feed-v2 task(s)`, which
is the only reliable indication that the queue is empty. A drain pass can itself
re-enqueue work, so the loop must be run until a pass both reports work and
leaves nothing pending, which `verify_v2_feed --strict` confirms via
`unhealthy_v2_queue_tasks`.

`verify_v2_feed --strict` is the acceptance gate: it fails while any assessment
carries a stale aggregation version.

Take a database copy before the first of those commands. The backfill writes
assessments and findings, so confirm the copy is complete and readable before
starting:

```
Copy-Item db.sqlite3 db.sqlite3.pre_<change>_backup_<yyyyMMdd>
```

Verify by size and integrity rather than by the copy command returning: the file
must match the live size and `pragma quick_check` must return `ok` when opened
read-only. A copy that is still growing is not a restore point. A writer killed mid-run
leaves a hot journal, and a read-only connection then fails with `attempt to
write a readonly database`. That is not corruption: open the database once
read-write so SQLite performs its own crash recovery, then confirm
`pragma quick_check` returns `ok` before continuing. A partially completed run is
safe to simply repeat, because every assessment is upserted on a natural key.

## Direct-employer cleaning

`careergps-direct` remains disabled for external polling. Approved, active,
published direct vacancies are nevertheless eligible for Clean-Job export when
the vacancy is not held, confirms no candidate fees, remains active/in scope,
and its source record points to a current raw publication version. The external
cleaner and normal v1.1 importer remain mandatory. Editing a publication creates
a new raw version, immediately breaking the old clean/raw currentness pin until
the replacement payload validates and activates.
