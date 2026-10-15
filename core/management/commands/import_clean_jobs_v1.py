import json
from collections import Counter
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from core.clean_jobs import CleanImportError, import_clean_payload, validate_clean_batch


class Command(BaseCommand):
    help = 'Validate and atomically activate externally cleaned Clean-Job Contract v1 JSONL.'

    def add_arguments(self, parser):
        parser.add_argument('path', help='Clean result JSONL file.')
        parser.add_argument('--stop-on-error', action='store_true')
        parser.add_argument(
            '--dry-run', action='store_true',
            help='Validate and reconcile the complete batch with zero database writes.')

    def handle(self, *args, **options):
        path = Path(options['path']).expanduser()
        if not path.is_file():
            raise CommandError(f'Clean result file not found: {path}')
        counters = Counter()
        diagnostics = []
        manifests = []
        payload_rows = []
        with path.open('r', encoding='utf-8') as handle:
            for line_number, line in enumerate(handle, start=1):
                if not line.strip():
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError as exc:
                    diagnostics.append(f'line {line_number}: invalid JSON: {exc}')
                    continue
                if not isinstance(row, dict):
                    diagnostics.append(
                        f'line {line_number}: record must be a JSON object, got '
                        f'{type(row).__name__}')
                    continue
                if row.get('record_type') == 'manifest':
                    manifests.append((line_number, row))
                    continue
                payload_rows.append((
                    line_number,
                    row.get('payload') if row.get('record_type') == 'clean_job' else row))

        if len(manifests) != 1:
            diagnostics.append(f'batch must contain exactly one manifest; found {len(manifests)}')
        if diagnostics:
            for message in diagnostics[:100]:
                self.stderr.write(message)
            raise CommandError(f'Batch parse failed with {len(diagnostics)} error(s); zero payloads imported.')
        try:
            batch = validate_clean_batch(manifests[0][1], payload_rows)
        except CleanImportError as exc:
            for message in exc.errors[:100]:
                self.stderr.write(message)
            raise CommandError(
                f'Batch validation failed with {len(exc.errors)} error(s); zero payloads imported.')

        self.stdout.write(
            f"Preflight passed: manifest=1 payloads={len(batch.rows)} "
            f"digest={batch.manifest['reconciliation_digest']}")
        if options['dry_run']:
            self.stdout.write(self.style.SUCCESS(
                'Clean v1.1 dry run completed successfully; zero database writes performed.'))
            return

        # The file is fully parsed and validated before the transaction opens.
        # Revalidation inside import_clean_payload catches source races; any
        # failure rolls back every attempt, revision, activation, and on-commit
        # queue callback in this batch.
        try:
            with transaction.atomic():
                for _line_number, payload, _validated in batch.rows:
                    result = import_clean_payload(payload)
                    counters['activated' if result.activated else 'validated'] += 1
                    counters['created' if result.created else 'reused'] += 1
        except Exception as exc:
            raise CommandError(
                f'Atomic batch import failed and was rolled back: {exc}') from exc

        self.stdout.write('Reconciliation: ' + ', '.join(
            f'{key}={value}' for key, value in sorted(counters.items())))
        self.stdout.write(self.style.SUCCESS('Clean v1 import completed successfully.'))
