from __future__ import annotations

import copy

from applications.models import Application, GeneratedDocument
from applications.services.candidate_facts import (
    build_candidate_fact_bundle,
)
from applications.services.job_context import resolve_job_context
from core.models import Opportunity, Profile, SavedOpportunity


ACTIVE_APPLICATION_STATUSES = {
    "planning",
    "ready_to_apply",
    "applied",
    "assessment",
    "interview",
}


def interview_choices_for_user(user):
    """
    Return the two lists shown in Interview Practice.

    An opportunity with an active Application Workspace is shown
    under applications and is not duplicated under saved.
    """

    applications = list(
        Application.objects.filter(
            user=user,
            status__in=ACTIVE_APPLICATION_STATUSES,
        )
        .select_related("opportunity", "clean_revision")
        .order_by("-updated_at", "-id")
    )

    application_opportunity_ids = {
        application.opportunity_id
        for application in applications
    }

    saved_opportunities = list(
        SavedOpportunity.objects.filter(
            user=user, opportunity__in=Opportunity.objects.professionally_ready())
        .exclude(
            opportunity_id__in=application_opportunity_ids
        )
        .select_related("opportunity", "opportunity__active_clean_revision")
        .order_by("-saved_at", "-id")
    )

    return {
        "applications": applications,
        "saved_opportunities": saved_opportunities,
    }


def resolve_user_interview_source(user, opportunity_id):
    """
    Authorise an interview source.

    Important:
    selecting a saved opportunity must NOT create an Application.
    """

    application = (
        Application.objects.filter(
            user=user,
            opportunity_id=opportunity_id,
            status__in=ACTIVE_APPLICATION_STATUSES,
        )
        .select_related("opportunity", "clean_revision")
        .first()
    )

    if application:
        return (
            application.opportunity,
            application,
            "application",
        )

    saved = (
        SavedOpportunity.objects.filter(
            user=user,
            opportunity_id=opportunity_id,
            opportunity__in=Opportunity.objects.professionally_ready(),
        )
        .select_related("opportunity", "opportunity__active_clean_revision")
        .first()
    )

    if saved:
        return (
            saved.opportunity,
            None,
            "saved",
        )

    return None, None, None


def _sanitise_candidate_bundle(bundle):
    """
    Interview Gemini does not need phone/email contact data.

    Keep the user's professional evidence while minimising
    unrelated personal information.
    """

    result = copy.deepcopy(bundle)
    clean_facts = []

    for fact in result.get("facts", []):
        if fact.get("type") != "basic":
            clean_facts.append(fact)
            continue

        data = fact.get("data", {})
        safe_data = {
            "name": data.get("name", ""),
            "location": data.get("location", ""),
        }

        safe_text = " | ".join(
            value
            for value in [
                safe_data["name"],
                safe_data["location"],
            ]
            if value
        )

        clean_facts.append({
            "id": fact["id"],
            "type": "basic",
            "text": safe_text,
            "data": safe_data,
        })

    result["facts"] = clean_facts
    return result


def _sanitise_document_data(value):
    """
    Remove contact metadata from generated documents before
    using them as supporting interview context.
    """

    blocked = {
        "email",
        "phone",
        "telephone",
        "mobile",
    }

    if isinstance(value, dict):
        return {
            key: _sanitise_document_data(item)
            for key, item in value.items()
            if str(key).casefold() not in blocked
        }

    if isinstance(value, list):
        return [
            _sanitise_document_data(item)
            for item in value
        ]

    return value


def _latest_document(user, opportunity, document_type, application=None):
    base = GeneratedDocument.objects.filter(
        user=user,
        opportunity=opportunity,
        document_type=document_type,
    )

    # Prefer a document attached to this exact Application Workspace.
    if application is not None:
        exact = (
            base.filter(application=application)
            .order_by("-created_at", "-id")
            .first()
        )
        if exact:
            return exact

    # Fall back to the latest opportunity-specific document.
    return (
        base.order_by("-created_at", "-id")
        .first()
    )


def _application_materials(user, opportunity, application=None):
    result = {}

    for document_type in ("cv", "cover_letter"):
        document = _latest_document(
            user,
            opportunity,
            document_type,
            application=application,
        )

        if not document:
            continue

        result[document_type] = {
            "id": document.id,
            "document_type": document.document_type,
            "created_at": document.created_at.isoformat(),
            "candidate_fact_ids": (
                document.candidate_fact_ids or []
            ),
            "structured_data": _sanitise_document_data(
                document.structured_data or {}
            ),
        }

    return result


def build_interview_context(user, opportunity, application=None):
    """
    Assemble the single factual context used for both:
    - interview question generation
    - post-interview evaluation
    """

    profile = Profile.objects.get(user=user)

    selected_revision = (
        application.clean_revision if application and application.clean_revision_id
        else opportunity.active_clean_revision
    )
    job_context = resolve_job_context(opportunity, clean_revision=selected_revision)

    raw_candidate_bundle = build_candidate_fact_bundle(profile)

    candidate_bundle = _sanitise_candidate_bundle(
        raw_candidate_bundle
    )

    documents = _application_materials(
        user,
        opportunity,
        application=application,
    )

    candidate_fact_ids = [
        fact["id"]
        for fact in candidate_bundle.get("facts", [])
    ]

    generated_document_ids = [
        document["id"]
        for document in documents.values()
    ]

    snapshot = {
        "job_info_status": job_context["status"],
        "job_information_limited": bool(
            job_context.get("limited")
        ),

        "job": job_context["data"],

        "candidate": {
            "profile_id": candidate_bundle["profile_id"],
            "qualification": candidate_bundle["qualification"],
            "institution": candidate_bundle["institution"],
            "skill_names": candidate_bundle["skill_names"],
            "facts": candidate_bundle["facts"],
        },

        "application_materials": documents,

        "rules": {
            "candidate_facts_are_authoritative": True,
            "job_information_is_authoritative": True,
            "application_materials_are_supporting_only": True,
            "application_materials_cannot_create_candidate_facts": True,
            "matching_metadata_included": False,
        },
    }

    return {
        "snapshot": snapshot,
        "job_info_status": job_context["status"],
        "candidate_fact_ids": candidate_fact_ids,
        "generated_document_ids": generated_document_ids,
    }
