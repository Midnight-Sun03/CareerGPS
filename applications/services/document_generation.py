from __future__ import annotations

import copy
import json

from django.conf import settings

from CareerGPS_Rebuild.ai_gateway import StructuredTask, run_structured_task
from pydantic import BaseModel, ConfigDict, Field

from .integrity import enforce_numeric_integrity, unsupported_numeric_claims


class FactRewrite(BaseModel):
    model_config = ConfigDict(extra="forbid")
    fact_id: str
    description: str = ""
    bullets: list[str] = Field(default_factory=list)


class CVWritingResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    summary: str
    rewrites: list[FactRewrite] = Field(default_factory=list)
    skill_order: list[str] = Field(default_factory=list)


class CoverLetterResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    date: str = ""
    recipient_name: str = "Hiring Manager"
    company_name: str = ""
    location: str = ""
    salutation: str = "Dear Hiring Manager,"
    opening: str
    body_paragraphs: list[str]
    closing: str
    sign_off: str = "Kind regards,"
    signature_name: str


_CV_SCHEMA = {
    "type": "object",
    "properties": {
        "summary": {"type": "string"},
        "rewrites": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "fact_id": {"type": "string"},
                    "description": {"type": "string"},
                    "bullets": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["fact_id", "description", "bullets"],
            },
        },
        "skill_order": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["summary", "rewrites", "skill_order"],
}


_COVER_SCHEMA = {
    "type": "object",
    "properties": {
        "date": {"type": "string"},
        "recipient_name": {"type": "string"},
        "company_name": {"type": "string"},
        "location": {"type": "string"},
        "salutation": {"type": "string"},
        "opening": {"type": "string"},
        "body_paragraphs": {"type": "array", "items": {"type": "string"}},
        "closing": {"type": "string"},
        "sign_off": {"type": "string"},
        "signature_name": {"type": "string"},
    },
    "required": [
        "date", "recipient_name", "company_name", "location", "salutation",
        "opening", "body_paragraphs", "closing", "sign_off", "signature_name",
    ],
}


_SYSTEM = """You are CareerGPS's controlled document writer.

CareerGPS provides candidate facts and job information. Candidate facts are the ONLY source of factual claims about the candidate. Job information describes the employer's vacancy and must never be converted into candidate experience, qualifications, skills, achievements, or years.

You may improve wording, ordering, emphasis, concision, and relevance. You may not invent or upgrade factual substance.

Never invent or upgrade employers, job titles, responsibilities, experience duration, qualifications, institutions, certifications, licences, skills, projects, achievements, leadership, dates, locations, metrics, percentages, awards, or results.

Do not turn a project into employment. Do not turn coursework into professional experience. Do not turn a requested job skill into a candidate skill. Missing information is acceptable. False information is not.
"""


def _interaction(prompt, schema, *, task_name="document"):
    return run_structured_task(StructuredTask(
        name=task_name,
        prompt=prompt,
        system_instruction=_SYSTEM,
        schema=schema,
        gemini_model=settings.DOCUMENT_AI_MODEL,
        thinking_level=settings.DOCUMENT_AI_THINKING_LEVEL,
        timeout_seconds=settings.DOCUMENT_AI_TIMEOUT_SECONDS,
    ))


def _allowed_rewrite_ids(candidate_facts):
    return {
        fact["id"]
        for fact in candidate_facts
        if fact["type"] in {"work", "project", "volunteering", "achievement"}
    }


def _cv_claim_payload(result):
    """Return only generated text that can make claims about the candidate."""
    return {
        "summary": result.summary,
        "rewrites": [
            {"description": rewrite.description, "bullets": rewrite.bullets}
            for rewrite in result.rewrites
        ],
    }


def _cover_letter_claim_payload(result):
    """Exclude document metadata; inspect only prose that can claim candidate facts."""
    return {
        "opening": result.opening,
        "body_paragraphs": result.body_paragraphs,
        "closing": result.closing,
    }


