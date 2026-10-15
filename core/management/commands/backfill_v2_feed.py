import json

from django.core.management.base import BaseCommand
from django.db.models import Count

from core.models import (
    CareerPreference, EvaluationRecomputeTask, OpportunityTitleGateDecision,
    ProfileEvaluationGeneration, ProfileFeedEvaluation,
    ProfileOpportunityEvaluation,
    TargetOpportunityRelationshipAssessment,
)
from core.professional_policies import FEED_GENERATION_VERSION, UNRELATED_CLASS
from core.services.feed_queue import (
    TASK_VERSION, drain_v2_queue, enqueue_profile_rebuild,
)
from core.services.feed_v2 import v2_feed_queryset


class Command(BaseCommand):
    help = 'Build complete feed-v2 generations in shadow mode and report cohorts.'

    def add_arguments(self, parser):
        parser.add_argument('--batch-size', type=int, default=20)

    def handle(self, *args, **options):
        before = dict(EvaluationRecomputeTask.objects.values_list(
            'state').annotate(n=Count('pk')).order_by('state'))
        profile_ids = list(CareerPreference.objects.exclude(
            target_roles='').order_by('profile_id').values_list('profile_id', flat=True))
        for profile_id in profile_ids:
            enqueue_profile_rebuild(
                profile_id, 'block2_shadow_backfill', change_kind='catalogue')

        processed = []
        while EvaluationRecomputeTask.objects.filter(
                task_version=TASK_VERSION, state='PENDING').exists():
            batch = drain_v2_queue(
                limit=max(1, options['batch_size']), worker_id='block2-shadow-backfill')
            if not batch:
                break
            processed.extend(batch)

        cohorts = []
        for profile_id in profile_ids:
            generation = ProfileEvaluationGeneration.objects.filter(
                profile_id=profile_id, state='ACTIVE',
                generation_version=FEED_GENERATION_VERSION,
            ).first()
            rows = ProfileFeedEvaluation.objects.filter(generation=generation) if generation else (
                ProfileFeedEvaluation.objects.none())
            cohorts.append({
                'profile_id': profile_id,
                'generation_id': generation.pk if generation else None,
                'generation_state': generation.state if generation else 'MISSING',
                'evaluation_count': rows.count(),
                'classes': dict(rows.values_list('discovery_category').annotate(
                    n=Count('pk')).order_by('discovery_category')),
                'tiers': dict(rows.values_list('relationship_tier').annotate(
                    n=Count('pk')).order_by('relationship_tier')),
                'fit_bands': dict(rows.values_list('band').annotate(
                    n=Count('pk')).order_by('band')),
                'ranking_order': list(
                    v2_feed_queryset(generation.profile).values_list(
                        'opportunity_id', flat=True)
                    if generation else []),
                'unrelated_assessment_count': (
                    TargetOpportunityRelationshipAssessment.objects.filter(
                        target_resolution__profile_id=profile_id,
                        final_class=UNRELATED_CLASS).count()),
            })

        report = {
            'assessment_classes': dict(
                TargetOpportunityRelationshipAssessment.objects.values_list(
                    'final_class').annotate(n=Count('pk')).order_by('final_class')),
            'assessment_tiers': dict(
                TargetOpportunityRelationshipAssessment.objects.values_list(
                    'evidence_tier').annotate(n=Count('pk')).order_by('evidence_tier')),
            'unrelated_reason_codes': dict(
                TargetOpportunityRelationshipAssessment.objects.filter(
                    final_class=UNRELATED_CLASS).values_list(
                        'reason_code').annotate(n=Count('pk')).order_by('reason_code')),
            'title_gate': dict(OpportunityTitleGateDecision.objects.values_list(
                'decision').annotate(n=Count('pk')).order_by('decision')),
            'cohorts': cohorts,
            'legacy_profile_relevant_current': ProfileOpportunityEvaluation.objects.filter(
                state='CURRENT', discovery_category='PROFILE_RELEVANT').count(),
            'queue_before': before,
            'v2_tasks_processed': len(processed),
            'v2_queue_after': dict(EvaluationRecomputeTask.objects.filter(
                task_version=TASK_VERSION).values_list('state').annotate(
                    n=Count('pk')).order_by('state')),
        }
        self.stdout.write(json.dumps(report, indent=2, sort_keys=True, default=str))
