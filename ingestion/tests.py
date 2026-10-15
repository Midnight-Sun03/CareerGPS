from datetime import timedelta
from decimal import Decimal
from io import StringIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from unittest import skip

from django.test import TestCase, override_settings
from django.utils import timezone
from django.contrib.auth.models import User
from django.core import mail
from django.urls import reverse

from applications.models import Application
from core.models import Notification, NotificationPreference, Opportunity, Profile, SavedOpportunity
from ingestion.adapters.companies.base import heading_sections, labelled_text, minimum_nqf, minimum_years, property_value, soup_for
from ingestion.adapters.companies.capitec import (
    CapitecScraper, capitec_experience_constraints, primary_capitec_experience,
    split_experience_requirements,
)
from ingestion.adapters.companies.discovery import DiscoveryScraper
from ingestion.adapters.companies.nedbank import NedbankScraper
from ingestion.adapters.companies.sasol import SasolScraper
from ingestion.adapters.companies.woolworths import WoolworthsScraper
from ingestion.adapters.greenhouse import GreenhouseAdapter
from ingestion.models import IngestionSource, OpportunitySourceRecord, RawOpportunity
from ingestion.pipeline.deduplication import find_duplicate
from ingestion.pipeline.fingerprinting import stable_content_hash
from ingestion.pipeline.lifecycle import mark_missing_source_records, refresh_opportunity_lifecycle
from ingestion.pipeline.normalization import (
    classification_policy, classify_opportunity, experience_bounds,
    geographic_scope_evidence, normalize_record, parse_decimal, parse_positive_integer,
)
from ingestion.pipeline.records import FetchedOpportunity
from ingestion.pipeline.runner import run_adapter
from ingestion.management.commands.ingest_all import Command as IngestAllCommand


class StaticAdapter:
    def __init__(self, records):
        self.records = records

    def fetch(self):
        yield from self.records


class NormalizationTests(TestCase):
    def setUp(self):
        self.source = IngestionSource.objects.create(
            name="Employer", code="employer-test", source_type="scraper", company="Employer"
        )

    def test_rule_classification_is_bounded_to_supported_types(self):
        self.assertEqual(classify_opportunity("Software Engineering Internship"), "internship")
        self.assertEqual(classify_opportunity("Senior Engineering Manager"), "unclassified")

    def test_incidental_junior_word_does_not_override_required_experience(self):
        fields = {
            "experience_text": "At least 3 years as a Business Analyst.",
            "experience_min_years": 3,
        }
        self.assertEqual(
            classify_opportunity(
                "Business Analyst",
                "The role mentors junior business analysts.",
                fields=fields,
            ),
            "unclassified",
        )

    def test_unknown_role_is_unclassified_and_not_out_of_scope(self):
        self.assertEqual(classify_opportunity("Quantitative Analyst"), "unclassified")

    def test_canonical_listing_kind_survives_normalization(self):
        for listing_kind in ("vacancy", "talent_pool", "pipeline"):
            with self.subTest(listing_kind=listing_kind):
                record = FetchedOpportunity(
                    external_id=listing_kind,
                    source_url=f"https://example.test/{listing_kind}",
                    fields={
                        "title": "Test role", "company": "Employer",
                        "listing_kind": listing_kind,
                    },
                )
                self.assertEqual(
                    normalize_record(record, self.source)["listing_kind"], listing_kind
                )

    def test_low_required_experience_is_positive_entry_level_evidence(self):
        fields = {
            "experience_text": "1-2 years in customer service.",
            "experience_min_years": 1,
            "experience_max_years": 2,
        }
        self.assertEqual(classify_opportunity("Service Consultant", fields=fields), "entry_level")

    def test_title_words_and_numeric_experience_alone_never_exclude(self):
        for title in (
            "Store Specialist: Beauty", "Executive Assistant", "Head Bartender",
            "Network Lead Graduate", "Assistant Catering Manager", "Relationship Manager",
            "Training Specialist",
        ):
            with self.subTest(title=title):
                result = classification_policy({"title": title, "experience_min_years": 5})
                self.assertNotEqual(result["scope_status"], "out_of_scope")

    def test_conflicting_programme_and_source_stage_is_retained_unclassified(self):
        result = classification_policy({
            "title": "Graduate Programme",
            "source_career_stage_signal": {
                "value": "experienced", "source_field": "verified source category",
                "confidence": "high",
            },
        })
        self.assertEqual(result["opportunity_kind"], "graduate_programme")
        self.assertEqual(result["career_stage"], "unknown")
        self.assertEqual(result["scope_status"], "unclassified")

    def test_unknown_location_and_dates_are_not_invented(self):
        record = FetchedOpportunity(
            external_id="1", source_url="https://example.test/1",
            fields={"title": "Graduate Programme", "company": "Employer"},
        )
        normalized = normalize_record(record, self.source)
        self.assertEqual(normalized["location"], "")
        self.assertEqual(normalized["country"], "")
        self.assertEqual(normalized["geographic_scope"], "unknown")
        self.assertIsNone(normalized["closing_date"])

    def test_geography_requires_positive_country_evidence(self):
        self.assertEqual(
            geographic_scope_evidence(location="Houston, United States")["status"],
            "confirmed_outside_south_africa",
        )
        self.assertEqual(
            geographic_scope_evidence(location="Hamburg, Germany")["status"],
            "confirmed_outside_south_africa",
        )
        self.assertEqual(
            geographic_scope_evidence(location="Unrecognised Locality")["status"],
            "unknown",
        )

    def test_unknown_location_is_retained_even_with_legacy_false_publishable(self):
        record = FetchedOpportunity(
            external_id="geo-unknown", source_url="https://example.test/geo-unknown",
            fields={
                "title": "Service Coordinator", "company": "Employer",
                "location_text": "Unrecognised Locality", "publishable": False,
            },
        )
        normalized = normalize_record(record, self.source)
        self.assertEqual(normalized["geographic_scope"], "unknown")
        self.assertEqual(normalized["scope_status"], "unclassified")
        self.assertEqual(normalized["country"], "")

    def test_explicit_south_african_country_cannot_contradict_scope(self):
        record = FetchedOpportunity(
            external_id="geo-za", source_url="https://example.test/geo-za",
            fields={
                "title": "Service Coordinator", "company": "Employer",
                "country": "South Africa",
                "geographic_scope": "confirmed_outside_south_africa",
                "geographic_scope_provenance": {
                    "source_field": "unsafe inference", "raw": "not recognised",
                },
            },
        )
        normalized = normalize_record(record, self.source)
        self.assertEqual(normalized["country"], "South Africa")
        self.assertEqual(normalized["geographic_scope"], "confirmed_south_africa")
        self.assertNotEqual(normalized["scope_status"], "out_of_scope")


class NumericParsingTests(TestCase):
    def test_experience_bounds_supports_plus_word_and_required_before_ideal(self):
        self.assertEqual(experience_bounds("Min: 3 plus years' experience"), (3, None))
        self.assertEqual(
            experience_bounds("Minimum: 4-6 years experience. Ideal: SQL experience."),
            (4, 6),
        )
        self.assertEqual(
            experience_bounds("1-2 years client service. 2-3 years customer service."),
            (1, 3),
        )
        self.assertEqual(experience_bounds("3-5 years in role and 8+ yrs overall"), (3, None))
        self.assertEqual(experience_bounds("At least 1-2-years sales experience"), (1, 2))
        self.assertEqual(experience_bounds("Minimum 3-years experience"), (3, None))
        self.assertEqual(
            experience_bounds("Administration qualification advantageous Experience 1 year administrative experience"),
            (1, None),
        )

    def test_parse_decimal_accepts_supported_numeric_values(self):
        cases = (
            (12, Decimal("12")),
            (Decimal("12.75"), Decimal("12.75")),
            ("-26.2041", Decimal("-26.2041")),
            (" 1,234,567.89 ", Decimal("1234567.89")),
        )
        for value, expected in cases:
            with self.subTest(value=value):
                self.assertEqual(parse_decimal(value), expected)

    def test_parse_decimal_safely_rejects_blank_invalid_and_non_finite_values(self):
        for value in (None, "", "   ", "not-a-number", "NaN", "Infinity", object()):
            with self.subTest(value=value):
                self.assertIsNone(parse_decimal(value))

    def test_positive_integer_rejects_negatives(self):
        self.assertEqual(parse_positive_integer("NQF 6"), 6)
        self.assertEqual(parse_positive_integer(0), 0)
        self.assertIsNone(parse_positive_integer("-1"))
        self.assertIsNone(parse_positive_integer("invalid"))

    def test_corrected_decimal_values_survive_normalization(self):
        source = IngestionSource.objects.create(name="API", code="numeric-api", source_type="api")
        record = FetchedOpportunity(
            external_id="numeric-1",
            source_url="https://example.test/numeric-1",
            fields={
                "title": "Data Internship",
                "company": "Example",
                "latitude": "-26.204100",
                "longitude": "28.047300",
                "salary_min": "12,500.50",
                "salary_max": 15000,
            },
        )
        normalized = normalize_record(record, source)
        self.assertEqual(normalized["latitude"], Decimal("-26.204100"))
        self.assertEqual(normalized["longitude"], Decimal("28.047300"))
        self.assertEqual(normalized["salary_min"], Decimal("12500.50"))
        self.assertEqual(normalized["salary_max"], Decimal("15000"))


