"""M4A human-review lifecycle, hard authorization and trust security tests."""
from datetime import timedelta
from unittest.mock import patch
from django.contrib import admin
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.http import Http404
from django.test import TestCase, Client, RequestFactory, override_settings
from django.urls import reverse
from django.utils import timezone
from applications.models import Application
from core.models import Education, Notification, NotificationPreference, Opportunity, OpportunityReport, ProfileSkill
from core.services.moderation import submit_report, resolve_report
from core.test_support import activate_clean_opportunity
from . import tests as m1, test_talent as m2, services, talent_services
from . import trust_services as trust
from .domains import website_domain, email_domain
from .permissions import membership_for, organization_can_use_employer_platform
from .models import (Organization, OrganizationMembership, OrganizationVerification, VerificationReviewEvent,
    VerificationDocument, OrganizationRiskFlag, EmployerAbuseCounter, MemberDomainSignal, EmployerAuditEvent)
from .abuse import consume
from .analytics import funnel
from .discovery import eligible_profiles


@override_settings(PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'])
class VerificationTests(TestCase):
    draft=m1.EmployerTests.draft
    req=m1.EmployerTests.req
    published=m1.EmployerTests.published
    document=m1.EmployerTests.document
    application=m1.EmployerTests.application
    submitted=m1.EmployerTests.submitted
    member=m1.EmployerTests.member
    url=m1.EmployerTests.url
    enable=m2.TalentTests.enable

    def published(self, **kwargs):
        vacancy = m1.EmployerTests.published(self, **kwargs)
        if not vacancy.opportunity.active_clean_revision_id:
            activate_clean_opportunity(vacancy.opportunity)
            vacancy.opportunity.refresh_from_db()
        return vacancy

    def setUp(self):
        m1.EmployerTests.setUp(self)
        self.org.verification_status='draft'
        self.org.save()
        self.reviewer=get_user_model().objects.create_user('platform-reviewer',is_staff=True,password='test')
        self.reviewer.user_permissions.add(Permission.objects.get(codename='review_employer'))
        self.data={'legal_name':'Example Hiring','organization_type':'sa_company','registration_number':'2026/123456/07',
            'country':'South Africa','website':'https://careers.example.co.za/jobs','business_email':'recruiter@example.co.za',
            'registry_source':'Claimed registry source','registry_reference':'Claimed reference','evidence_notes':'Submitted evidence'}

    def submit(self,**changes):
        return trust.submit_verification(user=self.owner,organization_id=self.org.pk,data={**self.data,**changes})

    def review(self,row,action,**kwargs):
        return trust.review_verification(actor=self.reviewer,verification_id=row.pk,action=action,**kwargs)

    def approved(self):
        row=self.submit()
        self.review(row,'begin_review')
        self.review(row,'registry_checked',source='Authoritative source checked manually',reference='Independent check reference')
        self.review(row,'independent_contact_confirmed',contact_source='Public company website',
            contact_reference='Published switchboard',contact_method='phone',contact_value='+27 11 555 0100')
        self.review(row,'approve')
        self.org.refresh_from_db()
        return row

    def suspend(self):
        return trust.set_account_status(actor=self.reviewer,organization_id=self.org.pk,status='suspended',reason='PRIVATE-PLATFORM-REASON')

    def restore(self):
        return trust.set_account_status(actor=self.reviewer,organization_id=self.org.pk,status='active',reason='Review completed')

    def test_new_organization_draft_no_self_approval_fields(self):
        row=services.create_organization(user=self.candidate,data={'name':'New employer','verification_status':'approved','account_status':'active','approved_at':timezone.now()})
        self.assertEqual(row.verification_status,'draft')
        self.assertIsNone(row.approved_at)
        self.assertFalse(organization_can_use_employer_platform(row))

    def test_submit_records_normalized_evidence_actor_time(self):
        row=self.submit()
        self.org.refresh_from_db()
        self.assertEqual(self.org.verification_status,'submitted')
        self.assertEqual(row.website_domain,'example.co.za')
        self.assertEqual(row.email_domain,'example.co.za')
        self.assertEqual(row.submitted_by,self.owner)
        self.assertIsNotNone(row.submitted_at)
        self.assertTrue(row.review_events.filter(action='verification_submitted').exists())

    def test_review_and_approval_requires_authoritative_check(self):
        row=self.submit()
        self.review(row,'begin_review')
        with self.assertRaises(ValidationError): self.review(row,'approve')
        self.review(row,'registry_checked',source='Checked registry',reference='Document reference')
        with self.assertRaises(ValidationError): self.review(row,'approve')
        self.review(row,'independent_contact_confirmed',contact_source='Public company website',
            contact_reference='Published switchboard',contact_method='phone',contact_value='+27 11 555 0100')
        row=self.review(row,'approve')
        self.org.refresh_from_db()
        self.assertTrue(organization_can_use_employer_platform(self.org))
        self.assertEqual(row.reviewer,self.reviewer)
        self.assertIsNotNone(row.registry_checked_at)
        self.assertEqual(row.independent_contact_confirmed_by,self.reviewer)
        self.assertIsNotNone(row.independent_contact_confirmed_at)
        self.assertIsNotNone(row.review_started_at)
        self.assertIsNotNone(row.reviewed_at)
        self.assertIsNotNone(self.org.approved_at)

    def test_registrant_email_cannot_count_as_independent_contact(self):
        row=self.submit()
        self.review(row,'begin_review')
        self.review(row,'registry_checked',source='Official registry',reference='Entry 42')
        with self.assertRaises(ValidationError):
            self.review(row,'independent_contact_confirmed',contact_source='Submitted application',
                contact_reference='Registrant email',contact_method='email',contact_value=self.data['business_email'])
        row.refresh_from_db()
        self.assertIsNone(row.independent_contact_confirmed_at)
        with self.assertRaises(ValidationError): self.review(row,'approve')
        self.review(row,'independent_contact_confirmed',contact_source='Official company site',
            contact_reference='Published directory',contact_method='phone',contact_value='+27 11 555 0100',
            internal_note='Private staff verification detail')
        row.refresh_from_db()
        self.assertEqual(row.independent_contact_confirmed_by,self.reviewer)
        self.assertTrue(row.review_events.filter(action='independent_contact_confirmed',
            internal_note='Private staff verification detail').exists())
        self.client.force_login(self.owner)
        response=self.client.get(self.url('verification',self.org.pk))
        self.assertNotContains(response,'Published directory')
        self.assertNotContains(response,'Private staff verification detail')

    def test_review_outcomes_notify_active_owner_without_private_review_data(self):
        NotificationPreference.objects.create(user=self.owner, stories=False, opportunities=False,
            deadlines=False, applications=False, interviews=False, career_intelligence=False)
        second_owner = get_user_model().objects.create_user('second-owner', password='test')
        OrganizationMembership.objects.create(organization=self.org, user=second_owner, role='owner', status='active')
        cases = [
            ('request_info', 'More verification information needed'),
            ('reject', 'Organization verification rejected'),
            ('approve', 'Organization verification approved'),
        ]
        for action, title in cases:
            row = self.submit()
            self.review(row, 'begin_review')
            if action == 'approve':
                self.review(row, 'registry_checked', source='PRIVATE-CHECKED-SOURCE',
                    reference='PRIVATE-REGISTRY-REFERENCE')
                self.review(row,'independent_contact_confirmed',contact_source='PRIVATE-CONTACT-SOURCE',
                    contact_reference='PRIVATE-CONTACT-REFERENCE',contact_method='phone',contact_value='+27 11 555 0100')
                options = {}
            else:
                options = {'employer_message': 'Please review the verification page.'}
            self.review(row, action, internal_note='PRIVATE-STAFF-NOTE', **options)
            notification = Notification.objects.get(user=self.owner, title=title)
            self.assertEqual(notification.category, 'system')
            self.assertEqual(notification.target_url, reverse('employers:verification', args=[self.org.pk]))
            self.assertNotIn('PRIVATE-', notification.title + notification.message)
            self.assertNotIn('Submitted evidence', notification.message)
            self.assertTrue(Notification.objects.filter(user=second_owner, title=title).exists())
            self.assertFalse(Notification.objects.filter(user=self.candidate, title=title).exists())
            with self.assertRaises(ValidationError):
                self.review(row, action, internal_note='PRIVATE-STAFF-NOTE', **options)
            self.assertEqual(Notification.objects.filter(user=self.owner, title=title).count(), 1)

    def test_stale_verification_notification_does_not_restore_organization_access(self):
        row = self.submit()
        self.review(row, 'begin_review')
        self.review(row, 'request_info', employer_message='More evidence required')
        notification = Notification.objects.get(user=self.owner, title='More verification information needed')
        OrganizationMembership.objects.filter(user=self.owner, organization=self.org).update(status='disabled')
        self.client.force_login(self.owner)
        response = self.client.post(reverse('notification_open', args=[notification.pk]))
        self.assertRedirects(response, notification.target_url, fetch_redirect_response=False)
        self.assertEqual(self.client.get(notification.target_url).status_code, 404)

    def test_approve_cannot_skip_review(self):
        with self.assertRaises(ValidationError): self.review(self.submit(),'approve')

    def test_more_information_resubmission_preserves_previous_review(self):
        old=self.submit();self.review(old,'begin_review')
        self.review(old,'request_info',employer_message='Please provide authorization evidence.',internal_note='PRIVATE-INTERNAL-NOTE')
        newer=self.submit(evidence_notes='Additional authorization reference')
        old.refresh_from_db()
        self.assertNotEqual(old.pk,newer.pk)
        self.assertEqual(old.status,'more_info_required')
        self.assertEqual(old.review_events.count(),3)
        self.assertTrue(newer.review_events.filter(action='verification_resubmitted').exists())
        self.assertEqual(old.review_events.last().internal_note,'PRIVATE-INTERNAL-NOTE')

    def test_employer_visible_message_without_internal_notes(self):
        row=self.submit();self.review(row,'begin_review')
        self.review(row,'request_info',employer_message='Provide authorization letter.',internal_note='PRIVATE-INTERNAL-NOTE')
        response=self.client.get(self.url('verification',self.org.pk))
        self.assertContains(response,'Provide authorization letter.')
        self.assertNotContains(response,'PRIVATE-INTERNAL-NOTE')
        self.assertNotContains(response,'registration_not_checked')

    def test_rejection_retains_evidence_and_reason(self):
        row=self.submit();self.review(row,'begin_review')
        row=self.review(row,'reject',employer_message='Evidence was insufficient.')
        self.assertEqual(row.status,'rejected')
        self.assertEqual(row.legal_name,self.data['legal_name'])
        self.assertEqual(row.employer_message,'Evidence was insufficient.')

    def test_rejection_and_information_request_require_message(self):
        row=self.submit();self.review(row,'begin_review')
        for action in ['reject','request_info']:
            with self.assertRaises(ValidationError): self.review(row,action)

    def test_rejected_can_submit_new_evidence(self):
        row=self.submit();self.review(row,'begin_review');self.review(row,'reject',employer_message='New evidence needed')
        self.assertNotEqual(self.submit().pk,row.pk)
        self.assertEqual(OrganizationVerification.objects.count(),2)

    def test_pending_submission_cannot_be_overwritten(self):
        self.submit()
        with self.assertRaises(ValidationError): self.submit(legal_name='Changed')
        self.assertEqual(OrganizationVerification.objects.get().legal_name,'Example Hiring')

    def test_historical_submission_cannot_be_reviewed_again(self):
        row=self.submit();self.review(row,'begin_review');self.review(row,'request_info',employer_message='More information')
        self.submit()
        with self.assertRaises(ValidationError): self.review(row,'begin_review')

    def test_normal_owner_cannot_approve(self):
        row=self.submit()
        with self.assertRaises(PermissionDenied): trust.review_verification(actor=self.owner,verification_id=row.pk,action='approve')

    def test_recruiter_cannot_review_or_resolve_flags(self):
        row=self.submit();user=self.member('recruiter')
        with self.assertRaises(PermissionDenied): trust.review_verification(actor=user,verification_id=row.pk,action='begin_review')
        flag=OrganizationRiskFlag.objects.first()
        with self.assertRaises(PermissionDenied): trust.resolve_flag(actor=user,flag_id=flag.pk,reason='No')

    def test_staff_without_custom_permission_denied(self):
        self.owner.is_staff=True;self.owner.save()
        with self.assertRaises(PermissionDenied): trust.review_verification(actor=self.owner,verification_id=self.submit().pk,action='begin_review')
        self.assertEqual(self.client.get(self.url('verification_review',self.org.pk)).status_code,403)

    def test_inactive_reviewer_denied(self):
        self.reviewer.is_active=False;self.reviewer.save()
        with self.assertRaises(PermissionDenied): self.review(self.submit(),'begin_review')

    def test_organization_admin_fields_cannot_bypass_workflow(self):
        self.client.force_login(self.reviewer)
        response=self.client.post(reverse('admin:employers_organization_change',args=[self.org.pk]),{'verification_status':'approved','account_status':'active','_save':'Save'})
        self.assertEqual(response.status_code,403)
        request=RequestFactory().post('/admin/');request.user=self.owner
        with self.assertRaises(PermissionDenied): admin.site._registry[Organization].save_model(request,self.org,None,True)
        self.org.refresh_from_db();self.assertEqual(self.org.verification_status,'draft')

    def test_owner_cannot_mark_registry_check_or_suspend(self):
        row=self.submit()
        with self.assertRaises(PermissionDenied): trust.review_verification(actor=self.owner,verification_id=row.pk,action='registry_checked',source='Fake',reference='Fake')
        with self.assertRaises(PermissionDenied): trust.set_account_status(actor=self.owner,organization_id=self.org.pk,status='suspended',reason='No')

    def test_sa_company_requires_number(self):
        with self.assertRaises(ValidationError): self.submit(registration_number='')
        self.assertFalse(OrganizationVerification.objects.exists())

    def test_sa_company_country_consistency(self):
        with self.assertRaises(ValidationError): self.submit(country='Canada')

    def test_government_institution_ngo_foreign_other_no_cipc_required(self):
        for kind in ['public_body','education','nonprofit','foreign','other']:
            with self.subTest(kind=kind):
                self.org.verification_status='draft';self.org.save()
                row=self.submit(organization_type=kind,registration_number='',country='Canada' if kind=='foreign' else 'South Africa')
                self.assertEqual(row.organization_type,kind)
                self.assertEqual(row.registration_number,'')

    def test_other_types_require_authoritative_source_reference(self):
        with self.assertRaises(ValidationError): self.submit(organization_type='nonprofit',registration_number='',registry_source='',registry_reference='')

    def test_unknown_organization_type_rejected(self):
        with self.assertRaises(ValidationError): self.submit(organization_type='invented')

    def test_domain_subdomain_case_port_path(self):
        for value in ['https://example.co.za','https://careers.EXAMPLE.co.za:8443/jobs?q=a','https://www.example.co.za/']:
            self.assertEqual(website_domain(value),'example.co.za')
        self.assertEqual(email_domain('Recruiter@Example.co.za'),'example.co.za')

    def test_unrelated_lookalike_domain_not_equivalent(self):
        self.assertNotEqual(website_domain('https://example-careers.co.za'),website_domain('https://example.co.za'))
        self.assertEqual(website_domain('https://example.co.za.evil.com'),'evil.com')

    def test_private_suffix_tenants_not_equivalent(self):
        self.assertEqual(website_domain('https://one.github.io'),'one.github.io')
        self.assertNotEqual(website_domain('https://one.github.io'),website_domain('https://two.github.io'))

    def test_idna_unicode_and_punycode_equivalent(self):
        self.assertEqual(website_domain('https://b\u00fccher.de'),website_domain('https://xn--bcher-kva.de'))

    def test_unsafe_url_schemes_rejected(self):
        for value in ['javascript:alert(1)','data:text/html,hello','file:///tmp/test','ftp://example.com','http://example.com']:
            with self.subTest(value=value),self.assertRaises(ValidationError): website_domain(value)

    def test_malformed_credentials_local_ip_urls_rejected(self):
        for value in ['example.com','https:///example.com','https://user:pass@example.com','https://127.0.0.1','https://localhost','https://example.com:99999','https://example.com\\@evil.com','https://example.com/%0aheader']:
            with self.subTest(value=value),self.assertRaises(ValidationError): website_domain(value)

    def test_domains_do_not_fetch_network(self):
        with patch('requests.sessions.Session.request',side_effect=AssertionError('No network')):
            self.assertEqual(website_domain('https://careers.example.co.za'),'example.co.za')

    def test_free_email_flags_not_automatic_rejection(self):
        row=self.submit(business_email='recruiter@gmail.com')
        self.assertTrue(self.org.risk_flags.filter(code='free_email_domain',severity='review').exists())
        self.review(row,'begin_review');self.review(row,'registry_checked',source='Checked source',reference='Institutional evidence')
        self.review(row,'independent_contact_confirmed',contact_source='Institution directory',contact_reference='Independent listing',contact_method='phone',contact_value='+27 11 555 0100')
        self.review(row,'approve')
        self.org.refresh_from_db();self.assertEqual(self.org.verification_status,'approved')

    def test_email_mismatch_is_review_flag(self):
        row=self.submit(business_email='recruiter@agency.com')
        flag=self.org.risk_flags.get(code='email_domain_mismatch')
        self.assertEqual(flag.evidence['email_domain'],'agency.com')
        self.assertEqual(flag.severity,'review')
        self.assertFalse(hasattr(flag,'score'))

    def test_missing_website_email_signals(self):
        self.submit(website='',business_email='')
        self.assertTrue({'website_missing','business_email_missing','registration_not_checked'}.issubset(set(self.org.risk_flags.values_list('code',flat=True))))

    def test_destination_unsafe_publication_rejected(self):
        self.approved()
        with self.assertRaises(ValidationError): self.draft(external_destination='javascript:alert(1)')
        self.assertFalse(self.org.vacancies.exists())
        self.assertTrue(self.org.risk_flags.filter(code='malformed_application_destination',severity='high_risk').exists())

    def test_destination_mismatch_and_shortener_flags(self):
        self.approved();vacancy=self.published(external_destination='https://bit.ly/example')
        self.assertEqual(vacancy.destination_domain,'bit.ly')
        self.assertTrue({'application_domain_mismatch','shortened_destination'}.issubset(set(self.org.risk_flags.values_list('code',flat=True))))
        self.assertTrue(Opportunity.objects.discoverable().filter(pk=vacancy.opportunity_id).exists())

    def test_destination_change_after_publication_flag(self):
        self.approved();vacancy=self.published(external_destination='https://example.co.za/jobs')
        services.save_vacancy(user=self.owner,organization_id=self.org.pk,vacancy_id=vacancy.pk,data={'external_destination':'https://agency.com/jobs'},requirements=[self.req('Python')])
        flag=self.org.risk_flags.get(code='application_domain_changed')
        self.assertEqual(flag.evidence,{'old_domain':'example.co.za','new_domain':'agency.com'})

    def test_no_fee_confirmation_required_for_publication(self):
        self.approved();vacancy=self.draft(no_candidate_fees=False)
        with self.assertRaises(ValidationError): services.publish_vacancy(user=self.owner,organization_id=self.org.pk,vacancy_id=vacancy.pk)
        vacancy.refresh_from_db();self.assertEqual(vacancy.status,'draft')

    def test_pending_protected_service_capabilities_denied(self):
        self.submit()
        for capability in ['view','pipeline','notes','documents','talent','talent_manage','contact','analytics','collaborate']:
            with self.subTest(capability=capability),self.assertRaises(PermissionDenied): membership_for(self.owner,self.org.pk,capability)
        vacancy=self.draft()
        with self.assertRaises(ValidationError): services.publish_vacancy(user=self.owner,organization_id=self.org.pk,vacancy_id=vacancy.pk)

    def test_pending_all_data_urls_denied(self):
        self.submit()
        for name in ['applicants','talent_search','talent_saved','talent_pools','analytics','tasks','assignments','activity','vacancies']:
            with self.subTest(name=name): self.assertEqual(self.client.get(self.url(name,self.org.pk)).status_code,403)

    def test_pending_status_profile_and_verification_available(self):
        self.submit()
        self.assertContains(self.client.get(self.url('dashboard',self.org.pk)),'Your company is being verified by CareerGPS.')
        self.assertEqual(self.client.get(self.url('verification',self.org.pk)).status_code,200)
        self.assertEqual(self.client.get(self.url('organization_settings',self.org.pk)).status_code,200)

    def test_pending_contact_and_saved_mutations_denied(self):
        self.submit()
        for function in [talent_services.reveal_contact,talent_services.save_candidate]:
            with self.assertRaises(PermissionDenied): function(user=self.owner,organization_id=self.org.pk,candidate_id=self.profile.pk)

    def test_rejected_and_suspended_protected_urls_denied(self):
        self.org.verification_status='rejected';self.org.save()
        self.assertEqual(self.client.get(self.url('talent_search',self.org.pk)).status_code,403)
        self.suspend()
        self.assertEqual(self.client.get(self.url('talent_search',self.org.pk)).status_code,404)
        self.assertEqual(self.client.get(self.url('analytics',self.org.pk)).status_code,404)

    def test_stale_membership_cannot_bypass_direct_data_services(self):
        self.approved();member=membership_for(self.owner,self.org.pk)
        self.org.verification_status='submitted';self.org.save()
        with self.assertRaises(PermissionDenied): funnel(member)
        with self.assertRaises(PermissionDenied): eligible_profiles(member)

    def test_approved_active_existing_capabilities_work(self):
        self.approved();self.enable()
        vacancy=self.published()
        for name,args in [('talent_search',[self.org.pk]),('analytics',[self.org.pk]),('intelligence',[self.org.pk,vacancy.pk]),('dashboard',[self.org.pk])]:
            self.assertEqual(self.client.get(self.url(name,*args)).status_code,200)

    def test_suspend_retains_approval_and_adds_publication_hold(self):
        record=self.approved();vacancy=self.published();before=record.review_events.count()
        self.suspend();vacancy.refresh_from_db();record.refresh_from_db();self.org.refresh_from_db()
        self.assertEqual(self.org.verification_status,'approved')
        self.assertEqual(record.status,'approved')
        self.assertEqual(record.review_events.count(),before)
        self.assertTrue(vacancy.trust_hold)
        self.assertFalse(Opportunity.objects.discoverable().exists())

    def test_suspension_preserves_candidate_application_documents_history(self):
        self.approved();hiring,doc=self.submitted()
        events=list(hiring.stage_events.values());documents=list(hiring.submitted_documents.values())
        self.suspend();hiring.refresh_from_db()
        self.assertEqual(list(hiring.stage_events.values()),events)
        self.assertEqual(list(hiring.submitted_documents.values()),documents)
        self.client.force_login(self.candidate)
        self.assertEqual(self.client.get(reverse('applications:workspace',args=[hiring.application_id])).status_code,200)

    def test_suspension_blocks_new_application_and_employer_applicant(self):
        self.approved();hiring,doc=self.submitted();self.suspend()
        self.assertEqual(self.client.get(self.url('applicant',self.org.pk,hiring.pk)).status_code,404)
        self.client.force_login(self.candidate)
        with self.assertRaises(Http404): services.submit_application(user=self.candidate,application_id=hiring.application_id,document_ids=[doc.pk],consent=True)

    def test_suspended_dashboard_and_documents_safe(self):
        self.approved();self.suspend()
        response=self.client.get(self.url('dashboard',self.org.pk))
        self.assertContains(response,'is suspended')
        self.assertNotContains(response,'PRIVATE-PLATFORM-REASON')
        self.assertNotContains(response,'Total current applicants')

    def test_restore_requires_separate_publication_release(self):
        self.approved();vacancy=self.published();self.suspend();self.restore()
        self.assertFalse(Opportunity.objects.discoverable().exists())
        with self.assertRaises(ValidationError): services.publish_vacancy(user=self.owner,organization_id=self.org.pk,vacancy_id=vacancy.pk)
        trust.release_vacancy(actor=self.reviewer,vacancy_id=vacancy.pk,reason='Checked retained publication')
        self.assertTrue(Opportunity.objects.discoverable().filter(pk=vacancy.opportunity_id).exists())

    def test_legacy_contact_recheck_blocks_until_new_review_and_separate_job_release(self):
        historical=self.approved()
        vacancy=self.published()
        flag=OrganizationRiskFlag.objects.create(organization=self.org,
            code='independent_contact_recheck_required',scope='organization',
            severity='high_risk',evidence={'reason':'Historical contact check absent'})
        vacancy.trust_hold=True;vacancy.save(update_fields=['trust_hold'])
        self.org.verification_status='more_info_required'
        self.org.save(update_fields=['verification_status'])
        self.assertFalse(organization_can_use_employer_platform(self.org))
        self.assertFalse(Opportunity.objects.discoverable().filter(pk=vacancy.opportunity_id).exists())
        self.assertContains(self.client.get(self.url('verification',self.org.pk)),
            'CareerGPS needs to re-check your company contact')
        fresh=self.submit()
        self.review(fresh,'begin_review')
        self.review(fresh,'registry_checked',source='Official register',reference='Recheck 42')
        with self.assertRaises(ValidationError): self.review(fresh,'approve')
        self.review(fresh,'independent_contact_confirmed',contact_source='Published switchboard',
            contact_reference='Official site',contact_method='phone',contact_value='+27 11 555 0100')
        self.review(fresh,'approve')
        self.org.refresh_from_db();flag.refresh_from_db();vacancy.refresh_from_db();historical.refresh_from_db()
        self.assertEqual(historical.status,'approved')
        self.assertTrue(organization_can_use_employer_platform(self.org))
        self.assertIsNotNone(flag.resolved_at)
        self.assertTrue(vacancy.trust_hold)
        self.assertFalse(Opportunity.objects.discoverable().filter(pk=vacancy.opportunity_id).exists())

    def test_restore_does_not_approve_rejected_org(self):
        self.org.verification_status='rejected';self.org.save();self.suspend();self.restore();self.org.refresh_from_db()
        self.assertFalse(organization_can_use_employer_platform(self.org))
        self.assertEqual(self.org.verification_status,'rejected')

    def test_ordinary_updates_cannot_bypass_suspension(self):
        self.approved();vacancy=self.published();self.suspend()
        with self.assertRaises(Http404): services.save_vacancy(user=self.owner,organization_id=self.org.pk,vacancy_id=vacancy.pk,data={'title':'Changed'},requirements=[])
        with self.assertRaises(Http404): services.update_organization(user=self.owner,organization_id=self.org.pk,data={'verification_status':'approved','account_status':'active'})

    def test_publication_release_preserves_opportunity_suppression(self):
        self.approved();vacancy=self.published();vacancy.opportunity.moderation_status='suppressed';vacancy.opportunity.save()
        self.suspend();self.restore()
        trust.release_vacancy(actor=self.reviewer,vacancy_id=vacancy.pk,reason='Account review done')
        self.assertFalse(Opportunity.objects.discoverable().exists())

    def test_registration_change_requires_review(self):
        old=self.approved();new=self.submit(registration_number='2026/999999/07')
        self.org.refresh_from_db();old.refresh_from_db()
        self.assertEqual(self.org.verification_status,'submitted')
        self.assertEqual(old.registration_number,'2026/123456/07')
        self.assertEqual(old.status,'approved')
        self.assertTrue(self.org.risk_flags.filter(code='identity_changed_after_approval').exists())

    def test_legal_name_type_country_domain_changes_require_review(self):
        for field,value in [('legal_name','Changed Legal Name'),('organization_type','nonprofit'),('country','Namibia'),('website','https://newcompany.com')]:
            with self.subTest(field=field):
                self.org.verification_status='draft';self.org.save()
                self.approved()
                data={field:value}
                if field=='country': data['organization_type']='foreign'
                # A separate object provides independent attempt budgets per subcase.
                EmployerAbuseCounter.objects.all().delete()
                self.submit(**data)
                self.org.refresh_from_db();self.assertEqual(self.org.verification_status,'submitted')

    def test_profile_primary_domain_change_pauses_approval(self):
        self.approved();vacancy=self.published()
        services.update_organization(user=self.owner,organization_id=self.org.pk,data={'website':'https://newcompany.com'})
        self.org.refresh_from_db();vacancy.refresh_from_db()
        self.assertEqual(self.org.verification_status,'draft')
        self.assertTrue(vacancy.trust_hold)

    def test_description_and_cosmetic_name_edits_keep_approval(self):
        self.approved()
        services.update_organization(user=self.owner,organization_id=self.org.pk,data={'description':'New public copy','name':' Example Hiring '})
        self.org.refresh_from_db();self.assertEqual(self.org.verification_status,'approved')

    def test_website_path_change_same_domain_keeps_approval(self):
        self.approved()
        services.update_organization(user=self.owner,organization_id=self.org.pk,data={'website':'https://www.example.co.za/about'})
        self.org.refresh_from_db();self.assertEqual(self.org.verification_status,'approved')

    def test_invited_member_domain_signal_is_not_a_ban(self):
        self.approved()
        user=get_user_model().objects.create_user('agency-recruiter',email='person@agency.com')
        member=services.change_membership(user=self.owner,organization_id=self.org.pk,username=user.username,role='recruiter')
        self.assertEqual(member.status,'invited')
        self.assertEqual(member.domain_signal.email_domain,'agency.com')
        self.assertTrue(self.org.risk_flags.filter(code='member_email_domain_mismatch').exists())
        services.accept_invitation(user=user,membership_id=member.pk)
        self.assertEqual(membership_for(user,self.org.pk).role,'recruiter')

    def test_owner_email_change_flags_and_audits(self):
        self.owner.email='owner@example.co.za';self.owner.save();self.approved()
        self.owner.email='owner@gmail.com';self.owner.save()
        signal=MemberDomainSignal.objects.get(membership__user=self.owner,membership__organization=self.org)
        self.assertTrue(signal.free_email)
        self.assertTrue(self.org.risk_flags.filter(code='owner_identity_domain_changed').exists())
        self.assertTrue(self.org.audit_events.filter(event_type='owner_domain_changed').exists())

    def test_matching_member_email_domain_signal(self):
        self.owner.email='Owner@EXAMPLE.co.za';self.owner.save();self.approved()
        self.assertTrue(MemberDomainSignal.objects.get(membership__user=self.owner,membership__organization=self.org).matches_organization)

    @override_settings(EMPLOYER_ABUSE_LIMITS={'organization_create':(1,86400)})
    def test_organization_creation_attempt_limit(self):
        services.create_organization(user=self.candidate,data={'name':'First'})
        with self.assertRaises(PermissionDenied): services.create_organization(user=self.candidate,data={'name':'Second'})
        self.assertEqual(Organization.objects.filter(created_by=self.candidate).count(),1)

    @override_settings(EMPLOYER_ABUSE_LIMITS={'organization_create':(1,86400)})
    def test_invalid_creation_attempt_consumes_budget(self):
        with self.assertRaises(ValidationError): services.create_organization(user=self.candidate,data={'name':''})
        with self.assertRaises(PermissionDenied): services.create_organization(user=self.candidate,data={'name':'Valid after invalid'})

    @override_settings(EMPLOYER_ABUSE_LIMITS={'verification_submit':(2,86400)})
    def test_verification_resubmission_cooldown(self):
        self.submit()
        with self.assertRaises(ValidationError): self.submit()
        with self.assertRaises(PermissionDenied): self.submit()

    @override_settings(EMPLOYER_ABUSE_LIMITS={'discovery':(1,60)})
    def test_discovery_request_limit_shared_between_members(self):
        self.approved()
        self.assertEqual(self.client.get(self.url('talent_search',self.org.pk)).status_code,200)
        user=self.member('recruiter');self.client.force_login(user)
        self.assertEqual(self.client.get(self.url('talent_search',self.org.pk)).status_code,403)

    @override_settings(EMPLOYER_ABUSE_LIMITS={'vacancy_publish':(1,3600)})
    def test_publish_burst_limit(self):
        self.approved();vacancy=self.published()
        with self.assertRaises(PermissionDenied): services.publish_vacancy(user=self.owner,organization_id=self.org.pk,vacancy_id=vacancy.pk)

    @override_settings(EMPLOYER_ABUSE_LIMITS={'contact_reveal':(1,3600)})
    def test_contact_reveal_budget_applies_to_service(self):
        self.approved();vacancy=self.published();self.enable(allow_contact_reveal=True)
        kwargs={'user':self.owner,'organization_id':self.org.pk,'candidate_id':self.profile.pk,'vacancy_id':vacancy.pk,'reason':'vacancy_review'}
        talent_services.reveal_contact(**kwargs)
        with self.assertRaises(PermissionDenied): talent_services.reveal_contact(**kwargs)

    @override_settings(EMPLOYER_ABUSE_LIMITS={'discovery':(1,60)})
    def test_database_counter_expiry(self):
        consume('discovery','test-subject')
        with self.assertRaises(PermissionDenied): consume('discovery','test-subject')
        EmployerAbuseCounter.objects.filter(key='discovery:test-subject').update(window_started_at=timezone.now()-timedelta(seconds=61))
        consume('discovery','test-subject')
        self.assertEqual(EmployerAbuseCounter.objects.get(key='discovery:test-subject').attempts,1)

    @override_settings(EMPLOYER_ABUSE_LIMITS={'discovery':(1,60)})
    def test_counter_subjects_independent(self):
        consume('discovery','one');consume('discovery','two')
        self.assertEqual(EmployerAbuseCounter.objects.filter(key__startswith='discovery:').count(),2)

    @override_settings(EMPLOYER_PUBLICATION_REVIEW_THRESHOLD=2)
    def test_newly_approved_publication_velocity_flag_not_suspension(self):
        self.approved();self.published();self.published(title='Second vacancy')
        self.assertTrue(self.org.risk_flags.filter(code='high_vacancy_velocity').exists())
        self.org.refresh_from_db();self.assertEqual(self.org.account_status,'active')

    @override_settings(EMPLOYER_PUBLICATION_REVIEW_THRESHOLD=2)
    def test_repeated_same_publication_not_distinct_velocity(self):
        self.approved();vacancy=self.published()
        services.publish_vacancy(user=self.owner,organization_id=self.org.pk,vacancy_id=vacancy.pk)
        self.assertFalse(self.org.risk_flags.filter(code='high_vacancy_velocity').exists())

    def report(self,vacancy,reason='scam',user=None):
        return submit_report(reporter=user or self.candidate,target=vacancy.opportunity,reason=reason,details='PRIVATE-REPORT-DETAILS')

    def test_distinct_serious_reports_flag_without_fraud_verdict(self):
        self.approved();vacancy=self.published()
        for i in range(3): self.report(vacancy,user=get_user_model().objects.create_user('reporter'+str(i)))
        flag=self.org.risk_flags.get(code='multiple_serious_reports')
        self.assertEqual(flag.evidence['distinct_reporters'],3)
        self.assertEqual(flag.severity,'high_risk')
        self.org.refresh_from_db();self.assertEqual(self.org.account_status,'active')
        self.assertNotIn('reporter0',str(flag.evidence))

    def test_one_reporter_multiple_vacancies_not_multiple_people(self):
        self.approved()
        for i in range(3): self.report(self.published(title='Vacancy '+str(i)))
        self.assertFalse(self.org.risk_flags.filter(code='multiple_serious_reports').exists())

    def test_fee_report_high_risk_and_existing_moderation_suppression(self):
        self.approved();vacancy=self.published();report=self.report(vacancy,'money')
        self.assertTrue(self.org.risk_flags.filter(code='candidate_fee_report',severity='high_risk').exists())
        self.reviewer.user_permissions.add(Permission.objects.get(codename='change_opportunityreport'),Permission.objects.get(codename='change_opportunity'))
        for attr in ['_perm_cache','_user_perm_cache','_group_perm_cache']:
            self.reviewer.__dict__.pop(attr,None)
        resolve_report(report=report,actor=self.reviewer,action='remove',note='Investigated fee report')
        self.assertFalse(Opportunity.objects.discoverable().exists())

    def test_report_flag_refresh_deduplicates(self):
        self.approved();report=self.report(self.published(),'money')
        trust.report_signals(report);trust.report_signals(report)
        self.assertEqual(self.org.risk_flags.filter(code='candidate_fee_report').count(),1)

    def test_reporters_and_internal_risk_hidden_from_employer(self):
        self.approved();report=self.report(self.published(),'money')
        for name in ['dashboard','verification']:
            response=self.client.get(self.url(name,self.org.pk))
            self.assertNotContains(response,'PRIVATE-REPORT-DETAILS')
            self.assertNotContains(response,'candidate_fee_report')
            self.assertNotContains(response,self.candidate.username)

    def test_non_serious_reports_no_serious_flag(self):
        self.approved();vacancy=self.published()
        for i in range(3): self.report(vacancy,'broken_link',get_user_model().objects.create_user('linkreporter'+str(i)))
        self.assertFalse(self.org.risk_flags.filter(code='multiple_serious_reports').exists())

    def test_flag_resolution_requires_admin_and_audits(self):
        self.submit();flag=self.org.risk_flags.first()
        trust.resolve_flag(actor=self.reviewer,flag_id=flag.pk,reason='Evidence checked')
        flag.refresh_from_db()
        self.assertEqual(flag.resolved_by,self.reviewer)
        self.assertIsNotNone(flag.resolved_at)
        self.assertTrue(self.org.audit_events.filter(event_type='risk_flag_resolved').exists())

    def test_pdf_private_download_owner_and_platform_only(self):
        upload=SimpleUploadedFile('registration.pdf',b'%PDF-1.4 test evidence',content_type='application/pdf')
        row=trust.submit_verification(user=self.owner,organization_id=self.org.pk,data=self.data,upload=upload)
        document=row.documents.get();url=self.url('verification_document',document.pk)
        response=self.client.get(url)
        self.assertEqual(response.status_code,200)
        self.assertIn('attachment',response['Content-Disposition'])
        self.assertEqual(response['X-Content-Type-Options'],'nosniff')
        self.assertEqual(response['Cache-Control'],'private, no-store')
        self.client.force_login(self.reviewer)
        self.assertEqual(self.client.get(url).status_code,200)
        for user in [self.candidate,self.other,self.member('recruiter')]:
            self.client.force_login(user)
            self.assertEqual(self.client.get(url).status_code,404)
        self.assertNotIn('%PDF',str(self.org.audit_events.values_list('context',flat=True)))

    def test_evidence_document_type_and_size_validation(self):
        for name,content in [('evidence.html',b'<html>bad</html>'),('fake.pdf',b'<script>alert(1)</script>'),('large.pdf',b'%PDF-'+b'x'*(5*1024*1024))]:
            with self.subTest(name=name),self.assertRaises(ValidationError):
                trust.submit_verification(user=self.owner,organization_id=self.org.pk,data=self.data,upload=SimpleUploadedFile(name,content))
        self.assertFalse(VerificationDocument.objects.exists())
        self.assertFalse(OrganizationVerification.objects.exists())

    def test_document_bytes_not_in_admin_edit_form(self):
        self.assertIn('content',admin.site._registry[VerificationDocument].exclude)

    def test_foreign_organization_verification_denied(self):
        self.submit();self.client.force_login(self.other)
        self.assertEqual(self.client.get(self.url('verification',self.org.pk)).status_code,404)
        with self.assertRaises(Http404): trust.submit_verification(user=self.other,organization_id=self.org.pk,data=self.data)

    def test_non_owner_cannot_submit_verification(self):
        user=self.member('recruiter');self.client.force_login(user)
        self.assertEqual(self.client.get(self.url('verification',self.org.pk)).status_code,403)
        with self.assertRaises(PermissionDenied): trust.submit_verification(user=user,organization_id=self.org.pk,data=self.data)

    def test_suspended_cannot_resubmit(self):
        self.approved();self.suspend()
        with self.assertRaises(PermissionDenied): self.submit()
        self.assertEqual(self.client.get(self.url('verification',self.org.pk)).status_code,200)

    def test_review_transitions_actor_timestamps_audit(self):
        row=self.approved()
        events=list(row.review_events.all())
        self.assertEqual([e.action for e in events],['verification_submitted','begin_review','registry_checked','independent_contact_confirmed','approve'])
        self.assertEqual(events[0].actor,self.owner)
        self.assertTrue(all(e.actor==self.reviewer for e in events[1:]))
        self.assertTrue(all(e.created_at for e in events))
        self.suspend();self.restore()
        self.assertTrue({'organization_suspended','organization_restored','verification_approve'}.issubset(set(self.org.audit_events.values_list('event_type',flat=True))))

    def test_admin_csrf_and_post_only_mutations(self):
        row=self.submit();flag=self.org.risk_flags.first()
        csrf=Client(enforce_csrf_checks=True);csrf.force_login(self.reviewer)
        self.assertEqual(csrf.post(self.url('verification_review',self.org.pk),{'action':'begin_review'}).status_code,403)
        url=self.url('verification_flag_resolve',self.org.pk,flag.pk)
        self.client.force_login(self.reviewer)
        self.assertEqual(self.client.get(url).status_code,405)
        self.assertEqual(csrf.post(url,{'reason':'Resolved'}).status_code,403)
        owner_csrf=Client(enforce_csrf_checks=True);owner_csrf.force_login(self.owner)
        self.assertEqual(owner_csrf.post(self.url('verification',self.org.pk),self.data).status_code,403)

    def test_admin_queue_correct_permission(self):
        self.submit();self.client.force_login(self.reviewer)
        self.assertEqual(self.client.get(reverse('admin:employers_organizationverification_changelist')).status_code,200)
        self.assertContains(self.client.get(self.url('verification_review',self.org.pk)),'Submitted information')

    def test_candidate_trust_indicator_factual_and_report_link(self):
        self.approved()
        vacancy = self.published(requirements=[
            self.req('Python'), self.req('BSc Computer Science', kind='qualification'),
        ])
        ProfileSkill.objects.create(profile=self.profile, entered_name='Python')
        Education.objects.create(profile=self.profile, qualification='BSc Computer Science')
        self.client.force_login(self.candidate)
        response=self.client.get(reverse('opportunity_detail',args=[vacancy.opportunity_id]))
        self.assertContains(response,'Employer approved by CareerGPS after review')
        self.assertContains(response,'Organization joined')
        self.assertContains(response,reverse('report_opportunity',args=[vacancy.opportunity_id]))
        self.assertNotContains(response,'Trust score')
        self.assertNotContains(response,'PRIVATE-')

    def test_unapproved_or_suspended_no_trust_badge(self):
        self.approved();vacancy=self.published();self.suspend()
        opportunity=Opportunity.objects.get(pk=vacancy.opportunity_id)
        self.assertIsNone(opportunity.employer_trust)

    def test_http_verification_vertical_slice(self):
        response=self.client.post(self.url('verification',self.org.pk),self.data)
        self.assertEqual(response.status_code,302)
        self.client.force_login(self.reviewer)
        response=self.client.post(self.url('verification_review',self.org.pk),
            {'action':'request_info','employer_message':'Provide more evidence','internal_note':'PRIVATE-NOTE'})
        self.assertEqual(response.status_code,302)
        self.client.force_login(self.owner)
        self.assertContains(self.client.get(self.url('verification',self.org.pk)),'Provide more evidence')
        self.assertEqual(self.client.post(self.url('verification',self.org.pk),self.data).status_code,302)
        self.client.force_login(self.reviewer)
        response=self.client.post(self.url('verification_review',self.org.pk),{'action':'verify'})
        self.assertEqual(response.status_code,302)
        self.client.force_login(self.owner)
        self.assertContains(self.client.get(self.url('dashboard',self.org.pk)),'Active vacancies')



@override_settings(PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'])
class OneClickVerifyTests(TestCase):
    """Three-action admin verification: inspect -> Verify / Decline / Message.

    No intermediate reviewer ceremony is exposed or required. Every decision
    still delegates to review_verification, so authorization, audit events,
    notifications and lifecycle safeguards remain identical to the previous
    workflow.
    """

    draft=m1.EmployerTests.draft
    req=m1.EmployerTests.req
    published=m1.EmployerTests.published
    document=m1.EmployerTests.document
    application=m1.EmployerTests.application
    submitted=m1.EmployerTests.submitted
    member=m1.EmployerTests.member
    url=m1.EmployerTests.url
    enable=m2.TalentTests.enable

    def setUp(self):
        m1.EmployerTests.setUp(self)
        self.org.verification_status='draft'
        self.org.save()
        self.reviewer=get_user_model().objects.create_user('platform-reviewer',is_staff=True,password='test')
        self.reviewer.user_permissions.add(Permission.objects.get(codename='review_employer'))
        self.data={'legal_name':'Example Hiring','organization_type':'sa_company','registration_number':'2026/123456/07',
            'country':'South Africa','website':'https://careers.example.co.za/jobs','business_email':'recruiter@example.co.za',
            'registry_source':'Claimed registry source','registry_reference':'Claimed reference','evidence_notes':'Submitted evidence'}

    def submit(self,**changes):
        return trust.submit_verification(user=self.owner,organization_id=self.org.pk,data={**self.data,**changes})

    def decide(self, action, **extra):
        self.client.force_login(self.reviewer)
        return self.client.post(self.url('verification_review',self.org.pk),{'action':action,**extra})

    # ---- Verify -------------------------------------------------------------

    def test_verify_is_one_action_with_no_prerequisite_steps(self):
        row=self.submit()
        response=self.decide('verify')
        self.assertEqual(response.status_code,302)
        self.org.refresh_from_db()
        row.refresh_from_db()
        self.assertEqual(self.org.verification_status,'approved')
        self.assertEqual(row.status,'approved')
        self.assertEqual(row.reviewer,self.reviewer)
        self.assertIsNotNone(row.review_started_at)
        self.assertIsNotNone(row.reviewed_at)
        self.assertIsNotNone(self.org.approved_at)
        self.assertTrue(organization_can_use_employer_platform(self.org))
        self.assertEqual(list(row.review_events.values_list('action',flat=True)),
            ['verification_submitted','begin_review','registry_checked','independent_contact_confirmed','approve'])
        self.assertIn('verification_approve',set(self.org.audit_events.values_list('event_type',flat=True)))

    def test_verify_preserves_actors_and_timestamps(self):
        row=self.submit()
        self.decide('verify')
        row.refresh_from_db()
        events=list(row.review_events.all())
        self.assertEqual(events[0].actor,self.owner)
        self.assertTrue(all(e.actor==self.reviewer for e in events[1:]))
        self.assertTrue(all(e.created_at for e in events))
        self.assertIsNotNone(row.registry_checked_at)
        self.assertEqual(row.independent_contact_confirmed_by,self.reviewer)

    def test_verify_already_verified_is_blocked(self):
        row=self.submit()
        self.decide('verify')
        response=self.decide('verify')
        self.assertEqual(response.status_code,200)
        self.assertContains(response,'already verified')
        self.org.refresh_from_db()
        self.assertEqual(self.org.verification_status,'approved')

    # ---- Decline ------------------------------------------------------------

    def test_decline_is_one_action_with_message(self):
        row=self.submit()
        response=self.decide('decline',employer_message='Registration details could not be confirmed.')
        self.assertEqual(response.status_code,302)
        self.org.refresh_from_db()
        row.refresh_from_db()
        self.assertEqual(self.org.verification_status,'rejected')
        self.assertEqual(row.status,'rejected')
        self.assertIn('reject',set(row.review_events.values_list('action',flat=True)))
        self.assertEqual(row.employer_message,'Registration details could not be confirmed.')
        self.assertNotEqual(self.org.verification_status,'approved')

    def test_decline_requires_an_employer_visible_reason(self):
        row=self.submit()
        response=self.decide('decline')
        self.assertEqual(response.status_code,200)
        self.assertContains(response,'Give the employer a reason so they know why verification was declined.')
        self.org.refresh_from_db()
        row.refresh_from_db()
        self.assertEqual(row.status,'submitted')

    # ---- Request information ------------------------------------------------

    def test_request_information_is_one_action(self):
        row=self.submit()
        response=self.decide('request_info',employer_message='Send your company registration document and an official contact.',
            internal_note='PRIVATE-NOTE')
        self.assertEqual(response.status_code,302)
        self.org.refresh_from_db()
        row.refresh_from_db()
        self.assertEqual(self.org.verification_status,'more_info_required')
        self.assertEqual(row.status,'more_info_required')
        self.assertEqual(row.employer_message,'Send your company registration document and an official contact.')
        self.client.force_login(self.owner)
        page=self.client.get(self.url('dashboard',self.org.pk))
        self.assertContains(page,'Action required')
        self.assertContains(page,'Send your company registration document and an official contact.')

    def test_request_information_requires_a_message(self):
        row=self.submit()
        response=self.decide('request_info')
        self.assertEqual(response.status_code,200)
        self.assertContains(response,'Write the message the employer will see about what is missing.')
        self.org.refresh_from_db()
        self.assertEqual(self.org.verification_status,'submitted')

    def test_awaiting_employer_state_has_clear_decision_message(self):
        self.submit()
        self.decide('request_info',employer_message='Provide the managing director letter.')
        for action in ('verify','decline'):
            response=self.decide(action,employer_message='Reason')
            self.assertEqual(response.status_code,200)
            self.assertContains(response,'The employer has been asked for more information. Verify or decline once they respond.')
            self.org.refresh_from_db()
            self.assertEqual(self.org.verification_status,'more_info_required')

    def test_resubmission_then_single_click_verify(self):
        self.submit()
        self.decide('request_info',employer_message='Provide the managing director letter.')
        self.client.force_login(self.owner)
        self.assertEqual(self.client.post(self.url('verification',self.org.pk),self.data).status_code,302)
        response=self.decide('verify')
        self.assertEqual(response.status_code,302)
        self.org.refresh_from_db()
        self.assertEqual(self.org.verification_status,'approved')

    # ---- Screen -------------------------------------------------------------

    def test_review_screen_offers_three_actions_without_ceremony(self):
        row=self.submit()
        self.client.force_login(self.reviewer)
        response=self.client.get(self.url('verification_review',self.org.pk))
        self.assertContains(response,'Submitted information')
        self.assertContains(response,'What do you want to do?')
        self.assertContains(response,'Verify employer')
        self.assertContains(response,'Decline employer')
        self.assertContains(response,'Request missing information')
        for obsolete in ('Verification checklist','Cannot verify yet','Begin review',
            'Record review action','Other review actions','Registry / institutional evidence check'):
            self.assertNotContains(response,obsolete)

    def test_review_screen_after_verify_has_no_decision_form(self):
        self.submit()
        self.decide('verify')
        response=self.client.get(self.url('verification_review',self.org.pk))
        self.assertContains(response,'Your company is verified')
        self.assertContains(response,'No verification action is needed')
        self.assertNotContains(response,'class="verify-form"')

    # ---- Security -----------------------------------------------------------

    def test_unauthorized_users_cannot_perform_any_decision(self):
        self.submit()
        bystander=get_user_model().objects.create_user('bystander',password='test')
        for action,extra in (('verify',{}),('decline',{'employer_message':'No.'}),
                ('request_info',{'employer_message':'Send documents.'})):
            self.client.force_login(bystander)
            response=self.client.post(self.url('verification_review',self.org.pk),{'action':action,**extra})
            self.assertEqual(response.status_code,403,action)
        self.client.force_login(self.owner)
        response=self.client.post(self.url('verification_review',self.org.pk),{'action':'verify'})
        self.assertEqual(response.status_code,403)
        self.org.refresh_from_db()
        self.assertNotEqual(self.org.verification_status,'approved')

    # ---- Regression: employer-facing states ---------------------------------

    def test_employer_states_are_plain_language_end_to_end(self):
        self.submit()
        self.client.force_login(self.owner)
        self.assertContains(self.client.get(self.url('dashboard',self.org.pk)),
            'Your company is being verified by CareerGPS.')
        self.decide('request_info',employer_message='Provide the managing director letter.')
        self.client.force_login(self.owner)
        response=self.client.get(self.url('dashboard',self.org.pk))
        self.assertContains(response,'Action required')
        self.assertContains(response,'We need some additional information before we can verify your company.')
        self.assertContains(response,'Provide the managing director letter.')
        self.client.post(self.url('verification',self.org.pk),self.data)
        self.decide('verify')
        self.client.force_login(self.owner)
        response=self.client.get(self.url('company',self.org.pk))
        self.assertContains(response,'Verified')
        self.assertContains(response,'Your company is verified.')
