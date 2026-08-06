from typing import Dict
import logging
from core.services.adzuna_simple import AdzunaAPI
from core.services.opportunity_service import OpportunityService

logger = logging.getLogger(__name__)

class OpportunityAggregator:
    """Aggregate opportunities from Adzuna only"""
    
    @classmethod
    def fetch_all(cls) -> Dict:
        """Fetch opportunities from Adzuna only"""
        results = {
            'total_fetched': 0,
            'total_saved': 0,
            'sources': {}
        }
        
        # Adzuna API (15-20 jobs per search term)
        print("\n📊 Adzuna API...")
        try:
            adzuna = AdzunaAPI()
            if adzuna.app_id and adzuna.api_key:
                adzuna_results = adzuna.fetch_and_save_opportunities()
                results['sources']['Adzuna'] = {
                    'fetched': adzuna_results['total_fetched'],
                    'created': adzuna_results['total_created'],
                    'updated': adzuna_results['total_updated'],
                    'status': 'success'
                }
                results['total_fetched'] += adzuna_results['total_fetched']
                results['total_saved'] += adzuna_results['total_created'] + adzuna_results['total_updated']
            else:
                print("  ⚠️ Adzuna credentials not configured")
                print("  📋 Get your keys at: https://developer.adzuna.com/")
                results['sources']['Adzuna'] = {'status': 'skipped', 'fetched': 0}
        except Exception as e:
            print(f"  ❌ Adzuna error: {str(e)[:50]}")
            results['sources']['Adzuna'] = {'status': 'error', 'error': str(e), 'fetched': 0}
        
        return results