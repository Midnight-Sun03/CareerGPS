from pathlib import Path
import shutil
from datetime import datetime
import textwrap

ROOT = Path(__file__).resolve().parent
INTERVIEW = ROOT / "interview"

stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
backup = ROOT / f"interview_before_rebuild_{stamp}"

if not INTERVIEW.exists():
    raise SystemExit("ERROR: interview/ folder was not found beside manage.py.")

shutil.copytree(INTERVIEW, backup)
print(f"Backup created: {backup.name}")


def write(relative_path, content):
    path = ROOT / relative_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        textwrap.dedent(content).lstrip("\n"),
        encoding="utf-8",
    )
    print(f"WROTE: {relative_path}")


write("interview/models.py", r'''
from django.conf import settings
from django.db import models


class Interview(models.Model):
    MODE_CHOICES = [
        ("written", "Written"),
        ("voice", "Voice"),
    ]

    SOURCE_CHOICES = [
        ("saved", "Saved opportunity"),
        ("application", "Application workspace"),
    ]

    STATUS_CHOICES = [
        ("setup", "Setup"),
        ("active", "Active"),
        ("completed", "Completed"),
        ("failed", "Failed"),
    ]

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="interviews",
    )

    opportunity = models.ForeignKey(
        "core.Opportunity",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="interview_sessions",
    )

    application = models.ForeignKey(
        "applications.Application",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="interview_sessions",
    )

    source_type = models.CharField(
        max_length=20,
        choices=SOURCE_CHOICES,
        blank=True,
    )

    mode = models.CharField(
        max_length=20,
        choices=MODE_CHOICES,
        default="written",
    )

    question_count = models.PositiveSmallIntegerField(default=5)

    job_info_status = models.CharField(
        max_length=30,
        blank=True,
    )

    context_snapshot = models.JSONField(
        default=dict,
        blank=True,
    )

    candidate_fact_ids = models.JSONField(
        default=list,
        blank=True,
    )

    generated_document_ids = models.JSONField(
        default=list,
        blank=True,
    )

    feedback_data = models.JSONField(
        default=dict,
        blank=True,
    )

    ai_model_name = models.CharField(
        max_length=120,
        blank=True,
    )

    error_message = models.TextField(blank=True)

    popia_consent = models.BooleanField(default=False)

    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default="setup",
    )

    # ---------------------------------------------------------
    # Legacy prototype compatibility fields.
    # They remain temporarily so old rows are not destroyed.
    # The rebuilt flow does NOT use them for interview logic.
    # ---------------------------------------------------------

    occupation = models.CharField(
        max_length=255,
        blank=True,
        default="",
    )

    experience_level = models.CharField(
        max_length=20,
        blank=True,
        default="",
    )

    cv_text = models.TextField(
        blank=True,
        null=True,
    )

    overall_presence_score = models.FloatField(
        null=True,
        blank=True,
    )
    eye_contact_score = models.FloatField(
        null=True,
        blank=True,
    )
    posture_score = models.FloatField(
        null=True,
        blank=True,
    )
    gesture_score = models.FloatField(
        null=True,
        blank=True,
    )

    communication_score = models.FloatField(
        null=True,
        blank=True,
    )
    confidence_score = models.FloatField(
        null=True,
        blank=True,
    )
    structure_score = models.FloatField(
        null=True,
        blank=True,
    )
    relevance_score = models.FloatField(
        null=True,
        blank=True,
    )

    strengths = models.JSONField(
        default=list,
        blank=True,
    )
    improvements = models.JSONField(
        default=list,
        blank=True,
    )
    feedback_summary = models.TextField(
        blank=True,
        null=True,
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at", "-id"]

    def __str__(self):
        if self.opportunity_id:
            title = self.opportunity.title
        else:
            title = self.occupation or "Interview"

        return (
            f"{self.user_id} - {title} "
            f"({self.mode}/{self.status})"
        )


class InterviewQuestion(models.Model):
    QUESTION_TYPE_CHOICES = [
        ("opening", "Opening"),
        ("motivation", "Motivation"),
        ("experience", "Experience"),
        ("behavioural", "Behavioural"),
        ("situational", "Situational"),
        ("role_specific", "Role specific"),
    ]

    ANSWER_MODE_CHOICES = [
        ("written", "Written"),
        ("voice", "Voice"),
    ]

    interview = models.ForeignKey(
        Interview,
        on_delete=models.CASCADE,
        related_name="questions",
    )

    question_index = models.PositiveSmallIntegerField()

    question_text = models.TextField()

    question_type = models.CharField(
        max_length=30,
        choices=QUESTION_TYPE_CHOICES,
        default="behavioural",
    )

    focus = models.CharField(
        max_length=255,
        blank=True,
    )

    answer_mode = models.CharField(
        max_length=20,
        choices=ANSWER_MODE_CHOICES,
        default="written",
    )

    answer_text = models.TextField(
        blank=True,
        null=True,
    )

    audio_file = models.FileField(
        upload_to="interviews/audio/",
        blank=True,
        null=True,
    )

    delivery_metrics = models.JSONField(
        default=dict,
        blank=True,
    )

    evaluation = models.JSONField(
        default=dict,
        blank=True,
    )

    # Legacy prototype fields.
    # Not used by the rebuilt interview engine.

    eye_contact_score = models.FloatField(
        null=True,
        blank=True,
    )
    posture_score = models.FloatField(
        null=True,
        blank=True,
    )
    gesture_score = models.FloatField(
        null=True,
        blank=True,
    )
    presence_score = models.FloatField(
        null=True,
        blank=True,
    )

    video_file = models.FileField(
        upload_to="interviews/video/",
        blank=True,
        null=True,
    )

    answer_feedback = models.TextField(
        blank=True,
        null=True,
    )

    answer_strengths = models.JSONField(
        default=list,
        blank=True,
    )

    answer_improvements = models.JSONField(
        default=list,
        blank=True,
    )

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["question_index", "id"]

    def __str__(self):
        return (
            f"Q{self.question_index + 1}: "
            f"{self.question_text[:60]}"
        )
''')


write("interview/services/__init__.py", r'''
''')


write("interview/services/context.py", r'''
from __future__ import annotations

import copy

from applications.models import Application, GeneratedDocument
from applications.services.candidate_facts import (
    build_candidate_fact_bundle,
)
from applications.services.job_context import resolve_job_context
from core.models import Profile, SavedOpportunity


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
        .select_related("opportunity")
        .order_by("-updated_at", "-id")
    )

    application_opportunity_ids = {
        application.opportunity_id
        for application in applications
    }

    saved_opportunities = list(
        SavedOpportunity.objects.filter(user=user)
        .exclude(
            opportunity_id__in=application_opportunity_ids
        )
        .select_related("opportunity")
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
        .select_related("opportunity")
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
        )
        .select_related("opportunity")
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

    job_context = resolve_job_context(opportunity)

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
''')


write("interview/services/delivery_metrics.py", r'''
from __future__ import annotations

import re


FILLER_PATTERNS = {
    "um": r"\bum+\b",
    "uh": r"\buh+\b",
    "erm": r"\berm+\b",
    "you know": r"\byou\s+know\b",
}


def analyse_delivery(
    transcript,
    *,
    duration_seconds=0,
    pause_count=0,
    longest_pause_seconds=0,
):
    """
    Calculate only observable delivery measurements.

    No confidence/personality/body-language score is inferred.
    """

    text = str(transcript or "").strip()

    words = re.findall(
        r"\b[\w'-]+\b",
        text,
        flags=re.UNICODE,
    )

    duration = max(
        float(duration_seconds or 0),
        0.0,
    )

    fillers = {}

    lowered = text.casefold()

    for label, pattern in FILLER_PATTERNS.items():
        count = len(
            re.findall(
                pattern,
                lowered,
                flags=re.IGNORECASE,
            )
        )

        if count:
            fillers[label] = count

    if duration > 0:
        words_per_minute = round(
            (len(words) / duration) * 60,
            1,
        )
    else:
        words_per_minute = None

    return {
        "duration_seconds": round(duration, 1),
        "word_count": len(words),
        "words_per_minute": words_per_minute,
        "pause_count": max(
            int(pause_count or 0),
            0,
        ),
        "longest_pause_seconds": round(
            max(
                float(longest_pause_seconds or 0),
                0.0,
            ),
            1,
        ),
        "filler_word_count": sum(
            fillers.values()
        ),
        "filler_words": fillers,
    }
''')


