"""Leased, retryable queue mechanics for v2 feed generations."""
from __future__ import annotations

from datetime import timedelta
from hashlib import sha256
import json
import uuid

from django.conf import settings
from django.db import transaction
from django.db.models import F
from django.utils import timezone

from core.models import (
    CareerTargetResolution,
    EvaluationRecomputeTask,
    OccupationSourceResolutionCandidate,
    Opportunity,
    Profile,
    ProfileFeedEvaluation,
    ProfileFeedState,
)
from core.professional_policies import (
    CANDIDATE_RELATIONSHIP_POLICY_VERSION, ESCO_RESOLVER_VERSION,
    FEED_GENERATION_VERSION, LABEL_ROUTE_POLICY_VERSION, OFO_RESOLVER_VERSION,
    RELATIONSHIP_AGGREGATION_VERSION,
)
from core.services.feed_v2 import (
    StaleGenerationBuild,
    authored_intent_fingerprint,
    build_profile_generation,
    current_evidence_fingerprint,
    has_authored_targets,
)
from core.services.occupational_resolution import (
    resolve_target_sources,
    resolve_vacancy_sources,
)
from core.services.title_gate import (
    READ_ONLY_SENIORITY_POLICY_VERSION, evaluate_revision,
)


TASK_VERSION = 'feed-v2'
# A task in one of these states still holds its logical dependency key.  The
# database enforces at most one live row per key, so a duplicate enqueue can
# never create concurrent work.  FAILED and SUPERSEDED are deliberately absent:
# they are terminal outcomes kept as audit history, and they must not block a
# later, legitimate re-enqueue of the same dependency.
LIVE_TASK_STATES = ('PENDING', 'RUNNING', 'COMPLETE')
PIPELINE_VERSIONS = [
    FEED_GENERATION_VERSION, READ_ONLY_SENIORITY_POLICY_VERSION,
    OFO_RESOLVER_VERSION, ESCO_RESOLVER_VERSION, LABEL_ROUTE_POLICY_VERSION,
    RELATIONSHIP_AGGREGATION_VERSION, CANDIDATE_RELATIONSHIP_POLICY_VERSION,
]


def _hash(value):
    return sha256(json.dumps(
        value, sort_keys=True, separators=(',', ':'), default=str,
    ).encode()).hexdigest()


def _profile_dependency(profile, causal_dependency=''):
    return _hash({
        'intent': authored_intent_fingerprint(profile),
        'evidence': current_evidence_fingerprint(profile),
        'causal_dependency': causal_dependency,
        'versions': PIPELINE_VERSIONS,
    })


def _mark_profile_transition(profile, reason, change_kind):
    state, _created = ProfileFeedState.objects.get_or_create(profile=profile)
    if not has_authored_targets(profile):
        next_state = 'NO_TARGETS'
    elif change_kind == 'intent':
        next_state = 'UPDATING_INTENT'
    elif change_kind == 'evidence':
        next_state = 'UPDATING_EVIDENCE'
    elif change_kind == 'policy':
        next_state = 'UPDATING_POLICY'
    else:
        next_state = 'UPDATING_CATALOGUE'
    # Two-phase resolving UX without a new state or migration: the intent
    # phase ("Resolving your career target...") is the stored UPDATING_INTENT
    # state; the candidate-retrieval phase ("Finding relevant
    # opportunities...") keeps that state and records its phase in the
    # transient pending reason.  Readers distinguish by prefix.
    if next_state == 'UPDATING_INTENT' and change_kind == 'intent':
        pending = f'intent_resolving:{reason}' if not str(
            reason).startswith('intent_') else reason
    else:
        pending = reason
    state.state = next_state
    state.pending_reason = pending
    state.last_error = ''
    state.save(update_fields=['state', 'pending_reason', 'last_error', 'updated_at'])


def _completed_product_is_current(profile, task):
    """True only when a COMPLETE task's product is still authoritative.

    ``COMPLETE`` alone never proves current validity: the generation a task
    built can be superseded by a later intent/evidence change (e.g.
    A → B → A leaves the historical A task COMPLETE while its generation is
    no longer the active one).  The product link written at completion
    (``diagnostics['generation_id']``) is compared against the profile's
    current ``active_generation``; a missing/None link fails open toward a
    fresh rebuild, never toward suppression.
    """
    generation_id = (task.diagnostics or {}).get('generation_id')
    if not generation_id:
        return False
    state = ProfileFeedState.objects.filter(
        profile_id=profile.pk).values('active_generation_id').first()
    if state is None or state['active_generation_id'] != generation_id:
        return False
    return True


