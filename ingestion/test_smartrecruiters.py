"""Focused SmartRecruiters platform-family tests for the banking phase.

All tests are offline: the generic adapter is exercised against a canned
SmartRecruiters Posting API client that mirrors the verified public contract
(``{offset, limit, totalFound, content[]}`` catalogue pages plus posting
detail routes).
"""

import inspect
import sys
from io import StringIO
from unittest.mock import patch

import requests
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase

from core.clean_jobs.source import source_record_eligibility_error
from core.models import Opportunity
from ingestion.adapters.smartrecruiters import (
    SmartRecruitersAdapter, SmartRecruitersContractError,
)
from ingestion.models import (
    IngestionRun, IngestionSource, OpportunitySourceRecord, RawOpportunity,
    SmartRecruitersCompany,
)
from ingestion.pipeline import run_adapter
from ingestion.pipeline.runner import _new_counters


TENANT = "TestTenant"


def listing_item(
    posting_id,
    *,
    title="Client Service Consultant",
    country="za",
    city="Johannesburg",
    region="GP",
    full_location="Johannesburg, GP, South Africa",
    custom_fields=None,
    **overrides,
):
    item = {
        "id": posting_id,
        "name": title,
        "uuid": f"uuid-{posting_id}",
        "jobAdId": f"ad-{posting_id}",
        "defaultJobAd": True,
        "refNumber": f"REF-{posting_id}",
        "company": {"identifier": TENANT, "name": "Test Employer"},
        "releasedDate": "2026-09-01T10:00:00.000Z",
        "location": {
            "city": city,
            "region": region,
            "country": country,
            "fullLocation": full_location,
            "remote": False,
            "hybrid": False,
        },
        "industry": {"id": "financial_services", "label": "Financial Services"},
        "department": {"id": "dep-1", "label": "Standard job"},
        "function": {"id": "other", "label": "Other"},
        "typeOfEmployment": {"id": "permanent", "label": "Full-time"},
        "experienceLevel": {"id": "associate", "label": "Associate"},
        "customField": custom_fields if custom_fields is not None else [
            {
                "fieldId": "COUNTRY",
                "fieldLabel": "Country/Region",
                "valueId": country or "za",
                "valueLabel": "South Africa",
            },
        ],
        "visibility": "PUBLIC",
        "ref": (
            f"https://api.smartrecruiters.com/v1/companies/{TENANT}"
            f"/postings/{posting_id}"
        ),
        "language": {"code": "en", "label": "English"},
    }
    item.update(overrides)
    return item


def detail_for(item, *, description=None, **overrides):
    posting_id = item["id"]
    text = description or (
        "<p>Responsible for serving clients and ensuring accurate transaction "
        "processing within the branch environment every day.</p>"
        "<ul><li>Deliver excellent client service</li></ul>"
    )
    detail = {
        "id": item["id"],
        "name": item["name"],
        "uuid": item["uuid"],
        "jobId": f"job-{posting_id}",
        "jobAdId": item["jobAdId"],
        "defaultJobAd": item["defaultJobAd"],
        "refNumber": item["refNumber"],
        "company": item["company"],
        "location": item["location"],
        "customField": item["customField"],
        "releasedDate": item["releasedDate"],
        "creator": {"name": "", "avatarUrl": ""},
        "postingUrl": f"https://jobs.smartrecruiters.com/{TENANT}/{posting_id}-role",
        "applyUrl": (
            f"https://jobs.smartrecruiters.com/{TENANT}/{posting_id}-role?oga=true"
        ),
        "referralUrl": (
            f"https://jobs.smartrecruiters.com/external-referrals/company/{TENANT}"
            f"/publication/{item['uuid']}"
        ),
        "jobAd": {
            "sections": {
                "companyDescription": {
                    "title": "Company Description",
                    "text": "<p>A regulated South African financial services employer.</p>",
                },
                "jobDescription": {"title": "Job Description", "text": text},
                "qualifications": {
                    "title": "Qualifications",
                    "text": "<ul><li>NQF 5 qualification in a related field</li></ul>",
                },
            },
        },
        "active": True,
        "visibility": "PUBLIC",
        "industry": item["industry"],
        "function": item["function"],
        "department": item["department"],
        "experienceLevel": item["experienceLevel"],
        "typeOfEmployment": item["typeOfEmployment"],
        "language": item["language"],
    }
    detail.update(overrides)
    return detail


