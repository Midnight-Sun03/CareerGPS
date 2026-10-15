# CareerGPS Phase 2.4 — AI Opportunity Understanding

## Objective

Phase 2.4 preprocesses the **opportunity side once** before user matching.

The language model never receives candidate/profile data. It receives only the compact opportunity information CareerGPS has already captured from the employer/source and converts that information into a strict CareerGPS extraction schema.

```text
source/API/scraper
      ↓
Opportunity (original source-side record)
      ↓
compact opportunity payload
      ↓
Gemini 3.5 Flash
      ↓
strict structured extraction
      ↓
source-evidence validation
      ↓
OpportunityExtraction + OpportunityCriterion
      ↓
OFO / ESCO normalization
      ↓
stored once
      ↓
all users match deterministically against the same processed job
```

## Non-negotiable boundaries

1. **No candidate data goes to Gemini.** No name, CV, education, skills, experience, location, profile or match result is used during opportunity extraction.
2. **The model is not evidence.** Employer/source information is the evidence. Every extracted criterion must contain `evidence_text` copied from data CareerGPS actually has.
3. **Unsupported evidence fails closed.** If the returned quote is not present in that opportunity's payload, the extraction is marked `FAILED` and no criteria are used.
4. **Partial sources stay partial.** Snippets are stored as `partial_text`; missing information is not treated as proof that a requirement does not exist.
5. **OFO/ESCO IDs are not invented by AI.** Taxonomy resolution happens locally after extraction.
6. **Matching stays deterministic.** AI structures the opportunity once; it never decides whether a user qualifies.
7. **Location/preferences do not establish career fit.** They remain visible as practical/preference signals but contribute zero career-fit evidence and zero score in Phase 2.4.
8. **RELATED remains score-neutral** until Phase 2.5 calibration.

## Database layer

### `Opportunity`
The original CareerGPS opportunity/source record. It remains untouched as the authoritative source-side record.

### `OpportunityExtraction`
One reusable post-AI interpretation per opportunity:

- status: `pending | processing | ready | failed | stale`
- source completeness: `full_structured | full_text | partial_text | unknown`
- source input hash
- provider/model/thinking level
- extraction schema and prompt versions
- raw structured model output
- validation errors / failure message
- processing timestamp

### `OpportunityCriterion`
Individual source-grounded facts:

- occupation
- skill / knowledge
- qualification
- experience
- certification / licence
- language
- explicit work eligibility
- work arrangement
- responsibility

Each criterion preserves:

- required/preferred/informational status
- raw interpreted value
- normalized value
- exact source evidence quote
- source section
- experience minimum/maximum when applicable
- metadata such as explicitly stated NQF

### `OpportunityCriterionTaxonomyMatch`
Persisted OFO/ESCO normalization for extracted occupation/skill criteria. Exact identity and conservative related/specialisation mappings are distinguished.

## Input to Gemini

CareerGPS does **not** send raw website HTML. It builds a compact payload from useful fields it already has, including where present:

- title
- company
- location
- description/job purpose
- minimum/preferred qualification text
- required/preferred experience text
- eligibility/competency sections
- responsibilities/duties
- work arrangement
- sector/category
- additional source-labelled employer fields that are not duplicates

A stable SHA-256 hash is calculated over this payload. A READY extraction with the same hash, model and schema/prompt version is reused rather than calling AI again.

## Gemini model

Default:

```text
model: gemini-3.5-flash
thinking level: medium
structured output: Pydantic schema through google-genai
batch size: 10 jobs/request
```

These settings are environment-configurable.

## Processing commands

Check coverage:

```powershell
.\.venv\Scripts\python.exe manage.py opportunity_ai_status
```

Process a small verification sample first:

```powershell
.\.venv\Scripts\python.exe manage.py extract_opportunities_ai --limit 10 --batch-size 5
```

Process one opportunity:

```powershell
.\.venv\Scripts\python.exe manage.py extract_opportunities_ai --opportunity-id 123
```

Process a source:

```powershell
.\.venv\Scripts\python.exe manage.py extract_opportunities_ai --source nedbank --limit 20
```

Resume the live catalogue:

```powershell
.\.venv\Scripts\python.exe manage.py extract_opportunities_ai
```

Unchanged READY jobs are skipped automatically. Failed jobs are retryable. If a provider quota/rate limit is detected, the command stops rather than hammering the API.

## Matching behavior

When a READY extraction exists, matching prefers its criteria instead of legacy free-text requirement blobs. If no READY extraction exists, the existing Phase 2.2/2.3 opportunity fields remain the fallback so the catalogue does not break during gradual backfill.

The user-facing explanation now explicitly says recommendations are based on opportunity information available to CareerGPS and the structured profile information saved by the user. A `partial_text` opportunity also tells the user that the employer may have additional requirements in the original advert.

## Verification completed in build environment

- `manage.py check` — pass
- `makemigrations --check --dry-run` — no changes
- Phase 2.4 + deterministic matcher + taxonomy tests — **69/69 pass**
- Core tests — **97/97 pass**
- Full `core + ingestion` suite reaches the pre-existing Linux/Windows `lxml` binary boundary for scraper tests; those tests must be run in the bundled Windows environment, as with Phase 2.3 final verification.

The bundled database has migration `ingestion.0008_phase_2_4_ai_opportunity_extraction` applied. Existing opportunity/user/taxonomy rows were not rewritten by Phase 2.4; the new post-AI tables start empty until the Gemini extraction command is deliberately run.
