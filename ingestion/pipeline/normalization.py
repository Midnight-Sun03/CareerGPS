"""Normalize source records and classify them from explicit, traceable evidence.

The normalizer deliberately separates three questions:

* what kind of opportunity is this?
* what career stage does the evidence support?
* should it be included in the CareerGPS catalogue?

Unknown or conflicting evidence is retained as ``unclassified``.  Titles and
numeric experience requirements are recorded as evidence, but are not global
exclusion rules.
"""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from bs4 import BeautifulSoup
from dateutil import parser as date_parser
from django.utils import timezone

from ingestion.pipeline.records import FetchedOpportunity


OPPORTUNITY_KINDS = {
    "job",
    "internship",
    "learnership",
    "graduate_programme",
    "vacation_work",
    "unknown",
}
CAREER_STAGES = {"early_career", "experienced", "unknown"}
SCOPE_STATUSES = {"in_scope", "unclassified", "out_of_scope"}
GEOGRAPHIC_SCOPE_STATUSES = {
    "confirmed_south_africa",
    "confirmed_outside_south_africa",
    "unknown",
}

# These are positive locality signals only. Absence from this set is never
# evidence that a vacancy is outside South Africa.
SOUTH_AFRICAN_LOCATION_MARKERS = {
    "south africa", "za", "zaf", "gauteng", "western cape", "eastern cape",
    "kwazulu natal", "kwazulu-natal", "limpopo", "mpumalanga", "north west",
    "free state", "northern cape", "johannesburg", "sandton", "cape town",
    "durban", "pretoria", "stellenbosch", "secunda", "sasolburg", "nationwide",
}
KNOWN_OUTSIDE_COUNTRY_NAMES = {
    "australia", "austria", "belgium", "botswana", "brazil", "canada", "china",
    "france", "germany", "india", "ireland", "italy", "japan", "kenya",
    "mozambique", "namibia", "netherlands", "new zealand", "nigeria", "poland",
    "portugal", "singapore", "spain", "sweden", "switzerland", "tanzania",
    "united arab emirates", "united kingdom", "united states", "usa", "zambia",
    "zimbabwe",
}

PROGRAMME_RULES = (
    ("learnership", re.compile(r"\blearnerships?\b", re.IGNORECASE)),
    ("internship", re.compile(r"\binternships?\b|\bintern\b", re.IGNORECASE)),
    (
        "graduate_programme",
        re.compile(
            r"\bgraduate\s+(?:development\s+)?(?:programme|program|scheme|internship)s?\b",
            re.IGNORECASE,
        ),
    ),
    (
        "vacation_work",
        re.compile(r"\bvacation\s+(?:work|programme|program)\b", re.IGNORECASE),
    ),
)

# These words are observations only.  They never independently set stage or
# scope because their meaning varies substantially between employers.
TITLE_EVIDENCE_PATTERN = re.compile(
    r"\b(senior|lead|leader|manager|principal|head|director|executive|expert|"
    r"specialist|assistant|consultant|analyst|coordinator|officer|associate|"
    r"administrator|junior|trainee)\b",
    re.IGNORECASE,
)

NO_EXPERIENCE_PATTERN = re.compile(
    r"\b(?:no|zero)\s+(?:prior\s+|previous\s+|formal\s+)?(?:work\s+)?experience"
    r"(?:\s+(?:is\s+)?required)?\b",
    re.IGNORECASE,
)
EXPERIENCE_RANGE_PATTERN = re.compile(
    r"(?<!\d)(\d+(?:\.\d+)?)\s*(?:-|–|—|to)\s*(\d+(?:\.\d+)?)\s*"
    r"(?:years?|yrs?)\b",
    re.IGNORECASE,
)
EXPERIENCE_SINGLE_PATTERN = re.compile(
    r"(?<!\d)(\d+(?:\.\d+)?)\s*(?:\+|plus)?\s*(?:relevant\s+)?(?:years?|yrs?)\b",
    re.IGNORECASE,
)


def plain_text(value: Any) -> str:
    if value is None:
        return ""
    text = str(value)
    if "<" in text and ">" in text:
        text = BeautifulSoup(text, "html.parser").get_text(" ")
    return re.sub(r"\s+", " ", text).strip()


