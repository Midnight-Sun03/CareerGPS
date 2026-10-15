from __future__ import annotations

from core.profile_projection import get_profile_projection


def _date(value):
    return value.isoformat() if value else ""


def _range(start, end, current=False):
    left = _date(start)
    right = "Present" if current else _date(end)
    return " - ".join(value for value in (left, right) if value)


def build_candidate_fact_bundle(profile, *, raw_cv_text=""):
    user = profile.user
    projection = get_profile_projection(profile)
    facts = []

    facts.append({
        "id": "basic:identity",
        "type": "basic",
        "text": " | ".join(value for value in [
            user.get_full_name() or user.username,
            user.email,
            profile.phone,
            profile.location,
        ] if value),
        "data": {
            "name": user.get_full_name() or user.username,
            "email": user.email,
            "phone": profile.phone,
            "location": profile.location,
        },
    })

    for item in profile.education_records.filter(confirmed_by_user=True):
        facts.append({
            "id": f"education:{item.id}",
            "type": "education",
            "text": " | ".join(value for value in [
                item.qualification,
                item.field_of_study,
                item.institution,
                _range(item.start_date, item.end_date, item.currently_studying),
                item.description,
            ] if value),
            "data": {
                "qualification": item.qualification,
                "field_of_study": item.field_of_study,
                "institution": item.institution,
                "start_date": _date(item.start_date),
                "end_date": _date(item.end_date),
                "current": item.currently_studying,
                "description": item.description,
            },
        })

    for item in profile.work_experiences.filter(confirmed_by_user=True):
        facts.append({
            "id": f"work:{item.id}",
            "type": "work",
            "text": " | ".join(value for value in [
                item.title,
                item.organisation,
                item.location,
                item.employment_type,
                item.dates or _range(item.start_date, item.end_date, item.currently_working),
                item.description,
                " ; ".join(item.bullet_list()),
            ] if value),
            "data": {
                "title": item.title,
                "organisation": item.organisation,
                "location": item.location,
                "employment_type": item.employment_type,
                "dates": item.dates or _range(item.start_date, item.end_date, item.currently_working),
                "description": item.description,
                "bullets": item.bullet_list(),
            },
        })

    for item in profile.projects.filter(confirmed_by_user=True):
        facts.append({
            "id": f"project:{item.id}",
            "type": "project",
            "text": " | ".join(value for value in [
                item.title,
                item.organisation,
                item.dates or _range(item.start_date, item.end_date, item.currently_ongoing),
                item.description,
                " ; ".join(item.bullet_list()),
            ] if value),
            "data": {
                "title": item.title,
                "organisation": item.organisation,
                "dates": item.dates or _range(item.start_date, item.end_date, item.currently_ongoing),
                "description": item.description,
                "bullets": item.bullet_list(),
                "url": item.project_url,
            },
        })

    for item in profile.certifications.filter(confirmed_by_user=True):
        facts.append({
            "id": f"certification:{item.id}",
            "type": "certification",
            "text": " | ".join(value for value in [
                item.title,
                item.issuer,
                item.dates,
                item.description,
            ] if value),
            "data": {
                "title": item.title,
                "issuer": item.issuer,
                "dates": item.dates,
                "description": item.description,
                "credential_url": item.credential_url,
            },
        })

    for item in profile.achievements.filter(confirmed_by_user=True):
        facts.append({
            "id": f"achievement:{item.id}",
            "type": "achievement",
            "text": " | ".join(value for value in [
                item.title,
                item.organisation,
                _date(item.date),
                item.description,
            ] if value),
            "data": {
                "title": item.title,
                "organisation": item.organisation,
                "date": _date(item.date),
                "description": item.description,
            },
        })

    for item in profile.volunteering_records.filter(confirmed_by_user=True):
        facts.append({
            "id": f"volunteering:{item.id}",
            "type": "volunteering",
            "text": " | ".join(value for value in [
                item.role,
                item.organisation,
                item.location,
                _range(item.start_date, item.end_date, item.currently_volunteering),
                item.description,
            ] if value),
            "data": {
                "role": item.role,
                "organisation": item.organisation,
                "location": item.location,
                "dates": _range(item.start_date, item.end_date, item.currently_volunteering),
                "description": item.description,
            },
        })

    for item in profile.languages.filter(confirmed_by_user=True):
        facts.append({
            "id": f"language:{item.id}",
            "type": "language",
            "text": " | ".join(value for value in [item.language, item.get_proficiency_display()] if value),
            "data": {
                "language": item.language,
                "proficiency": item.get_proficiency_display(),
            },
        })

    for skill in profile.profile_skills.filter(confirmed_by_user=True):
        facts.append({
            "id": f"skill:{skill.id}",
            "type": "skill",
            "text": skill.entered_name,
            "data": {"name": skill.entered_name},
        })

    for link in profile.profile_links.filter(confirmed_by_user=True):
        facts.append({
            "id": f"link:{link.id}",
            "type": "link",
            "text": f"{link.label} | {link.url}",
            "data": {"label": link.label, "type": link.link_type, "url": link.url},
        })

    if raw_cv_text.strip():
        facts.append({
            "id": "candidate_cv:user_supplied",
            "type": "candidate_cv",
            "text": raw_cv_text.strip(),
            "data": {"text": raw_cv_text.strip()},
        })

    return {
        "profile_id": profile.id,
        "qualification": projection["qualification"],
        "institution": projection["institution"],
        "skill_names": projection["skill_names"],
        "facts": facts,
    }
