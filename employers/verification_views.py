from io import BytesIO
from django.contrib import admin, messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError, PermissionDenied
from django.db.models import Prefetch
from django.http import FileResponse, Http404, HttpResponseBadRequest
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_GET, require_POST, require_http_methods
from .models import Organization, OrganizationVerification, VerificationDocument, OrganizationRiskFlag, EmployerVacancy
from .permissions import membership_for, require_platform_reviewer
from .verification_forms import VerificationForm, DecisionForm
from . import trust_services as services
from .views import _render


def private(response):
    response['Cache-Control']='private, no-store'
    return response


def status_context(organization):
    row=organization.verifications.first()
    return {'verification_message':row.employer_message if row else '',
        'status_label':organization.get_verification_status_display(),
        'contact_recheck_required':organization.risk_flags.filter(
            code='independent_contact_recheck_required', resolved_at__isnull=True).exists()}


@login_required
@require_http_methods(['GET','POST'])
def verification(request, organization_id):
    member=membership_for(request.user,organization_id,'verification')
    current=member.organization.verifications.first()
    initial={key:getattr(current,key) for key in services.EVIDENCE_FIELDS} if current else {
        'legal_name':member.organization.name,'website':member.organization.website,'business_email':request.user.email,'country':'South Africa'}
    form=VerificationForm(request.POST or None, request.FILES or None, initial=initial)
    if request.method=='POST' and form.is_valid():
        try:
            services.submit_verification(user=request.user,organization_id=organization_id,
                data=form.cleaned_data,upload=form.cleaned_data.get('evidence_document'))
        except ValidationError as exc:
            form.add_error(None,exc)
        else:
            return redirect('employers:verification',organization_id=organization_id)
    history=[{'submitted_at':row.submitted_at,'status':row.get_status_display(),'message':row.employer_message,
        'documents':list(row.documents.values('pk','filename'))} for row in member.organization.verifications.all()[:20]]
    editable=member.organization.account_status=='active' and member.organization.verification_status not in {'submitted','under_review'}
    return private(_render(request,'verification.html',member,form=form,history=history,editable=editable,**status_context(member.organization)))


@login_required
@require_GET
def document(request, document_id):
    row=get_object_or_404(VerificationDocument.objects.select_related('verification__organization'),pk=document_id)
    try:
        require_platform_reviewer(request.user)
    except PermissionDenied:
        member=membership_for(request.user,row.verification.organization_id,'entry')
        if member.role!='owner' and row.uploaded_by_id!=request.user.pk:
            raise Http404
    response=FileResponse(BytesIO(bytes(row.content)),as_attachment=True,filename=row.filename,content_type='application/pdf')
    response['X-Content-Type-Options']='nosniff'
    response['Content-Security-Policy']="sandbox; default-src 'none'"
    return private(response)


@login_required
@require_http_methods(['GET','POST'])
def review(request, organization_id):
    require_platform_reviewer(request.user)
    organization=get_object_or_404(Organization,pk=organization_id)
    row=organization.verifications.first()
    decision_form=DecisionForm(request.POST or None)
    decision_posted=request.method=='POST' and request.POST.get('action') in {'verify','decline','request_info'}
    if decision_posted and decision_form.is_valid():
        choice=decision_form.cleaned_data['action']
        employer_message=decision_form.cleaned_data['employer_message']
        internal_note=decision_form.cleaned_data['internal_note']
        try:
            if not row:
                raise ValidationError('The employer must submit evidence before verification review.')
            if choice=='verify':
                services.decide_verify(actor=request.user,verification_id=row.pk,employer_message=employer_message,internal_note=internal_note)
                messages.success(request,'Employer verified. Review action recorded.')
            elif choice=='decline':
                services.decide_decline(actor=request.user,verification_id=row.pk,employer_message=employer_message,internal_note=internal_note)
                messages.success(request,'Employer declined. Review action recorded.')
            else:
                if not employer_message.strip():
                    raise ValidationError('Write the message the employer will see about what is missing.')
                services.decide_request_info(actor=request.user,verification_id=row.pk,employer_message=employer_message,internal_note=internal_note)
                messages.success(request,'Message sent. Review action recorded.')
            return redirect('employers:verification_review',organization_id=organization_id)
        except ValidationError as exc:
            decision_form.add_error(None,exc)
    from core.models import OpportunityReport
    context=admin.site.each_context(request)
    context.update(title='Employer verification review',organization=organization,verification=row,
        decision_form=decision_form,
        evidence=[(OrganizationVerification._meta.get_field(key).verbose_name,getattr(row,key)) for key in services.EVIDENCE_FIELDS] if row else [],
        submissions=organization.verifications.prefetch_related('review_events__actor',Prefetch('documents',queryset=VerificationDocument.objects.defer('content'))).all(),
        flags=organization.risk_flags.select_related('resolved_by'),members=organization.memberships.select_related('user','domain_signal'),
        held_vacancies=organization.vacancies.filter(trust_hold=True),
        reports=OpportunityReport.objects.filter(opportunity__employer_vacancy__organization=organization).select_related('opportunity'),
        operational_events=organization.audit_events.filter(event_type__in=['organization_suspended','organization_restored','identity_sensitive_fields_changed']).select_related('actor'))
    return private(render(request,'employers/verification_review.html',context))


@login_required
@require_POST
def resolve_flag(request, organization_id, flag_id):
    require_platform_reviewer(request.user)
    get_object_or_404(OrganizationRiskFlag,pk=flag_id,organization_id=organization_id)
    try:
        services.resolve_flag(actor=request.user,flag_id=flag_id,reason=request.POST.get('reason',''))
    except ValidationError as exc:
        return HttpResponseBadRequest('; '.join(exc.messages))
    return redirect('employers:verification_review',organization_id=organization_id)


@login_required
@require_POST
def release_vacancy(request, organization_id, vacancy_id):
    require_platform_reviewer(request.user)
    get_object_or_404(EmployerVacancy,pk=vacancy_id,organization_id=organization_id)
    try:
        services.release_vacancy(actor=request.user,vacancy_id=vacancy_id,reason=request.POST.get('reason',''))
    except ValidationError as exc:
        return HttpResponseBadRequest('; '.join(exc.messages))
    return redirect('employers:verification_review',organization_id=organization_id)
