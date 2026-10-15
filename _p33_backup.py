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