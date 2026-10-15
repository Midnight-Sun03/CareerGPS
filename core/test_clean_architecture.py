from copy import deepcopy

from django.contrib.auth.models import User
from django.apps import apps
from django.test import SimpleTestCase, TestCase, override_settings
from django.utils import timezone

from core.clean_jobs.service import CleanImportError, import_clean_payload
from core.clean_jobs.source import source_input_hash
from core.models import (
    CareerPreference, CareerTargetIdentity, CareerTargetResolution, Opportunity,
    OpportunityCleanAttempt, OpportunityOccupationIdentity,
    OpportunityOccupationResolution, Profile, ProfileOpportunityEvaluation,
    ProfileSkill, Project, TaxonomyConcept, TaxonomyCrosswalk, TaxonomyLabel,
    TaxonomyRelease,
)
from core.professional_matching import (
    _fit_band, _requirement_units, criterion_results, profile_has_signal,
)
from core.professional_policies import (
    BAND_RANK, CURRENT_FIT_WEIGHTS, UNWEIGHTED_PENDING_CALIBRATION,
)
from core.services.occupational_resolution import resolve_vacancy_sources
from core.services.professional_evaluations import professional_feed_queryset
from core.services.professional_resolution import (
    discovery_relationship, resolve_preference_targets, resolve_text_identities,
    safe_title_target,
)
from core.test_support import create_current_evaluation, create_professional_opportunity
from applications.services.job_context import resolve_job_context
from ingestion.models import (
    IngestionSource, OpportunityExtraction, OpportunitySourceRecord, RawOpportunity,
)


class CleanArchitectureCase(TestCase):
    def setUp(self):
        self.source = IngestionSource.objects.create(
            name="Test source", code="test-source", source_type="api", company="Example Ltd")
        self.opportunity = Opportunity.objects.create(
            title="Untrusted raw title", company="Raw company", description="Raw fallback text",
            is_active=True, lifecycle_status="active", listing_kind="vacancy",
            scope_status="in_scope")
        self.raw = RawOpportunity.objects.create(
            source=self.source, source_external_id="job-1",
            raw_content=("Junior Data Analyst. Example Ltd. Johannesburg, Gauteng, South Africa. "
                         "Full-time hybrid vacancy. Python is required. "
                         "A source-grounded analyst role."),
            raw_payload={"title": "Junior Data Analyst", "company": "Example Ltd"},
            content_hash="a" * 64)
        self.record = OpportunitySourceRecord.objects.create(
            opportunity=self.opportunity, source=self.source,
            source_external_id="job-1", current_raw=self.raw,
            source_url="https://example.test/jobs/1")

    def payload(self):
        return {
            "schema_version": "1.1",
            "cleaner": {
                "name": "external-test-cleaner", "version": "1",
                "cleaned_at": timezone.now().isoformat(), "run_id": "run-1",
            },
            "source": {
                "source_name": self.source.code,
                "external_opportunity_id": "job-1",
                "source_url": self.record.source_url,
                "captured_at": self.raw.fetched_at.isoformat(),
                "source_input_hash": source_input_hash(self.record),
            },
            "job": {
                "cleaned_title": "Junior Data Analyst", "company": "Example Ltd",
                "location": {"raw": "Johannesburg", "city": "Johannesburg",
                             "region": "Gauteng", "country": "South Africa"},
                "employment_type": ["FULL_TIME"], "work_arrangement": "HYBRID",
                "opportunity_kind": "VACANCY", "posted_at": None, "closing_at": None,
                "salary": None, "cleaned_description": "A source-grounded analyst role.",
                "seniority": {
                    "label": "NON_SENIOR", "reason": "The source calls the role junior.",
                    "evidence": [{"text": "Junior Data Analyst"}],
                },
                "provenance": {
                    "cleaned_title": [{"source_section": "raw_content",
                                       "evidence": {"text": "Junior Data Analyst"}}],
                    "company": [{"source_section": "source_context.publisher_name",
                                 "evidence": {"text": "Example Ltd"}}],
                    "location": [{"source_section": "raw_content", "evidence": {
                        "text": "Johannesburg, Gauteng, South Africa"}}],
                    "employment_type": [{"source_section": "raw_content",
                                         "evidence": {"text": "Full-time"}}],
                    "work_arrangement": [{"source_section": "raw_content",
                                          "evidence": {"text": "hybrid"}}],
                    "opportunity_kind": [{"source_section": "source_context.listing_kind",
                                          "evidence": {"text": "vacancy"}}],
                    "posted_at": [], "closing_at": [], "salary": [],
                    "cleaned_description": [{"source_section": "raw_content", "evidence": {
                        "text": "A source-grounded analyst role."}}],
                },
            },
            "criteria": [{
                "criterion_id": "skill-python", "criterion_type": "SKILL",
                "normalized_value": "python", "raw_value": "Python",
                "requirement_level": "REQUIRED", "significance": "CORE",
                "source_section": "raw_content", "requirement_group": None,
                "evidence": [{"text": "Python is required"}],
                "qualification": None, "experience": None, "language": None,
            }],
        }