write("interview/services/ai.py", r'''
from __future__ import annotations

import json
import os
from typing import Literal

from django.conf import settings
from pydantic import BaseModel, ConfigDict, Field

from ingestion.ai.extractor import _should_try_fallback


class InterviewAIUnavailable(RuntimeError):
    pass


class InterviewQuestionPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question: str

    question_type: Literal[
        "opening",
        "motivation",
        "experience",
        "behavioural",
        "situational",
        "role_specific",
    ]

    focus: str = ""


class InterviewPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    questions: list[InterviewQuestionPlan]


class QuestionEvaluation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question_index: int

    feedback: str

    strengths: list[str] = Field(
        default_factory=list
    )

    improvements: list[str] = Field(
        default_factory=list
    )

    better_approach: str = ""

    relevance: Literal[
        "strong",
        "adequate",
        "needs_work",
    ]

    structure: Literal[
        "strong",
        "adequate",
        "needs_work",
    ]

    specificity: Literal[
        "strong",
        "adequate",
        "needs_work",
    ]


class InterviewEvaluation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    overall_summary: str

    strengths: list[str] = Field(
        default_factory=list
    )

    improvements: list[str] = Field(
        default_factory=list
    )

    question_feedback: list[QuestionEvaluation]


QUESTION_SCHEMA = {
    "type": "object",
    "properties": {
        "questions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "question": {
                        "type": "string"
                    },
                    "question_type": {
                        "type": "string",
                        "enum": [
                            "opening",
                            "motivation",
                            "experience",
                            "behavioural",
                            "situational",
                            "role_specific",
                        ],
                    },
                    "focus": {
                        "type": "string"
                    },
                },
                "required": [
                    "question",
                    "question_type",
                    "focus",
                ],
            },
        },
    },
    "required": [
        "questions",
    ],
}


EVALUATION_SCHEMA = {
    "type": "object",
    "properties": {
        "overall_summary": {
            "type": "string"
        },

        "strengths": {
            "type": "array",
            "items": {
                "type": "string"
            },
        },

        "improvements": {
            "type": "array",
            "items": {
                "type": "string"
            },
        },

        "question_feedback": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "question_index": {
                        "type": "integer"
                    },

                    "feedback": {
                        "type": "string"
                    },

                    "strengths": {
                        "type": "array",
                        "items": {
                            "type": "string"
                        },
                    },

                    "improvements": {
                        "type": "array",
                        "items": {
                            "type": "string"
                        },
                    },

                    "better_approach": {
                        "type": "string"
                    },

                    "relevance": {
                        "type": "string",
                        "enum": [
                            "strong",
                            "adequate",
                            "needs_work",
                        ],
                    },

                    "structure": {
                        "type": "string",
                        "enum": [
                            "strong",
                            "adequate",
                            "needs_work",
                        ],
                    },

                    "specificity": {
                        "type": "string",
                        "enum": [
                            "strong",
                            "adequate",
                            "needs_work",
                        ],
                    },
                },

                "required": [
                    "question_index",
                    "feedback",
                    "strengths",
                    "improvements",
                    "better_approach",
                    "relevance",
                    "structure",
                    "specificity",
                ],
            },
        },
    },

    "required": [
        "overall_summary",
        "strengths",
        "improvements",
        "question_feedback",
    ],
}


INTERVIEW_SYSTEM = """
You are CareerGPS's AI interview engine.

Act as a hiring interviewer, not an examiner or lecturer.

CareerGPS provides two authoritative factual worlds:

1. JOB INFORMATION
Trusted information describing the employer's vacancy.

2. VERIFIED CANDIDATE FACTS
Information that the candidate has confirmed in CareerGPS.

APPLICATION MATERIALS, including a CareerGPS-generated
CV or cover letter, are supporting interview context only.
They show how the candidate presented their verified
background for this vacancy.

APPLICATION MATERIALS MUST NOT create new candidate facts.

Never invent or upgrade:
- candidate experience;
- employers;
- responsibilities;
- experience duration;
- qualifications;
- institutions;
- skills;
- certifications;
- licences;
- projects;
- achievements;
- leadership;
- dates;
- metrics;
- results;
- industry exposure.

Never infer that a candidate possesses a requirement merely
because the vacancy asks for that requirement.

Adapt the interview to evidence that actually exists.

When relevant work experience exists:
- ask grounded questions about responsibilities;
- ask for examples;
- ask about decisions;
- ask about challenges;
- ask about actions and outcomes.

When relevant projects exist:
- ask about the candidate's contribution;
- decisions;
- challenges;
- reasoning;
- outcomes.

Use education, certifications, achievements and volunteering
where genuinely relevant.

If the candidate has little or no work experience:
- do not repeatedly ask for workplace examples they cannot
  truthfully provide;
- use education;
- projects;
- volunteering;
- activities;
- motivation;
- realistic workplace scenarios.

When the vacancy contains an important area that the candidate
has NOT demonstrated:
- do not imply they already have that experience;
- ask a learning, adaptation, reasoning or situational question.

Example:

BAD:
"Tell me about your chemical-sector logistics experience."

when no such experience is present.

GOOD:
"This role operates in the chemical sector. How would you
approach getting up to speed with product-specific and
transportation requirements in an unfamiliar industry?"

Questions should encourage candidates to:
- explain relevant experience when available;
- provide examples;
- describe decisions;
- discuss challenges;
- explain motivation;
- respond to workplace scenarios;
- relate their background to the vacancy;
- handle legitimate unfamiliar areas;
- demonstrate reasoning.

Do not ask textbook-definition, trivia, academic-recall or
exam-style questions unless that questioning would genuinely
be normal for the occupation.

Use South African context only when genuinely relevant to the
vacancy or occupation.

Do not force B-BBEE, NQF or local-policy questions into every
interview.

Do not use CareerGPS:
- match score;
- feed ranking;
- admission class;
- matched-criteria labels;
- gap-analysis labels.

Return only the requested structured JSON.
""".strip()


EVALUATION_SYSTEM = """
You are CareerGPS's interview feedback engine.

Evaluate the candidate's actual mock-interview responses as a
practical hiring-preparation coach.

Be specific, useful and evidence-grounded.

Do not invent what the candidate said.
Do not invent candidate experience.
Do not invent qualifications, skills, achievements or results.

Do not score or judge the candidate using a CareerGPS match
score, feed ranking, admission class or hidden gap label.

For written responses, evaluate:
- relevance;
- structure;
- specificity;
- quality of the example or reasoning.

For voice responses, CareerGPS may supply measured delivery
observations including:
- response duration;
- word count;
- words per minute;
- observed pauses;
- filler-word counts.

Those are observations only.

Do not turn those measurements into:
- confidence percentages;
- personality scores;
- charisma scores;
- eye-contact scores;
- posture scores;
- gesture scores;
- presence scores.

When giving a better-answer approach, show how the candidate
could structure and strengthen a truthful answer using evidence
they actually possess.

Never coach the candidate to claim missing experience,
qualifications, responsibilities or achievements.

Return only the requested structured JSON.
""".strip()


FRIENDLY_UNAVAILABLE = (
    "CareerGPS couldn't prepare the AI interview right now. "
    "Please try again shortly."
)


def _configured_keys():
    """
    Exact CareerGPS key contract:

    primary:
        GEMINI_API_KEY

    fallback:
        GEMuINI_API_KEY

    Do not rename GEMuINI_API_KEY.
    """

    primary = (
        getattr(
            settings,
            "GEMINI_API_KEY",
            "",
        )
        or os.environ.get(
            "GEMINI_API_KEY",
            "",
        )
    ).strip()

    fallback = (
        getattr(
            settings,
            "GEMuINI_API_KEY",
            "",
        )
        or os.environ.get(
            "GEMuINI_API_KEY",
            "",
        )
    ).strip()

    if fallback == primary:
        fallback = ""

    return primary, fallback


def _model_name():
    return (
        getattr(
            settings,
            "INTERVIEW_AI_MODEL",
            "",
        )
        or getattr(
            settings,
            "DOCUMENT_AI_MODEL",
            "gemini-3.5-flash",
        )
    )


def _thinking_level():
    return (
        getattr(
            settings,
            "INTERVIEW_AI_THINKING_LEVEL",
            "",
        )
        or getattr(
            settings,
            "DOCUMENT_AI_THINKING_LEVEL",
            "medium",
        )
    )


def _timeout_seconds():
    return int(
        getattr(
            settings,
            "INTERVIEW_AI_TIMEOUT_SECONDS",
            0,
        )
        or getattr(
            settings,
            "DOCUMENT_AI_TIMEOUT_SECONDS",
            120,
        )
    )


def _run(
    api_key,
    prompt,
    system_instruction,
    schema,
):
    from google import genai

    with genai.Client(
        api_key=api_key
    ) as client:

        interaction = (
            client.interactions.create(
                model=_model_name(),

                input=prompt,

                system_instruction=(
                    system_instruction
                ),

                response_format={
                    "type": "text",
                    "mime_type": (
                        "application/json"
                    ),
                    "schema": schema,
                },

                generation_config={
                    "thinking_level": (
                        _thinking_level()
                    ),
                },

                timeout=(
                    _timeout_seconds()
                ),
            )
        )

        text = getattr(
            interaction,
            "output_text",
            None,
        )

    if not text:
        raise RuntimeError(
            "Gemini returned an empty "
            "interview response."
        )

    return text


def _provider_interaction(
    prompt,
    system_instruction,
    schema,
):
    primary, fallback = _configured_keys()

    if not primary and not fallback:
        raise InterviewAIUnavailable(
            FRIENDLY_UNAVAILABLE
        )

    # -----------------------------------------
    # Primary key
    # -----------------------------------------

    if primary:
        try:
            return _run(
                primary,
                prompt,
                system_instruction,
                schema,
            )

        except Exception as exc:
            # If this is not a retryable provider
            # availability/quota/runtime failure,
            # don't waste the second key.
            if (
                not fallback
                or not _should_try_fallback(exc)
            ):
                raise InterviewAIUnavailable(
                    FRIENDLY_UNAVAILABLE
                ) from exc

    # -----------------------------------------
    # Exact GEMuINI_API_KEY fallback
    # -----------------------------------------

    if fallback:
        try:
            return _run(
                fallback,
                prompt,
                system_instruction,
                schema,
            )

        except Exception as exc:
            raise InterviewAIUnavailable(
                FRIENDLY_UNAVAILABLE
            ) from exc

    raise InterviewAIUnavailable(
        FRIENDLY_UNAVAILABLE
    )


def generate_interview_plan(
    context_snapshot,
    question_count,
):
    prompt = (
        f"Prepare exactly {question_count} realistic "
        "hiring-interview questions for this candidate "
        "and this vacancy.\n\n"

        "Build the interview from the evidence available. "
        "Do not force a fixed quota of question types. "

        "Use experience-based questions only where candidate "
        "evidence supports them. "

        "Do not provide answers, hints or coaching in the "
        "question plan.\n\n"

        "CAREERGPS INTERVIEW CONTEXT:\n"
        + json.dumps(
            context_snapshot,
            ensure_ascii=False,
            indent=2,
        )
    )

    raw = _provider_interaction(
        prompt,
        INTERVIEW_SYSTEM,
        QUESTION_SCHEMA,
    )

    result = (
        InterviewPlan.model_validate_json(raw)
    )

    if len(result.questions) != int(
        question_count
    ):
        raise InterviewAIUnavailable(
            "CareerGPS couldn't prepare a complete "
            "interview right now. Please try again shortly."
        )

    return result


def evaluate_interview(
    context_snapshot,
    answers,
):
    prompt = (
        "Evaluate this completed mock interview.\n\n"

        "Return feedback for every supplied question. "
        "Use the exact zero-based question_index. "

        "Do not generate arbitrary numeric scores.\n\n"

        "CAREERGPS INTERVIEW CONTEXT:\n"
        + json.dumps(
            context_snapshot,
            ensure_ascii=False,
            indent=2,
        )
        + "\n\nINTERVIEW RESPONSES:\n"
        + json.dumps(
            answers,
            ensure_ascii=False,
            indent=2,
        )
    )

    raw = _provider_interaction(
        prompt,
        EVALUATION_SYSTEM,
        EVALUATION_SCHEMA,
    )

    return (
        InterviewEvaluation.model_validate_json(
            raw
        )
    )
''')


