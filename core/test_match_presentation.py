from types import SimpleNamespace
from unittest.mock import patch
from unittest import skip

from django.contrib.auth.models import User
from django.test import SimpleTestCase, TestCase
from django.urls import reverse

from core.matching import (
    CriterionImportance, CriterionResult, EvidenceSignificance,
    EvidenceType, MatchStatus, get_matched_opportunities,
)
from core.models import CVtip, Education, Opportunity, Profile, ProfileSkill, Skill
from core.services.match_presentation import (
    card_match_reason, cv_guidance, feed_card_presentation, profile_evidence_presentation,
)


def criterion(key, text, status, *, importance=CriterionImportance.REQUIRED,
              evidence_type=EvidenceType.SKILL, significance=EvidenceSignificance.CORE,
              explanation="", profile_evidence=()):
    return CriterionResult(
        key=key, label=evidence_type.value.title(), evidence_type=evidence_type,
        importance=importance, status=status, criterion=text,
        profile_evidence=profile_evidence, explanation=explanation,
        significance=significance,
    )


class MatchPresentationUnitTests(SimpleTestCase):
    def test_card_reason_is_concise_and_does_not_mutate_fit_or_discovery(self):
        sql = criterion("sql", "SQL", MatchStatus.FOUND, profile_evidence=("SQL",))
        match = {
            "criteria": (sql,), "score": 73, "recommendation_band": "C",
            "discovery_provenance": (
                SimpleNamespace(kind="exact_desired_career"),
            ),
        }

        reason = card_match_reason(match)

        self.assertEqual(
            reason,
            "Your profile has evidence supporting SQL for this role.",
        )
        self.assertLessEqual(len(reason), 140)
        self.assertEqual((match["score"], match["recommendation_band"]), (73, "C"))
        self.assertEqual(match["criteria"], (sql,))
        self.assertEqual(match["discovery_provenance"][0].kind, "exact_desired_career")

    def test_card_does_not_treat_intent_or_preferences_as_fit(self):
        preference = criterion(
            "location", "Cape Town", MatchStatus.FOUND,
            importance=CriterionImportance.PREFERENCE,
            evidence_type=EvidenceType.LOCATION,
        )
        reason = card_match_reason({
            "criteria": (preference,),
            "discovery_provenance": (
                SimpleNamespace(kind="trusted_related_occupation"),
            ),
        })
        self.assertIn("limited comparable role requirements", reason)
        self.assertNotIn("supports Cape Town", reason)
        self.assertLessEqual(len(reason), 140)
        card = feed_card_presentation({
            "discovery_provenance": (SimpleNamespace(kind="trusted_related_occupation"),),
            "recommendation_band": "F", "required_results": (),
        })
        self.assertEqual(card["discovery_label"], "Related career")
        self.assertEqual(card["fit_label"], "Limited Current Fit")

    def test_evidence_counts_keep_missing_unknown_and_preferred_distinct(self):
        shared = (
            "CareerGPS preserved this experience wording, but no structured minimum "
            "duration is available for a safe comparison."
        )
        required = (
            criterion("sql", "SQL", MatchStatus.FOUND, profile_evidence=("SQL",)),
            criterion("years", "6+ years quality engineering", MatchStatus.NOT_FOUND_ON_PROFILE,
                      evidence_type=EvidenceType.EXPERIENCE,
                      significance=EvidenceSignificance.CRUCIAL,
                      explanation="No dated experience found."),
            criterion("lead", "Technical leadership", MatchStatus.UNKNOWN,
                      evidence_type=EvidenceType.EXPERIENCE, explanation=shared),
            criterion("automation", "Automation frameworks", MatchStatus.UNKNOWN,
                      evidence_type=EvidenceType.EXPERIENCE, explanation=shared),
        )
        preferred = (
            criterion("junit", "JUnit 5", MatchStatus.NOT_FOUND_ON_PROFILE,
                      importance=CriterionImportance.PREFERRED),
            criterion("payments", "Payments industry", MatchStatus.UNKNOWN,
                      importance=CriterionImportance.PREFERRED,
                      evidence_type=EvidenceType.EXPERIENCE),
        )
        preferences = (
            criterion("location", "Cape Town", MatchStatus.UNKNOWN,
                      importance=CriterionImportance.PREFERENCE,
                      evidence_type=EvidenceType.LOCATION),
        )
        result = SimpleNamespace(required_results=required, preferred_results=preferred,
                                 preference_results=preferences)

        presented = profile_evidence_presentation(result)

        self.assertIn("1 of 4 required criteria", presented["summary"])
        self.assertIn("1 major required gap", presented["summary"])
        self.assertEqual((presented["required"].missing_count,
                          presented["required"].unknown_count,
                          presented["required"].major_count), (1, 2, 1))
        self.assertEqual((presented["preferred"].missing_count,
                          presented["preferred"].unknown_count,
                          presented["preferred"].major_count), (1, 1, 0))
        self.assertEqual(presented["required"].shared_unknown_explanations, (shared,))
        self.assertFalse(presented["required"].rows[2].has_own_detail)
        self.assertEqual(presented["required"].rows[1].status_text, "Major gap")
        self.assertEqual(presented["required"].rows[2].status_text, "Unable to verify")
        self.assertEqual(presented["preferences"].rows[0].status_text, "Not confirmed")

    def test_cv_guidance_uses_saved_evidence_or_safe_fallback(self):
        found = criterion("sql", "SQL", MatchStatus.FOUND,
                          profile_evidence=("Confirmed SQL skill",))
        missing = criterion("excel", "Excel", MatchStatus.NOT_FOUND_ON_PROFILE)
        contextual = cv_guidance(SimpleNamespace(required_results=(found, missing)))
        fallback = cv_guidance(SimpleNamespace(required_results=(missing,)))

        self.assertIn("SQL", contextual[0].content)
        self.assertNotIn("Excel", contextual[0].content)
        self.assertIn("only when you can support", contextual[0].content)
        self.assertNotIn("SQL", fallback[0].content)
        self.assertIn("Do not add guessed numbers", fallback[0].content)


