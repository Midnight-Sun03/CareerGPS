import json

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db.models import Count
from django.utils import timezone

from core.management.commands.verify_v2_feed import verification_report
from core.models import EvaluationRecomputeTask


OBSOLETE_REASONS = {
    'active_clean_revision_changed': (
        'Superseded by Block 1 resolution plus feed-v2 opportunity/generation work.'),
    'careerpreference_changed': (
        'Superseded by feed-v2 intent-aware complete-profile generation work.'),
}


class Command(BaseCommand):
    help = 'Classify legacy pending tasks and optionally supersede them after safe v2 cutover.'

    def add_arguments(self, parser):
        parser.add_argument('--supersede', action='store_true')

    def handle(self, *args, **options):
        pending = EvaluationRecomputeTask.objects.filter(
            task_version='legacy-v1', state='PENDING')
        before = dict(pending.values_list('reason').annotate(
            n=Count('pk')).order_by('reason'))
        classified = {
            reason: {'count': before.get(reason, 0), 'classification': explanation}
            for reason, explanation in OBSOLETE_REASONS.items()
        }
        changed = 0
        if options['supersede']:
            gates = verification_report()
            if settings.CAREERGPS_FEED_READ_VERSION != 'v2' or not gates['passed']:
                raise CommandError(
                    'Legacy tasks may be superseded only after v2 is the active reader '
                    'and all strict verification gates pass.')
            now = timezone.now()
            for reason, explanation in OBSOLETE_REASONS.items():
                changed += pending.filter(reason=reason).update(
                    state='SUPERSEDED', superseded_at=now,
                    supersession_reason=explanation)
        after = dict(EvaluationRecomputeTask.objects.filter(
            task_version='legacy-v1').values_list('state').annotate(
                n=Count('pk')).order_by('state'))
        self.stdout.write(json.dumps({
            'pending_before_by_reason': before,
            'classification': classified,
            'superseded_now': changed,
            'legacy_states_after': after,
        }, indent=2, sort_keys=True))
