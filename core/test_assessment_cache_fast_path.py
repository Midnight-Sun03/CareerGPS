"""Regression coverage for the assessment-cache fast path.

`assess_target_vacancy` validates its stored verdict against the dependency hash
*before* it computes any relationship comparison.  The hash covers everything
`compare_source` and `aggregate_relationship_set` would read, so a current
verdict can be reused without recomputing it, and without changing the answer.

These tests pin that boundary from both sides: a fresh cache hit must not enter
the comparison at all, a stale hash must still recompute, a verdict whose
finding rows disagree with its own summary must still be repaired, and reusing a
verdict must never narrow the target-by-vacancy concept cross product.
"""
from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import TestCase

from core.models import (
    CareerPreference,
    OccupationRelationshipRule,
    OpportunityTitleResolution,
    Profile,
    TargetOpportunityRelationshipAssessment,
    TaxonomyConcept,
    TaxonomyLabel,
    TaxonomyRelation,
    TaxonomyRelease,
)
from core.professional_policies import (
    CANDIDATE_RELATIONSHIP_POLICY_VERSION,
    TITLE_MATCH_TIER,
)
from core.services import relationship_aggregation
from core.services.occupational_resolution import (
    maintain_target_resolution_containers,
)
from core.services.relationship_aggregation import assess_target_vacancy
from core.taxonomies.normalization import normalize_term
from core.test_support import create_professional_opportunity


DIRECT_RULE_FILTER = {
    'subject_kind': 'occupation',
    'object_kind': 'occupation',
    'metadata_relationship': 'source_broader',
}


