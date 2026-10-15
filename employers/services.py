"""Owned mutations; candidate records are touched only by candidate actions."""
import hashlib
import json
from uuid import uuid4

from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.shortcuts import get_object_or_404
from django.urls import reverse
from django.utils import timezone
from django.utils.text import slugify

from applications.models import Application, GeneratedDocument
from core.models import Opportunity
from core.services.notifications import notify
from ingestion.models import IngestionSource, RawOpportunity, OpportunitySourceRecord, OpportunityExtraction
from ingestion.ai.payload import build_compact_opportunity_payload
from .models import (Organization, OrganizationMembership, EmployerVacancy, VacancyRequirement,
    HiringApplication, ApplicationStageEvent, EmployerApplicationNote, SubmittedDocument, EmployerAuditEvent)
from .permissions import membership_for, visible_hiring_applications, organization_can_use_employer_platform
from .abuse import limited
from .trust_services import validate_destination_attempt

VACANCY_FIELDS = ('title', 'reference_number', 'location', 'city', 'province', 'country',
    'work_arrangement', 'employment_type', 'opportunity_kind', 'career_stage', 'description',
    'responsibilities', 'closing_date', 'salary_min', 'salary_max', 'salary_currency', 'salary_period', 'application_instructions', 'no_candidate_fees', 'external_destination')
REQUIREMENT_FIELDS = ('criterion_type', 'requirement_level', 'raw_value', 'minimum_years', 'maximum_years', 'minimum_nqf', 'context')
VISIBLE_STAGES = {'received', 'assessment', 'interview', 'offer', 'hired', 'rejected'}
APPLICATION_STATUS_FOR_STAGE = {
    'received': 'applied', 'screening': 'applied', 'shortlisted': 'applied',
    'assessment': 'assessment', 'interview': 'interview',
    'final_review': 'interview', 'offer': 'offer', 'hired': 'hired',
    'rejected': 'rejected',
}
STAGE_ORDER = ('received', 'screening', 'shortlisted', 'assessment', 'interview', 'final_review', 'offer', 'hired')


def audit(actor, organization, event_type, obj, **context):
    return EmployerAuditEvent.objects.create(actor=actor, organization=organization, event_type=event_type,
        object_type=obj._meta.model_name, object_id=obj.pk, context=context)


def owned_organization(user, organization_id, capability):
    # Serialize organization mutations, including last-owner checks and publish.
    get_object_or_404(Organization.objects.select_for_update(), pk=organization_id)
    return membership_for(user, organization_id, capability).organization


@limited('organization_create')
@transaction.atomic
def create_organization(*, user, data):
    if not user.is_authenticated:
        raise PermissionDenied
    organization = Organization(created_by=user, slug=f'{slugify(data["name"])[:180] or "organization"}-{uuid4().hex[:12]}',
        **{key: data[key] for key in ('name', 'website', 'description', 'industry', 'location') if key in data})
    from .domains import website_domain
    organization.primary_domain = website_domain(organization.website)
    organization.full_clean()
    organization.save()
    member = OrganizationMembership.objects.create(organization=organization, user=user, role='owner', status='active')
    audit(user, organization, 'organization_created', organization)
    audit(user, organization, 'membership_created', member, role='owner', status='active')
    from .trust_services import member_signal
    member_signal(member,user)
    return organization


@transaction.atomic
def update_organization(*, user, organization_id, data):
    organization = owned_organization(user, organization_id, 'settings')
    previous_name, previous_domain = organization.name, organization.primary_domain
    for key in ('name', 'website', 'description', 'industry', 'location'):
        if key in data:
            setattr(organization, key, data[key])
    organization.full_clean()
    organization.save()
    from .domains import website_domain
    from .trust_services import normalized, flag, hold_publications
    domain = website_domain(organization.website)
    if organization_can_use_employer_platform(organization) and (domain != previous_domain or normalized(previous_name) != normalized(organization.name)):
        organization.verification_status = 'draft'
        hold_publications(organization)
        flag(organization,user,'identity_changed_after_approval',{'fields':['display_name_or_primary_domain']},severity='high_risk')
        audit(user,organization,'identity_sensitive_fields_changed',organization)
    organization.primary_domain = domain
    organization.save(update_fields=['primary_domain','verification_status','updated_at'])
    audit(user, organization, 'organization_updated', organization)
    for vacancy in organization.vacancies.filter(status='published', trust_hold=False) if organization_can_use_employer_platform(organization) else []:
        _publish(vacancy, user, organization, event='vacancy_updated')
    return organization


