import re

from django.db import transaction
from django.utils import timezone
from urllib.parse import urlparse

from core.models import Opportunity
from ingestion.models import (
    IngestionRun, OpportunityExtraction, OpportunitySourceRecord, RawOpportunity,
)
from .deduplication import apply_canonical_fields, find_duplicate
from .lifecycle import mark_missing_source_records, refresh_opportunity_lifecycle
from .fingerprinting import stable_content_hash
from .normalization import normalize_record, plain_text


COUNTER_FIELDS = (
    "records_found", "raw_records_created", "opportunities_created", "opportunities_updated",
    "duplicates_found", "out_of_scope_count", "internship_count", "learnership_count",
    "graduate_count", "entry_level_count", "unclassified_count", "errors_count",
    "job_kind_count", "vacation_work_kind_count", "unknown_kind_count",
    "early_career_count", "experienced_count", "unknown_career_stage_count",
    "in_scope_count",
    "scope_unclassified_count",
    "evidence_ambiguities_count", "invalid_experience_parses_count",
    "title_only_exclusions_count", "numeric_only_exclusions_count",
    "discovery_qualification_duration_as_experience_count",
    "woolworths_qualification_duration_as_experience_count",
    "greenhouse_office_as_business_unit_count",
    "greenhouse_education_config_as_qualification_count",
    "application_question_as_eligibility_count",
    "invalid_application_routes_count",
)


def _new_counters():
    return {field: 0 for field in COUNTER_FIELDS}


def _application_url_is_usable(record):
    url = record.apply_url or record.source_url
    parsed = urlparse(str(url or "").strip())
    syntactically_valid = parsed.scheme in {"http", "https"} and bool(parsed.netloc)
    job_specific = parsed.path.rstrip("/").lower() not in {
        "", "/", "/jobs", "/job", "/search", "/careers",
        "/candidateapp/jobs/browse", "/candidateapp/jobs/categories",
    }
    if record.application_link_valid is False:
        return False
    return syntactically_valid and job_specific


def _invalidate_existing_source_extraction(source, external_id):
    """Make a prior interpretation non-current when a new raw version arrives."""
    opportunity_ids = OpportunitySourceRecord.objects.filter(
        source=source,
        source_external_id=str(external_id),
    ).values("opportunity_id")
    return OpportunityExtraction.objects.filter(
        opportunity_id__in=opportunity_ids,
    ).exclude(status="stale").update(
        status="stale",
        updated_at=timezone.now(),
    )


