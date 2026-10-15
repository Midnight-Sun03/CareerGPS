"""Canonical Clean-Job v1 factories shared by integration tests.

These helpers exercise the real export/import boundary. They intentionally do
not bypass professional-readiness rules or construct revisions directly.
"""
from hashlib import sha256

from django.utils import timezone

from core.clean_jobs.service import import_clean_payload
from core.clean_jobs.source import source_input_hash
from core.models import Opportunity, ProfileOpportunityEvaluation
from ingestion.models import IngestionSource, OpportunitySourceRecord, RawOpportunity


def valid_clean_payload(record, *, title=None, company=None, description=None,
                        seniority="NON_SENIOR", criteria=None, closing_at=None,
                        posted_at=None, opportunity_kind="VACANCY", run_id=None,
                        clean_location=None, employment_type=None,
                        work_arrangement=None, salary=None):
    opportunity = record.opportunity
    raw = record.current_raw
    title = title or opportunity.title or "Test opportunity"
    company = company or opportunity.company or "Example employer"
    description = description or opportunity.description or "Source-grounded test role."
    def reference(section, text):
        return {"source_section": section, "evidence": {"text": text}}
    raw_text = raw.raw_content
    raw_payload = raw.raw_payload if isinstance(raw.raw_payload, dict) else {}
    company_section = (
        "raw_payload.company"
        if raw_payload.get("company") == company
        else "source_context.publisher_name"
    )
    kind_evidence = opportunity_kind.title().replace('_', ' ')
    employment_evidence = {
        'FULL_TIME': 'full-time', 'PART_TIME': 'part-time',
        'CONTRACT': 'contract', 'TEMPORARY': 'temporary',
        'INTERNSHIP': 'internship', 'LEARNERSHIP': 'learnership',
        'APPRENTICESHIP': 'apprenticeship', 'CASUAL': 'casual',
        'VOLUNTEER': 'volunteer', 'OTHER': 'other',
    }.get((employment_type or ['UNKNOWN'])[0], '')
    kind_section = "raw_content"
    if opportunity_kind == "VACANCY" and opportunity.listing_kind == "vacancy":
        kind_evidence = "vacancy"
        kind_section = "source_context.listing_kind"
    return {
        "schema_version": "1.1",
        "cleaner": {
            "name": "canonical-test-cleaner",
            "version": "1",
            "cleaned_at": timezone.now().isoformat(),
            "run_id": run_id or f"test-{opportunity.pk}-{raw.pk}",
        },
        "source": {
            "source_name": record.source.code,
            "external_opportunity_id": record.source_external_id,
            "source_url": record.source_url or raw.source_url or raw.listing_url,
            "captured_at": raw.fetched_at.isoformat(),
            "source_input_hash": source_input_hash(record),
        },
        "job": {
            "cleaned_title": title,
            "company": company,
            "location": clean_location or {"raw": opportunity.location or "", "city": "",
                                            "region": "", "country": "South Africa"},
            "employment_type": employment_type or ["UNKNOWN"],
            "work_arrangement": work_arrangement or "UNKNOWN",
            "opportunity_kind": opportunity_kind,
            "posted_at": posted_at,
            "closing_at": closing_at,
            "salary": salary,
            "cleaned_description": description,
            "seniority": {
                "label": seniority,
                "reason": "Explicit synthetic test classification.",
                "evidence": [{"text": title}],
            },
            "provenance": {
                "cleaned_title": [reference("raw_content", title)],
                "company": [reference(company_section, company)],
                "location": [reference("raw_content", raw_text)],
                "employment_type": ([reference(
                    "raw_content", employment_evidence)]
                    if employment_type else []),
                "work_arrangement": ([reference("raw_content", work_arrangement)]
                                     if work_arrangement else []),
                "opportunity_kind": [reference(kind_section, kind_evidence)],
                "posted_at": ([reference("raw_content", posted_at)] if posted_at else []),
                "closing_at": ([reference("raw_content", closing_at)] if closing_at else []),
                "salary": ([reference("raw_content", salary.get("raw", ""))]
                           if salary else []),
                "cleaned_description": [reference("raw_content", description)],
            },
        },
        "criteria": criteria or [],
    }


