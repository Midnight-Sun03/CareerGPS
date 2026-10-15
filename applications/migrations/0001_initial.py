from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion
import django.utils.timezone


class Migration(migrations.Migration):
    initial = True

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("core", "0008_audited_taxonomy_relationships"),
    ]

    operations = [
        migrations.CreateModel(
            name="OpportunityJobEnrichment",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("status", models.CharField(choices=[("processing", "Processing"), ("verified_match", "Verified match"), ("not_verified", "Not verified"), ("error", "Error")], db_index=True, max_length=30)),
                ("title", models.CharField(blank=True, max_length=300)),
                ("employer", models.CharField(blank=True, max_length=300)),
                ("location", models.CharField(blank=True, max_length=300)),
                ("source_url", models.URLField(blank=True, max_length=2000)),
                ("structured_data", models.JSONField(blank=True, default=dict)),
                ("model_name", models.CharField(blank=True, max_length=120)),
                ("verification_details", models.JSONField(blank=True, default=dict)),
                ("last_error", models.TextField(blank=True)),
                ("attempted_at", models.DateTimeField(default=django.utils.timezone.now)),
                ("verified_at", models.DateTimeField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("opportunity", models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name="job_enrichment", to="core.opportunity")),
            ],
            options={"ordering": ["-updated_at", "-id"]},
        ),
        migrations.CreateModel(
            name="Application",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("status", models.CharField(choices=[("planning", "Planning"), ("ready_to_apply", "Ready to apply"), ("applied", "Applied"), ("assessment", "Assessment"), ("interview", "Interview"), ("offer", "Offer"), ("rejected", "Rejected"), ("withdrawn", "Withdrawn")], default="planning", max_length=30)),
                ("job_info_status", models.CharField(choices=[("catalogue_full", "CareerGPS full job information"), ("verified_match", "Externally verified full job information"), ("limited_info", "Limited job information"), ("manual", "User-provided job information")], default="limited_info", max_length=30)),
                ("notes", models.TextField(blank=True)),
                ("applied_at", models.DateTimeField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("enrichment", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="applications", to="applications.opportunityjobenrichment")),
                ("opportunity", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="applications", to="core.opportunity")),
                ("user", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="career_applications", to=settings.AUTH_USER_MODEL)),
            ],
            options={"ordering": ["-updated_at", "-id"]},
        ),
        migrations.AddConstraint(
            model_name="application",
            constraint=models.UniqueConstraint(fields=("user", "opportunity"), name="unique_user_opportunity_application"),
        ),
        migrations.CreateModel(
            name="GeneratedDocument",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("document_type", models.CharField(choices=[("cv", "CV"), ("cover_letter", "Cover letter")], max_length=30)),
                ("structured_data", models.JSONField(blank=True, default=dict)),
                ("json_resume", models.JSONField(blank=True, default=dict)),
                ("candidate_fact_ids", models.JSONField(blank=True, default=list)),
                ("job_info_status", models.CharField(blank=True, max_length=30)),
                ("template_key", models.CharField(default="careergps_professional", max_length=60)),
                ("renderer_backend", models.CharField(default="django_legacy", max_length=60)),
                ("pdf_file", models.FileField(blank=True, upload_to="generated_documents/pdf/")),
                ("docx_file", models.FileField(blank=True, upload_to="generated_documents/docx/")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("application", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="generated_documents", to="applications.application")),
                ("opportunity", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="generated_documents", to="core.opportunity")),
                ("user", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="generated_documents", to=settings.AUTH_USER_MODEL)),
            ],
            options={"ordering": ["-created_at", "-id"]},
        ),
    ]
