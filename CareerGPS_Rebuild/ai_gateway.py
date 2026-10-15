"""Provider selection for CareerGPS's runtime structured AI requests.

Feature services own prompts, schemas and validation. This module owns only
credentials, provider invocation, eligible failover and safe diagnostics.
"""

from __future__ import annotations

import copy
import json
import logging
from dataclasses import dataclass

from django.conf import settings


logger = logging.getLogger("careergps.ai_gateway")


@dataclass(frozen=True)
class StructuredTask:
    name: str
    prompt: str
    system_instruction: str
    schema: dict
    gemini_model: str
    thinking_level: str
    timeout_seconds: int
    web_search: bool = False


@dataclass(frozen=True)
class AttemptDiagnostic:
    task: str
    provider: str
    slot: str
    attempt: int
    model: str
    category: str
    continued: bool


class AIGatewayError(RuntimeError):
    def __init__(self, message, *, diagnostics=()):
        super().__init__(message)
        self.diagnostics = tuple(diagnostics)


class AIConfigurationError(AIGatewayError):
    pass


class AIProviderUnavailable(AIGatewayError):
    pass


class AIContractError(AIGatewayError):
    def __init__(self, message, *, category="contract", diagnostics=()):
        super().__init__(message, diagnostics=diagnostics)
        self.category = category


def _complete_json(text):
    if not isinstance(text, str) or not text.strip():
        raise AIContractError("AI provider returned no structured result.", category="empty")
    try:
        value = json.loads(text)
    except (TypeError, ValueError) as exc:
        raise AIContractError(
            "AI provider returned malformed structured data.", category="malformed"
        ) from exc
    if not isinstance(value, dict):
        raise AIContractError(
            "AI provider returned an invalid structured result.", category="non_object"
        )
    return text


def _strict_schema(schema):
    """Adapt the feature's logical schema to strict provider JSON Schema."""
    result = copy.deepcopy(schema)

    def visit(node):
        if not isinstance(node, dict):
            return
        if node.get("type") == "object":
            properties = node.get("properties", {})
            node["additionalProperties"] = False
            node["required"] = list(properties)
            for child in properties.values():
                visit(child)
        if node.get("type") == "array":
            visit(node.get("items"))

    visit(result)
    return result


class GeminiAdapter:
    provider = "gemini"

    def run(self, api_key, task):
        from google import genai
        from google.genai import types

        arguments = {
            "model": task.gemini_model,
            "input": task.prompt,
            "system_instruction": task.system_instruction,
            "response_format": {
                "type": "text",
                "mime_type": "application/json",
                "schema": task.schema,
            },
            "generation_config": {"thinking_level": task.thinking_level},
            "timeout": task.timeout_seconds,
        }

        if task.web_search:
            arguments["tools"] = [{"type": "google_search"}, {"type": "url_context"}]

        with genai.Client(
            api_key=api_key,
            http_options=types.HttpOptions(
                retry_options=types.HttpRetryOptions(attempts=0),
            ),
        ) as client:
            response = client.interactions.create(**arguments)
            return _complete_json(getattr(response, "output_text", None))


class OpenAIAdapter:
    provider = "openai"

    def run(self, api_key, task):
        from openai import OpenAI

        arguments = {
            "model": settings.OPENAI_MODEL,
            "instructions": task.system_instruction,
            "input": task.prompt,
            "text": {"format": {
                "type": "json_schema",
                "name": task.name,
                "schema": _strict_schema(task.schema),
                "strict": True,
            }},
            "store": False,
        }
        if task.web_search:
            arguments["tools"] = [{"type": "web_search"}]
            arguments["tool_choice"] = "required"

        with OpenAI(api_key=api_key, timeout=task.timeout_seconds, max_retries=0) as client:
            response = client.responses.create(**arguments)

        if getattr(response, "status", None) != "completed":
            raise AIContractError(
                "AI provider returned an incomplete structured result.",
                category="incomplete",
            )
        output = getattr(response, "output", ()) or ()
        if any(
            getattr(content, "type", None) == "refusal"
            for item in output
            for content in (getattr(item, "content", None) or ())
        ):
            raise AIContractError(
                "AI provider refused the structured request.", category="refusal"
            )
        if task.web_search:
            actions = {
                getattr(getattr(item, "action", None), "type", None)
                for item in output
                if getattr(item, "type", None) == "web_search_call"
                and getattr(item, "status", None) == "completed"
            }
            if not {"search", "open_page"}.issubset(actions):
                raise AIContractError(
                    "AI provider did not search and read a vacancy page.",
                    category="search_incomplete",
                )
        return _complete_json(getattr(response, "output_text", None))