class CleanImportTests(CleanArchitectureCase):
    def test_valid_payload_activates_immutable_revision_and_is_idempotent(self):
        payload = self.payload()
        first = import_clean_payload(payload)
        second = import_clean_payload(deepcopy(payload))
        self.opportunity.refresh_from_db()
        self.assertTrue(first.created)
        self.assertFalse(second.created)
        self.assertEqual(first.revision_id, second.revision_id)
        self.assertEqual(self.opportunity.active_clean_revision_id, first.revision_id)
        revision = self.opportunity.active_clean_revision
        revision.cleaned_title = "Mutated"
        with self.assertRaises(ValueError):
            revision.save()
        with self.assertRaises(ValueError):
            self.opportunity.clean_revisions.filter(pk=revision.pk).update(
                cleaned_title="Mutated")

    def test_evidence_not_in_source_is_quarantined(self):
        payload = self.payload()
        payload["criteria"][0]["evidence"][0]["text"] = "Invented requirement"
        with self.assertRaises(CleanImportError):
            import_clean_payload(payload)
        self.assertEqual(OpportunityCleanAttempt.objects.latest("pk").status, "quarantined")
        self.assertFalse(Opportunity.objects.professionally_ready().exists())

    def test_source_version_change_immediately_removes_listing(self):
        import_clean_payload(self.payload())
        self.assertTrue(Opportunity.objects.professionally_ready().exists())
        changed = RawOpportunity.objects.create(
            source=self.source, source_external_id="job-1",
            raw_content="Changed source", raw_payload={}, content_hash="b" * 64)
        self.record.current_raw = changed
        self.record.save(update_fields=["current_raw", "last_seen_at"])
        self.assertFalse(Opportunity.objects.professionally_ready().exists())

    def test_unknown_profile_dimension_is_not_a_gap(self):
        result = import_clean_payload(self.payload())
        user = User.objects.create_user("empty", password="password")
        profile = Profile.objects.create(user=user)
        rows = criterion_results(profile, self.opportunity.clean_revisions.get(pk=result.revision_id))
        self.assertEqual(rows[0]["status"], "UNKNOWN")

    def test_invalid_contract_matrix_never_activates(self):
        cases = {}
        payload = self.payload(); payload["schema_version"] = "2.0"; cases["schema"] = payload
        payload = self.payload(); payload["source"]["source_input_hash"] = "0" * 64; cases["hash"] = payload
        payload = self.payload(); payload["job"].pop("seniority"); cases["seniority"] = payload
        payload = self.payload(); payload["criteria"][0]["evidence"] = []; cases["evidence"] = payload
        payload = self.payload(); payload["job"]["work_arrangement"] = "SOMETIMES"; cases["enum"] = payload
        payload = self.payload(); payload["criteria"][0]["requirement_group"] = {
            "group_id": "only-one", "operator": "OR"}; cases["or_group"] = payload
        payload = self.payload(); payload["criteria"][0]["qualification"] = {
            "level": "BACHELORS", "fields": [], "nqf_level": None}; cases["structured"] = payload
        payload = self.payload(); payload["job"]["salary"] = {
            "raw": "R20-R10", "currency": "ZAR", "minimum": 20,
            "maximum": 10, "period": "MONTH", "is_negotiable": False}; cases["salary"] = payload
        payload = self.payload(); payload["job"]["posted_at"] = "last Tuesday"; cases["date"] = payload
        for label, invalid in cases.items():
            with self.subTest(label=label), self.assertRaises(CleanImportError):
                import_clean_payload(invalid)
        self.assertFalse(self.opportunity.clean_revisions.exists())
        self.assertIsNone(Opportunity.objects.get(pk=self.opportunity.pk).active_clean_revision_id)

    def test_new_revision_supersedes_and_failed_replacement_preserves_active(self):
        first = import_clean_payload(self.payload())
        replacement = self.payload()
        replacement["cleaner"]["run_id"] = "run-2"
        replacement["job"]["cleaned_description"] = "Python is required."
        replacement["job"]["provenance"]["cleaned_description"] = [{
            "source_section": "raw_content", "evidence": {"text": "Python is required."}}]
        second = import_clean_payload(replacement)
        self.assertNotEqual(first.revision_id, second.revision_id)
        self.assertEqual(self.opportunity.clean_revisions.get(pk=first.revision_id).status, "superseded")
        invalid = self.payload()
        invalid["cleaner"]["run_id"] = "run-3"
        invalid["criteria"][0]["evidence"][0]["text"] = "Not in source"
        with self.assertRaises(CleanImportError):
            import_clean_payload(invalid)
        self.opportunity.refresh_from_db()
        self.assertEqual(self.opportunity.active_clean_revision_id, second.revision_id)

    def test_job_context_requires_clean_revision_and_historical_pin_is_reproducible(self):
        self.assertEqual(resolve_job_context(self.opportunity)["status"],
                         "clean_revision_unavailable")
        first = import_clean_payload(self.payload())
        pinned = self.opportunity.clean_revisions.get(pk=first.revision_id)
        replacement = self.payload(); replacement["cleaner"]["run_id"] = "run-2"
        replacement["job"]["cleaned_title"] = "Data Analyst"
        import_clean_payload(replacement)
        context = resolve_job_context(self.opportunity, clean_revision=pinned)
        self.assertEqual(context["status"], "clean_revision_available")
        self.assertEqual(context["data"]["title"], "Junior Data Analyst")

    def test_search_and_detail_allow_clean_category_none_without_evaluation(self):
        import_clean_payload(self.payload())
        self.opportunity.refresh_from_db()
        resolve_vacancy_sources(self.opportunity.active_clean_revision)
        user = User.objects.create_user("browser", password="password")
        Profile.objects.create(user=user)
        self.client.force_login(user)
        search = self.client.get("/opportunities/", {"search": "Junior"})
        detail = self.client.get(f"/opportunities/{self.opportunity.pk}/")
        self.assertContains(search, "Junior Data Analyst")
        self.assertContains(detail, "Junior Data Analyst")
        self.assertNotContains(detail, "Evidence band")


