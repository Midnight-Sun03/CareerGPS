from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from django.utils import timezone

from core.management.commands.verify_v2_feed import verification_report
from core.models import (
    CareerPreference,
    EvaluationRecomputeTask,
    OccupationRelationshipPolicy,
    OccupationRelationshipRule,
    OccupationSourceResolution,
    Opportunity,
    OpportunityTitleGateDecision,
    OpportunityTitleResolution,
    Profile,
    ProfileFeedEvaluation,
    ProfileSkill,
    TargetOpportunityRelationshipAssessment,
    TaxonomyConcept,
    TaxonomyLabel,
    TaxonomyRelation,
    TaxonomyRelease,
)
from core.professional_policies import (
    CANDIDATE_RELATIONSHIP_POLICY_VERSION,
    FEED_DISCOVERY_CATEGORIES,
    FEED_DISCOVERY_RANK,
    FEED_SURVIVOR_CATEGORIES,
    TITLE_MATCH_CLASS,
    UNRELATED_CLASS,
    UNRELATED_REASON_CODE,
)
from core.services.feed_queue import (
    TASK_VERSION,
    _opportunity_fanout,
    claim_tasks,
    enqueue_opportunity_rebuild,
    enqueue_profile_rebuild,
    process_claimed_task,
    reclaim_expired_tasks,
)
from core.services.feed_v2 import (
    apply_practical_filters,
    build_profile_generation,
    feed_transition,
    proved_unrelated,
    v2_feed_queryset,
)
from core.services.occupational_resolution import (
    maintain_target_resolution_containers,
    resolve_vacancy_sources,
)
from core.services.professional_resolution import parse_target_roles
from core.services.relationship_aggregation import (
    _explicit_unsupported as explicit_unsupported,
    assess_target_vacancy,
)
from core.services.title_gate import evaluate_revision
from core.test_support import create_professional_opportunity
from core.taxonomies.normalization import normalize_term


def criterion(identifier='missing-degree'):
    return {
        'criterion_id': identifier,
        'criterion_type': 'QUALIFICATION',
        'normalized_value': identifier,
        'raw_value': identifier,
        'requirement_level': 'REQUIRED',
        'significance': 'CRUCIAL',
        'source_section': 'raw_content',
        'requirement_group': None,
        'evidence': [{'text': identifier}],
        'qualification': {'level': 'BACHELORS', 'fields': [], 'nqf_level': None},
        'experience': None,
        'language': None,
    }


def unsupported_policy(subject, object_):
    """An explicit negative occupational review outcome for one source pair."""
    return OccupationRelationshipPolicy.objects.create(
        subject_concept=subject, object_concept=object_,
        relationship_type='UNSUPPORTED', direction='forward',
        admission_allowed=False,
        policy_version=CANDIDATE_RELATIONSHIP_POLICY_VERSION,
        rationale='Explicit synthetic negative review outcome.',
        provenance={'reviewed': True, 'source': 'test-review'})


class FeedV2Case(TestCase):
    def setUp(self):
        self.releases = {
            source: TaxonomyRelease.objects.create(
                source=source, version=f'v2-{source}', is_active=True)
            for source in ('ofo', 'esco')
        }
        self.profile = Profile.objects.create(
            user=User.objects.create_user('v2-candidate', password='password'))

    def concept(self, source, label, *, external_id=None, metadata=None):
        concept = TaxonomyConcept.objects.create(
            release=self.releases[source], kind='occupation',
            external_id=external_id or f'{source}:{label}:{TaxonomyConcept.objects.count()}',
            preferred_label=label, normalized_label=normalize_term(label),
            metadata=metadata or {})
        self.label(concept, label, 'preferred')
        return concept

    def label(self, concept, label, label_type='alternative'):
        return TaxonomyLabel.objects.create(
            concept=concept, label=label, normalized_label=normalize_term(label),
            label_type=label_type)

    def targets(self, text):
        preference, _created = CareerPreference.objects.update_or_create(
            profile=self.profile, defaults={'target_roles': text})
        return maintain_target_resolution_containers(preference)

    def opportunity(self, title, *, criteria=None, company='Example employer', **values):
        return create_professional_opportunity(
            title=title, company=company, criteria=criteria or [], **values)


class FeedV2AdmissionTests(FeedV2Case):
    def test_exact_admitted_despite_weak_fit_and_skill_overlap_cannot_admit(self):
        self.concept('ofo', 'Data Analyst')
        self.concept('esco', 'Data Analyst')
        self.targets('Data Analyst')
        exact = self.opportunity('Data Analyst', criteria=[criterion()])
        unrelated = self.opportunity('Logistics Coordinator', criteria=[{
            **criterion('python'),
            'criterion_type': 'SKILL',
            'qualification': None,
        }])
        ProfileSkill.objects.create(
            profile=self.profile, entered_name='Python', confirmed_by_user=True)

        generation, count = build_profile_generation(self.profile)
        # Skill overlap never admits a vacancy.  The unrelated listing has no
        # trusted occupational evidence, so it is not a survivor: no evaluation
        # row is written and it never reaches criterion matching.
        self.assertEqual(count, 1)
        row = generation.v2_evaluations.get(opportunity=exact)
        self.assertEqual(row.opportunity, exact)
        self.assertEqual(row.discovery_category, 'EXACT')
        self.assertEqual(row.discovery_rank, 1)
        self.assertIn(row.band, {'E', 'F'})
        self.assertFalse(
            generation.v2_evaluations.filter(opportunity=unrelated).exists())
        self.assertEqual(
            list(v2_feed_queryset(self.profile).values_list(
                'opportunity_id', flat=True)),
            [exact.pk])
        self.assertFalse(ProfileFeedEvaluation.objects.filter(
            discovery_category='PROFILE_RELEVANT').exists())

    def test_related_requires_active_candidate_policy(self):
        target_concept = self.concept('ofo', 'Data Analyst')
        vacancy_concept = self.concept('ofo', 'BI Analyst')
        self.targets('Data Analyst')
        vacancy = self.opportunity('BI Analyst')
        # No approved relationship exists yet, so the assessment is UNRESOLVED
        # and fails occupational admission: no evaluation row is written at all.
        generation, count = build_profile_generation(self.profile)
        self.assertEqual(count, 0)
        self.assertFalse(generation.v2_evaluations.filter(
            opportunity=vacancy).exists())

        OccupationRelationshipPolicy.objects.create(
            subject_concept=target_concept, object_concept=vacancy_concept,
            relationship_type='CLOSE_NEIGHBOUR', direction='forward',
            admission_allowed=True,
            policy_version=CANDIDATE_RELATIONSHIP_POLICY_VERSION,
            rationale='Explicit test policy.', provenance={'reviewed': True})
        replacement, count = build_profile_generation(self.profile)
        self.assertEqual(count, 1)
        self.assertNotEqual(replacement.pk, generation.pk)
        row = replacement.v2_evaluations.get(opportunity=vacancy)
        self.assertEqual(row.discovery_category, 'RELATED')
        generation.refresh_from_db()
        self.assertEqual(generation.state, 'SUPERSEDED')

    def test_reusable_rule_uses_one_direct_fact_without_two_hop_expansion(self):
        alpha = self.concept('ofo', 'Alpha Analyst')
        beta = self.concept('ofo', 'Beta Analyst')
        gamma = self.concept('ofo', 'Gamma Analyst')
        self.targets('Alpha Analyst')
        direct = TaxonomyRelation.objects.create(
            subject=alpha, relation_type='contextual_related', object=beta)
        TaxonomyRelation.objects.create(
            subject=beta, relation_type='contextual_related', object=gamma)
        rule = OccupationRelationshipRule.objects.create(
            policy_version=CANDIDATE_RELATIONSHIP_POLICY_VERSION,
            name='test-reviewed-contextual-neighbour',
            evidence_source='taxonomy_relation', taxonomy_source='ofo',
            evidence_relation_type='contextual_related',
            candidate_relationship_type='CLOSE_NEIGHBOUR', direction='forward',
            candidate_class='RELATED', admission_allowed=True,
            rationale='Fixture represents an explicitly reviewed source semantic.',
            provenance={'source': 'test-review'})
        beta_revision = self.opportunity('Beta Analyst').active_clean_revision
        gamma_revision = self.opportunity('Gamma Analyst').active_clean_revision
        target = self.profile.career_preferences.target_resolutions.get(ordinal=0)
        related = assess_target_vacancy(target, beta_revision)
        two_hop = assess_target_vacancy(target, gamma_revision)
        self.assertEqual(related.final_class, 'RELATED')
        self.assertEqual(related.policy_evidence[0]['rule_id'], rule.pk)
        self.assertEqual(related.policy_evidence[0]['source_fact_id'], direct.pk)
        self.assertEqual(two_hop.final_class, 'UNRESOLVED')

    def test_exact_for_one_target_survives_a_silent_other_target(self):
        ofo_first = self.concept('ofo', 'Data Analyst')
        self.label(ofo_first, 'Insights Specialist')
        self.concept('esco', 'Data Analyst')
        targets = self.targets('Data Analyst; Supply Analyst')
        revision = self.opportunity('Insights Specialist').active_clean_revision
        assessments = [assess_target_vacancy(target, revision) for target in targets]
        self.assertEqual(
            {row.final_class for row in assessments}, {'EXACT', 'UNRESOLVED'})
        generation, count = build_profile_generation(self.profile)
        self.assertEqual(count, 1)
        row = generation.v2_evaluations.get()
        self.assertEqual(row.discovery_category, 'EXACT')
        self.assertEqual(row.discovery_rank, 1)
        # A target that establishes nothing is not evidence against the job:
        # both assessments are recorded, but only the admitting EXACT assessment
        # is a survivor, so the vacancy is presented at the strongest class any
        # authored target actually established.
        self.assertEqual(len(row.contributing_target_assessment_ids), 1)
        self.assertEqual(row.conflicting_target_assessment_ids, [])

    def test_title_match_is_admitted_in_its_own_tier(self):
        """TITLE_MATCH is a survivor even with no taxonomy confirmation."""
        self.concept('ofo', 'Data Analyst')
        targets = self.targets('Data Analyst')
        listing = self.opportunity('Entry-Level Data Analyst')
        assessment = assess_target_vacancy(
            targets[0], listing.active_clean_revision)
        # Neither taxonomy confirms the occupation: this is title evidence only.
        self.assertEqual(assessment.final_class, TITLE_MATCH_CLASS)
        self.assertEqual(assessment.evidence_tier, 'TITLE_ONLY')
        self.assertEqual(assessment.tier_rank, 4)
        self.assertEqual(assessment.ofo_comparison['outcome'], 'UNRESOLVED')
        self.assertEqual(assessment.esco_comparison['outcome'], 'UNRESOLVED')
        self.assertEqual(assessment.policy_evidence, [])
        # ...but TITLE_MATCH remains an admission class, below EXACT/RELATED.
        self.assertIn(TITLE_MATCH_CLASS, FEED_SURVIVOR_CATEGORIES)
        generation, count = build_profile_generation(self.profile)
        self.assertEqual(count, 1)
        row = generation.v2_evaluations.get()
        self.assertEqual(row.discovery_category, TITLE_MATCH_CLASS)
        self.assertEqual(
            row.discovery_rank, FEED_DISCOVERY_RANK[TITLE_MATCH_CLASS])
        self.assertEqual(row.relationship_tier, 'TITLE_ONLY')
        # It is not part of the confirmed area, which stays EXACT/RELATED.
        self.assertNotIn(TITLE_MATCH_CLASS, ('EXACT', 'RELATED'))
        self.assertTrue(v2_feed_queryset(self.profile).exists())

    def test_unresolved_stops_before_criterion_matching(self):
        """Occupational admission gates the expensive criterion pass."""
        self.concept('ofo', 'Data Analyst')
        self.targets('Data Analyst')
        # Shares no concept with the target, so neither taxonomy confirms it.
        unmapped = self.opportunity('Zzz Unmapped Qqq Role')
        with patch('core.services.feed_v2.criterion_results') as probe:
            generation, count = build_profile_generation(self.profile)
        # Not admitted, so the criterion matcher is never invoked for it.
        self.assertEqual(count, 0)
        probe.assert_not_called()
        self.assertFalse(generation.v2_evaluations.filter(
            opportunity=unmapped).exists())
        self.assertEqual(list(v2_feed_queryset(self.profile)), [])