write("interview/views.py", r'''
from __future__ import annotations

import json

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import (
    get_object_or_404,
    redirect,
    render,
)
from django.views.decorators.http import (
    require_http_methods,
    require_POST,
)

from .models import (
    Interview,
    InterviewQuestion,
)
from .services.ai import (
    InterviewAIUnavailable,
    evaluate_interview,
    generate_interview_plan,
)
from .services.context import (
    build_interview_context,
    interview_choices_for_user,
    resolve_user_interview_source,
)
from .services.delivery_metrics import (
    analyse_delivery,
)


ALLOWED_QUESTION_COUNTS = {
    5,
    8,
    10,
}


@login_required
def interview_home(request):
    choices = interview_choices_for_user(
        request.user
    )

    recent = (
        Interview.objects
        .filter(user=request.user)
        .select_related(
            "opportunity",
            "application",
        )
        .order_by(
            "-created_at",
            "-id",
        )[:5]
    )

    return render(
        request,
        "interview/home.html",
        {
            **choices,
            "recent_interviews": recent,
        },
    )


@login_required
def interview_prepare(
    request,
    opportunity_id,
):
    (
        opportunity,
        application,
        source_type,
    ) = resolve_user_interview_source(
        request.user,
        opportunity_id,
    )

    if opportunity is None:
        messages.error(
            request,
            (
                "Choose an opportunity from your "
                "saved opportunities or application "
                "workspaces."
            ),
        )

        return redirect(
            "interview_home"
        )

    return render(
        request,
        "interview/prepare.html",
        {
            "opportunity": opportunity,
            "application": application,
            "source_type": source_type,
        },
    )


@login_required
def interview_setup(request):
    """
    Compatibility route for old Interview links.
    """

    return redirect(
        "interview_home"
    )


@login_required
def interview_live(
    request,
    interview_id,
):
    interview = get_object_or_404(
        Interview.objects.select_related(
            "opportunity",
            "application",
        ),
        id=interview_id,
        user=request.user,
    )

    questions = list(
        interview.questions.values(
            "id",
            "question_index",
            "question_text",
            "question_type",
            "focus",
            "answer_text",
            "delivery_metrics",
        )
    )

    return render(
        request,
        "interview/live.html",
        {
            "interview": interview,
            "questions": questions,
            "questions_json": json.dumps(
                questions
            ),
        },
    )


@login_required
def interview_feedback(
    request,
    interview_id,
):
    interview = get_object_or_404(
        Interview.objects.select_related(
            "opportunity",
            "application",
        ),
        id=interview_id,
        user=request.user,
    )

    questions = (
        interview.questions.all()
    )

    feedback = (
        interview.feedback_data
        or {
            "overall_summary": (
                interview.feedback_summary
                or ""
            ),
            "strengths": (
                interview.strengths
                or []
            ),
            "improvements": (
                interview.improvements
                or []
            ),
        }
    )

    return render(
        request,
        "interview/feedback.html",
        {
            "interview": interview,
            "questions": questions,
            "feedback": feedback,
        },
    )


@login_required
def interview_saved(request):
    interviews = (
        Interview.objects
        .filter(user=request.user)
        .select_related(
            "opportunity",
            "application",
        )
        .order_by(
            "-created_at",
            "-id",
        )
    )

    return render(
        request,
        "interview/saved.html",
        {
            "interviews": interviews,
            "count": interviews.count(),
        },
    )


@login_required
@require_POST
def api_create_interview(request):
    try:
        data = json.loads(
            request.body or "{}"
        )

        opportunity_id = int(
            data.get(
                "opportunityId"
            )
            or 0
        )

        mode = str(
            data.get("mode")
            or ""
        ).strip()

        question_count = int(
            data.get(
                "questionCount"
            )
            or 5
        )

        popia_consent = bool(
            data.get("popiaConsent")
        )

        if mode not in {
            "written",
            "voice",
        }:
            return JsonResponse(
                {
                    "message": (
                        "Choose written or voice "
                        "interview practice."
                    )
                },
                status=400,
            )

        if question_count not in (
            ALLOWED_QUESTION_COUNTS
        ):
            return JsonResponse(
                {
                    "message": (
                        "Choose a valid interview length."
                    )
                },
                status=400,
            )

        if not popia_consent:
            return JsonResponse(
                {
                    "message": (
                        "Please consent to CareerGPS "
                        "processing your profile and "
                        "interview responses for AI-powered "
                        "interview practice."
                    )
                },
                status=400,
            )

        (
            opportunity,
            application,
            source_type,
        ) = resolve_user_interview_source(
            request.user,
            opportunity_id,
        )

        if opportunity is None:
            return JsonResponse(
                {
                    "message": (
                        "Choose an opportunity from "
                        "your saved opportunities or "
                        "application workspaces."
                    )
                },
                status=403,
            )

        context = build_interview_context(
            request.user,
            opportunity,
            application=application,
        )

        plan = generate_interview_plan(
            context["snapshot"],
            question_count,
        )

        interview = (
            Interview.objects.create(
                user=request.user,

                opportunity=opportunity,

                application=application,

                source_type=source_type,

                mode=mode,

                question_count=(
                    question_count
                ),

                job_info_status=(
                    context[
                        "job_info_status"
                    ]
                ),

                context_snapshot=(
                    context["snapshot"]
                ),

                candidate_fact_ids=(
                    context[
                        "candidate_fact_ids"
                    ]
                ),

                generated_document_ids=(
                    context[
                        "generated_document_ids"
                    ]
                ),

                popia_consent=True,

                status="active",

                # Legacy compatibility.
                occupation=(
                    opportunity.title
                ),
                experience_level="",
            )
        )

        InterviewQuestion.objects.bulk_create(
            [
                InterviewQuestion(
                    interview=interview,
                    question_index=index,
                    question_text=(
                        item.question.strip()
                    ),
                    question_type=(
                        item.question_type
                    ),
                    focus=(
                        item.focus.strip()
                    ),
                    answer_mode=mode,
                )
                for index, item
                in enumerate(
                    plan.questions
                )
            ]
        )

        return JsonResponse(
            {
                "success": True,

                "interviewId": (
                    interview.id
                ),

                "liveUrl": (
                    f"/interview/"
                    f"{interview.id}/live/"
                ),
            }
        )

    except InterviewAIUnavailable as exc:
        return JsonResponse(
            {
                "message": str(exc)
            },
            status=503,
        )

    except (
        ValueError,
        TypeError,
        json.JSONDecodeError,
    ):
        return JsonResponse(
            {
                "message": (
                    "CareerGPS couldn't read that "
                    "interview request."
                )
            },
            status=400,
        )

    except Exception:
        return JsonResponse(
            {
                "message": (
                    "CareerGPS couldn't prepare your "
                    "interview right now. Please try "
                    "again shortly."
                )
            },
            status=500,
        )


@login_required
@require_POST
def api_submit_written_answer(
    request,
    question_id,
):
    question = get_object_or_404(
        InterviewQuestion,
        id=question_id,
        interview__user=request.user,
        interview__mode="written",
    )

    try:
        data = json.loads(
            request.body or "{}"
        )
    except json.JSONDecodeError:
        return JsonResponse(
            {
                "message": (
                    "Could not read that answer."
                )
            },
            status=400,
        )

    answer = str(
        data.get("answerText")
        or ""
    ).strip()

    if not answer:
        return JsonResponse(
            {
                "message": (
                    "Enter an answer before continuing."
                )
            },
            status=400,
        )

    question.answer_text = answer
    question.answer_mode = "written"
    question.delivery_metrics = {}

    question.save(
        update_fields=[
            "answer_text",
            "answer_mode",
            "delivery_metrics",
        ]
    )

    return JsonResponse(
        {
            "success": True
        }
    )


@login_required
@require_POST
def api_submit_voice_answer(
    request,
    question_id,
):
    question = get_object_or_404(
        InterviewQuestion,
        id=question_id,
        interview__user=request.user,
        interview__mode="voice",
    )

    interview = question.interview

    if not interview.popia_consent:
        return JsonResponse(
            {
                "message": (
                    "Interview-processing consent "
                    "is required."
                )
            },
            status=403,
        )

    transcript = str(
        request.POST.get(
            "answerText"
        )
        or ""
    ).strip()

    if not transcript:
        return JsonResponse(
            {
                "message": (
                    "CareerGPS could not capture a "
                    "transcript for this answer. "
                    "Review the transcript field and "
                    "enter what you said before continuing."
                )
            },
            status=400,
        )

    try:
        duration_seconds = float(
            request.POST.get(
                "durationSeconds"
            )
            or 0
        )

        pause_count = int(
            request.POST.get(
                "pauseCount"
            )
            or 0
        )

        longest_pause_seconds = float(
            request.POST.get(
                "longestPauseSeconds"
            )
            or 0
        )

    except (
        TypeError,
        ValueError,
    ):
        return JsonResponse(
            {
                "message": (
                    "CareerGPS could not read the "
                    "voice-delivery measurements."
                )
            },
            status=400,
        )

    audio = request.FILES.get(
        "audio"
    )

    if audio:
        max_size = (
            15 * 1024 * 1024
        )

        if audio.size > max_size:
            return JsonResponse(
                {
                    "message": (
                        "That audio answer is too "
                        "large to save."
                    )
                },
                status=413,
            )

        allowed_types = {
            "audio/webm",
            "audio/mp4",
            "audio/ogg",
            "audio/mpeg",
            "audio/wav",
            "audio/x-wav",
        }

        if (
            audio.content_type
            not in allowed_types
        ):
            return JsonResponse(
                {
                    "message": (
                        "Unsupported audio format."
                    )
                },
                status=400,
            )

        question.audio_file.save(
            audio.name,
            audio,
            save=False,
        )

    question.answer_text = transcript
    question.answer_mode = "voice"

    question.delivery_metrics = (
        analyse_delivery(
            transcript,

            duration_seconds=(
                duration_seconds
            ),

            pause_count=(
                pause_count
            ),

            longest_pause_seconds=(
                longest_pause_seconds
            ),
        )
    )

    question.save(
        update_fields=[
            "answer_text",
            "answer_mode",
            "audio_file",
            "delivery_metrics",
        ]
    )

    return JsonResponse(
        {
            "success": True,
            "deliveryMetrics": (
                question.delivery_metrics
            ),
        }
    )


@login_required
@require_POST
def api_complete_interview(
    request,
    interview_id,
):
    interview = get_object_or_404(
        Interview.objects.prefetch_related(
            "questions"
        ),
        id=interview_id,
        user=request.user,
    )

    questions = list(
        interview.questions.all()
    )

    unanswered = [
        question
        for question in questions
        if not (
            question.answer_text
            or ""
        ).strip()
    ]

    if unanswered:
        return JsonResponse(
            {
                "message": (
                    "Complete each interview "
                    "question before generating feedback."
                )
            },
            status=400,
        )

    answers = [
        {
            "question_index": (
                question.question_index
            ),

            "question": (
                question.question_text
            ),

            "question_type": (
                question.question_type
            ),

            "focus": (
                question.focus
            ),

            "answer": (
                question.answer_text
            ),

            "answer_mode": (
                question.answer_mode
            ),

            "delivery_metrics": (
                question.delivery_metrics
                if question.answer_mode
                == "voice"
                else {}
            ),
        }
        for question in questions
    ]

    try:
        evaluation = evaluate_interview(
            interview.context_snapshot
            or {},
            answers,
        )

    except InterviewAIUnavailable as exc:
        return JsonResponse(
            {
                "message": str(exc)
            },
            status=503,
        )

    except Exception:
        return JsonResponse(
            {
                "message": (
                    "CareerGPS couldn't generate "
                    "your interview feedback right now. "
                    "Your answers are saved, so you can "
                    "try again."
                )
            },
            status=500,
        )

    feedback_by_index = {
        item.question_index: item
        for item
        in evaluation.question_feedback
    }

    for question in questions:
        item = feedback_by_index.get(
            question.question_index
        )

        if not item:
            continue

        question.evaluation = (
            item.model_dump()
        )

        # Legacy fields retain meaningful feedback
        # for compatibility with existing admin/data.
        question.answer_feedback = (
            item.feedback
        )

        question.answer_strengths = (
            item.strengths
        )

        question.answer_improvements = (
            item.improvements
        )

        question.save(
            update_fields=[
                "evaluation",
                "answer_feedback",
                "answer_strengths",
                "answer_improvements",
            ]
        )

    interview.feedback_data = (
        evaluation.model_dump()
    )

    interview.feedback_summary = (
        evaluation.overall_summary
    )

    interview.strengths = (
        evaluation.strengths
    )

    interview.improvements = (
        evaluation.improvements
    )

    interview.status = "completed"
    interview.error_message = ""

    interview.save(
        update_fields=[
            "feedback_data",
            "feedback_summary",
            "strengths",
            "improvements",
            "status",
            "error_message",
            "updated_at",
        ]
    )

    return JsonResponse(
        {
            "success": True,

            "feedbackUrl": (
                f"/interview/"
                f"{interview.id}/feedback/"
            ),
        }
    )


@login_required
@require_http_methods(
    ["DELETE"]
)
def api_delete_interview(
    request,
    interview_id,
):
    interview = get_object_or_404(
        Interview,
        id=interview_id,
        user=request.user,
    )

    interview.delete()

    return JsonResponse(
        {
            "success": True
        }
    )


@login_required
def api_get_interview(
    request,
    interview_id,
):
    interview = get_object_or_404(
        Interview,
        id=interview_id,
        user=request.user,
    )

    questions = list(
        interview.questions.values(
            "id",
            "question_index",
            "question_text",
            "question_type",
            "focus",
            "answer_text",
            "delivery_metrics",
            "evaluation",
        )
    )

    return JsonResponse(
        {
            "interview": {
                "id": interview.id,
                "status": interview.status,
                "mode": interview.mode,
                "opportunityId": (
                    interview.opportunity_id
                ),
            },

            "questions": questions,
        }
    )
''')


