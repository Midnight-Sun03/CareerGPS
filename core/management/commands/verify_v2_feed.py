import json

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db.models import Count, F, Q

from core.models import (
    CareerPreference, EvaluationRecomputeTask, OccupationSourceResolution,
    Opportunity, ProfileEvaluationGeneration, ProfileFeedEvaluation,
    ProfileFeedState,
    TargetOpportunityRelationshipAssessment,
)
from core.professional_policies import (
    CANDIDATE_RELATIONSHIP_POLICY_VERSION, ESCO_RESOLVER_VERSION,
    FEED_GENERATION_VERSION, FEED_SURVIVOR_CATEGORIES, LABEL_ROUTE_POLICY_VERSION,
    OFO_RESOLVER_VERSION, RELATIONSHIP_AGGREGATION_VERSION,
    SENIORITY_EXCLUDED_LABEL, UNRELATED_CLASS,
)
from core.services.feed_queue import TASK_VERSION
from core.services.feed_v2 import v2_feed_queryset
from core.services.professional_resolution import parse_target_roles
from core.services.title_gate import seniority_exclusions


def verification_report():
    # The senior decision is read-only: it is computed from the clean revision
    # immediately before admission and never stored, so no invariant may join a
    # stored gate row.  The universe is every professionally ready row, the
    # blocked subset is recomputed here with the very same ``evaluate_revision``
    # call the feed makes, and `admitted` is what a candidate can be shown.
    allowed = Opportunity.objects.professionally_ready(include_obvious_senior=True)
    blocked_revision_ids = list(seniority_exclusions(
        list(allowed.values_list('active_clean_revision_id', flat=True))))
    admitted = allowed.exclude(active_clean_revision_id__in=blocked_revision_ids)
    # Two distinct completeness facts, kept apart so a single number never
    # mixes them: the shared occupational resolution of the revision, and the
    # per-source resolution rows carrying the current resolver versions.  The
    # failure count is the distinct set of revisions failing either check.
    resolution_incomplete_ids = set(admitted.exclude(
        active_clean_revision__independent_occupation_resolution__state='READY',
    ).values_list('active_clean_revision_id', flat=True))
    source_rows_incomplete_ids = set()
    for opportunity in admitted:
        revision_id = opportunity.active_clean_revision_id
        source_rows = OccupationSourceResolution.objects.filter(
            opportunity_resolution__revision_id=revision_id,
            source__in=('ofo', 'esco'))
        count = source_rows.count()
        versions_current = (
            source_rows.filter(
                source='ofo', resolver_version=OFO_RESOLVER_VERSION,
                label_policy_version=LABEL_ROUTE_POLICY_VERSION).exists()
            and source_rows.filter(
                source='esco', resolver_version=ESCO_RESOLVER_VERSION,
                label_policy_version=LABEL_ROUTE_POLICY_VERSION).exists())
        if count != 2 or not versions_current:
            source_rows_incomplete_ids.add(revision_id)
    incomplete_vacancy_sources = len(
        resolution_incomplete_ids | source_rows_incomplete_ids)

    incomplete_targets = 0
    target_profiles = set()
    for preference in CareerPreference.objects.exclude(target_roles=''):
        target_profiles.add(preference.profile_id)
        targets = list(preference.target_resolutions.order_by('ordinal'))
        roles = parse_target_roles(preference.target_roles)
        if [target.authored_text for target in targets] != list(roles):
            incomplete_targets += max(len(roles), 1)
        for target in targets:
            rows = target.source_resolutions.filter(source__in=('ofo', 'esco'))
            versions_current = (
                rows.filter(source='ofo', resolver_version=OFO_RESOLVER_VERSION,
                            label_policy_version=LABEL_ROUTE_POLICY_VERSION).exists()
                and rows.filter(source='esco', resolver_version=ESCO_RESOLVER_VERSION,
                                label_policy_version=LABEL_ROUTE_POLICY_VERSION).exists())
            if rows.count() != 2 or not versions_current:
                incomplete_targets += 1

    target_ids = list(CareerPreference.objects.exclude(
        target_roles='').filter(target_resolutions__isnull=False).values_list(
            'target_resolutions__id', flat=True))
    expected_assessments = len(target_ids) * admitted.count()
    current_assessments = TargetOpportunityRelationshipAssessment.objects.filter(
        target_resolution_id__in=target_ids,
        revision_id__in=admitted.values_list('active_clean_revision_id', flat=True),
        aggregation_version=RELATIONSHIP_AGGREGATION_VERSION,
        relationship_policy_version=CANDIDATE_RELATIONSHIP_POLICY_VERSION,
    ).count()
    missing_current_assessments = max(0, expected_assessments - current_assessments)
    unrelated_assessments = TargetOpportunityRelationshipAssessment.objects.filter(
        revision_id__in=admitted.values_list('active_clean_revision_id', flat=True),
        aggregation_version=RELATIONSHIP_AGGREGATION_VERSION,
        relationship_policy_version=CANDIDATE_RELATIONSHIP_POLICY_VERSION,
        final_class=UNRELATED_CLASS).count()

    active_generations = ProfileEvaluationGeneration.objects.filter(
        generation_version=FEED_GENERATION_VERSION, state='ACTIVE')
    active_evaluations = ProfileFeedEvaluation.objects.filter(
        generation__in=active_generations)
    invalid_evaluations = active_evaluations.filter(
        ~Q(discovery_category__in=FEED_SURVIVOR_CATEGORIES)
        | Q(clean_revision__seniority_label=SENIORITY_EXCLUDED_LABEL)
        | ~Q(clean_revision_id=F('opportunity__active_clean_revision_id'))
        | ~Q(assessment_dependency_hash=F('relationship_assessment__dependency_hash'))
        | ~Q(discovery_category=F('relationship_assessment__final_class'))
        | ~Q(clean_revision_id=F('relationship_assessment__revision_id'))
        | ~Q(generation__profile_id=F(
            'relationship_assessment__target_resolution__profile_id'))
        | ~Q(relationship_assessment__aggregation_version=
             RELATIONSHIP_AGGREGATION_VERSION)
        | ~Q(relationship_assessment__relationship_policy_version=
             CANDIDATE_RELATIONSHIP_POLICY_VERSION)
        | ~Q(clean_revision__independent_occupation_resolution__state='READY')
    ).count()
    # A stored admission must never outlive the read-only decision that made
    # it.  If the policy changes, a previously admitted senior row is a failure
    # here rather than a stale stored gate row that keeps hiding a job.
    seniority_blocked_active_evaluations = active_evaluations.filter(
        clean_revision_id__in=blocked_revision_ids).count()
    partial_generations = 0
    active_profiles = set(active_generations.values_list('profile_id', flat=True))
    missing_active_profiles = sorted(target_profiles - active_profiles)
    non_ready_active_feed_states = list(ProfileFeedState.objects.filter(
        profile_id__in=active_profiles,
    ).exclude(state='READY').order_by('profile_id').values_list(
        'profile_id', 'state'))

    ordering_violations = []
    invisible_active_evaluations = 0
    state_pointer_mismatches = 0
    for generation in active_generations.select_related('profile'):
        feed_rows = list(v2_feed_queryset(generation.profile))
        class_tiers = [
            (row.discovery_rank, row.relationship_tier_rank)
            for row in feed_rows]
        if class_tiers != sorted(class_tiers):
            ordering_violations.append(generation.pk)
        persisted_count = generation.v2_evaluations.count()
        if generation.evaluation_count != persisted_count:
            partial_generations += 1
        invisible_active_evaluations += persisted_count - len(feed_rows)
        if not ProfileFeedState.objects.filter(
                profile_id=generation.profile_id,
                active_generation_id=generation.pk).exists():
            state_pointer_mismatches += 1

    queue_unhealthy = EvaluationRecomputeTask.objects.filter(
        task_version=TASK_VERSION,
        state__in=('PENDING', 'RUNNING', 'FAILED'),
    ).count()
    failures = {
        'incomplete_allowed_vacancy_sources': incomplete_vacancy_sources,
        'seniority_blocked_active_evaluations': seniority_blocked_active_evaluations,
        'incomplete_target_sources': incomplete_targets,
        'missing_current_assessments': missing_current_assessments,
        'invalid_v2_evaluations': invalid_evaluations,
        'partial_active_generations': partial_generations,
        'invisible_active_evaluations': invisible_active_evaluations,
        'active_state_pointer_mismatches': state_pointer_mismatches,
        'non_ready_active_feed_states': non_ready_active_feed_states,
        'building_current_generations': ProfileEvaluationGeneration.objects.filter(
            generation_version=FEED_GENERATION_VERSION,
            state='BUILDING').count(),
        'failed_current_generations': ProfileEvaluationGeneration.objects.filter(
            generation_version=FEED_GENERATION_VERSION,
            state='FAILED').count(),
        'missing_active_target_profiles': missing_active_profiles,
        'ranking_order_violations': ordering_violations,
        'unhealthy_v2_queue_tasks': queue_unhealthy,
        'worker_schedule_not_confirmed': not settings.CAREERGPS_V2_WORKER_SCHEDULED,
    }
    return {
        'passed': not any(bool(value) for value in failures.values()),
        'failures': failures,
        'feed_read_version': settings.CAREERGPS_FEED_READ_VERSION,
        'active_generation_count': len(active_profiles),
        'explicitly_unrelated_assessments': unrelated_assessments,
        'vacancy_resolution_incomplete': len(resolution_incomplete_ids),
        'vacancy_source_rows_incomplete': len(source_rows_incomplete_ids),
        'v2_evaluation_count': active_evaluations.count(),
        'v2_categories': dict(active_evaluations.values_list(
            'discovery_category').annotate(n=Count('pk')).order_by('discovery_category')),
    }


class Command(BaseCommand):
    help = 'Verify strict feed-v2 activation gates without changing the read flag.'

    def add_arguments(self, parser):
        parser.add_argument('--strict', action='store_true')

    def handle(self, *args, **options):
        report = verification_report()
        self.stdout.write(json.dumps(report, indent=2, sort_keys=True, default=str))
        if options['strict'] and not report['passed']:
            raise CommandError('feed-v2 activation gates failed')
