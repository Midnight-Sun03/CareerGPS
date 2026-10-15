"""Report authored target roles per profile (temporary helper)."""
import os

import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'CareerGPS.settings')
django.setup()

from core.models import CareerPreference  # noqa: E402

for preference in CareerPreference.objects.order_by('profile_id'):
    print('profile %-3s target_roles=%r' % (
        preference.profile_id, preference.target_roles))

import os

import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'CareerGPS.settings')
django.setup()

from django.conf import settings  # noqa: E402

print('FEED_READ_VERSION    =', settings.CAREERGPS_FEED_READ_VERSION)
print('V2_WORKER_SCHEDULED  =', settings.CAREERGPS_V2_WORKER_SCHEDULED)
print('DATABASE             =', settings.DATABASES['default']['NAME'])
