from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from django.urls import reverse

import importlib
from unittest import skip
from unittest.mock import patch

from django.apps import apps
from django.db import IntegrityError, connection
from django.test.utils import CaptureQueriesContext

from .matching import calculate_completion, get_matched_opportunities
from .models import (
    Achievement, CareerPreference, Certification, CVtip, Education, Interest, Language,
    Opportunity, Profile, ProfileInterest, ProfileLink, ProfileSkill, Project,
    SavedOpportunity, Skill, Volunteering, WorkExperience,
)
from .profile_projection import get_profile_projection
from .test_support import activate_clean_opportunity, create_current_evaluation
from .services.occupational_resolution import resolve_vacancy_sources
from .services.title_gate import evaluate_revision

PROFILE_SECTION_NAMES = [
    'education', 'experience', 'projects', 'skills', 'certifications',
    'achievements', 'volunteering', 'languages', 'interests', 'links',
    'career-preferences',
]


@override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend', EMAIL_HOST='smtp.test')
class RegistrationTests(TestCase):
    def test_registration_page_loads(self):
        response = self.client.get(reverse('register'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Create your account')

    def test_normal_registration_post_creates_inactive_user_and_profile(self):
        response = self.client.post(reverse('register'), {
            'first_name': 'New',
            'last_name': 'Candidate',
            'email': 'new-candidate@example.test',
            'password': 'safe-test-password',
            'confirm_password': 'safe-test-password',
            'terms_agreed': 'on',
            'privacy_acknowledged': 'on',
        })
        self.assertRedirects(response, reverse('verify_otp'))
        user = User.objects.get(username='new-candidate@example.test')
        self.assertFalse(user.is_active)
        self.assertTrue(Profile.objects.filter(user=user).exists())
        self.assertEqual(self.client.session['user_id'], user.id)
        self.assertNotIn('otp', self.client.session)
        self.assertIn('otp_hash', self.client.session)


class OpportunityDiscoveryTests(TestCase):
    def setUp(self):
        self.opportunity = Opportunity.objects.create(
            title="Software Internship", company="Example Co", opportunity_type="internship",
            location="Cape Town", description="A real database-backed listing.",
            primary_apply_url="https://example.test/apply",
        )

    def test_public_landing_does_not_expose_raw_catalogue(self):
        response = self.client.get("/")
        self.assertNotContains(response, 'id="opportunity-data"')
        self.assertNotContains(response, "Software Internship")
        self.assertNotContains(response, "Tech Template")

    @skip('Retired raw-opportunity matcher expectation; no-signal clean catalogue behavior is covered by CleanArchitectureCase.')
    def test_low_score_opportunities_remain_discoverable(self):
        user = User.objects.create_user("candidate", password="test-password")
        profile = Profile.objects.create(user=user, qualification="Unrelated", institution="", location="Durban")
        results = get_matched_opportunities(profile)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["score"], 0)
        self.assertEqual(results[0]["match_strength"], "Explore")

    def test_legacy_type_creation_populates_new_classification_dimensions(self):
        self.assertEqual(self.opportunity.opportunity_kind, "internship")
        self.assertEqual(self.opportunity.career_stage, "early_career")
        self.assertEqual(self.opportunity.scope_status, "in_scope")

    @skip('Retired legacy in-request CV-tip matching path.')
    def test_unknown_legacy_type_uses_general_cv_tip_fallback(self):
        self.opportunity.opportunity_type = "unclassified"
        self.opportunity.save(update_fields=["opportunity_type"])
        tip = CVtip.objects.create(
            opportunity_type="general", title="General CV guidance", content="Tailor your CV."
        )
        user = User.objects.create_user("cv-tip-user", password="test-password")
        profile = Profile.objects.create(user=user, qualification="", institution="", location="")
        results = get_matched_opportunities(profile, include_cv_tips=True)
        self.assertEqual(list(results[0]["cv_tips"]), [tip])


class ExistingRouteSmokeTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("route-user", password="test-password")
        Profile.objects.create(
            user=self.user, qualification="Diploma", institution="College",
            location="Johannesburg", profile_complete=True,
        )
        self.opportunity = Opportunity.objects.create(
            title="Route Test Learnership", company="Example", opportunity_type="learnership",
            location="Johannesburg", description="Test listing",
            qualification_minimum="BSc Computer Science",
        )
        Education.objects.create(profile=self.user.profile, qualification="BSc Computer Science")
        ProfileSkill.objects.create(profile=self.user.profile, entered_name="SQL")
        self.opportunity.required_skills.add(Skill.objects.create(name="SQL"))
        SavedOpportunity.objects.create(user=self.user, opportunity=self.opportunity)
        activate_clean_opportunity(self.opportunity)
        self.opportunity.refresh_from_db()
        resolve_vacancy_sources(self.opportunity.active_clean_revision)
        create_current_evaluation(self.user.profile, self.opportunity)

    def test_existing_user_facing_pages_load(self):
        self.client.force_login(self.user)
        routes = [
            reverse("dashboard"), reverse("profile"), reverse("opportunities"),
            reverse("opportunity_detail", args=[self.opportunity.id]),
            reverse("saved"),
            reverse("cv_enhancer"), reverse("cover_letter_generator"),
            reverse("interview_home"),
        ]
        for route in routes:
            with self.subTest(route=route):
                response = self.client.get(route)
                self.assertEqual(response.status_code, 200)

    def test_legacy_opportunity_type_filter_remains_compatible(self):
        self.client.force_login(self.user)
        matching = self.client.get(reverse("opportunities"), {"type": "learnership"})
        non_matching = self.client.get(reverse("opportunities"), {"type": "internship"})
        self.assertContains(matching, self.opportunity.title)
        self.assertNotContains(non_matching, self.opportunity.title)


class CandidateFeedTypeFilterTests(TestCase):
    """The candidate feed exposes exactly five type choices, and each choice
    filters inside the current feed universe with eligibility, seniority,
    and structured entry-level classification intact."""

    EXPECTED = {
        'all': {
            'Data Internship Placement', 'Data Learnership Placement',
            'Graduate Programme Placement', 'Junior Data Assistant',
            'Data Operations Role',
        },
        'internship': {'Data Internship Placement'},
        'learnership': {'Data Learnership Placement'},
        'graduate': {'Graduate Programme Placement'},
        'entry_level': {'Junior Data Assistant'},
    }

    def setUp(self):
        self.user = User.objects.create_user(
            'type-filter-user', password='test-password')
        # No career signal: the candidate reads the general catalogue universe.
        self.profile = Profile.objects.create(user=self.user)
        self.client.force_login(self.user)
        self.internship = self.make('Data Internship Placement', 'internship')
        self.learnership = self.make(
            'Data Learnership Placement', 'learnership')
        self.graduate = self.make('Graduate Programme Placement', 'graduate')
        self.entry = self.make('Junior Data Assistant', 'entry_level')
        # Structured mid-level row: shares the entry-level revision kind but
        # carries a different opportunity_type classification.
        self.mid = self.make('Data Operations Role', 'unclassified')
        self.senior = self.make(
            'Data Specialist Role', 'entry_level', seniority='OBVIOUS_SENIOR')

    def make(self, title, opportunity_type, *, seniority='NON_SENIOR'):
        opportunity = Opportunity.objects.create(
            title=title, company='Example Co', opportunity_type=opportunity_type,
            location='Cape Town',
            description=(
                f'{title}. A source-grounded test listing with duties '
                'and requirements for candidates.'),
        )
        activate_clean_opportunity(opportunity, title=title, seniority=seniority)
        opportunity.refresh_from_db()
        resolve_vacancy_sources(opportunity.active_clean_revision)
        return opportunity

    def titles(self, params):
        response = self.client.get(reverse('opportunities'), params)
        self.assertEqual(response.status_code, 200)
        return {row['opportunity'].title
                for row in response.context['results']}

    def visible_titles(self, params):
        """Every listing the page can show, in both personalised areas.

        The confirmed area and the clearly-labelled discovery area are separate
        lists, but a practical type filter must narrow both of them, so an
        assertion about "never appears" has to look at the whole page.
        """
        response = self.client.get(reverse('opportunities'), params)
        self.assertEqual(response.status_code, 200)
        return {row['opportunity'].title
                for row in (list(response.context['results'])
                            + list(response.context['discovery_results']))}

    def test_five_filter_states_stay_inside_the_current_universe(self):
        # Entry-level resolves against the structured classification, not the
        # revision kind the mid-level vacancy shares with it.
        self.assertEqual(self.entry.opportunity_type, 'entry_level')
        self.assertEqual(self.mid.opportunity_type, 'unclassified')
        self.assertEqual(
            self.mid.active_clean_revision.opportunity_kind, 'VACANCY')
        for choice, expected in self.EXPECTED.items():
            with self.subTest(choice=choice):
                self.assertEqual(self.titles({'type': choice}), expected)
        # Unlisted choices fall back to All: no hidden filter choices exist.
        self.assertEqual(self.titles({'type': 'bursary'}), self.EXPECTED['all'])

    def test_senior_blocked_vacancy_never_appears_in_any_filter_state(self):
        # The read-only decision is recomputed here from the same facts, so a
        # stored decision can never disagree with the feed, and the senior row
        # stays hidden in every filter state without any row being written.
        self.assertEqual(
            evaluate_revision(self.senior.active_clean_revision).decision, 'BLOCK')
        self.assertEqual(
            self.senior.active_clean_revision.seniority_label,
            'OBVIOUS_SENIOR')
        for choice in self.EXPECTED:
            with self.subTest(choice=choice):
                self.assertNotIn(
                    'Data Specialist Role', self.visible_titles({'type': choice}))

    def test_filter_states_apply_inside_the_personalized_survivor_feed(self):
        from .models import TaxonomyConcept, TaxonomyLabel, TaxonomyRelease
        from .services.feed_v2 import build_profile_generation
        from .services.occupational_resolution import (
            maintain_target_resolution_containers,
        )
        from .taxonomies.normalization import normalize_term
        releases = {
            source: TaxonomyRelease.objects.create(
                source=source, version=f'filter-{source}', is_active=True)
            for source in ('ofo', 'esco')
        }
        concept = TaxonomyConcept.objects.create(
            release=releases['ofo'], kind='occupation', external_id='ofo:jda:1',
            preferred_label='Junior Data Assistant',
            normalized_label=normalize_term('Junior Data Assistant'),
            metadata={})
        TaxonomyLabel.objects.create(
            concept=concept, label='Junior Data Assistant',
            normalized_label=normalize_term('Junior Data Assistant'),
            label_type='preferred')
        preference, _created = CareerPreference.objects.update_or_create(
            profile=self.profile,
            defaults={'target_roles': 'Junior Data Assistant'})
        maintain_target_resolution_containers(preference)
        _generation, count = build_profile_generation(self.profile)
        # Occupational admission is trusted evidence only.  The entry fixture
        # matches the candidate's target occupation exactly, so it is the one
        # admitted row; the other non-senior fixtures carry no trusted
        # occupational relationship and fail admission.  The OBVIOUS_SENIOR row
        # never enters the generation.
        self.assertEqual(count, 1)
        # Only the admitted fixture is personalised: a practical type filter
        # still has to narrow it wherever it could be listed.
        admitted = {'Junior Data Assistant'}
        personalised = {
            'all': admitted, 'internship': set(), 'learnership': set(),
            'graduate': set(), 'entry_level': admitted,
        }
        for choice, expected in personalised.items():
            with self.subTest(choice=choice):
                self.assertEqual(self.visible_titles({'type': choice}), expected)
        # A materialized evaluation cannot reintroduce the senior vacancy.
        self.assertNotIn(
            'Data Specialist Role', self.visible_titles({'type': 'entry_level'}))

    @override_settings(CAREERGPS_FEED_READ_VERSION='v2')
    def test_five_filter_states_hold_under_the_v2_read_version(self):
        for choice, expected in self.EXPECTED.items():
            with self.subTest(choice=choice):
                self.assertEqual(self.titles({'type': choice}), expected)

    def test_only_the_five_filter_choices_are_visible(self):
        response = self.client.get(reverse('opportunities'))
        for label in (
                '>All</option>', '>Internships</option>',
                '>Learnerships</option>', '>Graduate</option>',
                '>Entry-level</option>'):
            self.assertContains(response, label)
        for hidden in (
                'Strong matches', 'Exact matches', 'Related roles',
                'Current gaps', 'Work arrangement', 'Employment type',
                'Minimum salary', 'Apply filters'):
            self.assertNotContains(response, hidden)


class RichProfileModelTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user('rich-user', password='test-password')
        self.profile = Profile.objects.create(user=self.user, qualification='', institution='', location='')

    def test_multiple_education_records(self):
        Education.objects.create(profile=self.profile, institution='UJ', qualification='BCom')
        Education.objects.create(profile=self.profile, institution='Wits', qualification='Honours')
        self.assertEqual(self.profile.education_records.count(), 2)

    def test_free_entry_skill_and_duplicate_normalization(self):
        first = ProfileSkill.objects.create(
            profile=self.profile, entered_name='  Power   BI  ', normalized_name='ignored'
        )
        self.assertEqual(first.entered_name, 'Power BI')
        self.assertEqual(first.normalized_name, 'power bi')
        with self.assertRaises(IntegrityError):
            ProfileSkill.objects.create(
                profile=self.profile, entered_name='power bi', normalized_name='temporary'
            )

    def test_open_interest(self):
        interest = ProfileInterest.objects.create(
            profile=self.profile, entered_name='Renewable   Energy', normalized_name='temporary'
        )
        self.assertEqual(interest.normalized_name, 'renewable energy')

    def test_legacy_skill_custom_skill_and_interest_projection_is_preserved(self):
        skill = Skill.objects.create(name='Python')
        interest = Interest.objects.create(name='Data Science')
        self.profile.skills.add(skill)
        self.profile.interests.add(interest)
        self.profile.qualification = 'BSc Computer Science'
        self.profile.institution = 'Example University'
        self.profile.custom_skills = 'SQL, Power BI'
        self.profile.save()

        migration = importlib.import_module('core.migrations.0006_phase_2_profile_foundation')
        migration.preserve_legacy_profile_data(apps, None)
        projection = get_profile_projection(self.profile)
        self.assertEqual(set(projection['skill_names']), {'Python', 'SQL', 'Power BI'})
        self.assertEqual(projection['interest_names'], ['Data Science'])
        self.assertEqual(self.profile.profile_skills.count(), 3)
        education = self.profile.education_records.get()
        self.assertEqual(education.qualification, 'BSc Computer Science')
        self.assertEqual(education.institution, 'Example University')

    def test_migrated_skill_rename_and_delete_do_not_resurrect_legacy_value(self):
        legacy_skill = Skill.objects.create(name='Python')
        self.profile.skills.add(legacy_skill)
        migration = importlib.import_module('core.migrations.0006_phase_2_profile_foundation')
        migration.preserve_legacy_profile_data(apps, None)

        skill = self.profile.profile_skills.get()
        skill.entered_name = 'Python Programming'
        skill.save()
        self.assertEqual(get_profile_projection(self.profile)['skill_names'], ['Python Programming'])

        skill.delete()
        self.assertEqual(get_profile_projection(self.profile)['skill_names'], [])
        self.assertTrue(self.profile.skills.filter(name='Python').exists())

    def test_migrated_interest_rename_and_delete_do_not_resurrect_legacy_value(self):
        legacy_interest = Interest.objects.create(name='Data Science')
        self.profile.interests.add(legacy_interest)
        migration = importlib.import_module('core.migrations.0006_phase_2_profile_foundation')
        migration.preserve_legacy_profile_data(apps, None)

        interest = self.profile.profile_interests.get()
        interest.entered_name = 'Responsible AI'
        interest.save()
        self.assertEqual(get_profile_projection(self.profile)['interest_names'], ['Responsible AI'])

        interest.delete()
        self.assertEqual(get_profile_projection(self.profile)['interest_names'], [])

    def test_deleting_migrated_education_does_not_restore_legacy_qualification(self):
        self.profile.qualification = 'BSc Computer Science'
        self.profile.institution = 'Example University'
        self.profile.save()
        migration = importlib.import_module('core.migrations.0006_phase_2_profile_foundation')
        migration.preserve_legacy_profile_data(apps, None)

        self.profile.education_records.all().delete()
        projection = get_profile_projection(self.profile)
        self.assertEqual(projection['qualification'], '')
        self.assertEqual(projection['institution'], '')


class ProfileSectionCrudTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user('section-user', password='test-password')
        self.profile = Profile.objects.create(user=self.user, qualification='', institution='', location='')
        self.client.force_login(self.user)

    def add(self, section, data):
        return self.client.post(reverse('profile_section_add', args=[section]), data)

    def section_url(self, section):
        return reverse('profile_section', args=[section])

    def test_partial_personal_profile_saves(self):
        response = self.client.post(reverse('profile_personal_save'), {
            'first_name': '', 'last_name': '', 'phone': '', 'location': 'Durban',
        })
        self.assertRedirects(response, reverse('profile_personal'))
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.location, 'Durban')

    def test_project_crud(self):
        response = self.add('projects', {'add-projects-title': 'Portfolio app'})
        self.assertRedirects(response, self.section_url('projects'))
        project = Project.objects.get(profile=self.profile)
        response = self.client.post(
            reverse('profile_section_edit', args=['projects', project.id]),
            {f'projects-{project.id}-title': 'Career portfolio'},
        )
        self.assertRedirects(response, self.section_url('projects'))
        project.refresh_from_db()
        self.assertEqual(project.title, 'Career portfolio')
        response = self.client.post(reverse('profile_section_delete', args=['projects', project.id]))
        self.assertRedirects(response, self.section_url('projects'))
        self.assertFalse(Project.objects.filter(pk=project.id).exists())

    def test_work_experience_crud(self):
        response = self.add('experience', {
            'add-experience-title': 'Assistant',
            'add-experience-organisation': 'Example Co',
        })
        self.assertRedirects(response, self.section_url('experience'))
        record = WorkExperience.objects.get(profile=self.profile)
        self.client.post(reverse('profile_section_delete', args=['experience', record.id]))
        self.assertFalse(WorkExperience.objects.filter(pk=record.id).exists())

    def test_certification_crud(self):
        response = self.add('certifications', {'add-certifications-title': 'Cloud Fundamentals'})
        self.assertRedirects(response, self.section_url('certifications'))
        record = Certification.objects.get(profile=self.profile)
        self.client.post(reverse('profile_section_delete', args=['certifications', record.id]))
        self.assertFalse(Certification.objects.filter(pk=record.id).exists())

    def test_typed_skill_crud_rejects_normalized_duplicate(self):
        response = self.add('skills', {'add-skills-entered_name': 'Power   BI'})
        self.assertRedirects(response, self.section_url('skills'))
        skill = ProfileSkill.objects.get(profile=self.profile)
        self.assertEqual(skill.normalized_name, 'power bi')
        duplicate = self.add('skills', {'add-skills-entered_name': ' power bi '})
        self.assertEqual(duplicate.status_code, 400)
        self.assertEqual(ProfileSkill.objects.filter(profile=self.profile).count(), 1)

    def test_additional_profile_section_crud(self):
        cases = [
            ('achievements', {'add-achievements-title': 'Dean’s Award'}, Achievement),
            ('volunteering', {
                'add-volunteering-role': 'Mentor',
                'add-volunteering-organisation': 'Community Lab',
            }, Volunteering),
            ('languages', {'add-languages-language': 'isiZulu'}, Language),
            ('links', {
                'add-links-label': 'Portfolio', 'add-links-link_type': 'portfolio',
                'add-links-url': 'https://example.test/portfolio',
            }, ProfileLink),
            ('interests', {'add-interests-entered_name': 'Climate Technology'}, ProfileInterest),
            ('career-preferences', {
                'add-career-preferences-target_roles': 'Data Analyst',
                'add-career-preferences-work_arrangement': 'hybrid',
            }, CareerPreference),
        ]
        for section, data, model in cases:
            with self.subTest(section=section):
                response = self.add(section, data)
                self.assertRedirects(response, self.section_url(section))
                record = model.objects.get(profile=self.profile)
                response = self.client.post(
                    reverse('profile_section_delete', args=[section, record.id])
                )
                self.assertRedirects(response, self.section_url(section))
                self.assertFalse(model.objects.filter(pk=record.id).exists())

    def test_user_cannot_edit_or_delete_another_users_record(self):
        other = User.objects.create_user('other-user', password='test-password')
        other_profile = Profile.objects.create(user=other, qualification='', institution='', location='')
        project = Project.objects.create(profile=other_profile, title='Private project')
        edit = self.client.post(
            reverse('profile_section_edit', args=['projects', project.id]),
            {f'projects-{project.id}-title': 'Changed'},
        )
        delete = self.client.post(reverse('profile_section_delete', args=['projects', project.id]))
        self.assertEqual(edit.status_code, 404)
        self.assertEqual(delete.status_code, 404)
        project.refresh_from_db()
        self.assertEqual(project.title, 'Private project')

    def test_legacy_profile_post_cannot_create_split_brain_state(self):
        response = self.client.post(reverse('profile_complete'), {
            'qualification': 'Legacy-only qualification',
            'institution': 'Legacy-only institution',
            'location': 'Legacy-only location',
        })
        self.assertEqual(response.status_code, 405)
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.qualification, '')
        self.assertEqual(self.profile.institution, '')
        self.assertEqual(self.profile.location, '')
        self.assertFalse(self.profile.education_records.exists())

    def test_profile_hub_renders_all_section_tiles_without_edit_forms(self):
        response = self.client.get(reverse('profile'))
        self.assertEqual(response.status_code, 200)
        expected_titles = [
            'Personal Info', 'Education', 'Work Experience', 'Projects', 'Skills',
            'Certifications', 'Achievements', 'Volunteering', 'Languages',
            'Interests', 'Links &amp; Portfolio', 'Career Preferences',
        ]
        for title in expected_titles:
            self.assertContains(response, title)
        self.assertNotContains(response, '<form')
        self.assertContains(response, 'settings.js', count=1)

    def test_every_focused_section_requires_login_and_loads_for_owner(self):
        section_routes = [
            reverse('profile_personal'),
            *[self.section_url(section) for section in PROFILE_SECTION_NAMES],
        ]
        for route in section_routes:
            with self.subTest(route=route, state='authenticated'):
                self.assertEqual(self.client.get(route).status_code, 200)
        self.client.logout()
        for route in section_routes:
            with self.subTest(route=route, state='anonymous'):
                response = self.client.get(route)
                self.assertEqual(response.status_code, 302)
                self.assertIn(reverse('login'), response.url)

    def test_invalid_edit_form_remains_visibly_open(self):
        education = Education.objects.create(
            profile=self.profile, institution='UJ', qualification='BCom'
        )
        response = self.client.post(
            reverse('profile_section_edit', args=['education', education.id]),
            {
                f'education-{education.id}-institution': '',
                f'education-{education.id}-qualification': '',
            },
        )
        self.assertEqual(response.status_code, 400)
        self.assertContains(response, '<details open>', status_code=400)
        self.assertContains(response, 'This field is required.', status_code=400)

    def test_section_summary_uses_work_context_not_username(self):
        WorkExperience.objects.create(
            profile=self.profile, title='Junior Analyst', organisation='Example Co'
        )
        response = self.client.get(self.section_url('experience'))
        self.assertContains(response, 'Junior Analyst')
        self.assertContains(response, 'Example Co')
        self.assertNotContains(response, f'({self.user.username})')


