import json
import hashlib
import inspect
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse

from core.models import Opportunity, Profile, SavedOpportunity
from core.test_support import activate_clean_opportunity

from .models import Interview, InterviewQuestion
from .services.ai import InterviewPlan, InterviewQuestionPlan
from .services.visual_metrics import (
    MAX_VISUAL_PAYLOAD_BYTES,
    VisualMetricsValidationError,
    build_visual_coaching,
    validate_visual_metrics,
)
from .views import api_complete_interview


def opportunity():
    row = Opportunity.objects.create(
        title="Operations Analyst",
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


def valid_metrics(*, status="complete", duration=20.0):
    return {
        "schema_version": "careergps-visual-v1",
        "mediapipe_version": "1.0.1",
        "status": status,
        "duration_seconds": duration,
        "frames_sampled": 80,
        "pose_frames": 72,
        "face_frames": 65,
        "inference_ms_total": 1600.0,
        "posture": {
            "baseline_frames": 15,
            "stable_fraction": 0.8,
            "sustained_lean_events": 0,
            "large_shift_events": 0,
            "mean_shoulder_tilt_degrees": 3.0,
        },
        "movement": {"mean_normalized_displacement": 0.03, "significant_events": 0},
        "gestures": {"visible_fraction": 0.75, "event_count": 2, "mean_normalized_range": 0.12},
        "head_orientation": {
            "oriented_fraction": 0.82,
            "sustained_away_events": 0,
            "mean_absolute_yaw_degrees": 8.0,
            "mean_absolute_pitch_degrees": 6.0,
            "mean_absolute_roll_degrees": 2.0,
        },
        "framing": {
            "adequate_fraction": 0.9,
            "outside_frame_frames": 8,
            "missing_landmark_frames": 8,
            "camera_instability_events": 0,
        },
    }


def unavailable_metrics(duration=20.0, reason="candidate_continued_audio_only"):
    payload = valid_metrics(duration=duration)
    payload.update(
        status="unavailable",
        unavailable_reason=reason,
        frames_sampled=0,
        pose_frames=0,
        face_frames=0,
        inference_ms_total=0,
    )
    for group, values in payload.items():
        if isinstance(values, dict):
            for key, value in values.items():
                if isinstance(value, int):
                    values[key] = 0
                elif isinstance(value, float):
                    values[key] = None
    return payload


class VisualMetricsValidationTests(TestCase):
    def test_complete_payload_is_normalized_and_coaching_is_server_authored(self):
        payload = valid_metrics()
        cleaned = validate_visual_metrics(json.dumps(payload), browser_duration=20)
        self.assertEqual(cleaned["status"], "complete")
        self.assertEqual(cleaned["analysis_quality"], "high")
        self.assertEqual(cleaned["coaching"], build_visual_coaching(cleaned))
        self.assertNotIn("coaching", payload)

    def test_zero_is_preserved_as_a_measurement(self):
        cleaned = validate_visual_metrics(json.dumps(valid_metrics()))
        self.assertEqual(cleaned["movement"]["significant_events"], 0)
        self.assertEqual(cleaned["framing"]["camera_instability_events"], 0)

    def test_unavailable_payload_requires_known_reason_and_no_frames(self):
        cleaned = validate_visual_metrics(json.dumps(unavailable_metrics()))
        self.assertEqual(cleaned["analysis_quality"], "unavailable")
        broken = unavailable_metrics()
        broken["pose_frames"] = 1
        with self.assertRaises(VisualMetricsValidationError):
            validate_visual_metrics(json.dumps(broken))

    def test_unknown_raw_or_nested_fields_are_rejected(self):
        for mutator in (
            lambda value: value.update({"landmarks": [[0.1, 0.2]]}),
            lambda value: value["posture"].update({"raw_frames": "data"}),
            lambda value: value.update({"video": "data:video/webm;base64,AAAA"}),
        ):
            with self.subTest(mutator=mutator):
                payload = valid_metrics()
                mutator(payload)
                with self.assertRaises(VisualMetricsValidationError):
                    validate_visual_metrics(json.dumps(payload))

    def test_impossible_counts_ranges_and_duration_mismatch_are_rejected(self):
        payload = valid_metrics()
        payload["pose_frames"] = 81
        with self.assertRaises(VisualMetricsValidationError):
            validate_visual_metrics(json.dumps(payload))
        payload = valid_metrics()
        payload["posture"]["stable_fraction"] = 1.1
        with self.assertRaises(VisualMetricsValidationError):
            validate_visual_metrics(json.dumps(payload))
        with self.assertRaises(VisualMetricsValidationError):
            validate_visual_metrics(json.dumps(valid_metrics(duration=40)), browser_duration=20)

    def test_oversized_or_wrong_version_payload_is_rejected(self):
        payload = json.dumps(valid_metrics()) + (" " * MAX_VISUAL_PAYLOAD_BYTES)
        with self.assertRaises(VisualMetricsValidationError):
            validate_visual_metrics(payload)
        wrong = valid_metrics()
        wrong["schema_version"] = "future-version"
        with self.assertRaises(VisualMetricsValidationError):
            validate_visual_metrics(json.dumps(wrong))

    def test_pinned_model_assets_match_the_setup_checksums(self):
        model_root = Path(__file__).parent / "static" / "interview" / "models" / "mediapipe"
        expected = {
            "pose_landmarker_lite-v1.task": "59929e1d1ee95287735ddd833b19cf4ac46d29bc7afddbbf6753c459690d574a",
            "face_landmarker-v1.task": "64184e229b263107bc2b804c6625db1341ff2bb731874b0bcc2fe6544e0bc9ff",
        }
        for filename, checksum in expected.items():
            with self.subTest(filename=filename):
                self.assertEqual(hashlib.sha256((model_root / filename).read_bytes()).hexdigest(), checksum)

    def test_gemini_evaluation_path_does_not_include_visual_data(self):
        completion_source = inspect.getsource(api_complete_interview)
        self.assertNotIn("visual_metrics", completion_source)
        self.assertNotIn("visual_analysis", completion_source)


class Phase4InterviewFlowTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user("camera-owner", password="pass12345")
        self.other = User.objects.create_user("camera-other", password="pass12345")
        Profile.objects.create(user=self.owner, qualification="", institution="", location="Johannesburg")
        self.opportunity = opportunity()
        SavedOpportunity.objects.create(user=self.owner, opportunity=self.opportunity)
        self.client.force_login(self.owner)

    def _plan(self):
        return InterviewPlan(
            questions=[
                InterviewQuestionPlan(
                    question=f"Question {index + 1}?",
                    question_type="opening",
                    focus="experience",
                )
                for index in range(5)
            ]
        )

    def _create(self, *, mode="voice", camera_mode="camera_analysis", consent=True):
        with patch("interview.views.build_interview_context") as context, patch(
            "interview.views.generate_interview_plan", return_value=self._plan()
        ):
            context.return_value = {
                "snapshot": {"job": {}, "candidate": {"facts": []}},
                "job_info_status": "catalogue_full",
                "candidate_fact_ids": [],
                "generated_document_ids": [],
            }
            return self.client.post(
                reverse("api_create_interview"),
                data=json.dumps(
                    {
                        "opportunityId": self.opportunity.id,
                        "mode": mode,
                        "questionCount": 5,
                        "questionDelivery": "text_voice",
                        "popiaConsent": True,
                        "cameraMode": camera_mode,
                        "cameraConsent": consent,
                    }
                ),
                content_type="application/json",
            )

    def test_camera_mode_requires_explicit_consent(self):
        response = self._create(consent=False)
        self.assertEqual(response.status_code, 400)
        self.assertContains(response, "Explicit camera-analysis consent", status_code=400)
        self.assertFalse(Interview.objects.exists())

    def test_written_mode_forces_camera_off(self):
        response = self._create(mode="written", consent=True)
        self.assertEqual(response.status_code, 200)
        interview = Interview.objects.get()
        self.assertEqual(interview.camera_mode, "audio_only")
        self.assertFalse(interview.camera_consent)
        self.assertEqual(interview.visual_analysis_version, "")
        self.assertFalse(interview.questions.exclude(visual_analysis_status="not_requested").exists())

    def test_camera_mode_and_ready_question_state_are_persisted(self):
        self.assertEqual(self._create().status_code, 200)
        interview = Interview.objects.get()
        self.assertEqual(interview.camera_mode, "camera_analysis")
        self.assertTrue(interview.camera_consent)
        self.assertEqual(interview.visual_analysis_version, "careergps-visual-v2")
        self.assertFalse(interview.questions.exclude(visual_analysis_status="ready").exists())

    def test_historical_defaults_do_not_fabricate_analysis(self):
        interview = Interview.objects.create(user=self.owner, mode="voice", status="completed")
        question = InterviewQuestion.objects.create(
            interview=interview,
            question_index=0,
            question_text="Old question",
            answer_mode="voice",
        )
        self.assertEqual(interview.camera_mode, "audio_only")
        self.assertFalse(interview.camera_consent)
        self.assertEqual(question.visual_analysis_status, "not_requested")
        self.assertEqual(question.visual_metrics, {})

    def _camera_interview(self):
        interview = Interview.objects.create(
            user=self.owner,
            opportunity=self.opportunity,
            mode="voice",
            camera_mode="camera_analysis",
            camera_consent=True,
            visual_analysis_version="careergps-visual-v1",
            popia_consent=True,
            question_count=1,
            status="active",
        )
        question = InterviewQuestion.objects.create(
            interview=interview,
            question_index=0,
            question_text="Describe your experience.",
            answer_mode="voice",
            transcription_status="pending",
            voice_analysis_status="pending",
            visual_analysis_status="ready",
        )
        return interview, question

    def test_voice_upload_validates_before_saving_and_persists_compact_metrics(self):
        interview, question = self._camera_interview()
        transcribed = SimpleNamespace(
            transcript="I improved the process.",
            word_timestamps=[],
            vad_available=True,
            delivery_metrics={},
        )
        with TemporaryDirectory() as media_root, override_settings(MEDIA_ROOT=media_root), patch(
            "interview.views.transcribe_answer_audio", return_value=transcribed
        ):
            response = self.client.post(
                reverse("api_submit_voice_answer", args=[question.id]),
                data={
                    "durationSeconds": "20",
                    "provisionalTranscript": "I improved the process.",
                    "visualMetrics": json.dumps(valid_metrics()),
                    "audio": SimpleUploadedFile("answer.webm", b"audio", content_type="audio/webm"),
                },
            )
        self.assertEqual(response.status_code, 200)
        question.refresh_from_db()
        self.assertEqual(question.visual_analysis_status, "complete")
        self.assertEqual(question.visual_metrics["analysis_quality"], "high")
        self.assertIn("coaching", question.visual_metrics)
        self.assertNotIn("landmarks", question.visual_metrics)

    def test_invalid_visual_payload_does_not_save_audio(self):
        _, question = self._camera_interview()
        payload = valid_metrics()
        payload["frames"] = "raw"
        with TemporaryDirectory() as media_root, override_settings(MEDIA_ROOT=media_root):
            response = self.client.post(
                reverse("api_submit_voice_answer", args=[question.id]),
                data={
                    "durationSeconds": "20",
                    "visualMetrics": json.dumps(payload),
                    "audio": SimpleUploadedFile("answer.webm", b"audio", content_type="audio/webm"),
                },
            )
            question.refresh_from_db()
            self.assertEqual(response.status_code, 400)
            self.assertFalse(question.audio_file)

    def test_camera_answer_requires_metrics_and_audio_only_rejects_them(self):
        _, camera_question = self._camera_interview()
        response = self.client.post(
            reverse("api_submit_voice_answer", args=[camera_question.id]),
            data={
                "durationSeconds": "20",
                "audio": SimpleUploadedFile("answer.webm", b"audio", content_type="audio/webm"),
            },
        )
        self.assertEqual(response.status_code, 400)

        audio_interview = Interview.objects.create(
            user=self.owner,
            mode="voice",
            camera_mode="audio_only",
            popia_consent=True,
            status="active",
        )
        audio_question = InterviewQuestion.objects.create(
            interview=audio_interview,
            question_index=0,
            question_text="Audio question",
            answer_mode="voice",
            transcription_status="pending",
        )
        response = self.client.post(
            reverse("api_submit_voice_answer", args=[audio_question.id]),
            data={
                "durationSeconds": "20",
                "visualMetrics": json.dumps(valid_metrics()),
                "audio": SimpleUploadedFile("answer.webm", b"audio", content_type="audio/webm"),
            },
        )
        self.assertEqual(response.status_code, 400)

    def test_camera_failure_payload_keeps_audio_path_available(self):
        _, question = self._camera_interview()
        transcribed = SimpleNamespace(
            transcript="Audio remains authoritative.",
            word_timestamps=[],
            vad_available=False,
            delivery_metrics={},
        )
        with TemporaryDirectory() as media_root, override_settings(MEDIA_ROOT=media_root), patch(
            "interview.views.transcribe_answer_audio", return_value=transcribed
        ):
            response = self.client.post(
                reverse("api_submit_voice_answer", args=[question.id]),
                data={
                    "durationSeconds": "20",
                    "visualMetrics": json.dumps(unavailable_metrics()),
                    "audio": SimpleUploadedFile("answer.webm", b"audio", content_type="audio/webm"),
                },
            )
        self.assertEqual(response.status_code, 200)
        question.refresh_from_db()
        self.assertEqual(question.answer_text, "Audio remains authoritative.")
        self.assertEqual(question.visual_analysis_status, "unavailable")

    def test_live_template_binds_collection_to_answer_boundaries(self):
        interview, _ = self._camera_interview()
        response = self.client.get(reverse("interview_live", args=[interview.id]))
        page = response.content.decode()
        self.assertContains(response, "No video, image, or landmark stream is uploaded or stored")
        self.assertLess(page.index("setQuestionState(ANSWERING)"), page.index("cameraController.startAnswer"))
        self.assertLess(page.index("setQuestionState(ANSWER_COMPLETE)"), page.index("cameraController.finishAnswer"))
        worker = Path(__file__).parent / "static" / "interview" / "js" / "mediapipe_worker.js"
        controller = Path(__file__).parent / "static" / "interview" / "js" / "camera_analysis.js"
        self.assertIn("setInterval", controller.read_text(encoding="utf-8"))
        self.assertIn("125", controller.read_text(encoding="utf-8"))
        self.assertIn("!this.samplingEnabled", controller.read_text(encoding="utf-8"))
        self.assertIn("new Worker(this.config.workerUrl);", controller.read_text(encoding="utf-8"))
        self.assertNotIn("postMessage({type: \"frame\"", worker.read_text(encoding="utf-8"))

    def test_camera_readiness_uses_progress_timeouts_and_real_detection(self):
        static_root = Path(__file__).parent / "static" / "interview" / "js"
        controller = (static_root / "camera_analysis.js").read_text(encoding="utf-8")
        metrics = (static_root / "visual_metrics.js").read_text(encoding="utf-8")
        template = (
            Path(__file__).parent / "templates" / "interview" / "live.html"
        ).read_text(encoding="utf-8")

        self.assertNotIn("Model setup timed out.\")), 30000", controller)
        self.assertIn("resetInactivityTimeout();", controller)
        self.assertIn("INITIALIZATION_TOTAL_TIMEOUT_MS", controller)
        self.assertIn("if (this.worker) this.worker.terminate();", controller)
        self.assertIn("FIRST_FRAME_TIMEOUT_MS", controller)
        self.assertIn("await this.#waitForVideoDimensions()", controller)
        self.assertIn("coverage.analysisReady", controller)
        self.assertIn("READY_FRAME_STREAK = 3", controller)
        self.assertIn("Camera Analysis READY", controller)
        self.assertIn("Reposition so your face stays inside the preview", controller)
        self.assertIn("analysisReady: Boolean(sample.head?.framed)", metrics)
        self.assertIn("beginButton.disabled = true", template)
        self.assertIn("cameraUnavailableReason = null", template)
        self.assertIn("Retry Camera Analysis", template)

    def test_feedback_is_separate_private_and_uses_non_psychological_labels(self):
        interview, question = self._camera_interview()
        interview.status = "completed"
        interview.feedback_data = {"overall_summary": "Summary", "strengths": [], "improvements": []}
        interview.save(update_fields=["status", "feedback_data", "updated_at"])
        question.answer_text = "Answer"
        question.visual_analysis_status = "complete"
        question.visual_metrics = validate_visual_metrics(json.dumps(valid_metrics()))
        question.evaluation = {
            "relevance": "strong",
            "structure": "strong",
            "specificity": "strong",
            "feedback": "Useful answer.",
        }
        question.save(update_fields=["answer_text", "visual_analysis_status", "visual_metrics", "evaluation"])
        response = self.client.get(reverse("interview_feedback", args=[interview.id]))
        page = response.content.decode()
        self.assertLess(page.index("1. Answer Analysis"), page.index("2. Voice Analysis"))
        self.assertLess(page.index("2. Voice Analysis"), page.index("3. Camera / Body Analysis"))
        for banned in ("eye contact", "nervous", "emotion", "personality", "body confidence"):
            self.assertNotIn(banned, page.lower())
        self.client.force_login(self.other)
        self.assertEqual(self.client.get(reverse("interview_feedback", args=[interview.id])).status_code, 404)
