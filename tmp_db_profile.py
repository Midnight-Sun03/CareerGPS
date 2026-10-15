import sqlite3

con = sqlite3.connect('d:/CG/db.sqlite3')
print('READY clean revisions:', con.execute(
    "SELECT COUNT(*) FROM core_opportunitycleanrevision WHERE status = 'READY'"
).fetchone()[0])
print('total clean revisions:', con.execute(
    'SELECT COUNT(*) FROM core_opportunitycleanrevision'
).fetchone()[0])
print('distinct READY opportunities:', con.execute(
    "SELECT COUNT(DISTINCT opportunity_id) FROM core_opportunitycleanrevision WHERE status = 'READY'"
).fetchone()[0])
print('candidates:', con.execute(
    'SELECT COUNT(*) FROM core_occupationsourceresolutioncandidate'
).fetchone()[0])
print('candidates:', con.execute(
    'SELECT COUNT(*) FROM core_occupationsourceresolutioncandidate'
).fetchone()[0])
print('resolutions:', con.execute(
    'SELECT COUNT(*) FROM core_occupationsourceresolution'
).fetchone()[0])
print('title resolutions:', con.execute(
    'SELECT COUNT(*) FROM core_opportunitytitleresolution'
).fetchone()[0])

