from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("applications", "0001_initial"),
    ]

    operations = [
        migrations.AlterField(
            model_name="generateddocument",
            name="template_key",
            field=models.CharField(default="azurill", max_length=60),
        ),
        migrations.AddField(
            model_name="generateddocument",
            name="layout_settings",
            field=models.JSONField(blank=True, default=dict),
        ),
        migrations.AlterField(
            model_name="generateddocument",
            name="renderer_backend",
            field=models.CharField(default="careergps_rr_layout_adapter", max_length=60),
        ),
    ]
