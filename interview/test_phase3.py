import json
from tempfile import TemporaryDirectory
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse

from applications.models import Application
from core.models import Opportunity, Profile, SavedOpportunity
from core.test_support import activate_clean_opportunity

from .models import Interview, InterviewQuestion
from .services.ai import (
    InterviewEvaluation,
    InterviewPlan,
    InterviewQuestionPlan,
    QuestionEvaluation,
)
from .services.delivery_metrics import analyse_delivery, unavailable_delivery
from .services.transcription import AuthoritativeVoiceResult, TranscriptionUnavailable


def opportunity():
    row = Opportunity.objects.create(
        title="Data Analyst",
        company="Example Co",
        opportunity_type="entry_level",
        opportunity_kind="job",
        career_stage="early_career",
        scope_status="in_scope",
        location="Johannesburg",
        location_text="Johannesburg",
        description="Analyse operational data.",
        lifecycle_status="active",
        is_active=True,
    )
    activate_clean_opportunity(row)
    row.refresh_from_db()
    return row


def evaluation_for(indices):
    return InterviewEvaluation(
        overall_summary="A useful practice interview.",
        strengths=["Clear examples"],
        improvements=["Add outcomes"],
        question_feedback=[
            QuestionEvaluation(
                question_index=index,
                feedback="Relevant answer.",
                strengths=["Relevant"],
                improvements=["More detail"],
                better_approach="Use a specific example.",
                relevance="strong",
                structure="adequate",
                specificity="adequate",
            )
            for index in indices
        ],
    )


class Phase3Base(TestCase):
    def setUp(self):
        self.media_root = TemporaryDirectory()
        self.media_override = override_settings(MEDIA_ROOT=self.media_root.name)
        self.media_override.enable()
        self.addCleanup(self.media_override.disable)
        self.addCleanup(self.media_root.cleanup)
        self.user = User.objects.create_user("phase3", password="pass12345")
        Profile.objects.create(
            user=self.user,
            qualification="",
            institution="",
            location="Johannesburg",
        )
        self.opportunity = opportunity()
        SavedOpportunity.objects.create(user=self.user, opportunity=self.opportunity)
        self.client.force_login(self.user)

    def make_interview(self, *, mode="voice", count=1, status="active"):
        interview = Interview.objects.create(
            user=self.user,
            opportunity=self.opportunity,
            source_type="saved",
            mode=mode,
            question_delivery="text_voice" if mode == "voice" else "text_only",
            question_count=count,
            popia_consent=True,
            status=status,
            context_snapshot={"job": {"title": "Data Analyst"}},
            occupation="Data Analyst",
        )
        for index in range(count):
            InterviewQuestion.objects.create(
                interview=interview,
                question_index=index,
                question_text=f"Stored question {index + 1}?",
                answer_mode=mode,
                transcription_status="pending" if mode == "voice" else "not_applicable",
                voice_analysis_status="pending" if mode == "voice" else "not_applicable",
            )
        return interview


class InterviewSetupPhase3Tests(Phase3Base):
    def test_active_application_remains_selectable(self):
        Application.objects.create(
            user=self.user,
            opportunity=self.opportunity,
            status="planning",
        )
        response = self.client.get(reverse("interview_home"))
        self.assertContains(response, "Applications in progress")
        self.assertContains(
            response,
            reverse("interview_prepare", args=[self.opportunity.pk]),
        )

    @patch("interview.views.generate_interview_plan")
    @patch("interview.views.build_interview_context")
    def test_lengths_modes_sources_and_question_delivery_are_preserved(self, context, plan):
        context.return_value = {
            "snapshot": {},
            "job_info_status": "catalogue_full",
            "candidate_fact_ids": [],
            "generated_document_ids": [],
        }
        for count in (5, 8, 10):
            with self.subTest(count=count):
                plan.return_value = InterviewPlan(
                    questions=[
                        InterviewQuestionPlan(
                            question=f"Question {index + 1}?",
                            question_type="opening",
                            focus="background",
                        )
                        for index in range(count)
                    ]
                )
                response = self.client.post(
                    reverse("api_create_interview"),
                    data=json.dumps({
                        "opportunityId": self.opportunity.id,
                        "mode": "voice",
                        "questionCount": count,
                        "questionDelivery": "voice_only",
                        "popiaConsent": True,
                    }),
                    content_type="application/json",
                )
                self.assertEqual(response.status_code, 200)
                created = Interview.objects.get(pk=response.json()["interviewId"])
                self.assertEqual(created.source_type, "saved")
                self.assertEqual(created.mode, "voice")
                self.assertEqual(created.question_delivery, "voice_only")
                self.assertEqual(created.questions.count(), count)

    def test_invalid_question_delivery_is_rejected(self):
        response = self.client.post(
            reverse("api_create_interview"),
            data=json.dumps({
                "opportunityId": self.opportunity.id,
                "mode": "voice",
                "questionCount": 5,
                "questionDelivery": "paraphrase_with_ai",
                "popiaConsent": True,
            }),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400)


