from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("interview", "0003_interview_voice_foundation"),
    ]

    operations = [
        migrations.AddField(
            model_name="interview",
            name="camera_consent",
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name="interview",
            name="camera_mode",
            field=models.CharField(
                choices=[
                    ("audio_only", "Audio only"),
                    ("camera_analysis", "Audio + camera analysis"),
                ],
                default="audio_only",
                max_length=24,
            ),
        ),
        migrations.AddField(
            model_name="interview",
            name="visual_analysis_version",
            field=models.CharField(blank=True, default="", max_length=40),
        ),
        migrations.AddField(
            model_name="interviewquestion",
            name="visual_analysis_error",
            field=models.TextField(blank=True),
        ),
        migrations.AddField(
            model_name="interviewquestion",
            name="visual_analysis_status",
            field=models.CharField(
                choices=[
                    ("not_requested", "Not requested"),
                    ("ready", "Ready"),
                    ("collecting", "Collecting"),
                    ("complete", "Complete"),
                    ("partial", "Partially available"),
                    ("unavailable", "Unavailable"),
                    ("error", "Error"),
                ],
                default="not_requested",
                max_length=20,
            ),
        ),
        migrations.AddField(
            model_name="interviewquestion",
            name="visual_metrics",
            field=models.JSONField(blank=True, default=dict),
        ),
    ]
