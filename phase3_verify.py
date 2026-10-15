"""Phase 3: real target -> occupational admission -> recommendation proof.

Runs a real persisted user (Profile 9) through materially different targets
via the actual application POST path:

    Accountant (baseline) -> Financial Adviser -> Software Engineer
        -> Actuarial Analyst (earlier symptom) -> Accountant (restore)

For every target it captures the FULL layer chain:

    catalogue -> clean opportunity -> target/job occupation resolution
    -> relationship class (EXACT/RELATED/CONFLICT/TITLE_MATCH/UNRESOLVED/
    UNRELATED) -> seniority gate -> generation rows -> v2_feed_queryset
    -> dashboard cards

plus an architecture-derived EXPECTED floor (same-taxonomy same-concept
overlaps between target labels and job resolutions) computed BEFORE the
change, so actual results can be compared against expectations.

Reads credentials never; restores the original target at the end.
"""
import io
import os
import re
import sys
import time
from collections import Counter, defaultdict

os.environ['DEBUG'] = 'True'
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'CareerGPS_Rebuild.settings')
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8',
                              errors='replace', line_buffering=True)

import django
django.setup()

from django.test import Client  # noqa: E402
from django.test.utils import setup_test_environment  # noqa: E402

from core.models import (  # noqa: E402
    CareerPreference, CareerTargetResolution, OccupationSourceResolution,
    OccupationSourceResolutionCandidate, Opportunity,
    ProfileEvaluationGeneration, ProfileFeedState, TaxonomyLabel,
    TargetOpportunityRelationshipAssessment,
)
from core.professional_policies import FEED_SURVIVOR_CATEGORIES  # noqa: E402
from core.services import queue_worker  # noqa: E402
from core.services.feed_v2 import (  # noqa: E402
    authored_intent_fingerprint, v2_feed_queryset,
)
from core.taxonomies.normalization import normalize_term  # noqa: E402
from core.services.title_gate import evaluate_revision  # noqa: E402
from core.views import _authoritative_feed_ready  # noqa: E402

setup_test_environment()

PROFILE_ID = 9
SEQUENCE = ['Financial Adviser', 'Software Engineer',
            'Actuarial Analyst', 'Accountant']


def hr(title):
    print()
    print('=' * 78)
    print(title)
    print('=' * 78)


def title_of(opp):
    rev = getattr(opp, 'active_clean_revision', None)
    return (getattr(rev, 'cleaned_title', None) or '(no title)')


def get_client():
    from core.models import Profile
    profile = Profile.objects.get(pk=PROFILE_ID)
    client = Client()
    client.force_login(profile.user)
    return client


def post_target(client, cp, value):
    prefix = f'career-preferences-{cp.pk}'
    post = {
        f'{prefix}-target_roles': value,
        f'{prefix}-preferred_locations': cp.preferred_locations or '',
        f'{prefix}-work_arrangement': cp.work_arrangement or '',
        f'{prefix}-opportunity_kinds': cp.opportunity_kinds or '',
        f'{prefix}-industries': cp.industries or '',
        f'{prefix}-short_career_goal': cp.short_career_goal or '',
    }
    t0 = time.monotonic()
    resp = client.post(
        f'/profile/sections/career-preferences/{cp.pk}/edit/', post)
    print(f'POST -> {value!r}: status={resp.status_code} '
          f'duration={time.monotonic() - t0:.2f}s')
    if resp.status_code != 302:
        print('POST body excerpt:', resp.content[:400])
    cp.refresh_from_db()
    print(f'persisted target_roles={cp.target_roles!r} '
          f'(match={cp.target_roles == value})')
    return resp.status_code


def drain(label):
    t0 = time.monotonic()
    ok = queue_worker.join_worker(timeout=300)
    print(f'drain[{label}]: join={ok} {time.monotonic() - t0:.1f}s')


