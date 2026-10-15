from io import BytesIO
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError, ObjectDoesNotExist
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Count, OuterRef, Prefetch, Q, Subquery
from django.http import FileResponse, Http404, HttpResponseBadRequest
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_GET, require_POST, require_http_methods

from applications.models import Application
from core.models import Opportunity
from core.services.evidence_fit import compare_profile_to_revision
from . import services
from .forms import OrganizationForm, VacancyForm, VacancyIntakeForm, RequirementFormSet, MembershipForm, StageForm, NoteForm, SubmissionForm
from .models import Organization, OrganizationMembership, EmployerVacancy, VacancyIntake, HiringApplication, HiringAssignment, HiringTask, SubmittedDocument, ApplicationStageEvent
from .permissions import membership_for, visible_hiring_applications, CAPABILITIES, organization_can_use_employer_platform


def _render(request, template, member=None, **context):
    if member:
        approved = organization_can_use_employer_platform(member.organization)
        allowed = CAPABILITIES[member.role] if approved else CAPABILITIES[member.role] & {'settings','team','vacancy'}
        if member.organization.account_status != 'active': allowed = set()
        context.update(organization=member.organization, membership=member, permissions=allowed, employer_approved=approved)
    return render(request, 'employers/' + template, context)


def _error(request, exc):
    messages.error(request, '; '.join(exc.messages))


@login_required
@require_GET
def home(request):
    members = request.user.employer_memberships.select_related('organization').filter(status__in=['active', 'invited'])
    active = [member for member in members if member.status == 'active']
    if len(active) == 1 and members.count() == 1:
        return dashboard(request, active[0].organization_id)
    return _render(request, 'home.html', memberships=members)


@login_required
@require_http_methods(['GET', 'POST'])
def organization_create(request):
    form = OrganizationForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        organization = services.create_organization(user=request.user, data=form.cleaned_data)
        return redirect('employers:dashboard', organization_id=organization.pk)
    return _render(request, 'form.html', form=form, title='Create your organization', button='Create organization', intro='Give your company name and website so CareerGPS can open your hiring workspace. Verification evidence comes afterwards, before you can publish jobs.')


@login_required
@require_POST
def accept_invitation(request, membership_id):
    member = services.accept_invitation(user=request.user, membership_id=membership_id)
    return redirect('employers:dashboard', organization_id=member.organization_id)


@login_required
@require_GET
def dashboard(request, organization_id):
    member = membership_for(request.user, organization_id, 'entry')
    if not organization_can_use_employer_platform(member.organization):
        from .verification_views import status_context, private
        return private(_render(request,'verification_status.html',member,**status_context(member.organization)))
    return vacancies(request, organization_id)


@login_required
@require_GET
def company(request, organization_id):
    member = membership_for(request.user, organization_id, 'entry')
    from .verification_views import status_context, private
    return private(_render(request, 'company.html', member, **status_context(member.organization)))


@login_required
@require_http_methods(['GET', 'POST'])
def organization_settings(request, organization_id):
    member = membership_for(request.user, organization_id, 'settings')
    form = OrganizationForm(request.POST or None, instance=member.organization)
    if request.method == 'POST' and form.is_valid():
        try:
            services.update_organization(user=request.user, organization_id=organization_id, data=form.cleaned_data)
        except ValidationError as exc:
            form.add_error(None, exc)
        else:
            messages.success(request, 'Company profile saved.')
            return redirect('employers:organization_settings', organization_id=organization_id)
    return _render(request, 'form.html', member, form=form, title='Company profile', button='Save company profile')


@login_required
@require_http_methods(['GET', 'POST'])
def team(request, organization_id):
    member = membership_for(request.user, organization_id, 'team')
    form = MembershipForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        try:
            services.change_membership(user=request.user, organization_id=organization_id, **form.cleaned_data)
        except ValidationError as exc:
            form.add_error(None, exc)
        else:
            return redirect('employers:team', organization_id=organization_id)
    return _render(request, 'team.html', member, form=form, members=member.organization.memberships.select_related('user'))


