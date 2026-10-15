"""Read-only recovery check after phase1_change1 failure."""
import io
import os
import sys

os.environ['DEBUG'] = 'True'
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'CareerGPS_Rebuild.settings')
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8',
                              errors='replace', line_buffering=True)
import django
django.setup()

from django.utils import timezone
from core.models import (
    CareerPreference, EvaluationRecomputeTask, ProfileFeedState,
    ProfileEvaluationGeneration,
)

print('now:', timezone.now())

cp = CareerPreference.objects.get(profile_id=9)
print('profile9 target_roles:', repr(cp.target_roles), 'updated_at:', cp.updated_at)

st = ProfileFeedState.objects.filter(profile_id=9).first()
print('profile9 state:', st.state, 'active_gen:', st.active_generation_id,
      'pending:', st.pending_reason, 'updated:', st.updated_at)

st19 = ProfileFeedState.objects.filter(profile_id=19).first()
print('profile19 state:', st19.state, 'active_gen:', st19.active_generation_id,
      'pending:', st19.pending_reason, 'updated:', st19.updated_at)

print('non-terminal tasks (all profiles):')
for t in EvaluationRecomputeTask.objects.filter(
        state__in=('PENDING', 'RUNNING')).order_by('-pk')[:10]:
    print(f'  task={t.pk} profile={t.profile_id} ver={t.task_version} state={t.state}'
          f' attempts={t.attempts}/{t.max_attempts} avail={t.available_at}'
          f' lease={t.lease_expires_at} err={t.error_message[:60]!r}')

print('profile9 task newest:')
for t in EvaluationRecomputeTask.objects.filter(profile_id=9).order_by('-pk')[:4]:
    print(f'  task={t.pk} ver={t.task_version} state={t.state} attempts={t.attempts}'
          f' created={t.created_at} err={t.error_message[:60]!r}')

print('profile9 generations newest:')
for g in ProfileEvaluationGeneration.objects.filter(profile_id=9).order_by('-pk')[:4]:
    print(f'  gen={g.pk} state={g.state} reason={g.build_reason!r} evals={g.evaluation_count}'
          f' created={g.created_at}')

print('RECOVERY_CHECK_DONE')
