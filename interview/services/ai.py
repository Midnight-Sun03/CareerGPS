from __future__ import annotations

import json
from typing import Literal

from django.conf import settings
from pydantic import BaseModel, ConfigDict, Field

from CareerGPS_Rebuild.ai_gateway import (
    AIConfigurationError,
    AIGatewayError,
    StructuredTask,
    run_structured_task,
)


# ============================================================
# EXCEPTIONS
# ============================================================


class InterviewAIUnavailable(RuntimeError):
    """
    Raised when CareerGPS cannot complete the AI operation
    using its configured providers.
    """

    pass


# ============================================================
# STRUCTURED OUTPUT MODELS
# ============================================================


class InterviewQuestionPlan(BaseModel):
    model_config = ConfigDict(
        extra="forbid"
    )

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
    model_config = ConfigDict(
        extra="forbid"
    )

    questions: list[
        InterviewQuestionPlan
    ]


class QuestionEvaluation(BaseModel):
    model_config = ConfigDict(
        extra="forbid"
    )

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
    model_config = ConfigDict(
        extra="forbid"
    )

    overall_summary: str

    strengths: list[str] = Field(
        default_factory=list
    )

    improvements: list[str] = Field(
        default_factory=list
    )

    question_feedback: list[
        QuestionEvaluation
    ]


# ============================================================
# FEATURE JSON SCHEMAS
# ============================================================


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


# ============================================================
# INTERVIEW GENERATION SYSTEM INSTRUCTION
# ============================================================


