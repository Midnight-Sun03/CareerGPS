import json
from types import SimpleNamespace
from unittest.mock import patch

from django.test import SimpleTestCase, override_settings

from CareerGPS_Rebuild.ai_gateway import (
    AIConfigurationError,
    AIContractError,
    AIProviderUnavailable,
    GroqAdapter,
    OpenAIAdapter,
    StructuredTask,
    _failure_category,
    _strict_schema,
    run_structured_task,
)


class GatewayTests(SimpleTestCase):
    def task(self):
        return StructuredTask(
            name="test_task", prompt="prompt", system_instruction="system",
            schema={"type": "object", "properties": {"ok": {"type": "boolean"}},
                    "required": ["ok"]},
            gemini_model="gemini-test", thinking_level="low", timeout_seconds=10,
        )

    @override_settings(GEMINI_API_KEY="primary", GEMINI_FALLBACK_API_KEY="fallback",
                       OPENAI_API_KEY="openai", OPENAI_MODEL="openai-test")
    @patch("CareerGPS_Rebuild.ai_gateway.OpenAIAdapter.run")
    @patch("CareerGPS_Rebuild.ai_gateway.GeminiAdapter.run")
    def test_primary_success_stops_chain(self, gemini, openai):
        gemini.return_value = '{"ok":true}'
        self.assertEqual(run_structured_task(self.task()), '{"ok":true}')
        self.assertEqual([c.args[0] for c in gemini.call_args_list], ["primary"])
        openai.assert_not_called()

    @override_settings(GEMINI_API_KEY="primary", GEMINI_FALLBACK_API_KEY="fallback",
                       OPENAI_API_KEY="openai", OPENAI_MODEL="openai-test")
    @patch("CareerGPS_Rebuild.ai_gateway.OpenAIAdapter.run")
    @patch("CareerGPS_Rebuild.ai_gateway.GeminiAdapter.run")
    def test_gemini_fallback_success_stops_before_openai(self, gemini, openai):
        gemini.side_effect = [TimeoutError("timeout"), '{"ok":true}']
        self.assertEqual(run_structured_task(self.task()), '{"ok":true}')
        self.assertEqual([c.args[0] for c in gemini.call_args_list], ["primary", "fallback"])
        openai.assert_not_called()

    @override_settings(GEMINI_API_KEY="primary", GEMINI_FALLBACK_API_KEY="fallback",
                       OPENAI_API_KEY="openai", OPENAI_MODEL="openai-test")
    @patch("CareerGPS_Rebuild.ai_gateway.OpenAIAdapter.run", return_value='{"ok":true}')
    @patch("CareerGPS_Rebuild.ai_gateway.GeminiAdapter.run")
    def test_openai_after_both_gemini_fail(self, gemini, openai):
        gemini.side_effect = [TimeoutError("timeout"), ConnectionError("disconnected")]
        self.assertEqual(run_structured_task(self.task()), '{"ok":true}')
        self.assertEqual([c.args[0] for c in gemini.call_args_list], ["primary", "fallback"])
        openai.assert_called_once()
        self.assertEqual(openai.call_args.args[0], "openai")

    @override_settings(GEMINI_API_KEY="primary", GEMINI_FALLBACK_API_KEY="fallback",
                       OPENAI_API_KEY="openai", OPENAI_MODEL="openai-test")
    @patch("CareerGPS_Rebuild.ai_gateway.OpenAIAdapter.run")
    @patch("CareerGPS_Rebuild.ai_gateway.GeminiAdapter.run")
    def test_bad_request_does_not_fallback(self, gemini, openai):
        error = RuntimeError("invalid schema")
        error.status_code = 400
        gemini.side_effect = error
        with self.assertRaises(AIContractError) as context:
            run_structured_task(self.task())
        self.assertEqual(gemini.call_count, 1)
        openai.assert_not_called()
        self.assertEqual(context.exception.diagnostics[0].category, "http_400")

    @override_settings(GEMINI_API_KEY="same", GEMINI_FALLBACK_API_KEY="same",
                       OPENAI_API_KEY="openai", OPENAI_MODEL="openai-test")
    @patch("CareerGPS_Rebuild.ai_gateway.OpenAIAdapter.run", return_value='{"ok":true}')
    @patch("CareerGPS_Rebuild.ai_gateway.GeminiAdapter.run", side_effect=TimeoutError())
    def test_duplicate_gemini_key_is_skipped(self, gemini, openai):
        run_structured_task(self.task())
        self.assertEqual(gemini.call_count, 1)
        openai.assert_called_once()

    @override_settings(GEMINI_API_KEY="primary", GEMINI_FALLBACK_API_KEY="",
                       OPENAI_API_KEY="openai", OPENAI_MODEL="openai-test")
    @patch("CareerGPS_Rebuild.ai_gateway.OpenAIAdapter.run", return_value='{"ok":true}')
    @patch("CareerGPS_Rebuild.ai_gateway.GeminiAdapter.run", side_effect=TimeoutError())
    def test_missing_gemini_fallback_goes_to_openai(self, gemini, openai):
        run_structured_task(self.task())
        self.assertEqual(gemini.call_count, 1)
        openai.assert_called_once()

    @override_settings(GEMINI_API_KEY="", GEMINI_FALLBACK_API_KEY="",
                       OPENAI_API_KEY="openai", OPENAI_MODEL="openai-test")
    @patch("CareerGPS_Rebuild.ai_gateway.OpenAIAdapter.run", return_value='{"ok":true}')
    @patch("CareerGPS_Rebuild.ai_gateway.GeminiAdapter.run")
    def test_openai_only(self, gemini, openai):
        self.assertEqual(run_structured_task(self.task()), '{"ok":true}')
        gemini.assert_not_called()
        openai.assert_called_once()

    @override_settings(GEMINI_API_KEY="", GEMINI_FALLBACK_API_KEY="", OPENAI_API_KEY="")
    def test_no_provider_configuration(self):
        with self.assertRaisesMessage(AIConfigurationError, "GEMuINI_API_KEY"):
            run_structured_task(self.task())

    @override_settings(GEMINI_API_KEY="", GEMINI_FALLBACK_API_KEY="",
                       OPENAI_API_KEY="openai", OPENAI_MODEL="openai-test")
    @patch("CareerGPS_Rebuild.ai_gateway.OpenAIAdapter.run", side_effect=AIContractError("malformed"))
    def test_malformed_openai_result_is_rejected(self, openai):
        with self.assertRaises(AIContractError):
            run_structured_task(self.task())
        openai.assert_called_once()

    @override_settings(GEMINI_API_KEY="primary-secret", GEMINI_FALLBACK_API_KEY="",
                       OPENAI_API_KEY="openai-secret", OPENAI_MODEL="openai-test")
    @patch("CareerGPS_Rebuild.ai_gateway.OpenAIAdapter.run", side_effect=TimeoutError())
    @patch("CareerGPS_Rebuild.ai_gateway.GeminiAdapter.run", side_effect=TimeoutError())
    def test_all_fail_with_safe_diagnostics(self, gemini, openai):
        with self.assertRaises(AIProviderUnavailable) as context:
            run_structured_task(self.task())
        error = context.exception
        self.assertEqual([d.provider for d in error.diagnostics], ["gemini", "openai"])
        self.assertEqual([d.continued for d in error.diagnostics], [True, False])
        self.assertNotIn("primary-secret", str(error) + str(error.diagnostics))
        self.assertNotIn("openai-secret", str(error) + str(error.diagnostics))

    def test_openai_schema_translation_does_not_change_feature_schema(self):
        task = self.task()
        strict = _strict_schema(task.schema)
        self.assertEqual(strict["additionalProperties"], False)
        self.assertNotIn("additionalProperties", task.schema)

    def test_only_provider_recoverable_failures_are_eligible(self):
        for status in (401, 403, 429, 500, 502, 503, 504):
            error = RuntimeError("provider error")
            error.status_code = status
            self.assertTrue(_failure_category(error)[1])
        for status in (400, 404, 422):
            error = RuntimeError("request error")
            error.status_code = status
            self.assertFalse(_failure_category(error)[1])
        self.assertTrue(_failure_category(TimeoutError())[1])
        self.assertTrue(_failure_category(ConnectionError())[1])
        self.assertFalse(_failure_category(ValueError("invalid schema"))[1])

    @override_settings(GEMINI_API_KEY="", GEMINI_FALLBACK_API_KEY="fallback",
                       OPENAI_API_KEY="", OPENAI_MODEL="openai-test")
    @patch("CareerGPS_Rebuild.ai_gateway.GeminiAdapter.run", return_value='{"ok":true}')
    def test_gemini_fallback_can_be_only_configured_provider(self, gemini):
        self.assertEqual(run_structured_task(self.task()), '{"ok":true}')
        self.assertEqual(gemini.call_args.args[0], "fallback")


