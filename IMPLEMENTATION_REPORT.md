# CareerGPS community moderation and Career Intelligence implementation

Implemented and validated on 8 September 2026 in the existing Django project.

## IMPLEMENTED FEATURES

- Private Story reporting, moderation queues, retained removal/restoration history.
- Private Opportunity reporting, separate moderation suppression and a listing-evidence panel.
- Persisted in-app notification preferences and a centralized notification service.
- Structured desired careers, deterministic occupation assignments, persisted market snapshots and confirmed-profile comparisons.

The existing candidate evidence, matching/ranking, ingestion, Application Workspace,
CV/cover-letter and Interview architectures remain in place. Interview files were not changed.

## NEW MODELS

| Model | Purpose |
| --- | --- |
| `NotificationPreference` | One set of six optional category preferences per user. System notices cannot be disabled. |
| `StoryReport` | Private report, reason/details, open/resolved status, reviewer, timestamps and resolution. |
| `OpportunityReport` | Equivalent private opportunity moderation record. |
| `StoryModerationEvent` | Retained actor, old/new state, note and time for each explicit state transition. |
| `OpportunityModerationEvent` | Equivalent opportunity moderation history. |
| `DesiredCareer` | Internal normalized interpretation of authoritative CareerPreference intent. |
| `OpportunityCareerAssignment` | Exact/related/unresolved occupation assignment with version, method and evidence. |
| `CareerMarketSnapshot` | Versioned, dated, geographic market observation, coverage, metrics and provenance. |
| `CareerMarketSnapshotOpportunity` | Unique cohort member with frozen source and requirement inputs. |

`ReportRecord` and `ModerationEvent` are abstract shared model definitions, not additional tables.

## CHANGED MODELS

- `Notification`: category, event key, conditional unique user/event constraint and safe destination accessor.
- `Story`: `removed` state; ordinary saves no longer create notifications. Explicit moderation preserves the original publication timestamp through removal/restoration.
- `Opportunity`: separate `clear` / `under_review` / `suppressed` moderation state and explicit optional salary pay period. Discovery excludes suppressed listings and vacancies with a definite past deadline. Canonical writes and extraction invalidation commit together.
- The existing `OpportunityExtraction.stale` state is used without changing its schema or discarding prior criteria/provenance.

## MIGRATIONS

- `core.0010_community_and_career_intelligence`: new tables, fields, indexes and uniqueness constraints; depends on core 0009 and the swappable user model.
- `core.0011_notification_categories`: deterministic classification of existing Story, deadline and application notifications; depends on core 0010.

Both migrations were first validated on a copy and then applied to the working
`db.sqlite3`. No old applied migration was edited. All ten checked existing table
record counts match the pre-migration backup, including users, profiles, Stories,
notifications, opportunities, saved items, applications, generated documents,
interviews and raw source records.

A consistent SQLite backup is retained at `.validation/before-community-phase.sqlite3`.
Validation databases, screenshots and browser artifacts are ignored by `.gitignore`.

## NEW SERVICES

Under `core/services/`:

- `notifications.py`: validated internal destinations, categories/preferences, mandatory system delivery and idempotent in-app creation.
- `moderation.py`: authenticated report submission and permission-checked, atomic moderation/resolution.
- `career_targets.py`: explicit owned mutations that synchronize profile intent and normalized mappings.
- `career_intent.py`: profile role reading and mapping invalidation on preference saves/deletion.
- `career_resolution.py`: cached deterministic human-name resolution and user-bound signed choices.
- `career_assignments.py`: existing taxonomy resolver, bounded label queries and versioned assignments.
- `career_market.py`: deterministic aggregation and snapshot generation outside request rendering.
- `market_comparison.py`: existing conservative matching primitives, confirmed evidence and no readiness score.
- `integrity.py`: canonical/extraction/taxonomy-release invalidation, registered by `CoreConfig.ready()`.

## NEW ROUTES / VIEWS

| Route | Name / behavior |
| --- | --- |
| `/stories/<id>/report/` | `report_story`: authorized GET form, POST creation. |
| `/opportunities/<id>/report/` | `report_opportunity`: public catalogue or the requesting user's saved/application history. |
| `/career-intelligence/` | `career_intelligence`: read-only profile default, free-text exploration and snapshot display. |
| `/career-intelligence/set/` | `set_desired_career`: explicit, CSRF-protected owned POST to save intent and mapping. |
| `/career-intelligence/<id>/primary/` | `primary_career`: owner-only POST. |
| `/career-intelligence/<id>/remove/` | `delete_career`: owner-only POST. |