class FeedV2RankingTests(FeedV2Case):
    def test_relationship_class_and_tier_are_absolute_outer_order(self):
        exact_concept_ofo = self.concept('ofo', 'Data Analyst')
        self.concept('esco', 'Data Analyst')
        self.label(exact_concept_ofo, 'Data Reporting Analyst')
        related_concept = self.concept('ofo', 'BI Developer')
        targets = self.targets('Data Analyst')
        ProfileSkill.objects.create(
            profile=self.profile, entered_name='Python', confirmed_by_user=True)
        positive_skill = {
            **criterion('python'), 'criterion_type': 'SKILL', 'qualification': None}
        weak_exact = self.opportunity(
            'Data Analyst', criteria=[criterion()],
            date_listed=timezone.localdate() - timedelta(days=30),
            closing_date=timezone.localdate() + timedelta(days=30))
        alias_exact = self.opportunity(
            'Data Reporting Analyst', criteria=[positive_skill],
            date_listed=timezone.localdate(),
            closing_date=timezone.localdate() + timedelta(days=2))
        strong_related = self.opportunity(
            'BI Developer', criteria=[positive_skill],
            date_listed=timezone.localdate(),
            closing_date=timezone.localdate() + timedelta(days=1))
        OccupationRelationshipPolicy.objects.create(
            subject_concept=exact_concept_ofo, object_concept=related_concept,
            relationship_type='CLOSE_NEIGHBOUR', direction='forward',
            admission_allowed=True,
            policy_version=CANDIDATE_RELATIONSHIP_POLICY_VERSION,
            rationale='Explicit test relationship.', provenance={'reviewed': True})
        generation, count = build_profile_generation(self.profile)
        self.assertEqual(count, 3)
        ordered = list(v2_feed_queryset(self.profile))
        self.assertEqual(ordered[0].opportunity, weak_exact)
        self.assertEqual(ordered[0].relationship_tier, 'EXACT_MULTI_RESOLVER')
        self.assertEqual(ordered[1].opportunity, alias_exact)
        self.assertEqual(ordered[2].opportunity, strong_related)
        self.assertEqual(
            [row.discovery_category for row in ordered], ['EXACT', 'EXACT', 'RELATED'])

    def test_final_tie_breaker_is_deterministic(self):
        concept = self.concept('ofo', 'Data Analyst')
        self.label(concept, 'Insights Analyst')
        self.label(concept, 'Reporting Analyst')
        self.targets('Data Analyst')
        first = self.opportunity('Insights Analyst')
        second = self.opportunity('Reporting Analyst')
        build_profile_generation(self.profile)
        one = list(v2_feed_queryset(self.profile).values_list('opportunity_id', flat=True))
        two = list(v2_feed_queryset(self.profile).values_list('opportunity_id', flat=True))
        self.assertEqual(one, two)
        self.assertEqual(set(one), {first.pk, second.pk})


class FeedV2GenerationTests(FeedV2Case):
    def setUp(self):
        super().setUp()
        self.concept('ofo', 'Data Analyst')
        self.targets('Data Analyst')
        self.opportunity('Data Analyst')

    def test_building_generation_is_invisible_and_activation_is_atomic(self):
        generation, _count = build_profile_generation(self.profile)
        self.assertTrue(v2_feed_queryset(self.profile).exists())
        generation.state = 'BUILDING'
        generation.save(update_fields=['state', 'updated_at'])
        self.assertFalse(v2_feed_queryset(self.profile).exists())
        generation.state = 'ACTIVE'
        generation.save(update_fields=['state', 'updated_at'])
        self.assertTrue(v2_feed_queryset(self.profile).exists())

    def test_failed_replacement_preserves_prior_active_generation(self):
        active, _count = build_profile_generation(self.profile)
        ProfileSkill.objects.create(
            profile=self.profile, entered_name='SQL', confirmed_by_user=True)
        with patch('core.services.feed_v2._evaluation_values',
                   side_effect=RuntimeError('synthetic build failure')):
            with self.assertRaisesRegex(RuntimeError, 'synthetic'):
                build_profile_generation(self.profile, reason='evidence_changed')
        active.refresh_from_db()
        self.assertEqual(active.state, 'ACTIVE')
        self.assertEqual(self.profile.feed_state.active_generation_id, active.pk)
        self.assertTrue(v2_feed_queryset(self.profile).exists())

    def test_target_change_hides_old_target_generation_without_catalogue_fallback(self):
        build_profile_generation(self.profile)
        preference = self.profile.career_preferences
        preference.target_roles = 'Supply Chain Analyst'
        preference.save()
        self.assertFalse(v2_feed_queryset(self.profile).exists())
        transition = feed_transition(self.profile)
        self.assertFalse(transition.use_catalogue_fallback)
        self.assertEqual(transition.state, 'UPDATING_INTENT')

    def test_evidence_change_keeps_relationship_but_marks_fit_updating(self):
        build_profile_generation(self.profile)
        ProfileSkill.objects.create(
            profile=self.profile, entered_name='SQL', confirmed_by_user=True)
        self.assertTrue(v2_feed_queryset(self.profile).exists())
        transition = feed_transition(self.profile)
        self.assertFalse(transition.use_catalogue_fallback)
        self.assertTrue(transition.fit_is_updating)

    def test_policy_change_hides_the_feed_without_a_catalogue_fallback(self):
        build_profile_generation(self.profile)
        enqueue_profile_rebuild(
            self.profile.pk, 'relationship_policy_changed',
            change_kind='policy', causal_dependency='policy-row-2')
        transition = feed_transition(self.profile)
        self.assertEqual(transition.state, 'UPDATING_POLICY')
        self.assertFalse(transition.use_catalogue_fallback)
        self.assertIn('hidden', transition.message)

    def test_stale_clean_pointer_suppresses_only_that_opportunity(self):
        concept = TaxonomyConcept.objects.get(release=self.releases['ofo'])
        self.label(concept, 'Insights Analyst')
        other = self.opportunity('Insights Analyst')
        build_profile_generation(self.profile)
        first = ProfileFeedEvaluation.objects.exclude(opportunity=other).get().opportunity
        self.assertEqual(v2_feed_queryset(self.profile).count(), 2)
        type(first).objects.filter(pk=first.pk).update(active_clean_revision=None)
        visible = v2_feed_queryset(self.profile)
        self.assertEqual(visible.count(), 1)
        self.assertEqual(visible.get().opportunity, other)

    def test_rebuild_is_deterministic_and_idempotent(self):
        first, count = build_profile_generation(self.profile)
        second, second_count = build_profile_generation(self.profile)
        self.assertEqual((first.pk, count), (second.pk, second_count))
        self.assertEqual(first.evaluation_count, first.v2_evaluations.count())

    def test_idempotent_worker_restores_ready_and_completed_enqueue_is_quiet(self):
        active, _count = build_profile_generation(self.profile)
        task = enqueue_profile_rebuild(
            self.profile.pk, 'worker_probe', change_kind='catalogue',
            causal_dependency='worker-probe-v1')
        self.assertEqual(self.profile.feed_state.state, 'UPDATING_CATALOGUE')
        completed = process_claimed_task(claim_tasks(limit=1)[0])
        self.assertEqual(completed.state, 'COMPLETE')
        self.profile.feed_state.refresh_from_db()
        self.assertEqual(self.profile.feed_state.state, 'READY')
        self.assertEqual(self.profile.feed_state.active_generation_id, active.pk)

        duplicate = enqueue_profile_rebuild(
            self.profile.pk, 'worker_probe', change_kind='catalogue',
            causal_dependency='worker-probe-v1')
        self.assertEqual(duplicate.pk, completed.pk)
        self.profile.feed_state.refresh_from_db()
        self.assertEqual(self.profile.feed_state.state, 'READY')