class CompanyParsingHelperTests(TestCase):
    def test_minimum_nqf_supports_single_and_double_digit_levels(self):
        self.assertEqual(minimum_nqf("NQF Level 6"), 6)
        self.assertEqual(minimum_nqf("NQF 10"), 10)

    def test_heading_sections_supports_current_nested_successfactors_layout(self):
        soup = soup_for("""
            <div>
              <div><h2>Job Family</h2></div>
              <div>Client Services</div>
            </div>
        """)
        self.assertEqual(heading_sections(soup)["job family"], "Client Services")

    def test_minimum_years_common_forms(self):
        cases = (
            ("0 years", 0),
            ("0+ years", 0),
            ("1 year", 1),
            ("1+ year", 1),
            ("0-1 years", 0),
            ("0–2 years", 0),
            ("1-2 years", 1),
            ("3–5 years", 3),
            ("3+ years", 3),
            ("at least 3-years' experience", 3),
            ("2+ relevant years", 2),
            ("8+yrs", 8),
            ("less than 2 years", 0),
            ("no experience required", 0),
        )
        for value, expected in cases:
            with self.subTest(value=value):
                self.assertEqual(minimum_years(value), expected)

    def test_minimum_years_ignores_preferred_only_experience(self):
        self.assertIsNone(minimum_years("Preferred: 5+ years of banking experience"))
        self.assertEqual(
            minimum_years("Preferred: 5+ years. Essential: 1-2 years in client service."),
            1,
        )
        self.assertEqual(
            minimum_years("Minimum: 3 years. Ideal: 8+ years in a similar role."),
            3,
        )

    def test_property_and_label_helpers_reject_loading_placeholders(self):
        soup = soup_for("""
            <span data-careersite-propertyid="customfield1">Loading...</span>
            <span data-careersite-propertyid="customfield1">REQ-42</span>
            <div>Req ID</div><div>Loading...</div>
            <div>Req ID</div><div>REQ-99</div>
        """)
        self.assertEqual(property_value(soup, "customfield1"), "REQ-42")
        self.assertEqual(labelled_text(soup, "Req ID"), "REQ-99")


class DiscoveryAdapterTests(TestCase):
    def test_degree_duration_is_not_work_experience_and_function_is_not_stage(self):
        html = """
            <span data-careersite-propertyid="title">Systems Analyst</span>
            <meta itemprop="addressCountry" content="ZA">
            <span data-careersite-propertyid="department">Executive Operations</span>
            <span class="jobdescription">
              <p><b>Qualification and Experience</b></p>
              <p>3-year IT qualification. Strong communication skills.</p>
            </span>
        """
        record = DiscoveryScraper().parse_detail("https://careers.discovery.co.za/job/test/901/", html)
        self.assertEqual(record.fields["qualification_text"], "3-year IT qualification.")
        self.assertEqual(record.fields["experience_text"], "")
        self.assertIsNone(record.fields["experience_min_years"])
        self.assertNotIn("source_native_career_level", record.fields)
        source = IngestionSource.objects.get(code="discovery")
        normalized = normalize_record(record, source)
        self.assertEqual(normalized["career_stage"], "unknown")
        self.assertEqual(normalized["scope_status"], "unclassified")

    def test_qualification_and_experience_heading_is_source_native_experience(self):
        html = """
            <span data-careersite-propertyid="title">Telesales Consultant</span>
            <meta itemprop="addressCountry" content="ZA">
            <span class="jobdescription">
              <p><b>Key Purpose</b></p><p>Support customers.</p>
              <p><b>Qualification &amp; Experience</b></p>
              <p>Matric and at least 2 years sales experience.</p>
              <p><b>Personal Attributes</b></p><p>Resilience.</p>
            </span>
        """
        record = DiscoveryScraper().parse_detail(
            "https://careers.discovery.co.za/job/Sandton-Telesales-Consultant/999/",
            html,
        )
        self.assertEqual(record.fields["qualification_text"], "Matric")
        self.assertIn("2 years", record.fields["experience_text"])
        self.assertNotIn("Matric", record.fields["experience_text"])
        self.assertEqual(record.fields["experience_min_years"], 2)
        self.assertEqual(record.payload["structured"]["personal_attributes"], "Resilience.")

    def test_combined_requirements_keep_essential_and_advantageous_facts_separate(self):
        html = """
            <span data-careersite-propertyid="title">Business Analyst</span>
            <meta itemprop="addressCountry" content="ZA">
            <span class="jobdescription">
              <p>Education and Experience</p>
              <p>Essential:</p>
              <ul>
                <li>Matric</li>
                <li>IT Degree or Diploma in Business Analysis</li>
                <li>At least 3 – 4 years of working experience as a Business Analyst</li>
              </ul>
              <p>Advantageous</p>
              <ul>
                <li>Recognised Diploma/NQF 6 qualification in Business Analysis</li>
                <li>Minimum 2 years Business Analysis experience in insurance</li>
              </ul>
              <p>Technical Skills or Knowledge:</p><p>SQL and UML</p>
            </span>
        """
        record = DiscoveryScraper().parse_detail(
            "https://careers.discovery.co.za/job/Sandton-Business-Analyst/1430511533/",
            html,
        )

        self.assertIn("IT Degree or Diploma", record.fields["qualification_text"])
        self.assertNotIn("3 – 4 years", record.fields["qualification_text"])
        self.assertIn("NQF 6", record.fields["preferred_qualification_text"])
        self.assertNotIn("2 years", record.fields["preferred_qualification_text"])
        self.assertEqual(
            record.fields["experience_text"],
            "At least 3 – 4 years of working experience as a Business Analyst",
        )
        self.assertEqual(record.fields["experience_min_years"], 3)
        self.assertEqual(record.fields["experience_max_years"], 4)
        self.assertEqual(
            record.fields["preferred_experience_text"],
            "Minimum 2 years Business Analysis experience in insurance",
        )
        self.assertEqual(record.fields["preferred_experience_min_years"], 2)
        self.assertNotIn("Technical Skills", record.fields["experience_text"])
        self.assertIn(
            "Technical Skills or Knowledge",
            record.payload["structured"]["education_and_experience"],
        )

    def test_exact_twelve_months_is_one_year_but_other_months_are_not_rounded(self):
        html = """
            <span data-careersite-propertyid="title">Contact Centre Consultant</span>
            <meta itemprop="addressCountry" content="ZA">
            <span class="jobdescription">
              <p>Education:</p><ul><li>Matric</li></ul>
              <p>Experience</p>
              <ul><li>At least 12 months working experience in customer service</li></ul>
              <p>Advantageous</p><p>Business writing skills</p>
              <p>Employment Equity</p><p>Recruitment boilerplate.</p>
            </span>
        """
        record = DiscoveryScraper().parse_detail(
            "https://careers.discovery.co.za/job/test/12-months/",
            html,
        )
        self.assertEqual(
            record.fields["experience_text"],
            "At least 12 months working experience in customer service",
        )
        self.assertEqual(record.fields["experience_min_years"], 1)
        self.assertIsNone(record.fields["experience_max_years"])

        eighteen_month_html = html.replace("12 months", "18 months")
        record = DiscoveryScraper().parse_detail(
            "https://careers.discovery.co.za/job/test/18-months/",
            eighteen_month_html,
        )
        self.assertIn("18 months", record.fields["experience_text"])
        self.assertIsNone(record.fields["experience_min_years"])
        self.assertIsNone(record.fields["experience_max_years"])

    def test_combined_table_labels_and_later_clauses_remain_structured(self):
        html = """
            <span data-careersite-propertyid="title">Clinical Coordinator</span>
            <meta itemprop="addressCountry" content="ZA">
            <span class="jobdescription">
              <p>Education and Experience:</p>
              <table><tr>
                <td>Education &amp; Experience</td><td>Education</td>
                <td>Grade 12</td><td>Emergency Care qualification advantageous</td>
                <td>Experience</td><td>1 year administrative</td>
                <td>2 years call centre experience advantageous</td>
              </tr></table>
            </span>
        """
        record = DiscoveryScraper().parse_detail(
            "https://careers.discovery.co.za/job/test/table-requirements/",
            html,
        )

        self.assertEqual(record.fields["qualification_text"], "Grade 12")
        self.assertEqual(
            record.fields["preferred_qualification_text"],
            "Emergency Care qualification advantageous",
        )
        self.assertIn("1 year administrative", record.fields["experience_text"])
        self.assertEqual(record.fields["experience_min_years"], 1)
        self.assertIn("2 years", record.fields["preferred_experience_text"])

    def test_unrelated_knowledge_clause_does_not_hide_later_experience(self):
        html = """
            <span data-careersite-propertyid="title">Client Support Consultant</span>
            <meta itemprop="addressCountry" content="ZA">
            <span class="jobdescription">
              <p>Education and Experience</p>
              <ul>
                <li>NQF level 6 qualification</li>
                <li>Knowledge of employee benefits</li>
                <li>1 year customer service experience</li>
              </ul>
            </span>
        """
        record = DiscoveryScraper().parse_detail(
            "https://careers.discovery.co.za/job/test/later-experience/",
            html,
        )
        self.assertEqual(record.fields["qualification_text"], "NQF level 6 qualification")
        self.assertEqual(record.fields["experience_text"], "1 year customer service experience")
        self.assertEqual(record.fields["experience_min_years"], 1)

    def test_degree_or_equivalent_experience_keeps_only_confident_degree_clause(self):
        html = """
            <span data-careersite-propertyid="title">SAP Consultant</span>
            <meta itemprop="addressCountry" content="ZA">
            <span class="jobdescription">
              <p>Education and Experience</p>
              <p>Bachelor's degree in Information Systems or equivalent technical work experience.</p>
              <p>6 years of experience in SAP administration.</p>
            </span>
        """
        record = DiscoveryScraper().parse_detail(
            "https://careers.discovery.co.za/job/test/degree-alternative/",
            html,
        )
        self.assertEqual(
            record.fields["qualification_text"],
            "Bachelor's degree in Information Systems",
        )
        self.assertEqual(record.fields["experience_text"], "6 years of experience in SAP administration.")
        self.assertNotIn("degree", record.fields["experience_text"].lower())

    def test_current_styled_paragraph_markup_is_parsed_without_page_chrome(self):
        fixture = Path(__file__).with_name("test_fixtures") / "discovery_current.html"
        html = fixture.read_text(encoding="utf-8")
        record = DiscoveryScraper().parse_detail(
            "https://careers.discovery.co.za/job/Sandton-Junior-Claims-Consultant/12345/",
            html,
        )

        self.assertEqual(record.fields["title"], "Junior Claims Consultant")
        self.assertEqual(record.fields["business_unit"], "Discovery Insure")
        self.assertEqual(record.fields["department"], "Client Service")
        self.assertEqual(record.fields["location_text"], "Sandton - 1 Discovery Place, GP")
        self.assertEqual(record.fields["country"], "South Africa")
        self.assertEqual(record.fields["experience_min_years"], 0)
        self.assertIn("first-time claimants", record.fields["description"])
        self.assertIn("Register claims accurately", record.fields["responsibilities"])
        self.assertIn("Ability to work shifts", record.payload["structured"]["specific_requirements"])
        self.assertIn("Basic computer literacy", record.payload["structured"]["technical_capabilities"])
        self.assertNotIn("Accept cookies", record.fields["description"])
        self.assertNotIn("Privacy policy", record.fields["description"])
        self.assertEqual(
            record.apply_url,
            "https://careers.discovery.co.za/job/Sandton-Junior-Claims-Consultant/12345/",
        )
        self.assertEqual(record.fields["application_url_mechanism"], "detail_page_apply_gateway")


