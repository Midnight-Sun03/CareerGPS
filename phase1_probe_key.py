"""Read-only probe: why did the Accountant change create no feed-v2 task?"""
import io
import os
import sys

os.environ['DEBUG'] = 'True'
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'CareerGPS_Rebuild.settings')
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8',
                              errors='replace', line_buffering=True)

import django
django.setup()

from core.models import EvaluationRecomputeTask, Profile  # noqa: E402
from core.services.feed_queue import (  # noqa: E402
    TASK_VERSION, _profile_dependency,
)
from core.services.feed_v2 import authored_intent_fingerprint  # noqa: E402

profile = Profile.objects.get(pk=9)
dep = _profile_dependency(profile, '')
key = f'{TASK_VERSION}:profile:9:{dep}'
print('computed task_key:', key)
print('intent fingerprint:', authored_intent_fingerprint(profile))

print('tasks with exactly this key:')
for t in EvaluationRecomputeTask.objects.filter(task_key=key):
    print(f'  pk={t.pk} state={t.state} reason={t.reason!r} '
          f'created={t.created_at} completed={t.completed_at}')

print('keys of the two recent feed-v2 tasks:')
for pk in (5411, 5419, 5420):
    t = EvaluationRecomputeTask.objects.filter(pk=pk).first()
    if t:
        print(f'  pk={t.pk} {t.task_version} state={t.state} key={t.task_key}')

print('PROBE_DONE')
