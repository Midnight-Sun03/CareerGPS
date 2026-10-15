"""Event-driven provisional legacy matching bridge.

A target change gives two independent tasks.  The authoritative v2 task builds
an evidence-backed generation exactly as before; this module schedules a separate task that persists the legacy matcher's target-matched
ranking into `ProfileOpportunityEvaluation` so a page can read a prepared result
instead of running the matcher on the request path.

The two paths share nothing but the candidate's current intent/evidence identity
and the opportunity catalogue.  This module never touches
`ProfileEvaluationGeneration`, v2 admissions, `CareerTargetResolution`,
`CareerTargetIdentity`, or activation pointers, and it never determines whether
v2 may activate.

This is a bridge.  It does not reuse `materialize_profile()`:
that writes `ProfileEvaluationGeneration`, depends on the retired
`CareerTargetIdentity` pipeline, and calls `evaluate_profile_revision()` instead
of the matcher this bridge exists to persist.
"""
from __future__ import annotations

from dataclasses import asdict
from hashlib import sha256

import json

from django.conf import settings
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from core.models import (
    CareerPreference,
    EvaluationRecomputeTask,
    Profile,
    ProfileFeedState,
    ProfileOpportunityEvaluation,
)
from core.services.feed_queue import PIPELINE_VERSIONS, reclaim_expired_tasks
from core.services.feed_v2 import (
    authored_intent_fingerprint,
    current_evidence_fingerprint,
    has_authored_targets,
)
from core.services.notifications import notify

PROVISIONAL_TASK_VERSION = 'provisional-v1'
LIVE_STATES = ('PENDING', 'RUNNING', 'COMPLETE')
BAND_RANK = {'A': 1, 'B': 2, 'C': 3, 'E': 4, 'D': 5, 'F': 6}
CATEGORY_RANK = {'TARGET': 1, 'RELATED': 2, 'PROFILE_RELEVANT': 3}


def _hash(value):
    return sha256(json.dumps(
        value, sort_keys=True, separators=(',', ':'), default=str,
    ).encode()).hexdigest()


def provisional_dependency(profile):
    """The identity every provisional row is keyed on.

    Deliberately the same inputs the authoritative path uses -- authored target
    intent, candidate evidence, and the pipeline version set -- so a provisional
    result for an old target can never be read as the current one.
    """
    return _hash({
        'intent': authored_intent_fingerprint(profile),
        'evidence': current_evidence_fingerprint(profile),
        'versions': list(PIPELINE_VERSIONS),
    })


def _category_for(item):
    tier = item.get('career_direction_tier_priority', 0)
    if tier >= 2:
        return 'TARGET'
    if tier == 1:
        return 'RELATED'
    return 'PROFILE_RELEVANT'


def _serialise_criteria(item):
    criteria = item.get('criteria') or ()
    rows = []
    for criterion in criteria:
        try:
            value = asdict(criterion)
        except (TypeError, ValueError):
            value = {'label': getattr(criterion, 'label', '')}
        rows.append({key: str(val) for key, val in value.items()})
    return rows


def _candidate_scope(profile=None):
    """The shared read-only candidate universe: readiness plus senior gate.

    When a profile is given, scope further to indexed occupational candidates
    so provisional work no longer scans every READY job after a target change.
    """
    from core.models import Opportunity
    from core.services.title_gate import exclude_senior
    scope = exclude_senior(Opportunity.objects.professionally_ready(
        include_obvious_senior=False))
    if profile is None:
        return scope
    try:
        from core.models import CareerPreference
        from core.services.occupational_index import retrieve_candidates
        from core.services.occupational_resolution import (
            resolve_target_sources,
        )
        targets = list(profile.target_resolutions.all())
        if not targets and CareerPreference.objects.filter(
                profile=profile).exists():
            return scope
        sources = {t.pk: resolve_target_sources(t) for t in targets}
        candidate_ids = retrieve_candidates(
            targets, target_sources=sources, base_qs=scope)
        if candidate_ids:
            return scope.filter(
                active_clean_revision_id__in=candidate_ids)
    except Exception:
        pass
    return scope


