# Offline semantic model preparation and serving-process warmup

## Root cause and scope

Both `core.views.generate_cv` and `core.views.generate_cover_letter` call
`applications.services.semantic_relevance.select_relevant_facts`. Previously its
`@lru_cache(maxsize=1)` loader constructed `SentenceTransformer` from the configured
Hub identifier on the first request. That allowed model acquisition on the HTTP
request path. Later requests reused the process cache.

The existing setting remains `all-MiniLM-L6-v2`, Sentence Transformers' shorthand
for `sentence-transformers/all-MiniLM-L6-v2`. Neither the configured model nor the
pinned `sentence-transformers==6.0.0` requirement changes. There are no changes to
selection limits, tensor encoding, cosine similarity, stable ordering, candidate
evidence, Gemini prompts, repair rules, resume layouts or PDF/DOCX renderers.
Career Intelligence, matching, ingestion and Interview were untouched by that change.

Before:

```text
HTTP document request -> possible Hugging Face acquisition
                     -> semantic selection -> Gemini -> document
```

After:

```text
Setup/deployment -> Hugging Face -> deeply verified prepared local model
Worker startup -> lightweight local validation -> same model cached in worker RAM
HTTP document request -> memory cache -> semantic selection -> Gemini -> document
```

## Configuration and commands

Run these commands from the Django project directory (the directory with `manage.py`
and `.venv`):

```powershell
Set-Location 'C:\Users\malak\Downloads\CareerGPS-Phase-2.6.2-ranking-fix\CareerGPS-Phase-2.6.2'
```

Optionally add this to the existing, Git-ignored local `.env` using your own token:

```dotenv
HF_TOKEN=<my-token>
```

Alternatively, set it for the current PowerShell session:

```powershell
$env:HF_TOKEN = "<my-token>"
```

Do not supply the placeholder literally. The token is optional for the public model;
the command recommends authenticated access when it is absent. It does not log the
token or include provider exceptions in errors, including with `--traceback`.
The existing `python-dotenv` settings initialization loads `.env`; there is no new
secret store and no persisted Hugging Face login.

Optional local-path override:

```dotenv
SENTENCE_TRANSFORMER_LOCAL_PATH=.local_models/semantic
SENTENCE_TRANSFORMER_WARMUP_ON_START=True
```

This is also the safe development default. Relative paths resolve from the Django
project root. `.local_models/` is Git-ignored, including staging, backup and lock
files. For a custom location, use a deployment-owned directory outside source
control, or explicitly ignore that directory. Do not commit model artifacts.
`SENTENCE_TRANSFORMER_MODEL` remains the acquisition identifier, not a local path.

Prepare before starting the application:

```powershell
.\.venv\Scripts\python.exe manage.py prepare_semantic_model
.\.venv\Scripts\python.exe manage.py runserver
```

Stop an already running development server with Ctrl+C before restarting it.
For deployment, restart every Django worker after successful preparation. Wait for
`Semantic model warmup | outcome=ok` before benchmarking document generation.

To deliberately prepare a replacement:

```powershell
.\.venv\Scripts\python.exe manage.py prepare_semantic_model --force
```

If the same model is already valid, the ordinary command verifies it using only
local files and exits successfully without acquiring it again.

## Preparation, identity and replacement

The command loads the unchanged configured model with the optional `HF_TOKEN`,
saves it with Sentence Transformers' normal serialization, and writes
`careergps-model.json`. The manifest records the canonical model identifier,
embedding dimension and SHA-256 hashes of the saved files, without credentials.
It reloads the complete saved model locally and compares embeddings for two fixed,
non-private verification sentences (`rtol=1e-5`, `atol=1e-6`). There is no model
substitution, quantization, dimension reduction or changed sequence limit.

Preparation uses a temporary sibling directory and publishes only after validation.
An existing managed directory is renamed to a backup before the verified staging
directory takes its place. A failed publish restores the backup; if restoration
itself fails, the backup is retained for recovery. No working model is recursively
deleted before a verified replacement exists. Unrelated nonempty directories are
rejected even with `--force`.