@transaction.atomic
def change_membership(*, user, organization_id, username, role, status='invited'):
    organization = owned_organization(user, organization_id, 'team')
    if role not in dict(OrganizationMembership.ROLES) or status not in dict(OrganizationMembership.STATUS):
        raise ValidationError('Choose a valid role and membership state.')
    target_user = get_user_model().objects.filter(username=username).first()
    if target_user is None:
        raise ValidationError('Enter the exact username of an existing CareerGPS account.')
    member = OrganizationMembership.objects.filter(organization=organization, user=target_user).first()
    if member and member.role == 'owner' and member.status == 'active' and (role != 'owner' or status != 'active'):
        if not organization.memberships.filter(role='owner', status='active').exclude(pk=member.pk).exists():
            raise ValidationError('Keep at least one active organization owner.')
    if status == 'active' and (member is None or member.status != 'active'):
        raise ValidationError('Invite this member first; they must accept the invitation.')
    member, _ = OrganizationMembership.objects.update_or_create(organization=organization, user=target_user,
        defaults={'role': role, 'status': status})
    from .trust_services import member_signal
    member_signal(member,user)
    audit(user, organization, 'membership_changed', member, role=role, status=status)
    return member


@transaction.atomic
def accept_invitation(*, user, membership_id):
    member = get_object_or_404(OrganizationMembership.objects.select_for_update().select_related('organization'),
        pk=membership_id, user=user, status='invited', organization__account_status='active')
    member.status = 'active'
    member.save(update_fields=['status', 'updated_at'])
    from .trust_services import member_signal
    member_signal(member,user)
    audit(user, member.organization, 'membership_accepted', member, role=member.role)
    return member


def _requirement_payload(vacancy):
    return [{'id': row.pk, 'criterion_type': row.criterion_type, 'requirement_level': row.requirement_level,
        'raw_value': row.raw_value, 'normalized_value': ' '.join(row.raw_value.casefold().split()),
        'evidence_text': row.raw_value, 'source_section': 'Employer-entered structured requirement',
        'minimum_years': row.minimum_years, 'maximum_years': row.maximum_years,
        'metadata': {'context': row.context, 'minimum_nqf': row.minimum_nqf,
            'origin': 'employer_entered', 'confirmation_origin': row.origin,
            'source_excerpt': row.source_excerpt}}
        for row in vacancy.requirements.all()]


@validate_destination_attempt
@transaction.atomic
def save_vacancy(*, user, organization_id, data, requirements, vacancy_id=None):
    organization = owned_organization(user, organization_id, 'vacancy')
    vacancy = get_object_or_404(EmployerVacancy, pk=vacancy_id, organization=organization) if vacancy_id else EmployerVacancy(organization=organization, created_by=user)
    for key in VACANCY_FIELDS:
        if key in data:
            setattr(vacancy, key, data[key])
    vacancy.full_clean()
    vacancy.save()
    vacancy.requirements.all().delete()
    for data in requirements:
        requirement = VacancyRequirement(vacancy=vacancy, **{key: data.get(key) for key in REQUIREMENT_FIELDS if key in data},
            source_excerpt=data.get('source_excerpt', ''), origin=data.get('origin', 'employer_authored'))
        requirement.full_clean()
        requirement.save()
    from .trust_services import check_vacancy
    check_vacancy(vacancy,user)
    if vacancy.status == 'published' and not vacancy.trust_hold:
        _publish(vacancy, user, organization, event='vacancy_updated')
    else:
        audit(user, organization, 'vacancy_draft_saved', vacancy)
    return vacancy


