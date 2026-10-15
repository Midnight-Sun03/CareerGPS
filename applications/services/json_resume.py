from __future__ import annotations


def build_json_resume(candidate_bundle, *, selected_fact_ids=None):
    selected = set(selected_fact_ids or [fact["id"] for fact in candidate_bundle["facts"]])
    facts = [fact for fact in candidate_bundle["facts"] if fact["id"] in selected or fact["type"] == "basic"]

    basic = next((fact["data"] for fact in facts if fact["type"] == "basic"), {})
    resume = {
        "$schema": "https://raw.githubusercontent.com/jsonresume/resume-schema/master/schema.json",
        "basics": {
            "name": basic.get("name", ""),
            "email": basic.get("email", ""),
            "phone": basic.get("phone", ""),
            "location": {"address": basic.get("location", "")},
            "profiles": [],
        },
        "work": [],
        "volunteer": [],
        "education": [],
        "awards": [],
        "certificates": [],
        "skills": [],
        "languages": [],
        "projects": [],
    }

    for fact in facts:
        data = fact["data"]
        kind = fact["type"]
        if kind == "work":
            resume["work"].append({
                "name": data.get("organisation", ""),
                "position": data.get("title", ""),
                "location": data.get("location", ""),
                "summary": data.get("description", ""),
                "highlights": data.get("bullets", []),
                "x_careergps_fact_id": fact["id"],
            })
        elif kind == "project":
            resume["projects"].append({
                "name": data.get("title", ""),
                "description": data.get("description", ""),
                "highlights": data.get("bullets", []),
                "url": data.get("url", ""),
                "x_careergps_fact_id": fact["id"],
            })
        elif kind == "education":
            resume["education"].append({
                "institution": data.get("institution", ""),
                "studyType": data.get("qualification", ""),
                "area": data.get("field_of_study", ""),
                "startDate": data.get("start_date", ""),
                "endDate": "" if data.get("current") else data.get("end_date", ""),
                "x_careergps_fact_id": fact["id"],
            })
        elif kind == "certification":
            resume["certificates"].append({
                "name": data.get("title", ""),
                "issuer": data.get("issuer", ""),
                "url": data.get("credential_url", ""),
                "x_careergps_fact_id": fact["id"],
            })
        elif kind == "achievement":
            resume["awards"].append({
                "title": data.get("title", ""),
                "awarder": data.get("organisation", ""),
                "date": data.get("date", ""),
                "summary": data.get("description", ""),
                "x_careergps_fact_id": fact["id"],
            })
        elif kind == "volunteering":
            resume["volunteer"].append({
                "organization": data.get("organisation", ""),
                "position": data.get("role", ""),
                "summary": data.get("description", ""),
                "x_careergps_fact_id": fact["id"],
            })
        elif kind == "language":
            resume["languages"].append({
                "language": data.get("language", ""),
                "fluency": data.get("proficiency", ""),
                "x_careergps_fact_id": fact["id"],
            })
        elif kind == "skill":
            resume["skills"].append({
                "name": data.get("name", ""),
                "keywords": [],
                "x_careergps_fact_id": fact["id"],
            })
        elif kind == "link":
            resume["basics"]["profiles"].append({
                "network": data.get("label", ""),
                "url": data.get("url", ""),
                "username": "",
            })

    return resume
