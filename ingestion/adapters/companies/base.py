import re
import copy
from collections import deque
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

from ingestion.pipeline.normalization import (
    experience_bounds, geographic_scope_evidence, normalized_key, plain_text,
)
from ingestion.pipeline.records import FetchedOpportunity
from ingestion.adapters.base import PoliteHttpClient, SourceAccessDenied


def soup_for(html):
    return BeautifulSoup(html, "lxml")


def table_fields(soup):
    values = {}
    for row in soup.select("tr"):
        cells = row.find_all(["th", "td"], recursive=False)
        if len(cells) >= 2:
            values[normalized_key(cells[0].get_text(" ", strip=True))] = plain_text(cells[1].get_text(" ", strip=True))
    for element in soup.select("dl"):
        labels = element.find_all("dt")
        for label in labels:
            value = label.find_next_sibling("dd")
            if value:
                values[normalized_key(label.get_text(" ", strip=True))] = plain_text(value.get_text(" ", strip=True))
    return values


def heading_sections(soup):
    sections = {}
    headings = soup.find_all(re.compile(r"^h[1-6]$"))
    for heading in headings:
        key = normalized_key(heading.get_text(" ", strip=True))
        parts = []
        for sibling in heading.next_siblings:
            if getattr(sibling, "name", None) and re.match(r"^h[1-6]$", sibling.name):
                break
            if hasattr(sibling, "get_text"):
                text = plain_text(sibling.get_text("\n", strip=True))
            else:
                text = plain_text(str(sibling))
            if text:
                parts.append(text)
        if not parts and heading.parent:
            for sibling in heading.parent.next_siblings:
                if getattr(sibling, "name", None) and sibling.find(re.compile(r"^h[1-6]$")):
                    break
                if hasattr(sibling, "get_text"):
                    text = plain_text(sibling.get_text("\n", strip=True))
                else:
                    text = plain_text(str(sibling))
                if text:
                    parts.append(text)
        if key and parts:
            # A newline retains list/paragraph clause boundaries.  Several
            # sources combine education and experience under one heading.
            sections[key] = "\n".join(parts)
    return sections


def find_value(values, *aliases):
    for alias in aliases:
        wanted = normalized_key(alias)
        for key, value in values.items():
            if key == wanted or wanted in key:
                return value
    return ""


def find_section(sections, *aliases):
    return find_value(sections, *aliases)


def is_placeholder_value(value):
    return normalized_key(value) in {"loading", "please wait"}


def property_value(soup, *aliases):
    """Read SuccessFactors data properties and schema.org metadata safely."""
    wanted = {normalized_key(alias) for alias in aliases}
    for element in soup.find_all(attrs={"data-careersite-propertyid": True}):
        key = normalized_key(element.get("data-careersite-propertyid"))
        if key in wanted:
            value = plain_text(element.get_text(" ", strip=True))
            if value and not is_placeholder_value(value):
                return value
    for element in soup.find_all(attrs={"itemprop": True}):
        key = normalized_key(element.get("itemprop"))
        if key in wanted:
            value = plain_text(element.get("content") or element.get_text(" ", strip=True))
            if value and not is_placeholder_value(value):
                return value
    return ""


def labelled_text(soup, *aliases):
    values = table_fields(soup)
    found = find_value(values, *aliases)
    if found and not is_placeholder_value(found):
        return found
    lines = [plain_text(value) for value in soup.stripped_strings]
    for index, line in enumerate(lines):
        key = normalized_key(line)
        for alias in aliases:
            wanted = normalized_key(alias)
            if key == wanted and index + 1 < len(lines):
                candidate = lines[index + 1]
                if not is_placeholder_value(candidate):
                    return candidate
            if key.startswith(f"{wanted} "):
                candidate = line[len(alias):].lstrip(" :-")
                if candidate and not is_placeholder_value(candidate):
                    return candidate
    return ""


def title_from_page(soup):
    heading = soup.select_one("h1") or soup.select_one(".jobTitle") or soup.select_one("[itemprop='title']")
    return plain_text(heading.get_text(" ", strip=True)) if heading else ""


def valid_http_url(url, expected_domains=(), job_specific=False):
    try:
        parsed = urlparse(str(url or "").strip())
    except ValueError:
        return False
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return False
    hostname = (parsed.hostname or "").lower()
    if expected_domains and not any(
        hostname == domain.lower() or hostname.endswith(f".{domain.lower()}")
        for domain in expected_domains
    ):
        return False
    if job_specific and parsed.path.rstrip("/").lower() in {
        "", "/", "/jobs", "/job", "/search", "/careers", "/candidateapp/jobs/browse",
        "/candidateapp/jobs/categories",
    }:
        return False
    return True


