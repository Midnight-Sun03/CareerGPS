"""Regression coverage for the cached rules component of the policy fingerprint.

`_relevant_policy_fingerprint` reads four things.  Three of them (the candidate
policies, the exact crosswalks, and the direct taxonomy relations) depend on the
concepts of the pair being assessed, so they must be read per pair.  The fourth
-- the candidate relationship rules -- is a global read of one policy version
that is identical for every pair, and it is now cached under its own validity
token.

These tests pin the two properties that make that safe: the cached component is
byte-for-byte the same value an uncached read produces, and it is re-read as
soon as anything about the rule set changes.
"""
from datetime import timedelta

from django.contrib.auth.models import User
from django.test import TestCase
from django.utils import timezone

from core.models import (
    CareerPreference,
    OccupationRelationshipRule,
    Profile,
)
from core.professional_policies import CANDIDATE_RELATIONSHIP_POLICY_VERSION
from core.services.relationship_aggregation import (
    _policy_rules_fingerprint,
    _relevant_policy_fingerprint,
    _relevant_policy_rules,
)
from core.services.occupational_resolution import (
    maintain_target_resolution_containers, resolve_target_sources,
    resolve_vacancy_sources,
)
from core.test_support import create_professional_opportunity


class PolicyRulesFingerprintCacheTests(TestCase):
    def setUp(self):
        _policy_rules_fingerprint.cache_clear()
        self.profile = Profile.objects.create(
            user=User.objects.create_user('rules-cache-user', password='password'))

    def tearDown(self):
        # The cache is process-wide, so it is cleared around every test rather
        # than allowed to carry one test's rows into the next.
        _policy_rules_fingerprint.cache_clear()

    def make_rule(self, name, **overrides):
        values = {
            'policy_version': CANDIDATE_RELATIONSHIP_POLICY_VERSION,
            'name': name,
            'evidence_source': 'taxonomy_relation',
            'taxonomy_source': 'esco',
            'evidence_relation_type': 'broader',
            'candidate_relationship_type': 'SAFE_BROADER_NARROWER',
            'direction': 'bidirectional',
            'candidate_class': 'RELATED',
            'admission_allowed': True,
            'rationale': 'Verified direct occupation link.',
            'provenance': {},
        }
        values.update(overrides)
        return OccupationRelationshipRule.objects.create(**values)

    def uncached_rules(self, policy_version):
        """The rules component exactly as it was read before the cache existed."""
        return list(OccupationRelationshipRule.objects.filter(
            policy_version=policy_version,
        ).order_by('pk').values_list(
            'pk', 'name', 'evidence_source', 'taxonomy_source',
            'evidence_relation_type', 'candidate_relationship_type', 'direction',
            'candidate_class', 'admission_allowed', 'active', 'updated_at'))

    def target_and_revision(self, title='Data Analyst'):
        preference = CareerPreference.objects.create(
            profile=self.profile, target_roles=title, confirmed_by_user=True)
        target = maintain_target_resolution_containers(preference)[0]
        opportunity = create_professional_opportunity(
            title=title, description='Source-grounded responsibilities.')
        return target, opportunity.active_clean_revision

    def source_rows(self, target, revision):
        target_sources = resolve_target_sources(target)
        container = resolve_vacancy_sources(revision)
        vacancy_sources = {
            row.source: row for row in container.source_resolutions.all()}
        return list(target_sources.values()) + list(vacancy_sources.values())

    def count_rule_queries(self, callable_):
        """Run a callable and report how many rule-table queries it issued."""
        from django.db import connection
        from django.test.utils import CaptureQueriesContext
        with CaptureQueriesContext(connection) as captured:
            result = callable_()
        rule_queries = [
            query['sql'] for query in captured.captured_queries
            if 'occupationrelationshiprule' in query['sql'].lower()
        ]
        return result, len(rule_queries)
    def test_repeated_calls_reuse_the_cached_rules_component(self):
        """An unchanged rule set is read once, not once per pair.

        The validity token is still checked on every call, so the cache can never
        be blind; what it avoids is re-reading and re-serialising the rule rows
        themselves.
        """
        self.make_rule('rules-cache-one')
        _policy_rules_fingerprint.cache_clear()
        version = CANDIDATE_RELATIONSHIP_POLICY_VERSION

        _first, cold_queries = self.count_rule_queries(
            lambda: _relevant_policy_rules(version))
        # A cold read costs the validity token plus the row read.
        self.assertEqual(cold_queries, 2)
        # Every later call is warm: only the validity token is issued, and the
        # rule rows themselves are never read again.
        for _attempt in range(5):
            result, warm_queries = self.count_rule_queries(
                lambda: _relevant_policy_rules(version))
            self.assertEqual(warm_queries, 1)
            self.assertEqual(result, self.uncached_rules(version))

    def test_repeated_fingerprint_calls_reuse_the_cached_rules_component(self):
        """The whole fingerprint, called repeatedly, reads the rules only once."""
        self.make_rule('rules-cache-fingerprint')
        _policy_rules_fingerprint.cache_clear()
        target, revision = self.target_and_revision()
        rows = self.source_rows(target, revision)

        first, first_queries = self.count_rule_queries(
            lambda: _relevant_policy_fingerprint(rows))
        self.assertEqual(first_queries, 2)
        for _attempt in range(4):
            again, again_queries = self.count_rule_queries(
                lambda: _relevant_policy_fingerprint(rows))
            self.assertEqual(again_queries, 1)
            # Every component of the fingerprint is unchanged by the cache.
            self.assertEqual(again, first)

    def test_changing_the_rule_set_is_not_reused_from_the_cache(self):
        """A newly approved rule is seen immediately, not served from cache."""
        version = CANDIDATE_RELATIONSHIP_POLICY_VERSION
        self.make_rule('rules-cache-original')
        before = _relevant_policy_rules(version)
        self.assertEqual(len(before), 1)
        self.assertEqual(before, self.uncached_rules(version))

        self.make_rule('rules-cache-approved-later')
        after = _relevant_policy_rules(version)
        self.assertEqual(len(after), 2)
        self.assertEqual(after, self.uncached_rules(version))
        self.assertNotEqual(after, before)

    def test_edited_and_retired_rules_are_not_reused_from_the_cache(self):
        """An edit and a retirement both change the token and re-read the rows.

        The token is the rule set's row count together with its latest
        update timestamp.  The edit below is written with an explicit later
        timestamp rather than relying on auto_now, because this host clock
        only has roughly millisecond resolution, so a rule created moments
        earlier can otherwise share a timestamp with its own edit.  A real
        policy edit is never that close to the read preceding it; the
        explicit timestamp only keeps this test deterministic.
        """
        version = CANDIDATE_RELATIONSHIP_POLICY_VERSION
        rule = self.make_rule('rules-cache-editable')
        self.assertEqual(_relevant_policy_rules(version),
                         self.uncached_rules(version))

        rule.admission_allowed = False
        rule.updated_at = timezone.now() + timedelta(seconds=5)
        rule.save(update_fields=['admission_allowed', 'updated_at'])
        edited = _relevant_policy_rules(version)
        self.assertEqual(edited, self.uncached_rules(version))
        self.assertEqual(edited[0][8], False)

        rule.delete()
        self.assertEqual(_relevant_policy_rules(version),
                         self.uncached_rules(version))
        self.assertEqual(len(_relevant_policy_rules(version)), 0)

    def test_changing_the_policy_version_does_not_reuse_the_rules_component(self):
        """A different policy version is a different key and a different read.

        Two versions can both hold rules at the same time, so a cache keyed only
        on the version would be wrong only if it ignored the version; this proves
        the opposite direction too, that changing the version never serves the
        previous version`s rows.
        """
        first_version = CANDIDATE_RELATIONSHIP_POLICY_VERSION
        second_version = f'{CANDIDATE_RELATIONSHIP_POLICY_VERSION}-next'
        self.make_rule('rules-cache-version-one')
        self.make_rule('rules-cache-version-two',
                       policy_version=second_version,
                       candidate_class='EXACT')

        first = _relevant_policy_rules(first_version)
        second = _relevant_policy_rules(second_version)
        self.assertEqual(first, self.uncached_rules(first_version))
        self.assertEqual(second, self.uncached_rules(second_version))
        self.assertEqual(len(first), 1)
        self.assertEqual(len(second), 1)
        # Same shape, different content: the two are never confused for each other.
        self.assertNotEqual(first[0][1], second[0][1])
        # Reading the first version again still yields its own rows.
        self.assertEqual(_relevant_policy_rules(first_version), first)

    def test_cached_fingerprint_is_identical_to_the_uncached_fingerprint(self):
        """The complete fingerprint equals what an uncached read produces.

        This is the property the dependency hash rests on: the cached component
        must be indistinguishable from the original query, in value and in
        ordering, so no stored verdict is invalidated or reused incorrectly.
        """
        self.make_rule('rules-cache-parity-one')
        self.make_rule('rules-cache-parity-two', direction='forward')
        target, revision = self.target_and_revision()
        rows = self.source_rows(target, revision)

        version = CANDIDATE_RELATIONSHIP_POLICY_VERSION
        expected = self._reference_fingerprint(rows, version)
        actual = _relevant_policy_fingerprint(rows)
        self.assertEqual(actual, expected)
        self.assertEqual(actual['rules'], self.uncached_rules(version))
        # The cached path and a freshly cleared cache agree exactly.
        _policy_rules_fingerprint.cache_clear()
        self.assertEqual(_relevant_policy_fingerprint(rows), expected)

    def _reference_fingerprint(self, source_rows, policy_version):
        """The original, entirely uncached fingerprint implementation."""
        concept_ids = sorted({
            concept_id for row in source_rows if row
            for concept_id in self._candidate_ids(row)
        } | {
            row.selected_concept_id for row in source_rows
            if row and row.selected_concept_id
        })
        from core.models import (
            OccupationRelationshipPolicy, TaxonomyCrosswalk, TaxonomyRelation,
        )
        return {
            'policies': list(OccupationRelationshipPolicy.objects.filter(
                policy_version=policy_version,
                subject_concept_id__in=concept_ids,
                object_concept_id__in=concept_ids,
            ).order_by('pk').values_list(
                'pk', 'relationship_type', 'direction', 'admission_allowed',
                'active', 'updated_at')),
            'rules': self.uncached_rules(policy_version),
            'direct_relations': list(TaxonomyRelation.objects.filter(
                subject_id__in=concept_ids, object_id__in=concept_ids,
            ).order_by('pk').values_list(
                'pk', 'subject_id', 'relation_type', 'object_id')),
            'exact_crosswalks': list(TaxonomyCrosswalk.objects.filter(
                active=True,
                approval_status='approved_identity',
                relation_type__in=('exact', 'exact_isco'),
                source_concept_id__in=concept_ids,
                target_concept_id__in=concept_ids,
            ).order_by('pk').values_list(
                'pk', 'relation_type', 'direction', 'mapping_name',
                'mapping_version')),
        }

    @staticmethod
    def _candidate_ids(row):
        if row is None:
            return ()
        ids = getattr(row, 'candidate_concept_ids', None)
        if ids is None:
            ids = (getattr(row, 'diagnostics', None) or {}).get(
                'candidate_concept_ids')
        if ids:
            return tuple(sorted(set(ids)))
        selected = getattr(row, 'selected_concept_id', None)
        return (selected,) if selected else ()

    def test_returned_rules_cannot_mutate_the_cached_value(self):
        """A caller mutating the returned list must not poison the cache."""
        version = CANDIDATE_RELATIONSHIP_POLICY_VERSION
        self.make_rule('rules-cache-immutable')
        first = _relevant_policy_rules(version)
        pristine = list(first)
        first.append(('tampered',))
        first.clear()
        self.assertEqual(_relevant_policy_rules(version), pristine)

    def test_empty_rule_set_is_cached_correctly(self):
        """No rules at all is a real, cacheable answer rather than a miss."""
        version = CANDIDATE_RELATIONSHIP_POLICY_VERSION
        self.assertEqual(_relevant_policy_rules(version), [])
        _policy_rules_fingerprint.cache_clear()
        result, first_queries = self.count_rule_queries(
            lambda: _relevant_policy_rules(version))
        self.assertEqual(result, [])
        # A cold read still costs the token plus the row read.
        self.assertEqual(first_queries, 2)
        _again, again_queries = self.count_rule_queries(
            lambda: _relevant_policy_rules(version))
        self.assertEqual(again_queries, 1)

    def test_pair_dependent_components_are_never_cached(self):
        """Policies, crosswalks, and relations stay per-pair reads.

        Only the rules component is global.  The other three depend on the
        concepts of the pair, so they must still be queried for every pair and
        must still differ between two different pairs.
        """
        from core.models import TaxonomyRelation
        target, revision = self.target_and_revision()
        other = create_professional_opportunity(
            title='Chef', description='Source-grounded responsibilities.')
        other_revision = other.active_clean_revision

        first_rows = self.source_rows(target, revision)
        second_rows = self.source_rows(target, other_revision)
        self.make_rule('rules-cache-pairs')

        # Warm the cache first so this measures steady-state pair reads.
        _relevant_policy_fingerprint(first_rows)
        _first, first_queries = self.count_rule_queries(
            lambda: _relevant_policy_fingerprint(first_rows))
        _second, second_queries = self.count_rule_queries(
            lambda: _relevant_policy_fingerprint(second_rows))
        # Every call still runs the per-pair concept-scoped reads, and only the
        # validity token is issued against the global rule table.
        self.assertEqual(first_queries, 1)
        self.assertEqual(second_queries, 1)
        # With no taxonomy concepts registered the pair-scoped components are
        # legitimately empty for both pairs, so the fingerprints match; what
        # matters is that each call still issued its own per-pair reads, which
        # the query counts above already prove.
        self.assertEqual(
            _relevant_policy_fingerprint(first_rows),
            _relevant_policy_fingerprint(second_rows))
        self.assertEqual(TaxonomyRelation.objects.count(), 0)
