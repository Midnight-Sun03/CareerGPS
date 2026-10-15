# Phase 2.4 handoff

## Build status

Phase 2.4 opportunity-side AI preprocessing is implemented on top of the verified Phase 2.3 taxonomy build.

### Implemented

- persisted post-AI opportunity layer
- strict Gemini 3.5 Flash extraction schema
- multi-job request batching
- compact opportunity-only payloads
- zero candidate/profile data in extractor
- source completeness (`full_structured`, `full_text`, `partial_text`, `unknown`)
- stable input hashing and unchanged-job reuse
- verbatim evidence validation
- fail-closed unsupported claims
- extraction/version/model provenance
- OFO/ESCO normalization persisted for occupation/skill criteria
- READY extraction preferred by matcher
- legacy opportunity fields retained as fallback during backfill
- multiple qualification criteria supported
- AI-extracted experience constraints supported
- certification/language criteria supported conservatively
- preference/location signals removed from career-fit scoring/evidence counts
- partial-source disclosure added to match explanation
- status and extraction management commands

### Not done intentionally

- No Gemini backfill has been run automatically. This avoids spending quota without the user's explicit run and allows a 5–10 job quality inspection first.
- No Phase 2.5 scoring calibration. RELATED remains score-neutral.
- No candidate data is sent to the opportunity-understanding model.
- No attempt is made to reconstruct information missing from partial-source snippets.

## First Windows verification

```powershell
.\.venv\Scripts\python.exe manage.py check
.\.venv\Scripts\python.exe manage.py makemigrations --check --dry-run
.\.venv\Scripts\python.exe manage.py test
.\.venv\Scripts\python.exe manage.py opportunity_ai_status
```

If `google-genai` is missing from the local venv despite being pinned in `requirements.txt`:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

## First live AI test

Do **not** start with all 4,400 jobs. First process a small mixed sample and inspect it in Django admin / matching UI.

```powershell
.\.venv\Scripts\python.exe manage.py extract_opportunities_ai --source nedbank --limit 5 --batch-size 5
.\.venv\Scripts\python.exe manage.py opportunity_ai_status
```

Then verify that extracted values are supported by their stored `evidence_text`, required/preferred meaning is correct, and partial-source jobs disclose their limited source fidelity.
