from .abuse import limited
"""Organization-owned talent actions and candidate-owned privacy mutations."""
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.shortcuts import get_object_or_404
from django.urls import reverse
from django.utils import timezone
from applications.models import Application
from core.models import Profile
from core.models import SavedOpportunity, Opportunity
from core.services.notifications import notify
from .models import (Organization, CandidateVisibilityPreference, CandidateEmployerBlock, SavedCandidate,
    TalentPool, TalentPoolMember, TalentPoolMemberEvent, HiringApplication, EmployerVacancy, DiscoveryExperienceIndex,
    TalentInvitation)
from .permissions import discovery_membership
from .discovery import eligible_profiles, published_vacancy, refresh_experience_index
from .services import audit

VISIBILITY_FIELDS = ('discoverable_by_employers', 'allow_contact_reveal', 'allow_talent_pool_saving',
    'allow_prior_applicant_rediscovery', 'discovery_location')


def _owned_member(user, organization_id, capability='talent_manage'):
    get_object_or_404(Organization.objects.select_for_update(), pk=organization_id)
    return discovery_membership(user, organization_id, capability)


def _candidate(member, candidate_id, saving=False):
    # Candidate lock is also used by visibility/block mutations. Organization
    # mutations always acquire organization then profile locks, in that order.
    profile = get_object_or_404(Profile.objects.select_for_update(), pk=candidate_id)
    return get_object_or_404(eligible_profiles(member, saving=saving), pk=profile.pk)


def _pool_event(member, actor, action, source='', vacancy=None, application=None):
    return TalentPoolMemberEvent.objects.create(member=member, actor=actor, action=action,
        source=source, source_vacancy=vacancy, source_application=application)


def _deactivate_participation(profile, actor, organization=None):
    saved = SavedCandidate.objects.filter(candidate=profile, active=True)
    memberships = TalentPoolMember.objects.filter(candidate=profile, active=True)
    if organization:
        saved = saved.filter(organization=organization)
        memberships = memberships.filter(pool__organization=organization)
    saved.update(active=False)
    rows = list(memberships)
    memberships.update(active=False)
    TalentPoolMemberEvent.objects.bulk_create([
        TalentPoolMemberEvent(member=row, actor=actor, action='unavailable') for row in rows])


@transaction.atomic
def set_visibility(*, user, data):
    profile = get_object_or_404(Profile.objects.select_for_update(), user=user)
    preference, _ = CandidateVisibilityPreference.objects.get_or_create(profile=profile)
    for field in VISIBILITY_FIELDS:
        if field in data:
            setattr(preference, field, data[field])
    # M2 never permits unverified organizations, even if a future client sends false.
    preference.allow_verified_employers_only = True
    preference.full_clean()
    preference.save()
    if not preference.allow_talent_pool_saving or not (preference.discoverable_by_employers or preference.allow_prior_applicant_rediscovery):
        _deactivate_participation(profile, user)
    if preference.discoverable_by_employers or preference.allow_prior_applicant_rediscovery:
        DiscoveryExperienceIndex.objects.filter(profile=profile).delete()
        refresh_experience_index(Profile.objects.filter(pk=profile.pk))
    return preference


@transaction.atomic
def block_organization(*, user, organization_id, blocked=True):
    profile = get_object_or_404(Profile.objects.select_for_update(), user=user)
    organization = get_object_or_404(Organization, pk=organization_id)
    if blocked:
        CandidateEmployerBlock.objects.get_or_create(profile=profile, organization=organization)
        _deactivate_participation(profile, user, organization)
    else:
        CandidateEmployerBlock.objects.filter(profile=profile, organization=organization).delete()
    # Employer-facing audit deliberately does not disclose that a block exists.


@transaction.atomic
def invite_candidate(*, user, organization_id, candidate_id, vacancy_id):
    member = _owned_member(user, organization_id, 'talent_invite')
    vacancy = published_vacancy(member, vacancy_id)
    if vacancy is None:
        raise ValidationError('Choose a published vacancy for this invitation.')
    profile = _candidate(member, candidate_id)
    if HiringApplication.objects.filter(vacancy=vacancy, application__user_id=profile.user_id).exists():
        raise ValidationError('This candidate has already applied to this vacancy.')
    existing = TalentInvitation.objects.filter(vacancy=vacancy, candidate=profile).first()
    if existing:
        return existing
    invitation = TalentInvitation(organization=member.organization, vacancy=vacancy,
        candidate=profile, invited_by=user)
    invitation.full_clean()
    invitation.save()
    audit(user, member.organization, 'talent_invitation_sent', invitation,
        vacancy_id=vacancy.pk, candidate_id=profile.pk)
    notify(user=profile.user, category='applications', event_key=f'talent-invitation:{invitation.pk}',
        title='Invitation to consider a vacancy',
        message=f'{member.organization.name} invited you to consider {vacancy.title}.',
        target_url=reverse('employers:candidate_invitations'))
    return invitation


