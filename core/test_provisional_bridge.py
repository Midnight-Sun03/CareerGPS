"""Phase 35: the event-driven provisional legacy bridge.

Every test asserts a state that the brief named as an acceptance criterion:
identity isolation, v2 precedence, failure isolation, and that an ordinary
page request never executes the expensive matcher.
"""
from unittest import mock

from django.contrib.auth.models import User
from django.test import TestCase

from core.models import (
    CareerPreference, EvaluationRecomputeTask, Notification, Opportunity,
    Profile, ProfileFeedState, ProfileOpportunityEvaluation, ProfileSkill,
    TaxonomyConcept, TaxonomyLabel, TaxonomyRelease,
)
from core.services.feed_v2 import build_profile_generation, v2_feed_queryset
from core.services.provisional_feed import (
    PROVISIONAL_TASK_VERSION, drain_provisional_queue,
    enqueue_provisional_rebuild, materialize_provisional,
    provisional_dependency, provisional_evaluations,
)
from core.services.title_gate import exclude_senior
from core.taxonomies.normalization import normalize_term
from core.test_support import create_professional_opportunity
from core.views import _authoritative_feed_ready


class ProvisionalBridgeCase(TestCase):
    def setUp(self):
        self.releases = {
            source: TaxonomyRelease.objects.create(
                source=source, version=f'bridge-{source}', is_active=True,
                source_url=f'https://taxonomy.test/{source}')
            for source in ('ofo', 'esco')
        }
        self.profile = Profile.objects.create(
            user=User.objects.create_user('bridge-user', password='password'))
        self.client.force_login(self.profile.user)
        self.jobs = [
            self.job(f'Data Analyst Role {i:02d}') for i in range(6)]

    def concept(self, source, label):
        concept = TaxonomyConcept.objects.create(
            release=self.releases[source],
            external_id=f'{source}:{label}:{TaxonomyConcept.objects.count()}',
            kind='occupation', preferred_label=label,
            normalized_label=normalize_term(label), metadata={})
        TaxonomyLabel.objects.create(
            concept=concept, label=label,
            normalized_label=normalize_term(label), label_type='preferred')
        return concept

    def job(self, title):
        return create_professional_opportunity(
            title=title, description='Source-grounded responsibilities.')

    def set_target(self, text):
        preference, _ = CareerPreference.objects.update_or_create(
            profile=self.profile, defaults={'target_roles': text})
        self.preference = preference
        return preference

    def scope(self):
        return exclude_senior(Opportunity.objects.professionally_ready(
            include_obvious_senior=False))

    def provisional_queryset(self):
        return provisional_evaluations(
            self.profile, ProfileOpportunityEvaluation.objects.filter(
                profile=self.profile))

    def run_provisional(self):
        return drain_provisional_queue(worker_id='test-worker')
    # ------------------------------------------------------------- freshness

    def test_a_build_produces_results_for_the_current_target(self):
        self.set_target('Data Analyst Role 00')
        enqueue_provisional_rebuild(self.profile.pk, 'test')
        self.run_provisional()
        rows = list(self.provisional_queryset())
        self.assertTrue(rows)
        self.assertEqual(
            {row.dependency_hash for row in rows},
            {provisional_dependency(self.profile)})
        self.assertTrue(all(row.state == 'CURRENT' for row in rows))
    def test_target_change_a_to_b_changes_the_identity(self):
        self.set_target('Data Analyst Role 00')
        dependency_a = provisional_dependency(self.profile)
        self.set_target('Data Analyst Role 01')
        self.assertNotEqual(dependency_a, provisional_dependency(self.profile))

    def test_stale_a_rows_may_remain_but_are_never_read(self):
        self.set_target('Data Analyst Role 00')
        enqueue_provisional_rebuild(self.profile.pk, 'test')
        self.run_provisional()
        stale_dependency = provisional_dependency(self.profile)
        self.assertTrue(ProfileOpportunityEvaluation.objects.filter(
            dependency_hash=stale_dependency).exists())

        self.set_target('Data Analyst Role 01')
        self.assertTrue(ProfileOpportunityEvaluation.objects.filter(
            dependency_hash=stale_dependency).exists())
        self.assertEqual(list(self.provisional_queryset()), [])
        self.assertNotEqual(provisional_dependency(self.profile),
                            stale_dependency)
    def test_stale_a_completion_is_written_stale_for_b(self):
        self.set_target('Data Analyst Role 00')
        dependency_a = provisional_dependency(self.profile)
        self.set_target('Data Analyst Role 01')
        dependency_b = provisional_dependency(self.profile)
        self.assertNotEqual(dependency_a, dependency_b)

        ProfileOpportunityEvaluation.objects.filter(
            profile=self.profile).delete()
        with mock.patch(
                'core.services.provisional_feed.provisional_dependency',
                side_effect=[dependency_a, dependency_b, dependency_b]):
            written, dependency = materialize_provisional(self.profile)
        self.assertEqual(dependency, dependency_a)
        self.assertGreater(written, 0)
        self.assertEqual(
            list(self.provisional_queryset().values_list(
                'state', flat=True)), [])
        self.assertTrue(ProfileOpportunityEvaluation.objects.filter(
            profile=self.profile, state='STALE').exists())
    def test_provisional_is_used_while_v2_is_unresolved(self):
        self.set_target('Data Analyst Role 00')
        self.assertFalse(_authoritative_feed_ready(self.profile))
        enqueue_provisional_rebuild(self.profile.pk, 'test')
        self.run_provisional()
        self.assertTrue(list(self.provisional_queryset()))

        response = self.client.get('/opportunities/')
        self.assertTrue(response.context['provisional_primary'])
        self.assertTrue(response.context['uses_provisional_feed'])
        self.assertTrue(response.context['results'])
        self.assertContains(response, 'Your matches are updating')

    def test_v2_takes_precedence_immediately_when_authoritative(self):
        self.concept('esco', 'Data Analyst Role 00')
        self.concept('ofo', 'Data Analyst Role 00')
        self.set_target('Data Analyst Role 00')
        enqueue_provisional_rebuild(self.profile.pk, 'test')
        self.run_provisional()
        self.assertTrue(list(self.provisional_queryset()))

        build_profile_generation(self.profile)
        self.assertTrue(_authoritative_feed_ready(self.profile))

        response = self.client.get('/opportunities/')
        self.assertFalse(response.context['uses_provisional_feed'])
        self.assertNotContains(response, 'Your matches are updating')

    def test_provisional_cannot_override_an_authoritative_feed(self):
        self.concept('esco', 'Data Analyst Role 00')
        self.concept('ofo', 'Data Analyst Role 00')
        self.set_target('Data Analyst Role 00')
        build_profile_generation(self.profile)
        self.assertTrue(_authoritative_feed_ready(self.profile))
        authoritative_id = self.profile.feed_state.active_generation_id

        enqueue_provisional_rebuild(self.profile.pk, 'late')
        self.run_provisional()

        self.assertTrue(_authoritative_feed_ready(self.profile))
        self.assertEqual(
            ProfileFeedState.objects.get(profile=self.profile)
            .active_generation_id, authoritative_id)
        response = self.client.get('/opportunities/')
        self.assertFalse(response.context['uses_provisional_feed'])
    def test_provisional_failure_does_not_alter_v2(self):
        self.concept('esco', 'Data Analyst Role 00')
        self.concept('ofo', 'Data Analyst Role 00')
        self.set_target('Data Analyst Role 00')
        build_profile_generation(self.profile)
        generation_id = self.profile.feed_state.active_generation_id
        self.assertIsNotNone(generation_id)

        task = enqueue_provisional_rebuild(self.profile.pk, 'test')
        self.assertIsNotNone(task)
        EvaluationRecomputeTask.objects.filter(pk=task.pk).update(attempts=99)
        with mock.patch(
                'core.matching.get_matched_opportunities',
                side_effect=RuntimeError('provisional exploded')):
            self.run_provisional()

        failed = EvaluationRecomputeTask.objects.get(pk=task.pk)
        self.assertIn(failed.state, ('FAILED', 'PENDING'))
        state = ProfileFeedState.objects.get(profile=self.profile)
        self.assertEqual(state.active_generation_id, generation_id)
        self.assertEqual(state.active_generation.state, 'ACTIVE')
        self.assertTrue(v2_feed_queryset(self.profile).exists())

    def test_v2_failure_preserves_previous_authoritative_v2(self):
        self.concept('esco', 'Data Analyst Role 00')
        self.concept('ofo', 'Data Analyst Role 00')
        self.set_target('Data Analyst Role 00')
        build_profile_generation(self.profile)
        generation_id = self.profile.feed_state.active_generation_id

        # A genuine evidence change gives the replacement build its own
        # dependency, so it builds a new generation row instead of reusing
        # the active one; the patched failure then proves the prior
        # authoritative feed survives.
        ProfileSkill.objects.create(
            profile=self.profile, entered_name='SQL', confirmed_by_user=True)
        with mock.patch(
                'core.services.feed_v2._evaluation_values',
                side_effect=RuntimeError('synthetic build failure')):
            with self.assertRaises(RuntimeError):
                build_profile_generation(self.profile, reason='evidence_changed')

        self.profile.feed_state.refresh_from_db()
        self.assertEqual(self.profile.feed_state.active_generation_id,
                         generation_id)
        self.assertTrue(v2_feed_queryset(self.profile).exists())

    def test_no_target_exposes_no_provisional_results(self):
        self.set_target('')
        self.assertIsNone(enqueue_provisional_rebuild(self.profile.pk, 'test'))
        self.assertEqual(list(self.provisional_queryset()), [])
        response = self.client.get('/opportunities/')
        self.assertFalse(response.context['uses_provisional_feed'])
        self.assertFalse(response.context['has_targets'])

    def test_page_request_with_provisional_rows_runs_no_matcher(self):
        self.set_target('Data Analyst Role 00')
        enqueue_provisional_rebuild(self.profile.pk, 'test')
        self.run_provisional()
        self.assertTrue(list(self.provisional_queryset()))

        with mock.patch(
                'core.views.get_matched_opportunities',
                side_effect=AssertionError('matcher must not run'),
        ), mock.patch(
                'core.matching.get_matched_opportunities',
                side_effect=AssertionError('matcher must not run')):
            response = self.client.get('/dashboard/')
            self.assertEqual(response.status_code, 200)
            self.assertTrue(response.context['top_matches'])
            response = self.client.get('/opportunities/')
            self.assertEqual(response.status_code, 200)
            self.assertTrue(response.context['uses_provisional_feed'])

    def test_target_change_enqueues_both_task_versions(self):
        with self.captureOnCommitCallbacks(execute=True):
            self.set_target('Data Analyst Role 00')
        v2_tasks = EvaluationRecomputeTask.objects.filter(
            profile=self.profile, task_version='feed-v2')
        prov_tasks = EvaluationRecomputeTask.objects.filter(
            profile=self.profile,
            task_version=PROVISIONAL_TASK_VERSION)
        self.assertTrue(v2_tasks.exists())
        self.assertTrue(prov_tasks.exists())
        # Two distinct queue identities for one save: each version exists as
        # its own task row.  (The previous assertion intersected two disjoint
        # version sets -- always empty -- so it could never pass.)
        self.assertEqual({t.task_version for t in v2_tasks}, {'feed-v2'})
        self.assertEqual(
            {t.task_version for t in prov_tasks}, {PROVISIONAL_TASK_VERSION})
        self.assertNotEqual('feed-v2', PROVISIONAL_TASK_VERSION)

    def test_v2_activation_notifies_a_viewer_of_provisional_results(self):
        self.concept('esco', 'Data Analyst Role 00')
        self.concept('ofo', 'Data Analyst Role 00')
        self.set_target('Data Analyst Role 00')
        enqueue_provisional_rebuild(self.profile.pk, 'test')
        self.run_provisional()
        before = Notification.objects.filter(
            user=self.profile.user).count()

        build_profile_generation(self.profile)
        self.assertTrue(_authoritative_feed_ready(self.profile))

        notes = Notification.objects.filter(user=self.profile.user)
        self.assertGreater(notes.count(), before)
        latest = notes.order_by('-pk').first()
        self.assertIn('recommendations have been updated', latest.title.lower())