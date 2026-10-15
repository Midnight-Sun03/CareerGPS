import json
import re
from tempfile import TemporaryDirectory
from unittest.mock import patch
import os
from django.contrib.auth.models import User
from django.core.files.base import ContentFile
from django.test import TestCase, override_settings
from django.urls import reverse

from applications.models import Application
from core.models import (
    Opportunity,
    Profile,
    SavedOpportunity,
)

from .models import (
    Interview,
    InterviewQuestion,
)
from .services.ai import (
    InterviewEvaluation,
    InterviewAIUnavailable,
    InterviewPlan,
    InterviewQuestionPlan,
    evaluate_interview,
    generate_interview_plan,
)
from core.test_support import activate_clean_opportunity
from .services.context import (
    interview_choices_for_user,
)
from .services.delivery_metrics import (
    analyse_delivery,
)


def make_opportunity(
    title="Logistics Coordinator",
    company="Example Co",
):
    opportunity = Opportunity.objects.create(
        title=title,
        company=company,
        opportunity_type="entry_level",
        opportunity_kind="job",
        career_stage="early_career",
        scope_status="in_scope",
        location="Johannesburg",
        location_text="Johannesburg",
        description=(
            "Coordinate logistics and suppliers."
        ),
        lifecycle_status="active",
        is_active=True,
    )
    activate_clean_opportunity(opportunity)
    opportunity.refresh_from_db()
    return opportunity


class InterviewChoiceTests(TestCase):

    def setUp(self):
        self.user = User.objects.create_user(
            "interview-owner",
            password="pass12345",
        )

        Profile.objects.create(
            user=self.user,
            qualification="",
            institution="",
            location="Johannesburg",
        )

    def test_application_is_not_duplicated_in_saved(self):
        opportunity = make_opportunity()

        SavedOpportunity.objects.create(
            user=self.user,
            opportunity=opportunity,
        )

        Application.objects.create(
            user=self.user,
            opportunity=opportunity,
            status="planning",
        )

        choices = interview_choices_for_user(
            self.user
        )

        self.assertEqual(
            len(
                choices["applications"]
            ),
            1,
        )

        self.assertEqual(
            choices[
                "saved_opportunities"
            ],
            [],
        )

    def test_terminal_application_not_in_progress(self):
        opportunity = make_opportunity()

        Application.objects.create(
            user=self.user,
            opportunity=opportunity,
            status="rejected",
        )

        choices = interview_choices_for_user(
            self.user
        )

        self.assertEqual(
            choices["applications"],
            [],
        )


class InterviewCreateTests(TestCase):

    def setUp(self):
        self.user = User.objects.create_user(
            "interview-owner",
            password="pass12345",
        )

        Profile.objects.create(
            user=self.user,
            qualification="",
            institution="",
            location="Johannesburg",
        )

        self.client.login(
            username="interview-owner",
            password="pass12345",
        )

        self.opportunity = (
            make_opportunity()
        )

    @patch(
        "interview.views.generate_interview_plan"
    )
    @patch(
        "interview.views.build_interview_context"
    )
    def test_saved_interview_does_not_create_application(
        self,
        context,
        plan,
    ):
        SavedOpportunity.objects.create(
            user=self.user,
            opportunity=self.opportunity,
        )

        context.return_value = {
            "snapshot": {
                "job": {
                    "title":
                        self.opportunity.title
                },
                "candidate": {
                    "facts": []
                },
            },
            "job_info_status":
                "catalogue_full",
            "candidate_fact_ids": [],
            "generated_document_ids": [],
        }

        plan.return_value = InterviewPlan(
            questions=[
                InterviewQuestionPlan(
                    question=(
                        f"Interview question {index + 1}?"
                    ),
                    question_type="opening",
                    focus="background",
                )
                for index
                in range(5)
            ]
        )

        response = self.client.post(
            "/interview/api/create/",
            data=json.dumps({
                "opportunityId":
                    self.opportunity.id,
                "mode":
                    "written",
                "questionCount":
                    5,
                "popiaConsent":
                    True,
            }),
            content_type="application/json",
        )

        self.assertEqual(
            response.status_code,
            200,
        )

        self.assertFalse(
            Application.objects.filter(
                user=self.user,
                opportunity=(
                    self.opportunity
                ),
            ).exists()
        )

        interview = (
            Interview.objects.get(
                user=self.user
            )
        )

        self.assertEqual(
            interview.source_type,
            "saved",
        )

        self.assertEqual(
            interview.opportunity,
            self.opportunity,
        )

        self.assertEqual(
            interview.questions.count(),
            5,
        )

    def test_unowned_opportunity_denied(self):
        response = self.client.post(
            "/interview/api/create/",
            data=json.dumps({
                "opportunityId":
                    self.opportunity.id,
                "mode":
                    "written",
                "questionCount":
                    5,
                "popiaConsent":
                    True,
            }),
            content_type="application/json",
        )

        self.assertEqual(
            response.status_code,
            403,
        )


