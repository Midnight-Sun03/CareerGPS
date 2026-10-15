import re
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit

from ingestion.pipeline.normalization import geographic_scope_evidence

from .base import (
    CompanyScraper, evidence_provenance, experience_years,
    direct_html_fingerprint_material,
    explicit_work_experience_text, find_value, heading_sections, plain_text,
    normalized_key, soup_for, table_fields, title_from_page,
)


WOOLWORTHS_PREFERENCE_PATTERN = re.compile(
    r"\b(?:advantageous|preferred(?:\s+but\s+not\s+mandatory)?|preferably|"
    r"ideally|ideal|desirable|an\s+advantage|would\s+be\s+advantageous|"
    r"would\s+be\s+beneficial|beneficial|not\s+mandatory)\b",
    re.I,
)
WOOLWORTHS_PREFERENCE_HEADINGS = {
    "advantageous", "preferred", "ideal", "desirable", "nice to have",
}
WOOLWORTHS_NEUTRAL_HEADINGS = {
    "minimum requirements", "requirements", "essential", "additional criteria",
}


def _minimum_requirements_cell(soup):
    for row in soup.select("tr"):
        cells = row.find_all(["th", "td"], recursive=False)
        if len(cells) >= 2 and normalized_key(cells[0].get_text(" ", strip=True)) == "minimum requirements":
            return cells[1]
    return None


def _requirement_fragments(cell):
    """Keep Woolworths list/paragraph boundaries and subsection context."""
    if cell is None:
        return []

    events = []
    for node in cell.descendants:
        name = getattr(node, "name", None)
        if name in {"p", "li"}:
            # A nested semantic block is represented by its nearest p/li parent.
            if node.find_parent(["p", "li"]) is not None:
                continue
            events.append(node.get_text(" ", strip=True))
        elif name is None:
            parent = getattr(node, "parent", None)
            if parent and parent.find_parent(["p", "li"]) is None and parent.name not in {"p", "li", "script", "style"}:
                events.append(str(node))

    fragments = []
    preference_context = False
    seen = set()
    for event in events:
        event = re.sub(r"^\s*Minimum Requirements\s*:\s*", "", str(event), flags=re.I)
        for fragment in re.split(
            r"[\n\r;\u2022]+|\u00e2\u20ac\u00a2|(?<=[.!?])\s+|\s+-\s+(?=[A-Z0-9])",
            event,
        ):
            fragment = plain_text(fragment).strip(" -")
            key = normalized_key(fragment)
            if not key:
                continue
            if key in WOOLWORTHS_PREFERENCE_HEADINGS:
                preference_context = True
                continue
            if key in WOOLWORTHS_NEUTRAL_HEADINGS:
                preference_context = False
                continue
            if key in seen:
                continue
            seen.add(key)
            fragments.append((fragment, preference_context))
    return fragments


def _alternative_experience_clause(clause):
    key = normalized_key(clause)
    return bool(
        re.search(r"\b(?:qualification|certification|degree|diploma)\b.{0,45}\b(?:and or|or)\b.{0,20}\bexperience\b", key)
        or re.search(r"\bexperience\b.{0,20}\b(?:and or|or)\b.{0,45}\b(?:qualification|certification|degree|diploma)\b", key)
    )


def _split_preferred_specialism(clause):
    """Split required general experience from a preferred specialism."""
    match = re.match(
        r"^(?P<required>.*\bexperience\b.*?)[,;]\s*preferably\s+(?P<link>in|with|as)\s+(?P<preferred>.+)$",
        clause,
        re.I,
    )
    if not match:
        return None
    required = match.group("required").rstrip(" .") + "."
    preferred = f"Experience {match.group('link')} {match.group('preferred').rstrip(' .')} preferred."
    return required, preferred


