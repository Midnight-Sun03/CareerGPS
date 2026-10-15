from __future__ import annotations

import json
from pathlib import Path

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.http import FileResponse, Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from .models import Interview, InterviewQuestion
from .services.ai import InterviewAIUnavailable, evaluate_interview, generate_interview_plan
from .services.context import (
    build_interview_context,
    interview_choices_for_user,
    resolve_user_interview_source,
)
from .services.delivery_metrics import unavailable_delivery
from .services.transcription import TranscriptionUnavailable, transcribe_answer_audio
from .services.visual_metrics import (
    SCHEMA_VERSION as VISUAL_ANALYSIS_VERSION,
    VisualMetricsValidationError,
    build_overall_visual_summary,
    validate_visual_metrics,
)


ALLOWED_QUESTION_COUNTS = {5, 8, 10}
ALLOWED_DELIVERY_MODES = {"text_only", "voice_only", "text_voice"}
ALLOWED_CAMERA_MODES = {"audio_only", "camera_analysis"}
MAX_AUDIO_SIZE = 15 * 1024 * 1024
ALLOWED_AUDIO_TYPES = {
    "audio/webm",
    "audio/mp4",
    "audio/ogg",
    "audio/mpeg",
    "audio/wav",
    "audio/x-wav",
}


def _feedback_url(interview):
    return reverse("interview_feedback", args=[interview.id])


def _review_url(interview):
    return reverse("interview_transcript_review", args=[interview.id])


@login_required
def interview_home(request):
    choices = interview_choices_for_user(request.user)
    recent = (
        Interview.objects.filter(user=request.user)
        .select_related("opportunity", "application", "clean_revision")
        .order_by("-created_at", "-id")[:5]
    )
    return render(
        request,
        "interview/home.html",
        {**choices, "recent_interviews": recent},
    )


@login_required
def interview_prepare(request, opportunity_id):
    opportunity, application, source_type = resolve_user_interview_source(
        request.user,
        opportunity_id,
    )
    if opportunity is None:
        messages.error(
            request,
            "Choose an opportunity from your saved opportunities or application workspaces.",
        )
        return redirect("interview_home")
    return render(
        request,
        "interview/prepare.html",
        {
            "opportunity": opportunity,
            "application": application,
            "source_type": source_type,
        },
    )


@login_required
def interview_setup(request):
    return redirect("interview_home")


@login_required
def interview_live(request, interview_id):
    interview = get_object_or_404(
        Interview.objects.select_related("opportunity", "application", "clean_revision"),
        id=interview_id,
        user=request.user,
    )
    if interview.status == "completed":
        return redirect("interview_feedback", interview_id=interview.id)
    if interview.mode == "voice" and interview.status in {
        "transcript_review",
        "evaluating",
    }:
        return redirect("interview_transcript_review", interview_id=interview.id)

    if interview.mode == "voice" and interview.status == "active":
        processed_answer_count = interview.questions.exclude(
            transcription_status="pending"
        ).count()
        if processed_answer_count == interview.question_count:
            interview.status = "transcript_review"
            interview.save(update_fields=["status", "updated_at"])
            return redirect("interview_transcript_review", interview_id=interview.id)

    questions = list(
        interview.questions.values(
            "id",
            "question_index",
            "question_text",
            "question_type",
            "focus",
            "answer_text",
            "transcription_status",
        )
    )
    return render(
        request,
        "interview/live.html",
        {
            "interview": interview,
            "questions": questions,
            "questions_json": questions,
        },
    )


@login_required
def interview_transcript_review(request, interview_id):
    interview = get_object_or_404(
        Interview.objects.select_related("opportunity", "application", "clean_revision").prefetch_related(
            "questions"
        ),
        id=interview_id,
        user=request.user,
        mode="voice",
    )
    if interview.status == "completed":
        return redirect("interview_feedback", interview_id=interview.id)
    if interview.status not in {"transcript_review", "evaluating"}:
        return redirect("interview_live", interview_id=interview.id)
    return render(
        request,
        "interview/transcript_review.html",
        {"interview": interview, "questions": interview.questions.all()},
    )