@login_required
@require_GET
def vacancies(request, organization_id):
    member = membership_for(request.user, organization_id)
    from .presentation import vacancy_availability
    rows = list(member.organization.vacancies.select_related('organization', 'opportunity').annotate(
        applicant_count=Count('hiring_applications', distinct=True),
        new_count=Count('hiring_applications', filter=Q(hiring_applications__stage='received', hiring_applications__withdrawn_at__isnull=True), distinct=True),
        shortlisted_count=Count('hiring_applications', filter=Q(hiring_applications__stage='shortlisted', hiring_applications__withdrawn_at__isnull=True), distinct=True)))
    live_ids = set(Opportunity.objects.professionally_ready().filter(pk__in=[row.opportunity_id for row in rows if row.opportunity_id]).values_list('pk', flat=True))
    groups = {name: [] for name in ('active', 'closing', 'draft', 'hold', 'closed')}
    for row in rows:
        row.ui = vacancy_availability(row, discoverable_ids=live_ids)
        groups[row.ui['group']].append(row)
    from django.utils import timezone
    from .analytics import activity_rows, activity_projection
    from .collaboration_services import visible_tasks
    hiring = HiringApplication.objects.filter(vacancy__organization=member.organization, withdrawn_at__isnull=True)
    counts = dict(hiring.values('stage').annotate(count=Count('pk')).values_list('stage', 'count'))
    overdue = visible_tasks(member).filter(status='open', due_date__lt=timezone.localdate()).count()
    metrics = [('Active vacancies', len(groups['active']) + len(groups['closing'])),
        ('Drafts', len(groups['draft'])), ('Total current applicants', hiring.count()),
        ('Screening', counts.get('screening', 0)), ('Shortlisted', counts.get('shortlisted', 0)),
        ('Interviews', counts.get('interview', 0)), ('Offers', counts.get('offer', 0)),
        ('Hires', counts.get('hired', 0)), ('Overdue tasks', overdue)]
    awaiting_review = sum(row.new_count for row in rows)
    attention = []
    if awaiting_review:
        attention.append('Review %d new applicant%s' % (awaiting_review, '' if awaiting_review == 1 else 's'))
    if overdue:
        attention.append('%d overdue hiring task%s' % (overdue, '' if overdue == 1 else 's'))
    if groups['draft']:
        attention.append('Finish draft vacancy (%d)' % len(groups['draft']))
    response = _render(request, 'vacancies.html', member, groups=groups, job_count=len(rows),
        metrics=metrics, overdue_tasks=overdue, attention=attention,
        activities=activity_projection(activity_rows(member)[:5]))
    response['Cache-Control'] = 'private, no-store'
    return response


@login_required
@require_GET
def job_detail(request, organization_id, vacancy_id):
    member = membership_for(request.user, organization_id)
    vacancy = get_object_or_404(EmployerVacancy.objects.select_related('organization', 'opportunity'),
        pk=vacancy_id, organization=member.organization)
    from .presentation import vacancy_availability
    from .analytics import vacancy_time
    return _render(request, 'job_detail.html', member, vacancy=vacancy,
        availability=vacancy_availability(vacancy),
        applicant_count=vacancy.hiring_applications.count(),
        assignments=vacancy.assignments.filter(active=True, **({'assigned_member': member} if member.role == 'interviewer' else {})).select_related('assigned_member__user'),
        tasks=vacancy.hiring_tasks.filter(status='open', **({'assigned_to': member} if member.role == 'interviewer' else {})).select_related('assigned_to__user')[:8],
        timing=vacancy_time(vacancy))


@login_required
@require_http_methods(['GET', 'POST'])
def vacancy_edit(request, organization_id, vacancy_id=None):
    member = membership_for(request.user, organization_id, 'vacancy')
    vacancy = get_object_or_404(EmployerVacancy, pk=vacancy_id, organization=member.organization) if vacancy_id else None
    form = VacancyForm(request.POST or None, instance=vacancy)
    old_requirements = {row.pk: row for row in vacancy.requirements.all()} if vacancy else {}
    initial = [{**{key: getattr(row, key) for key in services.REQUIREMENT_FIELDS},
        'source_requirement_id': row.pk} for row in old_requirements.values()]
    requirements = RequirementFormSet(request.POST or None, initial=initial, prefix='requirements')
    if request.method == 'POST' and form.is_valid() and requirements.is_valid():
        rows = []
        for row in requirements:
            data = row.cleaned_data
            if not data or data.get('DELETE') or not data.get('raw_value'):
                continue
            original = old_requirements.get(data.get('source_requirement_id'))
            if original:
                data['source_excerpt'] = original.source_excerpt
                unchanged = all(data.get(key) == getattr(original, key) for key in services.REQUIREMENT_FIELDS)
                data['origin'] = original.origin if unchanged else 'employer_corrected' if original.source_excerpt else 'employer_authored'
            rows.append(data)
        try:
            vacancy = services.save_vacancy(user=request.user, organization_id=organization_id,
                vacancy_id=vacancy_id, data=form.cleaned_data, requirements=rows)
        except ValidationError as exc:
            form.add_error(None, exc)
        else:
            messages.success(request, 'Vacancy saved. Published changes are synchronized with the candidate listing.')
            if organization_can_use_employer_platform(member.organization):
                return redirect('employers:vacancies', organization_id=organization_id)
            return redirect('employers:vacancy_edit', organization_id=organization_id, vacancy_id=vacancy.pk)
    return _render(request, 'vacancy_form.html', member, form=form, requirements=requirements, vacancy=vacancy)


