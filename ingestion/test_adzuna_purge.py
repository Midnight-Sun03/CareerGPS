"""Regression coverage for the one-way source purge migration."""
from datetime import date
from importlib.util import find_spec
from pathlib import Path
from tempfile import TemporaryDirectory

from django.contrib.auth.models import User
from django.core.files.base import ContentFile
from django.core.management import get_commands
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import SimpleTestCase, TransactionTestCase, override_settings


class RemovedIntegrationTests(SimpleTestCase):
    def test_no_active_adapter_command_or_setting(self):
        from django.conf import settings

        self.assertIsNone(find_spec('ingestion.adapters.adzuna'))
        self.assertNotIn('ingest_adzuna', get_commands())
        self.assertNotIn('repair_adzuna_salary_flags', get_commands())
        self.assertFalse(hasattr(settings, 'ADZUNA_APP_ID'))
        self.assertFalse(hasattr(settings, 'ADZUNA_API_KEY'))


class AdzunaPurgeMigrationTests(TransactionTestCase):
    common = [('core', '0012_privacyrequest_legalacceptanceevent_and_more'),
              ('employers', '0005_talentinvitation_and_more'),
              ('interview', '0004_interview_camera_analysis')]
    migrate_from = common + [('ingestion', '0009_alter_ingestionsource_source_type'),
                             ('applications', '0003_alter_application_status')]
    migrate_to = [('ingestion', '0010_purge_adzuna'),
                  ('applications', '0004_remove_adzuna_enrichment')] + common

    def tearDown(self):
        # Later clean-architecture tables have foreign keys to models created
        # after this historical migration target. Restore every app to its leaf
        # before TransactionTestCase flushes the database.
        executor = MigrationExecutor(connection)
        executor.migrate(executor.loader.graph.leaf_nodes())
        super().tearDown()

    def test_multi_source_preflight_stops_before_deletion(self):
        executor = MigrationExecutor(connection)
        executor.migrate(self.migrate_from)
        old = executor.loader.project_state(self.migrate_from).apps
        Source = old.get_model('ingestion', 'IngestionSource')
        Opportunity = old.get_model('core', 'Opportunity')
        SourceRecord = old.get_model('ingestion', 'OpportunitySourceRecord')
        source = Source.objects.create(name='Adzuna', code='adzuna', source_type='api')
        other_source = Source.objects.create(
            name='Employer', code='employer-test', source_type='scraper', company='Employer',
        )
        opportunity = Opportunity.objects.create(
            title='Shared job', company='Employer', opportunity_type='entry_level',
            source_platform='Adzuna',
        )
        SourceRecord.objects.create(
            opportunity=opportunity, source=source, source_external_id='aggregator-1',
        )
        other_record = SourceRecord.objects.create(
            opportunity=opportunity, source=other_source, source_external_id='employer-1',
        )
        try:
            with self.assertRaisesRegex(RuntimeError, 'another source record'):
                MigrationExecutor(connection).migrate(self.migrate_to)
            self.assertTrue(Opportunity.objects.filter(pk=opportunity.pk).exists())
            self.assertTrue(Source.objects.filter(pk=source.pk).exists())
        finally:
            SourceRecord.objects.filter(pk=other_record.pk).delete()
            MigrationExecutor(connection).migrate(self.migrate_to)

    def test_purge_removes_dependent_data_and_files_but_keeps_candidate_evidence(self):
        with TemporaryDirectory() as media_root, override_settings(MEDIA_ROOT=media_root):
            executor = MigrationExecutor(connection)
            executor.migrate(self.migrate_from)
            old = executor.loader.project_state(self.migrate_from).apps
            try:
                Source = old.get_model('ingestion', 'IngestionSource')
                Raw = old.get_model('ingestion', 'RawOpportunity')
                SourceRecord = old.get_model('ingestion', 'OpportunitySourceRecord')
                Opportunity = old.get_model('core', 'Opportunity')
                Profile = old.get_model('core', 'Profile')
                Education = old.get_model('core', 'Education')
                Saved = old.get_model('core', 'SavedOpportunity')
                Report = old.get_model('core', 'OpportunityReport')
                Notification = old.get_model('core', 'Notification')
                Release = old.get_model('core', 'TaxonomyRelease')
                Concept = old.get_model('core', 'TaxonomyConcept')
                Desired = old.get_model('core', 'DesiredCareer')
                Assignment = old.get_model('core', 'OpportunityCareerAssignment')
                Snapshot = old.get_model('core', 'CareerMarketSnapshot')
                Member = old.get_model('core', 'CareerMarketSnapshotOpportunity')
                Application = old.get_model('applications', 'Application')
                Document = old.get_model('applications', 'GeneratedDocument')
                Enrichment = old.get_model('applications', 'OpportunityJobEnrichment')
                Interview = old.get_model('interview', 'Interview')
                Question = old.get_model('interview', 'InterviewQuestion')
                Extraction = old.get_model('ingestion', 'OpportunityExtraction')
                Criterion = old.get_model('ingestion', 'OpportunityCriterion')

                user = User.objects.create_user('purge-candidate', password='test')
                profile = Profile.objects.create(user_id=user.pk, institution='Independent', location='Cape Town')
                evidence = Education.objects.create(profile=profile, institution='University', qualification='BSc')
                release = Release.objects.create(source='esco', version='purge-test', is_active=True)
                career = Concept.objects.create(
                    release=release, kind='occupation', external_id='purge-test-career',
                    preferred_label='Data Analyst', normalized_label='data analyst',
                )
                desired = Desired.objects.create(profile=profile, occupation=career, display_label='Data Analyst')
                source = Source.objects.create(name='Adzuna', code='adzuna', source_type='api')
                opportunity = Opportunity.objects.create(
                    title='Data Analyst', company='Source Employer',
                    opportunity_type='entry_level', source_platform='Adzuna',
                )
                raw = Raw.objects.create(
                    source=source, source_external_id='job-1', content_hash='a' * 64,
                )
                SourceRecord.objects.create(
                    opportunity=opportunity, source=source,
                    source_external_id='job-1', current_raw=raw,
                )
                Saved.objects.create(user_id=user.pk, opportunity=opportunity)
                report = Report.objects.create(
                    reporter_id=user.pk, opportunity=opportunity, reason='broken_link',
                )
                Notification.objects.create(
                    user_id=user.pk, title='Report resolved',
                    event_key=f'opportunityreport:resolved:{report.pk}',
                )
                enrichment = Enrichment.objects.create(
                    opportunity=opportunity, status='verified_match',
                )
                app = Application.objects.create(user_id=user.pk, opportunity=opportunity)
                extraction = Extraction.objects.create(
                    opportunity=opportunity, input_hash='d' * 64, model_name='test',
                )
                Criterion.objects.create(
                    extraction=extraction, criterion_type='skill', raw_value='SQL',
                    evidence_text='SQL',
                )
                document = Document.objects.create(
                    user_id=user.pk, application=app, opportunity=opportunity,
                    document_type='cv',
                )
                document.pdf_file.save('source-only.pdf', ContentFile(b'purge me'), save=True)
                file_path = Path(document.pdf_file.path)
                self.assertTrue(file_path.exists())
                document.docx_file.save('shared.docx', ContentFile(b'keep shared'), save=True)
                shared_path = Path(document.docx_file.path)
                independent_document = Document.objects.create(
                    user_id=user.pk, document_type='cv', docx_file=document.docx_file.name,
                )
                interview = Interview.objects.create(
                    user_id=user.pk, opportunity=opportunity, application=app,
                    source_type='application',
                )
                question = Question.objects.create(
                    interview=interview, question_index=0, question_text='Why this role?',
                )
                question.audio_file.save('source-only.wav', ContentFile(b'purge audio'), save=True)
                audio_path = Path(question.audio_file.path)
                self.assertTrue(audio_path.exists())
                assignment = Assignment.objects.create(
                    opportunity=opportunity, occupation=career, evidence_class='exact',
                    method='identity_label', assignment_version='career-title-v1',
                    input_hash='b' * 64,
                )
                snapshot = Snapshot.objects.create(
                    occupation=career, observation_start=date(2026, 1, 1),
                    observation_end=date(2026, 1, 31), aggregation_version='test',
                    assignment_version='career-title-v1', input_hash='c' * 64,
                )
                Member.objects.create(snapshot=snapshot, opportunity=opportunity)

                executor = MigrationExecutor(connection)
                executor.migrate(self.migrate_to)
                new = executor.loader.project_state(self.migrate_to).apps
                self.assertFalse(new.get_model('ingestion', 'IngestionSource').objects.filter(code='adzuna').exists())
                self.assertFalse(new.get_model('ingestion', 'RawOpportunity').objects.filter(pk=raw.pk).exists())
                self.assertFalse(new.get_model('core', 'Opportunity').objects.filter(pk=opportunity.pk).exists())
                self.assertFalse(new.get_model('core', 'OpportunityReport').objects.filter(pk=report.pk).exists())
                self.assertFalse(new.get_model('core', 'Notification').objects.filter(event_key=f'opportunityreport:resolved:{report.pk}').exists())
                self.assertFalse(new.get_model('core', 'OpportunityCareerAssignment').objects.filter(pk=assignment.pk).exists())
                self.assertFalse(new.get_model('core', 'CareerMarketSnapshot').objects.filter(pk=snapshot.pk).exists())
                self.assertFalse(new.get_model('applications', 'Application').objects.filter(pk=app.pk).exists())
                self.assertFalse(new.get_model('applications', 'GeneratedDocument').objects.filter(pk=document.pk).exists())
                self.assertTrue(new.get_model('applications', 'GeneratedDocument').objects.filter(pk=independent_document.pk).exists())
                self.assertFalse(new.get_model('interview', 'Interview').objects.filter(pk=interview.pk).exists())
                self.assertFalse(new.get_model('interview', 'InterviewQuestion').objects.filter(pk=question.pk).exists())
                self.assertFalse(new.get_model('ingestion', 'OpportunityExtraction').objects.filter(pk=extraction.pk).exists())
                self.assertFalse(file_path.exists())
                self.assertTrue(shared_path.exists())
                self.assertFalse(audio_path.exists())
                self.assertTrue(new.get_model('core', 'Education').objects.filter(pk=evidence.pk).exists())
                self.assertTrue(new.get_model('core', 'DesiredCareer').objects.filter(pk=desired.pk).exists())
            finally:
                MigrationExecutor(connection).migrate(self.migrate_to)
