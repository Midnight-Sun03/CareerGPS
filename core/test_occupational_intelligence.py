from io import StringIO
import json

from django.contrib.auth.models import User
from django.core.management import call_command
from django.test import SimpleTestCase, TestCase, override_settings

from core.models import (
    CareerPreference,
    OccupationRelationshipPolicy,
    OccupationRelationshipRule,
    OccupationSourceResolution,
    OpportunityTitleGateDecision,
    OpportunityTitleResolution,
    Profile,
    TaxonomyConcept,
    TaxonomyCrosswalk,
    TaxonomyLabel,
    TaxonomyRelation,
)
from core.professional_policies import (
    CANDIDATE_RELATIONSHIP_POLICY_VERSION,
    DENIED_PAIRING_CLASS,
    INCONCLUSIVE_PAIRING_CLASS,
    TITLE_MATCH_TIER,
    UNRELATED_CLASS,
)
from core.services.occupational_resolution import (
    maintain_target_resolution_containers,
    resolve_target_sources,
    resolve_vacancy_sources,
)
from core.services.professional_evaluations import professional_feed_queryset
from core.services.professional_resolution import resolve_text_identities
from core.services.relationship_aggregation import assess_target_vacancy
from core.services.title_gate import evaluate_revision, evaluate_title
from core.test_support import create_current_evaluation, create_professional_opportunity
from core.taxonomies.normalization import normalize_term
from core.models import TaxonomyRelease


DIRECT_RULE_FILTER = {
    'subject_kind': 'occupation',
    'object_kind': 'occupation',
    'metadata_relationship': 'source_broader',
}


class TitleGateTests(SimpleTestCase):
    def test_allowed_titles(self):
        allowed = (
            'Data Analyst', 'Junior Data Analyst', 'Jr Data Analyst',
            'Jr. Data Analyst', 'Graduate Data Analyst', 'Entry-Level Data Analyst',
            'Assistant Manager', 'Account Executive', 'Sales Executive',
            'Customer Service Executive', 'Executive Assistant',
        )
        for title in allowed:
            with self.subTest(title=title):
                self.assertEqual(evaluate_title(title).decision, 'ALLOW')

    def test_blocked_titles_with_case_and_punctuation(self):
        blocked = (
            'Senior Data Analyst', 'Sr Data Analyst', 'Sr. Data Analyst',
            'Principal Data Analyst',
            'Director of Data Analytics', 'Head of Analytics',
            'Chief Data Officer', 'VP Data', 'Vice President Data',
            'General Manager Data', 'Data Manager', 'Executive Director',
            'Executive Head', 'Chief Executive', 'sR., Data Analyst',
        )
        for title in blocked:
            with self.subTest(title=title):
                self.assertEqual(evaluate_title(title).decision, 'BLOCK')

    def test_lead_is_a_role_unless_it_names_the_generation_activity(self):
        """`Lead` marks a leadership role; `lead generation` is an activity."""
        for title in (
                'Team Lead', 'Team Leader', 'Lead Process Engineer',
                'Lead Data Analyst', 'Lead Generation Manager'):
            with self.subTest(title=title):
                self.assertEqual(evaluate_title(title).decision, 'BLOCK')
        allowed = evaluate_title('Lead Generation Specialist')
        self.assertEqual(allowed.decision, 'ALLOW')
        # A manager is excluded for being a manager, not for the activity.
        self.assertEqual(
            evaluate_title('Lead Generation Manager').reason_code, 'MANAGER')

    def test_assistant_manager_exception_covers_intervening_domain_words(self):
        for title in ('Assistant Manager', 'Assistant Finance Manager',
                      'Assistant Butchery Manager'):
            with self.subTest(title=title):
                result = evaluate_title(title)
                self.assertEqual(result.decision, 'ALLOW')
                self.assertEqual(result.exception_applied, 'assistant_manager')
        # An independent senior signal still excludes it.
        self.assertEqual(
            evaluate_title('Senior Assistant Manager').decision, 'BLOCK')

    def test_domain_and_client_facing_terms_are_not_seniority(self):
        for title in ('Management Accountant', 'Executive Assistant',
                      'Account Executive', 'Customer Service Executive'):
            with self.subTest(title=title):
                self.assertEqual(evaluate_title(title).decision, 'ALLOW')

    def test_uncertain_alone_never_excludes(self):
        self.assertEqual(
            evaluate_title(
                'Accountant', seniority_label='UNCERTAIN').decision, 'ALLOW')

    def test_cleaner_description_evidence_preserves_real_senior_roles(self):
        """The inspected senior Accountant listing stays excluded, and it is
        excluded by its posting evidence, never by an ESCO concept."""
        result = evaluate_title(
            'Accountant', seniority_label='OBVIOUS_SENIOR',
            seniority_reason=(
                'The posting expressly assigns senior, head, lead or '
                'executive authority.'),
            seniority_evidence=[{'text': 'Accountant'}])
        self.assertEqual(result.decision, 'BLOCK')
        self.assertEqual(result.reason_code, 'CLEANER_OBVIOUS_SENIOR')

    def test_cleaner_echo_of_an_assistant_manager_title_does_not_override(self):
        canned = ('The posting expressly assigns senior, head, lead or '
                  'executive authority.')
        echoed = evaluate_title(
            'Assistant Manager', seniority_label='OBVIOUS_SENIOR',
            seniority_reason=canned,
            seniority_evidence=[{'text': 'Assistant Manager'}])
        self.assertEqual(echoed.decision, 'ALLOW')
        self.assertEqual(echoed.exception_applied, 'assistant_manager')
        # Prose beyond the title words does establish senior responsibility.
        prose = evaluate_title(
            'Assistant Manager', seniority_label='OBVIOUS_SENIOR',
            seniority_reason='The source explicitly assigns formal leadership.',
            seniority_evidence=[{'text': 'You will lead a team of five.'}])
        self.assertEqual(prose.decision, 'BLOCK')
        self.assertEqual(prose.reason_code, 'CLEANER_OBVIOUS_SENIOR')

    def test_top_management_words_are_excluded(self):
        for title in ('Branch Controller', 'Managing Attorney',
                      'Supervisor Deli', 'Head of Finance', 'Management'):
            with self.subTest(title=title):
                self.assertEqual(evaluate_title(title).decision, 'BLOCK')


