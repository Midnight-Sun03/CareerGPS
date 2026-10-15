from django.db import migrations, models


def backfill_classification_dimensions(apps, schema_editor):
    Opportunity = apps.get_model("core", "Opportunity")
    mappings = {
        "internship": ("internship", "early_career", "in_scope"),
        "learnership": ("learnership", "early_career", "in_scope"),
        "graduate": ("graduate_programme", "early_career", "in_scope"),
        "entry_level": ("job", "early_career", "in_scope"),
        "unclassified": ("job", "unknown", "unclassified"),
    }
    for legacy_type, (kind, stage, scope) in mappings.items():
        Opportunity.objects.filter(opportunity_type=legacy_type).update(
            opportunity_kind=kind,
            career_stage=stage,
            scope_status=scope,
        )


class Migration(migrations.Migration):
    dependencies = [("core", "0004_opportunity_evidence_and_unclassified")]

    operations = [
        migrations.AddField(
            model_name="opportunity",
            name="opportunity_kind",
            field=models.CharField(
                choices=[
                    ("job", "Job"), ("internship", "Internship"),
                    ("learnership", "Learnership"),
                    ("graduate_programme", "Graduate programme"),
                    ("vacation_work", "Vacation work"),
                    ("unknown", "Other / unknown"),
                ],
                default="job", max_length=30,
            ),
        ),
        migrations.AddField(
            model_name="opportunity",
            name="career_stage",
            field=models.CharField(
                choices=[
                    ("early_career", "Early career"),
                    ("experienced", "Experienced"),
                    ("unknown", "Unknown"),
                ],
                default="unknown", max_length=20,
            ),
        ),
        migrations.AddField(
            model_name="opportunity",
            name="scope_status",
            field=models.CharField(
                choices=[
                    ("in_scope", "In scope"),
                    ("unclassified", "Unclassified"),
                    ("out_of_scope", "Out of scope"),
                ],
                db_index=True, default="unclassified", max_length=20,
            ),
        ),
        migrations.AddField(
            model_name="opportunity", name="classification_evidence",
            field=models.JSONField(blank=True, default=list),
        ),
        migrations.AddField(
            model_name="opportunity", name="evidence_provenance",
            field=models.JSONField(blank=True, default=dict),
        ),
        migrations.AddField(
            model_name="opportunity", name="programme_identity",
            field=models.CharField(blank=True, max_length=100),
        ),
        migrations.AddField(
            model_name="opportunity", name="preferred_experience_min_years",
            field=models.PositiveSmallIntegerField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="opportunity", name="preferred_experience_max_years",
            field=models.PositiveSmallIntegerField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="opportunity", name="office",
            field=models.CharField(blank=True, max_length=200),
        ),
        migrations.RunPython(backfill_classification_dimensions, migrations.RunPython.noop),
    ]
