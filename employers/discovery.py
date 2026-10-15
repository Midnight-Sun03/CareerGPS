"""Discovery policy, SQL search and an explicit employer-facing data boundary."""
from collections import defaultdict
from dataclasses import dataclass
from decimal import Decimal, ROUND_CEILING
import heapq

from django.core.paginator import Paginator
from django.db.models import Exists, OuterRef, Prefetch, Q
from django.shortcuts import get_object_or_404
from django.utils import timezone

from core.models import (CareerTargetResolution, Profile, ProfileSkill, Education,
    Certification, WorkExperience, Opportunity, ProfileFeedEvaluation,
    ProfileFeedState)
from core.taxonomies.normalization import normalize_term
from core.professional_policies import (
    CANDIDATE_RELATIONSHIP_POLICY_VERSION, RELATIONSHIP_AGGREGATION_VERSION,
)
from core.services.evidence_fit import (
    attach_project_capability_evidence, candidate_evidence,
    compare_profile_to_revision, merged_experience_days,
    prefetch_confirmed_evidence,
)
from core.services.occupational_resolution import SOURCE_VERSIONS
from .models import (CandidateEmployerBlock, CandidateVisibilityPreference, DiscoveryExperienceIndex,
    EmployerVacancy, HiringApplication, SavedCandidate)

STATUS_LABELS = {'FOUND': 'Evidenced', 'RELATED': 'Related evidence',
    'NOT_FOUND_ON_PROFILE': 'Not found in profile', 'UNKNOWN': 'Not enough information'}
PAGE_SIZE = 20
BATCH_SIZE = 100


def eligible_profiles(member, scope='all', *, saving=False):
    """All privacy gates precede counts, filters, pagination and object lookup."""
    from .permissions import membership_for
    member = membership_for(member.user, member.organization_id, 'talent')
    organization = member.organization
    from .permissions import organization_can_use_employer_platform
    if not organization_can_use_employer_platform(organization):
        return Profile.objects.none()
    prior = HiringApplication.objects.filter(vacancy__organization=organization, application__user_id=OuterRef('user_id'))
    blocked = CandidateEmployerBlock.objects.filter(organization=organization, profile_id=OuterRef('pk'))
    rows = Profile.objects.filter(user__is_active=True).annotate(_has_prior=Exists(prior), _blocked=Exists(blocked)).filter(_blocked=False)
    public = Q(employer_visibility__discoverable_by_employers=True)
    previous = Q(_has_prior=True, employer_visibility__allow_prior_applicant_rediscovery=True)
    rows = rows.filter(previous if scope == 'prior' else public if scope == 'public' else public | previous)
    if saving:
        rows = rows.filter(employer_visibility__allow_talent_pool_saving=True)
    return rows


def published_vacancy(member, vacancy_id):
    if not vacancy_id:
        return None
    return get_object_or_404(EmployerVacancy.objects.select_related('opportunity'), pk=vacancy_id,
        organization=member.organization, status='published',
        opportunity__in=Opportunity.objects.professionally_ready())


