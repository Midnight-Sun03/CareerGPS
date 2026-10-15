from .abuse import discovery_request
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.db.models import Count, Q
from django.http import HttpResponseBadRequest
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_GET, require_POST, require_http_methods
from core.models import Profile, Opportunity
from applications.models import Application
from .models import (Organization, CandidateVisibilityPreference, CandidateEmployerBlock, SavedCandidate,
    TalentPool, TalentPoolMember, HiringApplication, TalentInvitation)
from .permissions import discovery_membership
from .discovery import (eligible_profiles, search_profiles, candidate_page, profile_batch, project_profile,
    match_batch, published_vacancy, practical_facts)
from .talent_forms import VisibilityForm, TalentSearchForm, PoolForm, CandidateActionForm, PoolAddForm, ContactForm
from . import talent_services as services
from .services import audit
from .views import _render


def _private(response):
    response['Cache-Control'] = 'private, no-store'
    return response


@login_required
@require_http_methods(['GET', 'POST'])
def visibility(request):
    profile = get_object_or_404(Profile, user=request.user)
    preference = CandidateVisibilityPreference.objects.filter(profile=profile).first()
    form = VisibilityForm(request.POST if request.method == 'POST' else None, instance=preference)
    if request.method == 'POST' and form.is_valid():
        services.set_visibility(user=request.user, data=form.cleaned_data)
        messages.success(request, 'Employer visibility saved.')
        return redirect('employers:visibility')
    query = request.GET.get('organization', '').strip()[:100]
    organizations = Organization.objects.filter(name__icontains=query).order_by('name')[:20] if query else []
    return _private(render(request, 'employers/visibility.html', {'form': form, 'organizations': organizations,
        'organization_query': query, 'blocks': CandidateEmployerBlock.objects.filter(profile=profile).select_related('organization')}))


@login_required
@require_POST
def block(request, organization_id):
    services.block_organization(user=request.user, organization_id=organization_id)
    messages.success(request, 'Organization hidden from your discovery profile.')
    return redirect('employers:visibility')


@login_required
@require_POST
def unblock(request, organization_id):
    services.block_organization(user=request.user, organization_id=organization_id, blocked=False)
    return redirect('employers:visibility')


@login_required
@require_GET
@discovery_request
def search(request, organization_id):
    member = discovery_membership(request.user, organization_id)
    form = TalentSearchForm(request.GET, member=member)
    page, vacancy = None, None
    if form.is_valid():
        vacancy = form.cleaned_data['vacancy']
        rows = search_profiles(member, form.cleaned_data)
        page = candidate_page(rows, vacancy, request.GET.get('page'))
        if form.cleaned_data.get('scope') == 'prior' and vacancy:
            # One access event for this deliberate rediscovery action, not each result.
            audit(request.user, member.organization, 'vacancy_rediscovery', vacancy)
    query = request.GET.copy()
    query.pop('page', None)
    return _private(_render(request, 'talent_search.html', member, form=form, page=page, vacancy=vacancy,
        page_query=query.urlencode(), source='vacancy_rediscovery' if vacancy and request.GET.get('scope') == 'prior' else
            'prior_applicant' if request.GET.get('scope') == 'prior' else 'search'))


@login_required
@require_GET
@discovery_request
def candidate(request, organization_id, candidate_id):
    member = discovery_membership(request.user, organization_id)
    context_form = CandidateActionForm({'vacancy_id': request.GET.get('vacancy')})
    if not context_form.is_valid():
        return HttpResponseBadRequest('Invalid vacancy.')
    vacancy = published_vacancy(member, context_form.cleaned_data.get('vacancy_id'))
    profile = get_object_or_404(profile_batch(eligible_profiles(member)), pk=candidate_id)
    card = project_profile(*match_batch([profile], vacancy)[0])
    audit(request.user, member.organization, 'candidate_discovery_viewed', profile, candidate_id=profile.pk,
        vacancy_id=vacancy.pk if vacancy else None)
    source = 'search' if profile.employer_visibility.discoverable_by_employers else 'vacancy_rediscovery' if vacancy else 'prior_applicant'
    requested_source = request.GET.get('source')
    if requested_source == 'saved_candidate' and SavedCandidate.objects.filter(candidate=profile, organization=member.organization, active=True).exists():
        source = 'saved_candidate'
    elif requested_source in {'prior_applicant', 'vacancy_rediscovery', 'applicant_view'} and profile.employer_visibility.allow_prior_applicant_rediscovery:
        if HiringApplication.objects.filter(application__user_id=profile.user_id, vacancy__organization=member.organization).exists():
            source = requested_source if requested_source != 'vacancy_rediscovery' or vacancy else 'prior_applicant'
    invitation = TalentInvitation.objects.filter(vacancy=vacancy, candidate=profile).first() if vacancy else None
    return _private(_render(request, 'talent_candidate.html', member, card=card, vacancy=vacancy, source=source,
        invitation=invitation, practical_facts=practical_facts(profile, vacancy, shared_discovery=True) if vacancy else (),
        pools=member.organization.talent_pools.filter(active=True) if card.can_save else [],
        contact_allowed=profile.employer_visibility.allow_contact_reveal and vacancy is not None))


