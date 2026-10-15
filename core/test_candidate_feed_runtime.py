"""Runtime candidate-feed contract tests.

Covers the complete matching/ranking specification: the candidate universe
is lifecycle/readiness plus the read-only seniority decision; matching ranks
and explains, it never admits or removes. Test names carry the numbered
requirement of the specification's regression list.
"""
from datetime import date
from types import SimpleNamespace

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from core.matching import (
    CriterionImportance, CriterionResult, EvidenceSignificance, EvidenceType,
    MatchCategory, MatchStatus, STATUS_SCORE, _career_direction_ranking_tier,
    _category_fit, _ranking_key, _recommendation_decision,
    build_taxonomy_context_for_run, get_matched_opportunities,
    match_profile_to_opportunity,
)
from core.models import (
    CareerPreference, Certification, OccupationRelationshipPolicy,
    Opportunity, Profile, ProfileSkill, TaxonomyConcept, TaxonomyLabel,
    TaxonomyRelease, TaxonomyRelation, WorkExperience,
)
from core.professional_policies import CANDIDATE_RELATIONSHIP_POLICY_VERSION
from core.services.feed_v2 import build_profile_generation
from core.services.occupational_resolution import (
    maintain_target_resolution_containers,
)
from core.taxonomies.normalization import normalize_term
from core.taxonomies.resolver import build_taxonomy_context
from core.services.title_gate import exclude_senior
from core.test_support import create_professional_opportunity


BAND_PRIORITY = {"A": 6, "B": 5, "C": 4, "D": 3, "E": 2, "F": 1}


def criterion(criterion_id, kind, value, *, level="REQUIRED",
              significance="CORE", experience=None):
    """One criterion in the current authoritative clean-payload shape."""
    return {
        "criterion_id": criterion_id,
        "criterion_type": kind,
        "normalized_value": str(value).lower(),
        "raw_value": str(value),
        "requirement_level": level,
        "significance": significance,
        "source_section": "raw_content",
        "requirement_group": None,
        "evidence": [{"text": str(value)}],
        "qualification": None,
        "experience": experience,
        "language": None,
    }


class RuntimeFeedCase(TestCase):
    """Shared fixtures: active releases, candidate, jobs, taxonomy helpers."""

    def setUp(self):
        self.releases = {
            source: TaxonomyRelease.objects.create(
                source=source, version=f"runtime-{source}", is_active=True)
            for source in ("ofo", "esco")
        }
        self.profile = Profile.objects.create(
            user=User.objects.create_user(
                "runtime-candidate", password="password"))
        self.client.force_login(self.profile.user)

    def concept(self, label, *, source="ofo", kind="occupation",
                metadata=None):
        concept = TaxonomyConcept.objects.create(
            release=self.releases[source], kind=kind,
            external_id=f"{source}:{kind}:{TaxonomyConcept.objects.count()}",
            preferred_label=label, normalized_label=normalize_term(label),
            metadata=metadata or {})
        self.label(concept, label, "preferred")
        return concept

    def label(self, concept, text, label_type="alternative"):
        return TaxonomyLabel.objects.create(
            concept=concept, label=text,
            normalized_label=normalize_term(text), label_type=label_type)

    def relation(self, narrower, broader):
        return TaxonomyRelation.objects.create(
            subject=narrower, object=broader, relation_type="broader")

    def target(self, text):
        preference, _created = CareerPreference.objects.update_or_create(
            profile=self.profile, defaults={"target_roles": text})
        return maintain_target_resolution_containers(preference)

    def job(self, title, *, seniority="NON_SENIOR",
            opportunity_type="entry_level", criteria=()):
        return create_professional_opportunity(
            title=title, seniority=seniority,
            opportunity_type=opportunity_type, criteria=list(criteria))

    def feed_rows(self, params=None):
        """The runtime ranking contract, read from the matcher itself.

        This suite is the specification for the deterministic matcher: bands,
        tiers and scores. It therefore calls the matcher directly rather than
        reading the candidate pages. The pages no longer run the matcher on an
        ordinary load -- a profile with no authored target, or one whose new
        target is still resolving, is served the provisional catalogue instead --
        so the ranking contract is asserted where it is actually produced.
        """
        return get_matched_opportunities(
            self.profile,
            queryset=exclude_senior(Opportunity.objects.professionally_ready(
                include_obvious_senior=False)),
            include_cv_tips=False)

    def discovery_rows(self, params=None):
        """The clearly-labelled discovery area of the same page."""
        response = self.client.get(reverse("opportunities"), params or {})
        self.assertEqual(response.status_code, 200)
        return list(response.context["discovery_results"])

    def feed_titles(self, params=None):
        return [row["opportunity"].title for row in self.feed_rows(params)]