@login_required
@require_GET
def interview_media(request, question_id, media_type):
    media = {
        "audio": (
            "audio_file",
            {
                ".webm": "audio/webm",
                ".mp4": "audio/mp4",
                ".m4a": "audio/mp4",
                ".ogg": "audio/ogg",
                ".mp3": "audio/mpeg",
                ".wav": "audio/wav",
            },
        ),
        "video": (
            "video_file",
            {".webm": "video/webm", ".mp4": "video/mp4", ".ogg": "video/ogg"},
        ),
    }.get(media_type)
    if media is None:
        raise Http404("Unsupported interview media type.")
    field_name, content_types = media
    question = get_object_or_404(
        InterviewQuestion.objects.select_related("interview"),
        pk=question_id,
        interview__user=request.user,
    )
    file_field = getattr(question, field_name)
    if not file_field:
        raise Http404("Interview media not found.")
    try:
        stream = file_field.open("rb")
    except (FileNotFoundError, OSError, ValueError):
        raise Http404("Interview media not found.")

    filename = Path(file_field.name).name
    response = FileResponse(
        stream,
        as_attachment=False,
        filename=filename,
        content_type=content_types.get(
            Path(filename).suffix.lower(),
            "application/octet-stream",
        ),
    )
    response["Cache-Control"] = "private, no-store"
    response["Pragma"] = "no-cache"
    response["X-Content-Type-Options"] = "nosniff"
    return response


@login_required
def interview_feedback(request, interview_id):
    interview = get_object_or_404(
        Interview.objects.select_related("opportunity", "application", "clean_revision"),
        id=interview_id,
        user=request.user,
    )
    feedback = interview.feedback_data or {
        "overall_summary": interview.feedback_summary or "",
        "strengths": interview.strengths or [],
        "improvements": interview.improvements or [],
    }
    questions = list(interview.questions.all())
    return render(
        request,
        "interview/feedback.html",
        {
            "interview": interview,
            "questions": questions,
            "feedback": feedback,
            "visual_summary": build_overall_visual_summary(interview, questions),
        },
    )


@login_required
def interview_saved(request):
    interviews = (
        Interview.objects.filter(user=request.user)
        .select_related("opportunity", "application", "clean_revision")
        .order_by("-created_at", "-id")
    )
    return render(
        request,
        "interview/saved.html",
        {"interviews": interviews, "count": interviews.count()},
    )


@login_required
@require_POST
def api_create_interview(request):
    try:
        data = json.loads(request.body or "{}")
        opportunity_id = int(data.get("opportunityId") or 0)
        mode = str(data.get("mode") or "").strip()
        question_count = int(data.get("questionCount") or 5)
        popia_consent = bool(data.get("popiaConsent"))
        question_delivery = str(data.get("questionDelivery") or "text_only").strip()
        camera_mode = str(data.get("cameraMode") or "audio_only").strip()
        camera_consent = data.get("cameraConsent") is True

        if mode not in {"written", "voice"}:
            return JsonResponse(
                {"message": "Choose written or spoken interview practice."},
                status=400,
            )
        if question_count not in ALLOWED_QUESTION_COUNTS:
            return JsonResponse({"message": "Choose a valid interview length."}, status=400)
        if mode == "voice" and question_delivery not in ALLOWED_DELIVERY_MODES:
            return JsonResponse({"message": "Choose how the questions should be delivered."}, status=400)
        if mode == "written":
            question_delivery = "text_only"
            camera_mode = "audio_only"
            camera_consent = False
        elif camera_mode not in ALLOWED_CAMERA_MODES:
            return JsonResponse({"message": "Choose a valid camera practice option."}, status=400)
        if camera_mode == "camera_analysis" and not camera_consent:
            return JsonResponse(
                {"message": "Explicit camera-analysis consent is required."},
                status=400,
            )
        if not popia_consent:
            return JsonResponse(
                {
                    "message": (
                        "Please consent to CareerGPS processing your profile and "
                        "interview responses for AI-powered interview practice."
                    )
                },
                status=400,
            )

        opportunity, application, source_type = resolve_user_interview_source(
            request.user,
            opportunity_id,
        )
        if opportunity is None:
            return JsonResponse(
                {
                    "message": (
                        "Choose an opportunity from your saved opportunities or "
                        "application workspaces."
                    )
                },
                status=403,
            )

        context = build_interview_context(request.user, opportunity, application=application)
        plan = generate_interview_plan(context["snapshot"], question_count)
        interview = Interview.objects.create(
            user=request.user,
            opportunity=opportunity,
            clean_revision=(
                application.clean_revision if application and application.clean_revision_id
                else opportunity.active_clean_revision
            ),
            application=application,
            source_type=source_type,
            mode=mode,
            question_delivery=question_delivery,
            camera_mode=camera_mode,
            camera_consent=camera_consent,
            visual_analysis_version=(
                VISUAL_ANALYSIS_VERSION if camera_mode == "camera_analysis" else ""
            ),
            question_count=question_count,
            job_info_status=context["job_info_status"],
            context_snapshot=context["snapshot"],
            candidate_fact_ids=context["candidate_fact_ids"],
            generated_document_ids=context["generated_document_ids"],
            popia_consent=True,
            status="active",
            occupation=(
                application.clean_revision.cleaned_title
                if application and application.clean_revision_id
                else opportunity.active_clean_revision.cleaned_title
            ),
            experience_level="",
        )
        InterviewQuestion.objects.bulk_create(
            [
                InterviewQuestion(
                    interview=interview,
                    question_index=index,
                    question_text=item.question.strip(),
                    question_type=item.question_type,
                    focus=item.focus.strip(),
                    answer_mode=mode,
                    transcription_status="pending" if mode == "voice" else "not_applicable",
                    voice_analysis_status="pending" if mode == "voice" else "not_applicable",
                    visual_analysis_status=(
                        "ready" if mode == "voice" and camera_mode == "camera_analysis"
                        else "not_requested"
                    ),
                )
                for index, item in enumerate(plan.questions)
            ]
        )
        return JsonResponse(
            {
                "success": True,
                "interviewId": interview.id,
                "liveUrl": reverse("interview_live", args=[interview.id]),
            }
        )
    except InterviewAIUnavailable as exc:
        return JsonResponse({"message": str(exc)}, status=503)
    except (ValueError, TypeError, json.JSONDecodeError):
        return JsonResponse(
            {"message": "CareerGPS couldn't read that interview request."},
            status=400,
        )
    except Exception:
        return JsonResponse(
            {
                "message": (
                    "CareerGPS couldn't prepare your interview right now. "
                    "Please try again shortly."
                )
            },
            status=500,
        )


