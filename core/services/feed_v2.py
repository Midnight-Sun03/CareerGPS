"""Generation-owned v2 personalized feed admission, fit, ranking, and filters."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from hashlib import sha256
import json
import uuid

from django.db import transaction
from django.db.models import F, Q, FloatField
from django.db.models.fields.json import KeyTextTransform
from django.db.models.functions import Cast
from django.utils import timezone

from core.models import (
    CareerPreference,
    CareerTargetResolution,
    EvaluationRecomputeTask,
    Opportunity,
    OpportunityCleanRevision,
    Profile,
    ProfileEvaluationGeneration,
    ProfileFeedEvaluation,
    ProfileFeedState,
    TargetOpportunityRelationshipAssessment,
)
from core.professional_matching import (
    _fit_band,
    _requirement_units,
    confirmed_evidence,
    criterion_results,
    profile_evidence_fingerprint,
)
from core.professional_policies import (
    BAND_POLICY_VERSION,
    CANDIDATE_RELATIONSHIP_POLICY_VERSION,
    FEED_ADMISSION_VERSION,
    FEED_CONFIRMED_CATEGORIES,
    FEED_DISCOVERY_RANK,
    FEED_GENERATION_VERSION,
    FEED_RANKING_VERSION,
    FEED_SURVIVOR_CATEGORIES,
    RELATIONSHIP_AGGREGATION_VERSION,
    TITLE_MATCH_CLASS,
    UNRELATED_CLASS,
)
from core.services.occupational_index import retrieve_candidates
from core.services.occupational_resolution import (
    maintain_target_resolution_containers,
    resolve_target_sources,
    resolve_vacancy_sources,
)
from core.services.professional_resolution import parse_target_roles
from core.services.relationship_aggregation import (
    assess_target_vacancy, fresh_assessment_for, stored_pair_verdicts,
)
from core.services.title_gate import (
    READ_ONLY_SENIORITY_POLICY_VERSION, evaluate_revision, exclude_senior,
)


class StaleGenerationBuild(RuntimeError):
    pass


@dataclass(frozen=True)
class FeedTransition:
    state: str
    use_catalogue_fallback: bool
    fit_is_updating: bool
    message: str


def _hash(value):
    return sha256(json.dumps(
        value, sort_keys=True, separators=(',', ':'), default=str,
    ).encode()).hexdigest()


def authored_target_roles(profile):
    """The authored career-target set that owns the generation intent hash.

    This is the single completeness authority for multi-target decisions: every
    per-target verdict, fan-out, and survivor decision is measured against these
    authored roles and never against resolution containers or assessment rows.
    """
    preference = CareerPreference.objects.filter(profile=profile).only(
        'target_roles').first()
    return tuple(parse_target_roles(preference.target_roles) if preference else ())


def authored_intent_fingerprint(profile):
    roles = authored_target_roles(profile)
    return _hash({'roles': list(roles), 'authority': 'CareerPreference.target_roles'})


def current_evidence_fingerprint(profile):
    return profile_evidence_fingerprint(profile)


def has_authored_targets(profile):
    return bool(authored_target_roles(profile))


def _eligible_revisions():
    """Every professionally ready catalogue row, seniority applied in code.

    `include_obvious_senior=True` is deliberate: nothing is pre-filtered by a
    stored decision, and each row passes the same read-only
    ``evaluate_revision`` check immediately before admission.
    """
    return Opportunity.objects.professionally_ready(
        include_obvious_senior=True,
    ).select_related(
        'active_clean_revision',
        'active_clean_revision__source_record',
        'active_clean_revision__raw_opportunity',
    ).order_by('pk')


def _catalogue_and_assessments(profile):
    preference = CareerPreference.objects.filter(profile=profile).first()
    targets = maintain_target_resolution_containers(preference) if preference else []
    # Every revision below is compared against the same authored target text, so
    # the target's own per-source resolution is resolved once here and reused for
    # the whole pass instead of being recomputed for every revision.
    resolved_target_sources = {
        target.pk: resolve_target_sources(target) for target in targets}

    # Catalogue-maintenance pass, bounded to a genuine gap: READY revisions
    # that have NEVER been occupationally resolved (an ingestion/fanout/
    # backfill hole).  Persisted job evidence is catalogue intelligence and is
    # never re-resolved by a target change; this loop resolves only the missing
    # rows, once, so the concept index can see those jobs at all.  In a
    # maintained catalogue this set is empty and the pass makes zero writes.
    gap_ids = list(_eligible_revisions().filter(
        active_clean_revision__independent_occupation_resolution__isnull=True,
    ).values_list('active_clean_revision_id', flat=True))
    gap_resolved = 0
    if gap_ids:
        for revision in OpportunityCleanRevision.objects.filter(
                pk__in=gap_ids).order_by('pk'):
            if evaluate_revision(revision).blocked:
                continue
            resolve_vacancy_sources(revision)
            gap_resolved += 1

    revisions = []
    assessment_rows = []
    catalogue_evidence = []
    senior_excluded = {}
    reused_assessments = 0
    recomputed_assessments = 0
    candidate_ids = retrieve_candidates(
        targets, target_sources=resolved_target_sources,
        base_qs=_eligible_revisions())
    eligible = _eligible_revisions()
    total_eligible = eligible.count()
    scoped = (eligible.filter(active_clean_revision_id__in=candidate_ids)
              if candidate_ids else eligible.none())
    for opportunity in scoped:
        revision = opportunity.active_clean_revision
        gate = evaluate_revision(revision)
        if gate.blocked:
            # Read-only exclusion, decided here and now.  The computed reason
            # is carried into the generation diagnostics for disclosure; no
            # seniority decision is stored.
            senior_excluded[revision.pk] = gate.reason_code
            continue
        vacancy = resolve_vacancy_sources(revision, readonly=True)
        if vacancy is None:
            # Candidate-scoped healing: a job the index matched (title or
            # concept) with no stored occupational evidence yet gets resolved
            # exactly once here.  Unrelated READY jobs are never reached by
            # this loop, so target changes still cannot trigger a
            # full-catalogue resolution scan.
            vacancy = resolve_vacancy_sources(revision)
        if vacancy is None or getattr(vacancy, 'state', None) != 'READY':
            continue
        revisions.append(revision)
        source_dependencies = dict(vacancy.source_resolutions.values_list(
            'source', 'dependency_hash'))
        catalogue_evidence.append({
            'opportunity_id': opportunity.pk,
            'revision_id': revision.pk,
            'payload_hash': revision.payload_hash,
            'title_resolution': vacancy.dependency_hash,
            'sources': source_dependencies,
        })
        per_revision = []
        for target in targets:
            sources = resolved_target_sources[target.pk]
            assessment = fresh_assessment_for(
                target, revision, target_sources=sources)
            if assessment is None:
                assessment = assess_target_vacancy(
                    target, revision, sources)
                recomputed_assessments += 1
            else:
                reused_assessments += 1
            if assessment is not None:
                per_revision.append(assessment)
        assessment_rows.append((revision, per_revision))
    diagnostics_note = {
        'candidate_revision_count': len(candidate_ids),
        'eligible_revision_count': total_eligible,
        'skipped_unrelated_revision_count': max(
            0, total_eligible - len(candidate_ids)),
        'assessments_reused': reused_assessments,
        'assessments_recomputed': recomputed_assessments,
        'gap_resolved_revision_count': gap_resolved,
    }
    return (targets, revisions, assessment_rows, _hash(catalogue_evidence),
            senior_excluded, diagnostics_note)


def proved_unrelated(assessments, authored_target_count):
    """True only when every authored target carries explicit negative evidence.

    Completeness is measured against the authored-target set that owns the
    generation intent hash (``CareerPreference.target_roles`` order), never
    against resolution containers or the number of assessment rows.  A missing,
    stale, unbuilt, or newly added target assessment fails open and keeps the
    vacancy, because unrelatedness is never inferred from absent evidence.
    """
    if not authored_target_count:
        return False
    by_ordinal = {
        row.target_resolution.ordinal: row for row in assessments
        if row.target_resolution.ordinal < authored_target_count
    }
    if len(by_ordinal) != authored_target_count:
        return False
    return all(
        row.final_class == UNRELATED_CLASS for row in by_ordinal.values())


def _survivor(assessments, authored_target_count):
    """Return (chosen, accepted) for materialization, or None when excluded.

    A revision leaves the feed only when every authored target proves explicit
    negative evidence.  EXACT, RELATED, UNRESOLVED and TITLE_MATCH revisions
    are all survivors and all receive active evaluations on equal footing; the
    representative is the strongest survivor class in feed discovery order.
    """
    if proved_unrelated(assessments, authored_target_count):
        return None
    accepted = [
        row for row in assessments
        if row.final_class in FEED_SURVIVOR_CATEGORIES]
    if not accepted:
        return None
    chosen = min(accepted, key=lambda row: (
        FEED_DISCOVERY_RANK[row.final_class],
        row.tier_rank,
        row.target_resolution.ordinal,
        row.pk,
    ))
    return chosen, accepted


def _relationship_digest(assessment):
    """Every confirmed occupation link one target/job pair established.

    The card itself carries one category, because a card is one job.  These are
    the individual ESCO and OFO relationships behind it, so a job linked through
    several occupations can be shown that way instead of being flattened into a
    single sentence.  A stored finding row is an established relationship, so a
    retained title-only collision is never in this digest; it travels with the
    pair's own `title_only_evidence` instead.
    """
    return [{
        'source': row.source,
        'class': row.relationship_class,
        'relationship_type': row.relationship_type,
        # The concept ids identify the relationship itself.  Codes and labels
        # alone cannot: two different occupations in one taxonomy may share a
        # label and carry no code, and a reader must still see two
        # relationships rather than one merged line.
        'target_concept_id': row.target_concept_id,
        'job_concept_id': row.job_concept_id,
        'target_code': row.target_code,
        'target_label': row.target_label,
        'job_code': row.job_code,
        'job_label': row.job_label,
        'reason_code': row.reason_code,
    } for row in assessment.findings.all().only(
        'source', 'relationship_class', 'relationship_type', 'target_concept_id',
        'job_concept_id', 'target_code', 'target_label', 'job_code',
        'job_label', 'reason_code')]


def _title_only_digest(assessment):
    """The retained title-only evidence of one target/job pair.

    A policy-free same-concept label collision was compared and kept, so its
    evidence is preserved here with the tier that marks it as a possibility.  It
    is disclosed beside the confirmed relationships and never counted as one.
    """
    return list((assessment.diagnostics or {}).get('title_only_evidence') or [])


def _evaluation_values(profile, revision, chosen, accepted, evidence,
                       evidence_hash):
    rows = criterion_results(profile, revision, evidence)
    fit = _fit_band(_requirement_units(rows))
    # Dissent is recorded on the assessment that heard it: the other taxonomy
    # gave a different answer there.  There is no "conflict" verdict class, so
    # the disclosure is the per-assessment evidence rather than a class.
    #
    # A conflict is a confirmed relationship carrying a genuine explicit
    # negative finding from the other taxonomy.  Three things are deliberately
    # NOT conflicts, and none of them may appear here:
    #
    # * `UNRELATED` is a decision about this one target, not an objection to a
    #   relationship another target established.  An unrelated assessment and
    #   its exclusion reason stay on the assessment; the job is still checked
    #   against every other target the user entered, and it may still be
    #   admitted once through a target that did confirm it.
    # * A source with no approved policy produced no finding at all (absent
    #   evidence, never a denial), and an unresolved source produced none.
    # * Absence of evidence is not disagreement in any form.
    conflicts = [
        row.pk for row in revision.target_relationships.filter(
            target_resolution__profile=profile,
            final_class__in=FEED_CONFIRMED_CATEGORIES,
        ).only('pk', 'conflicting_evidence')
        if row.conflicting_evidence]
    reasons = [{
        'assessment_id': row.pk,
        'target_resolution_id': row.target_resolution_id,
        'authored_target': row.target_resolution.authored_text,
        'relationship_class': row.final_class,
        'relationship_tier': row.evidence_tier,
        'reason_code': row.reason_code,
        'relationship_count': row.relationship_count,
        'relationships': _relationship_digest(row),
        # Retained title-only evidence of the same pair, disclosed with its tier
        # and never counted as a relationship.
        'title_only_evidence': _title_only_digest(row),
        'title_only_evidence_count': len(_title_only_digest(row)),
        'policy_evidence': row.policy_evidence,
    } for row in accepted]
    dependency_hash = _hash({
        'revision': [revision.pk, revision.payload_hash],
        'assessment_dependencies': [
            (row.pk, row.dependency_hash) for row in accepted],
        'evidence': evidence_hash,
        'versions': [FEED_ADMISSION_VERSION, FEED_RANKING_VERSION,
                     BAND_POLICY_VERSION],
    })
    return {
        'opportunity': revision.opportunity,
        'clean_revision': revision,
        'relationship_assessment': chosen,
        'assessment_dependency_hash': chosen.dependency_hash,
        'dependency_hash': dependency_hash,
        'discovery_category': chosen.final_class,
        'discovery_rank': FEED_DISCOVERY_RANK[chosen.final_class],
        'relationship_tier': chosen.evidence_tier,
        'relationship_tier_rank': chosen.tier_rank,
        'contributing_target_assessment_ids': [row.pk for row in accepted],
        'conflicting_target_assessment_ids': conflicts,
        'admission_reasons': reasons,
        'band': fit['band'],
        'band_rank': {'A': 1, 'B': 2, 'C': 3, 'E': 4, 'D': 5, 'F': 6}[fit['band']],
        'required_important_coverage': fit['required_coverage'],
        'preferred_important_coverage': fit['preferred_coverage'],
        'matched_important_category_count': fit['matched_categories'],
        'found_important_category_count': fit['found_categories'],
        'positive_important_count': fit['positive_important'],
        'supporting_found_count': fit['supporting_found'],
        'strongest_important_match': fit['strongest'],
        'major_gap_count': fit['major_gaps'],
        'gate_gap_count': fit['gate_gaps'],
        'foundation_gap_count': fit['foundation_gaps'],
        'professional_score': fit['score'],
        'criterion_results': rows,
    }


def _purge_target_specific_state(preference):
    """Remove every target-specific occupational row for a cleared target set.

    Clearing the authored target must not leave the previous target's
    occupational intelligence behind.  The container is the root of every
    target-specific row: `CareerTargetIdentity`, the container's
    `OccupationSourceResolution` (and its candidates), every
    `TargetOpportunityRelationshipAssessment`, and through it every
    `TargetOpportunityRelationshipFinding` are all CASCADE from it.

    The one thing that is *not* CASCADE is
    `ProfileFeedEvaluation.relationship_assessment`, which is PROTECT so a stored
    admission can never be left pointing at a missing assessment.  Those rows are
    therefore released first.  A generation for a profile with no authored
    targets can never match the intent fingerprint, so any ACTIVE generation is
    superseded before its admissions are removed.

    Candidate-independent vacancy resolutions and assessments are untouched: they
    hang off `OpportunityTitleResolution`, not off a target.
    """
    if preference is None:
        return 0
    profile = preference.profile
    containers = list(CareerTargetResolution.objects.filter(
        profile=profile).values_list('pk', flat=True))
    if not containers:
        return 0
    now = timezone.now()
    # The ids are materialised once: the queryset would otherwise be re-evaluated
    # after the update below and no longer report the rows it just superseded.
    live_ids = list(
        ProfileEvaluationGeneration.objects.filter(
            profile=profile, state='ACTIVE').values_list('pk', flat=True))
    if live_ids:
        ProfileEvaluationGeneration.objects.filter(
            pk__in=live_ids,
        ).update(state='SUPERSEDED', superseded_at=now)
    ProfileFeedEvaluation.objects.filter(
        relationship_assessment__target_resolution_id__in=containers,
    ).delete()
    state, _created = ProfileFeedState.objects.get_or_create(profile=profile)
    if state.active_generation_id in set(live_ids):
        state.active_generation = None
        state.save(update_fields=['active_generation', 'updated_at'])
    removed, _detail = CareerTargetResolution.objects.filter(
        pk__in=containers).delete()
    return len(containers)


def _mark_failed(generation, token, error):
    summary = str(error)[:4000]
    ProfileEvaluationGeneration.objects.filter(
        pk=generation.pk, build_token=token, state='BUILDING',
    ).update(
        state='FAILED', completed_at=timezone.now(), error_summary=summary,
        failure_diagnostics={'exception': type(error).__name__, 'message': summary},
    )
    state, _created = ProfileFeedState.objects.get_or_create(profile=generation.profile)
    if isinstance(error, StaleGenerationBuild) and state.state in {
            'UPDATING_INTENT', 'UPDATING_EVIDENCE', 'UPDATING_CATALOGUE',
            'UPDATING_POLICY'}:
        pass
    elif state.active_generation_id:
        current_intent = authored_intent_fingerprint(generation.profile)
        state.state = (
            'UPDATING_INTENT'
            if state.active_generation.intent_dependency_hash != current_intent
            else 'FAILED'
        )
    else:
        state.state = 'FAILED'
    state.last_error = summary
    state.pending_reason = generation.build_reason
    state.save(update_fields=[
        'state', 'last_error', 'pending_reason', 'updated_at'])


@transaction.atomic
def _activate_generation(generation_id, token):
    generation = ProfileEvaluationGeneration.objects.select_for_update().get(
        pk=generation_id)
    state, _created = ProfileFeedState.objects.select_for_update().get_or_create(
        profile=generation.profile)
    if generation.state != 'BUILDING' or generation.build_token != token:
        raise StaleGenerationBuild('The generation build lease is no longer current.')
    if generation.intent_dependency_hash != authored_intent_fingerprint(generation.profile):
        raise StaleGenerationBuild('Career targets changed while the generation was building.')
    if generation.evidence_dependency_hash != current_evidence_fingerprint(generation.profile):
        raise StaleGenerationBuild('Profile evidence changed while the generation was building.')
    if EvaluationRecomputeTask.objects.filter(
            profile=generation.profile, task_version='feed-v2', state='PENDING',
            created_at__gt=generation.started_at).exists():
        raise StaleGenerationBuild('A newer feed dependency task exists for this profile.')

    # The catalogue check below must apply the same read-only seniority
    # exclusion the build applied, or a senior row that was never assessed
    # would look like a catalogue change and fail an honest build.
    current_revision_ids = sorted(
        exclude_senior(_eligible_revisions()).filter(
            active_clean_revision__independent_occupation_resolution__state='READY',
        ).values_list('active_clean_revision_id', flat=True))
    expected_revision_ids = generation.diagnostics.get('eligible_revision_ids', [])
    if current_revision_ids != expected_revision_ids:
        raise StaleGenerationBuild('The professional catalogue changed during generation build.')

    rows = list(generation.v2_evaluations.select_related(
        'opportunity', 'clean_revision', 'relationship_assessment',
        'clean_revision__independent_occupation_resolution',
    ))
    if len(rows) != generation.evaluation_count:
        raise RuntimeError('Generation evaluation count does not match persisted rows.')
    for row in rows:
        if row.discovery_category == UNRELATED_CLASS:
            raise RuntimeError('A proven-unrelated revision entered a v2 generation.')
        if row.discovery_category not in FEED_SURVIVOR_CATEGORIES:
            raise RuntimeError('A non-surviving category entered a v2 generation.')
        if row.clean_revision_id != row.opportunity.active_clean_revision_id:
            raise RuntimeError('A stale clean revision entered a v2 generation.')
        if row.relationship_assessment.final_class != row.discovery_category:
            raise RuntimeError('Evaluation class disagrees with its relationship assessment.')
        if row.assessment_dependency_hash != row.relationship_assessment.dependency_hash:
            raise RuntimeError('Evaluation references a stale relationship assessment.')
        if evaluate_revision(row.clean_revision).blocked:
            raise RuntimeError('A senior revision entered a v2 generation.')
        if row.clean_revision.independent_occupation_resolution.state != 'READY':
            raise RuntimeError('A vacancy without ready source resolution entered v2.')
        if row.relationship_assessment.revision_id != row.clean_revision_id:
            raise RuntimeError('Relationship assessment references another clean revision.')
        if row.relationship_assessment.target_resolution.profile_id != generation.profile_id:
            raise RuntimeError('Relationship assessment belongs to another profile.')
        if not Opportunity.objects.professionally_ready(
                include_obvious_senior=True).filter(pk=row.opportunity_id).exists():
            raise RuntimeError('An ineligible catalogue row entered a v2 generation.')

    now = timezone.now()
    ProfileEvaluationGeneration.objects.filter(
        profile=generation.profile, state='ACTIVE',
    ).exclude(pk=generation.pk).update(
        state='SUPERSEDED', superseded_at=now)
    generation.state = 'ACTIVE'
    generation.completed_at = now
    generation.activated_at = now
    generation.error_summary = ''
    generation.failure_diagnostics = {}
    generation.save(update_fields=[
        'state', 'completed_at', 'activated_at', 'error_summary',
        'failure_diagnostics', 'updated_at'])
    state.active_generation = generation
    state.state = 'READY'
    state.pending_reason = ''
    state.last_error = ''
    state.diagnostics = {
        'evaluation_count': generation.evaluation_count,
        'generation_version': generation.generation_version,
    }
    state.save()
    _notify_authoritative_ready(generation)
    return generation


def _notify_authoritative_ready(generation):
    """Tell a candidate the authoritative feed replaced their provisional view.

    Uses the existing in-app notification only: no email, no SMS, no polling and
    no forced page refresh -- the candidate discovers it on their next load.
    A notification failure must never break activation, so it is isolated.
    """
    try:
        from core.services.notifications import notify
        notify(
            user=generation.profile.user,
            category='system',
            event_key=f'v2-authoritative-ready:{generation.pk}',
            title='Your recommendations have been updated',
            message='Refresh to see your latest matches.',
            target_url='/dashboard/',
        )
    except Exception:  # noqa: BLE001 - notification must not break activation
        return


def build_profile_generation(profile, *, reason='manual_v2_build', build_token=None):
    """Build invisibly, validate completely, then atomically activate."""
    profile = Profile.objects.get(pk=profile.pk)
    intent_hash = authored_intent_fingerprint(profile)
    evidence_hash = current_evidence_fingerprint(profile)
    state, _created = ProfileFeedState.objects.get_or_create(profile=profile)
    if not has_authored_targets(profile):
        # Clearing the target must not leave the previous target's occupational
        # state behind for a later target to inherit, so it is purged here. This
        # has to happen before the early return, because the cleanup normally
        # lives inside `_catalogue_and_assessments`, which is never reached for a
        # profile with no authored targets.
        _purge_target_specific_state(
            CareerPreference.objects.filter(profile=profile).first())
        state.state = 'NO_TARGETS'
        state.pending_reason = reason
        state.last_error = ''
        state.save(update_fields=['state', 'pending_reason', 'last_error', 'updated_at'])
        return None, 0

    (targets, _revisions, assessment_rows, catalogue_hash, senior_excluded,
     index_note) = _catalogue_and_assessments(profile)
    assessment_fingerprint = _hash([
        (assessment.pk, assessment.dependency_hash)
        for _revision, assessments in assessment_rows
        for assessment in assessments
    ])
    dependency_hash = _hash({
        'intent': intent_hash,
        'evidence': evidence_hash,
        'catalogue': catalogue_hash,
        'assessments': assessment_fingerprint,
        'versions': [FEED_GENERATION_VERSION, FEED_ADMISSION_VERSION,
                     FEED_RANKING_VERSION, BAND_POLICY_VERSION],
    })
    current = ProfileEvaluationGeneration.objects.filter(
        profile=profile, generation_version=FEED_GENERATION_VERSION,
        dependency_hash=dependency_hash, catalogue_fingerprint=catalogue_hash,
        state='ACTIVE',
    ).first()
    if current:
        outstanding = EvaluationRecomputeTask.objects.filter(
            profile=profile, task_version='feed-v2',
            state__in=('PENDING', 'RUNNING'),
        )
        if build_token:
            outstanding = outstanding.exclude(claim_token=build_token)
        if not outstanding.exists():
            state.active_generation = current
            state.state = 'READY'
            state.pending_reason = ''
            state.last_error = ''
            state.diagnostics = {
                'evaluation_count': current.evaluation_count,
                'generation_version': current.generation_version,
            }
            state.save()
        return current, current.evaluation_count

    token = build_token or uuid.uuid4().hex
    generation, _created = ProfileEvaluationGeneration.objects.get_or_create(
        profile=profile,
        dependency_hash=dependency_hash,
        catalogue_fingerprint=catalogue_hash,
        defaults={'generation_version': FEED_GENERATION_VERSION},
    )
    generation.v2_evaluations.all().delete()
    generation.state = 'BUILDING'
    generation.generation_version = FEED_GENERATION_VERSION
    generation.build_reason = reason
    generation.intent_dependency_hash = intent_hash
    generation.evidence_dependency_hash = evidence_hash
    generation.build_token = token
    generation.evaluation_count = 0
    generation.error_summary = ''
    generation.failure_diagnostics = {}
    generation.started_at = timezone.now()
    generation.completed_at = None
    generation.activated_at = None
    generation.superseded_at = None
    generation.diagnostics = {
        'target_resolution_ids': [target.pk for target in targets],
        'assessment_fingerprint': assessment_fingerprint,
        'seniority_policy_version': READ_ONLY_SENIORITY_POLICY_VERSION,
        'senior_excluded_revision_reasons': dict(sorted(senior_excluded.items())),
        **index_note,
        'shadow_build': True,
    }
    generation.save()
    if state.state not in {
            'UPDATING_INTENT', 'UPDATING_EVIDENCE', 'UPDATING_CATALOGUE',
            'UPDATING_POLICY'}:
        state.state = (
            'UPDATING_INTENT'
            if state.active_generation_id and
            state.active_generation.intent_dependency_hash != intent_hash
            else 'BUILDING')
    # Phase two of the resolving UX: target identities are now resolved and
    # candidate retrieval/assessment is running.  Still UPDATING_INTENT so no
    # migration is needed; the phase lives in the pending reason.
    if state.state == 'UPDATING_INTENT':
        reason = ('intent_retrieving:candidate_lookup'
                  if str(reason).startswith('intent_resolving:')
                  else reason)
    state.pending_reason = reason
    state.last_error = ''
    state.save(update_fields=['state', 'pending_reason', 'last_error', 'updated_at'])

    try:
        evidence = confirmed_evidence(profile)
        evaluations = []
        authored_target_count = len(authored_target_roles(profile))
        for revision, assessments in assessment_rows:
            # Seniority was already applied read-only in
            # `_catalogue_and_assessments`, before any assessment was written.
            survivor = _survivor(assessments, authored_target_count)
            if not survivor:
                continue
            chosen, accepted = survivor
            values = _evaluation_values(
                profile, revision, chosen, accepted, evidence, evidence_hash)
            evaluations.append(ProfileFeedEvaluation(
                generation=generation, **values))
        ProfileFeedEvaluation.objects.bulk_create(evaluations, batch_size=200)
        generation.evaluation_count = len(evaluations)
        generation.diagnostics = {
            **generation.diagnostics,
            'assessed_revision_count': len(assessment_rows),
            # The activation invariant compares against the same read-only
            # query `_activate_generation` runs: senior-filtered READY
            # revisions with stored occupational evidence.  Assessment rows
            # only cover indexed candidates, so the invariant must come from
            # the catalogue query itself, not from the candidate loop.
            'eligible_revision_ids': sorted(
                exclude_senior(_eligible_revisions()).filter(
                    active_clean_revision__independent_occupation_resolution__state='READY',
                ).values_list('active_clean_revision_id', flat=True)),
            'admitted_evaluation_count': len(evaluations),
        }
        generation.save(update_fields=['evaluation_count', 'diagnostics', 'updated_at'])
        activated = _activate_generation(generation.pk, token)
        return activated, len(evaluations)
    except Exception as exc:
        _mark_failed(generation, token, exc)
        raise


def v2_feed_queryset(profile, categories=None):
    """Stored v2 admissions for a profile, newest complete generation only.

    `categories` narrows the returned admission classes, which is how the
    page separates confirmed occupational matches from the discovery area
    without ever re-deriving a relationship on the request path.

    Seniority is re-checked read-only at display time: the caller passes its
    already senior-filtered universe in ``opportunity__in``, and
    ``professionally_ready(include_obvious_senior=False)`` drops stored
    admissions whose cleaner label marks them senior.  A generation can never
    contain a senior row anyway, because admission validated it read-only.
    """
    categories = categories or FEED_SURVIVOR_CATEGORIES
    state = ProfileFeedState.objects.select_related('active_generation').filter(
        profile=profile).first()
    if not state or not state.active_generation_id:
        return ProfileFeedEvaluation.objects.none()
    generation = state.active_generation
    if generation.state != 'ACTIVE':
        return ProfileFeedEvaluation.objects.none()
    if generation.intent_dependency_hash != authored_intent_fingerprint(profile):
        return ProfileFeedEvaluation.objects.none()
    return ProfileFeedEvaluation.objects.filter(
        generation=generation,
        discovery_category__in=categories,
        opportunity__in=Opportunity.objects.professionally_ready(
            include_obvious_senior=False),
        clean_revision_id=F('opportunity__active_clean_revision_id'),
        clean_revision__independent_occupation_resolution__state='READY',
        assessment_dependency_hash=F('relationship_assessment__dependency_hash'),
        relationship_assessment__final_class=F('discovery_category'),
        relationship_assessment__revision_id=F('clean_revision_id'),
        relationship_assessment__target_resolution__profile=profile,
        relationship_assessment__aggregation_version=RELATIONSHIP_AGGREGATION_VERSION,
        relationship_assessment__relationship_policy_version=(
            CANDIDATE_RELATIONSHIP_POLICY_VERSION),
    ).select_related(
        'opportunity', 'clean_revision', 'relationship_assessment', 'generation',
    ).order_by(
        'discovery_rank', 'relationship_tier_rank', 'band_rank',
        '-required_important_coverage', '-matched_important_category_count',
        '-positive_important_count', 'major_gap_count',
        '-preferred_important_coverage', '-professional_score',
        '-clean_revision__posted_sort_at',
        F('clean_revision__closing_sort_at').asc(nulls_last=True),
        'opportunity_id',
    )


# How many unassessed catalogue jobs one request lists.  The count is always the
# full number, so a truncated list is disclosed rather than silent.
UNCERTAIN_CATALOGUE_LIMIT = 24


def proved_unrelated_opportunity_ids(profile, revision_ids):
    """Ids whose own fresh stored evidence proves UNRELATED for every target.

    This is `proved_unrelated` read back from storage, with the same
    completeness rule: every authored target must carry a still-current
    `UNRELATED` assessment for the revision that is active on the opportunity
    right now.  Anything less is absent evidence, so the job stays browseable
    and is never presented as occupationally unrelated.
    """
    authored_count = len(authored_target_roles(profile))
    if not authored_count or not revision_ids:
        return set()
    rows = TargetOpportunityRelationshipAssessment.objects.filter(
        target_resolution__profile=profile,
        revision_id__in=revision_ids,
        revision__opportunity__active_clean_revision_id=F('revision_id'),
        final_class=UNRELATED_CLASS,
        aggregation_version=RELATIONSHIP_AGGREGATION_VERSION,
        relationship_policy_version=CANDIDATE_RELATIONSHIP_POLICY_VERSION,
    ).values('revision_id', 'target_resolution__ordinal')
    by_revision = {}
    for row in rows:
        by_revision.setdefault(
            row['revision_id'], set()).add(row['target_resolution__ordinal'])
    candidates = {revision_id: ordinals for revision_id, ordinals
                  in by_revision.items()
                  if set(range(authored_count)).issubset(ordinals)}
    if not candidates:
        return set()
    verdicts = stored_pair_verdicts(
        profile, OpportunityCleanRevision.objects.filter(pk__in=candidates))
    excluded = set()
    for revision_id, ordinals in candidates.items():
        for ordinal in ordinals:
            verdict = verdicts.get((ordinal, revision_id))
            if verdict is None or verdict['class'] != UNRELATED_CLASS:
                break
        else:
            excluded.add(revision_id)
    return excluded


def unassessed_catalogue_queryset(profile, scope):
    """Eligible catalogue jobs that no fresh stored assessment covers.

    `scope` is the caller\'s already senior-filtered, already visibility-filtered
    universe, so the read-only seniority decision is re-applied by construction
    and no extra row is ever opened here.  This function reads stored data only:
    it never resolves a title, never compares a pair and never writes.

    A job leaves this set for one of three stored reasons and no other: the
    active generation already admitted it (confirmed or discovery), or fresh
    evidence proves it `UNRELATED` for every authored target.  Everything left
    is a genuinely undecided catalogue job, which stays browseable in the
    clearly separate uncertain area while the feed transition message explains
    why the assessed areas are thin.
    """
    admitted = set(
        v2_feed_queryset(profile).values_list('opportunity_id', flat=True))
    undecided = scope.exclude(pk__in=admitted)
    revision_ids = list(
        undecided.values_list('active_clean_revision_id', flat=True))
    excluded = proved_unrelated_opportunity_ids(profile, revision_ids)
    return undecided.exclude(
        active_clean_revision_id__in=excluded).order_by(
            '-active_clean_revision__posted_sort_at', 'pk')


def feed_transition(profile):
    state = ProfileFeedState.objects.select_related('active_generation').filter(
        profile=profile).first()
    if not has_authored_targets(profile):
        return FeedTransition('NO_TARGETS', True, False, '')
    if not state or not state.active_generation_id:
        return FeedTransition(
            state.state if state else 'BUILDING', False, False,
            'Your personalized recommendations are updating. Your target feed will return when this rebuild completes.',
        )
    if state.active_generation.intent_dependency_hash != authored_intent_fingerprint(profile):
        pending = state.pending_reason or ''
        if pending.startswith('intent_retrieving:'):
            return FeedTransition(
                'UPDATING_INTENT', False, False,
                'Finding relevant opportunities for your new target...',
            )
        return FeedTransition(
            'UPDATING_INTENT', False, False,
            'Resolving your career target...',
        )
    if state.state == 'UPDATING_POLICY':
        return FeedTransition(
            state.state, False, False,
            'Occupational policy evidence is updating. Your target feed is hidden until validation completes.',
        )
    evidence_stale = (
        state.active_generation.evidence_dependency_hash
        != current_evidence_fingerprint(profile))
    if state.state == 'UPDATING_CATALOGUE':
        return FeedTransition(
            state.state, False, evidence_stale,
            'One or more listings are updating; other validated recommendations remain available.',
        )
    return FeedTransition(
        state.state, False, evidence_stale,
        ('Your current-fit evidence is updating; occupational matches remain available.'
         if evidence_stale else ''),
    )


def apply_practical_filters(queryset, filters):
    """Visibility-only filters; no resolver or generation state is mutated."""
    location = str(filters.get('location') or '').strip()
    if location:
        queryset = queryset.filter(
            Q(clean_revision__location__city__icontains=location)
            | Q(clean_revision__location__region__icontains=location)
            | Q(clean_revision__location__country__icontains=location)
            | Q(clean_revision__location__raw__icontains=location))
    company = str(filters.get('company') or '').strip()
    if company:
        queryset = queryset.filter(clean_revision__company__icontains=company)
    work = str(filters.get('work_arrangement') or '').strip().upper()
    if work:
        queryset = queryset.filter(clean_revision__work_arrangement=work)
    employment = str(filters.get('employment_type') or '').strip().upper()
    if employment:
        queryset = queryset.filter(clean_revision__employment_type__icontains=employment)
    kind = str(filters.get('opportunity_kind') or '').strip().upper()
    if kind:
        queryset = queryset.filter(clean_revision__opportunity_kind=kind)
    salary_min = str(filters.get('salary_min') or '').strip()
    if salary_min:
        try:
            queryset = queryset.annotate(
                _salary_min_value=Cast(
                    KeyTextTransform('minimum', 'clean_revision__salary'),
                    FloatField()),
            ).filter(_salary_min_value__gte=float(salary_min))
        except ValueError:
            pass
    recency = str(filters.get('recency_days') or '').strip()
    if recency.isdigit():
        queryset = queryset.filter(
            clean_revision__posted_sort_at__gte=timezone.now() - timedelta(days=int(recency)))
    closing_days = str(filters.get('closing_days') or '').strip()
    if closing_days.isdigit():
        today = timezone.now()
        queryset = queryset.filter(
            clean_revision__closing_sort_at__gte=today,
            clean_revision__closing_sort_at__lte=today + timedelta(days=int(closing_days)))
    return queryset
