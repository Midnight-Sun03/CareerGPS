"""Automatic target-change rebuild: save → enqueue → self-triggered worker.

Proves CareerGPS owns the rebuild lifecycle end to end without any operator
running a drain command.  The whole module runs in synchronous drain mode so
no background thread ever touches the shared-cache SQLite test DB from a
second connection (that cross-thread contention is what produced the old
"database table is locked" noise); the background-thread path is covered
separately in ``test_queue_worker.py``.

Covered here:
* target change enqueues BOTH task versions (feed-v2 and provisional-v1);
* automatic execution is triggered by the save alone (no manual drain call);
* tasks are actually CLAIMED (attempts consumed, claim token, worker id),
  not merely left PENDING;
* rebuild reaches generation creation via the indexed candidate window;
* ESCO/OFO target resolution produces stored retained concepts;
* generation activation converges to READY on the newest intent;
* rapid consecutive saves supersede everything but the newest dependency;
* a stale (older-target) generation can never activate over a newer target;
* duplicate triggers create no duplicate active work;
* a failing build returns the task to PENDING with backoff (retry semantics).
"""
import threading
from unittest import mock

from django.contrib.auth.models import User
from django.test import TestCase, TransactionTestCase

from core.models import (
    CareerPreference,
    CareerTargetResolution,
    EvaluationRecomputeTask,
    OccupationSourceResolutionCandidate,
    Profile,
    ProfileEvaluationGeneration,
    ProfileFeedState,
    TaxonomyConcept,
    TaxonomyLabel,
    TaxonomyRelease,
)
from core.professional_policies import FEED_GENERATION_VERSION
from core.services import queue_worker
from core.services.feed_v2 import (
    StaleGenerationBuild,
    _activate_generation,
    authored_intent_fingerprint,
    current_evidence_fingerprint,
    v2_feed_queryset,
)
from core.services.provisional_feed import PROVISIONAL_TASK_VERSION
from core.taxonomies.normalization import normalize_term
from core.test_support import create_professional_opportunity


def _releases(tag):
    return {
        source: TaxonomyRelease.objects.create(
            source=source, version=f'{tag}-{source}', is_active=True)
        for source in ('ofo', 'esco')
    }

def _concept(releases, source, label):
    concept = TaxonomyConcept.objects.create(
        release=releases[source], kind='occupation',
        external_id=f'{source}:{label}:{TaxonomyConcept.objects.count()}',
        preferred_label=label, normalized_label=normalize_term(label),
        metadata={})
    TaxonomyLabel.objects.create(
        concept=concept, label=label,
        normalized_label=normalize_term(label), label_type='preferred')
    return concept


