"""Compatibility-only legacy materializer and version-switched feed reader.

No production signal or default worker schedules legacy writes after Block 3.
The reader remains solely for an explicit, temporary rollback configuration.
"""
from __future__ import annotations

import hashlib
import json

from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from core.models import (
    CareerPreference, CareerTargetIdentity, CareerTargetResolution, Certification, Education, Language,
    CleanOpportunityCriterion, EvaluationRecomputeTask,
    OccupationDefiningCriterion, Opportunity, OpportunityCleanRevision,
    OpportunityOccupationIdentity, Profile, ProfileEvaluationGeneration,
    ProfileOpportunityEvaluation, ProfileSkill, ProjectCapability, WorkExperience,
    TaxonomyCrosswalk,
)
from core.professional_matching import (
    evaluate_profile_revision, profile_evidence_fingerprint, profile_has_signal,
)
from core.professional_policies import (
    BAND_POLICY_VERSION, MATCHING_POLICY_VERSION,
    PROFILE_RELEVANT_POLICY_VERSION, RELATIONSHIP_POLICY_VERSION,
)
from core.services.professional_resolution import (
    parse_target_roles, resolve_opportunity_revision, resolve_preference_targets,
    safe_title_target, taxonomy_fingerprint,
)
from core.taxonomies.normalization import normalize_term


def _hash(value):
    return hashlib.sha256(json.dumps(
        value, sort_keys=True, separators=(',', ':'), default=str).encode()).hexdigest()


def catalogue_fingerprint():
    rows = list(Opportunity.objects.professionally_ready().values_list(
        'pk', 'active_clean_revision_id', 'active_clean_revision__payload_hash',
        'active_clean_revision__seniority_label').order_by('pk'))
    return _hash(rows)


def profile_generation_dependency(profile):
    return _hash({
        'profile': profile_evidence_fingerprint(profile),
        'taxonomy': taxonomy_fingerprint(),
        'policies': [RELATIONSHIP_POLICY_VERSION, PROFILE_RELEVANT_POLICY_VERSION,
                     MATCHING_POLICY_VERSION, BAND_POLICY_VERSION],
    })


def enqueue_profile(profile_id, reason):
    def create():
        if not EvaluationRecomputeTask.objects.filter(
                profile_id=profile_id, state__in=('PENDING', 'RUNNING')).exists():
            EvaluationRecomputeTask.objects.create(profile_id=profile_id, reason=reason)
    transaction.on_commit(create)


def enqueue_opportunity(opportunity_id, reason):
    def create():
        if not EvaluationRecomputeTask.objects.filter(
                opportunity_id=opportunity_id, profile__isnull=True,
                state__in=('PENDING', 'RUNNING')).exists():
            EvaluationRecomputeTask.objects.create(
                opportunity_id=opportunity_id, reason=reason)
    transaction.on_commit(create)


def invalidate_profile(profile_id, reason):
    ProfileOpportunityEvaluation.objects.filter(
        profile_id=profile_id, state='CURRENT').update(state='STALE')
    enqueue_profile(profile_id, reason)


def invalidate_opportunity(opportunity_id, reason):
    ProfileOpportunityEvaluation.objects.filter(
        opportunity_id=opportunity_id, state='CURRENT').update(state='STALE')
    enqueue_opportunity(opportunity_id, reason)


def _safe_title_candidate_ids(preference):
    if not preference:
        return set()
    roles = parse_target_roles(preference.target_roles)
    revisions = OpportunityCleanRevision.objects.filter(
        opportunity__in=Opportunity.objects.professionally_ready(), status='active',
    ).only('pk', 'cleaned_title')
    # This remains a bounded candidate-index step in the worker, never a request
    # path; exact normalized titles use the indexed column first.
    normalized = {normalize_term(role) for role in roles}
    ids = set(revisions.filter(normalized_title__in=normalized).values_list('pk', flat=True))
    for revision in revisions.exclude(pk__in=ids):
        if any(safe_title_target(role, revision.cleaned_title) for role in roles):
            ids.add(revision.pk)
    return ids