class NedbankAdapterTests(TestCase):
    def test_career_stream_is_not_career_level(self):
        html = """
            <meta itemprop="streetAddress" content="Johannesburg, South Africa">
            <span data-careersite-propertyid="title">Relationship Manager</span>
            <span class="jobdescription">
              <h2>Career Stream</h2><p>Leadership</p>
              <h2>Job Purpose</h2><p>Support client relationships.</p>
            </span>
        """
        record = NedbankScraper().parse_detail("https://jobs.nedbank.co.za/job/test/902/", html)
        self.assertEqual(record.fields["sector"], "Leadership")
        self.assertEqual(record.fields["source_native_career_level"], "")
        source = IngestionSource.objects.get(code="nedbank")
        normalized = normalize_record(record, source)
        self.assertEqual(normalized["career_stage"], "unknown")
        self.assertEqual(normalized["scope_status"], "unclassified")

    def test_successfactors_numbered_pagination_is_followed(self):
        first = """
            <a class="jobTitle-link" href="/job/Johannesburg-Role-One/100/">Role One</a>
            <a href="?q=&startrow=25" rel="nofollow" title="Page 2">2</a>
        """
        second = '<a class="jobTitle-link" href="/job/Cape-Town-Role-Two/200/">Role Two</a>'

        class Client:
            def get(self, url):
                return SimpleNamespace(text=second if "startrow=25" in url else first, status_code=200)

        scraper = NedbankScraper(max_pages=10, client=Client())
        pages = list(scraper._listing_pages())
        self.assertEqual(len(pages), 2)
        self.assertEqual(scraper.stats["pages_traversed"], 2)

    def test_current_sections_and_non_placeholder_requisition_are_preserved(self):
        html = """
            <div itemscope itemtype="http://schema.org/JobPosting">
              <meta itemprop="streetAddress" content="Johannesburg, ZA">
              <meta itemprop="datePosted" content="22 Aug 2026">
              <meta itemprop="validThrough" content="30 Sep 2026">
              <span data-careersite-propertyid="title" itemprop="title">Junior Service Consultant</span>
              <span data-careersite-propertyid="customfield1">146605</span>
              <span class="jobdescription">
                <h2>Job Family</h2><p>Client Services</p>
                <h2>Career Stream</h2><p>Operations</p>
                <h2>Leadership Pipeline</h2><p>Manage Self: Technical</p>
                <h2>Job Purpose</h2><p>Support first-time banking clients.</p>
                <h2>Job Responsibilities</h2><p>Resolve client queries.</p>
                <h2>Job Responsibilities Continue</h2><p>Maintain accurate records.</p>
                <h2>Minimum Experience Level</h2><p>0-1 years in client service.</p>
                <h2>Minimum Requirements</h2><p>Must be eligible to work in South Africa.</p>
                <h2>Essential Qualifications - NQF Level</h2><p>Diploma (NQF 6)</p>
                <h2>Preferred Qualification</h2><p>Bachelor's degree</p>
                <h2>People Specification</h2><p>Client focused.</p>
                <h2>Technical / Professional Knowledge</h2><p>Banking products.</p>
                <h2>Behavioural Competencies</h2><p>Collaborating.</p>
              </span>
              <div>Req ID</div><div>Loading...</div>
            </div>
        """
        record = NedbankScraper().parse_detail("https://jobs.nedbank.co.za/job/test/1/", html)

        self.assertEqual(record.fields["reference_number"], "146605")
        self.assertEqual(record.fields["department"], "Client Services")
        self.assertEqual(record.fields["sector"], "Operations")
        self.assertEqual(record.fields["experience_min_years"], 0)
        self.assertEqual(record.fields["minimum_nqf"], 6)
        self.assertIn("eligible to work", record.fields["qualification_text"])
        self.assertIn("Maintain accurate records", record.fields["responsibilities"])
        self.assertEqual(record.payload["structured"]["leadership_pipeline"], "Manage Self: Technical")
        self.assertEqual(record.payload["structured"]["preferred_qualifications"], "Bachelor's degree")
        self.assertEqual(record.payload["structured"]["behavioural_competencies"], "Collaborating.")


