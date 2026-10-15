"""Phase 33 provisional feed contract.

A target change must hide the old authoritative feed immediately (the existing
contract) *and* immediately expose a truthful, browsable provisional catalogue,
without running the expensive legacy matcher on the page load.

These tests pin:
  * the single switch (authoritative vs provisional) and where it flips;
  * that the provisional catalogue is browsable in full, not a capped peek;
  * that no provisional card claims confirmation, a band, or a match;
  * that no old-target occupational evidence reaches the new target's view;
  * that the authoritative feed still wins the moment a generation activates.
"""
from unittest import mock

from django.contrib.auth.models import User
from django.test import TestCase

from core.models import (
    CareerPreference, Opportunity, Profile, TaxonomyConcept, TaxonomyLabel,
    TaxonomyRelease, TargetOpportunityRelationshipAssessment,
)
from core.services.feed_v2 import (
    build_profile_generation, has_authored_targets, v2_feed_queryset,
)
from core.services.occupational_resolution import (
    maintain_target_resolution_containers,
)
from core.taxonomies.normalization import normalize_term
from core.test_support import create_professional_opportunity
from core.views import _authoritative_feed_ready


class ProvisionalFeedContractTests(TestCase):
    def setUp(self):
        self.releases = {
            source: TaxonomyRelease.objects.create(
                source=source, version=f'prov-{source}', is_active=True,
                source_url=f'https://taxonomy.test/{source}',
            )
            for source in ('ofo', 'esco')
        }
        self.profile = Profile.objects.create(
            user=User.objects.create_user('provisional-user', password='password'))
        self.client.force_login(self.profile.user)
        self.set_target('Data Analyst')
        # Enough listings that a fixed 24-item peek is visibly not the whole
        # provisional experience.
        self.jobs = [
            self.job(f'Data Analyst Role {index:02d}') for index in range(30)
        ]

    def concept(self, source, label):
        concept = TaxonomyConcept.objects.create(
            release=self.releases[source],
            external_id=f'{source}:{label}:{TaxonomyConcept.objects.count()}',
            kind='occupation', preferred_label=label,
            normalized_label=normalize_term(label), metadata={},
        )
        TaxonomyLabel.objects.create(
            concept=concept, label=label,
            normalized_label=normalize_term(label), label_type='preferred',
        )
        return concept

    def job(self, title):
        return create_professional_opportunity(
            title=title, description='Source-grounded responsibilities.')

    def set_target(self, text):
        preference, _created = CareerPreference.objects.update_or_create(
            profile=self.profile, defaults={'target_roles': text})
        self.preference = preference
        return preference

    def build(self):
        return build_profile_generation(self.profile)
