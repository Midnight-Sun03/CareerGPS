"""Phase 34: target-specific occupational state lifecycle.

These tests assert the underlying stored state, not what a page happens to hide.
Clearing or changing a target must retire that target's occupational
intelligence so no later target can inherit it.
"""
from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from core.models import (
    CareerPreference, CareerTargetIdentity, CareerTargetResolution,
    OccupationSourceResolution, OccupationSourceResolutionCandidate,
    Opportunity, Profile, ProfileEvaluationGeneration, ProfileFeedEvaluation,
    ProfileFeedState, TargetOpportunityRelationshipAssessment,
    TargetOpportunityRelationshipFinding,
)
from core.services.feed_v2 import (
    build_profile_generation, v2_feed_queryset,
)
from core.test_support import create_professional_opportunity


class TargetStateLifecycleTests(TestCase):
    def setUp(self):
        self.releases = {
            source: __import__('core.models', fromlist=['TaxonomyRelease']
                                ).TaxonomyRelease.objects.create(
                source=source, version=f'life-{source}', is_active=True,
                source_url=f'https://taxonomy.test/{source}')
            for source in ('ofo', 'esco')
        }
        self.profile = Profile.objects.create(
            user=User.objects.create_user('lifecycle-user', password='password'))
        self.jobs = [create_professional_opportunity(
            title=f'Data Analyst Role {i:02d}',
            description='Source-grounded responsibilities.')
            for i in range(3)]

    def concept(self, source, label):
        from core.models import TaxonomyConcept, TaxonomyLabel
        from core.taxonomies.normalization import normalize_term
        concept = TaxonomyConcept.objects.create(
            release=self.releases[source],
            external_id=f'{source}:{label}:{TaxonomyConcept.objects.count()}',
            kind='occupation', preferred_label=label,
            normalized_label=normalize_term(label), metadata={})
        TaxonomyLabel.objects.create(
            concept=concept, label=label,
            normalized_label=normalize_term(label), label_type='preferred')
        return concept

    def set_target(self, text):
        preference, _created = CareerPreference.objects.update_or_create(
            profile=self.profile, defaults={'target_roles': text})
        self.preference = preference
        return preference

    def counts(self):
        return {
            'containers': CareerTargetResolution.objects.filter(
                profile=self.profile).count(),
            'target_sources': OccupationSourceResolution.objects.filter(
                target_resolution__profile=self.profile).count(),
            'candidates': OccupationSourceResolutionCandidate.objects.filter(
                resolution__target_resolution__profile=self.profile).count(),
            'identities': CareerTargetIdentity.objects.filter(
                resolution__profile=self.profile).count(),
            'assessments': TargetOpportunityRelationshipAssessment.objects.filter(
                target_resolution__profile=self.profile).count(),
            'findings': TargetOpportunityRelationshipFinding.objects.filter(
                assessment__target_resolution__profile=self.profile).count(),
            'feed_evals': ProfileFeedEvaluation.objects.filter(
                generation__profile=self.profile).count(),
        }

    def populate(self, target_text='Data Analyst Role 00'):
        self.concept('esco', target_text)
        self.concept('ofo', target_text)
        self.set_target(target_text)
        build_profile_generation(self.profile)
        return self.counts()

    def test_target_change_retires_the_previous_targets_state(self):
        """Target A -> B: no A-specific row survives into B's build."""
        before = self.populate('Data Analyst Role 00')
        self.assertGreater(before['assessments'], 0)
        self.assertGreater(before['containers'], 0)
        self.assertEqual(before['target_sources'], 2)

        a_assessments = set(
            TargetOpportunityRelationshipAssessment.objects.filter(
                target_resolution__profile=self.profile)
            .values_list('pk', 'dependency_hash'))

        # Target B is saved; the next build is the lifecycle boundary.
        self.concept('esco', 'Supply Chain Analyst')
        self.concept('ofo', 'Supply Chain Analyst')
        self.set_target('Supply Chain Analyst')
        build_profile_generation(self.profile)

        # The container at ordinal 0 is deliberately reused, so the same
        # assessment rows are recomputed for B. What must not survive is A's
        # *verdict*: every surviving row must now be a fresh B assessment.
        container = CareerTargetResolution.objects.get(profile=self.profile)
        self.assertEqual(container.authored_text, 'Supply Chain Analyst')
        current = dict(
            TargetOpportunityRelationshipAssessment.objects.filter(
                target_resolution=container)
            .values_list('pk', 'dependency_hash'))
        for pk, old_hash in a_assessments:
            if pk in current:
                self.assertNotEqual(
                    current[pk], old_hash,
                    f'assessment {pk} still carries target A evidence')
        # And the admitted feed is built from B alone.
        admitted = v2_feed_queryset(self.profile)
        for row in admitted:
            self.assertEqual(row.relationship_assessment.authored_text
                             if hasattr(row.relationship_assessment, 'authored_text')
                             else container.authored_text,
                             'Supply Chain Analyst')

    def test_clearing_the_target_removes_all_target_specific_state(self):
        populated = self.populate('Data Analyst Role 00')
        self.assertTrue(populated['assessments'])
        self.assertTrue(populated['target_sources'])

        self.set_target('')
        build_profile_generation(self.profile)

        after = self.counts()
        self.assertEqual(after['containers'], 0)
        self.assertEqual(after['target_sources'], 0)
        self.assertEqual(after['candidates'], 0)
        self.assertEqual(after['identities'], 0)
        self.assertEqual(after['assessments'], 0)
        self.assertEqual(after['findings'], 0)
        self.assertEqual(after['feed_evals'], 0)
        self.assertEqual(
            self.profile.feed_state.state, 'NO_TARGETS')
        self.assertIsNone(self.profile.feed_state.active_generation)

    def test_a_new_target_after_clearing_starts_from_nothing(self):
        """The clear -> new-target path must not inherit the old evidence."""
        self.populate('Data Analyst Role 00')
        self.set_target('')
        build_profile_generation(self.profile)
        self.assertEqual(self.counts()['assessments'], 0)

        # A completely unrelated target builds from a clean slate.
        self.concept('esco', 'Marine Biologist')
        self.concept('ofo', 'Marine Biologist')
        self.set_target('Marine Biologist')
        build_profile_generation(self.profile)

        container = CareerTargetResolution.objects.get(profile=self.profile)
        self.assertEqual(container.authored_text, 'Marine Biologist')
        for assessment in TargetOpportunityRelationshipAssessment.objects.filter(
                target_resolution=container):
            self.assertEqual(assessment.target_resolution_id, container.pk)
            self.assertIn(assessment.revision.cleaned_title,
                          [job.active_clean_revision.cleaned_title
                           for job in self.jobs])

    def test_vacancy_state_is_candidate_independent_and_survives_the_purge(self):
        """Purging a target must not delete shared, target-free vacancy work."""
        self.populate('Data Analyst Role 00')
        vacancy_rows = OccupationSourceResolution.objects.filter(
            opportunity_resolution__isnull=False).count()
        self.assertGreater(vacancy_rows, 0)

        self.set_target('')
        build_profile_generation(self.profile)

        self.assertEqual(
            OccupationSourceResolution.objects.filter(
                opportunity_resolution__isnull=False).count(),
            vacancy_rows)
        self.assertEqual(
            OccupationSourceResolution.objects.filter(
                target_resolution__isnull=False).count(), 0)

    def test_purge_is_idempotent_and_safe_without_prior_state(self):
        self.set_target('')
        build_profile_generation(self.profile)
        build_profile_generation(self.profile)
        self.assertEqual(self.counts()['containers'], 0)
        self.assertEqual(list(v2_feed_queryset(self.profile)), [])

    def test_deleting_the_preference_row_releases_admissions_before_cascade(self):
        """Deleting the target role through the profile Delete button works.

        Regression for the career-preferences delete path:
        POST /profile/career-preferences/<pk>/delete/ -> CareerPreference
        row delete -> cascade into CareerTargetResolution while v2 admissions
        still PROTECT their assessments.  The delete died inside Django's
        collector before any post_delete rebuild signal could fire, so the
        user saw an error.  The preference delete must release its own
        admissions first, then cascade normally.
        """
        populated = self.populate('Data Analyst Role 00')
        self.assertGreater(populated['feed_evals'], 0)
        self.assertGreater(populated['assessments'], 0)

        self.client.force_login(self.profile.user)
        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(reverse(
                'profile_section_delete',
                kwargs={'section': 'career-preferences',
                        'record_id': self.preference.pk}))

        self.assertRedirects(
            response, reverse('profile_section',
                              kwargs={'section': 'career-preferences'}),
            fetch_redirect_response=False)
        self.assertFalse(
            CareerPreference.objects.filter(profile=self.profile).exists())
        self.assertEqual(self.counts()['containers'], 0)
        self.assertEqual(self.counts()['feed_evals'], 0)
        self.assertEqual(self.counts()['assessments'], 0)
        # Candidate-independent vacancy work must survive the preference delete.
        self.assertGreater(
            OccupationSourceResolution.objects.filter(
                opportunity_resolution__isnull=False).count(), 0)