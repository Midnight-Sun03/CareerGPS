import logging
from datetime import datetime
from typing import List, Dict, Optional
from core.models import Opportunity, Skill

logger = logging.getLogger(__name__)

class OpportunityService:
    """Service for managing opportunities"""
    
    @staticmethod
    def create_or_update_opportunity(opp_data: Dict) -> Optional[Opportunity]:
        """Create or update a single opportunity"""
        try:
            required = ['title', 'company', 'opportunity_type']
            for field in required:
                if not opp_data.get(field):
                    logger.warning(f"Missing required field: {field}")
                    return None
            
            # Separate skills from main data
            skills_list = opp_data.pop('required_skills', [])
            
            # Get or create the opportunity
            opportunity, created = Opportunity.objects.update_or_create(
                title=opp_data['title'],
                company=opp_data['company'],
                defaults={
                    'opportunity_type': opp_data.get('opportunity_type', 'internship'),
                    'sector': opp_data.get('sector', 'General'),
                    'location': opp_data.get('location', 'South Africa'),
                    'duration': opp_data.get('duration', '12 Months'),
                    'stipend': opp_data.get('stipend', 'Market Related'),
                    'required_degree': opp_data.get('required_degree', ''),
                    'minimum_nqf': opp_data.get('minimum_nqf', 4),
                    'description': opp_data.get('description', ''),
                    'duties': opp_data.get('duties', ''),
                    'eligibility': opp_data.get('eligibility', 'South African citizens'),
                    'source_platform': opp_data.get('source_platform', 'API'),
                    'redirect_url': opp_data.get('redirect_url', '#'),
                    'application_deadline': opp_data.get('application_deadline'),
                    'is_active': opp_data.get('is_active', True),
                }
            )
            
            # Handle skills separately (many-to-many)
            if skills_list:
                skills = []
                for skill_name in skills_list:
                    if skill_name and skill_name.strip():
                        skill, _ = Skill.objects.get_or_create(name=skill_name.strip()[:100])
                        skills.append(skill)
                if skills:
                    opportunity.required_skills.set(skills)
            
            logger.info(f"{'Created' if created else 'Updated'}: {opportunity.title}")
            return opportunity
            
        except Exception as e:
            logger.error(f"Error saving opportunity {opp_data.get('title')}: {str(e)}")
            return None
    
    @staticmethod
    def bulk_save_opportunities(opportunities: List[Dict]) -> Dict:
        """Save multiple opportunities"""
        results = {
            'created': 0,
            'updated': 0,
            'errors': 0,
            'skipped': 0,
            'details': []
        }
        
        for opp_data in opportunities:
            try:
                opp = OpportunityService.create_or_update_opportunity(opp_data)
                if opp:
                    # Check if it was created today
                    if opp.created_at.date() == datetime.now().date():
                        results['created'] += 1
                        results['details'].append(f"✅ Created: {opp.title}")
                    else:
                        results['updated'] += 1
                        results['details'].append(f"🔄 Updated: {opp.title}")
                else:
                    results['skipped'] += 1
            except Exception as e:
                results['errors'] += 1
                results['details'].append(f"❌ Error: {str(e)}")
        
        return results
    
    @staticmethod
    def deactivate_expired_opportunities(days_after: int = 30) -> int:
        """Deactivate opportunities past their deadline"""
        from datetime import date, timedelta
        cutoff_date = date.today() - timedelta(days=days_after)
        
        expired = Opportunity.objects.filter(
            application_deadline__lt=cutoff_date,
            is_active=True
        )
        
        count = expired.count()
        expired.update(is_active=False)
        logger.info(f"Deactivated {count} expired opportunities")
        return count