class CandidateUniverseVisibilityTests(RuntimeFeedCase):
    """Spec items 1, 2, 3, 6, 7, 8 and 41: nothing eligible disappears."""

    def setUp(self):
        super().setUp()
        self.job("NON_SENIOR Data Analyst")
        self.job("UNCERTAIN Review Clerk", seniority="UNCERTAIN")
        self.job("Chief Finance Officer", seniority="OBVIOUS_SENIOR")
        self.job("Zero Evidence Qqq Role", criteria=())
        self.job("Ambiguous Data Role", criteria=())
        self.job("Zzz Unmapped Qqq Role", criteria=())
        # One active source maps the same label to two concepts.
        self.concept("Ambiguous Data Role")
        self.concept("Ambiguous Data Role")

    def test_01_non_senior_opportunities_remain_in_results(self):
        self.assertIn("NON_SENIOR Data Analyst", self.feed_titles())

    def test_02_uncertain_seniority_opportunities_remain_in_results(self):
        self.assertIn("UNCERTAIN Review Clerk", self.feed_titles())

    def test_03_obvious_senior_never_enters_the_feed(self):
        self.assertNotIn("Chief Finance Officer", self.feed_titles())

    def test_06_zero_score_opportunity_remains_visible(self):
        rows = self.feed_rows()
        zero = next(
            row for row in rows
            if row["opportunity"].title == "Zero Evidence Qqq Role")
        self.assertEqual(zero["score"], 0)

    def test_07_taxonomy_ambiguity_never_removes_a_job(self):
        context = build_taxonomy_context(
            {"occupation": ["Ambiguous Data Role"]})
        self.assertEqual(
            context.resolve("Ambiguous Data Role", ("occupation",)), ())
        self.assertIn("Ambiguous Data Role", self.feed_titles())

    def test_08_unresolved_taxonomy_job_remains_visible(self):
        self.assertIn("Zzz Unmapped Qqq Role", self.feed_titles())

    def test_41_no_target_profile_sees_everything_ranked_at_tier_zero(self):
        rows = self.feed_rows()
        titles = [row["opportunity"].title for row in rows]
        self.assertEqual(len(rows), 5)
        self.assertNotIn("Chief Finance Officer", titles)
        # Every career tier is 0 for a profile with no saved targets.
        self.assertEqual(
            {row["career_direction_tier_priority"] for row in rows}, {0})

class CareerTierRankingTests(TestCase):
    """Spec items 10-14: the mandatory final ranking tuple."""

    def item(self, tier, band, **overrides):
        values = {
            "career_direction_tier_priority": tier,
            "recommendation_band": band,
            "recommendation_priority": BAND_PRIORITY[band],
            "required_matched_important_category_count": 0,
            "required_important_coverage": 0,
            "matched_important_category_count": 0,
            "evidence_strength_priority": 0,
            "career_direction_priority": 0,
            "preferred_important_coverage": 0,
            "preference_fit": 0,
            "score": 0,
            "scope_priority": 1,
            "gate_gap_count": 0,
            "foundation_gap_count": 0,
            "strongest_important_match_priority": 0,
            "discovery_priority": 0,
        }
        values.update(overrides)
        return values

    def test_10_11_12_career_tier_is_the_outer_sort_key(self):
        items = [self.item(0, "A"), self.item(3, "A"),
                 self.item(1, "A"), self.item(2, "A")]
        ordered = sorted(items, key=_ranking_key, reverse=True)
        self.assertEqual(
            [row["career_direction_tier_priority"] for row in ordered],
            [3, 2, 1, 0])

    def test_13_career_tier_outranks_the_a_to_f_band(self):
        tier3_weak = self.item(3, "F")
        tier2_strong = self.item(2, "A")
        ordered = sorted(
            [tier2_strong, tier3_weak], key=_ranking_key, reverse=True)
        self.assertIs(ordered[0], tier3_weak)

    def test_14_within_one_tier_bands_rank_a_b_c_d_e_f(self):
        items = [self.item(0, band) for band in ("A", "B", "C", "D", "E", "F")]
        ordered = sorted(items, key=_ranking_key, reverse=True)
        self.assertEqual(
            [row["recommendation_band"] for row in ordered],
            ["A", "B", "C", "D", "E", "F"])