@skip('Retired legacy in-request MatchResult presentation; candidate pages now render materialized clean-revision evaluations.')
class MatchPresentationViewTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("presentation-user", password="test-password")
        self.profile = Profile.objects.create(
            user=self.user, qualification="", institution="", location="",
        )
        self.opportunity = Opportunity.objects.create(
            title="Data Analyst", company="Example Co", opportunity_type="entry_level",
            location="Cape Town", description="A source-backed listing.",
            qualification_minimum="BSc Computer Science",
            primary_apply_url="https://example.test/apply",
        )
        Education.objects.create(profile=self.profile, qualification="BSc Computer Science")
        sql = Skill.objects.create(name="SQL")
        python = Skill.objects.create(name="Python")
        self.opportunity.required_skills.add(sql, python)
        ProfileSkill.objects.create(profile=self.profile, entered_name="SQL")
        self.client.force_login(self.user)

    def test_dashboard_and_catalogue_show_short_reason_with_full_detail_preserved(self):
        before = get_matched_opportunities(self.profile)[0]
        for route, context_key in (("dashboard", "top_matches"),
                                   ("opportunities", "results")):
            with self.subTest(route=route):
                response = self.client.get(reverse(route))
                self.assertEqual(response.status_code, 200)
                item = response.context[context_key][0]
                self.assertLessEqual(len(item["card_reason"]), 140)
                self.assertIn("SQL", item["card_reason"])
                self.assertContains(response, item["card_reason"])
                self.assertNotContains(response, before["why_text"])
                self.assertEqual(
                    (item["score"], item["recommendation_band"],
                     item["recommendation_priority"]),
                    (before["score"], before["recommendation_band"],
                     before["recommendation_priority"]),
                )

        detail = self.client.get(reverse("opportunity_detail", args=[self.opportunity.pk]))
        self.assertContains(detail, before["why_text"])
        self.assertContains(detail, "How CareerGPS compares evidence")

    def test_detail_never_renders_unsafe_canned_cv_examples(self):
        CVtip.objects.create(
            opportunity_type="general", title="Unsafe canned example",
            content="Reduced customer churn by 18%", example="R380,000 saved annually",
        )
        detail = self.client.get(reverse("opportunity_detail", args=[self.opportunity.pk]))

        self.assertContains(detail, "Show how you used SQL")
        self.assertNotContains(detail, "Reduced customer churn")
        self.assertNotContains(detail, "R380,000")
        self.assertNotContains(detail, "Example: For example:")

    def test_detail_shows_shared_unknown_reason_once_and_keeps_details(self):
        shared = "No structured minimum duration is available for a safe comparison."
        required = (
            criterion("years", "6+ years engineering", MatchStatus.NOT_FOUND_ON_PROFILE,
                      evidence_type=EvidenceType.EXPERIENCE,
                      significance=EvidenceSignificance.CRUCIAL,
                      explanation="The required duration was not found."),
            criterion("lead", "Technical leadership", MatchStatus.UNKNOWN,
                      evidence_type=EvidenceType.EXPERIENCE, explanation=shared),
            criterion("framework", "Automation frameworks", MatchStatus.UNKNOWN,
                      evidence_type=EvidenceType.EXPERIENCE, explanation=shared),
        )
        preferred = (
            criterion("junit", "JUnit 5", MatchStatus.NOT_FOUND_ON_PROFILE,
                      importance=CriterionImportance.PREFERRED),
        )
        preferences = (
            criterion("location", "Cape Town", MatchStatus.UNKNOWN,
                      importance=CriterionImportance.PREFERENCE,
                      evidence_type=EvidenceType.LOCATION),
        )
        result = SimpleNamespace(
            criteria=required + preferred + preferences,
            required_results=required, preferred_results=preferred,
            preference_results=preferences,
            summary=SimpleNamespace(score=25, label="Explore",
                                    explanation="Full technical matching explanation.",
                                    major_gap_count=1),
        )

        with patch("core.views.personalized_opportunities", return_value=[{
            "match_result": result, "discovery_provenance": (),
            "recommendation_band": "E", "required_results": required,
        }]):
            response = self.client.get(reverse("opportunity_detail", args=[self.opportunity.pk]))

        html = response.content.decode()
        self.assertEqual(html.count(shared), 1)
        self.assertContains(response, "Not found: 1")
        self.assertContains(response, "Unable to verify: 2")
        self.assertContains(response, "Major gaps: 1")
        self.assertContains(response, "Major gap")
        self.assertContains(response, "Helpful signals, not mandatory requirements")
        self.assertContains(response, "Not confirmed")
        self.assertContains(response, "Preferences can affect ranking or practical fit")
        self.assertContains(response, "Full technical matching explanation.")
        self.assertContains(response, "View evidence and reasoning")
