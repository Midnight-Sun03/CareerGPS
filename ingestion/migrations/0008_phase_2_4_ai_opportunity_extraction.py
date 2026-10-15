from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0008_audited_taxonomy_relationships"),
        ("ingestion", "0007_classification_dimensions"),
    ]

    operations = [
        migrations.CreateModel(
            name="OpportunityExtraction",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("status", models.CharField(choices=[("pending", "Pending"), ("processing", "Processing"), ("ready", "Ready"), ("failed", "Failed"), ("stale", "Stale")], db_index=True, default="pending", max_length=20)),
                ("source_completeness", models.CharField(choices=[("full_structured", "Full structured source"), ("full_text", "Full text"), ("partial_text", "Partial text / snippet"), ("unknown", "Unknown")], default="unknown", max_length=30)),
                ("input_hash", models.CharField(db_index=True, max_length=64)),
                ("extractor", models.CharField(default="gemini", max_length=50)),
                ("model_name", models.CharField(max_length=100)),
                ("thinking_level", models.CharField(default="medium", max_length=20)),
                ("schema_version", models.CharField(default="2.4.1", max_length=30)),
                ("prompt_version", models.CharField(default="2.4.1", max_length=30)),
                ("raw_output", models.JSONField(blank=True, default=dict)),
                ("validation_errors", models.JSONField(blank=True, default=list)),
                ("error_message", models.TextField(blank=True)),
                ("processed_at", models.DateTimeField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("opportunity", models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name="ai_extraction", to="core.opportunity")),
            ],
            options={"ordering": ["status", "opportunity_id"]},
        ),
        migrations.CreateModel(
            name="OpportunityCriterion",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("criterion_type", models.CharField(choices=[("occupation", "Occupation / role"), ("skill", "Skill / knowledge"), ("qualification", "Qualification"), ("experience", "Experience"), ("certification", "Certification / licence"), ("language", "Language"), ("work_eligibility", "Work eligibility"), ("work_arrangement", "Work arrangement"), ("location", "Location"), ("responsibility", "Responsibility"), ("other", "Other")], db_index=True, max_length=30)),
                ("requirement_level", models.CharField(choices=[("required", "Required"), ("preferred", "Preferred"), ("informational", "Informational"), ("unknown", "Unknown")], db_index=True, default="unknown", max_length=20)),
                ("raw_value", models.TextField()),
                ("normalized_value", models.TextField(blank=True)),
                ("evidence_text", models.TextField()),
                ("source_section", models.CharField(blank=True, max_length=120)),
                ("minimum_years", models.PositiveSmallIntegerField(blank=True, null=True)),
                ("maximum_years", models.PositiveSmallIntegerField(blank=True, null=True)),
                ("metadata", models.JSONField(blank=True, default=dict)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("extraction", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="criteria", to="ingestion.opportunityextraction")),
            ],
            options={"ordering": ["criterion_type", "requirement_level", "id"]},
        ),
        migrations.CreateModel(
            name="OpportunityCriterionTaxonomyMatch",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("match_type", models.CharField(choices=[("exact", "Exact identity"), ("related", "Related label / specialisation")], max_length=20)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("concept", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="opportunity_criterion_matches", to="core.taxonomyconcept")),
                ("criterion", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="taxonomy_matches", to="ingestion.opportunitycriterion")),
            ],
        ),
        migrations.AddIndex(
            model_name="opportunityextraction",
            index=models.Index(fields=["status", "input_hash"], name="opp_extract_status_hash_idx"),
        ),
        migrations.AddIndex(
            model_name="opportunitycriterion",
            index=models.Index(fields=["extraction", "criterion_type", "requirement_level"], name="opp_criterion_lookup_idx"),
        ),
        migrations.AddIndex(
            model_name="opportunitycriteriontaxonomymatch",
            index=models.Index(fields=["criterion", "match_type"], name="criterion_tax_match_idx"),
        ),
        migrations.AddConstraint(
            model_name="opportunitycriteriontaxonomymatch",
            constraint=models.UniqueConstraint(fields=("criterion", "concept"), name="unique_criterion_taxonomy_concept"),
        ),
    ]
