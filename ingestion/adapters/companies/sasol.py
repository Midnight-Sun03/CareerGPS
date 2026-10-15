import re

from .base import (
    CompanyScraper, evidence_provenance, experience_years, find_section,
    geographic_scope_evidence, heading_sections, labelled_text, minimum_nqf,
    property_value, soup_for, successfactors_fingerprint_material, table_fields,
    title_from_page,
)


def split_wish_qualification(value):
    parts = re.split(r"\(?\b(?:wish|preferred|desirable)\b\)?\s*:?​?", value or "", maxsplit=1, flags=re.I)
    if len(parts) == 2:
        return parts[0].strip(), parts[1].strip()
    return (value or "").strip(), ""


class SasolScraper(CompanyScraper):
    code = "sasol"
    company = "Sasol"
    listing_urls = ("https://jobs.sasol.com/search/?createNewAlert=false&q=&locationsearch=",)
    detail_path_markers = ("/job/",)
    parser_version = "sasol-successfactors-2026-2"
    official_domains = ("jobs.sasol.com",)
    use_detail_page_as_application_gateway = True
    fingerprint_material = staticmethod(successfactors_fingerprint_material)

    def parse_detail(self, url, html):
        soup = soup_for(html)
        values, sections = table_fields(soup), heading_sections(soup)
        location = property_value(soup, "location", "streetAddress") or labelled_text(soup, "Location", "Work Location")
        formal_education = find_section(sections, "Formal Education")
        required_education, preferred_education = split_wish_qualification(formal_education)
        experience = find_section(sections, "Working Experience")
        experience_source = "working_experience"
        if not experience:
            embedded = re.search(r"\bmin(?:imum)?\s+experience\b.*", formal_education, re.I)
            if embedded:
                experience = embedded.group(0)
                experience_source = "formal_education_embedded_label"
        experience_min, experience_max = experience_years(experience)
        licences = " ".join(filter(None, [
            find_section(sections, "Licences"), find_section(sections, "Licenses"),
            find_section(sections, "Required Licences"), find_section(sections, "Required Licenses"),
        ]))
        skills = " ".join(filter(None, [
            find_section(sections, "Required Personal and Professional Skills"),
            find_section(sections, "Required Professional and Personal Skills"),
            find_section(sections, "Personal and Professional Skills"),
            find_section(sections, "Professional Personal Skills"),
        ]))
        posted_at = property_value(soup, "datePosted", "date") or labelled_text(soup, "Posting Date")
        closing_date = property_value(soup, "validThrough") or labelled_text(soup, "Closing Date")
        reference = property_value(soup, "customfield1") or labelled_text(soup, "Job Requisition ID", "Requisition ID")
        business_unit = labelled_text(soup, "OME", "Business Unit")
        work_arrangement = labelled_text(soup, "Work Arrangement")
        geography = geographic_scope_evidence(
            location=location, location_source_field="streetAddress / Location"
        )
        structured = {
            "purpose_of_job": find_section(sections, "Purpose of Job"),
            "key_accountabilities": find_section(sections, "Key Accountabilities"),
            "formal_education": formal_education,
            "minimum_qualification": required_education,
            "preferred_qualification": preferred_education,
            "working_experience": experience,
            "working_experience_source": experience_source,
            "required_personal_and_professional_skills": skills,
            "licences": licences,
            "ome": business_unit,
            "work_arrangement": work_arrangement,
            "requisition_id": reference,
            "geography": geography,
        }
        fields = {
            "title": property_value(soup, "title") or title_from_page(soup), "company": self.company,
            "city": location.split(",")[0], "location_text": location,
            "country": geography["country"],
            "geographic_scope": geography["status"],
            "geographic_scope_provenance": geography,
            "posted_at": posted_at,
            "closing_date": closing_date,
            "reference_number": reference,
            "business_unit": business_unit,
            "work_arrangement": work_arrangement,
            "description": find_section(sections, "Purpose of Job"),
            "responsibilities": find_section(sections, "Key Accountabilities"),
            "qualification_text": required_education,
            "preferred_qualification_text": preferred_education,
            "minimum_nqf": minimum_nqf(required_education),
            "experience_text": experience, "experience_min_years": experience_min,
            "experience_max_years": experience_max,
            "required_experience_text": experience,
            "required_experience_min": experience_min,
            "required_experience_max": experience_max,
            "qualification_minimum": required_education,
            "qualification_preferred": preferred_education,
            "skills_text": skills,
            "source_specific_evidence": structured,
            "evidence_provenance": {
                "experience_text": evidence_provenance(
                    "Working Experience" if experience_source == "working_experience" else "Formal Education embedded Min Experience",
                    source_text=experience,
                ),
                "qualification_text": evidence_provenance("Formal Education", source_text=required_education),
                "preferred_qualification_text": evidence_provenance("Formal Education (Wish)", source_text=preferred_education),
                "business_unit": evidence_provenance("OME", source_text=business_unit),
                "geography": geography,
            },
            "eligibility_text": " ".join(filter(None, [licences, skills])),
            "publishable": geography["status"] != "confirmed_outside_south_africa",
        }
        return self.build_record(
            url, html, fields, {"labels": values, "sections": sections, "structured": structured}
        )