def normalized_key(value: Any) -> str:
    text = unicodedata.normalize("NFKD", plain_text(value)).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


def geographic_scope_evidence(
    *,
    country: Any = "",
    location: Any = "",
    country_source_field: str = "country",
    location_source_field: str = "location",
) -> dict[str, Any]:
    """Return a loss-averse, three-state geographic fact with provenance."""
    explicit_country = plain_text(country)
    country_key = normalized_key(explicit_country)
    if country_key:
        if country_key in {"south africa", "za", "zaf", "republic of south africa"}:
            return {
                "status": "confirmed_south_africa", "country": "South Africa",
                "source_field": country_source_field, "confidence": "high",
                "raw": explicit_country,
            }
        return {
            "status": "confirmed_outside_south_africa", "country": explicit_country,
            "source_field": country_source_field, "confidence": "high",
            "raw": explicit_country,
        }

    location_text = plain_text(location)
    location_key = normalized_key(location_text)
    if location_key:
        marker_words = f" {location_key} "
        if any(f" {marker} " in marker_words for marker in SOUTH_AFRICAN_LOCATION_MARKERS):
            return {
                "status": "confirmed_south_africa", "country": "South Africa",
                "source_field": location_source_field, "confidence": "medium",
                "raw": location_text,
            }
        components = {normalized_key(part) for part in location_text.split(",") if plain_text(part)}
        outside_country = next(
            (name for name in KNOWN_OUTSIDE_COUNTRY_NAMES if name in components), ""
        )
        if outside_country:
            displayed_country = next(
                (plain_text(part) for part in location_text.split(",")
                 if normalized_key(part) == outside_country),
                outside_country.title(),
            )
            return {
                "status": "confirmed_outside_south_africa", "country": displayed_country,
                "source_field": location_source_field, "confidence": "high",
                "raw": location_text,
            }

    return {
        "status": "unknown", "country": "",
        "source_field": location_source_field, "confidence": "low",
        "raw": location_text,
    }


def parse_datetime(value: Any) -> datetime | None:
    if not value:
        return None
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, date):
        parsed = datetime.combine(value, datetime.min.time())
    else:
        try:
            parsed = date_parser.parse(str(value))
        except (TypeError, ValueError, OverflowError):
            return None
    if timezone.is_naive(parsed):
        parsed = timezone.make_aware(parsed, timezone.get_current_timezone())
    return parsed


def parse_date(value: Any) -> date | None:
    parsed = parse_datetime(value)
    return parsed.date() if parsed else None


def parse_decimal(value: Any) -> Decimal | None:
    if value in (None, ""):
        return None
    if isinstance(value, Decimal):
        return value
    match = re.fullmatch(r"\s*(-?\d[\d,]*(?:\.\d+)?)\s*", str(value))
    if not match:
        return None
    try:
        return Decimal(match.group(1).replace(",", ""))
    except InvalidOperation:
        return None


