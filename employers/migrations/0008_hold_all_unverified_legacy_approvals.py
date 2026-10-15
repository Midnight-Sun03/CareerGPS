from django.db import migrations


def hold_remaining_legacy_approvals(apps, schema_editor):
    Organization = apps.get_model('employers', 'Organization')
    OrganizationVerification = apps.get_model('employers', 'OrganizationVerification')
    EmployerVacancy = apps.get_model('employers', 'EmployerVacancy')
    OrganizationRiskFlag = apps.get_model('employers', 'OrganizationRiskFlag')
    database = schema_editor.connection.alias

    # Also cover previously approved suspended accounts, which may later be restored.
    for organization in Organization.objects.using(database).filter(verification_status='approved').iterator():
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
        ('employers', '0007_require_independent_contact_for_legacy_approvals'),
    ]

    operations = [
        migrations.RunPython(hold_remaining_legacy_approvals, migrations.RunPython.noop),
    ]
