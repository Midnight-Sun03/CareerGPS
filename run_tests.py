import os
import sys

# The machine-level DEBUG env var is set to an invalid value ("release");
# settings.env_bool would raise before anything else runs.
os.environ['DEBUG'] = 'True'
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'CareerGPS_Rebuild.settings')
# Shape argv so settings.TESTING_MODE ("test" in sys.argv[:2]) is True,
# exactly as `python manage.py test` would see it.  This keeps the
# request-cycle safety net (CAREERGPS_QUEUE_SAFETY_NET) disabled by default
# under the test runner.
sys.argv = ['manage.py', 'test'] + sys.argv[1:]

from django.core.management import execute_from_command_line

execute_from_command_line(sys.argv)