class FakePostingClient:
    """Canned SmartRecruiters Posting API with call capture."""

    def __init__(
        self,
        items,
        *,
        page_size=100,
        details=None,
        detail_errors=None,
        page_overrides=None,
    ):
        self.items = list(items)
        self.page_size = page_size
        if details is None:
            details = {
                item["id"]: detail_for(item)
                for item in self.items
                if isinstance(item, dict) and item.get("id") not in (None, "")
            }
        self.details = details
        self.detail_errors = detail_errors or {}
        self.page_overrides = page_overrides or {}
        self.calls = []

    def get_json(self, url, *, params=None, respect_robots=None):
        params = dict(params or {})
        self.calls.append((url, params))
        if url.endswith("/postings"):
            offset = int(params.get("offset", 0))
            if offset in self.page_overrides:
                return self.page_overrides[offset]
            limit = min(int(params.get("limit", 100)), self.page_size)
            return {
                "offset": offset,
                "limit": limit,
                "totalFound": len(self.items),
                "content": self.items[offset:offset + limit],
            }
        posting_id = url.rsplit("/", 1)[-1]
        if posting_id in self.detail_errors:
            raise self.detail_errors[posting_id]
        if posting_id not in self.details:
            raise requests.HTTPError(f"404 for posting {posting_id}")
        return self.details[posting_id]

    def catalogue_calls(self):
        return [
            (url, params) for url, params in self.calls if url.endswith("/postings")
        ]


def make_company(identifier=TENANT, **overrides):
    source = IngestionSource.objects.create(
        name=f"{identifier} SmartRecruiters",
        code=f"smartrecruiters-{identifier.lower()}",
        source_type="api",
        company="Test Employer",
        base_url=f"https://api.smartrecruiters.com/v1/companies/{identifier}",
        priority=15,
    )
    defaults = dict(
        source=source,
        company_name="Test Employer",
        careers_url="https://careers.test.example/jobs",
        country_codes=["ZA"],
        active=True,
    )
    defaults.update(overrides)
    return SmartRecruitersCompany.objects.create(
        company_identifier=identifier, **defaults
    )


def counters(**updates):
    result = _new_counters()
    result["errors"] = []
    result["diagnostics"] = []
    result.update(updates)
    return result


def ingest(company, client, **kwargs):
    return run_adapter(
        SmartRecruitersAdapter(company, client=client), company.source, **kwargs
    )


class SmartRecruitersConfigurationTests(TestCase):
    """The two phase-1 banking tenants arrive through the seed migration."""

    def test_standard_bank_group_is_configured(self):
        config = SmartRecruitersCompany.objects.get(
            company_identifier="StandardBankGroup"
        )
        source = config.source
        self.assertEqual(source.code, "smartrecruiters-standardbankgroup")
        self.assertEqual(source.source_type, "api")
        self.assertTrue(source.active)
        self.assertEqual(source.priority, 15)
        self.assertEqual(source.company, "Standard Bank Group")
        self.assertEqual(
            source.base_url,
            "https://api.smartrecruiters.com/v1/companies/StandardBankGroup",
        )
        self.assertEqual(
            config.careers_url,
            "https://www.standardbank.com/sbg/standard-bank-group/careers/apply/jobs/view-all-jobs",
        )
        self.assertEqual(config.country_codes, ["ZA"])
        self.assertTrue(config.active)
        self.assertEqual(config.company_name, "Standard Bank Group")

    def test_outsurance_is_configured(self):
        config = SmartRecruitersCompany.objects.get(
            company_identifier="OUTsurance"
        )
        source = config.source
        self.assertEqual(source.code, "smartrecruiters-outsurance")
        self.assertEqual(source.source_type, "api")
        self.assertTrue(source.active)
        self.assertEqual(source.priority, 15)
        self.assertEqual(source.company, "OUTsurance")
        self.assertEqual(
            source.base_url,
            "https://api.smartrecruiters.com/v1/companies/OUTsurance",
        )
        self.assertEqual(
            config.careers_url, "https://careers.smartrecruiters.com/OUTsurance"
        )
        self.assertEqual(config.country_codes, ["ZA"])
        self.assertTrue(config.active)

    def test_adapter_source_contains_no_employer_specific_logic(self):
        module = sys.modules[SmartRecruitersAdapter.__module__]
        source = inspect.getsource(module)
        for token in ("StandardBank", "OUTsurance", "FAIS", "Business Segment"):
            self.assertNotIn(token, source)