Existing `/settings/` now saves user-specific notification preferences.
`/notifications/<id>/open/` now requires POST; its redirect still opens the
Notification Centre with `?selected=<id>`. Direct selected-notification GETs remain read-only.

## ADMIN MODERATION FLOW

1. Open the StoryReport or OpportunityReport queue; filter by status, reason, date, target or authorized reporter identity.
2. Inspect the report. Opportunity reports link to the existing source-record and raw-evidence admin pages.
3. Optionally save a private moderator note.
4. Dismiss the report, remove/suppress the item and resolve it, or resolve after recording the action taken in the note.
5. Reviewer, review time and resolution are stored. State transitions have separate retained audit records.

Story admin edits use the moderation service and can restore publication.
Opportunity admin supports suppression, under-review and restoration independently
of source lifecycle. A clear moderation state does not resurrect an expired listing.

Report and audit records cannot be deleted through their admin pages. Intelligence
models are registered for inspection; desired-career mutations stay in the owned
user workflow. Notification preferences are available for authorized debugging.

## NOTIFICATION ARCHITECTURE

Optional categories are stories, opportunities, deadlines, applications, interviews
and career_intelligence. Defaults are enabled; missing preference records use those
defaults. `system` is mandatory and has no disabling preference.

All current moderation/report notifications use `notify()`. Stable keys and a
database uniqueness constraint prevent duplicate delivery of the same event to
one user. Disabled categories stop future optional creation; existing notifications
remain readable. Targets must be internal paths; external schemes, protocol-relative
URLs, backslashes, control characters and encoded bypasses are rejected. Legacy unsafe
targets are not rendered as links.

The bell and notification list use CSRF-protected POST buttons styled as clickable
items. Opening marks only the owned notification read, then redirects to the Centre.
The related-item link remains a separate action. Mark-all remains owned and POST-only.

No routine email or SMS delivery was added. OTP/password reset remain separate.
Unsupported digest/connection/message controls were removed from Settings and the
old notification localStorage defaults removed. Settings explicitly explains that
automatic deadline/application/interview alerts are not implemented in this phase.

## STORY REPORTING FLOW

Only another user's approved Story is reportable. A reason and optional details are
submitted by POST. A target lock and partial database constraint prevent duplicate
open reports. Resolution permits a later new report. Reports do not change public
visibility automatically and are never displayed in the public feed.

Moderators can remove a Story without deleting it, retain report/event history and
restore it later. Author approval/rejection titles remain unchanged; rejection notes
are included when supplied. Ordinary saves and repeated identical moderation states
do not create another notification. Story-category preferences apply to author and
report-review updates.

## OPPORTUNITY REPORTING / TRUST FLOW

All eleven requested reasons are available. Reporting is private and never sets a
trust score or automatically suppresses a listing. Moderator suppression is separate
from ingestion-owned lifecycle/active fields.

Ingestion writes only source-controlled fields on existing records. Even an ingestion
object loaded before moderation cannot write its stale suppression value back. Saved
and application records remain intact. Saved cards retain a Report action for listings
that have left the catalogue.

The detail panel presents source, reference, first/last observation, deadline, listing
state and application destination. It makes no safety-verification claim and exposes
neither reporter identity nor private moderation records.

## CAREER DIRECTION AUTHORITY

`CareerPreference.target_roles` is user-authored career intent. The Profile Career
Preferences form is the only user-facing career-direction editor. Commas,
semicolons and newlines separate roles. Practical preference edits do not change
occupation mappings, and career intent is never candidate evidence.

`DesiredCareer` is an internal normalized occupation mapping, derived from saved
target-role text only when one active trusted occupation has an exact preferred or
identity-alias label. Ambiguous labels, specialisations, suggestions, fuzzy/semantic
similarity and shared ISCO groups do not create mappings. Unresolved text remains
valid career direction and receives normal Profile completion credit. The
`reconcile_career_intent` command refreshes older mappings from current text.

