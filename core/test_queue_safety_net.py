"""Request-cycle safety net tests (core.middleware + queue_worker gate).

The safety net is the execution half of Phase A that survives process death:
the enqueue-time trigger starts a drain in the process that handled the save,
but if that process exits or the dev server autoreloads first, PENDING tasks
would otherwise sit forever.  These tests prove that a subsequent request
re-arms the same coalesced drain -- automatically, with no operator, and
without blocking the request.
"""
from unittest import mock

from django.http import HttpResponse
from django.test import TestCase, override_settings

from core.middleware import (
    QueueDrainSafetyNetMiddleware,
    _reset_throttle,
    maybe_drain_from_request,
)
from core.models import Profile, User as CoreUser
from core.services import queue_worker


class SafetyNetGateTests(TestCase):
    def setUp(self):
        queue_worker.set_synchronous_mode(True)
        _reset_throttle()

    def tearDown(self):
        queue_worker.set_synchronous_mode(False)
        queue_worker.join_worker(timeout=10)
        _reset_throttle()

    def _enqueue_pending(self, username='safety-user'):
        # Enqueue directly (not via a save) to mimic a task whose process
        # died between enqueue and drain: PENDING, never triggered.
        from core.services.feed_queue import enqueue_profile_rebuild
        profile = Profile.objects.create(
            user=CoreUser.objects.create_user(username, password='pw'))
        task = enqueue_profile_rebuild(profile.pk, 'safety-net-test')
        self.assertEqual(task.state, 'PENDING')
        self.assertEqual(task.attempts, 0)
        return task

    def test_safety_net_is_disabled_by_default_under_the_test_runner(self):
        from django.conf import settings
        self.assertFalse(settings.CAREERGPS_QUEUE_SAFETY_NET)
        self.assertFalse(maybe_drain_from_request())

    def test_throttled_check_skips_without_querying(self):
        # First call updates the throttle timestamp; the second call inside
        # the interval must return before touching the database at all.
        with override_settings(
                CAREERGPS_QUEUE_SAFETY_NET=True,
                CAREERGPS_QUEUE_SAFETY_NET_INTERVAL=3600), mock.patch(
                'core.services.queue_worker.claimable_work_exists') as gate:
            _reset_throttle()
            gate.return_value = False
            self.assertFalse(maybe_drain_from_request())
            self.assertEqual(gate.call_count, 1)
            self.assertFalse(maybe_drain_from_request())
            self.assertEqual(gate.call_count, 1)

    def test_enabled_gate_arms_and_drains_claimable_work(self):
        task = self._enqueue_pending()
        with override_settings(
                CAREERGPS_QUEUE_SAFETY_NET=True,
                CAREERGPS_QUEUE_SAFETY_NET_INTERVAL=0):
            _reset_throttle()
            self.assertTrue(
                maybe_drain_from_request(),
                'safety net did not arm a drain for claimable work')
        task.refresh_from_db()
        # Claimed, not merely still pending: attempt consumed by the same
        # compare-and-swap machinery the management command uses.
        self.assertEqual(task.state, 'COMPLETE')
        self.assertEqual(task.attempts, 1)
        self.assertEqual(task.diagnostics.get('worker_id'), 'auto-drain')

    def test_quiet_queue_does_not_arm_a_drain(self):
        from core.services import feed_queue, provisional_feed
        with override_settings(
                CAREERGPS_QUEUE_SAFETY_NET=True,
                CAREERGPS_QUEUE_SAFETY_NET_INTERVAL=0):
            _reset_throttle()
            with mock.patch.object(feed_queue, 'drain_v2_queue') as v2, \
                    mock.patch.object(provisional_feed,
                                      'drain_provisional_queue') as prov, \
                    mock.patch.object(queue_worker, '_start_locked') as start:
                self.assertFalse(maybe_drain_from_request())
                self.assertFalse(v2.called)
                self.assertFalse(prov.called)
        self.assertFalse(start.called)

    def test_middleware_never_breaks_the_request(self):
        with override_settings(
                CAREERGPS_QUEUE_SAFETY_NET=True,
                CAREERGPS_QUEUE_SAFETY_NET_INTERVAL=0):
            _reset_throttle()
            with mock.patch(
                    'core.services.queue_worker.claimable_work_exists',
                    side_effect=RuntimeError('db exploded')):
                middleware = QueueDrainSafetyNetMiddleware(
                    lambda request: HttpResponse('ok'))
                response = middleware(object())
        self.assertEqual(response.status_code, 200)

    def test_middleware_drains_through_a_real_request_cycle(self):
        task = self._enqueue_pending()
        with override_settings(
                CAREERGPS_QUEUE_SAFETY_NET=True,
                CAREERGPS_QUEUE_SAFETY_NET_INTERVAL=0):
            _reset_throttle()
            middleware = QueueDrainSafetyNetMiddleware(
                lambda request: HttpResponse('ok'))
            response = middleware(object())
        self.assertEqual(response.status_code, 200)
        task.refresh_from_db()
        self.assertEqual(task.state, 'COMPLETE')
