import re

from ingestion.pipeline.normalization import geographic_scope_evidence, plain_text

from .base import (
    CompanyScraper, evidence_provenance, experience_years,
    explicit_work_experience_text, find_section, heading_sections, labelled_text,
    soup_for, successfactors_fingerprint_material, table_fields, title_from_page,
)


def split_experience_requirements(value):
    text = plain_text(value)
    markers = list(re.finditer(
        r"\b(minimum|required|essential|ideal|preferred|advantageous)(?:\s+experience)?\s*:",
        text,
        re.I,
    ))
    if not markers:
        return text, ""
    required, preferred = [], []
    prefix = text[:markers[0].start()].strip()
    if prefix:
        required.append(prefix)
    for index, marker in enumerate(markers):
        end = markers[index + 1].start() if index + 1 < len(markers) else len(text)
        content = text[marker.end():end].strip(" :-")
        if not content:
            continue
        target = preferred if marker.group(1).lower() in {"ideal", "preferred", "advantageous"} else required
        target.append(content)
    return " ".join(required), " ".join(preferred)


CAPITEC_YEAR_PATTERN = re.compile(
    r"(?<!\d)(\d{1,2})\s*(?:(?:\+|plus)|(?:[-\u2013\u2014\ufffd]|to)\s*(\d{1,2}))?\s*"
    r"(?:relevant\s+)?(?:years?|yrs?)\b",
    re.I,
)
INLINE_PREFERRED_EXPERIENCE_PATTERN = re.compile(
    r"(?:at\s+least\s+)?\d{1,2}\s*(?:(?:\+|plus)|(?:[-\u2013\u2014\ufffd]|to)\s*\d{1,2})?\s*"
    r"(?:relevant\s+)?(?:years?|yrs?)[^.!?]{0,80}?\bexperience\b[^.!?]{0,60}?"
    r"\((?:advantageous|preferred|ideal)[^)]*\)",
    re.I,
)


def _capitec_experience_text(value):
    return (
        plain_text(value)
        .replace("\u2013", "-")
        .replace("\u2014", "-")
        .replace("\ufffd", "-")
    )


def move_inline_preferred_experience(required_text, preferred_text):
    required = _capitec_experience_text(required_text)
    moved = [match.group(0).strip() for match in INLINE_PREFERRED_EXPERIENCE_PATTERN.finditer(required)]
    for clause in moved:
        required = required.replace(clause, " ", 1)
    required = plain_text(required)
    preferred = "\n".join(filter(None, [plain_text(preferred_text), *moved]))
    return required, preferred, moved


def capitec_experience_constraints(required_text, preferred_text):
    """Preserve each numeric Capitec experience requirement and its role."""
    constraints = []
    for requirement, source_text in (
        ("required", required_text), ("preferred", preferred_text),
    ):
        text = _capitec_experience_text(source_text)
        matches = list(CAPITEC_YEAR_PATTERN.finditer(text))
        for index, match in enumerate(matches):
            previous_end = matches[index - 1].end() if index else 0
            next_start = matches[index + 1].start() if index + 1 < len(matches) else len(text)
            before = text[previous_end:match.start()]
            after = text[match.end():next_start]
            nearby_before = text[max(0, match.start() - 45):match.start()].lower()
            nearby_after = text[match.end():min(len(text), match.end() + 90)].lower()
            constraint_requirement = requirement
            if requirement == "required" and (
                re.search(r"\b(?:ideal|preferred|advantageous)\b", nearby_before)
                or re.search(r"\((?:advantageous|preferred|ideal)\b", nearby_after)
            ):
                constraint_requirement = "preferred"

            relation_text = f"{before} {after}".lower()
            if "overall" in relation_text:
                context = "overall"
            elif re.search(r"\b(?:of which|including|included within)\b", before, re.I):
                context = "subset"
            elif index and re.search(r"\bOR\b", before):
                context = "alternative"
            elif index and re.search(
                r"\b(?:management|managing)\s+experience\b|"
                r"\bexperience\s+(?:in\s+)?(?:leading|managing)\b|"
                r"\bresponsible\s+for\s+client\s+service\s+delivery\b",
                after,
                re.I,
            ):
                context = "subordinate_specialism"
            elif "similar role" in nearby_after:
                context = "similar_role"
            else:
                context = "role_domain"

            start = match.start()
            prefix = re.search(r"(?:at\s+least|minimum(?:\s+of)?)\s*$", nearby_before, re.I)
            if prefix:
                start = max(0, match.start() - (len(nearby_before) - prefix.start()))
            raw = plain_text(text[start:min(next_start, match.end() + 180)])
            constraints.append({
                "requirement": constraint_requirement,
                "context": context,
                "min": int(match.group(1)),
                "max": int(match.group(2)) if match.group(2) else None,
                "raw": raw,
                "source_field": "Experience",
                "confidence": "high",
            })
    return constraints


