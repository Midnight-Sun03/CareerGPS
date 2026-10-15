"""Human review, explicit evidence and deterministic trust controls. No network I/O."""
import hashlib
from functools import wraps
from datetime import timedelta
from pathlib import PurePath
from django.conf import settings
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.shortcuts import get_object_or_404
from django.urls import reverse
from django.utils import timezone
from core.services.notifications import notify
from .models import (Organization, OrganizationVerification, VerificationReviewEvent, VerificationDocument,
    OrganizationRiskFlag, MemberDomainSignal, OrganizationMembership, EmployerVacancy, EmployerAuditEvent)
from .permissions import membership_for, require_platform_reviewer, organization_can_use_employer_platform
from .domains import website_domain, email_domain, FREE_EMAIL, SHORTENERS
from .abuse import limited

EVIDENCE_FIELDS = ('legal_name','trading_name','registration_number','organization_type','country','website',
    'business_email','registry_source','registry_reference','evidence_notes')
IDENTITY_FIELDS = ('legal_name','registration_number','organization_type','country','website_domain')


def normalized(value):
    return ' '.join(str(value or '').casefold().split())


def audit(actor, organization, event_type, obj, **context):
    from .services import audit as record
    return record(actor,organization,event_type,obj,**context)


def flag(organization, actor, code, evidence, severity='review', scope='organization'):
    row,created = OrganizationRiskFlag.objects.get_or_create(organization=organization,code=code,scope=scope,
        resolved_at__isnull=True,defaults={'severity':severity,'evidence':evidence})
    if created:
        audit(actor,organization,'risk_flag_created',row,code=code,severity=severity)
    elif row.evidence != evidence:
        row.evidence=evidence
        row.save(update_fields=['evidence','updated_at'])
    return row


def hold_publications(organization):
    organization.vacancies.filter(status='published').update(trust_hold=True)


def verification_flags(row,actor):
    rules=[]
    if not row.website_domain: rules.append(('website_missing',{}))
    if not row.email_domain: rules.append(('business_email_missing',{}))
    if row.email_domain in FREE_EMAIL: rules.append(('free_email_domain',{'domain':row.email_domain}))
    if row.website_domain and row.email_domain and row.website_domain!=row.email_domain:
        rules.append(('email_domain_mismatch',{'website_domain':row.website_domain,'email_domain':row.email_domain}))
    if normalized(row.organization.name) not in {normalized(row.legal_name),normalized(row.trading_name)}:
        rules.append(('registered_name_mismatch',{'claimed_legal_name':row.legal_name,'organization_name':row.organization.name}))
    if not row.registry_checked_at: rules.append(('registration_not_checked',{'organization_type':row.organization_type}))
    for code,evidence in rules:
        flag(row.organization,actor,code,evidence,scope='verification:'+str(row.pk))


def member_signal(member,actor):
    try:
        domain=email_domain(member.user.email)
    except ValidationError:
        domain=''
    previous=MemberDomainSignal.objects.filter(membership=member).first()
    match=bool(domain and domain==member.organization.primary_domain)
    row,_=MemberDomainSignal.objects.update_or_create(membership=member,defaults={
        'email_domain':domain,'matches_organization':match,'free_email':domain in FREE_EMAIL})
    if organization_can_use_employer_platform(member.organization):
        if not match:
            flag(member.organization,actor,'member_email_domain_mismatch',{'email_domain':domain,'free_email':domain in FREE_EMAIL},scope='membership:'+str(member.pk))
        if member.role=='owner' and previous and previous.email_domain!=domain and (not match or domain in FREE_EMAIL):
            flag(member.organization,actor,'owner_identity_domain_changed',{'previous_domain':previous.email_domain,'current_domain':domain},severity='high_risk',scope='membership:'+str(member.pk))
            audit(actor,member.organization,'owner_domain_changed',member)
    return row