@login_required
@require_http_methods(['GET', 'POST'])
def vacancy_intake(request, organization_id):
    member = membership_for(request.user, organization_id, 'vacancy')
    # Existing manual clients remain supported; manual entry is now secondary UI.
    if request.method == 'POST' and 'title' in request.POST:
        return vacancy_edit(request, organization_id)
    form = VacancyIntakeForm(request.POST or None, request.FILES or None)
    if request.method == 'POST' and form.is_valid():
        import hashlib
        from .vacancy_intake import interpret_vacancy, read_vacancy_document, validate_source_text
        try:
            upload = form.cleaned_data.get('vacancy_document')
            text, filename = read_vacancy_document(upload) if upload else (
                validate_source_text(form.cleaned_data['vacancy_text']), '')
            facts, proposed, method, notice = interpret_vacancy(text)
        except ValidationError as exc:
            form.add_error(None, exc)
        else:
            intake = VacancyIntake.objects.create(organization=member.organization, created_by=request.user,
                source_text=text, source_filename=filename, source_sha256=hashlib.sha256(text.encode()).hexdigest(),
                proposed_facts=facts, proposed_requirements=proposed, extraction_method=method,
                extraction_notice=notice)
            services.audit(request.user, member.organization, 'vacancy_intake_created', intake,
                source='upload' if filename else 'paste')
            return redirect('employers:vacancy_review', organization_id=organization_id, intake_id=intake.pk)
    return _render(request, 'vacancy_intake.html', member, form=form)


@login_required
@require_http_methods(['GET', 'POST'])
def vacancy_review(request, organization_id, intake_id):
    member = membership_for(request.user, organization_id, 'vacancy')
    intake = get_object_or_404(VacancyIntake, pk=intake_id, organization=member.organization)
    if intake.vacancy_id:
        return redirect('employers:vacancy_edit', organization_id=organization_id, vacancy_id=intake.vacancy_id)
    form = VacancyForm(request.POST or None, initial=intake.proposed_facts)
    initial = [{**row, 'proposal_index': index,
        'requirement_level': row['requirement_level'] if row['requirement_level'] in {'required', 'preferred'} else ''}
        for index, row in enumerate(intake.proposed_requirements)]
    requirements = RequirementFormSet(request.POST or None, initial=initial, prefix='requirements')
    if request.method == 'POST' and form.is_valid() and requirements.is_valid():
        if not request.POST.get('confirm_facts') or not request.POST.get('confirm_requirements'):
            form.add_error(None, 'Review and explicitly confirm the final vacancy facts and requirements.')
        else:
            rows = []
            for row in requirements:
                data = row.cleaned_data
                if not data or data.get('DELETE') or not data.get('raw_value'):
                    continue
                index = data.get('proposal_index')
                proposal = intake.proposed_requirements[index] if index is not None and index < len(intake.proposed_requirements) else None
                if proposal:
                    data['source_excerpt'] = proposal['source_excerpt']
                    data['origin'] = ('interpreted_confirmed' if all(data.get(key) == proposal.get(key)
                        for key in services.REQUIREMENT_FIELDS) else 'employer_corrected')
                rows.append(data)
            try:
                with transaction.atomic():
                    vacancy = services.save_vacancy(user=request.user, organization_id=organization_id,
                        data=form.cleaned_data, requirements=rows)
                    from django.utils import timezone
                    intake.vacancy = vacancy
                    intake.confirmed_by = request.user
                    intake.confirmed_at = timezone.now()
                    intake.save(update_fields=['vacancy', 'confirmed_by', 'confirmed_at', 'updated_at'])
                    services.audit(request.user, member.organization, 'vacancy_interpretation_confirmed', intake,
                        vacancy_id=vacancy.pk, requirement_count=len(rows))
            except ValidationError as exc:
                form.add_error(None, exc)
            else:
                messages.success(request, 'Job draft confirmed. Publish after company verification and final review.')
                return redirect('employers:job_detail', organization_id=organization_id, vacancy_id=vacancy.pk)
    return _render(request, 'vacancy_review.html', member, intake=intake, form=form, requirements=requirements)


