import django.db.models.deletion
import django.utils.timezone
from django.db import migrations, models


def backfill_canonical_fields(apps, schema_editor):
    Opportunity = apps.get_model("core", "Opportunity")
    today = django.utils.timezone.localdate()
    for opportunity in Opportunity.objects.all().iterator():
        opportunity.location_text = opportunity.location
        opportunity.closing_date = opportunity.application_deadline
        opportunity.primary_apply_url = opportunity.redirect_url
        if opportunity.application_deadline and opportunity.application_deadline < today:
            opportunity.lifecycle_status = "expired"
            opportunity.is_active = False
        opportunity.save(update_fields=[
            "location_text", "closing_date", "primary_apply_url",
            "lifecycle_status", "is_active",
        ])


class Migration(migrations.Migration):
    dependencies = [("core", "0001_initial")]

    operations = [
        migrations.CreateModel(
            name="Certification",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("title", models.CharField(max_length=200)),
                ("dates", models.CharField(blank=True, max_length=100)),
                ("profile", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="certifications", to="core.profile")),
            ],
        ),
        migrations.CreateModel(
            name="Project",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("title", models.CharField(max_length=200)),
                ("organisation", models.CharField(blank=True, max_length=200)),
                ("dates", models.CharField(blank=True, max_length=100)),
                ("description", models.TextField(blank=True)),
                ("bullets", models.TextField(blank=True, help_text="One achievement per line")),
                ("profile", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="projects", to="core.profile")),
            ],
        ),
        migrations.CreateModel(
            name="WorkExperience",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("title", models.CharField(max_length=200)),
                ("organisation", models.CharField(blank=True, max_length=200)),
                ("dates", models.CharField(blank=True, max_length=100)),
                ("bullets", models.TextField(blank=True, help_text="One achievement per line")),
                ("profile", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="work_experiences", to="core.profile")),
            ],
        ),
        migrations.RenameField(model_name="opportunity", old_name="mimimum_nqf", new_name="minimum_nqf"),
        migrations.AddField(model_name="opportunity", name="business_unit", field=models.CharField(blank=True, max_length=200)),
        migrations.AddField(model_name="opportunity", name="canonical_fingerprint", field=models.CharField(blank=True, db_index=True, max_length=64)),
        migrations.AddField(model_name="opportunity", name="city", field=models.CharField(blank=True, max_length=100)),
        migrations.AddField(model_name="opportunity", name="closing_date", field=models.DateField(blank=True, null=True)),
        migrations.AddField(model_name="opportunity", name="compensation_text", field=models.CharField(blank=True, max_length=255)),
        migrations.AddField(model_name="opportunity", name="country", field=models.CharField(blank=True, max_length=100)),
        migrations.AddField(model_name="opportunity", name="department", field=models.CharField(blank=True, max_length=200)),
        migrations.AddField(model_name="opportunity", name="eligibility_text", field=models.TextField(blank=True)),
        migrations.AddField(model_name="opportunity", name="employment_type", field=models.CharField(blank=True, max_length=100)),
        migrations.AddField(model_name="opportunity", name="experience_min_years", field=models.PositiveSmallIntegerField(blank=True, null=True)),
        migrations.AddField(model_name="opportunity", name="experience_text", field=models.TextField(blank=True)),
        migrations.AddField(model_name="opportunity", name="first_seen_at", field=models.DateTimeField(default=django.utils.timezone.now)),
        migrations.AddField(model_name="opportunity", name="last_seen_at", field=models.DateTimeField(default=django.utils.timezone.now)),
        migrations.AddField(model_name="opportunity", name="lifecycle_status", field=models.CharField(choices=[("active", "Active"), ("expired", "Expired"), ("removed", "Removed From Source"), ("review", "Needs Review")], default="active", max_length=20)),
        migrations.AddField(model_name="opportunity", name="listing_kind", field=models.CharField(choices=[("vacancy", "Vacancy"), ("talent_pool", "Talent Pool"), ("pipeline", "Pipeline")], default="vacancy", max_length=20)),
        migrations.AddField(model_name="opportunity", name="location_text", field=models.CharField(blank=True, max_length=255)),
        migrations.AddField(model_name="opportunity", name="posted_at", field=models.DateTimeField(blank=True, null=True)),
        migrations.AddField(model_name="opportunity", name="preferred_skills", field=models.ManyToManyField(blank=True, related_name="preferred_for_opportunities", to="core.skill")),
        migrations.AddField(model_name="opportunity", name="primary_apply_url", field=models.URLField(blank=True)),
        migrations.AddField(model_name="opportunity", name="province", field=models.CharField(blank=True, max_length=100)),
        migrations.AddField(model_name="opportunity", name="qualification_text", field=models.TextField(blank=True)),
        migrations.AddField(model_name="opportunity", name="responsibilities", field=models.TextField(blank=True)),
        migrations.AddField(model_name="opportunity", name="salary_currency", field=models.CharField(blank=True, max_length=3)),
        migrations.AddField(model_name="opportunity", name="salary_is_predicted", field=models.BooleanField(blank=True, null=True)),
        migrations.AddField(model_name="opportunity", name="salary_max", field=models.DecimalField(blank=True, decimal_places=2, max_digits=12, null=True)),
        migrations.AddField(model_name="opportunity", name="salary_min", field=models.DecimalField(blank=True, decimal_places=2, max_digits=12, null=True)),
        migrations.AddField(model_name="opportunity", name="work_arrangement", field=models.CharField(blank=True, max_length=50)),
        migrations.AlterModelOptions(name="opportunity", options={"ordering": ["-posted_at", "-date_listed"]}),
        migrations.RunPython(backfill_canonical_fields, migrations.RunPython.noop),
    ]