def _active_question_or_404(user, question_id, mode):
    return get_object_or_404(
        InterviewQuestion.objects.select_related("interview"),
        id=question_id,
        interview__user=user,
        interview__mode=mode,
        interview__status="active",
    )


@login_required
@require_POST
def api_submit_written_answer(request, question_id):
    question = _active_question_or_404(request.user, question_id, "written")
    try:
        data = json.loads(request.body or "{}")
    except json.JSONDecodeError:
        return JsonResponse({"message": "Could not read that answer."}, status=400)
    answer = str(data.get("answerText") or "").strip()
    if not answer:
        return JsonResponse({"message": "Enter an answer before continuing."}, status=400)
    if (question.answer_text or "").strip():
        if question.answer_text.strip() == answer:
            return JsonResponse({"success": True, "alreadySaved": True})
        return JsonResponse(
            {"message": "This completed answer is already saved."},
            status=409,
        )
    question.answer_text = answer
    question.answer_mode = "written"
    question.delivery_metrics = {}
    question.save(update_fields=["answer_text", "answer_mode", "delivery_metrics"])
    return JsonResponse({"success": True})


def _audio_error(audio):
    if audio is None:
        return "Record an audio answer before continuing.", 400
    if audio.size > MAX_AUDIO_SIZE:
        return "That audio answer is too large to save.", 413
    base_type = str(audio.content_type or "").split(";", 1)[0].lower()
    if base_type not in ALLOWED_AUDIO_TYPES:
        return "Unsupported audio format.", 400
    return None


