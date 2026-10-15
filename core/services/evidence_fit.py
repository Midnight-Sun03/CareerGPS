"""Reusable deterministic comparison of confirmed evidence to Clean-Job facts.

This module deliberately has no occupational-admission or career-direction logic.
Block 1 relationship assessments remain the sole occupational authority.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from enum import Enum
from types import SimpleNamespace

from django.db.models import Prefetch

from core.models import (
    Certification, Education, Language, ProfileSkill, ProjectCapability,
    WorkExperience,
)
from core.professional_matching import (
    _criterion_status, _fit_band, _requirement_units, confirmed_evidence,
    criterion_results,
)
from core.taxonomies.normalization import normalize_term


class EvidenceStatus(str, Enum):
    FOUND = 'FOUND'
    RELATED = 'RELATED'
    NOT_FOUND_ON_PROFILE = 'NOT_FOUND_ON_PROFILE'
    UNKNOWN = 'UNKNOWN'


class EvidenceImportance(str, Enum):
    REQUIRED = 'required'
    PREFERRED = 'preferred'


class EvidenceKind(str, Enum):
    SKILL = 'skill'
    QUALIFICATION = 'qualification'
    EXPERIENCE = 'experience'
    CERTIFICATION = 'certification'
    LICENCE = 'licence'
    LANGUAGE = 'language'
    ELIGIBILITY = 'eligibility'
    OTHER = 'other'


class EvidenceSignificance(str, Enum):
    CRUCIAL = 'crucial'
    CORE = 'core'
    SUPPORTING = 'supporting'


@dataclass(frozen=True)
class EducationEvidence:
    qualification: str
    field_of_study: str
    nqf_level: int | None

    @property
    def display(self):
        value = ' - '.join(filter(None, (self.qualification, self.field_of_study)))
        return f'{value} (NQF {self.nqf_level})' if self.nqf_level is not None else value


@dataclass(frozen=True)
class WorkEvidence:
    title: str
    organisation: str
    start_date: date | None
    end_date: date | None
    currently_working: bool

    @property
    def display(self):
        return self.title or 'Work experience'


@dataclass(frozen=True)
class CandidateEvidence:
    skills: tuple[str, ...]
    education: tuple[EducationEvidence, ...]
    work_experiences: tuple[WorkEvidence, ...]
    certifications: tuple[str, ...]
    languages: tuple[str, ...]
    experience_months: int | None

    @property
    def target_roles(self):
        """Employer evidence comparison intentionally excludes private preferences."""
        return ()

    @property
    def locations(self):
        return ()

    @property
    def work_arrangement(self):
        return ''

    @property
    def opportunity_kinds(self):
        return ()

    @property
    def industries(self):
        return ()


@dataclass(frozen=True)
class EvidenceCriterion:
    key: str
    label: str
    evidence_type: EvidenceKind
    importance: EvidenceImportance
    status: EvidenceStatus
    criterion: str
    profile_evidence: tuple[str, ...]
    explanation: str
    significance: EvidenceSignificance
    source_section: str = ''

    @property
    def css_status(self):
        return self.status.value.casefold().replace('_', '-')


@dataclass(frozen=True)
class EvidenceFitSummary:
    score: object
    label: str
    found_count: int
    related_count: int
    not_found_count: int
    unknown_count: int
    comparable_count: int
    total_count: int
    explanation: str
    band: str
    major_gap_count: int


@dataclass(frozen=True)
class EvidenceFitResult:
    user: CandidateEvidence
    criteria: tuple[EvidenceCriterion, ...]
    summary: EvidenceFitSummary

    @property
    def required_results(self):
        return tuple(row for row in self.criteria if row.importance == EvidenceImportance.REQUIRED)

    @property
    def preferred_results(self):
        return tuple(row for row in self.criteria if row.importance == EvidenceImportance.PREFERRED)

    @property
    def preference_results(self):
        return ()


def prefetch_confirmed_evidence(queryset):
    """Bounded evidence loading for employer discovery batches."""
    relations = (
        ('profile_skills', ProfileSkill, ('entered_name', 'normalized_name')),
        ('education_records', Education, ('qualification', 'field_of_study', 'nqf_level')),
        ('work_experiences', WorkExperience, (
            'title', 'organisation', 'description', 'bullets', 'start_date', 'end_date',
            'currently_working')),
        ('certifications', Certification, ('title', 'description')),
        ('languages', Language, ('language', 'proficiency')),
    )
    rows = [
        Prefetch(
            relation,
            queryset=model.objects.filter(confirmed_by_user=True).only('profile_id', *fields),
            to_attr='_evidence_' + relation,
        )
        for relation, model, fields in relations
    ]
    return queryset.prefetch_related(*rows)


def attach_project_capability_evidence(profiles):
    """Attach confirmed project skills with one query for the whole batch."""
    profiles = list(profiles)
    by_profile = {profile.pk: [] for profile in profiles}
    for row in ProjectCapability.objects.filter(
            project__profile_id__in=by_profile,
            project__confirmed_by_user=True,
            confirmed_by_user=True).values(
                'pk', 'project__profile_id', 'entered_name', 'normalized_name'):
        by_profile[row['project__profile_id']].append(row)
    for profile in profiles:
        profile._evidence_project_capabilities = by_profile[profile.pk]
    return profiles


def merged_experience_days(rows, today):
    intervals = []
    for row in rows:
        end = today if row.currently_working else row.end_date
        if row.start_date and end and row.start_date <= end and row.start_date <= today:
            intervals.append((row.start_date, min(end, today)))
    if not intervals:
        return None
    intervals.sort()
    merged = []
    for start, end in intervals:
        if not merged or start > merged[-1][1] + timedelta(days=1):
            merged.append([start, end])
        else:
            merged[-1][1] = max(merged[-1][1], end)
    return sum((end - start).days + 1 for start, end in merged)


def _rows(profile, relation):
    cached = getattr(profile, '_evidence_' + relation, None)
    if cached is not None:
        return cached
    return list(getattr(profile, relation).filter(confirmed_by_user=True))


def candidate_evidence(profile, *, today=None):
    today = today or date.today()
    project_skills = [row['entered_name'].strip()
                      for row in getattr(profile, '_evidence_project_capabilities', ())
                      if row['entered_name'].strip()]
    skills = tuple(dict.fromkeys([
        row.entered_name.strip() for row in _rows(profile, 'profile_skills')
        if row.entered_name.strip()] + project_skills))
    education = tuple(EducationEvidence(
        row.qualification, row.field_of_study, row.nqf_level)
        for row in _rows(profile, 'education_records'))
    work_rows = _rows(profile, 'work_experiences')
    work = tuple(WorkEvidence(
        row.title, row.organisation, row.start_date, row.end_date,
        row.currently_working) for row in work_rows)
    days = merged_experience_days(work_rows, today)
    certifications = tuple(dict.fromkeys(
        row.title.strip() for row in _rows(profile, 'certifications') if row.title.strip()))
    languages = tuple(dict.fromkeys(
        row.language.strip() for row in _rows(profile, 'languages') if row.language.strip()))
    return CandidateEvidence(
        skills, education, work, certifications, languages,
        None if days is None else days * 12 // 365,
    )


def _criterion_explanation(status, kind):
    label = kind.value.replace('_', ' ')
    if status == EvidenceStatus.FOUND:
        return f'Confirmed profile evidence supports this {label} requirement.'
    if status == EvidenceStatus.RELATED:
        return f'Confirmed profile evidence has a trusted related {label} connection.'
    if status == EvidenceStatus.NOT_FOUND_ON_PROFILE:
        return f'No supporting {label} evidence was found in the confirmed profile.'
    return f'The profile has no confirmed {label} evidence that CareerGPS can compare safely.'


def compare_profile_to_revision(profile, revision):
    """Compare evidence only; never decides occupational relevance or eligibility."""
    if not hasattr(revision, '_evidence_criteria'):
        revision._evidence_criteria = tuple(
            revision.criteria.select_related('requirement_group').order_by('pk'))
    raw_rows = criterion_results(profile, revision, confirmed_evidence(profile))
    fit = _fit_band(_requirement_units(raw_rows))
    source = {row.criterion_id: row for row in revision._evidence_criteria}
    criteria = []
    for row in raw_rows:
        kind = EvidenceKind(row['criterion_type'].casefold())
        status = EvidenceStatus(row['status'])
        evidence = tuple(dict.fromkeys(
            item.get('label', '') for item in row['profile_evidence']
            if item.get('label')))
        criteria.append(EvidenceCriterion(
            key=row['criterion_id'],
            label=kind.value.replace('_', ' ').title(),
            evidence_type=kind,
            importance=EvidenceImportance(row['requirement_level'].casefold()),
            status=status,
            criterion=row['raw_value'],
            profile_evidence=evidence,
            explanation=_criterion_explanation(status, kind),
            significance=EvidenceSignificance(row['significance'].casefold()),
            source_section=getattr(source.get(row['criterion_id']), 'source_section', ''),
        ))
    counts = {status: sum(row.status == status for row in criteria) for status in EvidenceStatus}
    labels = {
        'A': 'Strong current evidence', 'B': 'Good current evidence',
        'C': 'Some current evidence', 'E': 'Major evidence gap',
        'D': 'Limited current evidence', 'F': 'Limited current evidence',
    }
    summary = EvidenceFitSummary(
        score=fit['score'], label=labels[fit['band']],
        found_count=counts[EvidenceStatus.FOUND],
        related_count=counts[EvidenceStatus.RELATED],
        not_found_count=counts[EvidenceStatus.NOT_FOUND_ON_PROFILE],
        unknown_count=counts[EvidenceStatus.UNKNOWN],
        comparable_count=len(criteria) - counts[EvidenceStatus.UNKNOWN],
        total_count=len(criteria), band=fit['band'],
        major_gap_count=fit['major_gaps'],
        explanation=(
            'This compares confirmed candidate evidence with validated Clean-Job '
            'requirements. It is not an occupational match or hiring decision.'),
    )
    return EvidenceFitResult(candidate_evidence(profile), tuple(criteria), summary)


def evidence_schema(revision):
    """Return stable Clean-Job criterion descriptors without candidate data."""
    rows = []
    if not hasattr(revision, '_evidence_criteria'):
        revision._evidence_criteria = tuple(
            revision.criteria.select_related('requirement_group').order_by('pk'))
    for criterion in revision._evidence_criteria:
        kind = EvidenceKind(criterion.criterion_type.casefold())
        rows.append(EvidenceCriterion(
            key=criterion.criterion_id,
            label=kind.value.replace('_', ' ').title(),
            evidence_type=kind,
            importance=EvidenceImportance(criterion.requirement_level.casefold()),
            status=EvidenceStatus.UNKNOWN,
            criterion=criterion.raw_value,
            profile_evidence=(),
            explanation=_criterion_explanation(EvidenceStatus.UNKNOWN, kind),
            significance=EvidenceSignificance(criterion.significance.casefold()),
            source_section=criterion.source_section,
        ))
    return tuple(rows)


def compare_market_requirement(profile, kind, label):
    """Use the same evidence comparator for a persisted market requirement."""
    mapped = {
        'skill': 'SKILL', 'qualification': 'QUALIFICATION',
        'experience': 'EXPERIENCE', 'certification': 'CERTIFICATION',
        'licence': 'LICENCE', 'language': 'LANGUAGE',
        'eligibility': 'ELIGIBILITY', 'other': 'OTHER',
    }.get(kind)
    if mapped is None:
        return EvidenceStatus.UNKNOWN, (), 'CareerGPS cannot safely compare this requirement.'
    criterion = SimpleNamespace(
        criterion_type=mapped, normalized_value=normalize_term(label),
        raw_value=label, qualification=None, experience=None, language=None,
    )
    state, evidence = _criterion_status(criterion, confirmed_evidence(profile))
    status = EvidenceStatus(state)
    labels = tuple(item.get('label', '') for item in evidence if item.get('label'))
    return status, labels, _criterion_explanation(status, EvidenceKind(mapped.casefold()))
