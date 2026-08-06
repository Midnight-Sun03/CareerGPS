from django.core.management.base import BaseCommand
from core.services.opportunity_aggregator import OpportunityAggregator
from core.models import Opportunity
from datetime import datetime

class Command(BaseCommand):
    help = "Import opportunities from Adzuna only"
    
    def add_arguments(self, parser):
        parser.add_argument(
            '--clear',
            action='store_true',
            help='Clear existing opportunities before importing'
        )
    
    def handle(self, *args, **kwargs):
        clear = kwargs.get('clear', False)
        
        if clear:
            count = Opportunity.objects.count()
            Opportunity.objects.all().delete()
            self.stdout.write(f"🗑️  Cleared {count} existing opportunities")
        
        self.stdout.write(f"\n🚀 Starting Adzuna import at {datetime.now()}")
        
        results = OpportunityAggregator.fetch_all()
        
        self.stdout.write("\n" + "="*50)
        self.stdout.write("📊 IMPORT SUMMARY")
        self.stdout.write("="*50)
        
        total_fetched = 0
        total_saved = 0
        
        for source_name, source_result in results.get('sources', {}).items():
            status = source_result.get('status', 'unknown')
            fetched = source_result.get('fetched', 0)
            total_fetched += fetched
            
            if status == 'success':
                created = source_result.get('created', 0)
                updated = source_result.get('updated', 0)
                total_saved += created + updated
                self.stdout.write(f"✅ {source_name}: {fetched} found, {created} created, {updated} updated")
            elif status == 'skipped':
                self.stdout.write(f"⏭️  {source_name}: Skipped (no API key)")
                self.stdout.write("  📋 Get your keys at: https://developer.adzuna.com/")
            else:
                self.stdout.write(f"❌ {source_name}: {source_result.get('error', 'Unknown error')}")
        
        self.stdout.write("-"*50)
        self.stdout.write(f"📈 Total: {total_fetched} fetched, {total_saved} saved")
        self.stdout.write("="*50)