import re

from ingestion.pipeline.normalization import geographic_scope_evidence, normalized_key, plain_text

from .base import (
    CompanyScraper,
    evidence_provenance,
    experience_years,
    explicit_work_experience_text,
    labelled_text,
    property_value,
    programme_identity_from_text,
    soup_for,
    successfactors_fingerprint_material,
    table_fields,
    title_from_page,
)


DISCOVERY_SECTION_ALIASES = {
    "purpose": ("key purpose", "role purpose", "job purpose", "purpose of the role"),
    "responsibilities": (
        "areas of responsibility may include but not limited to",
        "areas of responsibility may include but are not limited to",
        "areas of responsibility may include but is not limited to",
        "areas of responsibility",
        "key outputs",
    ),
    "education_and_experience": (
        "education and experience", "education & experience",
        "qualification and experience", "qualification & experience",
        "qualifications and experience", "qualifications & experience",
    ),
    "education": ("education", "qualification", "qualifications"),
    "experience": ("experience", "work experience"),
    "specific_requirements": ("specific requirements",),
    "personal_attributes": ("personal attributes and skills", "personal attributes"),
    "technical_capabilities": ("technical capabilities", "technical knowledge"),
}

DISCOVERY_SECTION_BOUNDARIES = (
    "advantageous",
    "employment equity",
    "technical skills or knowledge",
    "technical skills",
    "knowledge of",
)

DISCOVERY_QUALIFICATION_PATTERN = re.compile(
    r"\b(?:qualification|degree|diploma|certificate|certification|matric|"
    r"grade\s*12|bachelor|master|phd|nqf|tertiary)\b",
    re.IGNORECASE,
)
DISCOVERY_EXPERIENCE_PATTERN = re.compile(
    r"\b(?:work(?:ing)?|professional|relevant|practical|prior|previous|formal)?\s*experience\b",
    re.IGNORECASE,
)
DISCOVERY_EXPERIENCE_REQUIREMENT_START = re.compile(
    r"\b(?:"
    r"no\s+(?:(?:prior|formal|previous)\s+)*(?:work\s+)?experience"
    r"|"
    r"(?:(?:at\s+least|minimum|min|required)\s+)?"
    r"\d+(?:\s*(?:-|\u2013|\u2014|to)\s*\d+)?\s*(?:years?|months?)"
    r"(?:\s+of)?[^.;\n]{0,80}?\b(?:work(?:ing)?\s+)?experience"
    r")\b",
    re.IGNORECASE,
)


def discovery_element_text(element):
    """Preserve verified Discovery list-item boundaries inside rich text."""
    if element.name in {"ul", "ol"}:
        items = element.find_all("li", recursive=False) or element.find_all("li")
        lines = [plain_text(item.get_text(" ", strip=True)) for item in items]
        return "\n".join(line for line in lines if line)
    if element.name == "table":
        cells = [
            cell for cell in element.find_all(["th", "td"])
            if not cell.find(["th", "td"])
        ]
        lines = [
            plain_text(value)
            for cell in cells
            for value in cell.stripped_strings
        ]
        return "\n".join(line for line in lines if line)
    if element.find("br"):
        lines = [plain_text(line) for line in element.get_text("\n", strip=True).splitlines()]
        return "\n".join(line for line in lines if line)
    return plain_text(element.get_text(" ", strip=True))


def discovery_sections(container):
    """Split Discovery's rich-text body, whose headings are styled paragraphs."""
    sections = {key: [] for key in DISCOVERY_SECTION_ALIASES}
    current = None
    for element in container.find_all(recursive=False):
        text = discovery_element_text(element)
        if not text:
            continue
        key_text = normalized_key(text)
        matched = None
        matched_alias = ""
        for key, aliases in DISCOVERY_SECTION_ALIASES.items():
            for alias in aliases:
                alias_key = normalized_key(alias)
                if key_text == alias_key or key_text.startswith(f"{alias_key} "):
                    matched, matched_alias = key, alias
                    break
            if matched:
                break
        if matched:
            current = matched
            remainder = text[len(matched_alias):].lstrip(" :-")
            if remainder:
                sections[current].append(remainder)
        elif current in {"education", "experience"} and any(
            key_text == boundary or key_text.startswith(f"{boundary} ")
            for boundary in DISCOVERY_SECTION_BOUNDARIES
        ):
            # Dedicated Education/Experience sections end at these verified
            # Discovery headings. Do not absorb later advert boilerplate.
            current = None
        elif current:
            sections[current].append(text)
    return {key: "\n".join(parts) for key, parts in sections.items() if parts}


def _discovery_requirement_clauses(value):
    return [
        plain_text(clause)
        for clause in re.split(r"[\n\r;\u2022]+|(?<=[.!?])\s+", str(value or ""))
        if plain_text(clause)
    ]


