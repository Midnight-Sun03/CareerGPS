from django.db import migrations


CURRENT_NEDBANK_SEARCH_URL = "https://jobs.nedbank.co.za/search/"


def update_nedbank_source(apps, schema_editor):
    Source = apps.get_model("ingestion", "IngestionSource")
    Source.objects.filter(code="nedbank").update(base_url=CURRENT_NEDBANK_SEARCH_URL)


class Migration(migrations.Migration):
    dependencies = [("ingestion", "0005_phase_1_2_status_and_urls")]
    operations = [migrations.RunPython(update_nedbank_source, migrations.RunPython.noop)]