class SasolAdapterTests(TestCase):
    def test_sasolburg_is_not_treated_as_outside_south_africa(self):
        html = """
            <meta itemprop="streetAddress" content="Sasolburg">
            <span data-careersite-propertyid="title">Maintenance Operator</span>
        """
        record = SasolScraper().parse_detail("https://jobs.sasol.com/job/test/905/", html)
        source = IngestionSource.objects.get(code="sasol")
        normalized = normalize_record(record, source)
        self.assertEqual(record.fields["geographic_scope"], "confirmed_south_africa")
        self.assertEqual(normalized["country"], "South Africa")
        self.assertEqual(normalized["scope_status"], "unclassified")

    def test_explicit_foreign_sasol_locations_are_outside_south_africa(self):
        source = IngestionSource.objects.get(code="sasol")
        for location in ("Lake Charles, United States", "Hamburg, Germany"):
            with self.subTest(location=location):
                html = f"""
                    <meta itemprop="streetAddress" content="{location}">
                    <span data-careersite-propertyid="title">Operations Role</span>
                """
                record = SasolScraper().parse_detail("https://jobs.sasol.com/job/test/906/", html)
                normalized = normalize_record(record, source)
                self.assertEqual(
                    record.fields["geographic_scope"], "confirmed_outside_south_africa"
                )
                self.assertEqual(normalized["scope_status"], "out_of_scope")

    def test_ome_is_organisational_not_career_level(self):
        html = """
            <meta itemprop="streetAddress" content="Secunda, South Africa">
            <span data-careersite-propertyid="title">Operations Coordinator</span>
            <div>OME</div><div>Executive Operations</div>
            <span class="jobdescription"><h2>Purpose of Job</h2><p>Coordinate records.</p></span>
        """
        record = SasolScraper().parse_detail("https://jobs.sasol.com/job/test/903/", html)
        self.assertEqual(record.fields["business_unit"], "Executive Operations")
        self.assertNotIn("source_native_career_level", record.fields)
        source = IngestionSource.objects.get(code="sasol")
        normalized = normalize_record(record, source)
        self.assertEqual(normalized["career_stage"], "unknown")

    def test_explicit_experience_embedded_in_formal_education_is_not_lost(self):
        html = """
            <meta itemprop="streetAddress" content="Secunda, South Africa">
            <span data-careersite-propertyid="title">Process Technician Gr I</span>
            <span class="jobdescription">
              <h2>Formal Education</h2>
              <p>National Diploma. Min Experience 3yrs in a petrochemical industry.</p>
            </span>
        """
        record = SasolScraper().parse_detail("https://jobs.sasol.com/job/test/801/", html)
        self.assertEqual(record.fields["experience_min_years"], 3)
        self.assertIn("Min Experience 3yrs", record.fields["experience_text"])
        self.assertEqual(
            record.payload["structured"]["working_experience_source"],
            "formal_education_embedded_label",
        )

    def test_current_skills_heading_and_source_specific_fields_are_preserved(self):
        html = """
            <div itemscope itemtype="http://schema.org/JobPosting">
              <meta itemprop="streetAddress" content="Secunda, South Africa">
              <meta itemprop="datePosted" content="20 Aug 2026">
              <span data-careersite-propertyid="title">Process Trainee</span>
              <span data-careersite-propertyid="customfield1">REQ-800</span>
              <div>OME</div><div>Energy Operations</div>
              <div>Work Arrangement</div><div>On-site</div>
              <span class="jobdescription">
                <h2>Purpose of Job</h2><p>Support plant operations.</p>
                <h2>Key Accountabilities</h2><p>Complete operational checks.</p>
                <h2>Formal Education</h2><p>National Diploma.</p>
                <h2>Working Experience</h2><p>1+ year required.</p>
                <h2>Required Personal and Professional Skills</h2><p>Safety awareness.</p>
                <h2>Licences</h2><p>Code B licence.</p>
              </span>
            </div>
        """
        record = SasolScraper().parse_detail("https://jobs.sasol.com/job/test/800/", html)

        self.assertEqual(record.fields["experience_min_years"], 1)
        self.assertIn("Safety awareness", record.fields["eligibility_text"])
        self.assertIn("Code B licence", record.fields["eligibility_text"])
        self.assertEqual(record.payload["structured"]["ome"], "Energy Operations")
        self.assertEqual(record.payload["structured"]["work_arrangement"], "On-site")
        self.assertEqual(record.payload["structured"]["requisition_id"], "REQ-800")


class CapitecAdapterTests(TestCase):
    def test_capitec_contextual_listing_kinds_are_preserved(self):
        examples = (
            ("Marketing Manager (Talent Pool)", "talent_pool"),
            ("Business Analyst (Talent Pool)", "talent_pool"),
            ("Multi-Media Specialist - Pipeline", "pipeline"),
        )
        source = IngestionSource.objects.get(code="capitec")
        for title, expected in examples:
            with self.subTest(title=title):
                record = CapitecScraper().parse_detail(
                    "https://careers.capitecbank.co.za/job/test/910/",
                    f"<h1>{title}</h1><div>Location</div><div>Stellenbosch, ZA</div>",
                )
                self.assertEqual(record.fields["listing_kind"], expected)
                self.assertEqual(normalize_record(record, source)["listing_kind"], expected)

    def test_capitec_multi_constraint_primary_experience_is_semantic(self):
        cases = (
            (
                "At least 10 years in multi media, of which 5 years client service delivery",
                10, None, ["role_domain", "subset"],
            ),
            (
                "3-5 years in a similar role and 8+yrs overall experience",
                8, None, ["similar_role", "overall"],
            ),
            (
                "10 years credit risk analytics experience and 5 years management experience",
                10, None, ["role_domain", "subordinate_specialism"],
            ),
            (
                "Minimum of 3-4 years experience in facilities and 1 year experience leading a team",
                3, 4, ["role_domain", "subordinate_specialism"],
            ),
        )
        for text, minimum, maximum, contexts in cases:
            with self.subTest(text=text):
                constraints = capitec_experience_constraints(text, "")
                primary, ambiguous = primary_capitec_experience(constraints, "required")
                self.assertFalse(ambiguous)
                self.assertEqual((primary["min"], primary["max"]), (minimum, maximum))
                self.assertEqual([item["context"] for item in constraints], contexts)

    def test_advantageous_numeric_experience_moves_to_preferred(self):
        html = """
            <h1>Systems Analyst I</h1>
            <span class="jobdescription">
              <h2>Experience</h2>
              <p>Minimum: 5-8 years experience in Systems Analysis.
                 At least 1-2 years of telco experience (advantageous).</p>
            </span>
        """
        record = CapitecScraper().parse_detail(
            "https://careers.capitecbank.co.za/job/test/911/", html
        )
        self.assertEqual(
            (record.fields["experience_min_years"], record.fields["experience_max_years"]),
            (5, 8),
        )
        self.assertEqual(
            (
                record.fields["preferred_experience_min_years"],
                record.fields["preferred_experience_max_years"],
            ),
            (1, 2),
        )
        self.assertNotIn("telco", record.fields["experience_text"].lower())
        self.assertIn("telco", record.fields["preferred_experience_text"].lower())

    def test_ambiguous_multi_constraint_scalar_is_left_unset(self):
        html = """
            <h1>Specialist Role</h1>
            <span class="jobdescription">
              <h2>Experience</h2>
              <p>Minimum: 3 years in operations and 4 years in compliance.</p>
            </span>
        """
        record = CapitecScraper().parse_detail(
            "https://careers.capitecbank.co.za/job/test/912/", html
        )
        source = IngestionSource.objects.get(code="capitec")
        normalized = normalize_record(record, source)
        self.assertTrue(record.fields["experience_scalar_intentionally_unset"])
        self.assertIsNone(normalized["experience_min_years"])
        self.assertIsNone(normalized["experience_max_years"])

    def test_lowercase_or_inside_a_role_name_is_not_an_alternative_path(self):
        constraints = capitec_experience_constraints(
            "3-4 years as a strategic people partner or generalist, supporting clients. "
            "At least 4 years experience in an HR specialist discipline.",
            "",
        )
        primary, ambiguous = primary_capitec_experience(constraints, "required")
        self.assertIsNone(primary)
        self.assertTrue(ambiguous)

    def test_word_range_is_preserved_as_minimum_and_maximum(self):
        html = """
            <h1>Client Consultant</h1>
            <span class="jobdescription">
              <h2>Experience - Minimum</h2><p>2 to 4 years in client service.</p>
            </span>
        """
        record = CapitecScraper().parse_detail("https://careers.capitecbank.co.za/job/test/904/", html)
        self.assertEqual(record.fields["experience_min_years"], 2)
        self.assertEqual(record.fields["experience_max_years"], 4)

    def test_experience_split_helper_preserves_required_and_preferred_parts(self):
        required, preferred = split_experience_requirements(
            "Minimum: 1-2 years in client service. Ideal: 3+ years in banking."
        )
        self.assertEqual(required, "1-2 years in client service.")
        self.assertEqual(preferred, "3+ years in banking.")

    def test_qualification_experience_knowledge_and_skills_remain_distinct(self):
        html = """
            <h1>Junior Client Consultant</h1>
            <div>Company</div><div>Capitec Bank Ltd</div>
            <div>Location</div><div>Stellenbosch, ZA</div>
            <span class="jobdescription">
              <h2>Purpose Statement</h2><p>Help clients use digital banking.</p>
              <h2>Experience</h2><p>Minimum: 0-1 years in service. Ideal: 2+ years in banking.</p>
              <h2>Qualifications (Minimum)</h2><p>Grade 12</p>
              <h2>Qualifications (Ideal or Preferred)</h2><p>Banking certificate</p>
              <h2>Knowledge</h2><p>Digital banking products</p>
              <h2>Skills</h2><p>Communication</p>
              <h2>Conditions of Employment</h2><p>Clear criminal record</p>
            </span>
        """
        record = CapitecScraper().parse_detail("https://careers.capitecbank.co.za/job/test/2/", html)
        structured = record.payload["structured"]

        self.assertEqual(record.fields["qualification_text"], "Grade 12")
        self.assertEqual(record.fields["experience_text"], "0-1 years in service.")
        self.assertEqual(record.fields["experience_min_years"], 0)
        self.assertEqual(structured["preferred_qualification"], "Banking certificate")
        self.assertEqual(structured["preferred_experience"], "2+ years in banking.")
        self.assertEqual(structured["knowledge"], "Digital banking products")
        self.assertEqual(structured["skills"], "Communication")
        self.assertEqual(structured["conditions_of_employment"], "Clear criminal record")

    def test_current_editorial_headings_have_safe_fallback_mappings(self):
        html = """
            <h1>Strategic People Partner</h1>
            <span class="jobdescription">
              <h2>The Role</h2><p>Partner with operational leaders.</p>
              <h2>Partner at the Top</h2><p>Advise executives.</p>
              <h2>What You Bring</h2><p>3–4 years as a strategic people partner.</p>
              <h2>Qualifications</h2><p>HR degree (minimum)</p>
              <h2>Knowledge</h2><p>Labour legislation</p>
              <h2>You'll Also Need</h2><p>Clear criminal record</p>
              <h2>What Makes You Right For This</h2><p>Commercial curiosity</p>
            </span>
        """
        record = CapitecScraper().parse_detail("https://careers.capitecbank.co.za/job/test/3/", html)
        structured = record.payload["structured"]
        self.assertEqual(record.fields["description"], "Partner with operational leaders.")
        self.assertEqual(record.fields["experience_min_years"], 3)
        self.assertEqual(record.fields["qualification_text"], "HR degree (minimum)")
        self.assertEqual(record.fields["responsibilities"], "Advise executives.")
        self.assertEqual(structured["conditions_of_employment"], "Clear criminal record")
        self.assertEqual(structured["personal_attributes"], "Commercial curiosity")


