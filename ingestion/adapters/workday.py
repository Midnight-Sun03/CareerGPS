"""Generic public Workday (Candidate Experience / "CXS") board family.

One adapter serves every Workday employer board.  Employer-specific
configuration (``tenant``, ``site``, country scope, careers entry point) lives on
:class:`ingestion.models.WorkdayBoard`, so new Workday employers are added
through configuration rather than new scraping code.

Verified live contract (2026-09-26, read-only probes against all five boards):

* catalogue: ``POST {host}/wday/cxs/{tenant}/{site}/jobs`` with
  ``{"appliedFacets": {}, "limit": 20, "offset": N, "searchText": ""}``.
  The ``/jobs`` suffix is required; the same request without it is rejected with
  ``HTTP_400``.
* ``limit`` is capped at 20.  Larger values return a body with no ``total`` and
  no postings, so the page size is pinned to the verified maximum.
* the response carries ``{total, jobPostings[], facets[]}``.  ``total`` is not
  stable across pages and ``offset`` wraps back to the first page, so pagination
  terminates on a short page, an empty page, or a page that adds no new posting
  identity -- never on ``total`` alone.
* the search facets come back without values, so the catalogue cannot be filtered
  by country server-side.  Explicit country scope is therefore taken from each
  posting's detail record, and anything the source explicitly places outside the
  configured country is gated out of scope.
* posting detail: ``GET {host}/wday/cxs/{tenant}/{site}{externalPath}`` with the
  site segment retained.  The description lives in ``jobDescription`` (not
  ``description``), and the country descriptor plus ``alpha2Code`` live under
  ``jobRequisitionLocation.country``.

This module only acquires source evidence from the public board.  It is not the
cleaner, the taxonomy layer, the matching engine, or a readiness switch:
acquired records enter the existing raw/non-ready CareerGPS lifecycle and only a
validated clean import can make them READY.
"""

import copy
import re

import requests

from ingestion.pipeline.normalization import geographic_scope_evidence, plain_text
from ingestion.pipeline.records import FetchedOpportunity
from .base import PoliteHttpClient


class WorkdayContractError(RuntimeError):
    """The live Workday contract materially deviated from what is verified.

    Raised for catalogue-shape and posting-identity failures so a run fails
    visibly instead of silently inventing identity or structure.
    """


#: Ephemeral CXS detail fields that must not create a new raw version on every
#: run.  ``timeLeftToApply`` and the relative ``postedOn`` phrase change purely
#: with wall-clock time, and ``userAuthenticated`` reflects the caller.
EPHEMERAL_DETAIL_KEYS = frozenset({
    "userAuthenticated", "timeLeftToApply", "postedOn", "jobPostingEndDateAsText",
})


