from django.core.management.base import BaseCommand
from core.models import Opportunity
from datetime import date
import random


class Command(BaseCommand):
    help = "Seed South African opportunities"

    def handle(self, *args, **kwargs):

        opportunities = [

            {
                "title": "Vodacom Discover Graduate Programme 2026",
                "company": "Vodacom",
                "opportunity_type": "graduate",
                "sector": "Technology",
                "location": "Johannesburg",
                "duration": "24 Months",
                "stipend": "Market Related",
                "required_degree": "Computer Science / IT",
                "minimum_nqf": 7,
                "description": "Graduate programme focused on technology leadership.",
                "duties": "Software development and digital innovation.",
                "eligibility": "South African graduates under 27.",
                "source_platform": "YouthCareers",
                "redirect_url": "https://www.vodacom.com",
                "application_deadline": date(2026, 8, 31),
            },

            {
                "title": "FNB Learnership Programme 2026",
                "company": "FNB",
                "opportunity_type": "learnership",
                "sector": "Banking",
                "location": "Johannesburg",
                "duration": "12 Months",
                "stipend": "R4500 per month",
                "required_degree": "Matric",
                "minimum_nqf": 4,
                "description": "Banking learnership for unemployed youth.",
                "duties": "Customer support and banking operations.",
                "eligibility": "South African citizens aged 18-35.",
                "source_platform": "SPANi",
                "redirect_url": "https://www.fnb.co.za",
                "application_deadline": date(2026, 5, 22),
            },

            {
                "title": "CSIR Data Science Internship",
                "company": "CSIR",
                "opportunity_type": "internship",
                "sector": "Research",
                "location": "Pretoria",
                "duration": "12 Months",
                "stipend": "Market Related",
                "required_degree": "Data Science / Statistics",
                "minimum_nqf": 7,
                "description": "Internship focused on data analytics and AI.",
                "duties": "Data analysis and reporting.",
                "eligibility": "Recent graduates.",
                "source_platform": "SPANi",
                "redirect_url": "https://www.csir.co.za",
                "application_deadline": date(2026, 6, 15),
            },

        ]

        companies = [
            "Eskom",
            "Sasol",
            "Transnet",
            "MTN",
            "Standard Bank",
            "Discovery",
            "Nedbank",
            "Anglo American",
            "Telkom",
            "Capitec",
            "Toyota SA",
            "BMW SA",
            "SANRAL",
            "Deloitte",
            "PwC",
            "KPMG",
            "Shoprite",
            "Pick n Pay",
            "Amazon South Africa",
            "BBD",
        ]

        sectors = [
            "Technology",
            "Engineering",
            "Finance",
            "Mining",
            "Retail",
            "Telecommunications",
            "Consulting",
            "Logistics",
        ]

        locations = [
            "Johannesburg",
            "Cape Town",
            "Durban",
            "Pretoria",
            "Port Elizabeth",
        ]

        types = [
            "internship",
            "graduate",
            "learnership",
        ]

        degrees = [
            "Computer Science",
            "Information Technology",
            "Engineering",
            "Finance",
            "Business Administration",
            "Marketing",
        ]

        # AUTO GENERATE MANY MORE
        for i in range(1, 101):

            company = random.choice(companies)
            opportunity_type = random.choice(types)
            sector = random.choice(sectors)

            title = f"{company} {opportunity_type.title()} Programme {i}"

            Opportunity.objects.get_or_create(
                title=title,
                defaults={
                    "company": company,
                    "opportunity_type": opportunity_type,
                    "sector": sector,
                    "location": random.choice(locations),
                    "duration": "12 Months",
                    "stipend": "Market Related",
                    "required_degree": random.choice(degrees),
                    "minimum_nqf": random.choice([4, 5, 6, 7]),
                    "description": f"{company} is offering a {opportunity_type} opportunity in the {sector} sector.",
                    "duties": "Training and workplace exposure.",
                    "eligibility": "South African unemployed youth.",
                    "source_platform": "CareerGPS",
                    "redirect_url": "https://example.com",
                    "application_deadline": date(2026, 12, 31),
                    "is_active": True,
                }
            )

        # INSERT REAL ONES
        for item in opportunities:
            Opportunity.objects.get_or_create(
                title=item["title"],
                defaults=item
            )

        self.stdout.write(
            self.style.SUCCESS("100+ opportunities seeded successfully")
        )



