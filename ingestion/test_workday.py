"""Focused Workday CXS platform-family tests for the five phase-2 boards.

All tests are offline: the generic adapter is exercised against a canned CXS
client mirroring the live verified contract --
``POST .../wday/cxs/{tenant}/{site}/jobs`` returning pages of at most 20 as
``{total, jobPostings[]}``, and detail routes
``GET .../wday/cxs/{tenant}/{site}{externalPath}`` returning ``jobPostingInfo``
whose description lives in ``jobDescription`` and whose country lives under
``jobRequisitionLocation.country``.
"""

import copy
from datetime import timedelta
from io import StringIO
from unittest.mock import patch

import requests
from django.conf import settings
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase
from django.utils import timezone

from core.models import Opportunity
from ingestion.adapters.workday import WorkdayAdapter
from ingestion.models import (
    IngestionRun, IngestionSource, OpportunityExtraction,
    OpportunitySourceRecord, RawOpportunity, WorkdayBoard,
)
from ingestion.pipeline import run_adapter
from ingestion.pipeline.runner import _new_counters


# A board pair that is not seeded by the migration, so tests can create their own
# rows without colliding with the five configured phase-2 boards.
TEST_TENANT = "testtenant"
TEST_SITE = "TestCareers"


def listing_item(
    external_path,
    *,
    title="Client Service Consultant",
    locations_text="Johannesburg",
    posted_on="Posted Yesterday",
    **overrides,
):
    item = {
        "title": title,
        "externalPath": external_path,
        "locationsText": locations_text,
        "postedOn": posted_on,
        "bulletFields": ["R-15991295", "2026-10-09"],
    }
    item.update(overrides)
    return item


def detail_for(
    item,
    *,
    description="<p>Serve clients accurately within the branch environment every day.</p>",
    country_code="ZA",
    country_name="South Africa",
    location="Johannesburg",
    additional_locations=None,
    site=TEST_SITE,
    **overrides,
):
    """Build a CXS detail payload matching the verified live shape."""
    path = item["externalPath"]
    job_posting_id = path.rsplit("/", 1)[-1]
    info = {
        "title": item["title"],
        "jobDescription": description,
        "location": location,
        "jobPostingId": job_posting_id,
        "jobReqId": f"REQ-{job_posting_id}",
        "id": f"cxs-{job_posting_id}",
        "jobPostingSiteId": site,
        "postedOn": item.get("postedOn"),
        "timeLeftToApply": "11 Days Remaining",
        "startDate": "2026-09-25",
        "endDate": "2026-10-30",
        "timeType": "Full time",
        "remoteType": "",
        "canApply": True,
        "questionnaireId": "",
        "jobRequisitionLocation": {
            "descriptor": location,
            "country": {
                "descriptor": country_name,
                "id": f"id-{country_code}",
                "alpha2Code": country_code,
            },
        },
        "externalUrl": f"https://{TEST_TENANT}.wd3.myworkdayjobs.com/{site}{path}",
    }
    if additional_locations is not None:
        info["additionalLocations"] = additional_locations
    info.update(overrides)
    return {
        "hiringOrganization": {"name": "1001 Absa Bank", "url": ""},
        "jobPostingInfo": info,
        "similarJobs": [],
        "userAuthenticated": False,
    }


class FakeCxsClient:
    """Canned CXS endpoints with call capture, mirroring the verified contract."""

    def __init__(self, items, *, page_size=20, details=None, detail_errors=None,
                 page_overrides=None, site=TEST_SITE):
        self.items = list(items)
        self.page_size = page_size
        self.site = site
        if details is None:
            details = {
                item["externalPath"]: detail_for(item)
                for item in self.items
                if isinstance(item, dict) and item.get("externalPath")
            }
        self.details = details
        self.detail_errors = detail_errors or {}
        self.page_overrides = page_overrides or {}
        self.calls = []

    def post_json(self, url, *, payload=None, respect_robots=None, headers=None):
        payload = dict(payload or {})
        self.calls.append(("POST", url, payload, headers))
        if not url.endswith("/jobs"):
            raise AssertionError(f"catalogue must use the verified /jobs suffix: {url}")
        offset = int(payload.get("offset", 0))
        if offset in self.page_overrides:
            return self.page_overrides[offset]
        limit = min(int(payload.get("limit", 20)), self.page_size)
        return {
            "total": len(self.items),
            "jobPostings": self.items[offset:offset + limit],
            "facets": [],
        }

    def get_json(self, url, *, params=None, respect_robots=None, headers=None):
        self.calls.append(("GET", url, params, headers))
        path = url.split(f"/{self.site}", 1)[-1]
        if path in self.detail_errors:
            raise self.detail_errors[path]
        if path not in self.details:
            raise requests.HTTPError(f"404 for posting {path}")
        return self.details[path]

    def catalogue_calls(self):
        return [c for c in self.calls if c[0] == "POST"]

    def detail_calls(self):
        return [c for c in self.calls if c[0] == "GET"]