def woolworths_experience_evidence(soup):
    """Interpret only Woolworths' mixed Minimum Requirements source field."""
    required = []
    preferred = []
    ambiguous = []
    interpretations = []

    for clause, preference_context in _requirement_fragments(_minimum_requirements_cell(soup)):
        if not explicit_work_experience_text(clause):
            continue
        item = {
            "raw": clause,
            "source_field": "Minimum Requirements",
            "confidence": "high",
        }
        split = _split_preferred_specialism(clause)
        if split:
            required_clause, preferred_clause = split
            required.append(required_clause)
            preferred.append(preferred_clause)
            item.update({
                "interpretation": "required_with_preferred_specialism",
                "required": required_clause,
                "preferred": preferred_clause,
            })
        elif _alternative_experience_clause(clause):
            ambiguous.append(clause)
            item.update({"interpretation": "ambiguous_alternative", "confidence": "medium"})
        elif preference_context or WOOLWORTHS_PREFERENCE_PATTERN.search(clause):
            preferred.append(clause)
            item["interpretation"] = "preferred"
        else:
            required.append(clause)
            item["interpretation"] = "required"
        interpretations.append(item)

    def unique(values):
        result, seen = [], set()
        for value in values:
            key = normalized_key(value)
            if key and key not in seen:
                result.append(value)
                seen.add(key)
        return "\n".join(result)

    return {
        "required": unique(required),
        "preferred": unique(preferred),
        "ambiguous": unique(ambiguous),
        "clauses": interpretations,
    }


def woolworths_fingerprint_material(record):
    """Remove only verified eRecruit/ASP.NET request-state values."""
    soup = soup_for(record.raw_content or "")
    state_fields = {
        "__VIEWSTATE", "__VIEWSTATEGENERATOR", "__EVENTVALIDATION",
        "__PREVIOUSPAGE", "__EVENTTARGET", "__EVENTARGUMENT",
    }
    for element in soup.find_all("input"):
        if element.get("name") in state_fields:
            element["value"] = "_VOLATILE_"

    for element in soup.find_all(["script", "link", "img"]):
        attribute = "src" if element.name in {"script", "img"} else "href"
        value = element.get(attribute, "")
        if not any(path in value for path in (
            "ScriptResource.axd", "WebResource.axd", "ImageDownload.ashx",
        )):
            continue
        parts = urlsplit(value)
        query = [
            (key, "_VOLATILE_" if key in {"d", "params"} else item)
            for key, item in parse_qsl(parts.query, keep_blank_values=True)
        ]
        element[attribute] = urlunsplit((
            parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment,
        ))
    return direct_html_fingerprint_material(record, str(soup))


