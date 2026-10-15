import sqlite3

con = sqlite3.connect('d:/CG/db.sqlite3')
con.row_factory = sqlite3.Row

print('=== django_migrations ===')
for r in con.execute('SELECT name, applied FROM django_migrations ORDER BY id'):
    print(dict(r))

print()
print('=== candidate indexes ===')
for r in con.execute(
    "SELECT name, sql FROM sqlite_master "
    "WHERE type='index' AND tbl_name='core_occupationsourceresolutioncandidate' "
    "ORDER BY name"
):
    print(r['name'], '::', (r['sql'] or '')[:300])

print()
print('=== candidate unique constraints ===')
for r in con.execute(
    "SELECT type, name, sql FROM sqlite_master "
    "WHERE tbl_name='core_occupationsourceresolutioncandidate' AND sql IS NOT NULL"
):
    print(r['type'], '::', (r['sql'] or '')[:300])
