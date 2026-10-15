"""Focused verification of the indexed candidate lookup and target-change window.

Verifies the new composite index is recorded on the model and present in the
database, that the indexed candidate universe is bounded to READY revisions that
share occupational evidence with the target, and that the target-change window
never walks the full catalogue of READY jobs.
"""
from django.contrib.auth.models import User
from django.db import connection
from django.test import TestCase

from core.models import (
    CareerPreference,
    OccupationSourceResolutionCandidate,
    Opportunity,
    Profile,
    TaxonomyConcept,
    TaxonomyLabel,
    TaxonomyRelease,
)
from core.services.occupational_index import (
    candidate_revision_ids,
    retrieve_candidates,
)
from core.services.occupational_resolution import (
    maintain_target_resolution_containers,
    resolve_target_sources,
    resolve_vacancy_sources,
)
from core.taxonomies.normalization import normalize_term
from core.test_support import create_professional_opportunity


class CandidateIndexSchemaTests(TestCase):
    def setUp(self):
        self.releases = {
            source: TaxonomyRelease.objects.create(
                source=source, version=f'lookup-{source}', is_active=True)
            for source in ('ofo', 'esco')
        }
        self.profile = Profile.objects.create(
            user=User.objects.create_user('lookup-user', password='password'))

    def test_composite_index_recorded_on_model(self):
        index = [
            idx for idx in OccupationSourceResolutionCandidate._meta.indexes
            if idx.name == 'occ_candidate_concept_idx'
        ]
        self.assertEqual(len(index), 1)
        # (concept, resolution) is stored as the FK columns concept_id,
        # resolution_id in the database.
        self.assertEqual(index[0].fields, ['concept', 'resolution'])

