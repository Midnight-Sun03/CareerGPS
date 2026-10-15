from django.db import migrations


# Phase 2 public Workday (CXS) boards.  Each entry records the tenant/site
# segments verified live against the public catalogue, which must be
# POST .../wday/cxs/{tenant}/{site}/jobs and
# GET .../wday/cxs/{tenant}/{site}{externalPath}.
# Additional Workday employers are added through configuration (this seed
# mechanism or the admin), never through new scraper code.
BOARDS = [
    {
        "name": "Absa Workday",
        "code": "workday-absa",
        "company": "Absa",
        "tenant": "absa",
        "site": "ABSAcareersite",
        "careers_url": "https://absa.wd3.myworkdayjobs.com/ABSAcareersite",
        "base_url": "https://absa.wd3.myworkdayjobs.com/wday/cxs/absa/ABSAcareersite/jobs",
        "relevance_notes": (
            "Public Absa group board. The catalogue mixes South African, Kenyan and "
            "Mauritian postings, so explicit ZA scope is applied per posting from the "
            "detail country record."
        ),
    },
    {
        "name": "Old Mutual Careers Workday",
        "code": "workday-oldmutual-careers",
        "company": "Old Mutual",
        "tenant": "oldmutual",
        "site": "Old_Mutual_Careers",
        "careers_url": "https://oldmutual.wd3.myworkdayjobs.com/Old_Mutual_Careers",
        "base_url": "https://oldmutual.wd3.myworkdayjobs.com/wday/cxs/oldmutual/Old_Mutual_Careers/jobs",
        "relevance_notes": (
            "Pan-African Old Mutual careers board including Zimbabwe, Kenya and other "
            "non-SA entities; explicit ZA scope is applied per posting."
        ),
    },
    {
        "name": "Old Mutual Early Careers Workday",
        "code": "workday-oldmutual-early-careers",
        "company": "Old Mutual",
        "tenant": "oldmutual",
        "site": "Old_Mutual_Early_Careers",
        "careers_url": "https://oldmutual.wd3.myworkdayjobs.com/Old_Mutual_Early_Careers",
        "base_url": "https://oldmutual.wd3.myworkdayjobs.com/wday/cxs/oldmutual/Old_Mutual_Early_Careers/jobs",
        "relevance_notes": (
            "Old Mutual early careers board. Verified live as an empty board "
            "(HTTP 200 with zero postings); kept configured so it starts contributing "
            "when the employer publishes."
        ),
    },
    {
        "name": "FirstRand Workday",
        "code": "workday-firstrand",
        "company": "FirstRand",
        "tenant": "firstrand",
        "site": "FRB",
        "careers_url": "https://firstrand.wd3.myworkdayjobs.com/FRB",
        "base_url": "https://firstrand.wd3.myworkdayjobs.com/wday/cxs/firstrand/FRB/jobs",
        "relevance_notes": "Public FirstRand Bank board with a South African posting set.",
    },
    {
        "name": "Pick n Pay Workday",
        "code": "workday-picknpay",
        "company": "Pick n Pay",
        "tenant": "picknpay",
        "site": "PNP_Careers",
        "careers_url": "https://picknpay.wd3.myworkdayjobs.com/PNP_Careers",
        "base_url": "https://picknpay.wd3.myworkdayjobs.com/wday/cxs/picknpay/PNP_Careers/jobs",
        "relevance_notes": "Public Pick n Pay careers board.",
    },
]


def seed_workday_boards(apps, schema_editor):
    Source = apps.get_model("ingestion", "IngestionSource")
    Board = apps.get_model("ingestion", "WorkdayBoard")
    for entry in BOARDS:
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
        Board.objects.update_or_create(
            tenant=entry["tenant"],
            site=entry["site"],
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
    dependencies = [("ingestion", "0013_workdayboard")]
    operations = [
        migrations.RunPython(seed_workday_boards, migrations.RunPython.noop),
    ]