@login_required
@require_POST
def vacancy_publish(request, organization_id, vacancy_id):
    try:
        services.publish_vacancy(user=request.user, organization_id=organization_id, vacancy_id=vacancy_id)
    except ValidationError as exc:
        _error(request, exc)
    return redirect('employers:vacancies', organization_id=organization_id)


@login_required
@require_POST
def vacancy_close(request, organization_id, vacancy_id):
    services.close_vacancy(user=request.user, organization_id=organization_id, vacancy_id=vacancy_id)
    return redirect('employers:vacancies', organization_id=organization_id)


def _match(hiring):
    if hiring.withdrawn_at:
        return None
    try:
        profile = hiring.application.user.profile
        revision = (hiring.application.clean_revision
                    or hiring.application.opportunity.active_clean_revision)
        return compare_profile_to_revision(profile, revision) if revision else None
    except ObjectDoesNotExist:
        return None


@login_required
@require_GET
def applicants(request, organization_id, vacancy_id=None):
    member = membership_for(request.user, organization_id)
    rows = visible_hiring_applications(member).select_related('application__user', 'application__opportunity', 'vacancy')
    latest = ApplicationStageEvent.objects.filter(hiring_application_id=OuterRef('pk')).order_by('-created_at', '-pk')
    rows = rows.annotate(last_stage_at=Subquery(latest.values('created_at')[:1]))
    if 'documents' in CAPABILITIES[member.role]:
        rows = rows.prefetch_related(Prefetch('submitted_documents', queryset=SubmittedDocument.objects.defer('pdf_bytes', 'docx_bytes'), to_attr='visible_documents'))
    vacancy = None
    if vacancy_id:
        vacancy = get_object_or_404(EmployerVacancy, organization=member.organization, pk=vacancy_id)
        rows = rows.filter(vacancy=vacancy)
    if request.GET.get('vacancy'):
        rows = rows.filter(vacancy_id=request.GET.get('vacancy')) if request.GET['vacancy'].isdigit() else rows.none()
    if request.GET.get('stage') in dict(HiringApplication.STAGES):
        rows = rows.filter(stage=request.GET['stage'])
    if request.GET.get('q'):
        query = request.GET['q'].strip()[:100]
        rows = rows.filter(Q(application__user__username__icontains=query) |
            Q(application__user__first_name__icontains=query) |
            Q(application__user__last_name__icontains=query) |
            Q(vacancy__title__icontains=query))
    page = Paginator(rows.order_by('-submitted_at', '-pk'), 25).get_page(request.GET.get('page'))
    for hiring in page:
        hiring.match_result = _match(hiring)
        if hiring.match_result:
            criteria = hiring.match_result.criteria
            hiring.evidence_highlights = [row.criterion for row in criteria if row.status.value == 'FOUND'][:2]
            hiring.evidence_caution = next((row.criterion for row in criteria
                if row.importance.value == 'required' and row.status.value in {'UNKNOWN', 'NOT_FOUND_ON_PROFILE'}), '')
        else:
            hiring.evidence_highlights, hiring.evidence_caution = [], ''
    query = request.GET.copy()
    query.pop('page', None)
    return _render(request, 'applicants.html', member, page=page, vacancy=vacancy,
        vacancies=member.organization.vacancies.all(), stages=HiringApplication.STAGES,
        page_query=query.urlencode())


