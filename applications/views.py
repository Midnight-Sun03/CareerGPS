from django.contrib import messages
from django.contrib.auth.decorators import login_required
from pathlib import Path
import os
import tempfile

from django.db import transaction
from django.core.exceptions import ValidationError
from django.http import (
    FileResponse, Http404, HttpResponseBadRequest, HttpResponseServerError,
    JsonResponse,
)
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST

from core.models import Opportunity, Profile, SavedOpportunity

from .models import Application, GeneratedDocument
from .services.job_context import resolve_job_context
from .services.document_editing import (
    DocumentEditError, apply_cover_letter_edits, apply_cv_edits,
)
from .services.resume_templates import (
    normalize_layout_settings, normalize_template_key, public_template_options, render_resume_pdf,
)


PRIVATE_DOCUMENT_FORMATS = {
    "pdf": ("pdf_file", "application/pdf"),
    "docx": (
        "docx_file",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ),
}


def _private_file_response(file_field, *, filename, content_type, attachment=True):
    try:
        stream = file_field.open("rb")
    except (FileNotFoundError, OSError, ValueError):
        raise Http404("File not found.")
    response = FileResponse(
        stream,
        as_attachment=attachment,
        filename=filename,
        content_type=content_type,
    )
    response["Cache-Control"] = "private, no-store"
    response["Pragma"] = "no-cache"
    response["X-Content-Type-Options"] = "nosniff"
    return response


def _sync_application(application, job_context):
    application.job_info_status = job_context["status"]
    application.save(update_fields=["job_info_status", "updated_at"])
    return application


@login_required
def start_application(request, opportunity_id):
    opportunity = get_object_or_404(
        Opportunity.objects.professionally_ready().select_related("active_clean_revision"),
        pk=opportunity_id)
    application, created = Application.objects.get_or_create(
        user=request.user,
        opportunity=opportunity,
        defaults={"clean_revision": opportunity.active_clean_revision},
    )
    if not application.clean_revision_id:
        application.clean_revision = opportunity.active_clean_revision
        application.save(update_fields=["clean_revision", "updated_at"])
    job_context = resolve_job_context(opportunity, clean_revision=application.clean_revision)
    _sync_application(application, job_context)
    return redirect("applications:workspace", application_id=application.id)


@login_required
def workspace(request, application_id):
    application = get_object_or_404(
        Application.objects.select_related("opportunity", "clean_revision"),
        pk=application_id,
        user=request.user,
    )
    job_context = resolve_job_context(
        application.opportunity, clean_revision=application.clean_revision)
    _sync_application(application, job_context)
    documents = application.generated_documents.all()[:20]
    from employers.forms import SubmissionForm
    from employers.models import HiringApplication
    is_direct = application.opportunity.source_platform == 'CareerGPS Direct'
    hiring = HiringApplication.objects.filter(application=application).first() if is_direct else None
    extra = {'is_direct': is_direct, 'hiring': hiring,
        'submission_form': SubmissionForm(application=application) if is_direct and not hiring else None,
        'submitted_documents': hiring.submitted_documents.defer('pdf_bytes', 'docx_bytes') if hiring else [],
        'candidate_stage': hiring.stage_events.filter(candidate_visible=True).order_by('-created_at', '-pk').first() if hiring else None}
    revision = application.clean_revision
    opportunity_closed = (
        not application.opportunity.is_active
        or application.opportunity.lifecycle_status != 'active'
        or bool(revision and revision.closing_sort_at
                and revision.closing_sort_at < timezone.now())
    )
    return render(request, "applications/workspace.html", {
        "application": application,
        "opportunity": application.opportunity,
        "job_context": job_context,
        "job": job_context["data"],
        "documents": documents,
        "status_choices": Application.STATUS_CHOICES[:2] if is_direct else Application.STATUS_CHOICES,
        "opportunity_closed": opportunity_closed,
        **extra,
    })


