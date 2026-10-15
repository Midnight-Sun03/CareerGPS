from django.core.management.base import BaseCommand, CommandError

from core.models import EvaluationRecomputeTask, Profile
from core.services.feed_queue import (
    TASK_VERSION, drain_v2_queue, enqueue_profile_rebuild,
)
from core.services.provisional_feed import drain_provisional_queue


class Command(BaseCommand):
    help = 'Process persisted idempotent professional-evaluation recompute tasks.'

    def add_arguments(self, parser):
        parser.add_argument('--limit', type=int, default=100)
        parser.add_argument('--profile-id', action='append', type=int, dest='profile_ids')
        parser.add_argument('--all-profiles', action='store_true')
        parser.add_argument(
            '--legacy', action='store_true',
            help='Explicitly process legacy-v1 tasks instead of feed-v2 tasks.')
        parser.add_argument('--worker-id', default='management-command')
        parser.add_argument('--retry-failed', action='store_true')

    def handle(self, *args, **options):
        if not options['legacy']:
            if options['retry_failed']:
                EvaluationRecomputeTask.objects.filter(
                    task_version=TASK_VERSION, state='FAILED',
                    attempts__lt=models.F('max_attempts'),
                ).update(state='PENDING', available_at=timezone.now())
            if options['all_profiles']:
                for profile in Profile.objects.order_by('pk'):
                    enqueue_profile_rebuild(
                        profile.pk, 'all_profiles_v2_backfill', change_kind='catalogue')
            elif options.get('profile_ids'):
                for profile_id in options['profile_ids']:
                    enqueue_profile_rebuild(
                        profile_id, 'selected_profile_v2_rebuild', change_kind='catalogue')
            processed = drain_v2_queue(
                limit=max(1, options['limit']), worker_id=options['worker_id'])
            counts = {}
            for task in processed:
                counts[task.state] = counts.get(task.state, 0) + 1
            # The same worker drains the provisional bridge after the
            # authoritative work.  Separate task version, separate claims: a
            # provisional backlog never delays or invalidates v2, and one process
            # is enough because both live on the existing queue.
            provisional = drain_provisional_queue(
                limit=max(1, options['limit']),
                worker_id=options['worker_id'])
            provisional_counts = {}
            for task in provisional:
                provisional_counts[task.state] = (
                    provisional_counts.get(task.state, 0) + 1)
            self.stdout.write(self.style.SUCCESS(
                f'Processed {len(processed)} feed-v2 task(s): {counts}; '
                f'{len(provisional)} provisional task(s): '
                f'{provisional_counts}.'))
            return
        raise CommandError(
            'Legacy materialization writes are retired. The legacy reader is '
            'compatibility-only and can be enabled explicitly for rollback.')


from django.db import models  # noqa: E402
from django.utils import timezone  # noqa: E402
