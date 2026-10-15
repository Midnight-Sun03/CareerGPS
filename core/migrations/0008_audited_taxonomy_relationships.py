from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('core', '0007_phase_2_3_taxonomy_foundation'),
    ]

    operations = [
        migrations.AlterField(
            model_name='taxonomylabel',
            name='label_type',
            field=models.CharField(
                choices=[
                    ('preferred', 'Preferred'),
                    ('alternative', 'Alternative'),
                    ('hidden', 'Hidden'),
                    ('specialisation', 'Specialisation'),
                ],
                default='alternative',
                max_length=20,
            ),
        ),
        migrations.AlterField(
            model_name='taxonomyrelation',
            name='relation_type',
            field=models.CharField(
                choices=[
                    ('broader', 'Broader concept'),
                    ('essential', 'Essential skill relationship'),
                    ('optional', 'Optional skill relationship'),
                ],
                max_length=30,
            ),
        ),
    ]