def _publish(vacancy, user, organization, event):
    if not organization_can_use_employer_platform(organization):
        raise ValidationError('Platform verification and an active organization are required to publish.')
    if vacancy.trust_hold:
        raise ValidationError('Platform review must release this vacancy before publication.')
    if not vacancy.no_candidate_fees:
        raise ValidationError('Confirm that candidates are not required to pay an application or recruitment fee.')
    from .trust_services import check_vacancy
    check_vacancy(vacancy,user)
    if vacancy.closing_date and vacancy.closing_date < timezone.localdate():
        raise ValidationError('Set a current or future closing date before publishing or reopening.')
    requirements = _requirement_payload(vacancy)
    opportunity = vacancy.opportunity or Opportunity()
    for key in VACANCY_FIELDS:
        if key not in {'application_instructions','no_candidate_fees','external_destination'}:
            setattr(opportunity, key, getattr(vacancy, key))
    opportunity.company = organization.name
    opportunity.sector = organization.industry
    opportunity.location_text = vacancy.location
    opportunity.opportunity_type = {'internship': 'internship', 'learnership': 'learnership', 'graduate_programme': 'graduate'}.get(
        vacancy.opportunity_kind, 'entry_level' if vacancy.career_stage == 'early_career' else 'unclassified')
    opportunity.scope_status = 'in_scope'
    opportunity.listing_kind = 'vacancy'
    opportunity.is_active = True
    opportunity.lifecycle_status = 'active'
    opportunity.application_deadline = vacancy.closing_date
    opportunity.source_platform = 'CareerGPS Direct'
    opportunity.salary_is_predicted = False if vacancy.salary_min is not None or vacancy.salary_max is not None else None
    opportunity.minimum_nqf = max((row['metadata']['minimum_nqf'] for row in requirements
        if row['requirement_level'] == 'required' and row['metadata'].get('minimum_nqf') is not None), default=None)
    opportunity.last_seen_at = timezone.now()
    opportunity.posted_at = opportunity.posted_at or timezone.now()
    requirement_digest = hashlib.sha256(json.dumps(requirements, sort_keys=True).encode()).hexdigest()
    opportunity.source_specific_evidence = {'description_fidelity': 'full_structured',
        'requirement_origin': 'employer_entered', 'direct_requirement_digest': requirement_digest,
        'application_instructions': vacancy.application_instructions}
    opportunity.evidence_provenance = {'origin': 'employer_entered', 'organization_id': organization.pk, 'vacancy_id': vacancy.pk}
    opportunity.save()
    vacancy.opportunity = opportunity
    vacancy.published_requirements = requirements
    vacancy.published_input_hash = build_compact_opportunity_payload(opportunity)['input_hash']
    vacancy.publication_revision += 1
    vacancy.status = 'published'
    vacancy.save(update_fields=['opportunity', 'published_requirements', 'published_input_hash', 'publication_revision', 'status', 'updated_at'])
    # Publication replaces any accidental extraction for this direct source; it
    # never writes employer criteria into an AI extraction table.
    OpportunityExtraction.objects.filter(opportunity=opportunity).update(status='stale')
    source, _ = IngestionSource.objects.get_or_create(code='careergps-direct', defaults={
        'name': 'CareerGPS Direct', 'source_type': 'direct', 'active': False, 'priority': 1})
    payload = {key: str(getattr(vacancy, key)) if getattr(vacancy, key) is not None else '' for key in VACANCY_FIELDS}
    payload.update(requirements=requirements, organization_id=organization.pk, company=organization.name)
    requirement_text = [row['evidence_text'] for row in requirements if row.get('evidence_text')]
    raw_content = '\n'.join(filter(None, [
        vacancy.title, organization.name, vacancy.location, vacancy.city,
        vacancy.province, vacancy.country, vacancy.employment_type,
        vacancy.work_arrangement, vacancy.description, vacancy.responsibilities,
        *requirement_text,
    ]))
    raw_hash = hashlib.sha256(json.dumps(
        {'raw_payload': payload, 'raw_content': raw_content},
        sort_keys=True).encode()).hexdigest()
    raw, _ = RawOpportunity.objects.get_or_create(source=source, source_external_id=str(vacancy.pk), content_hash=raw_hash,
        defaults={'raw_payload': payload, 'raw_content': raw_content,
            'parser_version': 'employer-publication-v1', 'normalization_status': 'published',
            'opportunity_kind': vacancy.opportunity_kind, 'career_stage': vacancy.career_stage, 'scope_status': 'in_scope'})
    OpportunitySourceRecord.objects.update_or_create(source=source, source_external_id=str(vacancy.pk), defaults={
        'opportunity': opportunity, 'current_raw': raw, 'reference_number': vacancy.reference_number, 'active_at_source': True})
    audit(user, organization, event, vacancy, revision=vacancy.publication_revision, opportunity_id=opportunity.pk)
    return vacancy


