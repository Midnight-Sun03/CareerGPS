from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = "Retired legacy trace command."

    def handle(self, *args, **options):
        raise CommandError("Retired: inspect ProfileOpportunityEvaluation and clean_jobs_status instead.")