write("interview/urls.py", r'''
from django.urls import path

from . import views


urlpatterns = [
    path(
        "",
        views.interview_home,
        name="interview_home",
    ),

    # Compatibility with old links.
    path(
        "setup/",
        views.interview_setup,
        name="interview_setup",
    ),

    path(
        "opportunity/<int:opportunity_id>/prepare/",
        views.interview_prepare,
        name="interview_prepare",
    ),

    path(
        "<int:interview_id>/live/",
        views.interview_live,
        name="interview_live",
    ),

    path(
        "<int:interview_id>/feedback/",
        views.interview_feedback,
        name="interview_feedback",
    ),

    path(
        "saved/",
        views.interview_saved,
        name="interview_saved",
    ),

    path(
        "api/create/",
        views.api_create_interview,
        name="api_create_interview",
    ),

    path(
        "api/question/<int:question_id>/answer-written/",
        views.api_submit_written_answer,
        name="api_submit_written_answer",
    ),

    path(
        "api/question/<int:question_id>/answer-voice/",
        views.api_submit_voice_answer,
        name="api_submit_voice_answer",
    ),

    path(
        "api/<int:interview_id>/complete/",
        views.api_complete_interview,
        name="api_complete_interview",
    ),

    path(
        "api/<int:interview_id>/delete/",
        views.api_delete_interview,
        name="api_delete_interview",
    ),

    path(
        "api/<int:interview_id>/",
        views.api_get_interview,
        name="api_get_interview",
    ),
]
''')


write("interview/templates/interview/home.html", r'''
{% extends "dashboard_pages.html" %}

{% block title %}
CareerGPS - Interview Practice
{% endblock %}

{% block content %}
<style>
.interview-page {
    max-width: 1080px;
    margin: 0 auto;
    padding: 2rem 1.5rem 4rem;
}

.interview-header {
    display: flex;
    justify-content: space-between;
    gap: 1rem;
    align-items: flex-start;
    margin-bottom: 2rem;
}

.interview-eyebrow {
    color: #E8612A;
    font-size: .75rem;
    font-weight: 800;
    letter-spacing: .12em;
    text-transform: uppercase;
}

.interview-header h1 {
    margin: .35rem 0 .5rem;
    font-size: 2rem;
}

.interview-intro {
    color: #667085;
    line-height: 1.6;
    max-width: 720px;
}

.history-link {
    color: #E8612A;
    font-weight: 700;
    text-decoration: none;
    white-space: nowrap;
}

.interview-section {
    margin: 2rem 0;
}

.interview-section h2 {
    font-size: 1.15rem;
    margin-bottom: .85rem;
}

.opportunity-grid {
    display: grid;
    grid-template-columns:
        repeat(
            auto-fit,
            minmax(290px, 1fr)
        );
    gap: 1rem;
}

.opportunity-card {
    background: #fff;
    border: 1px solid rgba(0,0,0,.09);
    border-radius: 16px;
    padding: 1.25rem;
    display: flex;
    flex-direction: column;
    gap: .65rem;
}

.opportunity-card h3 {
    font-size: 1rem;
    margin: 0;
}

.opportunity-meta {
    color: #667085;
    font-size: .88rem;
    line-height: 1.45;
}

.status-pill {
    align-self: flex-start;
    padding: .25rem .55rem;
    border-radius: 999px;
    background: #f2f4f7;
    color: #475467;
    font-size: .72rem;
    font-weight: 700;
}

.practice-btn {
    margin-top: auto;
    display: inline-flex;
    justify-content: center;
    padding: .75rem 1rem;
    border-radius: 999px;
    background: #E8612A;
    color: #fff;
    text-decoration: none;
    font-weight: 750;
}

.empty-card {
    border: 1px dashed rgba(0,0,0,.15);
    background: #fff;
    border-radius: 16px;
    padding: 1.25rem;
    color: #667085;
}

.recent {
    border-top: 1px solid rgba(0,0,0,.08);
    margin-top: 2.5rem;
    padding-top: 1.5rem;
}

.recent-row {
    background: #fff;
    border: 1px solid rgba(0,0,0,.08);
    border-radius: 12px;
    padding: .9rem 1rem;
    margin-bottom: .65rem;
    display: flex;
    justify-content: space-between;
    gap: 1rem;
    align-items: center;
}

.recent-row a {
    color: #E8612A;
    text-decoration: none;
    font-weight: 700;
}

@media (max-width: 650px) {
    .interview-header {
        flex-direction: column;
    }

    .recent-row {
        align-items: flex-start;
        flex-direction: column;
    }
}
</style>


<div class="interview-page">

    <header class="interview-header">
        <div>
            <div class="interview-eyebrow">
                Interview Practice
            </div>

            <h1>
                Prepare for an opportunity
                you already care about
            </h1>

            <p class="interview-intro">
                Choose a saved opportunity or an
                application workspace. CareerGPS will
                prepare the interview using the vacancy
                information and your verified profile.
            </p>
        </div>

        <a
            class="history-link"
            href="{% url 'interview_saved' %}"
        >
            My Interviews
        </a>
    </header>


    <section class="interview-section">
        <h2>
            Applications in progress
        </h2>

        {% if applications %}

        <div class="opportunity-grid">

            {% for application in applications %}

            <article class="opportunity-card">

                <span class="status-pill">
                    {{ application.get_status_display }}
                </span>

                <h3>
                    {{ application.opportunity.title }}
                </h3>

                <div class="opportunity-meta">
                    {{ application.opportunity.company }}

                    {% if application.opportunity.location_text or application.opportunity.location %}
                        ·
                        {{ application.opportunity.location_text|default:application.opportunity.location }}
                    {% endif %}
                </div>

                <a
                    class="practice-btn"
                    href="{% url 'interview_prepare' application.opportunity.id %}"
                >
                    Prepare interview
                </a>

            </article>

            {% endfor %}

        </div>

        {% else %}

        <div class="empty-card">
            No active application workspaces yet.
        </div>

        {% endif %}
    </section>


    <section class="interview-section">
        <h2>
            Saved opportunities
        </h2>

        {% if saved_opportunities %}

        <div class="opportunity-grid">

            {% for saved in saved_opportunities %}

            <article class="opportunity-card">

                <span class="status-pill">
                    Saved
                </span>

                <h3>
                    {{ saved.opportunity.title }}
                </h3>

                <div class="opportunity-meta">
                    {{ saved.opportunity.company }}

                    {% if saved.opportunity.location_text or saved.opportunity.location %}
                        ·
                        {{ saved.opportunity.location_text|default:saved.opportunity.location }}
                    {% endif %}
                </div>

                <a
                    class="practice-btn"
                    href="{% url 'interview_prepare' saved.opportunity.id %}"
                >
                    Prepare interview
                </a>

            </article>

            {% endfor %}

        </div>

        {% else %}

        <div class="empty-card">
            No additional saved opportunities.
            Save an opportunity and it will become
            available for interview practice here.
        </div>

        {% endif %}
    </section>


    {% if recent_interviews %}

    <section class="recent">

        <h2>
            Recent practice
        </h2>

        {% for interview in recent_interviews %}

        <div class="recent-row">

            <div>
                <strong>
                    {% if interview.opportunity %}
                        {{ interview.opportunity.title }}
                    {% else %}
                        {{ interview.occupation }}
                    {% endif %}
                </strong>

                <div class="opportunity-meta">
                    {{ interview.get_mode_display }}
                    ·
                    {{ interview.created_at|date:"j M Y" }}
                </div>
            </div>

            {% if interview.status == "completed" %}

            <a
                href="{% url 'interview_feedback' interview.id %}"
            >
                View feedback
            </a>

            {% else %}

            <a
                href="{% url 'interview_live' interview.id %}"
            >
                Continue
            </a>

            {% endif %}

        </div>

        {% endfor %}

    </section>

    {% endif %}

</div>
{% endblock %}
''')