@limited('vacancy_publish','vacancy')
@transaction.atomic
def publish_vacancy(*, user, organization_id, vacancy_id):
    organization = owned_organization(user, organization_id, 'vacancy')
    vacancy = get_object_or_404(EmployerVacancy, organization=organization, pk=vacancy_id)
    result = _publish(vacancy, user, organization, event='vacancy_reopened' if vacancy.status == 'closed' else 'vacancy_published')
    from .trust_services import publication_velocity
    publication_velocity(organization,user)
    return result


@transaction.atomic
def close_vacancy(*, user, organization_id, vacancy_id):
    organization = owned_organization(user, organization_id, 'vacancy')
    vacancy = get_object_or_404(EmployerVacancy, organization=organization, pk=vacancy_id)
    if vacancy.status == 'closed':
        return vacancy
    vacancy.status = 'closed'
    vacancy.save(update_fields=['status', 'updated_at'])
    if vacancy.opportunity_id:
        vacancy.opportunity.is_active = False
        vacancy.opportunity.lifecycle_status = 'removed'
        vacancy.opportunity.save(update_fields=['is_active', 'lifecycle_status'])
        vacancy.opportunity.source_records.filter(source__code='careergps-direct').update(active_at_source=False)
    audit(user, organization, 'vacancy_closed', vacancy)
    return vacancy


def _read_document_file(file):
    if not file:
        return b''
    with file.open('rb') as stream:
        data = stream.read(10 * 1024 * 1024 + 1)
    if len(data) > 10 * 1024 * 1024:
        raise ValidationError('Each submitted file must be at most 10 MB.')
    return data


@transaction.atomic
def submit_application(*, user, application_id, document_ids, consent):
    application = get_object_or_404(Application.objects.select_related('opportunity'), pk=application_id, user=user)
    source = get_object_or_404(EmployerVacancy, opportunity_id=application.opportunity_id)
    Organization.objects.select_for_update().get(pk=source.organization_id)
    application = Application.objects.select_for_update().select_related('opportunity').get(pk=application_id, user=user)
    vacancy = get_object_or_404(EmployerVacancy.objects.select_related('organization'), opportunity=application.opportunity, status='published',
        organization__account_status='active', organization__verification_status='approved', trust_hold=False)
    if not Opportunity.objects.professionally_ready().filter(pk=application.opportunity_id).exists():
        raise ValidationError('This vacancy is no longer accepting applications.')
    existing = HiringApplication.objects.filter(application=application).first()
    if existing:
        if existing.withdrawn_at:
            raise ValidationError('This application has been withdrawn. Resubmission is not available in this phase.')
        return existing
    if consent is not True:
        raise ValidationError('Confirm that you want to share the selected documents and job-relevant profile evidence.')
    documents = list(GeneratedDocument.objects.filter(
        pk__in=document_ids, user=user, application=application,
        opportunity=application.opportunity, clean_revision=application.clean_revision))
    if len(documents) != len(set(document_ids)) or not 1 <= len(documents) <= 2 or {doc.document_type for doc in documents} not in ({'cv'}, {'cv', 'cover_letter'}):
        raise ValidationError('Select your CV and optionally one cover letter from this application.')
    if len({doc.document_type for doc in documents}) != len(documents):
        raise ValidationError('Select only one version of each document.')
    frozen = []
    for document in documents:
        try:
            pdf, docx = _read_document_file(document.pdf_file), _read_document_file(document.docx_file)
        except (OSError, ValueError):
            raise ValidationError('A selected document file is unavailable. Regenerate it before submitting.') from None
        if not pdf and not docx:
            raise ValidationError('Selected documents must have a downloadable file.')
        frozen.append((document, pdf, docx))
    hiring = HiringApplication(application=application, vacancy=vacancy)
    hiring.full_clean()
    hiring.save()
    for document, pdf, docx in frozen:
        SubmittedDocument.objects.create(hiring_application=hiring, original_document=document, document_type=document.document_type,
            pdf_bytes=pdf, docx_bytes=docx, pdf_sha256=hashlib.sha256(pdf).hexdigest() if pdf else '', docx_sha256=hashlib.sha256(docx).hexdigest() if docx else '')
    application.status = 'applied'
    application.applied_at = timezone.now()
    application.save(update_fields=['status', 'applied_at', 'updated_at'])
    event = ApplicationStageEvent.objects.create(hiring_application=hiring, to_stage='received', changed_by=user)
    audit(user, vacancy.organization, 'application_received', hiring)
    _notify_stage(hiring, event)
    return hiring


