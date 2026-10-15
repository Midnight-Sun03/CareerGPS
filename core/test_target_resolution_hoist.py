"""Regression coverage for reusing one pass's already-resolved target sources.

`_catalogue_and_assessments` compares every revision against the same authored
target text, so the target's own per-source resolution is resolved once and
passed into `assess_target_vacancy` instead of being recomputed per revision.

These tests pin that the hoisted data is genuinely the same resolution the
per-pair call used to produce, that the redundancy is really gone, that results
are unchanged, and above all that a target keeping several concepts still keeps
all of them.
"""
from unittest import mock

from django.contrib.auth.models import User
from django.test import TestCase

from core.models import (
    CareerPreference,
    OccupationSourceResolution,
    Profile,
    TaxonomyConcept,
    TaxonomyLabel,
    TaxonomyRelease,
)
from core.services import occupational_resolution as ores
from core.services import relationship_aggregation as ra
from core.services.feed_v2 import _catalogue_and_assessments
from core.services.occupational_resolution import (
    maintain_target_resolution_containers,
    resolve_target_sources,
)
from core.services.relationship_aggregation import assess_target_vacancy
from core.taxonomies.normalization import normalize_term
from core.test_support import create_professional_opportunity


class HoistedTargetResolutionTests(TestCase):
    def setUp(self):
        self.releases = {
            source: TaxonomyRelease.objects.create(
                source=source, version=f'hoist-{source}', is_active=True,
                source_url=f'https://taxonomy.test/{source}',
            )
            for source in ('ofo', 'esco')
        }
        self.profile = Profile.objects.create(
            user=User.objects.create_user('hoist-user', password='password'))

    def concept(self, source, label, *, label_type='preferred'):
        concept = TaxonomyConcept.objects.create(
            release=self.releases[source],
            external_id=f'{source}:{label}:{TaxonomyConcept.objects.count()}',
            kind='occupation', preferred_label=label,
            normalized_label=normalize_term(label), metadata={},
        )
        TaxonomyLabel.objects.create(
            concept=concept, label=label,
            normalized_label=normalize_term(label), label_type=label_type,
        )
        return concept

    def label(self, concept, label, label_type='alternative'):
        return TaxonomyLabel.objects.create(
            concept=concept, label=label,
            normalized_label=normalize_term(label), label_type=label_type,
        )

    def target(self, text):
        preference = CareerPreference.objects.create(
            profile=self.profile, target_roles=text, confirmed_by_user=True)
        return maintain_target_resolution_containers(preference)[0]

    def revision(self, title, description='Source-grounded responsibilities.'):
        opportunity = create_professional_opportunity(
            title=title, description=description)
        return opportunity.active_clean_revision

    def target_rows(self):
        return list(OccupationSourceResolution.objects.filter(
            target_resolution__profile=self.profile)
            .order_by('pk').values_list('target_resolution_id', flat=True)
            .distinct())

    def test_pass_resolves_the_target_once_not_once_per_revision(self):
        """The redundancy is gone: one resolution per target for a whole pass."""
        self.concept('esco', 'Data Engineer')
        self.concept('esco', 'Software Engineer')
        self.target('Data Engineer')
        for _ in range(3):
            self.revision('Data Engineer')

        real = ores.resolve_source
        calls = []

        def counted(**kwargs):
            if kwargs.get('target_resolution'):
                calls.append(kwargs['target_resolution'].pk)
            return real(**kwargs)

        with mock.patch.object(ores, 'resolve_source', side_effect=counted):
            _catalogue_and_assessments(self.profile)

        self.assertTrue(calls, 'the target must still be resolved')
        # Every resolution in the pass belongs to the one authored target, and
        # there are exactly two: one per taxonomy source, not one per revision.
        self.assertEqual(len(calls), len(ores.SOURCE_VERSIONS))
        self.assertEqual(set(calls), set(self.target_rows()))

    def test_pre_resolved_data_is_the_same_resolution(self):
        """Passing the rows in gives the identical result to resolving inline."""
        self.concept('esco', 'Data Engineer')
        target = self.target('Data Engineer')
        revision = self.revision('Data Engineer')

        inline = assess_target_vacancy(target, revision)
        resolved = resolve_target_sources(target)
        hoisted = assess_target_vacancy(target, revision, resolved)

        self.assertEqual(hoisted.pk, inline.pk)
        self.assertEqual(hoisted.dependency_hash, inline.dependency_hash)
        self.assertEqual(hoisted.final_class, inline.final_class)
        self.assertEqual(hoisted.reason_code, inline.reason_code)
        self.assertEqual(hoisted.relationship_count, inline.relationship_count)
        self.assertEqual(hoisted.esco_comparison, inline.esco_comparison)
        self.assertEqual(hoisted.ofo_comparison, inline.ofo_comparison)

    def test_omitting_the_argument_still_resolves_inline(self):
        """Every existing caller keeps the original behaviour unchanged."""
        self.concept('esco', 'Data Engineer')
        target = self.target('Data Engineer')
        revision = self.revision('Data Engineer')

        with mock.patch.object(
                ra, 'resolve_target_sources',
                wraps=ra.resolve_target_sources) as resolver:
            first = assess_target_vacancy(target, revision)
        resolver.assert_called_once_with(target)

        with mock.patch.object(
                ra, 'resolve_target_sources',
                wraps=ra.resolve_target_sources) as resolver:
            again = assess_target_vacancy(target, revision)
        resolver.assert_called_once_with(target)
        self.assertEqual(again.pk, first.pk)

    def test_supplied_sources_are_not_re_resolved(self):
        """A supplied resolution short-circuits resolution entirely."""
        self.concept('esco', 'Data Engineer')
        target = self.target('Data Engineer')
        revision = self.revision('Data Engineer')
        resolved = resolve_target_sources(target)

        with mock.patch.object(
                ra, 'resolve_target_sources',
                side_effect=AssertionError('must not re-resolve')) as resolver:
            assessment = assess_target_vacancy(target, revision, resolved)
        resolver.assert_not_called()
        self.assertIsNotNone(assessment)

    def test_multi_concept_target_keeps_every_concept(self):
        """A target resolving to two concepts still compares against both."""
        accountant = self.concept('esco', 'accountant')
        controller = self.concept('esco', 'financial controller')
        self.label(controller, 'accountant', 'alternative')
        target = self.target('accountant')
        resolved = resolve_target_sources(target)
        revision = self.revision('accountant')

        resolved_esco = resolved['esco']
        self.assertEqual(resolved_esco.state, 'RESOLVED')
        # Ambiguity keeps every retained result and selects no favourite.
        self.assertIsNone(resolved_esco.selected_concept_id)
        self.assertEqual(resolved_esco.result_count, 2)
        self.assertEqual(
            set(resolved_esco.candidates.values_list('concept_id', flat=True)),
            {accountant.pk, controller.pk})

        hoisted = assess_target_vacancy(target, revision, resolved)
        inline = assess_target_vacancy(target, revision)
        for assessment in (hoisted, inline):
            self.assertEqual(
                sorted(assessment.esco_comparison[
                    'target_candidate_concept_ids']),
                sorted([accountant.pk, controller.pk]))

    def test_cached_and_uncached_paths_both_still_work(self):
        """The Phase 22 fast path and a forced recompute both still behave."""
        self.concept('esco', 'Data Engineer')
        target = self.target('Data Engineer')
        revision = self.revision('Data Engineer')
        resolved = resolve_target_sources(target)

        first = assess_target_vacancy(target, revision, resolved)
        again = assess_target_vacancy(target, revision, resolved)
        self.assertEqual(again.pk, first.pk)
        self.assertEqual(again.findings.count(), first.findings.count())

        from core.models import TargetOpportunityRelationshipAssessment
        TargetOpportunityRelationshipAssessment.objects.filter(
            pk=first.pk).update(dependency_hash='0' * 64)
        rebuilt = assess_target_vacancy(target, revision, resolved)
        self.assertNotEqual(rebuilt.dependency_hash, '0' * 64)
        self.assertEqual(rebuilt.final_class, first.final_class)

    def test_shared_sources_are_never_mutated_between_pairs(self):
        """Reusing one mapping across revisions must not alter stored state."""
        self.concept('esco', 'Data Engineer')
        self.concept('esco', 'Software Engineer')
        target = self.target('Data Engineer')
        resolved = resolve_target_sources(target)
        snapshot = {source: (row.pk, row.dependency_hash)
                    for source, row in resolved.items()}

        for title in ('Data Engineer', 'Software Engineer', 'Data Analyst'):
            assess_target_vacancy(target, self.revision(title), resolved)

        for source, row in resolved.items():
            row.refresh_from_db()
            self.assertEqual(row.pk, snapshot[source][0])
            self.assertEqual(row.dependency_hash, snapshot[source][1])