def split_discovery_combined_requirements(value):
    """Derive only confident facts from Discovery's combined requirement block.

    The complete block remains in ``source_specific_evidence``. Ambiguous
    clauses containing inseparable qualification and experience language are
    deliberately left out of canonical fields.
    """
    required_qualifications = []
    preferred_qualifications = []
    required_experience = []
    preferred_experience = []
    preferred = False
    context = ""

    for original_clause in _discovery_requirement_clauses(value):
        clause = original_clause
        key = normalized_key(clause)
        if key in {"education experience", "qualification experience", "qualifications experience"}:
            context = ""
            preferred = False
            continue
        if key in {"education", "qualification", "qualifications"}:
            context = "qualification"
            preferred = False
            continue
        if key in {"experience", "work experience"}:
            context = "experience"
            preferred = False
            continue
        if any(
            key == boundary or key.startswith(f"{boundary} ")
            for boundary in (
                "technical skills or knowledge", "technical skills", "knowledge of",
                "employment equity", "personal attributes", "skills",
            )
        ):
            continue

        label = re.match(
            r"^\s*(essential|required|advantageous|preferred|ideal|desirable)\s*:?[\s]*(.*)$",
            clause,
            re.IGNORECASE,
        )
        if label:
            preferred = label.group(1).lower() in {
                "advantageous", "preferred", "ideal", "desirable",
            }
            clause = plain_text(label.group(2))
            if not clause:
                continue

        inline_preferred = preferred or bool(re.search(
            r"\b(?:preferred|preference|advantageous|desirable|an\s+advantage)\b",
            clause,
            re.IGNORECASE,
        ))
        has_qualification = (
            context == "qualification"
            or bool(DISCOVERY_QUALIFICATION_PATTERN.search(clause))
        )
        has_experience = (
            bool(DISCOVERY_EXPERIENCE_PATTERN.search(clause))
            or (
                context == "experience"
                and bool(re.search(
                    r"\b\d+(?:\s*(?:-|\u2013|\u2014|to)\s*\d+)?\s*(?:years?|months?)\b",
                    clause,
                    re.IGNORECASE,
                ))
            )
        )
        qualification_parts = []
        experience_parts = []

        if has_qualification and has_experience:
            experience_start = DISCOVERY_EXPERIENCE_REQUIREMENT_START.search(clause)
            if experience_start:
                qualification_part = clause[:experience_start.start()].rstrip(" ,:-")
                qualification_part = re.sub(r"\b(?:and|or)\s*$", "", qualification_part, flags=re.I).strip()
                experience_part = clause[experience_start.start():].strip()
                if DISCOVERY_QUALIFICATION_PATTERN.search(qualification_part):
                    qualification_parts.append(qualification_part)
                if DISCOVERY_EXPERIENCE_PATTERN.search(experience_part):
                    experience_parts.append(experience_part)
            else:
                alternative = re.search(
                    r"\s+(?:or|and/or)\s+(?:an?\s+)?equivalent\b[^.;\n]{0,100}\bexperience\b",
                    clause,
                    re.IGNORECASE,
                )
                if alternative:
                    qualification_part = clause[:alternative.start()].rstrip(" ,:-")
                    if DISCOVERY_QUALIFICATION_PATTERN.search(qualification_part):
                        qualification_parts.append(qualification_part)
            # Otherwise the concepts cannot be separated confidently. The
            # original clause remains available in source-specific evidence.
        elif has_qualification:
            qualification_parts.append(clause)
        elif has_experience:
            experience_parts.append(clause)

        qualification_target = (
            preferred_qualifications if inline_preferred else required_qualifications
        )
        experience_target = preferred_experience if inline_preferred else required_experience
        qualification_target.extend(qualification_parts)
        experience_target.extend(experience_parts)

    def unique_join(values):
        return "\n".join(dict.fromkeys(value for value in values if value))

    return {
        "qualification": unique_join(required_qualifications),
        "preferred_qualification": unique_join(preferred_qualifications),
        "experience": unique_join(required_experience),
        "preferred_experience": unique_join(preferred_experience),
    }


def discovery_experience_years(value):
    """Represent an exact 12-month requirement without rounding other months."""
    minimum, maximum = experience_years(value)
    if minimum is not None or maximum is not None:
        return minimum, maximum

    for clause in _discovery_requirement_clauses(value):
        if not DISCOVERY_EXPERIENCE_PATTERN.search(clause):
            continue
        month_match = re.search(r"(?<![\d-])12\s+months?\b", clause, re.IGNORECASE)
        if not month_match:
            continue
        qualifier = clause[max(0, month_match.start() - 24):month_match.start()]
        if re.search(r"\b(?:at\s+least|minimum|min|required)\b", qualifier, re.IGNORECASE):
            return 1, None
        return 1, 1
    return None, None


