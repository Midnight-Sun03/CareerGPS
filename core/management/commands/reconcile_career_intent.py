from django.core.management.base import BaseCommand

from core.models import CareerPreference
from core.services.professional_resolution import resolve_preference_targets


class Command(BaseCommand):
    help = "Rebuild target resolutions from authoritative CareerPreference target text."

    def handle(self, *args, **options):
        count = 0
        for preference in CareerPreference.objects.iterator(chunk_size=200):
            count += len(resolve_preference_targets(preference))
        self.stdout.write(self.style.SUCCESS(f"Resolved {count} target role(s)."))