class OccupationalIntelligenceTests(TestCase):
    def setUp(self):
        self.releases = {
            source: TaxonomyRelease.objects.create(
                source=source, version=f'block1-{source}', is_active=True,
                source_url=f'https://taxonomy.test/{source}',
            )
            for source in ('ofo', 'esco')
        }
        self.profile = Profile.objects.create(
            user=User.objects.create_user('block1-user', password='password'))

    def concept(self, source, label, *, external_id=None, metadata=None,
                label_type='preferred'):
        concept = TaxonomyConcept.objects.create(
            release=self.releases[source],
            external_id=external_id or f'{source}:{label}:{TaxonomyConcept.objects.count()}',
            kind='occupation',
            preferred_label=label,
            normalized_label=normalize_term(label),
            metadata=metadata or {},
        )
        TaxonomyLabel.objects.create(
            concept=concept,
            label=label,
            normalized_label=normalize_term(label),
            label_type=label_type,
        )
        return concept

    def label(self, concept, label, label_type='alternative'):
        return TaxonomyLabel.objects.create(
            concept=concept,
            label=label,
            normalized_label=normalize_term(label),
            label_type=label_type,
        )

    def target(self, text):
        preference = CareerPreference.objects.create(
            profile=self.profile, target_roles=text, confirmed_by_user=True)
        return maintain_target_resolution_containers(preference)[0]

    def revision(self, title, description='Source-grounded responsibilities.'):
        opportunity = create_professional_opportunity(
            title=title, description=description)
        return opportunity.active_clean_revision

    def test_gate_is_read_only_and_a_blocked_title_is_never_unresolved(self):
        first = self.revision('Data Analyst', description='Chief executive manager role.')
        second = self.revision('Data Analyst', description='Junior individual contributor role.')
        self.assertEqual(evaluate_revision(first).decision, 'ALLOW')
        self.assertEqual(evaluate_revision(second).decision, 'ALLOW')
        blocked = self.revision('Senior Data Analyst', description='No senior wording here.')
        self.assertEqual(evaluate_revision(blocked).decision, 'BLOCK')
        # The decision is computed in code and nothing is persisted: no
        # seniority row exists anywhere, and because a caller excludes the row
        # before resolving it, the blocked title has no occupational container
        # either.  A senior role is therefore never reported as an occupation
        # CareerGPS failed to resolve.
        self.assertFalse(
            OpportunityTitleResolution.objects.filter(revision=blocked).exists())
        self.assertFalse(OpportunityTitleGateDecision.objects.exists())

    def test_sources_persist_independently_and_crosswalk_does_not_leak(self):
        esco = self.concept('esco', 'Data Analyst')
        ofo = self.concept('ofo', 'Different OFO Name')
        crosswalk = TaxonomyCrosswalk.objects.create(
            source_release=self.releases['esco'], source_concept=esco,
            source_external_id=esco.external_id,
            target_release=self.releases['ofo'], target_concept=ofo,
            target_external_id=ofo.external_id,
            relation_type='exact', direction='forward',
            approval_status='approved_identity', mapping_name='test-exact',
            mapping_version='1', source_reference='row-1',
        )
        target = self.target('Data Analyst')
        rows = resolve_target_sources(target)
        self.assertEqual(rows['esco'].state, 'RESOLVED')
        self.assertEqual(rows['esco'].selected_concept, esco)
        self.assertEqual(rows['ofo'].state, 'UNRESOLVED')
        self.assertIsNone(rows['ofo'].selected_concept)
        self.assertEqual(rows['esco'].taxonomy_release.version, 'block1-esco')
        self.assertEqual(rows['esco'].provenance['taxonomy_source'], 'esco')
        self.assertEqual(rows['esco'].candidates.count(), 1)

        # Transitional characterization: the legacy pooled resolver still
        # expands this exact crosswalk; the new source rows deliberately do not.
        legacy, _state, _diagnostics = resolve_text_identities('Data Analyst')
        self.assertIn(ofo.pk, legacy)
        self.assertTrue(TaxonomyCrosswalk.objects.filter(pk=crosswalk.pk).exists())

    def test_source_local_ambiguity_is_not_guessed(self):
        first = self.concept('ofo', 'Data Analyst')
        second = self.concept('ofo', 'Data Analyst')
        row = resolve_target_sources(self.target('Data Analyst'))['ofo']
        # Several exact matches are several retained results, not an ambiguity
        # state: the resolver keeps every match and picks no favourite, so no
        # downstream step can ever build on a guessed concept.
        self.assertEqual(row.state, 'RESOLVED')
        self.assertEqual(row.result_count, 2)
        self.assertIsNone(row.selected_concept)
        self.assertEqual(
            set(row.candidates.values_list('concept_id', flat=True)),
            {first.pk, second.pk},
        )

    def test_description_and_requirements_do_not_resolve_title(self):
        self.concept('ofo', 'Data Analyst')
        revision = self.revision(
            'Novel Vacancy Name',
            description='Data Analyst. Python and SQL are required.',
        )
        rows = {
            row.source: row
            for row in resolve_vacancy_sources(revision).source_resolutions.all()
        }
        self.assertEqual(rows['ofo'].state, 'UNRESOLVED')
        self.assertEqual(rows['esco'].state, 'UNRESOLVED')

    def test_multi_resolver_exact_is_strongest_tier(self):
        self.concept('ofo', 'Data Analyst')
        self.concept('esco', 'Data Analyst')
        assessment = assess_target_vacancy(
            self.target('Data Analyst'), self.revision('Data Analyst'))
        self.assertEqual(assessment.final_class, 'EXACT')
        self.assertEqual(assessment.evidence_tier, 'EXACT_MULTI_RESOLVER')
        self.assertEqual(assessment.tier_rank, 1)

    def test_one_exact_and_one_unresolved_is_weaker_exact(self):
        self.concept('ofo', 'Data Analyst')
        assessment = assess_target_vacancy(
            self.target('Data Analyst'), self.revision('Data Analyst'))
        self.assertEqual(assessment.final_class, 'EXACT')
        self.assertEqual(assessment.evidence_tier, 'EXACT_SINGLE_RESOLVER')

    def test_resolver_difference_without_negative_evidence_is_not_a_conflict(self):
        """One taxonomy confirming is enough; the other answer is disclosed.

        The second taxonomy resolved the two texts to different occupations with
        no approved policy anywhere.  That establishes nothing and refuses
        nothing, so it is absent evidence rather than a denial: the confirmed
        relationship stands on its own, and it is never marked as conflicting.
        """
        ofo = self.concept('ofo', 'Data Analyst')
        self.label(ofo, 'Insights Analyst')
        self.concept('esco', 'Data Analyst')
        self.concept('esco', 'Insights Analyst')
        assessment = assess_target_vacancy(
            self.target('Data Analyst'), self.revision('Insights Analyst'))
        self.assertEqual(assessment.final_class, 'EXACT')
        self.assertEqual(assessment.reason_code, 'one_exact_other_unresolved')
        self.assertEqual(assessment.ofo_comparison['outcome'], 'EXACT')
        self.assertEqual(assessment.esco_comparison['outcome'], 'INCOMPATIBLE')
        # Different resolved occupations are reported by their own source, and
        # the pairing is classified as nothing established, not as a denial.
        esco_row = assessment.esco_comparison
        self.assertEqual(esco_row['denied_pairing_count'], 0)
        self.assertEqual(
            [row['relationship_class'] for row in esco_row['pairings']],
            [INCONCLUSIVE_PAIRING_CLASS])
        self.assertEqual(esco_row['pairings'][0]['reason'],
                         'different_resolved_occupations_without_approved_policy')
        # Nothing refused the pairing, so there is no conflict to disclose.
        self.assertEqual(assessment.conflicting_evidence, [])
        self.assertEqual(assessment.denied_pairing_count, 0)
        self.assertEqual(assessment.relationship_count, 1)

    def test_explicit_negative_from_the_other_taxonomy_is_a_real_conflict(self):
        """A conflict needs a confirmed connection and a genuine refusal.

        OFO refused this pairing through an active UNSUPPORTED policy while ESCO
        confirmed it through an approved relationship.  The confirmation stands,
        the refusal is disclosed as conflicting evidence, and the job is not
        hidden.
        """
        ofo_target = self.concept('ofo', 'Data Analyst')
        ofo_vacancy = self.concept('ofo', 'Insights Analyst')
        esco_target = self.concept('esco', 'Data Analyst')
        esco_vacancy = self.concept('esco', 'Insights Analyst')
        OccupationRelationshipPolicy.objects.create(
            subject_concept=ofo_target, object_concept=ofo_vacancy,
            relationship_type='UNSUPPORTED', direction='forward',
            admission_allowed=False,
            policy_version=CANDIDATE_RELATIONSHIP_POLICY_VERSION,
            rationale='Reviewed and refused.', provenance={'reviewed': True})
        OccupationRelationshipPolicy.objects.create(
            subject_concept=esco_target, object_concept=esco_vacancy,
            relationship_type='CLOSE_NEIGHBOUR', direction='forward',
            admission_allowed=True,
            policy_version=CANDIDATE_RELATIONSHIP_POLICY_VERSION,
            rationale='Reviewed and approved.', provenance={'reviewed': True})
        assessment = assess_target_vacancy(
            self.target('Data Analyst'), self.revision('Insights Analyst'))
        self.assertEqual(assessment.final_class, 'RELATED')
        self.assertEqual(assessment.reason_code,
                         'one_related_other_source_disagrees')
        self.assertEqual(assessment.ofo_comparison['outcome'], 'INCOMPATIBLE')
        self.assertEqual(
            [row['relationship_class']
             for row in assessment.ofo_comparison['pairings']],
            [DENIED_PAIRING_CLASS])
        self.assertEqual(assessment.denied_pairing_count, 1)
        self.assertEqual(assessment.inconclusive_pairing_count, 0)
        self.assertEqual(
            [row['source'] for row in assessment.conflicting_evidence], ['ofo'])
        self.assertEqual(assessment.relationship_count, 1)
        self.assertEqual(assessment.findings.count(), 1)

    def test_different_codes_without_policy_are_not_negative_evidence(self):
        """Different resolved occupations are absent evidence, not a denial.

        Neither taxonomy has an approved policy for this target and job, so
        neither established a connection and neither refused one.  The pair is
        therefore never unrelated and never in conflict: both sources disclose
        the difference they found, and the verdict stays unresolved.
        """
        self.concept('ofo', 'Data Analyst')
        self.concept('ofo', 'Warehouse Clerk')
        self.concept('esco', 'Data Analyst')
        self.concept('esco', 'Warehouse Clerk')
        assessment = assess_target_vacancy(
            self.target('Data Analyst'), self.revision('Warehouse Clerk'))
        self.assertEqual(assessment.final_class, 'UNRESOLVED')
        self.assertEqual(assessment.reason_code, 'no_approved_relationship')
        self.assertNotEqual(assessment.final_class, UNRELATED_CLASS)
        self.assertNotEqual(assessment.final_class, 'CONFLICT')
        self.assertEqual(assessment.conflicting_evidence, [])
        self.assertEqual(assessment.denied_pairing_count, 0)
        self.assertEqual(assessment.inconclusive_pairing_count, 2)
        self.assertEqual(assessment.relationship_count, 0)
        for row in (assessment.ofo_comparison, assessment.esco_comparison):
            self.assertEqual(row['outcome'], 'INCOMPATIBLE')
            self.assertEqual(row['denied_pairing_count'], 0)
            self.assertEqual([pairing['relationship_class']
                              for pairing in row['pairings']],
                             [INCONCLUSIVE_PAIRING_CLASS])
            self.assertEqual(
                row['pairings'][0]['reason'],
                'different_resolved_occupations_without_approved_policy')
        # Absent evidence is not a finding, so nothing is stored as a link.
        self.assertEqual(assessment.findings.count(), 0)

    def test_keyword_overlap_does_not_establish_relationship(self):
        assessment = assess_target_vacancy(
            self.target('Data Analyst'), self.revision('Business Analyst'))
        self.assertEqual(assessment.final_class, 'UNRESOLVED')

    def test_approved_alias_is_exact_and_stage_variant_is_title_match(self):
        concept = self.concept('ofo', 'Data Analyst')
        self.label(concept, 'Insights Analyst', 'alternative')
        target = self.target('Data Analyst')
        alias = assess_target_vacancy(
            target, self.revision('Insights Analyst'))
        self.assertEqual(alias.final_class, 'EXACT')
        self.assertEqual(alias.evidence_tier, 'EXACT_ALIAS_SPECIALISATION')

        # A stage variant of the authored wording is title evidence, not a
        # taxonomy confirmation, so it is reported in its own class and tier.
        stage = assess_target_vacancy(
            target, self.revision('Entry-Level Data Analyst'))
        self.assertEqual(stage.final_class, 'TITLE_MATCH')
        self.assertEqual(stage.evidence_tier, 'TITLE_ONLY')
        self.assertEqual(stage.tier_rank, 4)
        self.assertEqual(stage.reason_code, 'allowed_stage_prefix')
        self.assertEqual(stage.policy_evidence, [])

    def test_specialisation_requires_explicit_candidate_policy(self):
        concept = self.concept('ofo', 'Data Analyst')
        self.label(concept, 'Data Insights Analyst', 'specialisation')
        target = self.target('Data Analyst')
        revision = self.revision('Data Insights Analyst')
        before = assess_target_vacancy(target, revision)
        self.assertEqual(before.final_class, 'UNRESOLVED')
        OccupationRelationshipPolicy.objects.create(
            subject_concept=concept, object_concept=concept,
            relationship_type='SAME_OCCUPATION_SPECIALISATION',
            direction='bidirectional', admission_allowed=True,
            policy_version=CANDIDATE_RELATIONSHIP_POLICY_VERSION,
            rationale='Test-approved source-backed specialisation.',
            provenance={'approved_by': 'test'},
        )
        after = assess_target_vacancy(target, revision)
        self.assertEqual(after.final_class, 'EXACT')
        self.assertEqual(after.evidence_tier, 'EXACT_ALIAS_SPECIALISATION')

    def test_only_explicit_close_policy_produces_related(self):
        target_concept = self.concept('ofo', 'Data Analyst')
        vacancy_concept = self.concept('ofo', 'Business Intelligence Analyst')
        target = self.target('Data Analyst')
        revision = self.revision('Business Intelligence Analyst')
        raw = TaxonomyRelation.objects.create(
            subject=target_concept, relation_type='broader', object=vacancy_concept,
            metadata={'source': 'raw-test'},
        )
        before = assess_target_vacancy(target, revision)
        self.assertEqual(before.final_class, 'UNRESOLVED')
        OccupationRelationshipPolicy.objects.create(
            subject_concept=target_concept, object_concept=vacancy_concept,
            relationship_type='CLOSE_NEIGHBOUR', direction='forward',
            admission_allowed=True,
            policy_version=CANDIDATE_RELATIONSHIP_POLICY_VERSION,
            rationale='Test-approved close neighbour.',
            provenance={'approved_by': 'test'}, taxonomy_relation=raw,
        )
        after = assess_target_vacancy(target, revision)
        self.assertEqual(after.final_class, 'RELATED')
        self.assertEqual(after.evidence_tier, 'RELATED_CLOSE_NEIGHBOUR')

    def test_shared_isco_raw_broader_and_two_hops_do_not_admit(self):
        alpha = self.concept('ofo', 'Alpha Analyst', metadata={'isco_group': '1234'})
        beta = self.concept('ofo', 'Beta Analyst', metadata={'isco_group': '1234'})
        gamma = self.concept('ofo', 'Gamma Analyst', metadata={'isco_group': '1234'})
        TaxonomyRelation.objects.create(
            subject=alpha, relation_type='broader', object=gamma)
        for left, right in ((alpha, beta), (beta, gamma)):
            OccupationRelationshipPolicy.objects.create(
                subject_concept=left, object_concept=right,
                relationship_type='CLOSE_NEIGHBOUR', direction='forward',
                admission_allowed=True,
                policy_version=CANDIDATE_RELATIONSHIP_POLICY_VERSION,
                rationale='One hop only.', provenance={'approved_by': 'test'},
            )
        assessment = assess_target_vacancy(
            self.target('Alpha Analyst'), self.revision('Gamma Analyst'))
        self.assertEqual(assessment.final_class, 'UNRESOLVED')

    def test_known_unsafe_pairs_never_become_related(self):
        pairs = (
            ('Internal Auditor', 'accountant'),
            ('QA Engineer', 'application engineer'),
            ('Demand Planner', 'purchaser'),
            ('Corporate Counsel', 'lawyer'),
            ('Marketing Manager', 'chief marketing officer'),
        )
        preference = CareerPreference.objects.create(
            profile=self.profile,
            target_roles='; '.join(target for target, _vacancy in pairs),
        )
        targets = maintain_target_resolution_containers(preference)
        for (target_text, vacancy_title), target in zip(pairs, targets):
            left = self.concept('ofo', target_text)
            right = self.concept('ofo', vacancy_title)
            TaxonomyRelation.objects.create(
                subject=left, relation_type='broader', object=right)
            result = assess_target_vacancy(target, self.revision(vacancy_title))
            with self.subTest(target=target_text, vacancy=vacancy_title):
                self.assertTrue(result is None or result.final_class != 'RELATED')

    @override_settings(CAREERGPS_FEED_READ_VERSION='legacy')
    def test_shadow_gate_does_not_change_production_feed_admission(self):
        opportunity = create_professional_opportunity(
            title='Senior Data Analyst', seniority='NON_SENIOR')
        create_current_evaluation(self.profile, opportunity, category='PROFILE_RELEVANT')
        self.assertEqual(
            evaluate_revision(opportunity.active_clean_revision).decision, 'BLOCK')
        self.assertTrue(professional_feed_queryset(self.profile).filter(
            opportunity=opportunity).exists())

    def test_rerun_reuses_identical_source_and_assessment_dependencies(self):
        self.concept('ofo', 'Data Analyst')
        target = self.target('Data Analyst')
        revision = self.revision('Data Analyst')
        first = assess_target_vacancy(target, revision)
        source_ids = list(OccupationSourceResolution.objects.values_list(
            'pk', 'dependency_hash').order_by('pk'))
        second = assess_target_vacancy(target, revision)
        self.assertEqual(first.pk, second.pk)
        self.assertEqual(first.dependency_hash, second.dependency_hash)
        self.assertEqual(source_ids, list(OccupationSourceResolution.objects.values_list(
            'pk', 'dependency_hash').order_by('pk')))

    def test_backfill_report_distinguishes_exact_from_non_admission_agreement(self):
        self.concept('ofo', 'Data Analyst')
        self.concept('esco', 'Data Analyst')
        self.target('Data Analyst')
        self.revision('Data Analyst')
        output = StringIO()
        call_command('backfill_occupational_intelligence', stdout=output)
        report = json.loads(output.getvalue()[output.getvalue().index('{'):])
        self.assertEqual(
            report['assessment_source_outcomes']['ofo_esco_exact_agreement'], 1)
        self.assertEqual(report['relationship_classes']['EXACT'], 1)
        self.assertFalse(report['scope']['production_feed_cutover'])


