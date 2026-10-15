from django.db import migrations


SOURCES = [
    ("Adzuna South Africa", "adzuna", "api", "", "https://api.adzuna.com/v1/api/jobs/za", 50),
    ("Takealot Group Greenhouse", "greenhouse-takealotgroup", "api", "Takealot Group", "https://boards-api.greenhouse.io/v1/boards/takealotgroup", 15),
    ("Nedbank Careers", "nedbank", "scraper", "Nedbank", "https://jobs.nedbank.co.za/jobs", 10),
    ("Discovery Careers", "discovery", "scraper", "Discovery", "https://careers.discovery.co.za/go/All-Jobs/4509301/", 10),
    ("Sasol Careers", "sasol", "scraper", "Sasol", "https://jobs.sasol.com/go/All-Jobs/1507601/", 10),
    ("Capitec Careers", "capitec", "scraper", "Capitec Bank Ltd", "https://careers.capitecbank.co.za/go/All-Jobs/3585001/", 10),
    ("Woolworths South Africa Careers", "woolworths", "scraper", "Woolworths South Africa", "https://woolworths.erecruit.co/candidateapp/jobs/categories/", 10),
]


def seed_sources(apps, schema_editor):
    Source = apps.get_model("ingestion", "IngestionSource")
    Board = apps.get_model("ingestion", "GreenhouseBoard")
    source_by_code = {}
    for name, code, source_type, company, base_url, priority in SOURCES:
        source, _ = Source.objects.update_or_create(code=code, defaults={
            "name": name, "source_type": source_type, "company": company,
            "base_url": base_url, "priority": priority, "active": True,
        })
        source_by_code[code] = source
    Board.objects.update_or_create(board_token="takealotgroup", defaults={
        "source": source_by_code["greenhouse-takealotgroup"],
        "company_name": "Takealot Group",
        "careers_url": "https://job-boards.greenhouse.io/takealotgroup",
        "country_codes": ["ZA"],
        "relevance_notes": "First South African-relevant board; publish only CareerGPS-supported opportunity types.",
        "active": True,
    })


class Migration(migrations.Migration):
    dependencies = [("ingestion", "0001_initial")]
    operations = [migrations.RunPython(seed_sources, migrations.RunPython.noop)]