@login_required
@require_POST
def resolve_saved_opportunity(request, opportunity_id):
    opportunity = get_object_or_404(
        Opportunity.objects.professionally_ready().select_related("active_clean_revision"),
        pk=opportunity_id)
    allowed = (
        SavedOpportunity.objects.filter(user=request.user, opportunity=opportunity).exists()
        or Application.objects.filter(user=request.user, opportunity=opportunity).exists()
    )
    if not allowed:
        return JsonResponse({"error": "Select a saved opportunity or open it from your application workspace."}, status=403)

    application, _ = Application.objects.get_or_create(
        user=request.user,
        opportunity=opportunity,
        defaults={"clean_revision": opportunity.active_clean_revision},
    )
    if not application.clean_revision_id:
        application.clean_revision = opportunity.active_clean_revision
        application.save(update_fields=["clean_revision", "updated_at"])
    job_context = resolve_job_context(opportunity, clean_revision=application.clean_revision)
    _sync_application(application, job_context)

    status = job_context["status"]
    message = "CareerGPS will use the source information currently available for this opportunity."

    return JsonResponse({
        "status": status,
        "limited": job_context["limited"],
        "message": message,
        "application_id": application.id,
    })


@login_required
@require_POST
def update_status(request, application_id):
    application = get_object_or_404(Application, pk=application_id, user=request.user)
    status = request.POST.get("status", "")
    allowed = {value for value, _ in Application.STATUS_CHOICES}
    if status not in allowed:
        messages.error(request, "Invalid application status.")
        return redirect("applications:workspace", application_id=application.id)

    if application.opportunity.source_platform == 'CareerGPS Direct':
        from employers.models import HiringApplication
        from employers.services import withdraw_application
        hiring = HiringApplication.objects.filter(application=application).first()
        if hiring:
            if status == 'withdrawn':
                try:
                    withdraw_application(user=request.user, application_id=application.id)
                except ValidationError as exc:
                    messages.error(request, '; '.join(exc.messages))
            else:
                messages.error(request, 'Hiring stages are managed by the employer. You may withdraw your application.')
            return redirect('applications:workspace', application_id=application.id)
        if status not in {'planning', 'ready_to_apply'}:
            messages.error(request, 'Use Submit application to apply through CareerGPS.')
            return redirect('applications:workspace', application_id=application.id)
    application.status = status
    if status == "applied" and application.applied_at is None:
        application.applied_at = timezone.now()
    application.save(update_fields=["status", "applied_at", "updated_at"])
    return redirect("applications:workspace", application_id=application.id)


@login_required
@require_POST
def save_notes(request, application_id):
    application = get_object_or_404(Application, pk=application_id, user=request.user)
    application.notes = (request.POST.get("notes") or "")[:10000]
    application.save(update_fields=["notes", "updated_at"])
    messages.success(request, "Application notes saved.")
    return redirect("applications:workspace", application_id=application.id)


@login_required
@require_GET
def generated_document_download(request, document_id, file_format):
    field_name, content_type = PRIVATE_DOCUMENT_FORMATS.get(
        file_format,
        (None, None),
    )
    if field_name is None:
        raise Http404("Unsupported document format.")

    document = get_object_or_404(
        GeneratedDocument,
        pk=document_id,
        user=request.user,
    )
    file_field = getattr(document, field_name)
    if not file_field:
        raise Http404("File not found.")

    label = "cv" if document.document_type == "cv" else "cover-letter"
    return _private_file_response(
        file_field,
        filename=f"{label}-{document.pk}.{file_format}",
        content_type=content_type,
    )