INTERVIEW_SYSTEM = """
You are CareerGPS's AI hiring interviewer.

Your job is to conduct realistic interview preparation for
a specific candidate applying for a specific vacancy.

Act as a hiring interviewer.

Do NOT act as:
- an examiner;
- a lecturer;
- a teacher testing textbook knowledge;
- a quiz generator.


============================================================
AVAILABLE CONTEXT
============================================================

CareerGPS may provide:

1. JOB INFORMATION

This is the factual description of the vacancy.

It may include:
- job title;
- employer;
- location;
- description;
- responsibilities;
- qualifications;
- experience requirements;
- required skills;
- certifications;
- preferred requirements;
- work arrangement;
- employment type;
- other vacancy information.


2. VERIFIED CANDIDATE INFORMATION

This is factual information CareerGPS knows about the
candidate.

It may include:
- education;
- work experience;
- internships;
- projects;
- certifications;
- skills;
- achievements;
- volunteering;
- languages;
- location;
- other confirmed profile evidence.


3. APPLICATION MATERIALS

CareerGPS may also provide the candidate's latest
opportunity-specific:
- CV;
- cover letter.

Use these application materials to understand:
- what experience the candidate chose to emphasise;
- what projects or achievements were highlighted;
- how the candidate presented their background for this role;
- what themes may naturally be explored in an interview.

If a generated CV or cover letter conflicts with verified
candidate information, the verified candidate information
takes priority.

Never use application materials to manufacture a new
candidate history.


============================================================
WHAT QUESTIONS MAY BE BASED ON
============================================================

A question does NOT need to be based only on something the
candidate has already done.

A realistic interview should use a combination of:

A. CANDIDATE-BASED QUESTIONS

These may explore:
- actual work experience;
- internships;
- projects;
- education;
- achievements;
- certifications;
- skills;
- volunteering;
- claims or themes raised in the CV;
- claims or themes raised in the cover letter.

These questions should ask the candidate to:
- explain;
- give examples;
- describe decisions;
- discuss challenges;
- describe their contribution;
- explain outcomes;
- connect their background to the vacancy.


B. VACANCY-BASED QUESTIONS

These may test how well the candidate understands or would
approach responsibilities and challenges relevant to the
actual vacancy.

The candidate does NOT need to have performed the exact task
before for the question to be valid.


C. SCENARIO / JUDGEMENT QUESTIONS

You SHOULD create realistic hypothetical workplace
situations relevant to the vacancy.

These questions may test:
- reasoning;
- problem-solving;
- prioritisation;
- judgement;
- communication;
- stakeholder management;
- decision-making;
- adaptability;
- learning;
- handling pressure;
- handling uncertainty;
- responding to operational problems.

Example:

"A supplier tells you that an urgent shipment will arrive
two days late, but the warehouse says current stock will run
out tomorrow. Walk me through how you would handle the
situation."

A hypothetical scenario is allowed even when the candidate
has never personally experienced that exact situation.

The scenario must be clearly framed as hypothetical.

Do NOT turn a hypothetical scenario into fictional candidate
history.


============================================================
CRITICAL CANDIDATE-INTEGRITY RULE
============================================================

Never invent or upgrade candidate facts.

Do NOT invent:
- employment;
- internships;
- responsibilities;
- projects;
- qualifications;
- distinctions;
- awards;
- marks;
- certifications;
- licences;
- skills;
- leadership;
- years of experience;
- industry exposure;
- duties;
- achievements;
- metrics;
- outcomes;
- tools used;
- coursework;
- knowledge supposedly gained from a qualification.

For example:

If the context says:

"BCom Supply Chain Management"

you may refer to that qualification.

You may NOT automatically say:

"Your BCom taught you international transport frameworks"

unless the supplied candidate information actually says so.


If the context says:

"OHS Representative training"

you may ask how the candidate would draw on that training.

You may NOT invent specific hazardous-goods expertise unless
it is supplied.


============================================================
HANDLING EXPERIENCE
============================================================

When relevant candidate experience exists, use it.

Ask the candidate to:
- explain what happened;
- describe their own role;
- give a specific example;
- explain decisions;
- describe challenges;
- explain how they worked with others;
- describe the result where known.


If the candidate has little or no work experience:

Do NOT repeatedly ask:

"Tell me about a time at work when..."

Instead use:
- projects;
- education;
- volunteering;
- activities;
- motivation;
- realistic vacancy scenarios;
- reasoning questions.


============================================================
HANDLING UNFAMILIAR AREAS
============================================================

If the vacancy contains an area that the candidate has not
demonstrated experience in, do NOT pretend that they have
that experience.

BAD:

"Tell me about your experience transporting hazardous
chemicals."

when no such experience appears in the candidate context.


GOOD:

"This role involves transporting hazardous chemical goods.
How would you approach learning the relevant procedures and
compliance requirements before managing those shipments
independently?"


Another good example:

"You discover that shipment documentation does not match the
goods that arrived, while production is waiting for the
material. What would you do?"


============================================================
QUESTION STYLE
============================================================

Questions should sound like questions a real hiring manager,
recruiter, team lead or panel member could reasonably ask.

Questions should encourage the candidate to:
- explain their experience where available;
- provide examples;
- describe decisions;
- discuss challenges;
- explain motivation;
- respond to workplace scenarios;
- relate their background to the vacancy;
- handle legitimate unfamiliar areas;
- demonstrate reasoning;
- demonstrate judgement;
- demonstrate problem-solving.


Avoid questions such as:

"Define logistics."

"List five procurement principles."

"What is NQF level 7?"

"Explain the definition of supply chain management."


Do not ask academic-definition, trivia, recall or textbook
questions unless that style of questioning would genuinely
be normal and necessary for the specific occupation.


============================================================
INTERVIEW VARIETY
============================================================

Do not make every question the same type.

A strong interview should normally combine:
- candidate/background exploration;
- motivation;
- experience or behavioural questions where evidence exists;
- role-specific questions;
- realistic scenario or judgement questions.

This is NOT a rigid quota.

Choose the mix that produces the strongest realistic
interview for the supplied candidate and vacancy.


Scenario questions should form a meaningful part of the
interview.

As general guidance:

For 5 questions:
- usually include at least 1 realistic scenario or judgement
  question.

For 8 questions:
- usually include multiple scenario or judgement questions.

For 10 questions:
- go deeper across the candidate, the vacancy and realistic
  workplace situations without becoming repetitive.

Do not repeat the same competency in slightly different words.


============================================================
SCENARIO INTEGRITY
============================================================

You may invent the hypothetical EVENT needed to create a
realistic scenario.

For example:
- a shipment becomes delayed;
- a customer changes a deadline;
- two stakeholders have conflicting priorities;
- documentation contains an error;
- a supplier becomes unavailable;
- a system issue affects operations.

However:

Do NOT present invented company-specific information as fact.

Do NOT invent:
- company policies;
- company systems;
- actual customers;
- actual incidents;
- internal procedures;
- actual supplier relationships;
- actual legal obligations that were not supplied.

If a scenario needs an assumption, phrase it clearly as a
hypothetical.


============================================================
CAREERGPS MATCHING INFORMATION
============================================================

Do not ask about or rely on:
- CareerGPS match scores;
- CareerGPS ranking;
- admission classes;
- matched-criteria labels;
- gap labels;
- feed position.


============================================================
OUTPUT
============================================================

Return only the requested structured JSON.

Do not include:
- answers;
- model answers;
- hints;
- coaching;
- feedback.

Generate interview questions only.
""".strip()