Each rename is atomic on the same filesystem. Replacing a nonempty directory on
Windows requires two renames, so there can be a short interval without the target;
a request during that interval safely falls back. Prepare during deployment and
restart workers afterward. A sibling exclusive lock prevents concurrent preparation.
If setup is forcibly terminated, inspect retained staging/backup directories and
remove a stale `.semantic.prepare.lock` only after confirming no preparation process
is running. Directory names follow the configured target's name.

## Runtime and fallback

The cached loader checks the directory, manifest schema and configured model
identity, and checks the existence of each listed local artifact. It does not read
and hash artifact contents, recursively traverse the directory, or run embedding
comparisons during runtime. Local construction and a cheap dimension check remain.
Deep hash verification and embedding equivalence remain in preparation. A corrupt
local model that the library cannot load still triggers the safe fallback. It calls:

```python
SentenceTransformer(local_path, local_files_only=True, token=False, trust_remote_code=False)
```

This uses the library's supported [local-only loading semantics](https://sbert.net/docs/package_reference/sentence_transformer/model.html).
By default, the serving-process entry point warms the exact same `lru_cache`
before returning its WSGI/ASGI application. The first and subsequent document
requests therefore use memory. Lazy local loading remains available if startup
warmup is explicitly disabled or an initially unavailable model becomes available. Runtime does not need
`HF_TOKEN`. No global Hugging Face offline flags are set, so setup remains able to
download and unrelated integrations retain their behavior. Nothing loads or downloads
a model in `AppConfig.ready()`.

Missing, wrong-identity, damaged or unloadable models produce a fixed server warning
and return all original candidate facts through the existing safe fallback. Encoding
failures use the same fallback. No provider exception, filesystem path or candidate
content is included in this warning. Missing models are rejected before importing
Sentence Transformers, and an HTTP request never attempts acquisition or repair.

## Performance logs and manual test

The `careergps.documents` logger emits INFO timing records to the server console.
It records only document type, outcome, model source and monotonic durations:

- `job_context`: job-context resolution and construction of its selection text.
- `candidate_bundle`: construction of the trusted candidate fact bundle.
- `semantic_model_load`: first-load validation, imports and initialization.
- `semantic_selection`: selection/encoding time excluding model initialization.
- `gemini`: controlled writing, including any existing retries/integrity repair.
- `pdf`, `docx`: existing renderers, including their file writes.
- `persistence`: output-directory preparation and application/document database writes.
- `total`: the entire generation view, including validation and unclassified overhead.

Durations can be zero for skipped stages or cache hits. File writes performed by
renderers are included in their rendering duration, not counted twice as persistence.
The sum of stage times need not equal total because other view work also takes time.
Failed/rejected requests also emit timing records. Context-local storage prevents
timings being shared across requests. No private facts, job text, generated prose,
paths, user identifiers or tokens are logged by this instrumentation.

After successful preparation and restarting Django:

1. Wait for `Semantic startup timing | outcome=ok | validation=lightweight` and
   `Semantic model warmup | outcome=ok | source=local_disk | model_load=...s`.
2. Generate a cover letter FIRST. Expect `model_source=memory` and
   `semantic_model_load=0.000s` in its document timing record.
3. Generate a CV SECOND in the same worker. Expect the same memory/zero-load fields.
4. Record actual totals, Gemini and selection durations. Compare with the user's
   pre-warmup observations: cover letter 82.195s (50.152s model initialization) and
   subsequent CV 17.948s. No new live document-generation latency is claimed.
5. Missing/unloadable models produce `outcome=failed` at warmup and retain the
   genuine-facts document fallback. A request without rankable facts can show
   `model_source=not_needed`.

Startup logs are separate from request timings. They record manifest/existence
checks, deep artifact validation (zero in runtime), Sentence Transformers imports
(including Torch), model construction/weight loading, dimension validation and total.
They contain no candidate content, credentials or model contents. The dimension
helper prefers `get_embedding_dimension()` and falls back only on older APIs.

To independently verify network behavior, block Hugging Face egress for the Django
process while retaining Gemini access, or inspect outbound connections. Automated
model-heavy tests mock the provider and relevant request tests forbid socket
connections while rendering real PDF/DOCX files.

## Development and production lifecycle