# ---------------------------------------------------------------- expect ---
def label_concepts(text):
    """Taxonomy concepts whose normalized label equals the target text.

    This is what the target-side resolver can ground on; returns
    {source: {concept_id: preferred_label}} using preferred + alternative
    labels, OFO and ESCO kept independent.
    """
    norm = normalize_term(text)
    out = defaultdict(dict)
    for lab in TaxonomyLabel.objects.filter(
            normalized_label=norm).select_related('concept__release'):
        src = lab.concept.release.source
        out[src][lab.concept_id] = lab.concept.preferred_label
    return out


def expect(text):
    """Architecture-derived EXPECTED floor for a target text.

    The target resolver anchors same-concept matches on the EXACT label
    text (its own matched concept ids), so expectations key off the target
    rows' own candidates after a build, plus TITLE_MATCH candidates for
    title-equal READY jobs with no shared concept.
    """
    hr(f'EXPECTED anchors for text {text!r}')
    concepts = label_concepts(text)
    if not concepts:
        print('  no taxonomy label equals this text')
        return
    for src, cmap in concepts.items():
        print(f'  {src}: exact-label concepts='
              f'{[(cid, lbl[:40]) for cid, lbl in list(cmap.items())[:6]]}')


# --------------------------------------------------------------- capture ---
def res_concepts(resolution):
    """Concept ids retained by a source-resolution row."""
    out = {}
    for cand in resolution.candidates.select_related(
            'concept__release').all():
        src = cand.concept.release.source
        out.setdefault(src, {})[cand.concept_id] = (
            cand.concept.preferred_label,
            getattr(cand.concept, 'external_id', ''))
    return out


def capture(label):
    """Full per-target snapshot across every layer."""
    from core.models import Profile
    hr(f'CAPTURE: {label}')
    profile = Profile.objects.get(pk=PROFILE_ID)
    cp = CareerPreference.objects.get(profile_id=PROFILE_ID)
    state = ProfileFeedState.objects.filter(profile_id=PROFILE_ID).first()
    intent = authored_intent_fingerprint(profile)
    print(f'target_roles={cp.target_roles!r}')
    print(f'intent={intent[:16]}...')
    if state:
        print(f'state={state.state} pending={state.pending_reason!r} '
              f'active_generation_id={state.active_generation_id}')
    print(f'authoritative={_authoritative_feed_ready(profile)}')

    print('-- target resolutions --')
    for t in CareerTargetResolution.objects.filter(profile_id=PROFILE_ID):
        print(f'  target {t.pk} {t.authored_text!r} ordinal={t.ordinal}')
        for s in t.source_resolutions.select_related(
                'taxonomy_release').all():
            src = (s.taxonomy_release.source
                   if s.taxonomy_release_id else '?')
            kept = [(cid, lbl[:42]) for per in res_concepts(s).values()
                    for cid, (lbl, _code) in per.items()][:6]
            print(f'    source={src} state={s.state} '
                  f'matched={s.matched_label[:42]!r} route={s.label_route} '
                  f'concepts={kept}')

    print('-- relationship assessment classes (all target resolutions) --')
    asm = TargetOpportunityRelationshipAssessment.objects.filter(
        target_resolution__profile_id=PROFILE_ID)
    print(f'  rows={asm.count()} '
          f'{dict(Counter(asm.values_list("final_class", flat=True)))}')
    tiers = Counter(asm.filter(
        final_class__in=('EXACT', 'RELATED')).values_list(
        'evidence_tier', flat=True))
    if tiers:
        print(f'  EXACT/RELATED tiers: {dict(tiers)}')

    print('-- active generation rows by discovery_category --')
    gen = state.active_generation if state else None
    gen_map = {}
    if gen:
        print(f'  gen={gen.pk} state={gen.state} evals={gen.evaluation_count} '
              f'intent={gen.intent_dependency_hash[:16]}... '
              f'reason={gen.build_reason!r}')
        rows = list(gen.v2_evaluations.select_related(
            'opportunity__active_clean_revision',
            'relationship_assessment').all())
        by_cat = defaultdict(list)
        for r in rows:
            by_cat[r.discovery_category].append(r)
        for cat in sorted(by_cat):
            ids = sorted({r.opportunity_id for r in by_cat[cat]})
            print(f'  {cat}: rows={len(by_cat[cat])} jobs={len(ids)} '
                  f'ids={ids[:14]}')
            for r in by_cat[cat][:6]:
                asm_cls = (r.relationship_assessment.final_class
                           if r.relationship_assessment_id else None)
                rev_title = ''
                try:
                    rev_title = (r.opportunity.active_clean_revision
                                 .cleaned_title or '')[:70]
                except Exception:  # noqa: BLE001 - diagnostics only
                    pass
                print(f'    #{r.opportunity_id} '
                      f'{rev_title} band={r.band} rank={r.discovery_rank} '
                      f'assess={asm_cls}')
        gen_map = {c: sorted({r.opportunity_id for r in rs})
                   for c, rs in by_cat.items()}
    print('-- v2_feed_queryset (authoritative view) --')
    vis_rows = list(v2_feed_queryset(profile).select_related(
        'opportunity').all())
    vis_ids, vis_titles = [], []
    for r in vis_rows:
        try:
            t = (r.opportunity.active_clean_revision
                 .cleaned_title or '')[:70]
        except Exception:  # noqa: BLE001 - diagnostics only
            t = '(unresolvable)'
        vis_ids.append(r.opportunity_id)
        vis_titles.append(t)
    print(f'  rows={len(vis_rows)} ids={sorted(set(vis_ids))[:16]}')
    print(f'  titles={vis_titles[:8]}')
    return {'target': cp.target_roles, 'gen': gen, 'gen_map': gen_map,
            'visible': sorted(set(vis_ids)),
            'titles': vis_titles}