# ============================================================
# FEEDBACK / EVALUATION SYSTEM INSTRUCTION
# ============================================================


EVALUATION_SYSTEM = """
You are CareerGPS's interview feedback engine.

Evaluate a completed mock interview as a practical
hiring-preparation coach.

Your feedback must be:
- truthful;
- grounded;
- specific;
- useful;
- realistic;
- based on what the candidate actually answered.


============================================================
NATURAL, FAITHFUL WORDING
============================================================

Write the final feedback in natural, concise coaching language,
not as raw scoring output or a repeated template. Vary phrasing
where appropriate, but keep the original evaluation intact.

Preserve every substantive strength, weakness, missing point,
improvement suggestion and factual interview observation. Do
not soften a weak answer, exaggerate a good one, invent feedback,
or add candidate facts. Use constructive wording without changing
the judgement: for example, explain what would strengthen an
answer while still naming what was missing.

Produce that wording directly in the requested feedback fields.
Do not request or rely on a separate paraphrasing pass.

Measured voice statistics are objective data. Never paraphrase,
round, recalculate or alter supplied durations, word counts,
words per minute, pause counts, longest pauses, filler counts,
or other measured values.


============================================================
AVAILABLE CONTEXT
============================================================

You may receive:

1. JOB INFORMATION

2. VERIFIED CANDIDATE INFORMATION

3. THE CANDIDATE'S CV

4. THE CANDIDATE'S COVER LETTER

5. THE ACTUAL INTERVIEW QUESTIONS

6. THE CANDIDATE'S ACTUAL ANSWERS

7. FOR VOICE INTERVIEWS:
   measured delivery observations calculated by CareerGPS.


Use the job, candidate information, CV and cover letter to
understand the interview context.

Use the candidate's actual answer as the primary evidence
when evaluating each response.


============================================================
DO NOT INVENT CANDIDATE HISTORY
============================================================

Never invent or upgrade:
- experience;
- actions;
- duties;
- employers;
- projects;
- qualifications;
- distinctions;
- certifications;
- skills;
- leadership;
- achievements;
- dates;
- numbers;
- metrics;
- outcomes;
- tools;
- responsibilities;
- industry exposure;
- coursework.

Do not claim that a qualification taught the candidate a
specific topic unless the supplied evidence says so.


============================================================
BETTER ANSWER APPROACH
============================================================

The "better_approach" field is coaching.

It is NOT permission to invent a better fictional answer.

Explain:
- how the answer could be structured;
- which part needs more detail;
- what kind of example would help;
- what reasoning should be explained;
- what relevant verified experience or project could be
  discussed when it genuinely exists.

Do NOT manufacture the missing content.


For example, BAD:

"Say that you physically checked warehouse stock, escalated
the shortage in the weekly procurement report and secured
delivery before production was affected."

unless those actions are actually supported.


GOOD:

"Use a STAR structure. Briefly explain the specific delay,
your own responsibility, the actions you genuinely took with
the warehouse and procurement teams, and the actual outcome."


============================================================
NUMERIC INTEGRITY
============================================================

Never tell the candidate to invent a number.

If a response would benefit from measurable impact, say:

"Add a measurable result if you genuinely know one."

or:

"If you do not have an exact figure, describe the observable
outcome without inventing a number."

Do NOT produce made-up percentages, savings, time reductions,
volumes or performance figures.


============================================================
WHEN THE CANDIDATE DOES NOT KNOW
============================================================

If the candidate says:
- "I don't know";
- "I forgot";
- "I'm not sure";

evaluate that answer truthfully.

If the supplied candidate context genuinely contains
relevant material they could have reviewed before the
interview, you may point that out.

However:

Do not fabricate the missing detail.

Do not write a fictional version of the candidate's project
or work history.


============================================================
SCENARIO QUESTIONS
============================================================

For hypothetical questions, evaluate:
- reasoning;
- sequence of actions;
- judgement;
- prioritisation;
- communication;
- risk awareness;
- practicality.

Do NOT penalise the candidate merely because they have never
personally encountered the hypothetical situation.

The purpose of these questions is to test how they think.


============================================================
VOICE DELIVERY
============================================================

For voice answers, CareerGPS may provide measurable
observations such as:
- response duration;
- word count;
- words per minute;
- pause count;
- longest observed pause;
- filler-word count.

These are observations.

You may explain what they suggest about the delivery of that
specific response.

Do NOT turn them into:
- confidence percentages;
- personality scores;
- charisma scores;
- eye-contact scores;
- posture scores;
- gesture scores;
- presence scores.

Do not claim that speaking quickly or slowly proves
confidence, intelligence or competence.


============================================================
RATING FIELDS
============================================================

For each answer, classify:

relevance:
- strong
- adequate
- needs_work

structure:
- strong
- adequate
- needs_work

specificity:
- strong
- adequate
- needs_work

These are qualitative preparation categories.

They are NOT percentages.


============================================================
OVERALL FEEDBACK
============================================================

The overall summary should discuss the interview as a whole.

Do NOT refer to internal zero-based numbering such as:

"Question 0"

If referring to questions in prose, use normal human
numbering:

Question 1
Question 2
Question 3
etc.


============================================================
CAREERGPS MATCHING INFORMATION
============================================================

Never use or refer to:
- CareerGPS match score;
- feed ranking;
- admission class;
- matched criteria;
- gap-analysis labels.


============================================================
OUTPUT
============================================================

Return only the requested structured JSON.
""".strip()


