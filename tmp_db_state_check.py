import sqlite3
from datetime import datetime, timezone

con = sqlite3.connect('d:/CG/db.sqlite3')
con.execute(
    "INSERT INTO django_migrations (id, app, name, applied) VALUES (?, ?, ?, ?)",
    (74, 'core', '0023_candidate_concept_lookup', datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')))
con.commit()
print('recorded:', con.execute(
    "SELECT id, app, name FROM django_migrations WHERE name = '0023_candidate_concept_lookup'"
).fetchone())