class SmartRecruitersPaginationTests(TestCase):
    def setUp(self):
        self.company = make_company()

    def test_every_catalogue_page_is_traversed_completely(self):
        items = [listing_item(str(1000 + index)) for index in range(5)]
        client = FakePostingClient(items, page_size=2)
        adapter = SmartRecruitersAdapter(self.company, client=client)
        records = list(adapter.fetch())

        self.assertEqual(len(records), 5)
        self.assertEqual([r.external_id for r in records], [
            f"{TENANT}:1000", f"{TENANT}:1001", f"{TENANT}:1002",
            f"{TENANT}:1003", f"{TENANT}:1004",
        ])
        self.assertEqual(adapter.stats["pages_traversed"], 3)
        self.assertEqual(adapter.stats["listings_discovered"], 5)
        self.assertEqual(adapter.stats["valid_application_links"], 5)
        self.assertEqual(adapter.stats["detail_failures"], 0)
        offsets = [params["offset"] for _, params in client.catalogue_calls()]
        self.assertEqual(offsets, [0, 2, 4])

    def test_requests_ask_for_the_public_south_african_slice(self):
        client = FakePostingClient([listing_item("1100")])
        list(SmartRecruitersAdapter(self.company, client=client).fetch())

        catalogue_params = [params for _, params in client.catalogue_calls()]
        self.assertTrue(catalogue_params)
        for params in catalogue_params:
            self.assertEqual(params["country"], "za")
            self.assertEqual(params["destination"], "PUBLIC")
            self.assertEqual(params["limit"], 100)
        detail_urls = [url for url, _ in client.calls if not url.endswith("/postings")]
        self.assertEqual(
            detail_urls,
            [
                f"https://api.smartrecruiters.com/v1/companies/{TENANT}"
                "/postings/1100"
            ],
        )

    def test_max_jobs_bounds_detail_fetching(self):
        items = [listing_item(str(1200 + index)) for index in range(4)]
        client = FakePostingClient(items, page_size=2)
        adapter = SmartRecruitersAdapter(self.company, max_jobs=1, client=client)
        records = list(adapter.fetch())
        self.assertEqual(len(records), 1)
        detail_calls = [
            url for url, _ in client.calls if not url.endswith("/postings")
        ]
        self.assertEqual(len(detail_calls), 1)


