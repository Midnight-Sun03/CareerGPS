import sqlite3

con = sqlite3.connect('d:/CG/db.sqlite3')
cursor = con.cursor()
print('indexes on candidate table:')
for row in cursor.execute(
    "SELECT name, sql FROM sqlite_master "
    "WHERE type = 'index' AND tbl_name = 'core_occupationsourceresolutioncandidate'"
).fetchall():
    print(' ', row)

