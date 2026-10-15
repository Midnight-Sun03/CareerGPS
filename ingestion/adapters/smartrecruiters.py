"""Generic SmartRecruiters Posting API source family.

One adapter serves every SmartRecruiters tenant.  Employer-specific
configuration (``companyIdentifier``, country scope, careers entry point)
lives on :class:`ingestion.models.SmartRecruitersCompany`, so new tenants are
added through configuration rather than new scraping code.

This module only acquires source evidence from the public Posting API.  It is
not the cleaner, the taxonomy layer, the matching engine, or a readiness
switch: acquired records enter the existing raw/non-ready CareerGPS lifecycle
and only a validated clean import can make them READY.
"""

from urllib.parse import urlparse

import requests

from ingestion.pipeline.normalization import geographic_scope_evidence, plain_text
from ingestion.pipeline.records import FetchedOpportunity
from .base import PoliteHttpClient


class SmartRecruitersContractError(RuntimeError):
    """The live SmartRecruiters contract materially deviated from what is verified.

    Raised for catalogue-shape, posting-identity and pagination failures so a
    run fails visibly instead of silently inventing identity or structure.
    """


def _label(value):
    """Return a SmartRecruiters taxonomy object's label verbatim, or ""."""
    if isinstance(value, dict):
        return plain_text(value.get("label"))
    if isinstance(value, (list, tuple)):
        return ""
    return plain_text(value)


