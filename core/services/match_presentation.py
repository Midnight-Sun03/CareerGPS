"""Compatibility-only summaries for historical matcher result objects.

No production view imports this module after the v2 cutover.
"""

from collections import Counter
from dataclasses import dataclass

from core.matching import (
    CriterionImportance,
    EvidenceSignificance,
    EvidenceType,
    MatchStatus,
    EVIDENCE_CATEGORY,
)


def _subject(item):
    criterion = " ".join((item.criterion or "").split()).strip(" .")
    if criterion and len(criterion) <= 40:
        return criterion
    return item.evidence_type.value.replace("_", " ")


def feed_card_presentation(match):
    """Keep discovery, current fit, and a possible blocker visibly distinct."""
    kinds = {reason.kind for reason in match.get("discovery_provenance") or ()}
    if "exact_desired_career" in kinds:
        discovery_label = "Target career"
    elif "trusted_specialisation" in kinds:
        discovery_label = "Target-career specialisation"
    elif "trusted_related_occupation" in kinds:
        discovery_label = "Related career"
    else:
        discovery_label = "Profile match"
    fit_label = {
        "A": "Strong Match", "B": "Good Match", "C": "Relevant",
        "E": "Current Gap", "D": "Limited Current Fit", "F": "Limited Current Fit",
    }[match["recommendation_band"]]
    warning = next((
        f"Major required gap: {_subject(item)}"
        for item in match.get("required_results") or ()
        if item.significance == EvidenceSignificance.CRUCIAL
        and item.status == MatchStatus.NOT_FOUND_ON_PROFILE
    ), "")
    return {
        "discovery_label": discovery_label,
        "fit_label": fit_label,
        "major_warning": warning,
    }


def card_match_reason(match):
    """One card sentence, using only existing criterion and discovery facts."""
    criteria = tuple(match.get("criteria") or ())
    substantive = [
        item for item in criteria
        if item.importance != CriterionImportance.PREFERENCE
        and item.evidence_type in EVIDENCE_CATEGORY
    ]
    found = [item for item in substantive if item.status == MatchStatus.FOUND]
    related = [item for item in substantive if item.status == MatchStatus.RELATED]
    supported = found or related
    supported.sort(key=lambda item: (
        item.importance != CriterionImportance.REQUIRED,
        item.significance == EvidenceSignificance.SUPPORTING,
    ))
    subjects = []
    for item in supported:
        subject = _subject(item)
        if subject not in subjects:
            subjects.append(subject)
        if len(subjects) == 2:
            break
    subject_text = " and ".join(subjects)
    if len(subject_text) > 55:
        subject_text = subjects[0]

    major_gaps = [
        item for item in substantive
        if item.importance == CriterionImportance.REQUIRED
        and item.significance == EvidenceSignificance.CRUCIAL
        and item.status == MatchStatus.NOT_FOUND_ON_PROFILE
    ]
    if major_gaps:
        gap = (
            f"{_subject(major_gaps[0])} gap"
            if len(major_gaps) == 1 else f"{len(major_gaps)} gaps"
        )
        body = (
            "Relevant profile evidence, but a major required " + gap + " remains."
            if supported else "A major required " + gap + " remains."
        )
    elif found:
        body = f"Your profile has evidence supporting {subject_text} for this role."
    elif related:
        body = f"Your profile has related evidence for {subject_text}."
    elif any(item.status == MatchStatus.UNKNOWN for item in substantive):
        body = "Some role requirements cannot be verified from your profile."
    elif substantive:
        body = "Profile evidence for this role is limited."
    else:
        body = "This listing has limited comparable role requirements."

    if match.get("recommendation_band") in {"D", "F"} and match.get("discovery_priority", 0):
        return "This is connected to your career target, but your current profile shows limited evidence for the role."
    return body


@dataclass(frozen=True)
class PresentedCriterion:
    source: object
    status_text: str
    css_status: str
    is_major: bool
    shared_unknown_reason: bool

    @property
    def has_own_detail(self):
        return bool(
            (self.source.explanation and not self.shared_unknown_reason)
            or self.source.profile_evidence
            or len(self.source.criterion) > 110
        )


