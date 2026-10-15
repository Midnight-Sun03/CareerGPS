import json
import tempfile
from copy import deepcopy
from hashlib import sha256
from pathlib import Path
from unittest.mock import patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase

from core.clean_jobs.service import CleanImportError, import_clean_payload, validate_clean_payload
from core.clean_jobs.source import (
    EXPORT_SCHEMA_VERSION, SELECTION_POLICY_VERSION, canonical_hash,
    scope_exclusion_reason, source_quality_issue,
)
from core.models import Opportunity, OpportunityCleanAttempt, OpportunityCleanRevision
from core.test_support import valid_clean_payload
from ingestion.models import IngestionSource, OpportunitySourceRecord, RawOpportunity


class CleanJobPipelineTests(TestCase):
    def setUp(self):
        self.source = IngestionSource.objects.create(
            name='Pipeline source', code='pipeline-source', source_type='api',
            company='Example Employer')
        self.records = [self._record(index) for index in (1, 2)]

    def _record(self, index):
        opportunity = Opportunity.objects.create(
            title=f'Data Analyst {index}', company='Example Employer',
            description='A source-grounded data analysis role with clear responsibilities.',
            location='Johannesburg', opportunity_type='entry_level',
            is_active=True, lifecycle_status='active', listing_kind='vacancy',
            scope_status='in_scope')
        text = (
            f'Data Analyst {index}\nExample Employer\nJohannesburg\nSouth Africa\n'
            'Vacancy\nA source-grounded data analysis role with clear responsibilities.\n'
            'SQL is required. Salary ZAR 10000 to 12000; negotiability is not stated.')
        raw = RawOpportunity.objects.create(
            source=self.source, source_external_id=f'job-{index}', raw_content=text,
            raw_payload={'title': f'Data Analyst {index}', 'company': 'Example Employer',
                         'description': text},
            content_hash=sha256(text.encode()).hexdigest())
        return OpportunitySourceRecord.objects.create(
            opportunity=opportunity, source=self.source,
            source_external_id=f'job-{index}', current_raw=raw,
            source_url=f'https://example.test/jobs/{index}')

    def payload(self, index=0):
        record = self.records[index]
        return valid_clean_payload(
            record, title=record.opportunity.title,
            company='Example Employer', description=record.opportunity.description,
            posted_at=None)

    def manifest(self, payloads):
        rows = []
        for payload in payloads:
            record = next(row for row in self.records
                          if row.source_external_id == payload['source']['external_opportunity_id'])
            rows.append({'opportunity_id': record.opportunity_id,
                         'source_input_hash': payload['source']['source_input_hash']})
        rows.sort(key=lambda row: row['opportunity_id'])
        return {
            'record_type': 'manifest', 'format_version': EXPORT_SCHEMA_VERSION,
            'contract_version': '1.1', 'record_count': len(rows),
            'reconciliation_digest': canonical_hash(rows),
            'data_boundary': 'job_source_data_only',
            'selection_policy_version': SELECTION_POLICY_VERSION,
        }

    def write_batch(self, rows):
        handle = tempfile.NamedTemporaryFile(
            mode='w', encoding='utf-8', suffix='.jsonl', delete=False)
        with handle:
            for row in rows:
                handle.write(json.dumps(row) + '\n')
        self.addCleanup(lambda: Path(handle.name).unlink(missing_ok=True))
        return handle.name

    def test_salary_negotiability_may_be_unknown(self):
        payload = self.payload()
        payload['job']['salary'] = {
            'raw': 'Salary ZAR 10000 to 12000', 'currency': 'ZAR',
            'minimum': '10000', 'maximum': '12000', 'period': 'UNKNOWN',
            'is_negotiable': None,
        }
        payload['job']['provenance']['salary'] = [{
            'source_section': 'raw_content',
            'evidence': {'text': 'Salary ZAR 10000 to 12000; negotiability is not stated.'},
        }]
        validate_clean_payload(payload)

    def test_salary_boolean_requires_an_explicit_compensation_statement(self):
        payload = self.payload()
        payload['job']['salary'] = {
            'raw': 'Salary ZAR 10000 to 12000', 'currency': 'ZAR',
            'minimum': '10000', 'maximum': '12000', 'period': 'UNKNOWN',
            'is_negotiable': False,
        }
        payload['job']['provenance']['salary'] = [{
            'source_section': 'raw_content',
            'evidence': {'text': 'Salary ZAR 10000 to 12000; negotiability is not stated.'},
        }]
        with self.assertRaisesRegex(CleanImportError, 'is_negotiable=false'):
            validate_clean_payload(payload)

    def test_normalized_value_rejects_paragraphs(self):
        payload = self.payload()
        payload['criteria'] = [{
            'criterion_id': 'sql', 'criterion_type': 'SKILL',
            'normalized_value': 'SQL\n' + ('copied advert text ' * 30),
            'raw_value': 'SQL', 'requirement_level': 'REQUIRED',
            'significance': 'CORE', 'source_section': 'raw_content',
            'evidence': [{'text': 'SQL is required.'}],
        }]
        with self.assertRaises(CleanImportError):
            validate_clean_payload(payload)

    def test_or_group_requires_consistent_obligation_metadata(self):
        payload = self.payload()
        base = {
            'criterion_type': 'SKILL', 'normalized_value': 'SQL', 'raw_value': 'SQL',
            'significance': 'CORE', 'source_section': 'raw_content',
            'requirement_group': {'group_id': 'alternative', 'operator': 'OR'},
            'evidence': [{'text': 'SQL is required.'}],
        }
        payload['criteria'] = [
            {**base, 'criterion_id': 'one', 'requirement_level': 'REQUIRED'},
            {**base, 'criterion_id': 'two', 'requirement_level': 'PREFERRED'},
        ]
        with self.assertRaises(CleanImportError):
            validate_clean_payload(payload)

    def test_invented_title_is_rejected_by_provenance(self):
        payload = self.payload()
        payload['job']['cleaned_title'] = 'Invented Quantum Surgeon'
        with self.assertRaisesRegex(CleanImportError, 'not textually supported'):
            validate_clean_payload(payload)

    def test_deliberate_contract_and_source_tampering_is_rejected(self):
        criterion = {
            'criterion_id': 'sql', 'criterion_type': 'SKILL',
            'normalized_value': 'SQL', 'raw_value': 'SQL',
            'requirement_level': 'REQUIRED', 'significance': 'CORE',
            'source_section': 'raw_content',
            'evidence': [{'text': 'SQL is required.'}],
        }
        cases = {}
        payload = self.payload(); payload['source']['source_input_hash'] = '0' * 64
        cases['wrong hash'] = payload
        payload = self.payload(); payload['source']['source_url'] = 'https://stale.example/job'
        cases['stale URL'] = payload
        payload = self.payload(); payload['source']['captured_at'] = '2000-01-01T00:00:00+00:00'
        cases['stale captured timestamp'] = payload
        payload = self.payload(); payload['criteria'] = [deepcopy(criterion)]
        payload['criteria'][0]['evidence'][0]['text'] = 'Invented evidence'
        cases['wrong evidence'] = payload
        payload = self.payload(); payload['criteria'] = [deepcopy(criterion)]
        payload['criteria'][0]['source_section'] = 'raw_payload.missing'
        cases['invalid section'] = payload
        payload = self.payload()
        payload['job']['provenance']['cleaned_title'][0]['evidence']['start_char'] = 0
        cases['one-sided offset'] = payload
        payload = self.payload(); payload['criteria'] = [deepcopy(criterion), deepcopy(criterion)]
        cases['duplicate criterion ID'] = payload
        payload = self.payload(); payload['criteria'] = [deepcopy(criterion)]
        payload['criteria'][0]['requirement_group'] = {'group_id': 'alone', 'operator': 'OR'}
        cases['malformed OR group'] = payload
        payload = self.payload(); payload['unknown_extra_field'] = True
        cases['unknown extra field'] = payload

        for label, invalid in cases.items():
            with self.subTest(label=label), self.assertRaises(CleanImportError):
                validate_clean_payload(invalid)
        self.assertEqual(OpportunityCleanAttempt.objects.count(), 0)
        self.assertEqual(OpportunityCleanRevision.objects.count(), 0)

    def test_dry_run_validates_complete_batch_with_zero_writes(self):
        payloads = [self.payload(0), self.payload(1)]
        path = self.write_batch([self.manifest(payloads), *payloads])
        call_command('import_clean_jobs_v1', path, '--dry-run')
        self.assertEqual(OpportunityCleanAttempt.objects.count(), 0)
        self.assertEqual(OpportunityCleanRevision.objects.count(), 0)

    def test_non_object_json_is_controlled_and_writes_nothing(self):
        path = self.write_batch([self.manifest([]), ['not', 'an', 'object']])
        with self.assertRaises(CommandError):
            call_command('import_clean_jobs_v1', path, '--dry-run')
        self.assertEqual(OpportunityCleanAttempt.objects.count(), 0)

    def test_manifest_rejects_missing_and_duplicate_results(self):
        payloads = [self.payload(0), self.payload(1)]
        missing = self.write_batch([self.manifest(payloads), payloads[0]])
        with self.assertRaises(CommandError):
            call_command('import_clean_jobs_v1', missing, '--dry-run')
        duplicate = self.write_batch([self.manifest(payloads), payloads[0], payloads[0]])
        with self.assertRaises(CommandError):
            call_command('import_clean_jobs_v1', duplicate, '--dry-run')
        self.assertEqual(OpportunityCleanAttempt.objects.count(), 0)

    def test_manifest_rejects_unexpected_identity_digest_and_extra_fields(self):
        payload = self.payload()
        unexpected = deepcopy(payload)
        unexpected['source']['external_opportunity_id'] = 'job-that-was-not-exported'
        path = self.write_batch([self.manifest([payload]), unexpected])
        with self.assertRaises(CommandError):
            call_command('import_clean_jobs_v1', path, '--dry-run')

        bad_manifest = self.manifest([payload])
        bad_manifest['reconciliation_digest'] = '0' * 64
        path = self.write_batch([bad_manifest, payload])
        with self.assertRaises(CommandError):
            call_command('import_clean_jobs_v1', path, '--dry-run')

        extra_manifest = self.manifest([payload])
        extra_manifest['ignored'] = 'must not be ignored'
        path = self.write_batch([extra_manifest, payload])
        with self.assertRaises(CommandError):
            call_command('import_clean_jobs_v1', path, '--dry-run')
        self.assertEqual(OpportunityCleanAttempt.objects.count(), 0)

    def test_unexpected_activation_failure_rolls_back_whole_batch(self):
        payloads = [self.payload(0), self.payload(1)]
        path = self.write_batch([self.manifest(payloads), *payloads])
        real_import = import_clean_payload
        calls = {'count': 0}

        def fail_second(payload):
            calls['count'] += 1
            if calls['count'] == 2:
                raise RuntimeError('simulated late failure')
            return real_import(payload)

        with patch(
                'core.management.commands.import_clean_jobs_v1.import_clean_payload',
                side_effect=fail_second), self.assertRaises(CommandError):
            call_command('import_clean_jobs_v1', path)
        self.assertEqual(OpportunityCleanAttempt.objects.count(), 0)
        self.assertEqual(OpportunityCleanRevision.objects.count(), 0)
        self.assertFalse(Opportunity.objects.filter(
            active_clean_revision__isnull=False).exists())

    def test_scope_and_quality_checks_are_deterministic(self):
        record = self.records[0]
        record.opportunity.listing_kind = 'talent_pool'
        self.assertIn('listing_kind', scope_exclusion_reason(record))
        record.opportunity.listing_kind = 'vacancy'
        record.current_raw.raw_content = '[COPY AND PASTE THE LATEST ROLE DESCRIPTION]'
        record.current_raw.raw_payload = {
            'title': 'Data Analyst', 'company': 'Example Employer',
            'description': '[COPY AND PASTE THE LATEST ROLE DESCRIPTION]',
        }
        self.assertIn('placeholder', source_quality_issue(record))
