"""Bulk Greenhouse registration must be complete, conservative and repeatable."""
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from django.core.management import call_command
from django.test import TestCase

from ingestion.management.commands.add_greenhouse_sources import (
    existing_board_or_source, register_board, source_identity, validate_board,
)
from ingestion.models import GreenhouseBoard, IngestionSource, IngestionRun, RawOpportunity


class GreenhouseSourceCommandTests(TestCase):
    def setUp(self):
        self.temporary = TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.path = Path(self.temporary.name) / 'boards.txt'
        self.initial_greenhouse_sources = IngestionSource.objects.filter(code__startswith='greenhouse-').count()
        self.initial_greenhouse_boards = GreenhouseBoard.objects.count()

    def run_batch(self, lines, *, dry_run=False, validator=None):
        self.path.write_text(lines, encoding='utf-8')
        output = StringIO()
        with patch('ingestion.management.commands.add_greenhouse_sources.validate_board',
                   side_effect=validator or (lambda token, client: token.title())):
            call_command('add_greenhouse_sources', str(self.path), dry_run=dry_run, stdout=output)
        return output.getvalue()

    def existing(self, token='existing'):
        kind, board = register_board(token, 'Existing Employer')
        self.assertEqual(kind, 'created')
        return board

    def greenhouse_source_count(self):
        return (IngestionSource.objects.filter(code__startswith='greenhouse-').count()
                - self.initial_greenhouse_sources)

    def greenhouse_board_count(self):
        return GreenhouseBoard.objects.count() - self.initial_greenhouse_boards

    def test_new_valid_token_creates_complete_admin_visible_pair(self):
        output = self.run_batch('newemployer\n', validator=lambda token, client: 'New Employer')
        board = GreenhouseBoard.objects.select_related('source').get(board_token='newemployer')
        source = board.source
        self.assertIn('CREATED newemployer — New Employer', output)
        self.assertEqual(source.code, 'greenhouse-newemployer')
        self.assertEqual(source.name, 'New Employer Greenhouse')
        self.assertEqual(source.source_type, 'api')
        self.assertEqual(source.company, 'New Employer')
        self.assertEqual(source.base_url, source_identity('newemployer')[1])
        self.assertEqual(source.priority, 15)
        self.assertTrue(source.active)
        self.assertEqual(board.company_name, 'New Employer')
        self.assertEqual(board.careers_url, 'https://job-boards.greenhouse.io/newemployer')
        self.assertEqual(board.country_codes, ['ZA'])
        self.assertTrue(board.active)
        self.assertEqual(board.source_id, source.pk)
        self.assertFalse(IngestionRun.objects.exists())
        self.assertFalse(RawOpportunity.objects.exists())

    def test_existing_pair_is_skipped_without_validation_or_modification(self):
        board = self.existing()
        before = list(IngestionSource.objects.filter(pk=board.source_id).values())[0]
        board_before = list(GreenhouseBoard.objects.filter(pk=board.pk).values())[0]
        output = self.run_batch('existing\n', validator=lambda *_: self.fail('Should not validate an existing board'))
        self.assertIn('SKIPPED existing — already exists', output)
        self.assertEqual(list(IngestionSource.objects.filter(pk=board.source_id).values())[0], before)
        self.assertEqual(list(GreenhouseBoard.objects.filter(pk=board.pk).values())[0], board_before)

    def test_duplicate_input_and_second_run_create_once(self):
        output = self.run_batch('newboard\nNEWBOARD\n# comment\n\n')
        self.assertIn('duplicate_input=1', output)
        self.assertEqual(GreenhouseBoard.objects.filter(board_token='newboard').count(), 1)
        second = self.run_batch('newboard\n', validator=lambda *_: self.fail('Already registered'))
        self.assertIn('created=0', second)
        self.assertEqual(self.greenhouse_source_count(), 1)

    def test_mixed_existing_and_new(self):
        self.existing()
        output = self.run_batch('existing\nnewboard\n')
        self.assertIn('existing=1', output)
        self.assertIn('created=1', output)
        self.assertEqual(self.greenhouse_source_count(), 2)
        self.assertEqual(self.greenhouse_board_count(), 2)

    def test_invalid_and_failed_token_leave_neither_model_and_do_not_stop_batch(self):
        def validator(token, client):
            if token == 'invalidboard':
                raise ValueError('no public board')
            return 'Valid Employer'
        output = self.run_batch('invalidboard\nbad/token\nvalidboard\n', validator=validator)
        self.assertIn('FAILED invalidboard', output)
        self.assertIn('FAILED bad/token', output)
        self.assertIn('CREATED validboard', output)
        self.assertEqual(self.greenhouse_source_count(), 1)
        self.assertEqual(self.greenhouse_board_count(), 1)

    def test_existing_source_without_board_is_not_duplicated_or_repaired(self):
        code, root = source_identity('orphan')
        IngestionSource.objects.create(name='Old source', code=code, source_type='api', base_url=root)
        output = self.run_batch('orphan\n', validator=lambda *_: self.fail('Should not validate'))
        self.assertIn('manual review required', output)
        self.assertEqual(self.greenhouse_source_count(), 1)
        self.assertFalse(GreenhouseBoard.objects.filter(board_token='orphan').exists())
        self.assertEqual(existing_board_or_source('orphan')[0], 'source')

    def test_board_failure_rolls_back_its_source_and_later_token_succeeds(self):
        real_save = GreenhouseBoard.save

        def save(board, *args, **kwargs):
            if board.board_token == 'broken':
                raise RuntimeError('simulated board failure')
            return real_save(board, *args, **kwargs)

        with patch.object(GreenhouseBoard, 'save', save):
            output = self.run_batch('broken\nhealthy\n')
        self.assertIn('registration rolled back', output)
        self.assertIn('CREATED healthy', output)
        self.assertFalse(IngestionSource.objects.filter(code='greenhouse-broken').exists())
        self.assertFalse(GreenhouseBoard.objects.filter(board_token='broken').exists())
        self.assertEqual(self.greenhouse_source_count(), 1)

    def test_dry_run_validates_but_writes_nothing(self):
        output = self.run_batch('newboard\n', dry_run=True)
        self.assertIn('VALID newboard', output)
        self.assertIn('would create source and board', output)
        self.assertEqual(self.greenhouse_source_count(), 0)
        self.assertEqual(self.greenhouse_board_count(), 0)

    def test_public_board_validation_checks_name_jobs_and_rejects_sandbox(self):
        class Client:
            def __init__(self, board, jobs):
                self.board, self.jobs = board, jobs

            def get_json(self, url, **kwargs):
                return self.jobs if url.endswith('/jobs') else self.board

        valid_jobs = {'jobs': [{'id': 1, 'title': 'Engineer',
                                'absolute_url': 'https://job-boards.greenhouse.io/real/jobs/1'}]}
        self.assertEqual(validate_board('real', Client({'name': 'Real &amp; Co'}, valid_jobs)), 'Real & Co')
        for board, jobs in [({'name': 'Sandbox Board'}, valid_jobs),
                            ({'name': ''}, valid_jobs),
                            ({'name': 'Real Co'}, {'jobs': 'malformed'}),
                            ({'name': 'Real Co'}, {'jobs': [{'id': 1}]})]:
            with self.subTest(board=board, jobs=jobs), self.assertRaises(ValueError):
                validate_board('real', Client(board, jobs))