class SmartRecruitersScopeTests(TestCase):
    """Only the explicit South African source slice becomes an Opportunity."""

    def setUp(self):
        self.company = make_company()

    def test_south_african_posting_is_admitted_with_source_country(self):
        client = FakePostingClient([listing_item("2001")])
        result = ingest(self.company, client)

        self.assertEqual(result["opportunities_created"], 1)
        opportunity = Opportunity.objects.get()
        self.assertEqual(opportunity.country, "South Africa")
        self.assertEqual(opportunity.scope_status, "unclassified")

    def test_explicitly_foreign_posting_is_not_admitted(self):
        foreign = listing_item(
            "2002",
            country="ng",
            city="Lagos",
            region="Lagos",
            full_location="Lagos, Nigeria",
        )
        client = FakePostingClient([foreign])
        result = ingest(self.company, client)

        self.assertEqual(result["opportunities_created"], 0)
        self.assertEqual(result["out_of_scope_count"], 1)
        raw = RawOpportunity.objects.get()
        self.assertEqual(raw.scope_status, "out_of_scope")
        self.assertEqual(raw.normalization_status, "out_of_scope")
        self.assertFalse(OpportunitySourceRecord.objects.exists())

    def test_posting_without_country_evidence_stays_unknown_and_is_kept(self):
        unknown = listing_item(
            "2003",
            country=None,
            city="Anytown",
            region="",
            full_location="",
            custom_fields=[],
        )
        unknown["location"] = {"city": "Anytown", "remote": False, "hybrid": False}
        client = FakePostingClient([unknown])
        result = ingest(self.company, client)

        self.assertEqual(result["opportunities_created"], 1)
        opportunity = Opportunity.objects.get()
        # Unknown stays unknown: the catalogue request (country=za) admitted it,
        # but no country value is invented for the record.
        self.assertEqual(opportunity.country, "")
        self.assertEqual(opportunity.scope_status, "unclassified")

    def test_experience_levels_are_never_an_admission_filter(self):
        levels = [
            ("2101", {"id": "student", "label": "Student"}),
            ("2102", {"id": "entry_level", "label": "Entry level"}),
            ("2103", {"id": "associate", "label": "Associate"}),
            ("2104", {"id": "mid_senior_level", "label": "Mid-Senior Level"}),
            ("2105", {"id": "director", "label": "Director"}),
            ("2106", {"id": "executive", "label": "Executive"}),
        ]
        items = [
            listing_item(posting_id, experienceLevel=level)
            for posting_id, level in levels
        ]
        client = FakePostingClient(items)
        result = ingest(self.company, client)

        self.assertEqual(result["opportunities_created"], 6)
        self.assertEqual(Opportunity.objects.count(), 6)
        labels = set(
            Opportunity.objects.values_list("source_native_career_level", flat=True)
        )
        self.assertEqual(labels, {level["label"] for _, level in levels})


