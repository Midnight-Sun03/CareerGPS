from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("ingestion", "0004_update_capitec_source_url")]

    operations = [
        migrations.AddField(
            model_name="ingestionrun", name="internship_count",
            field=models.PositiveIntegerField(default=0),
        ),
        migrations.AddField(
            model_name="ingestionrun", name="learnership_count",
            field=models.PositiveIntegerField(default=0),
        ),
        migrations.AddField(
            model_name="ingestionrun", name="graduate_count",
            field=models.PositiveIntegerField(default=0),
        ),
        migrations.AddField(
            model_name="ingestionrun", name="entry_level_count",
            field=models.PositiveIntegerField(default=0),
        ),
        migrations.AddField(
            model_name="ingestionrun", name="unclassified_count",
            field=models.PositiveIntegerField(default=0),
        ),
        migrations.AddField(
            model_name="rawopportunity", name="listing_url",
            field=models.URLField(blank=True),
        ),
        migrations.AlterField(
            model_name="rawopportunity",
            name="normalization_status",
            field=models.CharField(
                choices=[
                    ("pending", "Pending"),
                    ("published", "Published"),
                    ("unclassified", "Unclassified"),
                    ("out_of_scope", "Out of scope"),
                    ("review", "Needs review"),
                    ("error", "Error"),
                ],
                default="pending",
                max_length=20,
            ),
        ),
        migrations.AddField(
            model_name="opportunitysourcerecord", name="listing_url",
            field=models.URLField(blank=True),
        ),
    ]
