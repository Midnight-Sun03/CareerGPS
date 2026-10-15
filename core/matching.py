"""Legacy matching compatibility module.

Production candidate, employer, market, and career-intelligence routes no longer
import this module. It remains for historical regression tests and the explicit
legacy-reader rollback window; Block 1 relationships plus feed v2 are the live
occupational authority.
"""

from __future__ import annotations

import datetime
import re
import unicodedata
from contextvars import ContextVar
from dataclasses import dataclass, replace
from enum import Enum
from typing import Iterable

from .models import CVtip, CareerPreference, CareerTargetResolution, Opportunity
from .profile_projection import get_matching_global_skill_ids, get_profile_progress
from .taxonomies.resolver import TaxonomyMatchContext, build_taxonomy_context
from ingestion.ai.payload import source_completeness_for


class MatchStatus(str, Enum):
    FOUND = "FOUND"
    RELATED = "RELATED"
    NOT_FOUND_ON_PROFILE = "NOT_FOUND_ON_PROFILE"
    UNKNOWN = "UNKNOWN"


class EvidenceType(str, Enum):
    SKILL = "skill"
    QUALIFICATION = "qualification"
    NQF = "nqf"
    EXPERIENCE = "experience"
    LOCATION = "location"
    WORK_ARRANGEMENT = "work_arrangement"
    ELIGIBILITY = "eligibility"
    OPPORTUNITY_KIND = "opportunity_kind"
    INDUSTRY = "industry"
    TARGET_ROLE = "target_role"
    CERTIFICATION = "certification"
    LANGUAGE = "language"


class CriterionImportance(str, Enum):
    REQUIRED = "required"
    PREFERRED = "preferred"
    PREFERENCE = "preference"


class EvidenceSignificance(str, Enum):
    """How much one criterion should matter when judging recommendation strength.

    This is deliberately separate from REQUIRED/PREFERRED. A requirement can be
    required by the employer but still be supporting evidence (for example, a
    generic behavioural competency).
    """

    CRUCIAL = "crucial"
    CORE = "core"
    SUPPORTING = "supporting"


class MatchCategory(str, Enum):
    QUALIFICATION = "qualification"
    EXPERIENCE = "experience"
    SKILL = "skill"
    CERTIFICATION = "certification"
    LANGUAGE = "language"
    WORK_ELIGIBILITY = "work_eligibility"


# Phase 2.5 baseline. These values are deliberately centralized so they can be
# calibrated against real CareerGPS profiles/jobs without rewriting the matcher.
STATUS_SCORE = {
    MatchStatus.FOUND: 1.0,
    MatchStatus.RELATED: 0.5,
    MatchStatus.NOT_FOUND_ON_PROFILE: 0.0,
}
CATEGORY_WEIGHT = {
    MatchCategory.QUALIFICATION: 25.0,
    MatchCategory.EXPERIENCE: 25.0,
    MatchCategory.SKILL: 25.0,
    MatchCategory.CERTIFICATION: 10.0,
    MatchCategory.LANGUAGE: 5.0,
    MatchCategory.WORK_ELIGIBILITY: 10.0,
}
SIGNIFICANCE_WEIGHT = {
    EvidenceSignificance.CRUCIAL: 3.0,
    EvidenceSignificance.CORE: 2.0,
    EvidenceSignificance.SUPPORTING: 0.5,
}
REQUIRED_SHARE = 0.80
PREFERRED_SHARE = 0.20

EVIDENCE_CATEGORY = {
    EvidenceType.QUALIFICATION: MatchCategory.QUALIFICATION,
    EvidenceType.NQF: MatchCategory.QUALIFICATION,
    EvidenceType.EXPERIENCE: MatchCategory.EXPERIENCE,
    EvidenceType.SKILL: MatchCategory.SKILL,
    EvidenceType.CERTIFICATION: MatchCategory.CERTIFICATION,
    EvidenceType.LANGUAGE: MatchCategory.LANGUAGE,
    EvidenceType.ELIGIBILITY: MatchCategory.WORK_ELIGIBILITY,
}

# A direct contradiction on these employer-stated required dimensions is a
# major conflict. Individual required skills still matter through the skill
# category, but one missing skill does not automatically override everything.
HARD_REQUIRED_EVIDENCE = {
    EvidenceType.QUALIFICATION,
    EvidenceType.NQF,
    EvidenceType.EXPERIENCE,
    EvidenceType.CERTIFICATION,
    EvidenceType.LANGUAGE,
    EvidenceType.ELIGIBILITY,
}

# Internal ordering for Band E distinguishes gate-like missing requirements from
# foundation gaps. This does not change whether a criterion is a major gap; it
# only helps rank opportunities that are already in the major-gap band.
#
# Gate-like requirements are binary employer gates where absence can block an
# application regardless of otherwise strong fit. Qualification/NQF/experience
# remain major foundation gaps. Required skills intentionally stay outside this
# set: one missing skill is not a hard conflict in CareerGPS.
GATE_GAP_EVIDENCE = {
    EvidenceType.CERTIFICATION,
    EvidenceType.LANGUAGE,
    EvidenceType.ELIGIBILITY,
}
FOUNDATION_GAP_EVIDENCE = {
    EvidenceType.QUALIFICATION,
    EvidenceType.NQF,
    EvidenceType.EXPERIENCE,
}

# Supporting skills are useful, but they must never outweigh hard career evidence.
# The source-section rule is strongest (e.g. Nedbank behavioural competencies).
# The text patterns are deliberately conservative and cover only common generic
# behavioural traits that repeatedly appear in CareerGPS adverts.
_SUPPORTING_SKILL_PATTERNS = (
    r"\bcommunication(?: skills?)?\b",
    r"\bteam(?:work| player)\b",
    r"\bcollaborat(?:e|ing|ion)\b",
    r"\borganis(?:ed|ed|ation|ational)\b",
    r"\bproactive\b",
    r"\bself[- ]?motivated\b",
    r"\battention to detail\b",
    r"\bdetail[- ]oriented\b",
    r"\badapt(?:ability| to change)\b",
    r"\bresilien(?:ce|t)\b",
    r"\btenacity\b",
    r"\btime management\b",
    r"\bwork under pressure\b",
    r"\bperform under pressure\b",
    r"\bstress tolerance\b",
    r"\bpositive attitude\b",
    r"\bflexible attitude\b",
    r"\bgoal orient(?:ated|ed)\b",
    r"\bself managed\b",
    r"\bself[- ]?management\b",
    r"\bcontinuous learning\b",
    r"\bapplied learning\b",
    r"\binitiating action\b",
    r"\bwork standards\b",
    r"\bearning trust\b",
    r"\bbuilding trust(?:ing relationships)?\b",
    r"\bcustomer focus\b",
    r"\bbuilding customer loyalty\b",
    r"\bquality orientation\b",
)


def _looks_supporting_skill(value: str, source_section: str = "") -> bool:
    """Classify skill evidence conservatively, preferring employer section labels.

    A source-labelled technical/professional section is stronger evidence than a
    generic text pattern. For example, ``Communication`` listed by an employer
    under technical/professional knowledge remains CORE, while the same wording
    under behavioural competencies is SUPPORTING. When no trustworthy section
    label exists, the conservative text patterns are used as a fallback.
    """
    section = _normalized_exact(source_section or "")
    if any(token in section for token in ("behavioural", "behavioral", "soft skill")):
        return True
    if any(token in section for token in (
        "technical professional",
        "technical/professional",
        "technical_professional",
        "technical knowledge",
        "professional knowledge",
        "technical competency",
        "technical competencies",
    )):
        return False
    normalized = _normalized_exact(value or "")
    return any(re.search(pattern, normalized) for pattern in _SUPPORTING_SKILL_PATTERNS)


def _partition_skill_values(values: Iterable[str]) -> tuple[tuple[str, ...], tuple[str, ...]]:
    core = []
    supporting = []
    for value in values:
        (supporting if _looks_supporting_skill(value) else core).append(value)
    return _unique_display_values(core), _unique_display_values(supporting)


def _default_significance(
    evidence_type: EvidenceType,
    importance: CriterionImportance,
) -> EvidenceSignificance:
    if evidence_type == EvidenceType.SKILL:
        return EvidenceSignificance.CORE
    if evidence_type in HARD_REQUIRED_EVIDENCE:
        return EvidenceSignificance.CRUCIAL
    return EvidenceSignificance.CORE


STATUS_LABELS = {
    MatchStatus.FOUND: "Found on your profile",
    MatchStatus.RELATED: "Related",
    MatchStatus.NOT_FOUND_ON_PROFILE: "Not found on your profile",
    MatchStatus.UNKNOWN: "Unable to compare safely",
}


@dataclass(frozen=True)
class ExperienceConstraint:
    """One source-preserved experience requirement used at runtime only."""

    key: str
    importance: CriterionImportance
    minimum_years: int | None
    maximum_years: int | None
    text: str
    context: str
    is_context_free_overall: bool
    maximum_is_restrictive: bool = False
    source_field: str = ""

    @property
    def duration_display(self) -> str:
        if self.minimum_years is None:
            return self.text
        if self.maximum_years is not None:
            return f"{self.minimum_years}–{self.maximum_years} years"
        return f"{self.minimum_years} year{'s' if self.minimum_years != 1 else ''}"


@dataclass(frozen=True)
class UserEducationEvidence:
    qualification: str
    field_of_study: str
    nqf_level: int | None

    @property
    def display(self) -> str:
        values = [value for value in (self.qualification, self.field_of_study) if value]
        label = " - ".join(values)
        if self.nqf_level is not None:
            label = f"{label} (NQF {self.nqf_level})" if label else f"NQF {self.nqf_level}"
        return label


@dataclass(frozen=True)
class UserWorkExperienceEvidence:
    title: str
    start_date: datetime.date | None
    end_date: datetime.date | None
    currently_working: bool

    @property
    def display(self) -> str:
        return self.title or "Work experience"


@dataclass(frozen=True)
class UserMatchingProfile:
    """Structured, user-confirmed evidence used during one matching run."""

    profile_id: int
    as_of_date: datetime.date
    skills: tuple[str, ...]
    education: tuple[UserEducationEvidence, ...]
    work_experiences: tuple[UserWorkExperienceEvidence, ...]
    experience_days: int | None
    experience_months: int | None
    locations: tuple[str, ...]
    work_arrangement: str
    target_roles: tuple[str, ...]
    opportunity_kinds: tuple[str, ...]
    industries: tuple[str, ...]
    certifications: tuple[str, ...]
    languages: tuple[str, ...]


@dataclass(frozen=True)
class OpportunityMatchingInput:
    """Canonical opportunity evidence approved for deterministic matching.

    Phase 2.4 keeps deterministic source facts authoritative and supplements
    them with persisted, source-grounded AI semantic-gap criteria. The original
    Opportunity remains the source record and is never overwritten.
    """

    opportunity_id: int
    required_skills: tuple[str, ...]
    preferred_skills: tuple[str, ...]
    required_qualification: str
    preferred_qualification: str
    minimum_nqf: int | None
    required_experience_min_years: int | None
    required_experience_text: str
    preferred_experience_min_years: int | None
    preferred_experience_text: str
    eligibility_text: str
    locations: tuple[str, ...]
    work_arrangement: str
    opportunity_kind: str
    sector: str
    title: str
    experience_constraints: tuple[ExperienceConstraint, ...] = ()
    required_qualifications: tuple[str, ...] = ()
    preferred_qualifications: tuple[str, ...] = ()
    required_certifications: tuple[str, ...] = ()
    preferred_certifications: tuple[str, ...] = ()
    required_languages: tuple[str, ...] = ()
    preferred_languages: tuple[str, ...] = ()
    required_supporting_skills: tuple[str, ...] = ()
    preferred_supporting_skills: tuple[str, ...] = ()
    source_completeness: str = "unknown"
    uses_ai_extraction: bool = False
    requirement_origin: str = "source_parsed"
    preferred_minimum_nqf: int | None = None
    preferred_eligibility_text: str = ""


@dataclass(frozen=True)
class CriterionResult:
    key: str
    label: str
    evidence_type: EvidenceType
    importance: CriterionImportance
    status: MatchStatus
    criterion: str
    profile_evidence: tuple[str, ...]
    explanation: str
    significance: EvidenceSignificance = EvidenceSignificance.CORE
    source_section: str = ""

    @property
    def status_label(self) -> str:
        return STATUS_LABELS[self.status]

    @property
    def css_status(self) -> str:
        return self.status.value.casefold().replace("_", "-")


@dataclass(frozen=True)
class CategoryFit:
    category: MatchCategory
    weight: float
    required_fit: int | None
    preferred_fit: int | None
    required_comparable: int
    preferred_comparable: int


@dataclass(frozen=True)
class MatchSummary:
    score: int
    label: str
    required_fit: int
    preferred_fit: int
    found_count: int
    related_count: int
    not_found_count: int
    unknown_count: int
    comparable_count: int
    total_count: int
    hard_required_conflict: bool
    category_fits: tuple[CategoryFit, ...]
    explanation: str
    evidence_strength: str = "Low"
    important_comparable_count: int = 0
    important_found_count: int = 0
    important_related_count: int = 0
    supporting_comparable_count: int = 0
    supporting_found_count: int = 0
    major_gap_count: int = 0
    important_match_points: float = 0.0
    required_important_match_points: float = 0.0
    preferred_important_match_points: float = 0.0
    important_category_count: int = 0
    matched_important_category_count: int = 0
    required_matched_important_category_count: int = 0
    required_important_coverage: int = 0
    preferred_important_coverage: int = 0
    strongest_important_match_priority: int = 0
    found_important_category_count: int = 0
    required_found_important_category_count: int = 0
    critical_unknown_count: int = 0
    gate_gap_count: int = 0
    foundation_gap_count: int = 0


@dataclass(frozen=True)
class OpportunityMatchResult:
    user: UserMatchingProfile
    opportunity: OpportunityMatchingInput
    criteria: tuple[CriterionResult, ...]
    summary: MatchSummary

    @property
    def required_results(self) -> tuple[CriterionResult, ...]:
        return tuple(
            item for item in self.criteria
            if item.importance == CriterionImportance.REQUIRED
        )

    @property
    def preferred_results(self) -> tuple[CriterionResult, ...]:
        return tuple(
            item for item in self.criteria
            if item.importance == CriterionImportance.PREFERRED
        )

    @property
    def preference_results(self) -> tuple[CriterionResult, ...]:
        return tuple(
            item for item in self.criteria
            if item.importance == CriterionImportance.PREFERENCE
        )


def _normalized_exact_text(text: str) -> str:
    text = unicodedata.normalize("NFKC", text)
    return re.sub(r"\s+", " ", text.strip()).casefold()


_NORMALIZATION_CACHE = ContextVar("matching_normalization_cache", default=None)


def _normalized_exact(value: object) -> str:
    text = str(value or "")
    cache = _NORMALIZATION_CACHE.get()
    if cache is None:
        return _normalized_exact_text(text)
    try:
        return cache[text]
    except KeyError:
        normalized = _normalized_exact_text(text)
        cache[text] = normalized
        return normalized