class OpenAIAdapterTests(SimpleTestCase):
    def task(self, *, web_search=False):
        return StructuredTask(
            name="web_research" if web_search else "cv_writing",
            prompt="opportunity identity" if web_search else "candidate facts",
            system_instruction="Return JSON", schema={
                "type": "object", "properties": {"ok": {"type": "boolean"}},
                "required": ["ok"],
            }, gemini_model="gemini-test", thinking_level="low",
            timeout_seconds=13, web_search=web_search,
        )

    @staticmethod
    def response(*, status="completed", output_text='{"ok":true}', search=False,
                 refusal=False):
        output = []
        if search:
            output.extend([
                SimpleNamespace(type="web_search_call", status="completed",
                                action=SimpleNamespace(type="search"), content=[]),
                SimpleNamespace(type="web_search_call", status="completed",
                                action=SimpleNamespace(type="open_page"), content=[]),
            ])
        if refusal:
            output.append(SimpleNamespace(type="message", content=[SimpleNamespace(type="refusal")]))
        return SimpleNamespace(status=status, output_text=output_text, output=output)

    def run_with_response(self, task, response):
        from types import ModuleType

        module = ModuleType("openai")
        captured = {}

        class FakeClient:
            def __init__(self, **kwargs):
                captured["client"] = kwargs
                self.responses = self

            def __enter__(self):
                return self

            def __exit__(self, *args):
                pass

            def create(self, **kwargs):
                captured["request"] = kwargs
                return response

        module.OpenAI = FakeClient
        with patch.dict("sys.modules", {"openai": module}), override_settings(OPENAI_MODEL="gpt-test"):
            result = OpenAIAdapter().run("secret", task)
        return result, captured

    def test_structured_output_and_web_search_configuration(self):
        result, captured = self.run_with_response(
            self.task(web_search=True), self.response(search=True)
        )
        self.assertEqual(result, '{"ok":true}')
        self.assertEqual(captured["client"]["max_retries"], 0)
        self.assertEqual(captured["request"]["model"], "gpt-test")
        self.assertEqual(captured["request"]["tools"], [{"type": "web_search"}])
        self.assertEqual(captured["request"]["tool_choice"], "required")
        self.assertFalse(captured["request"]["store"])
        self.assertEqual(captured["request"]["text"]["format"]["schema"]["additionalProperties"], False)

    def test_refusal_incomplete_malformed_and_missing_search_are_rejected(self):
        for response in (
            self.response(refusal=True),
            self.response(status="incomplete"),
            self.response(output_text="not json"),
        ):
            with self.subTest(response=response):
                with self.assertRaises(AIContractError):
                    self.run_with_response(self.task(), response)
        with self.assertRaises(AIContractError):
            self.run_with_response(self.task(web_search=True), self.response())