write("interview/templates/interview/prepare.html", r'''
{% extends "dashboard_pages.html" %}

{% block title %}
CareerGPS - Prepare Interview
{% endblock %}

{% block content %}
<style>
.prepare-page {
    max-width: 760px;
    margin: 0 auto;
    padding: 2rem 1.5rem 4rem;
}

.back-link {
    color: #667085;
    text-decoration: none;
}

.eyebrow {
    margin-top: 1.5rem;
    color: #E8612A;
    text-transform: uppercase;
    letter-spacing: .12em;
    font-size: .72rem;
    font-weight: 800;
}

.prepare-page h1 {
    margin: .4rem 0;
    font-size: 2rem;
}

.job-meta {
    color: #667085;
    margin-bottom: 1.8rem;
}

.prepare-card {
    background: #fff;
    border: 1px solid rgba(0,0,0,.09);
    border-radius: 16px;
    padding: 1.4rem;
    margin-bottom: 1rem;
}

.prepare-card h2 {
    margin: 0 0 .9rem;
    font-size: 1.05rem;
}

.mode-grid {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: .8rem;
}

.mode-card {
    display: block;
    border: 1px solid rgba(0,0,0,.12);
    border-radius: 14px;
    padding: 1rem;
    cursor: pointer;
}

.mode-card.active {
    border-color: #E8612A;
    box-shadow:
        0 0 0 2px rgba(232,97,42,.10);
}

.mode-card p {
    margin: .45rem 0 0;
    color: #667085;
    font-size: .86rem;
    line-height: 1.5;
}

.lengths {
    display: flex;
    gap: .65rem;
    flex-wrap: wrap;
}

.length-btn {
    border: 1px solid rgba(0,0,0,.12);
    background: #fff;
    border-radius: 999px;
    padding: .65rem .9rem;
    cursor: pointer;
}

.length-btn.active {
    background: #E8612A;
    color: #fff;
    border-color: #E8612A;
}

.consent {
    display: block;
    line-height: 1.55;
    color: #475467;
    font-size: .88rem;
}

.start-btn {
    width: 100%;
    border: 0;
    background: #E8612A;
    color: #fff;
    border-radius: 999px;
    padding: .95rem 1rem;
    font-size: 1rem;
    font-weight: 800;
    cursor: pointer;
}

.start-btn:disabled {
    opacity: .55;
    cursor: not-allowed;
}

.message {
    display: none;
    margin-bottom: 1rem;
    padding: .8rem 1rem;
    background: #fff3ef;
    color: #9d3515;
    border: 1px solid #ffd1c2;
    border-radius: 12px;
}

@media (max-width: 620px) {
    .mode-grid {
        grid-template-columns: 1fr;
    }
}
</style>


<div class="prepare-page">

    <a
        class="back-link"
        href="{% url 'interview_home' %}"
    >
        ← Back to Interview Practice
    </a>

    <div class="eyebrow">
        {% if source_type == "application" %}
            Application Workspace
        {% else %}
            Saved Opportunity
        {% endif %}
    </div>

    <h1>
        {{ opportunity.title }}
    </h1>

    <div class="job-meta">
        {{ opportunity.company }}

        {% if opportunity.location_text or opportunity.location %}
            ·
            {{ opportunity.location_text|default:opportunity.location }}
        {% endif %}
    </div>

    <div
        class="message"
        id="message"
    ></div>


    <section class="prepare-card">

        <h2>
            How would you like to practise?
        </h2>

        <div class="mode-grid">

            <label
                class="mode-card active"
                id="writtenChoice"
            >

                <input
                    type="radio"
                    name="mode"
                    value="written"
                    checked
                >

                <strong>
                    Written interview
                </strong>

                <p>
                    Type your answers one question
                    at a time and receive feedback
                    after the interview.
                </p>

            </label>


            <label
                class="mode-card"
                id="voiceChoice"
            >

                <input
                    type="radio"
                    name="mode"
                    value="voice"
                >

                <strong>
                    Voice interview
                </strong>

                <p>
                    Hear each question, answer using
                    your microphone and review your
                    transcript before submitting.
                </p>

            </label>

        </div>

    </section>


    <section class="prepare-card">

        <h2>
            Interview length
        </h2>

        <div class="lengths">

            <button
                type="button"
                class="length-btn"
                data-count="3"
            >
                Quick · 3
            </button>

            <button
                type="button"
                class="length-btn active"
                data-count="5"
            >
                Standard · 5
            </button>

            <button
                type="button"
                class="length-btn"
                data-count="8"
            >
                Extended · 8
            </button>

        </div>

    </section>


    <section class="prepare-card">

        <label class="consent">

            <input
                type="checkbox"
                id="popiaConsent"
            >

            I consent to CareerGPS processing my
            verified profile information and interview
            responses for AI-powered interview practice.

            <span id="voiceConsentText">
                If I choose voice practice, this also
                includes my interview audio and transcript.
            </span>

        </label>

    </section>


    <button
        class="start-btn"
        id="startBtn"
    >
        Prepare interview
    </button>

</div>


<script>
const CSRF = "{{ csrf_token }}";
const OPPORTUNITY_ID = {{ opportunity.id }};

let questionCount = 5;


function selectedMode() {
    return document.querySelector(
        'input[name="mode"]:checked'
    ).value;
}


function refreshModeCards() {
    const mode = selectedMode();

    document
        .getElementById("writtenChoice")
        .classList.toggle(
            "active",
            mode === "written"
        );

    document
        .getElementById("voiceChoice")
        .classList.toggle(
            "active",
            mode === "voice"
        );
}


document
    .querySelectorAll(
        'input[name="mode"]'
    )
    .forEach(input => {
        input.addEventListener(
            "change",
            refreshModeCards
        );
    });


document
    .querySelectorAll(
        ".length-btn"
    )
    .forEach(button => {

        button.addEventListener(
            "click",
            () => {

                document
                    .querySelectorAll(
                        ".length-btn"
                    )
                    .forEach(item =>
                        item.classList.remove(
                            "active"
                        )
                    );

                button.classList.add(
                    "active"
                );

                questionCount = Number(
                    button.dataset.count
                );
            }
        );
    });


function showMessage(message) {
    const element =
        document.getElementById(
            "message"
        );

    element.textContent = message;
    element.style.display = "block";
}


document
    .getElementById("startBtn")
    .addEventListener(
        "click",
        async () => {

            const button =
                document.getElementById(
                    "startBtn"
                );

            const consent =
                document.getElementById(
                    "popiaConsent"
                ).checked;

            const mode = selectedMode();

            document.getElementById(
                "message"
            ).style.display = "none";

            if (!consent) {
                showMessage(
                    "Please consent to interview processing before continuing."
                );
                return;
            }

            button.disabled = true;
            button.textContent =
                "Preparing interview…";

            try {

                const response = await fetch(
                    "{% url 'api_create_interview' %}",
                    {
                        method: "POST",

                        headers: {
                            "Content-Type":
                                "application/json",

                            "X-CSRFToken":
                                CSRF,
                        },

                        body: JSON.stringify({
                            opportunityId:
                                OPPORTUNITY_ID,

                            mode: mode,

                            questionCount:
                                questionCount,

                            popiaConsent:
                                true,
                        }),
                    }
                );

                const data =
                    await response.json();

                if (!response.ok) {
                    throw new Error(
                        data.message
                        ||
                        "CareerGPS could not prepare the interview."
                    );
                }

                window.location.href =
                    data.liveUrl;

            } catch (error) {

                showMessage(
                    error.message
                );

                button.disabled = false;
                button.textContent =
                    "Prepare interview";
            }
        }
    );
</script>

{% endblock %}
''')


