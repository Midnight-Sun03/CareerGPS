from django.core.management.base import BaseCommand
from core.services.adzuna_simple import AdzunaAPI
from core.models import Opportunity

class Command(BaseCommand):
    help = "Import opportunities from Adzuna API"
    
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
        
        self.stdout.write("\n" + "="*50)
        self.stdout.write("🚀 FETCHING FROM ADZUNA API")
        self.stdout.write("="*50)
        
        api = AdzunaAPI()
        
        if not api.app_id or not api.api_key:
            self.stdout.write(self.style.ERROR(
                "\n❌ ADZUNA_APP_ID and ADZUNA_API_KEY not configured!\n"
                "\n📋 HOW TO SET UP:"
                "\n1. Go to: https://developer.adzuna.com/"
                "\n2. Sign up for free account"
                "\n3. Create an app to get APP_ID and API_KEY"
                "\n4. Add to settings.py:"
                "\n   ADZUNA_APP_ID = 'your_app_id'"
                "\n   ADZUNA_API_KEY = 'your_api_key'"
            ))
            return
        
        results = api.fetch_and_save_opportunities()
        
        self.stdout.write("\n" + "="*50)
        self.stdout.write("📊 IMPORT SUMMARY")
        self.stdout.write("="*50)
        self.stdout.write(f"📈 Total found: {results['total_fetched']}")
        self.stdout.write(f"✅ Created: {results['total_created']}")
        self.stdout.write(f"🔄 Updated: {results['total_updated']}")
        self.stdout.write(f"❌ Errors: {results['errors']}")
        
        if results['total_fetched'] > 0:
            self.stdout.write(self.style.SUCCESS("\n✅ Import completed successfully!"))
        else:
            self.stdout.write(self.style.WARNING("\n⚠️  No opportunities found. Check your API key."))
        self.stdout.write("="*50)