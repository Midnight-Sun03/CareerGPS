CareerGPS 2.6.2 — CV render schema fix

Fixes:
- Generated legacy CV education entries are dictionaries expected by DOCX/PDF renderers, not strings.
- Certification entries are renderer-compatible dictionaries.
- Work entries expose both organisation and company using the same trusted candidate fact.
- Project entries preserve organisation from trusted candidate facts.
- Preserves the numeric-integrity repair already added to document_generation.py.

Replace only:
- applications/services/document_generation.py
- applications/tests.py

No migration, package, .env, .venv, database, matching, or ranking changes.

Recommended checks:
  .\.venv\Scripts\python.exe manage.py test applications
  .\.venv\Scripts\python.exe manage.py check
  .\.venv\Scripts\python.exe manage.py runserver
