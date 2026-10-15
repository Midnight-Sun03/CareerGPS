import json
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from core.clean_jobs.source import build_export_batch


class Command(BaseCommand):
    help = 'Export all current job-only source payloads for external Clean-Job v1 processing.'

    def add_arguments(self, parser):
        parser.add_argument('--output', required=True, help='Destination JSONL file.')
        parser.add_argument('--opportunity-id', action='append', type=int, dest='opportunity_ids')

    def handle(self, *args, **options):
        output = Path(options['output']).expanduser()
        if output.exists() and output.is_dir():
            raise CommandError(f'Output path is a directory: {output}')
        ids = options.get('opportunity_ids') or []
        manifest, exported, exclusions = build_export_batch(ids)
        for item in exclusions:
            self.stderr.write(
                'EXCLUDED '
                f"category={item['category']} "
                f"source={item['source_name']}:{item['external_opportunity_id']} "
                f"opportunity_id={item['opportunity_id']} reason={item['reason']}")
        output.parent.mkdir(parents=True, exist_ok=True)
        with output.open('w', encoding='utf-8', newline='\n') as handle:
            handle.write(json.dumps(manifest, ensure_ascii=False, sort_keys=True) + '\n')
            for row in exported:
                handle.write(json.dumps(
                    {'record_type': 'source_job', **row},
                    ensure_ascii=False, sort_keys=True, default=str,
                ) + '\n')
        self.stdout.write(self.style.SUCCESS(
            f'Exported {len(exported)} current source job(s) to {output}. '
            f"excluded_scope={sum(row['category'] == 'scope' for row in exclusions)} "
            f"excluded_source_quality={sum(row['category'] == 'source_quality' for row in exclusions)} "
            f"reconciliation_digest={manifest['reconciliation_digest']}"))