def candidate_revision_ids(profile):
    preference = CareerPreference.objects.filter(profile=profile).first()
    targets = resolve_preference_targets(preference) if preference else []
    target_concepts = set(CareerTargetIdentity.objects.filter(
        resolution__in=targets).values_list('concept_id', flat=True))
    concept_candidates = set(target_concepts)
    if target_concepts:
        related_targets = TaxonomyCrosswalk.objects.filter(
            active=True, approval_status='approved_related',
            source_concept_id__in=target_concepts,
            target_concept__isnull=False,
        ).values_list('target_concept_id', flat=True)
        concept_candidates.update(related_targets)
    revision_ids = set(OpportunityOccupationIdentity.objects.filter(
        concept_id__in=concept_candidates,
        resolution__revision__status='active',
    ).values_list('resolution__revision_id', flat=True))
    revision_ids.update(_safe_title_candidate_ids(preference))

    # Profile-Relevant candidate generation is registry-gated and uses exact
    # compatible evidence indexes. It never scans every profile x vacancy pair.
    evidence_values = set(ProfileSkill.objects.filter(
        profile=profile, confirmed_by_user=True).values_list('normalized_name', flat=True))
    evidence_values.update(ProjectCapability.objects.filter(
        project__profile=profile, project__confirmed_by_user=True,
        confirmed_by_user=True).values_list('normalized_name', flat=True))
    evidence_values.update(normalize_term(value) for value in Education.objects.filter(
        profile=profile, confirmed_by_user=True).values_list('qualification', flat=True))
    evidence_values.update(normalize_term(value) for value in Certification.objects.filter(
        profile=profile, confirmed_by_user=True).values_list('title', flat=True))
    evidence_values.update(normalize_term(value) for value in Language.objects.filter(
        profile=profile, confirmed_by_user=True).values_list('language', flat=True))
    evidence_values.update(normalize_term(value) for value in WorkExperience.objects.filter(
        profile=profile, confirmed_by_user=True).values_list('title', flat=True))
    evidence_values.discard('')
    approved = OccupationDefiningCriterion.objects.filter(
        approved=True, policy_version=PROFILE_RELEVANT_POLICY_VERSION,
        normalized_value__in=evidence_values)
    approved_values = set(approved.values_list('normalized_value', flat=True))
    approved_concepts = set(approved.values_list('occupation_id', flat=True))
    if approved_values and approved_concepts:
        role_revision_ids = set(OpportunityOccupationIdentity.objects.filter(
            concept_id__in=approved_concepts,
            resolution__revision__status='active').values_list(
                'resolution__revision_id', flat=True))
        criterion_revision_ids = set(CleanOpportunityCriterion.objects.filter(
            revision_id__in=role_revision_ids,
            normalized_value__in=approved_values,
            requirement_level='REQUIRED',
            significance__in=('CORE', 'CRUCIAL')).values_list('revision_id', flat=True))
        revision_ids.update(criterion_revision_ids)
    return revision_ids


@transaction.atomic
def materialize_profile(profile):
    profile = Profile.objects.select_for_update().get(pk=profile.pk)
    dependency = profile_generation_dependency(profile)
    catalogue = catalogue_fingerprint()
    generation, _ = ProfileEvaluationGeneration.objects.get_or_create(
        profile=profile, dependency_hash=dependency,
        catalogue_fingerprint=catalogue)
    generation.state = 'RUNNING'
    generation.started_at = timezone.now()
    generation.failure_diagnostics = {}
    generation.save(update_fields=['state', 'started_at', 'failure_diagnostics'])
    ProfileOpportunityEvaluation.objects.filter(
        profile=profile, state='CURRENT').update(state='STALE')

    if not profile_has_signal(profile):
        generation.state = 'COMPLETE'
        generation.completed_at = timezone.now()
        generation.save(update_fields=['state', 'completed_at'])
        return generation, 0

    count = 0
    revisions = OpportunityCleanRevision.objects.filter(
        pk__in=candidate_revision_ids(profile),
        opportunity__in=Opportunity.objects.professionally_ready(
            include_obvious_senior=False),
    ).select_related('opportunity', 'source_record', 'raw_opportunity')
    for revision in revisions:
        values = evaluate_profile_revision(profile, revision)
        if not values:
            continue
        ProfileOpportunityEvaluation.objects.update_or_create(
            profile=profile, opportunity=revision.opportunity,
            defaults={key: value for key, value in values.items()
                      if key not in {'profile', 'opportunity'}},
        )
        count += 1
    generation.state = 'COMPLETE'
    generation.completed_at = timezone.now()
    generation.save(update_fields=['state', 'completed_at'])
    return generation, count