def _notify_stage(hiring, event):
    if not event.candidate_visible:
        return
    notify(user=hiring.application.user, category='applications', event_key=f'hiring-stage:{event.pk}',
        title=f'Application update: {event.get_to_stage_display()}',
        message=f'{hiring.vacancy.title} at {hiring.vacancy.organization.name}: {event.get_to_stage_display()}.',
        target_url=reverse('applications:workspace', args=[hiring.application_id]))


@transaction.atomic
def move_stage(*, user, organization_id, hiring_id, stage, reason_category=''):
    organization = owned_organization(user, organization_id, 'pipeline')
    hiring = get_object_or_404(HiringApplication.objects.select_for_update().select_related('application__user', 'vacancy__organization'),
        pk=hiring_id, vacancy__organization=organization)
    if hiring.withdrawn_at:
        raise ValidationError('Withdrawn applications cannot progress through the hiring pipeline.')
    if stage not in dict(HiringApplication.STAGES) or reason_category not in dict(ApplicationStageEvent._meta.get_field('reason_category').choices):
        raise ValidationError('Choose a valid hiring stage and reason category.')
    if hiring.stage == stage:
        return hiring
    if hiring.stage in {'hired', 'rejected'}:
        raise ValidationError('A completed or rejected application cannot be reopened in this workflow.')
    if stage != 'rejected' and STAGE_ORDER.index(stage) < STAGE_ORDER.index(hiring.stage):
        raise ValidationError('Choose the current stage or a later hiring stage.')
    application = Application.objects.select_for_update().get(pk=hiring.application_id)
    if application.status == 'withdrawn':
        raise ValidationError('Withdrawn applications cannot progress through the hiring pipeline.')
    previous = hiring.stage
    hiring.stage = stage
    hiring.save(update_fields=['stage', 'updated_at'])
    application.status = APPLICATION_STATUS_FOR_STAGE[stage]
    application.save(update_fields=['status', 'updated_at'])
    event = ApplicationStageEvent.objects.create(hiring_application=hiring, from_stage=previous, to_stage=stage,
        changed_by=user, candidate_visible=stage in VISIBLE_STAGES, reason_category=reason_category)
    audit(user, organization, f'application_{stage}', hiring, from_stage=previous, to_stage=stage)
    _notify_stage(hiring, event)
    return hiring


@transaction.atomic
def add_note(*, user, organization_id, hiring_id, content):
    organization = owned_organization(user, organization_id, 'notes')
    member = membership_for(user, organization_id, 'notes')
    hiring = get_object_or_404(visible_hiring_applications(member), pk=hiring_id, vacancy__organization=organization)
    content = content.strip()
    if not content or len(content) > 10000:
        raise ValidationError('Enter a note of at most 10,000 characters.')
    note = EmployerApplicationNote(hiring_application=hiring, organization=organization, author=user, content=content)
    note.full_clean()
    note.save()
    audit(user, organization, 'private_note_added', note, hiring_application_id=hiring.pk)
    return note


@transaction.atomic
def withdraw_application(*, user, application_id):
    application = get_object_or_404(Application, user=user, pk=application_id)
    hiring = get_object_or_404(HiringApplication.objects.select_related('vacancy__organization'), application=application)
    Organization.objects.select_for_update().get(pk=hiring.vacancy.organization_id)
    application = Application.objects.select_for_update().get(pk=application_id, user=user)
    hiring = HiringApplication.objects.select_for_update().select_related('vacancy__organization').get(pk=hiring.pk)
    if hiring.stage in {'hired', 'rejected'}:
        raise ValidationError('A completed or rejected application cannot be withdrawn.')
    if hiring.withdrawn_at is None:
        hiring.withdrawn_at = timezone.now()
        hiring.save(update_fields=['withdrawn_at', 'updated_at'])
        application.status = 'withdrawn'
        application.save(update_fields=['status', 'updated_at'])
        audit(user, hiring.vacancy.organization, 'application_withdrawn', hiring)
    return hiring
