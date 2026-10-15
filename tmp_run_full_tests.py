import os
import subprocess
import sys

os.environ['DEBUG'] = 'True'
os.environ['DJANGO_SETTINGS_MODULE'] = 'CareerGPS_Rebuild.settings'

cmd = [
    sys.executable,
    'd:/CG/manage.py',
    'test',
    'core',
    '-v', '2',
]
with open('d:/CG/tmp_full_test_out.txt', 'wb') as out:
    rc = subprocess.call(cmd, stdout=out, stderr=subprocess.STDOUT)

print('EXIT_CODE', rc)
