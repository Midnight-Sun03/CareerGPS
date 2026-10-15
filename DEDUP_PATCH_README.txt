CareerGPS Phase 2.4 duplicate-consolidation patch (prompt v2.4.3)

WHAT CHANGED
- Gemini's permanent opportunity instruction now explicitly consolidates repeated semantic requirements.
- Same requirement repeated across text/sections should be returned once.
- Required wins over preferred when the same requirement/context appears with both strengths.
- Gemini is instructed to use the shortest clear verbatim evidence quote, rather than copying repeated source phrases.
- CareerGPS keeps a conservative deterministic duplicate guard after Gemini: punctuation/case/spacing duplicates collapse; full repeated phrases such as "Diploma Diploma Diploma" collapse; same criterion/context keeps the strongest requirement level.
- Added a fifth Gemini smoke-test stage specifically for duplicate consolidation.
- Prompt version bumped from 2.4.2 to 2.4.3. Schema remains 2.4.2.

INSTALL
Extract this patch directly into your current CareerGPS-changes folder and allow matching files to be replaced.
Do NOT replace/delete .env, db.sqlite3, or .venv.

IMPORTANT LOCAL .env CHANGE
If your .env contains OPPORTUNITY_AI_PROMPT_VERSION=2.4.2, change ONLY that line to:
OPPORTUNITY_AI_PROMPT_VERSION=2.4.3

Keep:
OPPORTUNITY_AI_SCHEMA_VERSION=2.4.2
OPPORTUNITY_AI_MODEL=gemini-3.5-flash
OPPORTUNITY_AI_THINKING_LEVEL=medium

VERIFY
1) .\.venv\Scripts\python.exe manage.py check
2) .\.venv\Scripts\python.exe manage.py test ingestion.test_phase_2_4
3) .\.venv\Scripts\python.exe manage.py gemini_structured_smoke

RETEST REAL JOB 5
.\.venv\Scripts\python.exe manage.py extract_opportunities_ai --opportunity-id 5 --force

Then inspect stored criteria:
.\.venv\Scripts\python.exe manage.py shell -c "from ingestion.models import OpportunityExtraction; e=OpportunityExtraction.objects.get(opportunity_id=5); [print(c.criterion_type,'|',c.requirement_level,'|',c.raw_value,'| EVIDENCE:',c.evidence_text) for c in e.criteria.all()]"
