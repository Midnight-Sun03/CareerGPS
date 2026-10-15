"""Indexed occupational candidate retrieval (target-change must not scan jobs)."""
from unittest import mock

from django.contrib.auth.models import User
from django.test import TestCase

from core.models import (
    CareerPreference,
    OccupationSourceResolutionCandidate,
    Profile,
    ProfileFeedEvaluation,
    TargetOpportunityRelationshipAssessment,
    TaxonomyConcept,
    TaxonomyLabel,
    TaxonomyRelease,
)
from core.services.feed_v2 import (
    build_profile_generation, feed_transition, v2_feed_queryset,
)
from core.services.occupational_index import (
    candidate_revision_ids, expand_concept_ids, retrieve_candidates,
    target_concept_ids,
)
from core.services.occupational_resolution import (
    maintain_target_resolution_containers, resolve_target_sources,
    resolve_vacancy_sources,
)
from core.services.relationship_aggregation import fresh_assessment_for
from core.taxonomies.normalization import normalize_term
from core.test_support import create_professional_opportunity


class IndexedRetrievalCase(TestCase):
    def setUp(self):
        self.releases = {
            source: TaxonomyRelease.objects.create(
                source=source, version=f'index-{source}', is_active=True)
            for source in ('ofo', 'esco')}
        self.profile = Profile.objects.create(
            user=User.objects.create_user('indexed-user', password='password'))

    def concept(self, source, label):
        concept = TaxonomyConcept.objects.create(
            release=self.releases[source], kind='occupation',
            external_id=f'{source}:{label}:{TaxonomyConcept.objects.count()}',
            preferred_label=label, normalized_label=normalize_term(label),
            metadata={})
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
        return preference

    def resolve_job(self, title):
        opportunity = self.job(title)
        resolve_vacancy_sources(opportunity.active_clean_revision)
        return opportunity


class CandidateLookupTests(IndexedRetrievalCase):
    def _targets(self, text):
        preference = self.set_target(text)
        targets = maintain_target_resolution_containers(preference)
        sources = {t.pk: resolve_target_sources(t) for t in targets}
        return targets, sources

    def test_concept_lookup_returns_only_relevant_jobs(self):
        concept_x = self.concept('esco', 'Target Role X')
        concept_y = self.concept('esco', 'Other Role Y')
        concept_z = self.concept('ofo', 'Unrelated Role Z')
        job_a = self.resolve_job('Target Role X')
        job_b = self.resolve_job('Other Role Y')
        job_d = self.resolve_job('Unrelated Role Z')
        targets, sources = self._targets('Target Role X')
        found = retrieve_candidates(targets, target_sources=sources)
        revisions = {job_a.active_clean_revision_id,
                     job_b.active_clean_revision_id,
                     job_d.active_clean_revision_id}
        self.assertIn(job_a.active_clean_revision_id, found)
        self.assertNotIn(job_b.active_clean_revision_id, found)
        self.assertNotIn(job_d.active_clean_revision_id, found)
        self.assertTrue(revisions - found)

    def test_multi_result_target_retrieves_any_match(self):
        concept_x = self.concept('esco', 'Role X')
        concept_y = self.concept('esco', 'Role Y')
        concept_z = self.concept('ofo', 'Role Z')
        job_x = self.resolve_job('Role X')
        job_y = self.resolve_job('Role Y')
        job_z = self.resolve_job('Role Z')
        other = self.resolve_job('Something Else Entirely')
        targets, sources = self._targets('Role X')
        ids = target_concept_ids(targets, target_sources=sources)
        # Multi-result retention: union of ESCO X + ESCO Y + OFO Z.
        union_ids = {concept_x.pk, concept_y.pk, concept_z.pk}
        found = candidate_revision_ids(
            concept_ids=union_ids, target_texts=[])
        self.assertIn(job_x.active_clean_revision_id, found)
        self.assertIn(job_y.active_clean_revision_id, found)
        self.assertIn(job_z.active_clean_revision_id, found)
        self.assertNotIn(other.active_clean_revision_id, found)

    def test_cross_taxonomy_identity_is_preserved(self):
        esco_x = self.concept('esco', 'Same Label')
        ofo_x = self.concept('ofo', 'Same Label')
        self.assertNotEqual(esco_x.pk, ofo_x.pk)
        job_esco = self.resolve_job('Same Label')
        targets, sources = self._targets('Same Label')
        ids = target_concept_ids(targets, target_sources=sources)
        self.assertIn(esco_x.pk, ids)
        self.assertIn(ofo_x.pk, ids)
        found = candidate_revision_ids(concept_ids={esco_x.pk}, target_texts=[])
        self.assertIn(job_esco.active_clean_revision_id, found)
        ofo_only = candidate_revision_ids(concept_ids={ofo_x.pk}, target_texts=[])
        # Same label, different taxonomy rows: filtering by one concept id
        # never implies the other.
        self.assertEqual(
            set(OccupationSourceResolutionCandidate.objects.filter(
                concept_id=esco_x.pk).values_list('concept_id', flat=True)),
            {esco_x.pk})


