"""Phase 1: run ONE production drain against the real dev DB and report.

Purpose: prove the WAL + busy-timeout hardening lets a real drain converge
the stuck profiles (9 and 19) without OperationalError("database is locked").
Uses the exact production mechanism: queue_worker.request_queue_drain()
then queue_worker.join_worker() (background thread, synchronous-mode OFF).
"""
import io
import os
import sys
import time

os.environ['DEBUG'] = 'True'
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'CareerGPS_Rebuild.settings')
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8',
                              errors='replace', line_buffering=True)

import django
django.setup()

from core.models import EvaluationRecomputeTask, ProfileFeedState  # noqa: E402
from core.services import queue_worker  # noqa: E402

print('sync mode:', queue_worker._synchronous_mode)
queue_worker.set_synchronous_mode(False)

t0 = time.monotonic()
queue_worker.request_queue_drain(reason='phase1-lock-verification')
drained = queue_worker.join_worker(timeout=180)
print(f'join_worker={drained} after {time.monotonic() - t0:.1f}s '
      f'(running={queue_worker._drain_running})')

for pid in (9, 19):
    print(f'--- profile {pid} ---')
    for t in EvaluationRecomputeTask.objects.filter(
            profile_id=pid).order_by('-pk')[:4]:
        print(f'  task {t.pk} {t.task_version} {t.state} att={t.attempts} '
              f'err={t.error_message!r} completed={t.completed_at}')
    st = ProfileFeedState.objects.filter(profile_id=pid).first()
    if st:
        print(f'  feed state={st.state} pending={st.pending_reason!r} '
              f'active_gen={st.active_generation_id} updated={st.updated_at}')

print('PHASE1_DRAIN_DONE')
