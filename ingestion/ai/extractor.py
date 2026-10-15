from __future__ import annotations

import json

from django.conf import settings

from .instruction import SYSTEM_INSTRUCTION
from .schema import OpportunityGapUnderstanding, OpportunityUnderstandingBatch


# Deliberately hand-written, shallow JSON Schema. The full Pydantic model is
# still the final local CareerGPS validator after Gemini returns JSON.
_GEMINI_GAP_SCHEMA = {
    "type": "object",
    "properties": {
        "opportunity_id": {"type": "integer"},
        "criteria": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "criterion_type": {
                        "type": "string",
                        "enum": [
                            "skill",
                            "qualification",
                            "certification",
                            "experience",
                            "language",
                            "work_eligibility",
                        ],
                    },
                    "value": {"type": "string"},
                    "requirement_level": {
                        "type": "string",
                        "enum": ["required", "preferred", "unknown"],
                    },
                    "evidence_text": {"type": "string"},
                    "source_section": {"type": "string"},
                    "minimum_years": {
                        "type": "integer",
                        "minimum": 0,
                        "maximum": 80,
                    },
                    "maximum_years": {
                        "type": "integer",
                        "minimum": 0,
                        "maximum": 80,
                    },
                    "context": {"type": "string"},
                },
                "required": [
                    "criterion_type",
                    "value",
                    "requirement_level",
                    "evidence_text",
                    "source_section",
                    "context",
                ],
            },
        },
    },
    "required": ["opportunity_id", "criteria"],
}


# A second API key can help only when the failure is tied to provider capacity,
# credentials/project access, or a transient transport/server problem. It must
# NOT hide deterministic request/schema bugs (for example INVALID_ARGUMENT 400)
# because sending the same invalid request with another key just wastes quota.
_FALLBACK_HTTP_CODES = {401, 403, 429, 500, 502, 503, 504}
_FALLBACK_MARKERS = (
    "resource_exhausted",
    "resource exhausted",
    "quota",
    "rate limit",
    "rate_limit",
    "too many requests",
    "unauthenticated",
    "permission_denied",
    "permission denied",
    "api key not valid",
    "invalid api key",
    "service unavailable",
    "temporarily unavailable",
    "deadline exceeded",
    "timed out",
    "timeout",
    "connection reset",
    "connection aborted",
)


def _exception_code(exc):
    for attr in ("code", "status_code", "http_status"):
        value = getattr(exc, attr, None)
        try:
            if value is not None:
                return int(value)
        except (TypeError, ValueError):
            pass
    return None


def _should_try_fallback(exc: Exception) -> bool:
    """Return True only for failures another API key may realistically solve."""
    if isinstance(exc, TimeoutError):
        return True

    code = _exception_code(exc)
    if code in _FALLBACK_HTTP_CODES:
        return True
    if code == 400:
        # INVALID_ARGUMENT is a request/schema problem, not a key-capacity issue.
        return False

    text = f"{type(exc).__name__}: {exc}".casefold()
    return any(marker in text for marker in _FALLBACK_MARKERS)


class GeminiOpportunityExtractor:
    """Gemini 3.5 opportunity extractor with primary -> fallback key failover.

    Provider calls remain one opportunity at a time. No candidate/profile data
    is ever supplied to this extractor.
    """

    provider = "gemini"

    def __init__(
        self,
        *,
        model_name=None,
        thinking_level=None,
        api_key=None,
        fallback_api_key=None,
        timeout_seconds=120,
    ):
        self.model_name = model_name or settings.OPPORTUNITY_AI_MODEL
        self.thinking_level = (
            thinking_level or settings.OPPORTUNITY_AI_THINKING_LEVEL or "medium"
        ).strip().lower()
        if self.thinking_level not in {"minimal", "low", "medium", "high"}:
            raise ValueError(
                "OPPORTUNITY_AI_THINKING_LEVEL must be minimal, low, medium, or high."
            )

        self.api_key = api_key if api_key is not None else settings.GEMINI_API_KEY
        self.fallback_api_key = (
            fallback_api_key
            if fallback_api_key is not None
            else getattr(settings, "OPPORTUNITY_AI_FALLBACK_API_KEY", "")
        )
        # Do not treat an accidentally duplicated key as a real fallback.
        if self.fallback_api_key == self.api_key:
            self.fallback_api_key = ""

        self.timeout_seconds = max(10, int(timeout_seconds or 120))
        self._active_key_name = "primary"
        self._active_api_key = self.api_key
        self.last_key_used = None
        self.last_failover_reason = ""

    @property
    def fallback_configured(self) -> bool:
        return bool(self.fallback_api_key)

    @property
    def active_key_name(self) -> str:
        return self._active_key_name

    def _client(self, api_key):
        from google import genai

        return genai.Client(api_key=api_key)

    def _request(self, api_key: str, prompt: str):
        client = self._client(api_key)
        interaction = client.interactions.create(
            model=self.model_name,
            input=prompt,
            system_instruction=SYSTEM_INSTRUCTION,
            response_format={
                "type": "text",
                "mime_type": "application/json",
                "schema": _GEMINI_GAP_SCHEMA,
            },
            generation_config={
                "thinking_level": self.thinking_level,
            },
            timeout=self.timeout_seconds,
        )

        text = getattr(interaction, "output_text", None)
        if not text:
            raise RuntimeError("Gemini returned an empty extraction response.")
        return text

    def _request_with_failover(self, prompt: str) -> str:
        if not self._active_api_key:
            # Primary may intentionally be empty while a fallback is configured.
            if self.fallback_api_key:
                self._active_api_key = self.fallback_api_key
                self._active_key_name = "fallback"
            else:
                raise RuntimeError(
                    "Neither GEMINI_API_KEY nor the Phase 2.4 fallback Gemini key is configured."
                )

        try:
            text = self._request(self._active_api_key, prompt)
            self.last_key_used = self._active_key_name
            return text
        except Exception as exc:
            # If the run is already on fallback, there is no third key to try.
            if self._active_key_name == "fallback":
                raise
            if not self.fallback_api_key or not _should_try_fallback(exc):
                raise

            # Switch for the remainder of this command/run. This matters when a
            # primary project's quota is exhausted: subsequent jobs should not
            # waste a doomed primary request before every fallback request.
            self.last_failover_reason = f"{type(exc).__name__}: {exc}"
            self._active_api_key = self.fallback_api_key
            self._active_key_name = "fallback"

            text = self._request(self._active_api_key, prompt)
            self.last_key_used = "fallback"
            return text

    def extract_one(self, payload: dict) -> OpportunityGapUnderstanding:
        if not self.api_key and not self.fallback_api_key:
            raise RuntimeError(
                "Neither GEMINI_API_KEY nor the Phase 2.4 fallback Gemini key is configured."
            )

        request_payload = {
            "opportunity_id": payload["opportunity_id"],
            "source_completeness": payload["source_completeness"],
            "sections": payload["sections"],
        }
        prompt = (
            "Understand the following CareerGPS opportunity according to the system "
            "instruction. Return ONLY the JSON object required by the response schema.\n\n"
            + json.dumps(request_payload, ensure_ascii=False, indent=2)
        )

        text = self._request_with_failover(prompt)
        return OpportunityGapUnderstanding.model_validate_json(text)

    def extract(self, payloads: list[dict]) -> OpportunityUnderstandingBatch:
        """Compatibility wrapper: process payloads sequentially, one API call each."""
        results = [self.extract_one(payload) for payload in payloads]
        return OpportunityUnderstandingBatch(opportunities=results)