class SmartRecruitersIdentityTests(TestCase):
    def setUp(self):
        self.company = make_company()

    def test_external_identity_is_company_identifier_plus_posting_id(self):
        client = FakePostingClient([listing_item("3001")])
        ingest(self.company, client)

        record = OpportunitySourceRecord.objects.get()
        self.assertEqual(record.source_external_id, f"{TENANT}:3001")
        self.assertEqual(record.reference_number, "REF-3001")

    def test_all_smartrecruiters_ids_remain_distinguishable(self):
        client = FakePostingClient([listing_item("3002")])
        ingest(self.company, client)

        record = OpportunitySourceRecord.objects.select_related("opportunity").get()
        evidence = record.opportunity.source_specific_evidence
        identifiers = {
            "posting_id": evidence["posting_id"],
            "posting_uuid": evidence["posting_uuid"],
            "job_id": evidence["job_id"],
            "job_ad_id": evidence["job_ad_id"],
            "ref_number": evidence["ref_number"],
        }
        self.assertEqual(identifiers, {
            "posting_id": "3002",
            "posting_uuid": "uuid-3002",
            "job_id": "job-3002",
            "job_ad_id": "ad-3002",
            "ref_number": "REF-3002",
        })
        self.assertEqual(len(set(identifiers.values())), 5)
        raw = record.current_raw
        self.assertEqual(raw.raw_payload["listing"]["id"], "3002")
        self.assertEqual(raw.raw_payload["posting"]["jobId"], "job-3002")

    def test_duplicate_posting_across_pages_is_one_source_opportunity(self):
        first = listing_item("3100")
        duplicate = listing_item("3100")
        other = listing_item("3101")
        client = FakePostingClient([first, other, duplicate], page_size=2)
        result = ingest(self.company, client)

        # The duplicate catalogue entry is never yielded twice: two unique
        # postings produce exactly two source opportunities, not three.
        self.assertEqual(result["records_found"], 2)
        self.assertEqual(OpportunitySourceRecord.objects.count(), 2)
        self.assertEqual(Opportunity.objects.count(), 2)

        adapter = SmartRecruitersAdapter(self.company, client=client)
        list(adapter.fetch())
        self.assertEqual(adapter.stats["duplicate_listings"], 1)

    def test_same_title_different_identity_stays_separate(self):
        items = [
            listing_item("3200", title="Universal Banker"),
            listing_item("3201", title="Universal Banker"),
        ]
        client = FakePostingClient(items)
        result = ingest(self.company, client)

        self.assertEqual(result["opportunities_created"], 2)
        self.assertEqual(Opportunity.objects.count(), 2)
        self.assertEqual(OpportunitySourceRecord.objects.count(), 2)
        self.assertEqual(
            set(
                OpportunitySourceRecord.objects.values_list(
                    "source_external_id", flat=True
                )
            ),
            {f"{TENANT}:3200", f"{TENANT}:3201"},
        )

    def test_missing_identity_fails_visibly_without_title_fallback(self):
        ghost = {
            "name": "Ghost Role Without Identity",
            "location": {"city": "Johannesburg", "country": "za"},
            "refNumber": "REF-GHOST",
        }
        client = FakePostingClient([ghost, listing_item("3300")])
        adapter = SmartRecruitersAdapter(self.company, client=client)
        with self.assertRaises(SmartRecruitersContractError) as caught:
            list(adapter.fetch())
        message = str(caught.exception)
        self.assertIn("trustworthy posting identity", message)
        self.assertIn("refusing to derive identity", message)

        run_client = FakePostingClient([ghost, listing_item("3300")])
        result = ingest(self.company, run_client)
        self.assertGreaterEqual(result["errors_count"], 1)
        self.assertEqual(Opportunity.objects.count(), 0)
        self.assertEqual(RawOpportunity.objects.exists(), False)
        run = IngestionRun.objects.get()
        self.assertEqual(run.status, "failed")
        self.assertIn("trustworthy posting identity", run.error_summary)
        self.assertIn("trustworthy posting identity", self.company.source.last_error)