class RecommendationBandTests(RuntimeFeedCase):
    """Spec items 15-23 (and 9): A-F classification, visibility preserved."""

    @staticmethod
    def fake_result(**overrides):
        values = dict(
            strength="High", found_cats=2, coverage=100, matched_cats=2,
            comparable=2, important_found=2, important_related=0,
            supporting_found=0, major_gaps=0, critical_unknowns=0,
            source_completeness="source_evidenced")
        values.update(overrides)
        summary = SimpleNamespace(
            evidence_strength=values["strength"],
            found_important_category_count=values["found_cats"],
            required_important_coverage=values["coverage"],
            matched_important_category_count=values["matched_cats"],
            important_comparable_count=values["comparable"],
            important_found_count=values["important_found"],
            important_related_count=values["important_related"],
            supporting_found_count=values["supporting_found"],
            major_gap_count=values["major_gaps"],
            critical_unknown_count=values["critical_unknowns"])
        return SimpleNamespace(
            summary=summary,
            opportunity=SimpleNamespace(
                source_completeness=values["source_completeness"]),
            preference_results=())

    def band_of(self, job):
        return _recommendation_decision(
            match_profile_to_opportunity(self.profile, job)).final_band

    def test_15_band_a_requires_direct_found_breadth(self):
        ProfileSkill.objects.create(
            profile=self.profile, entered_name="Python")
        Certification.objects.create(profile=self.profile, title="PMP")
        job = self.job("Evidence Breadth Role", criteria=[
            criterion("skill-1", "SKILL", "Python"),
            criterion("cert-1", "CERTIFICATION", "PMP"),
        ])
        result = match_profile_to_opportunity(self.profile, job)
        self.assertEqual(
            _recommendation_decision(result).final_band, "A")
        self.assertGreaterEqual(
            result.summary.found_important_category_count, 2)
        # RELATED-only breadth must never earn band A.
        related_only = self.fake_result(
            strength="High", found_cats=1, coverage=100, matched_cats=2)
        self.assertNotEqual(
            _recommendation_decision(related_only).final_band, "A")

    def test_16_band_b_needs_breadth_with_at_least_one_direct_found(self):
        ProfileSkill.objects.create(
            profile=self.profile, entered_name="Python")
        Certification.objects.create(profile=self.profile, title="PMP")
        job = self.job("Good Supported Role", criteria=[
            criterion("skill-1", "SKILL", "Python"),
            criterion("skill-2", "SKILL", "COBOL"),
            criterion("cert-1", "CERTIFICATION", "PMP"),
        ])
        self.assertEqual(self.band_of(job), "B")

    def test_17_exact_career_without_criteria_becomes_c(self):
        self.target("Sparse Exact Role")
        job = self.job("Sparse Exact Role", criteria=())
        self.assertEqual(self.band_of(job), "C")

    def test_18_career_aligned_with_positive_evidence_becomes_c(self):
        self.target("Aligned Evidence Role")
        ProfileSkill.objects.create(
            profile=self.profile, entered_name="Python")
        job = self.job("Aligned Evidence Role", criteria=[
            criterion("skill-1", "SKILL", "Python"),
        ])
        self.assertEqual(self.band_of(job), "C")

    def test_19_thin_signal_becomes_d(self):
        ProfileSkill.objects.create(
            profile=self.profile, entered_name="Python")
        job = self.job("Thin Support Role", criteria=[
            criterion("skill-1", "SKILL", "Python",
                      significance="SUPPORTING"),
        ])
        self.assertEqual(self.band_of(job), "D")

    def test_20_positive_band_plus_major_gap_becomes_e(self):
        self.target("Blocked Exact Role")
        ProfileSkill.objects.create(
            profile=self.profile, entered_name="Python")
        job = self.job("Blocked Exact Role", criteria=[
            criterion("skill-1", "SKILL", "Python"),
            criterion("cert-1", "CERTIFICATION", "PMP",
                      significance="CRUCIAL"),
        ])
        self.assertEqual(self.band_of(job), "E")
        # Spec item 9: the major-gap job stays visible in the feed.  Neither
        # taxonomy has any data for this title, so the identical title is title
        # resemblance rather than confirmed occupation identity: the job stays
        # discoverable in the clearly-labelled discovery area, and it is never
        # presented as a confirmed match.
        # The personalised areas are read from stored v2 admissions only, so the
        # generation has to exist before the page can show anything.
        build_profile_generation(self.profile)
        rows = {row["opportunity"].title: row
                for row in self.discovery_rows()}
        self.assertIn("Blocked Exact Role", rows)
        card = rows["Blocked Exact Role"]
        self.assertEqual(card["recommendation_band"], "E")
        self.assertFalse(card["is_confirmed_relationship"])
        self.assertEqual(card["admission_class"], "TITLE_MATCH")
        self.assertEqual(card["discovery_label"], "Title match only")

    def test_21_provisional_d_or_f_with_major_gap_stays_f(self):
        ProfileSkill.objects.create(
            profile=self.profile, entered_name="Python")
        WorkExperience.objects.create(
            profile=self.profile, title="Junior Analyst",
            start_date=date(2023, 1, 1), end_date=date(2024, 1, 1))
        job = self.job("No Experience Foundation Role", criteria=[
            criterion("skill-1", "SKILL", "Python",
                      significance="SUPPORTING"),
            criterion("exp-1", "EXPERIENCE", "5 years experience",
                      significance="CRUCIAL",
                      experience={"minimum_months": 60,
                                  "context": "context_free"}),
        ])
        self.assertEqual(self.band_of(job), "F")
        rows = {row["opportunity"].title: row for row in self.feed_rows()}
        self.assertIn("No Experience Foundation Role", rows)
        self.assertEqual(
            rows["No Experience Foundation Role"]["recommendation_band"],
            "F")

    def test_22_partial_source_caps_a_or_b_at_c(self):
        self.assertEqual(
            _recommendation_decision(self.fake_result()).final_band, "A")
        capped = _recommendation_decision(self.fake_result(
            source_completeness="partial_text"))
        self.assertEqual(capped.provisional_band, "A")
        self.assertEqual(capped.final_band, "C")

    def test_23_required_crucial_unknown_caps_a_or_b_at_c(self):
        capped = _recommendation_decision(
            self.fake_result(critical_unknowns=1))
        self.assertEqual(capped.provisional_band, "A")
        self.assertEqual(capped.final_band, "C")