class InterviewOwnershipTests(TestCase):

    def setUp(self):
        self.owner = User.objects.create_user(
            "owner",
            password="pass12345",
        )

        self.other = User.objects.create_user(
            "other",
            password="pass12345",
        )

        opportunity = make_opportunity()

        self.interview = (
            Interview.objects.create(
                user=self.owner,
                opportunity=opportunity,
                occupation=(
                    opportunity.title
                ),
                mode="written",
                status="active",
            )
        )

        self.question = (
            InterviewQuestion.objects.create(
                interview=(
                    self.interview
                ),
                question_index=0,
                question_text=(
                    "Tell me about yourself."
                ),
                question_type="opening",
            )
        )

    def test_other_user_cannot_submit_answer(self):
        self.client.login(
            username="other",
            password="pass12345",
        )

        response = self.client.post(
            (
                "/interview/api/question/"
                f"{self.question.id}"
                "/answer-written/"
            ),
            data=json.dumps({
                "answerText":
                    "Not my interview."
            }),
            content_type="application/json",
        )

        self.assertEqual(
            response.status_code,
            404,
        )

    def test_hostile_question_and_saved_answer_are_safe_json_in_both_modes(self):
        hostile = "</script><script>alert(1)</script>"
        self.question.question_text = hostile
        self.question.answer_text = hostile
        self.question.save(update_fields=["question_text", "answer_text"])
        self.client.force_login(self.owner)

        for mode in ("written", "voice"):
            with self.subTest(mode=mode):
                self.interview.mode = mode
                self.interview.save(update_fields=["mode", "updated_at"])
                response = self.client.get(
                    reverse("interview_live", args=[self.interview.pk])
                )
                self.assertEqual(response.status_code, 200)
                page = response.content.decode()
                self.assertNotIn(hostile, page)
                self.assertIn("\\u003C/script\\u003E", page)
                match = re.search(
                    r'<script id="interview-questions-data" type="application/json">(.*?)</script>',
                    page,
                    re.DOTALL,
                )
                self.assertIsNotNone(match)
                questions = json.loads(match.group(1))
                self.assertEqual(questions[0]["question_text"], hostile)
                self.assertEqual(questions[0]["answer_text"], hostile)

    def test_interview_audio_and_video_are_owner_only(self):
        with TemporaryDirectory() as media_root, override_settings(MEDIA_ROOT=media_root):
            self.question.audio_file.save(
                "answer.webm",
                ContentFile(b"private audio"),
            )
            self.question.video_file.save(
                "answer.mp4",
                ContentFile(b"private video"),
            )
            direct_audio_url = self.question.audio_file.url

            self.client.force_login(self.owner)
            for media_type, expected_type, expected_bytes in (
                ("audio", "audio/webm", b"private audio"),
                ("video", "video/mp4", b"private video"),
            ):
                with self.subTest(media_type=media_type):
                    response = self.client.get(
                        reverse("interview_media", args=[self.question.pk, media_type])
                    )
                    self.assertEqual(response.status_code, 200)
                    self.assertEqual(response["Content-Type"], expected_type)
                    self.assertEqual(response["Cache-Control"], "private, no-store")
                    self.assertEqual(response["Pragma"], "no-cache")
                    self.assertEqual(response["X-Content-Type-Options"], "nosniff")
                    self.assertEqual(b"".join(response.streaming_content), expected_bytes)

            feedback = self.client.get(
                reverse("interview_feedback", args=[self.interview.pk])
            )
            protected_url = reverse("interview_media", args=[self.question.pk, "audio"])
            self.assertContains(feedback, protected_url)
            self.assertNotContains(feedback, direct_audio_url)
            self.assertEqual(self.client.get(direct_audio_url).status_code, 404)
            self.client.force_login(self.other)
            self.assertEqual(self.client.get(protected_url).status_code, 404)
            self.client.logout()
            self.assertEqual(self.client.get(protected_url).status_code, 302)


