"""Career Intelligence comparison contract tests.

Career Intelligence measures the confirmed profile against current, comparable
employer requirements. It builds its own analytical cohort and never reads a
personalised feed generation, so these tests pin that independence, the
per-target resolution states, the 3-distinct-job recurring threshold, OR-group
and duplicate-criterion counting, profile evidence semantics, coverage limits,
source-job links and list counts.
"""
from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from core.models import (
    CareerPreference, EvaluationRecomputeTask, OpportunityCleanAttempt, Profile,
    ProfileFeedState, ProfileSkill, TaxonomyConcept, TaxonomyLabel, TaxonomyRelease,
)
from core.services.career_analysis import build_career_analysis
from core.taxonomies.normalization import normalize_term
from core.test_support import create_professional_opportunity


def criterion(criterion_id, kind, value, *, level="REQUIRED",
              significance="CORE", group=None):
    """One criterion in the authoritative clean-payload shape."""
    return {
        "criterion_id": criterion_id,
        "criterion_type": kind,
        "normalized_value": str(value).lower(),
        "raw_value": str(value),
        "requirement_level": level,
        "significance": significance,
        "source_section": "raw_content",
        "requirement_group": group,
        "evidence": [{"text": str(value)}],
        "qualification": None,
        "experience": None,
        "language": None,
    }


def labels_of(rows):
    return {row['label'] for row in rows}


class CareerAnalysisCase(TestCase):
    def setUp(self):
        self.releases = {
            source: TaxonomyRelease.objects.create(
                source=source, version=f"ci-{source}", is_active=True)
            for source in ("ofo", "esco")
        }
        self.profile = Profile.objects.create(
            user=User.objects.create_user("ci-candidate", password="password"))
        self.client.force_login(self.profile.user)

    def concept(self, label, *, source="ofo"):
        concept = TaxonomyConcept.objects.create(
            release=self.releases[source], kind="occupation",
            external_id=f"{source}:{TaxonomyConcept.objects.count()}",
            preferred_label=label, normalized_label=normalize_term(label),
            metadata={})
        TaxonomyLabel.objects.create(
            concept=concept, label=label, normalized_label=normalize_term(label),
            label_type="preferred")
        return concept

    def alias(self, concept, text):
        return TaxonomyLabel.objects.create(
            concept=concept, label=text, normalized_label=normalize_term(text),
            label_type="alternative")

    def skills(self, *names):
        for name in names:
            ProfileSkill.objects.create(profile=self.profile, entered_name=name)

    def targets(self, *roles):
        CareerPreference.objects.update_or_create(
            profile=self.profile, defaults={"target_roles": ", ".join(roles)})

    def job(self, title, criteria=(), *, company="Example employer"):
        return create_professional_opportunity(
            title=title, company=company, criteria=list(criteria))

    def seed_analysis(self):
        """Run the stored occupational pipeline this page now reads.

        Career Intelligence serves stored evidence only, so a fixture that
        wants a comparison must produce it the way production does: mirror the
        target containers, resolve each target, and assess every eligible pair.
        Nothing in this helper is reachable from the page itself.
        """
        from core.services.career_analysis import _eligible_revisions
        from core.services.occupational_resolution import (
            maintain_target_resolution_containers, resolve_target_sources,
        )
        from core.services.relationship_aggregation import assess_target_vacancy
        preference = CareerPreference.objects.filter(
            profile=self.profile).first()
        if preference is None:
            return
        targets = maintain_target_resolution_containers(preference)
        for target in targets:
            resolve_target_sources(target)
        revisions = _eligible_revisions()
        for target in targets:
            for revision in revisions:
                assess_target_vacancy(target, revision)

    def analysis(self):
        self.seed_analysis()
        return build_career_analysis(self.profile)

    def panel(self, ordinal=0):
        return self.analysis()['authored_targets'][ordinal]

class CohortIndependenceTests(CareerAnalysisCase):
    """The cohort comes from the catalogue, never from a feed generation."""

    def test_market_is_measured_with_no_feed_generation_at_all(self):
        self.concept("Ledger Analyst")
        self.targets("Ledger Analyst")
        for _ in range(3):
            self.job("Ledger Analyst", [criterion("c1", "SKILL", "Bank reconciliation")])

        self.assertFalse(ProfileFeedState.objects.filter(profile=self.profile).exists())
        panel = self.panel()
        self.assertEqual(panel['state'], 'compared')
        self.assertEqual(panel['job_count'], 3)
        self.assertEqual(panel['analysable_job_count'], 3)

    def test_unrelated_jobs_are_not_counted_as_demand(self):
        self.concept("Ledger Analyst")
        self.targets("Ledger Analyst")
        self.job("Ledger Analyst", [criterion("c1", "SKILL", "Bank reconciliation")])
        self.job("Distant Unrelated Qqq Role",
                 [criterion("c1", "SKILL", "Bank reconciliation")])

        panel = self.panel()
        self.assertEqual(panel['job_count'], 1)
        self.assertEqual([row['title'] for row in panel['jobs']], ['Ledger Analyst'])


