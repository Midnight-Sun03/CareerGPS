"""Phase 1: inspect current queue/feed state for profiles 9 and 19 (read-only)."""
import io
import os
import sys

os.environ['DEBUG'] = 'True'
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'CareerGPS_Rebuild.settings')
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8',
                              errors='replace', line_buffering=True)

import django
django.setup()

from core.models import EvaluationRecomputeTask, ProfileFeedState  # noqa: E402
from django.db import connection  # noqa: E402

c = connection.cursor()
c.execute('PRAGMA journal_mode')
print('journal:', c.fetchone()[0])
c.execute('PRAGMA busy_timeout')
print('busy:', c.fetchone()[0])

for pid in (9, 19):
    print(f'--- profile {pid} tasks (latest 6) ---')
    qs = EvaluationRecomputeTask.objects.filter(
        profile_id=pid).order_by('-pk')[:6]
    for t in qs:
        print(f'  task {t.pk} {t.task_version} {t.state} att={t.attempts} '
              f'err={t.error_message!r} created={t.created_at} '
              f'completed={t.completed_at}')
    st = ProfileFeedState.objects.filter(profile_id=pid).first()
    if st:
        print(f'  feed state={st.state} pending={st.pending_reason!r} '
              f'active_gen={st.active_generation_id} updated={st.updated_at}')
    else:
        print('  feed state MISSING')

print('PHASE1_STATE_DONE')