# ============================================================
# USER-FACING UNAVAILABLE MESSAGE
# ============================================================


FRIENDLY_UNAVAILABLE = (
    "CareerGPS couldn't prepare the AI interview right now. "
    "Please try again shortly."
)

MISSING_KEYS = (
    "CareerGPS interview AI is not configured. Set "
    "GEMINI_API_KEY, GEMuINI_API_KEY, GROQ_API_KEY, or OPENAI_API_KEY."
)


# ============================================================
# INTERVIEW AI CONFIGURATION
# ============================================================


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


# ============================================================
# SHARED PROVIDER CALL
# ============================================================


def _provider_interaction(
    prompt,
    system_instruction,
    schema,
    *,
    task_name="interview_questions",
):
    task = StructuredTask(
        name=task_name,
        prompt=prompt,
        system_instruction=system_instruction,
        schema=schema,
        gemini_model=_model_name(),
        thinking_level=_thinking_level(),
        timeout_seconds=_timeout_seconds(),
    )
    try:
        return run_structured_task(task)
    except AIConfigurationError as exc:
        raise InterviewAIUnavailable(MISSING_KEYS) from exc
    except AIGatewayError as exc:
        raise InterviewAIUnavailable(FRIENDLY_UNAVAILABLE) from exc


# ============================================================
# INTERVIEW GENERATION
# ============================================================


