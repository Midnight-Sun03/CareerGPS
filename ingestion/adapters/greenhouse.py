from html import unescape
from urllib.parse import urlparse

from ingestion.pipeline.normalization import geographic_scope_evidence, parse_decimal, plain_text
from ingestion.pipeline.records import FetchedOpportunity
from .base import PoliteHttpClient
from .greenhouse_employers import interpret_greenhouse_employer


class GreenhouseAdapter:
    API_ROOT = "https://boards-api.greenhouse.io/v1/boards/{token}"

    def __init__(self, board, max_jobs=None, client=None):
        self.board = board
        self.max_jobs = max_jobs
        self.client = client or PoliteHttpClient(delay=0.25)
        self.stats = {
            "listings_discovered": 0, "pages_traversed": 0,
            "detail_pages_fetched": 0, "valid_application_links": 0,
            "parsing_errors": 0, "link_errors": 0,
        }
        self.errors = []

    @staticmethod
    def _metadata_value(metadata, *labels):
        wanted = {label.lower() for label in labels}
        for item in metadata or []:
            if str(item.get("name", "")).lower() in wanted:
                return item.get("value")
        return None

    @staticmethod
    def _country_from_location(location):
        return geographic_scope_evidence(location=location)["country"]

    @staticmethod
    def _pay_fields(detail):
        ranges = detail.get("pay_input_ranges") or []
        if not ranges:
            return {}
        pay_range = ranges[0] or {}

        def amount(*keys):
            for key in keys:
                if pay_range.get(key) not in (None, ""):
                    value = parse_decimal(pay_range[key])
                    return value / 100 if value is not None and key.endswith("_cents") else value
            return None

        minimum = amount("min_cents", "minimum_cents", "min", "minimum")
        maximum = amount("max_cents", "maximum_cents", "max", "maximum")
        currency = pay_range.get("currency_code") or pay_range.get("currency") or ""
        label = pay_range.get("title") or pay_range.get("label") or ""
        return {
            "salary_min": minimum,
            "salary_max": maximum,
            "salary_currency": currency,
            "compensation_text": label,
        }

    @classmethod
    def _source_text(cls, value):
        if value in (None, ""):
            return ""
        if isinstance(value, (list, tuple)):
            return ", ".join(filter(None, (cls._source_text(item) for item in value)))
        if isinstance(value, dict):
            preferred = [value.get(key) for key in ("name", "label", "value", "title") if value.get(key) not in (None, "")]
            return cls._source_text(preferred or list(value.values()))
        return plain_text(value)

    @classmethod
    def _education_text(cls, value):
        if isinstance(value, str) and value.strip().lower() in {
            "education_required", "education_optional", "education_disabled",
        }:
            return ""
        return cls._source_text(value)

    def fetch(self):
        root = self.API_ROOT.format(token=self.board.board_token)
        listing = self.client.get_json(f"{root}/jobs", params={"content": "true"}, respect_robots=False)
        jobs = listing.get("jobs", [])
        self.stats["pages_traversed"] = 1
        self.stats["listings_discovered"] = len(jobs)
        if self.max_jobs:
            jobs = jobs[:self.max_jobs]
        for summary in jobs:
            job_id = summary["id"]
            detail = self.client.get_json(
                f"{root}/jobs/{job_id}",
                params={"questions": "true", "pay_transparency": "true", "pay_input_ranges": "true"},
                respect_robots=False,
            )
            self.stats["detail_pages_fetched"] += 1
            combined = {"board_token": self.board.board_token, "list_record": summary, "job": detail}
            content = unescape(unescape(detail.get("content") or summary.get("content") or ""))
            departments = detail.get("departments") or summary.get("departments") or []
            offices = detail.get("offices") or summary.get("offices") or []
            metadata = detail.get("metadata") or summary.get("metadata") or []
            education = detail.get("education") or summary.get("education") or []
            location = (detail.get("location") or summary.get("location") or {}).get("name", "")
            geography = geographic_scope_evidence(
                location=location, location_source_field="Greenhouse location.name"
            )
            closing_date = self._metadata_value(metadata, "application deadline", "closing date", "apply by")
            reference = detail.get("requisition_id") or summary.get("requisition_id") or detail.get("internal_job_id")
            fields = {
                "title": detail.get("title") or summary.get("title"),
                "company": self.board.company_name,
                "description": content,
                "location_text": location,
                "country": geography["country"],
                "geographic_scope": geography["status"],
                "geographic_scope_provenance": geography,
                "publishable": geography["status"] != "confirmed_outside_south_africa",
                "posted_at": detail.get("first_published") or summary.get("first_published") or detail.get("updated_at"),
                "closing_date": detail.get("application_deadline") or closing_date,
                "department": ", ".join(item.get("name", "") for item in departments if item.get("name")),
                "office": ", ".join(item.get("name", "") for item in offices if item.get("name")),
                "reference_number": reference,
                "requisition_id": detail.get("requisition_id") or summary.get("requisition_id"),
                "internal_job_id": detail.get("internal_job_id") or summary.get("internal_job_id"),
                "updated_at": detail.get("updated_at") or summary.get("updated_at"),
                # Greenhouse ``education`` is application-form configuration,
                # not an employer qualification requirement.
                "qualification_text": "",
                "employment_type": self._metadata_value(metadata, "employment type", "contract type") or "",
                "compensation_text": plain_text(self._metadata_value(metadata, "salary", "compensation", "pay range") or ""),
                "source_native_category": ", ".join(item.get("name", "") for item in departments if item.get("name")),
                "qualification_minimum": "",
                "evidence_provenance": {
                    "description": {
                        "source_field": "content", "confidence": "high",
                        "source_text": plain_text(content),
                    },
                    "department": {
                        "source_field": "departments", "confidence": "high",
                    },
                    "office": {
                        "source_field": "offices", "confidence": "high",
                    },
                    "geography": geography,
                },
            }
            fields.update({key: value for key, value in self._pay_fields(detail).items() if value not in (None, "")})
            combined["structured"] = {
                "departments": departments,
                "offices": offices,
                "metadata": metadata,
                "education": education,
                "requisition_id": fields["requisition_id"],
                "internal_job_id": fields["internal_job_id"],
                "first_published": fields["posted_at"],
                "updated_at": fields["updated_at"],
                "application_deadline": fields["closing_date"],
                "pay_input_ranges": detail.get("pay_input_ranges") or [],
                "questions": detail.get("questions") or [],
                "geography": geography,
            }
            employer_fields = interpret_greenhouse_employer(self.board.board_token, detail, content)
            employer_evidence = employer_fields.pop("source_specific_evidence", {})
            fields.update(employer_fields)
            if employer_evidence:
                combined["structured"]["employer_interpretation"] = employer_evidence
            fields["source_specific_evidence"] = combined["structured"]
            absolute_url = detail.get("absolute_url") or summary.get("absolute_url") or ""
            valid_application = (
                urlparse(absolute_url).scheme in {"http", "https"}
                and bool(urlparse(absolute_url).netloc)
                and str(job_id) in absolute_url
            )
            self.stats["valid_application_links"] += int(valid_application)
            if not valid_application:
                self.stats["link_errors"] += 1
                self.errors.append({"step": "application_url", "job_id": job_id, "url": absolute_url})
            yield FetchedOpportunity(
                external_id=f"{self.board.board_token}:{job_id}",
                listing_url=getattr(self.board, "careers_url", "") or f"{root}/jobs",
                source_url=absolute_url,
                apply_url=absolute_url,
                payload=combined,
                raw_content=content,
                fields=fields,
                parser_version="greenhouse-job-board-v2",
                detail_http_status=200,
                application_link_valid=valid_application,
            )