Warmup is called after `get_wsgi_application()` / `get_asgi_application()` in the
project's serving entry points. It runs synchronously before the entry point is
ready to serve. There is no `AppConfig.ready()` hook and no second model instance.
Repeated explicit warmup calls use the same cache; a lock serializes warmup calls.
Failures are logged safely and do not prevent the application from starting.

The installed Django `runserver` calls `get_handler()` from `inner_run()`.
`get_internal_wsgi_application()` then imports the configured WSGI application.
With autoreload, Django invokes `inner_run()` in the serving child, while the parent
runs its restart loop. Without autoreload, the one serving process imports it.
Static-file handling is preserved. No environment-variable guessing or custom
runserver command is required. Code reloads create a fresh process and warm it again.

`django.setup()`, `check`, `migrate`, `makemigrations`, `test`, `shell` and
`collectstatic` do not import serving entry points automatically and therefore do
not warm the model. Explicitly importing a serving entry point is an intentional
exception. There is no separate warmup command that misleadingly warms only a
short-lived command process.

For WSGI production, use worker-local application loading, e.g. Gunicorn's normal
non-preloaded workers. For ASGI, each worker imports the ASGI entry point. Each
worker owns its model and pays its startup time/memory cost once. Increase worker
startup timeouts as necessary for the measured cold load; initialization is not free.
Do not preload Torch/model objects in a master and fork them into workers.

If your server requires master-process application preloading, disable automatic
entry-point warmup in that deployment environment:

```dotenv
SENTENCE_TRANSFORMER_WARMUP_ON_START=False
```

Then call the explicit worker hook after fork and before accepting requests. For
Gunicorn, for example, place this in its deployment configuration:

```python
def post_worker_init(worker):
    from applications.services.semantic_warmup import warm_semantic_model
    warm_semantic_model()
```

This explicit hook works even when automatic warmup is disabled, and imports/loads
Torch in the worker. Do not omit the worker hook when disabling automatic warmup if
you want the first document request to use memory. No cross-process model sharing,
Redis, or extra service is introduced.

## Measured cold-load investigation

Read-only profiling used the existing prepared model and blocked socket connections.
The old deep-validation path reproduced in a fresh process measured:

| Component | Seconds |
| --- | ---: |
| Manifest/schema and file existence checks | 0.003 |
| Recursive artifact hashing | 0.202 |
| Sentence Transformers imports, including Torch and dependencies | 33.672 |
| Model construction and local weight loading | 0.385 |
| Dimension check | 0.000 |
| Total | 34.263 |

Imports dominated this measured run. The original user-observed 50.152s included
these same categories, but its exact historical per-component allocation cannot
be recovered. General Python/Django initialization before entering the loader was
not included in that timer. Embedding equivalence was already setup-only.

A separate fresh-process WSGI startup using lightweight validation measured:
manifest checks 0.003s, hashing 0.000s, imports 30.446s, model construction 0.386s,
dimension check 0.000s, loader total 30.835s, and warmup total **30.836s**. The cache
was populated before any request, and the following loader call was a cache hit.
No FutureWarning appeared. These are actual local startup measurements, not
controlled before/after document benchmarks; import and filesystem timing varies.

The main improvement moves initialization before user requests. Avoiding 0.202s of
hashing alone does not explain a tens-of-seconds improvement, and no claim is made
that importing/loading the model has become free.

## Validation

`manage.py check`: no issues. `makemigrations --check --dry-run`: no changes.
Targeted tests: 64 passed, zero failures/errors, in 9.032 seconds.
Full suite: **423 passed**, zero failures/errors, in **91.876 seconds**; exit code 0.
Logs are retained in `.validation/semantic-warmup-{check,migrations,targeted,full-suite}.log`.
This phase adds 18 tests and updates the existing corruption/dimension API tests.

Tests cover local-only warmup, cache identity/reuse, first-cover-letter and second-CV
memory timings with real file rendering, safe missing/corrupt-model behavior,
lightweight validation, retained deep preparation, current/legacy dimension APIs,
WSGI/ASGI initialization order, autoreloader parent behavior, serving-child order,
static handling, and ordinary management commands. Model-heavy automated operations
are mocked; tests never download or warm the real model. The two actual local
read-only profiling runs above were performed separately from the suite.