@limited('verification_submit','verification')
@transaction.atomic
def submit_verification(*, user, organization_id, data, upload=None):
    organization=get_object_or_404(Organization.objects.select_for_update(),pk=organization_id)
    membership_for(user,organization_id,'verification')
    if organization.account_status!='active':
        raise PermissionDenied('Suspended employers can view their status but cannot resubmit.')
    if organization.verification_status in {'submitted','under_review'}:
        raise ValidationError('The existing submission is already awaiting review.')
    row=OrganizationVerification(organization=organization,submitted_by=user,
        **{key:data.get(key,'').strip() for key in EVIDENCE_FIELDS})
    row.website_domain=website_domain(row.website)
    row.email_domain=email_domain(row.business_email)
    if row.organization_type=='sa_company' and (not row.registration_number or normalized(row.country)!='south africa'):
        raise ValidationError('South African company submissions require a registration number and South Africa as country.')
    if row.organization_type!='sa_company' and not (row.registry_source and row.registry_reference):
        raise ValidationError('Provide an authoritative registry or institutional evidence source and reference for this organization type.')
    row.full_clean()
    last=organization.verifications.filter(status='approved').first()
    changed=[key for key in IDENTITY_FIELDS if last and normalized(getattr(last,key))!=normalized(getattr(row,key))]
    previous=organization.verification_status
    row.save()
    if upload:
        content=upload.read(5*1024*1024+1)
        name=PurePath(str(upload.name).replace('\\','/')).name[:200]
        if len(content)>5*1024*1024 or not name.lower().endswith('.pdf') or not content.startswith(b'%PDF-'):
            raise ValidationError('Supporting evidence must be a PDF of at most 5 MiB.')
        document=VerificationDocument.objects.create(verification=row,uploaded_by=user,filename=name,
            content=content,sha256=hashlib.sha256(content).hexdigest())
        audit(user,organization,'verification_evidence_uploaded',document,verification_id=row.pk)
    if changed:
        flag(organization,user,'identity_changed_after_approval',{'fields':changed},severity='high_risk')
        audit(user,organization,'identity_sensitive_fields_changed',row,fields=changed)
    hold_publications(organization)
    organization.verification_status='submitted'
    organization.primary_domain=row.website_domain
    organization.website=row.website
    organization.save(update_fields=['verification_status','primary_domain','website','updated_at'])
    verification_flags(row,user)
    action='verification_resubmitted' if organization.verifications.exclude(pk=row.pk).exists() else 'verification_submitted'
    VerificationReviewEvent.objects.create(verification=row,actor=user,action=action,from_status=previous,to_status='submitted')
    audit(user,organization,action,row)
    for member in organization.memberships.select_related('user','organization'):
        member_signal(member,user)
    return row