class SmartRecruitersLifecycleTests(TestCase):
    def setUp(self):
        self.company = make_company()

    def test_unchanged_refetch_creates_no_duplicates_or_revisions(self):
        items = [listing_item("4001"), listing_item("4002")]
        first = ingest(self.company, FakePostingClient(items))
        second = ingest(self.company, FakePostingClient(items))

        self.assertEqual(first["opportunities_created"], 2)
        self.assertEqual(second["opportunities_created"], 0)
        self.assertEqual(second["raw_records_created"], 0)
        self.assertEqual(second["opportunities_updated"], 2)
        self.assertEqual(second["duplicates_found"], 2)
        self.assertEqual(RawOpportunity.objects.count(), 2)
        self.assertEqual(Opportunity.objects.count(), 2)
        self.assertEqual(OpportunitySourceRecord.objects.count(), 2)

    def test_changed_source_content_creates_a_new_raw_version(self):
        item = listing_item("4101")
        client = FakePostingClient([item])
        ingest(self.company, client)
        first_raw = RawOpportunity.objects.get()

        changed = FakePostingClient([item])
        changed.details["4101"]["jobAd"]["sections"]["jobDescription"]["text"] = (
            "<p>Updated responsibilities now include mentoring new branch "
            "colleagues and owning the client experience end to end.</p>"
        )
        result = ingest(self.company, changed)

        self.assertEqual(result["raw_records_created"], 1)
        self.assertEqual(RawOpportunity.objects.count(), 2)
        record = OpportunitySourceRecord.objects.get()
        self.assertNotEqual(record.current_raw_id, first_raw.pk)
        first_raw.refresh_from_db()
        self.assertNotEqual(first_raw.content_hash, record.current_raw.content_hash)
        # History is preserved, not overwritten.
        self.assertTrue(RawOpportunity.objects.filter(pk=first_raw.pk).exists())
        self.assertEqual(Opportunity.objects.count(), 1)

    def test_disappeared_posting_is_marked_missing_not_deleted(self):
        items = [listing_item("4201"), listing_item("4202")]
        ingest(self.company, FakePostingClient(items))
        result = ingest(self.company, FakePostingClient([listing_item("4201")]))

        self.assertEqual(result["records_found"], 1)
        gone = OpportunitySourceRecord.objects.get(
            source_external_id=f"{TENANT}:4202"
        )
        self.assertIsNotNone(gone.missing_since)
        self.assertTrue(gone.active_at_source)
        opportunity = gone.opportunity
        opportunity.refresh_from_db()
        self.assertTrue(opportunity.is_active)
        self.assertEqual(opportunity.lifecycle_status, "active")

    def test_acquisition_never_makes_the_opportunity_ready(self):
        client = FakePostingClient([listing_item("4301")])
        result = ingest(self.company, client)

        self.assertEqual(result["opportunities_created"], 1)
        record = OpportunitySourceRecord.objects.select_related("opportunity").get()
        opportunity = record.opportunity
        self.assertIsNone(opportunity.active_clean_revision_id)
        self.assertFalse(
            Opportunity.objects.professionally_ready()
            .filter(pk=opportunity.pk)
            .exists()
        )
        self.assertIn(
            record.current_raw.normalization_status,
            {"published", "unclassified"},
        )
        # The cleaner export path is open, but readiness still requires a
        # validated clean import.
        self.assertIsNone(source_record_eligibility_error(record))

    def test_explicitly_inactive_detail_preserves_the_source_fact(self):
        item = listing_item("4401")
        details = {"4401": detail_for(item, active=False)}
        client = FakePostingClient([item], details=details)
        result = ingest(self.company, client)

        self.assertEqual(result["opportunities_created"], 1)
        record = OpportunitySourceRecord.objects.select_related("opportunity").get()
        self.assertIs(
            record.opportunity.source_specific_evidence["active"], False
        )
        self.assertFalse(
            Opportunity.objects.professionally_ready()
            .filter(pk=record.opportunity_id)
            .exists()
        )

    def test_one_detail_failure_is_partial_and_does_not_corrupt_the_run(self):
        items = [listing_item("4501"), listing_item("4502"), listing_item("4503")]
        client = FakePostingClient(
            items,
            detail_errors={"4502": requests.HTTPError("500 server error")},
        )
        result = ingest(self.company, client)

        self.assertEqual(result["records_found"], 2)
        self.assertEqual(result["opportunities_created"], 2)
        self.assertEqual(result["detail_failures"], 1)
        self.assertGreaterEqual(result["errors_count"], 1)
        self.assertEqual(Opportunity.objects.count(), 2)
        self.assertFalse(
            OpportunitySourceRecord.objects.filter(
                source_external_id=f"{TENANT}:4502"
            ).exists()
        )
        run = IngestionRun.objects.get()
        self.assertEqual(run.status, "partial")

    def test_malformed_detail_and_identity_mismatch_record_contract_errors(self):
        items = [listing_item("4601"), listing_item("4602"), listing_item("4603")]
        details = {
            "4601": detail_for(items[0]),
            "4602": "<html>Service Unavailable</html>",
            "4603": detail_for(items[2], id="9999"),
        }
        client = FakePostingClient(items, details=details)
        result = ingest(self.company, client)

        self.assertEqual(result["records_found"], 1)
        self.assertEqual(result["detail_failures"], 2)
        run = IngestionRun.objects.get()
        self.assertEqual(run.status, "partial")
        steps = {error.get("step") for error in result["errors"]}
        self.assertIn("detail_contract", steps)

    def test_collection_shape_change_fails_the_run_visibly(self):
        client = FakePostingClient(
            [listing_item("4701")],
            page_overrides={0: {"results": [], "totalFound": 0}},
        )
        result = ingest(self.company, client)

        self.assertGreaterEqual(result["errors_count"], 1)
        self.assertEqual(Opportunity.objects.count(), 0)
        run = IngestionRun.objects.get()
        self.assertEqual(run.status, "failed")
        self.assertIn("'content'", run.error_summary)
        self.assertIn("'content'", self.company.source.last_error)

    def test_country_scope_must_be_configured_exactly_once(self):
        company = make_company(
            "NoScopeTenant",
            company_name="No Scope Co",
            country_codes=[],
        )
        result = run_adapter(
            SmartRecruitersAdapter(
                company, client=FakePostingClient([listing_item("4801")])
            ),
            company.source,
        )
        self.assertGreaterEqual(result["errors_count"], 1)
        run = IngestionRun.objects.get()
        self.assertEqual(run.status, "failed")
        self.assertIn("exactly one country scope", run.error_summary)


