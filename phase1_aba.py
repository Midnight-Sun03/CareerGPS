"""Phase 2: real-user A -> B -> A verification on profile 9 (dev DB).

Starts from the Phase-1 broken state: CareerPreference target = 'Accountant'
but active_generation 29 was built for 'Data Engineer' (the stale-COMPLETE
dedupe bug left the revert unserved).

Sequence, all through the real application path
(HTTP POST /profile/sections/career-preferences/<pk>/edit/):

  step 0: record broken baseline (intent A, feed B)
  step 1: POST -> 'Data Engineer' (B)
          Historical task 5419 is COMPLETE with product gen 29 == active,
          i.e. genuinely current -> legitimate suppression is EXPECTED:
          no new task, state stays READY, feed already corresponds to B.
  step 2: POST -> 'Accountant' (A again)
          Historical task 5411 is COMPLETE with product gen 26 while
          active is the B generation -> STALE -> must supersede 5411,
          create a fresh task, transition UPDATING_INTENT, build a new
          Accountant generation, activate it, reach READY, and the
          dashboard must show Accountant opportunities with no stale
          Data Engineer cards.

Evidence for every stage is printed; credentials are never printed.
"""
import io
import os
import re
import sys
import time

os.environ['DEBUG'] = 'True'
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'CareerGPS_Rebuild.settings')
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8',
                              errors='replace', line_buffering=True)

import django
django.setup()

from django.test import Client  # noqa: E402
from django.test.utils import setup_test_environment  # noqa: E402
from django.utils import timezone  # noqa: E402

from core.models import (  # noqa: E402
    CareerPreference, CareerTargetResolution, EvaluationRecomputeTask,
    Profile, ProfileEvaluationGeneration, ProfileFeedState,
)
from core.services import queue_worker  # noqa: E402
from core.services.feed_v2 import (  # noqa: E402
    authored_intent_fingerprint, v2_feed_queryset,
)
from core.views import _authoritative_feed_ready  # noqa: E402

setup_test_environment()

PROFILE_ID = 9


def hr(title):
    print()
    print('=' * 78)
    print(title)
    print('=' * 78)


def snapshot(label):
    hr(f'SNAPSHOT: {label}  ({timezone.now().isoformat()})')
    cp = CareerPreference.objects.get(profile_id=PROFILE_ID)
    state = ProfileFeedState.objects.filter(profile_id=PROFILE_ID).first()
    profile = Profile.objects.get(pk=PROFILE_ID)
    intent = authored_intent_fingerprint(profile)
    print(f'target_roles={cp.target_roles!r} updated_at={cp.updated_at}')
    if state:
        print(f'feed: state={state.state} pending={state.pending_reason!r} '
              f'active_generation_id={state.active_generation_id} '
              f'updated_at={state.updated_at}')
        if state.active_generation_id:
            g = state.active_generation
            print(f'active generation: id={g.pk} state={g.state} '
                  f'build_reason={g.build_reason!r} evals={g.evaluation_count} '
                  f'intent_hash={g.intent_dependency_hash[:16]}... '
                  f'matches_current_intent='
                  f'{g.intent_dependency_hash == intent}')
    print(f'current intent fingerprint: {intent[:16]}...')
    print(f'_authoritative_feed_ready: {_authoritative_feed_ready(profile)}')
    print('target resolutions: ' + '; '.join(
        f'{t.pk}:{t.authored_text!r}'
        for t in CareerTargetResolution.objects.filter(profile_id=PROFILE_ID)))
    return cp, state


def dump_tasks(label):
    hr(f'TASKS: {label}')
    for t in EvaluationRecomputeTask.objects.filter(
            profile_id=PROFILE_ID).order_by('-pk')[:6]:
        diag_gen = (t.diagnostics or {}).get('generation_id')
        print(f'  task={t.pk} {t.task_version} {t.state} att={t.attempts} '
              f'product_gen={diag_gen} err={t.error_message!r} '
              f'superseded={t.superseded_at} '
              f'reason={t.supersession_reason!r}')


def dump_generations(label):
    hr(f'GENERATIONS: {label}')
    for g in ProfileEvaluationGeneration.objects.filter(
            profile_id=PROFILE_ID).order_by('pk'):
        print(f'  gen={g.pk} state={g.state} reason={g.build_reason!r} '
              f'evals={g.evaluation_count} intent={g.intent_dependency_hash[:16]}... '
              f'activated={g.activated_at} superseded={g.superseded_at}')


def render_dashboard(client, label):
    resp = client.get('/dashboard/')
    content = resp.content.decode('utf-8', errors='replace')
    ids = sorted(set(re.findall(r'/opportunities/(\d+)/', content)))
    titles = re.findall(r'<h3[^>]*>([^<]{3,90})</h3>', content)
    print(f'DASHBOARD [{label}]: status={resp.status_code} '
          f'linked_ids={ids[:10]} h3_titles={titles[:8]}')
    return ids, titles


def post_target(client, cp, new_value):
    prefix = f'career-preferences-{cp.pk}'
    post = {
        f'{prefix}-target_roles': new_value,
        f'{prefix}-preferred_locations': cp.preferred_locations or '',
        f'{prefix}-work_arrangement': cp.work_arrangement or '',
        f'{prefix}-opportunity_kinds': cp.opportunity_kinds or '',
        f'{prefix}-industries': cp.industries or '',
        f'{prefix}-short_career_goal': cp.short_career_goal or '',
    }
    t0 = time.monotonic()
    resp = client.post(
        f'/profile/sections/career-preferences/{cp.pk}/edit/', post)
    print(f'POST target_roles -> {new_value!r}: status={resp.status_code} '
          f'location={resp.headers.get("Location", "")} '
          f'duration={time.monotonic() - t0:.2f}s')
    if resp.status_code != 302:
        print('POST body excerpt:', resp.content[:400])
    cp.refresh_from_db()
    print(f'CareerPreference now: target_roles={cp.target_roles!r} '
          f'(matches requested: {cp.target_roles == new_value})')
    return resp.status_code