def application_route(soup, page_url):
    for anchor in soup.find_all("a", href=True):
        text = normalized_key(anchor.get_text(" ", strip=True))
        if text in {"apply", "apply now", "apply online"} or "apply now" in text:
            candidate = urljoin(page_url, anchor["href"])
            if valid_http_url(candidate, job_specific=True):
                return candidate, "direct_apply_link"
    # Some sources, including Woolworths eRecruit, expose Apply as a POST
    # control on the detail page. Never submit that form during ingestion.
    for control in soup.select("button, input[type='submit'], input[type='button']"):
        text = normalized_key(control.get_text(" ", strip=True) or control.get("value", ""))
        if text in {"apply", "apply now", "apply online"}:
            return page_url, "detail_page_apply_gateway"
    return page_url, "detail_page_fallback"


def application_url(soup, page_url):
    return application_route(soup, page_url)[0]


def external_id_from_url(url):
    path = urlparse(url).path.rstrip("/")
    match = re.search(r"/(?:View/)?([A-Za-z0-9_-]+)$", path)
    return match.group(1) if match else path


def minimum_years(text):
    text = plain_text(text)
    if not text:
        return None

    preferred_markers = tuple(re.finditer(r"\b(?:preferred|ideal|advantageous|desirable)\b", text, re.I))
    required_markers = tuple(re.finditer(r"\b(?:minimum|required|essential|at least|must have)\b", text, re.I))

    def preferred_only(match):
        def distance(marker):
            return min(abs(marker.start() - match.start()), abs(marker.start() - match.end()))

        left = max(text.rfind(separator, 0, match.start()) for separator in (".", ";", "\n")) + 1
        right_candidates = [text.find(separator, match.end()) for separator in (".", ";", "\n")]
        right = min((position for position in right_candidates if position >= 0), default=len(text))
        nearby_preferred = [
            distance(marker) for marker in preferred_markers
            if left <= marker.start() <= right and distance(marker) <= 80
        ]
        nearby_required = [
            distance(marker) for marker in required_markers
            if left <= marker.start() <= right and distance(marker) <= 80
        ]
        return bool(nearby_preferred) and (not nearby_required or min(nearby_preferred) < min(nearby_required))

    candidates = []
    no_experience = re.finditer(
        r"\bno\s+(?:(?:prior|formal|previous)\s+)*(?:work\s+)?experience\s+(?:is\s+)?required\b",
        text,
        re.I,
    )
    candidates.extend((match, 0) for match in no_experience)
    year_pattern = re.compile(
        r"\b(?:(less\s+than)\s+)?(\d{1,2})\s*(?:(?:\+|plus)\s*|[-\u2013\u2014]\s*(?:\d{1,2}\s*)?)?(?:relevant\s+)?(?:years?|yrs?)\b",
        re.I,
    )
    for match in year_pattern.finditer(text):
        candidates.append((match, 0 if match.group(1) else int(match.group(2))))
    candidates.sort(key=lambda item: item[0].start())
    for match, value in candidates:
        if not preferred_only(match):
            return value
    return None


def experience_years(text, *, include_preferred=False):
    return experience_bounds(text, include_preferred=include_preferred)


def explicit_work_experience_text(value, *, source_is_experience_context=False):
    """Return only clauses that explicitly identify work experience.

    Use this for mixed-content sections (Discovery and Woolworths), not for a
    dedicated source field such as Sasol's Working Experience.
    """
    text = str(value or "")
    if "<" in text and ">" in text:
        text = soup_for(text).get_text("\n")
    clauses = re.split(r"[\n\r;•]+|(?<=[.!?])\s+", text)
    selected = []
    for clause in clauses:
        cleaned = plain_text(clause)
        if not cleaned:
            continue
        explicit_experience = re.search(
            r"\b(?:work(?:ing)?|professional|relevant|practical|prior|previous|formal)?\s*experience\b",
            cleaned,
            re.I,
        )
        contextual_duration = (
            source_is_experience_context
            and re.search(r"\b\d+(?:\s*(?:-|–|—|to)\s*\d+)?\s*(?:years?|yrs?)\b", cleaned, re.I)
            and not re.search(r"\b(?:qualification|degree|diploma|certificate|education|nqf)\b", cleaned, re.I)
        )
        if explicit_experience or contextual_duration:
            selected.append(cleaned)
    return "\n".join(selected)


def explicit_qualification_text(value):
    text = str(value or "")
    if "<" in text and ">" in text:
        text = soup_for(text).get_text("\n")
    clauses = re.split(r"[\n\r;•]+|(?<=[.!?])\s+", text)
    selected = []
    for clause in clauses:
        cleaned = plain_text(clause)
        if re.search(
            r"\b(?:qualification|degree|diploma|certificate|matric|bachelor|master|phd|nqf|education)\b",
            cleaned,
            re.I,
        ):
            selected.append(cleaned)
    return "\n".join(selected)


