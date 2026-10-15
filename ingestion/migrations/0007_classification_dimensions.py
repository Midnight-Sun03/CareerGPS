from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0005_opportunity_kind_stage_and_scope"),
        ("ingestion", "0006_update_nedbank_source_url"),
    ]

    operations = [
        migrations.AddField(
            model_name="rawopportunity", name="opportunity_kind",
            field=models.CharField(blank=True, max_length=30),
        ),
        migrations.AddField(
            model_name="rawopportunity", name="career_stage",
            field=models.CharField(blank=True, max_length=20),
        ),
        migrations.AddField(
            model_name="rawopportunity", name="scope_status",
            field=models.CharField(blank=True, max_length=20),
        ),
        migrations.AddField(
            model_name="ingestionrun", name="job_kind_count",
            field=models.PositiveIntegerField(default=0),
        ),
        migrations.AddField(
            model_name="ingestionrun", name="vacation_work_kind_count",
            field=models.PositiveIntegerField(default=0),
        ),
        migrations.AddField(
            model_name="ingestionrun", name="unknown_kind_count",
            field=models.PositiveIntegerField(default=0),
        ),
        migrations.AddField(
            model_name="ingestionrun", name="early_career_count",
            field=models.PositiveIntegerField(default=0),
        ),
        migrations.AddField(
            model_name="ingestionrun", name="experienced_count",
            field=models.PositiveIntegerField(default=0),
        ),
        migrations.AddField(
            model_name="ingestionrun", name="unknown_career_stage_count",
            field=models.PositiveIntegerField(default=0),
        ),
        migrations.AddField(
            model_name="ingestionrun", name="in_scope_count",
            field=models.PositiveIntegerField(default=0),
        ),
        migrations.AddField(
            model_name="ingestionrun", name="scope_unclassified_count",
            field=models.PositiveIntegerField(default=0),
        ),
    ]