class WoolworthsAdapterTests(TestCase):
    def test_mixed_required_and_preferred_experience_are_separated(self):
        html = """
            <table>
              <tr><td>Job Title</td><td>Department Coordinator</td></tr>
              <tr><td>Reference Number</td><td>WWL-1</td></tr>
              <tr><td>Minimum Education Level</td><td>Grade 12</td></tr>
              <tr><td>Minimum Requirements</td><td><ul>
                <li>Minimum 1 year customer-service experience.</li>
                <li>1-2 years retail experience preferred.</li>
                <li>Previous supervisory experience would be beneficial.</li>
              </ul></td></tr>
              <tr><td>Country</td><td>South Africa</td></tr>
            </table>
            <button>Apply Now</button>
        """
        record = WoolworthsScraper().parse_detail(
            "https://woolworths.erecruit.co/candidateapp/Jobs/View/WWL-1", html
        )
        self.assertEqual(record.fields["experience_text"], "Minimum 1 year customer-service experience.")
        self.assertEqual(record.fields["experience_min_years"], 1)
        self.assertIsNone(record.fields["experience_max_years"])
        self.assertIn("1-2 years retail experience preferred", record.fields["preferred_experience_text"])
        self.assertIn("supervisory experience would be beneficial", record.fields["preferred_experience_text"])
        self.assertEqual(record.fields["preferred_experience_min_years"], 1)
        self.assertEqual(record.fields["preferred_experience_max_years"], 2)
        self.assertEqual(
            record.fields["source_specific_evidence"]["minimum_requirements"],
            "Minimum 1 year customer-service experience. 1-2 years retail experience preferred. "
            "Previous supervisory experience would be beneficial.",
        )

    def test_advantageous_subsection_applies_to_following_experience_bullets(self):
        html = """
            <table>
              <tr><td>Job Title</td><td>Cyber Security Specialist</td></tr>
              <tr><td>Reference Number</td><td>WWL-2</td></tr>
              <tr><td>Minimum Requirements</td><td>
                <ul><li>Minimum 3 years relevant experience.</li></ul>
                <p><strong>Advantageous</strong></p>
                <ul><li>Experience in a regulated enterprise environment.</li></ul>
              </td></tr>
              <tr><td>Country</td><td>South Africa</td></tr>
            </table>
            <button>Apply Now</button>
        """
        record = WoolworthsScraper().parse_detail(
            "https://woolworths.erecruit.co/candidateapp/Jobs/View/WWL-2", html
        )
        self.assertEqual(record.fields["experience_text"], "Minimum 3 years relevant experience.")
        self.assertEqual(
            record.fields["preferred_experience_text"],
            "Experience in a regulated enterprise environment.",
        )

    def test_experience_alternative_is_preserved_without_inventing_semantics(self):
        html = """
            <table>
              <tr><td>Job Title</td><td>Beauty Consultant</td></tr>
              <tr><td>Reference Number</td><td>WWL-3</td></tr>
              <tr><td>Minimum Requirements</td><td>
                Qualification and/or experience in Cosmetology. At least 1 year of experience in beauty sales.
              </td></tr>
              <tr><td>Country</td><td>South Africa</td></tr>
            </table>
            <button>Apply Now</button>
        """
        record = WoolworthsScraper().parse_detail(
            "https://woolworths.erecruit.co/candidateapp/Jobs/View/WWL-3", html
        )
        evidence = record.fields["source_specific_evidence"]
        self.assertEqual(record.fields["experience_text"], "At least 1 year of experience in beauty sales.")
        self.assertEqual(record.fields["experience_min_years"], 1)
        self.assertEqual(record.fields["preferred_experience_text"], "")
        self.assertEqual(
            evidence["ambiguous_experience"],
            "Qualification and/or experience in Cosmetology.",
        )

    def test_preferred_specialism_does_not_make_general_experience_optional(self):
        html = """
            <table>
              <tr><td>Job Title</td><td>Store Specialist</td></tr>
              <tr><td>Reference Number</td><td>WWL-4</td></tr>
              <tr><td>Minimum Requirements</td><td>
                Relevant experience in retail or FMCG, preferably in beauty or cosmetics sales.
              </td></tr>
              <tr><td>Country</td><td>South Africa</td></tr>
            </table>
            <button>Apply Now</button>
        """
        record = WoolworthsScraper().parse_detail(
            "https://woolworths.erecruit.co/candidateapp/Jobs/View/WWL-4", html
        )
        self.assertEqual(record.fields["experience_text"], "Relevant experience in retail or FMCG.")
        self.assertEqual(
            record.fields["preferred_experience_text"],
            "Experience in beauty or cosmetics sales preferred.",
        )

    def test_qualification_duration_in_mixed_requirements_is_not_experience(self):
        html = """
            <table>
              <tr><td>Job Title</td><td>Store Specialist: Beauty</td></tr>
              <tr><td>Minimum Education Level</td><td>3-year diploma</td></tr>
              <tr><td>Minimum Requirements</td><td>3-year beauty qualification. Customer focus.</td></tr>
              <tr><td>Country</td><td>South Africa</td></tr>
            </table>
            <button>Apply Now</button>
        """
        record = WoolworthsScraper().parse_detail(
            "https://woolworths.erecruit.co/candidateapp/Jobs/View/905", html
        )
        self.assertEqual(record.fields["qualification_text"], "3-year diploma")
        self.assertEqual(record.fields["experience_text"], "")
        self.assertIsNone(record.fields["experience_min_years"])
        source = IngestionSource.objects.get(code="woolworths")
        normalized = normalize_record(record, source)
        self.assertEqual(normalized["scope_status"], "unclassified")

    def test_onclick_discovery_postback_pagination_detail_and_apply_gateway(self):
        fixture_dir = Path(__file__).with_name("test_fixtures")
        first_page = (fixture_dir / "woolworths_listing_onclick.html").read_text(encoding="utf-8")
        last_page = (fixture_dir / "woolworths_listing_last_page.html").read_text(encoding="utf-8")
        detail = (fixture_dir / "woolworths_detail_current.html").read_text(encoding="utf-8")

        class Client:
            def __init__(self):
                self.post_calls = []

            def get(self, url):
                if "/Jobs/View/" in url:
                    job_id = url.rstrip("/").rsplit("/", 1)[-1]
                    return SimpleNamespace(
                        text=detail.replace("WWL260901-8", job_id),
                        status_code=200,
                    )
                return SimpleNamespace(text=first_page, status_code=200)

            def post(self, url, data=None):
                self.post_calls.append((url, data))
                return SimpleNamespace(text=last_page, status_code=200)

        client = Client()
        scraper = WoolworthsScraper(max_pages=5, client=client)
        records = list(scraper.fetch())

        self.assertEqual([record.external_id for record in records], ["WWL260901-8", "WWL260901-9"])
        self.assertEqual(scraper.stats["listings_discovered"], 2)
        self.assertEqual(scraper.stats["pages_traversed"], 2)
        self.assertEqual(scraper.stats["detail_pages_fetched"], 2)
        self.assertEqual(scraper.stats["valid_application_links"], 2)
        self.assertEqual(
            client.post_calls[0][1]["__EVENTTARGET"],
            "ctl01$cphContent$lbResultsNextPage",
        )
        self.assertEqual(records[0].fields["qualification_text"], "Grade 12")
        self.assertEqual(records[0].fields["experience_min_years"], 0)
        self.assertEqual(records[0].fields["application_url_mechanism"], "detail_page_apply_gateway")
        self.assertEqual(records[0].apply_url, records[0].source_url)
        self.assertTrue(records[0].application_link_valid)


