from django.db import migrations, transaction
from django.db.models import Q


def purge_adzuna(apps, schema_editor):
    Source = apps.get_model('ingestion', 'IngestionSource')
    Raw = apps.get_model('ingestion', 'RawOpportunity')
    Run = apps.get_model('ingestion', 'IngestionRun')
    SourceRecord = apps.get_model('ingestion', 'OpportunitySourceRecord')
    Opportunity = apps.get_model('core', 'Opportunity')
    Saved = apps.get_model('core', 'SavedOpportunity')
    Report = apps.get_model('core', 'OpportunityReport')
    ModerationEvent = apps.get_model('core', 'OpportunityModerationEvent')
    Snapshot = apps.get_model('core', 'CareerMarketSnapshot')
    Notification = apps.get_model('core', 'Notification')
    Application = apps.get_model('applications', 'Application')
    Document = apps.get_model('applications', 'GeneratedDocument')
    Enrichment = apps.get_model('applications', 'OpportunityJobEnrichment')
    Interview = apps.get_model('interview', 'Interview')
    Question = apps.get_model('interview', 'InterviewQuestion')
    EmployerVacancy = apps.get_model('employers', 'EmployerVacancy')
    HiringApplication = apps.get_model('employers', 'HiringApplication')

    source = Source.objects.filter(code='adzuna').first()
    source_ids = [source.pk] if source else []
    opportunity_ids = set(Opportunity.objects.filter(
        Q(source_records__source_id__in=source_ids) |
        Q(source_platform__iexact='adzuna')
    ).distinct().values_list('pk', flat=True))
    if not source and not opportunity_ids:
        return

    # Never destroy a canonical opportunity that has survived under another
    # source, nor employer-owned data whose relationship requires review.
    if SourceRecord.objects.filter(opportunity_id__in=opportunity_ids).exclude(source_id__in=source_ids).exists():
        raise RuntimeError('Adzuna purge stopped: a selected opportunity has another source record.')
    if EmployerVacancy.objects.filter(opportunity_id__in=opportunity_ids).exists():
        raise RuntimeError('Adzuna purge stopped: an employer vacancy depends on a selected opportunity.')
    application_ids = list(Application.objects.filter(
        opportunity_id__in=opportunity_ids
    ).values_list('pk', flat=True))
    if HiringApplication.objects.filter(application_id__in=application_ids).exists():
        raise RuntimeError('Adzuna purge stopped: employer hiring history depends on a selected application.')

    artifact_names = []
    for row in Document.objects.filter(
        Q(opportunity_id__in=opportunity_ids) | Q(application_id__in=application_ids)
    ).only('pdf_file', 'docx_file'):
        for field_name in ('pdf_file', 'docx_file'):
            field = getattr(row, field_name)
            if field and field.name:
                artifact_names.append((field.storage, field.name))
    interview_ids = list(Interview.objects.filter(
        Q(opportunity_id__in=opportunity_ids) | Q(application_id__in=application_ids)
    ).values_list('pk', flat=True))
    for row in Question.objects.filter(interview_id__in=interview_ids).only('audio_file', 'video_file'):
        for field_name in ('audio_file', 'video_file'):
            field = getattr(row, field_name)
            if field and field.name:
                artifact_names.append((field.storage, field.name))

    report_ids = list(Report.objects.filter(opportunity_id__in=opportunity_ids).values_list('pk', flat=True))
    snapshot_ids = list(Snapshot.objects.filter(
        members__opportunity_id__in=opportunity_ids
    ).distinct().values_list('pk', flat=True))

    with transaction.atomic():
        # Snapshot metrics are frozen aggregates, so any contaminated snapshot
        # is removed as a whole before its protected memberships can block work.
        Snapshot.objects.filter(pk__in=snapshot_ids).delete()
        Notification.objects.filter(
            event_key__in=[f'opportunityreport:resolved:{pk}' for pk in report_ids]
        ).delete()
        Report.objects.filter(pk__in=report_ids).delete()
        ModerationEvent.objects.filter(opportunity_id__in=opportunity_ids).delete()
        Interview.objects.filter(pk__in=interview_ids).delete()
        Document.objects.filter(
            Q(opportunity_id__in=opportunity_ids) | Q(application_id__in=application_ids)
        ).delete()
        Application.objects.filter(pk__in=application_ids).delete()
        Saved.objects.filter(opportunity_id__in=opportunity_ids).delete()
        Enrichment.objects.filter(opportunity_id__in=opportunity_ids).delete()
        Opportunity.objects.filter(pk__in=opportunity_ids).delete()
        Raw.objects.filter(source_id__in=source_ids).delete()
        Run.objects.filter(source_id__in=source_ids).delete()
        Source.objects.filter(pk__in=source_ids).delete()

        def remove_unreferenced_artifacts():
            for storage, name in artifact_names:
                if not Document.objects.filter(Q(pdf_file=name) | Q(docx_file=name)).exists() and not Question.objects.filter(Q(audio_file=name) | Q(video_file=name)).exists():
                    storage.delete(name)

        transaction.on_commit(remove_unreferenced_artifacts)


class Migration(migrations.Migration):
    dependencies = [
        ('ingestion', '0009_alter_ingestionsource_source_type'),
        ('applications', '0003_alter_application_status'),
        ('core', '0012_privacyrequest_legalacceptanceevent_and_more'),
        ('employers', '0005_talentinvitation_and_more'),
        ('interview', '0004_interview_camera_analysis'),
    ]

    operations = [migrations.RunPython(purge_adzuna, migrations.RunPython.noop)]