def make_board(tenant=TEST_TENANT, site=TEST_SITE, company_name="Test Employer", **overrides):
    source = IngestionSource.objects.create(
        name=f"{site} Workday",
        code=f"workday-{site.lower()}",
        source_type="api",
        company=company_name,
        base_url=f"https://{tenant}.wd3.myworkdayjobs.com/wday/cxs/{tenant}/{site}/jobs",
        priority=15,
    )
    defaults = dict(
        source=source,
        company_name=company_name,
        tenant=tenant,
        careers_url=f"https://{tenant}.wd3.myworkdayjobs.com/{site}",
        country_codes=["ZA"],
        active=True,
    )
    defaults.update(overrides)
    return WorkdayBoard.objects.create(site=site, **defaults)


def counters(**updates):
    result = _new_counters()
    result["errors"] = []
    result["diagnostics"] = []
    result.update(updates)
    return result


def ingest(board, client, **kwargs):
    return run_adapter(WorkdayAdapter(board, client=client), board.source, **kwargs)


def client_for(board, items, **kwargs):
    """Build a fake client bound to the board's own site segment."""
    kwargs.setdefault("site", board.site)
    return FakeCxsClient(items, **kwargs)



class WorkdayConfigurationTests(TestCase):
    """The five phase-2 boards arrive through the seed migration."""

    def test_all_five_boards_are_configured_with_verified_segments(self):
        expected = {
            "ABSAcareersite": ("absa", "Absa"),
            "Old_Mutual_Careers": ("oldmutual", "Old Mutual"),
            "Old_Mutual_Early_Careers": ("oldmutual", "Old Mutual"),
            "FRB": ("firstrand", "FirstRand"),
            "PNP_Careers": ("picknpay", "Pick n Pay"),
        }
        for site, (tenant, company) in expected.items():
            with self.subTest(site=site):
                board = WorkdayBoard.objects.get(site=site)
                self.assertEqual(board.tenant, tenant)
                self.assertEqual(board.company_name, company)
                self.assertEqual(board.country_codes, ["ZA"])
                self.assertTrue(board.active)
                self.assertEqual(board.source.source_type, "api")
                self.assertTrue(board.source.active)
                # base_url must be the verified /jobs catalogue route.
                self.assertTrue(
                    board.source.base_url.endswith(
                        f"/wday/cxs/{tenant}/{site}/jobs"
                    ),
                    board.source.base_url,
                )

    def test_old_mutual_early_careers_empty_board_is_still_configured(self):
        board = WorkdayBoard.objects.get(site="Old_Mutual_Early_Careers")
        self.assertIn("empty", board.relevance_notes.lower())