class APIAdapterFidelityTests(TestCase):
    def test_greenhouse_detail_fields_and_numeric_pay_survive(self):
        summary = {"id": 99, "title": "Engineering Graduate", "absolute_url": "https://boards.example/99"}
        detail = {
            **summary,
            "content": "<p>Full employer advert</p>",
            "location": {"name": "Cape Town, South Africa"},
            "departments": [{"name": "Engineering"}],
            "offices": [{"name": "Cape Town Office"}],
            "metadata": [{"name": "Employment Type", "value": "Permanent"}],
            "education": [{"name": "Bachelor's degree"}],
            "requisition_id": "REQ-99",
            "internal_job_id": 10099,
            "first_published": "2026-08-01T10:00:00Z",
            "updated_at": "2026-08-20T10:00:00Z",
            "application_deadline": "2026-09-30",
            "pay_input_ranges": [{
                "min_cents": "1,250,000", "max_cents": 1500000,
                "currency_code": "ZAR", "title": "Monthly range",
            }],
            "questions": [{"label": "Sensitive application question"}],
        }

        class Client:
            def get_json(self, url, **kwargs):
                return {"jobs": [summary]} if url.endswith("/jobs") else detail

        board = SimpleNamespace(board_token="example", company_name="Example Co")
        record = list(GreenhouseAdapter(board, client=Client()).fetch())[0]
        self.assertEqual(record.listing_url, "https://boards-api.greenhouse.io/v1/boards/example/jobs")
        self.assertTrue(record.application_link_valid)
        self.assertEqual(record.raw_content, "<p>Full employer advert</p>")
        self.assertEqual(record.fields["qualification_text"], "")
        self.assertEqual(record.fields["office"], "Cape Town Office")
        self.assertNotIn("business_unit", record.fields)
        self.assertEqual(record.fields["requisition_id"], "REQ-99")
        self.assertEqual(record.fields["internal_job_id"], 10099)
        self.assertEqual(record.fields["salary_min"], Decimal("12500"))
        self.assertEqual(record.fields["salary_max"], Decimal("15000"))
        self.assertIn("questions", record.payload["job"])
        self.assertEqual(record.payload["structured"]["departments"], [{"name": "Engineering"}])
        self.assertEqual(record.payload["structured"]["questions"], [{"label": "Sensitive application question"}])
        self.assertEqual(record.fields.get("eligibility_text", ""), "")

        source = IngestionSource.objects.create(name="Greenhouse", code="gh-test", source_type="api")
        normalized = normalize_record(record, source)
        self.assertEqual(normalized["salary_min"], Decimal("12500"))
        self.assertEqual(normalized["salary_max"], Decimal("15000"))
        self.assertEqual(normalized["qualification_text"], "")
        self.assertEqual(normalized["office"], "Cape Town Office")

    def test_greenhouse_application_education_token_stays_raw_only(self):
        summary = {"id": 100, "title": "Graduate Engineer", "absolute_url": "https://boards.example/100"}
        detail = {**summary, "content": "Full advert", "education": "education_required"}

        class Client:
            def get_json(self, url, **kwargs):
                return {"jobs": [summary]} if url.endswith("/jobs") else detail

        board = SimpleNamespace(board_token="example", company_name="Example Co")
        record = list(GreenhouseAdapter(board, client=Client()).fetch())[0]
        self.assertEqual(record.payload["job"]["education"], "education_required")
        self.assertEqual(record.payload["structured"]["education"], "education_required")
        self.assertEqual(record.fields["qualification_text"], "")

class RawVersionFingerprintTests(TestCase):
    def test_woolworths_transport_tokens_are_ignored_but_source_fields_are_not(self):
        template = """
            <html><body><form>
              <input name="__VIEWSTATE" value="{token}">
              <input name="__EVENTVALIDATION" value="{token}">
              <script src="/candidateapp/ScriptResource.axd?d={token}&amp;t=stable"></script>
              <img src="/Handlers/ImageDownload.ashx?Image=logo&amp;params={token}">
              <table>
                <tr><td>Job Title</td><td>Store Assistant</td></tr>
                <tr><td>Reference Number</td><td>WWL-1</td></tr>
                <tr><td>Minimum Requirements</td><td>{requirements}</td></tr>
                <tr><td>Duties and Responsibilities</td><td>{duties}</td></tr>
                <tr><td>Closing Date</td><td>{closing}</td></tr>
                <tr><td>Job Type</td><td>{job_type}</td></tr>
                <tr><td>Business Unit</td><td>{business_unit}</td></tr>
                <tr><td>Location Description</td><td>{location}</td></tr>
                <tr><td>Country</td><td>South Africa</td></tr>
              </table><button>Apply Now</button>
            </form></body></html>
        """
        values = {
            "requirements": "One year retail experience.", "duties": "Assist customers.",
            "closing": "2026/09/30", "job_type": "Full Time",
            "business_unit": "Corporate Stores", "location": "Cape Town",
        }
        scraper = WoolworthsScraper()

        def record(token, **changes):
            current = {**values, **changes, "token": token}
            result = scraper.parse_detail(
                "https://woolworths.erecruit.co/candidateapp/Jobs/View/WWL-1",
                template.format(**current),
            )
            result.listing_url = "https://woolworths.erecruit.co/candidateapp/jobs/categories/"
            return result

        original = record("request-one")
        self.assertEqual(
            stable_content_hash(scraper, original),
            stable_content_hash(scraper, record("request-two")),
        )
        changes = {
            "requirements": "Two years retail experience.",
            "duties": "Manage stock.",
            "closing": "2026/10/15",
            "job_type": "Part Time",
            "business_unit": "Online Stores",
            "location": "Johannesburg",
        }
        original_hash = stable_content_hash(scraper, original)
        for field, value in changes.items():
            with self.subTest(field=field):
                self.assertNotEqual(original_hash, stable_content_hash(scraper, record("request-two", **{field: value})))

    def test_successfactors_request_noise_is_ignored_but_advert_text_is_not(self):
        scraper = NedbankScraper()

        def record(generated_id, csrf, cachebuster, purpose="Help clients."):
            html = (
                f'<label for="j_idt{generated_id}">Frequency</label>'
                f'<input id="j_idt{generated_id}">'
                f'<script>"X-CSRF-Token" : "{csrf}"</script>'
                f'<script src="/js/override.js?locale=en_GB&amp;i={cachebuster}"></script>'
                f'<section><h2>Job Purpose</h2><p>{purpose}</p></section>'
            )
            return FetchedOpportunity(
                external_id="ned-1", source_url="https://jobs.nedbank.co.za/job/ned-1",
                apply_url="https://jobs.nedbank.co.za/job/ned-1", raw_content=html,
                payload={"listing": {"title": "Service Consultant"}},
            )

        first = record(100, "csrf-one", 111)
        second = record(900, "csrf-two", 999)
        self.assertEqual(stable_content_hash(scraper, first), stable_content_hash(scraper, second))
        self.assertNotEqual(
            stable_content_hash(scraper, first),
            stable_content_hash(scraper, record(900, "csrf-two", 999, purpose="Changed purpose.")),
        )