write("interview/templates/interview/live.html", r'''
{% extends "dashboard_pages.html" %}

{% block title %}
CareerGPS - Interview
{% endblock %}

{% block content %}
<style>
.live-page {
    max-width: 820px;
    margin: 0 auto;
    padding: 2rem 1.5rem 4rem;
}

.live-header {
    display: flex;
    justify-content: space-between;
    gap: 1rem;
    align-items: flex-start;
}

.eyebrow {
    color: #E8612A;
    text-transform: uppercase;
    letter-spacing: .12em;
    font-size: .72rem;
    font-weight: 800;
}

.live-header h1 {
    margin: .3rem 0;
    font-size: 1.4rem;
}

.muted {
    color: #667085;
    font-size: .88rem;
}

.progress {
    height: 5px;
    margin: 1rem 0 1.5rem;
    background: #eceff3;
    border-radius: 999px;
    overflow: hidden;
}

.progress-bar {
    height: 100%;
    background: #E8612A;
    width: 0;
    transition: width .25s;
}

.question-card {
    background: #fff;
    border: 1px solid rgba(0,0,0,.09);
    border-radius: 18px;
    padding: 1.5rem;
}

.question-type {
    color: #E8612A;
    font-size: .72rem;
    font-weight: 800;
    text-transform: uppercase;
    letter-spacing: .08em;
}

.question-text {
    font-size: 1.2rem;
    line-height: 1.6;
    margin: .7rem 0 1.25rem;
}

textarea {
    width: 100%;
    min-height: 180px;
    border: 1px solid rgba(0,0,0,.12);
    border-radius: 12px;
    padding: 1rem;
    font: inherit;
    resize: vertical;
}

.actions {
    margin-top: 1rem;
    display: flex;
    gap: .65rem;
    flex-wrap: wrap;
}

.btn {
    border: 0;
    border-radius: 999px;
    padding: .75rem 1rem;
    font-weight: 800;
    cursor: pointer;
}

.btn-primary {
    background: #E8612A;
    color: #fff;
}

.btn-secondary {
    background: #f2f4f7;
    color: #344054;
}

.btn-danger {
    background: #b42318;
    color: #fff;
}

.btn:disabled {
    opacity: .55;
    cursor: not-allowed;
}

.voice-panel {
    display: none;
}

.voice-status {
    margin: .8rem 0;
    background: #f7f8fa;
    color: #475467;
    padding: .75rem 1rem;
    border-radius: 12px;
}

.transcript-label {
    display: block;
    color: #667085;
    font-size: .82rem;
    line-height: 1.5;
    margin-bottom: .45rem;
}

.delivery-preview {
    display: none;
    margin-top: 1rem;
    padding: 1rem;
    background: #f7f8fa;
    border-radius: 12px;
    color: #475467;
    font-size: .86rem;
    line-height: 1.6;
}

.audio-preview {
    display: none;
    width: 100%;
    margin-top: 1rem;
}

.message {
    display: none;
    margin-bottom: 1rem;
    padding: .8rem 1rem;
    background: #fff3ef;
    color: #9d3515;
    border: 1px solid #ffd1c2;
    border-radius: 12px;
}
</style>


<div class="live-page">

    <header class="live-header">

        <div>

            <div class="eyebrow">
                {{ interview.get_mode_display }}
                Interview
            </div>

            <h1>
                {% if interview.opportunity %}
                    {{ interview.opportunity.title }}
                {% else %}
                    {{ interview.occupation }}
                {% endif %}
            </h1>

            <div class="muted">
                {% if interview.opportunity %}
                    {{ interview.opportunity.company }}
                    ·
                {% endif %}

                <span id="questionCounter"></span>
            </div>

        </div>

        <a
            class="muted"
            href="{% url 'interview_home' %}"
        >
            Exit
        </a>

    </header>


    <div class="progress">
        <div
            class="progress-bar"
            id="progressBar"
        ></div>
    </div>


    <div
        class="message"
        id="message"
    ></div>


    <article class="question-card">

        <div
            class="question-type"
            id="questionType"
        ></div>

        <div
            class="question-text"
            id="questionText"
        ></div>


        <section id="writtenPanel">

            <textarea
                id="writtenAnswer"
                placeholder="Type your answer here…"
            ></textarea>

            <div class="actions">

                <button
                    class="btn btn-primary"
                    id="writtenSubmit"
                >
                    Submit & continue
                </button>

            </div>

        </section>


        <section
            class="voice-panel"
            id="voicePanel"
        >

            <div class="actions">

                <button
                    class="btn btn-secondary"
                    id="playQuestion"
                >
                    🔊 Play question
                </button>

                <button
                    class="btn btn-primary"
                    id="recordButton"
                >
                    🎙 Start answer
                </button>

            </div>


            <div
                class="voice-status"
                id="voiceStatus"
            >
                Ready when you are.
            </div>


            <label
                class="transcript-label"
                for="transcript"
            >
                Transcript — review and correct
                obvious speech-recognition mistakes
                before submitting.
            </label>

            <textarea
                id="transcript"
                placeholder="Your transcript will appear here. You can correct recognition mistakes."
            ></textarea>


            <audio
                controls
                class="audio-preview"
                id="audioPreview"
            ></audio>


            <div
                class="delivery-preview"
                id="deliveryPreview"
            ></div>


            <div class="actions">

                <button
                    class="btn btn-primary"
                    id="voiceSubmit"
                    disabled
                >
                    Submit & continue
                </button>

            </div>

        </section>

    </article>

</div>


<script>
const CSRF = "{{ csrf_token }}";

const INTERVIEW_ID =
    {{ interview.id }};

const MODE =
    "{{ interview.mode }}";

const QUESTIONS =
    {{ questions_json|safe }};


let currentIndex = 0;

let recorder = null;
let mediaStream = null;
let chunks = [];

let recordingStartedAt = 0;
let pendingAudio = null;
let pendingDuration = 0;

let recognition = null;
let finalTranscript = "";

let audioContext = null;
let analyser = null;
let analyserInterval = null;

let speechDetected = false;
let silenceStartedAt = null;
let pauseCount = 0;
let longestPauseMs = 0;


const questionText =
    document.getElementById(
        "questionText"
    );

const questionType =
    document.getElementById(
        "questionType"
    );

const questionCounter =
    document.getElementById(
        "questionCounter"
    );

const progressBar =
    document.getElementById(
        "progressBar"
    );

const messageBox =
    document.getElementById(
        "message"
    );

const writtenPanel =
    document.getElementById(
        "writtenPanel"
    );

const voicePanel =
    document.getElementById(
        "voicePanel"
    );


function showMessage(message) {
    messageBox.textContent = message;
    messageBox.style.display = "block";
}


function clearMessage() {
    messageBox.style.display = "none";
}


function friendlyType(value) {
    return value
        .replaceAll("_", " ")
        .replace(
            /\b\w/g,
            letter =>
                letter.toUpperCase()
        );
}


function renderQuestion() {
    clearMessage();

    const question =
        QUESTIONS[currentIndex];

    questionText.textContent =
        question.question_text;

    questionType.textContent =
        friendlyType(
            question.question_type
        );

    questionCounter.textContent =
        `Question ${currentIndex + 1} of ${QUESTIONS.length}`;

    progressBar.style.width =
        `${(currentIndex / QUESTIONS.length) * 100}%`;

    document.getElementById(
        "writtenAnswer"
    ).value =
        question.answer_text || "";

    document.getElementById(
        "transcript"
    ).value =
        question.answer_text || "";

    document.getElementById(
        "deliveryPreview"
    ).style.display = "none";

    document.getElementById(
        "audioPreview"
    ).style.display = "none";

    document.getElementById(
        "voiceStatus"
    ).textContent =
        "Ready when you are.";

    pendingAudio = null;
    pendingDuration = 0;

    document.getElementById(
        "voiceSubmit"
    ).disabled =
        !(
            question.answer_text
            || ""
        ).trim();
}


async function nextOrFinish() {

    if (
        currentIndex
        <
        QUESTIONS.length - 1
    ) {
        currentIndex += 1;
        renderQuestion();
        return;
    }

    progressBar.style.width = "100%";

    await completeInterview();
}


async function completeInterview() {

    document
        .querySelectorAll("button")
        .forEach(button => {
            button.disabled = true;
        });

    try {

        const response = await fetch(
            `/interview/api/${INTERVIEW_ID}/complete/`,
            {
                method: "POST",

                headers: {
                    "X-CSRFToken": CSRF,
                },
            }
        );

        const data =
            await response.json();

        if (!response.ok) {
            throw new Error(
                data.message
                ||
                "CareerGPS could not generate interview feedback."
            );
        }

        window.location.href =
            data.feedbackUrl;

    } catch (error) {

        showMessage(
            error.message
        );

        document
            .querySelectorAll("button")
            .forEach(button => {
                button.disabled = false;
            });
    }
}


document
    .getElementById(
        "writtenSubmit"
    )
    .addEventListener(
        "click",
        async () => {

            clearMessage();

            const answer =
                document
                    .getElementById(
                        "writtenAnswer"
                    )
                    .value
                    .trim();

            if (!answer) {
                showMessage(
                    "Enter an answer before continuing."
                );
                return;
            }

            const button =
                document.getElementById(
                    "writtenSubmit"
                );

            button.disabled = true;

            try {

                const question =
                    QUESTIONS[
                        currentIndex
                    ];

                const response =
                    await fetch(
                        `/interview/api/question/${question.id}/answer-written/`,
                        {
                            method: "POST",

                            headers: {
                                "Content-Type":
                                    "application/json",

                                "X-CSRFToken":
                                    CSRF,
                            },

                            body:
                                JSON.stringify({
                                    answerText:
                                        answer,
                                }),
                        }
                    );

                const data =
                    await response.json();

                if (!response.ok) {
                    throw new Error(
                        data.message
                        ||
                        "CareerGPS could not save your answer."
                    );
                }

                QUESTIONS[
                    currentIndex
                ].answer_text = answer;

                await nextOrFinish();

            } catch (error) {

                showMessage(
                    error.message
                );

            } finally {

                button.disabled = false;
            }
        }
    );


document
    .getElementById(
        "playQuestion"
    )
    .addEventListener(
        "click",
        () => {

            clearMessage();

            if (
                !(
                    "speechSynthesis"
                    in window
                )
            ) {
                showMessage(
                    "Question playback is not available in this browser."
                );
                return;
            }

            speechSynthesis.cancel();

            const utterance =
                new SpeechSynthesisUtterance(
                    QUESTIONS[
                        currentIndex
                    ].question_text
                );

            utterance.lang =
                "en-ZA";

            speechSynthesis.speak(
                utterance
            );
        }
    );


function startSpeechRecognition() {

    const SpeechRecognition =
        window.SpeechRecognition
        ||
        window.webkitSpeechRecognition;

    if (!SpeechRecognition) {
        recognition = null;
        return;
    }

    recognition =
        new SpeechRecognition();

    recognition.continuous = true;
    recognition.interimResults = true;
    recognition.lang = "en-ZA";

    finalTranscript = "";

    recognition.onresult =
        event => {

            let interim = "";

            for (
                let index =
                    event.resultIndex;

                index <
                    event.results.length;

                index++
            ) {

                if (
                    event.results[index]
                        .isFinal
                ) {
                    finalTranscript +=
                        event.results[index][0]
                            .transcript
                        + " ";
                } else {
                    interim +=
                        event.results[index][0]
                            .transcript;
                }
            }

            document.getElementById(
                "transcript"
            ).value =
                (
                    finalTranscript
                    + interim
                ).trim();
        };

    recognition.onerror =
        () => {};

    try {
        recognition.start();
    } catch (_) {}
}


function startPauseMeasurement(
    stream
) {
    try {

        audioContext =
            new (
                window.AudioContext
                ||
                window.webkitAudioContext
            )();

        const source =
            audioContext
                .createMediaStreamSource(
                    stream
                );

        analyser =
            audioContext
                .createAnalyser();

        analyser.fftSize = 2048;

        source.connect(
            analyser
        );

        const data =
            new Uint8Array(
                analyser.fftSize
            );

        pauseCount = 0;
        longestPauseMs = 0;
        silenceStartedAt = null;
        speechDetected = false;

        analyserInterval =
            setInterval(
                () => {

                    analyser
                        .getByteTimeDomainData(
                            data
                        );

                    let sum = 0;

                    for (
                        const value of data
                    ) {
                        const sample =
                            (
                                value - 128
                            )
                            / 128;

                        sum +=
                            sample
                            * sample;
                    }

                    const rms =
                        Math.sqrt(
                            sum
                            / data.length
                        );

                    const now =
                        performance.now();

                    const speaking =
                        rms > 0.02;

                    if (speaking) {

                        speechDetected = true;

                        if (
                            silenceStartedAt
                            !== null
                        ) {
                            const silence =
                                now
                                -
                                silenceStartedAt;

                            if (
                                silence
                                >= 1000
                            ) {
                                pauseCount += 1;

                                longestPauseMs =
                                    Math.max(
                                        longestPauseMs,
                                        silence
                                    );
                            }

                            silenceStartedAt =
                                null;
                        }

                    } else if (
                        speechDetected
                        &&
                        silenceStartedAt
                        === null
                    ) {

                        silenceStartedAt =
                            now;
                    }

                },
                100
            );

    } catch (_) {
        pauseCount = 0;
        longestPauseMs = 0;
    }
}


function stopPauseMeasurement() {

    if (analyserInterval) {
        clearInterval(
            analyserInterval
        );

        analyserInterval =
            null;
    }

    if (audioContext) {
        audioContext
            .close()
            .catch(() => {});

        audioContext = null;
    }
}


async function beginRecording() {

    clearMessage();

    try {

        mediaStream =
            await navigator
                .mediaDevices
                .getUserMedia({
                    audio: true,
                    video: false,
                });

    } catch (_) {

        showMessage(
            "CareerGPS needs microphone access for voice interview practice."
        );

        return;
    }

    chunks = [];

    let options = {};

    if (
        MediaRecorder
            .isTypeSupported(
                "audio/webm;codecs=opus"
            )
    ) {
        options = {
            mimeType:
                "audio/webm;codecs=opus",
        };
    }

    recorder =
        new MediaRecorder(
            mediaStream,
            options
        );

    recorder.ondataavailable =
        event => {

            if (event.data.size) {
                chunks.push(
                    event.data
                );
            }
        };

    recorder.start(500);

    recordingStartedAt =
        performance.now();

    startSpeechRecognition();

    startPauseMeasurement(
        mediaStream
    );

    const button =
        document.getElementById(
            "recordButton"
        );

    button.textContent =
        "⏹ Finish answer";

    button.classList.remove(
        "btn-primary"
    );

    button.classList.add(
        "btn-danger"
    );

    document.getElementById(
        "voiceStatus"
    ).textContent =
        "Recording…";

    document.getElementById(
        "voiceSubmit"
    ).disabled = true;
}


function finishRecorder() {

    return new Promise(
        resolve => {

            recorder.onstop =
                () => {

                    const mimeType =
                        recorder.mimeType
                        ||
                        "audio/webm";

                    resolve(
                        new Blob(
                            chunks,
                            {
                                type:
                                    mimeType,
                            }
                        )
                    );
                };

            recorder.stop();
        }
    );
}


document
    .getElementById(
        "recordButton"
    )
    .addEventListener(
        "click",
        async () => {

            if (
                !recorder
                ||
                recorder.state
                === "inactive"
            ) {
                await beginRecording();
                return;
            }

            pendingDuration =
                (
                    performance.now()
                    -
                    recordingStartedAt
                )
                / 1000;

            if (recognition) {
                try {
                    recognition.stop();
                } catch (_) {}

                recognition = null;
            }

            stopPauseMeasurement();

            pendingAudio =
                await finishRecorder();

            if (mediaStream) {

                mediaStream
                    .getTracks()
                    .forEach(
                        track =>
                            track.stop()
                    );

                mediaStream = null;
            }

            const button =
                document.getElementById(
                    "recordButton"
                );

            button.textContent =
                "🎙 Record again";

            button.classList.remove(
                "btn-danger"
            );

            button.classList.add(
                "btn-primary"
            );

            document.getElementById(
                "voiceStatus"
            ).textContent =
                (
                    "Recording complete. "
                    +
                    "Review your transcript before submitting."
                );

            const transcript =
                document.getElementById(
                    "transcript"
                ).value.trim();

            document.getElementById(
                "voiceSubmit"
            ).disabled =
                !transcript;

            const delivery =
                document.getElementById(
                    "deliveryPreview"
                );

            delivery.style.display =
                "block";

            delivery.textContent =
                (
                    `Recorded ${pendingDuration.toFixed(1)}s`
                    +
                    ` · observed pauses: ${pauseCount}`
                    +
                    ` · longest observed pause: ${(longestPauseMs / 1000).toFixed(1)}s`
                );

            const preview =
                document.getElementById(
                    "audioPreview"
                );

            if (
                preview.src
            ) {
                URL.revokeObjectURL(
                    preview.src
                );
            }

            preview.src =
                URL.createObjectURL(
                    pendingAudio
                );

            preview.style.display =
                "block";
        }
    );


document
    .getElementById(
        "transcript"
    )
    .addEventListener(
        "input",
        () => {

            document.getElementById(
                "voiceSubmit"
            ).disabled =
                !document
                    .getElementById(
                        "transcript"
                    )
                    .value
                    .trim();
        }
    );


document
    .getElementById(
        "voiceSubmit"
    )
    .addEventListener(
        "click",
        async () => {

            clearMessage();

            const transcript =
                document
                    .getElementById(
                        "transcript"
                    )
                    .value
                    .trim();

            if (!transcript) {

                showMessage(
                    "Review or enter the transcript before continuing."
                );

                return;
            }

            const question =
                QUESTIONS[
                    currentIndex
                ];

            const form =
                new FormData();

            form.append(
                "answerText",
                transcript
            );

            form.append(
                "durationSeconds",
                String(
                    pendingDuration
                    || 0
                )
            );

            form.append(
                "pauseCount",
                String(
                    pauseCount
                    || 0
                )
            );

            form.append(
                "longestPauseSeconds",
                String(
                    (
                        longestPauseMs
                        || 0
                    )
                    / 1000
                )
            );

            if (
                pendingAudio
                &&
                pendingAudio.size
            ) {

                const extension =
                    pendingAudio.type
                        .includes("mp4")
                    ? "m4a"
                    : "webm";

                form.append(
                    "audio",
                    pendingAudio,
                    (
                        `interview_${INTERVIEW_ID}`
                        +
                        `_q${question.question_index + 1}`
                        +
                        `.${extension}`
                    )
                );
            }

            const button =
                document.getElementById(
                    "voiceSubmit"
                );

            button.disabled = true;

            try {

                const response =
                    await fetch(
                        `/interview/api/question/${question.id}/answer-voice/`,
                        {
                            method: "POST",

                            headers: {
                                "X-CSRFToken":
                                    CSRF,
                            },

                            body: form,
                        }
                    );

                const data =
                    await response.json();

                if (!response.ok) {
                    throw new Error(
                        data.message
                        ||
                        "CareerGPS could not save your answer."
                    );
                }

                QUESTIONS[
                    currentIndex
                ].answer_text =
                    transcript;

                QUESTIONS[
                    currentIndex
                ].delivery_metrics =
                    data.deliveryMetrics
                    || {};

                pendingAudio = null;
                pendingDuration = 0;

                await nextOrFinish();

            } catch (error) {

                showMessage(
                    error.message
                );

                button.disabled = false;
            }
        }
    );


if (MODE === "voice") {

    writtenPanel.style.display =
        "none";

    voicePanel.style.display =
        "block";

} else {

    writtenPanel.style.display =
        "block";

    voicePanel.style.display =
        "none";
}


renderQuestion();


window.addEventListener(
    "beforeunload",
    () => {

        if (mediaStream) {
            mediaStream
                .getTracks()
                .forEach(
                    track =>
                        track.stop()
                );
        }

        stopPauseMeasurement();

        if (
            "speechSynthesis"
            in window
        ) {
            speechSynthesis.cancel();
        }
    }
);
</script>

{% endblock %}
''')