@override_settings(CAREERGPS_FEED_READ_VERSION='legacy')
class MaterializedFeedTests(CleanArchitectureCase):
    def test_feed_requires_current_evaluation_for_active_revision(self):
        result = import_clean_payload(self.payload())
        user = User.objects.create_user("candidate", password="password")
        profile = Profile.objects.create(user=user)
        ProfileSkill.objects.create(
            profile=profile, entered_name="Python", confirmed_by_user=True)
        revision = self.opportunity.clean_revisions.get(pk=result.revision_id)
        ProfileOpportunityEvaluation.objects.create(
            profile=profile, opportunity=self.opportunity, clean_revision=revision,
            dependency_hash="d" * 64, discovery_category="PROFILE_RELEVANT",
            discovery_rank=3, band="C", band_rank=3)
        self.assertEqual(list(professional_feed_queryset(profile)),
                         list(ProfileOpportunityEvaluation.objects.filter(profile=profile)))
        ProfileOpportunityEvaluation.objects.filter(profile=profile).update(state="STALE")
        self.assertFalse(professional_feed_queryset(profile).exists())

    def test_raw_opportunity_without_clean_revision_never_enters_feed(self):
        user = User.objects.create_user("raw-only", password="password")
        profile = Profile.objects.create(user=user)
        CareerPreference.objects.create(
            profile=profile, target_roles="Data Analyst", confirmed_by_user=True)
        self.assertFalse(Opportunity.objects.professionally_ready().exists())
        self.assertFalse(professional_feed_queryset(profile).exists())

    def test_legacy_ready_cannot_enable_search_or_professional_detail(self):
        OpportunityExtraction.objects.create(
            opportunity=self.opportunity, status="ready", model_name="legacy-test",
            input_hash="a" * 64)
        user = User.objects.create_user("legacy-ready", password="password")
        Profile.objects.create(user=user)
        self.client.force_login(user)
        search = self.client.get(
            "/opportunities/", {"search": "Untrusted raw title"})
        self.assertEqual(search.context["page_obj"].paginator.count, 0)
        self.assertEqual(
            self.client.get(f"/opportunities/{self.opportunity.pk}/").status_code, 404)

    def test_discovery_category_precedes_fit_band_in_database_order(self):
        import_clean_payload(self.payload())
        self.opportunity.refresh_from_db()
        related = create_professional_opportunity(title="Related role")
        profile_relevant = create_professional_opportunity(title="Profile role")
        user = User.objects.create_user("ordered", password="password")
        profile = Profile.objects.create(user=user)
        create_current_evaluation(profile, self.opportunity, category="TARGET", band="F")
        create_current_evaluation(profile, related, category="RELATED", band="A")
        create_current_evaluation(
            profile, profile_relevant, category="PROFILE_RELEVANT", band="A")
        self.assertEqual(
            list(professional_feed_queryset(profile).values_list(
                "discovery_category", "band")),
            [("TARGET", "F"), ("RELATED", "A"), ("PROFILE_RELEVANT", "A")])

    def test_obvious_senior_is_hidden_normally_but_direct_detail_is_allowed(self):
        payload = self.payload()
        payload["job"]["seniority"]["label"] = "OBVIOUS_SENIOR"
        import_clean_payload(payload)
        user = User.objects.create_user("senior-browser", password="password")
        Profile.objects.create(user=user)
        self.client.force_login(user)
        self.assertNotContains(self.client.get("/opportunities/"), "Junior Data Analyst")
        self.assertContains(
            self.client.get(f"/opportunities/{self.opportunity.pk}/"),
            "Junior Data Analyst")


