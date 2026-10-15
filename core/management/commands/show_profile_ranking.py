from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = "Retired legacy ranking command."

    def handle(self, *args, **options):
        raise CommandError("Retired: rankings are read from materialized ProfileOpportunityEvaluation rows.")