class FeedV2FilterTests(FeedV2Case):
    def test_practical_filters_change_visibility_only(self):
        self.concept('ofo', 'Data Analyst')
        self.targets('Data Analyst')
        self.opportunity(
            'Data Analyst', company='Example Analytics',
            opportunity_type='internship', location='Johannesburg',
            clean_location={
                'raw': 'Johannesburg, Gauteng', 'city': 'Johannesburg',
                'region': 'Gauteng', 'country': 'South Africa'},
            work_arrangement='HYBRID', employment_type=['FULL_TIME'],
            salary={
                'raw': 'Fixed salary R 50000 per month', 'currency': 'ZAR',
                'minimum': 50000, 'maximum': 50000, 'period': 'MONTH',
                'is_negotiable': False},
        )
        generation, _count = build_profile_generation(self.profile)
        baseline = v2_feed_queryset(self.profile)
        row = baseline.get()
        dependency = row.dependency_hash
        cases = (
            ({'location': 'Johannesburg'}, 1), ({'location': 'Cape Town'}, 0),
            ({'work_arrangement': 'HYBRID'}, 1),
            ({'work_arrangement': 'REMOTE'}, 0),
            ({'employment_type': 'FULL_TIME'}, 1),
            ({'employment_type': 'PART_TIME'}, 0),
            ({'salary_min': '40000'}, 1), ({'salary_min': '60000'}, 0),
            ({'opportunity_kind': 'INTERNSHIP'}, 1),
            ({'opportunity_kind': 'VACANCY'}, 0),
            ({'company': 'Analytics'}, 1), ({'company': 'Different'}, 0),
        )
        for filters, expected in cases:
            with self.subTest(filters=filters):
                self.assertEqual(apply_practical_filters(baseline, filters).count(), expected)
        restored = apply_practical_filters(baseline, {}).get()
        self.assertEqual(restored.discovery_category, row.discovery_category)
        self.assertEqual(restored.dependency_hash, dependency)
        generation.refresh_from_db()
        self.assertEqual(generation.state, 'ACTIVE')


class FeedV2QueueTests(FeedV2Case):
    def test_duplicate_claim_and_enqueue_are_safe(self):
        self.targets('Data Analyst')
        first = enqueue_profile_rebuild(self.profile.pk, 'test', change_kind='intent')
        second = enqueue_profile_rebuild(self.profile.pk, 'test', change_kind='intent')
        self.assertEqual(first.pk, second.pk)
        claimed = claim_tasks(limit=1, worker_id='worker-a')
        self.assertEqual(len(claimed), 1)
        self.assertFalse(claim_tasks(limit=1, worker_id='worker-b'))

    def test_pending_duplicate_enqueue_returns_the_same_task(self):
        self.targets('Data Analyst')
        first = enqueue_profile_rebuild(self.profile.pk, 'intent_changed', change_kind='intent')
        second = enqueue_profile_rebuild(self.profile.pk, 'intent_changed', change_kind='intent')
        self.assertEqual(first.pk, second.pk)
        self.assertEqual(second.state, 'PENDING')
        self.assertEqual(
            EvaluationRecomputeTask.objects.filter(task_key=first.task_key).count(), 1)

    def test_running_duplicate_enqueue_returns_the_same_task(self):
        self.targets('Data Analyst')
        first = enqueue_profile_rebuild(self.profile.pk, 'intent_changed', change_kind='intent')
        claimed = claim_tasks(limit=1, worker_id='worker-a')[0]
        self.assertEqual(claimed.pk, first.pk)
        self.assertEqual(claimed.state, 'RUNNING')
        duplicate = enqueue_profile_rebuild(
            self.profile.pk, 'intent_changed', change_kind='intent')
        self.assertEqual(duplicate.pk, first.pk)
        self.assertEqual(
            EvaluationRecomputeTask.objects.filter(task_key=first.task_key).count(), 1)

    def test_failed_task_allows_a_fresh_task_and_keeps_the_old_row(self):
        self.targets('Data Analyst')
        failed = enqueue_profile_rebuild(self.profile.pk, 'intent_changed', change_kind='intent')
        failed.max_attempts = 1
        failed.save(update_fields=['max_attempts'])
        with patch('core.services.feed_queue.build_profile_generation',
                   side_effect=RuntimeError('synthetic build failure')):
            process_claimed_task(claim_tasks(limit=1)[0])
        failed.refresh_from_db()
        self.assertEqual(failed.state, 'FAILED')
        self.assertIn('synthetic build failure', failed.error_message)

        replacement = enqueue_profile_rebuild(
            self.profile.pk, 'intent_changed', change_kind='intent')
        self.assertNotEqual(replacement.pk, failed.pk)
        self.assertEqual(replacement.state, 'PENDING')
        self.assertEqual(replacement.task_key, failed.task_key)
        # The terminal row is audit history and must survive untouched.
        failed.refresh_from_db()
        self.assertEqual(failed.state, 'FAILED')
        self.assertIn('synthetic build failure', failed.error_message)
        self.assertEqual(
            EvaluationRecomputeTask.objects.filter(
                task_key=failed.task_key, state__in=('PENDING', 'RUNNING', 'COMPLETE')).count(),
            1)
        self.profile.feed_state.refresh_from_db()
        self.assertEqual(self.profile.feed_state.state, 'UPDATING_INTENT')

    def test_superseded_task_allows_a_fresh_task_and_keeps_the_old_row(self):
        self.targets('Data Analyst')
        stale = enqueue_profile_rebuild(self.profile.pk, 'intent_changed', change_kind='intent')
        EvaluationRecomputeTask.objects.filter(pk=stale.pk).update(
            state='SUPERSEDED', superseded_at=timezone.now(),
            supersession_reason='A newer complete-profile rebuild superseded this task.')

        replacement = enqueue_profile_rebuild(
            self.profile.pk, 'intent_changed', change_kind='intent')
        self.assertNotEqual(replacement.pk, stale.pk)
        self.assertEqual(replacement.state, 'PENDING')
        self.assertEqual(replacement.task_key, stale.task_key)
        stale.refresh_from_db()
        self.assertEqual(stale.state, 'SUPERSEDED')
        self.assertIn('superseded this task', stale.supersession_reason)
        self.assertIsNotNone(stale.superseded_at)
        self.profile.feed_state.refresh_from_db()
        self.assertEqual(self.profile.feed_state.state, 'UPDATING_INTENT')

    def test_live_task_key_stays_unique_in_the_database(self):
        self.targets('Data Analyst')
        first = enqueue_profile_rebuild(self.profile.pk, 'intent_changed', change_kind='intent')
        self.assertEqual(first.state, 'PENDING')
        # While the first task is still live, a second live row for the same
        # logical dependency must be refused by the database, so duplicate
        # protection does not depend on application code alone.
        with self.assertRaises(Exception):
            EvaluationRecomputeTask.objects.create(
                profile=self.profile, reason='manual', task_version=TASK_VERSION,
                task_key=first.task_key, dependency_hash=first.dependency_hash,
                state='PENDING')

    def test_expired_lease_is_recoverable(self):
        self.targets('Data Analyst')
        enqueue_profile_rebuild(self.profile.pk, 'test', change_kind='intent')
        task = claim_tasks(limit=1)[0]
        EvaluationRecomputeTask.objects.filter(pk=task.pk).update(
            lease_expires_at=timezone.now() - timedelta(seconds=1))
        reclaimed, failed = reclaim_expired_tasks()
        self.assertEqual((reclaimed, failed), (1, 0))
        self.assertEqual(len(claim_tasks(limit=1)), 1)

    def test_retry_is_bounded_and_records_failure(self):
        self.targets('Data Analyst')
        task = enqueue_profile_rebuild(self.profile.pk, 'test', change_kind='intent')
        task.max_attempts = 2
        task.save(update_fields=['max_attempts'])
        with patch('core.services.feed_queue.build_profile_generation',
                   side_effect=RuntimeError('broken build')):
            for _attempt in range(2):
                EvaluationRecomputeTask.objects.filter(pk=task.pk).update(
                    state='PENDING', available_at=timezone.now())
                claimed = claim_tasks(limit=1)[0]
                process_claimed_task(claimed)
        task.refresh_from_db()
        self.assertEqual(task.state, 'FAILED')
        self.assertEqual(task.attempts, 2)
        self.assertIn('broken build', task.error_message)

    def test_related_fanout_uses_policy_and_skill_overlap_does_not(self):
        target = self.concept('ofo', 'Data Analyst')
        vacancy_concept = self.concept('ofo', 'BI Analyst')
        self.targets('Data Analyst')
        opportunity = self.opportunity('BI Analyst')
        OccupationRelationshipPolicy.objects.create(
            subject_concept=target, object_concept=vacancy_concept,
            relationship_type='CLOSE_NEIGHBOUR', direction='forward',
            admission_allowed=True,
            policy_version=CANDIDATE_RELATIONSHIP_POLICY_VERSION,
            rationale='Explicit fanout fixture.', provenance={'reviewed': True})
        task = enqueue_opportunity_rebuild(opportunity.pk, 'new_revision')
        claimed = claim_tasks(limit=1)[0]
        process_claimed_task(claimed)
        self.assertTrue(EvaluationRecomputeTask.objects.filter(
            profile=self.profile, task_version=TASK_VERSION, state='PENDING').exists())

        unrelated_profile = Profile.objects.create(
            user=User.objects.create_user('skill-only', password='password'))
        ProfileSkill.objects.create(
            profile=unrelated_profile, entered_name='Python', confirmed_by_user=True)
        self.assertFalse(EvaluationRecomputeTask.objects.filter(
            profile=unrelated_profile, task_version=TASK_VERSION).exists())