class PerTargetResolutionTests(CareerAnalysisCase):
    """Each saved target keeps its own state; none is silently discarded."""

    def setUp(self):
        super().setUp()
        self.concept("Ledger Analyst")
        self.concept("Blurred Analyst", source="esco")
        self.concept("Blurred Analyst", source="esco")
        self.targets("Ledger Analyst", "Blurred Analyst", "Zzz Unmapped Qqq")
        for _ in range(3):
            self.job("Ledger Analyst", [criterion("c1", "SKILL", "Bank reconciliation")])

    def test_three_targets_keep_three_distinct_states(self):
        analysis = self.analysis()
        self.assertTrue(analysis['has_targets'])
        self.assertEqual(len(analysis['authored_targets']), 3)
        self.assertEqual(
            [(row['label'], row['resolution'], row['state'])
             for row in analysis['authored_targets']],
            [('Ledger Analyst', 'resolved', 'compared'),
             # Several retained results are several occupation connections,
             # never an ambiguous state to hold the target back.
             ('Blurred Analyst', 'resolved', 'no_jobs'),
             ('Zzz Unmapped Qqq', 'unmapped', 'needs_clarification')])

    def test_ambiguous_target_explains_itself_and_compares_nothing(self):
        panel = self.panel(1)
        self.assertEqual(panel['job_count'], 0)
        # Two retained occupations are disclosed, and the comparison runs over
        # both; the empty cohort here is because no job matched either.
        self.assertEqual(panel['occupation_count'], 2)
        self.assertIn('matches 2 occupations', panel['state_note'])

    def test_unmapped_target_explains_itself_and_compares_nothing(self):
        panel = self.panel(2)
        self.assertEqual(panel['job_count'], 0)
        self.assertIn('does not match a single occupation', panel['state_note'])

class RecurringThresholdTests(CareerAnalysisCase):
    """A gap needs the requirement in at least three distinct comparable jobs."""

    def setUp(self):
        super().setUp()
        self.ledger = self.concept("Ledger Analyst")
        self.targets("Ledger Analyst")
        self.skills("Bank reconciliation")

    def add_jobs(self, count, criteria):
        for index in range(count):
            self.alias(self.ledger, f"Ledger Analyst {index}")
            self.job(f"Ledger Analyst {index}", criteria)

    def test_two_jobs_with_the_requirement_is_not_called_recurring(self):
        self.add_jobs(2, [criterion("c1", "SKILL", "IFRS reporting")])
        panel = self.panel()
        self.assertEqual(panel['state'], 'insufficient_coverage')
        self.assertEqual(panel['required_not_shown'], [])
        self.assertEqual(labels_of(panel['less_common']), {'IFRS reporting'})

    def test_three_jobs_with_the_requirement_is_reported_as_a_gap(self):
        self.add_jobs(3, [criterion("c1", "SKILL", "IFRS reporting")])
        panel = self.panel()
        self.assertEqual(panel['state'], 'compared')
        self.assertEqual(labels_of(panel['required_not_shown']), {'IFRS reporting'})
        gap = panel['required_not_shown'][0]
        self.assertEqual(gap['job_count'], 3)
        self.assertEqual(gap['total_jobs'], 3)
        self.assertEqual(gap['missing_count'], 3)
        self.assertEqual(len(gap['jobs']), 3)


