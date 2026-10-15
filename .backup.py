"""Make a verified online backup of the working database (temporary helper)."""
import os
import sqlite3
import sys

SOURCE = 'db.sqlite3'
TARGET = sys.argv[1] if len(sys.argv) > 1 else 'db.sqlite3.backup.tmp'

# The online backup API takes a consistent snapshot while the database stays in
# use, which is what makes this safe to run against a live worker.
source = sqlite3.connect('file:%s?mode=ro' % SOURCE, uri=True, timeout=60)
target = sqlite3.connect(TARGET)
source.backup(target)
target.close()
source.close()

# Verified only when the copy is complete and passes its own integrity check.
live_size = os.path.getsize(SOURCE)
copy_size = os.path.getsize(TARGET)
check = sqlite3.connect('file:%s?mode=ro' % TARGET, uri=True)
quick = check.execute('pragma quick_check').fetchone()[0]
check.close()
print('live_size   %d' % live_size)
print('backup_size %d' % copy_size)
print('quick_check %s' % quick)
print('VERIFIED %s' % (quick == 'ok' and copy_size == live_size))