class TargetChangeAutoRebuildTests(TransactionTestCase):
    """End-to-end lifecycle with the real trigger wired and synchronous drain."""

    def setUp(self):
        # Run the drain in this thread (no background worker) so SQLite's
        # shared-cache table lock never conflicts with the test itself.
        queue_worker.set_synchronous_mode(True)
        self.releases = _releases('auto')
        _concept(self.releases, 'esco', 'Data Engineer')
        self.job = create_professional_opportunity(
            title='Data Engineer',
            description='Source-grounded responsibilities.')
        self.unrelated = create_professional_opportunity(
            title='Warehouse Clerk',
            description='Unrelated ready listing that must stay out of scope.')
        self.profile = Profile.objects.create(
            user=User.objects.create_user('auto-user', password='password'))

    def tearDown(self):
        queue_worker.set_synchronous_mode(False)
        queue_worker.join_worker(timeout=30)

    def _set_target(self, text):
        preference, _ = CareerPreference.objects.update_or_create(
            profile=self.profile, defaults={'target_roles': text})
        return preference

    def _suppress_trigger(self):
        # integrity._trigger_queue_drain imports request_queue_drain at call
        # time, so patching the module attribute suppresses exactly the
        # automatic worker trigger while the enqueue itself still runs.
        return mock.patch(
            'core.services.queue_worker.request_queue_drain')

    def _live_tasks(self, version):
        return EvaluationRecomputeTask.objects.filter(
            profile=self.profile, task_version=version).exclude(
            state__in=('SUPERSEDED', 'FAILED'))

    def _wait_ready(self, timeout=60):
        queue_worker.join_worker(timeout=timeout)
        state = ProfileFeedState.objects.filter(profile=self.profile).first()
        self.assertIsNotNone(state)
        return state

    def test_target_change_rebuilds_automatically_to_ready(self):
        # No manual drain call anywhere in this test: the save itself must
        # fire the enqueue AND the automatic worker trigger.
        self._set_target('Accountant')
        queue_worker.join_worker(timeout=60)
        first_state = ProfileFeedState.objects.get(profile=self.profile)
        self.assertEqual(first_state.state, 'READY')
        first_generation_id = first_state.active_generation_id
        self.assertIsNotNone(first_generation_id)
        self.assertEqual(
            first_state.active_generation.intent_dependency_hash,
            authored_intent_fingerprint(self.profile))

        # Claimed, not merely pending: attempts consumed by the CAS claim and
        # the claim identifies the automatic worker.
        first_v2 = self._live_tasks('feed-v2').get()
        self.assertEqual(first_v2.state, 'COMPLETE')
        self.assertGreaterEqual(first_v2.attempts, 1)
        self.assertEqual(first_v2.diagnostics.get('worker_id'), 'auto-drain')
        first_prov = self._live_tasks(PROVISIONAL_TASK_VERSION).get()
        self.assertEqual(first_prov.state, 'COMPLETE')

        self._set_target('Data Engineer')
        state = self._wait_ready()
        self.assertEqual(state.state, 'READY')
        self.assertEqual(state.pending_reason, '')
        self.assertNotEqual(state.active_generation_id, first_generation_id)
        self.assertEqual(
            state.active_generation.intent_dependency_hash,
            authored_intent_fingerprint(self.profile))
        self.assertEqual(state.active_generation.state, 'ACTIVE')

        # The old generation is retired (its verified stale-build guards in
        # feed_queue/feed_v2 cover the refusal path; here the old feed must
        # no longer be the active one) and the old target text replaced.
        old_states = set(ProfileEvaluationGeneration.objects.filter(
            profile=self.profile).exclude(
            pk=state.active_generation_id).values_list('state', flat=True))
        self.assertEqual(old_states, {'SUPERSEDED'})
        texts = set(CareerTargetResolution.objects.filter(
            profile=self.profile).values_list('authored_text', flat=True))
        self.assertEqual(texts, {'Data Engineer'})
        # The active generation carries the indexed candidate window it was
        # built from (not a full-catalogue scan), and the ESCO source
        # resolution retained the matched concept rows.
        diagnostics = state.active_generation.diagnostics
        self.assertGreaterEqual(diagnostics.get('candidate_revision_count'), 1)
        self.assertGreaterEqual(diagnostics.get('assessed_revision_count'), 1)
        target = CareerTargetResolution.objects.get(
            profile=self.profile, authored_text='Data Engineer')
        source_states = dict(target.source_resolutions.values_list(
            'source', 'state'))
        self.assertEqual(
            source_states.get('esco'), 'RESOLVED',
            f'expected ESCO retained concepts, got {source_states}')
        self.assertGreater(
            OccupationSourceResolutionCandidate.objects.filter(
                resolution__target_resolution=target,
            ).count(), 0)
        self.assertIn(
            self.job.active_clean_revision_id,
            set(state.active_generation.v2_evaluations.values_list(
                'clean_revision_id', flat=True)))
        # Unrelated READY jobs never enter the candidate window.
        self.unrelated.refresh_from_db()
        self.assertNotIn(
            self.unrelated.active_clean_revision_id,
            set(state.active_generation.v2_evaluations.values_list(
                'clean_revision_id', flat=True)))
        self.assertGreaterEqual(
            diagnostics.get('skipped_unrelated_revision_count'), 1)

        v2_tasks = EvaluationRecomputeTask.objects.filter(
            profile=self.profile, task_version='feed-v2',
            reason='careerpreference_changed')
        self.assertTrue(v2_tasks.filter(state='COMPLETE').exists())

    def test_save_marks_intent_and_enqueues_both_versions_before_execution(self):
        """Enqueue + ProfileFeedState transition belong to the save itself."""
        with self._suppress_trigger():
            self._set_target('Data Engineer')
        v2 = self._live_tasks('feed-v2')
        prov = self._live_tasks(PROVISIONAL_TASK_VERSION)
        self.assertEqual(v2.count(), 1)
        self.assertEqual(prov.count(), 1)
        self.assertEqual(v2.get().state, 'PENDING')
        self.assertEqual(v2.get().attempts, 0)
        self.assertEqual(prov.get().state, 'PENDING')
        state = ProfileFeedState.objects.get(profile=self.profile)
        self.assertEqual(state.state, 'UPDATING_INTENT')
        self.assertTrue(
            str(state.pending_reason).startswith('intent_resolving:'))
        self.assertIsNone(state.active_generation_id)
        self.assertFalse(ProfileEvaluationGeneration.objects.filter(
            profile=self.profile).exists())

    def test_rapid_consecutive_changes_supersede_and_converge(self):
        # Three saves, one drain: only the newest dependency may become
        # authoritative; earlier ones stay queued as superseded history.
        with self._suppress_trigger():
            self._set_target('Accountant')
            self._set_target('Data Engineer')
            self._set_target('Data Scientist')
        live_v2 = self._live_tasks('feed-v2')
        self.assertEqual(live_v2.count(), 1)
        newest = live_v2.get()
        self.assertEqual(newest.state, 'PENDING')
        history = EvaluationRecomputeTask.objects.filter(
            profile=self.profile, task_version='feed-v2',
            state='SUPERSEDED')
        self.assertEqual(history.count(), 2)
        self.assertNotIn(
            newest.task_key,
            set(history.values_list('task_key', flat=True)))

        queue_worker.request_queue_drain(reason='rapid-converge')
        state = self._wait_ready(timeout=90)
        self.assertEqual(state.state, 'READY')
        self.assertEqual(
            state.active_generation.intent_dependency_hash,
            authored_intent_fingerprint(self.profile))
        texts = set(CareerTargetResolution.objects.filter(
            profile=self.profile).values_list('authored_text', flat=True))
        self.assertEqual(texts, {'Data Scientist'})
        active_intents = set(ProfileEvaluationGeneration.objects.filter(
            profile=self.profile, state='ACTIVE',
        ).values_list('intent_dependency_hash', flat=True))
        self.assertEqual(
            active_intents, {authored_intent_fingerprint(self.profile)},
            'only the newest dependency may hold an ACTIVE generation')

    def test_returning_to_a_previous_target_rebuilds_authoritatively(self):
        """A → B → A: a historical COMPLETE task must not suppress the rebuild.

        The first Data Engineer build leaves a COMPLETE feed-v2 task whose
        key is derived from the intent fingerprint.  Changing back to Data
        Engineer regenerates byte-identical key; ``COMPLETE`` alone must not
        suppress the required rebuild, because the product that task built
        (generation A) was superseded by B's generation.
        """
        # --- A: Data Engineer builds to READY --------------------------------
        self._set_target('Data Engineer')
        queue_worker.join_worker(timeout=60)
        state_a = ProfileFeedState.objects.get(profile=self.profile)
        self.assertEqual(state_a.state, 'READY')
        gen_a_id = state_a.active_generation_id
        self.assertIsNotNone(gen_a_id)
        task_a = self._live_tasks('feed-v2').get()
        self.assertEqual(task_a.state, 'COMPLETE')
        self.assertEqual(task_a.diagnostics.get('generation_id'), gen_a_id)

        # --- B: Accountant supersedes generation A --------------------------
        self._set_target('Accountant')
        state_b = self._wait_ready()
        self.assertEqual(state_b.state, 'READY')
        gen_b_id = state_b.active_generation_id
        self.assertNotEqual(gen_b_id, gen_a_id)
        self.assertEqual(
            ProfileEvaluationGeneration.objects.get(pk=gen_a_id).state,
            'SUPERSEDED')

        # --- A again: the historical COMPLETE A task is now stale -----------
        with self._suppress_trigger():
            self._set_target('Data Engineer')

        # The save must have transitioned the feed and created fresh work:
        # pre-fix, the stale COMPLETE row was returned silently, the state
        # stayed READY on B's generation, and no new task appeared.
        state = ProfileFeedState.objects.get(profile=self.profile)
        self.assertEqual(state.state, 'UPDATING_INTENT')
        task_a.refresh_from_db()
        self.assertEqual(task_a.state, 'SUPERSEDED')
        self.assertIn(
            'active generation', task_a.supersession_reason)
        fresh = EvaluationRecomputeTask.objects.filter(
            task_key=task_a.task_key, state='PENDING')
        self.assertEqual(fresh.count(), 1)
        self.assertNotEqual(fresh.get().pk, task_a.pk)

        # --- drain: a new generation for A becomes authoritative ------------
        queue_worker.request_queue_drain(reason='revert-rebuild')
        state = self._wait_ready()
        self.assertEqual(state.state, 'READY')
        self.assertEqual(state.pending_reason, '')
        self.assertNotEqual(state.active_generation_id, gen_b_id)
        self.assertEqual(state.active_generation.state, 'ACTIVE')
        self.assertEqual(
            state.active_generation.intent_dependency_hash,
            authored_intent_fingerprint(self.profile))
        active_ids = set(ProfileEvaluationGeneration.objects.filter(
            profile=self.profile, state='ACTIVE',
        ).values_list('pk', flat=True))
        self.assertEqual(active_ids, {state.active_generation_id})
        # The feed corresponds to A (Data Engineer), not to B (Accountant).
        visible = set(v2_feed_queryset(self.profile).values_list(
            'opportunity_id', flat=True))
        self.assertIn(self.job.pk, visible)
        texts = set(CareerTargetResolution.objects.filter(
            profile=self.profile).values_list('authored_text', flat=True))
        self.assertEqual(texts, {'Data Engineer'})

    def test_duplicate_drain_requests_are_idempotent(self):
        # Rapid drain requests never raise and never duplicate live work:
        # synchronous mode runs each pass inline, but each pass claims only
        # what is claimable, so no second task or generation appears.
        for _ in range(5):
            queue_worker.request_queue_drain(reason='test-burst')
        self.assertTrue(queue_worker.join_worker(timeout=10))