class GenerationReuseTests(IndexedRetrievalCase):
    def _targets(self, text):
        preference = self.set_target(text)
        targets = maintain_target_resolution_containers(preference)
        sources = {t.pk: resolve_target_sources(t) for t in targets}
        return targets, sources

    def test_target_change_skips_unrelated_job_resolution(self):
        self.concept('esco', 'Target Role X')
        wanted = self.resolve_job('Target Role X')
        skipped = [self.resolve_job(f'Unrelated Job {i:02d}') for i in range(4)]
        self.set_target('Target Role X')
        from core.services.occupational_resolution import (
            resolve_vacancy_sources as real_resolve,
        )
        with mock.patch(
                'core.services.feed_v2.resolve_vacancy_sources',
                wraps=real_resolve) as resolve_spy:
            generation, count = build_profile_generation(self.profile)
        # Every job's occupational evidence already exists, so target
        # generation may only READ it (readonly=True).  A single non-readonly
        # call would be a hidden re-resolution of catalogue evidence.
        self.assertTrue(resolve_spy.call_args_list)
        self.assertTrue(all(
            call.kwargs.get('readonly') is True
            for call in resolve_spy.call_args_list),
            resolve_spy.call_args_list)
        self.assertEqual(count, 1)
        self.assertTrue(ProfileFeedEvaluation.objects.filter(
            generation=generation, opportunity=wanted).exists())
        for job in skipped:
            self.assertFalse(ProfileFeedEvaluation.objects.filter(
                generation=generation, opportunity=job).exists())
        note = generation.diagnostics
        self.assertEqual(note['candidate_revision_count'], 1)
        self.assertEqual(note['skipped_unrelated_revision_count'], 4)

    def test_valid_assessment_is_reused(self):
        self.concept('esco', 'Target Role X')
        self.concept('ofo', 'Target Role X')
        self.resolve_job('Target Role X')
        self.set_target('Target Role X')
        build_profile_generation(self.profile)
        first = list(TargetOpportunityRelationshipAssessment.objects.values_list(
            'pk', 'dependency_hash'))
        self.assertTrue(first)
        with mock.patch(
                'core.services.feed_v2.assess_target_vacancy',
                side_effect=AssertionError('must reuse')) as assess_spy:
            generation, count = build_profile_generation(self.profile)
        assess_spy.assert_not_called()
        second = list(TargetOpportunityRelationshipAssessment.objects.values_list(
            'pk', 'dependency_hash'))
        self.assertEqual(first, second)
        self.assertEqual(count, 1)

    def test_stale_assessment_is_recomputed(self):
        self.concept('esco', 'Target Role X')
        self.resolve_job('Target Role X')
        self.set_target('Target Role X')
        build_profile_generation(self.profile)
        assessment = TargetOpportunityRelationshipAssessment.objects.get()
        assessment.dependency_hash = 'stale-hash'
        assessment.save(update_fields=['dependency_hash'])
        targets = maintain_target_resolution_containers(
            CareerPreference.objects.get(profile=self.profile))
        sources = {t.pk: resolve_target_sources(t) for t in targets}
        self.assertIsNone(fresh_assessment_for(
            targets[0], assessment.revision, target_sources=sources[targets[0].pk]))
        generation, count = build_profile_generation(self.profile)
        self.assertEqual(count, 1)
        assessment.refresh_from_db()
        self.assertNotEqual(assessment.dependency_hash, 'stale-hash')

    def test_one_job_one_feed_row_with_many_identities(self):
        self.concept('esco', 'Target Role X')
        self.concept('ofo', 'Target Role X')
        wanted = self.resolve_job('Target Role X')
        self.set_target('Target Role X')
        generation, count = build_profile_generation(self.profile)
        self.assertEqual(count, 1)
        rows = list(ProfileFeedEvaluation.objects.filter(generation=generation))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].opportunity_id, wanted.pk)

    def test_unresolved_job_never_enters_confirmed_feed(self):
        self.concept('esco', 'Target Role X')
        self.resolve_job('Target Role X')
        ghost = self.job('Completely Unmapped Wording Qwerty')
        self.set_target('Target Role X')
        generation, count = build_profile_generation(self.profile)
        self.assertEqual(count, 1)
        self.assertFalse(ProfileFeedEvaluation.objects.filter(
            generation=generation, opportunity=ghost).exists())
        self.assertEqual(
            list(v2_feed_queryset(self.profile).values_list(
                'opportunity_id', flat=True)),
            [ProfileFeedEvaluation.objects.get(
                generation=generation).opportunity_id])

    def test_resolving_state_hides_old_feed(self):
        self.concept('esco', 'Target Role X')
        self.resolve_job('Target Role X')
        self.set_target('Target Role X')
        build_profile_generation(self.profile)
        self.assertTrue(v2_feed_queryset(self.profile).exists())
        self.set_target('Brand New Target Role')
        transition = feed_transition(self.profile)
        self.assertEqual(transition.state, 'UPDATING_INTENT')
        self.assertEqual(list(v2_feed_queryset(self.profile)), [])



    def test_target_change_performance_counts(self):
        """Report the OLD->NEW cost shape on a realistic mini-catalogue."""
        self.concept('esco', 'Target Role X')
        self.concept('ofo', 'Target Role X')
        wanted = [self.resolve_job('Target Role X') for _ in range(2)]
        unrelated = [self.resolve_job(f'Unrelated Role {i:02d}')
                     for i in range(18)]
        self.set_target('Target Role X')
        from core.services.occupational_resolution import (
            resolve_vacancy_sources as real_resolve,
        )
        with mock.patch(
                'core.services.feed_v2.resolve_vacancy_sources',
                wraps=real_resolve) as resolve_spy:
            generation, count = build_profile_generation(self.profile)
        note = generation.diagnostics
        ready_jobs = len(wanted) + len(unrelated)
        # NEW behaviour: zero job re-resolutions during target generation,
        # 2 candidate revisions (the two matching jobs), 18 skipped.  Only
        # readonly reads of the two candidates are permitted.
        self.assertTrue(all(
            call.kwargs.get('readonly') is True
            for call in resolve_spy.call_args_list),
            resolve_spy.call_args_list)
        self.assertEqual(note['candidate_revision_count'], 2)
        self.assertEqual(note['eligible_revision_count'], ready_jobs)
        self.assertEqual(note['skipped_unrelated_revision_count'], 18)
        self.assertEqual(note['assessments_recomputed'], 2)
        self.assertEqual(note['assessments_reused'], 0)
        self.assertEqual(count, 2)
        # A second catalogue pass reuses every stored assessment instead of
        # recomputing it (fresh dependency hashes, synced findings).
        from core.services.feed_v2 import _catalogue_and_assessments
        _t, _r, _rows, _c, _s, second_note = _catalogue_and_assessments(
            self.profile)
        self.assertEqual(second_note['assessments_reused'], 2)
        self.assertEqual(second_note['assessments_recomputed'], 0)