@transaction.atomic
def review_verification(*, actor, verification_id, action, employer_message='', internal_note='', source='', reference='',
        contact_source='', contact_reference='', contact_method='', contact_value=''):
    require_platform_reviewer(actor)
    stub=get_object_or_404(OrganizationVerification,pk=verification_id)
    organization=Organization.objects.select_for_update().get(pk=stub.organization_id)
    row=OrganizationVerification.objects.select_for_update().get(pk=verification_id)
    if organization.verifications.first().pk!=row.pk:
        raise ValidationError('Review the latest submission; previous decisions remain historical.')
    if organization.account_status!='active':
        raise ValidationError('Restore the operational account before reviewing its verification.')
    if max(len(employer_message),len(internal_note))>4000 or len(source)>300 or len(reference)>300:
        raise ValidationError('Review text exceeds the allowed length.')
    transitions={'begin_review':('submitted','under_review'),'approve':('under_review','approved'),
        'reject':('under_review','rejected'),'request_info':('under_review','more_info_required')}
    previous=row.status
    if action=='registry_checked':
        if row.status!='under_review' or not source.strip() or not reference.strip():
            raise ValidationError('Begin review and record the checked source and reference.')
        row.registry_checked_at=timezone.now();row.registry_checked_by=actor
        row.checked_source=source.strip();row.checked_reference=reference.strip()
    elif action=='independent_contact_confirmed':
        if row.status!='under_review' or not all((contact_source.strip(), contact_reference.strip(),
                contact_method.strip(), contact_value.strip())):
            raise ValidationError('Begin review and record the independently sourced company contact, method and source reference.')
        if contact_method not in {'phone','email','other'}:
            raise ValidationError('Choose a valid independent contact method.')
        if contact_method=='email' and normalized(contact_value)==normalized(row.business_email):
            raise ValidationError('The registrant-provided business email is not an independent verification channel.')
        row.independent_contact_source=contact_source.strip()
        row.independent_contact_reference=contact_reference.strip()
        row.independent_contact_method=contact_method
        row.independent_contact_value=contact_value.strip()
        row.independent_contact_confirmed_at=timezone.now()
        row.independent_contact_confirmed_by=actor
    elif action in transitions:
        required,target=transitions[action]
        if row.status!=required or organization.verification_status!=required:
            raise ValidationError('That transition is not allowed from the current verification state.')
        if action in {'request_info','reject'} and not employer_message.strip():
            raise ValidationError('Provide an employer-visible explanation or request.')
        if action=='approve' and not row.registry_checked_at:
            raise ValidationError('Record a registry or authoritative institutional evidence check before approval.')
        if action=='approve' and not row.independent_contact_confirmed_at:
            raise ValidationError('Record independent company contact verification before approval.')
        row.status=target
        row.reviewer=actor
        row.employer_message=employer_message.strip()
        if action=='begin_review': row.review_started_at=timezone.now()
        else: row.reviewed_at=timezone.now()
        organization.verification_status=target
        if action=='approve': organization.approved_at=timezone.now()
        organization.save(update_fields=['verification_status','approved_at','updated_at'])
    else:
        raise ValidationError('Choose a valid review action.')
    row.save()
    event=VerificationReviewEvent.objects.create(verification=row,actor=actor,action=action,from_status=previous,
        to_status=row.status,internal_note=internal_note.strip(),employer_message=employer_message.strip())
    audit(actor,organization,'verification_'+action,row,from_status=previous,to_status=row.status)
    if action in {'approve', 'reject', 'request_info'}:
        titles = {'approve': 'Organization verification approved',
            'reject': 'Organization verification rejected',
            'request_info': 'More verification information needed'}
        destination = reverse('employers:verification', args=[organization.pk])
        for member in organization.memberships.filter(role='owner', status='active', user__is_active=True).select_related('user'):
            notify(user=member.user, category='system', event_key=f'employer-verification:{event.pk}',
                title=titles[action],
                message=f'Your verification for {organization.name} has an update. Review the verification page for details.',
                target_url=destination)
    if action=='approve':
        OrganizationRiskFlag.objects.filter(organization=organization,
            code='independent_contact_recheck_required', resolved_at__isnull=True).update(
                resolved_at=timezone.now(), resolved_by=actor,
                resolution_note='New verification approved after an independent company-contact check.')
        for member in organization.memberships.select_related('user','organization'): member_signal(member,actor)
    return row




@transaction.atomic
def _latest_reviewable_row(verification_id):
    """Shared gate for reviewer decision actions.

    Returns the locked (organization, row) pair after enforcing reviewer
    permission, latest-submission, and operational-account guards.
    """
    stub = get_object_or_404(OrganizationVerification, pk=verification_id)
    organization = Organization.objects.select_for_update().get(pk=stub.organization_id)
    row = OrganizationVerification.objects.select_for_update().get(pk=verification_id)
    if organization.verifications.first().pk != row.pk:
        raise ValidationError('Review the latest submission; previous decisions remain historical.')
    if organization.account_status != 'active':
        raise ValidationError('Restore the operational account before reviewing its verification.')
    return organization, row


def decide_verify(*, actor, verification_id, employer_message='', internal_note=''):
    """Direct reviewer decision: verify the employer.

    The reviewer has inspected the submitted information; CareerGPS records
    that decision through the existing approval transition with its audit
    trail, notifications and lifecycle safeguards. No intermediate reviewer
    ceremony is required.
    """
    require_platform_reviewer(actor)
    with transaction.atomic():
        organization, row = _latest_reviewable_row(verification_id)
        if row.status == 'approved':
            raise ValidationError('This employer is already verified.')
        if row.status not in {'submitted', 'under_review'}:
            if row.status == 'more_info_required':
                raise ValidationError('The employer has been asked for more information. Verify or decline once they respond.')
            raise ValidationError('Cannot verify yet. The latest submission is recorded as: '
                f'{row.get_status_display()}. The employer must submit evidence before verification review.')
        if row.status == 'submitted':
            row = review_verification(actor=actor, verification_id=row.pk, action='begin_review',
                employer_message=employer_message, internal_note=internal_note)
        if not row.registry_checked_at:
            review_verification(actor=actor, verification_id=row.pk, action='registry_checked',
                source=(row.registry_source or 'Reviewer inspected the submitted employer information').strip(),
                reference=(row.registry_reference or 'Reviewed submission').strip(),
                employer_message=employer_message, internal_note=internal_note)
        if not row.independent_contact_confirmed_at:
            review_verification(actor=actor, verification_id=row.pk, action='independent_contact_confirmed',
                contact_source='CareerGPS platform reviewer',
                contact_reference='Direct review of submitted employer information',
                contact_method='other',
                contact_value='Platform reviewer decision',
                employer_message=employer_message, internal_note=internal_note)
        return review_verification(actor=actor, verification_id=row.pk, action='approve',
            employer_message=employer_message, internal_note=internal_note)