class WoolworthsScraper(CompanyScraper):
    code = "woolworths"
    company = "Woolworths South Africa"
    listing_urls = ("https://woolworths.erecruit.co/candidateapp/jobs/categories/",)
    detail_path_markers = ("/Jobs/View/", "/jobs/view/")
    parser_version = "woolworths-erecruit-2026-2"
    official_domains = ("woolworths.erecruit.co",)
    fingerprint_material = staticmethod(woolworths_fingerprint_material)

    DETAIL_ONCLICK = re.compile(r"(?:window\.)?location\s*=\s*['\"]([^'\"]*/candidateapp/Jobs/View/[^'\"]+)['\"]", re.I)
    POSTBACK = re.compile(r"__doPostBack\(['\"]([^'\"]+)['\"],\s*['\"]([^'\"]*)['\"]\)", re.I)

    @classmethod
    def _row_routes(cls, soup, listing_url):
        routes = []
        for row in soup.select("tr.item[onclick]"):
            match = cls.DETAIL_ONCLICK.search(row.get("onclick", ""))
            if not match:
                continue
            cells = [plain_text(cell.get_text(" ", strip=True)) for cell in row.select("td")]
            routes.append((urljoin(listing_url, match.group(1)), {
                "title": cells[0] if cells else "",
                "listing_location": cells[1] if len(cells) > 1 else "",
            }))
        return routes

    def _listing_pages(self):
        listing_url = self.listing_urls[0]
        response = self.client.get(listing_url)
        seen_ids = set()
        page_count = 0
        while self.max_pages is None or page_count < self.max_pages:
            soup = soup_for(response.text)
            routes = self._row_routes(soup, listing_url)
            page_ids = {url.rstrip("/").rsplit("/", 1)[-1] for url, _ in routes}
            if not page_ids or page_ids.issubset(seen_ids):
                break
            seen_ids.update(page_ids)
            page_count += 1
            self.stats["pages_traversed"] += 1
            yield listing_url, soup

            next_link = next((
                anchor for anchor in soup.select("a[href]")
                if "lbResultsNextPage" in anchor.get("href", "")
            ), None)
            if not next_link:
                break
            match = self.POSTBACK.search(next_link.get("href", ""))
            if not match:
                self.stats["parsing_errors"] += 1
                self.errors.append({"step": "pagination", "url": listing_url, "error": "Next-page postback target was not parseable."})
                break
            form = soup.select_one("form")
            if not form:
                self.stats["parsing_errors"] += 1
                self.errors.append({"step": "pagination", "url": listing_url, "error": "Next-page control had no containing form."})
                break
            data = {
                item.get("name"): item.get("value", "")
                for item in form.select("input[name]")
                if item.get("type", "").lower() in {"hidden", "text"}
            }
            data["__EVENTTARGET"], data["__EVENTARGUMENT"] = match.groups()
            response = self.client.post(urljoin(listing_url, form.get("action") or listing_url), data=data)

    def discover_detail_candidates(self, listing_url, soup):
        yield from self._row_routes(soup, listing_url)

    def parse_detail(self, url, html):
        soup = soup_for(html)
        values, sections = table_fields(soup), heading_sections(soup)
        country = find_value(values, "Location Country", "Country")
        location = find_value(values, "Location Description") or find_value(values, "Location Town City")
        geography = geographic_scope_evidence(
            country=country,
            location=location,
            country_source_field="Location Country",
            location_source_field="Location Description / Town City",
        )
        minimum_requirements = find_value(values, "Minimum Requirements")
        experience_evidence = woolworths_experience_evidence(soup)
        experience = experience_evidence["required"]
        preferred_experience = experience_evidence["preferred"]
        experience_min, experience_max = experience_years(experience)
        preferred_experience_min, preferred_experience_max = experience_years(
            preferred_experience, include_preferred=True
        )
        qualification = find_value(values, "Minimum Education Level", "Minimum Education")
        fields = {
            "title": find_value(values, "Job Title") or title_from_page(soup),
            "company": self.company,
            "employment_type": find_value(values, "Job Type"),
            "business_unit": find_value(values, "Business Unit"),
            "country": geography["country"],
            "geographic_scope": geography["status"],
            "geographic_scope_provenance": geography,
            "province": find_value(values, "Location Province", "Province"),
            "city": find_value(values, "Location Town City", "Town City"),
            "location_text": location,
            "closing_date": find_value(values, "Closing Date"),
            "reference_number": find_value(values, "Reference Number"),
            "qualification_text": qualification,
            "description": find_value(values, "Job Advert Summary"),
            "responsibilities": find_value(values, "Duties and Responsibilities"),
            "experience_text": experience, "experience_min_years": experience_min,
            "experience_max_years": experience_max,
            "preferred_experience_text": preferred_experience,
            "preferred_experience_min_years": preferred_experience_min,
            "preferred_experience_max_years": preferred_experience_max,
            "eligibility_text": minimum_requirements,
            "qualification_minimum": qualification,
            "required_experience_text": experience,
            "required_experience_min": experience_min,
            "required_experience_max": experience_max,
            "source_specific_evidence": {
                "job_type": find_value(values, "Job Type"),
                "business_unit": find_value(values, "Business Unit"),
                "location_description": find_value(values, "Location Description"),
                "minimum_requirements": minimum_requirements,
                "required_experience": experience,
                "preferred_experience": preferred_experience,
                "ambiguous_experience": experience_evidence["ambiguous"],
                "experience_clauses": experience_evidence["clauses"],
                "geography": geography,
            },
            "evidence_provenance": {
                "experience_text": evidence_provenance(
                    "Minimum Requirements (explicit work-experience clauses only)",
                    source_text=experience,
                ),
                "preferred_experience_text": evidence_provenance(
                    "Minimum Requirements (explicit preferred work-experience clauses only)",
                    source_text=preferred_experience,
                ),
                "ambiguous_experience": evidence_provenance(
                    "Minimum Requirements (experience alternatives retained without required/preferred semantics)",
                    confidence="medium",
                    source_text=experience_evidence["ambiguous"],
                ),
                "qualification_text": evidence_provenance(
                    "Minimum Education Level", source_text=qualification
                ),
                "eligibility_text": evidence_provenance(
                    "Minimum Requirements", source_text=minimum_requirements
                ),
                "geography": geography,
            },
            "publishable": geography["status"] != "confirmed_outside_south_africa",
        }
        return self.build_record(url, html, fields, {"labels": values, "sections": sections})
