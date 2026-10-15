"""M2 privacy/access, real matching, lifecycle and query-budget regression tests."""
from dataclasses import asdict
from datetime import date, timedelta
from decimal import Decimal
import json
from unittest import skip

from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import connection, IntegrityError, transaction
from django.http import Http404
from django.test import TestCase, Client, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from core.models import (Profile, ProfileSkill, Education, WorkExperience, Certification, Language,
    CareerPreference, CareerTargetIdentity, CareerTargetResolution, Notification,
    TaxonomyRelease, TaxonomyConcept, TaxonomyLabel, TaxonomyRelation)
from core.matching import build_user_matching_profile, build_opportunity_matching_input, employer_evidence_match
from core.test_support import activate_clean_opportunity
from core.services.professional_resolution import resolve_preference_targets
from . import tests as m1
from . import talent_services as services
from .discovery import (eligible_profiles, search_profiles, profile_batch, match_batch, project_profile, candidate_page)
from .permissions import discovery_membership
from .models import (CandidateVisibilityPreference, CandidateEmployerBlock, SavedCandidate,
    TalentPool, TalentPoolMember, TalentPoolMemberEvent, EmployerAuditEvent, OrganizationMembership,
    DiscoveryExperienceIndex, HiringApplication)


@override_settings(PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'])
class TalentTests(TestCase):
    draft = m1.EmployerTests.draft
    req = m1.EmployerTests.req
    published = m1.EmployerTests.published
    document = m1.EmployerTests.document
    application = m1.EmployerTests.application
    submitted = m1.EmployerTests.submitted
    member = m1.EmployerTests.member
    url = m1.EmployerTests.url

    def published(self, **kwargs):
        vacancy = m1.EmployerTests.published(self, **kwargs)
        if not vacancy.opportunity.active_clean_revision_id:
            activate_clean_opportunity(vacancy.opportunity)
            vacancy.opportunity.refresh_from_db()
        return vacancy

    def setUp(self):
        m1.EmployerTests.setUp(self)
        self.candidate.email = 'PRIVATE-EMAIL@example.test'
        self.candidate.save()
        self.profile.location = '123 PRIVATE-STREET'
        self.profile.save()
        self.access = discovery_membership(self.owner, self.org.pk)

    def enable(self, profile=None, **values):
        profile = profile or self.profile
        data = dict(discoverable_by_employers=True, allow_talent_pool_saving=True,
            allow_contact_reveal=False, allow_prior_applicant_rediscovery=False, discovery_location='Cape Town')
        data.update(values)
        return services.set_visibility(user=profile.user, data=data)

    def another_candidate(self, index, **values):
        user = get_user_model().objects.create_user(f'person{index}', first_name=f'Person {index}')
        profile = Profile.objects.create(user=user, location='PRIVATE-STREET')
        self.enable(profile, **values)
        return profile

    def search(self, **params):
        return self.client.get(self.url('talent_search', self.org.pk), params)

    def detail(self, **params):
        return self.client.get(self.url('talent_candidate', self.org.pk, self.profile.pk), params)

    def pool(self):
        return services.create_pool(user=self.owner, organization_id=self.org.pk, data={'name': 'Future analysts'})

    def save(self, **kwargs):
        return services.save_candidate(user=self.owner, organization_id=self.org.pk, candidate_id=self.profile.pk, **kwargs)

    def add(self, pool, **kwargs):
        return services.add_to_pool(user=self.owner, organization_id=self.org.pk, candidate_id=self.profile.pk,
            pool_id=pool.pk, **kwargs)

    def test_existing_candidates_and_missing_preferences_are_private(self):
        self.assertFalse(CandidateVisibilityPreference.objects.exists())
        self.assertEqual(self.search().context['page'].paginator.count, 0)
        self.assertEqual(self.detail().status_code, 404)
        preference = CandidateVisibilityPreference(profile=self.profile)
        self.assertFalse(preference.discoverable_by_employers)
        self.assertFalse(preference.allow_contact_reveal)
        self.assertFalse(preference.allow_talent_pool_saving)
        self.assertFalse(preference.allow_prior_applicant_rediscovery)
        self.assertTrue(preference.allow_verified_employers_only)

    def test_opt_in_and_opt_out_apply_immediately(self):
        self.enable()
        self.assertEqual(self.search().context['page'].paginator.count, 1)
        self.assertEqual(self.detail().status_code, 200)
        services.set_visibility(user=self.candidate, data={'discoverable_by_employers': False})
        self.assertEqual(self.search().context['page'].paginator.count, 0)
        self.assertEqual(self.detail().status_code, 404)

    def test_visibility_get_never_enables_or_creates_preferences(self):
        self.client.force_login(self.candidate)
        response = self.client.get(self.url('visibility'))
        self.assertEqual(response.status_code, 200)
        self.assertFalse(CandidateVisibilityPreference.objects.exists())
        self.assertNotContains(response, 'checked')

    def test_settings_post_is_owned_and_does_not_enable_other_controls(self):
        self.client.force_login(self.candidate)
        self.client.post(self.url('visibility'), {'discoverable_by_employers': 'on', 'profile_id': self.owner.pk})
        preference = CandidateVisibilityPreference.objects.get(profile=self.profile)
        self.assertTrue(preference.discoverable_by_employers)
        self.assertFalse(preference.allow_talent_pool_saving)
        self.assertFalse(preference.allow_contact_reveal)
        self.assertFalse(preference.allow_prior_applicant_rediscovery)

    def test_verification_policy_cannot_be_downgraded_by_candidate(self):
        self.enable(allow_verified_employers_only=False)
        self.org.verification_status = 'draft'
        self.org.save()
        self.assertEqual(self.search().status_code, 403)

    def test_unverified_and_suspended_organizations_denied(self):
        self.enable()
        self.org.verification_status = 'submitted'
        self.org.save()
        self.assertEqual(self.search().status_code, 403)
        self.org.verification_status = 'approved'
        self.org.account_status = 'suspended'
        self.org.save()
        self.assertEqual(self.search().status_code, 404)

    def test_only_owner_recruiter_hiring_manager_can_search(self):
        self.enable()
        for role, expected in [('recruiter', 200), ('hiring_manager', 200), ('interviewer', 403), ('viewer', 403)]:
            self.client.force_login(self.member(role))
            self.assertEqual(self.search().status_code, expected)

    def test_staff_without_organization_membership_has_no_bypass(self):
        self.other.is_staff = self.other.is_superuser = True
        self.other.save()
        self.client.force_login(self.other)
        self.assertEqual(self.search().status_code, 404)

    def test_disabled_and_invited_members_cannot_search(self):
        for role, state in [('viewer', 'disabled'), ('recruiter', 'invited')]:
            self.client.force_login(self.member(role, state))
            self.assertEqual(self.search().status_code, 404)

    def test_search_by_current_confirmed_desired_career(self):
        self.enable()
        release=TaxonomyRelease.objects.create(source='esco',version='talent-filter',is_active=True)
        occupation=TaxonomyConcept.objects.create(release=release,kind='occupation',external_id='occupation:analyst',
            preferred_label='Data Analyst',normalized_label='data analyst')
        TaxonomyLabel.objects.create(concept=occupation,label='Data Analyst',normalized_label='data analyst')
        preference = CareerPreference.objects.create(
            profile=self.profile, target_roles='Data Analyst', short_career_goal='PRIVATE-GOAL')
        from core.services.occupational_resolution import (
            maintain_target_resolution_containers, resolve_target_sources)
        targets = maintain_target_resolution_containers(preference)
        for target in targets:
            resolve_target_sources(target)
        resolution = CareerTargetResolution.objects.get(profile=self.profile)
        self.assertTrue(resolution.source_resolutions.filter(
            state='RESOLVED', selected_concept=occupation).exists())
        self.assertEqual(self.search(career='Data Analyst').context['page'].paginator.count, 1)
        self.assertEqual(self.search(career='analyst').context['page'].paginator.count, 0)
        self.assertEqual(self.search(career='doctor').context['page'].paginator.count, 0)
        CareerPreference.objects.filter(profile=self.profile).update(confirmed_by_user=False)
        self.assertEqual(self.search(career='Data Analyst').context['page'].paginator.count, 1)
        release.is_active=False;release.save(update_fields=['is_active'])
        self.assertEqual(self.search(career='Data Analyst').context['page'].paginator.count, 0)

    def test_unresolved_career_text_is_not_exact_taxonomy_membership(self):
        self.enable()
        preference = CareerPreference.objects.create(profile=self.profile, target_roles='Marketing')
        resolve_preference_targets(preference)
        self.assertTrue(CareerTargetResolution.objects.filter(
            profile=self.profile, state='UNRESOLVED').exists())
        self.assertFalse(CareerTargetIdentity.objects.filter(
            resolution__profile=self.profile).exists())
        self.assertEqual(self.search(career='Marketing').context['page'].paginator.count, 0)

    def test_search_skill_only_confirmed_structured_evidence(self):
        self.enable()
        ProfileSkill.objects.create(profile=self.profile, entered_name='Python', confirmed_by_user=False)
        self.profile.custom_skills = 'SQL'
        self.profile.save()
        self.assertEqual(self.search(skill='Python').context['page'].paginator.count, 0)
        self.assertEqual(self.search(skill='SQL').context['page'].paginator.count, 0)
        ProfileSkill.objects.filter(profile=self.profile).update(confirmed_by_user=True)
        self.assertEqual(self.search(skill='Python').context['page'].paginator.count, 1)

    def test_search_qualification_excludes_legacy_and_unconfirmed(self):
        self.enable()
        self.profile.qualification = 'Private legacy degree'
        self.profile.save()
        Education.objects.create(profile=self.profile, qualification='Diploma', confirmed_by_user=True)
        Education.objects.create(profile=self.profile, qualification='PhD', confirmed_by_user=False)
        self.assertEqual(self.search(qualification='Diploma').context['page'].paginator.count, 1)
        self.assertEqual(self.search(qualification='PhD').context['page'].paginator.count, 0)
        self.assertEqual(self.search(qualification='legacy').context['page'].paginator.count, 0)

    def test_search_location_uses_only_explicit_shared_city(self):
        self.enable()
        self.assertEqual(self.search(location='Cape Town').context['page'].paginator.count, 1)
        self.assertEqual(self.search(location='PRIVATE-STREET').context['page'].paginator.count, 0)
        self.assertNotContains(self.detail(), 'PRIVATE-STREET')

    def test_search_certification_confirmed_only(self):
        self.enable()
        Certification.objects.create(profile=self.profile, title='AWS', confirmed_by_user=False)
        self.assertEqual(self.search(certification='AWS').context['page'].paginator.count, 0)
        Certification.objects.filter(profile=self.profile).update(confirmed_by_user=True)
        self.assertEqual(self.search(certification='AWS').context['page'].paginator.count, 1)

    def test_experience_filter_preserves_overlap_semantics(self):
        self.enable()
        for title in ['Developer', 'Analyst']:
            WorkExperience.objects.create(profile=self.profile, title=title, start_date=date(2022, 1, 1), end_date=date(2023, 12, 31))
        self.assertEqual(self.search(min_experience='2').context['page'].paginator.count, 1)
        self.assertEqual(self.search(min_experience='3').context['page'].paginator.count, 0)
        self.assertEqual(DiscoveryExperienceIndex.objects.get(profile=self.profile).months, 24)

    def test_experience_filter_recalculates_edits_and_stale_date(self):
        self.enable()
        row = WorkExperience.objects.create(profile=self.profile, title='Analyst', start_date=date(2020, 1, 1), end_date=date(2024, 1, 1))
        self.assertEqual(self.search(min_experience='3').context['page'].paginator.count, 1)
        row.end_date = date(2020, 12, 1)
        row.save()
        self.assertEqual(self.search(min_experience='3').context['page'].paginator.count, 0)
        DiscoveryExperienceIndex.objects.filter(profile=self.profile).update(months=600, calculated_on=date(2020, 1, 1))
        self.assertEqual(self.search(min_experience='3').context['page'].paginator.count, 0)

    def test_unknown_unconfirmed_experience_does_not_pass_numeric_filter(self):
        self.enable()
        WorkExperience.objects.create(profile=self.profile, title='Analyst', start_date=date(2000, 1, 1), currently_working=True, confirmed_by_user=False)
        self.assertEqual(self.search(min_experience='0').context['page'].paginator.count, 0)

    def test_projection_is_allowlisted_not_full_profile(self):
        self.enable()
        CareerPreference.objects.create(profile=self.profile, target_roles='Analyst', short_career_goal='PRIVATE-GOAL')
        WorkExperience.objects.create(profile=self.profile, title='Analyst', organisation='Shared company', description='PRIVATE-WORK-NOTES')
        Education.objects.create(profile=self.profile, qualification='Diploma', institution='PRIVATE-INSTITUTION', description='PRIVATE-EDUCATION-NOTE')
        response = self.detail()
        card = response.context['card']
        payload = json.dumps(asdict(card), default=str)
        for private in ['PRIVATE-EMAIL', 'PRIVATE-PHONE', 'PRIVATE-STREET', 'PRIVATE-GOAL', 'PRIVATE-WORK-NOTES', 'PRIVATE-INSTITUTION', 'PRIVATE-EDUCATION-NOTE']:
            self.assertNotIn(private, payload)
            self.assertNotContains(response, private)
        self.assertFalse(hasattr(card, 'user'))
        self.assertFalse(hasattr(card, 'profile'))
        self.assertFalse(hasattr(self.search().context['page'].paginator.object_list, 'model'))

    def test_identity_fallback_never_exposes_username_or_email(self):
        self.enable()
        self.candidate.first_name = self.candidate.last_name = ''
        self.candidate.username = 'PRIVATE-USERNAME@example.test'
        self.candidate.save()
        response = self.detail()
        self.assertContains(response, f'Candidate {self.profile.pk}')
        self.assertNotContains(response, 'PRIVATE-USERNAME')

    def test_general_search_has_no_vacancy_match_score(self):
        self.enable()
        response = self.search()
        self.assertIsNone(response.context['page'].object_list[0].match_summary)
        self.assertNotContains(response, 'Evidence fit:')

    @skip('Retired legacy raw-vacancy employer matcher projection.')
    def test_vacancy_context_reuses_existing_matcher(self):
        self.enable()
        ProfileSkill.objects.create(profile=self.profile, entered_name='Python')
        vacancy = self.published()
        expected = employer_evidence_match(build_user_matching_profile(self.profile, include_certifications=True, include_languages=True),
            build_opportunity_matching_input(vacancy.opportunity))
        card = self.search(vacancy=vacancy.pk).context['page'].object_list[0]
        self.assertEqual(card.match_summary, expected.summary)
        self.assertEqual([row['status'] for row in card.criteria], [row.status.value for row in expected.criteria])

    @skip('Retired legacy raw-vacancy employer matcher projection.')
    def test_match_found_missing_unknown_and_preference_exclusion(self):
        self.enable()
        ProfileSkill.objects.create(profile=self.profile, entered_name='Python')
        CareerPreference.objects.create(profile=self.profile, target_roles='PRIVATE-TARGET', preferred_locations='PRIVATE-LOCATION', industries='PRIVATE-INDUSTRY')
        vacancy = self.published(requirements=[self.req('Python'), self.req('SQL'), self.req('Right to work', 'work_eligibility')])
        card = self.detail(vacancy=vacancy.pk).context['card']
        self.assertEqual({row['status'] for row in card.criteria}, {'FOUND', 'NOT_FOUND_ON_PROFILE', 'UNKNOWN'})
        self.assertFalse(any(row['importance'] == 'preference' for row in card.criteria))
        self.assertNotIn('PRIVATE-LOCATION', str(card.criteria))
        self.assertNotIn('PRIVATE-INDUSTRY', str(card.criteria))

    @skip('Retired legacy taxonomy relation matcher; approved clean multi-taxonomy routes replace it.')
    def test_trusted_related_status_and_no_raw_taxonomy_identifiers(self):
        self.enable()
        release = TaxonomyRelease.objects.create(source='esco', version='m2-test', is_active=True)
        concepts = []
        for i, label in enumerate(['Python', 'Programming']):
            concept = TaxonomyConcept.objects.create(release=release, kind='skill', external_id='esco:RAW-SECRET-' + str(i), preferred_label=label, normalized_label=label.lower())
            TaxonomyLabel.objects.create(concept=concept, label=label, normalized_label=label.lower(), label_type='preferred')
            concepts.append(concept)
        TaxonomyRelation.objects.create(subject=concepts[0], object=concepts[1], relation_type='broader')
        ProfileSkill.objects.create(profile=self.profile, entered_name='Python')
        vacancy = self.published(requirements=[self.req('Programming')])
        response = self.detail(vacancy=vacancy.pk)
        self.assertEqual(response.context['card'].criteria[0]['status'], 'RELATED')
        self.assertContains(response, 'Related evidence')
        self.assertNotContains(response, 'RAW-SECRET')

    @skip('Retired legacy employer ranking implementation.')
    def test_ranking_keeps_low_scores_and_uses_current_evidence(self):
        self.enable()
        stronger = self.another_candidate(2)
        skill = ProfileSkill.objects.create(profile=stronger, entered_name='Python')
        vacancy = self.published()
        page = self.search(vacancy=vacancy.pk).context['page']
        self.assertEqual(page.paginator.count, 2)
        self.assertEqual(page.object_list[0].id, stronger.pk)
        skill.delete()
        ProfileSkill.objects.create(profile=self.profile, entered_name='Python')
        page = self.search(vacancy=vacancy.pk).context['page']
        self.assertEqual(page.object_list[0].id, self.profile.pk)

    def test_foreign_draft_and_closed_vacancy_cannot_be_discovery_context(self):
        self.enable()
        draft = self.draft()
        self.assertEqual(self.detail(vacancy=draft.pk).status_code, 404)
        vacancy = self.published()
        self.client.force_login(self.other)
        self.foreign.verification_status = 'approved'
        self.foreign.save()
        self.assertEqual(self.client.get(self.url('talent_candidate', self.foreign.pk, self.profile.pk), {'vacancy': vacancy.pk}).status_code, 404)

    def test_save_duplicate_unsave_preserves_history_and_candidate_evidence(self):
        self.enable()
        before = Profile.objects.values().get(pk=self.profile.pk)
        saved = self.save()
        self.assertEqual(saved.pk, self.save().pk)
        self.assertEqual(SavedCandidate.objects.count(), 1)
        services.unsave_candidate(user=self.owner, organization_id=self.org.pk, saved_id=saved.pk)
        saved.refresh_from_db()
        self.assertFalse(saved.active)
        self.assertEqual(Profile.objects.values().get(pk=self.profile.pk), before)
        self.assertEqual(EmployerAuditEvent.objects.filter(event_type__in=['candidate_saved', 'candidate_unsaved']).count(), 2)

    def test_save_requires_saving_consent(self):
        self.enable(allow_talent_pool_saving=False)
        with self.assertRaises(Http404):
            self.save()
        self.assertFalse(SavedCandidate.objects.exists())

    def test_saved_uniqueness_is_database_enforced(self):
        self.enable()
        self.save()
        with self.assertRaises(IntegrityError), transaction.atomic():
            SavedCandidate.objects.create(organization=self.org, candidate=self.profile, saved_by=self.owner)

    def test_saved_candidates_cross_org_isolation(self):
        self.enable()
        saved = self.save()
        self.foreign.verification_status = 'approved'
        self.foreign.save()
        with self.assertRaises(Http404):
            services.unsave_candidate(user=self.other, organization_id=self.foreign.pk, saved_id=saved.pk)
        self.client.force_login(self.other)
        response = self.client.get(self.url('talent_saved', self.foreign.pk))
        self.assertEqual(response.context['page'].paginator.count, 0)

    def test_saved_filters(self):
        self.enable()
        self.another_candidate(2)
        self.save()
        self.assertEqual(self.search(saved='yes').context['page'].paginator.count, 1)
        self.assertEqual(self.search(saved='no').context['page'].paginator.count, 1)

    def test_pool_create_add_duplicate_remove_readd_retains_source_history(self):
        self.enable()
        pool = self.pool()
        row = self.add(pool)
        self.assertEqual(row.pk, self.add(pool).pk)
        self.assertEqual(row.history.count(), 1)
        services.remove_from_pool(user=self.owner, organization_id=self.org.pk, pool_id=pool.pk, member_id=row.pk)
        self.save()
        self.add(pool, source='saved_candidate')
        self.assertEqual(list(row.history.values_list('action', flat=True)), ['added', 'removed', 'added'])
        self.assertEqual(list(row.history.values_list('source', flat=True)), ['search', '', 'saved_candidate'])

    def test_pool_database_uniqueness(self):
        self.enable()
        pool = self.pool()
        self.add(pool)
        with self.assertRaises(IntegrityError), transaction.atomic():
            TalentPoolMember.objects.create(pool=pool, candidate=self.profile, added_by=self.owner, source='search')

    def test_candidate_can_join_multiple_pools(self):
        self.enable()
        self.add(self.pool())
        self.add(self.pool())
        self.assertEqual(TalentPoolMember.objects.count(), 2)

    def test_pool_archive_retains_membership_history_but_hides_discovery(self):
        self.enable()
        pool = self.pool()
        row = self.add(pool)
        services.archive_pool(user=self.owner, organization_id=self.org.pk, pool_id=pool.pk)
        self.assertEqual(row.history.count(), 1)
        self.assertEqual(self.client.get(self.url('talent_pool', self.org.pk, pool.pk)).context['page'].paginator.count, 0)
        with self.assertRaises(Http404):
            self.add(pool)

    def test_pool_cross_org_view_and_mutations_denied(self):
        self.enable()
        pool = self.pool()
        row = self.add(pool)
        self.foreign.verification_status = 'approved'
        self.foreign.save()
        self.client.force_login(self.other)
        self.assertEqual(self.client.get(self.url('talent_pool', self.foreign.pk, pool.pk)).status_code, 404)
        with self.assertRaises(Http404):
            services.add_to_pool(user=self.other, organization_id=self.foreign.pk, pool_id=pool.pk, candidate_id=self.profile.pk)
        with self.assertRaises(Http404):
            services.remove_from_pool(user=self.other, organization_id=self.foreign.pk, pool_id=pool.pk, member_id=row.pk)

    def test_saving_revocation_deactivates_pool_and_saved_entries(self):
        self.enable()
        saved = self.save()
        row = self.add(self.pool())
        services.set_visibility(user=self.candidate, data={'allow_talent_pool_saving': False})
        saved.refresh_from_db()
        row.refresh_from_db()
        self.assertFalse(saved.active)
        self.assertFalse(row.active)
        self.assertEqual(row.history.last().action, 'unavailable')
        self.assertEqual(self.client.get(self.url('talent_saved', self.org.pk)).context['page'].paginator.count, 0)

    def test_opt_out_dynamically_hides_saved_pool_counts_and_urls(self):
        self.enable()
        self.save()
        pool = self.pool()
        self.add(pool)
        # Even a direct ORM privacy update cannot leave public ghost entries.
        CandidateVisibilityPreference.objects.filter(profile=self.profile).update(discoverable_by_employers=False)
        self.assertEqual(self.detail().status_code, 404)
        self.assertEqual(self.client.get(self.url('talent_saved', self.org.pk)).context['page'].paginator.count, 0)
        response = self.client.get(self.url('talent_pools', self.org.pk))
        self.assertEqual(list(response.context['pools'])[0].visible_count, 0)
        self.assertEqual(self.client.get(self.url('talent_pool', self.org.pk, pool.pk)).context['page'].paginator.count, 0)

    def test_prior_applicant_rediscovery_requires_explicit_consent(self):
        self.submitted()
        self.assertEqual(self.search(scope='prior').context['page'].paginator.count, 0)
        self.enable(discoverable_by_employers=False, allow_prior_applicant_rediscovery=True)
        self.assertEqual(self.search(scope='public').context['page'].paginator.count, 0)
        self.assertEqual(self.search(scope='prior').context['page'].paginator.count, 1)

    def test_unrelated_employer_cannot_rediscover_private_prior_applicant(self):
        self.submitted()
        self.enable(discoverable_by_employers=False, allow_prior_applicant_rediscovery=True)
        self.foreign.verification_status = 'approved'
        self.foreign.save()
        self.client.force_login(self.other)
        response = self.client.get(self.url('talent_search', self.foreign.pk), {'scope': 'prior'})
        self.assertEqual(response.context['page'].paginator.count, 0)
        self.assertEqual(self.client.get(self.url('talent_candidate', self.foreign.pk, self.profile.pk)).status_code, 404)

    def test_prior_applicant_pool_entry_retains_source_context(self):
        hiring, _ = self.submitted()
        self.enable(discoverable_by_employers=False, allow_prior_applicant_rediscovery=True)
        vacancy = self.published(title='Future analyst')
        row = self.add(self.pool(), source='vacancy_rediscovery', vacancy_id=vacancy.pk, hiring_id=hiring.pk)
        self.assertEqual(row.source_application_id, hiring.pk)
        self.assertEqual(row.source_vacancy_id, vacancy.pk)
        self.assertEqual(row.history.get().source, 'vacancy_rediscovery')

    def test_pool_source_cannot_forge_prior_application_relationship(self):
        self.enable(allow_prior_applicant_rediscovery=True)
        with self.assertRaises(PermissionDenied):
            self.add(self.pool(), source='prior_applicant')
        with self.assertRaises(Http404):
            self.add(self.pool(), source='saved_candidate')

    @skip('Retired legacy employer ranking implementation.')
    def test_current_profile_recalculated_for_rediscovery(self):
        self.submitted()
        self.enable(discoverable_by_employers=False, allow_prior_applicant_rediscovery=True)
        vacancy = self.published()
        first = self.search(scope='prior', vacancy=vacancy.pk).context['page'].object_list[0]
        ProfileSkill.objects.create(profile=self.profile, entered_name='Python')
        second = self.search(scope='prior', vacancy=vacancy.pk).context['page'].object_list[0]
        self.assertGreater(second.match_summary.score, first.match_summary.score)
        self.assertTrue(EmployerAuditEvent.objects.filter(event_type='vacancy_rediscovery').exists())

    def test_contact_hidden_in_cards_and_detail_even_with_consent(self):
        self.enable(allow_contact_reveal=True)
        vacancy = self.published()
        for response in [self.search(vacancy=vacancy.pk), self.detail(vacancy=vacancy.pk)]:
            self.assertNotContains(response, self.candidate.email)
            self.assertNotContains(response, self.profile.phone)
        self.assertContains(self.detail(vacancy=vacancy.pk), 'Reveal contact')

    def test_contact_post_reveals_only_allowed_values_and_audits_no_contact_values(self):
        self.enable(allow_contact_reveal=True)
        vacancy = self.published()
        response = self.client.post(self.url('talent_contact', self.org.pk, self.profile.pk),
            {'vacancy_id': vacancy.pk, 'reason': 'vacancy_review'})
        self.assertContains(response, self.candidate.email)
        self.assertContains(response, self.profile.phone)
        self.assertNotContains(response, self.profile.location)
        self.assertEqual(response['Cache-Control'], 'private, no-store')
        event = EmployerAuditEvent.objects.get(event_type='candidate_contact_revealed')
        self.assertEqual(event.actor, self.owner)
        self.assertEqual(event.context['candidate_id'], self.profile.pk)
        self.assertEqual(event.context['vacancy_id'], vacancy.pk)
        self.assertNotIn(self.candidate.email, str(event.context))
        self.assertNotIn(self.profile.phone, str(event.context))

    def test_contact_requires_explicit_purpose_vacancy_and_consent(self):
        self.enable()
        vacancy = self.published()
        with self.assertRaises(Http404):
            services.reveal_contact(user=self.owner, organization_id=self.org.pk, candidate_id=self.profile.pk, vacancy_id=vacancy.pk, reason='vacancy_review')
        self.enable(allow_contact_reveal=True)
        with self.assertRaises(ValidationError):
            services.reveal_contact(user=self.owner, organization_id=self.org.pk, candidate_id=self.profile.pk, reason='vacancy_review')

    def test_contact_authorized_roles_only(self):
        self.enable(allow_contact_reveal=True)
        vacancy = self.published()
        for role in ['hiring_manager', 'interviewer', 'viewer']:
            with self.assertRaises(PermissionDenied):
                services.reveal_contact(user=self.member(role), organization_id=self.org.pk, candidate_id=self.profile.pk, vacancy_id=vacancy.pk, reason='vacancy_review')
        result = services.reveal_contact(user=self.member('recruiter'), organization_id=self.org.pk, candidate_id=self.profile.pk, vacancy_id=vacancy.pk, reason='vacancy_review')
        self.assertEqual(result['email'], self.candidate.email)

    def test_contact_reveal_get_and_csrf_rejected(self):
        self.enable(allow_contact_reveal=True)
        vacancy = self.published()
        url = self.url('talent_contact', self.org.pk, self.profile.pk)
        self.assertEqual(self.client.get(url).status_code, 405)
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.owner)
        self.assertEqual(client.post(url, {'reason': 'vacancy_review', 'vacancy_id': vacancy.pk}).status_code, 403)

    def test_foreign_vacancy_cannot_authorize_contact(self):
        self.enable(allow_contact_reveal=True)
        vacancy = self.published()
        self.foreign.verification_status = 'approved'
        self.foreign.save()
        with self.assertRaises(Http404):
            services.reveal_contact(user=self.other, organization_id=self.foreign.pk, candidate_id=self.profile.pk, vacancy_id=vacancy.pk, reason='vacancy_review')

    def test_current_application_contact_exception_is_separate_from_discovery(self):
        hiring, _ = self.submitted()
        self.assertEqual(self.detail().status_code, 404)
        result = services.reveal_contact(user=self.owner, organization_id=self.org.pk, candidate_id=self.profile.pk,
            hiring_id=hiring.pk, reason='application_followup')
        self.assertEqual(result['email'], self.candidate.email)
        self.assertEqual(self.detail().status_code, 404)

    def test_withdrawn_and_rejected_applications_cannot_authorize_contact(self):
        from .services import withdraw_application, move_stage
        hiring, _ = self.submitted()
        move_stage(user=self.owner, organization_id=self.org.pk, hiring_id=hiring.pk, stage='rejected')
        with self.assertRaises(PermissionDenied):
            services.reveal_contact(user=self.owner, organization_id=self.org.pk, candidate_id=self.profile.pk, hiring_id=hiring.pk, reason='application_followup')
        with self.assertRaises(ValidationError):
            withdraw_application(user=self.candidate, application_id=hiring.application_id)
        withdrawn, _ = self.submitted()
        withdraw_application(user=self.candidate, application_id=withdrawn.application_id)
        with self.assertRaises(Http404):
            services.reveal_contact(user=self.owner, organization_id=self.org.pk, candidate_id=self.profile.pk, hiring_id=withdrawn.pk, reason='application_followup')

    def test_block_hides_discovery_save_pool_and_contact_without_explicit_reason(self):
        self.enable(allow_contact_reveal=True)
        vacancy = self.published()
        saved = self.save()
        row = self.add(self.pool())
        services.block_organization(user=self.candidate, organization_id=self.org.pk)
        self.assertEqual(self.search().context['page'].paginator.count, 0)
        self.assertEqual(self.detail().status_code, 404)
        saved.refresh_from_db()
        row.refresh_from_db()
        self.assertFalse(saved.active)
        self.assertFalse(row.active)
        with self.assertRaises(Http404):
            self.save()
        with self.assertRaises(Http404):
            services.reveal_contact(user=self.owner, organization_id=self.org.pk, candidate_id=self.profile.pk, vacancy_id=vacancy.pk, reason='vacancy_review')
        self.assertFalse(EmployerAuditEvent.objects.filter(event_type__icontains='block').exists())

    def test_block_beats_application_contact_exception_but_m1_application_stays(self):
        hiring, _ = self.submitted()
        services.block_organization(user=self.candidate, organization_id=self.org.pk)
        self.assertEqual(self.client.get(self.url('applicant', self.org.pk, hiring.pk)).status_code, 200)
        with self.assertRaises(Http404):
            services.reveal_contact(user=self.owner, organization_id=self.org.pk, candidate_id=self.profile.pk, hiring_id=hiring.pk, reason='application_followup')

    def test_unblock_restores_eligibility_without_reactivating_saved_state(self):
        self.enable()
        saved = self.save()
        services.block_organization(user=self.candidate, organization_id=self.org.pk)
        services.block_organization(user=self.candidate, organization_id=self.org.pk, blocked=False)
        self.assertEqual(self.detail().status_code, 200)
        saved.refresh_from_db()
        self.assertFalse(saved.active)

    def test_blocks_are_candidate_owned_unique_and_csrf_protected(self):
        self.client.force_login(self.candidate)
        url = self.url('visibility_block', self.org.pk)
        self.client.post(url, {'profile_id': 999})
        self.client.post(url)
        self.assertEqual(CandidateEmployerBlock.objects.get().profile_id, self.profile.pk)
        self.assertEqual(self.client.get(url).status_code, 405)
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.candidate)
        self.assertEqual(client.post(self.url('visibility_unblock', self.org.pk)).status_code, 403)

    def test_opt_out_never_breaks_m1_documents_and_applicant_views(self):
        hiring, _ = self.submitted()
        self.enable()
        services.set_visibility(user=self.candidate, data={'discoverable_by_employers': False})
        self.assertEqual(self.detail().status_code, 404)
        response = self.client.get(self.url('applicant', self.org.pk, hiring.pk))
        self.assertContains(response, 'Download PDF')
        document = hiring.submitted_documents.get()
        self.assertEqual(self.client.get(self.url('document_download', self.org.pk, document.pk, 'pdf')).status_code, 200)

    def test_account_deactivation_removes_all_discovery(self):
        self.enable()
        self.save()
        pool = self.pool()
        self.add(pool)
        self.candidate.is_active = False
        self.candidate.save()
        self.assertEqual(self.search().context['page'].paginator.count, 0)
        self.assertEqual(self.detail().status_code, 404)
        self.assertEqual(self.client.get(self.url('talent_pool', self.org.pk, pool.pk)).context['page'].paginator.count, 0)

    def test_candidate_deletion_keeps_minimal_saved_pool_history_no_ghost(self):
        self.enable()
        saved = self.save()
        row = self.add(self.pool())
        self.candidate.delete()
        saved.refresh_from_db()
        row.refresh_from_db()
        self.assertIsNone(saved.candidate_id)
        self.assertIsNone(row.candidate_id)
        self.assertEqual(row.history.count(), 1)
        self.assertEqual(self.search().context['page'].paginator.count, 0)

    def test_profile_views_audited_search_results_do_not_spam_audit_or_notifications(self):
        self.enable()
        count = EmployerAuditEvent.objects.count()
        self.search()
        self.assertEqual(EmployerAuditEvent.objects.count(), count)
        self.detail()
        self.assertEqual(EmployerAuditEvent.objects.filter(event_type='candidate_discovery_viewed').count(), 1)
        self.save()
        self.add(self.pool())
        self.assertFalse(Notification.objects.exists())

    def test_no_other_organization_notes_applications_or_documents_in_projection(self):
        from .services import add_note
        self.enable()
        hiring, _ = self.submitted()
        add_note(user=self.owner, organization_id=self.org.pk, hiring_id=hiring.pk, content='PRIVATE-EMPLOYER-NOTE')
        response = self.detail()
        for value in ['PRIVATE-EMPLOYER-NOTE', 'CANDIDATE-PRIVATE-NOTE', 'generated_documents', 'Download PDF', 'interview_practice']:
            self.assertNotContains(response, value)

    def test_search_page_query_count_is_bounded_not_per_candidate(self):
        self.enable()
        with CaptureQueriesContext(connection) as one:
            self.search()
        for i in range(19):
            profile = self.another_candidate(i + 10)
            ProfileSkill.objects.create(profile=profile, entered_name='Python')
        with CaptureQueriesContext(connection) as twenty:
            response = self.search()
        self.assertEqual(len(response.context['page'].object_list), 20)
        self.assertLessEqual(len(twenty), len(one) + 2, [row['sql'] for row in twenty])
        # Employer shell now adds the same two fixed bell queries as the candidate shell.
        # One fixed query loads trusted independent target resolutions.
        self.assertLessEqual(len(twenty), 22)

    def test_ranked_search_batches_evidence_without_per_candidate_queries(self):
        self.enable()
        vacancy = self.published()
        with CaptureQueriesContext(connection) as one:
            self.search(vacancy=vacancy.pk)
        for i in range(12):
            self.another_candidate(i + 10)
        with CaptureQueriesContext(connection) as many:
            self.search(vacancy=vacancy.pk)
        self.assertLessEqual(len(many), len(one) + 2)
        self.assertLessEqual(len(many), 38)

    @skip('Retired legacy employer ranking implementation.')
    def test_pagination_counts_only_eligible_and_does_not_drop_low_scores(self):
        self.enable()
        for i in range(22):
            self.another_candidate(i + 10, discoverable_by_employers=i < 20)
        vacancy = self.published()
        first = self.search(vacancy=vacancy.pk).context['page']
        second = self.search(vacancy=vacancy.pk, page=2).context['page']
        self.assertEqual(first.paginator.count, 21)
        self.assertEqual(len(first.object_list), 20)
        self.assertEqual(len(second.object_list), 1)
        self.assertFalse({card.id for card in first} & {card.id for card in second})

    def test_all_talent_mutations_reject_get(self):
        self.enable()
        saved = self.save()
        pool = self.pool()
        row = self.add(pool)
        for name, args in [('talent_save', [self.org.pk, self.profile.pk]), ('talent_unsave', [self.org.pk, saved.pk]),
                ('talent_pool_add', [self.org.pk, self.profile.pk]), ('talent_pool_remove', [self.org.pk, pool.pk, row.pk]),
                ('talent_pool_archive', [self.org.pk, pool.pk])]:
            self.assertEqual(self.client.get(self.url(name, *args)).status_code, 405)

    def test_talent_pages_render_and_use_private_cache_policy(self):
        self.enable()
        pool = self.pool()
        self.add(pool)
        self.save()
        for name, args in [('talent_search', [self.org.pk]), ('talent_candidate', [self.org.pk, self.profile.pk]),
                ('talent_saved', [self.org.pk]), ('talent_pools', [self.org.pk]), ('talent_pool', [self.org.pk, pool.pk])]:
            response = self.client.get(self.url(name, *args))
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response['Cache-Control'], 'private, no-store')

    def test_http_m2_vertical_slice(self):
        vacancy = self.published()
        self.client.force_login(self.candidate)
        self.client.post(self.url('visibility'), {'discoverable_by_employers': 'on', 'allow_talent_pool_saving': 'on',
            'allow_contact_reveal': 'on', 'discovery_location': 'Cape Town'})
        ProfileSkill.objects.create(profile=self.profile, entered_name='Python')
        self.client.force_login(self.owner)
        self.assertContains(self.client.get(self.url('job_detail', self.org.pk, vacancy.pk)), 'Discover relevant Talent')
        self.assertContains(self.search(vacancy=vacancy.pk), 'Casey Applicant')
        self.client.post(self.url('talent_pools', self.org.pk), {'name': 'Analysts'})
        pool = TalentPool.objects.get()
        self.assertEqual(self.client.post(self.url('talent_save', self.org.pk, self.profile.pk), {'vacancy_id': vacancy.pk}).status_code, 302)
        self.assertEqual(self.client.post(self.url('talent_pool_add', self.org.pk, self.profile.pk),
            {'pool_id': pool.pk, 'source': 'saved_candidate', 'vacancy_id': vacancy.pk}).status_code, 302)
        self.assertContains(self.client.get(self.url('talent_pool', self.org.pk, pool.pk)), 'Casey Applicant')
        self.assertContains(self.client.post(self.url('talent_contact', self.org.pk, self.profile.pk),
            {'vacancy_id': vacancy.pk, 'reason': 'vacancy_review'}), self.candidate.email)
        self.client.force_login(self.candidate)
        self.client.post(self.url('visibility_block', self.org.pk))
        self.client.force_login(self.owner)
        self.assertEqual(self.search().context['page'].paginator.count, 0)
        self.assertEqual(self.detail().status_code, 404)
        self.assertEqual(self.client.get(self.url('talent_pool', self.org.pk, pool.pk)).context['page'].paginator.count, 0)

    @skip('Retired legacy employer ranking implementation.')
    def test_global_ranking_considers_candidates_beyond_first_batch(self):
        self.enable()
        users = get_user_model().objects.bulk_create([get_user_model()(username=f'batch{i}', first_name=f'Candidate {i}') for i in range(105)])
        profiles = Profile.objects.bulk_create([Profile(user=user) for user in users])
        CandidateVisibilityPreference.objects.bulk_create([CandidateVisibilityPreference(profile=profile, discoverable_by_employers=True) for profile in profiles])
        strongest = profiles[-1]
        ProfileSkill.objects.create(profile=strongest, entered_name='Python')
        vacancy = self.published()
        page = self.search(vacancy=vacancy.pk).context['page']
        self.assertEqual(page.paginator.count, 106)
        self.assertEqual(page.object_list[0].id, strongest.pk)
        last = self.search(vacancy=vacancy.pk, page=6).context['page']
        self.assertEqual(len(last.object_list), 6)

    def test_each_talent_mutation_requires_csrf(self):
        self.enable()
        saved = self.save()
        pool = self.pool()
        row = self.add(pool)
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.owner)
        for name, args in [('talent_save', [self.org.pk, self.profile.pk]), ('talent_unsave', [self.org.pk, saved.pk]),
                ('talent_pools', [self.org.pk]), ('talent_pool_add', [self.org.pk, self.profile.pk]),
                ('talent_pool_remove', [self.org.pk, pool.pk, row.pk]), ('talent_pool_archive', [self.org.pk, pool.pk])]:
            self.assertEqual(client.post(self.url(name, *args), {}).status_code, 403)
        client.force_login(self.candidate)
        self.assertEqual(client.post(self.url('visibility'), {}).status_code, 403)

    def test_contact_cannot_use_another_candidates_application(self):
        hiring, _ = self.submitted()
        unrelated = self.another_candidate(44)
        with self.assertRaises(Http404):
            services.reveal_contact(user=self.owner, organization_id=self.org.pk, candidate_id=unrelated.pk,
                hiring_id=hiring.pk, reason='application_followup')
        self.assertFalse(EmployerAuditEvent.objects.filter(event_type='candidate_contact_revealed').exists())

    def test_blocked_prior_applicant_not_rediscoverable(self):
        self.submitted()
        self.enable(discoverable_by_employers=False, allow_prior_applicant_rediscovery=True)
        services.block_organization(user=self.candidate, organization_id=self.org.pk)
        self.assertEqual(self.search(scope='prior').context['page'].paginator.count, 0)

    def test_projection_never_includes_unconfirmed_credentials_or_work(self):
        self.enable()
        Education.objects.create(profile=self.profile, qualification='UNCONFIRMED-EDUCATION', confirmed_by_user=False)
        Certification.objects.create(profile=self.profile, title='UNCONFIRMED-CERTIFICATE', confirmed_by_user=False)
        WorkExperience.objects.create(profile=self.profile, title='UNCONFIRMED-WORK', confirmed_by_user=False)
        Language.objects.create(profile=self.profile, language='UNCONFIRMED-LANGUAGE', confirmed_by_user=False)
        self.assertNotContains(self.detail(), 'UNCONFIRMED')

    def test_saved_to_pool_navigation_preserves_source(self):
        self.enable()
        self.save()
        self.pool()
        response = self.client.get(self.url('talent_saved', self.org.pk))
        self.assertContains(response, '?source=saved_candidate')
        self.assertEqual(self.detail(source='saved_candidate').context['source'], 'saved_candidate')

    def test_application_contact_action_is_available_without_discovery(self):
        hiring, _ = self.submitted()
        response = self.client.get(self.url('applicant', self.org.pk, hiring.pk))
        self.assertContains(response, 'Reveal application contact')
        self.assertEqual(self.detail().status_code, 404)

    def test_hiring_manager_cannot_change_visibility_or_contact_but_can_save_pool(self):
        self.enable()
        manager = self.member('hiring_manager')
        saved = services.save_candidate(user=manager, organization_id=self.org.pk, candidate_id=self.profile.pk)
        pool = services.create_pool(user=manager, organization_id=self.org.pk, data={'name': 'Manager pool'})
        services.add_to_pool(user=manager, organization_id=self.org.pk, pool_id=pool.pk, candidate_id=self.profile.pk)
        self.assertTrue(saved.active)
        with self.assertRaises(Http404):
            services.set_visibility(user=manager, data={'discoverable_by_employers': False})
        self.assertTrue(CandidateVisibilityPreference.objects.get(profile=self.profile).discoverable_by_employers)