@transaction.atomic
def respond_to_invitation(*, user, invitation_id, action):
    invitation = get_object_or_404(TalentInvitation.objects.select_for_update().select_related(
        'vacancy__opportunity', 'candidate', 'organization', 'invited_by'), pk=invitation_id,
        candidate__user=user)
    if action not in {'accepted', 'declined'}:
        raise ValidationError('Choose accept or decline.')
    if invitation.status != 'pending':
        if invitation.status == action:
            return invitation, Application.objects.filter(user=user, opportunity=invitation.vacancy.opportunity).first() if action == 'accepted' else None
        raise ValidationError('This invitation has already been answered.')
    if action == 'accepted':
        vacancy = invitation.vacancy
        if (vacancy.status != 'published' or vacancy.trust_hold or
                not Opportunity.objects.professionally_ready().filter(pk=vacancy.opportunity_id).exists()):
            raise ValidationError('This vacancy is no longer available. You can decline the invitation.')
        application, _ = Application.objects.get_or_create(
            user=user, opportunity=vacancy.opportunity,
            defaults={'clean_revision': vacancy.opportunity.active_clean_revision})
        if not application.clean_revision_id:
            application.clean_revision = vacancy.opportunity.active_clean_revision
            application.save(update_fields=['clean_revision', 'updated_at'])
        SavedOpportunity.objects.get_or_create(user=user, opportunity=vacancy.opportunity)
    else:
        application = None
    invitation.status = action
    invitation.responded_at = timezone.now()
    invitation.save(update_fields=['status', 'responded_at'])
    audit(user, invitation.organization, f'talent_invitation_{action}', invitation,
        vacancy_id=invitation.vacancy_id, candidate_id=invitation.candidate_id)
    notify(user=invitation.invited_by, category='applications',
        event_key=f'talent-invitation-response:{invitation.pk}',
        title=f'Talent invitation {action}',
        message=f'Your invitation for {invitation.vacancy.title} was {action}.',
        target_url=reverse('employers:talent_invitations', args=[invitation.organization_id]))
    return invitation, application


@transaction.atomic
def save_candidate(*, user, organization_id, candidate_id, vacancy_id=None):
    member = _owned_member(user, organization_id)
    profile = _candidate(member, candidate_id, saving=True)
    vacancy = published_vacancy(member, vacancy_id)
    row = SavedCandidate.objects.filter(organization=member.organization, candidate=profile).first()
    if row and row.active:
        return row
    source = 'search' if profile.employer_visibility.discoverable_by_employers else 'prior_applicant'
    row, _ = SavedCandidate.objects.update_or_create(organization=member.organization, candidate=profile,
        defaults={'active': True, 'saved_by': user, 'source': source, 'related_vacancy': vacancy})
    audit(user, member.organization, 'candidate_saved', row, candidate_id=profile.pk, vacancy_id=vacancy.pk if vacancy else None, source=source)
    return row


@transaction.atomic
def unsave_candidate(*, user, organization_id, saved_id):
    member = _owned_member(user, organization_id)
    row = get_object_or_404(SavedCandidate, pk=saved_id, organization=member.organization)
    # Removal remains allowed after consent revocation; it grants no profile read.
    if row.active:
        row.active = False
        row.save(update_fields=['active', 'updated_at'])
        audit(user, member.organization, 'candidate_unsaved', row, candidate_id=row.candidate_id)


@transaction.atomic
def create_pool(*, user, organization_id, data):
    member = _owned_member(user, organization_id)
    pool = TalentPool(organization=member.organization, created_by=user, **{key: data[key] for key in ['name', 'description'] if key in data})
    pool.full_clean()
    pool.save()
    audit(user, member.organization, 'talent_pool_created', pool)
    return pool


@transaction.atomic
def archive_pool(*, user, organization_id, pool_id):
    member = _owned_member(user, organization_id)
    pool = get_object_or_404(TalentPool, organization=member.organization, pk=pool_id)
    if pool.active:
        pool.active = False
        pool.save(update_fields=['active', 'updated_at'])
        audit(user, member.organization, 'talent_pool_archived', pool)