class DeliveryMetricTests(TestCase):

    def test_short_answer_does_not_report_unreliable_pace(self):
        result = analyse_delivery(
            (
                "Um I coordinated supplier "
                "records and uh followed up "
                "on deliveries."
            ),
            duration_seconds=30,
            pause_count=2,
            longest_pause_seconds=2.4,
        )

        self.assertEqual(
            result["pause_count"],
            2,
        )

        self.assertEqual(
            result[
                "longest_pause_seconds"
            ],
            2.4,
        )

        self.assertEqual(
            result[
                "filler_word_count"
            ],
            2,
        )

        self.assertIsNone(
            result[
                "words_per_minute"
            ]
        )

        self.assertEqual(
            result[
                "pace_status"
            ],
            "insufficient_speech",
        )


    def test_longer_answer_reports_speaking_pace(self):
        transcript = (
            "During my internship I worked with the warehouse "
            "and procurement teams to monitor inventory levels "
            "and investigate delivery delays. When a supplier "
            "was late, I reviewed the available records, checked "
            "the stock position with the warehouse team, and "
            "followed up through procurement until the delivery "
            "issue was resolved."
        )

        result = analyse_delivery(
            transcript,
            duration_seconds=30,
            pause_count=1,
            longest_pause_seconds=1.8,
        )

        self.assertIsNotNone(
            result[
                "words_per_minute"
            ]
        )

        self.assertEqual(
            result[
                "pace_status"
            ],
            "available",
        )


    def test_mmm_is_counted_as_hesitation_filler(self):
        result = analyse_delivery(
            "MMM, it's Christmas.",
            duration_seconds=5,
        )

        self.assertEqual(
            result[
                "filler_word_count"
            ],
            1,
        )

        self.assertEqual(
            result[
                "filler_words"
            ].get("mm"),
            1,
        )