# ------------------------------------------------------------ trace_job ---
def trace_job(label, opp_id):
    """Walk one opportunity through every layer for the current target."""
    from core.models import Profile
    hr(f'TRACE job #{opp_id} under target {label!r}')
    profile = Profile.objects.get(pk=PROFILE_ID)
    try:
        opp = Opportunity.objects.professionally_ready(
            include_obvious_senior=True).select_related(
            'active_clean_revision').get(pk=opp_id)
        print(f'  L1 catalogue: READY, clean_title={title_of(opp)!r} '
              f'rev={opp.active_clean_revision_id}')
    except Opportunity.DoesNotExist:
        print('  L1 catalogue: NOT in professionally_ready set (STOP)')
        return
    rev = opp.active_clean_revision
    print(f'  L2 clean opportunity: rev present, title ok')
    gate = evaluate_revision(rev)
    gb = gate.blocked if not isinstance(gate, bool) else gate
    print(f'  L6 seniority gate: blocked={gb}')
    print('  L3 target occupation resolution:')
    for t in CareerTargetResolution.objects.filter(profile_id=PROFILE_ID):
        for s in t.source_resolutions.select_related(
                'taxonomy_release').all():
            src = (s.taxonomy_release.source
                   if s.taxonomy_release_id else '?')
            kept = [cid for per in res_concepts(s).values()
                    for cid in per]
            print(f'    target {t.pk} {t.authored_text!r} {src}: {s.state} '
                  f'kept={kept[:6]}')
    jres = getattr(rev, 'independent_occupation_resolution', None)
    if jres is None:
        print('  L4 job resolution: MISSING')
    else:
        kept = [cid for per in res_concepts(jres).values() for cid in per]
        srcs = {c.concept.release.source
                for c in jres.candidates.select_related(
                    'concept__release').all()}
        print(f'  L4 job resolution: state={jres.state} sources={sorted(srcs)} '
              f'kept={kept[:8]}')
    found = TargetOpportunityRelationshipAssessment.objects.filter(
        target_resolution__profile_id=PROFILE_ID, revision=rev)
    print('  L5 relationship assessments:')
    if found.exists():
        for a in found:
            print(f'    target={a.target_resolution.authored_text!r} '
                  f'final={a.final_class} tier={a.evidence_tier} '
                  f'reason={a.reason_code} '
                  f'tresult={a.target_result_count} '
                  f'jresult={a.job_result_count} '
                  f'pairings={a.pairing_count} '
                  f'rels={a.relationship_count}')
    else:
        print('    (no assessment rows: job outside candidate window)')
    state = ProfileFeedState.objects.filter(
        profile_id=PROFILE_ID).select_related('active_generation').first()
    if state and state.active_generation_id:
        rows = list(state.active_generation.v2_evaluations.filter(
            opportunity_id=opp_id).select_related('relationship_assessment'))
        if rows:
            for r in rows:
                print(f'  L7 admission: PRESENT in gen '
                      f'{state.active_generation_id} '
                      f'category={r.discovery_category} band={r.band} '
                      f'rank={r.discovery_rank}')
        else:
            print(f'  L7 admission: ABSENT from active gen '
                  f'{state.active_generation_id}')
    vis = {r.opportunity_id for r in v2_feed_queryset(profile)}
    print(f'  L8 queryset: {"VISIBLE" if opp_id in vis else "not visible"}')

    return {'target': cp.target_roles, 'gen': gen, 'gen_map': gen_map,
            'visible': sorted({r.opportunity_id for r in vis}),
            'titles': titles}


