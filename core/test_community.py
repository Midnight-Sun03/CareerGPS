from datetime import timedelta
from unittest.mock import patch

from django.contrib import admin
from django.contrib.auth.models import User
from django.core import mail
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, connection, transaction
from django.test import TestCase, Client, RequestFactory, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from core.models import (Education, Profile, ProfileSkill, Skill, Story, StoryReport, Opportunity, OpportunityReport,
    StoryModerationEvent, OpportunityModerationEvent, Notification, NotificationPreference, SavedOpportunity)
from core.services.notifications import notify, validate_target_url
from core.services.moderation import submit_report, resolve_report, moderate_story, moderate_opportunity
from ingestion.ai.payload import build_compact_opportunity_payload
from ingestion.models import IngestionSource, OpportunityExtraction, OpportunityCriterion
from ingestion.pipeline.records import FetchedOpportunity
from ingestion.pipeline.runner import run_adapter
from applications.models import Application
from core.test_support import activate_clean_opportunity, create_current_evaluation


@override_settings(PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'],
    EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
class CommunityCase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user('reporter', password='test')
        cls.author = User.objects.create_user('author', password='test')
        cls.other = User.objects.create_user('other', password='test')
        cls.moderator = User.objects.create_superuser('moderator', 'moderator@example.test', 'test')
        for user in (cls.user, cls.author, cls.other):
            Profile.objects.create(user=user)

    def setUp(self):
        self.client.force_login(self.user)

    def opportunity(self, **changes):
        professional = changes.pop('professional', True)
        values = dict(title='Data Analyst', company='Example Bank', location='Johannesburg',
            opportunity_type='unclassified', description='Analyse data.', source_platform='Example Careers',
            primary_apply_url='https://example.test/jobs/123')
        values.update(changes)
        opportunity = Opportunity.objects.create(**values)
        if professional:
            activate_clean_opportunity(opportunity)
            opportunity.refresh_from_db()
        return opportunity

    def make_feed_eligible(self, profile, opportunity):
        Education.objects.get_or_create(profile=profile, qualification='BSc Computer Science')
        ProfileSkill.objects.get_or_create(profile=profile, entered_name='SQL')
        skill, _ = Skill.objects.get_or_create(name='SQL')
        opportunity.qualification_minimum = 'BSc Computer Science'
        opportunity.save(update_fields=['qualification_minimum'])
        opportunity.required_skills.add(skill)
        if not opportunity.profile_evaluations.filter(profile=profile).exists():
            create_current_evaluation(profile, opportunity)

    def story(self, **changes):
        values = dict(user=self.author, display_name='Story author', title='My journey', content='Public story body', status='approved')
        values.update(changes)
        return Story.objects.create(**values)


class NotificationServiceTests(CommunityCase):
    def send(self, user=None, **overrides):
        values = dict(user=user or self.user, category='stories', event_key='event:1', title='Update', target_url='/stories/')
        values.update(overrides)
        return notify(**values)

    def test_enabled_category_creates_notification_without_email(self):
        NotificationPreference.objects.create(user=self.user, stories=True)
        self.assertEqual(self.send().user, self.user)
        self.assertEqual(len(mail.outbox), 0)

    def test_disabled_category_does_not_create(self):
        NotificationPreference.objects.create(user=self.user, stories=False)
        self.assertIsNone(self.send())
        self.assertFalse(Notification.objects.exists())

    def test_system_bypasses_optional_preferences(self):
        NotificationPreference.objects.create(user=self.user, stories=False, opportunities=False,
            deadlines=False, applications=False, interviews=False, career_intelligence=False)
        self.assertIsNotNone(self.send(category='system'))

    def test_preferences_are_user_specific(self):
        NotificationPreference.objects.create(user=self.user, stories=False)
        self.assertIsNone(self.send())
        self.assertIsNotNone(self.send(user=self.other))

    def test_event_key_idempotency_and_database_constraint(self):
        first = self.send()
        self.assertEqual(self.send().pk, first.pk)
        with self.assertRaises(IntegrityError), transaction.atomic():
            Notification.objects.create(user=self.user, event_key='event:1', title='Duplicate')
        self.assertIsNotNone(self.send(user=self.other))

    def test_preferences_do_not_remove_existing_notifications(self):
        notification = self.send()
        NotificationPreference.objects.create(user=self.user, stories=False)
        self.assertContains(self.client.get('/notifications/'), notification.title)
        self.assertEqual(self.send().pk, notification.pk)

    def test_internal_targets_allowed(self):
        for path in ('', '/stories/', '/notifications/?selected=12', '/profile/#skills'):
            with self.subTest(path=path):
                self.assertEqual(validate_target_url(path), path)

    def test_unsafe_targets_rejected(self):
        for path in ('javascript:alert(1)', 'data:text/html,hi', 'https://example.test', '//example.test',
            '/\\evil.test', '/%2fevil.test', '/%252fevil.test', '/a\nLocation: hi', ' stories/'):
            with self.subTest(path=path), self.assertRaises(ValidationError):
                self.send(target_url=path)
        self.assertFalse(Notification.objects.exists())

    def test_legacy_unsafe_destination_not_rendered(self):
        row = Notification.objects.create(user=self.user, title='Legacy', target_url='javascript:alert(1)')
        self.assertNotContains(self.client.get(f'/notifications/?selected={row.pk}'), 'javascript:alert')

    def test_open_is_post_and_only_marks_selected(self):
        selected = self.send()
        other = self.send(event_key='event:2')
        self.assertEqual(self.client.get(reverse('notification_open', args=[selected.pk])).status_code, 405)
        self.client.get(f'/notifications/?selected={selected.pk}')
        selected.refresh_from_db()
        self.assertFalse(selected.is_read)
        response = self.client.post(reverse('notification_open', args=[selected.pk]))
        self.assertRedirects(response, '/stories/', fetch_redirect_response=False)
        selected.refresh_from_db(); other.refresh_from_db()
        self.assertTrue(selected.is_read)
        self.assertFalse(other.is_read)

    def test_missing_or_unsafe_destination_opens_owned_detail(self):
        for target in ('', 'javascript:alert(1)'):
            with self.subTest(target=target):
                row = Notification.objects.create(user=self.user, title='Legacy', target_url=target)
                response = self.client.post(reverse('notification_open', args=[row.pk]))
                self.assertRedirects(response, f'/notifications/?selected={row.pk}')
                row.refresh_from_db()
                self.assertTrue(row.is_read)

    def test_notification_history_is_paginated_without_losing_older_items(self):
        rows = [self.send(event_key=f'page:{number}', title=f'Notification {number}')
                for number in range(35)]
        first = self.client.get('/notifications/')
        second = self.client.get('/notifications/?page=2')
        self.assertEqual(len(first.context['notifications']), 30)
        self.assertEqual(len(second.context['notifications']), 5)
        self.assertIn(rows[0], second.context['notifications'])
        self.assertContains(first, '?page=2')
        self.assertContains(second, 'Previous')

    def test_notification_center_reuses_count_and_first_page_for_bell(self):
        self.send()
        with CaptureQueriesContext(connection) as queries:
            self.client.get('/notifications/')
        notification_queries = [query for query in queries if 'core_notification' in query['sql']]
        self.assertEqual(len(notification_queries), 3)

    def test_mark_all_preserves_paged_detail_and_owner_scope(self):
        rows = [self.send(event_key=f'bulk:{number}') for number in range(35)]
        foreign = self.send(user=self.other, event_key='bulk:foreign')
        response = self.client.post('/notifications/read-all/',
            {'page': '2', 'selected': str(rows[0].pk)})
        self.assertRedirects(response, f'/notifications/?page=2&selected={rows[0].pk}')
        self.assertFalse(Notification.objects.filter(user=self.user, is_read=False).exists())
        foreign.refresh_from_db()
        self.assertFalse(foreign.is_read)

    def test_foreign_notification_access_denied(self):
        foreign = self.send(user=self.other, title='Private foreign message')
        self.assertEqual(self.client.post(reverse('notification_open', args=[foreign.pk])).status_code, 404)
        self.assertNotContains(self.client.get(f'/notifications/?selected={foreign.pk}'), 'Private foreign message')

    def test_mark_all_user_isolation_and_post_only(self):
        mine, foreign = self.send(), self.send(user=self.other)
        self.assertEqual(self.client.get('/notifications/read-all/').status_code, 405)
        self.client.post('/notifications/read-all/')
        mine.refresh_from_db(); foreign.refresh_from_db()
        self.assertTrue(mine.is_read)
        self.assertFalse(foreign.is_read)

    def test_open_requires_csrf(self):
        client = Client(enforce_csrf_checks=True); client.force_login(self.user)
        self.assertEqual(client.post(reverse('notification_open', args=[self.send().pk])).status_code, 403)

    def test_settings_are_server_persisted_and_owned(self):
        self.client.post('/settings/', {'stories': 'on', 'user': self.other.pk})
        preference = NotificationPreference.objects.get(user=self.user)
        self.assertTrue(preference.stories); self.assertFalse(preference.opportunities)
        self.assertFalse(NotificationPreference.objects.filter(user=self.other).exists())
        response = self.client.get('/settings/')
        self.assertContains(response, 'Save notification preferences')
        self.assertNotContains(response, 'Weekly digest')
        self.assertNotContains(response, 'Connection requests')
        self.assertContains(response, reverse('employers:visibility'))
        self.assertContains(response, 'Control whether approved employers can discover your')
        self.assertContains(response, 'Request access, correction, export, deletion')
        self.assertContains(response, 'application-stage')
        self.assertNotContains(response, 'application and interview alerts are not enabled')
        self.assertContains(response, 'account to another browser or device')

    def test_candidate_and_public_shells_load_accessibility_engine_once(self):
        candidate = self.client.get(reverse('settings'))
        public = Client().get(reverse('login'))
        for response in (candidate, public):
            self.assertContains(response, 'core/js/settings.js', count=1)
            self.assertContains(response, 'core/css/setfunctions.css', count=1)


class StoryReportingTests(CommunityCase):
    def test_dirty_status_omitted_from_update_fields_has_no_moderation_effect(self):
        story = self.story(status='pending')
        story.status = 'approved'; story.title = 'Edited title'
        story.save(update_fields=['title'])
        story.refresh_from_db()
        self.assertEqual(story.status, 'pending')
        self.assertFalse(Notification.objects.exists())
        self.assertFalse(StoryModerationEvent.objects.exists())

    def test_admin_report_queue_removal_action(self):
        report = submit_report(reporter=self.user, target=self.story(), reason='spam')
        self.client.force_login(self.moderator)
        response = self.client.post('/admin/core/storyreport/', {
            'action': 'remove_items', '_selected_action': [report.pk], 'index': 0})
        self.assertEqual(response.status_code, 302)
        report.refresh_from_db()
        self.assertEqual(report.reviewer, self.moderator)
        self.assertEqual(report.story.status, 'removed')

    def test_only_published_stories_reportable(self):
        for status in ('pending', 'rejected', 'removed'):
            with self.subTest(status=status):
                story = self.story(status=status)
                with self.assertRaises(ValidationError):
                    submit_report(reporter=self.user, target=story, reason='other')
                self.assertEqual(self.client.post(reverse('report_story', args=[story.pk]), {'reason': 'other'}).status_code, 404)

    def test_own_story_not_reportable(self):
        story = self.story(user=self.user)
        with self.assertRaises(ValidationError):
            submit_report(reporter=self.user, target=story, reason='other')

    def test_duplicate_open_report_prevented(self):
        story = self.story()
        submit_report(reporter=self.user, target=story, reason='other')
        with self.assertRaises(ValidationError):
            submit_report(reporter=self.user, target=story, reason='spam')
        with self.assertRaises(IntegrityError), transaction.atomic():
            StoryReport.objects.create(story=story, reporter=self.user, reason='spam')

    def test_resolved_report_allows_later_report(self):
        story = self.story()
        first = submit_report(reporter=self.user, target=story, reason='spam')
        resolve_report(report=first, actor=self.moderator, action='dismiss')
        second = submit_report(reporter=self.user, target=story, reason='privacy', details='New issue')
        self.assertNotEqual(first.pk, second.pk)

    def test_report_does_not_remove_story(self):
        story = self.story()
        submit_report(reporter=self.user, target=story, reason='hate')
        story.refresh_from_db(); self.assertEqual(story.status, 'approved')
        self.assertContains(self.client.get('/stories/'), 'Public story body')

    def test_dismiss_records_reviewer_and_resolution(self):
        report = submit_report(reporter=self.user, target=self.story(), reason='spam')
        report = resolve_report(report=report, actor=self.moderator, action='dismiss', note='Reviewed evidence')
        self.assertEqual(report.status, 'resolved'); self.assertEqual(report.resolution, 'dismissed')
        self.assertEqual(report.reviewer, self.moderator); self.assertIsNotNone(report.reviewed_at)

    def test_removal_hides_story_retains_history_and_notifies_author(self):
        story = self.story()
        report = submit_report(reporter=self.user, target=story, reason='harassment')
        resolve_report(report=report, actor=self.moderator, action='remove')
        story.refresh_from_db(); self.assertEqual(story.status, 'removed')
        self.assertTrue(StoryModerationEvent.objects.filter(story=story, to_status='removed').exists())
        self.assertNotContains(self.client.get('/stories/'), 'Public story body')
        self.assertTrue(Notification.objects.filter(user=self.author, category='stories').exists())
        self.assertTrue(StoryReport.objects.filter(pk=report.pk).exists())

    def test_reporter_privacy(self):
        story = self.story()
        submit_report(reporter=self.user, target=story, reason='privacy', details='private report detail')
        self.client.force_login(self.other)
        response = self.client.get('/stories/')
        self.assertNotContains(response, 'private report detail')
        self.assertNotContains(response, self.user.username)

    def test_author_preference_respected(self):
        NotificationPreference.objects.create(user=self.author, stories=False)
        story = self.story(status='pending')
        moderate_story(story_id=story.pk, actor=self.moderator, status='approved')
        self.assertFalse(Notification.objects.filter(user=self.author).exists())

    def test_approval_rejection_and_unchanged_saves(self):
        for status, title in [('approved', 'Your story was approved'), ('rejected', "Your story wasn't approved")]:
            with self.subTest(status=status):
                story = self.story(status='pending')
                moderate_story(story_id=story.pk, actor=self.moderator, status=status, note='Moderation explanation')
                moderate_story(story_id=story.pk, actor=self.moderator, status=status, note='Moderation explanation')
                story.refresh_from_db(); story.save(update_fields=['title'])
                self.assertEqual(Notification.objects.filter(user=self.author, title=title).count(), 1)
        self.assertIn('Moderation explanation', Notification.objects.get(notification_type='story_rejected').message)

    def test_restore_retains_original_publication_time(self):
        story = self.story(status='pending')
        story = moderate_story(story_id=story.pk, actor=self.moderator, status='approved')
        published = story.approved_at
        moderate_story(story_id=story.pk, actor=self.moderator, status='removed')
        restored = moderate_story(story_id=story.pk, actor=self.moderator, status='approved')
        self.assertEqual(restored.approved_at, published)
        self.assertEqual(StoryModerationEvent.objects.filter(story=story).count(), 3)

    def test_admin_moderation_uses_service(self):
        story = self.story(status='pending'); story.status = 'approved'
        request = RequestFactory().post('/admin/'); request.user = self.moderator
        admin.site._registry[Story].save_model(request, story, None, True)
        self.assertTrue(Notification.objects.filter(user=self.author, title='Your story was approved').exists())

    def test_non_moderator_cannot_resolve(self):
        report = submit_report(reporter=self.user, target=self.story(), reason='other')
        with self.assertRaises(PermissionDenied):
            resolve_report(report=report, actor=self.user, action='remove')

    def test_form_get_read_only_and_post_success(self):
        story = self.story(); url = reverse('report_story', args=[story.pk])
        self.assertEqual(self.client.get(url).status_code, 200)
        self.assertFalse(StoryReport.objects.exists())
        self.assertEqual(self.client.post(url, {'reason': 'other', 'reporter': self.other.pk}).status_code, 302)
        self.assertEqual(StoryReport.objects.get().reporter, self.user)


class OpportunityReportingTests(CommunityCase):
    def test_reporter_identity_and_details_not_in_opportunity_page(self):
        opportunity = self.opportunity()
        self.make_feed_eligible(self.other.profile, opportunity)
        submit_report(reporter=self.user, target=opportunity, reason='other', details='Private moderation-only evidence')
        self.client.force_login(self.other)
        response = self.client.get(reverse('opportunity_detail', args=[opportunity.pk]))
        self.assertNotContains(response, 'Private moderation-only evidence')
        self.assertNotContains(response, self.user.username)

    def test_all_reasons_and_report_does_not_suppress(self):
        for reason, _ in OpportunityReport.REASON_CHOICES:
            with self.subTest(reason=reason):
                opportunity = self.opportunity()
                report = submit_report(reporter=self.user, target=opportunity, reason=reason)
                self.assertEqual(report.reason, reason)
                opportunity.refresh_from_db(); self.assertEqual(opportunity.moderation_status, 'clear')
        self.assertEqual(Opportunity.objects.discoverable().count(), len(OpportunityReport.REASON_CHOICES))

    def test_duplicate_open_report_prevented(self):
        opportunity = self.opportunity()
        submit_report(reporter=self.user, target=opportunity, reason='scam')
        with self.assertRaises(ValidationError):
            submit_report(reporter=self.user, target=opportunity, reason='money')

    def test_suppression_excludes_catalogue_retains_history(self):
        opportunity = self.opportunity()
        SavedOpportunity.objects.create(user=self.user, opportunity=opportunity)
        Application.objects.create(user=self.user, opportunity=opportunity)
        report = submit_report(reporter=self.user, target=opportunity, reason='scam')
        report = resolve_report(report=report, actor=self.moderator, action='remove', note='Evidence reviewed')
        self.assertFalse(Opportunity.objects.discoverable().filter(pk=opportunity.pk).exists())
        self.assertTrue(SavedOpportunity.objects.filter(opportunity=opportunity).exists())
        self.assertTrue(Application.objects.filter(opportunity=opportunity).exists())
        self.assertEqual(report.reviewer, self.moderator); self.assertEqual(report.resolution, 'action_taken')
        self.assertTrue(OpportunityModerationEvent.objects.filter(opportunity=opportunity).exists())

    def test_resolved_report_notification_is_private(self):
        report = submit_report(reporter=self.user, target=self.opportunity(), reason='other')
        resolve_report(report=report, actor=self.moderator, action='dismiss', note='Private moderator discussion')
        notification = Notification.objects.get(user=self.user)
        self.assertNotIn('Private moderator discussion', notification.message)
        self.assertFalse(Notification.objects.filter(user=self.other).exists())

    def test_opportunity_preference_respected(self):
        NotificationPreference.objects.create(user=self.user, opportunities=False)
        report = submit_report(reporter=self.user, target=self.opportunity(), reason='other')
        resolve_report(report=report, actor=self.moderator, action='dismiss')
        self.assertFalse(Notification.objects.filter(user=self.user).exists())

    def test_restore_keeps_source_lifecycle(self):
        opportunity = self.opportunity(lifecycle_status='expired', is_active=False)
        moderate_opportunity(opportunity_id=opportunity.pk, actor=self.moderator, status='suppressed')
        moderate_opportunity(opportunity_id=opportunity.pk, actor=self.moderator, status='clear')
        self.assertFalse(Opportunity.objects.discoverable().filter(pk=opportunity.pk).exists())

    def test_expired_reporting_requires_own_history(self):
        opportunity = self.opportunity(closing_date=timezone.localdate() - timedelta(days=1))
        url = reverse('report_opportunity', args=[opportunity.pk])
        self.assertEqual(self.client.get(url).status_code, 404)
        SavedOpportunity.objects.create(user=self.user, opportunity=opportunity)
        self.assertEqual(self.client.post(url, {'reason': 'unavailable'}).status_code, 302)

    def test_trust_panel_no_unfounded_verification(self):
        opportunity = self.opportunity()
        self.make_feed_eligible(self.user.profile, opportunity)
        response = self.client.get(reverse('opportunity_detail', args=[opportunity.pk]))
        self.assertContains(response, 'Listing evidence')
        self.assertNotContains(response, 'https://example.test/jobs/123')
        self.assertNotContains(response, 'Verified safe')


class OpportunityIntegrityTests(CommunityCase):
    def test_extraction_response_from_old_input_cannot_become_ready(self):
        self.skipTest('Retired internal Phase 2.4 cleaner cannot produce READY; Clean-Job v1 source-race coverage replaces it.')

    def test_stale_ingestion_object_cannot_reset_concurrent_suppression(self):
        from ingestion.pipeline.deduplication import apply_canonical_fields
        source = IngestionSource.objects.create(
            name='Example Careers', code='example-careers', source_type='scraper', company='Example Bank')
        stale_object = self.opportunity()
        moderate_opportunity(opportunity_id=stale_object.pk, actor=self.moderator, status='suppressed')
        apply_canonical_fields(stale_object, {'description': 'Updated by ingestion'}, source, authoritative_update=True)
        stale_object.refresh_from_db()
        self.assertEqual(stale_object.moderation_status, 'suppressed')

    def test_past_deadline_excluded_history_retained(self):
        opportunity = self.opportunity(closing_date=timezone.localdate() - timedelta(days=1))
        SavedOpportunity.objects.create(user=self.user, opportunity=opportunity)
        self.assertFalse(opportunity.is_catalogue_available)
        self.assertFalse(Opportunity.objects.discoverable().filter(pk=opportunity.pk).exists())
        self.assertContains(self.client.get('/saved/'), opportunity.title)

    def test_closing_today_and_talent_pools_remain_available(self):
        today = self.opportunity(closing_date=timezone.localdate())
        pool = self.opportunity(listing_kind='talent_pool', closing_date=timezone.localdate() - timedelta(days=2))
        self.assertTrue(today.is_catalogue_available); self.assertTrue(pool.is_catalogue_available)
        self.assertEqual(Opportunity.objects.discoverable().count(), 2)

    def test_legacy_deadline_also_excludes(self):
        opportunity = self.opportunity(closing_date=timezone.localdate() + timedelta(days=3),
            application_deadline=timezone.localdate() - timedelta(days=1))
        self.assertFalse(Opportunity.objects.discoverable().filter(pk=opportunity.pk).exists())

    def test_material_change_stales_extraction_and_preserves_criteria(self):
        opportunity = self.opportunity()
        extraction = OpportunityExtraction.objects.create(opportunity=opportunity, status='ready', model_name='test',
            input_hash=build_compact_opportunity_payload(opportunity)['input_hash'])
        criterion = OpportunityCriterion.objects.create(extraction=extraction, criterion_type='skill', raw_value='SQL', evidence_text='SQL')
        opportunity.description = 'New materially different duties.'; opportunity.save(update_fields=['description'])
        extraction.refresh_from_db(); self.assertEqual(extraction.status, 'stale')
        self.assertTrue(OpportunityCriterion.objects.filter(pk=criterion.pk).exists())

    def test_update_fields_ignores_unsaved_material_changes(self):
        opportunity = self.opportunity()
        extraction = OpportunityExtraction.objects.create(opportunity=opportunity, status='ready', model_name='test',
            input_hash=build_compact_opportunity_payload(opportunity)['input_hash'])
        opportunity.description = 'Not persisted'; opportunity.last_seen_at = timezone.now()
        opportunity.save(update_fields=['last_seen_at'])
        extraction.refresh_from_db(); self.assertEqual(extraction.status, 'ready')

    def test_reingestion_preserves_suppression_and_stales_changed_extraction(self):
        source = IngestionSource.objects.create(
            name='Example Careers', code='example-careers', source_type='scraper', company='Example Bank')
        class FakeAdapter:
            text = 'Data analysis duties.'
            def fetch(self):
                yield FetchedOpportunity(external_id='ingest-test', source_url='https://example.test/jobs/1',
                    apply_url='https://example.test/jobs/1', payload={'description': self.text},
                    fields={'title': 'Data Analyst', 'company': 'Test employer', 'description': self.text, 'country': 'South Africa'})
        adapter = FakeAdapter()
        self.assertEqual(run_adapter(adapter, source)['errors_count'], 0)
        opportunity = Opportunity.objects.get(title='Data Analyst')
        moderate_opportunity(opportunity_id=opportunity.pk, actor=self.moderator, status='suppressed')
        opportunity.refresh_from_db()
        extraction = OpportunityExtraction.objects.create(opportunity=opportunity, status='ready', model_name='test',
            input_hash=build_compact_opportunity_payload(opportunity)['input_hash'])
        adapter.text = 'SQL and different duties.'
        self.assertEqual(run_adapter(adapter, source)['errors_count'], 0)
        opportunity.refresh_from_db(); extraction.refresh_from_db()
        self.assertEqual(opportunity.moderation_status, 'suppressed')
        self.assertEqual(extraction.status, 'stale')
