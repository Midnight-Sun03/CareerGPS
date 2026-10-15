import re

import django.db.models.deletion
from django.db import migrations, models


def normalize(value):
    return re.sub(r"\s+", " ", str(value or "").strip()).casefold()


def preserve_legacy_profile_data(apps, schema_editor):
    Profile = apps.get_model('core', 'Profile')
    Education = apps.get_model('core', 'Education')
    ProfileSkill = apps.get_model('core', 'ProfileSkill')
    ProfileInterest = apps.get_model('core', 'ProfileInterest')

    for profile in Profile.objects.all().iterator():
        if profile.qualification or profile.institution:
            Education.objects.get_or_create(
                profile=profile,
                qualification=profile.qualification,
                institution=profile.institution,
                defaults={'source': 'user_entered', 'confirmed_by_user': True},
            )

        skill_names = list(profile.skills.values_list('name', flat=True))
        skill_names.extend(profile.custom_skills.split(','))
        for entered_name in skill_names:
            entered_name = ' '.join(str(entered_name or '').split())
            normalized_name = normalize(entered_name)
            if normalized_name:
                ProfileSkill.objects.get_or_create(
                    profile=profile,
                    normalized_name=normalized_name,
                    defaults={
                        'entered_name': entered_name,
                        'source': 'user_entered',
                        'confirmed_by_user': True,
                    },
                )

        for entered_name in profile.interests.values_list('name', flat=True):
            entered_name = ' '.join(str(entered_name or '').split())
            normalized_name = normalize(entered_name)
            if normalized_name:
                ProfileInterest.objects.get_or_create(
                    profile=profile,
                    normalized_name=normalized_name,
                    defaults={
                        'entered_name': entered_name,
                        'source': 'user_entered',
                        'confirmed_by_user': True,
                    },
                )


def fact_fields():
    return [
        ('source', models.CharField(choices=[('user_entered', 'Entered by user'), ('cv_imported', 'Imported from CV')], default='user_entered', max_length=20)),
        ('confirmed_by_user', models.BooleanField(default=True)),
        ('created_at', models.DateTimeField(auto_now_add=True)),
        ('updated_at', models.DateTimeField(auto_now=True)),
    ]


