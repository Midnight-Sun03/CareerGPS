from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = "Retired assignment-based market snapshot command."

    def handle(self, *args, **options):
        raise CommandError("Retired: legacy opportunity-career assignments were deleted.")