class CandidateUniverseLookupTests(TestCase):
    def setUp(self):
        self.releases = {
            source: TaxonomyRelease.objects.create(
                source=source, version=f'lookup-{source}', is_active=True)
            for source in ('ofo', 'esco')
        }
        self.profile = Profile.objects.create(
            user=User.objects.create_user('lookup-user', password='password'))

    def concept(self, source, label):
        concept = TaxonomyConcept.objects.create(
            release=self.releases[source], kind='occupation',
            external_id=f'{source}:{label}:{TaxonomyConcept.objects.count()}',
            preferred_label=label, normalized_label=normalize_term(label),
            metadata={})
        TaxonomyLabel.objects.create(
            concept=concept, label=label, normalized_label=normalize_term(label),
            label_type='preferred')
        return concept

    def resolve_job(self, title):
        opportunity = create_professional_opportunity(
            title=title, description='Source-grounded responsibilities.')
        # Production resolves each vacancy once at import; the fixture does
        # the same so the concept-evidence path is genuinely exercised.
        resolve_vacancy_sources(opportunity.active_clean_revision)
        return opportunity

    def set_target(self, text):
        preference, _ = CareerPreference.objects.update_or_create(
            profile=self.profile, defaults={'target_roles': text})
        return preference

    def _targets(self, text):
        preference = self.set_target(text)
        targets = maintain_target_resolution_containers(preference)
        sources = {t.pk: resolve_target_sources(t) for t in targets}
        return targets, sources

    def test_candidate_revision_ids_bounded_to_ready_revisions(self):
        concept = self.concept('esco', 'Target Role X')
        job = self.resolve_job('Target Role X')
        targets, sources = self._targets('Target Role X')
        found = retrieve_candidates(targets, target_sources=sources)
        self.assertIn(job.active_clean_revision_id, found)
        self.assertEqual(len(found), 1)

    def test_candidate_revision_ids_respects_base_ready_set(self):
        """The candidate window is the intersection of resolvable ready
        revisions with the target's occupational evidence -- never every READY
        job in the catalogue."""
        concept_a = self.concept('esco', 'Candidate A')
        concept_b = self.concept('esco', 'Candidate B')
        candidate_a_job = self.resolve_job('Candidate A')
        candidate_b_job = self.resolve_job('Candidate B')
        unrelated_job = self.resolve_job('Unrelated')

        targets, sources = self._targets('Candidate A')
        found = retrieve_candidates(targets, target_sources=sources)
        self.assertIn(candidate_a_job.active_clean_revision_id, found)
        self.assertNotIn(candidate_b_job.active_clean_revision_id, found)
        self.assertNotIn(unrelated_job.active_clean_revision_id, found)

        # Constraining the ready set to one job narrows the window to it.
        scoped = candidate_revision_ids(
            concept_ids={concept_a.pk, concept_b.pk},
            base_qs=Opportunity.objects.professionally_ready().filter(
                pk=candidate_a_job.pk))
        self.assertEqual(scoped, {candidate_a_job.active_clean_revision_id})

    def unsharded_universe(self):
        # A multi-result target: 'Role X' matches concept_x (preferred) and
        # concept_y (alternative) in esco, plus concept_z (alternative) in
        # ofo -- mirroring the hoist regression pattern.
        concept_x = self.concept('esco', 'Role X')
        concept_y = self.concept('esco', 'Role Y')
        concept_z = self.concept('ofo', 'Role Z')
        TaxonomyLabel.objects.create(
            concept=concept_y, label='Role X',
            normalized_label=normalize_term('Role X'), label_type='alternative')
        TaxonomyLabel.objects.create(
            concept=concept_z, label='Role X',
            normalized_label=normalize_term('Role X'), label_type='alternative')
        job_x = self.resolve_job('Role X')
        job_y = self.resolve_job('Role Y')
        job_z = self.resolve_job('Role Z')
        other_job = self.resolve_job('Something Else Entirely')
        return concept_x, concept_y, concept_z, job_x, job_y, job_z, other_job

    def test_candidate_universe_is_indexed_not_sharded(self):
        """A multi-result target keeps every concept's revision, and unrelated
        READY jobs never enter the window."""
        _concept_x, _concept_y, concept_z, job_x, job_y, job_z, other_job = (
            self.unsharded_universe())

        targets, sources = self._targets('Role X')
        found = retrieve_candidates(targets, target_sources=sources)
        self.assertIn(job_x.active_clean_revision_id, found)
        self.assertIn(job_y.active_clean_revision_id, found)
        self.assertIn(job_z.active_clean_revision_id, found)
        self.assertNotIn(other_job.active_clean_revision_id, found)

        # The ofo leg is genuinely indexed: filtering by concept_z reaches
        # job_z's revision.  (job_x shares that concept through the
        # deliberately shared alternative label.)
        ofo_only = candidate_revision_ids(
            concept_ids={concept_z.pk}, target_texts=())
        self.assertIn(job_z.active_clean_revision_id, ofo_only)

        # Cross-taxonomy independence: a concept no other job shares never
        # implies another job's revision.
        lone_concept = self.concept('ofo', 'Lone Occupation W')
        lone_job = self.resolve_job('Lone Occupation W')
        lone_only = candidate_revision_ids(
            concept_ids={lone_concept.pk}, target_texts=())
        self.assertEqual(
            lone_only, {lone_job.active_clean_revision_id})

    def test_composite_index_present_in_database(self):
        with connection.cursor() as cursor:
            rows = cursor.execute(
                "SELECT name, sql FROM sqlite_master "
                "WHERE type = 'index' AND name = 'occ_candidate_concept_idx'"
            ).fetchall()
        self.assertEqual(len(rows), 1)
        # SQLite may quote identifiers; compare after normalising quotes.
        self.assertIn(
            '(concept_id, resolution_id)', rows[0][1].replace('"', ''))

    def test_candidate_lookup_uses_covering_index(self):
        with connection.cursor() as cursor:
            plan = cursor.execute(
                "EXPLAIN QUERY PLAN "
                "SELECT id FROM core_occupationsourceresolutioncandidate "
                "WHERE concept_id IN (1) AND resolution_id IN "
                "(SELECT id FROM core_opportunitytitleresolution "
                "WHERE revision_id IN (1)) LIMIT 1"
            ).fetchall()
        self.assertTrue(any(
            'USING COVERING INDEX occ_candidate_concept_idx'
            in ' '.join(str(part) for part in row)
            for row in plan
        ))