class Migration(migrations.Migration):
    dependencies = [('core', '0005_opportunity_kind_stage_and_scope')]

    operations = [
        migrations.AddField(
            model_name='profile', name='phone',
            field=models.CharField(blank=True, max_length=40),
        ),
        migrations.CreateModel(
            name='Education',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                *fact_fields(),
                ('institution', models.CharField(max_length=200)),
                ('qualification', models.CharField(max_length=200)),
                ('field_of_study', models.CharField(blank=True, max_length=200)),
                ('nqf_level', models.PositiveSmallIntegerField(blank=True, null=True)),
                ('start_date', models.DateField(blank=True, null=True)),
                ('end_date', models.DateField(blank=True, null=True)),
                ('currently_studying', models.BooleanField(default=False)),
                ('description', models.TextField(blank=True)),
                ('profile', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='education_records', to='core.profile')),
            ],
            options={'ordering': ['-currently_studying', '-end_date', '-start_date', '-id']},
        ),
        migrations.CreateModel(
            name='ProfileSkill',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                *fact_fields(),
                ('entered_name', models.CharField(max_length=100)),
                ('normalized_name', models.CharField(max_length=100)),
                ('profile', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='profile_skills', to='core.profile')),
            ],
            options={'ordering': ['normalized_name', 'id']},
        ),
        migrations.CreateModel(
            name='ProfileInterest',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                *fact_fields(),
                ('entered_name', models.CharField(max_length=100)),
                ('normalized_name', models.CharField(max_length=100)),
                ('profile', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='profile_interests', to='core.profile')),
            ],
            options={'ordering': ['normalized_name', 'id']},
        ),
        migrations.CreateModel(
            name='Achievement',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                *fact_fields(),
                ('title', models.CharField(max_length=200)),
                ('organisation', models.CharField(blank=True, max_length=200)),
                ('date', models.DateField(blank=True, null=True)),
                ('description', models.TextField(blank=True)),
                ('profile', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='achievements', to='core.profile')),
            ],
            options={'ordering': ['-date', '-id']},
        ),
        migrations.CreateModel(
            name='Volunteering',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                *fact_fields(),
                ('role', models.CharField(max_length=200)),
                ('organisation', models.CharField(max_length=200)),
                ('location', models.CharField(blank=True, max_length=200)),
                ('start_date', models.DateField(blank=True, null=True)),
                ('end_date', models.DateField(blank=True, null=True)),
                ('currently_volunteering', models.BooleanField(default=False)),
                ('description', models.TextField(blank=True)),
                ('profile', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='volunteering_records', to='core.profile')),
            ],
            options={'ordering': ['-currently_volunteering', '-end_date', '-start_date', '-id']},
        ),
        migrations.CreateModel(
            name='Language',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                *fact_fields(),
                ('language', models.CharField(max_length=100)),
                ('proficiency', models.CharField(blank=True, choices=[('basic', 'Basic'), ('conversational', 'Conversational'), ('professional', 'Professional working proficiency'), ('fluent', 'Fluent'), ('native', 'Native or bilingual')], max_length=30)),
                ('profile', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='languages', to='core.profile')),
            ],
            options={'ordering': ['language', 'id']},
        ),
        migrations.CreateModel(
            name='ProfileLink',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                *fact_fields(),
                ('label', models.CharField(max_length=100)),
                ('link_type', models.CharField(choices=[('portfolio', 'Portfolio'), ('linkedin', 'LinkedIn'), ('github', 'GitHub'), ('website', 'Website'), ('other', 'Other')], default='portfolio', max_length=20)),
                ('url', models.URLField()),
                ('profile', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='profile_links', to='core.profile')),
            ],
            options={'ordering': ['link_type', 'label', 'id']},
        ),
        migrations.CreateModel(
            name='CareerPreference',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                *fact_fields(),
                ('target_roles', models.TextField(blank=True, help_text='Separate values with commas')),
                ('preferred_locations', models.TextField(blank=True, help_text='Separate values with commas')),
                ('work_arrangement', models.CharField(blank=True, choices=[('', 'No preference'), ('onsite', 'On-site'), ('hybrid', 'Hybrid'), ('remote', 'Remote'), ('flexible', 'Flexible')], max_length=20)),
                ('opportunity_kinds', models.TextField(blank=True, help_text='Separate values with commas')),
                ('industries', models.TextField(blank=True, help_text='Separate values with commas')),
                ('short_career_goal', models.TextField(blank=True)),
                ('profile', models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name='career_preferences', to='core.profile')),
            ],
        ),
        *[
            migrations.AddField(model_name=model_name, name=name, field=field)
            for model_name in ('project', 'workexperience', 'certification')
            for name, field in fact_fields()
        ],
        migrations.AddField(model_name='project', name='start_date', field=models.DateField(blank=True, null=True)),
        migrations.AddField(model_name='project', name='end_date', field=models.DateField(blank=True, null=True)),
        migrations.AddField(model_name='project', name='currently_ongoing', field=models.BooleanField(default=False)),
        migrations.AddField(model_name='project', name='project_url', field=models.URLField(blank=True)),
        migrations.AddField(model_name='workexperience', name='location', field=models.CharField(blank=True, max_length=200)),
        migrations.AddField(model_name='workexperience', name='employment_type', field=models.CharField(blank=True, max_length=100)),
        migrations.AddField(model_name='workexperience', name='start_date', field=models.DateField(blank=True, null=True)),
        migrations.AddField(model_name='workexperience', name='end_date', field=models.DateField(blank=True, null=True)),
        migrations.AddField(model_name='workexperience', name='currently_working', field=models.BooleanField(default=False)),
        migrations.AddField(model_name='workexperience', name='description', field=models.TextField(blank=True)),
        migrations.AddField(model_name='certification', name='issuer', field=models.CharField(blank=True, max_length=200)),
        migrations.AddField(model_name='certification', name='issue_date', field=models.DateField(blank=True, null=True)),
        migrations.AddField(model_name='certification', name='expiry_date', field=models.DateField(blank=True, null=True)),
        migrations.AddField(model_name='certification', name='does_not_expire', field=models.BooleanField(default=False)),
        migrations.AddField(model_name='certification', name='credential_id', field=models.CharField(blank=True, max_length=200)),
        migrations.AddField(model_name='certification', name='credential_url', field=models.URLField(blank=True)),
        migrations.AddField(model_name='certification', name='description', field=models.TextField(blank=True)),
        migrations.AddConstraint(
            model_name='profileskill',
            constraint=models.UniqueConstraint(fields=('profile', 'normalized_name'), name='unique_profile_skill_name'),
        ),
        migrations.AddConstraint(
            model_name='profileinterest',
            constraint=models.UniqueConstraint(fields=('profile', 'normalized_name'), name='unique_profile_interest_name'),
        ),
        migrations.RunPython(preserve_legacy_profile_data, migrations.RunPython.noop),
    ]
