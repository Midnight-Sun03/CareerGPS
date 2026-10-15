from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0022_evaluation_task_live_key_constraint'),
    ]

    operations = [
        migrations.AddIndex(
            model_name='occupationsourceresolutioncandidate',
            index=models.Index(
                fields=['concept', 'resolution'], name='occ_candidate_concept_idx'),
        ),
    ]