@transaction.atomic
def add_to_pool(*, user, organization_id, pool_id, candidate_id, source='search', vacancy_id=None, hiring_id=None):
    member = _owned_member(user, organization_id)
    pool = get_object_or_404(TalentPool, pk=pool_id, organization=member.organization, active=True)
    profile = _candidate(member, candidate_id, saving=True)
    vacancy = published_vacancy(member, vacancy_id)
    application = None
    if source not in dict(TalentPoolMember.SOURCES):
        raise ValidationError('Choose a valid pool source.')
    if source == 'saved_candidate':
        get_object_or_404(SavedCandidate, candidate=profile, organization=member.organization, active=True)
    elif source in {'prior_applicant', 'vacancy_rediscovery', 'applicant_view'}:
        if not profile.employer_visibility.allow_prior_applicant_rediscovery:
            raise PermissionDenied
        related = HiringApplication.objects.filter(vacancy__organization=member.organization, application__user_id=profile.user_id)
        application = get_object_or_404(related, pk=hiring_id) if hiring_id else related.order_by('-submitted_at').first()
        if not application:
            raise PermissionDenied
        if source == 'vacancy_rediscovery' and not vacancy:
            raise ValidationError('Choose a published vacancy for rediscovery.')
    elif not profile.employer_visibility.discoverable_by_employers:
        raise PermissionDenied
    row = TalentPoolMember.objects.filter(pool=pool, candidate=profile).first()
    if row and row.active:
        return row
    row, _ = TalentPoolMember.objects.update_or_create(pool=pool, candidate=profile,
        defaults={'active': True, 'added_by': user, 'source': source, 'source_vacancy': vacancy, 'source_application': application})
    _pool_event(row, user, 'added', source, vacancy, application)
    audit(user, member.organization, 'talent_pool_candidate_added', row,
        candidate_id=profile.pk, pool_id=pool.pk, source=source, vacancy_id=vacancy.pk if vacancy else None,
        application_id=application.pk if application else None)
    return row


@transaction.atomic
def remove_from_pool(*, user, organization_id, pool_id, member_id):
    member = _owned_member(user, organization_id)
    row = get_object_or_404(TalentPoolMember, pk=member_id, pool_id=pool_id, pool__organization=member.organization)
    if row.active:
        row.active = False
        row.save(update_fields=['active', 'updated_at'])
        _pool_event(row, user, 'removed')
        audit(user, member.organization, 'talent_pool_candidate_removed', row, pool_id=pool_id, candidate_id=row.candidate_id)


@limited('contact_reveal','contact')
@transaction.atomic
def reveal_contact(*, user, organization_id, candidate_id, vacancy_id=None, hiring_id=None, reason):
    member = _owned_member(user, organization_id, 'contact')
    profile = get_object_or_404(Profile.objects.select_for_update().select_related('user'), pk=candidate_id, user__is_active=True)
    if CandidateEmployerBlock.objects.filter(profile=profile, organization=member.organization).exists():
        from django.http import Http404
        raise Http404
    if reason not in {'vacancy_review', 'application_followup'}:
        raise ValidationError('Choose a contact purpose.')
    application = None
    vacancy = None
    if hiring_id:
        application = get_object_or_404(HiringApplication.objects.select_related('vacancy'), pk=hiring_id,
            vacancy__organization=member.organization, application__user_id=profile.user_id, withdrawn_at__isnull=True)
        if application.stage in {'hired', 'rejected'} or reason != 'application_followup':
            raise PermissionDenied
        vacancy = application.vacancy
        if vacancy_id and vacancy.pk != vacancy_id:
            raise PermissionDenied
    else:
        profile = get_object_or_404(eligible_profiles(member).select_related('user'), pk=profile.pk,
            employer_visibility__allow_contact_reveal=True)
        vacancy = published_vacancy(member, vacancy_id)
        if not vacancy or reason != 'vacancy_review':
            raise ValidationError('Choose a published vacancy as the purpose for contact access.')
    audit(user, member.organization, 'candidate_contact_revealed', profile, candidate_id=profile.pk,
        vacancy_id=vacancy.pk, application_id=application.pk if application else None, reason=reason)
    # These fields exist only in this POST response, never in a discovery DTO,
    # URL, session, notification, redirect or audit payload.
    return {'email': profile.user.email, 'phone': profile.phone}