class WorkdayEndpointContractTests(TestCase):
    def setUp(self):
        self.board = make_board()

    def test_catalogue_uses_verified_jobs_suffix_and_page_limit(self):
        client = client_for(self.board, [listing_item("/job/Johannesburg/Consultant_JR-1")])
        ingest(self.board, client)

        _method, url, payload, headers = client.catalogue_calls()[0]
        self.assertEqual(
            url,
            f"https://{TEST_TENANT}.wd3.myworkdayjobs.com"
            f"/wday/cxs/{TEST_TENANT}/{TEST_SITE}/jobs",
        )
        self.assertEqual(payload["limit"], 20)
        self.assertEqual(payload["offset"], 0)
        self.assertEqual(payload["appliedFacets"], {})
        self.assertEqual(payload["searchText"], "")
        self.assertEqual(headers["Accept"], "application/json")
        self.assertEqual(
            headers["Referer"],
            f"https://{TEST_TENANT}.wd3.myworkdayjobs.com/{TEST_SITE}",
        )

    def test_detail_route_retains_the_site_segment(self):
        path = "/job/Johannesburg/Consultant_JR-1"
        client = client_for(self.board, [listing_item(path)])
        ingest(self.board, client)

        self.assertEqual(
            client.detail_calls()[0][1],
            f"https://{TEST_TENANT}.wd3.myworkdayjobs.com"
            f"/wday/cxs/{TEST_TENANT}/{TEST_SITE}{path}",
        )

    def test_description_is_read_from_job_description(self):
        client = FakeCxsClient([listing_item("/job/Johannesburg/Consultant_JR-1")])
        ingest(self.board, client)

        raw = RawOpportunity.objects.get()
        self.assertIn("branch environment", raw.raw_content)
        detail = raw.raw_payload["detail"]["jobPostingInfo"]
        self.assertIn("branch environment", detail["jobDescription"])

    def test_single_posting_carries_verified_evidence(self):
        client = FakeCxsClient([listing_item("/job/Johannesburg/Consultant_JR-1")])
        result = ingest(self.board, client)

        self.assertEqual(result["opportunities_created"], 1)
        raw = RawOpportunity.objects.get()
        self.assertEqual(raw.parser_version, "workday-cxs-v1")
        opportunity = Opportunity.objects.get()
        stored = opportunity.source_specific_evidence
        self.assertEqual(stored["tenant"], TEST_TENANT)
        self.assertEqual(stored["site"], TEST_SITE)
        self.assertEqual(stored["country_alpha2"], "ZA")
        self.assertEqual(stored["job_req_id"], "REQ-Consultant_JR-1")
        self.assertEqual(opportunity.reference_number, "REQ-Consultant_JR-1")
        self.assertEqual(opportunity.country, "South Africa")
        self.assertEqual(
            opportunity.evidence_provenance["geography"]["status"],
            "confirmed_south_africa",
        )



class WorkdayPaginationTests(TestCase):
    def setUp(self):
        self.board = make_board()

    def test_pagination_walks_every_page(self):
        items = [listing_item(f"/job/Johannesburg/Consultant_JR-{i}") for i in range(45)]
        client = FakeCxsClient(items, page_size=20)
        result = ingest(self.board, client)

        offsets = [c[2]["offset"] for c in client.catalogue_calls()]
        self.assertEqual(offsets, [0, 20, 40])
        self.assertEqual(result["records_found"], 45)
        self.assertEqual(Opportunity.objects.count(), 45)

    def test_unreliable_total_never_truncates_the_walk(self):
        # Verified live: `total` is reported as 0 on some pages. Pagination must
        # not stop on that value.
        items = [listing_item(f"/job/Johannesburg/Consultant_JR-{i}") for i in range(25)]
        client = FakeCxsClient(items, page_size=20)
        client.page_overrides = {
            20: {"total": 0, "jobPostings": items[20:25], "facets": []},
        }
        result = ingest(self.board, client)

        self.assertEqual(result["records_found"], 25)

    def test_wrapped_offset_terminates_instead_of_looping(self):
        # Verified live: an offset past the end replays the first page.
        items = [listing_item(f"/job/Johannesburg/Consultant_JR-{i}") for i in range(20)]
        client = FakeCxsClient(items, page_size=20)
        client.page_overrides = {
            20: {"total": 57, "jobPostings": items, "facets": []},
        }
        result = ingest(self.board, client)

        self.assertEqual(result["records_found"], 20)
        self.assertEqual(result["duplicate_listings"], 20)
        self.assertEqual(result["pages_traversed"], 2)

    def test_empty_page_terminates(self):
        client = FakeCxsClient([], page_size=20)
        result = ingest(self.board, client)

        self.assertEqual(result["records_found"], 0)
        self.assertEqual(result["pages_traversed"], 1)

    def test_max_jobs_bounds_the_run(self):
        items = [listing_item(f"/job/Johannesburg/Consultant_JR-{i}") for i in range(45)]
        bounded = run_adapter(
            WorkdayAdapter(
                self.board, max_jobs=5, client=FakeCxsClient(items, page_size=20)
            ),
            self.board.source,
            dry_run=True,
        )
        self.assertEqual(bounded["records_found"], 5)

    def test_duplicate_posting_is_one_source_opportunity(self):
        duplicate = listing_item("/job/Johannesburg/Consultant_JR-1")
        client = FakeCxsClient([duplicate, duplicate], page_size=20)
        result = ingest(self.board, client)

        self.assertEqual(result["records_found"], 1)
        self.assertEqual(OpportunitySourceRecord.objects.count(), 1)
        self.assertEqual(result["duplicate_listings"], 1)


