"""Test fixture helpers: simulate editing saved Profile career-role text."""
from django.core.exceptions import ValidationError
from django.db import transaction

from core.models import CareerPreference, CareerTargetResolution, Profile
from core.services.career_intent import profile_careers, reconcile_career_mappings
from core.services.career_resolution import resolve_career
from core.taxonomies.normalization import normalize_term


def _write_roles(profile, roles):
    preference, _ = CareerPreference.objects.get_or_create(profile=profile)
    preference.target_roles = ', '.join(roles)
    preference.confirmed_by_user = True
    preference.source = 'user_entered'
    preference.save(update_fields=['target_roles', 'confirmed_by_user', 'source', 'updated_at'])
    return reconcile_career_mappings(profile.pk)


@transaction.atomic
def add_career(*, profile, occupation):
    Profile.objects.select_for_update().get(pk=profile.pk)
    if resolve_career(occupation.preferred_label).occupation != occupation:
        raise ValidationError('This role does not have one trusted occupation identity.')
    roles = profile_careers(profile)
    if any(normalize_term(role) == normalize_term(occupation.preferred_label) for role in roles):
        raise ValidationError('This career is already in Career Preferences.')
    resolutions = _write_roles(profile, [*roles, occupation.preferred_label])
    return resolutions[-1]


@transaction.atomic
def set_primary(*, profile, target_id):
    Profile.objects.select_for_update().get(pk=profile.pk)
    target = CareerTargetResolution.objects.get(profile=profile, pk=target_id)
    roles = profile_careers(profile)
    if normalize_term(target.authored_text) not in {normalize_term(role) for role in roles}:
        raise ValidationError('This career no longer matches your profile.')
    resolutions = _write_roles(profile, [target.authored_text, *[role for role in roles
        if normalize_term(role) != normalize_term(target.authored_text)]])
    return resolutions[0]


@transaction.atomic
def remove_career(*, profile, target_id):
    Profile.objects.select_for_update().get(pk=profile.pk)
    target = CareerTargetResolution.objects.get(profile=profile, pk=target_id)
    _write_roles(profile, [role for role in profile_careers(profile)
        if normalize_term(role) != normalize_term(target.authored_text)])