class Command(BaseCommand):
    help = "Seed South African opportunities"

    def handle(self, *args, **kwargs):

        opportunities = [

            {
                "title": "Vodacom Discover Graduate Programme 2026",
                "company": "Vodacom",
                "opportunity_type": "graduate",
                "sector": "Technology",
                "location": "Johannesburg",
                "duration": "24 Months",
                "stipend": "Market Related",
                "required_degree": "Computer Science / IT",
                "minimum_nqf": 7,
                "description": "Graduate programme focused on technology leadership.",
                "duties": "Software development and digital innovation.",
                "eligibility": "South African graduates under 27.",
                "source_platform": "YouthCareers",
                "redirect_url": "https://www.vodacom.com",
                "application_deadline": date(2026, 8, 31),
            },

            {
                "title": "FNB Learnership Programme 2026",
                "company": "FNB",
                "opportunity_type": "learnership",
                "sector": "Banking",
                "location": "Johannesburg",
                "duration": "12 Months",
                "stipend": "R4500 per month",
                "required_degree": "Matric",
                "minimum_nqf": 4,
                "description": "Banking learnership for unemployed youth.",
                "duties": "Customer support and banking operations.",
                "eligibility": "South African citizens aged 18-35.",
                "source_platform": "SPANi",
                "redirect_url": "https://www.fnb.co.za",
                "application_deadline": date(2026, 5, 22),
            },

            {
                "title": "CSIR Data Science Internship",
                "company": "CSIR",
                "opportunity_type": "internship",
                "sector": "Research",
                "location": "Pretoria",
                "duration": "12 Months",
                "stipend": "Market Related",
                "required_degree": "Data Science / Statistics",
                "minimum_nqf": 7,
                "description": "Internship focused on data analytics and AI.",
                "duties": "Data analysis and reporting.",
                "eligibility": "Recent graduates.",
                "source_platform": "SPANi",
                "redirect_url": "https://www.csir.co.za",
                "application_deadline": date(2026, 6, 15),
            },

        ]

        companies = [
            "Eskom",
            "Sasol",
            "Transnet",
            "MTN",
            "Standard Bank",
            "Discovery",
            "Nedbank",
            "Anglo American",
            "Telkom",
            "Capitec",
            "Toyota SA",
            "BMW SA",
            "SANRAL",
            "Deloitte",
            "PwC",
            "KPMG",
            "Shoprite",
            "Pick n Pay",
            "Amazon South Africa",
            "BBD",
        ]

        sectors = [
            "Technology",
            "Engineering",
            "Finance",
            "Mining",
            "Retail",
            "Telecommunications",
            "Consulting",
            "Logistics",
        ]

        locations = [
            "Johannesburg",
            "Cape Town",
            "Durban",
            "Pretoria",
            "Port Elizabeth",
        ]

        types = [
            "internship",
            "graduate",
            "learnership",
        ]

        degrees = [
            "Computer Science",
            "Information Technology",
            "Engineering",
            "Finance",
            "Business Administration",
            "Marketing",
        ]

        # AUTO GENERATE MANY MORE
        for i in range(1, 101):

            company = random.choice(companies)
            opportunity_type = random.choice(types)
            sector = random.choice(sectors)

            title = f"{company} {opportunity_type.title()} Programme {i}"

            Opportunity.objects.get_or_create(
                title=title,
                defaults={
                    "company": company,
                    "opportunity_type": opportunity_type,
                    "sector": sector,
                    "location": random.choice(locations),
                    "duration": "12 Months",
                    "stipend": "Market Related",
                    "required_degree": random.choice(degrees),
                    "minimum_nqf": random.choice([4, 5, 6, 7]),
                    "description": f"{company} is offering a {opportunity_type} opportunity in the {sector} sector.",
                    "duties": "Training and workplace exposure.",
                    "eligibility": "South African unemployed youth.",
                    "source_platform": "CareerGPS",
                    "redirect_url": "https://example.com",
                    "application_deadline": date(2026, 12, 31),
                    "is_active": True,
                }
            )

        # INSERT REAL ONES
        for item in opportunities:
            Opportunity.objects.get_or_create(
                title=item["title"],
                defaults=item
            )

        self.stdout.write(
            self.style.SUCCESS("100+ opportunities seeded successfully")
        )