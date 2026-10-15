from django.core.exceptions import PermissionDenied
from django.http import Http404
from django.shortcuts import get_object_or_404
from .models import OrganizationMembership

CAPABILITIES = {
    'owner': {'view', 'vacancy', 'pipeline', 'notes', 'documents', 'team', 'settings', 'talent', 'talent_manage', 'talent_invite', 'contact', 'analytics', 'collaborate'},
    'recruiter': {'view', 'vacancy', 'pipeline', 'notes', 'documents', 'talent', 'talent_manage', 'talent_invite', 'contact', 'analytics', 'collaborate'},
    'hiring_manager': {'view', 'pipeline', 'notes', 'documents', 'talent', 'talent_manage', 'talent_invite', 'analytics', 'collaborate'},
    'interviewer': {'view', 'notes', 'documents'},
    'viewer': {'view', 'analytics'},
}


def organization_can_use_employer_platform(organization):
    return organization.verification_status == 'approved' and organization.account_status == 'active'


def approved_organization_filter(prefix=''):
    from django.db.models import Q
    return Q(**{prefix+'verification_status':'approved', prefix+'account_status':'active'})


def membership_for(user, organization_id, capability='view'):
    if not user.is_authenticated or not user.is_active:
        raise PermissionDenied
    membership = get_object_or_404(OrganizationMembership.objects.select_related('organization','user'),
        user=user, organization_id=organization_id, status='active')
    if capability == 'entry':
        return membership
    if capability == 'verification':
        if membership.role != 'owner':
            raise PermissionDenied('Only organization owners can submit verification evidence.')
        return membership
    if capability not in CAPABILITIES.get(membership.role, set()):
        raise PermissionDenied('Your organization role does not permit this action.')
    if membership.organization.account_status != 'active':
        raise Http404('Organization access is suspended.')
    if capability not in {'settings','team','vacancy'} and not organization_can_use_employer_platform(membership.organization):
        raise PermissionDenied('CareerGPS approval and an active employer account are required.')
    return membership


def discovery_membership(user, organization_id, capability='talent'):
    return membership_for(user, organization_id, capability)


def visible_hiring_applications(member, rows=None):
    """An interviewer sees only application work explicitly assigned to them."""
    from django.db.models import Q
    from .models import HiringApplication, HiringAssignment, HiringTask
    rows = rows if rows is not None else HiringApplication.objects.filter(vacancy__organization=member.organization)
    if member.role != 'interviewer':
        return rows
    assignments = HiringAssignment.objects.filter(organization=member.organization,
        assigned_member=member, active=True)
    application_ids = assignments.filter(hiring_application__isnull=False).values('hiring_application_id')
    vacancy_ids = assignments.filter(hiring_application__isnull=True).values('vacancy_id')
    task_ids = HiringTask.objects.filter(organization=member.organization, assigned_to=member,
        hiring_application__isnull=False).values('hiring_application_id')
    return rows.filter(Q(pk__in=application_ids) | Q(vacancy_id__in=vacancy_ids) | Q(pk__in=task_ids))


def require_platform_reviewer(user):
    if not user.is_authenticated or not user.is_active or not user.is_staff or not user.has_perm('employers.review_employer'):
        raise PermissionDenied('Platform employer-review permission is required.')
