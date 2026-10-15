"""Compatibility-only sparse-feed copy for historical matcher result objects."""

from django.urls import reverse
from django.db.models import Q

from core.models import Profile
from core.services.career_intent import profile_careers
from core.matching import (
    CriterionImportance, EvidenceSignificance, MatchStatus,
)


def _has_substantive_profile_evidence(profile):
    return Profile.objects.filter(pk=profile.pk).filter(
        Q(profile_skills__confirmed_by_user=True) |
        Q(education_records__confirmed_by_user=True) |
        Q(work_experiences__confirmed_by_user=True) |
        Q(certifications__confirmed_by_user=True) |
        Q(languages__confirmed_by_user=True)
    ).exists()


def sparse_profile_context(profile):
    return (
        bool(profile_careers(profile)),
        _has_substantive_profile_evidence(profile),
    )


def transition_disclosure(transition):
    """Disclosure copy for a stored feed transition; nothing is changed here.

    The transition state already decided what the feed may show.  This only
    turns that decision into user-facing wording, so the page can say *why*
    recommendations are updating instead of presenting a thinner feed as a
    thinner market.  Returns None when there is nothing to disclose.
    """
    if transition is None or not transition.message:
        return None
    return {
        'kind': 'transition',
        'state': transition.state,
        'title': 'Recommendations are updating',
        'message': transition.message,
    }


def explain_sparse_feed(profile, *, visible_feed, full_feed, evaluated,
                        filters_active=False, profile_context=None):
    """Explain only facts established by the same feed/evaluation used for display."""
    count = len(visible_feed)
    if count > 2:
        return None

    title = (
        "No relevant opportunities found right now." if count == 0
        else f"{count} relevant opportunit{'y' if count == 1 else 'ies'} found"
    )
    if filters_active and len(full_feed) > count:
        return {
            "kind": "filters", "title": title,
            "message": "Your current filters are hiding some opportunities in your personalised feed.",
            "action_label": "Clear filters", "action_url": reverse("opportunities"),
        }

    has_direction, has_evidence = profile_context or sparse_profile_context(profile)
    has_trusted_target = any(item["discovery_priority"] > 0 for item in full_feed)
    has_comparable_non_target = any(
        item["discovery_priority"] == 0 and any(
            criterion.importance == CriterionImportance.REQUIRED
            and criterion.significance in {EvidenceSignificance.CRUCIAL, EvidenceSignificance.CORE}
            and criterion.status != MatchStatus.UNKNOWN
            for criterion in item["criteria"]
        )
        for item in evaluated
    )
    if not has_direction and not has_evidence and count == 0:
        message = "Add target roles in Career Preferences or more profile evidence to start receiving relevant opportunities."
        if has_comparable_non_target:
            message += " Your profile currently has limited comparable evidence for available non-target jobs."
        return {
            "kind": "starting", "title": title, "message": message,
            "action_label": "Edit Career Preferences", "action_url": reverse("profile_section", args=["career-preferences"]),
        }
    if has_direction and not has_trusted_target:
        return {
            "kind": "no_target_vacancies", "title": title,
            "message": "Your career direction is saved. CareerGPS currently has no active vacancies it can confidently connect to that direction; evidence-supported jobs may still appear.",
            "action_label": "Review Career Preferences", "action_url": reverse("profile_section", args=["career-preferences"]),
        }
    if not has_evidence and has_comparable_non_target:
        return {
            "kind": "limited_evidence", "title": title,
            "message": "Your current profile provides limited comparable evidence for additional non-target opportunities.",
            "action_label": "Update profile evidence", "action_url": reverse("profile"),
        }
    if count:
        return {
            "kind": "narrow_feed", "title": title,
            "message": "CareerGPS keeps this list narrow: only opportunities connected to your saved career direction or supported by required profile evidence are shown.",
            "action_label": "", "action_url": "",
        }
    return {
        "kind": "no_admission", "title": title,
        "message": "No current vacancies meet the personalised feed's career-target or substantive-evidence rules.",
        "action_label": "", "action_url": "",
    }