def evidence_provenance(source_field, *, confidence="high", source_text=""):
    item = {"source_field": source_field, "confidence": confidence}
    if source_text:
        item["source_text"] = plain_text(source_text)
    return item


def direct_html_fingerprint_material(record, sanitized_html):
    """Hash full detail HTML plus source listing facts and application routes.

    Parsed detail fields are intentionally not included: they are derived from
    the full HTML and may change when parser code improves even though the
    source vacancy has not changed. Listing-page facts not guaranteed to occur
    in detail HTML remain loss-sensitive.
    """
    listing = copy.deepcopy((record.payload or {}).get("listing") or {})
    listing.pop("listing_url", None)
    payload = {
        "source_url": record.source_url,
        "apply_url": record.apply_url,
        "listing": listing,
    }
    return payload, sanitized_html


def successfactors_fingerprint_material(record):
    """Remove only verified SAP SuccessFactors request/cache noise."""
    html = str(record.raw_content or "")
    html = re.sub(r"\bj_idt\d+\b", "j_idt_VOLATILE", html)
    html = re.sub(
        r'("X-CSRF-Token"\s*:\s*")[^"]+("?)',
        r"\1_VOLATILE_\2",
        html,
        flags=re.I,
    )
    html = re.sub(
        r'(/js/override\.js\?[^"\']*?(?:&amp;|&)i=)\d+',
        r"\1_VOLATILE_",
        html,
        flags=re.I,
    )
    return direct_html_fingerprint_material(record, html)


def programme_identity_from_text(*values):
    text = " ".join(plain_text(value) for value in values if value)
    patterns = (
        ("learnership", r"\blearnerships?\b"),
        ("internship", r"\binternships?\b|\bintern\b"),
        ("graduate_programme", r"\bgraduate\s+(?:development\s+)?(?:programme|program|scheme|internship)s?\b"),
        ("vacation_work", r"\bvacation\s+(?:work|programme|program)\b"),
    )
    for identity, pattern in patterns:
        if re.search(pattern, text, re.I):
            return identity
    return ""


def minimum_nqf(text):
    match = re.search(r"NQF\s*(?:Level\s*)?(\d{1,2})", plain_text(text), re.I)
    return int(match.group(1)) if match else None


def is_south_african(*values):
    """Compatibility predicate; a false result is not outside-SA evidence."""
    return geographic_scope_evidence(location=" ".join(filter(None, map(plain_text, values))))[
        "status"
    ] == "confirmed_south_africa"