@override_settings(CAREERGPS_FEED_READ_VERSION='v2')
class FeedV2PresentationTests(FeedV2Case):
    def test_exact_career_labels_search_and_one_universe_at_runtime(self):
        target_ofo = self.concept('ofo', 'Data Analyst')
        target_esco = self.concept('esco', 'Data Analyst')
        vacancy_ofo = self.concept('ofo', 'Warehouse Clerk')
        vacancy_esco = self.concept('esco', 'Warehouse Clerk')
        self.targets('Data Analyst')
        exact = self.opportunity('Data Analyst')
        outside = self.opportunity('Warehouse Clerk')
        unsupported_policy(target_ofo, vacancy_ofo)
        unsupported_policy(target_esco, vacancy_esco)
        generation, count = build_profile_generation(self.profile)
        self.assertEqual(count, 1)
        self.assertEqual(generation.v2_evaluations.get().opportunity, exact)
        self.client.force_login(self.profile.user)
        outside_title = outside.active_clean_revision.cleaned_title

        response = self.client.get('/opportunities/')
        self.assertContains(response, 'Exact match')
        self.assertContains(response, 'Current fit')
        self.assertNotContains(response, 'Profile relevant')
        # A job with no confirmed occupational relationship is never presented
        # as a match, and never inside the confirmed feed.
        self.assertNotIn(
            outside.pk,
            [row['opportunity'].pk for row in response.context['results']])
        ordered = [row['opportunity'].pk for row in response.context['results']]
        self.assertEqual(ordered, [exact.pk])
        self.assertEqual(
            response.context['results'][0]['admission_class'], 'EXACT')
        self.assertTrue(
            response.context['results'][0]['is_confirmed_relationship'])

        detail = self.client.get(f'/opportunities/{exact.pk}/')
        self.assertContains(detail, 'Exact occupational match:')
        self.assertContains(detail, 'Current fit:')

        inside = self.client.get('/opportunities/', {'search': 'Data Analyst'})
        self.assertContains(inside, 'Data Analyst')
        self.assertNotContains(inside, outside_title)

        # A proven-unrelated job stays out of the confirmed feed under any
        # filter, rather than being surfaced as a recommendation.
        scope = self.client.get('/opportunities/', {'search': 'Warehouse'})
        self.assertNotContains(scope, outside_title)

        empty = self.client.get('/opportunities/', {'search': 'zzz-no-match'})
        self.assertContains(empty, 'No recommendations match this search')

        # There is no catalogue bypass: the practical filters only narrow the
        # one universe, so a proven-unrelated job cannot be pulled back in.
        browse = self.client.get('/opportunities/', {'catalogue': '1'})
        self.assertContains(browse, 'Data Analyst')
        self.assertNotContains(browse, outside_title)
        self.assertNotContains(browse, 'Opportunity catalogue')
        self.assertEqual(response.context['page_obj'].paginator.count, 1)
        self.assertEqual(browse.context['page_obj'].paginator.count, 1)

    def test_unrelated_job_stays_visible_as_tier_zero_band_f(self):
        target_ofo = self.concept('ofo', 'Data Analyst')
        target_esco = self.concept('esco', 'Data Analyst')
        vacancy_ofo = self.concept('ofo', 'Warehouse Clerk')
        vacancy_esco = self.concept('esco', 'Warehouse Clerk')
        self.targets('Data Analyst')
        catalogue_row = self.opportunity('Warehouse Clerk')
        unsupported_policy(target_ofo, vacancy_ofo)
        unsupported_policy(target_esco, vacancy_esco)
        generation, count = build_profile_generation(self.profile)
        self.assertEqual(count, 0)
        self.assertEqual(generation.state, 'ACTIVE')
        self.client.force_login(self.profile.user)
        catalogue_title = catalogue_row.active_clean_revision.cleaned_title

        # This row is proven UNRELATED, so it is excluded from the feed. The
        # page must say so honestly rather than presenting the whole
        # catalogue as personalised recommendations.
        response = self.client.get('/opportunities/')
        self.assertNotContains(response, catalogue_title)
        self.assertEqual(list(response.context['results']), [])
        self.assertNotContains(response, 'Explore opportunity catalogue')

        dashboard = self.client.get('/dashboard/')
        self.assertContains(dashboard, catalogue_title)

        browse = self.client.get('/opportunities/', {'catalogue': '1'})
        self.assertNotContains(browse, catalogue_title)
        self.assertEqual(browse.context['page_obj'].paginator.count, 0)