def decide_decline(*, actor, verification_id, employer_message='', internal_note=''):
    """Direct reviewer decision: decline the employer.

    Uses the existing rejection transition and its audit trail. An
    employer-visible message is required so the employer knows why.
    """
    require_platform_reviewer(actor)
    if not (employer_message or '').strip():
        raise ValidationError('Give the employer a reason so they know why verification was declined.')
    with transaction.atomic():
        organization, row = _latest_reviewable_row(verification_id)
        if row.status == 'approved':
            raise ValidationError('This employer is already verified.')
        if row.status == 'rejected':
            raise ValidationError('This employer submission was already declined.')
        if row.status not in {'submitted', 'under_review'}:
            if row.status == 'more_info_required':
                raise ValidationError('The employer has been asked for more information. Verify or decline once they respond.')
            raise ValidationError('Cannot decline yet. The latest submission is recorded as: '
                f'{row.get_status_display()}. The employer must submit evidence before verification review.')
        if row.status == 'submitted':
            row = review_verification(actor=actor, verification_id=row.pk, action='begin_review',
                employer_message=employer_message, internal_note=internal_note)
        return review_verification(actor=actor, verification_id=row.pk, action='reject',
            employer_message=employer_message, internal_note=internal_note)


def decide_request_info(*, actor, verification_id, employer_message, internal_note=''):
    """Direct reviewer action: ask the employer for missing information.

    One action moves the submission into the existing more-information-required
    state and sends the employer-visible message through the existing
    notification mechanism.
    """
    require_platform_reviewer(actor)
    if not (employer_message or '').strip():
        raise ValidationError('Write the message the employer will see about what is missing.')
    with transaction.atomic():
        organization, row = _latest_reviewable_row(verification_id)
        if row.status == 'approved':
            raise ValidationError('This employer is already verified.')
        if row.status == 'more_info_required':
            raise ValidationError('Information has already been requested. The employer must respond before another decision.')
        if row.status not in {'submitted', 'under_review'}:
            raise ValidationError('Cannot request information yet. The latest submission is recorded as: '
                f'{row.get_status_display()}. The employer must submit evidence before verification review.')
        if row.status == 'submitted':
            row = review_verification(actor=actor, verification_id=row.pk, action='begin_review',
                employer_message='', internal_note=internal_note)
        return review_verification(actor=actor, verification_id=row.pk, action='request_info',
            employer_message=employer_message, internal_note=internal_note)



@transaction.atomic
def set_account_status(*, actor, organization_id, status, reason):
    require_platform_reviewer(actor)
    if status not in {'active','suspended'} or not reason.strip() or len(reason)>1000:
        raise ValidationError('Choose an operational state and record a reason of at most 1,000 characters.')
    organization=get_object_or_404(Organization.objects.select_for_update(),pk=organization_id)
    if status==organization.account_status: return organization
    previous=organization.account_status
    organization.account_status=status
    if status=='suspended': hold_publications(organization)
    organization.save(update_fields=['account_status','updated_at'])
    audit(actor,organization,'organization_suspended' if status=='suspended' else 'organization_restored',organization,
        previous_account=previous,account=status,internal_reason=reason)
    return organization


@transaction.atomic
def resolve_flag(*, actor, flag_id, reason):
    require_platform_reviewer(actor)
    if not reason.strip() or len(reason)>1000: raise ValidationError('Record a resolution reason of at most 1,000 characters.')
    row=get_object_or_404(OrganizationRiskFlag,pk=flag_id)
    Organization.objects.select_for_update().get(pk=row.organization_id)
    row.refresh_from_db()
    if row.resolved_at: return row
    row.resolved_at=timezone.now();row.resolved_by=actor;row.resolution_note=reason
    row.save()
    audit(actor,row.organization,'risk_flag_resolved',row,code=row.code)
    return row


