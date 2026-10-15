"""Read-only SQLite recovery inspection on an NTFS target. Writes nothing in E:\\CG."""
import os
import sqlite3
import sys

path = os.environ["CGPS_RECOVERY_DB"]
print("path", path, flush=True)
print("size", os.path.getsize(path), flush=True)
connection = sqlite3.connect(path, timeout=5)
try:
    cursor = connection.cursor()
    cursor.execute("SELECT sqlite_version()")
    print("sqlite_version", cursor.fetchone()[0], flush=True)
    if "--deep" in sys.argv:
        cursor.execute("PRAGMA quick_check")
        print("quick_check", cursor.fetchall()[:5], flush=True)
    cursor.execute("SELECT COUNT(*) FROM sqlite_master")
    print("schema_objects", cursor.fetchone()[0], flush=True)
    cursor.execute("SELECT COUNT(*) FROM core_opportunity")
    print("opportunities", cursor.fetchone()[0], flush=True)
    cursor.execute("SELECT COUNT(*) FROM core_opportunitytitlegatedecision")
    print("gate_rows", cursor.fetchone()[0], flush=True)
finally:
    connection.close()
print("readonly_probe_ok", flush=True)
