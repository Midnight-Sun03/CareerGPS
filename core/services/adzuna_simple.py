import requests
import logging
from datetime import datetime, timedelta
from typing import List, Dict, Optional
from core.models import Opportunity, Skill

logger = logging.getLogger(__name__)

class AdzunaAPI:
    """
    Adzuna API - Optimized for speed
    """
    
    def __init__(self):
        self.app_id = self._get_app_id()
        self.api_key = self._get_api_key()
        self.base_url = "https://api.adzuna.com/v1/api/jobs/za"
        self.requests_made = 0
        self.max_requests = 2000
        self.session = requests.Session()  # Use session for reusing connection
        self.session.headers.update({
            'User-Agent': 'CareerGPS/1.0',
            'Accept-Encoding': 'gzip',  # Faster responses
        })
    
    def _get_app_id(self) -> str:
        from django.conf import settings
        return getattr(settings, 'ADZUNA_APP_ID', '')
    
    def _get_api_key(self) -> str:
        from django.conf import settings
        return getattr(settings, 'ADZUNA_API_KEY', '')
    
    def search_jobs(self, what: str, where: str = "South Africa", max_results: int = 50, page: int = 1) -> List[Dict]:
        """Search for jobs - OPTIMIZED for speed"""
        if not self.app_id or not self.api_key:
            return []
        
        url = f"{self.base_url}/search/{page}"
        
        params = {
            'app_id': self.app_id,
            'app_key': self.api_key,
            'what': what,
            'where': where,
            'content-type': 'application/json',
            'results_per_page': max_results,
        }
        
        try:
            self.requests_made += 1
            
            # Use session for faster requests
            response = self.session.get(url, params=params, timeout=15)
            
            if response.status_code == 200:
                data = response.json()
                if data.get('results'):
                    return data['results']
                return []
            elif response.status_code == 429:
                # Only wait when rate limited
                import time
                print("  ⏳ Rate limit... waiting 2s")
                time.sleep(2)
                return []
            else:
                return []
                
        except Exception as e:
            return []
    
    def fetch_and_save_opportunities(self) -> Dict:
        """Fetch opportunities - OPTIMIZED for speed"""
        results = {
            'total_fetched': 0,
            'total_created': 0,
            'total_updated': 0,
            'errors': 0,
        }
        
        # REDUCED: Fewer search terms for speed
        search_terms = [
            'internship',
            'learnership', 
            'graduate programme',
            'yes programme',
            'entry level',
            'junior',
        ]
        
        # REDUCED: Fewer locations
        locations = [
            "South Africa",
            "Johannesburg",
            "Cape Town",
            "Durban",
            "Pretoria",
            "Port Elizabeth",
            "Bloemfontein",
            "Midrand",      
            "Sandton",      
            "Centurion",
            "Randburg",
            "Umhlanga",
            "East London",
        ]
        
        # REDUCED: Only 2 pages per search
        max_pages = 1
        
        for term in search_terms:
            for location in locations:
                print(f"📊 {term} in {location}")
                
                for page in range(1, max_pages + 1):
                    jobs = self.search_jobs(term, location, max_results=30, page=page)
                    
                    if not jobs:
                        break
                    
                    # Save jobs immediately (don't wait)
                    for job in jobs:
                        try:
                            opp = self._parse_job(job, term)
                            if opp:
                                saved = self._save_opportunity(opp)
                                if saved:
                                    if saved.get('created'):
                                        results['total_created'] += 1
                                    else:
                                        results['total_updated'] += 1
                                    results['total_fetched'] += 1
                        except:
                            results['errors'] += 1
                    
                    # Check limit
                    if self.requests_made >= self.max_requests:
                        print(f"⚠️  Reached {self.max_requests} requests limit!")
                        break
                
                if self.requests_made >= self.max_requests:
                    break
            
            if self.requests_made >= self.max_requests:
                break
        
        print(f"\n📊 Requests made: {self.requests_made}")
        return results
    
    def _parse_job(self, job: Dict, search_term: str) -> Optional[Dict]:
        """Parse job data - simplified for speed"""
        try:
            type_map = {
                'internship': 'internship',
                'learnership': 'learnership',
                'graduate': 'graduate',
                'graduate programme': 'graduate',
                'trainee': 'internship',
                'entry level': 'internship',
                'junior': 'internship',
            }
            
            opp_type = 'internship'
            for key, value in type_map.items():
                if key in search_term.lower():
                    opp_type = value
                    break
            
            title = job.get('title', '')[:200]
            company = job.get('company', {}).get('display_name', 'Unknown')[:100] if job.get('company') else 'Unknown'
            location = job.get('location', {}).get('display_name', 'South Africa')[:100] if job.get('location') else 'South Africa'
            description = job.get('description', '')[:2000]
            
            # Quick salary check
            stipend = 'Market Related'
            if job.get('salary_min') and job.get('salary_max'):
                stipend = f"R{int(job['salary_min'])} - R{int(job['salary_max'])} per month"
            
            url = job.get('redirect_url', '#')
            if url and len(url) > 500:
                url = '#'
            
            return {
                'title': title,
                'company': company,
                'opportunity_type': opp_type,
                'sector': self._quick_sector(title + ' ' + description),
                'location': location,
                'duration': '12 Months',
                'stipend': stipend,
                'required_degree': self._quick_degree(title + ' ' + description),
                'description': description,
                'redirect_url': url,
                'application_deadline': datetime.now().date() + timedelta(days=30),
                'source_platform': 'Adzuna',
                'required_skills': self._quick_skills(title + ' ' + description),
                'is_active': True,
            }
        except:
            return None
    
    def _save_opportunity(self, opp_data: Dict) -> Dict:
        """Save opportunity"""
        try:
            skills_list = opp_data.pop('required_skills', [])
            
            opportunity, created = Opportunity.objects.get_or_create(
                title=opp_data['title'],
                company=opp_data['company'],
                defaults=opp_data
            )
            
            if not created:
                for key, value in opp_data.items():
                    if key not in ['skills', 'required_skills']:
                        setattr(opportunity, key, value)
                opportunity.save()
            
            if skills_list:
                skills = []
                for skill_name in skills_list[:3]:  # Limit skills
                    if skill_name and skill_name.strip():
                        skill, _ = Skill.objects.get_or_create(name=skill_name.strip()[:100])
                        skills.append(skill)
                if skills:
                    opportunity.required_skills.set(skills)
            
            return {'id': opportunity.id, 'created': created}
        except:
            return None
    
    # Simplified helper methods
    def _quick_sector(self, text: str) -> str:
        text_lower = text.lower()
        sectors = {
            'Technology': ['tech', 'software', 'developer', 'it', 'data'],
            'Finance': ['finance', 'banking', 'accounting'],
            'Engineering': ['engineer', 'engineering'],
            'Healthcare': ['health', 'medical', 'nurse'],
            'Retail': ['retail', 'store', 'shop'],
        }
        for sector, keywords in sectors.items():
            for keyword in keywords:
                if keyword in text_lower:
                    return sector
        return 'General'
    
    def _quick_degree(self, text: str) -> str:
        text_lower = text.lower()
        if 'matric' in text_lower:
            return 'Matric'
        if 'degree' in text_lower or 'bsc' in text_lower or 'bachelor' in text_lower:
            degrees = ['Computer Science', 'Engineering', 'Finance', 'Business', 'IT']
            for degree in degrees:
                if degree.lower() in text_lower:
                    return degree
        return 'Matric / Relevant Qualification'
    
    def _quick_skills(self, text: str) -> List[str]:
        skill_keywords = [
            'python', 'java', 'sql', 'project management', 'communication',
            'leadership', 'excel', 'finance', 'marketing', 'management',
            'sales', 'customer service', 'analytics', 'engineering', 'design'
        ]
        found_skills = []
        text_lower = text.lower()
        for skill in skill_keywords:
            if skill in text_lower and skill not in found_skills:
                found_skills.append(skill.title())
        return found_skills[:3]  # Only 3 skills max