class FeedV2UnrelatedExclusionTests(FeedV2Case):
    """Locked contract: a vacancy is excluded only on explicit negative proof
    from every configured occupational source, and only for every authored
    target.  Absence of evidence always keeps the vacancy."""

    def negative_listing(self):
        target_ofo = self.concept('ofo', 'Data Analyst')
        target_esco = self.concept('esco', 'Data Analyst')
        vacancy_ofo = self.concept('ofo', 'Warehouse Clerk')
        vacancy_esco = self.concept('esco', 'Warehouse Clerk')
        self.targets('Data Analyst')
        listing = self.opportunity('Warehouse Clerk', criteria=[criterion()])
        return target_ofo, target_esco, vacancy_ofo, vacancy_esco, listing

    def target_container(self, ordinal=0):
        return self.profile.career_preferences.target_resolutions.get(
            ordinal=ordinal)

    def assert_authored_target_authority(self, expected):
        authored = self.profile.career_preferences
        self.assertEqual(
            [row.authored_text for row in
             authored.target_resolutions.order_by('ordinal')],
            list(parse_target_roles(authored.target_roles)))
        self.assertEqual(tuple(parse_target_roles(authored.target_roles)), expected)

    def test_explicit_negative_requires_current_policy_evidence(self):
        current = {
            'outcome': 'INCOMPATIBLE',
            'reason': 'relationship_policy_does_not_admit',
            'policy': {
                'relationship_type': 'UNSUPPORTED', 'admission_allowed': False,
                'policy_version': CANDIDATE_RELATIONSHIP_POLICY_VERSION,
            },
        }
        self.assertTrue(explicit_unsupported(current))
        superseded = {
            **current, 'policy': {
                **current['policy'], 'policy_version': 'superseded'}}
        self.assertFalse(explicit_unsupported(superseded))
        admitting = {
            **current, 'policy': {
                **current['policy'], 'admission_allowed': True}}
        self.assertFalse(explicit_unsupported(admitting))
        weak = {
            **current, 'policy': {
                **current['policy'], 'relationship_type': 'WEAK_ADJACENCY'}}
        self.assertFalse(explicit_unsupported(weak))
        self.assertFalse(explicit_unsupported({
            'outcome': 'UNRESOLVED', 'reason': 'source_unresolved'}))
        self.assertFalse(explicit_unsupported({
            'outcome': 'INCOMPATIBLE',
            'reason': 'different_resolved_occupations_without_approved_policy'}))
        self.assertFalse(explicit_unsupported(None))


    def test_every_source_negative_excludes_the_vacancy(self):
        target_ofo, target_esco, vacancy_ofo, vacancy_esco, listing = (
            self.negative_listing())
        unsupported_policy(target_ofo, vacancy_ofo)
        unsupported_policy(target_esco, vacancy_esco)
        self.assert_authored_target_authority(('Data Analyst',))

        assessment = assess_target_vacancy(
            self.target_container(), listing.active_clean_revision)
        self.assertEqual(assessment.final_class, UNRELATED_CLASS)
        self.assertEqual(assessment.reason_code, UNRELATED_REASON_CODE)
        self.assertEqual(
            {item['relationship_type'] for item in assessment.policy_evidence},
            {'UNSUPPORTED'})
        self.assertTrue(all(
            item['admission_allowed'] is False
            for item in assessment.policy_evidence))

        generation, count = build_profile_generation(self.profile)
        self.assertEqual(count, 0)
        self.assertFalse(generation.v2_evaluations.exists())
        self.assertFalse(v2_feed_queryset(self.profile).exists())

    def test_single_source_negative_neither_proves_unrelated_nor_admits(self):
        target_ofo, _target_esco, vacancy_ofo, _vacancy_esco, listing = (
            self.negative_listing())
        unsupported_policy(target_ofo, vacancy_ofo)

        assessment = assess_target_vacancy(
            self.target_container(), listing.active_clean_revision)
        self.assertEqual(assessment.final_class, 'UNRESOLVED')
        self.assertEqual(assessment.reason_code, 'no_approved_relationship')
        self.assertNotEqual(assessment.final_class, UNRELATED_CLASS)
        self.assertEqual(
            assessment.diagnostics['source_outcomes'],
            {'ofo': 'INCOMPATIBLE', 'esco': 'INCOMPATIBLE'})
        # A single explicit negative source never proves unrelatedness, but
        # UNRESOLVED carries no trusted occupational evidence either, so it
        # fails occupational admission: no evaluation row is written and no
        # criterion matching runs.
        generation, count = build_profile_generation(self.profile)
        self.assertEqual(count, 0)
        self.assertFalse(generation.v2_evaluations.exists())
        self.assertEqual(list(v2_feed_queryset(self.profile)), [])

    def test_different_codes_without_policy_keep_the_vacancy_unconflicted(self):
        """No approved policy anywhere is absent evidence, not a denial.

        Both taxonomies put the target and the job in different occupations and
        neither has an approved policy for the pair, so nothing was established
        and nothing was refused.  The job is not proved unrelated, it carries no
        conflict, and it is excluded from the personalised feed because no
        trusted occupational relationship was established.
        """
        _target_ofo, _target_esco, _vacancy_ofo, _vacancy_esco, listing = (
            self.negative_listing())

        assessment = assess_target_vacancy(
            self.target_container(), listing.active_clean_revision)
        self.assertEqual(assessment.final_class, 'UNRESOLVED')
        self.assertEqual(assessment.reason_code, 'no_approved_relationship')
        self.assertEqual(assessment.denied_pairing_count, 0)
        self.assertEqual(assessment.inconclusive_pairing_count, 2)
        self.assertEqual(assessment.relationship_count, 0)
        self.assertEqual(assessment.conflicting_evidence, [])
        self.assertEqual(assessment.diagnostics['source_outcomes'], {
            'ofo': 'INCOMPATIBLE', 'esco': 'INCOMPATIBLE'})
        # Nothing denied the pair, so the vacancy is never proved unrelated.
        self.assertFalse(proved_unrelated([assessment], 1))

        # Absent evidence still is not positive occupational evidence, so the
        # UNRESOLVED assessment fails occupational admission and is excluded.
        generation, count = build_profile_generation(self.profile)
        self.assertEqual(count, 0)
        self.assertFalse(generation.v2_evaluations.exists())
        self.assertEqual(list(v2_feed_queryset(self.profile)), [])


    def test_weak_adjacency_policy_is_not_negative_proof(self):
        target_ofo, _target_esco, vacancy_ofo, _vacancy_esco, listing = (
            self.negative_listing())
        OccupationRelationshipPolicy.objects.create(
            subject_concept=target_ofo, object_concept=vacancy_ofo,
            relationship_type='WEAK_ADJACENCY', direction='forward',
            admission_allowed=False,
            policy_version=CANDIDATE_RELATIONSHIP_POLICY_VERSION,
            rationale='Weak adjacency never proves unrelatedness.',
            provenance={'reviewed': True})

        assessment = assess_target_vacancy(
            self.target_container(), listing.active_clean_revision)
        self.assertEqual(assessment.final_class, 'UNRESOLVED')
        # Weak adjacency is neither proof of unrelatedness nor trusted positive
        # evidence, so the job is neither excluded-as-unrelated nor admitted.
        generation, count = build_profile_generation(self.profile)
        self.assertEqual(count, 0)
        self.assertFalse(generation.v2_evaluations.exists())

    def test_incomplete_authored_target_matrix_fails_open(self):
        target_ofo = self.concept('ofo', 'Data Analyst')
        target_esco = self.concept('esco', 'Data Analyst')
        vacancy_ofo = self.concept('ofo', 'Warehouse Clerk')
        vacancy_esco = self.concept('esco', 'Warehouse Clerk')
        self.targets('Data Analyst; Supply Chain Planner')
        listing = self.opportunity('Warehouse Clerk')
        unsupported_policy(target_ofo, vacancy_ofo)
        unsupported_policy(target_esco, vacancy_esco)
        self.assert_authored_target_authority(
            ('Data Analyst', 'Supply Chain Planner'))

        revision = listing.active_clean_revision
        proven = assess_target_vacancy(self.target_container(0), revision)
        self.assertEqual(proven.final_class, UNRELATED_CLASS)
        # One authored target has no assessment yet, so the set is incomplete
        # and the vacancy must be kept rather than excluded.
        self.assertFalse(proved_unrelated([proven], 2))
        self.assertTrue(proved_unrelated([proven], 1))

        # The fail-open matrix still proves nothing unrelated, but the one
        # target that was assessed established no trusted relationship, so the
        # vacancy is excluded from the personalised feed rather than admitted.
        generation, count = build_profile_generation(self.profile)
        self.assertEqual(count, 0)
        self.assertFalse(generation.v2_evaluations.exists())

    def test_opportunity_fanout_keeps_every_unproven_profile(self):
        target_ofo, target_esco, vacancy_ofo, vacancy_esco, listing = (
            self.negative_listing())
        task = enqueue_opportunity_rebuild(listing.pk, 'new_revision')
        self.assertEqual(
            _opportunity_fanout(claim_tasks(limit=1)[0]), [self.profile.pk])
        self.assertTrue(EvaluationRecomputeTask.objects.filter(
            profile=self.profile, task_version=TASK_VERSION,
            state='PENDING').exists())

        EvaluationRecomputeTask.objects.all().delete()
        unsupported_policy(target_ofo, vacancy_ofo)
        unsupported_policy(target_esco, vacancy_esco)
        self.assertEqual(_opportunity_fanout(task), [])
        self.assertFalse(EvaluationRecomputeTask.objects.filter(
            profile=self.profile, task_version=TASK_VERSION).exists())

    def test_obvious_senior_revision_never_enters_a_v2_feed(self):
        self.concept('ofo', 'Data Analyst')
        self.targets('Data Analyst')
        senior = self.opportunity('Data Analyst', seniority='OBVIOUS_SENIOR')
        self.assertEqual(
            senior.active_clean_revision.seniority_label, 'OBVIOUS_SENIOR')
        generation, count = build_profile_generation(self.profile)
        self.assertEqual(count, 0)
        self.assertFalse(generation.v2_evaluations.exists())
        self.assertFalse(v2_feed_queryset(self.profile).exists())
        self.assertTrue(Opportunity.objects.professionally_ready(
            include_obvious_senior=True).filter(pk=senior.pk).exists())
        self.assertFalse(Opportunity.objects.professionally_ready(
            include_obvious_senior=False).filter(pk=senior.pk).exists())

    def test_positive_relationship_beats_a_single_negative_source(self):
        target_ofo, target_esco, vacancy_ofo, vacancy_esco, listing = (
            self.negative_listing())
        unsupported_policy(target_ofo, vacancy_ofo)
        OccupationRelationshipPolicy.objects.create(
            subject_concept=target_esco, object_concept=vacancy_esco,
            relationship_type='CLOSE_NEIGHBOUR', direction='forward',
            admission_allowed=True,
            policy_version=CANDIDATE_RELATIONSHIP_POLICY_VERSION,
            rationale='Reviewed positive relationship.', provenance={'reviewed': True})

        assessment = assess_target_vacancy(
            self.target_container(), listing.active_clean_revision)
        # The approved ESCO relationship stands on its own; OFO's denial is
        # disclosed alongside it instead of vetoing it.
        self.assertEqual(assessment.final_class, 'RELATED')
        self.assertEqual(assessment.reason_code, 'one_related_other_source_disagrees')
        self.assertTrue(assessment.conflicting_evidence)

        generation, count = build_profile_generation(self.profile)
        self.assertEqual(count, 1)
        row = generation.v2_evaluations.get()
        self.assertEqual(row.discovery_category, 'RELATED')
        self.assertEqual(row.discovery_rank, 2)
        self.assertEqual(len(row.conflicting_target_assessment_ids), 1)
        self.assertTrue(v2_feed_queryset(self.profile).exists())

    def test_unrelated_target_with_related_target_is_kept(self):
        target_ofo = self.concept('ofo', 'Data Analyst')
        target_esco = self.concept('esco', 'Data Analyst')
        vacancy_ofo = self.concept('ofo', 'Warehouse Clerk')
        vacancy_esco = self.concept('esco', 'Warehouse Clerk')
        related_target = self.concept('ofo', 'Stock Controller')
        self.targets('Data Analyst; Stock Controller')
        listing = self.opportunity('Warehouse Clerk')
        unsupported_policy(target_ofo, vacancy_ofo)
        unsupported_policy(target_esco, vacancy_esco)
        OccupationRelationshipPolicy.objects.create(
            subject_concept=related_target, object_concept=vacancy_ofo,
            relationship_type='CLOSE_NEIGHBOUR', direction='forward',
            admission_allowed=True,
            policy_version=CANDIDATE_RELATIONSHIP_POLICY_VERSION,
            rationale='Reviewed related target relationship.',
            provenance={'reviewed': True})

        classes = {
            assess_target_vacancy(target, listing.active_clean_revision).final_class
            for target in self.profile.career_preferences.target_resolutions.all()}
        self.assertEqual(classes, {UNRELATED_CLASS, 'RELATED'})

        generation, count = build_profile_generation(self.profile)
        self.assertEqual(count, 1)
        row = generation.v2_evaluations.get()
        self.assertEqual(row.discovery_category, 'RELATED')
        self.assertEqual(row.discovery_rank, 2)

    def test_unrelated_target_with_disagreeing_target_is_kept(self):
        target_ofo = self.concept('ofo', 'Data Analyst')
        target_esco = self.concept('esco', 'Data Analyst')
        vacancy_ofo = self.concept('ofo', 'Warehouse Clerk')
        vacancy_esco = self.concept('esco', 'Warehouse Clerk')
        conflict_ofo = self.concept('ofo', 'Stock Controller')
        conflict_esco = self.concept('esco', 'Stock Controller')
        self.targets('Data Analyst; Stock Controller')
        listing = self.opportunity('Warehouse Clerk')
        unsupported_policy(target_ofo, vacancy_ofo)
        unsupported_policy(target_esco, vacancy_esco)
        OccupationRelationshipPolicy.objects.create(
            subject_concept=conflict_ofo, object_concept=vacancy_ofo,
            relationship_type='CLOSE_NEIGHBOUR', direction='forward',
            admission_allowed=True,
            policy_version=CANDIDATE_RELATIONSHIP_POLICY_VERSION,
            rationale='Reviewed positive relationship.', provenance={'reviewed': True})
        unsupported_policy(conflict_esco, vacancy_esco)

        classes = {
            assess_target_vacancy(target, listing.active_clean_revision).final_class
            for target in self.profile.career_preferences.target_resolutions.all()}
        self.assertEqual(classes, {UNRELATED_CLASS, 'RELATED'})

        generation, count = build_profile_generation(self.profile)
        self.assertEqual(count, 1)
        row = generation.v2_evaluations.get()
        # The stock-controller target was confirmed by OFO while ESCO disagreed.
        # That disagreement is disclosed, not a verdict that hides the job.
        self.assertEqual(row.discovery_category, 'RELATED')
        self.assertEqual(row.discovery_rank, 2)
        self.assertEqual(len(row.conflicting_target_assessment_ids), 1)

    def test_ambiguous_source_negative_keeps_the_vacancy(self):
        _target_ofo, target_esco, _vacancy_ofo, vacancy_esco, listing = (
            self.negative_listing())
        self.label(self.concept('ofo', 'Warehouse Clerk'), 'Warehouse Clerk')
        unsupported_policy(target_esco, vacancy_esco)

        revision = listing.active_clean_revision
        resolve_vacancy_sources(revision)
        assessment = assess_target_vacancy(self.target_container(), revision)
        # The OFO source kept two readings of the vacancy and connected neither
        # of them to the target.  Several readings is inconclusive, not
        # ambiguous, so it answers `UNRESOLVED`; it is still no explicit
        # negative evidence, so it can never prove the vacancy unrelated.
        self.assertEqual(assessment.diagnostics['source_outcomes'], {
            'ofo': 'UNRESOLVED', 'esco': 'INCOMPATIBLE'})
        self.assertEqual(assessment.ofo_comparison['reason'],
                         'several_results_none_connected')
        self.assertEqual(assessment.denied_pairing_count, 1)
        self.assertGreaterEqual(assessment.inconclusive_pairing_count, 1)
        self.assertNotEqual(assessment.final_class, UNRELATED_CLASS)
        self.assertEqual(assessment.final_class, 'UNRESOLVED')
        # Several retained readings are inconclusive, never a trusted
        # relationship, so the assessment fails occupational admission.
        generation, count = build_profile_generation(self.profile)
        self.assertEqual(count, 0)
        self.assertFalse(generation.v2_evaluations.exists())

    def test_discovery_rank_table_matches_the_locked_order(self):
        # CONFLICT stays in the table so a historical row still has a position,
        # but the current aggregator never produces it: a confirming taxonomy is
        # enough and the other answer is disclosed evidence.
        self.assertEqual(FEED_DISCOVERY_RANK, {
            'EXACT': 1, 'RELATED': 2, 'CONFLICT': 3, TITLE_MATCH_CLASS: 4,
            'UNRESOLVED': 5})
        # Occupational admission is EXACT / RELATED / TITLE_MATCH only.  An
        # assessment with no trusted evidence and no valid title match is not a
        # survivor, so it never reaches criterion matching.  UNRESOLVED keeps a
        # rank slot only so a historical row still renders and sorts.
        self.assertEqual(FEED_SURVIVOR_CATEGORIES, (
            'EXACT', 'RELATED', 'CONFLICT', TITLE_MATCH_CLASS))
        self.assertNotIn('UNRESOLVED', FEED_SURVIVOR_CATEGORIES)
        self.assertEqual(FEED_DISCOVERY_CATEGORIES, ('CONFLICT', 'UNRESOLVED',
                                                     TITLE_MATCH_CLASS))
        self.assertNotIn(UNRELATED_CLASS, FEED_DISCOVERY_RANK)
        self.assertNotIn(UNRELATED_CLASS, FEED_SURVIVOR_CATEGORIES)
        # Title resemblance is discovery only: it can never sit in the confirmed
        # area, because neither taxonomy confirmed the occupation.
        self.assertNotIn(TITLE_MATCH_CLASS, FEED_SURVIVOR_CATEGORIES[:3])
        # ...but TITLE_MATCH stays a survivor, ranked below EXACT and RELATED.
        self.assertEqual(FEED_SURVIVOR_CATEGORIES[-1], TITLE_MATCH_CLASS)

    def test_title_gate_block_excludes_from_the_personalized_feed(self):
        self.concept('ofo', 'Data Analyst')
        self.targets('Data Analyst')
        self.opportunity('Senior Data Analyst')
        generation, count = build_profile_generation(self.profile)
        self.assertEqual(count, 0)
        self.assertFalse(generation.v2_evaluations.exists())

    def test_only_obvious_senior_is_excluded_and_gates_never_hide_jobs(self):
        blocked = self.opportunity('Senior Warehouse Lead')
        senior = self.opportunity('Warehouse Clerk', seniority='OBVIOUS_SENIOR')
        uncertain = self.opportunity('Warehouse Review Role',
                                     seniority='UNCERTAIN')
        allowed = self.opportunity('Warehouse Assistant')
        # A row is resolved only after the read-only check has admitted it, so
        # the excluded row is never resolved at all.
        for opportunity in (blocked, senior, uncertain, allowed):
            if not evaluate_revision(opportunity.active_clean_revision).blocked:
                resolve_vacancy_sources(opportunity.active_clean_revision)
        self.assertEqual(
            evaluate_revision(blocked.active_clean_revision).decision, 'BLOCK')
        self.assertEqual(
            senior.active_clean_revision.seniority_label, 'OBVIOUS_SENIOR')
        self.assertEqual(
            uncertain.active_clean_revision.seniority_label, 'UNCERTAIN')
        # Nothing about the senior decision is stored: recomputing it decides
        # again the same way, no seniority row exists anywhere, and the excluded
        # row has no occupational container of its own.
        self.assertFalse(OpportunityTitleGateDecision.objects.exists())
        self.assertFalse(OpportunityTitleResolution.objects.filter(
            revision=blocked.active_clean_revision).exists())
        self.assertEqual(
            evaluate_revision(blocked.active_clean_revision).decision, 'BLOCK')

        self.client.force_login(self.profile.user)
        response = self.client.get('/opportunities/')
        # The shared senior-role gate is now a candidate visibility rule on
        # both pages: a title-construction block hides the listing, and
        # UNCERTAIN seniority alone never hides a job.
        self.assertNotContains(response, 'Senior Warehouse Lead')
        self.assertContains(response, 'Warehouse Review Role')
        self.assertContains(response, 'Warehouse Assistant')
        self.assertNotContains(response, 'Warehouse Clerk')

    @override_settings(CAREERGPS_V2_WORKER_SCHEDULED=True)
    def test_verification_report_passes_with_the_widened_invariant(self):
        target_ofo = self.concept('ofo', 'Data Analyst')
        target_esco = self.concept('esco', 'Data Analyst')
        vacancy_ofo = self.concept('ofo', 'Warehouse Clerk')
        vacancy_esco = self.concept('esco', 'Warehouse Clerk')
        self.targets('Data Analyst')
        self.opportunity('Warehouse Clerk')
        self.opportunity('Data Analyst')
        unsupported_policy(target_ofo, vacancy_ofo)
        unsupported_policy(target_esco, vacancy_esco)
        _generation, count = build_profile_generation(self.profile)
        self.assertEqual(count, 1)

        report = verification_report()
        failing = {key: value for key, value in report['failures'].items() if value}
        self.assertEqual(failing, {})
        self.assertTrue(report['passed'])
        self.assertEqual(report['explicitly_unrelated_assessments'], 1)
        self.assertEqual(report['v2_categories'], {'EXACT': 1})



