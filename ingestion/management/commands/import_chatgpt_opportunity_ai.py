from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = "Retired legacy extraction importer; Clean-Job Contract v1 is authoritative."

    def add_arguments(self, parser):
        # Retain the old positional shape so operators receive the explicit
        # retirement error rather than an argument-parser error.
        parser.add_argument("path", nargs="?")
        parser.add_argument("--dry-run", action="store_true")
        parser.add_argument("--force-overwrite-ready", action="store_true")

    def handle(self, *args, **options):
        raise CommandError(
            "Retired: use export_clean_jobs_v1 and import_clean_jobs_v1. "
            "Legacy OpportunityExtraction rows cannot become professionally ready."
        )