@transaction.atomic
def enqueue_profile_rebuild(profile_id, reason, *, change_kind='catalogue',
                            causal_dependency=''):
    profile = Profile.objects.select_for_update().filter(pk=profile_id).first()
    if profile is None:
        return None
    dependency = _profile_dependency(profile, causal_dependency)
    task_key = f'{TASK_VERSION}:profile:{profile.pk}:{dependency}'
    existing = EvaluationRecomputeTask.objects.filter(
        task_key=task_key, state__in=LIVE_TASK_STATES).order_by('pk').first()
    if existing is not None:
        if existing.state in {'PENDING', 'RUNNING'}:
            _mark_profile_transition(profile, reason, change_kind)
            return existing
        # COMPLETE suppresses a new task only while its product is still the
        # active generation.  A stale completed task is retired to
        # SUPERSEDED (freeing the one-live-per-key constraint, exactly as a
        # FAILED row does) and falls through to create fresh work; otherwise
        # a reverted target would be served by an obsolete generation.
        if _completed_product_is_current(profile, existing):
            return existing
        EvaluationRecomputeTask.objects.filter(
            pk=existing.pk, state='COMPLETE',
        ).update(
            state='SUPERSEDED', superseded_at=timezone.now(),
            supersession_reason=(
                'Completed product is no longer the active generation; '
                'a fresh rebuild is required.'),
        )
    # No live task holds this key, so any FAILED or SUPERSEDED row is history
    # only and legitimate re-work needs a new task of its own.  The old rows
    # are left untouched as audit evidence.
    _mark_profile_transition(profile, reason, change_kind)
    now = timezone.now()
    EvaluationRecomputeTask.objects.filter(
        profile=profile, task_version=TASK_VERSION, state='PENDING',
    ).exclude(task_key=task_key).update(
        state='SUPERSEDED', superseded_at=now,
        supersession_reason='A newer complete-profile rebuild superseded this task.',
    )
    return EvaluationRecomputeTask.objects.create(
        profile=profile,
        reason=reason,
        task_version=TASK_VERSION,
        task_key=task_key,
        dependency_hash=dependency,
        max_attempts=settings.CAREERGPS_V2_TASK_MAX_ATTEMPTS,
        diagnostics={'change_kind': change_kind},
    )


@transaction.atomic
def enqueue_opportunity_rebuild(opportunity_id, reason):
    opportunity = Opportunity.objects.select_related('active_clean_revision').filter(
        pk=opportunity_id).first()
    if opportunity is None:
        return None
    revision = opportunity.active_clean_revision
    dependency = _hash({
        'opportunity_id': opportunity.pk,
        'revision_id': revision.pk if revision else None,
        'payload_hash': revision.payload_hash if revision else None,
        'versions': PIPELINE_VERSIONS,
    })
    task_key = f'{TASK_VERSION}:opportunity:{opportunity.pk}:{dependency}'
    existing = EvaluationRecomputeTask.objects.filter(
        task_key=task_key, state__in=LIVE_TASK_STATES).order_by('pk').first()
    if existing is not None:
        return existing
    now = timezone.now()
    EvaluationRecomputeTask.objects.filter(
        opportunity=opportunity, task_version=TASK_VERSION, state='PENDING',
    ).exclude(task_key=task_key).update(
        state='SUPERSEDED', superseded_at=now,
        supersession_reason='A newer clean revision superseded this task.',
    )
    return EvaluationRecomputeTask.objects.create(
        opportunity=opportunity,
        reason=reason,
        task_version=TASK_VERSION,
        task_key=task_key,
        dependency_hash=dependency,
        max_attempts=settings.CAREERGPS_V2_TASK_MAX_ATTEMPTS,
        diagnostics={'change_kind': 'catalogue'},
    )