def _profiles_for_opportunity(opportunity):
    revision = opportunity.active_clean_revision
    if not revision:
        return set()
    resolution = resolve_opportunity_revision(revision)
    concept_ids = set(resolution.identities.values_list('concept_id', flat=True))
    profile_ids = set(CareerTargetIdentity.objects.filter(
        concept_id__in=concept_ids).values_list('resolution__profile_id', flat=True))
    criterion_values = set(revision.criteria.values_list('normalized_value', flat=True))
    profile_ids.update(ProfileSkill.objects.filter(
        confirmed_by_user=True, normalized_name__in=criterion_values).values_list(
            'profile_id', flat=True))
    profile_ids.update(ProjectCapability.objects.filter(
        project__confirmed_by_user=True, confirmed_by_user=True,
        normalized_name__in=criterion_values).values_list('project__profile_id', flat=True))
    profile_ids.update(ProfileOpportunityEvaluation.objects.filter(
        opportunity=opportunity).values_list('profile_id', flat=True))
    return profile_ids


@transaction.atomic
def process_task(task):
    task = EvaluationRecomputeTask.objects.select_for_update().get(pk=task.pk)
    if task.state != 'PENDING':
        return task
    task.state = 'RUNNING'
    task.attempts += 1
    task.save(update_fields=['state', 'attempts', 'updated_at'])
    try:
        if task.profile_id:
            materialize_profile(task.profile)
        elif task.opportunity_id:
            opportunity = Opportunity.objects.select_related('active_clean_revision').get(
                pk=task.opportunity_id)
            for profile_id in _profiles_for_opportunity(opportunity):
                enqueue_profile(profile_id, task.reason)
        task.state = 'COMPLETE'
        task.error_message = ''
    except Exception as exc:
        task.state = 'FAILED'
        task.error_message = str(exc)[:4000]
        task.save(update_fields=['state', 'error_message', 'updated_at'])
        return task
    task.save(update_fields=['state', 'error_message', 'updated_at'])
    return task


def legacy_professional_feed_queryset(profile):
    return ProfileOpportunityEvaluation.objects.filter(
        profile=profile,
        state='CURRENT',
        discovery_category__in=('TARGET', 'RELATED', 'PROFILE_RELEVANT'),
        opportunity__in=Opportunity.objects.professionally_ready(
            include_obvious_senior=False),
        clean_revision_id=models.F('opportunity__active_clean_revision_id'),
    ).select_related(
        'opportunity', 'clean_revision',
    ).order_by(
        'discovery_rank', 'band_rank', '-required_important_coverage',
        '-matched_important_category_count', '-positive_important_count',
        'major_gap_count', '-preferred_important_coverage', '-professional_score',
        '-clean_revision__posted_sort_at', '-opportunity__date_listed', 'opportunity_id',
    )


def professional_feed_queryset(profile):
    """Version-switched reader; legacy remains available for Block 3 rollback."""
    from django.conf import settings
    if settings.CAREERGPS_FEED_READ_VERSION == 'v2':
        from core.services.feed_v2 import v2_feed_queryset
        return v2_feed_queryset(profile)
    if settings.CAREERGPS_FEED_READ_VERSION == 'legacy':
        return legacy_professional_feed_queryset(profile)
    raise RuntimeError('CAREERGPS_FEED_READ_VERSION must be explicitly v2 or legacy.')


from django.db import models  # noqa: E402
