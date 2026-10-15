"""CareerPreference.target_roles is the sole authored career-intent authority."""
from core.models import CareerPreference
from core.services.professional_resolution import parse_target_roles, resolve_preference_targets


def profile_careers(profile):
    value = CareerPreference.objects.filter(profile=profile).values_list(
        "target_roles", flat=True).first() or ""
    return parse_target_roles(value)


def reconcile_career_mappings(profile):
    preference = CareerPreference.objects.filter(profile_id=profile).first()
    return resolve_preference_targets(preference) if preference else []