def _numeric_repair_instruction(unsupported):
    tokens = ", ".join(unsupported)
    return (
        "\n\nINTEGRITY REPAIR REQUIRED:\n"
        "The previous draft was rejected because it introduced numeric token(s) "
        f"not present in the candidate facts: {tokens}.\n"
        "Return the COMPLETE JSON response again. Remove those unsupported numeric "
        "claims. Do not replace them with different numbers and do not infer a number "
        "from the job advert. Use non-numeric wording or omit the claim. All candidate "
        "facts must remain grounded only in CANDIDATE FACTS.\n"
    )


def generate_cv_writing(candidate_bundle, selected_facts, job_data, *, additional_notes=""):
    prompt = (
        "Tailor the candidate's CV wording for this job.\n\n"
        "Return:\n"
        "- a concise professional summary grounded only in candidate facts;\n"
        "- optional rewrites for supplied work/project/volunteering/achievement fact IDs;\n"
        "- the candidate's existing skill names ordered by relevance.\n\n"
        "Do not create new fact IDs. Do not create new skills. If a fact is already clear, it may be omitted from rewrites.\n\n"
        "CANDIDATE FACTS:\n"
        + json.dumps(selected_facts, ensure_ascii=False, indent=2)
        + "\n\nJOB INFORMATION:\n"
        + json.dumps(job_data, ensure_ascii=False, indent=2)
        + "\n\nUSER NOTES:\n"
        + (additional_notes or "[none]")
    )
    allowed_ids = _allowed_rewrite_ids(candidate_bundle["facts"])
    actual_skills = [
        fact["data"].get("name", "")
        for fact in selected_facts
        if fact.get("type") == "skill" and fact.get("data", {}).get("name")
    ]
    lookup = {skill.casefold(): skill for skill in actual_skills}

    def parse_and_constrain(raw_text):
        result = CVWritingResult.model_validate_json(raw_text)
        result.rewrites = [
            rewrite for rewrite in result.rewrites if rewrite.fact_id in allowed_ids
        ]
        ordered = []
        for skill in result.skill_order:
            real = lookup.get(str(skill).strip().casefold())
            if real and real not in ordered:
                ordered.append(real)
        for skill in actual_skills:
            if skill not in ordered:
                ordered.append(skill)
        result.skill_order = ordered
        return result

    integrity_bundle = copy.deepcopy(candidate_bundle)
    if additional_notes.strip():
        integrity_bundle["facts"].append({
            "id": "user_notes:generation",
            "type": "user_notes",
            "text": additional_notes.strip(),
            "data": {"text": additional_notes.strip()},
        })

    result = parse_and_constrain(_interaction(prompt, _CV_SCHEMA, task_name="cv_writing"))
    unsupported = unsupported_numeric_claims(_cv_claim_payload(result), integrity_bundle)
    if unsupported:
        result = parse_and_constrain(
            _interaction(
                prompt + _numeric_repair_instruction(unsupported),
                _CV_SCHEMA,
                task_name="cv_writing",
            )
        )

    enforce_numeric_integrity(_cv_claim_payload(result), integrity_bundle)
    return result


def generate_cover_letter(candidate_bundle, selected_facts, job_data, *, additional_notes="", tone="professional"):
    name = ""
    for fact in candidate_bundle["facts"]:
        if fact["type"] == "basic":
            name = fact["data"].get("name", "")
            break

    prompt = (
        "Write a tailored cover letter using only the supplied candidate facts and job information. "
        "Do not simply repeat the CV. Connect genuine candidate evidence to the employer's needs. "
        "Keep the tone " + tone + ". Avoid generic opening phrases such as 'I am writing to apply'.\n\n"
        "The signature_name must be exactly this candidate name: " + name + "\n\n"
        "CANDIDATE FACTS:\n"
        + json.dumps(selected_facts, ensure_ascii=False, indent=2)
        + "\n\nJOB INFORMATION:\n"
        + json.dumps(job_data, ensure_ascii=False, indent=2)
        + "\n\nUSER NOTES:\n"
        + (additional_notes or "[none]")
    )
    def parse_and_constrain(raw_text):
        result = CoverLetterResult.model_validate_json(raw_text)
        result.signature_name = name
        if job_data.get("employer"):
            result.company_name = job_data["employer"]
        if job_data.get("location"):
            result.location = job_data["location"]
        return result

    integrity_bundle = copy.deepcopy(candidate_bundle)
    if additional_notes.strip():
        integrity_bundle["facts"].append({
            "id": "user_notes:generation",
            "type": "user_notes",
            "text": additional_notes.strip(),
            "data": {"text": additional_notes.strip()},
        })

    result = parse_and_constrain(_interaction(prompt, _COVER_SCHEMA, task_name="cover_letter"))
    unsupported = unsupported_numeric_claims(_cover_letter_claim_payload(result), integrity_bundle)
    if unsupported:
        result = parse_and_constrain(
            _interaction(
                prompt + _numeric_repair_instruction(unsupported),
                _COVER_SCHEMA,
                task_name="cover_letter",
            )
        )

    enforce_numeric_integrity(_cover_letter_claim_payload(result), integrity_bundle)
    return result