@override_settings(GROQ_API_KEY="")
class GatewayFeatureTests(SimpleTestCase):
    @staticmethod
    def _question_json():
        return json.dumps({"questions": [
            {"question": f"Question {index}?", "question_type": "opening", "focus": "work"}
            for index in range(5)
        ]})

    @staticmethod
    def _evaluation_json():
        return json.dumps({"overall_summary": "Practice complete.", "strengths": [],
                           "improvements": [], "question_feedback": [{
                               "question_index": 0, "feedback": "Clear.", "strengths": [],
                               "improvements": [], "better_approach": "Add an example.",
                               "relevance": "strong", "structure": "adequate",
                               "specificity": "adequate"}]})

    @override_settings(GEMINI_API_KEY="primary", GEMINI_FALLBACK_API_KEY="",
                       OPENAI_API_KEY="openai", OPENAI_MODEL="openai-test")
    @patch("CareerGPS_Rebuild.ai_gateway.OpenAIAdapter.run")
    @patch("CareerGPS_Rebuild.ai_gateway.GeminiAdapter.run", side_effect=TimeoutError())
    def test_interview_generation_and_evaluation_use_openai_and_existing_validators(self, gemini, openai):
        from interview.services.ai import evaluate_interview, generate_interview_plan

        openai.side_effect = [self._question_json(), self._evaluation_json()]
        plan = generate_interview_plan({}, 5)
        evaluation = evaluate_interview({}, [{"question_index": 0, "answer": "A"}])
        self.assertEqual(len(plan.questions), 5)
        self.assertEqual(evaluation.question_feedback[0].relevance, "strong")
        self.assertEqual([c.args[1].name for c in openai.call_args_list],
                         ["interview_questions", "interview_evaluation"])

    @override_settings(GEMINI_API_KEY="", GEMINI_FALLBACK_API_KEY="",
                       OPENAI_API_KEY="openai", OPENAI_MODEL="openai-test")
    @patch("CareerGPS_Rebuild.ai_gateway.OpenAIAdapter.run")
    def test_documents_use_openai_with_factual_controls(self, openai):
        from applications.services.document_generation import generate_cover_letter, generate_cv_writing

        bundle = {"facts": [{"id": "basic:1", "type": "basic", "text": "Ada",
                             "data": {"name": "Ada"}},
                            {"id": "skill:1", "type": "skill", "text": "Python",
                             "data": {"name": "Python"}}]}
        selected = bundle["facts"]
        job = {"employer": "Example Co", "location": "Cape Town"}
        openai.side_effect = [
            json.dumps({"summary": "Python developer", "rewrites": [
                {"fact_id": "invented", "description": "Invented", "bullets": []}],
                "skill_order": ["Fake Skill", "Python"]}),
            json.dumps({"date": "", "recipient_name": "Hiring Manager", "company_name": "Wrong",
                        "location": "Wrong", "salutation": "Dear Hiring Manager,",
                        "opening": "I use Python.", "body_paragraphs": ["I use Python."],
                        "closing": "Thank you.", "sign_off": "Kind regards,",
                        "signature_name": "Wrong"}),
        ]
        cv = generate_cv_writing(bundle, selected, job)
        cover = generate_cover_letter(bundle, selected, job)
        self.assertEqual(cv.rewrites, [])
        self.assertEqual(cv.skill_order, ["Python"])
        self.assertEqual(cover.signature_name, "Ada")
        self.assertEqual(cover.company_name, "Example Co")
        self.assertEqual(cover.location, "Cape Town")
        self.assertEqual([c.args[1].name for c in openai.call_args_list],
                         ["cv_writing", "cover_letter"])


