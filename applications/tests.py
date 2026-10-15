from tempfile import TemporaryDirectory
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core.files.base import ContentFile
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from core.models import Education, Opportunity, Profile, ProfileSkill, SavedOpportunity, Skill
from core.test_support import activate_clean_opportunity, create_current_evaluation
from ingestion.models import OpportunitySourceRecord

from applications.models import Application, GeneratedDocument
from applications.services.job_context import resolve_job_context


class ApplicationFlowTests(TestCase):
    def make_feed_eligible(self, user, opportunity):
        profile, _ = Profile.objects.get_or_create(user=user)
        Education.objects.get_or_create(profile=profile, qualification="BSc Computer Science")
        ProfileSkill.objects.get_or_create(profile=profile, entered_name="SQL")
        skill, _ = Skill.objects.get_or_create(name="SQL")
        opportunity.qualification_minimum = "BSc Computer Science"
        opportunity.save(update_fields=["qualification_minimum"])
        opportunity.required_skills.add(skill)
        if not opportunity.active_clean_revision_id:
            activate_clean_opportunity(opportunity)
            opportunity.refresh_from_db()
        if not opportunity.profile_evaluations.filter(profile=profile).exists():
            create_current_evaluation(profile, opportunity)

    def make_opportunity(self, **overrides):
        values = {
            "title": "Data Analyst",
            "company": "Example Bank",
            "opportunity_type": "entry_level",
            "location": "Johannesburg",
            "location_text": "Johannesburg, Gauteng",
            "description": "Short catalogue description.",
            "source_platform": "Sasol",
            "scope_status": "in_scope",
            "lifecycle_status": "active",
            "is_active": True,
        }
        values.update(overrides)
        return Opportunity.objects.create(**values)

    def attach_source(self, opportunity, *, code="sasol", completeness="partial_text"):
        revision = activate_clean_opportunity(opportunity, source_code=code)
        opportunity.refresh_from_db()
        return revision.source_record.source

    def test_partial_source_keeps_unknown_fields_unknown(self):
        opportunity = self.make_opportunity()
        self.attach_source(opportunity)
        context = resolve_job_context(opportunity)
        self.assertEqual(context["status"], "clean_revision_available")
        self.assertEqual(context["data"]["description"], "Short catalogue description.")
        self.assertEqual(context["data"]["skills"], [])

    def test_full_source_uses_only_catalogue(self):
        opportunity = self.make_opportunity()
        self.attach_source(opportunity, completeness="full_text")
        context = resolve_job_context(opportunity)
        self.assertEqual(context["status"], "clean_revision_available")
        self.assertEqual(context["data"]["description"], opportunity.description)

    def test_saved_resolver_requires_saved_opportunity_or_existing_application(self):
        opportunity = self.make_opportunity(source_platform="Sasol")
        self.attach_source(opportunity, code="sasol", completeness="full_text")
        user = User.objects.create_user("person", password="pass12345")
        self.client.login(username="person", password="pass12345")

        url = f"/applications/resolve-saved/{opportunity.id}/"
        response = self.client.post(url)
        self.assertEqual(response.status_code, 403)

        SavedOpportunity.objects.create(user=user, opportunity=opportunity)
        response = self.client.post(url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "clean_revision_available")

    def test_prepare_workspace_is_idempotent_and_apply_is_external(self):
        opportunity = self.make_opportunity()
        source = self.attach_source(opportunity)
        record = OpportunitySourceRecord.objects.get(opportunity=opportunity, source=source)
        record.apply_url = "https://jobs.example.com/position/123"
        record.source_url = record.apply_url
        record.save(update_fields=["apply_url"])
        activate_clean_opportunity(
            opportunity, source_code=source.code, source_url=record.apply_url)
        opportunity.refresh_from_db()
        user = User.objects.create_user("prepare-person", password="pass12345")
        self.make_feed_eligible(user, opportunity)
        self.client.login(username="prepare-person", password="pass12345")

        first = self.client.get(reverse("applications:start", args=[opportunity.id]))
        second = self.client.get(reverse("applications:start", args=[opportunity.id]))
        self.assertEqual(first.status_code, 302)
        self.assertEqual(second.url, first.url)
        self.assertEqual(Application.objects.filter(user=user, opportunity=opportunity).count(), 1)
        application = Application.objects.get(user=user, opportunity=opportunity)
        self.assertEqual(application.status, "planning")
        self.assertIsNone(application.applied_at)
        workspace = self.client.get(first.url)
        self.assertContains(workspace, "https://jobs.example.com/position/123")
        self.assertContains(workspace, "Candidate status:")
        self.assertContains(workspace, "Preparing")

    def test_explicit_candidate_status_and_closed_workspace_history(self):
        opportunity = self.make_opportunity()
        user = User.objects.create_user("status-person", password="pass12345")
        self.make_feed_eligible(user, opportunity)
        SavedOpportunity.objects.create(user=user, opportunity=opportunity)
        self.client.force_login(user)
        self.client.get(reverse("applications:start", args=[opportunity.pk]))
        application = Application.objects.get(user=user, opportunity=opportunity)
        application.notes = "Preparation notes retained"
        application.save(update_fields=["notes"])
        document = GeneratedDocument.objects.create(
            user=user, application=application, opportunity=opportunity, document_type="cv",
        )
        status_url = reverse("applications:update_status", args=[application.pk])
        self.assertIsNone(application.applied_at)
        self.client.get(reverse("applications:workspace", args=[application.pk]))
        application.refresh_from_db()
        self.assertEqual(application.status, "planning")
        self.client.post(status_url, {"status": "applied"})
        application.refresh_from_db()
        self.assertEqual(application.status, "applied")
        self.assertIsNotNone(application.applied_at)

        opportunity.is_active = False
        opportunity.lifecycle_status = "expired"
        opportunity.save(update_fields=["is_active", "lifecycle_status"])
        workspace = self.client.get(reverse("applications:workspace", args=[application.pk]))
        self.assertContains(workspace, "Opportunity closed")
        self.assertContains(workspace, "Candidate status:")
        self.assertContains(workspace, "<strong>Applied</strong>", html=True)
        self.assertContains(workspace, "Preparation notes retained")
        self.assertContains(workspace, reverse("applications:document_preview", args=[document.pk]))
        saved = self.client.get(reverse("saved"))
        self.assertContains(saved, reverse("applications:workspace", args=[application.pk]))
        self.assertNotContains(saved, reverse("applications:start", args=[opportunity.pk]))
        application.refresh_from_db()
        self.assertEqual(application.status, "applied")

        self.client.post(status_url, {"status": "withdrawn"})
        application.refresh_from_db()
        self.assertEqual(application.status, "withdrawn")
        self.assertContains(self.client.get(reverse("applications:workspace", args=[application.pk])),
                            "<strong>Withdrawn</strong>", html=True)

    def test_candidate_surfaces_keep_apply_and_prepare_distinct(self):
        opportunity = self.make_opportunity()
        source = self.attach_source(opportunity)
        record = OpportunitySourceRecord.objects.get(opportunity=opportunity, source=source)
        record.apply_url = "https://jobs.example.com/position/456"
        record.source_url = record.apply_url
        record.save(update_fields=["apply_url"])
        activate_clean_opportunity(
            opportunity, source_code=source.code, source_url=record.apply_url)
        opportunity.refresh_from_db()
        user = User.objects.create_user("surface-person", password="pass12345")
        Profile.objects.create(user=user, institution="", location="Johannesburg")
        self.make_feed_eligible(user, opportunity)
        SavedOpportunity.objects.create(user=user, opportunity=opportunity)
        self.client.force_login(user)

        for route in (
            reverse("dashboard"), reverse("opportunities"),
            reverse("opportunity_detail", args=[opportunity.pk]), reverse("saved"),
        ):
            with self.subTest(route=route):
                response = self.client.get(route)
                self.assertEqual(response.status_code, 200)
                self.assertContains(response, "Prepare application" if route == reverse("saved") else "Prepare")
                self.assertContains(response, "https://jobs.example.com/position/456")
                self.assertContains(response, "Apply on employer site" if route == reverse("saved") else "Apply ↗")
        self.assertFalse(Application.objects.filter(user=user, opportunity=opportunity).exists())

        self.client.get(reverse("applications:start", args=[opportunity.pk]))
        detail = self.client.get(reverse("opportunity_detail", args=[opportunity.pk]))
        self.assertContains(detail, "Continue preparation")
        application = Application.objects.get(user=user, opportunity=opportunity)
        self.assertEqual(application.status, "planning")
        self.assertIsNone(application.applied_at)

    def test_public_landing_exposes_no_raw_application_routes(self):
        trusted = self.make_opportunity(title="Trusted route")
        source = self.attach_source(trusted)
        record = OpportunitySourceRecord.objects.get(opportunity=trusted, source=source)
        record.apply_url = "https://jobs.example.com/position/789"
        record.save(update_fields=["apply_url"])
        self.make_opportunity(
            title="Untrusted route", primary_apply_url="https://aggregator.example/jobs/999",
        )

        response = self.client.get(reverse("landing"))
        self.assertNotContains(response, "https://jobs.example.com/position/789")
        self.assertNotContains(response, "https://aggregator.example/jobs/999")

    def test_untrusted_or_inactive_source_has_no_apply_link(self):
        opportunity = self.make_opportunity(primary_apply_url="https://aggregator.example/jobs/123")
        self.assertEqual(opportunity.external_application_url, "")
        source = self.attach_source(opportunity)
        record = OpportunitySourceRecord.objects.get(opportunity=opportunity, source=source)
        record.apply_url = "https://jobs.example.com/position/123"
        record.active_at_source = False
        record.save(update_fields=["apply_url", "active_at_source"])
        self.assertEqual(opportunity.external_application_url, "")
        user = User.objects.create_user("no-link-person", password="pass12345")
        Profile.objects.create(user=user, institution="", location="Johannesburg")
        self.make_feed_eligible(user, opportunity)
        self.client.force_login(user)
        detail = self.client.get(reverse("opportunity_detail", args=[opportunity.pk]))
        self.assertEqual(detail.status_code, 404)
        self.assertNotContains(detail, "Apply ↗", status_code=404)
        self.assertNotContains(
            detail,
            "https://aggregator.example/jobs/123",
            status_code=404,
        )

    def test_generate_cv_rejects_unsaved_opportunity_id(self):
        opportunity = self.make_opportunity(source_platform="Sasol")
        self.attach_source(opportunity, code="sasol", completeness="full_text")
        User.objects.create_user("cv-person", password="pass12345")
        self.client.login(username="cv-person", password="pass12345")

        response = self.client.post("/generate-cv/", {"opportunity_id": str(opportunity.id)})

        self.assertEqual(response.status_code, 403)



