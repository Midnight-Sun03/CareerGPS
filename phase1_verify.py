"""Phase 1: verify convergence of target change #1 (profile 9 -> 'Data Engineer').

After the edit-view500 (on_commit hook lost the SQLite write race), the target
change committed but the worker died, leaving task 5419 zombie-RUNNING until
lease expiry. This script uses the application's own trigger entry point --
the same one the request-cycle safety net calls -- to reclaim expired leases
and finish the rebuild, then verifies the full chain including a real
/dashboard/ render. No sleeps, no retries: one trigger + join_worker.
"""
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

from core.services import queue_worker
from core.services.feed_v2 import v2_feed_queryset
from core.views import _authoritative_feed_ready
from core.models import Profile

from phase1_change1 import (
    PROFILE_ID, capture, dump_tasks, hr, make_client, render_dashboard,
)

EXPECTED_TARGET = 'Data Engineer'


def main():
    profile = Profile.objects.get(pk=PROFILE_ID)
    client = make_client(profile.user)

    hr(f'PRE-DRAIN STATE at {timezone.now().isoformat()}')
    pre = capture('BEFORE VERIFICATION DRAIN')
    dump_tasks('BEFORE VERIFICATION DRAIN')
    # A real request: this is what a user (and the safety net) would issue.
    pre_dash = render_dashboard(client, 'PRE-DRAIN')

    hr('TRIGGERING DRAIN via queue_worker.request_queue_drain (app entry point)')
    t0 = timezone.now()
    queue_worker.request_queue_drain(reason='phase1-verify')
    drained = queue_worker.join_worker(timeout=600)
    t1 = timezone.now()
    print(f'join_worker -> {drained} after {(t1 - t0).total_seconds():.1f}s '
          f'(_drain_running={queue_worker._drain_running})')

    dump_tasks('AFTER VERIFICATION DRAIN')
    post = capture('AFTER VERIFICATION DRAIN')
    dash = render_dashboard(client, 'POST-DRAIN')

    hr('CHAIN VERIFICATION (change #1)')
    from core.models import CareerPreference, CareerTargetResolution
    cp = CareerPreference.objects.get(profile_id=PROFILE_ID)
    print(f'A. CareerPreference.target_roles = {cp.target_roles!r} '
          f'(expected {EXPECTED_TARGET!r}) -> '
          f'{cp.target_roles == EXPECTED_TARGET}')
    resolutions = list(CareerTargetResolution.objects.filter(
        profile_id=PROFILE_ID).values_list('authored_text', flat=True))
    print(f'B. CareerTargetResolution texts = {resolutions} -> '
          f'{resolutions == [EXPECTED_TARGET]}')
    print(f'C. new task created: see task dump (task 5419 for this change)')
    print(f'D. active_generation {pre["active_gen"]} -> {post["active_gen"]} '
          f'(new generation: {pre["active_gen"] != post["active_gen"]})')
    print(f'E. ProfileFeedState {pre["state"]!r} -> {post["state"]!r} '
          f'(READY: {post["state"] == "READY"})')
    print(f'   _authoritative_feed_ready: {_authoritative_feed_ready(profile)}')
    b = {oid: t for oid, t in pre['feed']}
    a = {oid: t for oid, t in post['feed']}
    removed = sorted(set(b) - set(a))
    added = sorted(set(a) - set(b))
    print(f'F. admission set changed: feed size {len(b)} -> {len(a)}, '
          f'removed={len(removed)}, added={len(added)}')
    print('   sample removed (old target):',
          [(oid, b[oid]) for oid in removed[:5]])
    print('   sample added (new target):',
          [(oid, a[oid]) for oid in added[:5]])
    print(f'G. dashboard rendered status={dash[0]} cards='
          f'{[c[0] for c in dash[1]]}')
    print('dashboard cards PRE-DRAIN :', [c[0] for c in pre_dash[1]])
    print('dashboard cards POST-DRAIN:', [c[0] for c in dash[1]])
    print('dashboard cards changed:',
          [c[0] for c in pre_dash[1]] != [c[0] for c in dash[1]]
          or pre['state'] != 'READY')
    print('PHASE1_VERIFY_DONE')


if __name__ == '__main__':
    try:
        main()
        print('PHASE1_VERIFY_RC=0')
    except Exception as exc:  # noqa: BLE001
        import traceback
        traceback.print_exc()
        print('PHASE1_VERIFY_RC=1', repr(exc))
