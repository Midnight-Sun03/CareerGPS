"""Organization-locked collaboration; assignments never grant additional roles."""
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.shortcuts import get_object_or_404
from django.urls import reverse
from django.utils import timezone
from core.services.notifications import notify
from .models import (OrganizationMembership, EmployerVacancy, HiringApplication, HiringAssignment,
    HiringAssignmentEvent, HiringTask, HiringTaskEvent)
from .permissions import membership_for
from .services import owned_organization, audit


def _actor(user, organization_id, capability='collaborate'):
    org = owned_organization(user, organization_id, capability)
    return membership_for(user, org.pk, capability)


def _assignee(org, member_id):
    return get_object_or_404(OrganizationMembership, pk=member_id, organization=org,
        status='active', user__is_active=True)


def _context(org, vacancy_id=None, hiring_id=None):
    vacancy = get_object_or_404(EmployerVacancy, pk=vacancy_id, organization=org) if vacancy_id else None
    hiring = get_object_or_404(HiringApplication.objects.select_related('vacancy'), pk=hiring_id,
        vacancy__organization=org) if hiring_id else None
    if hiring:
        if vacancy and hiring.vacancy_id != vacancy.pk:
            raise ValidationError('The application must belong to the selected vacancy.')
        vacancy = hiring.vacancy
    return vacancy, hiring


@transaction.atomic
def assign(*, user, organization_id, member_id, responsibility, vacancy_id=None, hiring_id=None):
    actor = _actor(user, organization_id)
    target = _assignee(actor.organization, member_id)
    vacancy, hiring = _context(actor.organization, vacancy_id, hiring_id)
    if not vacancy or responsibility not in dict(HiringAssignment.ROLES):
        raise ValidationError('Choose a vacancy and responsibility.')
    if bool(hiring) != (responsibility == 'application'):
        raise ValidationError('Application responsibility requires an application.')
    if responsibility == 'primary_recruiter' and target.role not in {'owner', 'recruiter'}:
        raise ValidationError('Primary recruiters must have the owner or recruiter role.')
    if responsibility == 'hiring_manager' and target.role not in {'owner', 'hiring_manager'}:
        raise ValidationError('Hiring managers must have the owner or hiring manager role.')
    active = HiringAssignment.objects.filter(organization=actor.organization, vacancy=vacancy,
        hiring_application=hiring, responsibility=responsibility, active=True)
    if responsibility == 'collaborator':
        active = active.filter(assigned_member=target)
    current = active.first()
    if current and current.assigned_member_id == target.pk:
        return current
    if current:
        _remove(actor, current)
    row = HiringAssignment(organization=actor.organization, vacancy=vacancy, hiring_application=hiring,
        responsibility=responsibility, assigned_member=target, assigned_by=actor)
    row.full_clean()
    row.save()
    HiringAssignmentEvent.objects.create(assignment=row, assigned_member=target, actor=actor, action='assigned')
    audit(user, actor.organization, 'responsibility_assigned', row, vacancy_id=vacancy.pk)
    return row


def _remove(actor, row):
    row.active = False
    row.save(update_fields=['active'])
    HiringAssignmentEvent.objects.create(assignment=row, assigned_member=row.assigned_member, actor=actor, action='removed')
    audit(actor.user, actor.organization, 'responsibility_removed', row, vacancy_id=row.vacancy_id)


@transaction.atomic
def remove_assignment(*, user, organization_id, assignment_id):
    actor = _actor(user, organization_id)
    row = get_object_or_404(HiringAssignment, pk=assignment_id, organization=actor.organization)
    if row.active:
        _remove(actor, row)
    return row


def visible_tasks(member):
    rows = HiringTask.objects.filter(organization=member.organization)
    if member.role == 'interviewer':
        rows = rows.filter(assigned_to=member)
    return rows


def _task_event(actor, task, action, notify_assignee=False):
    event = HiringTaskEvent.objects.create(task=task, actor=actor, assigned_to=task.assigned_to, action=action)
    audit(actor.user, actor.organization, 'task_' + action, task, vacancy_id=task.vacancy_id)
    if notify_assignee:
        # No candidate, title, free text or profile data in notification previews.
        notify(user=task.assigned_to.user, category='system', event_key=f'employer-task:{event.pk}',
            title='Hiring task assigned', message='Your employer workspace has a new task assignment.',
            target_url=reverse('employers:task_detail', args=[actor.organization_id, task.pk]))


@transaction.atomic
def create_task(*, user, organization_id, title, member_id, description='', due_date=None, vacancy_id=None, hiring_id=None):
    actor = _actor(user, organization_id)
    target = _assignee(actor.organization, member_id)
    if target.role == 'viewer':
        raise ValidationError('Viewers are read-only and cannot be task assignees.')
    vacancy, hiring = _context(actor.organization, vacancy_id, hiring_id)
    task = HiringTask(organization=actor.organization, vacancy=vacancy, hiring_application=hiring,
        title=title.strip(), description=description.strip(), assigned_to=target, created_by=actor, due_date=due_date)
    task.full_clean()
    task.save()
    _task_event(actor, task, 'assigned', True)
    return task


@transaction.atomic
def update_task(*, user, organization_id, task_id, action, member_id=None, title=None, description=None, due_date=None):
    actor = _actor(user, organization_id, 'view')
    task = get_object_or_404(visible_tasks(actor).select_for_update(), pk=task_id)
    if actor.role == 'interviewer':
        if action != 'completed' or task.assigned_to_id != actor.pk:
            raise PermissionDenied('Interviewers can complete only their own assigned tasks.')
    else:
        membership_for(user, organization_id, 'collaborate')
    if action not in {'completed', 'cancelled', 'reassigned', 'edited'}:
        raise ValidationError('Choose a valid task action.')
    if task.status != 'open':
        if action == task.status:
            return task
        raise ValidationError('Completed or cancelled tasks cannot be changed.')
    if action == 'reassigned':
        target = _assignee(actor.organization, member_id)
        if target.role == 'viewer':
            raise ValidationError('Viewers cannot be task assignees.')
        if task.assigned_to_id == target.pk:
            return task
        task.assigned_to = target
    elif action == 'edited':
        task.title = (title or '').strip()
        task.description = (description or '').strip()
        task.due_date = due_date
    else:
        task.status = action
        task.completed_at = timezone.now() if action == 'completed' else None
    task.full_clean()
    task.save()
    _task_event(actor, task, action, action == 'reassigned')
    return task