class RequirementCountingTests(CareerAnalysisCase):
    """One requirement is counted at most once per distinct job."""

    def setUp(self):
        super().setUp()
        self.ledger = self.concept("Ledger Analyst")
        self.targets("Ledger Analyst")
        self.skills("Bank reconciliation")

    def add_jobs(self, count, criteria):
        for index in range(count):
            self.alias(self.ledger, f"Ledger Analyst {index}")
            self.job(f"Ledger Analyst {index}", criteria)

    def test_duplicate_criteria_in_one_job_count_once(self):
        self.add_jobs(3, [
            criterion("dup-a", "SKILL", "IFRS reporting"),
            criterion("dup-b", "SKILL", "IFRS Reporting"),
        ])
        panel = self.panel()
        self.assertEqual(labels_of(panel['required_not_shown']), {'IFRS reporting'})
        self.assertEqual(panel['required_not_shown'][0]['job_count'], 3)
        self.assertEqual(len(panel['required_not_shown'][0]['jobs']), 3)

    def test_or_group_counts_once_and_adopts_the_supported_member(self):
        group = {"group_id": "ledger-tool", "operator": "OR"}
        self.add_jobs(3, [
            criterion("or-a", "SKILL", "Bank reconciliation", group=group),
            criterion("or-b", "SKILL", "Ledger Pro", group=group),
        ])
        panel = self.panel()
        self.assertEqual(labels_of(panel['required_shown']), {'Bank reconciliation'})
        self.assertEqual(panel['required_shown'][0]['job_count'], 3)
        self.assertEqual(panel['required_shown'][0]['supported_count'], 3)
        self.assertNotIn('Ledger Pro', labels_of(panel['required_shown']))

class ProfileEvidenceSemanticsTests(CareerAnalysisCase):
    """Shown, not shown, and cannot confirm stay separate."""

    def setUp(self):
        super().setUp()
        ledger = self.concept("Ledger Analyst")
        self.targets("Ledger Analyst")
        self.skills("Bank reconciliation")
        for index in range(3):
            self.alias(ledger, f"Ledger Analyst {index}")
            self.job(f"Ledger Analyst {index}", [
                criterion(f"found-{index}", "SKILL", "Bank reconciliation"),
                criterion(f"absent-{index}", "SKILL", "IFRS reporting"),
                criterion(f"unknown-{index}", "CERTIFICATION", "CA(SA)"),
            ])

    def test_supported_absent_and_unknown_are_three_distinct_buckets(self):
        panel = self.panel()
        self.assertEqual(panel['state'], 'compared')
        self.assertEqual(labels_of(panel['required_shown']), {'Bank reconciliation'})
        self.assertEqual(labels_of(panel['required_not_shown']), {'IFRS reporting'})
        self.assertEqual(labels_of(panel['required_cannot_confirm']), {'CA(SA)'})

    def test_supported_item_names_the_profile_evidence(self):
        panel = self.panel()
        self.assertEqual(panel['required_shown'][0]['evidence'], ['Bank reconciliation'])


