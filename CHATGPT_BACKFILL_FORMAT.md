# CareerGPS ChatGPT Phase 2.4 Backfill

This workflow backfills `OpportunityExtraction` / `OpportunityCriterion` without sending user/profile/CV data anywhere.

## Integrity rules

1. The export contains **opportunity-side data only**.
2. The only admissible extraction evidence is `payload.sections`.
3. Every result must preserve `opportunity_id` and the exact exported `input_hash`.
4. Every `evidence_text` must be a verbatim substring of the current CareerGPS payload. Unsupported evidence is rejected.
5. The importer validates the entire result file before writing anything.
6. If a job changed after export, its hash no longer matches and the result is rejected rather than imported stale.
7. Existing READY extractions are preserved by default.
8. Imported ChatGPT results are stored in the same Phase 2.4 tables with `extractor=chatgpt_backfill`, not falsely labelled as Gemini.
9. OFO/ESCO normalization runs locally during import using the existing CareerGPS taxonomy code.
10. A result becomes `READY` only after schema validation, evidence validation, deduplication, database persistence and taxonomy normalization succeed.

## 1. Export

Export every Opportunity row:

```powershell
.\.venv\Scripts\python.exe manage.py export_opportunities_for_chatgpt --output careergps_opportunity_ai_export.jsonl
```

Or only current live/discoverable opportunities:

```powershell
.\.venv\Scripts\python.exe manage.py export_opportunities_for_chatgpt --discoverable-only --output careergps_opportunity_ai_export.jsonl
```

For the separate structured-processing queue, export opportunities without a
`READY` extraction whose closing date is today, later, or unknown:

```powershell
.\.venv\Scripts\python.exe manage.py export_opportunities_for_chatgpt --needs-processing --output careergps_opportunity_ai_export.jsonl
```

This filter is only for the processing export. It does not change the source
ingestion lifecycle or the default all-opportunities export. Each exported row
keeps the stable `opportunity_id` and `input_hash`, source-derived description
in `payload.sections`, and provider IDs, URLs, and dates in audit metadata.
Only `payload.sections` may support extracted requirements.

Normal `ingest_all` and individual source ingestion commands do not call Gemini.
The existing `extract_opportunities_ai` command remains an explicit manual
legacy operation; the validated JSONL importer is the separate path for
processed results.

Upload the JSONL file to ChatGPT. Do not paste jobs manually.

## 2. Result format

ChatGPT returns JSONL. First line:

```json
{"record_type":"manifest","format_version":"careergps-chatgpt-backfill-results-v1","source_catalogue_digest":"<copied from export manifest>","model_name":"GPT-5.6 Sol"}
```

Each remaining line:

```json
{"record_type":"result","opportunity_id":123,"input_hash":"<exact exported hash>","criteria":[{"criterion_type":"qualification","value":"Honours degree in Actuarial Science and/or Mathematical Statistics/Computer Science or strong mathematical/economics background","requirement_level":"required","evidence_text":"<verbatim source quote>","source_section":"minimum_qualification","minimum_years":null,"maximum_years":null,"context":""}]}
```

Allowed criterion types are the current Phase 2.4 schema:
`skill`, `qualification`, `certification`, `experience`, `language`, `work_eligibility`.

## 3. Validate before import

```powershell
.\.venv\Scripts\python.exe manage.py import_chatgpt_opportunity_ai careergps_ai_results.jsonl --dry-run
```

If the dry run is not clean, do **not** import.

## 4. Import

```powershell
.\.venv\Scripts\python.exe manage.py import_chatgpt_opportunity_ai careergps_ai_results.jsonl
```

The importer uses the same Phase 2.4 persistence and taxonomy-normalization path as normal AI extraction. Valid rows finish in the existing `OpportunityExtraction` table with `status=ready`.

## Existing READY jobs

By default, a current same-hash READY extraction is preserved and not overwritten. Only use this deliberately if you have reviewed the replacement:

```powershell
.\.venv\Scripts\python.exe manage.py import_chatgpt_opportunity_ai careergps_ai_results.jsonl --force-overwrite-ready
```