write("interview/templates/interview/feedback.html", r'''
{% extends "dashboard_pages.html" %}

{% block title %}
CareerGPS - Interview Feedback
{% endblock %}

{% block content %}
<style>
.feedback-page {
    max-width: 900px;
    margin: 0 auto;
    padding: 2rem 1.5rem 4rem;
}

.back-link {
    color: #667085;
    text-decoration: none;
}

.eyebrow {
    margin-top: 1.5rem;
    color: #E8612A;
    font-size: .72rem;
    font-weight: 800;
    letter-spacing: .12em;
    text-transform: uppercase;
}

.feedback-page h1 {
    margin: .4rem 0 .3rem;
    font-size: 2rem;
}

.meta {
    color: #667085;
}

.feedback-card {
    background: #fff;
    border: 1px solid rgba(0,0,0,.09);
    border-radius: 16px;
    padding: 1.4rem;
    margin-top: 1rem;
}

.feedback-card h2 {
    margin: 0 0 .8rem;
    font-size: 1.08rem;
}

.feedback-card p,
.feedback-card li {
    color: #475467;
    line-height: 1.65;
}

.feedback-grid {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 1rem;
}

.answer {
    background: #f7f8fa;
    color: #344054;
    border-radius: 12px;
    padding: 1rem;
    white-space: pre-wrap;
    margin-bottom: 1rem;
}

.rating-row {
    display: flex;
    flex-wrap: wrap;
    gap: .5rem;
    margin: .8rem 0;
}

.rating {
    background: #fff7ed;
    color: #9a3412;
    font-size: .78rem;
    padding: .3rem .55rem;
    border-radius: 999px;
}

.delivery-grid {
    display: grid;
    grid-template-columns:
        repeat(
            auto-fit,
            minmax(145px, 1fr)
        );
    gap: .6rem;
    margin: .9rem 0;
}

.metric {
    padding: .7rem .8rem;
    background: #f7f8fa;
    border-radius: 10px;
    color: #475467;
    font-size: .83rem;
}

.feedback-actions {
    margin-top: 2rem;
    display: flex;
    gap: .7rem;
    flex-wrap: wrap;
}

.feedback-btn {
    text-decoration: none;
    padding: .75rem 1rem;
    border-radius: 999px;
    font-weight: 800;
}

.feedback-btn.primary {
    background: #E8612A;
    color: #fff;
}

.feedback-btn.secondary {
    background: #f2f4f7;
    color: #344054;
}

audio {
    width: 100%;
    margin-top: 1rem;
}

@media (max-width: 650px) {
    .feedback-grid {
        grid-template-columns: 1fr;
    }
}
</style>


<div class="feedback-page">

    <a
        class="back-link"
        href="{% url 'interview_saved' %}"
    >
        ← My Interviews
    </a>

    <div class="eyebrow">
        Interview feedback
    </div>

    <h1>
        {% if interview.opportunity %}
            {{ interview.opportunity.title }}
        {% else %}
            {{ interview.occupation }}
        {% endif %}
    </h1>

    <div class="meta">

        {% if interview.opportunity %}
            {{ interview.opportunity.company }}
            ·
        {% endif %}

        {{ interview.get_mode_display }}

        ·

        {{ interview.created_at|date:"j F Y" }}

    </div>


    <section class="feedback-card">

        <h2>
            Overall
        </h2>

        <p>
            {{ feedback.overall_summary|default:interview.feedback_summary }}
        </p>

    </section>


    <div class="feedback-grid">

        <section class="feedback-card">

            <h2>
                What you did well
            </h2>

            <ul>
                {% for item in feedback.strengths %}
                    <li>
                        {{ item }}
                    </li>
                {% empty %}
                    <li>
                        No strengths recorded yet.
                    </li>
                {% endfor %}
            </ul>

        </section>


        <section class="feedback-card">

            <h2>
                What to work on
            </h2>

            <ul>
                {% for item in feedback.improvements %}
                    <li>
                        {{ item }}
                    </li>
                {% empty %}
                    <li>
                        No improvements recorded yet.
                    </li>
                {% endfor %}
            </ul>

        </section>

    </div>


    {% for question in questions %}

    <section class="feedback-card">

        <h2>
            Question {{ forloop.counter }}
        </h2>

        <p>
            <strong>
                {{ question.question_text }}
            </strong>
        </p>


        <div class="answer">
            {{ question.answer_text|default:"No answer recorded." }}
        </div>


        {% if question.delivery_metrics %}

        <div class="delivery-grid">

            <div class="metric">
                Duration:
                {{ question.delivery_metrics.duration_seconds }}
                seconds
            </div>

            <div class="metric">
                Words:
                {{ question.delivery_metrics.word_count }}
            </div>

            <div class="metric">
                Pace:
                {{ question.delivery_metrics.words_per_minute|default:"—" }}
                words/min
            </div>

            <div class="metric">
                Observed pauses:
                {{ question.delivery_metrics.pause_count }}
            </div>

            <div class="metric">
                Longest observed pause:
                {{ question.delivery_metrics.longest_pause_seconds }}
                seconds
            </div>

            <div class="metric">
                Fillers counted:
                {{ question.delivery_metrics.filler_word_count }}
            </div>

        </div>

        {% endif %}


        {% if question.evaluation %}

        <div class="rating-row">

            <span class="rating">
                Relevance:
                {{ question.evaluation.relevance }}
            </span>

            <span class="rating">
                Structure:
                {{ question.evaluation.structure }}
            </span>

            <span class="rating">
                Specificity:
                {{ question.evaluation.specificity }}
            </span>

        </div>


        <h2>
            Feedback
        </h2>

        <p>
            {{ question.evaluation.feedback }}
        </p>


        {% if question.evaluation.strengths %}

        <strong>
            What worked
        </strong>

        <ul>
            {% for item in question.evaluation.strengths %}
                <li>
                    {{ item }}
                </li>
            {% endfor %}
        </ul>

        {% endif %}


        {% if question.evaluation.improvements %}

        <strong>
            What could be stronger
        </strong>

        <ul>
            {% for item in question.evaluation.improvements %}
                <li>
                    {{ item }}
                </li>
            {% endfor %}
        </ul>

        {% endif %}


        {% if question.evaluation.better_approach %}

        <strong>
            Better answer approach
        </strong>

        <p>
            {{ question.evaluation.better_approach }}
        </p>

        {% endif %}

        {% endif %}


        {% if question.audio_file %}

        <audio
            controls
            src="{{ question.audio_file.url }}"
        ></audio>

        {% endif %}

    </section>

    {% endfor %}


    <div class="feedback-actions">

        {% if interview.opportunity %}

        <a
            class="feedback-btn primary"
            href="{% url 'interview_prepare' interview.opportunity.id %}"
        >
            Practise again
        </a>

        {% endif %}

        <a
            class="feedback-btn secondary"
            href="{% url 'interview_saved' %}"
        >
            All interviews
        </a>

    </div>

</div>

{% endblock %}
''')