class SmartRecruitersCustomFieldTests(TestCase):
    def test_vendor_custom_fields_survive_without_employer_hardcoding(self):
        company = make_company()
        custom = [
            {
                "fieldId": "5f3cc8098669527e4bd06487",
                "fieldLabel": "FAIS",
                "valueId": "45f087c9-204f-482c-9738-72132c6a0c43",
                "valueLabel": "Financial Advice Giving Role",
            },
            {
                "fieldId": "62876b58f049c63266563039",
                "fieldLabel": "Business Segment",
                "valueId": "personalprivatebanking",
                "valueLabel": "Personal & Private Banking",
            },
        ]
        item = listing_item("5001", custom_fields=custom)
        result = ingest(company, FakePostingClient([item]))

        self.assertEqual(result["opportunities_created"], 1)
        raw = RawOpportunity.objects.get()
        self.assertEqual(raw.raw_payload["listing"]["customField"], custom)
        self.assertEqual(raw.raw_payload["posting"]["customField"], custom)
        opportunity = Opportunity.objects.get()
        self.assertEqual(
            opportunity.source_specific_evidence["custom_fields"], custom
        )
        # Vendor custom data stays source evidence: no employer-specific
        # canonical field absorbs it and no qualification/eligibility meaning
        # is invented during acquisition.
        self.assertEqual(opportunity.business_unit, "")
        self.assertEqual(opportunity.qualification_text, "")
        self.assertEqual(opportunity.eligibility_text, "")
        self.assertEqual(opportunity.source_native_career_level, "Associate")
        self.assertEqual(opportunity.employment_type, "Full-time")


class SmartRecruitersCommandTests(TestCase):
    @patch("ingestion.management.commands.ingest_smartrecruiters.run_adapter")
    def test_ingest_command_targets_only_the_selected_company(self, mocked_run):
        mocked_run.return_value = counters()
        company = make_company()
        output = StringIO()
        call_command(
            "ingest_smartrecruiters",
            "--company", TENANT,
            "--dry-run",
            stdout=output,
        )

        self.assertEqual(mocked_run.call_count, 1)
        adapter, source = mocked_run.call_args.args
        self.assertIsInstance(adapter, SmartRecruitersAdapter)
        self.assertEqual(adapter.company.company_identifier, TENANT)
        self.assertEqual(source, company.source)
        self.assertIn("smartrecruiters-testtenant", output.getvalue())

    @patch("ingestion.management.commands.ingest_smartrecruiters.run_adapter")
    def test_command_without_filter_targets_all_seeded_tenants(self, mocked_run):
        mocked_run.return_value = counters()
        make_company()
        call_command(
            "ingest_smartrecruiters", "--dry-run", stdout=StringIO()
        )

        identifiers = sorted(
            call.args[0].company.company_identifier
            for call in mocked_run.call_args_list
        )
        self.assertEqual(
            identifiers, ["OUTsurance", "StandardBankGroup", TENANT]
        )

    def test_unknown_company_filter_is_a_command_error(self):
        with self.assertRaises(CommandError):
            call_command(
                "ingest_smartrecruiters",
                "--company", "NoSuchTenant",
                stdout=StringIO(),
            )
