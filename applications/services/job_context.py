from __future__ import annotations


def _location(value):
    if isinstance(value, str):
        return value
    value = value or {}
    return ", ".join(str(value.get(key) or "").strip() for key in (
        "city", "region", "country") if value.get(key))


def _criteria_groups(revision):
    groups = {
        "qualifications": [], "experience": [], "skills": [],
        "certifications": [], "responsibilities": [],
        "preferred_requirements": [], "other_requirements": [],
    }
    for criterion in revision.criteria.all():
        value = str(criterion.raw_value or "").strip()
        if not value:
            continue
        if criterion.requirement_level == "PREFERRED":
            groups["preferred_requirements"].append(value)
            continue
        target = {
            "QUALIFICATION": "qualifications", "EXPERIENCE": "experience",
            "SKILL": "skills", "CERTIFICATION": "certifications",
        }.get(criterion.criterion_type, "other_requirements")
        groups[target].append(value)
    return groups


def catalogue_job_data(opportunity, clean_revision=None):
    revision = clean_revision or opportunity.active_clean_revision
    if not revision:
        return None
    return {
        "title": revision.cleaned_title, "employer": revision.company,
        "location": _location(revision.location),
        "description": revision.cleaned_description,
        **_criteria_groups(revision),
        "deadline": revision.closing_at,
        "work_arrangement": revision.work_arrangement,
        "employment_type": revision.employment_type,
        "application_instructions": "", "source_url": revision.source_url,
        "clean_revision_id": revision.pk,
        "source_input_hash": revision.source_input_hash,
    }


def job_context_text(data):
    labels = [
        ("Title", data.get("title", "")), ("Employer", data.get("employer", "")),
        ("Location", data.get("location", "")), ("Description", data.get("description", "")),
        ("Qualifications", data.get("qualifications", [])),
        ("Experience", data.get("experience", [])), ("Skills", data.get("skills", [])),
        ("Certifications", data.get("certifications", [])),
        ("Responsibilities", data.get("responsibilities", [])),
        ("Preferred requirements", data.get("preferred_requirements", [])),
        ("Other requirements", data.get("other_requirements", [])),
        ("Work arrangement", data.get("work_arrangement", "")),
        ("Employment type", data.get("employment_type", "")),
        ("Deadline", data.get("deadline", "")),
        ("Application instructions", data.get("application_instructions", "")),
    ]
    lines = []
    for label, value in labels:
        if isinstance(value, list):
            if value:
                lines.append(f"{label}: " + "; ".join(str(item) for item in value))
        elif value:
            lines.append(f"{label}: {value}")
    return "\n".join(lines)


def resolve_job_context(opportunity, *, clean_revision=None, force=False):
    """Return immutable Clean-Job facts; legacy/raw fallbacks are forbidden."""
    del force
    revision = clean_revision or opportunity.active_clean_revision
    valid_selected = bool(
        revision and revision.opportunity_id == opportunity.pk
        and revision.status in {"validated", "active", "superseded"}
        and revision.schema_version == "1.1"
    )
    if clean_revision is None:
        valid_selected = bool(
            valid_selected and revision.status == "active"
            and revision.pk == opportunity.active_clean_revision_id
            and revision.raw_opportunity_id == revision.source_record.current_raw_id
        )
    if not valid_selected:
        return {"status": "clean_revision_unavailable", "limited": True, "data": None}
    return {"status": "clean_revision_available", "limited": False,
            "data": catalogue_job_data(opportunity, revision)}
