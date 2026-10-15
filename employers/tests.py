"""Employer boundary, publication, submission and hiring regression tests."""
from datetime import timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from django.contrib import admin
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.files.base import ContentFile
from django.db import IntegrityError, transaction
from django.db.models.deletion import ProtectedError
from django.http import Http404
from django.test import Client, RequestFactory, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from applications.models import Application, GeneratedDocument
from applications.services.job_context import resolve_job_context
from core.matching import build_opportunity_matching_input, match_profile_to_opportunity, MatchStatus, CriterionImportance
from core.models import Opportunity, Profile, ProfileSkill, Education, Notification, NotificationPreference, SavedOpportunity
from core.services.requirement_projection import requirement_projection
from core.test_support import activate_clean_opportunity, create_current_evaluation
from unittest import skip
from ingestion.models import OpportunityExtraction, OpportunitySourceRecord, RawOpportunity
from ingestion.ai.service import process_opportunity_batch
from . import services
from .models import (Organization, OrganizationMembership, EmployerVacancy, VacancyRequirement,
    HiringApplication, SubmittedDocument, ApplicationStageEvent, EmployerAuditEvent, EmployerApplicationNote)
from .permissions import membership_for


@override_settings(PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'])
class EmployerTests(TestCase):
    def setUp(self):
        self.media = TemporaryDirectory()
        self.addCleanup(self.media.cleanup)
        media_settings = override_settings(MEDIA_ROOT=self.media.name)
        media_settings.enable()
        self.addCleanup(media_settings.disable)
        self.owner = get_user_model().objects.create_user('owner', password='test')
        self.other = get_user_model().objects.create_user('other', password='test')
        self.candidate = get_user_model().objects.create_user('candidate', password='test', first_name='Casey', last_name='Applicant')
        self.profile = Profile.objects.create(user=self.candidate, location='Cape Town', phone='PRIVATE-PHONE')
        self.org = services.create_organization(user=self.owner, data={'name': 'Example Hiring', 'industry': 'Technology'})
        self.org.verification_status = 'approved'
        self.org.save()
        self.foreign = services.create_organization(user=self.other, data={'name': 'Other Employer'})
        self.foreign.verification_status = 'approved'
        self.foreign.save()
        self.client.force_login(self.owner)

    def test_employer_shell_shows_owned_unread_bell_and_settings_engine(self):
        for number in range(6):
            Notification.objects.create(user=self.owner, title=f'Your task {number}', event_key=f'shell:{number}')
        Notification.objects.create(user=self.other, title='Foreign private task', event_key='shell:foreign')
        response = self.client.get(reverse('employers:dashboard', args=[self.org.pk]))
        self.assertContains(response, 'Notifications: 6 unread')
        self.assertContains(response, 'Your task 5')
        self.assertNotContains(response, 'Your task 0')
        self.assertNotContains(response, 'Foreign private task')
        self.assertContains(response, 'core/js/settings.js', count=1)
        self.assertContains(response, 'core/css/setfunctions.css', count=1)
        self.assertContains(response, 'core/css/notifications.css', count=1)
        self.assertContains(response, 'core/js/notifications.js', count=1)

    def draft(self, requirements=None, **data):
        values = dict(title='Data Analyst', description='Analyze reports with the team.', location='Cape Town',
            career_stage='early_career', opportunity_kind='job', no_candidate_fees=True)
        values.update(data)
        return services.save_vacancy(user=self.owner, organization_id=self.org.pk, data=values,
            requirements=requirements if requirements is not None else [self.req('Python')])

    def req(self, value, kind='skill', level='required', **extra):
        return dict(raw_value=value, criterion_type=kind, requirement_level=level, **extra)

    def published(self, **kwargs):
        vacancy = self.draft(**kwargs)
        return services.publish_vacancy(user=self.owner, organization_id=self.org.pk, vacancy_id=vacancy.pk)

    def document(self, application, kind='cv', data=b'%PDF-1.4 original submitted CV'):
        doc = GeneratedDocument.objects.create(user=application.user, application=application,
            opportunity=application.opportunity, clean_revision=application.clean_revision,
            document_type=kind)
        doc.pdf_file.save(f'{doc.pk}.pdf', ContentFile(data))
        return doc

    def application(self, vacancy=None):
        vacancy = vacancy or self.published()
        if not vacancy.opportunity.active_clean_revision_id:
            activate_clean_opportunity(vacancy.opportunity)
            vacancy.opportunity.refresh_from_db()
        application = Application.objects.create(
            user=self.candidate, opportunity=vacancy.opportunity,
            clean_revision=vacancy.opportunity.active_clean_revision,
            notes='CANDIDATE-PRIVATE-NOTE')
        return application, self.document(application)

    def submitted(self, vacancy=None):
        application, doc = self.application(vacancy)
        hiring = services.submit_application(user=self.candidate, application_id=application.pk, document_ids=[doc.pk], consent=True)
        return hiring, doc

    def member(self, role, status='active'):
        user = get_user_model().objects.create_user(role, password='test')
        OrganizationMembership.objects.create(organization=self.org, user=user, role=role, status=status)
        return user

    def url(self, name, *args):
        return reverse('employers:' + name, args=args)

    def test_creation_establishes_unverified_owner_and_audits(self):
        org = services.create_organization(user=self.candidate, data={'name': 'New Org', 'verification_status': 'approved'})
        self.assertEqual(org.verification_status, 'draft')
        self.assertEqual(org.memberships.get().role, 'owner')
        self.assertEqual(org.memberships.get().status, 'active')
        self.assertEqual(org.audit_events.count(), 2)

    def test_membership_database_uniqueness(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            OrganizationMembership.objects.create(user=self.owner, organization=self.org, role='viewer')

    def test_last_active_owner_cannot_be_removed(self):
        with self.assertRaises(ValidationError):
            services.change_membership(user=self.owner, organization_id=self.org.pk, username=self.owner.username, role='viewer', status='disabled')
        self.assertEqual(self.org.memberships.get().role, 'owner')

    def test_invitation_requires_target_acceptance(self):
        member = services.change_membership(user=self.owner, organization_id=self.org.pk, username=self.candidate.username, role='recruiter')
        with self.assertRaises(Http404):
            membership_for(self.candidate, self.org.pk)
        with self.assertRaises(Http404):
            services.accept_invitation(user=self.other, membership_id=member.pk)
        services.accept_invitation(user=self.candidate, membership_id=member.pk)
        self.assertEqual(membership_for(self.candidate, self.org.pk).role, 'recruiter')

    def test_owner_cannot_force_new_member_active(self):
        with self.assertRaises(ValidationError):
            services.change_membership(user=self.owner, organization_id=self.org.pk, username=self.candidate.username, role='owner', status='active')

    def test_disabled_and_invited_members_denied(self):
        for role, status in [('viewer', 'disabled'), ('recruiter', 'invited')]:
            with self.subTest(status=status), self.assertRaises(Http404):
                membership_for(self.member(role, status), self.org.pk)

    def test_all_five_role_capabilities(self):
        expected = {
            'owner': {'view', 'vacancy', 'pipeline', 'notes', 'documents', 'team', 'settings'},
            'recruiter': {'view', 'vacancy', 'pipeline', 'notes', 'documents'},
            'hiring_manager': {'view', 'pipeline', 'notes', 'documents'},
            'interviewer': {'view', 'notes', 'documents'}, 'viewer': {'view'}}
        for role, allowed in expected.items():
            user = self.owner if role == 'owner' else self.member(role)
            for capability in expected['owner']:
                with self.subTest(role=role, capability=capability):
                    if capability in allowed:
                        self.assertEqual(membership_for(user, self.org.pk, capability).role, role)
                    else:
                        with self.assertRaises(PermissionDenied):
                            membership_for(user, self.org.pk, capability)

    def test_platform_staff_has_no_employer_bypass(self):
        self.other.is_staff = self.other.is_superuser = True
        self.other.save()
        self.client.force_login(self.other)
        self.assertEqual(self.client.get(self.url('dashboard', self.org.pk)).status_code, 404)

    def test_suspension_blocks_discovery_access_and_mutations(self):
        vacancy = self.published()
        self.org.account_status = 'suspended'
        self.org.save()
        self.assertFalse(Opportunity.objects.discoverable().filter(pk=vacancy.opportunity_id).exists())
        self.assertContains(self.client.get(self.url('dashboard', self.org.pk)), 'is suspended')
        with self.assertRaises(Http404):
            services.close_vacancy(user=self.owner, organization_id=self.org.pk, vacancy_id=vacancy.pk)

    def test_revoked_verification_hides_direct_not_external(self):
        vacancy = self.published()
        external = Opportunity.objects.create(title='External', company='External', opportunity_type='internship', location='Cape Town')
        self.org.verification_status = 'submitted'
        self.org.save()
        ids = list(Opportunity.objects.discoverable().values_list('pk', flat=True))
        self.assertNotIn(vacancy.opportunity_id, ids)
        self.assertIn(external.pk, ids)

    def test_draft_has_no_canonical_opportunity(self):
        vacancy = self.draft()
        self.assertEqual(vacancy.status, 'draft')
        self.assertIsNone(vacancy.opportunity_id)
        self.assertFalse(Opportunity.objects.exists())

    def test_unverified_draft_allowed_publish_denied(self):
        self.org.verification_status = 'draft'
        self.org.save()
        vacancy = self.draft()
        with self.assertRaises(ValidationError):
            services.publish_vacancy(user=self.owner, organization_id=self.org.pk, vacancy_id=vacancy.pk)
        self.assertFalse(Opportunity.objects.exists())

    def test_pending_organization_saves_draft_to_permitted_edit_page(self):
        self.org.verification_status = 'submitted'
        self.org.save(update_fields=['verification_status'])
        data = {
            'no_candidate_fees': 'on',
            'title': 'Pending Draft Analyst',
            'location': 'Cape Town',
            'description': 'Analyze reports.',
            'country': 'South Africa',
            'opportunity_kind': 'job',
            'career_stage': 'early_career',
            'requirements-TOTAL_FORMS': '0',
            'requirements-INITIAL_FORMS': '0',
        }

        response = self.client.post(self.url('vacancy_create', self.org.pk), data)

        vacancy = self.org.vacancies.get(title='Pending Draft Analyst')
        self.assertRedirects(
            response,
            self.url('vacancy_edit', self.org.pk, vacancy.pk),
        )
        self.assertEqual(vacancy.status, 'draft')
        self.assertIsNone(vacancy.opportunity_id)
        self.assertEqual(
            self.client.post(self.url('vacancy_publish', self.org.pk, vacancy.pk)).status_code,
            302,
        )
        vacancy.refresh_from_db()
        self.assertEqual(vacancy.status, 'draft')
        self.assertIsNone(vacancy.opportunity_id)

    def test_approved_organization_draft_redirect_is_unchanged(self):
        data = {
            'no_candidate_fees': 'on',
            'title': 'Approved Draft Analyst',
            'location': 'Cape Town',
            'description': 'Analyze reports.',
            'country': 'South Africa',
            'opportunity_kind': 'job',
            'career_stage': 'early_career',
            'requirements-TOTAL_FORMS': '0',
            'requirements-INITIAL_FORMS': '0',
        }

        response = self.client.post(self.url('vacancy_create', self.org.pk), data)

        self.assertRedirects(response, self.url('vacancies', self.org.pk))

    def test_publication_creates_raw_source_but_not_professional_readiness(self):
        vacancy = self.published()
        opportunity = Opportunity.objects.get(pk=vacancy.opportunity_id)
        self.assertEqual(opportunity.source_platform, 'CareerGPS Direct')
        self.assertEqual(opportunity.company, self.org.name)
        self.assertTrue(Opportunity.objects.discoverable().filter(pk=opportunity.pk).exists())
        record = OpportunitySourceRecord.objects.get(opportunity=opportunity)
        self.assertEqual(record.source.source_type, 'direct')
        self.assertFalse(record.source.active)
        self.assertEqual(record.current_raw.raw_payload['requirements'][0]['raw_value'], 'Python')
        self.assertIsNone(requirement_projection(opportunity))
        self.assertFalse(Opportunity.objects.professionally_ready().filter(pk=opportunity.pk).exists())
        self.assertFalse(OpportunityExtraction.objects.exists())
        self.assertEqual(opportunity.application_url, reverse('applications:start', args=[opportunity.pk]))

    def test_published_edit_synchronizes_preserves_identity_and_source_history(self):
        vacancy = self.published()
        application = Application.objects.create(user=self.candidate, opportunity=vacancy.opportunity)
        services.save_vacancy(user=self.owner, organization_id=self.org.pk, vacancy_id=vacancy.pk,
            data={'title': 'Senior Analyst'}, requirements=[self.req('SQL', level='preferred')])
        vacancy.refresh_from_db()
        self.assertEqual(vacancy.opportunity.title, 'Senior Analyst')
        self.assertEqual(vacancy.opportunity_id, application.opportunity_id)
        self.assertEqual(vacancy.publication_revision, 2)
        self.assertEqual(RawOpportunity.objects.count(), 2)
        self.assertIsNone(requirement_projection(vacancy.opportunity))
        self.assertFalse(Opportunity.objects.professionally_ready().filter(
            pk=vacancy.opportunity_id).exists())
        self.assertTrue(self.org.audit_events.filter(event_type='vacancy_updated').exists())

    def test_invalid_published_edit_rolls_back_all_changes(self):
        vacancy = self.published()
        with self.assertRaises(ValidationError):
            services.save_vacancy(user=self.owner, organization_id=self.org.pk, vacancy_id=vacancy.pk,
                data={'title': 'Invalid edit'}, requirements=[self.req('Python', minimum_years=4)])
        vacancy.refresh_from_db()
        self.assertEqual(vacancy.title, 'Data Analyst')
        self.assertEqual(vacancy.requirements.get().raw_value, 'Python')
        self.assertEqual(vacancy.publication_revision, 1)

    def test_close_reopen_preserves_submitted_and_saved_history(self):
        hiring, _ = self.submitted()
        saved = SavedOpportunity.objects.create(user=self.candidate, opportunity=hiring.vacancy.opportunity)
        services.close_vacancy(user=self.owner, organization_id=self.org.pk, vacancy_id=hiring.vacancy_id)
        self.assertFalse(Opportunity.objects.discoverable().exists())
        self.assertTrue(SubmittedDocument.objects.filter(hiring_application=hiring).exists())
        self.assertTrue(SavedOpportunity.objects.filter(pk=saved.pk).exists())
        services.publish_vacancy(user=self.owner, organization_id=self.org.pk, vacancy_id=hiring.vacancy_id)
        self.assertEqual(Opportunity.objects.discoverable().get().pk, hiring.application.opportunity_id)
        self.assertTrue(self.org.audit_events.filter(event_type='vacancy_reopened').exists())

    def test_expired_cannot_publish(self):
        vacancy = self.draft(closing_date=timezone.localdate() - timedelta(days=1))
        with self.assertRaises(ValidationError):
            services.publish_vacancy(user=self.owner, organization_id=self.org.pk, vacancy_id=vacancy.pk)

    def test_publication_keeps_platform_suppression(self):
        vacancy = self.published()
        vacancy.opportunity.moderation_status = 'suppressed'
        vacancy.opportunity.save()
        services.publish_vacancy(user=self.owner, organization_id=self.org.pk, vacancy_id=vacancy.pk)
        self.assertFalse(Opportunity.objects.discoverable().exists())

    def test_organization_rename_requires_review_preserving_published_company(self):
        vacancy = self.published()
        services.update_organization(user=self.owner, organization_id=self.org.pk, data={'name': 'Renamed Employer'})
        vacancy.refresh_from_db()
        self.org.refresh_from_db()
        self.assertEqual(self.org.verification_status,'draft')
        self.assertTrue(vacancy.trust_hold)
        self.assertFalse(Opportunity.objects.discoverable().exists())
        self.assertEqual(vacancy.opportunity.company, 'Example Hiring')
        self.assertIsNone(requirement_projection(vacancy.opportunity))

    @skip('Retired raw employer-requirement matcher projection; source requirements await external Clean-Job v1 cleaning.')
    def test_all_requirement_types_and_levels_preserved(self):
        requirements = [self.req('Python or R'), self.req('SQL', level='preferred'),
            self.req('Diploma or degree', 'qualification', minimum_nqf=6),
            self.req('Advanced diploma', 'qualification', 'preferred', minimum_nqf=7),
            self.req('3 to 5 years developing software', 'experience', minimum_years=3, maximum_years=5, context='software development'),
            self.req('At most 2 years', 'experience', 'preferred', maximum_years=2),
            self.req('AWS', 'certification'), self.req('English', 'language', 'preferred'),
            self.req('Right to work', 'work_eligibility'), self.req('Eligible for travel', 'work_eligibility', 'preferred')]
        vacancy = self.published(requirements=requirements)
        model = build_opportunity_matching_input(vacancy.opportunity)
        self.assertEqual(model.required_skills, ('Python or R',))
        self.assertEqual(model.preferred_skills, ('SQL',))
        self.assertEqual(model.minimum_nqf, 6)
        self.assertEqual(model.preferred_minimum_nqf, 7)
        self.assertEqual(model.required_certifications, ('AWS',))
        self.assertEqual(model.preferred_languages, ('English',))
        self.assertEqual(model.preferred_eligibility_text, 'Eligible for travel')
        self.assertEqual([(item.minimum_years, item.maximum_years) for item in model.experience_constraints], [(3, 5), (None, 2)])
        self.assertFalse(model.uses_ai_extraction)

    def test_invalid_requirement_semantics_rejected(self):
        for row in [self.req('Years', 'experience', minimum_years=5, maximum_years=2),
                self.req('Degree', 'qualification', minimum_nqf=11), self.req('Skill', minimum_nqf=6)]:
            with self.subTest(row=row), self.assertRaises(ValidationError):
                self.draft(requirements=[row])

    @skip('Retired raw employer-requirement matcher projection.')
    def test_same_matcher_direct_evidence_and_unknown_eligibility(self):
        ProfileSkill.objects.create(profile=self.profile, entered_name='Python')
        vacancy = self.published(requirements=[self.req('Python or R'), self.req('Right to work', 'work_eligibility')])
        result = match_profile_to_opportunity(self.profile, vacancy.opportunity)
        skill = next(row for row in result.criteria if row.criterion == 'Python or R')
        eligibility = next(row for row in result.criteria if row.criterion == 'Right to work')
        self.assertEqual(skill.status, MatchStatus.FOUND)
        self.assertEqual(eligibility.status, MatchStatus.UNKNOWN)
        self.assertFalse(eligibility.profile_evidence)

    @skip('Retired raw employer-requirement matcher projection.')
    def test_preferred_nqf_and_eligibility_never_become_required(self):
        Education.objects.create(profile=self.profile, qualification='Diploma', institution='College', nqf_level=6)
        vacancy = self.published(requirements=[self.req('Degree', 'qualification', 'preferred', minimum_nqf=7),
            self.req('Work eligibility', 'work_eligibility', 'preferred')])
        result = match_profile_to_opportunity(self.profile, vacancy.opportunity)
        rows = [row for row in result.criteria if row.key in {'preferred:nqf', 'preferred:eligibility'}]
        self.assertEqual(len(rows), 2)
        self.assertTrue(all(row.importance == CriterionImportance.PREFERRED for row in rows))

    @skip('Retired raw employer-requirement matcher projection; confirmed-only evidence is covered by Clean-Job v1 tests.')
    def test_unconfirmed_profile_skill_does_not_become_evidence(self):
        ProfileSkill.objects.create(profile=self.profile, entered_name='Python', confirmed_by_user=False)
        result = match_profile_to_opportunity(self.profile, self.published().opportunity)
        row = next(row for row in result.criteria if row.criterion == 'Python')
        self.assertEqual(row.status, MatchStatus.NOT_FOUND_ON_PROFILE)
        self.assertFalse(row.profile_evidence)

    @skip('Retired legacy direct-requirement projection; employer publication now requires external Clean-Job v1 processing.')
    def test_direct_requirements_never_call_ai_even_force(self):
        vacancy = self.published()
        with patch('ingestion.ai.service.GeminiOpportunityExtractor') as extractor:
            result = process_opportunity_batch([vacancy.opportunity], force=True)
        extractor.assert_not_called()
        self.assertEqual(result[0].status, 'skipped')
        self.assertFalse(OpportunityExtraction.objects.exists())

    @skip('Retired raw employer-requirement job context; downstream professional context is clean-revision-only.')
    def test_direct_job_context_uses_structured_requirements(self):
        vacancy = self.published(application_instructions='Include the selected CV.', requirements=[self.req('Python'),
            self.req('Diploma', 'qualification', minimum_nqf=6), self.req('Development', 'experience', minimum_years=3, maximum_years=5)])
        context = resolve_job_context(vacancy.opportunity, force=True)
        self.assertEqual(context['status'], 'catalogue_available')
        self.assertIn('Python', str(context['data']))
        self.assertIn('Include the selected CV.', str(context['data']))
        self.assertIn('minimum NQF 6', str(context['data']))
        self.assertIn('minimum 3 years; maximum 5 years', str(context['data']))
        app, _ = self.application(vacancy)
        self.client.force_login(self.candidate)
        self.assertContains(self.client.get(reverse('applications:workspace', args=[app.pk])), 'Include the selected CV.')

    @skip('Retired legacy CareerMarketSnapshot aggregation input path.')
    def test_market_freeze_preserves_direct_provenance_without_ai_coverage(self):
        row = _freeze(self.published().opportunity)
        self.assertEqual(row['requirement_origin'], 'employer_entered')
        self.assertFalse(row['extraction_ready'])
        self.assertIsNone(row['extraction'])
        self.assertEqual(_requirements([row], 'skill')['coverage'], 1)
        self.assertEqual(row['sources'][0]['code'], 'careergps-direct')

    def test_material_canonical_change_invalidates_direct_projection(self):
        vacancy = self.published()
        vacancy.opportunity.title = 'Platform changed title'
        vacancy.opportunity.save()
        self.assertIsNone(requirement_projection(vacancy.opportunity))

    def test_submission_reuses_candidate_application_identity(self):
        hiring, doc = self.submitted()
        hiring.application.refresh_from_db()
        self.assertEqual(Application.objects.count(), 1)
        self.assertEqual(hiring.stage, 'received')
        self.assertEqual(hiring.application.status, 'applied')
        self.assertIsNotNone(hiring.application.applied_at)
        self.assertEqual(hiring.submitted_documents.get().original_document_id, doc.pk)
        self.assertEqual(hiring.stage_events.get().changed_by, self.candidate)
        self.assertEqual(Notification.objects.filter(user=self.candidate, category='applications').count(), 1)

    def test_duplicate_submit_idempotent(self):
        hiring, doc = self.submitted()
        again = services.submit_application(user=self.candidate, application_id=hiring.application_id, document_ids=[doc.pk], consent=True)
        self.assertEqual(hiring.pk, again.pk)
        self.assertEqual(HiringApplication.objects.count(), 1)
        self.assertEqual(Notification.objects.count(), 1)

    def test_consent_required(self):
        app, doc = self.application()
        with self.assertRaises(ValidationError):
            services.submit_application(user=self.candidate, application_id=app.pk, document_ids=[doc.pk], consent=False)
        self.assertFalse(HiringApplication.objects.exists())

    def test_submission_requires_own_application(self):
        app, doc = self.application()
        with self.assertRaises(Http404):
            services.submit_application(user=self.other, application_id=app.pk, document_ids=[doc.pk], consent=True)

    def test_submission_rejects_foreign_document_and_other_application_version(self):
        app, doc = self.application()
        foreign_app = Application.objects.create(user=self.other, opportunity=app.opportunity)
        foreign_doc = self.document(foreign_app)
        other_app, other_doc = self.application()
        for ids in [[foreign_doc.pk], [other_doc.pk], [doc.pk, foreign_doc.pk]]:
            with self.subTest(ids=ids), self.assertRaises(ValidationError):
                services.submit_application(user=self.candidate, application_id=app.pk, document_ids=ids, consent=True)

    def test_requires_cv_not_cover_letter_alone(self):
        app, _ = self.application()
        letter = self.document(app, 'cover_letter')
        with self.assertRaises(ValidationError):
            services.submit_application(user=self.candidate, application_id=app.pk, document_ids=[letter.pk], consent=True)

    def test_cv_and_optional_cover_letter_are_both_frozen(self):
        app, cv = self.application()
        letter = self.document(app, 'cover_letter', b'cover letter bytes')
        hiring = services.submit_application(user=self.candidate, application_id=app.pk, document_ids=[cv.pk, letter.pk], consent=True)
        self.assertEqual(hiring.submitted_documents.count(), 2)

    def test_missing_document_file_rolls_back_submission(self):
        app, doc = self.application()
        Path(doc.pdf_file.path).unlink()
        with self.assertRaises(ValidationError):
            services.submit_application(user=self.candidate, application_id=app.pk, document_ids=[doc.pk], consent=True)
        self.assertFalse(HiringApplication.objects.exists())
        app.refresh_from_db()
        self.assertEqual(app.status, 'planning')

    def test_oversized_document_rejected(self):
        app, doc = self.application()
        Path(doc.pdf_file.path).write_bytes(b'x' * (10 * 1024 * 1024 + 1))
        with self.assertRaises(ValidationError):
            services.submit_application(user=self.candidate, application_id=app.pk, document_ids=[doc.pk], consent=True)

    def test_closed_vacancy_does_not_accept_submission(self):
        app, doc = self.application()
        services.close_vacancy(user=self.owner, organization_id=self.org.pk, vacancy_id=app.opportunity.employer_vacancy.pk)
        with self.assertRaises(Http404):
            services.submit_application(user=self.candidate, application_id=app.pk, document_ids=[doc.pk], consent=True)

    def test_frozen_document_survives_rewrite_regeneration_and_original_deletion(self):
        hiring, doc = self.submitted()
        frozen = hiring.submitted_documents.get()
        original = bytes(frozen.pdf_bytes)
        Path(doc.pdf_file.path).write_bytes(b'changed layout')
        self.document(hiring.application, data=b'new generation')
        doc.delete()
        frozen.refresh_from_db()
        self.assertEqual(bytes(frozen.pdf_bytes), original)
        self.assertIsNone(frozen.original_document_id)
        self.assertEqual(len(frozen.pdf_sha256), 64)

    def test_protected_employer_document_download(self):
        hiring, _ = self.submitted()
        doc = hiring.submitted_documents.get()
        response = self.client.get(self.url('document_download', self.org.pk, doc.pk, 'pdf'))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(b''.join(response.streaming_content), bytes(doc.pdf_bytes))
        self.assertEqual(response['Cache-Control'], 'private, no-store')
        self.assertIn('attachment', response['Content-Disposition'])

    def test_foreign_employer_and_viewer_cannot_download(self):
        hiring, _ = self.submitted()
        doc = hiring.submitted_documents.get()
        self.client.force_login(self.other)
        self.assertEqual(self.client.get(self.url('document_download', self.foreign.pk, doc.pk, 'pdf')).status_code, 404)
        self.client.force_login(self.member('viewer'))
        self.assertEqual(self.client.get(self.url('document_download', self.org.pk, doc.pk, 'pdf')).status_code, 403)

    def test_candidate_download_ownership(self):
        hiring, _ = self.submitted()
        doc = hiring.submitted_documents.get()
        self.client.force_login(self.candidate)
        self.assertEqual(self.client.get(self.url('candidate_document', doc.pk, 'pdf')).status_code, 200)
        self.client.force_login(self.owner)
        self.assertEqual(self.client.get(self.url('candidate_document', doc.pk, 'pdf')).status_code, 404)

    def test_all_progression_stages_have_history_audit_and_synchronized_candidate_status(self):
        hiring, _ = self.submitted()
        before_profile = Profile.objects.values().get(pk=self.profile.pk)
        expected = {'screening': 'applied', 'shortlisted': 'applied', 'assessment': 'assessment',
            'interview': 'interview', 'final_review': 'interview', 'offer': 'offer', 'hired': 'hired'}
        for stage, status in expected.items():
            services.move_stage(user=self.owner, organization_id=self.org.pk, hiring_id=hiring.pk, stage=stage)
            self.assertTrue(hiring.stage_events.filter(to_stage=stage, changed_by=self.owner).exists())
            self.assertTrue(self.org.audit_events.filter(event_type='application_' + stage).exists())
            hiring.application.refresh_from_db()
            self.assertEqual(hiring.application.status, status)
        hiring.application.refresh_from_db()
        self.assertEqual(hiring.application.status, 'hired')
        self.assertEqual(hiring.application.notes, 'CANDIDATE-PRIVATE-NOTE')
        self.assertEqual(Profile.objects.values().get(pk=self.profile.pk), before_profile)
        self.assertEqual(hiring.stage_events.count(), 8)
        self.assertEqual(Notification.objects.count(), 5)

    def test_same_stage_is_idempotent(self):
        hiring, _ = self.submitted()
        services.move_stage(user=self.owner, organization_id=self.org.pk, hiring_id=hiring.pk, stage='received')
        self.assertEqual(hiring.stage_events.count(), 1)
        self.assertEqual(Notification.objects.count(), 1)

    def test_interviewer_and_viewer_cannot_move_stage(self):
        hiring, _ = self.submitted()
        for role in ['interviewer', 'viewer']:
            with self.subTest(role=role), self.assertRaises(PermissionDenied):
                services.move_stage(user=self.member(role), organization_id=self.org.pk, hiring_id=hiring.pk, stage='offer')

    def test_cross_organization_stage_and_note_denied(self):
        hiring, _ = self.submitted()
        with self.assertRaises(Http404):
            services.move_stage(user=self.other, organization_id=self.foreign.pk, hiring_id=hiring.pk, stage='rejected')
        with self.assertRaises(Http404):
            services.add_note(user=self.other, organization_id=self.foreign.pk, hiring_id=hiring.pk, content='Foreign note')
        self.assertFalse(EmployerApplicationNote.objects.exists())

    def test_private_notes_do_not_notify_or_enter_evidence(self):
        hiring, _ = self.submitted()
        before = match_profile_to_opportunity(self.profile, hiring.application.opportunity)
        services.add_note(user=self.owner, organization_id=self.org.pk, hiring_id=hiring.pk, content='EMPLOYER-SECRET Python expert')
        after = match_profile_to_opportunity(self.profile, hiring.application.opportunity)
        self.assertEqual(before.criteria, after.criteria)
        self.assertEqual(Notification.objects.count(), 1)
        self.assertNotIn('EMPLOYER-SECRET', str(list(EmployerAuditEvent.objects.values('context'))))

    def test_private_note_visible_only_to_authorized_organization_roles(self):
        hiring, _ = self.submitted()
        services.add_note(user=self.owner, organization_id=self.org.pk, hiring_id=hiring.pk, content='EMPLOYER-SECRET')
        self.assertContains(self.client.get(self.url('applicant', self.org.pk, hiring.pk)), 'EMPLOYER-SECRET')
        self.client.force_login(self.member('viewer'))
        self.assertNotContains(self.client.get(self.url('applicant', self.org.pk, hiring.pk)), 'EMPLOYER-SECRET')
        self.client.force_login(self.candidate)
        self.assertEqual(self.client.get(self.url('applicant', self.org.pk, hiring.pk)).status_code, 404)
        self.assertNotContains(self.client.get(reverse('applications:workspace', args=[hiring.application_id])), 'EMPLOYER-SECRET')

    def test_notification_preferences_do_not_erase_stage_events(self):
        NotificationPreference.objects.create(user=self.candidate, applications=False)
        hiring, _ = self.submitted()
        services.move_stage(user=self.owner, organization_id=self.org.pk, hiring_id=hiring.pk, stage='offer')
        self.assertFalse(Notification.objects.exists())
        self.assertEqual(hiring.stage_events.count(), 2)

    def test_withdraw_retains_history_stops_pipeline_and_live_match(self):
        hiring, doc = self.submitted()
        services.withdraw_application(user=self.candidate, application_id=hiring.application_id)
        hiring.refresh_from_db()
        self.assertIsNotNone(hiring.withdrawn_at)
        self.assertEqual(hiring.application.status, 'withdrawn')
        self.assertTrue(hiring.submitted_documents.exists())
        frozen = hiring.submitted_documents.get()
        self.assertEqual(
            self.client.get(self.url('document_download', self.org.pk, frozen.pk, 'pdf')).status_code,
            200,
        )
        self.assertIsNone(self.client.get(self.url('applicant', self.org.pk, hiring.pk)).context['match_result'])
        with self.assertRaises(ValidationError):
            services.move_stage(user=self.owner, organization_id=self.org.pk, hiring_id=hiring.pk, stage='interview')
        with self.assertRaises(ValidationError):
            services.submit_application(user=self.candidate, application_id=hiring.application_id, document_ids=[doc.pk], consent=True)

    def test_rejected_application_retains_authorized_frozen_document_access(self):
        hiring, _ = self.submitted()
        services.move_stage(
            user=self.owner,
            organization_id=self.org.pk,
            hiring_id=hiring.pk,
            stage='rejected',
        )
        frozen = hiring.submitted_documents.get()
        self.assertEqual(
            self.client.get(self.url('document_download', self.org.pk, frozen.pk, 'pdf')).status_code,
            200,
        )

    def test_employer_cannot_withdraw_candidate_application(self):
        hiring, _ = self.submitted()
        with self.assertRaises(Http404):
            services.withdraw_application(user=self.owner, application_id=hiring.application_id)

    def test_hiring_identity_cannot_point_to_another_vacancy(self):
        app, _ = self.application()
        with self.assertRaises(ValidationError):
            HiringApplication(application=app, vacancy=self.published()).full_clean()

    def test_application_and_canonical_deletion_protected_after_submission(self):
        hiring, _ = self.submitted()
        with self.assertRaises(ProtectedError):
            hiring.application.delete()
        with self.assertRaises(ProtectedError):
            hiring.application.opportunity.delete()

    def test_employer_get_pages_render(self):
        hiring, _ = self.submitted()
        for name, args in [('home', []), ('organization_create', []), ('dashboard', [self.org.pk]),
                ('organization_settings', [self.org.pk]), ('team', [self.org.pk]), ('vacancies', [self.org.pk]),
                ('vacancy_create', [self.org.pk]), ('vacancy_edit', [self.org.pk, hiring.vacancy_id]),
                ('applicants', [self.org.pk]), ('vacancy_applicants', [self.org.pk, hiring.vacancy_id]),
                ('applicant', [self.org.pk, hiring.pk])]:
            with self.subTest(name=name):
                self.assertEqual(self.client.get(self.url(name, *args)).status_code, 200)

    def test_cross_org_object_routes_return_404(self):
        hiring, _ = self.submitted()
        self.client.force_login(self.other)
        for name, args in [('dashboard', [self.org.pk]), ('vacancy_edit', [self.foreign.pk, hiring.vacancy_id]),
                ('vacancy_applicants', [self.foreign.pk, hiring.vacancy_id]), ('applicant', [self.foreign.pk, hiring.pk])]:
            with self.subTest(name=name):
                self.assertEqual(self.client.get(self.url(name, *args)).status_code, 404)
        self.assertEqual(self.client.post(self.url('stage_change', self.foreign.pk, hiring.pk), {'stage': 'offer'}).status_code, 404)
        self.assertEqual(self.client.post(self.url('vacancy_publish', self.foreign.pk, hiring.vacancy_id)).status_code, 404)

    def test_mutation_routes_reject_get(self):
        hiring, _ = self.submitted()
        for name, args in [('vacancy_publish', [self.org.pk, hiring.vacancy_id]), ('vacancy_close', [self.org.pk, hiring.vacancy_id]),
                ('stage_change', [self.org.pk, hiring.pk]), ('note_add', [self.org.pk, hiring.pk]),
                ('candidate_submit', [hiring.application_id]), ('candidate_withdraw', [hiring.application_id]),
                ('accept_invitation', [999])]:
            with self.subTest(name=name):
                self.assertEqual(self.client.get(self.url(name, *args)).status_code, 405)

    def test_csrf_required_for_mutations(self):
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.owner)
        vacancy = self.draft()
        self.assertEqual(client.post(self.url('vacancy_publish', self.org.pk, vacancy.pk)).status_code, 403)
        self.assertEqual(client.post(self.url('organization_create'), {'name': 'CSRF'}).status_code, 403)

    def test_anonymous_employer_routes_require_login(self):
        self.client.logout()
        self.assertEqual(self.client.get(self.url('home')).status_code, 302)
        self.assertEqual(self.client.get(self.url('dashboard', self.org.pk)).status_code, 302)

    def test_employer_cannot_read_candidate_workspace_or_edit_notes_documents(self):
        hiring, doc = self.submitted()
        self.assertEqual(self.client.get(reverse('applications:workspace', args=[hiring.application_id])).status_code, 404)
        self.assertEqual(self.client.post(reverse('applications:save_notes', args=[hiring.application_id]), {'notes': 'overwrite'}).status_code, 404)
        self.assertEqual(self.client.post(reverse('applications:update_cv_layout', args=[doc.pk]), {}).status_code, 404)
        response = self.client.get(self.url('applicant', self.org.pk, hiring.pk))
        self.assertNotContains(response, 'CANDIDATE-PRIVATE-NOTE')
        self.assertNotContains(response, 'PRIVATE-PHONE')

    def test_candidate_cannot_forge_received_or_hiring_stage(self):
        app, doc = self.application()
        self.client.force_login(self.candidate)
        self.client.post(reverse('applications:update_status', args=[app.pk]), {'status': 'applied'})
        app.refresh_from_db()
        self.assertEqual(app.status, 'planning')
        self.assertFalse(HiringApplication.objects.exists())
        services.submit_application(user=self.candidate, application_id=app.pk, document_ids=[doc.pk], consent=True)
        self.client.post(reverse('applications:update_status', args=[app.pk]), {'status': 'offer'})
        app.refresh_from_db()
        self.assertEqual(app.status, 'applied')
        self.assertEqual(app.hiring_application.stage, 'received')

    def test_platform_admin_verification_and_suspension_audited(self):
        from .trust_services import set_account_status
        self.other.is_staff=self.other.is_superuser=True
        self.other.save()
        set_account_status(actor=self.other,organization_id=self.org.pk,status='suspended',reason='Platform review')
        event=self.org.audit_events.get(event_type='organization_suspended')
        self.assertEqual(event.actor,self.other)
        self.assertEqual(event.context['previous_account'],'active')
        self.assertEqual(event.context['account'],'suspended')


    def test_admin_history_has_no_add_delete_or_editable_fields(self):
        request = RequestFactory().get('/admin/')
        request.user = self.owner
        for model in [ApplicationStageEvent, SubmittedDocument, EmployerAuditEvent, EmployerApplicationNote]:
            model_admin = admin.site._registry[model]
            self.assertFalse(model_admin.has_add_permission(request))
            self.assertFalse(model_admin.has_delete_permission(request))
            self.assertIn('id', model_admin.get_readonly_fields(request))

    def test_http_vertical_slice(self):
        self.assertEqual(self.client.post(self.url('organization_create'), {'name': 'HTTP Employer'}).status_code, 302)
        org = Organization.objects.get(name='HTTP Employer')
        # Real platform admin action, using the same account's existing auth.
        self.owner.is_staff = self.owner.is_superuser = True
        self.owner.save()
        from .trust_services import submit_verification
        submit_verification(user=self.owner,organization_id=org.pk,data={
            'legal_name':'HTTP Employer','organization_type':'sa_company','country':'South Africa',
            'registration_number':'2026/123456/07'})
        response=self.client.post(self.url('verification_review',org.pk),{'action':'verify'})
        self.assertEqual(response.status_code,302)
        data = {'no_candidate_fees':'on', 'title': 'HTTP Analyst', 'location': 'Cape Town', 'description': 'Analyze data.', 'country': 'South Africa',
            'opportunity_kind': 'job', 'career_stage': 'early_career', 'requirements-TOTAL_FORMS': '2',
            'requirements-INITIAL_FORMS': '0', 'requirements-0-criterion_type': 'skill',
            'requirements-0-requirement_level': 'required', 'requirements-0-raw_value': 'Python',
            'requirements-1-criterion_type': 'qualification',
            'requirements-1-requirement_level': 'required',
            'requirements-1-raw_value': 'BSc Computer Science'}
        response = self.client.post(self.url('vacancy_create', org.pk), data)
        self.assertEqual(response.status_code, 302, getattr(response, 'context', None))
        vacancy = org.vacancies.get()
        self.assertEqual(self.client.post(self.url('vacancy_publish', org.pk, vacancy.pk)).status_code, 302)
        vacancy.refresh_from_db()
        activate_clean_opportunity(vacancy.opportunity)
        vacancy.opportunity.refresh_from_db()
        ProfileSkill.objects.create(profile=self.profile, entered_name='Python')
        Education.objects.create(profile=self.profile, qualification='BSc Computer Science')
        create_current_evaluation(self.profile, vacancy.opportunity)
        self.client.force_login(self.candidate)
        self.assertContains(self.client.get(reverse('opportunities')), 'HTTP Analyst')
        self.assertEqual(self.client.get(reverse('opportunity_detail', args=[vacancy.opportunity_id])).status_code, 200)
        self.assertEqual(self.client.get(reverse('applications:start', args=[vacancy.opportunity_id])).status_code, 302)
        app = Application.objects.get(user=self.candidate, opportunity=vacancy.opportunity)
        doc = self.document(app)
        self.assertContains(self.client.get(reverse('applications:workspace', args=[app.pk])), 'Submit application')
        self.assertEqual(self.client.post(self.url('candidate_submit', app.pk), {'cv': doc.pk, 'consent': 'on'}).status_code, 302)
        hiring = HiringApplication.objects.get(application=app)
        self.client.force_login(self.owner)
        self.assertContains(self.client.get(self.url('dashboard', org.pk)), 'Application received')
        self.assertContains(self.client.get(self.url('applicants', org.pk)), 'Casey Applicant')
        self.assertContains(self.client.get(self.url('applicant', org.pk, hiring.pk)), 'Download PDF')
        self.client.post(self.url('note_add', org.pk, hiring.pk), {'content': 'INTERNAL-ONLY'})
        for stage in ['shortlisted', 'interview', 'offer']:
            self.assertEqual(self.client.post(self.url('stage_change', org.pk, hiring.pk), {'stage': stage}).status_code, 302)
        self.client.force_login(self.candidate)
        response = self.client.get(reverse('applications:workspace', args=[app.pk]))
        self.assertContains(response, 'Employer update: Offer')
        self.assertNotContains(response, 'INTERNAL-ONLY')
        self.assertTrue(Notification.objects.filter(user=self.candidate, title='Application update: Offer').exists())
        self.assertEqual(self.client.post(self.url('candidate_withdraw', app.pk)).status_code, 302)
        self.assertContains(self.client.get(reverse('applications:workspace', args=[app.pk])), 'Withdrawn')

    def test_candidate_navigation_has_employer_switch(self):
        self.client.force_login(self.candidate)
        self.assertContains(self.client.get(reverse('dashboard')), 'Switch to Employer')

    def test_employer_comparison_excludes_private_career_preferences(self):
        from core.models import CareerPreference
        CareerPreference.objects.create(profile=self.profile, target_roles='Data Analyst',
            preferred_locations='PRIVATE-LOCATION', industries='PRIVATE-INDUSTRY', short_career_goal='PRIVATE-GOAL')
        hiring, _ = self.submitted()
        response = self.client.get(self.url('applicant', self.org.pk, hiring.pk))
        for value in ['PRIVATE-LOCATION', 'PRIVATE-INDUSTRY', 'PRIVATE-GOAL']:
            self.assertNotContains(response, value)
        self.assertFalse(response.context['match_result'].user.target_roles)
        self.assertFalse(response.context['match_result'].user.locations)
        self.assertFalse(response.context['match_result'].user.industries)

    def test_internal_stage_is_not_exposed_in_candidate_workspace(self):
        hiring, _ = self.submitted()
        services.move_stage(user=self.owner, organization_id=self.org.pk, hiring_id=hiring.pk, stage='shortlisted')
        self.client.force_login(self.candidate)
        response = self.client.get(reverse('applications:workspace', args=[hiring.application_id]))
        self.assertContains(response, 'Employer update: Received')
        self.assertNotContains(response, 'Shortlisted')

    def test_recruiter_and_hiring_manager_can_progress_but_not_change_team(self):
        hiring, _ = self.submitted()
        for role, stage in [('recruiter', 'shortlisted'), ('hiring_manager', 'interview')]:
            self.client.force_login(self.member(role))
            self.assertEqual(self.client.post(self.url('stage_change', self.org.pk, hiring.pk), {'stage': stage}).status_code, 302)
            self.assertEqual(self.client.get(self.url('team', self.org.pk)).status_code, 403)
        hiring.refresh_from_db()
        self.assertEqual(hiring.stage, 'interview')

    def test_interviewer_can_note_but_cannot_author_vacancy(self):
        hiring, _ = self.submitted()
        interviewer=self.member('interviewer')
        from .collaboration_services import assign
        assign(user=self.owner,organization_id=self.org.pk,member_id=interviewer.employer_memberships.get().pk,
            responsibility='application',hiring_id=hiring.pk)
        self.client.force_login(interviewer)
        self.assertEqual(self.client.post(self.url('note_add', self.org.pk, hiring.pk), {'content': 'Reviewed submitted experience'}).status_code, 302)
        self.assertEqual(self.client.get(self.url('vacancy_create', self.org.pk)).status_code, 403)

    def test_source_provenance_retains_zero_compensation(self):
        self.published(salary_min=0, salary_max=0, salary_currency='ZAR', salary_period='month')
        self.assertEqual(RawOpportunity.objects.get().raw_payload['salary_min'], '0.00')

    def test_external_ingestion_cannot_merge_into_or_reopen_direct_vacancy(self):
        from ingestion.models import IngestionSource
        from ingestion.pipeline.deduplication import find_duplicate, apply_canonical_fields
        vacancy = self.published(reference_number='SAME-REF')
        source = IngestionSource.objects.create(code='external-test', name='External test', source_type='api', priority=1)
        incoming = {'reference_number': 'SAME-REF', 'title': vacancy.title, 'company': self.org.name,
            'location': 'Cape Town', 'description': 'UNTRUSTED REPLACEMENT', 'is_active': True, 'lifecycle_status': 'active'}
        self.assertIsNone(find_duplicate(source, 'external', incoming)[0])
        services.close_vacancy(user=self.owner, organization_id=self.org.pk, vacancy_id=vacancy.pk)
        vacancy.refresh_from_db()
        apply_canonical_fields(vacancy.opportunity, incoming, source, authoritative_update=True)
        vacancy.opportunity.refresh_from_db()
        self.assertFalse(vacancy.opportunity.is_active)
        self.assertNotEqual(vacancy.opportunity.description, 'UNTRUSTED REPLACEMENT')

    @skip('Retired legacy raw-vacancy taxonomy matcher.')
    def test_taxonomy_alias_alternative_remains_found_and_narrower_remains_related(self):
        from core.models import TaxonomyRelease, TaxonomyConcept, TaxonomyLabel, TaxonomyRelation
        release = TaxonomyRelease.objects.create(source='esco', version='employer-test', is_active=True)
        concepts = []
        for index, label in enumerate(['Python', 'Programming']):
            concept = TaxonomyConcept.objects.create(release=release, kind='skill', external_id=str(index),
                preferred_label=label, normalized_label=label.lower())
            TaxonomyLabel.objects.create(concept=concept, label=label, normalized_label=label.lower(), label_type='preferred')
            concepts.append(concept)
        TaxonomyLabel.objects.create(concept=concepts[0], label='Python programming', normalized_label='python programming', label_type='alternative')
        TaxonomyRelation.objects.create(subject=concepts[0], object=concepts[1], relation_type='broader')
        ProfileSkill.objects.create(profile=self.profile, entered_name='Python programming')
        vacancy = self.published(requirements=[self.req('Python or R'), self.req('Programming')])
        result = match_profile_to_opportunity(self.profile, vacancy.opportunity)
        self.assertEqual(next(row for row in result.criteria if row.criterion == 'Python or R').status, MatchStatus.FOUND)
        self.assertEqual(next(row for row in result.criteria if row.criterion == 'Programming').status, MatchStatus.RELATED)

    @skip('Retired legacy raw-vacancy requirement matcher.')
    def test_certification_alternatives_preserve_one_requirement(self):
        from core.models import Certification
        Certification.objects.create(profile=self.profile, title='AWS')
        vacancy = self.published(requirements=[self.req('AWS or Azure', 'certification')])
        result = match_profile_to_opportunity(self.profile, vacancy.opportunity)
        rows = [row for row in result.criteria if row.criterion == 'AWS or Azure']
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].status, MatchStatus.FOUND)

    @skip('Retired legacy OpportunityCareerAssignment/CareerMarketSnapshot pipeline.')
    def test_career_intelligence_direct_snapshot_and_invalidation(self):
        from core.models import TaxonomyRelease, TaxonomyConcept, TaxonomyLabel, CareerMarketSnapshot
        from core.services.career_assignments import update_assignments
        from core.services.career_market import build_snapshots
        release = TaxonomyRelease.objects.create(source='esco', version='market-employer-test', is_active=True)
        concept = TaxonomyConcept.objects.create(release=release, kind='occupation', external_id='analyst',
            preferred_label='Data Analyst', normalized_label='data analyst')
        TaxonomyLabel.objects.create(concept=concept, label='Data Analyst', normalized_label='data analyst', label_type='preferred')
        vacancy = self.published()
        update_assignments()
        build_snapshots(occupation_id=concept.pk)
        snapshot = CareerMarketSnapshot.objects.get(occupation=concept, is_stale=False)
        self.assertEqual(snapshot.opportunity_count, 1)
        self.assertEqual(snapshot.coverage['employer_structured'], 1)
        self.assertEqual(snapshot.coverage['ready_extractions'], 0)
        self.assertEqual(snapshot.metrics['requirements']['skill']['items'][0]['label'], 'Python')
        frozen = snapshot.members.get().evidence
        services.save_vacancy(user=self.owner, organization_id=self.org.pk, vacancy_id=vacancy.pk,
            data={}, requirements=[self.req('SQL')])
        snapshot.refresh_from_db()
        self.assertTrue(snapshot.is_stale)
        self.assertEqual(snapshot.members.get().evidence, frozen)
        update_assignments()
        build_snapshots(occupation_id=concept.pk)
        current = CareerMarketSnapshot.objects.get(occupation=concept, is_stale=False)
        self.assertEqual(current.metrics['requirements']['skill']['items'][0]['label'], 'SQL')
        self.org.account_status = 'suspended'
        self.org.save()
        current.refresh_from_db()
        self.assertTrue(current.is_stale)

    def test_complete_role_and_csrf_document_submission_flow(self):
        app, doc = self.application()
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.candidate)
        url = self.url('candidate_submit', app.pk)
        self.assertEqual(client.post(url, {'cv': doc.pk, 'consent': 'on'}).status_code, 403)
        client.get(reverse('applications:workspace', args=[app.pk]))
        response = client.post(url, {'cv': doc.pk, 'consent': 'on', 'csrfmiddlewaretoken': client.cookies['csrftoken'].value})
        self.assertEqual(response.status_code, 302)
        self.assertTrue(HiringApplication.objects.filter(application=app).exists())