def _document_view_context(document, *, edit_error=""):
    layout = normalize_layout_settings(document.layout_settings)
    resume = document.json_resume if isinstance(document.json_resume, dict) else {}
    structured = document.structured_data if isinstance(document.structured_data, dict) else {}
    templates = public_template_options()
    context = {
        "document": document,
        "resume": resume,
        "cv": structured,
        "letter": structured,
        "layout": layout,
        "hidden_sections": set(layout["hidden_sections"]),
        "resume_templates": templates,
        "template_label": next(
            (item["label"] for item in templates if item["key"] == document.template_key),
            document.template_key,
        ),
        "section_options": [
            ("summary", "Profile"),
            ("work", "Experience"),
            ("projects", "Projects"),
            ("education", "Education"),
            ("skills", "Skills"),
            ("certificates", "Certifications"),
            ("languages", "Languages"),
            ("awards", "Awards"),
            ("volunteer", "Volunteering"),
        ],
        "edit_error": edit_error,
    }
    context["cv_edit"] = {
        "work": [
            {"index": index, "item": item, "bullets_text": "\n".join(item.get("bullets", []))}
            for index, item in enumerate(structured.get("work_experience", []))
            if isinstance(item, dict)
        ],
        "projects": [
            {"index": index, "item": item, "bullets_text": "\n".join(item.get("bullets", []))}
            for index, item in enumerate(structured.get("key_projects", []))
            if isinstance(item, dict)
        ],
        "awards": [
            {"index": index, "item": item}
            for index, item in enumerate(resume.get("awards", []))
            if isinstance(item, dict)
        ],
        "volunteer": [
            {"index": index, "item": item}
            for index, item in enumerate(resume.get("volunteer", []))
            if isinstance(item, dict)
        ],
        "certificates": [
            {
                "index": index,
                "item": item,
                "display": item.get("display") or " - ".join(
                    bit for bit in (item.get("name", ""), item.get("issuer", "")) if bit
                ),
            }
            for index, item in enumerate(resume.get("certificates", []))
            if isinstance(item, dict)
        ],
        "skill_groups": [
            {"index": index, "item": item, "items_text": "\n".join(item.get("items", []))}
            for index, item in enumerate(structured.get("technical_skills", []))
            if isinstance(item, dict)
        ],
    }
    body = structured.get("body_paragraphs", [])
    context["body_paragraphs_text"] = "\n\n".join(body) if isinstance(body, list) else ""
    return context


@login_required
@require_GET
def generated_document_preview(request, document_id):
    document = get_object_or_404(
        GeneratedDocument.objects.select_related("application", "opportunity"),
        pk=document_id,
        user=request.user,
    )
    document.template_key = normalize_template_key(document.template_key)
    return render(
        request,
        "applications/document_preview.html",
        _document_view_context(document),
    )


@login_required
def cv_document_editor(request, document_id):
    document = get_object_or_404(
        GeneratedDocument.objects.select_related("application", "opportunity"),
        pk=document_id,
        user=request.user,
    )
    document.template_key = normalize_template_key(document.template_key)
    template = (
        "applications/cv_editor.html"
        if document.document_type == "cv"
        else "applications/cover_letter_editor.html"
    )
    return render(request, template, _document_view_context(document))


def _temporary_output(target):
    descriptor, name = tempfile.mkstemp(
        prefix=f".{target.stem}-edit-",
        suffix=target.suffix,
        dir=str(target.parent),
    )
    os.close(descriptor)
    return Path(name)


def _install_outputs(pairs):
    """Atomically install local output files and retain rollback copies."""
    backups = []
    installed = []
    try:
        for temporary, target in pairs:
            backup = _temporary_output(target)
            backup.unlink(missing_ok=True)
            if target.exists():
                os.replace(target, backup)
            else:
                backup = None
            backups.append((backup, target))
        for temporary, target in pairs:
            os.replace(temporary, target)
            installed.append(target)
    except Exception:
        for target in installed:
            target.unlink(missing_ok=True)
        for backup, target in backups:
            if backup is not None and backup.exists():
                os.replace(backup, target)
        raise
    return backups


def _restore_outputs(backups):
    for backup, target in backups:
        target.unlink(missing_ok=True)
        if backup is not None and backup.exists():
            os.replace(backup, target)


def _discard_output_backups(backups):
    for backup, _target in backups:
        if backup is not None:
            try:
                backup.unlink(missing_ok=True)
            except OSError:
                pass