@override_settings(
    GEMINI_API_KEY="primary", GEMINI_FALLBACK_API_KEY="fallback",
    GROQ_API_KEY="groq-secret", GROQ_MODEL="openai/gpt-oss-120b",
    OPENAI_API_KEY="openai-secret", OPENAI_MODEL="openai-test",
)
class GroqGatewayTests(SimpleTestCase):
    def task(self, name="interview_questions", *, web_search=False):
        return StructuredTask(
            name=name, prompt="prompt", system_instruction="system",
            schema={"type": "object", "properties": {"ok": {"type": "boolean"}},
                    "required": ["ok"]},
            gemini_model="gemini-test", thinking_level="low", timeout_seconds=10,
            web_search=web_search,
        )

    @patch("CareerGPS_Rebuild.ai_gateway.OpenAIAdapter.run")
    @patch("CareerGPS_Rebuild.ai_gateway.GroqAdapter.run")
    @patch("CareerGPS_Rebuild.ai_gateway.GeminiAdapter.run", return_value='{"ok":true}')
    def test_interview_primary_success_skips_groq_and_openai(self, gemini, groq, openai):
        self.assertEqual(run_structured_task(self.task()), '{"ok":true}')
        self.assertEqual(gemini.call_count, 1)
        groq.assert_not_called()
        openai.assert_not_called()

    @patch("CareerGPS_Rebuild.ai_gateway.OpenAIAdapter.run")
    @patch("CareerGPS_Rebuild.ai_gateway.GroqAdapter.run")
    @patch("CareerGPS_Rebuild.ai_gateway.GeminiAdapter.run")
    def test_interview_gemini_fallback_success_skips_groq(self, gemini, groq, openai):
        gemini.side_effect = [TimeoutError(), '{"ok":true}']
        self.assertEqual(run_structured_task(self.task()), '{"ok":true}')
        self.assertEqual([c.args[0] for c in gemini.call_args_list], ["primary", "fallback"])
        groq.assert_not_called()
        openai.assert_not_called()

    @patch("CareerGPS_Rebuild.ai_gateway.OpenAIAdapter.run")
    @patch("CareerGPS_Rebuild.ai_gateway.GroqAdapter.run", return_value='{"ok":true}')
    @patch("CareerGPS_Rebuild.ai_gateway.GeminiAdapter.run", side_effect=TimeoutError())
    def test_interview_groq_success_after_both_gemini_keys(self, gemini, groq, openai):
        self.assertEqual(run_structured_task(self.task()), '{"ok":true}')
        self.assertEqual([c.args[0] for c in gemini.call_args_list], ["primary", "fallback"])
        self.assertEqual(groq.call_args.args[0], "groq-secret")
        openai.assert_not_called()

    @patch("CareerGPS_Rebuild.ai_gateway.OpenAIAdapter.run", return_value='{"ok":true}')
    @patch("CareerGPS_Rebuild.ai_gateway.GroqAdapter.run", side_effect=TimeoutError())
    @patch("CareerGPS_Rebuild.ai_gateway.GeminiAdapter.run", side_effect=TimeoutError())
    def test_openai_after_eligible_groq_failure(self, gemini, groq, openai):
        self.assertEqual(run_structured_task(self.task()), '{"ok":true}')
        self.assertEqual(gemini.call_count, 2)
        groq.assert_called_once()
        openai.assert_called_once()

    @override_settings(GEMINI_FALLBACK_API_KEY="", OPENAI_API_KEY="")
    @patch("CareerGPS_Rebuild.ai_gateway.GroqAdapter.run", return_value='{"ok":true}')
    @patch("CareerGPS_Rebuild.ai_gateway.GeminiAdapter.run", side_effect=TimeoutError())
    def test_primary_then_groq_without_other_fallbacks(self, gemini, groq):
        self.assertEqual(run_structured_task(self.task()), '{"ok":true}')
        self.assertEqual(gemini.call_count, 1)
        groq.assert_called_once()

    @override_settings(GEMINI_API_KEY="", GEMINI_FALLBACK_API_KEY="")
    @patch("CareerGPS_Rebuild.ai_gateway.OpenAIAdapter.run", return_value='{"ok":true}')
    @patch("CareerGPS_Rebuild.ai_gateway.GroqAdapter.run", side_effect=TimeoutError())
    def test_groq_then_openai_without_gemini(self, groq, openai):
        self.assertEqual(run_structured_task(self.task()), '{"ok":true}')
        groq.assert_called_once()
        openai.assert_called_once()

    @override_settings(GEMINI_API_KEY="", GEMINI_FALLBACK_API_KEY="", OPENAI_API_KEY="")
    @patch("CareerGPS_Rebuild.ai_gateway.GroqAdapter.run", return_value='{"ok":true}')
    def test_groq_only(self, groq):
        self.assertEqual(run_structured_task(self.task()), '{"ok":true}')
        groq.assert_called_once()

    @patch("CareerGPS_Rebuild.ai_gateway.OpenAIAdapter.run")
    @patch("CareerGPS_Rebuild.ai_gateway.GroqAdapter.run")
    @patch("CareerGPS_Rebuild.ai_gateway.GeminiAdapter.run", side_effect=TimeoutError())
    def test_ineligible_groq_error_stops_before_openai(self, gemini, groq, openai):
        error = ValueError("invalid schema")
        groq.side_effect = error
        with self.assertRaises(AIContractError) as context:
            run_structured_task(self.task())
        self.assertEqual(context.exception.diagnostics[-1].provider, "groq")
        self.assertEqual(context.exception.diagnostics[-1].category, "application")
        openai.assert_not_called()

    @patch("CareerGPS_Rebuild.ai_gateway.OpenAIAdapter.run")
    @patch("CareerGPS_Rebuild.ai_gateway.GroqAdapter.run",
           side_effect=AIContractError("malformed", category="malformed"))
    @patch("CareerGPS_Rebuild.ai_gateway.GeminiAdapter.run", side_effect=TimeoutError())
    def test_malformed_groq_output_stops_before_openai(self, gemini, groq, openai):
        with self.assertRaises(AIContractError) as context:
            run_structured_task(self.task())
        self.assertEqual(context.exception.diagnostics[-1].category, "malformed")
        openai.assert_not_called()

    @patch("CareerGPS_Rebuild.ai_gateway.OpenAIAdapter.run", return_value='{"ok":true}')
    @patch("CareerGPS_Rebuild.ai_gateway.GroqAdapter.run")
    @patch("CareerGPS_Rebuild.ai_gateway.GeminiAdapter.run", side_effect=TimeoutError())
    def test_groq_quota_failure_advances_to_openai(self, gemini, groq, openai):
        error = RuntimeError("rate limited")
        error.status_code = 429
        groq.side_effect = error
        self.assertEqual(run_structured_task(self.task()), '{"ok":true}')
        groq.assert_called_once()
        openai.assert_called_once()

    @override_settings(GEMINI_API_KEY="", GEMINI_FALLBACK_API_KEY="", OPENAI_API_KEY="")
    @patch("CareerGPS_Rebuild.ai_gateway.GroqAdapter.run", side_effect=TimeoutError())
    def test_groq_diagnostics_have_distinct_provider_and_model(self, groq):
        with self.assertRaises(AIProviderUnavailable) as context:
            run_structured_task(self.task())
        diagnostic = context.exception.diagnostics[0]
        self.assertEqual((diagnostic.provider, diagnostic.model),
                         ("groq", "openai/gpt-oss-120b"))
        self.assertNotIn("groq-secret", str(context.exception.diagnostics))

    @patch("CareerGPS_Rebuild.ai_gateway.OpenAIAdapter.run", return_value='{"ok":true}')
    @patch("CareerGPS_Rebuild.ai_gateway.GroqAdapter.run")
    @patch("CareerGPS_Rebuild.ai_gateway.GeminiAdapter.run", side_effect=TimeoutError())
    def test_web_search_never_routes_to_groq(self, gemini, groq, openai):
        self.assertEqual(
            run_structured_task(self.task("web_research", web_search=True)),
            '{"ok":true}',
        )
        self.assertEqual(gemini.call_count, 2)
        groq.assert_not_called()
        openai.assert_called_once()

    @override_settings(GEMINI_API_KEY="", GEMINI_FALLBACK_API_KEY="", OPENAI_API_KEY="")
    @patch("CareerGPS_Rebuild.ai_gateway.GroqAdapter.run")
    def test_groq_only_cannot_run_web_search(self, groq):
        with self.assertRaises(AIConfigurationError):
            run_structured_task(self.task("web_research", web_search=True))
        groq.assert_not_called()


