"""Focused employer lifecycle and vacancy invitation contract tests."""
import importlib
from types import SimpleNamespace

from django.apps import apps
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.files.base import ContentFile
from django.http import Http404
from django.test import TestCase, override_settings
from django.db import connection
from django.urls import reverse

from applications.models import Application
from core.models import Notification, SavedOpportunity, CareerPreference
from core.test_support import activate_clean_opportunity
from . import services, talent_services
from .models import HiringApplication, TalentInvitation, EmployerAuditEvent
from . import tests as employer_tests


@override_settings(PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'])
class EmployerCompletionTests(TestCase):
    draft = employer_tests.EmployerTests.draft
    req = employer_tests.EmployerTests.req
    published = employer_tests.EmployerTests.published
    document = employer_tests.EmployerTests.document
    application = employer_tests.EmployerTests.application
    submitted = employer_tests.EmployerTests.submitted
    member = employer_tests.EmployerTests.member
    url = employer_tests.EmployerTests.url

    def published(self, **kwargs):
        vacancy = employer_tests.EmployerTests.published(self, **kwargs)
        if not vacancy.opportunity.active_clean_revision_id:
            activate_clean_opportunity(vacancy.opportunity)
            vacancy.opportunity.refresh_from_db()
        return vacancy

    def setUp(self):
        employer_tests.EmployerTests.setUp(self)

    def enable_discovery(self):
        return talent_services.set_visibility(user=self.candidate, data={
            'discoverable_by_employers': True, 'discovery_location': 'Cape Town',
        })

    def invite(self, vacancy=None):
        vacancy = vacancy or self.published()
        self.enable_discovery()
        return talent_services.invite_candidate(user=self.owner, organization_id=self.org.pk,
            candidate_id=self.profile.pk, vacancy_id=vacancy.pk)

    def test_stages_sync_shared_status_and_reject_terminal_or_backtracking(self):
        hiring, _ = self.submitted()
        services.move_stage(user=self.owner, organization_id=self.org.pk, hiring_id=hiring.pk, stage='interview')
        hiring.application.refresh_from_db()
        self.assertEqual(hiring.application.status, 'interview')
        self.assertEqual(hiring.stage_events.count(), 2)
        with self.assertRaises(ValidationError):
            services.move_stage(user=self.owner, organization_id=self.org.pk, hiring_id=hiring.pk, stage='screening')
        self.assertEqual(hiring.stage_events.count(), 2)
        services.move_stage(user=self.owner, organization_id=self.org.pk, hiring_id=hiring.pk, stage='rejected')
        hiring.application.refresh_from_db()
        self.assertEqual(hiring.application.status, 'rejected')
        with self.assertRaises(ValidationError):
            services.move_stage(user=self.owner, organization_id=self.org.pk, hiring_id=hiring.pk, stage='offer')
        with self.assertRaises(ValidationError):
            services.withdraw_application(user=self.candidate, application_id=hiring.application_id)
        self.assertEqual(hiring.stage_events.count(), 3)
        self.assertTrue(Notification.objects.filter(user=self.candidate, title='Application update: Rejected').exists())
        self.assertNotContains(self.client.get(self.url('applicant', self.org.pk, hiring.pk)), 'Update hiring stage')
        self.client.force_login(self.candidate)
        self.assertContains(self.client.get(reverse('applications:workspace', args=[hiring.application_id])),
            'Application status: <strong>Rejected</strong>', html=True)

    def test_internal_actions_do_not_notify_candidate(self):
        hiring, _ = self.submitted()
        before = Notification.objects.filter(user=self.candidate).count()
        services.move_stage(user=self.owner, organization_id=self.org.pk, hiring_id=hiring.pk, stage='screening')
        services.add_note(user=self.owner, organization_id=self.org.pk, hiring_id=hiring.pk, content='Private review')
        self.assertEqual(Notification.objects.filter(user=self.candidate).count(), before)
        hiring.application.refresh_from_db()
        self.assertEqual(hiring.application.status, 'applied')

    def test_existing_stage_backfill_aligns_status_without_new_events(self):
        interview, _ = self.submitted()
        rejected, _ = self.submitted()
        withdrawn, _ = self.submitted()
        services.move_stage(user=self.owner, organization_id=self.org.pk, hiring_id=interview.pk, stage='interview')
        services.move_stage(user=self.owner, organization_id=self.org.pk, hiring_id=rejected.pk, stage='rejected')
        services.withdraw_application(user=self.candidate, application_id=withdrawn.application_id)
        Application.objects.filter(pk__in=[interview.application_id, rejected.application_id,
            withdrawn.application_id]).update(status='applied')
        stage_count = sum(row.stage_events.count() for row in (interview, rejected, withdrawn))
        audit_count = EmployerAuditEvent.objects.count()
        notification_count = Notification.objects.count()
        migration = importlib.import_module('employers.migrations.0005_talentinvitation_and_more')
        migration.synchronize_existing_application_statuses(apps, SimpleNamespace(connection=connection))
        self.assertEqual(list(Application.objects.filter(pk__in=[interview.application_id,
            rejected.application_id, withdrawn.application_id]).order_by('pk').values_list('status', flat=True)),
            ['interview', 'rejected', 'withdrawn'])
        self.assertEqual(sum(row.stage_events.count() for row in (interview, rejected, withdrawn)), stage_count)
        self.assertEqual(EmployerAuditEvent.objects.count(), audit_count)
        self.assertEqual(Notification.objects.count(), notification_count)

    def test_invitation_is_unique_specific_and_accept_does_not_submit(self):
        vacancy = self.published()
        invitation = self.invite(vacancy)
        duplicate = self.invite(vacancy)
        self.assertEqual(invitation.pk, duplicate.pk)
        self.assertEqual(TalentInvitation.objects.count(), 1)
        self.assertEqual(Notification.objects.filter(user=self.candidate,
            event_key=f'talent-invitation:{invitation.pk}').count(), 1)
        self.assertEqual(invitation.vacancy, vacancy)
        invitation, application = talent_services.respond_to_invitation(user=self.candidate,
            invitation_id=invitation.pk, action='accepted')
        self.assertEqual(invitation.status, 'accepted')
        self.assertEqual(application.status, 'planning')
        self.assertTrue(SavedOpportunity.objects.filter(user=self.candidate, opportunity=vacancy.opportunity).exists())
        self.assertFalse(HiringApplication.objects.filter(application=application).exists())
        self.assertEqual(talent_services.respond_to_invitation(user=self.candidate,
            invitation_id=invitation.pk, action='accepted')[1].pk, application.pk)
        self.assertTrue(Notification.objects.filter(user=self.owner,
            event_key=f'talent-invitation-response:{invitation.pk}').exists())

    def test_decline_is_terminal_without_creating_application(self):
        invitation = self.invite()
        talent_services.respond_to_invitation(user=self.candidate, invitation_id=invitation.pk, action='declined')
        self.assertTrue(Notification.objects.filter(user=self.owner,
            event_key=f'talent-invitation-response:{invitation.pk}', title='Talent invitation declined').exists())
        self.assertFalse(Application.objects.filter(user=self.candidate, opportunity=invitation.vacancy.opportunity).exists())
        with self.assertRaises(ValidationError):
            talent_services.respond_to_invitation(user=self.candidate, invitation_id=invitation.pk, action='accepted')
        self.assertEqual(talent_services.invite_candidate(user=self.owner, organization_id=self.org.pk,
            candidate_id=self.profile.pk, vacancy_id=invitation.vacancy_id).pk, invitation.pk)

    def test_invitation_rechecks_opt_out_and_block_and_preserves_history(self):
        vacancy = self.published()
        self.enable_discovery()
        talent_services.set_visibility(user=self.candidate, data={'discoverable_by_employers': False})
        with self.assertRaises(Http404):
            talent_services.invite_candidate(user=self.owner, organization_id=self.org.pk,
                candidate_id=self.profile.pk, vacancy_id=vacancy.pk)
        self.enable_discovery()
        invitation = talent_services.invite_candidate(user=self.owner, organization_id=self.org.pk,
            candidate_id=self.profile.pk, vacancy_id=vacancy.pk)
        talent_services.block_organization(user=self.candidate, organization_id=self.org.pk)
        with self.assertRaises(Http404):
            talent_services.invite_candidate(user=self.owner, organization_id=self.org.pk,
                candidate_id=self.profile.pk, vacancy_id=vacancy.pk)
        self.assertTrue(TalentInvitation.objects.filter(pk=invitation.pk, status='pending').exists())
        self.assertEqual(self.client.get(self.url('talent_candidate', self.org.pk, self.profile.pk)).status_code, 404)
        self.client.force_login(self.candidate)
        self.assertContains(self.client.get(self.url('candidate_invitations')), vacancy.title)

    def test_invitation_roles_foreign_ids_and_candidate_ownership(self):
        vacancy = self.published()
        self.enable_discovery()
        with self.assertRaises(PermissionDenied):
            talent_services.invite_candidate(user=self.member('viewer'), organization_id=self.org.pk,
                candidate_id=self.profile.pk, vacancy_id=vacancy.pk)
        with self.assertRaises(Http404):
            talent_services.invite_candidate(user=self.other, organization_id=self.foreign.pk,
                candidate_id=self.profile.pk, vacancy_id=vacancy.pk)
        invitation = talent_services.invite_candidate(user=self.owner, organization_id=self.org.pk,
            candidate_id=self.profile.pk, vacancy_id=vacancy.pk)
        with self.assertRaises(Http404):
            talent_services.respond_to_invitation(user=self.other, invitation_id=invitation.pk, action='accepted')
        self.client.force_login(self.other)
        self.assertNotContains(self.client.get(self.url('candidate_invitations')), vacancy.title)

    def test_closed_vacancy_cannot_be_accepted_and_can_be_declined(self):
        invitation = self.invite()
        services.close_vacancy(user=self.owner, organization_id=self.org.pk, vacancy_id=invitation.vacancy_id)
        with self.assertRaises(ValidationError):
            talent_services.respond_to_invitation(user=self.candidate, invitation_id=invitation.pk, action='accepted')
        invitation.refresh_from_db()
        self.assertEqual(invitation.status, 'pending')
        talent_services.respond_to_invitation(user=self.candidate, invitation_id=invitation.pk, action='declined')

    def test_candidate_deletion_keeps_minimal_invitation_history(self):
        invitation = self.invite()
        self.profile.delete()
        invitation.refresh_from_db()
        self.assertIsNone(invitation.candidate_id)
        self.assertEqual(invitation.status, 'pending')

    def test_invitation_http_journey_shows_state_and_opens_workspace(self):
        vacancy = self.published()
        self.enable_discovery()
        detail = self.url('talent_candidate', self.org.pk, self.profile.pk)
        self.assertContains(self.client.get(detail, {'vacancy': vacancy.pk}), 'Send invitation')
        response = self.client.post(self.url('talent_invite', self.org.pk, self.profile.pk),
            {'vacancy_id': vacancy.pk})
        self.assertEqual(response.status_code, 302)
        self.assertIn(f'vacancy={vacancy.pk}', response['Location'])
        invitation = TalentInvitation.objects.get()
        detail_response = self.client.get(detail, {'vacancy': vacancy.pk})
        self.assertEqual(detail_response.context['invitation'].pk, invitation.pk)
        self.assertContains(detail_response, 'Invitation:')
        self.client.force_login(self.candidate)
        self.assertContains(self.client.get(self.url('candidate_invitations')), 'Accept and prepare application')
        response = self.client.post(self.url('candidate_invitation_response', invitation.pk), {'action': 'accepted'})
        self.assertEqual(response.status_code, 302)
        application = Application.objects.get(user=self.candidate, opportunity=vacancy.opportunity)
        self.assertEqual(response['Location'], reverse('applications:workspace', args=[application.pk]))
        self.assertContains(self.client.get(self.url('candidate_invitations')), 'Open application workspace')
        self.client.force_login(self.owner)
        self.assertContains(self.client.get(self.url('talent_invitations', self.org.pk)), 'Accepted')
        self.client.force_login(self.other)
        self.assertEqual(self.client.get(self.url('talent_invitations', self.org.pk)).status_code, 404)

    def test_sent_history_masks_candidate_after_opt_out(self):
        self.invite()
        self.assertContains(self.client.get(self.url('talent_invitations', self.org.pk)), 'Casey Applicant')
        talent_services.set_visibility(user=self.candidate, data={'discoverable_by_employers': False})
        response = self.client.get(self.url('talent_invitations', self.org.pk))
        self.assertContains(response, 'Candidate unavailable')
        self.assertNotContains(response, 'Casey Applicant')

    def test_sensitive_views_audited_and_frozen_document_preserved(self):
        hiring, original = self.submitted()
        self.assertEqual(self.client.get(self.url('applicant', self.org.pk, hiring.pk)).status_code, 200)
        submitted = hiring.submitted_documents.get()
        response = self.client.get(self.url('document_download', self.org.pk, submitted.pk, 'pdf'))
        self.assertEqual(response.status_code, 200)
        self.assertTrue(EmployerAuditEvent.objects.filter(organization=self.org,
            event_type='applicant_profile_viewed', object_id=hiring.pk).exists())
        self.assertTrue(EmployerAuditEvent.objects.filter(organization=self.org,
            event_type='submitted_document_downloaded', object_id=submitted.pk).exists())
        original.pdf_file.save('changed.pdf', ContentFile(b'%PDF-1.4 changed'))
        submitted.refresh_from_db()
        self.assertNotIn(b' changed', bytes(submitted.pdf_bytes))
        self.client.force_login(self.other)
        self.assertEqual(self.client.get(self.url('document_download', self.foreign.pk, submitted.pk, 'pdf')).status_code, 404)

    def test_practical_facts_do_not_expose_private_preferences(self):
        CareerPreference.objects.create(profile=self.profile, preferred_locations='PRIVATE-LOCATION',
            work_arrangement='remote')
        hiring, _ = self.submitted()
        response = self.client.get(self.url('applicant', self.org.pk, hiring.pk))
        self.assertContains(response, 'Practical job facts')
        self.assertNotContains(response, 'PRIVATE-LOCATION')
        self.assertContains(response, 'Not shared')
