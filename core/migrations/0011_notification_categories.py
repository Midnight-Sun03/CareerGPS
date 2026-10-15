from django.db import migrations


def categorize_existing(apps, schema_editor):
    Notification = apps.get_model('core', 'Notification')
    Notification.objects.filter(notification_type__in=['story_approved', 'story_rejected']).update(category='stories')
    Notification.objects.filter(notification_type='opportunity_deadline').update(category='deadlines')
    Notification.objects.filter(notification_type='application_status').update(category='applications')


class Migration(migrations.Migration):
    dependencies = [('core', '0010_community_and_career_intelligence')]
    operations = [migrations.RunPython(categorize_existing, migrations.RunPython.noop)]