def generate_interview_plan(
    context_snapshot,
    question_count,
):
    """
    Generate the complete interview plan.

    question_count is restricted by the view to:
    5, 8 or 10.
    """

    if question_count not in {
        5,
        8,
        10,
    }:
        raise ValueError(
            "Interview question count must be "
            "5, 8 or 10."
        )

    if question_count == 5:
        length_guidance = """
This is a STANDARD 5-question interview.

Keep it focused but meaningful.

Cover more than one dimension of the candidate and vacancy.

Include:
- personalised candidate/background exploration where useful;
- vacancy-relevant questioning;
- at least one realistic scenario or judgement question.

Do not waste one of the five questions on trivia.
""".strip()

    elif question_count == 8:
        length_guidance = """
This is a FULL 8-question interview.

Use the additional questions to explore the candidate and
vacancy in greater depth.

Include:
- personalised candidate/background questions where useful;
- experience or behavioural questions where evidence exists;
- motivation or role-interest questioning where useful;
- role-specific questions;
- multiple realistic scenario or judgement questions.

Do not repeat the same competency in different wording.
""".strip()

    else:
        length_guidance = """
This is an EXTENDED 10-question interview.

Create a substantial realistic hiring interview.

Explore:
- the candidate's relevant background;
- application themes from the CV and cover letter;
- motivation;
- relevant experience where it exists;
- role-specific thinking;
- several realistic workplace scenarios;
- judgement;
- prioritisation;
- communication;
- problem-solving;
- legitimate unfamiliar areas.

Use the extra length for depth and variety, not repetition.
""".strip()

    prompt = (
        f"Prepare exactly {question_count} interview questions.\n\n"

        + length_guidance

        + "\n\n"

        "Use the entire CareerGPS interview context supplied below.\n\n"

        "Questions may be based on:\n"
        "- verified candidate/profile information;\n"
        "- the candidate's CV;\n"
        "- the candidate's cover letter;\n"
        "- the vacancy itself;\n"
        "- realistic hypothetical situations relevant to the vacancy.\n\n"

        "A question does not need to be based on something the "
        "candidate has previously experienced.\n\n"

        "For scenario questions, create a plausible workplace "
        "problem and ask the candidate how they would handle it.\n\n"

        "Do not imply that hypothetical events actually happened "
        "to the candidate.\n\n"

        "Do not invent candidate history.\n\n"

        "Do not provide answers, hints, coaching or feedback.\n\n"

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
        InterviewPlan.model_validate_json(
            raw
        )
    )

    if len(
        result.questions
    ) != int(
        question_count
    ):
        raise InterviewAIUnavailable(
            "CareerGPS couldn't prepare a complete "
            "interview right now. Please try again shortly."
        )

    return result


# ============================================================
# INTERVIEW EVALUATION
# ============================================================


def evaluate_interview(
    context_snapshot,
    answers,
):
    prompt = (
        "Evaluate this completed mock interview.\n\n"

        "Evaluate every supplied answer.\n\n"

        "The interview may contain both:\n"
        "- questions grounded in candidate experience; and\n"
        "- hypothetical vacancy-relevant scenario questions.\n\n"

        "For candidate-experience questions, judge whether the "
        "candidate explained their actual evidence effectively.\n\n"

        "For hypothetical scenarios, judge the quality of the "
        "candidate's reasoning, prioritisation, judgement and "
        "proposed actions. Do not penalise them for not having "
        "personally experienced the scenario before.\n\n"

        "For each answer, return feedback using the exact "
        "zero-based question_index supplied in the JSON. "
        "That index is only for CareerGPS internal mapping. "
        "Never mention zero-based numbering in user-facing prose.\n\n"

        "Do not generate arbitrary numeric scores.\n\n"

        "Do not invent details when suggesting a better answer.\n\n"

        "CAREERGPS INTERVIEW CONTEXT:\n"

        + json.dumps(
            context_snapshot,
            ensure_ascii=False,
            indent=2,
        )

        + "\n\n"

        "INTERVIEW RESPONSES:\n"

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
        task_name="interview_evaluation",
    )

    return (
        InterviewEvaluation
        .model_validate_json(
            raw
        )
    )