@login_required
@require_POST
def update_document_content(request, document_id):
    with transaction.atomic():
        document = get_object_or_404(
            GeneratedDocument.objects.select_for_update(),
            pk=document_id,
            user=request.user,
        )
        if document.document_type not in {"cv", "cover_letter"}:
            return HttpResponseBadRequest("Unsupported generated document type.")
        if not document.pdf_file or not document.docx_file:
            return HttpResponseBadRequest("Regenerate this document before editing it.")

        try:
            pdf_target = Path(document.pdf_file.path)
            docx_target = Path(document.docx_file.path)
        except (NotImplementedError, ValueError):
            return HttpResponseBadRequest("This document storage cannot be edited in place.")

        pdf_temp = _temporary_output(pdf_target)
        docx_temp = _temporary_output(docx_target)
        backups = []
        try:
            if document.document_type == "cv":
                structured, resume = apply_cv_edits(
                    document.structured_data,
                    document.json_resume,
                    request.POST,
                )
                from core.views import _build_docx
                render_resume_pdf(
                    resume,
                    structured,
                    str(pdf_temp),
                    template_key=document.template_key,
                    layout_settings=document.layout_settings,
                )
                _build_docx(
                    structured,
                    str(docx_temp),
                    json_resume=resume,
                    layout_settings=document.layout_settings,
                )
                document.structured_data = structured
                document.json_resume = resume
                update_fields = ["structured_data", "json_resume"]
            else:
                letter = apply_cover_letter_edits(document.structured_data, request.POST)
                from core.views import _build_cover_letter_docx, _build_cover_letter_pdf
                _build_cover_letter_pdf(letter, str(pdf_temp))
                _build_cover_letter_docx(letter, str(docx_temp))
                document.structured_data = letter
                update_fields = ["structured_data"]

            backups = _install_outputs((
                (pdf_temp, pdf_target),
                (docx_temp, docx_target),
            ))
            try:
                document.save(update_fields=update_fields)
            except Exception:
                _restore_outputs(backups)
                backups = []
                raise
            _discard_output_backups(backups)
            backups = []
        except DocumentEditError as exc:
            context = _document_view_context(document, edit_error=str(exc))
            template = (
                "applications/cv_editor.html"
                if document.document_type == "cv"
                else "applications/cover_letter_editor.html"
            )
            return render(request, template, context, status=400)
        except Exception:
            return HttpResponseServerError("The document outputs could not be regenerated.")
        finally:
            _discard_output_backups(backups)
            for temporary in (pdf_temp, docx_temp):
                try:
                    temporary.unlink(missing_ok=True)
                except OSError:
                    pass

    messages.success(request, "Your document changes were saved.")
    return redirect("applications:document_preview", document_id=document.id)


@login_required
@require_POST
@transaction.atomic
def update_cv_layout(request, document_id):
    document = get_object_or_404(
        GeneratedDocument.objects.select_for_update(),
        pk=document_id,
        user=request.user,
        document_type="cv",
    )
    template_key = normalize_template_key(request.POST.get("template_key"))
    layout = normalize_layout_settings({
        "density": request.POST.get("density", "comfortable"),
        "hidden_sections": request.POST.getlist("hidden_sections"),
    })

    if not document.pdf_file:
        return JsonResponse({"error": "This CV has no PDF file to rebuild."}, status=400)

    backups = []
    try:
        pdf_target = Path(document.pdf_file.path)
        pdf_temp = _temporary_output(pdf_target)
        render_resume_pdf(
            document.json_resume or {},
            document.structured_data or {},
            str(pdf_temp),
            template_key=template_key,
            layout_settings=layout,
        )
        docx_temp = None
        if document.docx_file:
            from core.views import _build_docx
            docx_target = Path(document.docx_file.path)
            docx_temp = _temporary_output(docx_target)
            _build_docx(
                document.structured_data or {},
                str(docx_temp),
                json_resume=document.json_resume or {},
                layout_settings=layout,
            )
        pairs = [(pdf_temp, pdf_target)]
        if docx_temp is not None:
            pairs.append((docx_temp, docx_target))
        backups = _install_outputs(pairs)

        document.template_key = template_key
        document.layout_settings = layout
        document.renderer_backend = "careergps_rr_layout_adapter"
        try:
            document.save(update_fields=["template_key", "layout_settings", "renderer_backend"])
        except Exception:
            _restore_outputs(backups)
            backups = []
            raise
        _discard_output_backups(backups)
        backups = []
    except Exception as exc:
        return JsonResponse({"error": f"Could not rebuild the CV outputs: {exc}"}, status=500)
    finally:
        _discard_output_backups(backups)
        for temporary in (locals().get("pdf_temp"), locals().get("docx_temp")):
            if temporary is not None:
                try:
                    temporary.unlink(missing_ok=True)
                except OSError:
                    pass

    return JsonResponse({
        "ok": True,
        "template_key": template_key,
        "layout_settings": layout,
        "pdf_url": reverse(
            "applications:document_download",
            args=[document.pk, "pdf"],
        ),
        "docx_url": reverse(
            "applications:document_download",
            args=[document.pk, "docx"],
        ) if document.docx_file else "",
    })