@transaction.atomic
def reclaim_expired_tasks(now=None):
    now = now or timezone.now()
    expired = EvaluationRecomputeTask.objects.select_for_update().filter(
        task_version=TASK_VERSION,
        state='RUNNING',
        lease_expires_at__lt=now,
    )
    reclaimed = failed = 0
    for task in expired:
        task.claim_token = ''
        task.claimed_at = None
        task.lease_expires_at = None
        task.error_message = 'Worker lease expired before completion.'
        if task.attempts >= task.max_attempts:
            task.state = 'FAILED'
            task.completed_at = now
            failed += 1
        else:
            task.state = 'PENDING'
            task.available_at = now
            reclaimed += 1
        task.save()
    return reclaimed, failed


def claim_tasks(*, limit=100, worker_id='management-command', now=None):
    """Claim with compare-and-swap; safe even where SKIP LOCKED is unavailable."""
    now = now or timezone.now()
    reclaim_expired_tasks(now)
    candidate_ids = list(EvaluationRecomputeTask.objects.filter(
        task_version=TASK_VERSION,
        state='PENDING',
        available_at__lte=now,
    ).order_by('available_at', 'pk').values_list('pk', flat=True)[:max(1, limit)])
    claimed = []
    lease = timedelta(seconds=settings.CAREERGPS_V2_TASK_LEASE_SECONDS)
    for task_id in candidate_ids:
        token = uuid.uuid4().hex
        changed = EvaluationRecomputeTask.objects.filter(
            pk=task_id, state='PENDING', available_at__lte=now,
        ).update(
            state='RUNNING',
            attempts=F('attempts') + 1,
            claim_token=token,
            claimed_at=now,
            lease_expires_at=now + lease,
            diagnostics={'worker_id': worker_id},
        )
        if changed:
            claimed.append(EvaluationRecomputeTask.objects.get(pk=task_id))
    return claimed


def _opportunity_fanout(task):
    """Resolve one READY job once, then fan out to affected profiles.

    Overlapping profiles (shared occupational evidence or title variants) are
    assessed for this one revision and kept unless the existing fail-open
    ``proved_unrelated`` gate proves every authored target explicitly
    unrelated.  Profiles with no overlap keep the historical fail-open
    enqueue without assessment: their indexed build cannot admit this job
    either, so the rebuild stays cheap.  This preserves the pinned contract
    (every unproven profile rebuilds; proven-unrelated profiles do not)
    without re-scanning the job catalogue.
    """
    opportunity = Opportunity.objects.select_related(
        'active_clean_revision',
        'active_clean_revision__source_record',
        'active_clean_revision__raw_opportunity',
    ).get(pk=task.opportunity_id)
    revision = opportunity.active_clean_revision
    affected_profiles = set()
    if (revision
            and not evaluate_revision(revision).blocked
            and Opportunity.objects.professionally_ready(
                include_obvious_senior=True).filter(pk=opportunity.pk).exists()):
        vacancy = resolve_vacancy_sources(revision)
        if vacancy.state == 'READY':
            # Overlap detection needs each target's retained concepts. Targets
            # that have never been resolved (no build ran yet) are resolved
            # once here; in a maintained catalogue this set is empty.
            for target in CareerTargetResolution.objects.filter(
                    source_resolutions__isnull=True):
                resolve_target_sources(target)
            overlap = _overlap_profile_ids(revision)
            overlap.update(ProfileFeedEvaluation.objects.filter(
                opportunity=opportunity).values_list(
                    'generation__profile_id', flat=True))
            for profile_id in overlap:
                if not _profile_proven_unrelated_for(profile_id, revision):
                    affected_profiles.add(profile_id)
            # Fail-open enqueue for authored profiles outside the overlap
            # set: no stored evidence can prove them unrelated, and their
            # indexed build skips this job anyway.
            affected_profiles.update(
                CareerTargetResolution.objects.exclude(
                    profile_id__in=overlap,
                ).values_list('profile_id', flat=True).distinct())
    for profile_id in affected_profiles:
        enqueue_profile_rebuild(
            profile_id, task.reason, change_kind='catalogue',
            causal_dependency=task.dependency_hash)
    return sorted(affected_profiles)