@skip('Retired Phase Two raw matcher/feed compatibility suite; Clean-Job v1 materialized feed tests replace it.')
class PhaseTwoCompatibilityTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user('phase-two-user', password='test-password')
        self.profile = Profile.objects.create(user=self.user, qualification='', institution='', location='Cape Town')
        self.client.force_login(self.user)

    def opportunity(self, **overrides):
        values = {
            'title': 'Data Analyst Internship', 'company': 'Example Co',
            'opportunity_type': 'internship', 'location': 'Cape Town',
            'description': 'Use Python for data analysis.',
            'primary_apply_url': 'https://apply.example.test/jobs/123',
        }
        values.update(overrides)
        return Opportunity.objects.create(**values)

    def make_feed_eligible(self, opportunity):
        Education.objects.get_or_create(profile=self.profile, qualification="BSc Computer Science")
        ProfileSkill.objects.get_or_create(profile=self.profile, entered_name="SQL")
        skill, _ = Skill.objects.get_or_create(name="SQL")
        opportunity.qualification_minimum = "BSc Computer Science"
        opportunity.save(update_fields=["qualification_minimum"])
        opportunity.required_skills.add(skill)
        return opportunity

    def test_existing_matcher_uses_rich_profile_projection(self):
        Education.objects.create(
            profile=self.profile, institution='UCT', qualification='BSc Data Science'
        )
        ProfileSkill.objects.create(
            profile=self.profile, entered_name='Python', normalized_name='temporary'
        )
        ProfileInterest.objects.create(
            profile=self.profile, entered_name='Data', normalized_name='temporary'
        )
        skill = Skill.objects.create(name='Python')
        opportunity = self.opportunity(required_qualification='Data Science degree')
        opportunity.required_skills.add(skill)
        result = get_matched_opportunities(self.profile)[0]
        self.assertGreaterEqual(result['score'], 80)

    def test_profile_strength_does_not_pad_feed_with_unrelated_job(self):
        opportunity = self.opportunity()
        self.assertLess(calculate_completion(self.profile), 100)
        response = self.client.get(reverse('opportunities'))
        self.assertNotContains(response, opportunity.title)
        self.assertContains(response, "No relevant opportunities found right now")

    def test_dashboard_uses_same_ranked_order_and_shows_best_five(self):
        CareerPreference.objects.create(
            profile=self.profile,
            target_roles="Data Analyst Internship",
        )
        opportunities = []
        for index in range(6):
            title = "Data Analyst Internship" if index == 0 else f"General Opportunity {index}"
            opportunities.append(self.opportunity(title=title))

        dashboard = self.client.get(reverse('dashboard'))
        feed = self.client.get(reverse('opportunities'))

        dashboard_ids = [
            item['opportunity'].id for item in dashboard.context['top_matches']
        ]
        feed_ids = [
            item['opportunity'].id for item in feed.context['page_obj'].object_list
        ]
        self.assertEqual(dashboard_ids, [])
        self.assertEqual(dashboard_ids, feed_ids[:5])

    def test_opportunity_feed_is_paginated(self):
        for index in range(25):
            self.make_feed_eligible(self.opportunity(title=f'Opportunity {index}'))
        response = self.client.get(reverse('opportunities'))
        self.assertEqual(len(response.context['results']), 24)
        self.assertEqual(response.context['page_obj'].paginator.num_pages, 2)

    def test_normal_feed_matching_avoids_per_opportunity_queries_and_cv_tips(self):
        for index in range(12):
            self.opportunity(title=f'Query test {index}')
        with CaptureQueriesContext(connection) as queries:
            results = get_matched_opportunities(self.profile, include_cv_tips=False)
            self.assertEqual(len(results), 12)
        self.assertLessEqual(len(queries), 9)
        self.assertFalse(any('core_cvtip' in query['sql'].lower() for query in queries))

    def test_prefiltered_feed_matches_legacy_filtering_and_ranking(self):
        from core.taxonomies.normalization import normalize_term
        from core.models import (
            TaxonomyConcept, TaxonomyLabel, TaxonomyRelation, TaxonomyRelease,
        )
        from employers import services as employer_services
        from ingestion.models import OpportunityCriterion, OpportunityExtraction

        CareerPreference.objects.create(
            profile=self.profile,
            target_roles='Data Analyst Internship',
            preferred_locations='Cape Town',
        )
        ProfileSkill.objects.create(profile=self.profile, entered_name='Python')
        release = TaxonomyRelease.objects.create(
            source='esco', version='phase-one-equivalence', is_active=True
        )

        def taxonomy_concept(external_id, label, kind):
            concept = TaxonomyConcept.objects.create(
                release=release,
                external_id=external_id,
                kind=kind,
                preferred_label=label,
                normalized_label=normalize_term(label),
            )
            TaxonomyLabel.objects.create(
                concept=concept,
                label=label,
                normalized_label=normalize_term(label),
                label_type='preferred',
            )
            return concept

        programming_concept = taxonomy_concept(
            'skill:programming', 'Programming', 'skill_group'
        )
        python_concept = taxonomy_concept('skill:python', 'Python', 'skill')
        TaxonomyRelation.objects.create(
            subject=python_concept,
            relation_type='broader',
            object=programming_concept,
        )
        python = Skill.objects.create(name='Python')
        programming = Skill.objects.create(name='Programming')
        saved_ids = set()
        created = []
        for index in range(31):
            opportunity = self.opportunity(
                title=(
                    f'Python Analyst {index}' if index in {2, 8}
                    else f'General Opportunity {index}'
                ),
                company='100% Careers' if index == 5 else 'Example Co',
                opportunity_type='internship' if index % 4 == 0 else 'unclassified',
                location='Johannesburg' if index == 7 else 'Cape Town',
                sector='Financial Services' if index == 9 else '',
                required_qualification='Python diploma' if index == 11 else '',
            )
            if index % 3 == 0:
                opportunity.required_skills.add(python)
            if index == 10:
                opportunity.required_skills.add(programming)
            if index in {2, 17}:
                SavedOpportunity.objects.create(user=self.user, opportunity=opportunity)
                saved_ids.add(opportunity.id)
            created.append(opportunity)

        extraction = OpportunityExtraction.objects.create(
            opportunity=created[1],
            status='ready',
            source_completeness='full_structured',
            input_hash='phase-one-equivalence',
            model_name='test-model',
        )
        OpportunityCriterion.objects.create(
            extraction=extraction,
            criterion_type='language',
            requirement_level='preferred',
            raw_value='English',
            evidence_text='English is preferred',
        )
        # This type was historically prefetched but is not consumed by the
        # matcher; the optimized filtered prefetch must leave decisions intact.
        OpportunityCriterion.objects.create(
            extraction=extraction,
            criterion_type='responsibility',
            requirement_level='informational',
            raw_value='Prepare reports',
            evidence_text='Prepare reports',
        )

        employer_owner = User.objects.create_user(
            'phase-one-employer', password='test-password'
        )
        organization = employer_services.create_organization(
            user=employer_owner,
            data={'name': 'Direct Data Employer', 'industry': 'Technology'},
        )
        organization.verification_status = 'approved'
        organization.save(update_fields=['verification_status', 'updated_at'])
        vacancy = employer_services.save_vacancy(
            user=employer_owner,
            organization_id=organization.id,
            data={
                'title': 'Direct Data Internship',
                'description': 'Analyse trusted reports.',
                'location': 'Cape Town',
                'career_stage': 'early_career',
                'opportunity_kind': 'internship',
                'no_candidate_fees': True,
            },
            requirements=[{
                'raw_value': 'Python',
                'criterion_type': 'skill',
                'requirement_level': 'required',
            }],
        )
        employer_services.publish_vacancy(
            user=employer_owner,
            organization_id=organization.id,
            vacancy_id=vacancy.id,
        )

        from core.services.opportunity_feed import personalized_opportunities
        legacy_results = personalized_opportunities(self.profile)
        for item in legacy_results:
            item['is_saved'] = item['opportunity'].id in saved_ids

        def legacy_page(search='', opportunity_type='all', page=1):
            needle = search.lower()
            filtered = legacy_results
            if search:
                filtered = [
                    item for item in filtered
                    if any(
                        needle in (value or '').lower()
                        for value in (
                            item['opportunity'].title,
                            item['opportunity'].company,
                            item['opportunity'].location,
                            item['opportunity'].sector,
                            item['opportunity'].required_qualification,
                        )
                    )
                ]
            if opportunity_type != 'all':
                filtered = [
                    item for item in filtered
                    if item['opportunity'].opportunity_type == opportunity_type
                ]
            start = (page - 1) * 24
            return filtered[start:start + 24]

        def signature(item):
            return (
                item['opportunity'].id,
                item['score'],
                item['recommendation_band'],
                item['career_direction_tier_priority'],
                item['why_text'],
                item['is_saved'],
                tuple(
                    (criterion.criterion, criterion.status, criterion.importance)
                    for criterion in item['criteria']
                ),
            )

        scenarios = (
            ({}, '', 'all', 1),
            ({'search': 'python'}, 'python', 'all', 1),
            ({'search': '100%'}, '100%', 'all', 1),
            ({'type': 'internship'}, '', 'internship', 1),
            (
                {'search': 'cape town', 'type': 'internship'},
                'cape town', 'internship', 1,
            ),
            ({'page': 2}, '', 'all', 2),
        )
        for params, search, opportunity_type, page in scenarios:
            with self.subTest(params=params):
                response = self.client.get(reverse('opportunities'), params)
                actual = response.context['page_obj'].object_list
                expected = legacy_page(search, opportunity_type, page)
                self.assertEqual(
                    [signature(item) for item in actual],
                    [signature(item) for item in expected],
                )

    def test_explicit_filters_restrict_the_queryset_before_matching(self):
        wanted = self.opportunity(
            title='Python Internship', opportunity_type='internship'
        )
        self.opportunity(title='Python Graduate Role', opportunity_type='graduate')
        self.opportunity(title='Unrelated Internship', opportunity_type='internship')

        captured = {}

        def matcher(profile, queryset=None):
            captured['profile'] = profile
            captured['ids'] = list(queryset.values_list('id', flat=True))
            return [], []

        with patch('core.views.evaluate_personalized_feed', side_effect=matcher):
            response = self.client.get(
                reverse('opportunities'),
                {'search': 'python', 'type': 'internship'},
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(captured['profile'], self.profile)
        self.assertEqual(captured['ids'], [wanted.id])

    def test_opportunities_query_count_is_bounded_for_request_filters(self):
        for index in range(36):
            self.opportunity(
                title=f'Python Role {index}' if index < 3 else f'Role {index}',
                opportunity_type='internship' if index % 6 == 0 else 'unclassified',
            )

        for params in ({}, {'search': 'python'}, {'type': 'internship'}):
            with self.subTest(params=params):
                with CaptureQueriesContext(connection) as queries:
                    response = self.client.get(reverse('opportunities'), params)
                self.assertEqual(response.status_code, 200)
                # A per-opportunity relation lookup would exceed this bound for
                # the 36-row fixture; framework-level request queries may vary.
                self.assertLessEqual(len(queries), 20)

    def test_opportunities_javascript_uses_catalogue_wide_server_search(self):
        script = (
            apps.get_app_config('core').path
            + '/static/core/js/opportunities.js'
        )
        with open(script, encoding='utf-8') as handle:
            contents = handle.read()
        self.assertIn('searchInput.form.requestSubmit()', contents)
        self.assertNotIn('filterJobs', contents)
        response = self.client.get(reverse('opportunities'))
        self.assertContains(response, 'type="submit" class="search-submit"')

    def test_catalogue_rules_include_unclassified_and_exclude_unavailable(self):
        unclassified = self.opportunity(
            title='Unclassified role', opportunity_type='unclassified',
            opportunity_kind='job', career_stage='unknown', scope_status='unclassified',
        )
        out_of_scope = self.opportunity(
            title='Out of scope', scope_status='out_of_scope', career_stage='experienced',
        )
        expired = self.opportunity(
            title='Expired', lifecycle_status='expired', is_active=False,
        )
        discoverable_ids = set(Opportunity.objects.discoverable().values_list('id', flat=True))
        self.assertIn(unclassified.id, discoverable_ids)
        self.assertNotIn(out_of_scope.id, discoverable_ids)
        self.assertNotIn(expired.id, discoverable_ids)

    def test_all_catalogue_apply_links_use_trusted_source_route(self):
        from ingestion.models import IngestionSource, OpportunitySourceRecord
        opportunity = self.opportunity(redirect_url='https://fallback.example.test/jobs/123')
        OpportunitySourceRecord.objects.create(
            opportunity=opportunity, source=IngestionSource.objects.get(code='sasol'),
            source_external_id='apply-route-123', apply_url=opportunity.primary_apply_url,
        )
        SavedOpportunity.objects.create(user=self.user, opportunity=opportunity)
        self.make_feed_eligible(opportunity)
        for route in [
            reverse('dashboard'), reverse('opportunities'),
            reverse('opportunity_detail', args=[opportunity.id]), reverse('saved'),
        ]:
            with self.subTest(route=route):
                response = self.client.get(route)
                self.assertContains(response, opportunity.primary_apply_url)

    def test_expired_saved_opportunity_remains_history(self):
        opportunity = self.opportunity(lifecycle_status='expired', is_active=False)
        saved = SavedOpportunity.objects.create(user=self.user, opportunity=opportunity)
        response = self.client.get(reverse('saved'))
        self.assertContains(response, opportunity.title)
        self.assertContains(response, 'No longer available')
        self.assertTrue(SavedOpportunity.objects.filter(pk=saved.pk).exists())
        unsave = self.client.post(reverse('toggle_save', args=[opportunity.id]))
        self.assertEqual(unsave.status_code, 302)
        self.assertFalse(SavedOpportunity.objects.filter(pk=saved.pk).exists())