def assemble_legacy_cv(candidate_bundle, json_resume, writing):
    rewrite_by_id = {rewrite.fact_id: rewrite for rewrite in writing.rewrites}
    basics = json_resume.get("basics", {})
    result = {
        "full_name": basics.get("name", ""),
        "email": basics.get("email", ""),
        "phone": basics.get("phone", ""),
        "location": (basics.get("location") or {}).get("address", ""),
        "summary": writing.summary,
        "key_projects": [],
        "work_experience": [],
        "education": [],
        "relevant_coursework": [],
        "certifications": [],
        "technical_skills": [],
        "soft_skills": [],
    }

    facts = {fact["id"]: fact for fact in candidate_bundle["facts"]}

    for project in json_resume.get("projects", []):
        fact_id = project.get("x_careergps_fact_id", "")
        fact = facts.get(fact_id, {})
        data = fact.get("data", {})
        rewrite = rewrite_by_id.get(fact_id)
        result["key_projects"].append({
            "title": project.get("name", ""),
            "organisation": data.get("organisation", ""),
            "tech_stack": "",
            "dates": data.get("dates", ""),
            "description": rewrite.description if rewrite and rewrite.description else project.get("description", ""),
            "bullets": rewrite.bullets if rewrite and rewrite.bullets else project.get("highlights", []),
        })

    for work in json_resume.get("work", []):
        fact_id = work.get("x_careergps_fact_id", "")
        fact = facts.get(fact_id, {})
        data = fact.get("data", {})
        rewrite = rewrite_by_id.get(fact_id)
        bullets = rewrite.bullets if rewrite and rewrite.bullets else data.get("bullets", [])
        if rewrite and rewrite.description:
            bullets = [rewrite.description, *bullets]
        organisation = work.get("name", "")
        result["work_experience"].append({
            "title": work.get("position", ""),
            # Keep both names during the legacy-renderer transition. The old
            # DOCX/PDF builders read ``organisation`` while the newer resume
            # template adapter accepts ``company``. Both point to the same
            # trusted candidate fact; no new factual content is introduced.
            "organisation": organisation,
            "company": organisation,
            "dates": data.get("dates", ""),
            "bullets": bullets,
        })

    for edu in json_resume.get("education", []):
        fact_id = edu.get("x_careergps_fact_id", "")
        fact = facts.get(fact_id, {})
        data = fact.get("data", {})
        qualification = " ".join(
            piece for piece in [edu.get("studyType", ""), edu.get("area", "")] if piece
        ).strip()
        start = str(edu.get("startDate", "") or "").strip()
        end = "Present" if data.get("current") else str(edu.get("endDate", "") or "").strip()
        dates = " - ".join(value for value in (start, end) if value)
        result["education"].append({
            "qualification": qualification,
            "institution": edu.get("institution", ""),
            "dates": dates,
        })

    for cert in json_resume.get("certificates", []):
        fact_id = cert.get("x_careergps_fact_id", "")
        fact = facts.get(fact_id, {})
        data = fact.get("data", {})
        title = " - ".join(
            piece for piece in [cert.get("name", ""), cert.get("issuer", "")] if piece
        )
        result["certifications"].append({
            "title": title,
            "dates": data.get("dates", ""),
        })

    if writing.skill_order:
        result["technical_skills"] = [{"category": "Skills", "items": writing.skill_order}]

    return result
