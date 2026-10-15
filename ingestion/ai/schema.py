from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


CriterionType = Literal[
    "skill",
    "qualification",
    "certification",
    "experience",
    "language",
    "work_eligibility",
]
RequirementLevel = Literal["required", "preferred", "unknown"]


class SemanticCriterion(BaseModel):
    model_config = ConfigDict(extra="forbid")

    """One language-dependent fact Gemini adds to CareerGPS's code extraction.

    The provider schema is intentionally simpler than this local Pydantic model.
    CareerGPS performs the final validation here after Gemini returns JSON.
    """

    criterion_type: CriterionType = Field(
        description=(
            "Requirement category as framed by the employer. Do not convert an "
            "employer-stated experience requirement into a skill merely because "
            "the experience concerns a tool or technology."
        )
    )
    value: str = Field(
        min_length=1,
        description=(
            "Concise requirement value/subject. For experience, use the subject/context "
            "the employer requires experience in; preserve OR/equivalent wording when material."
        ),
    )
    requirement_level: RequirementLevel
    evidence_text: str = Field(
        min_length=1,
        description="Verbatim supporting quote from the supplied CareerGPS opportunity information.",
    )
    source_section: str = Field(
        default="",
        description="CareerGPS input section containing the supporting quote.",
    )
    minimum_years: int | None = Field(default=None, ge=0, le=80)
    maximum_years: int | None = Field(default=None, ge=0, le=80)
    context: str = Field(
        default="",
        description="Experience/domain context when relevant; otherwise empty string.",
    )

    @model_validator(mode="after")
    def validate_semantics(self):
        if (
            self.minimum_years is not None
            and self.maximum_years is not None
            and self.maximum_years < self.minimum_years
        ):
            raise ValueError("maximum_years cannot be lower than minimum_years")
        if self.criterion_type != "experience" and (
            self.minimum_years is not None or self.maximum_years is not None
        ):
            raise ValueError("minimum_years/maximum_years are only valid for experience criteria")
        return self


class OpportunityGapUnderstanding(BaseModel):
    model_config = ConfigDict(extra="forbid")

    """Semantic gaps AI understood for one opportunity.

    Gemini does NOT recreate title/company/location/salary/dates or decide match
    suitability. Those remain deterministic CareerGPS data/business logic.
    """

    opportunity_id: int
    criteria: list[SemanticCriterion] = Field(default_factory=list, max_length=100)


class OpportunityUnderstandingBatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    """Compatibility wrapper used by tests/services; provider calls are one job at a time."""

    opportunities: list[OpportunityGapUnderstanding]
