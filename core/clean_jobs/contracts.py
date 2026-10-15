from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


CLEAN_SCHEMA_VERSION = '1.1'

NORMALIZED_VALUE_POLICY = (
    'A concise, source-faithful, human-readable employer criterion. It is not '
    'a taxonomy label or code, occupational enrichment, a guessed requirement, '
    'or a candidate-specific rewrite. Structured qualification, experience, '
    'and language details belong in their dedicated objects.'
)


class StrictModel(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)


class Cleaner(StrictModel):
    name: str = Field(min_length=1, max_length=120)
    version: str = Field(min_length=1, max_length=60)
    cleaned_at: datetime
    run_id: str = Field(min_length=1, max_length=120)


class Source(StrictModel):
    source_name: str = Field(min_length=1, max_length=120)
    external_opportunity_id: str = Field(min_length=1, max_length=255)
    source_url: str = Field(default='', max_length=2000)
    captured_at: datetime
    source_input_hash: str = Field(pattern=r'^[0-9a-f]{64}$')


class Location(StrictModel):
    raw: str | None = None
    city: str | None = None
    region: str | None = None
    country: str | None = None


class Salary(StrictModel):
    raw: str = Field(min_length=1)
    currency: str | None = Field(default=None, min_length=3, max_length=3)
    minimum: Decimal | None = Field(default=None, ge=0)
    maximum: Decimal | None = Field(default=None, ge=0)
    period: Literal['HOUR', 'DAY', 'WEEK', 'MONTH', 'YEAR', 'PROJECT', 'OTHER', 'UNKNOWN']
    # ``None`` means the source does not state whether the compensation is
    # negotiable.  False is reserved for an explicit fixed/non-negotiable
    # statement; silence must never be converted to False.
    is_negotiable: bool | None

    @model_validator(mode='after')
    def salary_bounds(self):
        if self.minimum is not None and self.maximum is not None and self.maximum < self.minimum:
            raise ValueError('salary.maximum cannot be lower than salary.minimum')
        if (self.minimum is not None or self.maximum is not None) and not self.currency:
            raise ValueError('salary.currency is required when a numeric bound is present')
        return self


class Evidence(StrictModel):
    text: str = Field(min_length=1)
    start_char: int | None = Field(default=None, ge=0)
    end_char: int | None = Field(default=None, ge=1)

    @model_validator(mode='after')
    def offsets(self):
        if (self.start_char is None) != (self.end_char is None):
            raise ValueError('evidence offsets must both be null or both be populated')
        if self.start_char is not None and self.end_char <= self.start_char:
            raise ValueError('evidence.end_char must be greater than start_char')
        return self


class SourceReference(StrictModel):
    """A compact, auditable reference to one exact exported source value."""
    source_section: str = Field(min_length=1, max_length=200)
    evidence: Evidence


class JobProvenance(StrictModel):
    """Source support for material job facts without duplicating whole adverts.

    A known/populated fact needs at least one reference. Unknown/null facts may
    use an empty list. Semantic cleaning remains the cleaner's responsibility;
    the importer verifies that every cited value exists in the exact export and
    applies deterministic linkage checks for textual job facts.
    """
    cleaned_title: list[SourceReference] = Field(min_length=1)
    company: list[SourceReference] = Field(min_length=1)
    location: list[SourceReference] = Field(default_factory=list)
    employment_type: list[SourceReference] = Field(default_factory=list)
    work_arrangement: list[SourceReference] = Field(default_factory=list)
    opportunity_kind: list[SourceReference] = Field(min_length=1)
    posted_at: list[SourceReference] = Field(default_factory=list)
    closing_at: list[SourceReference] = Field(default_factory=list)
    salary: list[SourceReference] = Field(default_factory=list)
    cleaned_description: list[SourceReference] = Field(min_length=1)


class Seniority(StrictModel):
    label: Literal['NON_SENIOR', 'OBVIOUS_SENIOR', 'UNCERTAIN']
    reason: str = Field(min_length=1)
    evidence: list[Evidence] = Field(min_length=1)


def _date_string(value: str | None) -> str | None:
    if value is None:
        return None
    value = value.strip()
    if not value:
        raise ValueError('date value cannot be blank')
    try:
        if 'T' in value:
            datetime.fromisoformat(value.replace('Z', '+00:00'))
        else:
            date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError('must be an ISO date or datetime preserving source precision') from exc
    return value


