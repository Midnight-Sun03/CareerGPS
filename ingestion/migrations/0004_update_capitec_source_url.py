from django.db import migrations


CURRENT_CAPITEC_SEARCH_URL = "https://careers.capitecbank.co.za/search/?createNewAlert=false&q=&locationsearch="


def update_capitec_source(apps, schema_editor):
    Source = apps.get_model("ingestion", "IngestionSource")
    Source.objects.filter(code="capitec").update(base_url=CURRENT_CAPITEC_SEARCH_URL)


class Migration(migrations.Migration):
    dependencies = [("ingestion", "0003_update_sasol_source_url")]
    operations = [migrations.RunPython(update_capitec_source, migrations.RunPython.noop)]