Career Intelligence reads the saved direction first. Trusted mappings, when
available, select occupation-specific live market cohorts; an unresolved direction
limits that analysis without claiming the user has no direction. Opportunity
discovery and employer Talent use derived mappings for trusted exact/related
occupation identity. The evidence matcher remains authoritative for fit and
admission, with saved text available only for career-direction relevance/order.

Unresolved careers and missing, stale or empty snapshots show a controlled no-data
state with search available. Career names have no visible taxonomy source prefixes;
technical snapshot provenance remains in the existing provenance section.

## CAREER COHORT ASSIGNMENT

Version: `career-title-v1`.

- Exact/canonical: unambiguous identity labels from the existing active taxonomy resolver.
- Related: trusted specialisation labels or a direct narrower-to-broader occupation edge.
- Unresolved: no trustworthy label resolution, including same-source ambiguity.

No Gemini, title-substring guessing, ISCO-group equivalence or multi-hop expansion
is used. A listing may belong to more than one explicit taxonomy cohort; it is counted
once within each cohort. Assignment methods, title evidence, active releases and input
hashes are stored. Identical inputs preserve existing assignments.

The working database has 553 exact and 216 related assignment rows, plus 3,833 unresolved
listing rows. These are assignment rows, not disjoint opportunity counts.

## CAREER MARKET SNAPSHOT ARCHITECTURE

Version: `career-market-v1`. Default scope: all observed structured/unknown locations.
Default observation window: the last 30 inclusive local calendar days, using last_seen_at.
Future-observed, expired, suppressed, inactive, out-of-scope and unreliable-assignment
records are excluded from current snapshots.

Snapshots contain cohort IDs, dates, versions, source counts, criterion-specific
coverage and metrics. Each member freezes the canonical values used, criterion text,
requirement level, supporting quote, extraction hash/version and source/raw references.
Later canonical or extraction changes cannot rewrite those frozen inputs.

Changed evidence marks snapshots stale. Page GETs do not rebuild them and only show
current-day, current-version, non-stale analysis. Missing/stale analysis has an explicit
unavailable state. The maintenance command refreshes assignments and snapshots;
identical inputs within the same window reuse the snapshot. Changed inputs or new dates
preserve earlier snapshots and create new observations.

Requirements are counted per distinct listing, using listings with that type of
requirement as the percentage denominator. Skill totals combine requirement levels
without double counting and retain the required/preferred/unknown breakdown.
Qualification alternatives and requirement levels remain intact. Experience only
uses interpretable required minima; missing/conflicting duration stays unknown.
Geography distinguishes structured fields from raw location text. Sectors are labeled
as source categories rather than an official classification.

Salary groups require a false prediction flag, explicit hour/month/year period,
valid positive advertised bounds and a currency; at least five observations must
share a currency/period. Unknown periods are not inferred from source conventions.

No trend calculator is implemented. Stored future comparison gates require at least
four observations spanning 90 days, spacing of at least 21 days, cohorts of at least
30, requirement coverage of at least 50%, identical source sets, compatible taxonomy,
extraction/assignment/aggregation versions and the same scope/window length.

## YOU VS MARKET LOGIC

The existing matching engine supplies skill, qualification, certification and language
comparisons. Only confirmed structured candidate facts are loaded. Trusted related
skill evidence uses the existing directional taxonomy policy.

Results are presented as supported (`FOUND`/`RELATED`), no supporting profile evidence
(`NOT_FOUND_ON_PROFILE`) or `UNKNOWN`. Unknown requirement levels and work eligibility
are not converted to missing. The dashboard says “No supporting evidence found in your
CareerGPS profile.” It does not assert that a person lacks an ability or calculate a
generic career-readiness percentage. The display is bounded to common requirement groups.

## DATA COVERAGE / LIMITATIONS

Actual working data after migration/backfill:

| Measure | Count |
| --- | ---: |
| Stored opportunities retained | 4,404 |
| Currently discoverable opportunities | 4,286 |
| Career snapshots built | 251 |
| Distinct eligible listings across those cohorts | 555 |
| Snapshot memberships, including different taxonomy cohorts | 749 |
| Included distinct listings with skill criteria | 47 |
| Included distinct listings with qualification criteria | 120 |
| Included distinct listings with certification criteria | 27 |
| Included distinct listings with experience criteria (not necessarily interpretable) | 102 |
| Reliable salary observations | 0 |

