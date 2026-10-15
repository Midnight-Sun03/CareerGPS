from django.core.management.base import BaseCommand
from django.db.models import Count

from core.models import Opportunity
from ingestion.models import OpportunityExtraction


class Command(BaseCommand):
    help = "Show Phase 2.4 post-AI opportunity extraction coverage."

    def handle(self, *args, **options):
        live = Opportunity.objects.discoverable().count()
        counts = dict(
            OpportunityExtraction.objects.values_list("status")
            .annotate(total=Count("id"))
        )
        ready_live = OpportunityExtraction.objects.filter(
            status="ready",
            opportunity__is_active=True,
            opportunity__lifecycle_status="active",
            opportunity__scope_status__in=("in_scope", "unclassified"),
        ).count()
        partial_live = OpportunityExtraction.objects.filter(
            status="ready",
            source_completeness="partial_text",
            opportunity__is_active=True,
            opportunity__lifecycle_status="active",
            opportunity__scope_status__in=("in_scope", "unclassified"),
        ).count()
        self.stdout.write(f"Live/discoverable opportunities: {live}")
        self.stdout.write(f"READY live extractions: {ready_live}")
        self.stdout.write(f"READY partial-source extractions: {partial_live}")
        self.stdout.write(f"Coverage: {(ready_live / live * 100):.1f}%" if live else "Coverage: n/a")
        for status in ("pending", "processing", "ready", "failed", "stale"):
            self.stdout.write(f"{status}: {counts.get(status, 0)}")