class WorkdayScopeTests(TestCase):
    def setUp(self):
        self.board = make_board()

    def test_posting_outside_south_africa_is_gated_out_of_scope(self):
        path = "/job/Nairobi/Senior-Analyst_JR-77"
        item = listing_item(path, locations_text="Nairobi")
        client = client_for(self.board, [item], details={
            path: detail_for(item, country_code="KE", country_name="Kenya",
                             location="Nairobi"),
        })
        result = ingest(self.board, client)

        self.assertEqual(result["non_south_africa_postings"], 1)
        self.assertEqual(result["out_of_scope_count"], 1)
        # An explicitly out-of-scope posting is never published as an
        # Opportunity; it is retained as raw evidence only.
        self.assertEqual(Opportunity.objects.count(), 0)
        raw = RawOpportunity.objects.get()
        self.assertEqual(raw.scope_status, "out_of_scope")
        self.assertEqual(raw.normalization_status, "out_of_scope")
        # The exclusion stays source-grounded, with its provenance intact.
        geography = raw.raw_payload["detail"]["jobPostingInfo"][
            "jobRequisitionLocation"
        ]["country"]
        self.assertEqual(geography["alpha2Code"], "KE")
        self.assertEqual(geography["descriptor"], "Kenya")

    def test_missing_country_falls_back_to_location_evidence(self):
        # No country object at all: the shared helper falls back to the
        # location string, so the outcome stays source-grounded.
        path = "/job/Remote-Contractor_JR-9"
        item = listing_item(path, locations_text="")
        detail = detail_for(item, location="")
        detail["jobPostingInfo"]["jobRequisitionLocation"] = {"descriptor": ""}
        client = client_for(self.board, [item], details={path: detail})
        result = ingest(self.board, client)

        self.assertEqual(result["non_south_africa_postings"], 0)
        opportunity = Opportunity.objects.get()
        # Absent country evidence is never invented in either direction.
        self.assertEqual(
            opportunity.evidence_provenance["geography"]["status"], "unknown"
        )
        self.assertNotEqual(opportunity.scope_status, "out_of_scope")
        self.assertEqual(opportunity.country, "")

    def test_ambiguous_country_scope_is_a_contract_error(self):
        board = make_board(site="Ambiguous_Careers", country_codes=["ZA", "ZW"])
        client = FakeCxsClient([listing_item("/job/A/Consultant_JR-1")], site="Ambiguous_Careers")
        result = ingest(board, client)

        self.assertEqual(result["records_found"], 0)
        self.assertTrue(result["errors"])
        self.assertIn("exactly one country scope", result["errors"][0]["error"])
        # A misconfigured board is never marked successful.
        self.assertIsNone(board.source.last_success_at)



class WorkdayContractFailureTests(TestCase):
    def setUp(self):
        self.board = make_board()

    def test_catalogue_without_job_postings_is_a_contract_error(self):
        client = FakeCxsClient([], page_size=20, page_overrides={0: {"total": 5}})
        result = ingest(self.board, client)

        self.assertEqual(result["records_found"], 0)
        self.assertTrue(result["errors"])
        self.assertIn("'jobPostings' to be a list", result["errors"][0]["error"])

    def test_posting_without_external_path_is_never_identified_by_title(self):
        client = FakeCxsClient([
            {"title": "Client Service Consultant", "locationsText": "Johannesburg"},
        ])
        result = ingest(self.board, client)

        self.assertEqual(result["records_found"], 0)
        self.assertIn("externalPath", result["errors"][0]["error"])
        self.assertEqual(Opportunity.objects.count(), 0)

    def test_one_failing_detail_does_not_abort_the_board(self):
        good = "/job/Johannesburg/Consultant_JR-1"
        bad = "/job/Johannesburg/Consultant_JR-2"
        client = FakeCxsClient(
            [listing_item(good), listing_item(bad)],
            detail_errors={bad: requests.HTTPError("500 for posting")},
        )
        result = ingest(self.board, client)

        self.assertEqual(result["detail_failures"], 1)
        self.assertEqual(result["opportunities_created"], 1)
        self.assertEqual(Opportunity.objects.count(), 1)

    def test_detail_without_job_posting_info_is_recorded_not_invented(self):
        path = "/job/Johannesburg/Consultant_JR-3"
        client = FakeCxsClient(
            [listing_item(path)],
            details={path: {"hiringOrganization": {"name": "Absa"}}},
        )
        result = ingest(self.board, client)

        self.assertEqual(result["detail_failures"], 1)
        self.assertEqual(Opportunity.objects.count(), 0)
        self.assertIn("jobPostingInfo", result["errors"][0]["error"])


