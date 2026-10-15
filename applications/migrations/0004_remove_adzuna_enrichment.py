from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('applications', '0003_alter_application_status'),
        ('ingestion', '0010_purge_adzuna'),
    ]

    operations = [
        migrations.RemoveField(model_name='application', name='enrichment'),
        migrations.DeleteModel(name='OpportunityJobEnrichment'),
        migrations.AlterField(
            model_name='application',
            name='job_info_status',
            field=models.CharField(
                choices=[
                    ('catalogue_full', 'CareerGPS full job information'),
                    ('catalogue_available', 'Source information available'),
                    ('manual', 'User-provided job information'),
                ],
                default='catalogue_available',
                max_length=30,
            ),
        ),
    ]