# ------------------------------------------------------------------ switch

    def test_switch_is_false_before_any_generation_exists(self):
        self.assertTrue(has_authored_targets(self.profile))
        self.assertFalse(_authoritative_feed_ready(self.profile))

    def test_switch_becomes_true_only_after_activation(self):
        self.build()
        self.assertTrue(_authoritative_feed_ready(self.profile))

    def test_target_change_flips_the_switch_back_immediately(self):
        self.build()
        self.assertTrue(_authoritative_feed_ready(self.profile))

        self.set_target('Supply Chain Analyst')

        self.assertFalse(_authoritative_feed_ready(self.profile))
        self.assertEqual(list(v2_feed_queryset(self.profile)), [])

    def test_build_transition_state_does_not_make_the_feed_authoritative(self):
        """A BUILDING generation is not authoritative, so provisional stays on."""
        self.build()
        generation = self.profile.feed_state.active_generation
        generation.state = 'BUILDING'
        generation.save(update_fields=['state', 'updated_at'])
        self.assertFalse(_authoritative_feed_ready(self.profile))

    # ------------------------------------------------------------ provisional

    def test_provisional_page_browses_more_than_the_uncertain_cap(self):
        response = self.client.get('/opportunities/')
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context['has_targets'])
        self.assertTrue(response.context['provisional_primary'])
        # 30 eligible jobs: a 24-item peek would leave the rest unreachable.
        self.assertEqual(response.context['uncertain_count'], 30)
        self.assertEqual(len(response.context['uncertain_results']), 24)
        # ...and page two reaches the remainder.
        second = self.client.get('/opportunities/', {'page': 2})
        self.assertEqual(len(second.context['uncertain_results']), 6)

    def test_provisional_makes_no_occupational_claim(self):
        response = self.client.get('/opportunities/')
        self.assertEqual(list(response.context['results']), [])
        self.assertEqual(list(response.context['discovery_results']), [])
        for card in response.context['uncertain_results']:
            self.assertEqual(card['discovery_label'], 'Not yet assessed')
            self.assertFalse(card['is_confirmed_relationship'])
            self.assertEqual(card['relationship_lines'], [])
            self.assertEqual(card['title_only_lines'], [])
            self.assertNotIn('Band', card['fit_label'])
        self.assertNotContains(response, 'Exact match')
        # The confirmed-area subtitle is only rendered when that area has rows;
        # with none, the page states the picture is incomplete instead.
        self.assertNotContains(response, 'were confirmed as an exact')
        self.assertContains(response, 'not yet assessed')

    def test_no_target_profile_gets_a_paginated_general_catalogue(self):
        self.set_target('')
        response = self.client.get('/opportunities/')
        self.assertFalse(response.context['has_targets'])
        self.assertEqual(response.context['confirmed_count'], 0)
        self.assertEqual(len(response.context['results']), 24)
        self.assertContains(response, 'no target roles are saved yet')

    # ----------------------------------------------------------------- safety

    def test_old_target_evidence_never_reaches_the_new_target_view(self):
        """Old-target assessments must not appear for the new target."""
        self.concept('esco', 'Data Analyst')
        self.build()
        old_targets = maintain_target_resolution_containers(self.preference)
        self.assertTrue(
            TargetOpportunityRelationshipAssessment.objects.filter(
                target_resolution__in=old_targets).exists())

        self.set_target('Supply Chain Analyst')

        response = self.client.get('/opportunities/')
        self.assertEqual(list(response.context['results']), [])
        self.assertTrue(response.context['provisional_primary'])
        # Every provisional card is unassessed, so no old verdict is presented.
        for card in response.context['uncertain_results']:
            self.assertFalse(card['is_confirmed_relationship'])
            self.assertEqual(card['relationship_lines'], [])
        # The stored rows are still on disk, keyed to the old target.
        self.assertTrue(
            TargetOpportunityRelationshipAssessment.objects.filter(
                target_resolution__in=old_targets).exists())

    def test_authoritative_feed_returns_immediately_after_activation(self):
        # Target text and job title must resolve to the same concept for the resolver
        # to confirm a same-concept EXACT relationship.
        self.concept('esco', 'Data Analyst Role 00')
        self.concept('ofo', 'Data Analyst Role 00')
        self.set_target('Data Analyst Role 00')
        self.build()
        response = self.client.get('/opportunities/')
        self.assertFalse(response.context['provisional_primary'])
        self.assertEqual(len(response.context['results']), 1)
        card = response.context['results'][0]
        self.assertTrue(card['is_confirmed_relationship'])
        self.assertEqual(card['discovery_label'], 'Exact match')

    # -------------------------------------------------------------- dashboard

    def test_dashboard_does_not_run_the_matcher_without_a_generation(self):
        with mock.patch(
                'core.views.get_matched_opportunities',
                side_effect=AssertionError('matcher must not run')):
            response = self.client.get('/dashboard/')
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.context['has_professional_signal'])
        self.assertEqual(len(response.context['top_matches']), 5)
        for card in response.context['top_matches']:
            self.assertEqual(card['discovery_label'], 'Not yet assessed')
        self.assertContains(response, 'Recent Opportunities')
        self.assertNotContains(response, 'Best Matches')

    def test_dashboard_keeps_the_matcher_when_authoritative(self):
        self.concept('esco', 'Data Analyst')
        self.build()
        response = self.client.get('/dashboard/')
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context['has_professional_signal'])
        self.assertContains(response, 'Best Matches')

    def test_dashboard_target_change_drops_back_to_catalogue_wording(self):
        self.concept('esco', 'Data Analyst')
        self.build()
        self.assertTrue(
            self.client.get('/dashboard/').context['has_professional_signal'])

        self.set_target('Supply Chain Analyst')

        response = self.client.get('/dashboard/')
        self.assertFalse(response.context['has_professional_signal'])
        self.assertNotContains(response, 'Best Matches')
        self.assertContains(response, 'Recent Opportunities')