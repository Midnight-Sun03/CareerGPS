from django.core.management.base import BaseCommand
from django.db.models import Count

from core.models import Opportunity, OpportunityCleanAttempt, OpportunityCleanRevision


class Command(BaseCommand):
    help = 'Report Clean-Job v1 readiness and quarantine reconciliation counts.'

    def handle(self, *args, **options):
        attempts = list(OpportunityCleanAttempt.objects.values('status').annotate(count=Count('pk')).order_by('status'))
        revisions = list(OpportunityCleanRevision.objects.values('status').annotate(count=Count('pk')).order_by('status'))
        self.stdout.write(f'Attempts: {attempts}')
        self.stdout.write(f'Revisions: {revisions}')
        self.stdout.write(f'Professional catalogue (including obvious senior): {Opportunity.objects.professionally_ready().count()}')
        self.stdout.write(f'Candidate catalogue: {Opportunity.objects.professionally_ready(include_obvious_senior=False).count()}')