class ScoringSemanticsTests(TestCase):
    """Spec items 24-25: status scores and UNKNOWN exclusion."""

    @staticmethod
    def row(status, *, significance=EvidenceSignificance.CORE):
        return CriterionResult(
            key="k", label="Skill", evidence_type=EvidenceType.SKILL,
            importance=CriterionImportance.REQUIRED, status=status,
            criterion="c", profile_evidence=(), explanation="",
            significance=significance)

    def test_24_unknown_is_excluded_from_the_scoring_denominator(self):
        fits, _counts = _category_fit(
            [self.row(MatchStatus.FOUND),
             self.row(MatchStatus.UNKNOWN)],
            CriterionImportance.REQUIRED)
        # UNKNOWN leaves the denominator: one FOUND is a full 1.0 fit,
        # not diluted to 0.5.
        self.assertEqual(fits[MatchCategory.SKILL], 1.0)

    def test_25_status_scores_are_one_half_and_zero(self):
        self.assertEqual(STATUS_SCORE[MatchStatus.FOUND], 1.0)
        self.assertEqual(STATUS_SCORE[MatchStatus.RELATED], 0.5)
        self.assertEqual(
            STATUS_SCORE[MatchStatus.NOT_FOUND_ON_PROFILE], 0.0)
        self.assertNotIn(MatchStatus.UNKNOWN, STATUS_SCORE)