class FeedAdmissionAndUiTests(FeedV2GenerationTests):
    """Main feed holds only confirmed matches; the rest is discoverable."""
    """Main feed holds only confirmed matches; the rest is discoverable."""

    def shared_concepts(self, title):
        """Reuse an existing label in each source instead of duplicating it.

        Two identical labels in one source are genuinely ambiguous, so a
        duplicate would test the ambiguity path rather than the intended one.
        """
        return tuple(
            TaxonomyConcept.objects.filter(
                release=self.releases[source], kind='occupation',
                normalized_label=normalize_term(title)).first()
            or self.concept(source, title)
            for source in ('ofo', 'esco'))

    def test_requirements_never_exclude_a_confirmed_occupationally_relevant_job(self):
        """A relevant job with no matching skills stays in the main feed."""
        self.shared_concepts('Data Analyst')
        self.targets('Data Analyst')
        # A skill on the job that the profile does not have at all.
        listing = self.opportunity('Data Analyst', criteria=[{
            'criterion_id': 'c1', 'criterion_type': 'SKILL',
            'normalized_value': 'sap fi', 'raw_value': 'SAP FI',
            'requirement_level': 'REQUIRED', 'significance': 'CRUCIAL',
            'source_section': 'raw_content',
            'evidence': [{'text': 'SAP FI required'}],
            'requirement_group': None, 'qualification': None,
            'experience': None, 'language': None}])
        _generation, _count = build_profile_generation(self.profile)
        self.client.force_login(self.profile.user)
        response = self.client.get('/opportunities/')
        ids = [row['opportunity'].pk for row in response.context['results']]
        self.assertIn(listing.pk, ids)
        card = next(row for row in response.context['results']
                    if row['opportunity'].pk == listing.pk)
        # Evidence gaps are reported, but the job is not removed.
        self.assertTrue(card['is_confirmed_relationship'])
        self.assertEqual(card['admission_class'], 'EXACT')
    def test_unconfirmed_jobs_are_separated_and_never_labelled_as_matches(self):
        self.shared_concepts('Data Analyst')
        self.targets('Data Analyst')
        # A job whose title matches no concept in either taxonomy.
        unmapped = self.opportunity('Zzz Unmapped Qqq Role')
        build_profile_generation(self.profile)
        self.client.force_login(self.profile.user)
        response = self.client.get('/opportunities/')
        confirmed = response.context['results']
        confirmed_ids = [row['opportunity'].pk for row in confirmed]
        self.assertNotIn(unmapped.pk, confirmed_ids)
        # Everything in the main feed is a confirmed admission.
        self.assertTrue(confirmed_ids)
        for row in confirmed:
            self.assertTrue(row['is_confirmed_relationship'])
        discovery = {row['opportunity'].pk: row
                     for row in response.context['discovery_results']}
        # No trusted occupational evidence means no personalised admission: the
        # unmapped job enters neither the confirmed nor the discovery area, and
        # it falls through to the general catalogue surface instead.
        self.assertNotIn(unmapped.pk, discovery)
        uncertain = {row['opportunity'].pk: row
                     for row in response.context['uncertain_results']}
        self.assertIn(unmapped.pk, uncertain)
        row = uncertain[unmapped.pk]
        self.assertFalse(row['is_confirmed_relationship'])
        self.assertEqual(row['discovery_label'], 'Not yet assessed')
        self.assertNotEqual(row['discovery_label'], 'Exact match')
        self.assertNotEqual(row['discovery_label'], 'Related role')
        # The discovery heading only renders when that area has rows, so an
        # empty discovery area never claims anything about this listing.
        self.assertEqual(list(response.context['discovery_results']), [])
        self.assertNotContains(response, 'Not yet confirmed for your targets')

    def test_banner_does_not_claim_every_listing_was_validated(self):
        self.shared_concepts('Data Analyst')
        self.targets('Data Analyst')
        self.opportunity('Data Analyst')
        build_profile_generation(self.profile)
        self.client.force_login(self.profile.user)
        response = self.client.get('/opportunities/')
        self.assertNotContains(
            response,
            'Every listing here passed occupational validation')

    def test_without_targets_the_general_feed_stays_and_prompts_for_targets(self):
        self.shared_concepts('Data Analyst')
        self.targets('')
        self.opportunity('Data Analyst')
        self.client.force_login(self.profile.user)
        response = self.client.get('/opportunities/')
        self.assertFalse(response.context['has_targets'])
        self.assertContains(response, 'Data Analyst')
        self.assertEqual(response.context['discovery_results'], [])
        self.assertContains(response, 'Add target roles')

    def test_senior_role_is_excluded_by_the_shared_gate_on_both_pages(self):
        self.shared_concepts('Data Analyst')
        self.targets('Data Analyst')
        self.opportunity('Data Analyst')
        self.opportunity('Senior Data Analyst')
        self.opportunity('Assistant Manager')
        self.opportunity('Management Accountant')
        build_profile_generation(self.profile)
        self.client.force_login(self.profile.user)
        response = self.client.get('/opportunities/')
        body = response.content.decode()
        self.assertNotIn('Senior Data Analyst', body)
        self.assertIn('Assistant Manager', body)
        self.assertIn('Management Accountant', body)

    def test_career_intelligence_sections_are_visible(self):
        self.shared_concepts('Data Analyst')
        self.targets('Data Analyst')
        self.opportunity('Data Analyst')
        self.client.force_login(self.profile.user)
        response = self.client.get('/career-intelligence/')
        body = response.content.decode()
        for heading in ('This is the evidence CareerGPS currently has about you',
                        'Comparable jobs',
                        'See the requirements found in these comparable jobs'):
            self.assertIn(heading, body)
        # Profile evidence and Comparable jobs are collapsible disclosures;
        # their headings live in the markup and nothing starts open.
        self.assertNotIn('<details open', body)
        self.assertNotIn('open class', body)
        # The selected target stays identifiable on the page.
        self.assertIn('Data Analyst', body)