class SmartRecruitersAdapter:
    API_ROOT = "https://api.smartrecruiters.com/v1/companies/{company_identifier}"
    # Verified live: the public catalogue caps ``limit`` at 100 and pages with ``offset``.
    PAGE_LIMIT = 100
    PARSER_VERSION = "smartrecruiters-posting-api-v1"

    def __init__(self, company, max_jobs=None, client=None):
        self.company = company
        self.max_jobs = max_jobs
        self.client = client or PoliteHttpClient(delay=0.25)
        self.stats = {
            "listings_discovered": 0,
            "pages_traversed": 0,
            "detail_pages_fetched": 0,
            "valid_application_links": 0,
            "parsing_errors": 0,
            "link_errors": 0,
            "duplicate_listings": 0,
            "non_south_africa_postings": 0,
            "detail_failures": 0,
        }
        self.errors = []

    @property
    def root(self):
        return self.API_ROOT.format(company_identifier=self.company.company_identifier)

    def _contract_error(self, endpoint, expected, observed):
        return SmartRecruitersContractError(
            "SmartRecruiters source contract failure: "
            f"source=smartrecruiters-{self.company.company_identifier.lower()} "
            f"company_identifier={self.company.company_identifier} "
            f"endpoint={endpoint} expected={expected} observed={observed}"
        )

    def _country_scope(self):
        codes = [
            str(code).strip()
            for code in (self.company.country_codes or [])
            if str(code).strip()
        ]
        if len(codes) != 1:
            raise SmartRecruitersContractError(
                "SmartRecruiters source configuration failure: "
                f"company_identifier={self.company.company_identifier} must configure exactly "
                "one country scope; the verified catalogue contract filters one country value "
                f"per request (observed {codes!r})."
            )
        return codes[0].lower()

    def _catalogue_page(self, country, offset):
        endpoint = f"{self.root}/postings"
        params = {
            "country": country,
            "destination": "PUBLIC",
            "limit": self.PAGE_LIMIT,
            "offset": offset,
        }
        page = self.client.get_json(endpoint, params=params, respect_robots=False)
        if not isinstance(page, dict):
            raise self._contract_error(
                endpoint,
                "a JSON object with a 'content' posting collection",
                type(page).__name__,
            )
        content = page.get("content")
        if not isinstance(content, list):
            raise self._contract_error(
                endpoint,
                "'content' to be a list of postings",
                f"{type(content).__name__}={content!r:.200}",
            )
        try:
            total = int(page.get("totalFound"))
        except (TypeError, ValueError):
            raise self._contract_error(
                endpoint, "an integer 'totalFound'", repr(page.get("totalFound"))
            ) from None
        try:
            limit = int(page.get("limit") or self.PAGE_LIMIT)
        except (TypeError, ValueError):
            raise self._contract_error(
                endpoint, "an integer echoed 'limit'", repr(page.get("limit"))
            ) from None
        if limit <= 0:
            raise self._contract_error(endpoint, "a positive echoed 'limit'", repr(limit))
        return content, total, limit

    @staticmethod
    def _posting_id(item, endpoint, company_identifier):
        if not isinstance(item, dict):
            raise SmartRecruitersContractError(
                "SmartRecruiters source contract failure: "
                f"company_identifier={company_identifier} endpoint={endpoint} "
                f"expected=a posting object in 'content' observed={type(item).__name__}"
            )
        posting_id = item.get("id")
        if (
            not isinstance(posting_id, (str, int))
            or isinstance(posting_id, bool)
            or not str(posting_id).strip()
        ):
            raise SmartRecruitersContractError(
                "SmartRecruiters source contract failure: "
                f"company_identifier={company_identifier} endpoint={endpoint} "
                f"expected=a trustworthy posting identity ('id') observed={posting_id!r}; "
                "refusing to derive identity from title/company/location."
            )
        return str(posting_id).strip()

    @staticmethod
    def _dict_field(detail, listing, key):
        value = detail.get(key) if isinstance(detail, dict) else None
        if isinstance(value, dict):
            return value
        value = listing.get(key)
        return value if isinstance(value, dict) else {}

    @staticmethod
    def _list_field(detail, listing, key):
        value = detail.get(key) if isinstance(detail, dict) else None
        if isinstance(value, list):
            return value
        value = listing.get(key)
        return value if isinstance(value, list) else []

    def fetch(self):
        country = self._country_scope()
        identifier = self.company.company_identifier
        offset = 0
        yielded = 0
        seen = set()
        while True:
            endpoint = f"{self.root}/postings"
            content, total, limit = self._catalogue_page(country, offset)
            self.stats["pages_traversed"] += 1
            if not content:
                break
            for item in content:
                posting_id = self._posting_id(item, endpoint, identifier)
                external_id = f"{identifier}:{posting_id}"
                if external_id in seen:
                    # The same posting reached through another page/filter route
                    # remains one CareerGPS source opportunity.
                    self.stats["duplicate_listings"] += 1
                    continue
                if self.max_jobs is not None and yielded >= self.max_jobs:
                    return
                seen.add(external_id)
                self.stats["listings_discovered"] += 1
                record = self._build_record(posting_id, item)
                if record is None:
                    continue  # per-detail failure already recorded
                yielded += 1
                yield record
            # ``offset`` strictly increases, so pagination always terminates.
            offset += limit
            if offset >= total:
                break

    def _build_record(self, posting_id, listing):
        identifier = self.company.company_identifier
        endpoint = f"{self.root}/postings/{posting_id}"
        try:
            detail = self.client.get_json(endpoint, respect_robots=False)
        except requests.RequestException as exc:
            # One bad posting must not kill the whole source run.
            self.stats["detail_failures"] += 1
            self.errors.append({
                "step": "detail_fetch",
                "company_identifier": identifier,
                "posting_id": posting_id,
                "url": endpoint,
                "error": str(exc)[:500],
            })
            return None
        if not isinstance(detail, dict):
            self.stats["detail_failures"] += 1
            self.errors.append({
                "step": "detail_contract",
                "company_identifier": identifier,
                "posting_id": posting_id,
                "error": (
                    f"detail response is {type(detail).__name__}, expected the "
                    "SmartRecruiters posting object"
                ),
            })
            return None
        self.stats["detail_pages_fetched"] += 1
        detail_id = detail.get("id")
        if detail_id in (None, "") or str(detail_id).strip() != str(posting_id):
            self.stats["detail_failures"] += 1
            self.errors.append({
                "step": "detail_contract",
                "company_identifier": identifier,
                "posting_id": posting_id,
                "error": (
                    f"detail posting id {detail_id!r} does not match the requested "
                    f"posting {posting_id!r}"
                ),
            })
            return None
        return self._make_record(posting_id, listing, detail)

    def _make_record(self, posting_id, listing, detail):
        identifier = self.company.company_identifier
        name = plain_text(detail.get("name")) or plain_text(listing.get("name"))

        location = self._dict_field(detail, listing, "location")
        full_location = plain_text(location.get("fullLocation"))
        if not full_location:
            full_location = ", ".join(
                part for part in (
                    plain_text(location.get("city")),
                    plain_text(location.get("region")),
                ) if part
            )
        geography = geographic_scope_evidence(
            country=location.get("country"),
            location=full_location,
            country_source_field="SmartRecruiters location.country",
            location_source_field="SmartRecruiters location.fullLocation",
        )
        if geography["status"] == "confirmed_outside_south_africa":
            # The request already asked for the South Africa slice; anything the
            # source explicitly places abroad is counted here and gated to
            # out_of_scope by the existing pipeline geography evidence.
            self.stats["non_south_africa_postings"] += 1

        job_ad = self._dict_field(detail, listing, "jobAd")
        sections = job_ad.get("sections") if isinstance(job_ad.get("sections"), dict) else {}
        advert_html = "\n".join(
            section.get("text")
            for section in sections.values()
            if isinstance(section, dict)
            and isinstance(section.get("text"), str)
            and section.get("text").strip()
        )

        source_url = plain_text(detail.get("postingUrl"))
        apply_url = plain_text(detail.get("applyUrl"))
        application_link_valid = self._application_link_valid(apply_url, posting_id)
        if not apply_url or not application_link_valid:
            self.stats["link_errors"] += 1
            self.errors.append({
                "step": "application_url",
                "company_identifier": identifier,
                "posting_id": posting_id,
                "url": apply_url,
                "error": "posting has no job-specific HTTP(S) application route",
            })
        else:
            self.stats["valid_application_links"] += 1

        department = self._dict_field(detail, listing, "department")
        type_of_employment = self._dict_field(detail, listing, "typeOfEmployment")
        experience_level = self._dict_field(detail, listing, "experienceLevel")
        custom_fields = self._list_field(detail, listing, "customField")

        evidence = {
            "platform": "smartrecruiters",
            "company_identifier": identifier,
            "posting_id": posting_id,
            "posting_uuid": plain_text(detail.get("uuid")) or plain_text(listing.get("uuid")),
            "job_id": plain_text(detail.get("jobId")),
            "job_ad_id": plain_text(detail.get("jobAdId")),
            "default_job_ad": detail.get("defaultJobAd", listing.get("defaultJobAd")),
            "ref_number": plain_text(detail.get("refNumber")) or plain_text(listing.get("refNumber")),
            "visibility": plain_text(detail.get("visibility")) or plain_text(listing.get("visibility")),
            "active": detail.get("active"),
            "released_date": plain_text(detail.get("releasedDate")) or plain_text(listing.get("releasedDate")),
            "posting_url": source_url,
            "apply_url": apply_url,
            "referral_url": plain_text(detail.get("referralUrl")),
            "industry": self._dict_field(detail, listing, "industry"),
            "function": self._dict_field(detail, listing, "function"),
            "department": department,
            "type_of_employment": type_of_employment,
            "experience_level": experience_level,
            "language": self._dict_field(detail, listing, "language"),
            "custom_fields": custom_fields,
            "location": location,
            "geography": geography,
        }

        fields = {
            "title": name,
            "company": self.company.company_name,
            "description": advert_html,
            "location_text": full_location,
            "city": plain_text(location.get("city")),
            "province": plain_text(location.get("region")),
            "country": geography["country"],
            "geographic_scope": geography["status"],
            "geographic_scope_provenance": geography,
            "posted_at": evidence["released_date"],
            "reference_number": evidence["ref_number"],
            "department": _label(department),
            "source_native_category": _label(department),
            # experienceLevel is preserved verbatim; it is never an ingestion
            # admission filter nor a career-stage signal.
            "source_native_career_level": _label(experience_level),
            "employment_type": _label(type_of_employment),
            "evidence_provenance": {
                "description": {"source_field": "jobAd.sections", "confidence": "high"},
                "department": {"source_field": "department.label", "confidence": "high"},
                "geography": geography,
            },
            "source_specific_evidence": evidence,
        }

        return FetchedOpportunity(
            external_id=f"{identifier}:{posting_id}",
            listing_url=plain_text(self.company.careers_url) or f"{self.root}/postings",
            source_url=source_url or apply_url,
            apply_url=apply_url,
            payload={
                "platform": "smartrecruiters",
                "company_identifier": identifier,
                # Verbatim mirror of the posting name so the existing clean
                # export title check can discover it in the raw payload.
                "title": name,
                "listing": listing,
                "posting": detail,
            },
            raw_content=advert_html,
            fields=fields,
            parser_version=self.PARSER_VERSION,
            detail_http_status=200,
            application_link_valid=application_link_valid,
        )

    @staticmethod
    def _application_link_valid(apply_url, posting_id):
        parsed = urlparse(apply_url or "")
        segments = [part for part in parsed.path.split("/") if part]
        return (
            parsed.scheme in {"http", "https"}
            and bool(parsed.netloc)
            and (str(posting_id) in parsed.path or len(segments) >= 2)
        )