class GroqAdapterTests(SimpleTestCase):
    @staticmethod
    def task():
        return StructuredTask(
            name="cv_writing", prompt="prompt", system_instruction="system",
            schema={"type": "object", "properties": {"ok": {"type": "boolean"}},
                    "required": ["ok"]},
            gemini_model="unused", thinking_level="low", timeout_seconds=11,
        )

    def run_with_reply(self, *, content='{"ok":true}', finish_reason="stop", refusal=None):
        captured = {}

        class FakeClient:
            def __init__(self, **kwargs):
                captured["client"] = kwargs
                self.chat = SimpleNamespace(completions=self)

            def __enter__(self):
                return self

            def __exit__(self, *args):
                pass

            def create(self, **kwargs):
                captured["request"] = kwargs
                return SimpleNamespace(choices=[SimpleNamespace(
                    finish_reason=finish_reason,
                    message=SimpleNamespace(content=content, refusal=refusal),
                )])

        with patch("openai.OpenAI", FakeClient), override_settings(GROQ_MODEL="openai/gpt-oss-120b"):
            result = GroqAdapter().run("groq-secret", self.task())
        return result, captured

    def test_groq_uses_distinct_endpoint_key_model_and_strict_schema(self):
        result, captured = self.run_with_reply()
        self.assertEqual(result, '{"ok":true}')
        self.assertEqual(captured["client"]["api_key"], "groq-secret")
        self.assertEqual(captured["client"]["base_url"], "https://api.groq.com/openai/v1")
        self.assertEqual(captured["client"]["max_retries"], 0)
        self.assertEqual(captured["request"]["model"], "openai/gpt-oss-120b")
        self.assertTrue(captured["request"]["response_format"]["json_schema"]["strict"])
        self.assertFalse(captured["request"]["response_format"]["json_schema"]["schema"]["additionalProperties"])

    def test_malformed_refused_and_truncated_groq_results_are_rejected(self):
        for arguments in (
            {"content": "not json"},
            {"refusal": "no"},
            {"finish_reason": "length"},
        ):
            with self.subTest(arguments=arguments), self.assertRaises(AIContractError):
                self.run_with_reply(**arguments)