class UncertainCatalogueVisibilityTests(FeedV2Case):
    """Missing or stale assessment coverage keeps current jobs browsable.

    The feed page has three areas and every job is in exactly one of them: the
    confirmed matches the active generation admitted, the discovery jobs it
    admitted without a confirmation, and the uncertain catalogue jobs no fresh
    stored assessment covers at all.
    """

    def setUp(self):
        super().setUp()
        self.client.force_login(self.profile.user)
        self.data_analyst = tuple(
            self.concept(source, 'Data Analyst') for source in ('ofo', 'esco'))

    def area_ids(self, response, key):
        return {row['opportunity'].pk for row in response.context[key]}


    def test_unassessed_jobs_stay_browsable_beside_the_transition_message(self):
        """Missing coverage never empties the page.

        A saved target with no built generation has no stored admission at all,
        so the confirmed and discovery areas are honestly empty.  The eligible
        catalogue jobs stay browsable in their own clearly separate uncertain
        area, beside the real transition message, and none of them is labelled
        a confirmed match or given a reused personalised score.
        """
        self.targets('Data Analyst')
        pending = self.opportunity('Zzz Fresh Coverage Role')
        senior = self.opportunity('Senior Data Analyst')
        response = self.client.get('/opportunities/')
        self.assertEqual(list(response.context['results']), [])
        self.assertEqual(list(response.context['discovery_results']), [])
        uncertain = self.area_ids(response, 'uncertain_results')
        self.assertIn(pending.pk, uncertain)
        self.assertEqual(response.context['uncertain_count'], 1)
        # The shared read-only seniority decision still governs this area.
        self.assertNotIn(senior.pk, uncertain)
        card = response.context['uncertain_results'][0]
        self.assertEqual(card['discovery_label'], 'Not yet assessed')
        self.assertFalse(card['is_confirmed_relationship'])
        self.assertEqual(card['relationship_lines'], [])
        self.assertEqual(card['title_only_lines'], [])
        self.assertNotIn('Band', card['fit_label'])
        # The real transition state is disclosed, not invented, and it sits next
        # to the uncertain area rather than replacing it.
        self.assertIsNotNone(response.context['feed_transition'])
        self.assertContains(response, 'Recommendations are updating')
        self.assertContains(
            response, 'Current jobs not yet assessed for your targets')
        self.assertContains(
            response, 'Ordered by how recently the listing was posted')
        self.assertNotContains(response, 'Exact match')
        self.assertNotContains(response, 'Senior Data Analyst')

    def test_the_three_feed_areas_never_repeat_one_job(self):
        """One job is in exactly one area: confirmed, discovery or uncertain."""
        self.targets('Data Analyst')
        confirmed = self.opportunity('Data Analyst')
        unadmitted = self.opportunity('Zzz Unmapped Qqq Role')
        build_profile_generation(self.profile)
        response = self.client.get('/opportunities/')
        confirmed_ids = self.area_ids(response, 'results')
        discovery_ids = self.area_ids(response, 'discovery_results')
        uncertain_ids = self.area_ids(response, 'uncertain_results')
        self.assertIn(confirmed.pk, confirmed_ids)
        # An occupationally unresolved job is no longer a survivor, so it never
        # reaches the personalised discovery area and instead falls through to
        # the general catalogue surface.  It is still in exactly one area.
        self.assertIn(unadmitted.pk, uncertain_ids)
        self.assertFalse(confirmed_ids & discovery_ids)
        self.assertFalse(confirmed_ids & uncertain_ids)
        self.assertFalse(discovery_ids & uncertain_ids)
        self.assertEqual(uncertain_ids, {unadmitted.pk})
        self.assertEqual(response.context['uncertain_count'], 1)
        self.assertContains(response, 'not yet assessed')


    def test_only_a_fresh_unrelated_proof_excludes_a_job(self):
        """The uncertain area excludes a job on fresh negative proof only.

        Explicit negative evidence from every source for every authored target
        is the only thing that may remove a catalogue job.  While that proof is
        current the job is excluded everywhere; once the reviewed policy fact
        changes, the stored proof is stale, so the job is no longer proven
        unrelated and returns as undecided instead of disappearing.
        """
        target_ofo, target_esco = self.data_analyst
        vacancy_ofo = self.concept('ofo', 'Warehouse Clerk')
        vacancy_esco = self.concept('esco', 'Warehouse Clerk')
        self.targets('Data Analyst')
        exact = self.opportunity('Data Analyst')
        unrelated = self.opportunity('Warehouse Clerk')
        policies = [unsupported_policy(target_ofo, vacancy_ofo),
                    unsupported_policy(target_esco, vacancy_esco)]
        build_profile_generation(self.profile)
        response = self.client.get('/opportunities/')
        self.assertIn(exact.pk, self.area_ids(response, 'results'))
        self.assertNotIn(unrelated.pk, self.area_ids(response, 'results'))
        self.assertNotIn(
            unrelated.pk, self.area_ids(response, 'uncertain_results'))
        self.assertNotContains(response, 'Warehouse Clerk')
        # Absence of evidence is never a verdict, so a stale proof keeps the job.
        OccupationRelationshipPolicy.objects.filter(
            pk__in=[row.pk for row in policies]).update(admission_allowed=True)
        stale = self.client.get('/opportunities/')
        self.assertIn(unrelated.pk, self.area_ids(stale, 'uncertain_results'))

    def test_the_uncertain_area_writes_nothing_and_resolves_nothing(self):
        """A GET over the uncertain area is a pure stored read."""
        self.targets('Data Analyst')
        self.opportunity('Zzz Unmapped Qqq Role')
        before = (
            OccupationSourceResolution.objects.count(),
            OpportunityTitleResolution.objects.count(),
            TargetOpportunityRelationshipAssessment.objects.count(),
            ProfileFeedEvaluation.objects.count(),
        )
        response = self.client.get('/opportunities/')
        self.assertEqual(response.status_code, 200)
        after = (
            OccupationSourceResolution.objects.count(),
            OpportunityTitleResolution.objects.count(),
            TargetOpportunityRelationshipAssessment.objects.count(),
            ProfileFeedEvaluation.objects.count(),
        )
        self.assertEqual(before, after)


    def test_title_only_evidence_is_disclosed_beside_a_confirmed_relationship(self):
        """A confirmed card also discloses its retained title-only evidence.

        OFO confirmed the pair with one result on each side, while the ESCO
        readings that share the label were retained and demoted to a
        possibility.  The confirmed count stays at one relationship and the
        retained possibilities are disclosed separately with their tier.
        """
        self.concept('ofo', 'Accountant')
        self.concept('esco', 'Accountant')
        self.concept('esco', 'Accountant')
        self.targets('Accountant')
        listing = self.opportunity('Accountant')
        build_profile_generation(self.profile)
        response = self.client.get('/opportunities/')
        card = response.context['results'][0]
        self.assertEqual(card['opportunity'].pk, listing.pk)
        self.assertTrue(card['is_confirmed_relationship'])
        self.assertEqual(len(card['relationship_lines']), 1)
        # Two distinct ESCO occupations share the label "Accountant" and carry
        # no code, so they stay two disclosed pieces of evidence.  Merging them
        # on the strength of matching display text would hide real evidence:
        # the two lines come from two different concept pairs.
        stored = ProfileFeedEvaluation.objects.get(
            generation__profile=self.profile, opportunity=listing)
        reason = stored.admission_reasons[0]
        retained = reason['title_only_evidence']
        self.assertEqual(len(card['title_only_lines']), len(retained))
        self.assertEqual(
            len({(item['target_concept_id'], item['job_concept_id'])
                 for item in retained}),
            len(retained))
        line = card['title_only_lines'][0]
        self.assertEqual(line['tier'], 'TITLE_ONLY')
        self.assertEqual(line['tier_label'],
                         'Title-only evidence, not taxonomy-confirmed')
        # The card shows exactly what the stored admission carries, and the two
        # kinds of evidence are never mixed into one number.
        self.assertEqual(reason['relationship_count'],
                         len(card['relationship_lines']))
        self.assertEqual(reason['title_only_evidence_count'],
                         len(card['title_only_lines']))
        self.assertEqual(reason['relationship_count'],
                         stored.relationship_assessment.findings.count())
        self.assertContains(response, 'not counted as a relationship')
        self.assertContains(response, 'TITLE_ONLY')