@login_required
@require_POST
def api_submit_voice_answer(request, question_id):
    question = _active_question_or_404(request.user, question_id, "voice")
    interview = question.interview
    if not interview.popia_consent:
        return JsonResponse({"message": "Interview-processing consent is required."}, status=403)
    if question.audio_file:
        return JsonResponse(
            {"message": "This completed answer is already saved."},
            status=409,
        )

    audio = request.FILES.get("audio")
    audio_error = _audio_error(audio)
    if audio_error:
        message, status = audio_error
        return JsonResponse({"message": message}, status=status)

    try:
        browser_duration = float(request.POST.get("durationSeconds") or 0)
    except (TypeError, ValueError):
        browser_duration = None
    provisional = str(request.POST.get("provisionalTranscript") or "").strip()

    visual_metrics = {}
    visual_status = "not_requested"
    visual_error = ""
    raw_visual_metrics = request.POST.get("visualMetrics")
    if interview.camera_mode == "camera_analysis":
        if not interview.camera_consent:
            return JsonResponse({"message": "Camera-analysis consent is required."}, status=403)
        if browser_duration is None or browser_duration < 0:
            return JsonResponse(
                {"message": "A valid answer duration is required for camera analysis."},
                status=400,
            )
        try:
            visual_metrics = validate_visual_metrics(
                raw_visual_metrics,
                browser_duration=browser_duration,
            )
        except VisualMetricsValidationError as exc:
            return JsonResponse({"message": str(exc)}, status=400)
        visual_status = visual_metrics["status"]
        if visual_status in {"unavailable", "error"}:
            visual_error = visual_metrics.get("unavailable_reason", "analysis_error")
    elif raw_visual_metrics is not None:
        return JsonResponse(
            {"message": "Camera analysis was not requested for this interview."},
            status=400,
        )

    question.audio_file.save(audio.name, audio, save=False)
    question.provisional_transcript = provisional or None
    question.transcription_status = "pending"
    question.voice_analysis_status = "pending"
    question.visual_analysis_status = visual_status
    question.visual_metrics = visual_metrics
    question.visual_analysis_error = visual_error
    question.save(
        update_fields=[
            "audio_file",
            "provisional_transcript",
            "transcription_status",
            "voice_analysis_status",
            "visual_analysis_status",
            "visual_metrics",
            "visual_analysis_error",
        ]
    )

    try:
        result = transcribe_answer_audio(question.audio_file)
    except TranscriptionUnavailable:
        question.raw_transcript = None
        question.corrected_transcript = None
        question.answer_text = None
        question.word_timestamps = []
        question.transcription_status = "unavailable"
        question.transcription_error = "Authoritative audio transcription unavailable."
        question.voice_analysis_status = "unavailable"
        question.delivery_metrics = unavailable_delivery(
            browser_duration_seconds=browser_duration,
            reason="transcription_unavailable",
        )
    else:
        question.raw_transcript = result.transcript
        question.corrected_transcript = result.transcript
        question.answer_text = result.transcript
        question.word_timestamps = result.word_timestamps
        question.transcription_status = "available"
        question.transcription_error = ""
        question.voice_analysis_status = "available" if result.vad_available else "partial"
        question.delivery_metrics = result.delivery_metrics

    question.answer_mode = "voice"
    question.save(
        update_fields=[
            "raw_transcript",
            "corrected_transcript",
            "provisional_transcript",
            "answer_text",
            "word_timestamps",
            "transcription_status",
            "transcription_error",
            "voice_analysis_status",
            "delivery_metrics",
            "answer_mode",
            "visual_analysis_status",
            "visual_metrics",
            "visual_analysis_error",
        ]
    )

    completed_count = interview.questions.exclude(transcription_status="pending").count()
    finished = completed_count == interview.question_count
    if finished:
        Interview.objects.filter(pk=interview.pk, status="active").update(
            status="transcript_review"
        )
    return JsonResponse(
        {
            "success": True,
            "transcriptionStatus": question.transcription_status,
            "interviewFinished": finished,
            "visualAnalysisStatus": question.visual_analysis_status,
            "reviewUrl": _review_url(interview) if finished else "",
        }
    )


@login_required
@require_POST
def api_review_transcripts(request, interview_id):
    interview = get_object_or_404(
        Interview.objects.prefetch_related("questions"),
        id=interview_id,
        user=request.user,
        mode="voice",
        status="transcript_review",
    )
    try:
        rows = json.loads(request.body or "{}").get("transcripts", [])
    except (AttributeError, json.JSONDecodeError):
        return JsonResponse({"message": "Could not read those transcripts."}, status=400)
    if not isinstance(rows, list):
        return JsonResponse({"message": "Could not read those transcripts."}, status=400)

    questions = list(interview.questions.all())
    by_id = {question.id: question for question in questions}
    supplied = {}
    for row in rows:
        try:
            question_id = int(row.get("questionId"))
        except (AttributeError, TypeError, ValueError):
            return JsonResponse({"message": "A transcript entry is invalid."}, status=400)
        if question_id in supplied or question_id not in by_id:
            return JsonResponse({"message": "A transcript entry is invalid."}, status=400)
        supplied[question_id] = str(row.get("transcript") or "").strip()

    if set(supplied) != set(by_id):
        return JsonResponse({"message": "Review every transcript before continuing."}, status=400)
    if any(not transcript for transcript in supplied.values()):
        return JsonResponse(
            {
                "message": (
                    "Enter what you said where transcription was unavailable, "
                    "then review every answer."
                )
            },
            status=400,
        )

    for question in questions:
        corrected = supplied[question.id]
        question.corrected_transcript = corrected
        question.answer_text = corrected
    InterviewQuestion.objects.bulk_update(
        questions,
        ["corrected_transcript", "answer_text"],
    )
    return JsonResponse({"success": True})


def _validate_evaluation_coverage(evaluation, questions):
    expected = [question.question_index for question in questions]
    received = [item.question_index for item in evaluation.question_feedback]
    return len(received) == len(expected) and len(set(received)) == len(received) and set(received) == set(expected)


