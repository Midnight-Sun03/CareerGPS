from django.db.models import Min
from django.utils import timezone

from core.models import Opportunity
from ingestion.models import OpportunitySourceRecord
from .normalization import normalized_key


CANONICAL_FIELDS = {
    "title", "company", "opportunity_type", "opportunity_kind", "career_stage", "scope_status",
    "classification_reason", "classification_evidence", "evidence_provenance", "programme_identity",
    "employment_type", "listing_kind",
    "work_arrangement", "location", "location_text", "city", "province", "country",
    "latitude", "longitude",
    "posted_at", "closing_date", "department", "business_unit", "sector", "description",
    "responsibilities", "duties", "qualification_text", "required_qualification",
    "minimum_nqf", "experience_text", "experience_min_years", "eligibility_text",
    "qualification_minimum", "qualification_preferred", "experience_max_years",
    "preferred_experience_text", "preferred_experience_min_years", "preferred_experience_max_years",
    "source_native_category", "source_native_career_level",
    "programme_signals", "early_career_signals", "seniority_signals", "source_specific_evidence",
    "eligibility", "office", "reference_number", "primary_apply_url", "redirect_url",
    "source_platform", "salary_min", "salary_max", "salary_currency",
    "salary_is_predicted", "compensation_text", "stipend", "canonical_fingerprint",
    "last_seen_at", "lifecycle_status", "is_active",
}
# Moderation state intentionally never belongs to CANONICAL_FIELDS.

AUTHORITATIVE_CLEARABLE_FIELDS = {
    "required_qualification", "qualification_text", "qualification_minimum",
    "qualification_preferred", "minimum_nqf",
    "experience_text", "experience_min_years", "experience_max_years",
    "preferred_experience_text", "preferred_experience_min_years",
    "preferred_experience_max_years",
}


def _local_calendar_date(value):
    if value is None:
        return None
    if hasattr(value, "tzinfo") and timezone.is_aware(value):
        value = timezone.localtime(value)
    return value.date() if hasattr(value, "date") else value


def find_duplicate(source, external_id, normalized):
    existing_record = OpportunitySourceRecord.objects.select_related("opportunity").filter(
        source=source, source_external_id=external_id
    ).first()
    if existing_record:
        return existing_record.opportunity, existing_record, "source_id"

    reference = normalized.get("reference_number")
    if reference:
        # Within one source, its authoritative external ID is the identity.
        # A different external ID must not be collapsed into an existing
        # listing merely because text or even a reused reference happens to
        # match.  References are only used to link records across providers.
        records = (
            OpportunitySourceRecord.objects.select_related("opportunity")
            .filter(reference_number__iexact=reference)
            .exclude(source=source)
            .exclude(opportunity__source_platform="CareerGPS Direct")
        )
        company_key = normalized_key(normalized.get("company"))
        title_key = normalized_key(normalized.get("title"))
        incoming_location = normalized_key(
            normalized.get("location_text") or normalized.get("location")
        )
        for record in records:
            opportunity = record.opportunity
            if normalized_key(opportunity.company) != company_key:
                continue
            if normalized_key(opportunity.title) != title_key:
                continue

            existing_location = normalized_key(
                opportunity.location_text or opportunity.location
            )
            location_compatible = (
                not incoming_location
                or not existing_location
                or incoming_location == existing_location
                or incoming_location in existing_location
                or existing_location in incoming_location
            )
            incoming_posted = normalized.get("posted_at")
            posted_conflicts = (
                incoming_posted
                and opportunity.posted_at
                and _local_calendar_date(incoming_posted)
                != _local_calendar_date(opportunity.posted_at)
            )
            incoming_closing = normalized.get("closing_date")
            closing_conflicts = (
                incoming_closing
                and opportunity.closing_date
                and incoming_closing != opportunity.closing_date
            )
            if location_compatible and not posted_conflicts and not closing_conflicts:
                return opportunity, None, "reference"

    fingerprint = normalized.get("canonical_fingerprint")
    if fingerprint:
        opportunity = Opportunity.objects.filter(canonical_fingerprint=fingerprint).exclude(source_platform="CareerGPS Direct").first()
        if opportunity:
            return opportunity, None, "fingerprint"
    return None, None, ""


def apply_canonical_fields(
    opportunity, normalized, source, creating=False, authoritative_update=False
):
    # Direct publications have an employer-owned source and lifecycle. External
    # ingestion must not silently replace them or reopen a closed vacancy.
    if not creating and opportunity.source_platform == 'CareerGPS Direct':
        return opportunity
    best_priority = None if creating else opportunity.source_records.aggregate(value=Min("source__priority"))["value"]
    may_replace = creating or best_priority is None or source.priority <= best_priority
    for field, value in normalized.items():
        if field not in CANONICAL_FIELDS:
            continue
        if value in (None, ""):
            if authoritative_update and may_replace and field in AUTHORITATIVE_CLEARABLE_FIELDS:
                model_field = opportunity._meta.get_field(field)
                setattr(opportunity, field, None if model_field.null else model_field.get_default())
            continue
        current = getattr(opportunity, field, None)
        if may_replace or current in (None, "") or field in {"last_seen_at", "is_active", "lifecycle_status"}:
            setattr(opportunity, field, value)
    if creating:
        opportunity.save()
    else:
        # A worker may have loaded this object before a moderator suppressed it.
        # Never write that stale in-memory moderation field back to the database.
        opportunity.save(update_fields=sorted(CANONICAL_FIELDS | {'application_deadline'}))
    return opportunity
