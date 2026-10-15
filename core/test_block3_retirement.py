from io import StringIO

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, override_settings

from core.models import EvaluationRecomputeTask, Opportunity


@override_settings(
    CAREERGPS_FEED_READ_VERSION='v2',
    CAREERGPS_V2_WORKER_SCHEDULED=True,
)
class LegacyMaterializationRetirementTests(TestCase):
    def test_obsolete_queue_rows_are_auditably_superseded(self):
        opportunity = Opportunity.objects.create(
            title='Historical queue row', company='Example', is_active=False)
        task = EvaluationRecomputeTask.objects.create(
            opportunity=opportunity, reason='active_clean_revision_changed',
            task_version='legacy-v1')
        output = StringIO()
        call_command('reconcile_legacy_evaluation_tasks', '--supersede', stdout=output)
        task.refresh_from_db()
        self.assertEqual(task.state, 'SUPERSEDED')
        self.assertIsNotNone(task.superseded_at)
        self.assertIn('feed-v2', task.supersession_reason)
        self.assertIn('"superseded_now": 1', output.getvalue())

    def test_legacy_worker_requires_no_new_materialization_writes(self):
        with self.assertRaisesRegex(CommandError, 'Legacy materialization writes are retired'):
            call_command('process_evaluation_queue', '--legacy')