def _profile_proven_unrelated_for(profile_id, revision):
    """True only when every authored target proves UNRELATED for this job."""
    from core.models import CareerPreference
    from core.services.feed_v2 import authored_target_roles, proved_unrelated
    from core.services.occupational_resolution import resolve_target_sources
    from core.services.relationship_aggregation import assess_target_vacancy
    preference = CareerPreference.objects.filter(profile_id=profile_id).first()
    if preference is None:
        return True
    authored_count = len(authored_target_roles(preference.profile_id))
    assessments = []
    for target in CareerTargetResolution.objects.filter(profile_id=profile_id):
        sources = resolve_target_sources(target)
        assessment = assess_target_vacancy(target, revision, sources)
        if assessment is not None:
            assessments.append(assessment)
    return proved_unrelated(assessments, authored_count)


def _overlap_profile_ids(revision):
    """Profiles whose targets share concepts/policy links/title evidence."""
    from core.services.occupational_index import expand_concept_ids
    concept_ids = {
        int(v) for v in OccupationSourceResolutionCandidate.objects.filter(
            resolution__opportunity_resolution__revision=revision,
        ).values_list('concept_id', flat=True) if v}
    profiles = set()
    if concept_ids:
        expanded = expand_concept_ids(
            concept_ids, include_non_admitting=True)
        target_ids = OccupationSourceResolutionCandidate.objects.filter(
            concept_id__in=expanded,
            resolution__target_resolution__isnull=False,
        ).values_list('resolution__target_resolution_id', flat=True)
        profiles.update(CareerTargetResolution.objects.filter(
            pk__in=target_ids).values_list('profile_id', flat=True))
    from core.services.relationship_aggregation import _title_tokens
    revision_tokens = set(_title_tokens(
        getattr(revision, 'cleaned_title', '')))
    if revision_tokens:
        for text in CareerTargetResolution.objects.values_list(
                'authored_text', flat=True).distinct().iterator(
                    chunk_size=500):
            if set(_title_tokens(text)) & revision_tokens:
                profiles.update(CareerTargetResolution.objects.filter(
                    authored_text=text).values_list(
                        'profile_id', flat=True).distinct())
    return profiles


def _retry_or_fail(task, token, exc):
    task = EvaluationRecomputeTask.objects.get(pk=task.pk)
    if task.state != 'RUNNING' or task.claim_token != token:
        return task
    now = timezone.now()
    error_message = str(exc)[:4000]
    if task.attempts >= task.max_attempts:
        state = 'FAILED'
        completed_at = now
        available_at = task.available_at
    else:
        state = 'PENDING'
        completed_at = None
        available_at = now + timedelta(minutes=min(60, 2 ** task.attempts))
    EvaluationRecomputeTask.objects.filter(
        pk=task.pk, state='RUNNING', claim_token=token,
    ).update(
        state=state, completed_at=completed_at, available_at=available_at,
        error_message=error_message, claim_token='', claimed_at=None,
        lease_expires_at=None,
    )
    return EvaluationRecomputeTask.objects.get(pk=task.pk)


def process_claimed_task(task):
    token = task.claim_token
    current = EvaluationRecomputeTask.objects.filter(
        pk=task.pk, state='RUNNING', claim_token=token).first()
    if current is None:
        return task
    try:
        if current.profile_id:
            generation, count = build_profile_generation(
                current.profile,
                reason=current.reason,
                build_token=token,
            )
            result = {
                'generation_id': generation.pk if generation else None,
                'evaluation_count': count,
            }
        else:
            result = {'affected_profile_ids': _opportunity_fanout(current)}
        completed = EvaluationRecomputeTask.objects.filter(
            pk=current.pk, state='RUNNING', claim_token=token).update(
                state='COMPLETE', completed_at=timezone.now(),
                claim_token='', claimed_at=None, lease_expires_at=None,
                error_message='', diagnostics={**current.diagnostics, **result},
            )
        if not completed:
            raise StaleGenerationBuild('Task lease changed before completion.')
        return EvaluationRecomputeTask.objects.get(pk=current.pk)
    except Exception as exc:
        return _retry_or_fail(current, token, exc)


def drain_v2_queue(*, limit=100, worker_id='management-command'):
    processed = []
    for task in claim_tasks(limit=limit, worker_id=worker_id):
        processed.append(process_claimed_task(task))
    return processed
