"""Validated, artefact-only edits for saved generated documents."""

from __future__ import annotations

from copy import deepcopy
import re


MAX_FIELD_LENGTH = 10_000
MAX_DOCUMENT_TEXT_LENGTH = 60_000


class DocumentEditError(ValueError):
    pass


def _mapping(value, label):
    if not isinstance(value, dict):
        raise DocumentEditError(f"The saved {label} structure is invalid.")
    return value


def _items(container, key):
    value = container.get(key, [])
    if not isinstance(value, list) or any(not isinstance(item, dict) for item in value):
        raise DocumentEditError(f"The saved {key} section is invalid.")
    return value


def _string_list(value, label):
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise DocumentEditError(f"The saved {label} structure is invalid.")
    return value


def _text(post, name):
    value = str(post.get(name, "")).replace("\r\n", "\n").replace("\r", "\n")
    if len(value) > MAX_FIELD_LENGTH:
        raise DocumentEditError(f"{name.replace('_', ' ').capitalize()} is too long.")
    return value


def _lines(post, name):
    return [line.strip() for line in _text(post, name).splitlines() if line.strip()]


def _check_total(*documents):
    def strings(value):
        if isinstance(value, dict):
            for item in value.values():
                yield from strings(item)
        elif isinstance(value, list):
            for item in value:
                yield from strings(item)
        elif isinstance(value, str):
            yield value

    if sum(len(value) for document in documents for value in strings(document)) > MAX_DOCUMENT_TEXT_LENGTH:
        raise DocumentEditError("The edited document is too long.")


def apply_cv_edits(structured_data, json_resume, post):
    """Return synchronized copies of the two existing saved CV projections."""
    cv = deepcopy(_mapping(structured_data, "CV"))
    resume = deepcopy(_mapping(json_resume, "JSON Resume"))
    basics = _mapping(resume.get("basics", {}), "CV basics")

    cv["summary"] = _text(post, "summary")
    basics["summary"] = cv["summary"]
    resume["basics"] = basics

    work = _items(cv, "work_experience")
    resume_work = _items(resume, "work")
    for index, item in enumerate(work):
        _string_list(item.get("bullets", []), "work bullets")
        bullets = _lines(post, f"work_{index}_bullets")
        item["bullets"] = bullets
        if index < len(resume_work):
            resume_work[index]["summary"] = ""
            resume_work[index]["highlights"] = list(bullets)

    projects = _items(cv, "key_projects")
    resume_projects = _items(resume, "projects")
    for index, item in enumerate(projects):
        _string_list(item.get("bullets", []), "project bullets")
        description = _text(post, f"project_{index}_description")
        bullets = _lines(post, f"project_{index}_bullets")
        item["description"] = description
        item["bullets"] = bullets
        if index < len(resume_projects):
            resume_projects[index]["description"] = description
            resume_projects[index]["highlights"] = list(bullets)

    for key, prefix in (("awards", "award"), ("volunteer", "volunteer")):
        for index, item in enumerate(_items(resume, key)):
            item["summary"] = _text(post, f"{prefix}_{index}_summary")

    certificates = _items(resume, "certificates")
    legacy_certificates = _items(cv, "certifications")
    for index, item in enumerate(certificates):
        display = _text(post, f"certificate_{index}_display")
        item["display"] = display
        if index < len(legacy_certificates):
            legacy_certificates[index]["title"] = display

    skill_groups = _items(cv, "technical_skills")
    skill_names = []
    for index, group in enumerate(skill_groups):
        _string_list(group.get("items", []), "skills")
        group["items"] = _lines(post, f"skills_{index}_items")
        skill_names.extend(group["items"])
    resume_skills = _items(resume, "skills")
    rebuilt_skills = []
    for index, name in enumerate(skill_names):
        item = deepcopy(resume_skills[index]) if index < len(resume_skills) else {"keywords": []}
        item["name"] = name
        rebuilt_skills.append(item)
    resume["skills"] = rebuilt_skills

    _check_total(cv, resume)
    return cv, resume


def apply_cover_letter_edits(structured_data, post):
    letter = deepcopy(_mapping(structured_data, "cover letter"))
    _string_list(letter.get("body_paragraphs", []), "cover letter paragraphs")
    for key in ("salutation", "opening", "closing", "sign_off", "signature_name"):
        if not isinstance(letter.get(key, ""), str):
            raise DocumentEditError(f"The saved {key.replace('_', ' ')} structure is invalid.")
    body = _text(post, "body_paragraphs")
    paragraphs = [
        paragraph.strip()
        for paragraph in re.split(r"\n[ \t]*\n", body)
        if paragraph.strip()
    ]
    if len(paragraphs) > 20:
        raise DocumentEditError("The cover letter has too many body paragraphs.")

    for key in ("salutation", "opening", "closing", "sign_off", "signature_name"):
        letter[key] = _text(post, key)
    letter["body_paragraphs"] = paragraphs
    _check_total(letter)
    return letter
