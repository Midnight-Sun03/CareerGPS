from django.db import migrations


CURRENT_SASOL_SEARCH_URL = "https://jobs.sasol.com/search/?createNewAlert=false&q=&locationsearch="


def update_sasol_source(apps, schema_editor):
    Source = apps.get_model("ingestion", "IngestionSource")
    Source.objects.filter(code="sasol").update(base_url=CURRENT_SASOL_SEARCH_URL)


class Migration(migrations.Migration):
    dependencies = [("ingestion", "0002_seed_sources")]
    operations = [migrations.RunPython(update_sasol_source, migrations.RunPython.noop)]