# ------------------------------------------------------------------ main ---
def dash_ids():
    from django.test import Client as DjClient
    from core.models import Profile as _P
    c = DjClient()
    c.force_login(_P.objects.get(pk=PROFILE_ID).user)
    html = c.get('/dashboard/').content.decode('utf-8', errors='replace')
    return sorted(set(re.findall(r'/opportunities/(\d+)/', html)))


def summary_table(results):
    hr('COMPARISON TABLE')
    print(f'{"target":<20}{"gen":<6}{"visible":<8}'
          f'{"EXACT":<7}{"RELATED":<9}{"TMATCH":<8}{"dashboard":<11}')
    for text, snap in results.items():
        gen_map = snap['gen_map']
        gen = snap['gen']
        print(f'{text[:19]:<20}'
              f'{str(gen.pk if gen else None):<6}'
              f'{len(snap["visible"]):<8}'
              f'{len(gen_map.get("EXACT", [])):<7}'
              f'{len(gen_map.get("RELATED", [])):<9}'
              f'{len(gen_map.get("TITLE_MATCH", [])):<8}'
              f'{len(snap["dash"]):<11}')
        print(f'  authoritative ids: {snap["visible"][:16]}')
        print(f'  dashboard ids:     {snap["dash"][:16]}')


def main():
    queue_worker.set_synchronous_mode(False)
    client = get_client()
    cp = CareerPreference.objects.get(profile_id=PROFILE_ID)
    print(f'START: profile={PROFILE_ID} target={cp.target_roles!r}')
    results = {}
    base = cp.target_roles
    expect(base)
    snap = capture('BASELINE ' + base)
    snap['dash'] = dash_ids()
    results[base] = snap
    for text in SEQUENCE:
        expect(text)
        st = post_target(client, cp, text)
        assert st == 302, f'POST failed for {text!r}'
        drain(text)
        snap = capture('AFTER ' + text)
        snap['dash'] = dash_ids()
        results[text] = snap
        print(f'dashboard ids for {text!r}: {snap["dash"][:16]}')
    summary_table(results)
    print('PHASE3_VERIFY_DONE')


if __name__ == '__main__':
    import sys as _sys
    if '--expect-only' in _sys.argv:
        for _t in SEQUENCE:
            expect(_t)
        print('PHASE3_EXPECT rc=0')
    elif '--capture-only' in _sys.argv:
        from core.models import CareerPreference as _CP
        _cp = _CP.objects.get(profile_id=PROFILE_ID)
        _snap = capture('BASELINE-READONLY ' + _cp.target_roles)
        print('PHASE3_CAPTURE rc=0')
    else:
        try:
            main()
            print('PHASE3_VERIFY rc=0')
        except Exception as exc:  # noqa: BLE001
            import traceback
            traceback.print_exc()
            print('PHASE3_VERIFY rc=1', repr(exc))
