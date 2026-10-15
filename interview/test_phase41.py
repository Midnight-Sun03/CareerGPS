import json
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse

from .models import Interview, InterviewQuestion
from .services.visual_metrics import VisualMetricsValidationError, validate_visual_metrics
from .test_phase4 import valid_metrics as legacy_metrics


def adaptive_metrics(*, shoulders=False, hips=False, wrists=False, head_frames=72):
    sampled = 80
    pose_frames = 72 if shoulders or hips or wrists else 0
    status = "complete" if head_frames >= 52 else "partial" if head_frames else "unavailable"
    payload = {
        "schema_version": "careergps-visual-v2",
        "mediapipe_version": "1.0.1",
        "status": status,
        "duration_seconds": 20.0,
        "frames_sampled": sampled,
        "pose_frames": pose_frames,
        "face_frames": head_frames,
        "inference_ms_total": 1000.0,
        "coverage": {
            "shoulder_frames": 60 if shoulders else 0,
            "hip_frames": 60 if hips else 0,
            "wrist_frames": 60 if wrists else 0,
        },
    }
    if head_frames:
        payload["head_orientation"] = {
            "oriented_fraction": 0.8,
            "sustained_away_events": 1,
            "mean_absolute_yaw_degrees": 10.0,
            "mean_absolute_pitch_degrees": 8.0,
            "mean_absolute_roll_degrees": 4.0,
            "stable_position_fraction": 0.75,
            "significant_position_events": 2,
        }
        payload["framing"] = {
            "adequate_fraction": 0.9,
            "outside_frame_frames": 5,
        }
    else:
        payload["unavailable_reason"] = "insufficient_coverage"
    if shoulders:
        payload["shoulders"] = {
            "mean_shoulder_tilt_degrees": 4.0,
            "stable_fraction": 0.8,
            "significant_events": 1,
        }
    if hips:
        payload["hips"] = {"mean_relative_movement": 0.05, "significant_events": 0}
    if wrists:
        payload["gestures"] = {"event_count": 2, "mean_normalized_range": 0.12}
    return payload


class AdaptiveVisualValidationTests(TestCase):
    def test_head_only_accepts_zero_pose_frames_and_no_body_groups(self):
        cleaned = validate_visual_metrics(json.dumps(adaptive_metrics()), browser_duration=20)
        self.assertEqual(cleaned["status"], "complete")
        self.assertEqual(cleaned["analysis_quality"], "high")
        self.assertEqual(cleaned["face_frames"], 72)
        self.assertEqual(cleaned["signal_coverage"]["head"]["status"], "available")
        for name in ("shoulders", "hips", "gestures"):
            self.assertNotIn(name, cleaned)
        self.assertFalse(any("shoulder" in item.lower() or "hip" in item.lower()
                             for item in cleaned["coaching"]["observations"]
                             + cleaned["coaching"]["practice_tips"]))

    def test_head_and_shoulders_need_no_hips(self):
        cleaned = validate_visual_metrics(json.dumps(adaptive_metrics(shoulders=True)))
        self.assertEqual(cleaned["shoulders"]["mean_shoulder_tilt_degrees"], 4.0)
        self.assertNotIn("hips", cleaned)
        self.assertEqual(cleaned["signal_coverage"]["hips"]["status"], "unavailable")

    def test_head_and_hips_need_no_shoulders(self):
        cleaned = validate_visual_metrics(json.dumps(adaptive_metrics(hips=True)))
        self.assertIn("hips", cleaned)
        self.assertNotIn("shoulders", cleaned)

    def test_full_frame_retains_all_optional_groups(self):
        cleaned = validate_visual_metrics(json.dumps(adaptive_metrics(
            shoulders=True, hips=True, wrists=True,
        )))
        for name in ("head_orientation", "framing", "shoulders", "hips", "gestures"):
            self.assertIn(name, cleaned)

    def test_interrupted_optional_landmarks_are_low_coverage_not_zero(self):
        payload = adaptive_metrics()
        payload["pose_frames"] = 20
        payload["coverage"]["shoulder_frames"] = 10
        payload["coverage"]["wrist_frames"] = 8
        cleaned = validate_visual_metrics(json.dumps(payload))
        self.assertEqual(cleaned["signal_coverage"]["shoulders"]["status"], "low_coverage")
        self.assertEqual(cleaned["signal_coverage"]["gestures"]["status"], "low_coverage")
        self.assertNotIn("shoulders", cleaned)
        self.assertNotIn("gestures", cleaned)

    def test_temporarily_lost_head_is_partial_and_absent_head_unavailable(self):
        partial = adaptive_metrics(head_frames=20)
        cleaned = validate_visual_metrics(json.dumps(partial))
        self.assertEqual(cleaned["status"], "partial")
        self.assertEqual(cleaned["signal_coverage"]["head"]["status"], "low_coverage")

        missing = adaptive_metrics(head_frames=0)
        missing["pose_frames"] = 12
        cleaned = validate_visual_metrics(json.dumps(missing))
        self.assertEqual(cleaned["status"], "unavailable")
        self.assertNotIn("head_orientation", cleaned)

    def test_invalid_shoulder_angle_rejected_but_missing_shoulders_accepted(self):
        payload = adaptive_metrics(shoulders=True)
        payload["shoulders"]["mean_shoulder_tilt_degrees"] = 179.0
        with self.assertRaisesMessage(VisualMetricsValidationError, "outside the allowed range"):
            validate_visual_metrics(json.dumps(payload))
        payload = adaptive_metrics()
        self.assertNotIn("shoulders", validate_visual_metrics(json.dumps(payload)))

    def test_nonfinite_unknown_and_browser_coaching_are_rejected(self):
        for mutation in (
            lambda value: value["head_orientation"].update({"mean_absolute_yaw_degrees": float("nan")}),
            lambda value: value.update({"coaching": {"summary": "client-authored"}}),
            lambda value: value["coverage"].update({"raw_landmarks": [[0.5, 0.5]]}),
        ):
            payload = adaptive_metrics()
            mutation(payload)
            with self.subTest(payload=payload), self.assertRaises(VisualMetricsValidationError):
                validate_visual_metrics(json.dumps(payload))

    def test_legacy_v1_payload_remains_accepted(self):
        cleaned = validate_visual_metrics(json.dumps(legacy_metrics()))
        self.assertEqual(cleaned["schema_version"], "careergps-visual-v1")
        self.assertIn("posture", cleaned)


class AdaptiveVisualFlowTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user("adaptive-owner", password="pass12345")
        self.other = User.objects.create_user("adaptive-other", password="pass12345")
        self.interview = Interview.objects.create(
            user=self.owner, mode="voice", camera_mode="camera_analysis",
            camera_consent=True, visual_analysis_version="careergps-visual-v2",
            popia_consent=True, question_count=1, status="active",
        )
        self.question = InterviewQuestion.objects.create(
            interview=self.interview, question_index=0, question_text="Describe your work.",
            answer_mode="voice", transcription_status="pending", visual_analysis_status="ready",
        )
        self.client.force_login(self.owner)

    def test_head_only_audio_upload_and_feedback_show_supported_signals_only(self):
        result = SimpleNamespace(
            transcript="I improved a process.", word_timestamps=[], vad_available=True,
            delivery_metrics={},
        )
        with TemporaryDirectory() as media_root, override_settings(MEDIA_ROOT=media_root), patch(
            "interview.views.transcribe_answer_audio", return_value=result,
        ):
            response = self.client.post(
                reverse("api_submit_voice_answer", args=[self.question.id]),
                data={
                    "durationSeconds": "20",
                    "visualMetrics": json.dumps(adaptive_metrics()),
                    "audio": SimpleUploadedFile("answer.webm", b"audio", content_type="audio/webm"),
                },
            )
        self.assertEqual(response.status_code, 200)
        self.question.refresh_from_db()
        self.assertEqual(self.question.visual_analysis_status, "complete")
        self.assertNotIn("shoulders", self.question.visual_metrics)
        self.assertEqual(self.question.answer_text, "I improved a process.")

        self.interview.status = "completed"
        self.interview.feedback_data = {"overall_summary": "Summary", "strengths": [], "improvements": []}
        self.interview.save(update_fields=["status", "feedback_data"])
        self.question.evaluation = {
            "relevance": "strong", "structure": "strong", "specificity": "strong",
            "feedback": "Clear answer.",
        }
        self.question.save(update_fields=["evaluation"])
        page = self.client.get(reverse("interview_feedback", args=[self.interview.id])).content.decode()
        self.assertIn("3. Visual Interview Coaching", page)
        self.assertIn("Usable head frames", page)
        self.assertNotIn("Additional shoulder observations", page)
        self.assertNotIn("Additional torso observations", page)
        self.assertNotIn("Additional visible hand observations", page)
        self.client.force_login(self.other)
        self.assertEqual(self.client.get(reverse("interview_feedback", args=[self.interview.id])).status_code, 404)

    def test_feedback_cards_follow_independent_optional_coverage(self):
        self.interview.status = "completed"
        self.interview.feedback_data = {"overall_summary": "Summary", "strengths": [], "improvements": []}
        self.interview.save(update_fields=["status", "feedback_data"])
        self.question.evaluation = {"feedback": "Clear answer."}
        for shoulders, hips, wrists in (
            (True, False, False),
            (False, True, False),
            (True, True, True),
        ):
            with self.subTest(shoulders=shoulders, hips=hips, wrists=wrists):
                self.question.visual_metrics = validate_visual_metrics(json.dumps(adaptive_metrics(
                    shoulders=shoulders, hips=hips, wrists=wrists,
                )))
                self.question.visual_analysis_status = "complete"
                self.question.save(update_fields=["visual_metrics", "visual_analysis_status", "evaluation"])
                page = self.client.get(reverse("interview_feedback", args=[self.interview.id])).content.decode()
                self.assertEqual("Additional shoulder observations" in page, shoulders)
                self.assertEqual("Additional torso observations" in page, hips)
                self.assertEqual("Additional visible hand observations" in page, wrists)