Career assignment and extraction coverage are sparse. The largest cohort currently
contains 32 listings. An AI-ready record alone is not evidence of complete requirements.
Formatting/case normalization does not claim corporate identity resolution or universal
qualification/certification equivalence. Salary periods remain unavailable, and there
is insufficient structured work-arrangement data. Trends remain unavailable.

Careers without an existing usable snapshot show the controlled no-data state
until maintenance runs. Scheduling is operational: no hidden GET work, Celery worker
or automatic scheduler was introduced. Run maintenance after ingestion/extraction and
daily if current-day dashboards are wanted. Referenced taxonomy concepts are protected
for history; import a new release version rather than deleting referenced concepts.

## SECURITY / INTEGRITY FIXES

- Notification URL validation, event uniqueness, preferences and owned POST read mutations.
- Private, authenticated reports with CSRF, status/ownership checks and open-report constraints.
- Explicit moderator permission checks, atomic transitions and retained review history.
- Suppression preserved even when ingestion holds an older object instance.
- Definite past vacancy deadlines excluded from current discovery without deleting history.
- Changed canonical extraction inputs mark ready/processing extraction stale atomically.
- An extraction response generated against an older input cannot become ready after a concurrent content change.
Historical source-specific salary repair details were superseded by the source purge.

## FILES CREATED

- `core/services/__init__.py`
- `core/services/notifications.py`
- `core/services/moderation.py`
- `core/services/career_targets.py`
- `core/services/integrity.py`
- `core/services/career_assignments.py`
- `core/services/career_market.py`
- `core/services/market_comparison.py`
- `core/community_views.py`
- `core/management/commands/maintain_career_market.py`
- `core/migrations/0010_community_and_career_intelligence.py`
- `core/migrations/0011_notification_categories.py`
- `core/templates/core/report_item.html`
- `core/templates/core/career_intelligence.html`
- `core/static/core/css/career_intelligence.css`
- `core/test_community.py`
- `core/test_career_intelligence.py`
- `DEFERRED_SECURITY_FINDINGS.md`
- `IMPLEMENTATION_REPORT.md`

Ignored validation artifacts include logs, the pre-migration backup, a database copy,
synthetic rendered pages and desktop/mobile screenshots under `.validation/`.

## FILES MODIFIED

- `.gitignore`
- `core/apps.py`, `core/models.py`, `core/forms.py`, `core/views.py`, `core/urls.py`, `core/admin.py`
- `core/tests.py` (stale plaintext-OTP assertion and isolated email test settings)
- `templates/dashboard_pages.html`
- `core/templates/core/settings.html`, `notifications.html`, `stories.html`, `opportunity_detail.html`, `saved.html`
- `core/static/core/css/main.css`, `notifications.css`, `settings.css`, `opportunity_detail.css`
- `core/static/core/js/settings.js`
- `ingestion/ai/service.py`
- `ingestion/pipeline/deduplication.py`
- `db.sqlite3` (validated migrations, salary metadata repair, assignments and snapshots)

## TEST RESULTS

| Validation | Result |
| --- | --- |
| Baseline after correcting the stale OTP test | 259 passed; 0 failures/errors; 108.296 seconds |
| Final targeted community/intelligence tests | 91 passed; 0 failures/errors; 3.531 seconds |
| Complete suite after career-selection revision | 378 passed; 0 failures/errors; 200.464 seconds |
| `manage.py check` | No issues |
| `manage.py makemigrations --check` | No changes detected |
| Migrations on current-data copy, then working database | Both applied successfully |
| Salary repair repeat on validation copy | 4,120 updates, then 0 |
| Existing record preservation | Ten checked table counts match backup |
| Browser layout | Edge desktop 1440px and mobile 390px: no horizontal overflow |

Tests cover preferences and no email, event constraints, unsafe targets, CSRF and read
ownership, report privacy/duplicates/status, admin moderation, re-ingestion including a
stale worker object, deadline handling, extraction invalidation/in-flight responses,
salary repair, desired-career validation/ownership/primary constraints, exact/related/
ambiguous assignments, sparse denominators, provenance, salary confidence and confirmed
profile comparisons. A page test checks that GET neither scans the opportunity table
nor performs writes/rebuilds.

The current-data copy assigned all 4,404 listings in 61 queries (~1.61 seconds) and built
251 snapshots in ~6.35 seconds. Snapshot creation used 1,266 queries, mostly per-snapshot
creation/member writes; opportunity evidence is prefetched rather than queried once
per listing. These are local measurements, not production load benchmarks.