def positive_integer(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        result = int(float(str(value)))
    except (TypeError, ValueError):
        return None
    return max(result, 0)


def parse_positive_integer(value: Any) -> int | None:
    if value in (None, "") or re.search(r"-\s*\d", str(value)):
        return None
    match = re.search(r"\d+", str(value))
    return int(match.group(0)) if match else None


def classify_listing_kind(value: Any) -> str:
    text = plain_text(value).lower()
    if text in {"vacancy", "talent_pool", "pipeline"}:
        return text
    if "talent pool" in text or "talent community" in text:
        return "talent_pool"
    if "pipeline" in text:
        return "pipeline"
    return "vacancy"


def _experience_text(value: Any) -> str:
    """Keep clause boundaries while removing markup and noisy whitespace."""
    if value is None:
        return ""
    text = str(value)
    text = text.replace("â€“", "-").replace("â€”", "-").replace("\ufffd", "-")
    # Some live SuccessFactors content renders hyphens on both sides of a
    # range or between the number and unit (``1-2-years``, ``3-years``).
    text = re.sub(r"(?<!\d)(\d+)\s*-\s*(\d+)\s*-\s*(years?|yrs?)\b", r"\1-\2 \3", text, flags=re.I)
    text = re.sub(r"(?<!\d)(\d+)\s*-\s*(years?|yrs?)\b", r"\1 \2", text, flags=re.I)
    if "<" in text and ">" in text:
        text = BeautifulSoup(text, "html.parser").get_text("\n")
    lines = [re.sub(r"\s+", " ", line).strip() for line in text.splitlines()]
    return "\n".join(line for line in lines if line)


def experience_bounds(value: Any, *, include_preferred: bool = False) -> tuple[int | None, int | None]:
    """Extract explicit work-experience years, excluding education durations.

    Adapters are responsible for selecting work-experience clauses.  This
    function intentionally does not scan an entire advert or infer that every
    number followed by "years" is work experience.
    """
    text = _experience_text(value)
    if not text:
        return None, None

    clauses = re.split(r"[\n.;]+", text)
    selected: list[str] = []
    for clause in clauses:
        lower = clause.lower()
        if not include_preferred:
            if re.match(r"^\s*(?:ideal|preferred|advantageous|desirable)\b", lower):
                continue
            if re.match(r"^\s*(?:ideal|preferred)\s+experience\b", lower):
                continue
        selected.append(clause)
    searchable = "\n".join(selected)
    if not searchable:
        return None, None
    if NO_EXPERIENCE_PATTERN.search(searchable):
        return 0, 0

    range_matches = list(EXPERIENCE_RANGE_PATTERN.finditer(searchable))
    range_spans = [match.span() for match in range_matches]
    single_matches = [
        match for match in EXPERIENCE_SINGLE_PATTERN.finditer(searchable)
        if not any(start <= match.start() < end for start, end in range_spans)
    ]
    if not include_preferred:
        def preferred_only(match):
            nearby_after = searchable[match.end():match.end() + 35]
            nearby_before = searchable[max(0, match.start() - 35):match.start()]
            required_nearby = re.search(
                r"\b(?:minimum|required|essential|at least|must have|min)\b",
                searchable[max(0, match.start() - 80):match.end() + 35],
                re.I,
            )
            inline_preferred = re.search(r"\b(?:preferred|advantageous|desirable)\b", nearby_after, re.I)
            labelled_preferred = re.search(r"\b(?:preferred|ideal)\s+(?:work\s+)?experience\b", nearby_before, re.I)
            return bool((inline_preferred or labelled_preferred) and not required_nearby)

        range_matches = [match for match in range_matches if not preferred_only(match)]
        range_spans = [match.span() for match in range_matches]
        single_matches = [
            match for match in single_matches
            if not preferred_only(match)
            and not any(start <= match.start() < end for start, end in range_spans)
        ]
    if range_matches or single_matches:
        minima = [int(float(match.group(1))) for match in range_matches]
        maxima = [int(float(match.group(2))) for match in range_matches]
        minima.extend(int(float(match.group(1))) for match in single_matches)
        # A standalone ``N+ years`` requirement has no supported upper bound.
        maximum = None if single_matches else max(maxima)
        return min(minima), maximum
    return None, None


def minimum_years(value: Any) -> int | None:
    return experience_bounds(value)[0]


def _evidence(
    kind: str,
    value: Any,
    source: str,
    source_field: str,
    *,
    confidence: str = "high",
    raw: Any = None,
) -> dict[str, Any]:
    item = {
        "kind": kind,
        "value": value,
        "source": source,
        "source_field": source_field,
        "confidence": confidence,
    }
    if raw not in (None, ""):
        item["raw"] = raw
    return item


def _program_kind(fields: dict[str, Any]) -> tuple[str, str]:
    supplied = normalized_key(fields.get("opportunity_kind") or fields.get("programme_type"))
    aliases = {
        "graduate": "graduate_programme",
        "graduate-program": "graduate_programme",
        "graduate-programme": "graduate_programme",
        "vacation": "vacation_work",
        "vacation-work": "vacation_work",
    }
    supplied = aliases.get(supplied, supplied.replace("-", "_").replace(" ", "_"))
    if supplied in OPPORTUNITY_KINDS and supplied not in {"unknown", "job"}:
        return supplied, "source field"

    identity_text = " ".join(
        plain_text(fields.get(key))
        for key in ("programme_identity", "programme_type", "title")
        if fields.get(key)
    )
    for kind, pattern in PROGRAMME_RULES:
        if pattern.search(identity_text):
            return kind, "explicit programme identity"
    return "job", "no explicit programme identity"


def _source_stage_evidence(fields: dict[str, Any], source: str) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    supplied = fields.get("source_career_stage_signal") or []
    if isinstance(supplied, dict):
        supplied = [supplied]
    if isinstance(supplied, str):
        supplied = [{"value": supplied, "source_field": "source_career_stage_signal"}]
    for item in supplied:
        if not isinstance(item, dict):
            continue
        value = normalized_key(item.get("value")).replace("-", "_").replace(" ", "_")
        aliases = {
            "early": "early_career",
            "young_professionals": "early_career",
            "graduate": "early_career",
            "senior": "experienced",
            "experienced_professionals": "experienced",
        }
        value = aliases.get(value, value)
        if value not in CAREER_STAGES - {"unknown"}:
            continue
        result.append(
            _evidence(
                "source_career_stage",
                value,
                source,
                plain_text(item.get("source_field")) or "source_career_stage_signal",
                confidence=plain_text(item.get("confidence")) or "high",
                raw=item.get("raw"),
            )
        )
    return result


def classification_policy(
    fields: dict[str, Any],
    *,
    source: str = "",
) -> dict[str, Any]:
    """Apply conservative shared policy to normalized evidence only."""
    kind, kind_reason = _program_kind(fields)
    evidence: list[dict[str, Any]] = [
        _evidence("opportunity_kind", kind, source, "programme_identity", raw=kind_reason)
    ]

    title = plain_text(fields.get("title"))
    for term in TITLE_EVIDENCE_PATTERN.findall(title):
        evidence.append(
            _evidence(
                "title_observation",
                term.lower(),
                source,
                "title",
                confidence="low",
                raw=title,
            )
        )

    source_stage = _source_stage_evidence(fields, source)
    evidence.extend(source_stage)
    early = [item for item in source_stage if item["value"] == "early_career"]
    experienced = [item for item in source_stage if item["value"] == "experienced"]

    min_years = positive_integer(fields.get("experience_min_years"))
    max_years = positive_integer(fields.get("experience_max_years"))
    provenance = fields.get("evidence_provenance") or {}
    required_provenance = provenance.get("experience_text", {}) if isinstance(provenance, dict) else {}
    required_confidence = required_provenance.get("confidence", "high")
    if min_years is not None:
        evidence.append(
            _evidence(
                "minimum_experience_years",
                min_years,
                source,
                required_provenance.get("source_field", "experience_text"),
                confidence=required_confidence,
            )
        )
    if max_years is not None:
        evidence.append(
            _evidence(
                "maximum_experience_years",
                max_years,
                source,
                required_provenance.get("source_field", "experience_text"),
                confidence=required_confidence,
            )
        )

    # A complete, high-confidence required range ending at two years is useful
    # early-career evidence.  A minimum alone is not treated as a bounded range.
    if max_years is not None and max_years <= 2 and required_confidence == "high":
        early.append(
            _evidence(
                "required_experience_range",
                "early_career",
                source,
                required_provenance.get("source_field", "experience_text"),
                confidence="high",
                raw={"minimum": min_years, "maximum": max_years},
            )
        )
        evidence.extend(early[-1:])

    explicit_exclusion = fields.get("source_scope_exclusion")
    if explicit_exclusion:
        exclusion = explicit_exclusion if isinstance(explicit_exclusion, dict) else {}
        exclusion_reason = plain_text(exclusion.get("reason") or explicit_exclusion)
        evidence.append(
            _evidence(
                "source_scope_exclusion",
                True,
                source,
                plain_text(exclusion.get("source_field")) or "source_scope_exclusion",
                confidence=plain_text(exclusion.get("confidence")) or "high",
                raw=exclusion.get("raw") or exclusion_reason,
            )
        )
        career_stage = "unknown"
        scope_status = "out_of_scope"
        reason = f"Explicit source scope exclusion: {exclusion_reason}"
    elif kind in {"internship", "learnership", "graduate_programme", "vacation_work"} and experienced:
        career_stage = "unknown"
        scope_status = "unclassified"
        reason = "Explicit programme identity conflicts with experienced source evidence; retained for review."
    elif early and experienced:
        career_stage = "unknown"
        scope_status = "unclassified"
        reason = "Conflicting early-career and experienced source evidence; retained for review."
    elif kind in {"internship", "learnership", "graduate_programme", "vacation_work"}:
        career_stage = "early_career"
        scope_status = "in_scope"
        reason = f"Explicit {kind.replace('_', ' ')} identity."
    elif experienced:
        career_stage = "experienced"
        scope_status = "out_of_scope"
        reason = "High-confidence source-native evidence identifies an experienced-career role."
    elif early:
        career_stage = "early_career"
        scope_status = "in_scope"
        reason = "High-confidence evidence supports an early-career role."
    else:
        career_stage = "unknown"
        scope_status = "unclassified"
        reason = "Insufficient decisive career-stage evidence; retained as unclassified."

    legacy_type = {
        "internship": "internship",
        "learnership": "learnership",
        "graduate_programme": "graduate",
    }.get(kind)
    if not legacy_type:
        legacy_type = "entry_level" if career_stage == "early_career" else "unclassified"

    return {
        "opportunity_kind": kind,
        "career_stage": career_stage,
        "scope_status": scope_status,
        "opportunity_type": legacy_type,
        "classification_reason": reason,
        "classification_evidence": evidence,
    }


def classify_opportunity(
    title_or_fields: dict[str, Any] | str,
    content: str = "",
    *,
    fields: dict[str, Any] | None = None,
) -> str:
    """Compatibility wrapper for callers of the former classifier API."""
    policy_fields = dict(title_or_fields) if isinstance(title_or_fields, dict) else dict(fields or {})
    if not isinstance(title_or_fields, dict):
        policy_fields["title"] = title_or_fields
    result = classification_policy(policy_fields)
    if result["scope_status"] == "out_of_scope":
        return "out_of_scope"
    return result["opportunity_type"]


def classification_reason(
    title_or_fields: dict[str, Any] | str,
    content: str = "",
    *,
    fields: dict[str, Any] | None = None,
) -> str:
    policy_fields = dict(title_or_fields) if isinstance(title_or_fields, dict) else dict(fields or {})
    if not isinstance(title_or_fields, dict):
        policy_fields["title"] = title_or_fields
    return classification_policy(policy_fields)["classification_reason"]


def split_location(
    fields: dict[str, Any], geographic_evidence: dict[str, Any] | None = None
) -> tuple[str, str, str]:
    city = plain_text(fields.get("city"))
    province = plain_text(fields.get("province"))
    country = plain_text(fields.get("country"))
    inferred_country = plain_text((geographic_evidence or {}).get("country"))
    if city or province or country:
        return city, province, country or inferred_country
    parts = [
        part.strip()
        for part in plain_text(fields.get("location_text") or fields.get("location")).split(",")
        if part.strip()
    ]
    if len(parts) >= 3:
        return parts[0], parts[-2], country or inferred_country
    if len(parts) == 2:
        if inferred_country and normalized_key(parts[-1]) == normalized_key(inferred_country):
            return parts[0], "", inferred_country
        return parts[0], parts[1], inferred_country
    if parts:
        return parts[0], "", inferred_country
    return "", "", inferred_country


def fingerprint(source_code: str, external_id: str, application_url: str, title: str, company: str) -> str:
    identity = "|".join(
        [source_code, external_id, application_url, normalized_key(title), normalized_key(company)]
    )
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()


def content_hash(fields: dict[str, Any], raw_content: str | None = None) -> str:
    if raw_content is not None:
        serialized = json.dumps(fields or {}, sort_keys=True, default=str, ensure_ascii=False)
        return hashlib.sha256(f"{serialized}|{raw_content}".encode("utf-8")).hexdigest()
    content = "|".join(
        plain_text(fields.get(key))
        for key in sorted(fields)
        if key not in {"raw_payload", "fetched_at"}
    )
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def normalize_record(record: FetchedOpportunity, source: Any = None) -> dict[str, Any]:
    fields = dict(record.fields or {})
    source_code = plain_text(getattr(source, "code", "") or fields.get("source") or "")
    title = plain_text(fields.get("title"))
    company = plain_text(fields.get("company"))

    experience_text = plain_text(fields.get("experience_text"))
    preferred_experience_text = plain_text(fields.get("preferred_experience_text"))
    parsed_min, parsed_max = experience_bounds(fields.get("experience_text"))
    experience_min_years = positive_integer(fields.get("experience_min_years"))
    experience_max_years = positive_integer(fields.get("experience_max_years"))
    scalar_intentionally_unset = bool(fields.get("experience_scalar_intentionally_unset"))
    if experience_min_years is None and not scalar_intentionally_unset:
        experience_min_years = parsed_min
    if experience_max_years is None and not scalar_intentionally_unset:
        experience_max_years = parsed_max

    preferred_parsed_min, preferred_parsed_max = experience_bounds(
        fields.get("preferred_experience_text"), include_preferred=True
    )
    preferred_experience_min_years = positive_integer(fields.get("preferred_experience_min_years"))
    preferred_experience_max_years = positive_integer(fields.get("preferred_experience_max_years"))
    preferred_scalar_intentionally_unset = bool(
        fields.get("preferred_experience_scalar_intentionally_unset")
    )
    if preferred_experience_min_years is None and not preferred_scalar_intentionally_unset:
        preferred_experience_min_years = preferred_parsed_min
    if preferred_experience_max_years is None and not preferred_scalar_intentionally_unset:
        preferred_experience_max_years = preferred_parsed_max

    inferred_geography = geographic_scope_evidence(
        country=fields.get("country"),
        location=fields.get("location_text") or fields.get("location"),
        country_source_field="country",
        location_source_field="location_text",
    )
    supplied_geography = plain_text(fields.get("geographic_scope"))
    # An explicit country is stronger than an adapter-supplied inference and
    # cannot be contradicted by it.
    if fields.get("country") and inferred_geography["status"] != "unknown":
        if supplied_geography == inferred_geography["status"]:
            geographic_evidence = {
                **inferred_geography,
                **(fields.get("geographic_scope_provenance") or {}),
                "status": inferred_geography["status"],
                "country": inferred_geography["country"],
            }
        else:
            geographic_evidence = inferred_geography
    elif supplied_geography in GEOGRAPHIC_SCOPE_STATUSES:
        geographic_evidence = {
            **inferred_geography,
            **(fields.get("geographic_scope_provenance") or {}),
            "status": supplied_geography,
        }
        if supplied_geography == "unknown":
            geographic_evidence["country"] = ""
    else:
        geographic_evidence = inferred_geography
    city, province, country = split_location(fields, geographic_evidence)
    qualification_text = plain_text(fields.get("qualification_text"))
    minimum_nqf_value = parse_positive_integer(fields.get("minimum_nqf"))
    if minimum_nqf_value is None:
        nqf_match = re.search(r"\bNQF\s*(?:Level\s*)?(\d{1,2})\b", qualification_text, re.I)
        minimum_nqf_value = int(nqf_match.group(1)) if nqf_match else None
    policy_input = {
        **fields,
        "title": title,
        "experience_min_years": experience_min_years,
        "experience_max_years": experience_max_years,
    }
    if geographic_evidence["status"] == "confirmed_outside_south_africa":
        policy_input["source_scope_exclusion"] = {
            "reason": "Source record is confirmed outside the configured South Africa catalogue.",
            "source_field": geographic_evidence.get("source_field", "location_text"),
            "confidence": geographic_evidence.get("confidence", "high"),
            "raw": geographic_evidence.get("raw"),
        }
    policy = classification_policy(policy_input, source=source_code)
    programme_identity = plain_text(fields.get("programme_identity"))
    if not programme_identity and policy["opportunity_kind"] != "job":
        programme_identity = policy["opportunity_kind"]

    result = {
        "source": source_code,
        "external_id": plain_text(record.external_id),
        "title": title,
        "company": company,
        "description": plain_text(fields.get("description")),
        "responsibilities": plain_text(fields.get("responsibilities")),
        "experience_text": experience_text,
        "experience_min_years": experience_min_years,
        "experience_max_years": experience_max_years,
        "preferred_experience_text": preferred_experience_text,
        "preferred_experience_min_years": preferred_experience_min_years,
        "preferred_experience_max_years": preferred_experience_max_years,
        "qualification_text": qualification_text,
        "preferred_qualification_text": plain_text(fields.get("preferred_qualification_text")),
        "minimum_nqf": minimum_nqf_value,
        "skills": plain_text(fields.get("skills_text") or fields.get("skills") or fields.get("knowledge")),
        "eligibility_text": plain_text(fields.get("eligibility_text")),
        "programme_identity": programme_identity,
        "source_career_stage_signal": fields.get("source_career_stage_signal") or [],
        "evidence_provenance": fields.get("evidence_provenance") or {},
        "department": plain_text(fields.get("department")),
        "business_unit": plain_text(fields.get("business_unit")),
        "sector": plain_text(fields.get("sector") or fields.get("category")),
        "office": plain_text(fields.get("office")),
        "employment_type": plain_text(fields.get("employment_type")),
        "contract_type": plain_text(fields.get("contract_type")),
        "contract_time": plain_text(fields.get("contract_time")),
        "contract_duration": plain_text(fields.get("contract_duration")),
        "listing_kind": classify_listing_kind(fields.get("listing_kind")),
        "city": city,
        "province": province,
        "country": country,
        "geographic_scope": geographic_evidence["status"],
        "geographic_scope_provenance": geographic_evidence,
        "salary_min": parse_decimal(fields.get("salary_min")),
        "salary_max": parse_decimal(fields.get("salary_max")),
        "salary_currency": plain_text(fields.get("salary_currency")) or "ZAR",
        "salary_text": plain_text(
            fields.get("salary_text") or fields.get("compensation_text") or fields.get("stipend")
        ),
        "posted_at": parse_datetime(fields.get("posted_at") or fields.get("posted_date")),
        "closing_date": parse_date(fields.get("closing_date")),
        "reference_number": plain_text(fields.get("reference_number") or record.external_id),
        "application_url": plain_text(record.apply_url or fields.get("application_url")),
        "latitude": parse_decimal(fields.get("latitude")),
        "longitude": parse_decimal(fields.get("longitude")),
        "required_qualification": plain_text(
            fields.get("required_qualification") or fields.get("qualification_text")
        ),
        "qualification_minimum": plain_text(
            fields.get("qualification_minimum") or fields.get("qualification_text")
        ),
        "qualification_preferred": plain_text(
            fields.get("qualification_preferred") or fields.get("preferred_qualification_text")
        ),
        "source_native_category": plain_text(fields.get("source_native_category")),
        "source_native_career_level": plain_text(fields.get("source_native_career_level")),
        "source_specific_evidence": fields.get("source_specific_evidence") or {},
        "eligibility": plain_text(fields.get("eligibility_text")),
        "location_text": plain_text(fields.get("location_text") or fields.get("location")),
        "location": plain_text(fields.get("location_text") or fields.get("location")),
        "primary_apply_url": plain_text(record.apply_url or fields.get("application_url")),
        "redirect_url": plain_text(record.apply_url or fields.get("application_url")),
        "source_platform": source_code,
        "salary_is_predicted": fields.get("salary_is_predicted"),
        "compensation_text": plain_text(fields.get("compensation_text") or fields.get("salary_text")),
        "work_arrangement": plain_text(fields.get("work_arrangement")),
        "last_seen_at": timezone.now(),
        "lifecycle_status": "active",
        "is_active": True,
        **policy,
    }
    result["canonical_fingerprint"] = fingerprint(
        source_code,
        result["external_id"],
        result["application_url"],
        title,
        company,
    )
    result["content_hash"] = content_hash(result)
    return result