class PipelineTests(TestCase):
    def setUp(self):
        self.aggregator = IngestionSource.objects.create(
            name="Aggregator", code="aggregator-test", source_type="api", priority=50
        )
        self.employer = IngestionSource.objects.create(
            name="Employer", code="direct-test", source_type="scraper", company="Example Co", priority=10
        )

    def record(self, external_id="job-1", description="Short source description"):
        return FetchedOpportunity(
            external_id=external_id,
            source_url=f"https://example.test/{external_id}",
            apply_url=f"https://example.test/{external_id}/apply",
            payload={"id": external_id, "untouched": {"value": True}},
            raw_content="<p>Original source content</p>",
            fields={
                "title": "Data Graduate Programme", "company": "Example Co",
                "location_text": "Cape Town", "country": "South Africa",
                "reference_number": "REQ-100", "description": description,
                "posted_at": "2026-08-01",
            },
        )

    def test_raw_payload_and_canonical_record_are_both_preserved(self):
        counters = run_adapter(StaticAdapter([self.record()]), self.aggregator)
        self.assertEqual(counters["opportunities_created"], 1)
        self.assertEqual(RawOpportunity.objects.get().raw_payload["untouched"], {"value": True})
        self.assertEqual(OpportunitySourceRecord.objects.count(), 1)

    def test_reference_dedup_links_two_sources_and_prefers_employer_content(self):
        run_adapter(StaticAdapter([self.record(description="Snippet")]), self.aggregator)
        run_adapter(StaticAdapter([self.record("direct-9", "Complete employer advert")]), self.employer)
        opportunity = Opportunity.objects.get()
        self.assertEqual(opportunity.source_records.count(), 2)
        self.assertEqual(opportunity.description, "Complete employer advert")

    def test_same_source_different_external_ids_remain_separate(self):
        first = self.record("source-1")
        second = self.record("source-2")
        first.fields["reference_number"] = "REQ-ONE"
        second.fields["reference_number"] = "REQ-TWO"

        run_adapter(StaticAdapter([first, second]), self.employer)

        self.assertEqual(Opportunity.objects.count(), 2)
        self.assertEqual(OpportunitySourceRecord.objects.count(), 2)
        self.assertEqual(
            OpportunitySourceRecord.objects.values("opportunity_id").distinct().count(),
            2,
        )

    def test_same_source_same_external_id_resolves_same_canonical(self):
        record = self.record("stable-source-id")
        run_adapter(StaticAdapter([record]), self.employer)
        normalized = normalize_record(record, self.employer)

        opportunity, source_record, reason = find_duplicate(
            self.employer, record.external_id, normalized
        )

        self.assertEqual(opportunity.pk, Opportunity.objects.get().pk)
        self.assertEqual(source_record.pk, OpportunitySourceRecord.objects.get().pk)
        self.assertEqual(reason, "source_id")

    def test_repeat_ingestion_updates_without_duplicate_canonical(self):
        record = self.record("repeat-source-id")
        run_adapter(StaticAdapter([record]), self.employer)
        second = run_adapter(StaticAdapter([record]), self.employer)

        self.assertEqual(Opportunity.objects.count(), 1)
        self.assertEqual(OpportunitySourceRecord.objects.count(), 1)
        self.assertEqual(second["opportunities_created"], 0)
        self.assertEqual(second["opportunities_updated"], 1)
        self.assertEqual(second["duplicates_found"], 1)

    def test_same_source_update_clears_stale_requirement_fields(self):
        initial = self.record("requirements-change")
        initial.fields.update({
            "qualification_text": "Bachelor's degree",
            "experience_text": "Minimum 3 years experience",
            "experience_min_years": 3,
        })
        run_adapter(StaticAdapter([initial]), self.employer)

        updated = self.record("requirements-change")
        updated.raw_content = "<p>Updated source content without requirements</p>"
        updated.fields.update({
            "qualification_text": "",
            "experience_text": "",
            "experience_min_years": None,
        })
        run_adapter(StaticAdapter([updated]), self.employer)

        opportunity = Opportunity.objects.get()
        self.assertEqual(opportunity.qualification_text, "")
        self.assertEqual(opportunity.experience_text, "")
        self.assertIsNone(opportunity.experience_min_years)

    def test_cross_source_title_company_location_without_reference_stays_separate(self):
        aggregator_record = self.record("aggregator-1")
        employer_record = self.record("employer-1")
        aggregator_record.fields["reference_number"] = ""
        employer_record.fields["reference_number"] = ""

        run_adapter(StaticAdapter([aggregator_record]), self.aggregator)
        run_adapter(StaticAdapter([employer_record]), self.employer)

        self.assertEqual(Opportunity.objects.count(), 2)
        self.assertEqual(OpportunitySourceRecord.objects.count(), 2)

    def test_dry_run_does_not_write(self):
        counters = run_adapter(StaticAdapter([self.record()]), self.aggregator, dry_run=True)
        self.assertEqual(counters["opportunities_created"], 1)
        self.assertFalse(Opportunity.objects.exists())
        self.assertFalse(RawOpportunity.objects.exists())

    def test_explain_count_limits_output_not_fetching(self):
        records = [self.record(f"job-{index}") for index in range(3)]
        counters = run_adapter(
            StaticAdapter(records),
            self.aggregator,
            dry_run=True,
            explain=True,
            explain_count=1,
        )
        self.assertEqual(counters["records_found"], 3)
        self.assertEqual(len(counters["diagnostics"]), 1)
        self.assertEqual(counters["diagnostics"][0]["status"], "publish")
        self.assertIn("graduate", counters["diagnostics"][0]["classification_reason"])

    def test_unclassified_record_is_preserved_and_published_as_unclassified(self):
        record = self.record()
        record.fields["title"] = "Quantitative Analyst"
        counters = run_adapter(StaticAdapter([record]), self.aggregator)
        self.assertEqual(counters["unclassified_count"], 1)
        self.assertEqual(counters["out_of_scope_count"], 0)
        self.assertEqual(RawOpportunity.objects.get().normalization_status, "unclassified")
        self.assertEqual(Opportunity.objects.get().opportunity_type, "unclassified")

    def test_numeric_experience_alone_is_retained_unclassified(self):
        record = self.record()
        record.fields.update({
            "title": "Business Analyst",
            "experience_text": "Minimum 5 years required.",
            "experience_min_years": 5,
        })
        counters = run_adapter(StaticAdapter([record]), self.aggregator)
        self.assertEqual(counters["out_of_scope_count"], 0)
        self.assertEqual(RawOpportunity.objects.get().normalization_status, "unclassified")
        self.assertEqual(Opportunity.objects.get().scope_status, "unclassified")

    def test_intentionally_unset_experience_scalar_is_not_a_parse_error(self):
        record = self.record()
        record.fields.update({
            "title": "Strategic People Partner",
            "experience_text": "3-4 years in one domain and 4 years in another domain.",
            "experience_min_years": None,
            "experience_max_years": None,
            "experience_scalar_intentionally_unset": True,
        })
        counters = run_adapter(StaticAdapter([record]), self.aggregator, dry_run=True)
        self.assertEqual(counters["invalid_experience_parses_count"], 0)
        self.assertEqual(counters["scope_unclassified_count"], 1)

    def test_generic_or_non_http_application_url_is_rejected(self):
        record = self.record()
        record.apply_url = "javascript:void(0)"
        counters = run_adapter(StaticAdapter([record]), self.aggregator, dry_run=True)
        self.assertEqual(counters["errors_count"], 1)
        self.assertEqual(counters["opportunities_created"], 0)