class FitPolicyTests(SimpleTestCase):
    def unit(self, identifier, kind, status, *, significance="CORE", level="REQUIRED", group=None):
        return {
            "criterion_id": identifier, "criterion_type": kind,
            "normalized_value": identifier, "raw_value": identifier,
            "requirement_level": level, "significance": significance,
            "requirement_group": group, "status": status, "profile_evidence": [],
        }

    def test_evidence_only_bands_a_through_f_and_crucial_unknown_cap(self):
        found = lambda ident, kind: self.unit(ident, kind, "FOUND")
        missing = lambda ident, kind, **kw: self.unit(
            ident, kind, "NOT_FOUND_ON_PROFILE", **kw)
        self.assertEqual(_fit_band([found("a", "SKILL"), found("b", "SKILL"),
                                    found("c", "EXPERIENCE"), found("d", "EXPERIENCE")])["band"], "A")
        self.assertEqual(_fit_band([found("a", "SKILL"), found("b", "EXPERIENCE"),
                                    missing("c", "SKILL")])["band"], "B")
        self.assertEqual(_fit_band([found("a", "SKILL"), found("b", "EXPERIENCE"),
                                    missing("c", "SKILL"), missing("d", "EXPERIENCE"),
                                    missing("e", "CERTIFICATION")])["band"], "C")
        self.assertEqual(_fit_band([found("a", "SKILL")])["band"], "D")
        self.assertEqual(_fit_band([missing("a", "SKILL")])["band"], "F")
        blocker = missing("gate", "QUALIFICATION", significance="CRUCIAL")
        self.assertEqual(_fit_band([found("a", "SKILL"), found("b", "SKILL"),
                                    found("c", "EXPERIENCE"), found("d", "EXPERIENCE"),
                                    blocker])["band"], "E")
        unknown_gate = self.unit("gate", "QUALIFICATION", "UNKNOWN", significance="CRUCIAL")
        self.assertEqual(_fit_band([found("a", "SKILL"), found("b", "SKILL"),
                                    found("c", "EXPERIENCE"), found("d", "EXPERIENCE"),
                                    unknown_gate])["band"], "C")

    def test_or_group_uses_best_supported_alternative(self):
        rows = [self.unit("python", "SKILL", "FOUND", group="languages"),
                self.unit("r", "SKILL", "NOT_FOUND_ON_PROFILE", group="languages")]
        units = _requirement_units(rows)
        self.assertEqual(len(units), 1)
        self.assertEqual(units[0]["status"], "FOUND")

    def test_licence_is_a_gate_but_has_no_unapproved_scalar_weight(self):
        self.assertNotIn("LICENCE", CURRENT_FIT_WEIGHTS)
        self.assertIn("LICENCE", UNWEIGHTED_PENDING_CALIBRATION)
        skill = self.unit("python", "SKILL", "FOUND")
        found_licence = self.unit("licence", "LICENCE", "FOUND")
        missing_licence = self.unit(
            "licence", "LICENCE", "NOT_FOUND_ON_PROFILE", significance="CRUCIAL")
        self.assertEqual(_fit_band([skill])["score"], _fit_band([skill, found_licence])["score"])
        self.assertEqual(_fit_band([skill, missing_licence])["major_gaps"], 1)
        self.assertEqual(_fit_band([skill, missing_licence])["band"], "F")

    def test_unknown_is_neutral_and_e_band_order_is_locked(self):
        skill = self.unit("python", "SKILL", "FOUND")
        unknown = self.unit("degree", "QUALIFICATION", "UNKNOWN")
        baseline = _fit_band([skill])
        result = _fit_band([skill, unknown])
        self.assertEqual(result["score"], baseline["score"])
        self.assertEqual(result["major_gaps"], 0)
        self.assertEqual(BAND_RANK, {'A': 1, 'B': 2, 'C': 3, 'E': 4, 'D': 5, 'F': 6})