class WorkdayAdapter:
    #: Verified live maximum page size; larger ``limit`` values yield no ``total``.
    PAGE_LIMIT = 20
    PARSER_VERSION = "workday-cxs-v1"

    def __init__(self, board, max_jobs=None, client=None):
        self.board = board
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

    # -- endpoints ---------------------------------------------------------
    @property
    def host(self):
        return f"{self.board.tenant}.wd3.myworkdayjobs.com"

    @property
    def site_url(self):
        return f"https://{self.host}/{self.board.site}"

    @property
    def catalogue_url(self):
        """Verified: the CXS catalogue requires the trailing ``/jobs`` segment."""
        return f"https://{self.host}/wday/cxs/{self.board.tenant}/{self.board.site}/jobs"

    def detail_url(self, external_path):
        """Verified: the detail route keeps the site segment before ``externalPath``."""
        return f"https://{self.host}/wday/cxs/{self.board.tenant}/{self.board.site}{external_path}"

    # -- configuration -----------------------------------------------------
    def _country_scope(self):
        codes = [
            str(code).strip()
            for code in (self.board.country_codes or [])
            if str(code).strip()
        ]
        if len(codes) != 1:
            raise WorkdayContractError(
                "Workday source configuration failure: "
                f"tenant={self.board.tenant} site={self.board.site} must configure exactly "
                "one country scope. The verified CXS catalogue returns its search facets "
                "without values, so country scope is applied per posting from the detail "
                f"record and must therefore be unambiguous (observed {codes!r})."
            )
        return codes[0].upper()

    def _contract_error(self, endpoint, expected, observed):
        return WorkdayContractError(
            "Workday source contract failure: "
            f"tenant={self.board.tenant} site={self.board.site} "
            f"endpoint={endpoint} expected={expected} observed={observed}"
        )

    def _request_headers(self):
        # Verified live: the CXS endpoints answer a same-site JSON request.
        return {
            "Accept": "application/json",
            "Accept-Language": "en-US",
            "Referer": self.site_url,
        }

    # -- catalogue ---------------------------------------------------------
    def _catalogue_page(self, offset):
        endpoint = self.catalogue_url
        payload = {
            "appliedFacets": {},
            "limit": self.PAGE_LIMIT,
            "offset": offset,
            "searchText": "",
        }
        # ``post_json`` returns the raw response; the contract check below
        # validates the parsed body.
        response = self.client.post_json(
            endpoint, payload=payload, respect_robots=False,
            headers=self._request_headers(),
        )
        page = response.json() if hasattr(response, "json") else response
        if not isinstance(page, dict):
            raise self._contract_error(
                endpoint,
                "a JSON object with a 'jobPostings' collection",
                type(page).__name__,
            )
        postings = page.get("jobPostings")
        if not isinstance(postings, list):
            raise self._contract_error(
                endpoint,
                "'jobPostings' to be a list of postings",
                f"{type(postings).__name__}={postings!r:.200}",
            )
        return postings, page.get("total")

    @staticmethod
    def _external_path(item, endpoint, board_label):
        if not isinstance(item, dict):
            raise WorkdayContractError(
                "Workday source contract failure: "
                f"{board_label} endpoint={endpoint} "
                f"expected=a posting object in 'jobPostings' observed={type(item).__name__}"
            )
        path = item.get("externalPath")
        if not isinstance(path, str) or not path.startswith("/") or not path.strip():
            raise WorkdayContractError(
                "Workday source contract failure: "
                f"{board_label} endpoint={endpoint} "
                f"expected=a trustworthy posting identity ('externalPath') observed={path!r}; "
                "refusing to derive identity from title/location."
            )
        return path.strip()

    def fetch(self):
        """Yield one record per posting, walking every page the board exposes."""
        self._country_scope()  # fail fast on ambiguous configuration
        board_label = f"tenant={self.board.tenant} site={self.board.site}"
        offset = 0
        yielded = 0
        seen = set()
        while True:
            endpoint = self.catalogue_url
            postings, _total = self._catalogue_page(offset)
            self.stats["pages_traversed"] += 1
            if not postings:
                # Empty page: verified terminating condition.
                break
            new_on_page = 0
            for item in postings:
                external_path = self._external_path(item, endpoint, board_label)
                if external_path in seen:
                    # ``offset`` wraps on these boards; a replayed page is not new work.
                    self.stats["duplicate_listings"] += 1
                    continue
                if self.max_jobs is not None and yielded >= self.max_jobs:
                    return
                seen.add(external_path)
                new_on_page += 1
                self.stats["listings_discovered"] += 1
                record = self._build_record(external_path, item)
                if record is None:
                    continue  # per-detail failure already recorded
                yielded += 1
                yield record
            if new_on_page == 0:
                # A page that adds no new identity means the catalogue wrapped;
                # continuing would loop forever.
                break
            if len(postings) < self.PAGE_LIMIT:
                # Verified short page: the last page of the board.
                break
            offset += self.PAGE_LIMIT


    # -- detail ------------------------------------------------------------
    def _build_record(self, external_path, listing):
        endpoint = self.detail_url(external_path)
        try:
            detail = self.client.get_json(
                endpoint, respect_robots=False, headers=self._request_headers(),
            )
        except requests.RequestException as exc:
            # One bad posting must not kill the whole source run.
            self.stats["detail_failures"] += 1
            self.errors.append({
                "step": "detail_fetch",
                "tenant": self.board.tenant,
                "site": self.board.site,
                "external_path": external_path,
                "url": endpoint,
                "error": str(exc)[:500],
            })
            return None
        if not isinstance(detail, dict):
            self.stats["detail_failures"] += 1
            self.errors.append({
                "step": "detail_contract",
                "tenant": self.board.tenant,
                "site": self.board.site,
                "external_path": external_path,
                "error": (
                    f"detail response is {type(detail).__name__}, expected the "
                    "Workday CXS job detail object"
                ),
            })
            return None
        info = detail.get("jobPostingInfo")
        if not isinstance(info, dict):
            self.stats["detail_failures"] += 1
            self.errors.append({
                "step": "detail_contract",
                "tenant": self.board.tenant,
                "site": self.board.site,
                "external_path": external_path,
                "error": (
                    "detail response has no 'jobPostingInfo' object; the CXS detail "
                    "contract changed"
                ),
            })
            return None
        self.stats["detail_pages_fetched"] += 1
        return self._make_record(external_path, listing, detail, info)

    @staticmethod
    def _location_text(info):
        location_text = plain_text(info.get("location"))
        additional = info.get("additionalLocations")
        extras = []
        if isinstance(additional, list):
            extras = [plain_text(item) for item in additional if plain_text(item)]
        if extras:
            joined = ", ".join(extras)
            location_text = f"{location_text}, {joined}" if location_text else joined
        return location_text

    def _make_record(self, external_path, listing, detail, info):
        title = plain_text(info.get("title")) or plain_text(listing.get("title"))
        # Verified: the description is served as ``jobDescription`` HTML.
        description = info.get("jobDescription") or ""
        location_text = self._location_text(info)

        # Verified: the country descriptor and alpha2 code live under
        # ``jobRequisitionLocation.country`` (with a top-level fallback).
        requisition_location = info.get("jobRequisitionLocation")
        if not isinstance(requisition_location, dict):
            requisition_location = {}
        country = requisition_location.get("country")
        if not isinstance(country, dict):
            country = info.get("country") if isinstance(info.get("country"), dict) else {}
        country_descriptor = plain_text(country.get("descriptor"))
        country_code = plain_text(country.get("alpha2Code")).upper()

        geography = geographic_scope_evidence(
            # The descriptor is the source's own human-readable country name and
            # is preferred over the bare alpha2 code, which the shared helper
            # would otherwise pass through verbatim as the country value.
            country=country_descriptor or country_code,
            location=location_text,
            country_source_field="Workday jobRequisitionLocation.country",
            location_source_field="Workday jobPostingInfo.location",
        )
        if geography["status"] == "confirmed_outside_south_africa":
            # The catalogue cannot filter by country server-side, so postings the
            # source explicitly places abroad are counted and gated here.
            self.stats["non_south_africa_postings"] += 1

        apply_url = plain_text(info.get("externalUrl"))
        application_link_valid = self._application_link_valid(apply_url, external_path)
        if not apply_url or not application_link_valid:
            self.stats["link_errors"] += 1
            self.errors.append({
                "step": "application_url",
                "tenant": self.board.tenant,
                "site": self.board.site,
                "external_path": external_path,
                "url": apply_url,
                "error": "posting has no job-specific HTTP(S) route on the public board",
            })
        else:
            self.stats["valid_application_links"] += 1

        # ``startDate`` is the machine-readable posting date; ``postedOn`` is a
        # relative human phrase and is preserved as evidence only.
        posted_at = plain_text(info.get("startDate"))


        additional = info.get("additionalLocations")
        evidence = {
            "platform": "workday",
            "tenant": self.board.tenant,
            "site": self.board.site,
            "external_path": external_path,
            "job_posting_id": plain_text(info.get("jobPostingId")),
            "job_req_id": plain_text(info.get("jobReqId")),
            "cxs_posting_id": plain_text(info.get("id")),
            "job_posting_site_id": plain_text(info.get("jobPostingSiteId")),
            "posted_on_text": plain_text(info.get("postedOn")),
            "time_left_to_apply": plain_text(info.get("timeLeftToApply")),
            "start_date": posted_at,
            "end_date": plain_text(info.get("endDate")),
            "time_type": plain_text(info.get("timeType")),
            "remote_type": plain_text(info.get("remoteType")) or plain_text(listing.get("remoteType")),
            "can_apply": info.get("canApply"),
            "questionnaire_id": plain_text(info.get("questionnaireId")),
            "country_descriptor": country_descriptor,
            "country_alpha2": country_code,
            "job_requisition_location": requisition_location,
            "additional_locations": additional if isinstance(additional, list) else [],
            "hiring_organization": detail.get("hiringOrganization"),
            "listing_bullet_fields": listing.get("bulletFields"),
            "listing_locations_text": plain_text(listing.get("locationsText")),
            "listing_posted_on": plain_text(listing.get("postedOn")),
            "public_url": apply_url,
            "geography": geography,
        }

        fields = {
            "title": title,
            "company": self.board.company_name,
            "description": description,
            "location_text": location_text,
            "country": geography["country"] or country_descriptor,
            "geographic_scope": geography["status"],
            "geographic_scope_provenance": geography,
            "posted_at": posted_at,
            "closing_date": plain_text(info.get("endDate")),
            "reference_number": plain_text(info.get("jobReqId")),
            "employment_type": plain_text(info.get("timeType")),
            "work_arrangement": plain_text(info.get("remoteType")),
            "evidence_provenance": {
                "description": {"source_field": "jobPostingInfo.jobDescription", "confidence": "high"},
                "geography": geography,
                "posted_at": {
                    "source_field": "jobPostingInfo.startDate",
                    "confidence": "high",
                },
            },
            "source_specific_evidence": evidence,
        }
        if geography["status"] == "confirmed_outside_south_africa":
            # Explicit, source-stated scope exclusion recorded with provenance.
            fields["source_scope_exclusion"] = {
                "reason": (
                    f"Source states this posting is in {geography['country']}, "
                    f"outside the configured {self._country_scope()} scope"
                ),
                "source_field": geography["source_field"],
                "confidence": geography["confidence"],
                "raw": geography["raw"],
            }

        return FetchedOpportunity(
            external_id=f"{self.board.tenant}:{self.board.site}:{external_path}",
            listing_url=plain_text(self.board.careers_url) or self.site_url,
            source_url=apply_url or self.detail_url(external_path),
            apply_url=apply_url,
            payload={
                "platform": "workday",
                "tenant": self.board.tenant,
                "site": self.board.site,
                "external_path": external_path,
                # Verbatim mirror of the title so the existing clean export title
                # check can discover it in the raw payload.
                "title": title,
                "listing": listing,
                "detail": detail,
            },
            raw_content=description,
            fields=fields,
            parser_version=self.PARSER_VERSION,
            detail_http_status=200,
            application_link_valid=application_link_valid,
        )

    def fingerprint_material(self, record):
        """Hash stable vacancy content only.

        Wall-clock CXS fields (``timeLeftToApply``, the relative ``postedOn``
        phrase) and the caller-specific ``userAuthenticated`` flag change on every
        request and would otherwise mint a new raw version daily for vacancies
        that have not actually changed.
        """
        payload = copy.deepcopy(record.payload)
        payload.pop("userAuthenticated", None)
        detail = payload.get("detail")
        if isinstance(detail, dict):
            for key in EPHEMERAL_DETAIL_KEYS:
                detail.pop(key, None)
            info = detail.get("jobPostingInfo")
            if isinstance(info, dict):
                for key in EPHEMERAL_DETAIL_KEYS:
                    info.pop(key, None)
        return payload, record.raw_content

    @staticmethod
    def _application_link_valid(apply_url, external_path):
        """A public Workday route is job-specific when it keeps a ``/job/`` segment."""
        if not apply_url or not external_path:
            return False
        if not re.match(r"^https?://[^/]+/.+", apply_url):
            return False
        return "/job/" in apply_url