@override_settings(
    GEMINI_API_KEY="", GEMINI_FALLBACK_API_KEY="", GROQ_API_KEY="groq-secret",
    GROQ_MODEL="openai/gpt-oss-120b", OPENAI_API_KEY="",
)
class GroqFeatureTests(SimpleTestCase):
    @patch("CareerGPS_Rebuild.ai_gateway.GeminiAdapter.run")
    @patch("CareerGPS_Rebuild.ai_gateway.OpenAIAdapter.run")
    @patch("CareerGPS_Rebuild.ai_gateway.GroqAdapter.run")
    def test_groq_only_interview_questions_and_evaluation(self, groq, openai, gemini):
        from interview.services.ai import evaluate_interview, generate_interview_plan

        groq.side_effect = [
            GatewayFeatureTests._question_json(),
            GatewayFeatureTests._evaluation_json(),
        ]
        plan = generate_interview_plan({}, 5)
        evaluation = evaluate_interview({}, [{"question_index": 0, "answer": "A"}])
        self.assertEqual(len(plan.questions), 5)
        self.assertEqual(evaluation.question_feedback[0].relevance, "strong")
        self.assertEqual([c.args[1].name for c in groq.call_args_list],
                         ["interview_questions", "interview_evaluation"])
        gemini.assert_not_called()
        openai.assert_not_called()

    @patch("CareerGPS_Rebuild.ai_gateway.GroqAdapter.run")
    def test_groq_only_cv_and_cover_letter_preserve_factual_controls(self, groq):
        from applications.services.document_generation import generate_cover_letter, generate_cv_writing

        bundle = {"facts": [
            {"id": "basic:1", "type": "basic", "text": "Ada", "data": {"name": "Ada"}},
            {"id": "skill:1", "type": "skill", "text": "Python", "data": {"name": "Python"}},
        ]}
        groq.side_effect = [
            json.dumps({"summary": "Python specialist", "rewrites": [
                {"fact_id": "invented", "description": "Invented work", "bullets": []}],
                "skill_order": ["Invented Skill", "Python"]}),
            json.dumps({
                "date": "", "recipient_name": "Hiring Manager", "company_name": "Wrong",
                "location": "Wrong", "salutation": "Dear Hiring Manager,",
                "opening": "I use Python.", "body_paragraphs": ["I use Python."],
                "closing": "Thank you.", "sign_off": "Kind regards,",
                "signature_name": "Wrong",
            }),
        ]
        job = {"employer": "Example Co", "location": "Cape Town"}
        cv = generate_cv_writing(bundle, bundle["facts"], job)
        letter = generate_cover_letter(bundle, bundle["facts"], job)
        self.assertEqual(cv.rewrites, [])
        self.assertEqual(cv.skill_order, ["Python"])
        self.assertEqual(letter.signature_name, "Ada")
        self.assertEqual(letter.company_name, "Example Co")
        self.assertEqual(letter.location, "Cape Town")
        self.assertEqual([c.args[1].name for c in groq.call_args_list],
                         ["cv_writing", "cover_letter"])