def refresh_experience_index(profiles):
    """Portable, overlap-safe derived index; only stale authorized IDs are read.

    Work edits refresh their own row. Ongoing employment also advances with the
    date, so an experience-filtered search refreshes stale rows in fixed batches.
    SQL still performs the numeric filter; no full profiles are loaded to filter.
    """
    today = timezone.localdate()
    stale = profiles.exclude(discovery_experience__calculated_on=today).order_by('pk')
    last_id = 0
    while True:
        ids = list(stale.filter(pk__gt=last_id).values_list('pk', flat=True)[:BATCH_SIZE])
        if not ids:
            break
        work = defaultdict(list)
        for row in WorkExperience.objects.filter(profile_id__in=ids, confirmed_by_user=True).only(
                'profile_id', 'start_date', 'end_date', 'currently_working'):
            work[row.profile_id].append(row)
        updates = []
        for profile_id in ids:
            days = merged_experience_days(work[profile_id], today)
            updates.append(DiscoveryExperienceIndex(profile_id=profile_id,
                months=None if days is None else days * 12 // 365, calculated_on=today))
        DiscoveryExperienceIndex.objects.bulk_create(updates, update_conflicts=True,
            unique_fields=['profile'], update_fields=['months', 'calculated_on'])
        last_id = ids[-1]


def search_profiles(member, filters):
    rows = eligible_profiles(member, filters.get('scope') or 'public')
    if filters.get('career'):
        career = CareerTargetResolution.objects.filter(
            profile_id=OuterRef('pk'), normalized_text=normalize_term(filters['career']),
            source_resolutions__state='RESOLVED',
            source_resolutions__taxonomy_release__is_active=True,
            source_resolutions__resolver_version__in=SOURCE_VERSIONS.values())
        rows = rows.alias(_career_exact=Exists(career)).filter(_career_exact=True)
    if filters.get('location'):
        rows = rows.filter(employer_visibility__discovery_location__icontains=filters['location'])
    for field, model, column in [('skill', ProfileSkill, 'entered_name'), ('qualification', Education, 'qualification'),
            ('certification', Certification, 'title')]:
        if filters.get(field):
            evidence = model.objects.filter(profile_id=OuterRef('pk'), confirmed_by_user=True,
                **{column + '__icontains': filters[field]})
            rows = rows.alias(**{'_filter_' + field: Exists(evidence)}).filter(**{'_filter_' + field: True})
    if filters.get('min_experience') is not None:
        refresh_experience_index(rows)
        months = int((filters['min_experience'] * Decimal(12)).to_integral_value(rounding=ROUND_CEILING))
        rows = rows.filter(discovery_experience__months__gte=months, discovery_experience__calculated_on=timezone.localdate())
    saved = SavedCandidate.objects.filter(organization=member.organization, candidate_id=OuterRef('pk'), active=True,
        candidate__employer_visibility__allow_talent_pool_saving=True)
    rows = rows.annotate(_saved=Exists(saved))
    if filters.get('saved') in {'yes', 'no'}:
        rows = rows.filter(_saved=filters['saved'] == 'yes')
    return rows.order_by('pk')


def profile_batch(rows):
    # No internal Profile/User instance crosses the view/template boundary.
    resolved_targets = CareerTargetResolution.objects.filter(
        source_resolutions__state='RESOLVED',
        source_resolutions__taxonomy_release__is_active=True,
        source_resolutions__resolver_version__in=SOURCE_VERSIONS.values(),
    ).distinct()
    return prefetch_confirmed_evidence(rows.select_related('user', 'employer_visibility').prefetch_related(
        Prefetch('target_resolutions', queryset=resolved_targets,
                 to_attr='_resolved_target_resolutions')).only(
        'id', 'user_id', 'user__id', 'user__first_name', 'user__last_name',
        'career_preferences__id', 'career_preferences__profile_id', 'career_preferences__target_roles',
        'career_preferences__confirmed_by_user', 'employer_visibility'))


@dataclass(frozen=True)
class DiscoveryCard:
    id: int
    display_name: str
    location: str
    careers: tuple
    skills: tuple
    education: tuple
    employment: tuple
    certifications: tuple
    languages: tuple
    experience_months: int | None
    can_save: bool
    is_saved: bool
    match_summary: object = None
    criteria: tuple = ()
    target_alignment: object = None


def project_profile(profile, evidence, result=None, target_alignment=None):
    visibility = profile.employer_visibility
    criteria = tuple({'criterion': row.criterion, 'label': row.label,
        'importance': row.importance.value, 'status': row.status.value, 'status_label': STATUS_LABELS[row.status.value],
        'explanation': row.explanation.replace('Your ', "The candidate's ").replace('your ', "the candidate's "),
        'evidence': row.profile_evidence} for row in result.criteria) if result else ()
    return DiscoveryCard(id=profile.pk,
        display_name=profile.user.get_full_name().strip() or f'Candidate {profile.pk}',
        location=visibility.discovery_location,
        careers=tuple(row.authored_text for row in profile._resolved_target_resolutions),
        skills=evidence.skills,
        education=tuple({'qualification': row.qualification, 'field_of_study': row.field_of_study,
            'nqf_level': row.nqf_level} for row in evidence.education),
        employment=tuple({'title': row.title, 'organization': row.organisation,
            'start_date': row.start_date, 'end_date': row.end_date, 'current': row.currently_working}
            for row in evidence.work_experiences),
        certifications=evidence.certifications, languages=evidence.languages,
        experience_months=evidence.experience_months,
        can_save=visibility.allow_talent_pool_saving, is_saved=getattr(profile, '_saved', False),
        match_summary=result.summary if result else None, criteria=criteria,
        target_alignment=target_alignment)


def practical_facts(profile, vacancy, *, shared_discovery=False):
    """Show only logistics already shared for discovery; never infer a score."""
    visibility = getattr(profile, 'employer_visibility', None) if shared_discovery else None
    return (
        {'label': 'Location', 'vacancy': vacancy.location or 'Not specified',
         'candidate': visibility.discovery_location if visibility and visibility.discovery_location else 'Not shared'},
        {'label': 'Work arrangement', 'vacancy': vacancy.get_work_arrangement_display() if vacancy.work_arrangement else 'Not specified',
         'candidate': 'Not shared'},
    )


_ALIGNMENT_LABELS = {
    'EXACT': 'Exact target alignment',
    'RELATED': 'Related target alignment',
    'CONFLICT': 'Conflicting occupation evidence',
    'UNRESOLVED': 'Occupation relationship not established',
}


def match_batch(profiles, vacancy):
    profiles = attach_project_capability_evidence(profiles)
    evidence = [candidate_evidence(profile, today=timezone.localdate()) for profile in profiles]
    if not vacancy:
        return [(profile, facts, None, None) for profile, facts in zip(profiles, evidence)]
    revision = vacancy.opportunity.active_clean_revision
    profile_ids = [profile.pk for profile in profiles]
    active = dict(ProfileFeedState.objects.filter(
        profile_id__in=profile_ids, state='READY', active_generation__state='ACTIVE',
    ).values_list('profile_id', 'active_generation_id'))
    alignments = {}
    if active:
        rows = ProfileFeedEvaluation.objects.filter(
            generation_id__in=active.values(), opportunity=vacancy.opportunity,
            clean_revision=revision,
            relationship_assessment__aggregation_version=RELATIONSHIP_AGGREGATION_VERSION,
            relationship_assessment__relationship_policy_version=(
                CANDIDATE_RELATIONSHIP_POLICY_VERSION),
        ).select_related('generation', 'relationship_assessment')
        for row in rows:
            if active.get(row.generation.profile_id) == row.generation_id:
                alignments[row.generation.profile_id] = {
                    'class': row.discovery_category,
                    'label': _ALIGNMENT_LABELS.get(
                        row.discovery_category, 'Occupation relationship not established'),
                    'tier': row.relationship_tier,
                    'reason': row.relationship_assessment.reason_code,
                }
    return [(
        profile, facts, compare_profile_to_revision(profile, revision),
        alignments.get(profile.pk),
    ) for profile, facts in zip(profiles, evidence)]


def candidate_page(rows, vacancy, page_number):
    """SQL filtering/pagination; bounded batches for exact shared-engine ranking.

    General search does not score candidates. Vacancy ranking considers every
    eligible SQL result, retaining only a heap of IDs/scores through this page.
    It never hides a low-scoring candidate or ranks only a sampled first page.
    """
    paginator = Paginator(range(rows.count()), PAGE_SIZE)
    page = paginator.get_page(page_number)
    if not vacancy:
        page.object_list = [project_profile(*entry) for entry in match_batch(list(profile_batch(rows[(page.number - 1) * PAGE_SIZE:page.number * PAGE_SIZE])), None)]
        return page
    keep = page.number * PAGE_SIZE
    heap = []
    last_id = 0
    while True:
        profiles = list(profile_batch(rows.filter(pk__gt=last_id).order_by('pk')[:BATCH_SIZE]))
        if not profiles:
            break
        for profile, evidence, result, _alignment in match_batch(profiles, vacancy):
            item = (result.summary.score, -profile.pk)
            if len(heap) < keep:
                heapq.heappush(heap, item)
            elif item > heap[0]:
                heapq.heapreplace(heap, item)
        last_id = profiles[-1].pk
    ids = [-item[1] for item in sorted(heap, reverse=True)][(page.number - 1) * PAGE_SIZE:keep]
    # Reapply the privacy queryset before materializing the selected page.
    cards = {entry[0].pk: project_profile(*entry) for entry in match_batch(list(profile_batch(rows.filter(pk__in=ids))), vacancy)}
    page.object_list = [cards[pk] for pk in ids if pk in cards]
    return page
