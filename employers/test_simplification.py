"""Focused contracts for the simplified employer surface and intake boundary."""
from datetime import timedelta
from io import BytesIO

from bs4 import BeautifulSoup
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone

from core.models import Opportunity
from core.test_support import activate_clean_opportunity
from . import tests as baseline
from .collaboration_services import assign, create_task
from .models import VacancyIntake, VacancyRequirement
from .presentation import vacancy_availability
from .vacancy_intake import read_vacancy_document


@override_settings(PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'],
    GEMINI_API_KEY='', OPPORTUNITY_AI_FALLBACK_API_KEY='')
class EmployerSimplificationTests(baseline.TestCase):
    # Reuse the established organisation/candidate fixtures, not the test methods.
    setUp = baseline.EmployerTests.setUp
    draft = baseline.EmployerTests.draft
    req = baseline.EmployerTests.req
    published = baseline.EmployerTests.published
    document = baseline.EmployerTests.document
    application = baseline.EmployerTests.application
    submitted = baseline.EmployerTests.submitted
    member = baseline.EmployerTests.member
    url = baseline.EmployerTests.url

    def published(self, **kwargs):
        vacancy = baseline.EmployerTests.published(self, **kwargs)
        if not vacancy.opportunity.active_clean_revision_id:
            activate_clean_opportunity(vacancy.opportunity)
            vacancy.opportunity.refresh_from_db()
        return vacancy

    VACANCY_TEXT = ('Job title: Data Analyst\nLocation: Cape Town\n'
        'Responsibilities:\nBuild reports.\nRequirements:\n- Python\n'
        '- 2 years experience in data analysis\nPreferred:\n- SQL\n')

    def test_four_primary_areas_and_contextual_routes(self):
        response = self.client.get(self.url('dashboard', self.org.pk))
        nav = BeautifulSoup(response.content, 'html.parser').select_one('nav[aria-label="Employer navigation"]')
        self.assertEqual([link.get_text(strip=True) for link in nav.select('a')],
            ['Jobs', 'Applications', 'Talent', 'Company'])
        self.assertEqual(self.client.get(self.url('company', self.org.pk)).status_code, 200)
        for name in ('analytics', 'assignments', 'tasks', 'activity', 'team', 'verification'):
            self.assertEqual(self.client.get(self.url(name, self.org.pk)).status_code, 200)

    def test_effective_availability_is_not_saved_status(self):
        vacancy = self.published(closing_date=timezone.localdate() + timedelta(days=3))
        self.assertEqual(vacancy_availability(vacancy)['label'], 'Closing soon')
        self.assertEqual(vacancy_availability(vacancy, today=timezone.localdate() + timedelta(days=5),
            discoverable_ids={vacancy.opportunity_id})['label'], 'Closed')
        vacancy.trust_hold = True
        self.assertEqual(vacancy_availability(vacancy)['group'], 'hold')
        vacancy.trust_hold = False
        vacancy.opportunity.lifecycle_status = 'removed'
        self.assertEqual(vacancy_availability(vacancy)['label'], 'Closed')
        vacancy.status = 'draft'
        self.assertEqual(vacancy_availability(vacancy)['label'], 'Draft')

    def _review_post(self, intake):
        return {'title': 'Data Analyst', 'location': 'Cape Town', 'country': 'South Africa',
            'description': intake.source_text, 'opportunity_kind': 'job', 'career_stage': 'unknown',
            'no_candidate_fees': 'on', 'confirm_facts': 'on', 'confirm_requirements': 'on',
            'requirements-TOTAL_FORMS': '3', 'requirements-INITIAL_FORMS': '3',
            'requirements-0-criterion_type': 'skill', 'requirements-0-requirement_level': 'required',
            'requirements-0-raw_value': 'Python', 'requirements-0-proposal_index': '0',
            'requirements-1-criterion_type': 'experience', 'requirements-1-requirement_level': 'required',
            'requirements-1-raw_value': '2 years experience in data analysis', 'requirements-1-minimum_years': '2',
            'requirements-1-proposal_index': '1',
            'requirements-2-criterion_type': 'skill', 'requirements-2-requirement_level': 'preferred',
            'requirements-2-raw_value': 'SQL', 'requirements-2-proposal_index': '2'}

    def test_paste_requires_employer_confirmation_before_publication(self):
        response = self.client.post(self.url('vacancy_create', self.org.pk), {'vacancy_text': self.VACANCY_TEXT})
        self.assertEqual(response.status_code, 302)
        intake = VacancyIntake.objects.get()
        self.assertIsNone(intake.confirmed_at)
        self.assertFalse(self.org.vacancies.exists())
        self.assertEqual([row['requirement_level'] for row in intake.proposed_requirements],
            ['required', 'required', 'preferred'])
        review = self.url('vacancy_review', self.org.pk, intake.pk)
        incomplete = self._review_post(intake)
        incomplete.pop('confirm_requirements')
        self.assertEqual(self.client.post(review, incomplete).status_code, 200)
        self.assertFalse(self.org.vacancies.exists())
        confirmed = self.client.post(review, self._review_post(intake))
        self.assertEqual(confirmed.status_code, 302, getattr(confirmed, 'context', None))
        intake.refresh_from_db()
        self.assertIsNotNone(intake.confirmed_at)
        self.assertEqual(intake.vacancy.requirements.count(), 3)
        self.assertTrue(all(row.origin == 'interpreted_confirmed' for row in intake.vacancy.requirements.all()))
        self.assertTrue(all(row.source_excerpt for row in intake.vacancy.requirements.all()))
        self.assertEqual(intake.vacancy.status, 'draft')
        self.client.post(self.url('vacancy_publish', self.org.pk, intake.vacancy_id))
        intake.vacancy.refresh_from_db()
        self.assertEqual(intake.vacancy.status, 'published')
        self.assertEqual(intake.vacancy.opportunity.source_platform, 'CareerGPS Direct')
        self.assertEqual([row['requirement_level'] for row in intake.vacancy.published_requirements],
            ['required', 'required', 'preferred'])
        self.assertTrue(all(row['metadata']['source_excerpt'] for row in intake.vacancy.published_requirements))

    def test_document_text_extraction_and_safe_failure(self):
        text = self.VACANCY_TEXT.encode()
        self.assertIn('Data Analyst', read_vacancy_document(SimpleUploadedFile('job.txt', text))[0])
        from docx import Document
        document = Document(); document.add_paragraph(self.VACANCY_TEXT)
        stream = BytesIO(); document.save(stream)
        self.assertIn('Data Analyst', read_vacancy_document(SimpleUploadedFile('job.docx', stream.getvalue()))[0])
        from reportlab.pdfgen import canvas
        pdf = BytesIO(); page = canvas.Canvas(pdf); page.drawString(50, 750, 'Job title: Data Analyst. Location: Cape Town. Required: Python.'); page.save()
        self.assertIn('Data Analyst', read_vacancy_document(SimpleUploadedFile('job.pdf', pdf.getvalue()))[0])
        uploaded = self.client.post(self.url('vacancy_create', self.org.pk),
            {'vacancy_document': SimpleUploadedFile('job.docx', stream.getvalue())})
        self.assertEqual(uploaded.status_code, 302)
        intake = VacancyIntake.objects.get()
        self.assertEqual(intake.source_filename, 'job.docx')
        self.assertIsNone(intake.confirmed_at)
        self.assertFalse(self.org.vacancies.exists())
        VacancyIntake.objects.all().delete()
        bad = self.client.post(self.url('vacancy_create', self.org.pk),
            {'vacancy_document': SimpleUploadedFile('bad.pdf', b'%PDF-broken')})
        self.assertEqual(bad.status_code, 200)
        self.assertContains(bad, 'Paste the vacancy text or use manual entry')
        self.assertFalse(VacancyIntake.objects.exists())

    def test_employer_can_reclassify_correct_remove_and_add_with_provenance(self):
        self.client.post(self.url('vacancy_create', self.org.pk), {'vacancy_text': self.VACANCY_TEXT})
        intake=VacancyIntake.objects.get()
        self.assertNotIn('closing_date', intake.proposed_facts)
        self.assertNotIn('work_arrangement', intake.proposed_facts)
        data=self._review_post(intake)
        data['requirements-TOTAL_FORMS']='4'
        data['requirements-0-requirement_level']='preferred'
        data['requirements-1-DELETE']='on'
        data['requirements-2-raw_value']='Advanced SQL'
        data.update({'requirements-3-criterion_type':'skill', 'requirements-3-requirement_level':'required',
            'requirements-3-raw_value':'Power BI'})
        response=self.client.post(self.url('vacancy_review',self.org.pk,intake.pk),data)
        self.assertEqual(response.status_code,302,getattr(response,'context',None))
        intake.refresh_from_db()
        rows={row.raw_value:row for row in VacancyRequirement.objects.filter(vacancy=intake.vacancy)}
        self.assertEqual(set(rows),{'Python','Advanced SQL','Power BI'})
        self.assertEqual(rows['Python'].requirement_level,'preferred')
        self.assertEqual(rows['Python'].origin,'employer_corrected')
        self.assertIn('Python',rows['Python'].source_excerpt)
        self.assertEqual(rows['Advanced SQL'].origin,'employer_corrected')
        self.assertEqual(rows['Power BI'].origin,'employer_authored')
        self.assertFalse(rows['Power BI'].source_excerpt)
        self.assertIsNone(intake.vacancy.closing_date)
        self.assertFalse(intake.vacancy.work_arrangement)

    def test_pending_company_keeps_confirmed_draft_but_cannot_publish(self):
        self.org.verification_status='submitted';self.org.save(update_fields=['verification_status'])
        self.assertEqual(self.client.post(self.url('vacancy_create',self.org.pk),
            {'vacancy_text':self.VACANCY_TEXT}).status_code,302)
        intake=VacancyIntake.objects.get()
        self.assertEqual(self.client.post(self.url('vacancy_review',self.org.pk,intake.pk),
            self._review_post(intake)).status_code,302)
        intake.refresh_from_db()
        self.assertEqual(intake.vacancy.status,'draft')
        self.assertIsNone(intake.vacancy.opportunity_id)
        self.client.post(self.url('vacancy_publish',self.org.pk,intake.vacancy_id))
        intake.vacancy.refresh_from_db()
        self.assertEqual(intake.vacancy.status,'draft')

    def test_closed_job_retains_submitted_history(self):
        vacancy=self.published()
        hiring,document=self.submitted(vacancy)
        self.client.post(self.url('vacancy_close',self.org.pk,vacancy.pk))
        self.assertEqual(self.client.get(self.url('job_detail',self.org.pk,vacancy.pk)).status_code,200)
        self.assertEqual(self.client.get(self.url('applicant',self.org.pk,hiring.pk)).status_code,200)
        self.assertTrue(hiring.submitted_documents.exists())
        self.assertTrue(hiring.stage_events.exists())

    def test_interviewer_scope_covers_list_detail_document_note_and_task(self):
        assigned, assigned_doc = self.submitted()
        unrelated, other_doc = self.submitted()
        interviewer = self.member('interviewer')
        membership = interviewer.employer_memberships.get()
        assign(user=self.owner, organization_id=self.org.pk, member_id=membership.pk,
            responsibility='application', hiring_id=assigned.pk)
        self.client.force_login(interviewer)
        page = self.client.get(self.url('applicants', self.org.pk))
        self.assertEqual(page.context['page'].paginator.count, 1)
        self.assertEqual(self.client.get(self.url('applicant', self.org.pk, assigned.pk)).status_code, 200)
        self.assertEqual(self.client.get(self.url('applicant', self.org.pk, unrelated.pk)).status_code, 404)
        self.assertEqual(self.client.get(self.url('document_download', self.org.pk,
            unrelated.submitted_documents.get().pk, 'pdf')).status_code, 404)
        self.assertEqual(self.client.post(self.url('note_add', self.org.pk, unrelated.pk),
            {'content': 'Unrelated'}).status_code, 404)
        self.client.force_login(self.owner)
        self.assertEqual(self.client.get(self.url('applicant', self.org.pk, unrelated.pk)).status_code, 200)
        task = create_task(user=self.owner, organization_id=self.org.pk, title='Review application',
            member_id=membership.pk, hiring_id=unrelated.pk)
        self.client.force_login(interviewer)
        self.assertEqual(self.client.get(self.url('applicant', self.org.pk, unrelated.pk)).status_code, 200)

    def test_talent_percentage_absent_and_application_sources_are_distinct(self):
        from .talent_services import set_visibility
        vacancy = self.published()
        set_visibility(user=self.candidate, data={'discoverable_by_employers': True})
        talent = self.client.get(self.url('talent_search', self.org.pk), {'vacancy': vacancy.pk})
        self.assertNotContains(talent, 'Evidence fit:')
        self.assertEqual(self.client.get(self.url('applicants', self.org.pk)).context['page'].paginator.count, 0)
        hiring, document = self.submitted(vacancy)
        application = self.client.get(self.url('applicant', self.org.pk, hiring.pk))
        self.assertContains(application, 'current confirmed profile evidence')
        self.assertContains(application, 'frozen CV and cover-letter versions')