class CompanyScraper:
    code = ""
    company = ""
    listing_urls = ()
    detail_path_markers = ("/job/",)
    parser_version = "company-v1"
    official_domains = ()
    use_detail_page_as_application_gateway = False

    def __init__(self, max_pages=None, max_jobs=None, client=None):
        self.max_pages = max_pages
        self.max_jobs = max_jobs
        self.client = client or PoliteHttpClient()
        self.stats = {
            "listings_discovered": 0,
            "pages_traversed": 0,
            "detail_pages_fetched": 0,
            "valid_application_links": 0,
            "parsing_errors": 0,
            "link_errors": 0,
        }
        self.errors = []

    def _is_detail_url(self, url):
        path = urlparse(url).path
        return any(marker.lower() in path.lower() for marker in self.detail_path_markers)

    def _listing_pages(self):
        queue = deque(self.listing_urls)
        visited = set()
        seen_detail_urls = set()
        while queue and (self.max_pages is None or len(visited) < self.max_pages):
            url = queue.popleft()
            if url in visited:
                continue
            visited.add(url)
            try:
                response = self.client.get(url)
            except (requests.RequestException, SourceAccessDenied) as exc:
                self.stats["link_errors"] += 1
                self.errors.append({"step": "listing_fetch", "url": url, "error": str(exc)})
                continue
            soup = soup_for(response.text)
            page_detail_urls = {
                urljoin(url, anchor["href"])
                for anchor in soup.select("a[href]")
                if self._is_detail_url(urljoin(url, anchor["href"]))
            }
            if page_detail_urls and page_detail_urls.issubset(seen_detail_urls):
                continue
            seen_detail_urls.update(page_detail_urls)
            self.stats["pages_traversed"] += 1
            yield url, soup
            for anchor in soup.select("a[href]"):
                text = normalized_key(anchor.get_text(" ", strip=True))
                title = normalized_key(anchor.get("title", ""))
                classes = normalized_key(" ".join(anchor.get("class", [])))
                candidate = urljoin(url, anchor["href"])
                if urlparse(candidate).netloc != urlparse(url).netloc:
                    continue
                is_pagination = (
                    any(word in text for word in ("next", "more jobs"))
                    or (title.startswith("page ") and title != "page 1")
                    or title in {"next page", "last page"}
                    or "pagination item" in classes
                )
                if is_pagination and candidate not in visited and candidate not in queue:
                    queue.append(candidate)

    def listing_metadata(self, anchor):
        container = anchor.find_parent("tr", class_="data-row") or anchor.find_parent("div", class_=lambda value: value and "job-row" in value)
        if not container:
            container = anchor.parent
        def selected_text(*selectors):
            for selector in selectors:
                element = container.select_one(selector)
                if element:
                    return plain_text(element.get_text(" ", strip=True))
            return ""
        return {
            "title": plain_text(anchor.get_text(" ", strip=True)),
            "source_native_category": selected_text(".jobDepartment", ".jobFacility"),
            "listing_location": selected_text(".jobLocation", ".section-field.city div[id$='-value']"),
            "listing_posted_at": selected_text(".jobDate", ".section-field.date div[id$='-value']"),
        }

    def discover_detail_candidates(self, listing_url, soup):
        for anchor in soup.select("a[href]"):
            url = urljoin(listing_url, anchor["href"])
            if self._is_detail_url(url):
                yield url, self.listing_metadata(anchor)

    def fetch(self):
        discovered = {}
        for listing_url, soup in self._listing_pages():
            for detail_url, metadata in self.discover_detail_candidates(listing_url, soup):
                if detail_url not in discovered:
                    discovered[detail_url] = {"listing_url": listing_url, **metadata}
                if self.max_jobs and len(discovered) >= self.max_jobs:
                    break
            if self.max_jobs and len(discovered) >= self.max_jobs:
                break
        self.stats["listings_discovered"] = len(discovered)
        if not discovered:
            raise RuntimeError(f"{self.company} listing discovery found no source-supported job identifiers or detail routes.")
        for detail_url, listing in discovered.items():
            try:
                response = self.client.get(detail_url)
                self.stats["detail_pages_fetched"] += 1
                record = self.parse_detail(detail_url, response.text)
            except (requests.RequestException, SourceAccessDenied) as exc:
                self.stats["link_errors"] += 1
                self.errors.append({"step": "detail_fetch", "url": detail_url, "error": str(exc)})
                continue
            except Exception as exc:
                self.stats["parsing_errors"] += 1
                self.errors.append({"step": "detail_parsing", "url": detail_url, "error": str(exc)})
                continue
            if record:
                record.listing_url = listing["listing_url"]
                record.detail_http_status = response.status_code
                for key, value in listing.items():
                    if key != "listing_url" and value not in (None, ""):
                        record.fields.setdefault(key, value)
                listing_fallbacks = {
                    "title": listing.get("title"),
                    "location_text": listing.get("listing_location"),
                    "posted_at": listing.get("listing_posted_at"),
                }
                for key, value in listing_fallbacks.items():
                    if value not in (None, "") and record.fields.get(key) in (None, ""):
                        record.fields[key] = value
                self.finalize_record(record, listing)
                record.payload.setdefault("listing", listing)
                record.application_link_valid = valid_http_url(
                    record.apply_url,
                    self.official_domains or (urlparse(detail_url).hostname,),
                    job_specific=True,
                )
                if record.application_link_valid:
                    self.stats["valid_application_links"] += 1
                else:
                    self.stats["link_errors"] += 1
                    self.errors.append({"step": "application_url", "url": record.apply_url, "detail_url": detail_url})
                yield record

    def finalize_record(self, record, listing):
        """Allow source-specific mapping of listing-page facts after detail parsing."""
        return record

    def build_record(self, url, html, fields, parsed_payload):
        apply_url, mechanism = application_route(soup_for(html), url)
        if self.use_detail_page_as_application_gateway:
            apply_url = url
            mechanism = "detail_page_apply_gateway"
        fields.setdefault("application_url_mechanism", mechanism)
        evidence = fields.setdefault("source_specific_evidence", {})
        if isinstance(evidence, dict):
            evidence.setdefault("application_url_mechanism", mechanism)
        parsed_payload.setdefault("routing", {
            "detail_url": url,
            "apply_url": apply_url,
            "application_url_mechanism": mechanism,
        })
        return FetchedOpportunity(
            external_id=str(fields.get("reference_number") or external_id_from_url(url)),
            source_url=url,
            apply_url=apply_url,
            payload=parsed_payload,
            raw_content=html,
            fields=fields,
            parser_version=self.parser_version,
        )

    def parse_detail(self, url, html):
        raise NotImplementedError