def activate_clean_opportunity(opportunity, *, title=None, company=None,
                               description=None, seniority="NON_SENIOR",
                               criteria=None, closing_at=None, posted_at=None,
                               source_code="canonical-test-source", source_url=None,
                               clean_location=None, employment_type=None,
                               work_arrangement=None, salary=None):
    source, _ = IngestionSource.objects.get_or_create(
        code=source_code,
        defaults={
            "name": "Canonical test source" if source_code == "canonical-test-source" else source_code.title(),
            "source_type": "api" if source_code == "canonical-test-source" else "scraper",
            "company": "" if source_code == "canonical-test-source" else (company or opportunity.company),
        },
    )
    title = title or opportunity.title or "Test opportunity"
    posted_source = posted_at if posted_at is not None else (
        opportunity.date_listed.isoformat() if opportunity.date_listed else None)
    closing_source = closing_at if closing_at is not None else (
        opportunity.closing_date.isoformat() if opportunity.closing_date else None)
    kind_source = {
        "internship": "Internship", "learnership": "Learnership",
        "apprenticeship": "Apprenticeship", "graduate": "Graduate programme",
        "graduate_programme": "Graduate programme", "bursary": "Bursary",
    }.get(opportunity.opportunity_type, "Vacancy")
    source_description = description or opportunity.description or "Source-grounded test role."
    employment_evidence = {
        'FULL_TIME': 'full-time', 'PART_TIME': 'part-time',
        'CONTRACT': 'contract', 'TEMPORARY': 'temporary',
        'INTERNSHIP': 'internship', 'LEARNERSHIP': 'learnership',
        'APPRENTICESHIP': 'apprenticeship', 'CASUAL': 'casual',
        'VOLUNTEER': 'volunteer', 'OTHER': 'other',
    }.get((employment_type or ['UNKNOWN'])[0], '')
    if len(source_description) < 40 or len(source_description.split()) < 6:
        source_description = (
            source_description + " This synthetic vacancy source includes clearly stated "
            "responsibilities and requirements."
        )
    raw_content = "\n".join(filter(None, [title, company or opportunity.company,
                                             source_description,
                                             opportunity.location, "South Africa",
                                             kind_source, posted_source, closing_source,
                                             (clean_location or {}).get("raw"),
                                             employment_evidence,
                                             work_arrangement,
                                             (salary or {}).get("raw"),
                                             *[evidence["text"]
                                               for criterion in (criteria or [])
                                               for evidence in criterion.get("evidence", [])]]))
    content_hash = sha256(raw_content.encode("utf-8")).hexdigest()
    external_id = f"test-clean-{source_code}-{opportunity.pk}"
    source_url = source_url or f"https://example.test/jobs/{opportunity.pk}"
    raw, _ = RawOpportunity.objects.get_or_create(
        source=source, source_external_id=external_id, content_hash=content_hash,
        defaults={
            "raw_content": raw_content,
            "raw_payload": {"title": title, "company": company or opportunity.company,
                            "description": source_description},
            "listing_url": source_url,
            "source_url": source_url,
        },
    )
    record, _ = OpportunitySourceRecord.objects.update_or_create(
        source=source, source_external_id=external_id,
        defaults={
            "opportunity": opportunity,
            "current_raw": raw,
            "listing_url": source_url,
            "source_url": source_url,
            "apply_url": source_url,
            "active_at_source": True,
        },
    )
    payload = valid_clean_payload(
        record, title=title, company=company, description=description,
        seniority=seniority, criteria=criteria,
        opportunity_kind={
            "internship": "INTERNSHIP", "learnership": "LEARNERSHIP",
            "apprenticeship": "APPRENTICESHIP", "graduate": "GRADUATE_PROGRAMME",
            "graduate_programme": "GRADUATE_PROGRAMME", "bursary": "BURSARY",
        }.get(opportunity.opportunity_type, "VACANCY"),
        closing_at=(closing_at if closing_at is not None else
                    opportunity.closing_date.isoformat() if opportunity.closing_date else None),
        posted_at=(posted_at if posted_at is not None else
                   opportunity.date_listed.isoformat() if opportunity.date_listed else None),
        clean_location=clean_location, employment_type=employment_type,
        work_arrangement=work_arrangement, salary=salary,
    )
    result = import_clean_payload(payload)
    return opportunity.clean_revisions.get(pk=result.revision_id)


def create_professional_opportunity(**values):
    clean = {
        key: values.pop(key) for key in tuple(values)
        if key in {"seniority", "criteria", "closing_at", "posted_at",
                   "clean_location", "employment_type", "work_arrangement", "salary"}
    }
    defaults = {
        "title": "Test opportunity",
        "company": "Example employer",
        "description": (
            "Source-grounded test role with clearly stated responsibilities "
            "and requirements."
        ),
        "is_active": True,
        "lifecycle_status": "active",
        "listing_kind": "vacancy",
        "scope_status": "in_scope",
    }
    defaults.update(values)
    opportunity = Opportunity.objects.create(**defaults)
    activate_clean_opportunity(
        opportunity, title=opportunity.title, company=opportunity.company,
        description=opportunity.description, **clean)
    opportunity.refresh_from_db()
    return opportunity


def create_current_evaluation(profile, opportunity, *, category="PROFILE_RELEVANT",
                              band="C", score=0, **values):
    ranks = {"TARGET": 1, "RELATED": 2, "PROFILE_RELEVANT": 3}
    band_ranks = {"A": 1, "B": 2, "C": 3, "E": 4, "D": 5, "F": 6}
    defaults = {
        "clean_revision": opportunity.active_clean_revision,
        "state": "CURRENT",
        "dependency_hash": "test-dependency",
        "discovery_category": category,
        "discovery_rank": ranks[category],
        "band": band,
        "band_rank": band_ranks[band],
        "professional_score": score,
    }
    defaults.update(values)
    return ProfileOpportunityEvaluation.objects.create(
        profile=profile, opportunity=opportunity, **defaults)