@dataclass(frozen=True)
class EvidenceSection:
    rows: tuple[PresentedCriterion, ...]
    missing_count: int
    unknown_count: int
    major_count: int
    shared_unknown_explanations: tuple[str, ...]


def _evidence_section(criteria, *, required=False, preference=False):
    explanations = Counter(
        item.explanation for item in criteria
        if item.status == MatchStatus.UNKNOWN and item.explanation
    )
    shared = tuple(text for text, count in explanations.items() if count > 1)
    rows = []
    for item in criteria:
        major = (
            required
            and item.significance == EvidenceSignificance.CRUCIAL
            and item.evidence_type in EVIDENCE_CATEGORY
            and item.status == MatchStatus.NOT_FOUND_ON_PROFILE
        )
        status_text = {
            MatchStatus.FOUND: "Found",
            MatchStatus.RELATED: "Related evidence",
            MatchStatus.NOT_FOUND_ON_PROFILE: "Not found",
            MatchStatus.UNKNOWN: "Not confirmed" if preference else "Unable to verify",
        }[item.status]
        rows.append(PresentedCriterion(
            source=item,
            status_text="Major gap" if major else status_text,
            css_status=item.css_status,
            is_major=major,
            shared_unknown_reason=(
                item.status == MatchStatus.UNKNOWN and item.explanation in shared
            ),
        ))
    return EvidenceSection(
        rows=tuple(rows),
        missing_count=sum(item.status == MatchStatus.NOT_FOUND_ON_PROFILE for item in criteria),
        unknown_count=sum(item.status == MatchStatus.UNKNOWN for item in criteria),
        major_count=sum(row.is_major for row in rows),
        shared_unknown_explanations=shared,
    )


def profile_evidence_presentation(result):
    """Keep the full matcher output; collapse only its default display."""
    required = _evidence_section(result.required_results, required=True)
    preferred = _evidence_section(result.preferred_results)
    preferences = _evidence_section(result.preference_results, preference=True)
    required_count = len(required.rows)
    supported_count = sum(
        row.source.status in {MatchStatus.FOUND, MatchStatus.RELATED}
        for row in required.rows
    )
    if required_count:
        summary = (
            f"CareerGPS found direct or related profile evidence for "
            f"{supported_count} of {required_count} required criteria."
        )
        if required.major_count:
            summary += (
                f" {required.major_count} major required "
                f"gap{'s' if required.major_count != 1 else ''} remain."
            )
    elif preferred.rows:
        summary = "No required criteria were available; preferred evidence is shown below."
    else:
        summary = "This listing has no structured role requirements to compare yet."
    return {
        "summary": summary,
        "required": required,
        "preferred": preferred,
        "preferences": preferences,
    }


@dataclass(frozen=True)
class CvGuidance:
    title: str
    content: str


def cv_guidance(result):
    """Use confirmed matching evidence, never canned candidate achievements."""
    tips = []
    subjects = set()
    for item in result.required_results:
        if (
            item.status != MatchStatus.FOUND
            or not item.profile_evidence
            or item.evidence_type not in {
                EvidenceType.SKILL, EvidenceType.EXPERIENCE,
                EvidenceType.QUALIFICATION, EvidenceType.CERTIFICATION,
            }
        ):
            continue
        subject = _subject(item)
        if subject in subjects:
            continue
        subjects.add(subject)
        if item.evidence_type == EvidenceType.SKILL:
            title = f"Show how you used {subject}"
            advice = (
                "Describe a real task where you used it; include outcomes or numbers "
                "only when you can support them."
            )
        elif item.evidence_type == EvidenceType.EXPERIENCE:
            title = "Make your relevant experience concrete"
            advice = (
                "Explain your actual responsibilities and actions; include outcomes or "
                "numbers only when you can support them."
            )
        else:
            title = "State your credential accurately"
            advice = (
                "Name the credential as awarded. Add coursework, projects or results "
                "only if they are in your real record."
            )
        tips.append(CvGuidance(
            title=title,
            content=f"Your saved profile has evidence for {subject}. {advice}",
        ))
        if len(tips) == 2:
            break
    if tips:
        return tuple(tips)
    return (
        CvGuidance(
            title="Keep every claim supportable",
            content=(
                "Describe what you actually did, how you did it, and the result "
                "only where you have evidence for that result. Do not add guessed numbers."
            ),
        ),
    )
