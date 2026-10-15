from __future__ import annotations

import hashlib
import json
import re
from collections import OrderedDict


_WS = re.compile(r"\s+")


def clean_text(value) -> str:
    return _WS.sub(" ", str(value or "").strip())


def _append_section(sections, seen, name, value):
    text = clean_text(value)
    if not text:
        return
    identity = text.casefold()
    if identity in seen:
        return
    seen.add(identity)
    sections[name] = text


def source_completeness_for(opportunity) -> str:
    # Current trusted sources provide the published vacancy. Optional fields
    # omitted by an employer do not turn that vacancy into a partial listing.
    structured_fields = [
        opportunity.qualification_minimum,
        opportunity.qualification_preferred,
        opportunity.experience_text,
        opportunity.preferred_experience_text,
        opportunity.eligibility_text,
        opportunity.eligibility,
        opportunity.responsibilities,
        opportunity.duties,
    ]
    if sum(bool(clean_text(value)) for value in structured_fields) >= 2:
        return "full_structured"
    if clean_text(opportunity.description):
        return "full_text"
    return "unknown"


def build_compact_opportunity_payload(opportunity) -> dict:
    """Return only useful opportunity-side evidence already held by CareerGPS."""
    sections = OrderedDict()
    seen = set()

    _append_section(sections, seen, "title", opportunity.title)
    _append_section(sections, seen, "company", opportunity.company)
    _append_section(
        sections,
        seen,
        "location",
        opportunity.location_text or opportunity.location,
    )
    _append_section(sections, seen, "job_purpose_or_description", opportunity.description)
    _append_section(sections, seen, "minimum_qualification", opportunity.qualification_minimum or opportunity.required_qualification)
    _append_section(sections, seen, "preferred_qualification", opportunity.qualification_preferred)
    _append_section(sections, seen, "required_experience", opportunity.experience_text)
    _append_section(sections, seen, "preferred_experience", opportunity.preferred_experience_text)
    _append_section(sections, seen, "eligibility_or_competencies", opportunity.eligibility_text or opportunity.eligibility)
    _append_section(sections, seen, "responsibilities", opportunity.responsibilities or opportunity.duties)
    _append_section(sections, seen, "work_arrangement", opportunity.work_arrangement)
    _append_section(sections, seen, "sector", opportunity.sector or opportunity.source_native_category)

    # Preserve source-labelled employer fields that may contain useful detail not
    # represented cleanly by the canonical Opportunity columns. Avoid re-sending
    # duplicate text already present above.
    specific = opportunity.source_specific_evidence if isinstance(opportunity.source_specific_evidence, dict) else {}
    ignored_specific_keys = {
        "geography", "application_url_mechanism", "requisition_id", "salary_is_predicted",
        "category_tag", "location_area", "description_fidelity", "absence_is_negative_evidence",
    }
    for key, value in specific.items():
        if key in ignored_specific_keys:
            continue
        if isinstance(value, (str, int, float)):
            _append_section(sections, seen, f"source_{key}", value)

    payload = {
        "opportunity_id": opportunity.pk,
        "source_completeness": source_completeness_for(opportunity),
        "sections": sections,
    }
    payload["input_hash"] = hashlib.sha256(
        json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()
    return payload


def evidence_corpus(payload: dict) -> str:
    return "\n".join(clean_text(value) for value in payload.get("sections", {}).values() if clean_text(value))
