from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = "Retired internal Gemini job-cleaning command."

    def add_arguments(self, parser):
        # Preserve accepted legacy flags solely to produce one clear retirement
        # diagnostic for old operator scripts. No provider code is imported.
        parser.add_argument("--limit", type=int)
        parser.add_argument("--batch-size", type=int)
        parser.add_argument("--opportunity-id", action="append", type=int, dest="opportunity_ids")
        parser.add_argument("--source", action="append", dest="sources")
        parser.add_argument("--force", action="store_true")
        parser.add_argument("--model")
        parser.add_argument("--thinking-level")
        parser.add_argument("--timeout-seconds", type=int)
        parser.add_argument("--include-nondiscoverable", action="store_true")

    def handle(self, *args, **options):
        raise CommandError(
            "Internal Gemini opportunity extraction is disabled. Use "
            "export_clean_jobs_v1 and import_clean_jobs_v1."
        )