@override_settings(GROQ_API_KEY="")
class GeminiFallbackTests(TestCase):

    @staticmethod
    def _question_json():
        return json.dumps({
            "questions": [
                {
                    "question": f"Question {index + 1}?",
                    "question_type": "opening",
                    "focus": "background",
                }
                for index in range(5)
            ]
        })

    @staticmethod
    def _evaluation_json():
        return json.dumps({
            "overall_summary": "Useful practice.",
            "strengths": ["Clear"],
            "improvements": ["More detail"],
            "question_feedback": [{
                "question_index": 0,
                "feedback": "Relevant.",
                "strengths": ["Direct"],
                "improvements": ["Add evidence"],
                "better_approach": "Use an example.",
                "relevance": "strong",
                "structure": "adequate",
                "specificity": "adequate",
            }],
        })

    @override_settings(
        GEMINI_API_KEY="primary-key",
        GEMINI_FALLBACK_API_KEY="fallback-key",
        OPENAI_API_KEY="",
    )
    @patch("CareerGPS_Rebuild.ai_gateway.GeminiAdapter.run")
    @patch("CareerGPS_Rebuild.ai_gateway._failure_category")
    def test_successful_primary_is_the_only_key_for_generation_and_evaluation(
        self,
        should_retry,
        run,
    ):
        run.side_effect = [
            self._question_json(),
            self._evaluation_json(),
        ]

        plan = generate_interview_plan({}, 5)
        evaluation = evaluate_interview(
            {},
            [{"question_index": 0, "question": "Q?", "answer": "A."}],
        )

        self.assertIsInstance(plan, InterviewPlan)
        self.assertIsInstance(evaluation, InterviewEvaluation)
        self.assertEqual(
            [call.args[0] for call in run.call_args_list],
            ["primary-key", "primary-key"],
        )
        should_retry.assert_not_called()

    @override_settings(
        GEMINI_API_KEY="primary-key",
        GEMINI_FALLBACK_API_KEY="fallback-key",
        OPENAI_API_KEY="",
    )
    @patch("CareerGPS_Rebuild.ai_gateway.GeminiAdapter.run")
    @patch(
        "CareerGPS_Rebuild.ai_gateway._failure_category",
        return_value=("transport", True),
    )
    def test_question_generation_uses_exact_fallback_after_eligible_failure(
        self,
        should_retry,
        run,
    ):
        run.side_effect = [TimeoutError("primary timeout"), self._question_json()]

        result = generate_interview_plan({}, 5)

        self.assertIsInstance(result, InterviewPlan)
        self.assertEqual(
            [call.args[0] for call in run.call_args_list],
            ["primary-key", "fallback-key"],
        )
        should_retry.assert_called_once()

    @override_settings(
        GEMINI_API_KEY="primary-key",
        GEMINI_FALLBACK_API_KEY="fallback-key",
        OPENAI_API_KEY="",
    )
    @patch("CareerGPS_Rebuild.ai_gateway.GeminiAdapter.run")
    @patch(
        "CareerGPS_Rebuild.ai_gateway._failure_category",
        return_value=("transport", True),
    )
    def test_final_evaluation_uses_exact_fallback_after_eligible_failure(
        self,
        should_retry,
        run,
    ):
        run.side_effect = [TimeoutError("primary timeout"), self._evaluation_json()]

        result = evaluate_interview(
            {},
            [{"question_index": 0, "question": "Q?", "answer": "A."}],
        )

        self.assertIsInstance(result, InterviewEvaluation)
        self.assertEqual(
            [call.args[0] for call in run.call_args_list],
            ["primary-key", "fallback-key"],
        )
        should_retry.assert_called_once()

    @override_settings(
        GEMINI_API_KEY="primary-key",
        GEMINI_FALLBACK_API_KEY="fallback-key",
        OPENAI_API_KEY="",
    )
    @patch("CareerGPS_Rebuild.ai_gateway.GeminiAdapter.run", side_effect=ValueError("invalid schema"))
    @patch(
        "CareerGPS_Rebuild.ai_gateway._failure_category",
        return_value=("application", False),
    )
    def test_ineligible_primary_failure_does_not_use_fallback(
        self,
        should_retry,
        run,
    ):
        with self.assertRaises(InterviewAIUnavailable):
            generate_interview_plan({}, 5)

        self.assertEqual(run.call_count, 1)
        self.assertEqual(run.call_args.args[0], "primary-key")
        should_retry.assert_called_once()

    @override_settings(
        GEMINI_API_KEY="same-key",
        GEMINI_FALLBACK_API_KEY="same-key",
        OPENAI_API_KEY="",
    )
    @patch("CareerGPS_Rebuild.ai_gateway.GeminiAdapter.run", side_effect=TimeoutError("timeout"))
    def test_same_physical_key_is_not_attempted_twice(self, run):
        with self.assertRaises(InterviewAIUnavailable):
            generate_interview_plan({}, 5)

        self.assertEqual(run.call_count, 1)
        self.assertEqual(run.call_args.args[0], "same-key")

    @override_settings(
        GEMINI_API_KEY="primary-key",
        GEMINI_FALLBACK_API_KEY="fallback-key",
        OPENAI_API_KEY="",
    )
    @patch(
        "CareerGPS_Rebuild.ai_gateway.GeminiAdapter.run"
    )
    @patch(
        "CareerGPS_Rebuild.ai_gateway._failure_category",
        return_value=("provider_transient", True),
    )
    def test_primary_then_exact_gemuini_fallback(
        self,
        should_retry,
        run,
    ):
        from interview.services.ai import (
            _provider_interaction,
        )

        run.side_effect = [
            RuntimeError(
                "Primary unavailable"
            ),
            '{"ok":true}',
        ]

        result = _provider_interaction(
            "prompt",
            "system",
            {},
        )

        self.assertEqual(
            result,
            '{"ok":true}',
        )

        self.assertEqual(
            run.call_args_list[
                0
            ].args[0],
            "primary-key",
        )

        self.assertEqual(
            run.call_args_list[
                1
            ].args[0],
            "fallback-key",
        )

    @override_settings(
        GEMINI_API_KEY="",
        GEMINI_FALLBACK_API_KEY="",
        OPENAI_API_KEY="",
    )
    @patch.dict(
        os.environ,
        {
            "GEMINI_API_KEY": "",
            "GEMuINI_API_KEY": "",
        },
    )
    def test_no_keys_raises_controlled_message(self):
        from interview.services.ai import (
            _provider_interaction,
        )

        with self.assertRaisesMessage(
            InterviewAIUnavailable,
            "CareerGPS interview AI is not configured. Set GEMINI_API_KEY, GEMuINI_API_KEY, GROQ_API_KEY, or OPENAI_API_KEY.",
        ):
            _provider_interaction(
                "prompt",
                "system",
                {},
            )
