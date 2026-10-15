from django.db import migrations


def require_reverification(apps, schema_editor):
    Organization = apps.get_model('employers', 'Organization')
    OrganizationVerification = apps.get_model('employers', 'OrganizationVerification')
    EmployerVacancy = apps.get_model('employers', 'EmployerVacancy')
    OrganizationRiskFlag = apps.get_model('employers', 'OrganizationRiskFlag')
    database = schema_editor.connection.alias

    approved = Organization.objects.using(database).filter(
        verification_status='approved', account_status='active')
    for organization in approved.iterator():
        latest = (OrganizationVerification.objects.using(database)
            .filter(organization_id=organization.pk).order_by('-submitted_at', '-pk').first())
        if latest and latest.status == 'approved' and latest.independent_contact_confirmed_at:
            continue
        OrganizationRiskFlag.objects.using(database).get_or_create(
            organization_id=organization.pk,
            code='independent_contact_recheck_required',
            scope='organization',
            resolved_at=None,
            defaults={
                'severity': 'high_risk',
                'evidence': {'reason': 'Prior approval has no recorded independent company-contact check'},
            },
        )
        EmployerVacancy.objects.using(database).filter(
            organization_id=organization.pk, status='published').update(trust_hold=True)
        Organization.objects.using(database).filter(pk=organization.pk).update(
            verification_status='more_info_required')


class Migration(migrations.Migration):
    dependencies = [
        ('employers', '0006_organizationverification_independent_contact_confirmed_at_and_more'),
    ]

    operations = [
        migrations.RunPython(require_reverification, migrations.RunPython.noop),
    ]