class TaxonomyEvidenceTests(RuntimeFeedCase):
    """Spec items 26-36: deterministic, bounded OFO/ESCO evidence rules."""

    def target_status(self, job):
        result = match_profile_to_opportunity(self.profile, job)
        row = next(
            item for item in result.preference_results
            if item.evidence_type == EvidenceType.TARGET_ROLE)
        return row.status, result

    def test_26_canonical_taxonomy_identity_produces_found(self):
        concept = self.concept("Health Data Analyst")
        self.label(concept, "Clinical Data Specialist", "alternative")
        self.target("Clinical Data Specialist")
        status, _result = self.target_status(
            self.job("Health Data Analyst"))
        self.assertEqual(status, MatchStatus.FOUND)

    def test_27_ofo_specialisation_label_produces_related_not_found(self):
        concept = self.concept("Pharmacist")
        self.label(concept, "Pharmacist Residency", "specialisation")
        self.target("Pharmacist")
        status, _result = self.target_status(
            self.job("Pharmacist Residency"))
        self.assertEqual(status, MatchStatus.RELATED)

    def test_28_direct_one_hop_relation_produces_related(self):
        narrower = self.concept("Neurology Practice")
        broader = self.concept("Physician")
        self.relation(narrower, broader)
        self.target("Neurology Practice")
        status, _result = self.target_status(self.job("Physician"))
        self.assertEqual(status, MatchStatus.RELATED)

    def test_29_two_hop_relation_is_not_established(self):
        first = self.concept("Clinical Psychology Focus")
        middle = self.concept("Mental Health Practitioner")
        outer = self.concept("Health Practitioner")
        self.relation(first, middle)
        self.relation(middle, outer)
        self.target("Clinical Psychology Focus")
        status, _result = self.target_status(
            self.job("Health Practitioner"))
        self.assertNotIn(
            status, (MatchStatus.FOUND, MatchStatus.RELATED))

    def test_30_ambiguous_source_label_abstains(self):
        self.concept("Ambiguous Analyst Role")
        self.concept("Ambiguous Analyst Role")
        context = build_taxonomy_context(
            {"occupation": ["Ambiguous Analyst Role"]})
        self.assertEqual(
            context.resolve("Ambiguous Analyst Role", ("occupation",)), ())

    def test_31_32_shared_isco_group_is_ranking_adjacency_only(self):
        self.concept(
            "Signal Processing Engineer",
            metadata={"isco_group": "2241"})
        self.concept(
            "Radio Frequency Engineer", metadata={"isco_group": "2241"})
        self.target("Signal Processing Engineer")
        job = self.job("Radio Frequency Engineer")
        result = match_profile_to_opportunity(self.profile, job)
        status = next(
            item.status for item in result.preference_results
            if item.evidence_type == EvidenceType.TARGET_ROLE)
        # 32: adjacency never becomes career evidence or employer fit.
        self.assertNotIn(status, (MatchStatus.FOUND, MatchStatus.RELATED))
        # 31: it still produces the tier-1 adjacency ranking signal.
        context = build_taxonomy_context_for_run(
            result.user, (result.opportunity,))
        tier, _label = _career_direction_ranking_tier(
            result.user, result.opportunity, result,
            taxonomy_context=context)
        self.assertEqual(tier, 1)

    def test_33_candidate_narrower_skill_supports_broader_requirement(self):
        broad = self.concept("Programming", kind="skill")
        narrow = self.concept("Python", kind="skill")
        self.relation(narrow, broad)
        ProfileSkill.objects.create(
            profile=self.profile, entered_name="Python")
        job = self.job("Programming Duty Role", criteria=[
            criterion("skill-1", "SKILL", "Programming"),
        ])
        result = match_profile_to_opportunity(self.profile, job)
        skill = next(
            item for item in result.criteria
            if item.evidence_type == EvidenceType.SKILL)
        self.assertEqual(skill.status, MatchStatus.RELATED)

    def test_34_broad_skill_does_not_prove_narrower_specialist_skill(self):
        broad = self.concept("Programming", kind="skill")
        narrow = self.concept("Python", kind="skill")
        self.relation(narrow, broad)
        ProfileSkill.objects.create(
            profile=self.profile, entered_name="Programming")
        job = self.job("Specialist Python Role", criteria=[
            criterion("skill-1", "SKILL", "Python"),
        ])
        result = match_profile_to_opportunity(self.profile, job)
        skill = next(
            item for item in result.criteria
            if item.evidence_type == EvidenceType.SKILL)
        self.assertNotIn(
            skill.status, (MatchStatus.FOUND, MatchStatus.RELATED))

    def test_35_context_experience_needs_related_dated_duration(self):
        self.concept("Business Analyst")
        WorkExperience.objects.create(
            profile=self.profile, title="Business Analyst",
            start_date=date(2020, 1, 1), end_date=date(2023, 1, 1))
        job = self.job("Project Analysis Role", criteria=[
            criterion("exp-1", "EXPERIENCE", "3 years business analysis",
                      experience={"minimum_months": 36,
                                  "context": "business analyst"}),
        ])
        result = match_profile_to_opportunity(self.profile, job)
        experience = next(
            item for item in result.criteria
            if item.evidence_type == EvidenceType.EXPERIENCE)
        self.assertEqual(experience.status, MatchStatus.RELATED)

    def test_36_duration_shortfall_is_not_rescued_by_taxonomy(self):
        self.concept("Business Analyst")
        WorkExperience.objects.create(
            profile=self.profile, title="Business Analyst",
            start_date=date(2023, 1, 1), end_date=date(2024, 1, 1))
        job = self.job("Long Experienced Analyst Role", criteria=[
            criterion("exp-1", "EXPERIENCE", "5 years experience",
                      experience={"minimum_months": 60,
                                  "context": "context_free"}),
        ])
        result = match_profile_to_opportunity(self.profile, job)
        experience = next(
            item for item in result.criteria
            if item.evidence_type == EvidenceType.EXPERIENCE)
        self.assertEqual(
            experience.status, MatchStatus.NOT_FOUND_ON_PROFILE)

    def test_36b_vague_context_stays_unknown_despite_enough_duration(self):
        # An explicit but non-resolvable context cannot become a taxonomy
        # target: duration is met, but the matcher must retain UNKNOWN
        # rather than invent an occupation.
        WorkExperience.objects.create(
            profile=self.profile, title="Business Analyst",
            start_date=date(2020, 1, 1), end_date=date(2023, 1, 1))
        job = self.job("Vague Context Role", criteria=[
            criterion("exp-1", "EXPERIENCE", "3 years in a relevant field",
                      experience={"minimum_months": 36,
                                  "context": "unspecified"}),
        ])
        result = match_profile_to_opportunity(self.profile, job)
        experience = next(
            item for item in result.criteria
            if item.evidence_type == EvidenceType.EXPERIENCE)
        self.assertEqual(experience.status, MatchStatus.UNKNOWN)

    def test_36c_unresolvable_context_stays_unknown_despite_enough_duration(self):
        # A real-looking phrase that resolves to no trusted OFO/ESCO
        # occupation must also retain UNKNOWN.
        WorkExperience.objects.create(
            profile=self.profile, title="Business Analyst",
            start_date=date(2020, 1, 1), end_date=date(2023, 1, 1))
        job = self.job("Unresolvable Context Role", criteria=[
            criterion("exp-1", "EXPERIENCE",
                      "3 years moon colony operations experience",
                      experience={"minimum_months": 36,
                                  "context": "moon colony operations"}),
        ])
        result = match_profile_to_opportunity(self.profile, job)
        experience = next(
            item for item in result.criteria
            if item.evidence_type == EvidenceType.EXPERIENCE)
        self.assertEqual(experience.status, MatchStatus.UNKNOWN)

# __PART4__






