"""Compatibility view over the active immutable Clean-Job revision.

No legacy extraction, raw field, or employer-draft fallback is permitted here.
"""
from dataclasses import dataclass
from types import SimpleNamespace


@dataclass(frozen=True)
class RequirementProjection:
    origin: str
    source_completeness: str
    criteria: tuple
    status: str = "ready"


def requirement_projection(opportunity):
    revision = opportunity.active_clean_revision
    if not revision or revision.status != "active" or revision.schema_version != "1.1":
        return None
    if revision.raw_opportunity_id != revision.source_record.current_raw_id:
        return None
    criteria = []
    for row in revision.criteria.all():
        experience = row.experience or {}
        criteria.append(SimpleNamespace(
            raw_value=row.raw_value,
            normalized_value=row.normalized_value,
            criterion_type=row.criterion_type.lower(),
            requirement_level=row.requirement_level.lower(),
            significance=row.significance.lower(),
            source_section=row.source_section,
            minimum_years=(experience.get("minimum_months") / 12
                           if experience.get("minimum_months") is not None else None),
            maximum_years=None,
            metadata={"context": experience.get("context", ""),
                      "minimum_nqf": (row.qualification or {}).get("nqf_level")},
            evidence_text="; ".join(
                evidence.text or "" for evidence in row.evidence.all()),
        ))
    return RequirementProjection("clean_job_v1", "source_evidenced", tuple(criteria))
