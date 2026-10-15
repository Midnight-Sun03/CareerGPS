from .abuse import discovery_request
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.http import HttpResponseBadRequest
from django.shortcuts import get_object_or_404, redirect
from django.views.decorators.http import require_GET, require_POST, require_http_methods
from .permissions import membership_for, discovery_membership, CAPABILITIES
from .models import EmployerVacancy, HiringAssignment
from .analytics import funnel, vacancy_time, activity_rows, activity_projection
from .intelligence import supply, requirement_schema, market_context
from .discovery import published_vacancy
from . import collaboration_services as services
from .intelligence_forms import AssignmentForm, TaskForm, TaskEditForm, ReassignForm
from .views import _render


def private_render(request, template, member, **context):
    response = _render(request, template, member, **context)
    response['Cache-Control'] = 'private, no-store'
    return response


@login_required
@require_GET
def analytics(request, organization_id):
    member = membership_for(request.user, organization_id, 'analytics')
    vacancy = None
    if request.GET.get('vacancy'):
        try:
            vacancy_id = int(request.GET['vacancy'])
        except ValueError:
            return HttpResponseBadRequest('Choose a vacancy.')
        vacancy = get_object_or_404(EmployerVacancy, pk=vacancy_id, organization=member.organization)
    return private_render(request, 'analytics.html', member, data=funnel(member, vacancy), vacancy=vacancy,
        vacancies=member.organization.vacancies.all(), timing=vacancy_time(vacancy) if vacancy else None)


@login_required
@require_GET
@discovery_request
def intelligence(request, organization_id, vacancy_id):
    member = discovery_membership(request.user, organization_id)
    vacancy = published_vacancy(member, vacancy_id)
    try:
        # A single pass serves requirements, combinations and all supply sections.
        data = supply(member, vacancy, request.GET.getlist('criteria'))
    except ValidationError as exc:
        return HttpResponseBadRequest('; '.join(exc.messages))
    return private_render(request, 'intelligence.html', member, vacancy=vacancy, supply=data,
        demand=market_context(vacancy), data=funnel(member, vacancy), timing=vacancy_time(vacancy))


@login_required
@require_http_methods(['GET','POST'])
def assignments(request, organization_id):
    member = membership_for(request.user, organization_id, 'view' if request.method == 'GET' else 'collaborate')
    form = AssignmentForm(member.organization, request.POST or None,
        initial={key: request.GET[key] for key in ['vacancy','hiring'] if request.GET.get(key)})
    if request.method == 'POST' and form.is_valid():
        try:
            services.assign(user=request.user, organization_id=organization_id, **form.service_data())
        except ValidationError as exc:
            form.add_error(None, exc)
        else:
            return redirect('employers:assignments', organization_id=organization_id)
    rows = HiringAssignment.objects.filter(organization=member.organization).select_related(
        'vacancy', 'assigned_member__user', 'assigned_by__user', 'hiring_application').prefetch_related('history__actor__user')
    if member.role == 'interviewer':
        rows = rows.filter(assigned_member=member)
    page = Paginator(rows.order_by('-assigned_at','-pk'), 20).get_page(request.GET.get('page'))
    return private_render(request, 'assignments.html', member, form=form, page=page)


@login_required
@require_POST
def assignment_remove(request, organization_id, assignment_id):
    services.remove_assignment(user=request.user, organization_id=organization_id, assignment_id=assignment_id)
    return redirect('employers:assignments', organization_id=organization_id)


@login_required
@require_http_methods(['GET','POST'])
def tasks(request, organization_id):
    member = membership_for(request.user, organization_id, 'view' if request.method == 'GET' else 'collaborate')
    form = TaskForm(member.organization, request.POST or None,
        initial={key: request.GET[key] for key in ['vacancy','hiring'] if request.GET.get(key)})
    if request.method == 'POST' and form.is_valid():
        try:
            task = services.create_task(user=request.user, organization_id=organization_id, **form.service_data())
        except ValidationError as exc:
            form.add_error(None, exc)
        else:
            return redirect('employers:task_detail', organization_id=organization_id, task_id=task.pk)
    rows = services.visible_tasks(member).select_related('vacancy', 'assigned_to__user')
    page = Paginator(rows, 20).get_page(request.GET.get('page'))
    return private_render(request, 'tasks.html', member, form=form, page=page)


@login_required
@require_GET
def task_detail(request, organization_id, task_id):
    member = membership_for(request.user, organization_id)
    task = get_object_or_404(services.visible_tasks(member).select_related('vacancy','assigned_to__user'), pk=task_id)
    return private_render(request, 'task.html', member, task=task, edit_form=TaskEditForm(instance=task),
        reassign_form=ReassignForm(member.organization), history=task.history.select_related('actor__user','assigned_to__user'),
        can_complete='collaborate' in CAPABILITIES[member.role] or member.role == 'interviewer' and task.assigned_to_id == member.pk)


@login_required
@require_POST
def task_update(request, organization_id, task_id):
    member = membership_for(request.user, organization_id)
    data = {'action': request.POST.get('action')}
    if data['action'] == 'reassigned':
        form = ReassignForm(member.organization, request.POST)
        if not form.is_valid():
            return HttpResponseBadRequest('Choose an active task assignee in this organization.')
        data['member_id'] = form.cleaned_data['member'].pk
    elif data['action'] == 'edited':
        task = get_object_or_404(services.visible_tasks(member), pk=task_id)
        form = TaskEditForm(request.POST, instance=task)
        if not form.is_valid():
            return HttpResponseBadRequest('Enter a valid title, description and due date.')
        data.update(form.cleaned_data)
    try:
        services.update_task(user=request.user, organization_id=organization_id, task_id=task_id, **data)
    except ValidationError as exc:
        return HttpResponseBadRequest('; '.join(exc.messages))
    return redirect('employers:task_detail', organization_id=organization_id, task_id=task_id)


@login_required
@require_GET
def activity(request, organization_id):
    member = membership_for(request.user, organization_id)
    page = Paginator(activity_rows(member), 30).get_page(request.GET.get('page'))
    page.object_list = activity_projection(page.object_list)
    return private_render(request, 'activity.html', member, page=page)
