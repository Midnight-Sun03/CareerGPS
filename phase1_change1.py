"""Phase 1: real-user target change #1.

Selected user: profile 9 (crisfri@gmail.com), current target 'Accountant',
feed READY on active_generation 26.

Path exercised (the real application path):
  HTTP POST /profile/sections/career-preferences/<pk>/edit/
    -> edit_profile_section (core/views.py)
    -> CareerPreferenceForm.save()
    -> CareerPreference.save() (transaction.atomic)
    -> post_save -> professional_profile_input_changed (core/services/integrity.py)
    -> transaction.on_commit -> enqueue_profile_rebuild + _trigger_queue_drain
    -> queue_worker.request_queue_drain -> background drain thread (production mode)
    -> claim_tasks -> process_claimed_task -> build_profile_generation
    -> _activate_generation -> ProfileFeedState READY

Reads go through the same services the dashboard view uses:
  _authoritative_feed_ready + v2_feed_queryset, plus a real rendered
  /dashboard/ request via the Django test client (session login, no password).
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

from django.contrib.sessions.backends.db import SessionStore
from django.test import Client
from django.test.utils import setup_test_environment
from django.utils import timezone

from core.models import (
    CareerPreference, CareerTargetResolution, EvaluationRecomputeTask,
    ProfileEvaluationGeneration, ProfileFeedState,
)
from core.services import queue_worker
from core.services.feed_v2 import (
    authored_intent_fingerprint, v2_feed_queryset,
)
from core.views import _authoritative_feed_ready

setup_test_environment()

PROFILE_ID = 9
NEW_TARGET = 'Accountant'


def hr(title):
    print()
    print('=' * 78)
    print(title)
    print('=' * 78)


def title_of(opp):
    rev = getattr(opp, 'active_clean_revision', None)
    return getattr(rev, 'cleaned_title', None) or '(no title)'


def capture(label):
    """One full snapshot of the lifecycle state for the profile."""
    hr(f'SNAPSHOT: {label}  ({timezone.now().isoformat()})')
    cp = CareerPreference.objects.get(profile_id=PROFILE_ID)
    state = ProfileFeedState.objects.filter(profile_id=PROFILE_ID).first()
    print(f'CareerPreference: pk={cp.pk} target_roles={cp.target_roles!r} '
          f'source={cp.source} updated_at={cp.updated_at}')
    if state:
        print(f'ProfileFeedState: id={state.pk} state={state.state} '
              f'pending={state.pending_reason!r} last_error={state.last_error!r} '
              f'active_generation_id={state.active_generation_id} '
              f'updated_at={state.updated_at}')
    else:
        print('ProfileFeedState: MISSING')

    from core.models import Profile
    profile = Profile.objects.get(pk=PROFILE_ID)
    intent = authored_intent_fingerprint(profile)
    print(f'authored_intent_fingerprint: {intent[:16]}...')
    if state and state.active_generation_id:
        g = state.active_generation
        match = g.intent_dependency_hash == intent
        print(f'active generation: id={g.pk} state={g.state} '
              f'build_reason={g.build_reason!r} eval_count={g.evaluation_count} '
              f'activated_at={g.activated_at} intent_hash={g.intent_dependency_hash[:16]}... '
              f'INTENT_MATCHES_CURRENT={match}')
    print(f'_authoritative_feed_ready: {_authoritative_feed_ready(profile)}')

    print('CareerTargetResolution rows:')
    for r in CareerTargetResolution.objects.filter(profile_id=PROFILE_ID):
        sources = dict(r.source_resolutions.values_list('source', 'state'))
        print(f'  id={r.pk} authored_text={r.authored_text!r} sources={sources}')

    print('All generations (oldest first):')
    for g in ProfileEvaluationGeneration.objects.filter(
            profile_id=PROFILE_ID).order_by('pk'):
        print(f'  gen={g.pk} state={g.state} reason={g.build_reason!r} '
              f'evals={g.evaluation_count} intent={str(g.intent_dependency_hash)[:12]} '
              f'created={g.created_at} activated={g.activated_at} superseded={g.superseded_at}')

    print('v2_feed_queryset (the dashboard source):')
    rows = list(v2_feed_queryset(profile).select_related(
        'opportunity__active_clean_revision').values_list(
        'opportunity_id', 'opportunity__active_clean_revision__cleaned_title'))
    print(f'  count={len(rows)}')
    for oid, t in rows:
        print(f'  #{oid} {t}')
    return {'target': cp.target_roles, 'state': state.state if state else None,
            'active_gen': state.active_generation_id if state else None,
            'intent': intent, 'feed': rows}


def make_client(user):
    session = SessionStore()
    session['_auth_user_id'] = str(user.pk)
    session['_auth_user_backend'] = 'django.contrib.auth.backends.ModelBackend'
    session['_auth_user_hash'] = user.get_session_auth_hash()
    session.create()
    client = Client()
    client.cookies['sessionid'] = session.session_key
    return client


def render_dashboard(client, label):
    resp = client.get('/dashboard/')
    ctx = resp.context
    cards = []
    for card in (ctx.get('top_matches') or []):
        opp = card.get('opportunity')
        cards.append((opp.pk, title_of(opp)))
    print(f'DASHBOARD {label}: status={resp.status_code} '
          f'signal={ctx.get("has_professional_signal")} matches={len(cards)}')
    for oid, t in cards:
        print(f'  #{oid} {t}')
    return resp.status_code, cards


def dump_tasks(label):
    print(f'EvaluationRecomputeTask rows ({label}):')
    for t in EvaluationRecomputeTask.objects.filter(
            profile_id=PROFILE_ID).order_by('-pk')[:10]:
        print(f'  task={t.pk} ver={t.task_version} state={t.state} attempts={t.attempts}'
              f' err={t.error_message[:80]!r} avail={t.available_at} done={t.completed_at}'
              f' diag={str(t.diagnostics)[:120]} key={t.task_key[:48]}...')


def dump_profile19():
    hr('REAL-WORLD STUCK USER INSPECTION: profile 19 (read-only)')
    st = ProfileFeedState.objects.filter(profile_id=19).first()
    if st:
        print(f'profile 19 state={st.state} pending={st.pending_reason!r} '
              f'active_gen={st.active_generation_id} updated={st.updated_at}')
    for t in EvaluationRecomputeTask.objects.filter(
            profile_id=19).order_by('-pk')[:6]:
        print(f'  task={t.pk} ver={t.task_version} state={t.state} '
              f'attempts={t.attempts}/{t.max_attempts}'
              f' err={t.error_message[:120]!r} avail={t.available_at} '
              f'done={t.completed_at} created={t.created_at}')
    print('  generations for profile 19:',
          list(ProfileEvaluationGeneration.objects.filter(
              profile_id=19).values_list('pk', 'state')))


def main():
    from core.models import Profile
    cp = CareerPreference.objects.get(profile_id=PROFILE_ID)
    profile = Profile.objects.get(pk=PROFILE_ID)
    user = profile.user
    prefix = f'career-preferences-{cp.pk}'

    hr(f'SELECTED USER: profile={PROFILE_ID} (username: {user.username!r})')

    before = capture('BEFORE target change')
    dump_tasks('BEFORE')
    client = make_client(user)
    before_cards = render_dashboard(client, 'BEFORE')
    dump_profile19()

    # ---------------- target change through the real view ----------------
    hr(f'MUTATION: POST edit view target_roles {before["target"]!r} -> {NEW_TARGET!r}')
    post = {
        f'{prefix}-target_roles': NEW_TARGET,
        f'{prefix}-preferred_locations': cp.preferred_locations or '',
        f'{prefix}-work_arrangement': cp.work_arrangement or '',
        f'{prefix}-opportunity_kinds': cp.opportunity_kinds or '',
        f'{prefix}-industries': cp.industries or '',
        f'{prefix}-short_career_goal': cp.short_career_goal or '',
    }
    t0 = timezone.now()
    resp = client.post(
        f'/profile/sections/career-preferences/{cp.pk}/edit/', post)
    t1 = timezone.now()
    print(f'POST status={resp.status_code} '
          f'Location={resp.headers.get("Location", "")} '
          f'duration={(t1 - t0).total_seconds():.2f}s')
    if resp.status_code != 302:
        print('POST body excerpt:', resp.content[:500])
        return

    cp.refresh_from_db()
    print(f'CareerPreference after POST: target_roles={cp.target_roles!r} '
          f'updated_at={cp.updated_at}')
    print(f'POST==new target: {cp.target_roles == NEW_TARGET}')

    # wait for the production background drain (sanctioned mechanism)
    hr('WAITING FOR BACKGROUND DRAIN (queue_worker.join_worker)')
    t2 = timezone.now()
    drained = queue_worker.join_worker(timeout=300)
    t3 = timezone.now()
    print(f'join_worker -> {drained} after {(t3 - t2).total_seconds():.1f}s '
          f'(_drain_running={queue_worker._drain_running})')

    dump_tasks('AFTER change 1')
    after = capture('AFTER target change')
    after_cards = render_dashboard(client, 'AFTER')

    hr('BEFORE vs AFTER COMPARISON (change 1)')
    print(f'target: {before["target"]!r} -> {after["target"]!r}')
    print(f'state:  {before["state"]!r} -> {after["state"]!r}')
    print(f'active_generation: {before["active_gen"]} -> {after["active_gen"]}')
    b = {oid: t for oid, t in before['feed']}
    a = {oid: t for oid, t in after['feed']}
    removed = sorted(set(b) - set(a))
    added = sorted(set(a) - set(b))
    print(f'feed size: {len(b)} -> {len(a)}; '
          f'removed={len(removed)} added={len(added)} same={len(set(b) & set(a))}')
    print('removed opportunities:')
    for oid in removed[:20]:
        print(f'  - #{oid} {b[oid]}')
    print('added opportunities:')
    for oid in added[:20]:
        print(f'  + #{oid} {a[oid]}')
    print('dashboard cards BEFORE:', before_cards[1])
    print('dashboard cards AFTER :', after_cards[1])
    print('dashboard cards equal:',
          [c[0] for c in before_cards[1]] == [c[0] for c in after_cards[1]])


if __name__ == '__main__':
    try:
        main()
        print('PHASE1_CHANGE1_DONE rc=0')
    except Exception as exc:  # noqa: BLE001
        import traceback
        traceback.print_exc()
        print('PHASE1_CHANGE1_DONE rc=1', repr(exc))