def materialize_provisional(profile):
    """Persist the legacy matcher's ranking for the current target identity.

    Writes only `ProfileOpportunityEvaluation`.  Never touches the authoritative
    generation, v2 admissions, or activation.  Returns (row_count, dependency).

    Stale protection: the identity is recomputed immediately before writing.  If
    it moved while the matcher ran, every row is written STALE rather than
    CURRENT, so an A-build that lands late cannot become B's visible result.
    """
    profile = Profile.objects.get(pk=profile.pk)
    if not has_authored_targets(profile):
        # No authored target: nothing target-specific to persist, and the
        # truthful catalogue fallback stays the experience.
        ProfileOpportunityEvaluation.objects.filter(profile=profile).delete()
        return 0, None

    from core.matching import get_matched_opportunities

    dependency = provisional_dependency(profile)
    ranked = get_matched_opportunities(
        profile, queryset=_candidate_scope(profile), include_cv_tips=False)

    # The identity is re-read *after* the expensive work: if the candidate saved
    # a different target while this build ran, the rows are STALE not CURRENT.
    stale = provisional_dependency(profile) != dependency

    if not stale:
        ProfileOpportunityEvaluation.objects.filter(
            profile=profile, state='CURRENT').update(state='STALE')

    written = 0
    for item in ranked:
        opportunity = item['opportunity']
        revision = opportunity.active_clean_revision
        if revision is None:
            continue
        band = item.get('recommendation_band', 'F')
        category = _category_for(item)
        ProfileOpportunityEvaluation.objects.update_or_create(
            profile=profile,
            opportunity=opportunity,
            clean_revision=revision,
            defaults={
                'state': 'STALE' if stale else 'CURRENT',
                'dependency_hash': dependency,
                'discovery_category': category,
                'discovery_rank': CATEGORY_RANK[category],
                'band': band,
                'band_rank': BAND_RANK.get(band, 6),
                'professional_score': item.get('score', 0),
                'criterion_results': _serialise_criteria(item),
                'discovery_reasons': [
                    str(item.get('why_text') or '')],
            },
        )
        written += 1
    return written, dependency


def provisional_evaluations(profile, queryset):
    """Current-identity provisional rows, or an empty queryset.

    The freshness guard lives here: a row whose `dependency_hash` does not equal
    the candidate's *current* intent/evidence identity is never returned, so stale
    A rows physically left in the table are not readable for B.
    """
    dependency = provisional_dependency(profile)
    return queryset.filter(
        state='CURRENT',
        dependency_hash=dependency,
    )


# ------------------------------------------------------------- task layer
#
# A separate task version inside the existing EvaluationRecomputeTask queue:
# same table and same claim/lease/retry conventions, its own identity.  It is
# never claimed by drain_v2_queue (which filters task_version='feed-v2'), so the
# authoritative build can neither be blocked nor invalidated by provisional work,
# and a provisional failure cannot reach v2 state.

@transaction.atomic
def enqueue_provisional_rebuild(profile_id, reason):
    """Schedule one provisional build for the current target identity.

    Dedupe is keyed on (provisional version, profile, identity), so resaving an
    unchanged target stays quiet while a *changed* target supersedes the older
    pending provisional task instead of queueing behind it.
    """
    profile = Profile.objects.select_for_update().filter(pk=profile_id).first()
    if profile is None or not has_authored_targets(profile):
        return None
    dependency = provisional_dependency(profile)
    task_key = f'{PROVISIONAL_TASK_VERSION}:profile:{profile.pk}:{dependency}'
    existing = EvaluationRecomputeTask.objects.filter(
        task_key=task_key, state__in=LIVE_STATES).order_by('pk').first()
    if existing is not None:
        return existing
    now = timezone.now()
    EvaluationRecomputeTask.objects.filter(
        profile=profile, task_version=PROVISIONAL_TASK_VERSION,
        state='PENDING',
    ).exclude(task_key=task_key).update(
        state='SUPERSEDED', superseded_at=now,
        supersession_reason='A newer provisional target identity superseded this task.')
    return EvaluationRecomputeTask.objects.create(
        profile=profile, reason=reason,
        task_version=PROVISIONAL_TASK_VERSION, task_key=task_key,
        dependency_hash=dependency,
        max_attempts=settings.CAREERGPS_V2_TASK_MAX_ATTEMPTS,
        diagnostics={'change_kind': 'intent', 'path': 'provisional-legacy'})