class DiscoveryScraper(CompanyScraper):
    code = "discovery"
    company = "Discovery"
    listing_urls = ("https://careers.discovery.co.za/go/All-Jobs/4509301/",)
    detail_path_markers = ("/job/",)
    parser_version = "discovery-successfactors-2026-2"
    fingerprint_material = staticmethod(successfactors_fingerprint_material)
    official_domains = ("careers.discovery.co.za",)
    use_detail_page_as_application_gateway = True

    def parse_detail(self, url, html):
        soup = soup_for(html)
        values = table_fields(soup)
        body = soup.select_one(".jobdescription") or soup.select_one("[itemprop='description']")
        sections = discovery_sections(body) if body else {}
        body_text = plain_text(body.get_text(" ", strip=True)) if body else ""

        locality = property_value(soup, "addressLocality")
        region = property_value(soup, "addressRegion")
        country_code = property_value(soup, "addressCountry")
        location = ", ".join(value for value in (locality, region) if value)
        geography = geographic_scope_evidence(
            country=country_code,
            location=location,
            country_source_field="addressCountry",
            location_source_field="jobLocation / Location",
        )
        education_experience = sections.get("education_and_experience", "")
        derived = split_discovery_combined_requirements(education_experience)
        qualification = sections.get("education", "") or derived["qualification"]
        preferred_qualification = derived["preferred_qualification"] if education_experience else ""
        if sections.get("experience"):
            experience = explicit_work_experience_text(
                sections["experience"], source_is_experience_context=True
            )
            preferred_experience = ""
        else:
            experience = derived["experience"]
            preferred_experience = derived["preferred_experience"]
        experience_min, experience_max = discovery_experience_years(experience)
        preferred_experience_min, preferred_experience_max = discovery_experience_years(
            preferred_experience
        )
        specific_requirements = sections.get("specific_requirements", "")
        personal_attributes = sections.get("personal_attributes", "")
        technical_capabilities = sections.get("technical_capabilities", "")
        eligibility = " ".join(filter(None, [
            education_experience,
            specific_requirements,
            personal_attributes,
            technical_capabilities,
        ]))
        title = property_value(soup, "title") or title_from_page(soup)
        business_unit = property_value(soup, "customfield1") or labelled_text(soup, "Business Unit")
        function = property_value(soup, "department") or labelled_text(soup, "Function")
        posted_at = property_value(soup, "datePosted", "date") or labelled_text(soup, "Date", "Posting Date")
        closing_date = property_value(soup, "validThrough")
        structured = {
            "business_unit": business_unit,
            "function": function,
            "location": location,
            "posting_date": posted_at,
            "closing_date": closing_date,
            "purpose": sections.get("purpose", ""),
            "responsibilities": sections.get("responsibilities", ""),
            "education_and_experience": education_experience,
            "education": sections.get("education", ""),
            "experience": sections.get("experience", ""),
            "specific_requirements": specific_requirements,
            "personal_attributes": personal_attributes,
            "technical_capabilities": technical_capabilities,
            "geography": geography,
        }
        fields = {
            "title": title,
            "company": self.company,
            "business_unit": business_unit,
            "department": function,
            "location_text": location,
            "city": locality,
            "province": region,
            "country": geography["country"],
            "geographic_scope": geography["status"],
            "geographic_scope_provenance": geography,
            "posted_at": posted_at,
            "closing_date": closing_date,
            "description": sections.get("purpose", "") or body_text,
            "programme_identity": programme_identity_from_text(
                title, sections.get("purpose", "")
            ),
            "responsibilities": sections.get("responsibilities", ""),
            "qualification_text": qualification,
            "preferred_qualification_text": preferred_qualification,
            "experience_text": experience,
            "experience_min_years": experience_min,
            "experience_max_years": experience_max,
            "required_experience_text": experience,
            "required_experience_min": experience_min,
            "required_experience_max": experience_max,
            "preferred_experience_text": preferred_experience,
            "preferred_experience_min_years": preferred_experience_min,
            "preferred_experience_max_years": preferred_experience_max,
            "qualification_minimum": qualification,
            "qualification_preferred": preferred_qualification,
            "skills_text": " ".join(filter(None, [personal_attributes, technical_capabilities])),
            "source_specific_evidence": structured,
            "evidence_provenance": {
                "qualification_text": evidence_provenance(
                    "Education" if sections.get("education") else "Education and Experience",
                    source_text=qualification,
                ),
                "experience_text": evidence_provenance(
                    "Experience" if sections.get("experience") else "Education and Experience",
                    source_text=experience,
                ),
                **({
                    "preferred_qualification_text": evidence_provenance(
                        "Education and Experience (advantageous)",
                        source_text=preferred_qualification,
                    )
                } if preferred_qualification else {}),
                **({
                    "preferred_experience_text": evidence_provenance(
                        "Education and Experience (advantageous)",
                        source_text=preferred_experience,
                    )
                } if preferred_experience else {}),
                "business_unit": evidence_provenance("Business Unit", source_text=business_unit),
                "department": evidence_provenance("Function", source_text=function),
                "geography": geography,
            },
            "eligibility_text": eligibility,
            "listing_kind": "talent_pool" if "talent pool" in title.lower() else "vacancy",
            "publishable": geography["status"] != "confirmed_outside_south_africa",
        }
        return self.build_record(
            url,
            html,
            fields,
            {"labels": values, "sections": sections, "structured": structured},
        )