class LifecycleTests(TestCase):
    def setUp(self):
        self.source = IngestionSource.objects.create(
            name="Lifecycle", code="lifecycle-test", source_type="api"
        )

    def test_passed_vacancy_closing_date_expires(self):
        opportunity = Opportunity.objects.create(
            title="Accounting Internship", company="Example", opportunity_type="internship",
            location="Johannesburg", description="Test",
            closing_date=timezone.localdate() - timedelta(days=1),
        )
        refresh_opportunity_lifecycle()
        opportunity.refresh_from_db()
        self.assertFalse(opportunity.is_active)
        self.assertEqual(opportunity.lifecycle_status, "expired")

    def test_future_and_undisclosed_closing_dates_remain_active(self):
        future = Opportunity.objects.create(
            title="Future vacancy", company="Example", opportunity_type="entry_level",
            closing_date=timezone.localdate() + timedelta(days=1),
        )
        undisclosed = Opportunity.objects.create(
            title="Undisclosed date", company="Example", opportunity_type="entry_level",
        )
        refresh_opportunity_lifecycle()
        self.assertEqual(set(Opportunity.objects.discoverable().values_list("pk", flat=True)),
                         {future.pk, undisclosed.pk})

    def test_closed_notification_only_for_interested_not_applied_and_once(self):
        opportunity = Opportunity.objects.create(
            title="Closing vacancy", company="Example", opportunity_type="entry_level",
            closing_date=timezone.localdate() - timedelta(days=1),
        )
        saved_user = User.objects.create_user("saved-candidate")
        preparing_user = User.objects.create_user("preparing-candidate")
        applied_user = User.objects.create_user("applied-candidate")
        unrelated_user = User.objects.create_user("unrelated-candidate")
        for user in (saved_user, preparing_user, applied_user, unrelated_user):
            Profile.objects.create(user=user)
        SavedOpportunity.objects.create(user=saved_user, opportunity=opportunity)
        SavedOpportunity.objects.create(user=applied_user, opportunity=opportunity)
        preparation = Application.objects.create(user=preparing_user, opportunity=opportunity)
        Application.objects.create(user=applied_user, opportunity=opportunity,
                                   status="applied", applied_at=timezone.now())

        refresh_opportunity_lifecycle()
        refresh_opportunity_lifecycle()
        notices = Notification.objects.filter(event_key__startswith="opportunity-closed:")
        self.assertEqual(set(notices.values_list("user_id", flat=True)),
                         {saved_user.pk, preparing_user.pk})
        self.assertEqual(notices.count(), 2)
        self.assertEqual(notices.get(user=saved_user).target_url, "/saved/")
        self.assertIn(str(preparation.pk), notices.get(user=preparing_user).target_url)
        self.assertEqual(len(mail.outbox), 0)

    @skip('Retired raw-deadline notification authority; clean revision dates now govern candidate notifications.')
    def test_closing_soon_is_weekly_idempotent_and_separate_from_closure(self):
        today = timezone.localdate()
        opportunity = Opportunity.objects.create(
            title="Accounts Officer", company="Example", opportunity_type="entry_level",
            closing_date=today + timedelta(days=7),
        )
        saved_user = User.objects.create_user("soon-saved")
        preparing_user = User.objects.create_user("soon-preparing")
        applied_user = User.objects.create_user("soon-applied")
        withdrawn_user = User.objects.create_user("soon-withdrawn")
        opted_out_user = User.objects.create_user("soon-opted-out")
        for user in (saved_user, preparing_user, applied_user, withdrawn_user, opted_out_user):
            Profile.objects.create(user=user)
        for user in (saved_user, applied_user, withdrawn_user, opted_out_user):
            SavedOpportunity.objects.create(user=user, opportunity=opportunity)
        NotificationPreference.objects.create(user=opted_out_user, deadlines=False)
        preparing = Application.objects.create(user=preparing_user, opportunity=opportunity)
        applied = Application.objects.create(
            user=applied_user, opportunity=opportunity, status="applied", applied_at=timezone.now(),
        )
        withdrawn = Application.objects.create(
            user=withdrawn_user, opportunity=opportunity, status="withdrawn",
        )
        no_date = Opportunity.objects.create(
            title="Undated vacancy", company="Example", opportunity_type="entry_level",
        )
        SavedOpportunity.objects.create(user=saved_user, opportunity=no_date)
        outside_window = Opportunity.objects.create(
            title="Later vacancy", company="Example", opportunity_type="entry_level",
            closing_date=today + timedelta(days=8),
        )
        SavedOpportunity.objects.create(user=saved_user, opportunity=outside_window)
        already_closed = Opportunity.objects.create(
            title="Closed vacancy", company="Example", opportunity_type="entry_level",
            closing_date=today - timedelta(days=1),
        )
        SavedOpportunity.objects.create(user=saved_user, opportunity=already_closed)

        refresh_opportunity_lifecycle()
        refresh_opportunity_lifecycle()
        reminders = Notification.objects.filter(event_key__startswith="opportunity-closing-soon:")
        self.assertEqual(set(reminders.values_list("user_id", flat=True)),
                         {saved_user.pk, preparing_user.pk})
        self.assertEqual(reminders.count(), 2)
        self.assertEqual(reminders.get(user=saved_user).target_url, "/saved/")
        self.assertIn(str(preparing.pk), reminders.get(user=preparing_user).target_url)
        self.assertEqual(len(mail.outbox), 0)
        self.client.force_login(saved_user)
        for route in ("dashboard", "opportunities", "notifications"):
            self.client.get(reverse(route))
        self.assertEqual(Notification.objects.filter(
            event_key__startswith="opportunity-closing-soon:"
        ).count(), 2)
        self.assertEqual(Notification.objects.filter(
            event_key__startswith="opportunity-closed:", user=saved_user,
        ).count(), 1)  # Only the independently closed vacancy so far.

        opportunity.closing_date = today - timedelta(days=1)
        opportunity.application_deadline = opportunity.closing_date
        opportunity.save(update_fields=["closing_date", "application_deadline"])
        refresh_opportunity_lifecycle()
        refresh_opportunity_lifecycle()
        self.assertEqual(Notification.objects.filter(
            event_key__startswith="opportunity-closing-soon:",
        ).count(), 2)
        closed = Notification.objects.filter(
            event_key__startswith=f"opportunity-closed:{opportunity.pk}:"
        )
        self.assertEqual(set(closed.values_list("user_id", flat=True)),
                         {saved_user.pk, preparing_user.pk, opted_out_user.pk})
        applied.refresh_from_db()
        withdrawn.refresh_from_db()
        self.assertEqual(applied.status, "applied")
        self.assertEqual(withdrawn.status, "withdrawn")

    @override_settings(INGESTION_MISSING_GRACE_DAYS=7)
    def test_missing_record_waits_for_grace_then_marks_removed(self):
        opportunity = Opportunity.objects.create(
            title="Audit Learnership", company="Example", opportunity_type="learnership",
            location="Pretoria", description="Test",
        )
        source_record = OpportunitySourceRecord.objects.create(
            opportunity=opportunity, source=self.source, source_external_id="gone"
        )
        mark_missing_source_records(self.source, set())
        source_record.refresh_from_db()
        self.assertTrue(source_record.active_at_source)
        source_record.missing_since = timezone.now() - timedelta(days=8)
        source_record.save(update_fields=["missing_since"])
        refresh_opportunity_lifecycle()
        source_record.refresh_from_db()
        opportunity.refresh_from_db()
        self.assertFalse(source_record.active_at_source)
        self.assertEqual(opportunity.lifecycle_status, "removed")


class IngestAllCommandTests(TestCase):
    @patch("ingestion.management.commands.ingest_all.call_command")
    def test_one_provider_failure_does_not_abort_later_sources(self, call_command):
        call_command.side_effect = [RuntimeError("provider unavailable"), None, None, None]
        command = IngestAllCommand()
        command.stdout = StringIO()
        command.stderr = StringIO()

        command.handle(dry_run=True, max_jobs=1)

        self.assertEqual(call_command.call_count, 4)
        self.assertEqual(
            [call.args[0] for call in call_command.call_args_list],
            [
                "ingest_greenhouse", "ingest_smartrecruiters", "ingest_workday",
                "ingest_company_scrapers",
            ],
        )
        self.assertIn("ingest_greenhouse failed", command.stderr.getvalue())

    @patch("ingestion.management.commands.ingest_all.call_command")
    def test_non_dry_run_refreshes_trusted_career_assignments(self, call_command):
        command = IngestAllCommand()
        command.stdout = StringIO()
        command.stderr = StringIO()
        command.handle(dry_run=False, max_jobs=1)
        self.assertEqual([call.args[0] for call in call_command.call_args_list], [
            "ingest_greenhouse", "ingest_smartrecruiters", "ingest_workday",
            "ingest_company_scrapers", "maintain_career_market",
        ])
        self.assertTrue(call_command.call_args_list[-1].kwargs["assign_only"])