@login_required
@require_POST
def invite(request, organization_id, candidate_id):
    form = CandidateActionForm(request.POST)
    if not form.is_valid() or not form.cleaned_data.get('vacancy_id'):
        return HttpResponseBadRequest('Choose a published vacancy.')
    invitation = services.invite_candidate(user=request.user, organization_id=organization_id,
        candidate_id=candidate_id, vacancy_id=form.cleaned_data['vacancy_id'])
    messages.success(request, f'Invitation {invitation.get_status_display().lower()}.')
    return redirect(f"{reverse('employers:talent_candidate', args=[organization_id, candidate_id])}?vacancy={invitation.vacancy_id}")


@login_required
@require_GET
def sent_invitations(request, organization_id):
    member = discovery_membership(request.user, organization_id)
    page = Paginator(TalentInvitation.objects.filter(organization=member.organization).select_related(
        'candidate__user', 'vacancy'), 25).get_page(request.GET.get('page'))
    invitations = list(page)
    visible = set(eligible_profiles(member).filter(
        pk__in=[row.candidate_id for row in invitations if row.candidate_id]).values_list('pk', flat=True))
    items = [{'invitation': row, 'candidate_name':
        (row.candidate.user.get_full_name().strip() or f'Candidate {row.candidate_id}')
        if row.candidate_id in visible else 'Candidate unavailable',
        'candidate_id': row.candidate_id if row.candidate_id in visible else None}
        for row in invitations]
    return _private(_render(request, 'talent_invitations.html', member, items=items, page=page))


@login_required
@require_GET
def candidate_invitations(request):
    invitations = TalentInvitation.objects.filter(candidate__user=request.user).select_related(
        'organization', 'vacancy').order_by('-created_at', '-pk')
    applications = dict(Application.objects.filter(user=request.user,
        opportunity_id__in=invitations.values('vacancy__opportunity_id')).values_list('opportunity_id', 'pk'))
    available = set(Opportunity.objects.professionally_ready().filter(
        pk__in=invitations.values('vacancy__opportunity_id')).values_list('pk', flat=True))
    items = [{'invitation': row, 'application_id': applications.get(row.vacancy.opportunity_id),
        'available': row.vacancy.status == 'published' and not row.vacancy.trust_hold and
            row.vacancy.opportunity_id in available} for row in invitations]
    return _private(render(request, 'employers/candidate_invitations.html', {'items': items}))


@login_required
@require_POST
def candidate_invitation_response(request, invitation_id):
    try:
        invitation, application = services.respond_to_invitation(user=request.user,
            invitation_id=invitation_id, action=request.POST.get('action'))
    except ValidationError as exc:
        messages.error(request, '; '.join(exc.messages))
        return redirect('employers:candidate_invitations')
    if invitation.status == 'accepted' and application:
        return redirect('applications:workspace', application_id=application.pk)
    return redirect('employers:candidate_invitations')


@login_required
@require_GET
@discovery_request
def saved(request, organization_id):
    member = discovery_membership(request.user, organization_id)
    rows = eligible_profiles(member, saving=True).filter(employer_saves__organization=member.organization, employer_saves__active=True).order_by('pk')
    page = candidate_page(rows, None, request.GET.get('page'))
    identities = dict(SavedCandidate.objects.filter(organization=member.organization, candidate_id__in=[card.id for card in page], active=True).values_list('candidate_id', 'pk'))
    items = [{'card': card, 'saved_id': identities[card.id]} for card in page]
    return _private(_render(request, 'talent_saved.html', member, page=page, items=items, source='saved_candidate'))