No Git executable was available for a working-tree diff. Current source was re-read
before edits, the baseline recorded, and current database changes preserved. Validation
used process-local development settings rather than editing `.env`. Browser checks used
isolated synthetic user data. No routine notification emails or live AI extraction calls
were sent by the implementation/backfill work.

Detailed logs: `baseline-tests.log`, `final-validation.log`,
`.validation/current-data-validation.log`, `.validation/applied-migrations-backfill.log`.

## REMAINING DEFERRED ISSUES

See `DEFERRED_SECURITY_FINDINGS.md` for parked Interview script escaping/private-media
delivery, disabled-account reuse, durable OTP abuse controls, saved-matching parity,
legacy GET mutations/redirects, enrichment/prose validation and old prototype cleanup.
Those findings were not silently treated as fixed.

## MANUAL COMMANDS I NEED TO RUN

Migrations, salary repair, assignments and initial snapshots have already been applied
to this working database. The following commands reproduce the steps and support future
maintenance. No new dependencies are required.

From PowerShell:

```powershell
Set-Location 'C:\Users\malak\Downloads\CareerGPS-Phase-2.6.2-ranking-fix\CareerGPS-Phase-2.6.2'

# Local development only; addresses the inherited DEBUG=False / empty-key environment.
$env:DEBUG = 'True'

# Schema
.\.venv\Scripts\python.exe manage.py migrate

# Career assignments and market snapshots, separately
.\.venv\Scripts\python.exe manage.py maintain_career_market --assign-only
.\.venv\Scripts\python.exe manage.py maintain_career_market --snapshots-only --window-days 30

# Or refresh both after ingestion/extraction; also run daily for current-day snapshots.
.\.venv\Scripts\python.exe manage.py maintain_career_market

# Inspection and a non-writing preview
.\.venv\Scripts\python.exe manage.py maintain_career_market --status
.\.venv\Scripts\python.exe manage.py maintain_career_market --dry-run

# Validation
.\.venv\Scripts\python.exe manage.py check
.\.venv\Scripts\python.exe manage.py makemigrations --check
.\.venv\Scripts\python.exe manage.py test core.test_community core.test_career_intelligence
.\.venv\Scripts\python.exe manage.py test

# Run/restart the local server, then sign in and visit /career-intelligence/
.\.venv\Scripts\python.exe manage.py runserver
```

For a specific occupation, use `--career-id <occupation-concept-id>` with
`maintain_career_market --snapshots-only`. Optional `--country 'South Africa'` uses the
stored structured country field; the current user dashboard displays the default
`all_observed` scope. Dry-run snapshots use persisted assignments, not uncommitted
assignment proposals.

## FINAL VERDICT

The four features are implemented in the existing architecture, the working database
has been migrated/backfilled, and the final 378-test suite passes. Career Intelligence
is an honest, sparse-data V1 with visible denominators and retained provenance. It does
not claim complete market coverage, reliable salary analytics without pay periods,
career-readiness percentages or working historical trends.


## CAREER-SELECTION REVISION VALIDATION

The focused UX revision adds 28 regression tests in `core/test_career_selection.py`
and updates the existing selection assertions. The complete suite passed:
`Ran 378 tests in 200.464s`, `OK`, with no failures or errors. The suite's Django
system check reported no issues. `makemigrations --check --dry-run` reported
`No changes detected`.

Coverage includes automatic profile analysis, empty profiles, exact names, trusted
aliases, conservative specialisations, same-source/cross-source ambiguity, optional
substring suggestions, no-data and stale/empty snapshots, cache reuse, release
changes, profile edit/deletion synchronization, no GET database writes, explicit
owned POST saves, CSRF, forged/expired choices and legacy bookmark ownership.

Edge browser checks used fixtures rendered from an isolated test database. The
exploration, disambiguation and no-data pages passed at 1440px and 390px, with no
horizontal overflow and no occupation select controls. Desktop/mobile screenshots
are retained under `.validation/selection-*.png`; the full test output is retained
in `.validation/career-selection-full-suite.log`.

This revision makes no model/migration, assignment, snapshot aggregation/member,
market comparison or Interview changes. It does not rebuild the working database's
analytics or alter its saved profile intent during validation.