@login_required
@require_GET
def applicant(request, organization_id, hiring_id):
    member = membership_for(request.user, organization_id)
    hiring = get_object_or_404(visible_hiring_applications(member).select_related('application__user', 'application__opportunity', 'vacancy'),
        pk=hiring_id, vacancy__organization=member.organization)
    services.audit(request.user, member.organization, 'applicant_profile_viewed', hiring,
        vacancy_id=hiring.vacancy_id)
    allowed = CAPABILITIES[member.role]
    try:
        profile_id = hiring.application.user.profile.pk
    except ObjectDoesNotExist:
        profile_id = None
    from .analytics import application_stage_age
    from .discovery import practical_facts
    practical = practical_facts(None, hiring.vacancy)
    return _render(request, 'applicant.html', member, stage_age=application_stage_age(hiring), hiring=hiring, match_result=_match(hiring),
        practical_facts=practical,
        stage_form=StageForm(initial={'stage': hiring.stage}), note_form=NoteForm(), candidate_profile_id=profile_id,
        notes=hiring.private_notes.select_related('author') if 'notes' in allowed else [],
        documents=hiring.submitted_documents.defer('pdf_bytes', 'docx_bytes') if 'documents' in allowed else [],
        events=hiring.stage_events.select_related('changed_by'))


@login_required
@require_POST
def stage_change(request, organization_id, hiring_id):
    membership_for(request.user, organization_id, 'pipeline')
    form = StageForm(request.POST)
    if not form.is_valid():
        return HttpResponseBadRequest('Choose a valid hiring stage.')
    try:
        services.move_stage(user=request.user, organization_id=organization_id, hiring_id=hiring_id, **form.cleaned_data)
    except ValidationError as exc:
        _error(request, exc)
    return redirect('employers:applicant', organization_id=organization_id, hiring_id=hiring_id)


@login_required
@require_POST
def note_add(request, organization_id, hiring_id):
    member = membership_for(request.user, organization_id, 'notes')
    get_object_or_404(visible_hiring_applications(member), pk=hiring_id)
    form = NoteForm(request.POST)
    if not form.is_valid():
        return HttpResponseBadRequest('Enter a private note of at most 10,000 characters.')
    services.add_note(user=request.user, organization_id=organization_id, hiring_id=hiring_id, **form.cleaned_data)
    return redirect('employers:applicant', organization_id=organization_id, hiring_id=hiring_id)


def _document_response(document, file_format):
    if file_format not in {'pdf', 'docx'}:
        raise Http404
    data = getattr(document, file_format + '_bytes')
    if not data:
        raise Http404
    response = FileResponse(BytesIO(bytes(data)), as_attachment=True,
        filename=f'submitted-{document.pk}-{document.document_type}.{file_format}',
        content_type='application/pdf' if file_format == 'pdf' else 'application/vnd.openxmlformats-officedocument.wordprocessingml.document')
    response['Cache-Control'] = 'private, no-store'
    response['X-Content-Type-Options'] = 'nosniff'
    return response


@login_required
@require_GET
def document_download(request, organization_id, document_id, file_format):
    member = membership_for(request.user, organization_id, 'documents')
    document = get_object_or_404(SubmittedDocument.objects.filter(hiring_application__in=visible_hiring_applications(member)),
        pk=document_id, hiring_application__vacancy__organization=member.organization)
    if file_format not in {'pdf', 'docx'} or not getattr(document, file_format + '_bytes'):
        raise Http404
    services.audit(request.user, member.organization, 'submitted_document_downloaded', document,
        hiring_application_id=document.hiring_application_id, document_type=document.document_type,
        file_format=file_format)
    return _document_response(document, file_format)


@login_required
@require_POST
def candidate_submit(request, application_id):
    application = get_object_or_404(Application, pk=application_id, user=request.user)
    form = SubmissionForm(request.POST, application=application)
    if form.is_valid():
        try:
            services.submit_application(user=request.user, application_id=application_id,
                document_ids=[doc.pk for doc in (form.cleaned_data['cv'], form.cleaned_data.get('cover_letter')) if doc],
                consent=form.cleaned_data['consent'])
        except ValidationError as exc:
            _error(request, exc)
        else:
            messages.success(request, 'Your application and selected document versions were received by the employer.')
    else:
        messages.error(request, 'Select your CV and confirm sharing before submitting.')
    return redirect('applications:workspace', application_id=application_id)


@login_required
@require_POST
def candidate_withdraw(request, application_id):
    try:
        services.withdraw_application(user=request.user, application_id=application_id)
    except ValidationError as exc:
        _error(request, exc)
    else:
        messages.success(request, 'Your application has been withdrawn.')
    return redirect('applications:workspace', application_id=application_id)


@login_required
@require_GET
def candidate_document(request, document_id, file_format):
    document = get_object_or_404(SubmittedDocument, pk=document_id, hiring_application__application__user=request.user)
    return _document_response(document, file_format)