class WorkdayRawVersionTests(TestCase):
    def setUp(self):
        self.board = make_board()

    def test_wall_clock_fields_do_not_create_new_raw_versions(self):
        path = "/job/Johannesburg/Consultant_JR-1"
        item = listing_item(path)
        ingest(self.board, FakeCxsClient([item], page_size=20))
        self.assertEqual(RawOpportunity.objects.count(), 1)
        first_hash = RawOpportunity.objects.get().content_hash

        # Same vacancy, different clock-derived values only.
        later = copy.deepcopy(detail_for(item))
        later["jobPostingInfo"]["timeLeftToApply"] = "10 Days Remaining"
        later["jobPostingInfo"]["postedOn"] = "Posted Today"
        later["userAuthenticated"] = True
        ingest(self.board, FakeCxsClient([item], page_size=20, details={path: later}))

        self.assertEqual(RawOpportunity.objects.count(), 1)
        self.assertEqual(RawOpportunity.objects.get().content_hash, first_hash)

    def test_real_content_change_creates_a_new_raw_version(self):
        path = "/job/Johannesburg/Consultant_JR-1"
        item = listing_item(path)
        ingest(self.board, FakeCxsClient([item], page_size=20))
        self.assertEqual(RawOpportunity.objects.count(), 1)

        changed = copy.deepcopy(detail_for(item, description="<p>Rewritten advert.</p>"))
        ingest(self.board, FakeCxsClient([item], page_size=20, details={path: changed}))
        self.assertEqual(RawOpportunity.objects.count(), 2)

    def test_raw_evidence_is_retained_verbatim(self):
        path = "/job/Johannesburg/Consultant_JR-1"
        item = listing_item(path)
        ingest(self.board, FakeCxsClient([item], page_size=20))

        raw = RawOpportunity.objects.get()
        self.assertEqual(raw.raw_payload["platform"], "workday")
        self.assertEqual(raw.raw_payload["tenant"], TEST_TENANT)
        self.assertEqual(raw.raw_payload["site"], TEST_SITE)
        self.assertEqual(raw.raw_payload["external_path"], path)
        # The vendor payload is preserved untrimmed, ephemeral fields included.
        self.assertEqual(
            raw.raw_payload["detail"]["jobPostingInfo"]["timeLeftToApply"],
            "11 Days Remaining",
        )
        self.assertIn("userAuthenticated", raw.raw_payload["detail"])

    def test_identity_is_tenant_site_and_external_path(self):
        path = "/job/Johannesburg/Consultant_JR-1"
        ingest(self.board, FakeCxsClient([listing_item(path)], page_size=20))

        record = OpportunitySourceRecord.objects.get()
        self.assertEqual(
            record.source_external_id, f"{TEST_TENANT}:{TEST_SITE}:{path}"
        )



