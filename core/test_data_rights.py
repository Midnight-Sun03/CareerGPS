"""Self-service data export and account closure (owner-approved behaviour).

The suite proves the locked deletion contract: password verification before the
destructive step, no deletion from a GET or from a password alone, immediate
erasure/anonymisation, session invalidation, and an export that contains only
the requester's own data and states its own limitations.
"""

import hashlib
import io
import json
import zipfile
from datetime import timedelta
from tempfile import TemporaryDirectory
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core import mail
from django.core.files.base import ContentFile
from django.template import Context, Template
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from applications.models import Application, GeneratedDocument
from employers.models import (ApplicationStageEvent, EmployerApplicationNote,
    EmployerVacancy, HiringApplication, Organization, OrganizationMembership,
    SavedCandidate, SubmittedDocument)
from interview.models import Interview, InterviewQuestion

from . import data_rights
from .models import (DataRightsEvent, LegalAcceptanceEvent, Notification, Opportunity,
    PrivacyRequest, Profile, SavedOpportunity, Story, StoryReport, WorkExperience)


@override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend',
                   EMAIL_HOST='smtp.test')
class DataRightsTestCase(TestCase):
    """One candidate with profile, evidence, media, hiring history and export data."""

    def setUp(self):
        self.media = TemporaryDirectory()
        self.addCleanup(self.media.cleanup)
        media_setting = override_settings(MEDIA_ROOT=self.media.name)
        media_setting.enable()
        self.addCleanup(media_setting.disable)

        self.password = 'StrongCareerGPS2026!'
        self.email = 'candidate@example.test'
        self.user = User.objects.create_user('candidate', email=self.email,
            password=self.password, first_name='Sam', last_name='Example')
        self.profile = Profile.objects.create(user=self.user, qualification='BSc',
            institution='Example University', location='Cape Town',
            phone='+27 11 000 0000', custom_skills='Python')
        WorkExperience.objects.create(profile=self.profile, title='Analyst',
            organisation='Example Employer')
        for document_type, action in (('terms', 'agreed'), ('privacy', 'acknowledged')):
            LegalAcceptanceEvent.objects.create(user=self.user, document_type=document_type,
                document_version='2026-09-16.1', content_sha256=document_type[0] * 64,
                action=action, context='registration')

        self.opportunity = Opportunity.objects.create(title='Analyst Role',
            company='Example Employer', opportunity_type='entry_level',
            location='Cape Town', description='Work')
        self.application = Application.objects.create(user=self.user,
            opportunity=self.opportunity, status='applied',
            notes='Candidate note that must not survive')
        self.document = GeneratedDocument.objects.create(user=self.user,
            application=self.application, opportunity=self.opportunity, document_type='cv')
        self.document.pdf_file.save('cv.pdf', ContentFile(b'%PDF owned'))
        self.document.docx_file.save('cv.docx', ContentFile(b'PK owned'))
        self.pdf_name, self.docx_name = self.document.pdf_file.name, self.document.docx_file.name
        self.document_storage = self.document.pdf_file.storage

        self.interview = Interview.objects.create(user=self.user, mode='voice')
        self.question = InterviewQuestion.objects.create(interview=self.interview,
            question_index=0, question_text='Tell me about yourself', answer_mode='voice',
            corrected_transcript='Corrected words')
        self.question.audio_file.save('answer.webm', ContentFile(b'owned audio'))
        self.audio_name = self.question.audio_file.name
        self.audio_storage = self.question.audio_file.storage

        self.saved = SavedOpportunity.objects.create(user=self.user,
            opportunity=self.opportunity)
        self.notification = Notification.objects.create(user=self.user, category='system',
            notification_type='system', title='Welcome', message='Hello')

        self.employer_owner = User.objects.create_user('employer-owner',
            email='owner@example.test', password=self.password)
        self.other_user = User.objects.create_user('other-candidate',
            email='other@example.test', password=self.password)
        self.other_profile = Profile.objects.create(user=self.other_user,
            qualification='BA', institution='Other University', location='Durban')
        self.organization = Organization.objects.create(name='Example Hiring',
            slug='example-hiring', created_by=self.employer_owner,
            verification_status='approved', account_status='active')
        self.vacancy = EmployerVacancy.objects.create(organization=self.organization,
            opportunity=self.opportunity, title='Analyst Role', location='Cape Town',
            description='Work', created_by=self.employer_owner)
        self.hiring = HiringApplication.objects.create(application=self.application,
            vacancy=self.vacancy)
        self.submitted = SubmittedDocument.objects.create(hiring_application=self.hiring,
            original_document=self.document, document_type='cv', pdf_bytes=b'%PDF frozen',
            pdf_sha256='b' * 64)
        ApplicationStageEvent.objects.create(hiring_application=self.hiring,
            to_stage='screening', changed_by=self.employer_owner)
        EmployerApplicationNote.objects.create(hiring_application=self.hiring,
            organization=self.organization, author=self.employer_owner,
            content='EMPLOYER-PRIVATE-NOTE')
        self.saved_candidate = SavedCandidate.objects.create(
            organization=self.organization, candidate=self.profile,
            saved_by=self.employer_owner)

        self.published_story = Story.objects.create(user=self.user, display_name='Sam E',
            qualification='BSc', institution='Example University', title='My story',
            content='PUBLISHED-STORY-CONTENT', status='approved')
        self.unpublished_story = Story.objects.create(user=self.user, display_name='Sam E',
            qualification='BSc', title='Draft story', content='DRAFT-CONTENT',
            status='pending')
        StoryReport.objects.create(story=self.unpublished_story, reporter=self.other_user,
            reason='spam', details='Reported for testing')

    # -- helpers ----------------------------------------------------------

    def sign_in(self):
        self.assertTrue(self.client.login(username='candidate', password=self.password))

    def start_closure(self, password=None):
        return self.client.post(reverse('account_closure'),
            {'password': self.password if password is None else password})

    def confirm_closure(self):
        """Walk the locked flow (password, then final confirmation) and post it."""

        with self.captureOnCommitCallbacks(execute=True):
            start = self.start_closure()
            self.assertEqual(start.status_code, 302)
            self.assertEqual(start['Location'], reverse('account_closure_confirm'))
            final = self.client.post(reverse('account_closure_confirm'))
        return final

    def export_archive(self):
        response = self.client.post(reverse('data_export'))
        self.assertEqual(response.status_code, 200)
        payload = b''.join(response.streaming_content)
        return response, zipfile.ZipFile(io.BytesIO(payload))

    def archive_text(self, archive):
        return '\n'.join(archive.read(name).decode('utf-8', 'replace')
                         for name in archive.namelist())


    # -- password, token and request-method guards ------------------------

    def test_wrong_password_cannot_delete_and_reveals_nothing_internal(self):
        self.sign_in()
        response = self.start_closure('not-the-password')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, data_rights.STATUS_MESSAGES['password_failed'])
        self.assertNotContains(response, 'Traceback')
        self.assertNotContains(response, 'does not exist')
        self.user.refresh_from_db()
        self.assertTrue(self.user.is_active)
        self.assertTrue(self.user.has_usable_password())
        self.assertTrue(Profile.objects.filter(user=self.user).exists())
        self.assertFalse(PrivacyRequest.objects.filter(requester=self.user).exists())
        self.assertFalse(DataRightsEvent.objects.filter(
            user=self.user, action=DataRightsEvent.Action.CLOSURE_CONFIRMED).exists())
        self.assertNotIn(data_rights.CLOSURE_TOKEN_SESSION_KEY, self.client.session)
        self.assertTrue(DataRightsEvent.objects.filter(
            user=self.user,
            action=DataRightsEvent.Action.CLOSURE_PASSWORD_FAILED).exists())

    def test_correct_password_alone_never_deletes(self):
        self.sign_in()
        start = self.start_closure()
        self.assertEqual(start['Location'], reverse('account_closure_confirm'))
        self.user.refresh_from_db()
        self.assertTrue(self.user.is_active)
        self.assertTrue(Profile.objects.filter(user=self.user).exists())
        self.assertEqual(Interview.objects.filter(user=self.user).count(), 1)

    def test_confirmation_requires_a_fresh_password_token(self):
        self.sign_in()
        response = self.client.post(reverse('account_closure_confirm'))
        self.assertRedirects(response, reverse('account_closure'))
        self.user.refresh_from_db()
        self.assertTrue(self.user.is_active)
        self.assertTrue(Profile.objects.filter(user=self.user).exists())

    def test_confirmation_token_expires(self):
        self.sign_in()
        self.start_closure()
        session = self.client.session
        session[data_rights.CLOSURE_TOKEN_ISSUED_KEY] = (
            timezone.now() - data_rights.CLOSURE_TOKEN_TTL - timedelta(minutes=1)).isoformat()
        session.save()
        response = self.client.post(reverse('account_closure_confirm'))
        self.assertRedirects(response, reverse('account_closure'))
        self.assertTrue(Profile.objects.filter(user=self.user).exists())

    def test_get_requests_can_never_delete(self):
        self.sign_in()
        self.assertEqual(self.client.get(reverse('account_closure')).status_code, 200)
        self.assertRedirects(self.client.get(reverse('account_closure_confirm')),
                             reverse('account_closure'))
        self.assertEqual(self.client.get(reverse('data_export')).status_code, 302)
        self.assertTrue(Profile.objects.filter(user=self.user).exists())
        self.assertEqual(Interview.objects.filter(user=self.user).count(), 1)

    def test_destructive_requests_require_csrf(self):
        csrf_client = Client(enforce_csrf_checks=True)
        self.assertTrue(csrf_client.login(username='candidate', password=self.password))
        refused = csrf_client.post(reverse('account_closure'), {'password': self.password})
        self.assertEqual(refused.status_code, 403)
        self.assertTrue(Profile.objects.filter(user=self.user).exists())

    def test_final_confirmation_is_csrf_protected(self):
        csrf_client = Client(enforce_csrf_checks=True)
        self.assertTrue(csrf_client.login(username='candidate', password=self.password))
        csrf_client.get(reverse('account_closure'))
        token = csrf_client.cookies['csrftoken'].value
        start = csrf_client.post(reverse('account_closure'),
            {'password': self.password, 'csrfmiddlewaretoken': token})
        self.assertEqual(start['Location'], reverse('account_closure_confirm'))
        refused = csrf_client.post(reverse('account_closure_confirm'))
        self.assertEqual(refused.status_code, 403)
        self.user.refresh_from_db()
        self.assertTrue(self.user.is_active)
        accepted = csrf_client.post(reverse('account_closure_confirm'),
            {'csrfmiddlewaretoken': token})
        self.assertRedirects(accepted, reverse('landing'), fetch_redirect_response=False)

    def test_anonymous_requests_are_redirected_to_the_login_page(self):
        for name in ('data_export', 'account_closure', 'account_closure_confirm'):
            with self.subTest(name=name):
                response = self.client.get(reverse(name))
                self.assertEqual(response.status_code, 302)
                self.assertIn(reverse('login'), response['Location'])

    def test_password_failures_are_throttled_and_issue_no_token(self):
        self.sign_in()
        for _ in range(data_rights.PASSWORD_FAILURE_LIMIT):
            self.start_closure('wrong-password')
        response = self.start_closure()
        self.assertContains(response, data_rights.STATUS_MESSAGES['password_throttled'])
        self.assertNotIn(data_rights.CLOSURE_TOKEN_SESSION_KEY, self.client.session)
        self.assertTrue(Profile.objects.filter(user=self.user).exists())

    def test_organisation_owner_is_blocked_until_ownership_is_transferred(self):
        OrganizationMembership.objects.create(organization=self.organization,
            user=self.user, role='owner', status='active')
        self.sign_in()
        response = self.start_closure()
        self.assertRedirects(response, reverse('settings'))
        self.user.refresh_from_db()
        self.assertTrue(self.user.is_active)
        self.assertTrue(Profile.objects.filter(user=self.user).exists())
        self.assertTrue(DataRightsEvent.objects.filter(
            user=self.user, action=DataRightsEvent.Action.CLOSURE_BLOCKED).exists())

    def test_ordinary_employer_member_is_not_blocked(self):
        OrganizationMembership.objects.create(organization=self.organization,
            user=self.user, role='recruiter', status='active')
        self.sign_in()
        start = self.start_closure()
        self.assertEqual(start['Location'], reverse('account_closure_confirm'))

    def test_organisation_creator_is_blocked_without_an_owner_role(self):
        Organization.objects.create(name='Second Hiring', slug='second-hiring',
            created_by=self.user)
        self.sign_in()
        self.assertRedirects(self.start_closure(), reverse('settings'))

    # -- the locked deletion contract --------------------------------------

    def test_successful_closure_prevents_login(self):
        self.sign_in()
        final = self.confirm_closure()
        self.assertRedirects(final, reverse('landing'), fetch_redirect_response=False)
        self.user.refresh_from_db()
        self.assertFalse(self.user.is_active)
        self.assertFalse(self.user.has_usable_password())
        self.assertFalse(self.client.login(username=self.user.username,
                                          password=self.password))
        response = self.client.post(reverse('login'),
            {'email': self.email, 'password': self.password})
        self.assertEqual(response.status_code, 200)

    def test_existing_sessions_on_other_devices_are_invalidated(self):
        self.sign_in()
        second_device = Client()
        self.assertTrue(second_device.login(username='candidate', password=self.password))
        self.assertEqual(second_device.get(reverse('settings')).status_code, 200)
        self.confirm_closure()
        after = second_device.get(reverse('settings'))
        self.assertEqual(after.status_code, 302)
        self.assertIn(reverse('login'), after['Location'])

    def test_personal_profile_data_is_removed_and_the_email_is_released(self):
        self.sign_in()
        self.confirm_closure()
        self.user.refresh_from_db()
        self.assertFalse(Profile.objects.filter(user=self.user).exists())
        self.assertFalse(WorkExperience.objects.filter(profile=self.profile).exists())
        self.assertFalse(SavedOpportunity.objects.filter(user=self.user).exists())
        self.assertFalse(Notification.objects.filter(user=self.user).exists())
        self.assertEqual(self.user.first_name, '')
        self.assertEqual(self.user.last_name, '')
        self.assertTrue(data_rights.is_anonymised_account(self.user))
        self.assertIn(data_rights.ANONYMISED_EMAIL_DOMAIN, self.user.email)
        self.assertNotIn(self.email, self.user.email)
        self.assertFalse(User.objects.filter(email__iexact=self.email,
                                            is_active=True).exists())

    def test_interview_records_and_audio_are_removed(self):
        self.sign_in()
        self.confirm_closure()
        self.assertFalse(Interview.objects.filter(user=self.user).exists())
        self.assertFalse(InterviewQuestion.objects.filter(pk=self.question.pk).exists())
        self.assertFalse(self.audio_storage.exists(self.audio_name))

    def test_generated_documents_and_their_files_are_removed(self):
        self.sign_in()
        self.confirm_closure()
        self.assertFalse(GeneratedDocument.objects.filter(user=self.user).exists())
        self.assertFalse(self.document_storage.exists(self.pdf_name))
        self.assertFalse(self.document_storage.exists(self.docx_name))

    def test_closure_email_is_sent_to_the_address_on_file(self):
        self.sign_in()
        self.confirm_closure()
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, [self.email])
        self.assertIn('deleted', mail.outbox[0].subject)

    def test_email_failure_never_blocks_or_rolls_back_closure(self):
        with patch('core.data_rights.EmailMultiAlternatives.send',
                   side_effect=RuntimeError('smtp unavailable')):
            report = data_rights.close_account(self.user)
        self.assertFalse(report.email_sent)
        self.assertEqual(report.email_error, 'RuntimeError')
        self.user.refresh_from_db()
        self.assertFalse(self.user.is_active)
        self.assertFalse(Profile.objects.filter(user=self.user).exists())
        self.assertTrue(data_rights.is_anonymised_account(self.user))

    def test_closure_records_a_completed_tracking_request(self):
        self.sign_in()
        self.confirm_closure()
        request = PrivacyRequest.objects.get(requester=self.user)
        self.assertEqual(request.category, PrivacyRequest.Category.DELETION)
        self.assertEqual(request.status, PrivacyRequest.Status.COMPLETED)
        self.assertIsNotNone(request.handled_at)
        self.assertIsNone(request.handled_by)
        self.assertEqual(request.staff_note, '')
        self.assertNotEqual(request.description, '')

    # -- retention, isolation and repeat safety ----------------------------

    def test_retained_hiring_history_is_limited_and_anonymised(self):
        self.sign_in()
        self.confirm_closure()
        for record in (self.application, self.submitted, self.published_story,
                       self.unpublished_story, self.saved_candidate):
            record.refresh_from_db()

        # Submitted workspaces survive for the employer, without candidate notes.
        self.assertTrue(HiringApplication.objects.filter(pk=self.hiring.pk).exists())
        self.assertEqual(ApplicationStageEvent.objects.filter(
            hiring_application=self.hiring).count(), 1)
        self.assertEqual(self.application.notes, '')
        self.assertEqual(bytes(self.submitted.pdf_bytes), b'%PDF frozen')
        self.assertIsNone(self.submitted.original_document)
        self.assertTrue(EmployerApplicationNote.objects.filter(
            hiring_application=self.hiring).exists())

        # Consent evidence stays linked to the now-anonymised account.
        self.assertEqual(LegalAcceptanceEvent.objects.filter(user=self.user).count(), 2)

        # Published stories keep their words without the author's identity.
        self.assertIsNone(self.published_story.user)
        self.assertEqual(self.published_story.display_name, '')
        self.assertEqual(self.published_story.qualification, '')
        self.assertEqual(self.published_story.content, 'PUBLISHED-STORY-CONTENT')

        # A moderation-protected story keeps its row but loses personal fields.
        self.assertIsNone(self.unpublished_story.user)
        self.assertEqual(self.unpublished_story.display_name, '')
        self.assertEqual(self.unpublished_story.content, '')
        self.assertTrue(StoryReport.objects.filter(story=self.unpublished_story).exists())

        # Employer discovery links are cut without deleting employer records.
        self.assertIsNone(self.saved_candidate.candidate)

    def test_unsubmitted_application_workspace_is_erased(self):
        other_opportunity = Opportunity.objects.create(title='Other Role',
            company='Other Employer', opportunity_type='entry_level',
            location='Cape Town', description='Work')
        draft = Application.objects.create(user=self.user, opportunity=other_opportunity,
            status='planning', notes='Draft note')
        self.sign_in()
        self.confirm_closure()
        self.assertFalse(Application.objects.filter(pk=draft.pk).exists())
        self.assertTrue(Application.objects.filter(pk=self.application.pk).exists())

    def test_other_users_data_is_untouched(self):
        other_interview = Interview.objects.create(user=self.other_user)
        other_question = InterviewQuestion.objects.create(interview=other_interview,
            question_index=0, question_text='Other question')
        other_question.audio_file.save('other.webm', ContentFile(b'other audio'))
        Notification.objects.create(user=self.other_user, category='system',
            notification_type='system', title='Other', message='Other')
        self.sign_in()
        self.confirm_closure()
        self.other_user.refresh_from_db()
        self.assertTrue(self.other_user.is_active)
        self.assertTrue(self.other_user.has_usable_password())
        self.assertTrue(Profile.objects.filter(user=self.other_user).exists())
        self.assertTrue(Interview.objects.filter(user=self.other_user).exists())
        self.assertTrue(other_question.audio_file.storage.exists(
            other_question.audio_file.name))
        self.assertTrue(Notification.objects.filter(user=self.other_user).exists())
        self.assertEqual(StoryReport.objects.filter(reporter=self.other_user).count(), 1)
        self.assertTrue(SavedCandidate.objects.filter(pk=self.saved_candidate.pk).exists())

    def test_closure_is_safe_on_repeat(self):
        first = data_rights.close_account(self.user)
        self.assertFalse(first.already_closed)
        self.user.refresh_from_db()
        second = data_rights.close_account(self.user)
        self.assertTrue(second.already_closed)
        self.assertEqual(PrivacyRequest.objects.filter(requester=self.user).count(), 1)
        self.assertEqual(DataRightsEvent.objects.filter(
            user=self.user, action=DataRightsEvent.Action.CLOSURE_COMPLETED).count(), 1)
        self.assertEqual(LegalAcceptanceEvent.objects.filter(user=self.user).count(), 2)

    def test_employer_facing_label_never_shows_the_anonymised_username(self):
        self.sign_in()
        self.confirm_closure()
        self.user.refresh_from_db()
        template = Template('{% load employer_display %}{{ person|candidate_identity }}')
        rendered = template.render(Context({'person': self.user})).strip()
        self.assertEqual(rendered, data_rights.DELETED_CANDIDATE_LABEL)
        self.assertNotIn(self.user.username, rendered)

    # -- export ------------------------------------------------------------

    def test_export_contains_only_the_requesters_own_data(self):
        self.sign_in()
        response, archive = self.export_archive()
        self.assertEqual(response['Content-Type'], 'application/zip')
        self.assertIn('attachment', response['Content-Disposition'])
        self.assertIn('no-store', response['Cache-Control'])

        names = set(archive.namelist())
        self.assertIn('manifest.json', names)
        for name in ('account.json', 'profile.json', 'evidence.json', 'career.json',
                     'applications.json', 'interviews.json', 'documents.json',
                     'notifications.json', 'story.json', 'legal_acceptances.json',
                     'privacy_requests.json', 'saved_opportunities.json'):
            self.assertIn(name, names)

        profile = json.loads(archive.read('profile.json'))['profile']
        self.assertEqual(profile['qualification'], 'BSc')
        self.assertEqual(profile['phone'], '+27 11 000 0000')
        evidence = json.loads(archive.read('evidence.json'))
        self.assertEqual(evidence['work_experiences'][0]['title'], 'Analyst')
        interviews = json.loads(archive.read('interviews.json'))['interviews']
        self.assertEqual(interviews[0]['questions'][0]['corrected_transcript'],
                         'Corrected words')
        applications = json.loads(archive.read('applications.json'))['applications']
        self.assertEqual(applications[0]['employer_stage_history'][0]['to_stage'],
                         'screening')

        text = self.archive_text(archive)
        self.assertIn('Candidate note that must not survive', text)
        self.assertNotIn('other@example.test', text)
        self.assertNotIn('Other University', text)
        self.assertNotIn('Durban', text)
        self.assertNotIn('other-candidate', text)

    def test_export_excludes_internal_and_employer_private_records(self):
        self.sign_in()
        _, archive = self.export_archive()
        text = self.archive_text(archive)
        self.assertNotIn('EMPLOYER-PRIVATE-NOTE', text)
        self.assertNotIn('pbkdf2_sha256', text)
        self.assertNotIn('sessionid', text)

        manifest = json.loads(archive.read('manifest.json'))
        self.assertEqual(manifest['schema_version'], data_rights.EXPORT_SCHEMA_VERSION)
        self.assertTrue(any('employer' in note for note in manifest['excluded']))
        self.assertEqual(manifest['missing_files'], [])

    def test_export_surfaces_missing_files_instead_of_omitting_them(self):
        self.question.audio_file.name = 'interviews/audio/gone.webm'
        self.question.save(update_fields=['audio_file'])
        self.sign_in()
        _, archive = self.export_archive()
        manifest = json.loads(archive.read('manifest.json'))
        self.assertIn('interviews/audio/gone.webm', manifest['missing_files'])
        self.assertTrue(any('missing_files' in note for note in manifest['limitations']))
        self.assertNotIn('files/interview_audio/',
                         ' '.join(entry['path'] for entry in manifest['files']))

    def test_export_carries_owned_media_so_no_private_link_is_needed(self):
        self.sign_in()
        _, archive = self.export_archive()
        manifest = json.loads(archive.read('manifest.json'))
        paths = {entry['path']: entry for entry in manifest['files']}
        audio = [path for path in paths if path.startswith('files/interview_audio/')]
        self.assertTrue(audio)
        self.assertEqual(paths[audio[0]]['bytes'], len(b'owned audio'))
        self.assertEqual(paths[audio[0]]['sha256'],
                         hashlib.sha256(b'owned audio').hexdigest())
        with archive.open(audio[0]) as handle:
            self.assertEqual(handle.read(), b'owned audio')
        documents = [path for path in paths
                     if path.startswith('files/generated_documents/')]
        self.assertEqual(len(documents), 2)

    def test_export_records_an_accountability_event_without_personal_data(self):
        self.sign_in()
        self.export_archive()
        event = DataRightsEvent.objects.get(user=self.user,
            action=DataRightsEvent.Action.EXPORT_CREATED)
        self.assertEqual(event.detail['files'], 3)
        self.assertNotIn(self.email, json.dumps(event.detail))

    def test_export_is_rate_limited(self):
        self.sign_in()
        for _ in range(data_rights.EXPORT_HOURLY_LIMIT):
            data_rights.record_event(self.user, DataRightsEvent.Action.EXPORT_CREATED)
        response = self.client.post(reverse('data_export'))
        self.assertRedirects(response, reverse('settings'))

    def test_settings_page_offers_the_export_and_closure_entry_points(self):
        self.sign_in()
        response = self.client.get(reverse('settings'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, reverse('data_export'))
        self.assertContains(response, 'Download My Data')
        self.assertContains(response, reverse('account_closure'))
        self.assertContains(response, 'Delete Account')
        self.assertContains(response, 'This cannot be undone.')

    def test_closure_steps_show_the_locked_wording_and_password_field(self):
        self.sign_in()
        password_step = self.client.get(reverse('account_closure'))
        self.assertEqual(password_step.status_code, 200)
        self.assertContains(password_step, 'Delete your account')
        self.assertContains(password_step, 'type="password"')
        self.start_closure()
        confirm_step = self.client.get(reverse('account_closure_confirm'))
        self.assertEqual(confirm_step.status_code, 200)
        self.assertContains(confirm_step, 'Delete your CareerGPS account?')
        self.assertContains(confirm_step, 'Removed straight away')
        self.assertContains(confirm_step, 'Kept in limited, anonymised form')
        self.assertContains(confirm_step, 'Yes, I want to delete my account')