def primary_capitec_experience(constraints, requirement):
    candidates = [item for item in constraints if item["requirement"] == requirement]
    if not candidates:
        return None, False
    overall = [item for item in candidates if item["context"] == "overall"]
    if len(overall) == 1:
        return overall[0], False
    primary = [
        item for item in candidates
        if item["context"] not in {"subset", "subordinate_specialism", "similar_role", "alternative"}
    ]
    if len(primary) == 1:
        return primary[0], False
    if len(candidates) == 1:
        return candidates[0], False
    return None, True


class CapitecScraper(CompanyScraper):
    code = "capitec"
    company = "Capitec Bank Ltd"
    listing_urls = ("https://careers.capitecbank.co.za/search/?createNewAlert=false&q=&locationsearch=",)
    detail_path_markers = ("/job/",)
    parser_version = "capitec-successfactors-2026-2"
    fingerprint_material = staticmethod(successfactors_fingerprint_material)
    official_domains = ("careers.capitecbank.co.za",)
    use_detail_page_as_application_gateway = True

    def _is_detail_url(self, url):
        return super()._is_detail_url(url) and url.rstrip("/") not in {item.rstrip("/") for item in self.listing_urls}

    def parse_detail(self, url, html):
        soup = soup_for(html)
        values, sections = table_fields(soup), heading_sections(soup)
        title = title_from_page(soup)
        purpose = (
            find_section(sections, "Purpose Statement")
            or find_section(sections, "About the Role")
            or find_section(sections, "The Role")
        )
        # Exact keys avoid treating ``Knowledge and Experience`` as a second,
        # duplicate generic ``Experience`` section.
        dedicated_required = (
            sections.get("experience minimum", "")
            or sections.get("minimum experience", "")
        )
        dedicated_preferred = (
            sections.get("experience ideal", "")
            or sections.get("experience preferred", "")
            or sections.get("ideal experience", "")
            or sections.get("preferred experience", "")
            or sections.get("advantageous experience", "")
        )
        generic_experience = sections.get("experience", "")
        split_required, split_preferred = split_experience_requirements(generic_experience)
        what_you_bring_experience = explicit_work_experience_text(
            sections.get("what you bring", ""), source_is_experience_context=True
        )
        mixed_experience = "\n".join(filter(None, [
            sections.get("minimum knowledge and experience", ""),
            sections.get("knowledge and experience", ""),
            what_you_bring_experience,
        ]))
        required_experience = "\n".join(filter(None, [
            dedicated_required, split_required, mixed_experience,
        ]))
        preferred_experience = "\n".join(filter(None, [dedicated_preferred, split_preferred]))
        required_experience, preferred_experience, moved_preferred = move_inline_preferred_experience(
            required_experience, preferred_experience
        )
        experience_constraints = capitec_experience_constraints(
            required_experience, preferred_experience
        )
        required_primary, required_ambiguous = primary_capitec_experience(
            experience_constraints, "required"
        )
        preferred_primary, preferred_ambiguous = primary_capitec_experience(
            experience_constraints, "preferred"
        )
        required_min = required_primary["min"] if required_primary else None
        required_max = required_primary["max"] if required_primary else None
        preferred_min = preferred_primary["min"] if preferred_primary else None
        preferred_max = preferred_primary["max"] if preferred_primary else None
        minimum_qualification = " ".join(filter(None, [
            find_section(sections, "Qualifications Minimum"),
            find_section(sections, "Qualification Minimum"),
            find_section(sections, "Minimum Education"),
            find_section(sections, "Education Minimum"),
        ]))
        generic_qualification = find_section(sections, "Qualifications")
        if not minimum_qualification and "minimum" in generic_qualification.lower():
            minimum_qualification = generic_qualification
        preferred_qualification = " ".join(filter(None, [
            find_section(sections, "Qualifications Ideal or Preferred"),
            find_section(sections, "Qualification Ideal or Preferred"),
            find_section(sections, "Preferred Qualification"),
            find_section(sections, "Education Ideal or Preferred"),
        ]))
        knowledge = find_section(sections, "Knowledge") or find_section(sections, "Knowledge and Experience")
        skills = find_section(sections, "Skills")
        conditions = (
            find_section(sections, "Conditions of Employment")
            or find_section(sections, "You'll Also Need")
            or find_section(sections, "Additional Information")
        )
        responsibilities = " ".join(filter(None, [
            find_section(sections, "What You Will Do"), find_section(sections, "What You'll Do"),
            find_section(sections, "Duties"), find_section(sections, "Partner at the Top"),
            find_section(sections, "Turn Insight into Action"), find_section(sections, "Drive Real Change"),
            find_section(sections, "Connect the Dots Across the Business"),
        ]))
        personal_attributes = find_section(sections, "What Makes You Right for This")
        location = labelled_text(soup, "Location")
        geography = geographic_scope_evidence(
            country="South Africa",
            location=location,
            country_source_field="Capitec South Africa source configuration",
            location_source_field="Location",
        )
        body = soup.select_one(".jobdescription") or soup.select_one("[itemprop='description']")
        advert_text = plain_text(body.get_text(" ", strip=True)) if body else ""
        title_lower = title.lower()
        listing_context = "\n".join(filter(None, [title, purpose, advert_text]))
        advert_lower = listing_context.lower()
        if "talent pool" in advert_lower or "talent community" in advert_lower:
            listing_kind = "talent_pool"
            trigger = "talent pool" if "talent pool" in advert_lower else "talent community"
            trigger_at = advert_lower.find(trigger)
            listing_kind_evidence = listing_context[
                max(0, trigger_at - 80):min(
                    len(listing_context), trigger_at + len(trigger) + 120
                )
            ] or title
        elif (
            "talent pipeline" in advert_lower
            or "future opportunities" in advert_lower
            or re.search(r"(?:[-â€“â€”:]|\()\s*pipeline\)?\s*$", title, re.I)
        ):
            listing_kind = "pipeline"
            listing_kind_evidence = title
            for trigger in ("talent pipeline", "future opportunities"):
                trigger_at = advert_lower.find(trigger)
                if trigger_at >= 0:
                    listing_kind_evidence = listing_context[
                        max(0, trigger_at - 80):min(
                            len(listing_context), trigger_at + len(trigger) + 120
                        )
                    ]
                    break
        else:
            listing_kind = "vacancy"
            listing_kind_evidence = "No Capitec talent-pool or pipeline identity."
        structured = {
            "minimum_qualification": minimum_qualification,
            "preferred_qualification": preferred_qualification,
            "required_experience": required_experience,
            "preferred_experience": preferred_experience,
            "experience_constraints": experience_constraints,
            "experience_primary_required": required_primary,
            "experience_primary_preferred": preferred_primary,
            "required_experience_scalar_ambiguous": required_ambiguous,
            "preferred_experience_scalar_ambiguous": preferred_ambiguous,
            "inline_preferred_experience_moved": moved_preferred,
            "knowledge": knowledge,
            "skills": skills,
            "conditions_of_employment": conditions,
            "responsibilities": responsibilities,
            "personal_attributes": personal_attributes,
            "geography": geography,
            "listing_kind": {
                "value": listing_kind,
                "source_field": "Capitec title/advert context",
                "raw": listing_kind_evidence,
            },
        }
        fields = {
            "title": title, "company": labelled_text(soup, "Company") or self.company,
            "location_text": location, "country": geography["country"],
            "geographic_scope": geography["status"],
            "geographic_scope_provenance": geography,
            "posted_at": labelled_text(soup, "Date", "Posting Date"),
            "closing_date": labelled_text(soup, "Apply by", "Closing Date") or None,
            "listing_kind": listing_kind, "description": purpose,
            "responsibilities": responsibilities,
            "qualification_text": minimum_qualification,
            "required_qualification": minimum_qualification,
            "experience_text": required_experience,
            "experience_min_years": required_min,
            "experience_max_years": required_max,
            "required_experience_text": required_experience,
            "required_experience_min": required_min,
            "required_experience_max": required_max,
            "preferred_experience_text": preferred_experience,
            "preferred_experience_min_years": preferred_min,
            "preferred_experience_max_years": preferred_max,
            "experience_scalar_intentionally_unset": required_ambiguous,
            "preferred_experience_scalar_intentionally_unset": preferred_ambiguous,
            "qualification_minimum": minimum_qualification,
            "qualification_preferred": preferred_qualification,
            "preferred_qualification_text": preferred_qualification,
            "skills_text": " ".join(filter(None, [knowledge, skills])),
            "source_specific_evidence": structured,
            "evidence_provenance": {
                "experience_text": evidence_provenance(
                    "Experience - Minimum / proven experience clause", source_text=required_experience
                ),
                "preferred_experience_text": evidence_provenance(
                    "Experience - Ideal", source_text=preferred_experience
                ),
                "experience_constraints": evidence_provenance(
                    "Experience (separate required/preferred contextual constraints)",
                    source_text=" | ".join(item["raw"] for item in experience_constraints),
                ),
                "qualification_text": evidence_provenance(
                    "Qualifications/Education - Minimum", source_text=minimum_qualification
                ),
                "preferred_qualification_text": evidence_provenance(
                    "Qualifications/Education - Ideal/Preferred", source_text=preferred_qualification
                ),
                "geography": geography,
            },
            "eligibility_text": " ".join(filter(None, [knowledge, skills, conditions, personal_attributes])),
            "publishable": geography["status"] != "confirmed_outside_south_africa",
        }
        return self.build_record(
            url, html, fields, {"labels": values, "sections": sections, "structured": structured}
        )