class WorkdayLifecycleBoundaryTests(TestCase):
    def setUp(self):
        self.board = make_board()

    def test_ingestion_does_not_make_anything_ready(self):
        path = "/job/Johannesburg/Graduate-Programme_JR-1"
        item = listing_item(path, title="Graduate Programme 2027")
        ingest(self.board, FakeCxsClient([item], page_size=20))

        opportunity = Opportunity.objects.get()
        # Acquisition only: nothing reaches the ready/matching path.
        self.assertNotEqual(opportunity.lifecycle_status, "ready")
        self.assertEqual(
            OpportunityExtraction.objects.filter(
                opportunity=opportunity, status="ready"
            ).count(),
            0,
        )

    def test_first_miss_respects_the_missing_grace_window(self):
        path = "/job/Johannesburg/Consultant_JR-1"
        ingest(self.board, FakeCxsClient([listing_item(path)], page_size=20))
        record = OpportunitySourceRecord.objects.get()
        self.assertTrue(record.active_at_source)
        self.assertIsNone(record.missing_since)

        ingest(self.board, FakeCxsClient([], page_size=20))
        record.refresh_from_db()
        opportunity = Opportunity.objects.get()
        # The first miss records absence but does not retire the record.
        self.assertIsNotNone(record.missing_since)
        self.assertTrue(record.active_at_source)
        self.assertTrue(opportunity.is_active)
        self.assertEqual(opportunity.lifecycle_status, "active")

    def test_record_is_retired_only_after_the_grace_window(self):
        grace_days = settings.INGESTION_MISSING_GRACE_DAYS
        path = "/job/Johannesburg/Consultant_JR-1"
        ingest(self.board, FakeCxsClient([listing_item(path)], page_size=20))

        OpportunitySourceRecord.objects.update(
            missing_since=timezone.now() - timedelta(days=grace_days + 1)
        )
        ingest(self.board, FakeCxsClient([], page_size=20))

        record = OpportunitySourceRecord.objects.get()
        opportunity = Opportunity.objects.get()
        self.assertFalse(record.active_at_source)
        self.assertFalse(opportunity.is_active)
        self.assertEqual(opportunity.lifecycle_status, "removed")


class WorkdayCommandTests(TestCase):
    @patch("ingestion.management.commands.ingest_workday.run_adapter")
    def test_ingest_command_targets_only_the_selected_site(self, mocked_run):
        mocked_run.return_value = counters()
        board = make_board(tenant="testfrb", site="FRB_Test", company_name="FirstRand")
        output = StringIO()
        call_command("ingest_workday", "--site", "FRB_Test", "--dry-run", stdout=output)

        self.assertEqual(mocked_run.call_count, 1)
        adapter, source = mocked_run.call_args.args
        self.assertIsInstance(adapter, WorkdayAdapter)
        self.assertEqual(adapter.board.site, "FRB_Test")
        self.assertEqual(source, board.source)
        self.assertIn("workday-frb_test", output.getvalue())

    @patch("ingestion.management.commands.ingest_workday.run_adapter")
    def test_tenant_filter_covers_every_site_of_that_tenant(self, mocked_run):
        mocked_run.return_value = counters()
        call_command(
            "ingest_workday", "--tenant", "oldmutual", "--dry-run", stdout=StringIO()
        )

        sites = sorted(call.args[0].board.site for call in mocked_run.call_args_list)
        self.assertEqual(sites, ["Old_Mutual_Careers", "Old_Mutual_Early_Careers"])

    @patch("ingestion.management.commands.ingest_workday.run_adapter")
    def test_command_without_filter_targets_all_seeded_boards(self, mocked_run):
        mocked_run.return_value = counters()
        call_command("ingest_workday", "--dry-run", stdout=StringIO())

        sites = sorted(call.args[0].board.site for call in mocked_run.call_args_list)
        self.assertEqual(sites, [
            "ABSAcareersite", "FRB", "Old_Mutual_Careers",
            "Old_Mutual_Early_Careers", "PNP_Careers",
        ])

    def test_unknown_site_filter_is_a_command_error(self):
        with self.assertRaises(CommandError):
            call_command("ingest_workday", "--site", "NoSuchSite", stdout=StringIO())

    def test_explain_requires_dry_run(self):
        with self.assertRaises(CommandError):
            call_command("ingest_workday", "--explain", stdout=StringIO())

    def test_successful_run_is_recorded_on_the_ingestion_run(self):
        board = make_board()
        item = listing_item("/job/Cape-Town/Manager_JR-1")
        result = run_adapter(
            WorkdayAdapter(board, client=client_for(board, [item], page_size=20)),
            board.source,
        )

        self.assertEqual(result["opportunities_created"], 1)
        run = IngestionRun.objects.get()
        self.assertEqual(run.status, "success")
        self.assertEqual(run.metadata["adapter"], "WorkdayAdapter")