def run_adapter(adapter, source, dry_run=False, explain=False, explain_count=20):
    counters = _new_counters()
    diagnostics = []
    explain_count = max(0, int(explain_count))
    run = None if dry_run else IngestionRun.objects.create(source=source, metadata={"adapter": adapter.__class__.__name__})
    seen_ids = set()
    errors = []
    if not dry_run:
        source.last_checked_at = timezone.now()
        source.save(update_fields=["last_checked_at"])

    try:
        records = adapter.fetch()
        for record in records:
            raw = None
            raw_created = False
            counters["records_found"] += 1
            seen_ids.add(str(record.external_id))
            try:
                if not dry_run:
                    digest = stable_content_hash(adapter, record)
                    raw, raw_created = RawOpportunity.objects.get_or_create(
                        source=source,
                        source_external_id=str(record.external_id),
                        content_hash=digest,
                        defaults={
                            "ingestion_run": run, "listing_url": record.listing_url,
                            "source_url": record.source_url,
                            "apply_url": record.apply_url, "raw_payload": record.payload,
                            "raw_content": record.raw_content, "parser_version": record.parser_version,
                        },
                    )
                    counters["raw_records_created"] += int(raw_created)
                    if raw_created:
                        _invalidate_existing_source_extraction(source, record.external_id)

                normalized = normalize_record(record, source)
                experience_text = normalized["experience_text"]
                qualification_duration_in_experience = bool(
                    re.search(r"\b\d+\s*[- ]?year\b", experience_text, re.I)
                    and re.search(r"\b(?:qualification|degree|diploma|education|certificate)\b", experience_text, re.I)
                    and not re.search(r"\b(?:work|working|professional|relevant)?\s*experience\b", experience_text, re.I)
                )
                if source.code == "discovery" and qualification_duration_in_experience:
                    counters["discovery_qualification_duration_as_experience_count"] += 1
                if source.code == "woolworths" and qualification_duration_in_experience:
                    counters["woolworths_qualification_duration_as_experience_count"] += 1
                structured = record.payload.get("structured", {}) if isinstance(record.payload, dict) else {}
                if source.code.startswith("greenhouse"):
                    if structured.get("offices") and record.fields.get("business_unit"):
                        counters["greenhouse_office_as_business_unit_count"] += 1
                    education_config = structured.get("education")
                    if (
                        isinstance(education_config, str)
                        and education_config in {
                            "education_required", "education_optional", "education_disabled"
                        }
                        and normalized["qualification_text"]
                    ):
                        counters["greenhouse_education_config_as_qualification_count"] += 1
                    if structured.get("questions") and normalized["eligibility_text"]:
                        counters["application_question_as_eligibility_count"] += 1
                if not normalized["title"] or not normalized["company"]:
                    raise ValueError("A source record must include a title and company.")
                if not _application_url_is_usable(record):
                    counters["invalid_application_routes_count"] += 1
                    raise ValueError("A source record must include a valid job-specific HTTP/HTTPS application route.")
                classification = normalized["opportunity_type"]
                opportunity_kind = normalized["opportunity_kind"]
                career_stage = normalized["career_stage"]
                scope_status = normalized["scope_status"]
                if classification in {"internship", "learnership", "graduate", "entry_level", "unclassified"}:
                    counters[f"{classification}_count"] += 1
                if opportunity_kind in {"job", "vacation_work", "unknown"}:
                    counters[f"{opportunity_kind}_kind_count"] += 1
                counters[f"{career_stage}_career_stage_count" if career_stage == "unknown" else f"{career_stage}_count"] += 1
                if scope_status == "in_scope":
                    counters["in_scope_count"] += 1
                elif scope_status == "unclassified":
                    counters["scope_unclassified_count"] += 1
                    counters["evidence_ambiguities_count"] += 1
                if (
                    normalized["experience_text"]
                    and normalized["experience_min_years"] is None
                    and not record.fields.get("experience_scalar_intentionally_unset")
                    and re.search(r"\b\d+\s*(?:\+|[-–—]\s*\d+|to\s+\d+)?\s*(?:relevant\s+)?(?:years?|yrs?)\b", normalized["experience_text"], re.I)
                ):
                    counters["invalid_experience_parses_count"] += 1
                if explain and len(diagnostics) < explain_count:
                    reason = normalized["classification_reason"]
                    diagnostics.append({
                        "title": normalized["title"],
                        "source": source.code,
                        "opportunity_type": classification,
                        "opportunity_kind": opportunity_kind,
                        "career_stage": career_stage,
                        "scope_status": scope_status,
                        "status": "out_of_scope" if scope_status == "out_of_scope" else "publish",
                        "classification_reason": reason,
                        "classification_evidence": normalized["classification_evidence"],
                        "evidence_provenance": normalized["evidence_provenance"],
                        "experience_text": normalized["experience_text"],
                        "experience_min_years": normalized["experience_min_years"],
                        "qualification_text": normalized["qualification_text"],
                        "minimum_nqf": normalized["minimum_nqf"],
                        "listing_kind": normalized["listing_kind"],
                        "listing_url": record.listing_url,
                        "source_url": record.source_url,
                        "apply_url": record.apply_url,
                        "detail_http_status": record.detail_http_status,
                        "application_link_valid": _application_url_is_usable(record),
                    })
                if dry_run:
                    if scope_status != "out_of_scope":
                        # A dry run is intentionally independent of canonical
                        # database schema/data, so proposed migrations can be
                        # validated before they are applied.
                        counters["opportunities_created"] += 1
                    else:
                        counters["out_of_scope_count"] += 1
                    continue

                if scope_status == "out_of_scope":
                    raw.normalization_status = "out_of_scope"
                    raw.classified_type = classification
                    raw.opportunity_kind = opportunity_kind
                    raw.career_stage = career_stage
                    raw.scope_status = scope_status
                    raw.save(update_fields=[
                        "normalization_status", "classified_type", "opportunity_kind",
                        "career_stage", "scope_status",
                    ])
                    counters["out_of_scope_count"] += 1
                    continue

                with transaction.atomic():
                    opportunity, source_record, duplicate_reason = find_duplicate(source, record.external_id, normalized)
                    creating = opportunity is None
                    if creating:
                        opportunity = Opportunity()
                    else:
                        counters["duplicates_found"] += int(bool(duplicate_reason))
                    apply_canonical_fields(
                        opportunity,
                        normalized,
                        source,
                        creating=creating,
                        authoritative_update=source_record is not None,
                    )
                    counters["opportunities_created" if creating else "opportunities_updated"] += 1

                    source_record, _ = OpportunitySourceRecord.objects.update_or_create(
                        source=source,
                        source_external_id=str(record.external_id),
                        defaults={
                            "opportunity": opportunity, "current_raw": raw,
                            "listing_url": record.listing_url,
                            "source_url": record.source_url, "apply_url": record.apply_url,
                            "reference_number": normalized.get("reference_number", ""),
                            "source_fingerprint": normalized["canonical_fingerprint"],
                            "missing_since": None, "active_at_source": True,
                        },
                    )
                    # A newly linked source version may have deduplicated onto an
                    # existing opportunity. It is still new evidence and requires
                    # another external clean before professional use.
                    if raw_created and not creating:
                        OpportunityExtraction.objects.filter(
                            opportunity=opportunity,
                        ).exclude(status="stale").update(
                            status="stale",
                            updated_at=timezone.now(),
                        )
                    raw.normalization_status = "unclassified" if scope_status == "unclassified" else "published"
                    raw.classified_type = classification
                    raw.opportunity_kind = opportunity_kind
                    raw.career_stage = career_stage
                    raw.scope_status = scope_status
                    raw.save(update_fields=[
                        "normalization_status", "classified_type", "opportunity_kind",
                        "career_stage", "scope_status",
                    ])
            except Exception as exc:
                counters["errors_count"] += 1
                errors.append({"external_id": str(record.external_id), "error": str(exc)[:1000]})
                if explain and len(diagnostics) < explain_count:
                    diagnostics.append({
                        "title": plain_text(record.fields.get("title")),
                        "source": source.code,
                        "opportunity_type": "",
                        "status": "error",
                        "classification_reason": str(exc)[:300],
                        "experience_text": plain_text(record.fields.get("experience_text")),
                        "experience_min_years": record.fields.get("experience_min_years"),
                        "qualification_text": plain_text(record.fields.get("qualification_text")),
                        "minimum_nqf": record.fields.get("minimum_nqf"),
                        "listing_kind": record.fields.get("listing_kind", ""),
                        "listing_url": record.listing_url,
                        "source_url": record.source_url,
                        "apply_url": record.apply_url,
                        "detail_http_status": record.detail_http_status,
                        "application_link_valid": False,
                    })
                if raw is not None:
                    raw.normalization_status = "error"
                    raw.normalization_error = str(exc)[:4000]
                    raw.save(update_fields=["normalization_status", "normalization_error"])

        adapter_errors = list(getattr(adapter, "errors", []))
        if adapter_errors:
            counters["errors_count"] += len(adapter_errors)
            errors.extend(adapter_errors)
        for key, value in getattr(adapter, "stats", {}).items():
            counters[key] = value

        if not dry_run:
            mark_missing_source_records(source, seen_ids)
            refresh_opportunity_lifecycle()
            source.last_success_at = timezone.now()
            source.last_error = ""
            source.save(update_fields=["last_success_at", "last_error"])
    except Exception as exc:
        counters["errors_count"] += 1
        errors.append({"source": source.code, "error": str(exc)[:2000]})
        if not dry_run:
            source.last_error = str(exc)[:4000]
            source.save(update_fields=["last_error"])

    if run:
        for key, value in counters.items():
            setattr(run, key, value)
        run.finished_at = timezone.now()
        run.error_details = errors
        run.error_summary = "\n".join(
            str(item.get("error") or item.get("step") or item) for item in errors
        )[:8000]
        if errors and counters["records_found"]:
            run.status = "partial"
        elif errors:
            run.status = "failed"
        else:
            run.status = "success"
        run.save()
    counters["errors"] = errors
    counters["diagnostics"] = diagnostics
    return counters