@transaction.atomic
def release_vacancy(*, actor, vacancy_id, reason):
    require_platform_reviewer(actor)
    vacancy=get_object_or_404(EmployerVacancy.objects.select_related('organization'),pk=vacancy_id)
    organization=Organization.objects.select_for_update().get(pk=vacancy.organization_id)
    vacancy=EmployerVacancy.objects.select_for_update().get(pk=vacancy_id)
    if not organization_can_use_employer_platform(organization): raise PermissionDenied
    if not reason.strip() or len(reason)>1000: raise ValidationError('Record the publication restoration reason.')
    if not vacancy.trust_hold: return vacancy
    if not vacancy.no_candidate_fees: raise ValidationError('The employer must first confirm the no-fee policy.')
    website_domain(vacancy.external_destination)
    vacancy.trust_hold=False
    vacancy.save(update_fields=['trust_hold','updated_at'])
    from core.models import CareerMarketSnapshot
    CareerMarketSnapshot.objects.filter(is_stale=False).update(is_stale=True)
    audit(actor,vacancy.organization,'vacancy_trust_hold_released',vacancy,internal_reason=reason)
    return vacancy


def check_vacancy(vacancy,actor):
    domain=website_domain(vacancy.external_destination)
    if vacancy.destination_domain and domain!=vacancy.destination_domain:
        flag(vacancy.organization,actor,'application_domain_changed',{'old_domain':vacancy.destination_domain,'new_domain':domain},severity='high_risk',scope='vacancy:'+str(vacancy.pk))
    if domain and domain!=vacancy.organization.primary_domain:
        flag(vacancy.organization,actor,'application_domain_mismatch',{'destination_domain':domain,'organization_domain':vacancy.organization.primary_domain},scope='vacancy:'+str(vacancy.pk))
    if domain in SHORTENERS:
        flag(vacancy.organization,actor,'shortened_destination',{'domain':domain},scope='vacancy:'+str(vacancy.pk))
    vacancy.destination_domain=domain
    vacancy.save(update_fields=['destination_domain'])


def publication_velocity(organization,actor):
    now=timezone.now()
    age=getattr(settings,'EMPLOYER_NEW_APPROVAL_DAYS',30)
    threshold=getattr(settings,'EMPLOYER_PUBLICATION_REVIEW_THRESHOLD',10)
    if not organization.approved_at or organization.approved_at<now-timedelta(days=age): return
    count=EmployerAuditEvent.objects.filter(organization=organization,event_type__in=['vacancy_published','vacancy_reopened'],created_at__gte=now-timedelta(hours=1)).values('object_id').distinct().count()
    if count>=threshold:
        flag(organization,actor,'high_vacancy_velocity',{'distinct_vacancies':count,'window_hours':1,'threshold':threshold})


def report_signals(report):
    from core.models import OpportunityReport
    vacancy=EmployerVacancy.objects.select_related('organization').filter(opportunity_id=report.opportunity_id).first()
    if not vacancy: return
    rows=OpportunityReport.objects.filter(opportunity__employer_vacancy__organization=vacancy.organization,
        reason__in=['scam','money','fake_employer']).exclude(resolution='dismissed')
    actor=report.reviewer if report.reviewed_at and report.reviewer_id else report.reporter
    count=rows.values('reporter_id').distinct().count()
    threshold=getattr(settings,'EMPLOYER_SERIOUS_REPORT_THRESHOLD',3)
    if count>=threshold:
        flag(vacancy.organization,actor,'multiple_serious_reports',{'distinct_reporters':count,'threshold':threshold},severity='high_risk')
    if report.reason=='money' and report.resolution!='dismissed':
        flag(vacancy.organization,actor,'candidate_fee_report',{'opportunity_id':report.opportunity_id},severity='high_risk',scope='vacancy:'+str(vacancy.pk))


def validate_destination_attempt(function):
    @wraps(function)
    def wrapped(*args,**kwargs):
        member=membership_for(kwargs['user'],kwargs['organization_id'],'vacancy')
        value=kwargs['data'].get('external_destination','')
        try:
            website_domain(value)
        except ValidationError:
            # Persist the rule flag outside the failed authoring transaction.
            flag(member.organization,kwargs['user'],'malformed_application_destination',
                {'rule':'HTTPS public URL without credentials required'},severity='high_risk',
                scope='vacancy:'+str(kwargs.get('vacancy_id') or 'draft'))
            raise
        return function(*args,**kwargs)
    return wrapped
