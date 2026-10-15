"""Advance trusted source and published-closing-date lifecycle state."""

from django.core.management.base import BaseCommand

from ingestion.pipeline.lifecycle import refresh_opportunity_lifecycle


class Command(BaseCommand):
    help = "Expire published closing dates, notify interested users, and send closing-soon reminders. Run daily."

    def handle(self, *args, **options):
        refresh_opportunity_lifecycle()
        self.stdout.write(self.style.SUCCESS("Opportunity lifecycle refreshed."))
