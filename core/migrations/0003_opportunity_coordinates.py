from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("core", "0002_opportunity_ingestion_fields_and_profile_history")]
    operations = [
        migrations.AddField(
            model_name="opportunity", name="latitude",
            field=models.DecimalField(blank=True, decimal_places=6, max_digits=9, null=True),
        ),
        migrations.AddField(
            model_name="opportunity", name="longitude",
            field=models.DecimalField(blank=True, decimal_places=6, max_digits=9, null=True),
        ),
    ]