def _unique_display_values(values: Iterable[object]) -> tuple[str, ...]:
    seen = set()
    result = []
    for value in values:
        display = " ".join(str(value or "").split())
        normalized = _normalized_exact(display)
        if display and normalized not in seen:
            seen.add(normalized)
            result.append(display)
    return tuple(result)


def _collapse_exact_repeated_text(value: str) -> str:
    """Collapse a whole phrase accidentally concatenated two or more times.

    Examples from live source data include ``Diploma Diploma Diploma`` and an
    entire qualification phrase repeated three times. This is deterministic
    cleanup, not semantic interpretation.
    """
    text = " ".join((value or "").split())
    tokens = text.split()
    count = len(tokens)
    if count < 2:
        return text
    for unit_len in range(1, count // 2 + 1):
        if count % unit_len:
            continue
        repetitions = count // unit_len
        if repetitions < 2:
            continue
        unit = tokens[:unit_len]
        if unit * repetitions == tokens:
            # Avoid changing ordinary doubled emphasis such as "very very";
            # single-token repetition is collapsed only when repeated 3+ times.
            if unit_len == 1 and repetitions < 3:
                continue
            return " ".join(unit)
    return text


def _split_profile_values(value: str) -> tuple[str, ...]:
    return _unique_display_values(re.split(r"[,;\n]+", value or ""))


_CONTEXTUAL_EXPERIENCE_CONTEXTS = {
    "alternative",
    "role_domain",
    "similar_role",
    "specialism",
    "subordinate_specialism",
    "subset",
}
_CONTEXT_FREE_EXPERIENCE_PATTERNS = (
    re.compile(
        r"(?:(?:a )?minimum(?: of)?|at least)? ?\d{1,2}(?:\+| ?(?:-|to) ?\d{1,2})? "
        r"years? (?:of )?(?:(?:overall|total) )?(?:(?:work|working|professional) )?"
        r"experience(?: required)?"
    ),
    re.compile(
        r"(?:overall|total) (?:(?:work|working|professional) )?experience "
        r"(?:of )?(?:(?:a )?minimum(?: of)?|at least)? ?\d{1,2}(?:\+)? years?"
    ),
    re.compile(
        r"(?:(?:a )?minimum(?: of)?|at least)? ?\d{1,2}(?:\+)? years? "
        r"overall experience"
    ),
    re.compile(r"no (?:prior )?(?:work )?experience required"),
)


def _normalized_experience_clause(value: object) -> str:
    normalized = _normalized_exact(value)
    normalized = normalized.replace("–", "-").replace("—", "-")
    normalized = re.sub(r"(?<=\d)\s*yrs?\b", " years", normalized)
    normalized = re.sub(r"\s+", " ", normalized).strip(" .;:")
    return normalized


def _is_context_free_overall_experience(
    text: str,
    context: str = "",
    *,
    explicitly_context_free: bool = False,
) -> bool:
    """Recognize only narrow, complete statements of total experience."""
    normalized_context = _normalized_exact(context).replace("-", "_").replace(" ", "_")
    if normalized_context in _CONTEXTUAL_EXPERIENCE_CONTEXTS:
        return False
    if explicitly_context_free:
        return True
    normalized_text = _normalized_experience_clause(text)
    if not normalized_text:
        return normalized_context in {"", "context_free", "general", "overall"}
    return any(pattern.fullmatch(normalized_text) for pattern in _CONTEXT_FREE_EXPERIENCE_PATTERNS)


def _nonnegative_integer(value) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed >= 0 else None


def _runtime_experience_constraints(opportunity) -> tuple[ExperienceConstraint, ...]:
    """Convert supported preserved evidence into source-neutral constraints."""
    constraints = []
    source_specific = opportunity.source_specific_evidence
    if isinstance(source_specific, dict):
        preserved = source_specific.get("experience_constraints")
    else:
        preserved = None

    if isinstance(preserved, list):
        for index, item in enumerate(preserved, start=1):
            if not isinstance(item, dict):
                continue
            requirement = _normalized_exact(item.get("requirement"))
            if requirement not in {
                CriterionImportance.REQUIRED.value,
                CriterionImportance.PREFERRED.value,
            }:
                continue
            importance = CriterionImportance(requirement)
            minimum = _nonnegative_integer(item.get("min"))
            maximum = _nonnegative_integer(item.get("max"))
            text = " ".join(str(item.get("raw") or "").split())
            if minimum is None and not text:
                continue
            context = _normalized_exact(item.get("context")) or "unspecified"
            constraints.append(ExperienceConstraint(
                key=f"{importance.value}:experience:{index}",
                importance=importance,
                minimum_years=minimum,
                maximum_years=maximum,
                text=text,
                context=context,
                is_context_free_overall=_is_context_free_overall_experience(
                    text,
                    context,
                    explicitly_context_free=(
                        item.get("is_context_free_overall") is True
                        or item.get("context_free") is True
                    ),
                ),
                maximum_is_restrictive=item.get("maximum_is_restrictive") is True,
                source_field=" ".join(str(item.get("source_field") or "").split()),
            ))

    present_importance = {item.importance for item in constraints}
    canonical_values = (
        (
            CriterionImportance.REQUIRED,
            opportunity.experience_min_years,
            opportunity.experience_max_years,
            opportunity.experience_text,
        ),
        (
            CriterionImportance.PREFERRED,
            opportunity.preferred_experience_min_years,
            opportunity.preferred_experience_max_years,
            opportunity.preferred_experience_text,
        ),
    )
    for importance, minimum, maximum, text_value in canonical_values:
        if importance in present_importance:
            continue
        text = " ".join((text_value or "").split())
        minimum = _nonnegative_integer(minimum)
        maximum = _nonnegative_integer(maximum)
        if minimum is None and not text:
            continue
        constraints.append(ExperienceConstraint(
            key=f"{importance.value}:experience",
            importance=importance,
            minimum_years=minimum,
            maximum_years=maximum,
            text=text,
            context=(
                "overall"
                if _is_context_free_overall_experience(text) else "unspecified"
            ),
            is_context_free_overall=_is_context_free_overall_experience(text),
            maximum_is_restrictive=False,
            source_field=(
                "experience_text"
                if importance == CriterionImportance.REQUIRED
                else "preferred_experience_text"
            ),
        ))
    return tuple(constraints)


def _merged_experience_days(records, today: datetime.date) -> int | None:
    """Return distinct dated work-experience days without double counting overlaps."""
    intervals = []
    for record in records:
        start = record.start_date
        end = today if record.currently_working else record.end_date
        if not start or not end or start > end or start > today:
            continue
        intervals.append((start, min(end, today)))
    if not intervals:
        return None

    intervals.sort()
    merged = []
    for start, end in intervals:
        if not merged or start > merged[-1][1] + datetime.timedelta(days=1):
            merged.append([start, end])
        else:
            merged[-1][1] = max(merged[-1][1], end)
    return sum((end - start).days + 1 for start, end in merged)


def _confirmed_values(profile, relation, *fields):
    rows = getattr(profile, '_matching_' + relation, None)
    if rows is not None:
        return [tuple(getattr(row, field) for field in fields) for row in rows]
    return getattr(profile, relation).filter(confirmed_by_user=True).values_list(*fields)


def prefetch_matching_evidence(queryset):
    """Share the confirmed evidence loader across candidate and employer batches."""
    from django.db.models import Prefetch
    from .models import ProfileSkill, Education, WorkExperience, Certification, Language
    relations = [('profile_skills', ProfileSkill, ('entered_name',)),
        ('education_records', Education, ('qualification', 'field_of_study', 'nqf_level')),
        ('work_experiences', WorkExperience, ('title', 'organisation', 'start_date', 'end_date', 'currently_working')),
        ('certifications', Certification, ('title',)), ('languages', Language, ('language',))]
    return queryset.select_related('career_preferences').prefetch_related(*[
        Prefetch(name, queryset=model.objects.filter(confirmed_by_user=True).only('profile_id', *fields), to_attr='_matching_' + name)
        for name, model, fields in relations])


def build_user_matching_profile(
    profile,
    *,
    today: datetime.date | None = None,
    include_certifications: bool = False,
    include_languages: bool = False,
    include_preferences: bool = True,
) -> UserMatchingProfile:
    """Load all matching evidence in a fixed number of profile queries."""
    today = today or datetime.date.today()
    skills = _unique_display_values(row[0] for row in _confirmed_values(profile, 'profile_skills', 'entered_name'))
    education = tuple(UserEducationEvidence(*row) for row in
        _confirmed_values(profile, 'education_records', 'qualification', 'field_of_study', 'nqf_level'))
    experience_records = getattr(profile, '_matching_work_experiences', None)
    if experience_records is None:
        experience_records = list(profile.work_experiences.filter(confirmed_by_user=True).only(
            'title', 'start_date', 'end_date', 'currently_working'))
    work_experiences = tuple(
        UserWorkExperienceEvidence(
            title=" ".join((record.title or "").split()),
            start_date=record.start_date,
            end_date=record.end_date,
            currently_working=record.currently_working,
        )
        for record in experience_records
    )
    experience_days = _merged_experience_days(work_experiences, today)
    experience_months = (
        None if experience_days is None else (experience_days * 12) // 365
    )

    try:
        preference = profile.career_preferences if include_preferences else None
    except CareerPreference.DoesNotExist:
        preference = None

    preference_is_confirmed = bool(preference and preference.confirmed_by_user)
    # Trusted discovery already handles mapped occupations. Pass only unresolved
    # free-text roles to the legacy direction fallback, avoiding a second C-band
    # route for the same target without losing legitimate unresolved intent.
    roles = _split_profile_values(preference.target_roles) if preference_is_confirmed else ()
    mapped_labels = {
        _normalized_exact(label) for label in CareerTargetResolution.objects.filter(
            profile=profile, state='RESOLVED',
        ).values_list('authored_text', flat=True)
    } if roles else set()
    preferred_locations = (
        _split_profile_values(preference.preferred_locations)
        if preference_is_confirmed else ()
    )
    locations = _unique_display_values((profile.location, *preferred_locations)) if include_preferences else ()

    certifications = (
        _unique_display_values(
            (row[0] for row in _confirmed_values(profile, 'certifications', 'title'))
        )
        if include_certifications else ()
    )
    languages = (
        _unique_display_values(
            (row[0] for row in _confirmed_values(profile, 'languages', 'language'))
        )
        if include_languages else ()
    )

    return UserMatchingProfile(
        profile_id=profile.pk,
        as_of_date=today,
        skills=skills,
        education=education,
        work_experiences=work_experiences,
        experience_days=experience_days,
        experience_months=experience_months,
        locations=locations,
        work_arrangement=(preference.work_arrangement if preference_is_confirmed else ""),
        target_roles=tuple(role for role in roles if _normalized_exact(role) not in mapped_labels),
        opportunity_kinds=(
            _split_profile_values(preference.opportunity_kinds)
            if preference_is_confirmed else ()
        ),
        industries=(
            _split_profile_values(preference.industries)
            if preference_is_confirmed else ()
        ),
        certifications=certifications,
        languages=languages,
    )


def _ready_ai_criteria(opportunity):
    # Compatibility name; both sources use the exact same criterion evaluation.
    from core.services.requirement_projection import requirement_projection
    projection = requirement_projection(opportunity)
    return (projection, projection.criteria) if projection else (None, ())


def _ai_experience_constraints(criteria) -> tuple[ExperienceConstraint, ...]:
    constraints = []
    for index, criterion in enumerate(
        (item for item in criteria if item.criterion_type == "experience"),
        start=1,
    ):
        level = criterion.requirement_level
        if level not in {"required", "preferred"}:
            continue
        importance = CriterionImportance(level)
        context = " ".join(str((criterion.metadata or {}).get("context") or criterion.raw_value or "").split())
        text = " ".join((criterion.evidence_text or criterion.raw_value or "").split())
        minimum = _nonnegative_integer(criterion.minimum_years)
        maximum = _nonnegative_integer(criterion.maximum_years)
        constraints.append(ExperienceConstraint(
            key=f"{importance.value}:ai-experience:{index}",
            importance=importance,
            minimum_years=minimum,
            maximum_years=maximum,
            text=text,
            context=context or "unspecified",
            is_context_free_overall=_is_context_free_overall_experience(text, context),
            # A maximum with no minimum represents a real cap (for example,
            # "must have no prior work experience"). Ordinary 3-5 year ranges
            # are not treated as disqualifying overqualification.
            maximum_is_restrictive=(minimum is None and maximum is not None),
            source_field=criterion.source_section or "AI-extracted opportunity evidence",
        ))
    return tuple(constraints)



def _merge_experience_constraints(
    base_constraints: tuple[ExperienceConstraint, ...],
    ai_constraints: tuple[ExperienceConstraint, ...],
) -> tuple[ExperienceConstraint, ...]:
    """Merge semantic AI context into code-extracted experience facts.

    Explicit source/code values remain authoritative. Gemini may enrich a
    matching constraint with context or add a genuinely missed requirement, but
    it never silently replaces a code-parsed numeric requirement.
    """
    merged = list(base_constraints)

    def norm(value: str) -> str:
        return _normalized_exact(value or "")

    for ai in ai_constraints:
        match_index = None
        for index, base in enumerate(merged):
            if base.importance != ai.importance:
                continue

            base_text = norm(base.text)
            ai_text = norm(ai.text)
            evidence_overlaps = bool(
                base_text and ai_text
                and (base_text in ai_text or ai_text in base_text)
            )
            same_numbers = (
                base.minimum_years == ai.minimum_years
                and base.maximum_years == ai.maximum_years
                and (base.minimum_years is not None or base.maximum_years is not None)
            )
            same_section = bool(
                base.source_field
                and ai.source_field
                and norm(base.source_field) == norm(ai.source_field)
            )
            if evidence_overlaps or (same_numbers and same_section):
                match_index = index
                break

        if match_index is None:
            merged.append(ai)
            continue

        base = merged[match_index]
        ai_context = norm(ai.context)
        context = (
            ai.context
            if ai_context and ai_context not in {"unspecified", "overall"}
            else base.context
        )
        merged[match_index] = ExperienceConstraint(
            key=base.key,
            importance=base.importance,
            minimum_years=(
                base.minimum_years
                if base.minimum_years is not None else ai.minimum_years
            ),
            maximum_years=(
                base.maximum_years
                if base.maximum_years is not None else ai.maximum_years
            ),
            text=base.text or ai.text,
            context=context or "unspecified",
            is_context_free_overall=(
                base.is_context_free_overall
                if context == base.context
                else _is_context_free_overall_experience(base.text or ai.text, context)
            ),
            maximum_is_restrictive=base.maximum_is_restrictive,
            source_field=base.source_field or ai.source_field,
        )
    return tuple(merged)


def _base_opportunity_matching_input(opportunity) -> OpportunityMatchingInput:
    """Build job facts only from the current validated Clean-Job revision."""
    revision = opportunity.active_clean_revision
    if not revision or revision.status != 'active' or revision.schema_version != '1.1':
        raise ValueError('Opportunity has no active Clean-Job v1 revision.')
    if revision.raw_opportunity_id != revision.source_record.current_raw_id:
        raise ValueError('Opportunity clean revision does not match the current source version.')
    location = revision.location or {}
    if isinstance(location, dict):
        locations = _unique_display_values(
            location.get(key) for key in ('raw', 'city', 'region', 'country'))
    else:
        locations = _unique_display_values((str(location),))
    return OpportunityMatchingInput(
        opportunity_id=opportunity.pk,
        required_skills=(), preferred_skills=(),
        required_qualification='', preferred_qualification='', minimum_nqf=None,
        required_experience_min_years=None, required_experience_text='',
        preferred_experience_min_years=None, preferred_experience_text='',
        eligibility_text='',
        locations=locations,
        work_arrangement=revision.work_arrangement,
        opportunity_kind=revision.opportunity_kind,
        sector='', title=revision.cleaned_title,
        experience_constraints=(),
        required_qualifications=(), preferred_qualifications=(),
        required_supporting_skills=(), preferred_supporting_skills=(),
        source_completeness='source_evidenced',
    )


def build_opportunity_matching_input(opportunity) -> OpportunityMatchingInput:
    """Compatibility projection over Clean-Job v1 only; no raw/legacy fallback."""
    base = _base_opportunity_matching_input(opportunity)
    extraction, ai_criteria = _ready_ai_criteria(opportunity)
    if extraction is None:
        return base

    def values(kind, level=None):
        return _unique_display_values(
            item.raw_value
            for item in ai_criteria
            if item.criterion_type == kind
            and (level is None or item.requirement_level == level)
        )

    def skill_values(level, *, supporting):
        return _unique_display_values(
            item.raw_value
            for item in ai_criteria
            if item.criterion_type == "skill"
            and item.requirement_level == level
            and _looks_supporting_skill(item.raw_value, item.source_section) == supporting
        )

    required_supporting_skills = _unique_display_values((
        *base.required_supporting_skills,
        *skill_values("required", supporting=True),
    ))
    preferred_supporting_skills = _unique_display_values((
        *base.preferred_supporting_skills,
        *skill_values("preferred", supporting=True),
    ))
    required_supporting_norm = {_normalized_exact(value) for value in required_supporting_skills}
    preferred_supporting_norm = {_normalized_exact(value) for value in preferred_supporting_skills}

    required_skills = _unique_display_values(
        value for value in (
            *base.required_skills,
            *skill_values("required", supporting=False),
        )
        if _normalized_exact(value) not in required_supporting_norm
    )
    preferred_skills = _unique_display_values(
        value for value in (
            *base.preferred_skills,
            *skill_values("preferred", supporting=False),
        )
        if _normalized_exact(value) not in preferred_supporting_norm
    )
    required_qualifications = _unique_display_values((
        *base.required_qualifications,
        *values("qualification", "required"),
    ))
    preferred_qualifications = _unique_display_values((
        *base.preferred_qualifications,
        *values("qualification", "preferred"),
    ))
    required_certifications = _unique_display_values((
        *base.required_certifications,
        *values("certification", "required"),
    ))
    preferred_certifications = _unique_display_values((
        *base.preferred_certifications,
        *values("certification", "preferred"),
    ))
    required_languages = _unique_display_values((
        *base.required_languages,
        *values("language", "required"),
    ))
    preferred_languages = _unique_display_values((
        *base.preferred_languages,
        *values("language", "preferred"),
    ))

    # A READY semantic extraction replaces the old generic eligibility blob
    # only for eligibility interpretation. Ordinary competencies from that blob
    # should have become skill/knowledge criteria instead of appearing as one
    # giant UNKNOWN eligibility criterion.
    work_eligibility = values("eligibility", "required")
    eligibility_text = "; ".join(work_eligibility)

    experience_constraints = _merge_experience_constraints(
        base.experience_constraints,
        _ai_experience_constraints(ai_criteria),
    )
    required_experience = next((
        item for item in experience_constraints
        if item.importance == CriterionImportance.REQUIRED
    ), None)
    preferred_experience = next((
        item for item in experience_constraints
        if item.importance == CriterionImportance.PREFERRED
    ), None)

    return OpportunityMatchingInput(
        opportunity_id=base.opportunity_id,
        required_skills=required_skills,
        preferred_skills=preferred_skills,
        required_qualification=(
            required_qualifications[0] if required_qualifications else ""
        ),
        preferred_qualification=(
            preferred_qualifications[0] if preferred_qualifications else ""
        ),
        minimum_nqf=base.minimum_nqf,
        required_experience_min_years=(
            base.required_experience_min_years
            if base.required_experience_min_years is not None
            else (required_experience.minimum_years if required_experience else None)
        ),
        required_experience_text=(
            base.required_experience_text
            or (required_experience.text if required_experience else "")
        ),
        preferred_experience_min_years=(
            base.preferred_experience_min_years
            if base.preferred_experience_min_years is not None
            else (preferred_experience.minimum_years if preferred_experience else None)
        ),
        preferred_experience_text=(
            base.preferred_experience_text
            or (preferred_experience.text if preferred_experience else "")
        ),
        eligibility_text=eligibility_text,
        locations=base.locations,
        work_arrangement=base.work_arrangement,
        opportunity_kind=base.opportunity_kind,
        sector=base.sector,
        title=base.title,
        experience_constraints=experience_constraints,
        required_qualifications=required_qualifications,
        preferred_qualifications=preferred_qualifications,
        required_certifications=required_certifications,
        preferred_certifications=preferred_certifications,
        required_languages=required_languages,
        preferred_languages=preferred_languages,
        required_supporting_skills=required_supporting_skills,
        preferred_supporting_skills=preferred_supporting_skills,
        source_completeness=extraction.source_completeness,
        uses_ai_extraction=extraction.origin == "ai_extracted",
        requirement_origin=extraction.origin,
        preferred_minimum_nqf=max((item.metadata['minimum_nqf'] for item in ai_criteria
            if item.criterion_type == 'qualification' and item.requirement_level == 'preferred'
            and isinstance(item.metadata.get('minimum_nqf'), int)), default=None),
        preferred_eligibility_text="; ".join(values("eligibility", "preferred")),
    )

def _result(
    *,
    key: str,
    label: str,
    evidence_type: EvidenceType,
    importance: CriterionImportance,
    status: MatchStatus,
    criterion: str,
    profile_evidence: Iterable[str] = (),
    explanation: str,
    significance: EvidenceSignificance | None = None,
    source_section: str = "",
) -> CriterionResult:
    return CriterionResult(
        key=key,
        label=label,
        evidence_type=evidence_type,
        importance=importance,
        status=status,
        criterion=criterion,
        profile_evidence=tuple(profile_evidence),
        explanation=explanation,
        significance=(
            significance
            if significance is not None
            else _default_significance(evidence_type, importance)
        ),
        source_section=source_section,
    )


_SKILL_CONCEPT_KINDS = ("skill", "skill_group")
_OCCUPATION_CONCEPT_KINDS = ("occupation", "occupation_group")


def _trusted_same_concept(context, left_term, right_term, kinds):
    if context is None:
        return None
    left = context.resolve_identity(left_term, kinds)
    right = context.resolve_identity(right_term, kinds)
    same = context.same_concept(left, right)
    return same[0] if same else None


def _trusted_narrower_support(context, evidence_term, criterion_term, kinds):
    if context is None:
        return None
    evidence = context.resolve(evidence_term, kinds)
    criterion = context.resolve(criterion_term, kinds)
    return context.narrower_supports_broader(evidence, criterion, max_depth=1)


def _source_name(concept) -> str:
    return {"esco": "ESCO", "ofo": "OFO"}.get(
        getattr(concept, "source", ""), str(getattr(concept, "source", "")).upper()
    )


def _skill_results(
    user,
    names,
    importance,
    taxonomy_context=None,
    *,
    significance=EvidenceSignificance.CORE,
):
    profile_skills = {_normalized_exact(skill): skill for skill in user.skills}
    results = []
    for name in names:
        normalized = _normalized_exact(name)
        alternatives = [part.strip() for part in re.split(r"\s+(?:or|and/or)\s+", name, flags=re.IGNORECASE) if part.strip()]
        if len(alternatives) > 1:
            options = _skill_results(user, alternatives, importance, taxonomy_context, significance=significance)
            priority = {MatchStatus.FOUND: 3, MatchStatus.RELATED: 2, MatchStatus.UNKNOWN: 1, MatchStatus.NOT_FOUND_ON_PROFILE: 0}
            best = max(options, key=lambda item: priority[item.status])
            results.append(replace(best, key=f"{importance.value}:skill:{normalized}", criterion=name,
                explanation=(f"Alternative {best.criterion}: {best.explanation}" if best.status in {MatchStatus.FOUND, MatchStatus.RELATED}
                    else "No supporting evidence for the stated skill alternatives was found in your Skills section.")))
            continue
        exact = profile_skills.get(normalized)
        status = MatchStatus.NOT_FOUND_ON_PROFILE
        evidence = ()
        explanation = f"{name} was not found in your Skills section."

        if exact:
            status = MatchStatus.FOUND
            evidence = (exact,)
            explanation = f"{name} is listed in your Skills section."
        elif taxonomy_context is not None:
            # Canonical identity is stronger than a semantic relationship. An
            # official preferred/alternative label for the same trusted concept
            # counts as the same skill even when the wording differs.
            identity = None
            supporting = None
            supporting_profile_skill = None
            for profile_skill in user.skills:
                identity = _trusted_same_concept(
                    taxonomy_context,
                    profile_skill,
                    name,
                    _SKILL_CONCEPT_KINDS,
                )
                if identity:
                    supporting_profile_skill = profile_skill
                    break
                relation = _trusted_narrower_support(
                    taxonomy_context,
                    profile_skill,
                    name,
                    _SKILL_CONCEPT_KINDS,
                )
                if relation and supporting is None:
                    supporting = relation
                    supporting_profile_skill = profile_skill

            if identity:
                status = MatchStatus.FOUND
                evidence = (supporting_profile_skill,)
                explanation = (
                    f"{supporting_profile_skill} and {name} are labels for the same "
                    f"{_source_name(identity)} concept."
                )
            elif supporting:
                narrower, broader = supporting
                status = MatchStatus.RELATED
                evidence = (supporting_profile_skill,)
                explanation = (
                    f"{supporting_profile_skill} is a more specific trusted concept "
                    f"that supports the broader {name} criterion in {_source_name(narrower)}. "
                    "CareerGPS treats this as related evidence, not direct proof."
                )

        results.append(_result(
            key=f"{importance.value}:skill:{normalized}",
            label="Skill",
            evidence_type=EvidenceType.SKILL,
            importance=importance,
            status=status,
            criterion=name,
            profile_evidence=evidence,
            explanation=explanation,
            significance=significance,
        ))
    return results


_QUALIFICATION_LEVEL_PATTERNS = (
    (10, (r"\bdoctorate\b", r"\bdoctoral\b", r"\bph\.?d\.?\b")),
    (9, (r"\bmaster'?s?\b", r"\bmsc\b", r"\bmcom\b", r"\bmeng\b")),
    (8, (r"\bhonou?rs\b", r"\bpostgraduate diploma\b")),
    (7, (
        r"\bbachelor'?s?\b", r"\bundergraduate degree\b", r"\bfirst degree\b",
        r"\bnational 1st degree\b", r"\bbsc\b", r"\bbcom\b", r"\bbeng\b",
        r"\bbtech\b", r"\bba\b", r"\bllb\b",
        r"\badvanced diploma\b",
    )),
    (6, ()),
    (4, (
        r"\bmatric\b",
        r"\bgrade\s*(?:10|11|12)(?:\s*[-–]\s*12)?\b",
        r"\bnational senior certificate\b",
        r"\bnsc\b",
    )),
)

_QUALIFICATION_NOISE = {
    "a", "an", "and", "any", "applicable", "certificate", "completed", "degree",
    "diploma", "discipline", "equivalent", "field", "formal", "grade", "in",
    "level", "minimum", "national", "nqf", "of", "or", "other",
    "qualification", "qualifications", "related", "relevant", "required",
    "school", "senior", "the", "undergraduate", "year", "years",
}
_GENERIC_SUBJECT_WORDS = {
    "engineering", "management", "science", "studies", "technology"
}
_QUALIFICATION_AMBIGUITY = re.compile(
    r"\b(applicable|appropriate|equivalent|related|relevant|similar)\b"
)


def _qualification_levels(value: str) -> tuple[int, ...]:
    normalized = _normalized_exact(value).replace("’", "'")
    levels = []
    remainder = normalized
    for level, patterns in _QUALIFICATION_LEVEL_PATTERNS:
        if any(re.search(pattern, normalized) for pattern in patterns):
            levels.append(level)
        for pattern in patterns:
            remainder = re.sub(pattern, " ", remainder)
    if re.search(r"\bdegree\b", remainder) and not any(level >= 7 for level in levels):
        levels.append(7)
    if re.search(r"\bdiploma\b", remainder) and not any(level >= 6 for level in levels):
        levels.append(6)
    return tuple(dict.fromkeys(levels))


def _qualification_subject_tokens(value: str) -> set[str]:
    normalized = _normalized_exact(value).replace("’", "'")
    for _, patterns in _QUALIFICATION_LEVEL_PATTERNS:
        for pattern in patterns:
            normalized = re.sub(pattern, " ", normalized)
    words = re.findall(r"[a-z0-9+#.]+", normalized)
    return {
        word
        for word in words
        if len(word) > 1
        and not word.isdigit()
        and word not in _QUALIFICATION_NOISE
    }


def _qualification_matches(
    requirement: str, education: UserEducationEvidence
) -> bool:
    required = _normalized_exact(requirement)
    candidate_values = _unique_display_values((
        education.qualification,
        f"{education.qualification} {education.field_of_study}",
    ))
    candidate_normalized = [_normalized_exact(value) for value in candidate_values]
    if any(
        required == candidate
        or (
            len(required) >= 3
            and re.search(rf"(?<!\w){re.escape(required)}(?!\w)", candidate)
        )
        for candidate in candidate_normalized
    ):
        return True

    required_levels = _qualification_levels(requirement)
    candidate_levels = _qualification_levels(education.qualification)
    alternatives_are_explicit = bool(re.search(r"\bor\b|/", required))
    required_level = (
        min(required_levels) if alternatives_are_explicit else max(required_levels)
    ) if required_levels else None
    if not required_level or not candidate_levels or max(candidate_levels) < required_level:
        return False

    required_subjects = _qualification_subject_tokens(requirement)
    candidate_subjects = _qualification_subject_tokens(
        f"{education.qualification} {education.field_of_study}"
    )
    if not required_subjects:
        return True
    if not candidate_subjects:
        return False

    required_specific = required_subjects - _GENERIC_SUBJECT_WORDS
    candidate_specific = candidate_subjects - _GENERIC_SUBJECT_WORDS
    if not required_specific:
        return bool(required_subjects & candidate_subjects)
    return bool(required_specific & candidate_specific) and (
        required_specific <= candidate_subjects
        or candidate_specific <= required_subjects
    )


def _qualification_result(user, requirement, importance, key_suffix=""):
    if not requirement:
        return None
    matching = [
        item.display
        for item in user.education
        if _qualification_matches(requirement, item)
    ]
    if matching:
        return _result(
            key=f"{importance.value}:qualification{key_suffix}",
            label="Qualification",
            evidence_type=EvidenceType.QUALIFICATION,
            importance=importance,
            status=MatchStatus.FOUND,
            criterion=requirement,
            profile_evidence=matching,
            explanation=(
                "A safely comparable qualification is recorded in your Education section."
            ),
        )
    if user.education and _QUALIFICATION_AMBIGUITY.search(
        _normalized_exact(requirement)
    ):
        return _result(
            key=f"{importance.value}:qualification{key_suffix}",
            label="Qualification",
            evidence_type=EvidenceType.QUALIFICATION,
            importance=importance,
            status=MatchStatus.UNKNOWN,
            criterion=requirement,
            explanation=(
                "This qualification uses relationship wording that Phase 2.2 cannot "
                "evaluate without guessing. Please review it yourself."
            ),
        )
    return _result(
        key=f"{importance.value}:qualification{key_suffix}",
        label="Qualification",
        evidence_type=EvidenceType.QUALIFICATION,
        importance=importance,
        status=MatchStatus.NOT_FOUND_ON_PROFILE,
        criterion=requirement,
        explanation=(
            "No safely comparable qualification was found in your Education section."
        ),
    )


_CERTIFICATION_NOISE = {"certificate", "certification", "certified", "licence", "license"}


def _normalized_certification(value: str) -> str:
    words = [
        word for word in re.findall(r"[a-z0-9+#.]+", _normalized_exact(value))
        if word not in _CERTIFICATION_NOISE
    ]
    return " ".join(words)


def _certification_results(user, requirements, importance):
    results = []
    candidate_map = {
        _normalized_certification(value): value
        for value in user.certifications
        if _normalized_certification(value)
    }
    for index, requirement in enumerate(requirements, start=1):
        required = _normalized_certification(requirement)
        evidence = candidate_map.get(required)
        if not evidence:
            alternatives = re.split(r"\s+(?:or|and/or)\s+", requirement, flags=re.IGNORECASE)
            evidence = next((candidate_map[_normalized_certification(part)] for part in alternatives
                if _normalized_certification(part) in candidate_map), None)
        results.append(_result(
            key=f"{importance.value}:certification:{index}",
            label="Certification",
            evidence_type=EvidenceType.CERTIFICATION,
            importance=importance,
            status=(MatchStatus.FOUND if evidence else MatchStatus.NOT_FOUND_ON_PROFILE),
            criterion=requirement,
            profile_evidence=((evidence,) if evidence else ()),
            explanation=(
                "This certification is recorded in your Certifications section."
                if evidence else
                "This certification was not found in your Certifications section."
            ),
        ))
    return results


def _language_results(user, requirements, importance):
    results = []
    candidate_map = {_normalized_exact(value): value for value in user.languages}
    candidate_names = set(candidate_map)

    for index, requirement in enumerate(requirements, start=1):
        normalized = _normalized_exact(requirement)
        if re.search(r"\band/or\b|/|\bor\b", normalized):
            parts = [
                part.strip()
                for part in re.split(r"\s*(?:/|\band/or\b|\bor\b)\s*", normalized)
                if part.strip()
            ]
            matched_parts = [part for part in parts if part in candidate_names]
            found = bool(parts and matched_parts)
            evidence = tuple(candidate_map[part] for part in matched_parts)
            explanation = (
                "At least one stated language alternative is recorded in your Languages section."
                if found else
                "None of the stated language alternatives were found in your Languages section."
            )
        elif " and " in normalized:
            parts = [part.strip() for part in normalized.split(" and ") if part.strip()]
            found = bool(parts) and all(part in candidate_names for part in parts)
            evidence = tuple(
                candidate_map[part] for part in parts if part in candidate_names
            )
            explanation = (
                "All stated required languages are recorded in your Languages section."
                if found else
                "Not all stated required languages were found in your Languages section."
            )
        else:
            evidence_value = candidate_map.get(normalized)
            found = bool(evidence_value)
            evidence = ((evidence_value,) if evidence_value else ())
            explanation = (
                "This language is recorded in your Languages section."
                if found else
                "This language was not found in your Languages section."
            )

        results.append(_result(
            key=f"{importance.value}:language:{index}",
            label="Language",
            evidence_type=EvidenceType.LANGUAGE,
            importance=importance,
            status=(MatchStatus.FOUND if found else MatchStatus.NOT_FOUND_ON_PROFILE),
            criterion=requirement,
            profile_evidence=evidence,
            explanation=explanation,
        ))
    return results


def _nqf_result(user, minimum_nqf, importance=CriterionImportance.REQUIRED):
    if minimum_nqf is None:
        return None
    if not 1 <= minimum_nqf <= 10:
        return _result(
            key=f"{importance.value.lower()}:nqf",
            label="NQF level",
            evidence_type=EvidenceType.NQF,
            importance=importance,
            status=MatchStatus.UNKNOWN,
            criterion=f"NQF {minimum_nqf}",
            explanation=(
                "The opportunity's NQF value is outside the range CareerGPS can compare safely."
            ),
        )
    profile_levels = [
        item.nqf_level for item in user.education if item.nqf_level is not None
    ]
    found = bool(profile_levels and max(profile_levels) >= minimum_nqf)
    return _result(
        key=f"{importance.value.lower()}:nqf",
        label="Minimum NQF",
        evidence_type=EvidenceType.NQF,
        importance=importance,
        status=(MatchStatus.FOUND if found else MatchStatus.NOT_FOUND_ON_PROFILE),
        criterion=f"NQF {minimum_nqf}",
        profile_evidence=tuple(
            f"NQF {value}" for value in sorted(set(profile_levels), reverse=True)
        ),
        explanation=(
            f"Your Education section records NQF {max(profile_levels)}, meeting this minimum."
            if found else
            f"NQF {minimum_nqf} or higher was not found in your Education section."
        ),
    )


def _experience_display(months: int | None) -> str:
    if months is None:
        return ""
    years, remaining_months = divmod(months, 12)
    parts = []
    if years:
        parts.append(f"{years} year{'s' if years != 1 else ''}")
    if remaining_months or not parts:
        parts.append(
            f"{remaining_months} month{'s' if remaining_months != 1 else ''}"
        )
    return " ".join(parts)


def _experience_constraint_result(user, constraint: ExperienceConstraint):
    """Compare one structured duration constraint without inferring relevance."""
    label = (
        "Minimum experience"
        if constraint.importance == CriterionImportance.REQUIRED
        else "Preferred experience"
    )
    criterion = constraint.text or constraint.duration_display
    minimum = constraint.minimum_years

    # Explicit maximum-only clauses are safely comparable. This is especially
    # important for internships/learnerships that require no prior experience.
    if (
        minimum is None
        and constraint.maximum_is_restrictive
        and constraint.maximum_years is not None
    ):
        maximum_months = constraint.maximum_years * 12
        profile_months = user.experience_months or 0
        found = profile_months <= maximum_months
        evidence = (
            (_experience_display(user.experience_months),)
            if user.experience_months is not None
            else ("No dated work experience recorded",)
        )
        return _result(
            key=constraint.key,
            label=label,
            evidence_type=EvidenceType.EXPERIENCE,
            importance=constraint.importance,
            status=(MatchStatus.FOUND if found else MatchStatus.NOT_FOUND_ON_PROFILE),
            criterion=criterion,
            profile_evidence=evidence,
            explanation=(
                "Your dated Work Experience records are within the employer's stated maximum."
                if found else
                "Your dated Work Experience records exceed the employer's stated maximum."
            ),
        )

    if minimum is None:
        return _result(
            key=constraint.key,
            label=label,
            evidence_type=EvidenceType.EXPERIENCE,
            importance=constraint.importance,
            status=MatchStatus.UNKNOWN,
            criterion=criterion,
            explanation=(
                "CareerGPS preserved this experience wording, but no structured minimum "
                "duration is available for a safe comparison."
            ),
        )

    if minimum == 0:
        if constraint.is_context_free_overall:
            return _result(
                key=constraint.key,
                label=label,
                evidence_type=EvidenceType.EXPERIENCE,
                importance=constraint.importance,
                status=MatchStatus.FOUND,
                criterion=criterion or "No prior experience required",
                explanation="The opportunity states that no prior experience is required.",
            )
        return _result(
            key=constraint.key,
            label=label,
            evidence_type=EvidenceType.EXPERIENCE,
            importance=constraint.importance,
            status=MatchStatus.UNKNOWN,
            criterion=criterion,
            explanation=(
                "The duration threshold is zero, but this clause also contains experience "
                "context that CareerGPS cannot safely verify without inferring relevance."
            ),
        )

    profile_months = user.experience_months
    evidence = (
        (_experience_display(profile_months),) if profile_months is not None else ()
    )
    minimum_months = minimum * 12

    if profile_months is None or profile_months < minimum_months:
        return _result(
            key=constraint.key,
            label=label,
            evidence_type=EvidenceType.EXPERIENCE,
            importance=constraint.importance,
            status=MatchStatus.NOT_FOUND_ON_PROFILE,
            criterion=criterion,
            profile_evidence=evidence,
            explanation=(
                f"CareerGPS could not confirm at least {minimum} "
                f"year{'s' if minimum != 1 else ''} from dated Work Experience records "
                "on your profile."
            ),
        )

    if (
        constraint.maximum_is_restrictive
        and constraint.maximum_years is not None
        and profile_months > constraint.maximum_years * 12
    ):
        maximum = constraint.maximum_years
        return _result(
            key=constraint.key,
            label=label,
            evidence_type=EvidenceType.EXPERIENCE,
            importance=constraint.importance,
            status=MatchStatus.NOT_FOUND_ON_PROFILE,
            criterion=criterion,
            profile_evidence=evidence,
            explanation=(
                f"Your dated Work Experience records show {_experience_display(profile_months)}, "
                f"which is above the explicitly restrictive maximum of {maximum} "
                f"year{'s' if maximum != 1 else ''}."
            ),
        )

    if constraint.is_context_free_overall:
        return _result(
            key=constraint.key,
            label=label,
            evidence_type=EvidenceType.EXPERIENCE,
            importance=constraint.importance,
            status=MatchStatus.FOUND,
            criterion=criterion,
            profile_evidence=evidence,
            explanation=(
                f"Your dated Work Experience records show {_experience_display(profile_months)}, "
                "meeting this overall duration requirement."
            ),
        )

    return _result(
        key=constraint.key,
        label=label,
        evidence_type=EvidenceType.EXPERIENCE,
        importance=constraint.importance,
        status=MatchStatus.UNKNOWN,
        criterion=criterion,
        profile_evidence=evidence,
        explanation=(
            f"Your dated Work Experience records show {_experience_display(profile_months)}, "
            "which meets the stated duration. CareerGPS cannot safely verify the required "
            "role, discipline, industry, responsibility, specialism, or similar context "
            "without inferring relevance."
        ),
    )


def _clean_experience_target(value: str) -> str:
    text = " ".join((value or "").split()).strip(" ,.;:-")
    text = re.sub(r"^(?:a|an|the)\s+", "", text, flags=re.IGNORECASE)
    text = re.sub(
        r"\s+(?:including|with responsibility for|with exposure to|supporting|dealing with|"
        r"ideally\b|proven\b|basic\b|of which\b).*$",
        "",
        text,
        flags=re.IGNORECASE,
    ).strip(" ,.;:-")
    # Long clauses are too ambiguous for deterministic taxonomy lookup. Keep the
    # explicit source text in the UI, but do not fabricate a semantic target.
    if not text or len(text.split()) > 10 or len(text) > 100:
        return ""
    return text


def _split_explicit_role_list(value: str) -> tuple[str, ...]:
    parts = re.split(r"\s*,\s*|\s+or\s+", value, flags=re.IGNORECASE)
    return _unique_display_values(
        _clean_experience_target(part) for part in parts if _clean_experience_target(part)
    )


def _structured_experience_context_term(context: object) -> str:
    """Normalize one explicit structured `experience.context` value.

    Read-only helper for `_experience_context_terms`: sentinel/generic values
    (`unspecified`, `overall`, `general`, `context_free`, `role_domain`,
    `alternative`, `specialism`, `subordinate_specialism`, `subset`, and the
    empty value) never become taxonomy targets. Clean-job phrasings such as
    "Business Analyst experience" collapse conservatively to the occupational
    phrase so the existing trusted OFO/ESCO resolver can attempt a lookup.
    """
    text = " ".join(str(context or "").split())
    if not text:
        return ""
    sentinel = _normalized_exact(text).replace("-", "_").replace(" ", "_")
    if sentinel in _CONTEXTUAL_EXPERIENCE_CONTEXTS | {
        "", "unspecified", "overall", "general", "context_free",
    }:
        return ""
    collapsed = re.sub(
        r"\s+experience\s*$", "", text, flags=re.IGNORECASE,
    ).strip(" ,.;:-")
    return _clean_experience_target(collapsed)


def _experience_context_terms(
    constraint: ExperienceConstraint,
    opportunity: OpportunityMatchingInput,
) -> tuple[str, ...]:
    """Extract only explicit context phrases from an experience criterion.

    This is intentionally narrow. It does not read advert descriptions and it
    does not infer missing meaning. If an explicit target phrase cannot be
    isolated safely, Phase 2.2's UNKNOWN result is preserved.
    """
    if constraint.is_context_free_overall:
        return ()
    if _normalized_exact(constraint.context).replace(" ", "_") == "similar_role":
        return (opportunity.title,) if opportunity.title else ()

    # Structured explicit context wins over raw-text parsing: the clean-job
    # contract's `experience.context` is trusted source data, while raw text
    # must still prove an explicit phrase through the narrow patterns below.
    structured = _structured_experience_context_term(constraint.context)
    if structured:
        return (structured,)

    text = " ".join((constraint.text or "").split())
    if not text:
        return ()

    candidates = []
    # Explicit occupational wording: "experience as Business Analyst" or
    # "3 years as a strategic people partner".
    for pattern in (
        r"\bexperience\s+(?:in\s+)?as\s+(?:a|an)?\s*([^.;]+)",
        r"\byears?['’]?\s+as\s+(?:a|an)?\s*([^.;]+)",
        r"\byrs?\.?\s+as\s+(?:a|an)?\s*([^.;]+)",
    ):
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            candidates.extend(_split_explicit_role_list(match.group(1)))
            break

    # Explicit domain wording: "experience in retail credit" / "years in
    # multimedia". Stop at punctuation; a long unresolved clause simply
    # abstains rather than being guessed into a concept.
    for pattern in (
        r"\bexperience\s+in\s+([^.;]+)",
        r"\byears?['’]?\s+in\s+([^.;]+)",
        r"\byrs?\.?\s+in\s+([^.;]+)",
    ):
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            phrase = _clean_experience_target(match.group(1))
            if phrase:
                candidates.append(phrase)
            break

    # Common source form: "5+ years marketing experience". Capture only the
    # short phrase immediately before the word experience.
    duration_context = re.search(
        r"\b\d{1,2}(?:\s*(?:-|to)\s*\d{1,2})?\+?\s*(?:years?|yrs?)['’]?\s+"
        r"(?:of\s+)?(?:proven\s+)?([A-Za-z][A-Za-z0-9 &/+.#-]{1,80}?)\s+experience\b",
        text,
        flags=re.IGNORECASE,
    )
    if duration_context:
        phrase = _clean_experience_target(duration_context.group(1))
        if phrase:
            candidates.append(phrase)

    return _unique_display_values(candidates)


def _role_specific_experience_months(user, supporting_titles: set[str]) -> int | None:
    records = [
        record
        for record in user.work_experiences
        if _normalized_exact(record.title) in supporting_titles
    ]
    days = _merged_experience_days(records, user.as_of_date)
    return None if days is None else (days * 12) // 365


def _taxonomy_experience_result(
    user,
    opportunity,
    constraint: ExperienceConstraint,
    base_result: CriterionResult,
    taxonomy_context: TaxonomyMatchContext | None,
):
    # Phase 2.3 is monotonic: it cannot rescue a hard duration failure or alter
    # a context-free FOUND result.
    if (
        taxonomy_context is None
        or base_result.status != MatchStatus.UNKNOWN
        or constraint.minimum_years is None
        or constraint.minimum_years <= 0
    ):
        return base_result

    targets = _experience_context_terms(constraint, opportunity)
    if not targets or not user.work_experiences:
        return base_result

    supporting_titles = set()
    support_notes = []
    for record in user.work_experiences:
        if not record.title:
            continue
        record_concepts = taxonomy_context.resolve(
            record.title, _OCCUPATION_CONCEPT_KINDS
        )
        if not record_concepts:
            continue
        for target in targets:
            target_concepts = taxonomy_context.resolve(
                target, _OCCUPATION_CONCEPT_KINDS
            )
            if not target_concepts:
                continue
            same = taxonomy_context.same_concept(record_concepts, target_concepts)
            relation = taxonomy_context.narrower_supports_broader(
                record_concepts, target_concepts, max_depth=1
            )
            if same:
                supporting_titles.add(_normalized_exact(record.title))
                support_notes.append((record.title, target, same[0]))
                break
            if relation:
                supporting_titles.add(_normalized_exact(record.title))
                support_notes.append((record.title, target, relation[0]))
                break

    if not supporting_titles:
        return base_result

    related_months = _role_specific_experience_months(user, supporting_titles)
    minimum_months = constraint.minimum_years * 12
    # Finding some related employment does not prove unrelated/unmapped records
    # are irrelevant. If the related subset is too short, retain UNKNOWN rather
    # than introducing a new false negative.
    if related_months is None or related_months < minimum_months:
        return base_result

    evidence_titles = _unique_display_values(
        title for title, _, _ in support_notes
    )
    source = _source_name(support_notes[0][2]) if support_notes else "taxonomy"
    return _result(
        key=base_result.key,
        label=base_result.label,
        evidence_type=EvidenceType.EXPERIENCE,
        importance=base_result.importance,
        status=MatchStatus.RELATED,
        criterion=base_result.criterion,
        profile_evidence=(
            *evidence_titles,
            _experience_display(related_months),
        ),
        explanation=(
            f"Your dated Work Experience includes {_experience_display(related_months)} "
            f"in role evidence that {source} maps to this stated role context. "
            "CareerGPS treats this as related support, not direct proof that every "
            "responsibility or specialism in the requirement was performed."
        ),
    )


def _experience_constraint_results(
    user,
    opportunity,
    constraints,
    taxonomy_context=None,
):
    results = []
    for constraint in constraints:
        base = _experience_constraint_result(user, constraint)
        results.append(_taxonomy_experience_result(
            user,
            opportunity,
            constraint,
            base,
            taxonomy_context,
        ))
    return results


def _location_parts(values: Iterable[str]) -> set[str]:
    parts = set()
    for value in values:
        normalized = _normalized_exact(value)
        if normalized:
            parts.add(normalized)
        for part in re.split(r"[,;/|]+", normalized):
            part = part.strip()
            if part:
                parts.add(part)
    return parts


def _location_result(user, opportunity):
    if not opportunity.locations:
        return None
    user_locations = _location_parts(user.locations)
    opportunity_locations = _location_parts(opportunity.locations)
    found_values = user_locations & opportunity_locations
    if (
        not found_values
        and "nationwide" in opportunity_locations
        and user_locations
    ):
        found_values = {"nationwide"}
    found = bool(found_values)
    return _result(
        key="preference:location",
        label="Location",
        evidence_type=EvidenceType.LOCATION,
        importance=CriterionImportance.PREFERENCE,
        status=(MatchStatus.FOUND if found else MatchStatus.NOT_FOUND_ON_PROFILE),
        criterion=" / ".join(opportunity.locations),
        profile_evidence=user.locations if found else (),
        explanation=(
            "The opportunity location exactly matches a location recorded in your profile or preferences."
            if found else
            "This opportunity's location was not found in your profile or location preferences."
        ),
    )


_ARRANGEMENT_ALIASES = {
    "on site": "onsite",
    "on-site": "onsite",
    "onsite": "onsite",
    "hybrid": "hybrid",
    "remote": "remote",
    "flexible": "flexible",
}


def _work_arrangement_result(user, opportunity):
    # No saved preference means there is nothing to rank against. Treating a
    # blank preference as NOT_FOUND would incorrectly penalize users who are
    # flexible or simply have not specified one.
    if not opportunity.work_arrangement or not user.work_arrangement:
        return None
    opportunity_value = _ARRANGEMENT_ALIASES.get(
        _normalized_exact(opportunity.work_arrangement)
    )
    user_value = _ARRANGEMENT_ALIASES.get(
        _normalized_exact(user.work_arrangement)
    )
    if not opportunity_value:
        return _result(
            key="preference:work-arrangement",
            label="Work arrangement",
            evidence_type=EvidenceType.WORK_ARRANGEMENT,
            importance=CriterionImportance.PREFERENCE,
            status=MatchStatus.UNKNOWN,
            criterion=opportunity.work_arrangement,
            explanation=(
                "The opportunity's work arrangement is not structured enough to compare safely."
            ),
        )
    found = bool(
        user_value and (user_value == "flexible" or user_value == opportunity_value)
    )
    return _result(
        key="preference:work-arrangement",
        label="Work arrangement",
        evidence_type=EvidenceType.WORK_ARRANGEMENT,
        importance=CriterionImportance.PREFERENCE,
        status=(MatchStatus.FOUND if found else MatchStatus.NOT_FOUND_ON_PROFILE),
        criterion=opportunity.work_arrangement,
        profile_evidence=(user.work_arrangement,) if found else (),
        explanation=(
            "The work arrangement exactly matches your recorded preference."
            if found else
            "This work arrangement was not found in your Career Preferences."
        ),
    )


_KIND_ALIASES = {
    "job": "job",
    "jobs": "job",
    "internship": "internship",
    "internships": "internship",
    "learnership": "learnership",
    "learnerships": "learnership",
    "graduate programme": "graduate_programme",
    "graduate programmes": "graduate_programme",
    "graduate program": "graduate_programme",
    "graduate programs": "graduate_programme",
    "vacation work": "vacation_work",
}


def _role_tokens(value: object) -> tuple[str, ...]:
    """Normalize a role title into comparable whole-word tokens.

    This is used only for career-direction ranking, never employer eligibility.
    It lets a saved target such as ``Data Analyst`` recognize a decorated source
    title such as ``BI Data Analyst`` or ``Data Analyst Intern`` without AI or
    fuzzy semantic guessing.
    """
    return tuple(re.findall(r"[a-z0-9+#]+", _normalized_exact(value)))


def _role_phrase_contained(left: object, right: object) -> bool:
    """True when one multi-token role phrase occurs contiguously in the other.

    Single-word containment is deliberately rejected because titles such as
    ``Analyst`` or ``Consultant`` are too broad to establish useful career
    direction by substring alone.
    """
    left_tokens = _role_tokens(left)
    right_tokens = _role_tokens(right)
    if len(left_tokens) < 2 or len(right_tokens) < 2:
        return False

    shorter, longer = (
        (left_tokens, right_tokens)
        if len(left_tokens) <= len(right_tokens)
        else (right_tokens, left_tokens)
    )
    width = len(shorter)
    return any(
        tuple(longer[index:index + width]) == tuple(shorter)
        for index in range(len(longer) - width + 1)
    )


def _career_preference_results(user, opportunity, taxonomy_context=None):
    results = []
    if user.opportunity_kinds:
        normalized_preferences = {
            _KIND_ALIASES.get(_normalized_exact(value), _normalized_exact(value))
            for value in user.opportunity_kinds
        }
        found = opportunity.opportunity_kind in normalized_preferences
        results.append(_result(
            key="preference:opportunity-kind",
            label="Opportunity type",
            evidence_type=EvidenceType.OPPORTUNITY_KIND,
            importance=CriterionImportance.PREFERENCE,
            status=(MatchStatus.FOUND if found else MatchStatus.NOT_FOUND_ON_PROFILE),
            criterion=opportunity.opportunity_kind.replace("_", " ").title(),
            profile_evidence=user.opportunity_kinds if found else (),
            explanation=(
                "The opportunity type exactly matches your Career Preferences."
                if found else
                "This opportunity type was not found in your Career Preferences."
            ),
        ))
    if user.industries:
        if not opportunity.sector:
            status = MatchStatus.UNKNOWN
            explanation = (
                "The opportunity has no structured sector to compare with your industry preferences."
            )
        else:
            status = (
                MatchStatus.FOUND
                if _normalized_exact(opportunity.sector)
                in {_normalized_exact(value) for value in user.industries}
                else MatchStatus.NOT_FOUND_ON_PROFILE
            )
            explanation = (
                "The opportunity sector exactly matches your industry preferences."
                if status == MatchStatus.FOUND else
                "This exact sector was not found in your industry preferences."
            )
        results.append(_result(
            key="preference:industry",
            label="Industry",
            evidence_type=EvidenceType.INDUSTRY,
            importance=CriterionImportance.PREFERENCE,
            status=status,
            criterion=opportunity.sector or "Not provided",
            profile_evidence=(
                user.industries if status == MatchStatus.FOUND else ()
            ),
            explanation=explanation,
        ))
    if user.target_roles:
        normalized_targets = {
            _normalized_exact(value): value for value in user.target_roles
        }
        exact_target = normalized_targets.get(_normalized_exact(opportunity.title))
        status = MatchStatus.NOT_FOUND_ON_PROFILE
        evidence = ()
        explanation = "This exact role title was not found in your Career Preferences."

        if exact_target:
            status = MatchStatus.FOUND
            evidence = (exact_target,)
            explanation = (
                "The opportunity title exactly matches a target role in your Career Preferences."
            )
        else:
            contained_target = next((
                target for target in user.target_roles
                if _role_phrase_contained(target, opportunity.title)
            ), None)
            if contained_target:
                status = MatchStatus.RELATED
                evidence = (contained_target,)
                explanation = (
                    f"The job title contains the saved target-role phrase {contained_target!r} "
                    "as a contiguous multi-word role phrase. This supports career-direction "
                    "relevance only; it does not establish employer eligibility."
                )

        if (
            status == MatchStatus.NOT_FOUND_ON_PROFILE
            and taxonomy_context is not None
            and opportunity.title
        ):
            opportunity_concepts = taxonomy_context.resolve(
                opportunity.title, _OCCUPATION_CONCEPT_KINDS
            )
            opportunity_identity_concepts = taxonomy_context.resolve_identity(
                opportunity.title, _OCCUPATION_CONCEPT_KINDS
            )
            related_target = None
            related_source = None
            relation_reason = ""
            for target in user.target_roles:
                target_concepts = taxonomy_context.resolve(
                    target, _OCCUPATION_CONCEPT_KINDS
                )
                target_identity_concepts = taxonomy_context.resolve_identity(
                    target, _OCCUPATION_CONCEPT_KINDS
                )
                if not target_concepts or not opportunity_concepts:
                    continue
                same = taxonomy_context.same_concept(
                    target_identity_concepts, opportunity_identity_concepts
                )
                if same:
                    status = MatchStatus.FOUND
                    evidence = (target,)
                    explanation = (
                        f"{target} and {opportunity.title} are labels for the same "
                        f"{_source_name(same[0])} occupation concept."
                    )
                    break

                # A non-identity OFO specialisation can still resolve to the
                # same canonical concept, but it is deliberately RELATED rather
                # than FOUND. Otherwise only a *direct* source-asserted broader
                # edge may support RELATED. Sharing an ISCO unit group is only a
                # candidate-discovery bridge and never matching evidence.
                non_identity_same = taxonomy_context.same_concept(
                    target_concepts, opportunity_concepts
                )
                if non_identity_same:
                    related_target = target
                    related_source = non_identity_same[0]
                    relation_reason = "the same canonical occupation with a non-identity specialisation label"
                    break
                forward = taxonomy_context.narrower_supports_broader(
                    opportunity_concepts, target_concepts, max_depth=1
                )
                reverse = taxonomy_context.narrower_supports_broader(
                    target_concepts, opportunity_concepts, max_depth=1
                )
                if forward or reverse:
                    relation = forward or reverse
                    related_target = target
                    related_source = relation[0]
                    relation_reason = "a direct trusted broader/narrower occupation relationship"
                    break

            if status != MatchStatus.FOUND and related_target:
                status = MatchStatus.RELATED
                evidence = (related_target,)
                explanation = (
                    f"{opportunity.title} is related to your target role {related_target} through "
                    f"{relation_reason} recorded by {_source_name(related_source)}. "
                    "This is preference evidence only; it does not establish eligibility."
                )

        results.append(_result(
            key="preference:target-role",
            label="Target role",
            evidence_type=EvidenceType.TARGET_ROLE,
            importance=CriterionImportance.PREFERENCE,
            status=status,
            criterion=opportunity.title,
            profile_evidence=evidence,
            explanation=explanation,
        ))
    return results


def _eligibility_result(opportunity, importance=CriterionImportance.REQUIRED):
    text = opportunity.preferred_eligibility_text if importance == CriterionImportance.PREFERRED else opportunity.eligibility_text
    if not text:
        return None
    return _result(
        key=f"{importance.value.lower()}:eligibility",
        label="Eligibility",
        evidence_type=EvidenceType.ELIGIBILITY,
        importance=importance,
        status=MatchStatus.UNKNOWN,
        criterion=text,
        explanation=(
            "CareerGPS preserves this requirement but cannot safely verify it against "
            "the current structured profile fields. Please review it yourself."
        ),
    )


def _criterion_category(item: CriterionResult) -> MatchCategory | None:
    return EVIDENCE_CATEGORY.get(item.evidence_type)


def _category_fit(
    criteria: Iterable[CriterionResult],
    importance: CriterionImportance,
) -> tuple[dict[MatchCategory, float], dict[MatchCategory, int]]:
    """Return significance-aware per-category fit and comparable counts.

    UNKNOWN is excluded rather than treated as a failure. RELATED contributes
    half credit. Within a category, crucial/core/supporting criteria contribute
    3/2/0.5 respectively, preventing a pile of generic behavioural traits from
    overpowering technical or hard requirements.
    """
    grouped: dict[MatchCategory, list[tuple[float, float]]] = {}
    for item in criteria:
        if item.importance != importance:
            continue
        category = _criterion_category(item)
        if category is None or item.status == MatchStatus.UNKNOWN:
            continue
        score = STATUS_SCORE.get(item.status)
        if score is None:
            continue
        grouped.setdefault(category, []).append((
            score,
            SIGNIFICANCE_WEIGHT[item.significance],
        ))

    fits = {}
    counts = {}
    for category, values in grouped.items():
        denominator = sum(weight for _, weight in values)
        if denominator <= 0:
            continue
        fits[category] = sum(score * weight for score, weight in values) / denominator
        counts[category] = len(values)
    return fits, counts


def _weighted_category_fit(category_fits: dict[MatchCategory, float]) -> int | None:
    if not category_fits:
        return None
    denominator = sum(CATEGORY_WEIGHT[category] for category in category_fits)
    if denominator <= 0:
        return None
    numerator = sum(
        CATEGORY_WEIGHT[category] * fit
        for category, fit in category_fits.items()
    )
    return round((numerator / denominator) * 100)


def _important_coverage(
    important: Iterable[CriterionResult],
    importance: CriterionImportance,
) -> int:
    """Return category-balanced coverage for crucial/core evidence only.

    This intentionally excludes supporting behavioural evidence. A 100 here is
    still only a *coverage rate*; recommendation bands separately require enough
    evidence breadth so one matched field cannot masquerade as a top match.
    """
    fits, _ = _category_fit(important, importance)
    value = _weighted_category_fit(fits)
    return value if value is not None else 0


def _build_summary(
    criteria: tuple[CriterionResult, ...],
    *,
    source_completeness: str = "unknown",
) -> MatchSummary:
    """Build an evidence-aware employer-fit summary.

    The percentage remains a *known-requirement fit rate*. It is intentionally
    not the ranking decision. Match labels also require sufficient meaningful
    evidence so that 1/1 cannot masquerade as a well-supported 100% match.
    """
    substantive_all = [
        item for item in criteria
        if item.importance in {
            CriterionImportance.REQUIRED,
            CriterionImportance.PREFERRED,
        }
        and _criterion_category(item) is not None
    ]

    required_categories, required_counts = _category_fit(
        substantive_all, CriterionImportance.REQUIRED
    )
    preferred_categories, preferred_counts = _category_fit(
        substantive_all, CriterionImportance.PREFERRED
    )
    required_fit_value = _weighted_category_fit(required_categories)
    preferred_fit_value = _weighted_category_fit(preferred_categories)

    if required_fit_value is not None and preferred_fit_value is not None:
        score = round(
            required_fit_value * REQUIRED_SHARE
            + preferred_fit_value * PREFERRED_SHARE
        )
    elif required_fit_value is not None:
        score = required_fit_value
    elif preferred_fit_value is not None:
        score = preferred_fit_value
    else:
        score = 0

    comparable_statuses = {
        MatchStatus.FOUND,
        MatchStatus.RELATED,
        MatchStatus.NOT_FOUND_ON_PROFILE,
    }
    comparable = [
        item for item in substantive_all
        if item.status in comparable_statuses
    ]
    important = [
        item for item in comparable
        if item.significance in {
            EvidenceSignificance.CRUCIAL,
            EvidenceSignificance.CORE,
        }
    ]
    supporting = [
        item for item in comparable
        if item.significance == EvidenceSignificance.SUPPORTING
    ]

    important_found_count = sum(item.status == MatchStatus.FOUND for item in important)
    important_related_count = sum(item.status == MatchStatus.RELATED for item in important)
    supporting_found_count = sum(item.status == MatchStatus.FOUND for item in supporting)
    required_important_match_points = sum(
        STATUS_SCORE[item.status]
        for item in important
        if item.importance == CriterionImportance.REQUIRED
        and item.status in STATUS_SCORE
    )
    preferred_important_match_points = sum(
        STATUS_SCORE[item.status]
        for item in important
        if item.importance == CriterionImportance.PREFERRED
        and item.status in STATUS_SCORE
    )
    important_match_points = (
        required_important_match_points
        + 0.5 * preferred_important_match_points
    )
    important_categories = {
        _criterion_category(item) for item in important
        if _criterion_category(item) is not None
    }
    important_category_count = len(important_categories)
    matched_important = [
        item for item in important
        if item.status in {MatchStatus.FOUND, MatchStatus.RELATED}
    ]
    found_important = [
        item for item in important
        if item.status == MatchStatus.FOUND
    ]
    matched_important_categories = {
        _criterion_category(item) for item in matched_important
        if _criterion_category(item) is not None
    }
    found_important_categories = {
        _criterion_category(item) for item in found_important
        if _criterion_category(item) is not None
    }
    required_matched_important_categories = {
        _criterion_category(item) for item in matched_important
        if item.importance == CriterionImportance.REQUIRED
        and _criterion_category(item) is not None
    }
    required_found_important_categories = {
        _criterion_category(item) for item in found_important
        if item.importance == CriterionImportance.REQUIRED
        and _criterion_category(item) is not None
    }
    matched_important_category_count = len(matched_important_categories)
    found_important_category_count = len(found_important_categories)
    required_matched_important_category_count = len(
        required_matched_important_categories
    )
    required_found_important_category_count = len(
        required_found_important_categories
    )
    required_important_coverage = _important_coverage(
        important, CriterionImportance.REQUIRED
    )
    preferred_important_coverage = _important_coverage(
        important, CriterionImportance.PREFERRED
    )
    strongest_important_match_priority = 0
    if any(
        item.significance == EvidenceSignificance.CRUCIAL
        for item in matched_important
    ):
        strongest_important_match_priority = 2
    elif any(
        item.significance == EvidenceSignificance.CORE
        for item in matched_important
    ):
        strongest_important_match_priority = 1

    major_gaps = [
        item for item in substantive_all
        if item.importance == CriterionImportance.REQUIRED
        and item.significance == EvidenceSignificance.CRUCIAL
        and item.status == MatchStatus.NOT_FOUND_ON_PROFILE
    ]
    hard_required_conflict = bool(major_gaps)
    gate_gap_count = sum(
        item.evidence_type in GATE_GAP_EVIDENCE for item in major_gaps
    )
    foundation_gap_count = sum(
        item.evidence_type in FOUNDATION_GAP_EVIDENCE for item in major_gaps
    )
    # Defensive fallback: any future crucial hard-evidence type that is not
    # explicitly categorized still counts as a foundation gap for ordering.
    foundation_gap_count += max(
        0, len(major_gaps) - gate_gap_count - foundation_gap_count
    )
    required_unknown = any(
        item.importance == CriterionImportance.REQUIRED
        and item.significance in {EvidenceSignificance.CRUCIAL, EvidenceSignificance.CORE}
        and item.status == MatchStatus.UNKNOWN
        for item in substantive_all
    )
    critical_unknown_count = sum(
        item.importance == CriterionImportance.REQUIRED
        and item.significance == EvidenceSignificance.CRUCIAL
        and item.status == MatchStatus.UNKNOWN
        for item in substantive_all
    )

    # Evidence strength is driven by independent substantive breadth rather than
    # advert verbosity. Two genuine important requirements in two different
    # categories can be enough for High evidence on a complete source; twenty
    # rows from one category do not become High merely because the advert is long.
    # Evidence breadth, not optional employer-field disclosure, controls strength.
    if len(important) >= 2 and important_category_count >= 2:
        evidence_strength = "High"
    elif len(important) >= 2:
        evidence_strength = "Medium"
    else:
        evidence_strength = "Low"

    required_important_comparable = [
        item for item in important
        if item.importance == CriterionImportance.REQUIRED
    ]

    # A match label describes confidence in employer fit, not just arithmetic.
    # One matched criterion can still have a 100% known-fit rate, but it is never
    # allowed to become Strong/Good without enough important evidence. Preferred
    # matches also cannot rescue a job when no important required criterion matches.
    if not comparable:
        label = "Explore"
    elif (
        required_important_comparable
        and required_important_match_points == 0
    ):
        label = "Explore"
    elif hard_required_conflict:
        label = "Partial Match" if important_match_points > 0 else "Explore"
    elif (
        evidence_strength == "High"
        and not required_unknown
        and important_match_points >= 3.0
        and (required_fit_value if required_fit_value is not None else score) >= 80
    ):
        label = "Strong Match"
    elif (
        evidence_strength in {"High", "Medium"}
        and important_match_points >= 2.0
        and (required_fit_value if required_fit_value is not None else score) >= 60
    ):
        label = "Good Match"
    elif important_match_points > 0 or supporting_found_count > 0:
        label = "Partial Match"
    else:
        label = "Explore"

    found_count = sum(item.status == MatchStatus.FOUND for item in substantive_all)
    related_count = sum(item.status == MatchStatus.RELATED for item in substantive_all)
    not_found_count = sum(
        item.status == MatchStatus.NOT_FOUND_ON_PROFILE for item in substantive_all
    )
    unknown_count = sum(item.status == MatchStatus.UNKNOWN for item in substantive_all)

    category_rows = []
    for category in MatchCategory:
        required_category_fit = required_categories.get(category)
        preferred_category_fit = preferred_categories.get(category)
        if required_category_fit is None and preferred_category_fit is None:
            continue
        category_rows.append(CategoryFit(
            category=category,
            weight=CATEGORY_WEIGHT[category],
            required_fit=(
                round(required_category_fit * 100)
                if required_category_fit is not None else None
            ),
            preferred_fit=(
                round(preferred_category_fit * 100)
                if preferred_category_fit is not None else None
            ),
            required_comparable=required_counts.get(category, 0),
            preferred_comparable=preferred_counts.get(category, 0),
        ))

    if important_found_count or important_related_count:
        parts = []
        if important_found_count:
            parts.append(
                f"{important_found_count} direct important evidence item"
                f"{'s' if important_found_count != 1 else ''}"
            )
        if important_related_count:
            parts.append(
                f"{important_related_count} related important evidence item"
                f"{'s' if important_related_count != 1 else ''}"
            )
        explanation = "CareerGPS found " + " and ".join(parts) + "."
    elif supporting_found_count:
        explanation = (
            f"CareerGPS found {supporting_found_count} supporting evidence item"
            f"{'s' if supporting_found_count != 1 else ''}, but no important career-fit evidence yet."
        )
    elif substantive_all:
        explanation = (
            "CareerGPS has not found safely comparable important career-fit evidence on your profile yet."
        )
    else:
        explanation = (
            "This opportunity does not yet contain substantive structured criteria CareerGPS can compare."
        )

    explanation += (
        f" Evidence strength is {evidence_strength.lower()} based on "
        f"{len(important)} comparable crucial/core requirement"
        f"{'s' if len(important) != 1 else ''} across {important_category_count} important "
        f"{'categories' if important_category_count != 1 else 'category'}."
    )
    if major_gaps:
        explanation += (
            f" CareerGPS found {len(major_gaps)} major required gap"
            f"{'s' if len(major_gaps) != 1 else ''}."
        )
    explanation += (
        " Required employer criteria carry more weight than preferred criteria."
        " Generic supporting skills carry less weight than crucial/core evidence."
        " Location and other preferences do not establish career fit and never create employer fit."
        " Recommendations are based on the opportunity information available to CareerGPS and the structured information saved on your profile."
    )
    explanation += " Always review the employer listing before applying."

    return MatchSummary(
        score=score,
        label=label,
        required_fit=(required_fit_value if required_fit_value is not None else 0),
        preferred_fit=(preferred_fit_value if preferred_fit_value is not None else 0),
        found_count=found_count,
        related_count=related_count,
        not_found_count=not_found_count,
        unknown_count=unknown_count,
        comparable_count=len(comparable),
        total_count=len(substantive_all),
        hard_required_conflict=hard_required_conflict,
        category_fits=tuple(category_rows),
        explanation=explanation,
        evidence_strength=evidence_strength,
        important_comparable_count=len(important),
        important_found_count=important_found_count,
        important_related_count=important_related_count,
        supporting_comparable_count=len(supporting),
        supporting_found_count=supporting_found_count,
        major_gap_count=len(major_gaps),
        important_match_points=important_match_points,
        required_important_match_points=required_important_match_points,
        preferred_important_match_points=preferred_important_match_points,
        important_category_count=important_category_count,
        matched_important_category_count=matched_important_category_count,
        required_matched_important_category_count=(
            required_matched_important_category_count
        ),
        required_important_coverage=required_important_coverage,
        preferred_important_coverage=preferred_important_coverage,
        strongest_important_match_priority=strongest_important_match_priority,
        found_important_category_count=found_important_category_count,
        required_found_important_category_count=(
            required_found_important_category_count
        ),
        critical_unknown_count=critical_unknown_count,
        gate_gap_count=gate_gap_count,
        foundation_gap_count=foundation_gap_count,
    )

def _taxonomy_terms_for_run(
    user: UserMatchingProfile,
    opportunities: Iterable[OpportunityMatchingInput],
) -> dict[str, tuple[str, ...]]:
    """Collect only terms Phase 2.3 is allowed to resolve.

    Qualification, NQF, location, industry, eligibility and other hard/structured
    dimensions intentionally remain outside this taxonomy layer for now.
    """
    skill_terms = list(user.skills)
    occupation_terms = list(user.target_roles)
    occupation_terms.extend(
        record.title for record in user.work_experiences if record.title
    )

    for opportunity in opportunities:
        skill_terms.extend(opportunity.required_skills)
        skill_terms.extend(opportunity.required_supporting_skills)
        skill_terms.extend(opportunity.preferred_skills)
        skill_terms.extend(opportunity.preferred_supporting_skills)
        if user.target_roles and opportunity.title:
            occupation_terms.append(opportunity.title)
        for constraint in opportunity.experience_constraints:
            occupation_terms.extend(
                _experience_context_terms(constraint, opportunity)
            )

    skill_terms = [part.strip() for term in skill_terms for part in re.split(r"\s+(?:or|and/or)\s+", term, flags=re.IGNORECASE)]
    return {
        "skill": _unique_display_values(skill_terms),
        "skill_group": _unique_display_values(skill_terms),
        "occupation": _unique_display_values(occupation_terms),
        "occupation_group": _unique_display_values(occupation_terms),
    }


def build_taxonomy_context_for_run(
    user: UserMatchingProfile,
    opportunities: Iterable[OpportunityMatchingInput],
) -> TaxonomyMatchContext:
    """Build one reusable local taxonomy context for a batch of matches.

    Views that compare several opportunities must build this once and pass it to
    every evaluation. This preserves Phase 2.2's no-N+1 matching guarantee even
    after taxonomy enrichment is enabled.
    """
    return build_taxonomy_context(_taxonomy_terms_for_run(user, opportunities))


def evaluate_match(
    user: UserMatchingProfile,
    opportunity: OpportunityMatchingInput,
    *,
    taxonomy_context: TaxonomyMatchContext | None = None,
) -> OpportunityMatchResult:
    """Compare structured evidence, then add only trusted taxonomy support.

    Phase 2.3 is monotonic: trusted aliases may establish canonical identity and
    trusted directional relationships may add RELATED evidence. It never rescues
    failed hard constraints or invents an unsupported relationship.
    """
    criteria = []
    criteria.extend(_skill_results(
        user,
        opportunity.required_skills,
        CriterionImportance.REQUIRED,
        taxonomy_context,
        significance=EvidenceSignificance.CORE,
    ))
    criteria.extend(_skill_results(
        user,
        opportunity.required_supporting_skills,
        CriterionImportance.REQUIRED,
        taxonomy_context,
        significance=EvidenceSignificance.SUPPORTING,
    ))
    criteria.extend(_skill_results(
        user,
        opportunity.preferred_skills,
        CriterionImportance.PREFERRED,
        taxonomy_context,
        significance=EvidenceSignificance.CORE,
    ))
    criteria.extend(_skill_results(
        user,
        opportunity.preferred_supporting_skills,
        CriterionImportance.PREFERRED,
        taxonomy_context,
        significance=EvidenceSignificance.SUPPORTING,
    ))
    required_qualifications = (
        opportunity.required_qualifications
        or ((opportunity.required_qualification,) if opportunity.required_qualification else ())
    )
    preferred_qualifications = (
        opportunity.preferred_qualifications
        or ((opportunity.preferred_qualification,) if opportunity.preferred_qualification else ())
    )
    for index, requirement in enumerate(required_qualifications, start=1):
        item = _qualification_result(
            user, requirement, CriterionImportance.REQUIRED,
            key_suffix=(f":{index}" if len(required_qualifications) > 1 else ""),
        )
        if item is not None:
            criteria.append(item)
    for index, requirement in enumerate(preferred_qualifications, start=1):
        item = _qualification_result(
            user, requirement, CriterionImportance.PREFERRED,
            key_suffix=(f":{index}" if len(preferred_qualifications) > 1 else ""),
        )
        if item is not None:
            criteria.append(item)
    criteria.extend(_certification_results(
        user, opportunity.required_certifications, CriterionImportance.REQUIRED
    ))
    criteria.extend(_certification_results(
        user, opportunity.preferred_certifications, CriterionImportance.PREFERRED
    ))
    criteria.extend(_language_results(
        user, opportunity.required_languages, CriterionImportance.REQUIRED
    ))
    criteria.extend(_language_results(
        user, opportunity.preferred_languages, CriterionImportance.PREFERRED
    ))
    for item in (
        _nqf_result(user, opportunity.minimum_nqf),
        _nqf_result(user, opportunity.preferred_minimum_nqf, CriterionImportance.PREFERRED),
        _eligibility_result(opportunity),
        _eligibility_result(opportunity, CriterionImportance.PREFERRED),
        _location_result(user, opportunity),
        _work_arrangement_result(user, opportunity),
    ):
        if item is not None:
            criteria.append(item)
    criteria.extend(_experience_constraint_results(
        user,
        opportunity,
        opportunity.experience_constraints,
        taxonomy_context,
    ))
    criteria.extend(_career_preference_results(
        user, opportunity, taxonomy_context
    ))
    criteria_tuple = tuple(criteria)
    return OpportunityMatchResult(
        user=user,
        opportunity=opportunity,
        criteria=criteria_tuple,
        summary=_build_summary(criteria_tuple, source_completeness=opportunity.source_completeness),
    )


def match_profile_to_opportunity(
    profile,
    opportunity,
    *,
    user_matching_profile=None,
    taxonomy_context=None,
):
    opportunity_input = build_opportunity_matching_input(opportunity)
    user_input = user_matching_profile or build_user_matching_profile(
        profile,
        include_certifications=bool(
            opportunity_input.required_certifications or opportunity_input.preferred_certifications
        ),
        include_languages=bool(
            opportunity_input.required_languages or opportunity_input.preferred_languages
        ),
    )
    if taxonomy_context is None:
        taxonomy_context = build_taxonomy_context_for_run(
            user_input, (opportunity_input,)
        )
    return evaluate_match(
        user_input,
        opportunity_input,
        taxonomy_context=taxonomy_context,
    )


MATCH_TIER_PRIORITY = {
    "Strong Match": 3,
    "Good Match": 2,
    "Partial Match": 1,
    "Explore": 0,
}

EVIDENCE_STRENGTH_PRIORITY = {
    "High": 3,
    "Medium": 2,
    "Low": 1,
}

RECOMMENDATION_BAND_PRIORITY = {
    "A": 6,  # Strong, well-supported match
    "B": 5,  # Good, supported match
    "C": 4,  # Relevant/credible but limited evidence
    "D": 3,  # Thin / weak support
    "E": 2,  # Relevant but blocked (confirmed major gap)
    "F": 1,  # Low relevance / poor fit
}

RECOMMENDATION_BAND_LABELS = {
    "A": "Strong supported match",
    "B": "Good supported match",
    "C": "Relevant / limited evidence",
    "D": "Thin / weak support",
    "E": "Relevant but blocked",
    "F": "Low relevance / poor fit",
}


@dataclass(frozen=True)
class RecommendationDecision:
    """Transparent A-F admission decision for one opportunity/profile pair."""

    provisional_band: str
    confidence_capped_band: str
    final_band: str
    label: str
    reason: str


def _career_direction_priority(result: OpportunityMatchResult) -> tuple[int, str]:
    target = next(
        (
            item for item in result.preference_results
            if item.evidence_type == EvidenceType.TARGET_ROLE
        ),
        None,
    )
    if target is None:
        return 0, "No target-role preference"
    if target.status == MatchStatus.FOUND:
        return 2, "Exact target-role alignment"
    if target.status == MatchStatus.RELATED:
        return 1, "Related target-role alignment"
    return 0, "No supported target-role alignment"


def _ranking_role_phrase_contained(target_role: object, opportunity_title: object) -> bool:
    """Ranking-only title containment, including single-word saved targets.

    Phase 2.3 matching evidence deliberately requires multi-word containment because
    generic single words can be too broad to call RELATED evidence. Ranking has a
    different job: respect the career direction the user explicitly chose. Therefore
    a saved target such as ``Pharmacist`` may rank ``Pharmacist Assistant`` as
    career-related, while this signal remains preference-only and can never satisfy
    an employer requirement or remove a blocker.
    """
    target_tokens = _role_tokens(target_role)
    title_tokens = _role_tokens(opportunity_title)
    if not target_tokens or not title_tokens or len(target_tokens) > len(title_tokens):
        return False

    width = len(target_tokens)
    return any(
        tuple(title_tokens[index:index + width]) == tuple(target_tokens)
        for index in range(len(title_tokens) - width + 1)
    )


def _career_direction_ranking_tier(
    user: UserMatchingProfile,
    opportunity: OpportunityMatchingInput,
    result: OpportunityMatchResult,
    taxonomy_context: TaxonomyMatchContext | None = None,
) -> tuple[int, str]:
    """Retain legacy career-direction context without giving it feed priority.

    The historical tier values for saved free-text target roles are:

    3 = exact/canonical target occupation
    2 = closely related title or trusted direct occupation relationship
    1 = adjacent occupation in the same trusted ISCO unit group
    0 = no supported career-direction relationship

    This never changes criterion statuses, recommendation bands, eligibility,
    evidence strength, blockers, employer-fit scoring, or current feed order.
    """
    if not user.target_roles:
        return 0, "No target role saved"

    existing_priority, existing_label = _career_direction_priority(result)
    if existing_priority == 2:
        return 3, existing_label
    if existing_priority == 1:
        return 2, existing_label

    # Ranking may safely use a broader whole-token title containment rule than
    # matching evidence because this signal cannot establish eligibility. This is
    # what lets a single-word target such as Pharmacist prioritize titles such as
    # Pharmacist Assistant without claiming the occupations are identical.
    for target in user.target_roles:
        if _ranking_role_phrase_contained(target, opportunity.title):
            return 2, f"Title is closely related to saved target role {target!r}"

    # If exact labels resolve locally, a shared trusted ISCO unit group is useful
    # as an adjacency signal. It remains ranking-only: the existing matcher still
    # refuses to treat shared ISCO membership as employer-fit evidence.
    if taxonomy_context is not None and opportunity.title:
        opportunity_concepts = taxonomy_context.resolve(
            opportunity.title, _OCCUPATION_CONCEPT_KINDS
        )
        if opportunity_concepts:
            for target in user.target_roles:
                target_concepts = taxonomy_context.resolve(
                    target, _OCCUPATION_CONCEPT_KINDS
                )
                if target_concepts and taxonomy_context.occupations_share_isco_group(
                    target_concepts, opportunity_concepts
                ):
                    return 1, f"Same trusted occupation family as saved target role {target!r}"

    return 0, "No supported career-direction relationship"


def _preference_fit(result: OpportunityMatchResult) -> int:
    """Return a ranking-only feasibility/preference signal.

    Career direction is handled separately. UNKNOWN preferences are ignored;
    FOUND/RELATED/NOT_FOUND are scored 1/.5/0. This value never changes employer
    fit or recommendation evidence strength.
    """
    values = []
    for item in result.preference_results:
        if item.evidence_type == EvidenceType.TARGET_ROLE:
            continue
        score = STATUS_SCORE.get(item.status)
        if score is not None:
            values.append(score)
    if not values:
        return 0
    return round((sum(values) / len(values)) * 100)


def _positive_support_band(result: OpportunityMatchResult) -> tuple[str, str]:
    """Earn A-F from positive support only; confirmed blockers are handled later.

    This ordering is deliberate: positive evidence earns relevance, uncertainty
    can cap confidence, and a blocker may only downgrade relevance that already
    existed. A missing requirement must never manufacture relevance by itself.
    """
    summary = result.summary
    career_priority, _ = _career_direction_priority(result)
    exact_career = career_priority == 2
    has_career_alignment = career_priority > 0
    has_positive_important = (
        summary.important_found_count + summary.important_related_count
    ) > 0

    # A requires direct evidence in at least two independent meaningful areas.
    # RELATED-only breadth cannot manufacture a top recommendation.
    if (
        summary.evidence_strength == "High"
        and summary.found_important_category_count >= 2
        and summary.required_important_coverage >= 80
    ):
        return "A", "Strong direct evidence across multiple important areas."

    # B can use conservative RELATED support, but it still requires at least one
    # directly FOUND important area and meaningful breadth across two areas.
    if (
        summary.evidence_strength in {"High", "Medium"}
        and summary.matched_important_category_count >= 2
        and summary.found_important_category_count >= 1
        and summary.required_important_coverage >= 60
    ):
        return "B", "Good supported evidence across multiple important areas."

    # C is for genuine relevance that CareerGPS cannot support strongly enough
    # for A/B. Exact target-role alignment with no comparable important employer
    # criteria is uncertainty, not a poor fit.
    if exact_career and summary.important_comparable_count == 0:
        return "C", "Exact target role, but insufficient comparable employer evidence."

    if has_career_alignment and has_positive_important:
        return "C", "Career-aligned role with at least one positive important match."

    if (
        not has_career_alignment
        and summary.matched_important_category_count >= 2
        and summary.required_important_coverage >= 30
        and (
            summary.evidence_strength in {"High", "Medium"}
            or summary.important_comparable_count >= 4
        )
    ):
        return "C", "Meaningful positive evidence spans multiple important areas."

    # D is intentionally broad but weak: one important match, supporting-only
    # matches, or career alignment that lacks enough substantive employer support.
    if (
        has_positive_important
        or summary.supporting_found_count > 0
        or has_career_alignment
    ):
        return "D", "Only thin or weak positive support is available."

    return "F", "No meaningful positive employer-fit or career signal was established."


def _recommendation_decision(result: OpportunityMatchResult) -> RecommendationDecision:
    provisional_band, reason = _positive_support_band(result)
    capped_band = provisional_band
    cap_reasons = []

    # Confidence caps do not create failure. They only prevent A/B when CareerGPS
    # knows the source or a critical mandatory criterion remains uncertain.
    if capped_band in {"A", "B"} and result.summary.critical_unknown_count > 0:
        capped_band = "C"
        cap_reasons.append("a critical mandatory requirement is still UNKNOWN")

    if capped_band in {"A", "B"} and (
        getattr(result.opportunity, "source_completeness", "")
        == "partial_text"
    ):
        capped_band = "C"
        cap_reasons.append("the vacancy source text is only partial")

    final_band = capped_band
    if result.summary.major_gap_count > 0:
        # A blocker may downgrade a genuinely relevant job, but it may never
        # promote a D/F job into the more relevant E band.
        final_band = "E" if capped_band in {"A", "B", "C"} else "F"
        if final_band == "E":
            reason = "Meaningful relevance exists, but a confirmed major requirement blocks it."
        else:
            reason = "The job has a confirmed major gap without enough positive relevance for Band E."
    elif cap_reasons:
        reason = reason + " Confidence capped because " + " and ".join(cap_reasons) + "."

    return RecommendationDecision(
        provisional_band=provisional_band,
        confidence_capped_band=capped_band,
        final_band=final_band,
        label=RECOMMENDATION_BAND_LABELS[final_band],
        reason=reason,
    )


def _recommendation_band(result: OpportunityMatchResult) -> tuple[str, str]:
    """Backward-compatible final A-F band accessor."""
    decision = _recommendation_decision(result)
    return decision.final_band, decision.label


def _within_band_ranking_key(item: dict) -> tuple[int, ...]:
    """Return the comparator appropriate to *why* a job is in its band.

    No universal magic score is used. The band already answers the broad
    recommendation question; this key only orders opportunities that share the
    same recommendation class. Raw advert length never gets a direct bonus.
    """
    band = item["recommendation_band"]

    if band in {"A", "B"}:
        # Supported matches: reward breadth of actual required evidence, then
        # how much of the important required set is covered. Breadth is capped
        # so an advert cannot win merely by listing more categories.
        return (
            min(item["required_matched_important_category_count"], 2),
            item["required_important_coverage"],
            min(item["matched_important_category_count"], 3),
            item["evidence_strength_priority"],
            item["career_direction_priority"],
            item["preferred_important_coverage"],
            item["preference_fit"],
            item["score"],
            item["scope_priority"],
        )

    if band == "C":
        # Limited-evidence/relevant opportunities: substantive matched breadth
        # comes first. Career direction helps once the real employer evidence is
        # accounted for; an exact title alone does not beat several meaningful
        # matching areas.
        return (
            min(item["matched_important_category_count"], 2),
            item["required_important_coverage"],
            item["career_direction_priority"],
            item["evidence_strength_priority"],
            item["preferred_important_coverage"],
            item["preference_fit"],
            item["score"],
            item["scope_priority"],
        )

    if band == "D":
        # Thin evidence cannot prove suitability, so career direction is the
        # most useful discriminator. If career direction ties, one crucial/core
        # match beats supporting-only evidence, then direct coverage breaks ties.
        return (
            item["career_direction_priority"],
            item["strongest_important_match_priority"],
            item["required_important_coverage"],
            min(item["matched_important_category_count"], 1),
            item["preference_fit"],
            item["score"],
            item["scope_priority"],
        )

    if band == "E":
        # Major-gap jobs are ordered by the seriousness of what blocks them, not
        # by how many positive facts happen to be listed. One missing gate-like
        # requirement is worse than ordinary foundation gaps. Fewer gaps wins;
        # then career direction and surviving substantive fit matter.
        return (
            -item["gate_gap_count"],
            -item["foundation_gap_count"],
            item["career_direction_priority"],
            min(item["required_matched_important_category_count"], 2),
            item["required_important_coverage"],
            min(item["matched_important_category_count"], 3),
            item["evidence_strength_priority"],
            item["preferred_important_coverage"],
            item["preference_fit"],
            item["score"],
            item["scope_priority"],
        )

    # Band F can now contain either genuinely unrelated jobs or weakly supported
    # jobs that were further downgraded by a confirmed blocker. Keep it simple,
    # but preserve any residual career/substantive signal before preferences.
    return (
        item["career_direction_priority"],
        item["strongest_important_match_priority"],
        min(item["matched_important_category_count"], 1),
        item["required_important_coverage"],
        -item["gate_gap_count"],
        -item["foundation_gap_count"],
        item["preference_fit"],
        item["scope_priority"],
        item["score"],
    )


def _ranking_key(item: dict) -> tuple[int, ...]:
    """Career direction is the outer key; A-F and its comparator follow.

    Mandatory feed order: career-direction tier (descending) -> A-F
    priority -> the band-specific within-band comparator -> stable tie
    behaviour. The retired discovery surface always contributes 0, so
    persisted discovery provenance never influences ordering.
    """
    return (
        item.get("career_direction_tier_priority", 0),
        item["recommendation_priority"],
        *_within_band_ranking_key(item),
    )


def _result_dictionary(
    opportunity,
    result,
    cv_tips=(),
    *,
    user: UserMatchingProfile | None = None,
    taxonomy_context: TaxonomyMatchContext | None = None,
    discovery_provenance=(),
):
    found_labels = [
        item.criterion for item in result.criteria if item.status == MatchStatus.FOUND
    ]
    if not discovery_provenance:
        from core.services.opportunity_discovery import evidence_fallback_reason
        fallback_reason = evidence_fallback_reason(result.criteria)
        discovery_provenance = (fallback_reason,) if fallback_reason else ()
    career_direction_priority, career_direction_label = _career_direction_priority(result)
    if user is not None:
        career_direction_tier_priority, career_direction_tier_label = (
            _career_direction_ranking_tier(
                user,
                result.opportunity,
                result,
                taxonomy_context=taxonomy_context,
            )
        )
    else:
        # Backward-safe fallback for any internal caller that does not provide the
        # batch user/context. Existing exact/related signals still map correctly.
        career_direction_tier_priority = (
            3 if career_direction_priority == 2
            else 2 if career_direction_priority == 1
            else 0
        )
        career_direction_tier_label = career_direction_label
    recommendation_decision = _recommendation_decision(result)
    recommendation_band = recommendation_decision.final_band
    recommendation_label = recommendation_decision.label
    discovery_priority = max(({
        "exact_desired_career": 3,
        "trusted_specialisation": 2,
        "trusted_related_occupation": 1,
    }.get(getattr(reason, "kind", None), 0)
        for reason in discovery_provenance), default=0)
    return {
        "opportunity": opportunity,
        "score": result.summary.score,
        "required_fit": result.summary.required_fit,
        "preferred_fit": result.summary.preferred_fit,
        "match_strength": result.summary.label,
        # Preserve the Phase 2.5 field for backwards compatibility/UI detail.
        "career_direction_priority": career_direction_priority,
        "career_direction_label": career_direction_label,
        # Legacy diagnostic tier; the personalised feed ranks fit before intent.
        "career_direction_tier_priority": career_direction_tier_priority,
        "career_direction_tier_label": career_direction_tier_label,
        "preference_fit": _preference_fit(result),
        "recommendation_band": recommendation_band,
        "recommendation_label": recommendation_label,
        "recommendation_priority": RECOMMENDATION_BAND_PRIORITY[recommendation_band],
        "provisional_recommendation_band": recommendation_decision.provisional_band,
        "confidence_capped_band": recommendation_decision.confidence_capped_band,
        "recommendation_reason": recommendation_decision.reason,
        "evidence_strength": result.summary.evidence_strength,
        "evidence_strength_priority": EVIDENCE_STRENGTH_PRIORITY[result.summary.evidence_strength],
        "important_comparable_count": result.summary.important_comparable_count,
        "important_found_count": result.summary.important_found_count,
        "important_related_count": result.summary.important_related_count,
        "important_match_points": result.summary.important_match_points,
        "required_important_match_points": result.summary.required_important_match_points,
        "preferred_important_match_points": result.summary.preferred_important_match_points,
        "matched_important_category_count": (
            result.summary.matched_important_category_count
        ),
        "required_matched_important_category_count": (
            result.summary.required_matched_important_category_count
        ),
        "required_important_coverage": result.summary.required_important_coverage,
        "preferred_important_coverage": result.summary.preferred_important_coverage,
        "strongest_important_match_priority": (
            result.summary.strongest_important_match_priority
        ),
        "found_important_category_count": (
            result.summary.found_important_category_count
        ),
        "required_found_important_category_count": (
            result.summary.required_found_important_category_count
        ),
        "critical_unknown_count": result.summary.critical_unknown_count,
        "supporting_comparable_count": result.summary.supporting_comparable_count,
        "supporting_found_count": result.summary.supporting_found_count,
        "major_gap_count": result.summary.major_gap_count,
        "gate_gap_count": result.summary.gate_gap_count,
        "foundation_gap_count": result.summary.foundation_gap_count,
        "comparable_evidence_count": result.summary.comparable_count,
        "uses_ai_extraction": result.opportunity.uses_ai_extraction,
        "source_completeness": result.opportunity.source_completeness,
        "scope_priority": 1 if opportunity.scope_status == "in_scope" else 0,
        "why_text": result.summary.explanation,
        "matched_reasons": found_labels,
        "criteria": result.criteria,
        "required_results": result.required_results,
        "preferred_results": result.preferred_results,
        "preference_results": result.preference_results,
        "match_result": result,
        "cv_tips": cv_tips,
        "discovery_provenance": discovery_provenance,
        "discovery_priority": discovery_priority,
    }


OPPORTUNITY_MATCHING_FIELDS = (
    "id",
    "title",
    "company",
    "opportunity_type",
    "opportunity_kind",
    "scope_status",
    "work_arrangement",
    "sector",
    "location",
    "location_text",
    "city",
    "province",
    "country",
    "duration",
    "stipend",
    "required_qualification",
    "qualification_text",
    "qualification_minimum",
    "qualification_preferred",
    "minimum_nqf",
    "experience_text",
    "experience_min_years",
    "experience_max_years",
    "preferred_experience_text",
    "preferred_experience_min_years",
    "preferred_experience_max_years",
    "source_native_category",
    "source_specific_evidence",
    "description",
    "duties",
    "responsibilities",
    "eligibility",
    "eligibility_text",
    "source_platform",
    "redirect_url",
    "primary_apply_url",
    "application_deadline",
    "ai_extraction__id",
    "ai_extraction__opportunity_id",
    "ai_extraction__status",
    "ai_extraction__source_completeness",
    "employer_vacancy__id",
    "employer_vacancy__opportunity_id",
    "employer_vacancy__published_input_hash",
    "employer_vacancy__published_requirements",
)


def _matching_opportunity_queryset(queryset):
    """Load only the catalogue data consumed by matching and its card view."""
    from django.db.models import Prefetch
    from ingestion.models import OpportunityCriterion, OpportunitySourceRecord

    matching_criteria = OpportunityCriterion.objects.filter(
        criterion_type__in=(
            "skill",
            "qualification",
            "experience",
            "certification",
            "language",
            "work_eligibility",
        )
    ).only(
        "id",
        "extraction_id",
        "criterion_type",
        "requirement_level",
        "raw_value",
        "evidence_text",
        "source_section",
        "minimum_years",
        "maximum_years",
        "metadata",
    )
    from core.models import CleanCriterionEvidence, CleanOpportunityCriterion

    revision_fields = (
        "active_clean_revision",
        "active_clean_revision__status",
        "active_clean_revision__schema_version",
        "active_clean_revision__raw_opportunity_id",
        "active_clean_revision__source_record",
        "active_clean_revision__source_record__current_raw_id",
        "active_clean_revision__cleaned_title",
        "active_clean_revision__company",
        "active_clean_revision__location",
        "active_clean_revision__work_arrangement",
        "active_clean_revision__opportunity_kind",
        "active_clean_revision__closing_at",
        "active_clean_revision__source_url",
    )
    clean_criteria = CleanOpportunityCriterion.objects.only(
        "id", "revision", "criterion_type", "normalized_value",
        "raw_value", "requirement_level", "significance",
        "source_section", "qualification", "experience",
    ).prefetch_related(
        Prefetch(
            "evidence",
            queryset=CleanCriterionEvidence.objects.only(
                "id", "criterion", "ordinal", "text"),
        ),
    )
    return queryset.only(
        *OPPORTUNITY_MATCHING_FIELDS, *revision_fields
    ).select_related(
        "ai_extraction", "employer_vacancy",
        "active_clean_revision", "active_clean_revision__source_record",
    ).prefetch_related(
        "required_skills",
        "preferred_skills",
        Prefetch("ai_extraction__criteria", queryset=matching_criteria),
        Prefetch(
            "source_records",
            queryset=OpportunitySourceRecord.objects.select_related(
                "source", "source__greenhouse_board"
            ).order_by("source__priority", "-last_seen_at"),
            to_attr="_application_source_records",
        ),
        Prefetch("active_clean_revision__criteria",
                 queryset=clean_criteria),
    )


def get_matched_opportunities(profile, include_cv_tips=False, queryset=None):
    """Return ranked opportunities with normalization reused for this run only."""
    token = _NORMALIZATION_CACHE.set({})
    try:
        return _get_matched_opportunities(profile, include_cv_tips, queryset)
    finally:
        _NORMALIZATION_CACHE.reset(token)


def _get_matched_opportunities(profile, include_cv_tips=False, queryset=None):
    """Return every discoverable opportunity, ranked by trusted evidence."""
    opportunities_qs = (
        queryset if queryset is not None else Opportunity.objects.professionally_ready()
    )
    opportunities = list(_matching_opportunity_queryset(opportunities_qs))
    # Desired-career cohorts are constructed from persisted, current taxonomy
    # assignments before matching. They never modify the evidence comparison.
    from core.services.opportunity_discovery import discovery_reasons
    provenance_by_opportunity = discovery_reasons(profile, opportunities)
    if not isinstance(provenance_by_opportunity, dict):
        # The discovery surface is retired (list/None returns); career
        # direction derives from confirmed target roles at runtime.
        provenance_by_opportunity = {}
    opportunity_inputs = [
        build_opportunity_matching_input(opportunity)
        for opportunity in opportunities
    ]
    user_input = build_user_matching_profile(
        profile,
        include_certifications=any(
            item.required_certifications or item.preferred_certifications
            for item in opportunity_inputs
        ),
        include_languages=any(
            item.required_languages or item.preferred_languages
            for item in opportunity_inputs
        ),
    )
    taxonomy_context = build_taxonomy_context_for_run(
        user_input, opportunity_inputs
    )
    matching_global_skill_ids = (
        get_matching_global_skill_ids(profile) if include_cv_tips else []
    )
    results = []
    for opportunity, opportunity_input in zip(opportunities, opportunity_inputs):
        match_result = evaluate_match(
            user_input,
            opportunity_input,
            taxonomy_context=taxonomy_context,
        )
        cv_tips = ()
        if include_cv_tips:
            cv_tips = CVtip.objects.filter(
                opportunity_type=opportunity.opportunity_type,
                is_active=True,
                related_skills__id__in=matching_global_skill_ids,
            ).distinct()[:3]
            if not cv_tips:
                cv_tips = CVtip.objects.filter(
                    opportunity_type="general", is_active=True
                )[:3]
        results.append(_result_dictionary(
            opportunity,
            match_result,
            cv_tips,
            user=user_input,
            taxonomy_context=taxonomy_context,
            discovery_provenance=provenance_by_opportunity.get(
                opportunity.id, ()),
        ))

    # Keep this raw evaluator available to matching callers; the user-facing
    # feed applies its admission boundary in core.services.opportunity_feed.
    results.sort(key=_ranking_key, reverse=True)
    return results


# Transitional wrappers retained for older imports. They now delegate to the
# evidence engine rather than the legacy keyword scorer.
def calculate_match_score(
    profile,
    opportunity,
    profile_projection=None,
    user_matching_profile=None,
):
    del profile_projection
    result = match_profile_to_opportunity(
        profile,
        opportunity,
        user_matching_profile=user_matching_profile,
    )
    reasons = [
        item.criterion for item in result.criteria if item.status == MatchStatus.FOUND
    ]
    return result.summary.score, reasons


def get_match_strength(score):
    if score >= 80:
        return "Strong Match"
    if score >= 60:
        return "Good Match"
    if score >= 40:
        return "Partial Match"
    return "Explore"


def build_why_text(matched_reasons, match_strength):
    del match_strength
    if not matched_reasons:
        return "No safely comparable supporting evidence was found on your profile yet."
    return "Found on your profile: " + ", ".join(matched_reasons) + "."


def calculate_completion(profile):
    score, _ = get_profile_progress(profile)
    return score


def get_progress_items(profile):
    _, sections = get_profile_progress(profile)
    return sections


def employer_evidence_match(user, opportunity, taxonomy_context=None):
    """The shared evidence engine, excluding candidate preference ordering."""
    user = replace(user, locations=(), work_arrangement='', target_roles=(), opportunity_kinds=(), industries=())
    if taxonomy_context is None:
        taxonomy_context = build_taxonomy_context_for_run(user, (opportunity,))
    result = evaluate_match(user, opportunity, taxonomy_context=taxonomy_context)
    criteria = tuple(row for row in result.criteria if row.importance != CriterionImportance.PREFERENCE)
    return replace(result, criteria=criteria, summary=_build_summary(criteria, source_completeness=opportunity.source_completeness))
