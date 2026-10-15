from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from ingestion.ai.extractor import GeminiOpportunityExtractor


class Command(BaseCommand):
    help = "Diagnose Gemini basic, structured-output, thinking, and CareerGPS schema API compatibility."

    def add_arguments(self, parser):
        parser.add_argument("--model", default=settings.OPPORTUNITY_AI_MODEL)
        parser.add_argument("--timeout-seconds", type=int, default=60)

    def handle(self, *args, **options):
        if not settings.GEMINI_API_KEY:
            raise CommandError("GEMINI_API_KEY is not configured in .env")

        from google import genai

        model = options["model"]
        timeout = options["timeout_seconds"]
        client = genai.Client(api_key=settings.GEMINI_API_KEY)

        def run(name, fn):
            self.stdout.write(f"{name} ...")
            try:
                value = fn()
            except Exception as exc:
                self.stderr.write(self.style.ERROR(f"{name}: FAILED: {exc}"))
                raise CommandError(
                    f"Stopped at {name}. Earlier stages passed; this isolates an API/schema compatibility failure."
                ) from exc
            self.stdout.write(self.style.SUCCESS(f"{name}: PASS {value}"))

        run(
            "1/basic interaction",
            lambda: client.interactions.create(
                model=model,
                input="Reply with exactly OK.",
                timeout=timeout,
            ).output_text,
        )

        tiny_schema = {
            "type": "object",
            "properties": {"status": {"type": "string", "enum": ["ok"]}},
            "required": ["status"],
        }
        run(
            "2/tiny structured output",
            lambda: client.interactions.create(
                model=model,
                input='Return status "ok".',
                response_format={
                    "type": "text",
                    "mime_type": "application/json",
                    "schema": tiny_schema,
                },
                timeout=timeout,
            ).output_text,
        )

        run(
            "3/structured output + configured thinking",
            lambda: client.interactions.create(
                model=model,
                input='Return status "ok".',
                response_format={
                    "type": "text",
                    "mime_type": "application/json",
                    "schema": tiny_schema,
                },
                generation_config={"thinking_level": settings.OPPORTUNITY_AI_THINKING_LEVEL},
                timeout=timeout,
            ).output_text,
        )

        # This stage checks only that the real CareerGPS response schema can be
        # accepted and parsed by the API. It deliberately does NOT judge the
        # semantic correctness of a made-up job. Semantic quality is evaluated
        # against real CareerGPS opportunities.
        structural_payload = {
            "opportunity_id": 999999,
            "source_completeness": "full_text",
            "sections": {
                "job_purpose_or_description": "SQL skills are required."
            },
        }
        extractor = GeminiOpportunityExtractor(
            model_name=model,
            thinking_level=settings.OPPORTUNITY_AI_THINKING_LEVEL,
            api_key=settings.GEMINI_API_KEY,
            timeout_seconds=timeout,
        )
        run(
            "4/CareerGPS response-schema compatibility",
            lambda: extractor.extract_one(structural_payload).model_dump(mode="json"),
        )

        self.stdout.write(
            self.style.SUCCESS(
                "Gemini API/schema diagnostics passed. Semantic behavior must be judged on real CareerGPS jobs."
            )
        )