class GeneratedDocumentAccessTests(TestCase):
    def setUp(self):
        self.media = TemporaryDirectory()
        self.addCleanup(self.media.cleanup)
        media_settings = override_settings(MEDIA_ROOT=self.media.name)
        media_settings.enable()
        self.addCleanup(media_settings.disable)

        self.owner = User.objects.create_user("document-owner", password="pass12345")
        self.other = User.objects.create_user("document-other", password="pass12345")
        self.opportunity = Opportunity.objects.create(
            title="Private document role",
            company="Example Co",
            opportunity_type="entry_level",
            scope_status="in_scope",
            lifecycle_status="active",
            is_active=True,
        )
        self.application = Application.objects.create(
            user=self.owner,
            opportunity=self.opportunity,
            status="rejected",
        )
        self.document = GeneratedDocument.objects.create(
            user=self.owner,
            application=self.application,
            opportunity=self.opportunity,
            document_type="cv",
        )
        self.document.pdf_file.save("private.pdf", ContentFile(b"%PDF private"))
        self.document.docx_file.save("private.docx", ContentFile(b"PK private"))

    def test_owner_can_download_both_formats_with_private_headers(self):
        self.client.force_login(self.owner)
        for file_format, expected in (("pdf", b"%PDF private"), ("docx", b"PK private")):
            with self.subTest(file_format=file_format):
                response = self.client.get(
                    reverse("applications:document_download", args=[self.document.pk, file_format])
                )
                self.assertEqual(response.status_code, 200)
                self.assertEqual(b"".join(response.streaming_content), expected)
                self.assertEqual(response["Cache-Control"], "private, no-store")
                self.assertEqual(response["Pragma"], "no-cache")
                self.assertEqual(response["X-Content-Type-Options"], "nosniff")
                self.assertIn("attachment", response["Content-Disposition"])

    def test_other_user_and_anonymous_user_cannot_download(self):
        url = reverse("applications:document_download", args=[self.document.pk, "pdf"])
        self.client.force_login(self.other)
        self.assertEqual(self.client.get(url).status_code, 404)
        self.client.logout()
        self.assertEqual(self.client.get(url).status_code, 302)

    def test_direct_media_url_is_not_served_and_owner_access_survives_withdrawal(self):
        direct_url = self.document.pdf_file.url
        self.client.force_login(self.owner)
        self.assertEqual(self.client.get(direct_url).status_code, 404)
        self.application.status = "withdrawn"
        self.application.save(update_fields=["status", "updated_at"])
        protected_url = reverse(
            "applications:document_download",
            args=[self.document.pk, "pdf"],
        )
        response = self.client.get(protected_url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(b"".join(response.streaming_content), b"%PDF private")


class ResumeTemplateTests(TestCase):
    def sample_resume(self):
        return {
            "basics": {
                "name": "Test Person",
                "email": "test@example.com",
                "phone": "0123456789",
                "location": {"address": "Johannesburg"},
                "profiles": [],
            },
            "work": [
                {
                    "name": "Example Company",
                    "position": "Data Assistant",
                    "summary": "Worked with operational data.",
                    "highlights": ["Built reports", "Used SQL"],
                }
            ],
            "projects": [
                {
                    "name": "Dashboard Project",
                    "description": "Built a Power BI dashboard.",
                    "highlights": ["Analysed project data"],
                }
            ],
            "education": [
                {
                    "institution": "Example University",
                    "studyType": "BCom",
                    "area": "Information Systems",
                }
            ],
            "skills": [{"name": "SQL"}, {"name": "Power BI"}],
            "languages": [{"language": "English", "fluency": "Fluent"}],
            "certificates": [],
            "awards": [],
            "volunteer": [],
        }

    def sample_cv(self):
        return {
            "full_name": "Test Person",
            "summary": "Information systems student with data project experience.",
            "work_experience": [
                {
                    "title": "Data Assistant",
                    "company": "Example Company",
                    "dates": "2025",
                    "bullets": ["Built reports", "Used SQL"],
                }
            ],
            "key_projects": [
                {
                    "title": "Dashboard Project",
                    "description": "Built a Power BI dashboard.",
                    "dates": "2026",
                    "bullets": ["Analysed project data"],
                }
            ],
            "technical_skills": [{"category": "Skills", "items": ["SQL", "Power BI"]}],
        }

    def test_all_curated_templates_render_pdf(self):
        import os
        import tempfile

        from applications.services.resume_templates import public_template_options, render_resume_pdf

        with tempfile.TemporaryDirectory() as tmp:
            for template in public_template_options():
                target = os.path.join(tmp, f"{template['key']}.pdf")
                render_resume_pdf(
                    self.sample_resume(),
                    self.sample_cv(),
                    target,
                    template_key=template["key"],
                )
                self.assertTrue(os.path.exists(target))
                self.assertGreater(os.path.getsize(target), 1000)

    def test_invalid_template_key_fails_closed_to_default(self):
        from applications.services.resume_templates import normalize_template_key

        self.assertEqual(normalize_template_key("not-a-template"), "azurill")

    def test_cv_editor_and_layout_update_are_user_owned(self):
        import tempfile
        from django.core.files.base import ContentFile
        from django.test import override_settings
        from applications.models import GeneratedDocument

        owner = User.objects.create_user("cv-owner", password="pass12345")
        other = User.objects.create_user("cv-other", password="pass12345")

        with tempfile.TemporaryDirectory() as media_root, override_settings(MEDIA_ROOT=media_root):
            document = GeneratedDocument.objects.create(
                user=owner,
                document_type="cv",
                structured_data=self.sample_cv(),
                json_resume=self.sample_resume(),
                template_key="azurill",
                layout_settings={"hidden_sections": [], "density": "comfortable"},
                renderer_backend="careergps_rr_layout_adapter",
            )
            document.pdf_file.save("template-test.pdf", ContentFile(b"old"), save=True)

            self.client.login(username="cv-other", password="pass12345")
            denied = self.client.get(f"/applications/documents/{document.id}/edit/")
            self.assertEqual(denied.status_code, 404)

            self.client.logout()
            self.client.login(username="cv-owner", password="pass12345")
            editor = self.client.get(f"/applications/documents/{document.id}/edit/")
            self.assertEqual(editor.status_code, 200)
            self.assertContains(editor, "CV template editor")
            self.assertContains(
                editor,
                reverse("applications:document_download", args=[document.pk, "pdf"]),
            )
            self.assertNotContains(editor, document.pdf_file.url)

            response = self.client.post(
                f"/applications/documents/{document.id}/layout/",
                {
                    "template_key": "ditto",
                    "density": "compact",
                    "hidden_sections": ["languages", "awards"],
                },
            )
            self.assertEqual(response.status_code, 200)
            self.assertEqual(
                response.json()["pdf_url"],
                reverse("applications:document_download", args=[document.pk, "pdf"]),
            )
            document.refresh_from_db()
            self.assertEqual(document.template_key, "ditto")
            self.assertEqual(document.layout_settings["density"], "compact")
            self.assertEqual(document.layout_settings["hidden_sections"], ["awards", "languages"])
            self.assertGreater(document.pdf_file.size, 1000)


    def test_long_two_column_cv_can_span_multiple_pages(self):
        import os
        import tempfile

        from applications.services.resume_templates import (
            render_resume_pdf,
        )

        resume = self.sample_resume()
        cv = self.sample_cv()

        # Enough sidebar content to make the old
        # one-row implementation exceed one page.
        skill_names = [
            f"Professional Skill {index}"
            for index in range(1, 81)
        ]

        resume["skills"] = [
            {
                "name": name
            }
            for name in skill_names
        ]

        cv["technical_skills"] = [
            {
                "category": "Skills",
                "items": skill_names,
            }
        ]

        # Enough main-column content to ensure that the
        # CV genuinely needs more than one page.
        cv["work_experience"] = [
            {
                "title": (
                    f"Supply Chain Role {index}"
                ),
                "company": (
                    f"Example Company {index}"
                ),
                "dates": "2024 - 2026",
                "bullets": [
                    (
                        "Coordinated operational activities "
                        "across suppliers, warehouse teams "
                        "and internal stakeholders."
                    ),
                    (
                        "Monitored inventory movements and "
                        "followed up on delivery issues."
                    ),
                    (
                        "Maintained accurate operational "
                        "records and reporting."
                    ),
                    (
                        "Supported day-to-day procurement "
                        "and logistics administration."
                    ),
                ],
            }
            for index in range(1, 16)
        ]

        with tempfile.TemporaryDirectory() as tmp:

            # These are the two templates that previously
            # placed the whole CV inside one Table row.
            for template_key in (
                "azurill",
                "bronzor",
            ):
                target = os.path.join(
                    tmp,
                    f"long-{template_key}.pdf",
                )

                render_resume_pdf(
                    resume,
                    cv,
                    target,
                    template_key=(
                        template_key
                    ),
                )

                self.assertTrue(
                    os.path.exists(
                        target
                    )
                )

                self.assertGreater(
                    os.path.getsize(
                        target
                    ),
                    1000,
                )


class GeneratedDocumentEditingTests(TestCase):
    def setUp(self):
        self.media = TemporaryDirectory()
        self.addCleanup(self.media.cleanup)
        media_settings = override_settings(MEDIA_ROOT=self.media.name)
        media_settings.enable()
        self.addCleanup(media_settings.disable)

        from core.models import Profile, WorkExperience
        from core.views import (
            _build_cover_letter_docx, _build_cover_letter_pdf, _build_docx,
        )
        from applications.services.resume_templates import render_resume_pdf

        self.owner = User.objects.create_user("edit-owner", password="pass12345")
        self.other = User.objects.create_user("edit-other", password="pass12345")
        self.staff = User.objects.create_user(
            "edit-staff", password="pass12345", is_staff=True
        )
        self.profile = Profile.objects.create(
            user=self.owner, qualification="Diploma", institution="College",
            location="Cape Town",
        )
        self.evidence = WorkExperience.objects.create(
            profile=self.profile,
            title="Data Assistant",
            organisation="Source Employer",
            description="Original candidate evidence.",
        )
        self.opportunity = Opportunity.objects.create(
            title="Editable document role", company="Example Co",
            opportunity_type="entry_level", location="Cape Town",
            description="Role description", scope_status="in_scope",
        )
        self.application = Application.objects.create(
            user=self.owner, opportunity=self.opportunity
        )
        self.resume = {
            "basics": {"name": "Edit Owner", "email": "owner@example.test", "phone": "123", "location": {"address": "Cape Town"}, "profiles": []},
            "work": [{"name": "Source Employer", "position": "Data Assistant", "summary": "Old work summary", "highlights": ["Old work bullet"], "x_careergps_fact_id": f"work:{self.evidence.pk}"}],
            "projects": [{"name": "Stock Project", "description": "Old project", "highlights": ["Old project bullet"]}],
            "education": [{"institution": "College", "studyType": "Diploma", "area": "Data"}],
            "skills": [{"name": "SQL"}],
            "languages": [{"language": "English", "fluency": "Fluent"}],
            "certificates": [{"name": "Data Certificate", "issuer": "Institute", "date": "2025"}],
            "awards": [{"title": "Student Award", "awarder": "College", "date": "2024", "summary": "Old achievement"}],
            "volunteer": [{"position": "Volunteer", "organization": "Community", "summary": "Old volunteering"}],
        }
        self.cv_data = {
            "full_name": "Edit Owner", "email": "owner@example.test", "phone": "123", "location": "Cape Town",
            "summary": "Old summary",
            "work_experience": [{"title": "Data Assistant", "company": "Source Employer", "dates": "2024", "bullets": ["Old work bullet"]}],
            "key_projects": [{"title": "Stock Project", "organisation": "College", "dates": "2025", "description": "Old project", "bullets": ["Old project bullet"]}],
            "education": [{"qualification": "Diploma Data", "institution": "College", "dates": "2023 - 2025"}],
            "certifications": [{"title": "Data Certificate - Institute", "dates": "2025"}],
            "technical_skills": [{"category": "Skills", "items": ["SQL"]}],
            "soft_skills": [],
        }
        self.letter_data = {
            "date": "15 September 2026", "recipient_name": "Hiring Manager",
            "company_name": "Example Co", "location": "Cape Town",
            "recipient_line": "Hiring Manager: Example Co",
            "salutation": "Dear Hiring Manager,", "opening": "Old opening.",
            "body_paragraphs": ["Old body one.", "Old body two."],
            "closing": "Old closing.", "sign_off": "Kind regards,",
            "signature_name": "Edit Owner",
        }

        self.cv = GeneratedDocument.objects.create(
            user=self.owner, application=self.application, opportunity=self.opportunity,
            document_type="cv", structured_data=self.cv_data,
            json_resume=self.resume, candidate_fact_ids=[f"work:{self.evidence.pk}"],
            template_key="bronzor",
            layout_settings={"density": "compact", "hidden_sections": []},
        )
        self.cv.pdf_file.save("editable-cv.pdf", ContentFile(b"initial"))
        self.cv.docx_file.save("editable-cv.docx", ContentFile(b"initial"))
        render_resume_pdf(
            self.resume, self.cv_data, self.cv.pdf_file.path,
            template_key=self.cv.template_key, layout_settings=self.cv.layout_settings,
        )
        _build_docx(
            self.cv_data, self.cv.docx_file.path, json_resume=self.resume,
            layout_settings=self.cv.layout_settings,
        )

        self.cover = GeneratedDocument.objects.create(
            user=self.owner, application=self.application, opportunity=self.opportunity,
            document_type="cover_letter", structured_data=self.letter_data,
            candidate_fact_ids=[f"work:{self.evidence.pk}"],
        )
        self.cover.pdf_file.save("editable-cover.pdf", ContentFile(b"initial"))
        self.cover.docx_file.save("editable-cover.docx", ContentFile(b"initial"))
        _build_cover_letter_pdf(self.letter_data, self.cover.pdf_file.path)
        _build_cover_letter_docx(self.letter_data, self.cover.docx_file.path)
        self.client.force_login(self.owner)

    def cv_payload(self, **overrides):
        values = {
            "summary": "Edited professional summary.",
            "work_0_bullets": "Edited work bullet one.\nEdited work bullet two.",
            "project_0_description": "Edited project description.",
            "project_0_bullets": "Edited project bullet.",
            "award_0_summary": "Edited achievement description.",
            "volunteer_0_summary": "Edited volunteering description.",
            "certificate_0_display": "Edited certificate display",
            "skills_0_items": "SQL\nPower BI",
        }
        values.update(overrides)
        return values

    def cover_payload(self, **overrides):
        values = {
            "salutation": "Dear Recruitment Team,",
            "opening": "Edited opening.",
            "body_paragraphs": "Edited body one.\n\nEdited body two.",
            "closing": "Edited closing.",
            "sign_off": "Warm regards,",
            "signature_name": "Edit Owner",
        }
        values.update(overrides)
        return values

    def file_text(self, document):
        import pdfplumber
        from docx import Document
        with pdfplumber.open(document.pdf_file.path) as pdf:
            pdf_text = "\n".join(page.extract_text() or "" for page in pdf.pages)
        docx = Document(document.docx_file.path)
        docx_text = "\n".join(paragraph.text for paragraph in docx.paragraphs)
        return pdf_text, docx_text

    def test_owner_preview_uses_saved_state_layout_and_protected_downloads(self):
        response = self.client.get(reverse("applications:document_preview", args=[self.cv.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Old summary")
        self.assertContains(response, "Modern")
        self.assertContains(response, "Compact spacing")
        self.assertContains(response, reverse("applications:document_download", args=[self.cv.pk, "pdf"]))
        self.assertNotContains(response, self.cv.pdf_file.url)

        cover = self.client.get(reverse("applications:document_preview", args=[self.cover.pk]))
        self.assertContains(cover, "Dear Hiring Manager,")
        self.assertContains(cover, "Old body one.")

        workspace = self.client.get(
            reverse("applications:workspace", args=[self.application.pk])
        )
        self.assertContains(
            workspace, reverse("applications:document_preview", args=[self.cv.pk])
        )
        self.assertNotContains(workspace, self.cv.pdf_file.url)

    def test_cv_edit_updates_preview_pdf_docx_without_touching_evidence_or_ai(self):
        hostile = "</script><script>alert(1)</script> Edited summary."
        source_before = (self.profile.qualification, self.evidence.description)
        application_count = Application.objects.count()
        with patch("core.views.generate_cv_writing") as cv_ai, patch("core.views.generate_controlled_cover_letter") as cover_ai:
            response = self.client.post(
                reverse("applications:update_document_content", args=[self.cv.pk]),
                self.cv_payload(summary=hostile),
            )
        self.assertRedirects(response, reverse("applications:document_preview", args=[self.cv.pk]))
        cv_ai.assert_not_called()
        cover_ai.assert_not_called()

        self.cv.refresh_from_db()
        self.profile.refresh_from_db()
        self.evidence.refresh_from_db()
        self.assertEqual(self.cv.structured_data["summary"], hostile)
        self.assertEqual(self.cv.structured_data["work_experience"][0]["bullets"], ["Edited work bullet one.", "Edited work bullet two."])
        self.assertEqual(self.cv.json_resume["work"][0]["highlights"], ["Edited work bullet one.", "Edited work bullet two."])
        self.assertEqual(self.cv.template_key, "bronzor")
        self.assertEqual(self.cv.layout_settings["density"], "compact")
        self.assertEqual((self.profile.qualification, self.evidence.description), source_before)
        self.assertEqual(Application.objects.count(), application_count)

        preview = self.client.get(reverse("applications:document_preview", args=[self.cv.pk]))
        self.assertContains(preview, "&lt;/script&gt;&lt;script&gt;alert(1)&lt;/script&gt; Edited summary.")
        self.assertNotContains(preview, "<script>alert(1)</script>")
        pdf_text, docx_text = self.file_text(self.cv)
        for value in ("Edited summary.", "Edited work bullet two.", "Edited achievement description.", "Edited volunteering description.", "Edited certificate display", "Power BI"):
            self.assertIn(value, pdf_text)
            self.assertIn(value, docx_text)

    def test_cover_letter_edit_updates_preview_and_both_outputs_without_ai(self):
        source_before = self.evidence.description
        hostile = "</script><script>alert(1)</script> Edited opening."
        with patch("core.views.generate_cv_writing") as cv_ai, patch("core.views.generate_controlled_cover_letter") as cover_ai:
            response = self.client.post(
                reverse("applications:update_document_content", args=[self.cover.pk]),
                self.cover_payload(opening=hostile),
            )
        self.assertRedirects(response, reverse("applications:document_preview", args=[self.cover.pk]))
        cv_ai.assert_not_called()
        cover_ai.assert_not_called()
        self.cover.refresh_from_db()
        self.evidence.refresh_from_db()
        self.assertEqual(self.cover.structured_data["opening"], hostile)
        self.assertEqual(self.cover.structured_data["body_paragraphs"], ["Edited body one.", "Edited body two."])
        self.assertEqual(self.evidence.description, source_before)
        preview = self.client.get(reverse("applications:document_preview", args=[self.cover.pk]))
        self.assertNotContains(preview, "<script>alert(1)</script>")
        self.assertContains(preview, "Warm regards,")
        pdf_text, docx_text = self.file_text(self.cover)
        for value in ("Edited opening.", "Edited body two.", "Edited closing.", "Warm regards,"):
            self.assertIn(value, pdf_text)
            self.assertIn(value, docx_text)

    def test_text_and_layout_edits_preserve_each_other(self):
        self.client.post(
            reverse("applications:update_document_content", args=[self.cv.pk]),
            self.cv_payload(),
        )
        layout = self.client.post(
            reverse("applications:update_cv_layout", args=[self.cv.pk]),
            {"template_key": "ditto", "density": "comfortable", "hidden_sections": ["languages"]},
        )
        self.assertEqual(layout.status_code, 200)
        self.cv.refresh_from_db()
        self.assertEqual(self.cv.structured_data["summary"], "Edited professional summary.")
        self.assertEqual(self.cv.template_key, "ditto")

        self.client.post(
            reverse("applications:update_document_content", args=[self.cv.pk]),
            self.cv_payload(summary="Edited after layout."),
        )
        self.cv.refresh_from_db()
        self.assertEqual(self.cv.template_key, "ditto")
        self.assertEqual(self.cv.layout_settings["hidden_sections"], ["languages"])
        self.assertEqual(self.cv.structured_data["summary"], "Edited after layout.")

    def test_preview_edit_and_save_are_owner_only(self):
        from employers.models import Organization, OrganizationMembership
        employer = User.objects.create_user("edit-employer", password="pass12345")
        unrelated_employer = User.objects.create_user(
            "edit-unrelated-employer", password="pass12345"
        )
        for index, user in enumerate((employer, unrelated_employer), start=1):
            organization = Organization.objects.create(
                name=f"Editing Employer {index}", slug=f"editing-employer-{index}",
                created_by=user, verification_status="approved",
            )
            OrganizationMembership.objects.create(
                organization=organization, user=user, role="owner", status="active"
            )
        routes = (
            reverse("applications:document_preview", args=[self.cv.pk]),
            reverse("applications:document_edit", args=[self.cv.pk]),
        )
        for user in (self.other, self.staff, employer, unrelated_employer):
            self.client.force_login(user)
            for route in routes:
                self.assertEqual(self.client.get(route).status_code, 404)
            self.assertEqual(
                self.client.post(
                    reverse("applications:update_document_content", args=[self.cv.pk]),
                    self.cv_payload(),
                ).status_code,
                404,
            )
        self.client.logout()
        for route in routes:
            self.assertEqual(self.client.get(route).status_code, 302)
        self.assertEqual(
            self.client.post(
                reverse("applications:update_document_content", args=[self.cv.pk]),
                self.cv_payload(),
            ).status_code,
            302,
        )

    def test_frozen_submitted_cv_and_cover_letter_do_not_change(self):
        from employers.models import EmployerVacancy, HiringApplication, Organization, SubmittedDocument
        employer = User.objects.create_user("freeze-employer", password="pass12345")
        organization = Organization.objects.create(
            name="Freeze Employer", slug="freeze-employer", created_by=employer,
            verification_status="approved",
        )
        vacancy = EmployerVacancy.objects.create(
            organization=organization, opportunity=self.opportunity,
            title=self.opportunity.title, location="Cape Town",
            description="Role", status="published", created_by=employer,
        )
        hiring = HiringApplication.objects.create(
            application=self.application, vacancy=vacancy
        )
        frozen = []
        for document in (self.cv, self.cover):
            row = SubmittedDocument.objects.create(
                hiring_application=hiring, original_document=document,
                document_type=document.document_type,
                pdf_bytes=b"frozen-pdf-" + document.document_type.encode(),
                docx_bytes=b"frozen-docx-" + document.document_type.encode(),
            )
            frozen.append((row, bytes(row.pdf_bytes), bytes(row.docx_bytes)))

        self.client.post(reverse("applications:update_document_content", args=[self.cv.pk]), self.cv_payload())
        self.client.post(reverse("applications:update_document_content", args=[self.cover.pk]), self.cover_payload())
        for row, pdf_before, docx_before in frozen:
            row.refresh_from_db()
            self.assertEqual(bytes(row.pdf_bytes), pdf_before)
            self.assertEqual(bytes(row.docx_bytes), docx_before)

    def test_document_save_requires_csrf(self):
        csrf_client = Client(enforce_csrf_checks=True)
        csrf_client.force_login(self.owner)
        response = csrf_client.post(
            reverse("applications:update_document_content", args=[self.cv.pk]),
            self.cv_payload(),
        )
        self.assertEqual(response.status_code, 403)

    def test_invalid_saved_document_structure_is_rejected_without_replacing_files(self):
        with self.cv.pdf_file.open("rb") as handle:
            pdf_before = handle.read()
        with self.cv.docx_file.open("rb") as handle:
            docx_before = handle.read()
        self.cv.structured_data["work_experience"] = "invalid"
        self.cv.save(update_fields=["structured_data"])
        response = self.client.post(
            reverse("applications:update_document_content", args=[self.cv.pk]),
            self.cv_payload(),
        )
        self.assertEqual(response.status_code, 400)
        with self.cv.pdf_file.open("rb") as handle:
            self.assertEqual(handle.read(), pdf_before)
        with self.cv.docx_file.open("rb") as handle:
            self.assertEqual(handle.read(), docx_before)


@override_settings(GROQ_API_KEY="")
class GeminiDocumentRuntimeTests(TestCase):
    def test_document_interaction_keeps_client_alive_until_request_finishes(self):
        import google
        from types import SimpleNamespace
        from unittest.mock import patch
        from django.test import override_settings
        from CareerGPS_Rebuild.ai_gateway import GeminiAdapter, StructuredTask

        events = []

        class FakeInteractions:
            def __init__(self, owner):
                self.owner = owner

            def create(self, **kwargs):
                self.assert_owner_open()
                events.append("create")
                return SimpleNamespace(output_text='{"ok": true}')

            def assert_owner_open(self):
                if not self.owner.entered or self.owner.closed:
                    raise RuntimeError("Cannot send a request, as the client has been closed.")

        class FakeClient:
            def __init__(self, api_key, **kwargs):
                self.api_key = api_key
                self.entered = False
                self.closed = False
                self.interactions = FakeInteractions(self)
                events.append(f"init:{api_key}")

            def __enter__(self):
                self.entered = True
                events.append("enter")
                return self

            def __exit__(self, *args):
                self.closed = True
                events.append("exit")

        fake_genai = SimpleNamespace(Client=FakeClient)
        with patch.object(google, "genai", fake_genai, create=True), override_settings(
            DOCUMENT_AI_MODEL="test-model",
            DOCUMENT_AI_THINKING_LEVEL="low",
            DOCUMENT_AI_TIMEOUT_SECONDS=10,
        ):
            text = GeminiAdapter().run("primary-key", StructuredTask(
                name="cv_writing", prompt="prompt", system_instruction="system",
                schema={}, gemini_model="test-model", thinking_level="low",
                timeout_seconds=10,
            ))

        self.assertEqual(text, '{"ok": true}')
        self.assertEqual(events, ["init:primary-key", "enter", "create", "exit"])

    def test_document_generation_uses_fallback_key_only_for_retryable_provider_failure(self):
        from unittest.mock import patch
        from django.test import override_settings
        from applications.services import document_generation

        calls = []

        def fake_run(api_key, task):
            calls.append(api_key)
            if api_key == "primary-key":
                exc = RuntimeError("429 RESOURCE_EXHAUSTED quota")
                exc.status_code = 429
                raise exc
            return '{"ok": true}'

        with override_settings(
            GEMINI_API_KEY="primary-key",
            GEMINI_FALLBACK_API_KEY="fallback-key",
            OPPORTUNITY_AI_FALLBACK_API_KEY="fallback-key",
            OPENAI_API_KEY="",
        ), patch("CareerGPS_Rebuild.ai_gateway.GeminiAdapter.run", side_effect=fake_run):
            text = document_generation._interaction("prompt", {})

        self.assertEqual(text, '{"ok": true}')
        self.assertEqual(calls, ["primary-key", "fallback-key"])

    def test_document_generation_does_not_waste_fallback_on_bad_request(self):
        from unittest.mock import patch
        from django.test import override_settings
        from applications.services import document_generation

        calls = []

        def fake_run(api_key, task):
            calls.append(api_key)
            exc = RuntimeError("400 INVALID_ARGUMENT")
            exc.status_code = 400
            raise exc

        with override_settings(
            GEMINI_API_KEY="primary-key",
            GEMINI_FALLBACK_API_KEY="fallback-key",
            OPPORTUNITY_AI_FALLBACK_API_KEY="fallback-key",
            OPENAI_API_KEY="",
        ), patch("CareerGPS_Rebuild.ai_gateway.GeminiAdapter.run", side_effect=fake_run):
            with self.assertRaises(RuntimeError):
                document_generation._interaction("prompt", {})

        self.assertEqual(calls, ["primary-key"])


class DocumentNumericIntegrityRepairTests(TestCase):
    def candidate_bundle(self):
        return {
            "profile_id": 1,
            "qualification": "BCom Finance",
            "institution": "Example University",
            "skill_names": ["Python"],
            "facts": [
                {
                    "id": "skill:1",
                    "type": "skill",
                    "text": "Python",
                    "data": {"name": "Python"},
                }
            ],
        }

    def job_data(self):
        return {
            "title": "Data Analyst",
            "employer": "Example Bank",
            "location": "Johannesburg",
            "description": "Use Python for analysis.",
            "qualifications": [],
            "experience": [],
            "skills": ["Python"],
            "certifications": [],
            "responsibilities": [],
            "preferred_requirements": [],
            "other_requirements": [],
            "deadline": "",
            "work_arrangement": "",
            "employment_type": "",
            "application_instructions": "",
            "source_url": "",
        }

    @patch("applications.services.document_generation._interaction")
    def test_cv_repairs_unsupported_numeric_claim_once(self, interaction):
        from applications.services.document_generation import generate_cv_writing

        interaction.side_effect = [
            '{"summary":"Candidate with 1 year of Python experience.","rewrites":[],"skill_order":["Python"]}',
            '{"summary":"Candidate with Python skills relevant to analysis.","rewrites":[],"skill_order":["Python"]}',
        ]
        bundle = self.candidate_bundle()

        result = generate_cv_writing(bundle, bundle["facts"], self.job_data())

        self.assertEqual(result.summary, "Candidate with Python skills relevant to analysis.")
        self.assertEqual(interaction.call_count, 2)
        self.assertIn("INTEGRITY REPAIR REQUIRED", interaction.call_args_list[1].args[0])
        self.assertIn("1", interaction.call_args_list[1].args[0])

    @patch("applications.services.document_generation._interaction")
    def test_cv_still_fails_if_repair_repeats_unsupported_number(self, interaction):
        from applications.services.document_generation import generate_cv_writing

        interaction.side_effect = [
            '{"summary":"Candidate with 1 year of Python experience.","rewrites":[],"skill_order":["Python"]}',
            '{"summary":"Candidate with 2 years of Python experience.","rewrites":[],"skill_order":["Python"]}',
        ]
        bundle = self.candidate_bundle()

        with self.assertRaisesMessage(ValueError, "unsupported numeric claims: 2"):
            generate_cv_writing(bundle, bundle["facts"], self.job_data())
        self.assertEqual(interaction.call_count, 2)

    @patch("applications.services.document_generation._interaction")
    def test_cover_letter_metadata_numbers_are_not_candidate_claims(self, interaction):
        from applications.services.document_generation import generate_cover_letter

        interaction.return_value = (
            '{"date":"05 September 2026","recipient_name":"Hiring Manager",'
            '"company_name":"Example Bank","location":"Johannesburg",'
            '"salutation":"Dear Hiring Manager,",'
            '"opening":"My Python skills align with the role.",'
            '"body_paragraphs":["I can contribute Python capability grounded in my profile."],'
            '"closing":"Thank you for considering my application.",'
            '"sign_off":"Kind regards,","signature_name":""}'
        )
        bundle = self.candidate_bundle()

        result = generate_cover_letter(bundle, bundle["facts"], self.job_data())

        self.assertEqual(result.date, "05 September 2026")
        self.assertEqual(interaction.call_count, 1)


class LegacyCVAssemblySchemaTests(TestCase):
    def test_legacy_cv_uses_renderer_compatible_dict_shapes(self):
        from applications.services.document_generation import CVWritingResult, assemble_legacy_cv

        candidate_bundle = {
            "facts": [
                {
                    "id": "education:1",
                    "type": "education",
                    "data": {
                        "qualification": "BCom",
                        "field_of_study": "Finance",
                        "institution": "Example University",
                        "start_date": "2024-01-01",
                        "end_date": "",
                        "current": True,
                    },
                },
                {
                    "id": "work:1",
                    "type": "work",
                    "data": {
                        "title": "Finance Assistant",
                        "organisation": "Example Bank",
                        "dates": "2025",
                        "bullets": ["Prepared reports"],
                    },
                },
                {
                    "id": "project:1",
                    "type": "project",
                    "data": {
                        "title": "Finance Dashboard",
                        "organisation": "University Project",
                        "dates": "2026",
                        "bullets": ["Built dashboard"],
                    },
                },
                {
                    "id": "certification:1",
                    "type": "certification",
                    "data": {
                        "title": "Data Analyst",
                        "issuer": "Example Institute",
                        "dates": "2026",
                    },
                },
            ]
        }
        json_resume = {
            "basics": {
                "name": "Mate Dog",
                "email": "mate@example.com",
                "phone": "",
                "location": {"address": "Johannesburg"},
            },
            "education": [{
                "studyType": "BCom",
                "area": "Finance",
                "institution": "Example University",
                "startDate": "2024-01-01",
                "endDate": "",
                "x_careergps_fact_id": "education:1",
            }],
            "work": [{
                "name": "Example Bank",
                "position": "Finance Assistant",
                "x_careergps_fact_id": "work:1",
            }],
            "projects": [{
                "name": "Finance Dashboard",
                "description": "",
                "highlights": ["Built dashboard"],
                "x_careergps_fact_id": "project:1",
            }],
            "certificates": [{
                "name": "Data Analyst",
                "issuer": "Example Institute",
                "x_careergps_fact_id": "certification:1",
            }],
        }
        writing = CVWritingResult(summary="Finance student.", rewrites=[], skill_order=[])

        cv_data = assemble_legacy_cv(candidate_bundle, json_resume, writing)

        self.assertEqual(cv_data["education"], [{
            "qualification": "BCom Finance",
            "institution": "Example University",
            "dates": "2024-01-01 - Present",
        }])
        self.assertEqual(cv_data["work_experience"][0]["organisation"], "Example Bank")
        self.assertEqual(cv_data["work_experience"][0]["company"], "Example Bank")
        self.assertEqual(cv_data["key_projects"][0]["organisation"], "University Project")
        self.assertEqual(cv_data["certifications"], [{
            "title": "Data Analyst - Example Institute",
            "dates": "2026",
        }])
