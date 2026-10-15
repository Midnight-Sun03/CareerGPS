"""Block 3 direct-publication and semantic-retirement regression coverage."""
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse

from core.clean_jobs.service import CleanImportError, import_clean_payload
from core.clean_jobs.source import build_export_batch, scope_exclusion_reason
from core.models import (
    CareerPreference, Opportunity, OpportunityCleanRevision,
    OpportunityTitleGateDecision, OpportunityTitleResolution, Profile,
    ProfileFeedEvaluation, ProfileSkill,
    TargetOpportunityRelationshipAssessment, TaxonomyConcept, TaxonomyLabel,
    TaxonomyRelease,
)
from core.services.title_gate import evaluate_revision
from core.services.feed_queue import drain_v2_queue
from core.services.feed_v2 import v2_feed_queryset
from core.services.career_brief import build_career_brief
from core.services.market_comparison import compare_profile_to_market
from core.test_support import valid_clean_payload
from ingestion.models import IngestionSource, OpportunitySourceRecord, RawOpportunity

from . import services


@override_settings(CAREERGPS_FEED_READ_VERSION='v2')
class DirectEmployerCleanFeedTests(TestCase):
    def setUp(self):
        self.owner = get_user_model().objects.create_user('block3-owner')
        self.candidate = get_user_model().objects.create_user('block3-candidate')
        self.profile = Profile.objects.create(user=self.candidate)
        self.organization = services.create_organization(
            user=self.owner, data={'name': 'Trusted Direct Employer'})
        self.organization.verification_status = 'approved'
        self.organization.save(update_fields=['verification_status'])
        for source in ('ofo', 'esco'):
            release = TaxonomyRelease.objects.create(
                source=source, version=f'block3-{source}', is_active=True)
            concept = TaxonomyConcept.objects.create(
                release=release, kind='occupation', external_id=f'{source}:data-analyst',
                preferred_label='Data Analyst', normalized_label='data analyst')
            TaxonomyLabel.objects.create(
                concept=concept, label='Data Analyst', normalized_label='data analyst',
                label_type='preferred')
        CareerPreference.objects.create(profile=self.profile, target_roles='Data Analyst')

    def requirement(self):
        return {'criterion_type': 'skill', 'requirement_level': 'required',
                'raw_value': 'Python'}

    def publish(self, *, title='Data Analyst'):
        vacancy = services.save_vacancy(
            user=self.owner, organization_id=self.organization.pk,
            data={
                'title': title, 'description': (
                    'Analyse operational data, prepare reports, and communicate '
                    'source-backed findings to the delivery team.'),
                'location': 'Cape Town', 'country': 'South Africa',
                'career_stage': 'early_career', 'opportunity_kind': 'job',
                'no_candidate_fees': True,
            },
            requirements=[self.requirement()])
        return services.publish_vacancy(
            user=self.owner, organization_id=self.organization.pk,
            vacancy_id=vacancy.pk)

    def payload(self, vacancy):
        record = OpportunitySourceRecord.objects.get(opportunity=vacancy.opportunity)
        return valid_clean_payload(
            record, title=vacancy.title, company=self.organization.name,
            description=vacancy.description,
            criteria=[{
                'criterion_id': 'skill-python', 'criterion_type': 'SKILL',
                'normalized_value': 'python', 'raw_value': 'Python',
                'requirement_level': 'REQUIRED', 'significance': 'CORE',
                'source_section': 'raw_content', 'requirement_group': None,
                'evidence': [{'text': 'Python'}], 'qualification': None,
                'experience': None, 'language': None,
            }])

    def drain(self):
        for _index in range(8):
            if not drain_v2_queue(limit=100, worker_id='block3-test'):
                break

    def test_approved_direct_publication_travels_through_clean_and_v2(self):
        vacancy = self.publish()
        record = OpportunitySourceRecord.objects.get(opportunity=vacancy.opportunity)
        self.assertFalse(record.source.active)
        manifest, rows, exclusions = build_export_batch([vacancy.opportunity_id])
        self.assertEqual(manifest['record_count'], 1)
        self.assertEqual(len(rows), 1)
        self.assertEqual(exclusions, [])
        self.assertFalse(Opportunity.objects.professionally_ready().filter(
            pk=vacancy.opportunity_id).exists())

        with self.captureOnCommitCallbacks(execute=True):
            result = import_clean_payload(self.payload(vacancy))
        self.assertTrue(result.activated)
        self.assertTrue(OpportunityCleanRevision.objects.filter(
            pk=result.revision_id, status='active').exists())
        self.drain()

        revision = OpportunityCleanRevision.objects.get(pk=result.revision_id)
        # The senior decision is read-only and recomputed on every read; no
        # decision row is written and none is consulted to admit the listing.
        self.assertEqual(evaluate_revision(revision).decision, 'ALLOW')
        self.assertFalse(OpportunityTitleGateDecision.objects.exists())
        self.assertEqual(revision.independent_occupation_resolution.state, 'READY')
        self.assertEqual(revision.independent_occupation_resolution.source_resolutions.filter(
            state='RESOLVED').count(), 2)
        assessment = TargetOpportunityRelationshipAssessment.objects.get(
            revision=revision, target_resolution__profile=self.profile)
        self.assertEqual(assessment.final_class, 'EXACT')
        evaluation = ProfileFeedEvaluation.objects.get(
            generation__profile=self.profile, opportunity=vacancy.opportunity,
            generation__state='ACTIVE')
        self.assertEqual(evaluation.discovery_category, 'EXACT')
        self.assertEqual(list(v2_feed_queryset(self.profile)), [evaluation])

    def test_direct_trust_gates_and_arbitrary_inactive_source(self):
        vacancy = self.publish()
        record = OpportunitySourceRecord.objects.get(opportunity=vacancy.opportunity)
        exported_payload = self.payload(vacancy)
        cases = (
            ('unapproved', self.organization, 'verification_status', 'submitted'),
            ('inactive organization', self.organization, 'account_status', 'suspended'),
            ('held', vacancy, 'trust_hold', True),
            ('draft', vacancy, 'status', 'draft'),
            ('fee policy', vacancy, 'no_candidate_fees', False),
        )
        for label, obj, field, value in cases:
            original = getattr(obj, field)
            setattr(obj, field, value)
            obj.save(update_fields=[field])
            record.refresh_from_db()
            with self.subTest(label=label):
                self.assertIsNotNone(scope_exclusion_reason(record))
            setattr(obj, field, original)
            obj.save(update_fields=[field])

        record.refresh_from_db()
        record.active_at_source = False
        record.save(update_fields=['active_at_source'])
        self.assertIn('no longer active', scope_exclusion_reason(record))
        record.active_at_source = True
        record.save(update_fields=['active_at_source'])

        vacancy.trust_hold = True
        vacancy.save(update_fields=['trust_hold'])
        with self.assertRaises(CleanImportError):
            import_clean_payload(exported_payload)
        vacancy.trust_hold = False
        vacancy.save(update_fields=['trust_hold'])

        source = IngestionSource.objects.create(
            name='Inactive external', code='inactive-external', source_type='api',
            active=False)
        opportunity = Opportunity.objects.create(
            title='External Analyst', company='External', description='A valid role description.',
            is_active=True, lifecycle_status='active', listing_kind='vacancy',
            scope_status='in_scope')
        raw = RawOpportunity.objects.create(
            source=source, source_external_id='one', content_hash='e' * 64,
            raw_content='External Analyst\nExternal\nA sufficiently detailed vacancy description for export.',
            raw_payload={'title': 'External Analyst', 'company': 'External',
                         'description': 'A sufficiently detailed vacancy description for export.'})
        external = OpportunitySourceRecord.objects.create(
            source=source, source_external_id='one', opportunity=opportunity,
            current_raw=raw, active_at_source=True)
        self.assertEqual(scope_exclusion_reason(external), 'ingestion source is disabled')

    def test_edit_invalidates_currentness_and_failed_import_cannot_restore_it(self):
        vacancy = self.publish()
        old_payload = self.payload(vacancy)
        first = import_clean_payload(old_payload)
        self.assertTrue(Opportunity.objects.professionally_ready().filter(
            pk=vacancy.opportunity_id).exists())
        old_raw_id = OpportunitySourceRecord.objects.get(
            opportunity=vacancy.opportunity).current_raw_id

        vacancy = services.save_vacancy(
            user=self.owner, organization_id=self.organization.pk,
            vacancy_id=vacancy.pk, data={'description': (
                'Analyse governed data, publish revised reports, and explain '
                'validated findings to operational stakeholders.')},
            requirements=[{'criterion_type': 'skill', 'requirement_level': 'required',
                           'raw_value': 'SQL'}])
        record = OpportunitySourceRecord.objects.get(opportunity=vacancy.opportunity)
        self.assertNotEqual(record.current_raw_id, old_raw_id)
        self.assertFalse(Opportunity.objects.professionally_ready().filter(
            pk=vacancy.opportunity_id).exists())
        self.assertEqual(vacancy.opportunity.active_clean_revision_id, first.revision_id)
        with self.assertRaises(CleanImportError):
            import_clean_payload(old_payload)
        self.assertFalse(v2_feed_queryset(self.profile).exists())

    def test_blocked_title_and_failed_clean_never_enter_feed(self):
        blocked = self.publish(title='Senior Data Analyst')
        payload = self.payload(blocked)
        invalid = deepcopy(payload)
        invalid['job']['cleaned_title'] = 'Invented Data Role'
        with self.assertRaises(CleanImportError):
            import_clean_payload(invalid)
        self.assertFalse(Opportunity.objects.professionally_ready().filter(
            pk=blocked.opportunity_id).exists())
        with self.captureOnCommitCallbacks(execute=True):
            import_clean_payload(payload)
        self.drain()
        revision = blocked.opportunity.clean_revisions.get()
        # A blocked title is excluded read-only before any resolution work, so
        # it is never reported as an occupation CareerGPS could not resolve.
        self.assertEqual(evaluate_revision(revision).decision, 'BLOCK')
        self.assertFalse(OpportunityTitleResolution.objects.filter(
            revision=revision).exists())
        self.assertFalse(ProfileFeedEvaluation.objects.filter(
            opportunity=blocked.opportunity).exists())

    def test_live_candidate_employer_market_and_career_paths_do_not_call_legacy_matcher(self):
        vacancy = self.publish()
        with self.captureOnCommitCallbacks(execute=True):
            import_clean_payload(self.payload(vacancy))
        self.drain()
        vacancy.opportunity.refresh_from_db()
        ProfileSkill.objects.create(
            profile=self.profile, entered_name='Python', confirmed_by_user=True)
        from employers.discovery import match_batch, profile_batch
        profile = list(profile_batch(Profile.objects.filter(pk=self.profile.pk)))[0]
        snapshot = SimpleNamespace(metrics={'requirements': {'skill': {'items': [{
            'label': 'Python', 'level': 'required', 'count': 1}]}}})
        with patch('core.matching.get_matched_opportunities',
                   side_effect=AssertionError('legacy feed called')), patch(
                       'core.matching.employer_evidence_match',
                       side_effect=AssertionError('legacy employer matcher called')):
            employer_row = match_batch([profile], vacancy)[0]
            self.assertEqual(employer_row[2].criteria[0].status.value, 'FOUND')
            self.assertEqual(employer_row[3]['class'], 'EXACT')
            market = compare_profile_to_market(self.profile, snapshot)
            self.assertEqual(market['supported'][0]['status'], 'FOUND')
            targets = list(self.profile.target_resolutions.all())
            self.assertEqual(build_career_brief(
                self.profile, targets)['exact_count'], 1)
            self.client.force_login(self.candidate)
            response = self.client.get(reverse('opportunities'))
            self.assertEqual(response.status_code, 200)
            self.assertContains(response, 'Exact match')
