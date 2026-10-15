from django.db import migrations


# Phase 1 banking/finance tenants for the generic SmartRecruiters platform
# family. Additional SmartRecruiters employers are added through configuration
# (this seed mechanism or the admin), never through new scraper code.
SOURCES = [
    {
        "name": "Standard Bank Group SmartRecruiters",
        "code": "smartrecruiters-standardbankgroup",
        "company": "Standard Bank Group",
        "company_identifier": "StandardBankGroup",
        "base_url": "https://api.smartrecruiters.com/v1/companies/StandardBankGroup",
        "careers_url": "https://www.standardbank.com/sbg/standard-bank-group/careers/apply/jobs/view-all-jobs",
        "relevance_notes": "Public South African SmartRecruiters slice (country=za).",
    },
    {
        "name": "OUTsurance SmartRecruiters",
        "code": "smartrecruiters-outsurance",
        "company": "OUTsurance",
        "company_identifier": "OUTsurance",
        "base_url": "https://api.smartrecruiters.com/v1/companies/OUTsurance",
        "careers_url": "https://careers.smartrecruiters.com/OUTsurance",
        "relevance_notes": "Public South African SmartRecruiters slice (country=za).",
    },
]


def seed_smartrecruiters_sources(apps, schema_editor):
    Source = apps.get_model("ingestion", "IngestionSource")
    Company = apps.get_model("ingestion", "SmartRecruitersCompany")
    for entry in SOURCES:
        source, _ = Source.objects.update_or_create(
            code=entry["code"],
            defaults={
                "name": entry["name"],
                "source_type": "api",
                "company": entry["company"],
                "base_url": entry["base_url"],
                "priority": 15,
                "active": True,
            },
        )
        Company.objects.update_or_create(
            company_identifier=entry["company_identifier"],
            defaults={
                "source": source,
                "company_name": entry["company"],
                "careers_url": entry["careers_url"],
                "country_codes": ["ZA"],
                "relevance_notes": entry["relevance_notes"],
                "active": True,
            },
        )


class Migration(migrations.Migration):
    dependencies = [("ingestion", "0011_smartrecruiterscompany")]
    operations = [
        migrations.RunPython(seed_smartrecruiters_sources, migrations.RunPython.noop),
    ]