class CoverageAndLinkTests(CareerAnalysisCase):
    """Thin coverage is explained, and every claim links to its source jobs."""

    def setUp(self):
        super().setUp()
        self.ledger = self.concept("Ledger Analyst")
        self.targets("Ledger Analyst")
        self.skills("Bank reconciliation")

    def test_no_comparable_jobs_is_a_state_not_a_market_claim(self):
        self.job("Distant Unrelated Qqq Role", [])
        panel = self.panel()
        self.assertEqual(panel['state'], 'no_jobs')
        # Coverage was inconclusive, so the page must not claim there is no
        # market; it says the picture is incomplete instead.
        self.assertIn('could not yet decide', panel['state_note'])

    def test_comparable_jobs_without_structured_requirements(self):
        self.alias(self.ledger, "Ledger Analyst")
        self.job("Ledger Analyst", [])
        panel = self.panel()
        self.assertEqual(panel['state'], 'no_requirements')
        self.assertIn('do not carry structured requirements', panel['state_note'])

    def test_two_comparable_jobs_withhold_recurring_conclusions(self):
        for index in range(2):
            self.alias(self.ledger, f"Ledger Analyst {index}")
            self.job(f"Ledger Analyst {index}",
                     [criterion("c1", "SKILL", "IFRS reporting")])
        panel = self.panel()
        self.assertEqual(panel['state'], 'insufficient_coverage')
        self.assertEqual(panel['required_not_shown'], [])
        self.assertIn('at least 3', panel['state_note'])
        # The rendered candidate page explains this state in plain language
        # and never exposes internal comparison wording.
        response = self.client.get(reverse('career_intelligence'))
        self.assertEqual(response.status_code, 200)
        body = response.content.decode()
        self.assertIn('not yet enough to confirm what recurs', body)
        for token in ('ESCO', 'OFO', 'taxonomy', 'state_note',
                      'stored comparison', 'target x job', '{#'):
            self.assertNotIn(token, body)

    def test_title_only_evidence_is_disclosed_and_never_counted(self):
        """A retained label collision stays out of the comparable cohort.

        The target wording and the job title each keep two OFO occupations that
        share the bare label, so two label collisions were retained.  Neither
        confirmed the pair, so the relationship count stays empty, the job
        count stays empty, and the candidate page says the picture is
        incomplete instead of exposing taxonomy wording.
        """
        self.concept("Ledger Analyst")
        self.targets("Ledger Analyst")
        self.job("Ledger Analyst", [criterion("c1", "SKILL", "Bank reconciliation")])
        panel = self.panel()
        self.assertEqual(panel['title_only_evidence_count'], 2)
        self.assertEqual(panel['relationship_count'], 0)
        self.assertEqual(panel['job_count'], 0)
        self.assertEqual(
            [row['title'] for row in panel['title_only_jobs']],
            ['Ledger Analyst'])
        evidence = panel['title_only_jobs'][0]['evidence']
        self.assertEqual(len(evidence), 2)
        self.assertEqual({row['tier'] for row in evidence}, {'TITLE_ONLY'})
        self.assertEqual(
            {row['tier_label'] for row in evidence},
            {'Title-only evidence, not taxonomy-confirmed'})
        response = self.client.get(reverse('career_intelligence'))
        self.assertContains(
            response, 'could not establish that they belong to this career direction')
        self.assertNotContains(response, 'ESCO')
        self.assertNotContains(response, 'OFO')
        self.assertNotContains(response, 'taxonomy')
        # The candidate page keeps its always-visible sections; section
        # disclosure count is a presentation detail owned by the template.
        body = response.content.decode()
        self.assertNotIn('<details open', body)

    def test_a_pending_feed_task_never_disguises_a_real_market_state(self):
        """The page ignores the feed, so a feed rebuild is not a page state."""
        self.job("Distant Unrelated Qqq Role", [])
        EvaluationRecomputeTask.objects.create(
            profile=self.profile, task_version="feed-v2", state="PENDING")
        panel = self.panel()
        self.assertEqual(panel['state'], 'no_jobs')

    def test_unimported_clean_results_report_an_updating_picture(self):
        self.job("Distant Unrelated Qqq Role", [])
        OpportunityCleanAttempt.objects.create(status="received")
        panel = self.panel()
        self.assertEqual(panel['state'], 'updating')
        self.assertIn('not an empty market', panel['state_note'])

    def test_page_links_each_requirement_to_its_source_jobs(self):
        opportunities = []
        for index in range(3):
            self.alias(self.ledger, f"Ledger Analyst {index}")
            opportunities.append(self.job(
                f"Ledger Analyst {index}",
                [criterion("c1", "SKILL", "IFRS reporting")]))
        self.seed_analysis()
        response = self.client.get(reverse('career_intelligence'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "not currently shown in your confirmed profile")
        body = response.content.decode()
        for token in ('ESCO', 'OFO', 'taxonomy', 'resolver', 'matrix',
                      'stored comparison', 'relationship assessment', '{#'):
            self.assertNotIn(token, body)
        for opportunity in opportunities:
            self.assertContains(
                response, reverse('opportunity_detail', args=[opportunity.pk]))


class ProfileSummaryTests(CareerAnalysisCase):
    """A listed count must never disagree with the values rendered."""

    def test_eight_skills_are_all_listed_under_a_count_of_eight(self):
        names = [f"Skill number {index}" for index in range(1, 9)]
        self.skills(*names)
        bucket = next(
            row for row in self.analysis()['evidence']['buckets']
            if row['label'] == 'Skills')
        self.assertEqual(bucket['count'], 8)
        self.assertEqual(len(bucket['items']), 8)
        self.assertEqual(bucket['hidden'], 0)
        response = self.client.get(reverse('career_intelligence'))
        self.assertContains(response, '<small>(8)</small>')
        for name in names:
            self.assertContains(response, name)

    def test_a_shortened_skill_list_says_so_explicitly(self):
        names = [f"Skill number {index}" for index in range(1, 13)]
        self.skills(*names)
        bucket = next(
            row for row in self.analysis()['evidence']['buckets']
            if row['label'] == 'Skills')
        self.assertEqual(bucket['count'], 12)
        self.assertEqual(len(bucket['items']), 8)
        self.assertEqual(bucket['hidden'], 4)
        response = self.client.get(reverse('career_intelligence'))
        self.assertContains(response, '<small>(12)</small>')
        self.assertContains(response, 'Showing 8 of 12')

    def test_profile_summary_is_visible_with_no_saved_target(self):
        self.skills("Bank reconciliation")
        analysis = self.analysis()
        self.assertFalse(analysis['has_targets'])
        self.assertTrue(analysis['evidence']['buckets'])
        response = self.client.get(reverse('career_intelligence'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Bank reconciliation')
        self.assertContains(response, 'You have not saved a career direction yet')