class SpokenStateMachineTemplateTests(Phase3Base):
    def test_state_machine_tts_and_microphone_guards_are_rendered(self):
        interview = self.make_interview()
        response = self.client.get(reverse("interview_live", args=[interview.pk]))
        page = response.content.decode()
        self.assertContains(response, "Microphone check")
        for state in (
            "QUESTION_READY",
            "LISTENING",
            "READY_TO_ANSWER",
            "ANSWERING",
            "ANSWER_COMPLETE",
        ):
            self.assertIn(state, page)
        self.assertIn("new SpeechSynthesisUtterance(exactStoredQuestion())", page)
        self.assertIn("return QUESTIONS[currentIndex].question_text", page)
        self.assertIn("questionState !== READY_TO_ANSWER", page)
        self.assertIn("questionState === ANSWERING", page)
        self.assertIn("Interviewer voice is unavailable", page)
        self.assertNotIn("Review and correct", page)


class AuthoritativeVoicePipelineTests(Phase3Base):
    def raw_result(self):
        metrics = analyse_delivery(
            "Um I analysed the inventory records with the warehouse team and presented the findings to managers after checking every unusual movement.",
            duration_seconds=12,
            speech_duration_seconds=10,
            pause_count=1,
            longest_pause_seconds=1.4,
        )
        return AuthoritativeVoiceResult(
            transcript="Um I analysed the inventory records.",
            word_timestamps=[{"word": "Um", "start": 0.1, "end": 0.3, "probability": 0.9}],
            delivery_metrics=metrics,
            vad_available=True,
        )

    @patch("interview.views.transcribe_answer_audio")
    def test_audio_creates_raw_transcript_and_correction_does_not_change_voice_evidence(self, transcribe):
        transcribe.return_value = self.raw_result()
        interview = self.make_interview()
        question = interview.questions.get()
        response = self.client.post(
            reverse("api_submit_voice_answer", args=[question.pk]),
            data={
                "audio": SimpleUploadedFile("answer.webm", b"audio", content_type="audio/webm"),
                "durationSeconds": "12.4",
                "provisionalTranscript": "Browser guessed text",
            },
        )
        self.assertEqual(response.status_code, 200)
        question.refresh_from_db()
        interview.refresh_from_db()
        original_metrics = question.delivery_metrics.copy()
        self.assertEqual(question.raw_transcript, "Um I analysed the inventory records.")
        self.assertEqual(question.corrected_transcript, question.raw_transcript)
        self.assertEqual(question.provisional_transcript, "Browser guessed text")
        self.assertEqual(question.transcription_status, "available")
        self.assertEqual(interview.status, "transcript_review")

        response = self.client.post(
            reverse("api_review_transcripts", args=[interview.pk]),
            data=json.dumps({
                "transcripts": [{
                    "questionId": question.pk,
                    "transcript": "I analysed the inventory records.",
                }]
            }),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        question.refresh_from_db()
        self.assertEqual(question.raw_transcript, "Um I analysed the inventory records.")
        self.assertEqual(question.corrected_transcript, "I analysed the inventory records.")
        self.assertEqual(question.answer_text, question.corrected_transcript)
        self.assertEqual(question.delivery_metrics, original_metrics)
        self.assertEqual(question.delivery_metrics["filler_word_count"], 1)

    @patch("interview.views.transcribe_answer_audio", side_effect=TranscriptionUnavailable("missing model"))
    def test_model_failure_preserves_audio_and_uses_unavailable_not_zero(self, transcribe):
        interview = self.make_interview()
        question = interview.questions.get()
        response = self.client.post(
            reverse("api_submit_voice_answer", args=[question.pk]),
            data={
                "audio": SimpleUploadedFile("answer.webm", b"audio", content_type="audio/webm"),
                "durationSeconds": "8.2",
                "provisionalTranscript": "A provisional transcript",
            },
        )
        self.assertEqual(response.status_code, 200)
        question.refresh_from_db()
        self.assertTrue(question.audio_file)
        self.assertEqual(question.transcription_status, "unavailable")
        self.assertEqual(question.voice_analysis_status, "unavailable")
        self.assertIsNone(question.delivery_metrics["filler_word_count"])
        self.assertIsNone(question.delivery_metrics["pause_count"])
        self.assertEqual(question.delivery_metrics["analysis_status"], "unavailable")

    def test_wpm_uses_actual_speech_duration(self):
        transcript = " ".join(["word"] * 30)
        result = analyse_delivery(
            transcript,
            duration_seconds=30,
            speech_duration_seconds=15,
            pause_count=2,
            longest_pause_seconds=2,
        )
        self.assertEqual(result["words_per_minute"], 120.0)
        self.assertEqual(result["silence_duration_seconds"], 15.0)
        unavailable = unavailable_delivery(reason="vad_failed")
        self.assertIsNone(unavailable["longest_pause_seconds"])


class CompletionSafetyTests(Phase3Base):
    @patch("interview.views.evaluate_interview")
    def test_correction_is_gemini_input_and_completion_is_idempotent(self, evaluate):
        interview = self.make_interview(status="transcript_review")
        question = interview.questions.get()
        question.audio_file = "interviews/audio/answer.webm"
        question.raw_transcript = "Um I said sequel."
        question.corrected_transcript = "I said SQL."
        question.answer_text = "I said SQL."
        question.transcription_status = "available"
        question.voice_analysis_status = "available"
        question.delivery_metrics = {"filler_word_count": 1, "pause_count": 2}
        question.save()
        evaluate.return_value = evaluation_for([0])

        url = reverse("api_complete_interview", args=[interview.pk])
        first = self.client.post(url)
        second = self.client.post(url)
        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        self.assertTrue(second.json()["alreadyCompleted"])
        evaluate.assert_called_once()
        supplied_answers = evaluate.call_args.args[1]
        self.assertEqual(supplied_answers[0]["answer"], "I said SQL.")
        self.assertNotIn("delivery_metrics", supplied_answers[0])

        question.refresh_from_db()
        metrics_before = question.delivery_metrics.copy()
        edit = self.client.post(
            reverse("api_review_transcripts", args=[interview.pk]),
            data=json.dumps({
                "transcripts": [{"questionId": question.pk, "transcript": "Changed later"}]
            }),
            content_type="application/json",
        )
        self.assertEqual(edit.status_code, 404)
        question.refresh_from_db()
        self.assertEqual(question.answer_text, "I said SQL.")
        self.assertEqual(question.delivery_metrics, metrics_before)

    @patch("interview.views.evaluate_interview")
    def test_missing_or_duplicate_provider_feedback_is_not_success(self, evaluate):
        interview = self.make_interview(mode="written", count=2)
        for question in interview.questions.all():
            question.answer_text = f"Answer {question.question_index}"
            question.save(update_fields=["answer_text"])
        evaluate.return_value = evaluation_for([0, 0])
        response = self.client.post(reverse("api_complete_interview", args=[interview.pk]))
        self.assertEqual(response.status_code, 502)
        interview.refresh_from_db()
        self.assertEqual(interview.status, "active")
        self.assertFalse(interview.feedback_data)


class TranscriptReviewSecurityTests(Phase3Base):
    def test_hostile_transcript_and_question_remain_inert(self):
        hostile = "</textarea><script>alert(1)</script>"
        interview = self.make_interview(status="transcript_review")
        question = interview.questions.get()
        question.question_text = hostile
        question.corrected_transcript = hostile
        question.audio_file = "interviews/audio/answer.webm"
        question.transcription_status = "available"
        question.save()
        response = self.client.get(reverse("interview_transcript_review", args=[interview.pk]))
        page = response.content.decode()
        self.assertEqual(response.status_code, 200)
        self.assertNotIn(hostile, page)
        self.assertIn("&lt;/textarea&gt;&lt;script&gt;", page)