def drain(label):
    t0 = time.monotonic()
    drained = queue_worker.join_worker(timeout=300)
    print(f'drain [{label}]: join_worker={drained} '
          f'after {time.monotonic() - t0:.1f}s '
          f'(running={queue_worker._drain_running})')


def main():
    queue_worker.set_synchronous_mode(False)
    profile = Profile.objects.get(pk=PROFILE_ID)
    user = profile.user

    # ---------------- step 0: broken baseline ----------------
    snapshot('BASELINE (expected broken: intent A / feed B)')
    dump_tasks('baseline')
    dump_generations('baseline')
    client = Client()
    client.force_login(user)
    base_ids, base_titles = render_dashboard(client, 'baseline')
    print(f'baseline feed size: {len(base_ids)}')

    # ---------------- step 1: A -> B (Data Engineer) ----------------
    hr('STEP 1: POST target -> Data Engineer')
    cp = CareerPreference.objects.get(profile_id=PROFILE_ID)
    if cp.target_roles == 'Data Engineer':
        print('target already Data Engineer; skipping POST')
    else:
        assert post_target(client, cp, 'Data Engineer') == 302
        drain('step 1')
    cp1, state1 = snapshot('AFTER STEP 1 (expect READY for Data Engineer)')
    dump_tasks('after step 1')
    b_ids, b_titles = render_dashboard(client, 'step1-B')
    step1_expected = (
        cp1.target_roles == 'Data Engineer'
        and state1 is not None and state1.state == 'READY'
        and state1.active_generation is not None
        and state1.active_generation.intent_dependency_hash
        == authored_intent_fingerprint(profile))
    print(f'STEP1 VALID (target B + READY + authoritative): {step1_expected}')

    # ---------------- step 2: B -> A (the bug case) ----------------
    hr('STEP 2: POST target -> Accountant (historical COMPLETE task exists)')
    pre_active = ProfileFeedState.objects.get(
        profile_id=PROFILE_ID).active_generation_id
    assert post_target(client, cp, 'Accountant') == 302

    # Immediate post-save evidence (before the drain finishes): the enqueue
    # must have superseded the stale COMPLETE row and created fresh work.
    st = ProfileFeedState.objects.get(profile_id=PROFILE_ID)
    print(f'immediately after POST: feed state={st.state!r} '
          f'pending={st.pending_reason!r}')
    fresh = EvaluationRecomputeTask.objects.filter(
        profile_id=PROFILE_ID, task_version='feed-v2',
        state='PENDING').order_by('-pk')
    fresh_list = list(fresh)
    print('fresh PENDING tasks: ' + ', '.join(
        f'{t.pk}(key={t.task_key[:40]}...)' for t in fresh_list))
    t5411 = EvaluationRecomputeTask.objects.filter(pk=5411).first()
    if t5411:
        print(f'historical task 5411: state={t5411.state!r} '
              f'supersession_reason={t5411.supersession_reason!r}')

    drain('step 2')
    cp2, state2 = snapshot('AFTER STEP 2 (expect READY for Accountant)')
    dump_tasks('after step 2')
    dump_generations('after step 2')
    a_ids, a_titles = render_dashboard(client, 'step2-A')

    # ---------------- verdicts ----------------
    hr('VERDICTS')
    v = {}
    v['S2a target is Accountant'] = cp2.target_roles == 'Accountant'
    v['S2b feed READY'] = state2 is not None and state2.state == 'READY'
    v['S2c pending cleared'] = (
        state2 is not None and state2.pending_reason == '')
    v['S2d active generation changed from B'] = (
        state2 is not None and state2.active_generation_id != pre_active)
    v['S2e active generation is ACTIVE'] = (
        state2 is not None and state2.active_generation is not None
        and state2.active_generation.state == 'ACTIVE')
    v['S2f active intent matches Accountant'] = (
        state2 is not None and state2.active_generation is not None
        and state2.active_generation.intent_dependency_hash
        == authored_intent_fingerprint(profile))
    v['S2g authoritative feed ready'] = _authoritative_feed_ready(profile)
    v['S2h stale COMPLETE row superseded (5411)'] = (
        t5411 is not None and t5411.state == 'SUPERSEDED')
    v['S2i fresh task was created after POST'] = len(fresh_list) >= 1
    texts = set(CareerTargetResolution.objects.filter(
        profile_id=PROFILE_ID).values_list('authored_text', flat=True))
    v['S2j resolutions are Accountant only'] = texts == {'Accountant'}
    v['S2k dashboard links changed from B phase'] = a_ids != b_ids
    for k in sorted(v):
        print(f'  [{"PASS" if v[k] else "FAIL"}] {k}')
    print(f'dashboard ids B phase: {b_ids}')
    print(f'dashboard ids A phase: {a_ids}')
    print(f'dashboard titles A phase: {a_titles}')
    ok = all(v.values())
    print(f'PHASE2_ABA_{"DONE" if ok else "FAILED"} rc={0 if ok else 1}')
    return 0 if ok else 1


if __name__ == '__main__':
    try:
        sys.exit(main())
    except Exception as exc:  # noqa: BLE001
        import traceback
        traceback.print_exc()
        print('PHASE2_ABA_FAILED rc=1', repr(exc))
        sys.exit(1)

