"""Unit tests for the self-drain trigger and queue-task retry/fail logic.

These exercise the lightweight trigger (no full generation build) so the
fast tests stay fast, while the end-to-end rebuild lifecycle lives in
``test_target_auto_rebuild.py``, the request-cycle safety net in
``test_queue_safety_net.py``, and lease reclaim in ``test_feed_v2.py``.
"""
import threading
from unittest import mock

from django.test import TestCase

from core.models import EvaluationRecomputeTask, Profile, User as CoreUser
from core.services import queue_worker
from core.services.feed_queue import _retry_or_fail


class QueueWorkerUnitTests(TestCase):
    def tearDown(self):
        queue_worker.set_synchronous_mode(False)
        queue_worker.join_worker(timeout=10)

    def _profile(self):
        return Profile.objects.create(
            user=CoreUser.objects.create_user('probe-user', password='password'))

    def test_request_never_raises(self):
        queue_worker.request_queue_drain(reason='unit-probe')
        self.assertTrue(queue_worker.join_worker(timeout=10))

    def test_request_when_worker_alive_does_not_double_spawn(self):
        queue_worker.request_queue_drain(reason='first')
        queue_worker.join_worker(timeout=10)
        queue_worker.request_queue_drain(reason='second')
        self.assertTrue(queue_worker.join_worker(timeout=10))

    def test_failed_tasks_retry_with_backoff(self):
        task = EvaluationRecomputeTask.objects.create(
            profile=self._profile(),
            reason='unit-retry', task_version='feed-v2', state='RUNNING',
            claim_token='token-1', attempts=0, max_attempts=3,
            available_at=self._now())
        updated = _retry_or_fail(task, 'token-1', ValueError('boom'))
        self.assertEqual(updated.state, 'PENDING')
        self.assertGreater(updated.available_at.timestamp(), task.available_at.timestamp())

    def test_exhausted_tasks_fail_terminally(self):
        task = EvaluationRecomputeTask.objects.create(
            profile=self._profile(),
            reason='unit-fail', task_version='feed-v2', state='RUNNING',
            claim_token='token-2', attempts=3, max_attempts=3,
            available_at=self._now())
        updated = _retry_or_fail(task, 'token-2', ValueError('boom'))
        self.assertEqual(updated.state, 'FAILED')
        self.assertIsNotNone(updated.completed_at)

    def _now(self):
        from django.utils import timezone
        return timezone.now()



class SynchronousModeTests(TestCase):
    """Regression guards for the synchronous/test drain path.

    The original implementation called ``_drain_loop`` while holding the
    non-reentrant module ``_lock``; ``_drain_loop`` re-acquires the same lock
    at its pass boundary, so every synchronous trigger self-deadlocked and
    the test process hung forever.  These tests fail loudly (bounded event
    wait) instead of hanging, and prove the corrected ownership protocol:
    the lock is released before the loop runs and re-entrant triggers
    coalesce rather than recurse.
    """

    def tearDown(self):
        queue_worker.set_synchronous_mode(False)
        queue_worker.join_worker(timeout=10)

    def test_synchronous_trigger_returns_instead_of_self_deadlocking(self):
        queue_worker.set_synchronous_mode(True)
        finished = threading.Event()

        def run_trigger():
            queue_worker.request_queue_drain(reason='deadlock-probe')
            finished.set()

        helper = threading.Thread(target=run_trigger, daemon=True)
        helper.start()
        self.assertTrue(
            finished.wait(10),
            'request_queue_drain self-deadlocked in synchronous mode '
            '(lock held across _drain_loop)')
        helper.join(timeout=5)

    def test_synchronous_drain_with_no_work_is_quiet_and_releases_ownership(self):
        queue_worker.set_synchronous_mode(True)
        queue_worker.request_queue_drain(reason='no-work')
        self.assertFalse(
            queue_worker._drain_running,
            'drain ownership was not released after an empty pass')
        self.assertFalse(queue_worker._drain_requested)
        self.assertTrue(queue_worker.join_worker(timeout=5))
        self.assertEqual(
            EvaluationRecomputeTask.objects.count(), 0,
            'an empty drain must not create work')

    def test_work_arriving_during_a_drain_is_coalesced_not_lost(self):
        """A trigger fired while a pass runs must cause one more pass."""
        queue_worker.set_synchronous_mode(True)
        calls = {'v2': 0}

        def fake_drain(**kwargs):
            calls['v2'] += 1
            if calls['v2'] == 1:
                # Simulates a target save landing mid-drain: the re-entrant
                # trigger must coalesce (old code: self-deadlock here).
                queue_worker.request_queue_drain(reason='arrived-mid-drain')
            return []

        with mock.patch(
                'core.services.feed_queue.drain_v2_queue',
                side_effect=fake_drain), mock.patch(
                'core.services.provisional_feed.drain_provisional_queue',
                return_value=[]):
            queue_worker.request_queue_drain(reason='outer')

        self.assertEqual(
            calls['v2'], 2,
            'the mid-drain trigger was lost instead of causing a second pass')
        self.assertFalse(queue_worker._drain_running)

    def test_duplicate_triggers_never_spawn_a_second_worker(self):
        """Burst of triggers: at most one in-flight drain (ownership gate)."""
        queue_worker.set_synchronous_mode(False)
        spawned = []
        real_start = queue_worker._start_locked

        def counting_start(reason):
            spawned.append(reason)
            real_start(reason)

        # Hold ownership exactly as an in-flight drain would, so the gate is
        # tested deterministically rather than racing a real worker thread.
        with queue_worker._lock:
            queue_worker._drain_running = True
        try:
            with mock.patch.object(queue_worker, '_start_locked',
                                   side_effect=counting_start):
                for i in range(5):
                    queue_worker.request_queue_drain(reason=f'burst-{i}')
            self.assertEqual(
                spawned, [],
                'duplicate triggers spawned workers while one was in flight')
            # Flag must still be set so the in-flight drain runs another pass.
            self.assertTrue(queue_worker._drain_requested)
        finally:
            with queue_worker._lock:
                queue_worker._drain_running = False
                queue_worker._drain_requested = False