class Job(StrictModel):
    cleaned_title: str = Field(min_length=1, max_length=300)
    company: str = Field(min_length=1, max_length=300)
    location: Location
    employment_type: list[Literal[
        'FULL_TIME', 'PART_TIME', 'CONTRACT', 'TEMPORARY', 'INTERNSHIP',
        'LEARNERSHIP', 'APPRENTICESHIP', 'CASUAL', 'VOLUNTEER', 'OTHER', 'UNKNOWN',
    ]] = Field(min_length=1)
    work_arrangement: Literal['ONSITE', 'HYBRID', 'REMOTE', 'FIELD_BASED', 'OTHER', 'UNKNOWN']
    opportunity_kind: Literal[
        'VACANCY', 'INTERNSHIP', 'LEARNERSHIP', 'APPRENTICESHIP',
        'GRADUATE_PROGRAMME', 'BURSARY', 'OTHER',
    ]
    posted_at: str | None
    closing_at: str | None
    salary: Salary | None
    cleaned_description: str = Field(min_length=1)
    seniority: Seniority
    provenance: JobProvenance

    _posted = field_validator('posted_at')(_date_string)
    _closing = field_validator('closing_at')(_date_string)

    @field_validator('employment_type')
    @classmethod
    def employment_values_are_unique(cls, value):
        if len(value) != len(set(value)):
            raise ValueError('employment_type values must be unique')
        if 'UNKNOWN' in value and len(value) != 1:
            raise ValueError('UNKNOWN cannot be combined with known employment types')
        return value

    @model_validator(mode='after')
    def populated_facts_have_provenance(self):
        checks = {
            'location': any(value not in (None, '') for value in self.location.model_dump().values()),
            'employment_type': self.employment_type != ['UNKNOWN'],
            'work_arrangement': self.work_arrangement != 'UNKNOWN',
            'posted_at': self.posted_at is not None,
            'closing_at': self.closing_at is not None,
            'salary': self.salary is not None,
        }
        for name, populated in checks.items():
            if populated and not getattr(self.provenance, name):
                raise ValueError(f'job.provenance.{name} is required for a known/populated fact')
        return self


class RequirementGroup(StrictModel):
    group_id: str = Field(min_length=1, max_length=100)
    operator: Literal['OR']


class Qualification(StrictModel):
    level: Literal[
        'CERTIFICATE', 'DIPLOMA', 'BACHELORS', 'HONOURS',
        'POSTGRADUATE_DIPLOMA', 'MASTERS', 'DOCTORATE', 'OTHER', 'UNKNOWN',
    ]
    fields: list[str] = Field(default_factory=list)
    nqf_level: int | None = Field(default=None, ge=1, le=10)


class Experience(StrictModel):
    minimum_months: int | None = Field(default=None, ge=0, le=960)
    maximum_months: int | None = Field(default=None, ge=0, le=960)
    context: str = Field(min_length=1)

    @model_validator(mode='after')
    def bounds(self):
        if (self.minimum_months is not None and self.maximum_months is not None
                and self.maximum_months < self.minimum_months):
            raise ValueError('experience.maximum_months cannot be lower than minimum_months')
        return self


class Language(StrictModel):
    name: str = Field(min_length=1)
    proficiency: Literal['BASIC', 'INTERMEDIATE', 'PROFICIENT', 'FLUENT', 'NATIVE', 'UNKNOWN']


class Criterion(StrictModel):
    criterion_id: str = Field(min_length=1, max_length=100)
    criterion_type: Literal[
        'SKILL', 'QUALIFICATION', 'EXPERIENCE', 'CERTIFICATION',
        'LICENCE', 'LANGUAGE', 'ELIGIBILITY', 'OTHER',
    ]
    normalized_value: str = Field(min_length=1, max_length=200)
    raw_value: str = Field(min_length=1)
    requirement_level: Literal['REQUIRED', 'PREFERRED', 'INFORMATIONAL', 'UNKNOWN']
    significance: Literal['CRUCIAL', 'CORE', 'SUPPORTING', 'UNKNOWN']
    source_section: str = Field(min_length=1, max_length=200)
    requirement_group: RequirementGroup | None = None
    evidence: list[Evidence] = Field(min_length=1)
    qualification: Qualification | None = None
    experience: Experience | None = None
    language: Language | None = None

    @field_validator('normalized_value')
    @classmethod
    def normalized_value_is_concise(cls, value):
        if '\n' in value or '\r' in value:
            raise ValueError('normalized_value must be a single line')
        if len(value.split()) > 24:
            raise ValueError('normalized_value must be concise (at most 24 words)')
        return value

    @model_validator(mode='after')
    def structured_object_compatibility(self):
        expected = {
            'QUALIFICATION': 'qualification',
            'EXPERIENCE': 'experience',
            'LANGUAGE': 'language',
        }.get(self.criterion_type)
        values = {
            'qualification': self.qualification,
            'experience': self.experience,
            'language': self.language,
        }
        for name, value in values.items():
            if name == expected and value is None:
                raise ValueError(f'{name} must be populated for {self.criterion_type}')
            if name != expected and value is not None:
                raise ValueError(f'{name} must be null for {self.criterion_type}')
        return self


class CleanJobPayload(StrictModel):
    schema_version: Literal['1.1']
    cleaner: Cleaner
    source: Source
    job: Job
    criteria: list[Criterion] = Field(default_factory=list, max_length=500)

    @model_validator(mode='after')
    def unique_ids_and_valid_or_groups(self):
        ids = [row.criterion_id for row in self.criteria]
        if len(ids) != len(set(ids)):
            raise ValueError('criterion_id values must be unique within a revision')
        groups: dict[str, list[Criterion]] = {}
        for criterion in self.criteria:
            if criterion.requirement_group:
                groups.setdefault(criterion.requirement_group.group_id, []).append(criterion)
        for group_id, members in groups.items():
            if len(members) < 2:
                raise ValueError(f'OR group {group_id!r} must contain at least two alternatives')
            if len({row.requirement_group.operator for row in members}) != 1:
                raise ValueError(f'OR group {group_id!r} has inconsistent operators')
            if len({row.requirement_level for row in members}) != 1:
                raise ValueError(f'OR group {group_id!r} has inconsistent requirement levels')
            if len({row.significance for row in members}) != 1:
                raise ValueError(f'OR group {group_id!r} has inconsistent significance')
        return self