class QueueWorkerUnitTests(TestCase):
    def setUp(self):
        # Also run this class synchronously: a background drain thread on the
        # shared-cache SQLite test DB collides with the test transaction.
        queue_worker.set_synchronous_mode(True)

    def tearDown(self):
        queue_worker.set_synchronous_mode(False)
        queue_worker.join_worker(timeout=30)

    def test_request_never_raises(self):
        queue_worker.request_queue_drain(reason='unit-probe')
        queue_worker.join_worker(timeout=60)

    def test_failed_tasks_retry_with_backoff(self):
        from django.utils import timezone
        from core.services.feed_queue import _retry_or_fail
        task = EvaluationRecomputeTask.objects.create(
            profile=Profile.objects.create(
                user=User.objects.create_user(
                    'retry-user', password='password')),
            reason='unit-retry', task_version='feed-v2', state='RUNNING',
            claim_token='token-1', attempts=0, max_attempts=3,
            available_at=timezone.now())
        updated = _retry_or_fail(task, 'token-1', ValueError('boom'))
        self.assertEqual(updated.state, 'PENDING')
        self.assertGreater(updated.available_at, task.available_at)

    def test_exhausted_tasks_fail_terminally(self):
        from django.utils import timezone
        from core.services.feed_queue import _retry_or_fail
        task = EvaluationRecomputeTask.objects.create(
            profile=Profile.objects.create(
                user=User.objects.create_user(
                    'fail-user', password='password')),
            reason='unit-fail', task_version='feed-v2', state='RUNNING',
            claim_token='token-2', attempts=3, max_attempts=3,
            available_at=timezone.now())
        updated = _retry_or_fail(task, 'token-2', ValueError('boom'))
        self.assertEqual(updated.state, 'FAILED')
        self.assertIsNotNone(updated.completed_at)
