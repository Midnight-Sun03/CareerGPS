from __future__ import annotations

from dataclasses import dataclass
import re

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from ingestion.models import OpportunityCriterion, OpportunityExtraction

from .extractor import GeminiOpportunityExtractor
from .normalization import normalize_extraction_taxonomies
from .payload import build_compact_opportunity_payload, clean_text, evidence_corpus


@dataclass(frozen=True)
class ProcessingResult:
    opportunity_id: int
    status: str
    criterion_count: int = 0
    message: str = ""


def _normalised_quote(value: str) -> str:
    return clean_text(value).casefold()


def _evidence_supported(evidence_text: str, payload: dict, source_section: str = "") -> bool:
    quote = _normalised_quote(evidence_text)
    if not quote:
        return False

    sections = payload.get("sections", {}) or {}
    source_section = clean_text(source_section)
    if source_section:
        if source_section not in sections:
            return False
        return quote in _normalised_quote(sections.get(source_section, ""))
    return quote in _normalised_quote(evidence_corpus(payload))


def _collect_validation_errors(understanding, payload: dict) -> list[str]:
    errors = []
    if understanding.opportunity_id != payload["opportunity_id"]:
        errors.append(
            f"Response opportunity_id {understanding.opportunity_id} does not match "
            f"{payload['opportunity_id']}."
        )

    for index, item in enumerate(understanding.criteria, start=1):
        if not _evidence_supported(item.evidence_text, payload, item.source_section):
            errors.append(
                f"Unsupported evidence_text for criterion {index} "
                f"({item.criterion_type}): {item.evidence_text!r}"
            )
    return errors


