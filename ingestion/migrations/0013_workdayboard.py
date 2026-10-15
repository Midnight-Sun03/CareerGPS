from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("ingestion", "0012_seed_smartrecruiters_sources"),
    ]

    operations = [
        migrations.CreateModel(
            name="WorkdayBoard",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("company_name", models.CharField(max_length=160)),
                ("tenant", models.SlugField(help_text="CXS tenant segment, e.g. 'absa'.", max_length=120)),
                ("site", models.SlugField(help_text="CXS site segment, e.g. 'ABSAcareersite'.", max_length=160)),
                ("careers_url", models.URLField(blank=True)),
                ("country_codes", models.JSONField(blank=True, default=list)),
                ("relevance_notes", models.TextField(blank=True)),
                ("active", models.BooleanField(default=True)),
                ("last_checked_at", models.DateTimeField(blank=True, null=True)),
                ("last_success_at", models.DateTimeField(blank=True, null=True)),
                ("last_error", models.TextField(blank=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("source", models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name="workday_board", to="ingestion.ingestionsource")),
            ],
            options={
                "ordering": ["company_name", "site"],
            },
            bases=(models.Model,),
        ),
        migrations.AddConstraint(
            model_name="workdayboard",
            constraint=models.UniqueConstraint(
                fields=("tenant", "site"), name="unique_workday_tenant_site",
            ),
        ),
    ]
