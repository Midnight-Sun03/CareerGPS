from django.core.management.base import BaseCommand, CommandError

from ingestion.adapters.companies import SCRAPER_CLASSES
from ingestion.models import IngestionSource
from ingestion.pipeline import run_adapter
from ._output import add_explain_arguments, explain_options, render_summary


class Command(BaseCommand):
    help = "Run one or all configured direct-company scrapers."

    def add_arguments(self, parser):
        parser.add_argument("--source", choices=sorted(SCRAPER_CLASSES), help="Only run one company source.")
        parser.add_argument("--max-pages", type=int, help="Optional page safety limit; omitted means follow pagination to its end.")
        parser.add_argument("--max-jobs", type=int)
        parser.add_argument("--dry-run", action="store_true")
        add_explain_arguments(parser)

    def handle(self, *args, **options):
        diagnostics = explain_options(options)
        codes = [options["source"]] if options.get("source") else list(SCRAPER_CLASSES)
        configured = {source.code: source for source in IngestionSource.objects.filter(code__in=codes, active=True)}
        if options.get("source") and options["source"] not in configured:
            raise CommandError(f"The active source '{options['source']}' is not configured. Run migrations first.")
        for code in codes:
            source = configured.get(code)
            if not source:
                self.stderr.write(self.style.WARNING(f"Skipping unconfigured or inactive source: {code}"))
                continue
            scraper = SCRAPER_CLASSES[code](max_pages=options["max_pages"], max_jobs=options["max_jobs"])
            counters = run_adapter(scraper, source, dry_run=options["dry_run"], **diagnostics)
            render_summary(self, code, counters, options["dry_run"])