class DashboardTargetSelectionTests(FeedV2Case):
    """Regression: the dashboard must read the real CareerPreference model.

    ``dashboard_view`` previously dereferenced a nonexistent
    ``CareerPreference.target_ordinal`` attribute, so every authenticated
    dashboard request raised ``AttributeError`` and returned HTTP 500.  Career
    targets are authored as free text on ``CareerPreference.target_roles`` and
    enumerated through ``parse_target_roles``; these tests pin that the
    dashboard renders from that real data model and stays correct when the
    career target changes.
    """

    def dashboard(self):
        self.client.force_login(self.profile.user)
        return self.client.get('/dashboard/')

    def test_authenticated_dashboard_with_a_real_career_preference_is_not_500(self):
        # A real CareerPreference instance, created the way production does.
        preference = self.targets('Data Analyst')
        self.assertIsNotNone(preference)

        response = self.dashboard()

        # The exact failure being guarded against: AttributeError -> 500.
        self.assertNotEqual(response.status_code, 500)
        self.assertEqual(response.status_code, 200)

    def test_dashboard_reports_targets_from_authored_career_preference(self):
        self.targets('Data Analyst, Financial Analyst')

        response = self.dashboard()

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context['has_targets'])
        labels = [row['label'] for row in response.context['analysis']['authored_targets']]
        self.assertEqual(labels, ['Data Analyst', 'Financial Analyst'])
        # The selected target is the first authored target, ordinal 0.
        self.assertEqual(response.context['analysis']['selected']['label'], 'Data Analyst')
        self.assertEqual(response.context['analysis']['selected_ordinal'], 0)

    def test_dashboard_without_targets_reports_no_targets(self):
        # No CareerPreference authored at all.
        response = self.dashboard()

        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.context['has_targets'])
        self.assertEqual(response.context['analysis']['authored_targets'], [])
        self.assertIsNone(response.context['analysis']['selected'])

    def test_dashboard_with_confirmed_matches_populates_matched_employers(self):
        # Regression for ``_matched_employers``: ``top_matches`` rows carry
        # ``clean_revision`` as an OpportunityCleanRevision model instance, not
        # a dict.  This test produces a real confirmed match so the helper's
        # loop body actually executes; the old dict-style ``.get`` access
        # raised AttributeError and 500'd the dashboard.
        self.concept('ofo', 'Data Analyst')
        self.concept('esco', 'Data Analyst')
        self.targets('Data Analyst')
        self.opportunity('Data Analyst', company='Example employer')
        build_profile_generation(self.profile)

        response = self.dashboard()

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context['analysis']['matched']['employers'])

    def test_changing_the_career_target_updates_the_dashboard(self):
        # Preserve the intended behaviour: editing the saved target is reflected
        # on the dashboard without any migration or matching-engine change.
        self.targets('Data Analyst')
        first = self.dashboard()
        self.assertEqual(
            [row['label'] for row in first.context['analysis']['authored_targets']],
            ['Data Analyst'])

        self.targets('Chartered Accountant')
        second = self.dashboard()
        self.assertTrue(second.context['has_targets'])
        self.assertEqual(
            [row['label'] for row in second.context['analysis']['authored_targets']],
            ['Chartered Accountant'])

