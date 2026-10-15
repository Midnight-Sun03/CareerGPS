from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("core", "0003_opportunity_coordinates")]

    operations = [
        migrations.AlterField(
            model_name="opportunity",
            name="opportunity_type",
            field=models.CharField(
                choices=[
                    ("internship", "Internship"),
                    ("learnership", "Learnership"),
                    ("graduate", "Graduate program"),
                    ("entry_level", "Entry Level Position"),
                    ("unclassified", "Unclassified"),
                ],
                max_length=200,
            ),
        ),
        migrations.AddField(
            model_name="opportunity", name="classification_reason",
            field=models.TextField(blank=True),
        ),
        migrations.AddField(
            model_name="opportunity", name="qualification_minimum",
            field=models.TextField(blank=True),
        ),
        migrations.AddField(
            model_name="opportunity", name="qualification_preferred",
            field=models.TextField(blank=True),
        ),
        migrations.AddField(
            model_name="opportunity", name="experience_max_years",
            field=models.PositiveSmallIntegerField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="opportunity", name="preferred_experience_text",
            field=models.TextField(blank=True),
        ),
        migrations.AddField(
            model_name="opportunity", name="source_native_category",
            field=models.CharField(blank=True, max_length=255),
        ),
        migrations.AddField(
            model_name="opportunity", name="source_native_career_level",
            field=models.CharField(blank=True, max_length=255),
        ),
        migrations.AddField(
            model_name="opportunity", name="programme_signals",
            field=models.JSONField(blank=True, default=list),
        ),
        migrations.AddField(
            model_name="opportunity", name="early_career_signals",
            field=models.JSONField(blank=True, default=list),
        ),
        migrations.AddField(
            model_name="opportunity", name="seniority_signals",
            field=models.JSONField(blank=True, default=list),
        ),
        migrations.AddField(
            model_name="opportunity", name="source_specific_evidence",
            field=models.JSONField(blank=True, default=dict),
        ),
    ]