class AssessmentCacheFastPathTests(TestCase):
    def setUp(self):
        self.releases = {
            source: TaxonomyRelease.objects.create(
                source=source, version=f'fastpath-{source}', is_active=True,
                source_url=f'https://taxonomy.test/{source}',
            )
            for source in ('ofo', 'esco')
        }
        self.profile = Profile.objects.create(
            user=User.objects.create_user('fastpath-user', password='password'))

    def concept(self, source, label, *, external_id=None, label_type='preferred',
                metadata=None):
        concept = TaxonomyConcept.objects.create(
            release=self.releases[source],
            external_id=external_id or f'{source}:{label}:{TaxonomyConcept.objects.count()}',
            kind='occupation',
            preferred_label=label,
            normalized_label=normalize_term(label),
            metadata=metadata or {},
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

    def direct_rule(self, name='fastpath-esco-direct-broader'):
        return OccupationRelationshipRule.objects.create(
            policy_version=CANDIDATE_RELATIONSHIP_POLICY_VERSION,
            name=name,
            evidence_source='taxonomy_relation', taxonomy_source='esco',
            evidence_relation_type='broader',
            candidate_relationship_type='SAFE_BROADER_NARROWER',
            direction='bidirectional', candidate_class='RELATED',
            admission_allowed=True, rationale='Verified direct occupation link.',
            provenance={'evidence_filter': DIRECT_RULE_FILTER},
        )

    def reuse(self, target, revision):
        """Reuse the stored verdict, proving no comparison was entered."""
        with patch.object(
                relationship_aggregation, 'compare_source',
                side_effect=AssertionError('compare_source must not run'),
        ) as compare, patch.object(
                relationship_aggregation, 'aggregate_relationship_set',
                side_effect=AssertionError('aggregate_relationship_set must not run'),
        ) as aggregate:
            reused = assess_target_vacancy(target, revision)
        compare.assert_not_called()
        aggregate.assert_not_called()
        return reused

    def test_fresh_cache_hit_returns_without_comparing_or_aggregating(self):
        """A current stored verdict is reused as-is, with no comparison run.

        The first call builds the verdict.  The second must recognise the
        identical dependency hash and return that same row, and neither
        `compare_source` nor `aggregate_relationship_set` may be entered.
        """
        self.concept('esco', 'Data Analyst')
        target = self.target('Data Analyst')
        revision = self.revision('Data Analyst')
        stored = assess_target_vacancy(target, revision)
        self.assertEqual(stored.final_class, 'EXACT')
        self.assertEqual(stored.relationship_count, 1)

        reused = self.reuse(target, revision)
        # The stored row itself is returned, not a rebuilt copy of it.
        self.assertEqual(reused.pk, stored.pk)
        self.assertEqual(reused.final_class, 'EXACT')
        self.assertEqual(reused.dependency_hash, stored.dependency_hash)
        self.assertEqual(reused.relationship_count, stored.relationship_count)
        self.assertEqual(reused.findings.count(), stored.findings.count())
        self.assertEqual(reused.assessed_at, stored.assessed_at)

    def test_repeated_reuse_never_grows_or_rewrites_anything(self):
        """Reusing a current verdict leaves the row and its evidence untouched."""
        self.concept('esco', 'Data Analyst')
        target = self.target('Data Analyst')
        revision = self.revision('Data Analyst')
        stored = assess_target_vacancy(target, revision)
        evidence_before = sorted(stored.findings.values_list(
            'pk', 'target_concept_id', 'job_concept_id'))
        updated_before = stored.assessed_at

        for _attempt in range(3):
            self.assertEqual(assess_target_vacancy(target, revision).pk, stored.pk)

        self.assertEqual(
            TargetOpportunityRelationshipAssessment.objects.count(), 1)
        self.assertEqual(
            sorted(stored.findings.values_list(
                'pk', 'target_concept_id', 'job_concept_id')),
            evidence_before)
        self.assertEqual(
            assess_target_vacancy(target, revision).assessed_at, updated_before)

    def test_stale_dependency_hash_still_recomputes(self):
        """A verdict whose hash no longer matches is recomputed, not reused."""
        self.concept('esco', 'Data Analyst')
        target = self.target('Data Analyst')
        revision = self.revision('Data Analyst')
        stored = assess_target_vacancy(target, revision)
        self.assertEqual(stored.final_class, 'EXACT')

        # Simulate a verdict left behind by an older policy version.  Its
        # findings agree with its own summary, so only the dependency hash can
        # reveal that it is stale.
        TargetOpportunityRelationshipAssessment.objects.filter(
            pk=stored.pk).update(dependency_hash='0' * 64)

        with patch.object(
                relationship_aggregation, 'aggregate_relationship_set',
                wraps=relationship_aggregation.aggregate_relationship_set,
        ) as aggregate:
            refreshed = assess_target_vacancy(target, revision)
        aggregate.assert_called()
        self.assertNotEqual(refreshed.dependency_hash, '0' * 64)
        self.assertEqual(refreshed.pk, stored.pk)
        self.assertEqual(refreshed.final_class, 'EXACT')
        self.assertEqual(refreshed.findings.count(), 1)

    def test_policy_change_invalidates_the_cached_verdict(self):
        """New approved policy evidence is a real dependency, not a stale cache.

        The pair first resolves to two different occupations with no approved
        link, so nothing is confirmed.  Approving the direct occupation link is
        exactly the policy change the dependency hash exists to detect, so the
        earlier verdict must not survive it.
        """
        analyst = self.concept('esco', 'Data Analyst')
        scientist = self.concept('esco', 'Data Scientist')
        target = self.target('Data Analyst')
        revision = self.revision('Data Scientist')
        first = assess_target_vacancy(target, revision)
        self.assertEqual(first.relationship_count, 0)

        TaxonomyRelation.objects.create(
            subject=scientist, relation_type='broader', object=analyst,
            metadata={'relationship': 'source_broader'})
        self.direct_rule()
        second = assess_target_vacancy(target, revision)
        self.assertNotEqual(second.dependency_hash, first.dependency_hash)
        self.assertEqual(second.final_class, 'RELATED')
        self.assertEqual(second.relationship_count, 1)
        self.assertEqual(second.findings.count(), 1)

    def test_finding_count_mismatch_falls_through_to_self_heal(self):
        """A current verdict with missing findings is repaired, not returned.

        This is the behaviour a fast path must not skip: the hash still matches,
        but the confirmed relationship rows no longer agree with the stored
        summary, so the comparisons are recomputed and the findings are rebuilt
        from them rather than trusted.
        """
        self.concept('esco', 'Data Analyst')
        target = self.target('Data Analyst')
        revision = self.revision('Data Analyst')
        stored = assess_target_vacancy(target, revision)
        self.assertEqual(stored.relationship_count, 1)
        self.assertEqual(stored.findings.count(), 1)

        # The summary still claims one relationship; the row behind it is gone.
        stored.findings.all().delete()
        self.assertEqual(stored.findings.count(), 0)

        with patch.object(
                relationship_aggregation, 'compare_source',
                wraps=relationship_aggregation.compare_source,
        ) as compare:
            healed = assess_target_vacancy(target, revision)
        compare.assert_called()
        self.assertEqual(healed.pk, stored.pk)
        self.assertEqual(healed.dependency_hash, stored.dependency_hash)
        self.assertEqual(healed.relationship_count, 1)
        self.assertEqual(healed.findings.count(), 1)

    def test_self_heal_does_not_trust_a_summary_that_outruns_its_findings(self):
        """A summary running ahead of its findings must not be taken at face value.

        The stored count is only ever a summary of the finding rows, so a count
        that disagrees with them is not evidence that rows are missing.  The
        repair is driven by the freshly computed comparisons, and the finding
        rows -- the authoritative record -- are rebuilt to match them.  The
        stored summary is left exactly as the pre-existing recomputation path
        leaves it, so the fast path changes nothing about how a mismatched
        summary is handled.
        """
        self.concept('esco', 'Data Analyst')
        target = self.target('Data Analyst')
        revision = self.revision('Data Analyst')
        stored = assess_target_vacancy(target, revision)
        TargetOpportunityRelationshipAssessment.objects.filter(
            pk=stored.pk).update(relationship_count=99)
        stored.refresh_from_db()
        self.assertNotEqual(stored.relationship_count, stored.findings.count())

        with patch.object(
                relationship_aggregation, 'compare_source',
                wraps=relationship_aggregation.compare_source,
        ) as compare:
            healed = assess_target_vacancy(target, revision)
        # A disagreement between the summary and its rows always falls through.
        compare.assert_called()
        self.assertEqual(healed.pk, stored.pk)
        # The relationships themselves are unchanged by the corrupted summary.
        self.assertEqual(healed.final_class, 'EXACT')
        self.assertEqual(healed.findings.count(), 1)
        self.assertEqual(healed.dependency_hash, stored.dependency_hash)

    def test_fast_path_preserves_the_multi_concept_cross_product(self):
        """Two target concepts times two job concepts stays all four pairings.

        The ambiguous Accountant case keeps every retained result on both sides
        and compares all of them.  The fast path must not narrow that cross
        product, and reusing a stored verdict must not change the retained
        candidate ids the stored comparison payload records.
        """
        accountant = self.concept('esco', 'accountant')
        controller = self.concept('esco', 'financial controller')
        self.label(controller, 'accountant', 'alternative')
        TaxonomyRelation.objects.create(
            subject=controller, relation_type='broader', object=accountant,
            metadata={'relationship': 'source_broader'})
        self.direct_rule('fastpath-multi-concept-broader')
        target = self.target('accountant')
        revision = self.revision('Accountant')
        stored = assess_target_vacancy(target, revision)

        # Every candidate on both sides is retained; none was chosen out.
        self.assertEqual(
            sorted(stored.esco_comparison['target_candidate_concept_ids']),
            sorted([accountant.pk, controller.pk]))
        self.assertEqual(
            sorted(stored.esco_comparison['vacancy_candidate_concept_ids']),
            sorted([accountant.pk, controller.pk]))
        self.assertEqual(stored.esco_comparison['pairing_count'], 4)
        self.assertEqual(stored.final_class, 'RELATED')

        reused = self.reuse(target, revision)
        self.assertEqual(reused.pk, stored.pk)
        # The reused row still describes the same full cross product.
        self.assertEqual(reused.esco_comparison['pairing_count'], 4)
        self.assertEqual(
            sorted(reused.esco_comparison['target_candidate_concept_ids']),
            sorted([accountant.pk, controller.pk]))
        self.assertEqual(
            sorted(reused.esco_comparison['vacancy_candidate_concept_ids']),
            sorted([accountant.pk, controller.pk]))

    def test_multi_concept_stays_multi_concept_across_a_stale_rebuild(self):
        """Recomputing a multi-concept pair must not collapse it to one concept.

        The fast path must not be the only thing respecting the cross product:
        when the hash is stale the full comparison still runs, and it must
        produce the same two-by-two candidate sets rather than picking a
        favourite.
        """
        accountant = self.concept('esco', 'accountant')
        controller = self.concept('esco', 'financial controller')
        self.label(controller, 'accountant', 'alternative')
        TaxonomyRelation.objects.create(
            subject=controller, relation_type='broader', object=accountant,
            metadata={'relationship': 'source_broader'})
        self.direct_rule('fastpath-multi-concept-stale')
        target = self.target('accountant')
        revision = self.revision('Accountant')
        stored = assess_target_vacancy(target, revision)
        TargetOpportunityRelationshipAssessment.objects.filter(
            pk=stored.pk).update(dependency_hash='0' * 64)

        rebuilt = assess_target_vacancy(target, revision)
        self.assertEqual(rebuilt.esco_comparison['pairing_count'], 4)
        self.assertEqual(
            sorted(rebuilt.esco_comparison['target_candidate_concept_ids']),
            sorted([accountant.pk, controller.pk]))
        self.assertEqual(rebuilt.final_class, 'RELATED')

    def test_ambiguous_pair_with_no_confirmation_is_reused_without_comparison(self):
        """A pair with nothing established is still reusable and still weaker.

        Both sides retained two occupations and connected none, so the stored
        verdict carries no findings at all.  Zero findings against a zero
        summary is a healthy row, so the fast path applies and the weaker
        title-only class is preserved rather than re-derived.
        """
        accountant = self.concept('esco', 'accountant')
        controller = self.concept('esco', 'financial controller')
        self.label(accountant, 'accountant', 'alternative')
        self.label(controller, 'accountant', 'alternative')
        target = self.target('accountant')
        revision = self.revision('Accountant')
        stored = assess_target_vacancy(target, revision)
        self.assertEqual(stored.relationship_count, 0)
        self.assertEqual(stored.findings.count(), 0)

        reused = self.reuse(target, revision)
        self.assertEqual(reused.pk, stored.pk)
        self.assertEqual(reused.final_class, 'TITLE_MATCH')
        self.assertEqual(reused.evidence_tier, TITLE_MATCH_TIER)
        self.assertEqual(reused.relationship_count, 0)
        self.assertEqual(reused.findings.count(), 0)
        self.assertEqual(reused.policy_evidence, [])

    def test_blocked_revision_is_still_excluded_before_any_cache_work(self):
        """The read-only seniority gate still short-circuits ahead of the cache."""
        self.concept('esco', 'Data Analyst')
        target = self.target('Data Analyst')
        blocked = self.revision('Senior Data Analyst',
                                description='No senior wording in the evidence.')
        self.assertIsNone(assess_target_vacancy(target, blocked))
        # A blocked title never reaches resolution or assessment storage.
        self.assertFalse(OpportunityTitleResolution.objects.filter(
            revision=blocked).exists())
        self.assertFalse(TargetOpportunityRelationshipAssessment.objects.filter(
            target_resolution=target).exists())