class CandidateSetComparisonTests(OccupationalIntelligenceTests):
    """All-pairs candidate comparison and taxonomy independence."""

    def direct_rule(self):
        return OccupationRelationshipRule.objects.create(
            policy_version=CANDIDATE_RELATIONSHIP_POLICY_VERSION,
            name='test-esco-direct-broader',
            evidence_source='taxonomy_relation', taxonomy_source='esco',
            evidence_relation_type='broader',
            candidate_relationship_type='SAFE_BROADER_NARROWER',
            direction='bidirectional', candidate_class='RELATED',
            admission_allowed=True, rationale='Verified direct occupation link.',
            provenance={'evidence_filter': DIRECT_RULE_FILTER},
        )

    def test_every_candidate_pairing_related_yields_related_not_exact(self):
        """The inspected Accountant case: two candidates on both sides, all
        four pairings exact-or-related, so RELATED and never EXACT."""
        accountant = self.concept('esco', 'accountant')
        controller = self.concept('esco', 'financial controller')
        # Both concepts legitimately carry the bare label "accountant".
        self.label(controller, 'accountant', 'alternative')
        TaxonomyRelation.objects.create(
            subject=controller, relation_type='broader', object=accountant,
            metadata={'relationship': 'source_broader'})
        self.direct_rule()
        assessment = assess_target_vacancy(
            self.target('accountant'), self.revision('Accountant'))
        # No concept was selected out of the ambiguous set.
        esco_row = assessment.esco_comparison
        self.assertEqual(sorted(esco_row['target_candidate_concept_ids']),
                         sorted([accountant.pk, controller.pk]))
        self.assertEqual(esco_row['outcome'], 'RELATED')
        self.assertEqual(esco_row['reason'],
                         'every_candidate_pairing_is_exact_or_approved_related')
        self.assertEqual(esco_row['pairing_count'], 4)
        self.assertEqual(esco_row['related_pairing_count'], 2)
        self.assertNotEqual(assessment.final_class, 'EXACT')
        self.assertEqual(assessment.final_class, 'RELATED')
        # The qualifying relation and its policy are recorded as evidence.
        self.assertTrue(assessment.policy_evidence)

    def test_identical_candidate_sets_are_not_automatically_exact(self):
        """Identical candidate sets are evaluated, not assumed.

        Two concepts on both sides with no approved direct link leaves the
        cross pairings unconnected, so the taxonomy confirms nothing. The
        identical title still yields the weaker title-evidence tier, which is
        explicitly not a taxonomy confirmation.
        """
        accountant = self.concept('esco', 'accountant')
        controller = self.concept('esco', 'financial controller')
        self.label(accountant, 'accountant', 'alternative')
        self.label(controller, 'accountant', 'alternative')
        self.direct_rule()
        # Remove the only fact that could connect the cross pairings.
        TaxonomyRelation.objects.all().delete()
        assessment = assess_target_vacancy(
            self.target('accountant'), self.revision('Accountant'))
        self.assertEqual(assessment.esco_comparison['outcome'], 'AMBIGUOUS')
        self.assertEqual(assessment.esco_comparison['reason'],
                         'candidate_pairings_do_not_agree')
        # Neither taxonomy confirmed it, so the result is title evidence only.
        self.assertEqual(assessment.final_class, 'TITLE_MATCH')
        self.assertEqual(assessment.evidence_tier, 'TITLE_ONLY')
        self.assertEqual(assessment.policy_evidence, [])

    def test_one_confirmed_pairing_establishes_the_relationship(self):
        """One trusted pairing is enough when other pairings connect nothing.

        Both texts retain the same two ESCO occupations, and the approved
        relationship between them is one-directional: the cross pairing going
        forward is approved, the reverse one establishes nothing.  The approved
        pairing confirms the relationship on its own, every established
        relationship is kept, and the source answers with that relationship
        instead of an ambiguity that would withhold the job.
        """
        accountant = self.concept('esco', 'accountant')
        controller = self.concept('esco', 'financial controller')
        self.label(controller, 'accountant', 'alternative')
        OccupationRelationshipPolicy.objects.create(
            subject_concept=accountant, object_concept=controller,
            relationship_type='CLOSE_NEIGHBOUR', direction='forward',
            admission_allowed=True,
            policy_version=CANDIDATE_RELATIONSHIP_POLICY_VERSION,
            rationale='Reviewed close neighbour.', provenance={'reviewed': True})
        assessment = assess_target_vacancy(
            self.target('accountant'), self.revision('Accountant'))
        esco_row = assessment.esco_comparison
        self.assertEqual(esco_row['pairing_count'], 4)
        self.assertEqual(esco_row['exact_pairing_count'], 2)
        self.assertEqual(esco_row['related_pairing_count'], 1)
        self.assertEqual(esco_row['inconclusive_pairing_count'], 1)
        self.assertEqual(esco_row['denied_pairing_count'], 0)
        # The approved pairing answers for the source, with its own reason.
        self.assertEqual(esco_row['outcome'], 'RELATED')
        self.assertEqual(esco_row['reason'], 'approved_candidate_relationship')
        self.assertEqual(esco_row['route'], 'close_neighbour')
        # Every relationship that was established is retained in the comparison
        # payload: two label collisions and the approved relationship.  The
        # collisions are possible candidates and keep the title tier; only the
        # approved relationship is confirmation, so only it is stored as a
        # finding and only it is counted as a relationship.
        self.assertEqual(esco_row['relationship_count'], 3)
        self.assertEqual(
            {row['route']: row['evidence_tier']
             for row in esco_row['relationships']},
            {'preferred': TITLE_MATCH_TIER, 'alias': TITLE_MATCH_TIER,
             'close_neighbour': 'RELATED_CLOSE_NEIGHBOUR'})
        self.assertEqual(assessment.final_class, 'RELATED')
        self.assertEqual(assessment.evidence_tier, 'RELATED_CLOSE_NEIGHBOUR')
        self.assertEqual(assessment.tier_rank, 3)
        self.assertEqual(assessment.reason_code,
                         'one_approved_relationship_other_unresolved')
        self.assertEqual(assessment.conflicting_evidence, [])
        self.assertEqual(assessment.relationship_count, 1)
        self.assertEqual(assessment.findings.count(), 1)
        # The title-only collisions keep their full evidence in the comparison
        # JSON and are disclosed separately, with their tier.
        self.assertEqual(
            [row['evidence_tier']
             for row in assessment.diagnostics['title_only_evidence']],
            [TITLE_MATCH_TIER, TITLE_MATCH_TIER])
        self.assertEqual(
            assessment.diagnostics['title_only_evidence_count'], 2)
        self.assertEqual(
            {row['evidence_tier'] for row in esco_row['relationships']
             if row['route'] in {'preferred', 'alias'}},
            {TITLE_MATCH_TIER})
        self.assertTrue(all(
            row.get('target_concept_id') and row.get('job_concept_id')
            and row.get('target_label') and row.get('job_label')
            for row in assessment.diagnostics['title_only_evidence']))

    def test_several_confirmed_occupations_are_not_ambiguous(self):
        """Several confirmed connections are still a confirmation.

        Both texts retain the same three ESCO occupations, and approved
        same-occupation policies confirm the two cross pairings that connect
        them.  The source answers EXACT, every confirmed relationship and every
        label collision is retained, and the pair is never reported as ambiguity
        that would withhold the job.
        """
        accountant = self.concept('esco', 'accountant')
        controller = self.concept('esco', 'financial controller')
        clerk = self.concept('esco', 'accounting clerk')
        for concept in (controller, clerk):
            self.label(concept, 'accountant', 'alternative')
        for left, right in ((accountant, controller), (controller, accountant)):
            OccupationRelationshipPolicy.objects.create(
                subject_concept=left, object_concept=right,
                relationship_type='SAME_OCCUPATION', direction='forward',
                admission_allowed=True,
                policy_version=CANDIDATE_RELATIONSHIP_POLICY_VERSION,
                rationale='Reviewed same occupation.',
                provenance={'reviewed': True})
        assessment = assess_target_vacancy(
            self.target('accountant'), self.revision('Accountant'))
        esco_row = assessment.esco_comparison
        self.assertEqual(esco_row['pairing_count'], 9)
        # Two approved same-occupation pairings and three label collisions are
        # all established relationships; only the approved two are confirmations.
        self.assertEqual(esco_row['exact_pairing_count'], 5)
        self.assertEqual(esco_row['relationship_count'], 5)
        self.assertEqual(esco_row['inconclusive_pairing_count'], 4)
        self.assertEqual(esco_row['outcome'], 'EXACT')
        self.assertEqual(esco_row['reason'], 'approved_same_occupation_policy')
        self.assertEqual(
            sorted(row['evidence_tier'] for row in esco_row['relationships']
                   if row['route'] == 'policy'),
            ['EXACT_SINGLE_RESOLVER', 'EXACT_SINGLE_RESOLVER'])
        self.assertEqual(
            {row['evidence_tier'] for row in esco_row['relationships']
             if row['route'] != 'policy'},
            {TITLE_MATCH_TIER})
        self.assertEqual(assessment.final_class, 'EXACT')
        # Two approved same-occupation pairings and three label collisions were
        # all established relationships in the comparison payload; only the
        # approved two are confirmations, so only they are findings and only
        # they are counted.
        self.assertEqual(assessment.exact_relationship_count, 2)
        self.assertEqual(assessment.relationship_count, 2)
        self.assertEqual(assessment.reason_code, 'one_exact_other_unresolved')
        self.assertEqual(len(assessment.policy_evidence), 2)
        self.assertEqual(assessment.findings.count(), 2)
        self.assertEqual(
            {row.evidence_tier for row in assessment.findings.all()},
            {'EXACT_SINGLE_RESOLVER'})
        # The three collisions are retained, disclosed with their tier, and kept
        # out of the confirmed count.
        self.assertEqual(assessment.diagnostics['title_only_evidence_count'], 3)
        self.assertEqual(
            {row['evidence_tier']
             for row in assessment.diagnostics['title_only_evidence']},
            {TITLE_MATCH_TIER})

    def test_multiplicity_without_a_connection_is_inconclusive(self):
        """Several readings that connected nothing are not an ambiguity.

        Both texts keep two ESCO occupations and no approved policy connects any
        of the pairings.  Keeping several readings is why no candidate can be
        committed to, but it is not evidence that the readings disagree, so the
        taxonomy answers `UNRESOLVED`: never an ambiguity that withholds the job,
        never a difference it cannot support, and never `UNRELATED`.
        """
        first = self.concept('esco', 'Accountant')
        second = self.concept('esco', 'Accounting Clerk')
        self.label(second, 'Accountant', 'alternative')
        # The job keeps one other occupation, so no pairing of the two sets is
        # the same concept: nothing is retained and nothing is refused.
        self.concept('esco', 'Data Analyst')
        assessment = assess_target_vacancy(
            self.target('Accountant'), self.revision('Data Analyst'))
        esco_row = assessment.esco_comparison
        self.assertEqual(esco_row['pairing_count'], 2)
        self.assertEqual(esco_row['target_result_count'], 2)
        self.assertEqual(esco_row['vacancy_result_count'], 1)
        self.assertEqual(esco_row['outcome'], 'UNRESOLVED')
        self.assertEqual(esco_row['reason'], 'several_results_none_connected')
        self.assertEqual(esco_row['relationship_count'], 0)
        self.assertEqual(esco_row['inconclusive_pairing_count'], 2)
        self.assertEqual(esco_row['denied_pairing_count'], 0)
        self.assertEqual(sorted(esco_row['target_candidate_concept_ids']),
                         sorted([first.pk, second.pk]))
        # Nothing established means no finding and no unrelated verdict.
        self.assertEqual(assessment.findings.count(), 0)
        self.assertEqual(assessment.relationship_count, 0)
        self.assertEqual(assessment.final_class, 'UNRESOLVED')
        self.assertNotEqual(assessment.final_class, 'UNRELATED')
        self.assertEqual(assessment.conflicting_evidence, [])
        self.assertEqual(assessment.diagnostics['title_only_evidence'], [])

    def test_genuine_ambiguity_is_kept_for_readings_that_disagree(self):
        """Ambiguity survives only where separate retained evidence supports it.

        The two retained ESCO occupations both carry the bare label
        "accountant", so the two texts collided on two different occupations and
        the readings disagree about which occupation either text denotes.  That
        compared evidence, not multiplicity, is what makes this ambiguous.
        """
        accountant = self.concept('esco', 'accountant')
        controller = self.concept('esco', 'financial controller')
        self.label(accountant, 'accountant', 'alternative')
        self.label(controller, 'accountant', 'alternative')
        assessment = assess_target_vacancy(
            self.target('Accountant'), self.revision('Accountant'))
        esco_row = assessment.esco_comparison
        self.assertEqual(esco_row['outcome'], 'AMBIGUOUS')
        self.assertEqual(esco_row['reason'], 'candidate_pairings_do_not_agree')
        # The collisions stay retained and disclosed, and none of them is a
        # confirmed relationship or a stored finding.
        self.assertEqual(esco_row['relationship_count'], 2)
        self.assertEqual(assessment.findings.count(), 0)
        self.assertEqual(assessment.diagnostics['title_only_evidence_count'], 2)
        self.assertEqual(
            {row['evidence_tier']
             for row in assessment.diagnostics['title_only_evidence']},
            {TITLE_MATCH_TIER})

    def test_title_only_collisions_are_never_stored_as_findings(self):
        """A retained possibility is disclosed, not persisted as a finding.

        Both texts keep the same two occupations and collide on both of them, so
        two relationships were retained and neither of them confirmed the pair.
        The stored findings stay empty, the retained evidence stays in the
        comparison JSON with its tier, and the pair is never counted as a
        relationship.
        """
        self.concept('esco', 'Accountant')
        self.concept('esco', 'Accountant')
        assessment = assess_target_vacancy(
            self.target('Accountant'), self.revision('Accountant'))
        self.assertEqual(assessment.findings.count(), 0)
        self.assertEqual(assessment.relationship_count, 0)
        self.assertEqual(assessment.exact_relationship_count, 0)
        self.assertEqual(assessment.diagnostics['title_only_evidence_count'], 2)
        esco_pairs = assessment.esco_comparison['relationships']
        self.assertEqual(len(esco_pairs), 2)
        self.assertEqual(
            {row['evidence_tier'] for row in esco_pairs}, {TITLE_MATCH_TIER})
        # The full per-pairing evidence is preserved in the comparison payload.
        self.assertEqual(assessment.esco_comparison['pairing_count'], 4)
        self.assertEqual(assessment.esco_comparison['outcome'], 'AMBIGUOUS')
        self.assertTrue(all(
            row['target_concept_id'] and row['vacancy_concept_id']
            for row in esco_pairs))
        self.assertTrue(all(
            row['target_concept_id'] and row['job_concept_id']
            for row in assessment.diagnostics['title_only_evidence']))

    def test_every_distinct_confirmed_relationship_is_preserved(self):
        """Two confirmed concepts that share a label stay two relationships."""
        accountant = self.concept('esco', 'accountant')
        controller = self.concept('esco', 'financial controller')
        self.label(controller, 'accountant', 'alternative')
        for left, right in ((accountant, controller), (controller, accountant)):
            OccupationRelationshipPolicy.objects.create(
                subject_concept=left, object_concept=right,
                relationship_type='SAME_OCCUPATION', direction='forward',
                admission_allowed=True,
                policy_version=CANDIDATE_RELATIONSHIP_POLICY_VERSION,
                rationale='Reviewed same occupation.',
                provenance={'reviewed': True})
        assessment = assess_target_vacancy(
            self.target('Accountant'), self.revision('Accountant'))
        self.assertEqual(assessment.final_class, 'EXACT')
        self.assertEqual(assessment.relationship_count, 2)
        findings = list(assessment.findings.all())
        self.assertEqual(len(findings), 2)
        self.assertEqual({row.target_concept_id for row in findings},
                         {accountant.pk, controller.pk})
        self.assertEqual({row.job_concept_id for row in findings},
                         {accountant.pk, controller.pk})
        # The label collisions that share the wording are still retained, and
        # still excluded from the confirmed set.
        self.assertEqual(assessment.diagnostics['title_only_evidence_count'], 2)

    def test_group_membership_row_is_not_a_direct_relationship(self):
        """An ISCO-group `broader` row is broad membership, not a direct link.

        The same `broader` relation_type is used by two different ESCO imports.
        Only the occupation-pillar row is a direct occupation-to-occupation
        link, and the rule is narrowed to it by provenance and object kind.
        """
        accountant = self.concept('esco', 'accountant')
        group = TaxonomyConcept.objects.create(
            release=self.releases['esco'], kind='occupation_group',
            external_id='esco:group:2411', preferred_label='Accountants',
            normalized_label=normalize_term('Accountants'), metadata={})
        TaxonomyRelation.objects.create(
            subject=accountant, relation_type='broader', object=group,
            metadata={'relationship': 'isco_group'})
        self.direct_rule()
        self.concept('esco', 'accounting clerk')
        assessment = assess_target_vacancy(
            self.target('accountant'), self.revision('Accounting Clerk'))
        self.assertIsNotNone(assessment)
        self.assertNotEqual(assessment.final_class, 'RELATED')

    def test_ambiguous_source_does_not_veto_a_confirmed_other_source(self):
        """A source that confirms nothing cannot delete a source that did.

        ESCO retained two occupations on both sides and connected none of them,
        so it confirms nothing.  OFO resolved both texts to one occupation, so it
        confirms the pair.  The confirmation stands: the pair is EXACT, the
        unconfirmed ESCO collisions are still retained in the stored comparison
        payload and disclosed as title-only evidence, and no source answers for
        another.
        """
        self.concept('ofo', 'Data Analyst')
        accountant = self.concept('esco', 'Data Analyst')
        controller = self.concept('esco', 'Financial Controller')
        self.label(controller, 'Data Analyst', 'alternative')
        assessment = assess_target_vacancy(
            self.target('Data Analyst'), self.revision('Data Analyst'))
        self.assertEqual(assessment.ofo_comparison['outcome'], 'EXACT')
        self.assertEqual(assessment.esco_comparison['outcome'], 'AMBIGUOUS')
        self.assertEqual(assessment.esco_comparison['relationship_count'], 2)
        self.assertEqual(assessment.esco_comparison['inconclusive_pairing_count'], 2)
        self.assertEqual(assessment.final_class, 'EXACT')
        self.assertEqual(assessment.evidence_tier, 'EXACT_SINGLE_RESOLVER')
        self.assertEqual(assessment.reason_code, 'one_exact_other_unresolved')
        self.assertEqual(assessment.conflicting_evidence, [])
        # Only the OFO confirmation is a relationship; the two ESCO collisions
        # are possibilities and stay out of the count and out of the findings.
        self.assertEqual(assessment.relationship_count, 1)
        tiers = {(row.source, row.evidence_tier)
                 for row in assessment.findings.all()}
        self.assertEqual(tiers, {('ofo', 'EXACT_SINGLE_RESOLVER')})
        self.assertEqual(assessment.findings.count(), 1)
        self.assertEqual(
            {(row['source'], row['evidence_tier'])
             for row in assessment.diagnostics['title_only_evidence']},
            {('esco', TITLE_MATCH_TIER)})
        self.assertTrue(accountant.pk in
                        assessment.esco_comparison['target_candidate_concept_ids'])

    def test_stage_variant_is_not_presented_as_a_taxonomy_confirmation(self):
        """A title match stays in its own weaker tier with no policy evidence."""
        assessment = assess_target_vacancy(
            self.target('Data Analyst'), self.revision('Data Analyst'))
        self.assertEqual(assessment.final_class, 'TITLE_MATCH')
        self.assertEqual(assessment.evidence_tier, 'TITLE_ONLY')
        self.assertEqual(assessment.reason_code, 'exact_authored_title')
        self.assertEqual(assessment.ofo_comparison['outcome'], 'UNRESOLVED')
        self.assertEqual(assessment.esco_comparison['outcome'], 'UNRESOLVED')
        self.assertEqual(assessment.policy_evidence, [])

    def test_shared_word_and_distant_parent_are_not_direct_relationships(self):
        alpha = self.concept('esco', 'Data Analyst',
                             metadata={'iscoGroup': '2411'})
        beta = self.concept('esco', 'Data Scientist',
                            metadata={'iscoGroup': '2411'})
        self.direct_rule()
        # Same ISCO group and a shared word, but no direct occupation link.
        assessment = assess_target_vacancy(
            self.target('Data Analyst'), self.revision('Data Scientist'))
        self.assertNotEqual(assessment.final_class, 'RELATED')