def _collapse_full_repetition(value: str) -> str:
    """Collapse a whole value made of the same adjacent phrase repeated N times.

    This is deliberately conservative. It fixes source/model artefacts such as
    "Diploma Diploma Diploma" or a complete qualification phrase repeated three
    times, but does not try fuzzy semantic rewriting.
    """
    text = clean_text(value)
    tokens = text.split()
    count = len(tokens)
    if count < 2:
        return text

    for block_size in range(1, (count // 2) + 1):
        if count % block_size:
            continue
        block = tokens[:block_size]
        repetitions = count // block_size
        if repetitions < 2:
            continue
        if block * repetitions == tokens:
            return " ".join(block)
    return text


def _semantic_key_text(value: str) -> str:
    """Normalize only mechanical differences for deterministic duplicate guard."""
    text = _collapse_full_repetition(value).casefold()
    text = re.sub(r"[^\w]+", " ", text, flags=re.UNICODE)
    return " ".join(text.split())


def _criterion_rows(understanding):
    rows = []
    for item in understanding.criteria:
        metadata = {}
        if item.context:
            metadata["context"] = _collapse_full_repetition(item.context)

        raw_value = _collapse_full_repetition(item.value)
        if not raw_value:
            continue

        rows.append({
            "criterion_type": item.criterion_type,
            "requirement_level": item.requirement_level,
            "raw_value": raw_value,
            "evidence_text": _collapse_full_repetition(item.evidence_text),
            "source_section": clean_text(item.source_section)[:120],
            "minimum_years": item.minimum_years if item.criterion_type == "experience" else None,
            "maximum_years": item.maximum_years if item.criterion_type == "experience" else None,
            "metadata": metadata,
        })

    # Gemini is instructed to consolidate semantic duplicates. This second
    # deterministic layer is intentionally conservative: it only merges criteria
    # that are mechanically the same after punctuation/case/spacing cleanup.
    # For the same criterion/context, REQUIRED outranks PREFERRED, which outranks
    # UNKNOWN. At equal strength, keep the shortest clear evidence quote.
    strength = {"required": 3, "preferred": 2, "unknown": 1}
    selected = {}
    order = []

    for row in rows:
        context_key = ""
        if row["criterion_type"] == "experience":
            context_key = _semantic_key_text((row.get("metadata") or {}).get("context", ""))

        key = (
            row["criterion_type"],
            _semantic_key_text(row["raw_value"]),
            row.get("minimum_years"),
            row.get("maximum_years"),
            context_key,
        )

        current = selected.get(key)
        if current is None:
            selected[key] = row
            order.append(key)
            continue

        current_strength = strength.get(current["requirement_level"], 0)
        new_strength = strength.get(row["requirement_level"], 0)
        if new_strength > current_strength:
            selected[key] = row
            continue
        if new_strength == current_strength and len(row["evidence_text"]) < len(current["evidence_text"]):
            selected[key] = row

    return [selected[key] for key in order]


def _mark_failed(opportunity, payload, extractor, message, *, raw_output=None, validation_errors=None):
    extraction, _ = OpportunityExtraction.objects.get_or_create(
        opportunity=opportunity,
        defaults={
            "input_hash": payload["input_hash"],
            "model_name": extractor.model_name,
        },
    )
    extraction.status = "failed"
    extraction.input_hash = payload["input_hash"]
    extraction.source_completeness = payload["source_completeness"]
    extraction.extractor = extractor.provider
    extraction.model_name = extractor.model_name
    extraction.thinking_level = extractor.thinking_level
    extraction.schema_version = settings.OPPORTUNITY_AI_SCHEMA_VERSION
    extraction.prompt_version = settings.OPPORTUNITY_AI_PROMPT_VERSION
    extraction.raw_output = raw_output or {}
    extraction.validation_errors = validation_errors or []
    extraction.error_message = str(message)
    extraction.processed_at = timezone.now()
    extraction.save()
    extraction.criteria.all().delete()
    return ProcessingResult(opportunity.pk, "failed", 0, str(message))


def save_validated_import(opportunity, payload, understanding, extractor):
    """Retired legacy boundary retained only for an explicit failure message."""
    del opportunity, payload, understanding, extractor
    raise RuntimeError(
        "Legacy extraction activation is retired; import the Clean-Job v1 contract instead."
    )


def _set_processing(opportunity, payload, extractor):
    values = {
        "status": "processing",
        "input_hash": payload["input_hash"],
        "source_completeness": payload["source_completeness"],
        "extractor": extractor.provider,
        "model_name": extractor.model_name,
        "thinking_level": extractor.thinking_level,
        "schema_version": settings.OPPORTUNITY_AI_SCHEMA_VERSION,
        "prompt_version": settings.OPPORTUNITY_AI_PROMPT_VERSION,
        "error_message": "",
        "validation_errors": [],
    }
    extraction, created = OpportunityExtraction.objects.get_or_create(
        opportunity=opportunity,
        defaults={**values},
    )
    if not created:
        for field, value in values.items():
            setattr(extraction, field, value)
        extraction.save(update_fields=[*values.keys(), "updated_at"])
    return extraction


def _extract_one(extractor, payload):
    method = getattr(extractor, "extract_one", None)
    if callable(method):
        return method(payload)

    # Compatibility for tests/custom providers that still expose the old batch
    # method. Exactly one payload is sent and exactly one result is accepted.
    response = extractor.extract([payload])
    if len(response.opportunities) != 1:
        raise RuntimeError(
            f"Extractor returned {len(response.opportunities)} results for one opportunity."
        )
    return response.opportunities[0]


def process_opportunity_batch(opportunities, *, extractor=None, force=False):
    """Reject the retired internal cleaner for source opportunities.

    Employer-entered vacancies keep their existing structured-authority skip.
    External source jobs must use export -> external clean -> validated import.
    """
    opportunities = list(opportunities)
    direct = [item for item in opportunities if item.source_platform == 'CareerGPS Direct']
    opportunities = [item for item in opportunities if item.source_platform != 'CareerGPS Direct']
    results = [ProcessingResult(item.pk, 'skipped', 0, 'employer_structured') for item in direct]
    if not opportunities:
        return results
    raise RuntimeError(
        "Internal opportunity extraction is disabled. Export source jobs for "
        "external cleaning and import the validated cleaned-result JSON instead."
    )
