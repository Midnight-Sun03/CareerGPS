import json
from types import SimpleNamespace
from unittest.mock import patch

from django.template.loader import render_to_string
from django.test import SimpleTestCase

from interview.services.ai import evaluate_interview


class InterviewFeedbackPresentationTests(SimpleTestCase):
    def test_existing_evaluation_call_requests_natural_faithful_wording(self):
        provider_response = {
            "overall_summary": "You explained the stock process clearly, but did not address discrepancies.",
            "strengths": ["Clear inventory explanation"],
            "improvements": ["Explain how discrepancies are investigated"],
            "question_feedback": [{
                "question_index": 0,
                "feedback": (
                    "You showed a solid understanding of inventory management, "
                    "but did not explain how you would investigate stock discrepancies."
                ),
                "strengths": ["Understood inventory management"],
                "improvements": ["Address stock discrepancies"],
                "better_approach": "Explain the steps you would genuinely take.",
                "relevance": "adequate",
                "structure": "adequate",
                "specificity": "needs_work",
            }],
        }
        context = {"job": "Inventory role"}
        answers = [{"question_index": 0, "answer": "I check stock.",
                    "delivery_metrics": {"duration_seconds": 12.5,
                                         "words_per_minute": 84}}]

        with patch("interview.services.ai._provider_interaction",
                   return_value=json.dumps(provider_response)) as provider:
            evaluation = evaluate_interview(context, answers)

        self.assertEqual(provider.call_count, 1)
        prompt, system_instruction, _schema = provider.call_args.args[:3]
        self.assertIn("natural, concise coaching language", system_instruction)
        self.assertIn("Preserve every substantive strength, weakness, missing point", system_instruction)
        self.assertIn("Do not soften a weak answer", " ".join(system_instruction.split()))
        self.assertIn("Never paraphrase", system_instruction)
        self.assertIn('"duration_seconds": 12.5', prompt)
        self.assertIn('"words_per_minute": 84', prompt)
        self.assertEqual(evaluation.question_feedback[0].specificity, "needs_work")
        self.assertEqual(evaluation.question_feedback[0].strengths,
                         ["Understood inventory management"])
        self.assertEqual(evaluation.question_feedback[0].improvements,
                         ["Address stock discrepancies"])
        self.assertEqual(evaluation.question_feedback[0].better_approach,
                         "Explain the steps you would genuinely take.")

    def test_objective_voice_metrics_render_as_measured(self):
        question = SimpleNamespace(
            answer_mode="voice", voice_analysis_status="available",
            delivery_metrics={
                "duration_seconds": 12.5,
                "speech_duration_seconds": 10.0,
                "recognized_word_count": 42,
                "pace_status": "available",
                "words_per_minute": 84,
                "pause_status": "available",
                "pause_count": 2,
                "longest_pause_seconds": 1.25,
                "filler_status": "available",
                "filler_word_count": 1,
            },
        )

        html = render_to_string("interview/_voice_analysis.html", {"question": question})

        for measured in ("12.5 seconds", "10.0 seconds", "42", "84 WPM",
                         "Meaningful pauses:", "1.25 seconds", "Recognised filler words:"):
            self.assertIn(measured, html)
        self.assertNotIn("saved audio", html.lower())