class GroqAdapter:
    provider = "groq"

    def run(self, api_key, task):
        from openai import OpenAI

        with OpenAI(
            api_key=api_key,
            base_url="https://api.groq.com/openai/v1",
            timeout=task.timeout_seconds,
            max_retries=0,
        ) as client:
            response = client.chat.completions.create(
                model=settings.GROQ_MODEL,
                messages=[
                    {"role": "system", "content": task.system_instruction},
                    {"role": "user", "content": task.prompt},
                ],
                response_format={
                    "type": "json_schema",
                    "json_schema": {
                        "name": task.name,
                        "schema": _strict_schema(task.schema),
                        "strict": True,
                    },
                },
            )

        choices = getattr(response, "choices", None) or ()
        if not choices:
            raise AIContractError("AI provider returned no structured result.", category="empty")
        choice = choices[0]
        if getattr(choice, "finish_reason", None) != "stop":
            raise AIContractError(
                "AI provider returned an incomplete structured result.",
                category="incomplete",
            )
        message = getattr(choice, "message", None)
        if getattr(message, "refusal", None):
            raise AIContractError(
                "AI provider refused the structured request.", category="refusal"
            )
        return _complete_json(getattr(message, "content", None))


_ELIGIBLE_CODES = {401, 403, 429, 500, 502, 503, 504}
_ELIGIBLE_MARKERS = (
    "resource_exhausted", "resource exhausted", "quota", "rate limit",
    "rate_limit", "too many requests", "unauthenticated", "permission_denied",
    "permission denied", "api key not valid", "invalid api key",
    "service unavailable", "temporarily unavailable", "deadline exceeded",
    "timed out", "timeout", "connection reset", "connection aborted",
    "apiconnectionerror", "capacity",
)


def _failure_category(exc):
    if isinstance(exc, AIContractError):
        return exc.category, False
    if isinstance(exc, (TimeoutError, ConnectionError)):
        return "transport", True
    code = None
    for attribute in ("status_code", "http_status", "code"):
        try:
            code = int(getattr(exc, attribute))
            break
        except (TypeError, ValueError, AttributeError):
            continue
    if code in _ELIGIBLE_CODES:
        return f"http_{code}", True
    if code is not None:
        return f"http_{code}", False
    marker_text = f"{type(exc).__name__}: {exc}".casefold()
    if any(marker in marker_text for marker in _ELIGIBLE_MARKERS):
        return "provider_transient", True
    return "application", False


_GROQ_TASK_NAMES = frozenset({
    "interview_questions", "interview_evaluation", "cv_writing", "cover_letter",
})


def _attempts(task):
    primary = (getattr(settings, "GEMINI_API_KEY", "") or "").strip()
    fallback = (getattr(settings, "GEMINI_FALLBACK_API_KEY", "") or "").strip()
    groq_key = (getattr(settings, "GROQ_API_KEY", "") or "").strip()
    openai_key = (getattr(settings, "OPENAI_API_KEY", "") or "").strip()
    attempts = []
    if primary:
        attempts.append((GeminiAdapter(), "primary", primary))
    if fallback and fallback != primary:
        attempts.append((GeminiAdapter(), "fallback", fallback))
    if groq_key and task.name in _GROQ_TASK_NAMES and not task.web_search:
        attempts.append((GroqAdapter(), "fallback", groq_key))
    if openai_key:
        attempts.append((OpenAIAdapter(), "fallback", openai_key))
    return attempts


def run_structured_task(task):
    attempts = _attempts(task)
    if not attempts:
        options = "GEMINI_API_KEY, GEMuINI_API_KEY"
        if task.name in _GROQ_TASK_NAMES and not task.web_search:
            options += ", GROQ_API_KEY"
        raise AIConfigurationError(f"CareerGPS AI is not configured. Set {options}, or OPENAI_API_KEY.")
    diagnostics = []
    for number, (adapter, slot, api_key) in enumerate(attempts, start=1):
        model = {
            "gemini": task.gemini_model,
            "groq": getattr(settings, "GROQ_MODEL", "openai/gpt-oss-120b"),
            "openai": settings.OPENAI_MODEL,
        }[adapter.provider]
        try:
            result = adapter.run(api_key, task)
        except Exception as exc:
            category, eligible = _failure_category(exc)
            continued = eligible and number < len(attempts)
            diagnostic = AttemptDiagnostic(
                task.name, adapter.provider, slot, number, model, category, continued
            )
            diagnostics.append(diagnostic)
            logger.warning(
                "AI attempt failed task=%s provider=%s slot=%s attempt=%s model=%s "
                "category=%s continued=%s",
                task.name, adapter.provider, slot, number, model, category, continued,
            )
            if not eligible:
                raise AIContractError(
                    "CareerGPS could not validate the AI request or result.",
                    diagnostics=diagnostics,
                ) from exc
        else:
            logger.info(
                "AI attempt succeeded task=%s provider=%s slot=%s attempt=%s model=%s",
                task.name, adapter.provider, slot, number, model,
            )
            return result
    raise AIProviderUnavailable(
        "CareerGPS AI is temporarily unavailable. Please try again shortly.",
        diagnostics=diagnostics,
    )