@login_required
@require_POST
def save(request, organization_id, candidate_id):
    form = CandidateActionForm(request.POST)
    if not form.is_valid():
        return HttpResponseBadRequest('Invalid vacancy.')
    services.save_candidate(user=request.user, organization_id=organization_id, candidate_id=candidate_id,
        vacancy_id=form.cleaned_data.get('vacancy_id'))
    return redirect('employers:talent_saved', organization_id=organization_id)


@login_required
@require_POST
def unsave(request, organization_id, saved_id):
    services.unsave_candidate(user=request.user, organization_id=organization_id, saved_id=saved_id)
    return redirect('employers:talent_saved', organization_id=organization_id)


@login_required
@require_http_methods(['GET', 'POST'])
@discovery_request
def pools(request, organization_id):
    member = discovery_membership(request.user, organization_id, 'talent_manage' if request.method == 'POST' else 'talent')
    form = PoolForm(request.POST if request.method == 'POST' else None)
    if request.method == 'POST' and form.is_valid():
        pool = services.create_pool(user=request.user, organization_id=organization_id, data=form.cleaned_data)
        return redirect('employers:talent_pool', organization_id=organization_id, pool_id=pool.pk)
    visible = eligible_profiles(member, saving=True).values('pk')
    rows = member.organization.talent_pools.annotate(visible_count=Count('members', filter=Q(members__active=True, members__candidate__in=visible)))
    return _private(_render(request, 'talent_pools.html', member, pools=rows, form=form))


@login_required
@require_GET
@discovery_request
def pool(request, organization_id, pool_id):
    member = discovery_membership(request.user, organization_id)
    pool = get_object_or_404(TalentPool, organization=member.organization, pk=pool_id)
    rows = eligible_profiles(member, saving=True).filter(talent_memberships__pool=pool,
        talent_memberships__active=True).order_by('pk') if pool.active else Profile.objects.none()
    page = candidate_page(rows, None, request.GET.get('page'))
    members = {row.candidate_id: row for row in pool.members.filter(candidate_id__in=[card.id for card in page]).prefetch_related('history')}
    items = [{'card': card, 'member_id': members[card.id].pk, 'source': members[card.id].get_source_display(),
        'added_at': members[card.id].added_at, 'history': [{'action': event.get_action_display(), 'at': event.created_at}
            for event in members[card.id].history.all()]} for card in page]
    return _private(_render(request, 'talent_pool.html', member, pool=pool, page=page, items=items))


@login_required
@require_POST
def pool_add(request, organization_id, candidate_id):
    form = PoolAddForm(request.POST)
    if not form.is_valid():
        return HttpResponseBadRequest('Choose a pool and valid source.')
    try:
        row = services.add_to_pool(user=request.user, organization_id=organization_id, candidate_id=candidate_id, **form.cleaned_data)
    except ValidationError as exc:
        return HttpResponseBadRequest('; '.join(exc.messages))
    return redirect('employers:talent_pool', organization_id=organization_id, pool_id=row.pool_id)


@login_required
@require_POST
def pool_remove(request, organization_id, pool_id, member_id):
    services.remove_from_pool(user=request.user, organization_id=organization_id, pool_id=pool_id, member_id=member_id)
    return redirect('employers:talent_pool', organization_id=organization_id, pool_id=pool_id)


@login_required
@require_POST
def pool_archive(request, organization_id, pool_id):
    services.archive_pool(user=request.user, organization_id=organization_id, pool_id=pool_id)
    return redirect('employers:talent_pools', organization_id=organization_id)


@login_required
@require_POST
def contact(request, organization_id, candidate_id):
    form = ContactForm(request.POST)
    if not form.is_valid():
        return HttpResponseBadRequest('Choose a contact purpose and vacancy or application.')
    try:
        contact = services.reveal_contact(user=request.user, organization_id=organization_id, candidate_id=candidate_id, **form.cleaned_data)
    except ValidationError as exc:
        return HttpResponseBadRequest('; '.join(exc.messages))
    member = discovery_membership(request.user, organization_id, 'contact')
    return _private(_render(request, 'talent_contact.html', member, contact=contact))