class LockedBoundaryTests(CleanArchitectureCase):
    def test_unresolved_authored_preference_remains_signal_and_desiredcareer_is_absent(self):
        user = User.objects.create_user("unresolved", password="password")
        profile = Profile.objects.create(user=user)
        preference = CareerPreference.objects.create(
            profile=profile, target_roles="Novel Role With No Taxonomy Identity")
        resolutions = resolve_preference_targets(preference)
        self.assertEqual(resolutions[0].state, "UNRESOLVED")
        self.assertTrue(profile_has_signal(profile))
        with self.assertRaises(LookupError):
            apps.get_model("core", "DesiredCareer")

    def test_project_free_text_is_not_skill_evidence(self):
        result = import_clean_payload(self.payload())
        user = User.objects.create_user("project-prose", password="password")
        profile = Profile.objects.create(user=user)
        Project.objects.create(
            profile=profile, title="Analytics project",
            description="Built the entire project in Python.")
        revision = self.opportunity.clean_revisions.get(pk=result.revision_id)
        self.assertEqual(criterion_results(profile, revision)[0]["status"], "UNKNOWN")

    def test_exact_labels_preserve_multiple_taxonomy_sources(self):
        for source in ("esco", "ofo"):
            release = TaxonomyRelease.objects.create(
                source=source, version=f"test-{source}", is_active=True)
            concept = TaxonomyConcept.objects.create(
                release=release, external_id=f"{source}:analyst", kind="occupation",
                preferred_label="Data Analyst", normalized_label="data analyst")
            TaxonomyLabel.objects.create(
                concept=concept, label="Data Analyst", normalized_label="data analyst",
                label_type="preferred")
        identities, state, _ = resolve_text_identities("Data Analyst")
        self.assertEqual(state, "RESOLVED")
        self.assertEqual(
            set(TaxonomyConcept.objects.filter(pk__in=identities).values_list(
                "release__source", flat=True)), {"esco", "ofo"})

    def test_related_resolution_does_not_follow_two_crosswalk_hops(self):
        revision = import_clean_payload(self.payload()).revision_id
        release = TaxonomyRelease.objects.create(
            source="esco", version="no-transitive", is_active=True)
        concepts = [TaxonomyConcept.objects.create(
            release=release, external_id=f"occupation:{name}", kind="occupation",
            preferred_label=name, normalized_label=name.casefold())
            for name in ("Alpha", "Beta", "Gamma")]
        preference = CareerPreference.objects.create(
            profile=Profile.objects.create(
                user=User.objects.create_user("one-hop", password="password")),
            target_roles="Alpha")
        target = CareerTargetResolution.objects.create(
            preference=preference, profile=preference.profile, ordinal=0,
            authored_text="Alpha", normalized_text="alpha", state="RESOLVED",
            resolver_version="test", dependency_hash="a" * 64)
        CareerTargetIdentity.objects.create(
            resolution=target, concept=concepts[0], route="exact_label")
        opportunity_resolution = OpportunityOccupationResolution.objects.create(
            revision_id=revision, state="RESOLVED", resolver_version="test",
            dependency_hash="b" * 64, diagnostics={})
        OpportunityOccupationIdentity.objects.create(
            resolution=opportunity_resolution, concept=concepts[2],
            route="exact_label", source_text="Gamma")
        for index, (left, right) in enumerate(zip(concepts, concepts[1:])):
            TaxonomyCrosswalk.objects.create(
                source_release=release, source_concept=left,
                source_external_id=left.external_id, target_release=release,
                target_concept=right, target_external_id=right.external_id,
                relation_type="close", direction="forward",
                approval_status="approved_related", mapping_name="test",
                mapping_version="1", source_reference=f"row-{index}")
        category, _, _ = discovery_relationship([target], opportunity_resolution)
        self.assertIsNone(category)

    def test_safe_title_fallback_is_exact_or_harmless_only(self):
        self.assertTrue(safe_title_target("Data Analyst", "Data Analyst"))
        self.assertTrue(safe_title_target("Data Analyst", "Junior Data Analyst"))
        self.assertFalse(safe_title_target("Data Analyst", "Senior Data Analyst"))
        self.assertFalse(safe_title_target("Data Analyst", "Data Analyst / Engineer"))