write("interview/templates/interview/saved.html", r'''
{% extends "dashboard_pages.html" %}

{% block title %}
CareerGPS - My Interviews
{% endblock %}

{% block content %}
<style>
.history-page {
    max-width: 900px;
    margin: 0 auto;
    padding: 2rem 1.5rem 4rem;
}

.history-header {
    display: flex;
    justify-content: space-between;
    gap: 1rem;
    align-items: flex-start;
    margin-bottom: 1.5rem;
}

.eyebrow {
    color: #E8612A;
    font-size: .72rem;
    font-weight: 800;
    letter-spacing: .12em;
    text-transform: uppercase;
}

.history-header h1 {
    margin: .3rem 0;
    font-size: 2rem;
}

.muted {
    color: #667085;
}

.new-btn {
    background: #E8612A;
    color: #fff;
    text-decoration: none;
    padding: .7rem 1rem;
    border-radius: 999px;
    font-weight: 800;
}

.interview-list {
    display: flex;
    flex-direction: column;
    gap: .8rem;
}

.interview-row {
    background: #fff;
    border: 1px solid rgba(0,0,0,.09);
    border-radius: 15px;
    padding: 1.1rem;
    display: flex;
    justify-content: space-between;
    gap: 1rem;
    align-items: center;
}

.row-title {
    font-weight: 800;
}

.row-actions {
    display: flex;
    gap: .5rem;
    flex-wrap: wrap;
    justify-content: flex-end;
}

.row-btn {
    border: 0;
    border-radius: 999px;
    padding: .55rem .8rem;
    text-decoration: none;
    font-size: .85rem;
    font-weight: 750;
    cursor: pointer;
}

.row-btn.primary {
    background: #fff1eb;
    color: #b54708;
}

.row-btn.secondary {
    background: #f2f4f7;
    color: #344054;
}

.row-btn.danger {
    background: #fee4e2;
    color: #b42318;
}

.empty-state {
    background: #fff;
    border: 1px dashed rgba(0,0,0,.14);
    border-radius: 15px;
    padding: 2rem;
    text-align: center;
    color: #667085;
}

@media (max-width: 650px) {
    .interview-row {
        align-items: flex-start;
        flex-direction: column;
    }

    .row-actions {
        justify-content: flex-start;
    }
}
</style>


<div class="history-page">

    <header class="history-header">

        <div>

            <div class="eyebrow">
                Interview Practice
            </div>

            <h1>
                My Interviews
            </h1>

            <div class="muted">
                {{ count }}
                practice session{{ count|pluralize }}
            </div>

        </div>

        <a
            class="new-btn"
            href="{% url 'interview_home' %}"
        >
            New interview
        </a>

    </header>


    {% if interviews %}

    <div class="interview-list">

        {% for interview in interviews %}

        <article
            class="interview-row"
            id="interview-{{ interview.id }}"
        >

            <div>

                <div class="row-title">

                    {% if interview.opportunity %}
                        {{ interview.opportunity.title }}
                    {% else %}
                        {{ interview.occupation }}
                    {% endif %}

                </div>

                <div class="muted">

                    {% if interview.opportunity %}
                        {{ interview.opportunity.company }}
                        ·
                    {% endif %}

                    {{ interview.get_mode_display }}

                    ·

                    {{ interview.question_count }}
                    questions

                    ·

                    {{ interview.created_at|date:"j M Y" }}

                </div>

            </div>


            <div class="row-actions">

                {% if interview.status == "completed" %}

                <a
                    class="row-btn primary"
                    href="{% url 'interview_feedback' interview.id %}"
                >
                    View feedback
                </a>

                {% else %}

                <a
                    class="row-btn primary"
                    href="{% url 'interview_live' interview.id %}"
                >
                    Continue
                </a>

                {% endif %}


                {% if interview.opportunity %}

                <a
                    class="row-btn secondary"
                    href="{% url 'interview_prepare' interview.opportunity.id %}"
                >
                    Practise again
                </a>

                {% endif %}


                <button
                    class="row-btn danger"
                    onclick="deleteInterview({{ interview.id }})"
                >
                    Delete
                </button>

            </div>

        </article>

        {% endfor %}

    </div>

    {% else %}

    <div class="empty-state">
        You haven't started any interview
        practice yet.
    </div>

    {% endif %}

</div>


<script>
const CSRF = "{{ csrf_token }}";


async function deleteInterview(id) {

    const confirmed = confirm(
        "Delete this interview, its answers and recordings?"
    );

    if (!confirmed) {
        return;
    }

    const response =
        await fetch(
            `/interview/api/${id}/delete/`,
            {
                method: "DELETE",

                headers: {
                    "X-CSRFToken":
                        CSRF,
                },
            }
        );

    if (response.ok) {

        const row =
            document.getElementById(
                `interview-${id}`
            );

        if (row) {
            row.remove();
        }

    } else {

        alert(
            "CareerGPS could not delete that interview."
        );
    }
}
</script>

{% endblock %}
''')


write("interview/tests.py", r'''
import json
from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import TestCase, override_settings

from applications.models import Application
from core.models import (
    Opportunity,
    Profile,
    SavedOpportunity,
)

from .models import (
    Interview,
    InterviewQuestion,
)
from .services.ai import (
    InterviewAIUnavailable,
    InterviewPlan,
    InterviewQuestionPlan,
)
from .services.context import (
    interview_choices_for_user,
)
from .services.delivery_metrics import (
    analyse_delivery,
)


def make_opportunity(
    title="Logistics Coordinator",
    company="Example Co",
):
    return Opportunity.objects.create(
        title=title,
        company=company,
        opportunity_type="entry_level",
        opportunity_kind="job",
        career_stage="early_career",
        scope_status="in_scope",
        location="Johannesburg",
        location_text="Johannesburg",
        description=(
            "Coordinate logistics and suppliers."
        ),
        lifecycle_status="active",
        is_active=True,
    )


class InterviewChoiceTests(TestCase):

    def setUp(self):
        self.user = User.objects.create_user(
            "interview-owner",
            password="pass12345",
        )

        Profile.objects.create(
            user=self.user,
            qualification="",
            institution="",
            location="Johannesburg",
        )

    def test_application_is_not_duplicated_in_saved(self):
        opportunity = make_opportunity()

        SavedOpportunity.objects.create(
            user=self.user,
            opportunity=opportunity,
        )

        Application.objects.create(
            user=self.user,
            opportunity=opportunity,
            status="planning",
        )

        choices = interview_choices_for_user(
            self.user
        )

        self.assertEqual(
            len(
                choices["applications"]
            ),
            1,
        )

        self.assertEqual(
            choices[
                "saved_opportunities"
            ],
            [],
        )

    def test_terminal_application_not_in_progress(self):
        opportunity = make_opportunity()

        Application.objects.create(
            user=self.user,
            opportunity=opportunity,
            status="rejected",
        )

        choices = interview_choices_for_user(
            self.user
        )

        self.assertEqual(
            choices["applications"],
            [],
        )


class InterviewCreateTests(TestCase):

    def setUp(self):
        self.user = User.objects.create_user(
            "interview-owner",
            password="pass12345",
        )

        Profile.objects.create(
            user=self.user,
            qualification="",
            institution="",
            location="Johannesburg",
        )

        self.client.login(
            username="interview-owner",
            password="pass12345",
        )

        self.opportunity = (
            make_opportunity()
        )

    @patch(
        "interview.views.generate_interview_plan"
    )
    @patch(
        "interview.views.build_interview_context"
    )
    def test_saved_interview_does_not_create_application(
        self,
        context,
        plan,
    ):
        SavedOpportunity.objects.create(
            user=self.user,
            opportunity=self.opportunity,
        )

        context.return_value = {
            "snapshot": {
                "job": {
                    "title":
                        self.opportunity.title
                },
                "candidate": {
                    "facts": []
                },
            },
            "job_info_status":
                "catalogue_full",
            "candidate_fact_ids": [],
            "generated_document_ids": [],
        }

        plan.return_value = InterviewPlan(
            questions=[
                InterviewQuestionPlan(
                    question=(
                        f"Interview question {index + 1}?"
                    ),
                    question_type="opening",
                    focus="background",
                )
                for index
                in range(5)
            ]
        )

        response = self.client.post(
            "/interview/api/create/",
            data=json.dumps({
                "opportunityId":
                    self.opportunity.id,
                "mode":
                    "written",
                "questionCount":
                    3,
                "popiaConsent":
                    True,
            }),
            content_type="application/json",
        )

        self.assertEqual(
            response.status_code,
            200,
        )

        self.assertFalse(
            Application.objects.filter(
                user=self.user,
                opportunity=(
                    self.opportunity
                ),
            ).exists()
        )

        interview = (
            Interview.objects.get(
                user=self.user
            )
        )

        self.assertEqual(
            interview.source_type,
            "saved",
        )

        self.assertEqual(
            interview.opportunity,
            self.opportunity,
        )

        self.assertEqual(
            interview.questions.count(),
            5,
        )

    def test_unowned_opportunity_denied(self):
        response = self.client.post(
            "/interview/api/create/",
            data=json.dumps({
                "opportunityId":
                    self.opportunity.id,
                "mode":
                    "written",
                "questionCount":
                    3,
                "popiaConsent":
                    True,
            }),
            content_type="application/json",
        )

        self.assertEqual(
            response.status_code,
            403,
        )


class InterviewOwnershipTests(TestCase):

    def setUp(self):
        self.owner = User.objects.create_user(
            "owner",
            password="pass12345",
        )

        self.other = User.objects.create_user(
            "other",
            password="pass12345",
        )

        opportunity = make_opportunity()

        self.interview = (
            Interview.objects.create(
                user=self.owner,
                opportunity=opportunity,
                occupation=(
                    opportunity.title
                ),
                mode="written",
                status="active",
            )
        )

        self.question = (
            InterviewQuestion.objects.create(
                interview=(
                    self.interview
                ),
                question_index=0,
                question_text=(
                    "Tell me about yourself."
                ),
                question_type="opening",
            )
        )

    def test_other_user_cannot_submit_answer(self):
        self.client.login(
            username="other",
            password="pass12345",
        )

        response = self.client.post(
            (
                "/interview/api/question/"
                f"{self.question.id}"
                "/answer-written/"
            ),
            data=json.dumps({
                "answerText":
                    "Not my interview."
            }),
            content_type="application/json",
        )

        self.assertEqual(
            response.status_code,
            404,
        )


class DeliveryMetricTests(TestCase):

    def test_delivery_measurements_are_real_values(self):
        result = analyse_delivery(
            (
                "Um I coordinated supplier "
                "records and uh followed up "
                "on deliveries."
            ),
            duration_seconds=30,
            pause_count=2,
            longest_pause_seconds=2.4,
        )

        self.assertEqual(
            result["pause_count"],
            2,
        )

        self.assertEqual(
            result[
                "longest_pause_seconds"
            ],
            2.4,
        )

        self.assertEqual(
            result[
                "filler_word_count"
            ],
            2,
        )

        self.assertIsNotNone(
            result[
                "words_per_minute"
            ]
        )


class GeminiFallbackTests(TestCase):

    @override_settings(
        GEMINI_API_KEY="primary-key",
        GEMuINI_API_KEY="fallback-key",
    )
    @patch(
        "interview.services.ai._run"
    )
    @patch(
        "interview.services.ai._should_try_fallback",
        return_value=True,
    )
    def test_primary_then_exact_gemuini_fallback(
        self,
        should_retry,
        run,
    ):
        from interview.services.ai import (
            _provider_interaction,
        )

        run.side_effect = [
            RuntimeError(
                "Primary unavailable"
            ),
            '{"ok":true}',
        ]

        result = _provider_interaction(
            "prompt",
            "system",
            {},
        )

        self.assertEqual(
            result,
            '{"ok":true}',
        )

        self.assertEqual(
            run.call_args_list[
                0
            ].args[0],
            "primary-key",
        )

        self.assertEqual(
            run.call_args_list[
                1
            ].args[0],
            "fallback-key",
        )

    @override_settings(
        GEMINI_API_KEY="",
        GEMuINI_API_KEY="",
    )
    def test_no_keys_raises_controlled_message(self):
        from interview.services.ai import (
            _provider_interaction,
        )

        with self.assertRaises(
            InterviewAIUnavailable
        ):
            _provider_interaction(
                "prompt",
                "system",
                {},
            )
''')


print()
print("=" * 72)
print("INTERVIEW REBUILD FILES INSTALLED")
print("=" * 72)
print()
print("Next:")
print(r".\.venv\Scripts\python.exe manage.py makemigrations interview")
print(r".\.venv\Scripts\python.exe manage.py migrate")
print(r".\.venv\Scripts\python.exe manage.py check")
print(r".\.venv\Scripts\python.exe manage.py test interview")