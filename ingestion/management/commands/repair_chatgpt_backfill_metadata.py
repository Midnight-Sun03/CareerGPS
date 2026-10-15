from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = "Retired legacy READY-metadata repair command."

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true")

    def handle(self, *args, **options):
        raise CommandError(
            "Retired: legacy READY extraction metadata is non-authoritative. "
            "Use the Clean-Job Contract v1 export/import workflow."
        )