@login_required
@require_POST
def api_complete_interview(request, interview_id):
    with transaction.atomic():
        interview = get_object_or_404(
            Interview.objects.select_for_update(),
            id=interview_id,
            user=request.user,
        )
        if interview.status == "completed" and interview.feedback_data:
            return JsonResponse(
                {"success": True, "alreadyCompleted": True, "feedbackUrl": _feedback_url(interview)}
            )
        if interview.status == "evaluating":
            return JsonResponse(
                {"message": "Feedback is already being generated."},
                status=409,
            )
        allowed_status = "transcript_review" if interview.mode == "voice" else "active"
        if interview.status != allowed_status:
            return JsonResponse({"message": "This interview cannot be completed yet."}, status=409)
        questions = list(interview.questions.all())
        if len(questions) != interview.question_count or any(
            not (question.answer_text or "").strip() for question in questions
        ):
            return JsonResponse(
                {"message": "Complete each interview question before generating feedback."},
                status=400,
            )
        interview.status = "evaluating"
        interview.error_message = ""
        interview.save(update_fields=["status", "error_message", "updated_at"])

    answers = [
        {
            "question_index": question.question_index,
            "question": question.question_text,
            "question_type": question.question_type,
            "focus": question.focus,
            "answer": question.answer_text,
            "answer_mode": question.answer_mode,
        }
        for question in questions
    ]
    try:
        evaluation = evaluate_interview(interview.context_snapshot or {}, answers)
        if not _validate_evaluation_coverage(evaluation, questions):
            raise ValueError("Provider feedback did not cover every question exactly once.")
    except InterviewAIUnavailable as exc:
        Interview.objects.filter(pk=interview.pk, status="evaluating").update(
            status=allowed_status,
            error_message=str(exc),
        )
        return JsonResponse({"message": str(exc)}, status=503)
    except Exception:
        Interview.objects.filter(pk=interview.pk, status="evaluating").update(
            status=allowed_status,
            error_message="Feedback generation failed.",
        )
        return JsonResponse(
            {
                "message": (
                    "CareerGPS couldn't generate your interview feedback right now. "
                    "Your answers are saved, so you can try again."
                )
            },
            status=502,
        )

    feedback_by_index = {item.question_index: item for item in evaluation.question_feedback}
    for question in questions:
        item = feedback_by_index[question.question_index]
        question.evaluation = item.model_dump()
        question.answer_feedback = item.feedback
        question.answer_strengths = item.strengths
        question.answer_improvements = item.improvements

    with transaction.atomic():
        locked = Interview.objects.select_for_update().get(pk=interview.pk)
        if locked.status == "completed" and locked.feedback_data:
            return JsonResponse(
                {"success": True, "alreadyCompleted": True, "feedbackUrl": _feedback_url(locked)}
            )
        if locked.status != "evaluating":
            return JsonResponse({"message": "Interview completion state changed."}, status=409)
        InterviewQuestion.objects.bulk_update(
            questions,
            ["evaluation", "answer_feedback", "answer_strengths", "answer_improvements"],
        )
        locked.feedback_data = evaluation.model_dump()
        locked.feedback_summary = evaluation.overall_summary
        locked.strengths = evaluation.strengths
        locked.improvements = evaluation.improvements
        locked.status = "completed"
        locked.error_message = ""
        locked.save(
            update_fields=[
                "feedback_data",
                "feedback_summary",
                "strengths",
                "improvements",
                "status",
                "error_message",
                "updated_at",
            ]
        )
    return JsonResponse({"success": True, "feedbackUrl": _feedback_url(interview)})


@login_required
@require_http_methods(["DELETE"])
def api_delete_interview(request, interview_id):
    interview = get_object_or_404(Interview, id=interview_id, user=request.user)
    interview.delete()
    return JsonResponse({"success": True})


@login_required
def api_get_interview(request, interview_id):
    interview = get_object_or_404(Interview, id=interview_id, user=request.user)
    questions = list(
        interview.questions.values(
            "id",
            "question_index",
            "question_text",
            "question_type",
            "focus",
            "answer_text",
            "raw_transcript",
            "corrected_transcript",
            "transcription_status",
            "voice_analysis_status",
            "delivery_metrics",
            "visual_analysis_status",
            "visual_metrics",
            "evaluation",
        )
    )
    return JsonResponse(
        {
            "interview": {
                "id": interview.id,
                "status": interview.status,
                "mode": interview.mode,
                "questionDelivery": interview.question_delivery,
                "cameraMode": interview.camera_mode,
                "opportunityId": interview.opportunity_id,
            },
            "questions": questions,
        }
    )