def _reclaim_provisional(now):
    from datetime import timedelta
    expired = EvaluationRecomputeTask.objects.filter(
        task_version=PROVISIONAL_TASK_VERSION, state='RUNNING',
        lease_expires_at__lt=now)
    for task in expired:
        task.claim_token = ''
        task.claimed_at = None
        task.lease_expires_at = None
        task.error_message = 'Worker lease expired before completion.'
        if task.attempts >= task.max_attempts:
            task.state = 'FAILED'
            task.completed_at = now
        else:
            task.state = 'PENDING'
            task.available_at = now
        task.save()


def claim_provisional_tasks(*, limit=100, worker_id='management-command',
                            now=None):
    from datetime import timedelta
    from uuid import uuid4
    now = now or timezone.now()
    with transaction.atomic():
        _reclaim_provisional(now)
        ids = list(EvaluationRecomputeTask.objects.filter(
            task_version=PROVISIONAL_TASK_VERSION, state='PENDING',
            available_at__lte=now,
        ).order_by('available_at', 'pk').values_list('pk', flat=True)[:limit])
        lease = timedelta(seconds=settings.CAREERGPS_V2_TASK_LEASE_SECONDS)
        claimed = []
        for task_id in ids:
            token = uuid4().hex
            changed = EvaluationRecomputeTask.objects.filter(
                pk=task_id, state='PENDING', available_at__lte=now,
            ).update(state='RUNNING', claim_token=token, claimed_at=now,
                     lease_expires_at=now + lease,
                     diagnostics={'worker_id': worker_id})
            if changed:
                claimed.append(EvaluationRecomputeTask.objects.get(pk=task_id))
        return claimed


def _run_provisional(task):
    token = task.claim_token
    current = EvaluationRecomputeTask.objects.filter(
        pk=task.pk, state='RUNNING', claim_token=token).first()
    if current is None:
        return task
    try:
        count, dependency = materialize_provisional(current.profile)
        EvaluationRecomputeTask.objects.filter(
            pk=current.pk, state='RUNNING', claim_token=token,
        ).update(state='COMPLETE', completed_at=timezone.now(), claim_token='',
                 claimed_at=None, lease_expires_at=None, error_message='',
                 diagnostics={**current.diagnostics, 'evaluation_count': count,
                              'dependency_hash': dependency})
        return EvaluationRecomputeTask.objects.get(pk=current.pk)
    except Exception as exc:  # noqa: BLE001 - isolated by construction
        # Nothing in this module writes authoritative v2 state, so a provisional
        # failure cannot affect the v2 build or an ACTIVE generation.
        from datetime import timedelta as _td
        message = str(exc)[:4000]
        now = timezone.now()
        if current.attempts >= current.max_attempts:
            state, available = 'FAILED', current.available_at
        else:
            state = 'PENDING'
            available = now + _td(minutes=min(60, 2 ** current.attempts))
        EvaluationRecomputeTask.objects.filter(
            pk=current.pk, state='RUNNING', claim_token=token,
        ).update(state=state, available_at=available, error_message=message,
                 completed_at=(now if state == 'FAILED' else None),
                 claim_token='', claimed_at=None, lease_expires_at=None)
        return EvaluationRecomputeTask.objects.get(pk=current.pk)


def drain_provisional_queue(*, limit=100,
                            worker_id='management-command'):
    return [_run_provisional(task)
            for task in claim_provisional_tasks(limit=limit,
                                                worker_id=worker_id)]