"""Compatibility projection from the rich profile to existing CareerGPS consumers."""

from .models import Skill, normalize_profile_value


def _deduplicated_names(values):
    seen = set()
    result = []
    for value in values:
        display = " ".join(str(value or "").split())
        normalized = normalize_profile_value(display)
        if display and normalized not in seen:
            seen.add(normalized)
            result.append(display)
    return result


def get_profile_projection(profile):
    """
    Present rich profile facts to legacy matching/CV consumers.

    Migration 0006 copied every existing legacy fact into the rich profile
    tables. Those records are now authoritative. The legacy Profile fields
    remain in the schema for compatibility, but must not resurrect a fact a
    user has edited or deleted from the rich profile.
    """
    education = profile.education_records.order_by(
        '-currently_studying', '-end_date', '-start_date', '-id'
    ).first()

    skill_values = list(profile.profile_skills.values_list('entered_name', flat=True))
    interest_values = list(profile.profile_interests.values_list('entered_name', flat=True))

    return {
        'qualification': education.qualification if education else '',
        'institution': education.institution if education else '',
        'location': profile.location,
        'skill_names': _deduplicated_names(skill_values),
        'interest_names': _deduplicated_names(interest_values),
    }


def get_matching_global_skill_ids(profile, projection=None):
    """Map free-entry profile skills to the optional legacy Skill vocabulary."""
    projection = projection or get_profile_projection(profile)
    profile_names = {
        normalize_profile_value(name) for name in projection['skill_names']
    }
    if not profile_names:
        return []
    return [
        skill.id
        for skill in Skill.objects.only('id', 'name')
        if normalize_profile_value(skill.name) in profile_names
    ]


def get_profile_progress(profile):
    """Return useful, non-blocking profile strength and section indicators."""
    projection = get_profile_projection(profile)
    has_personal = bool(profile.user.first_name or profile.user.last_name or profile.user.email)
    has_experience = profile.work_experiences.exists() or profile.projects.exists()
    has_credentials = profile.certifications.exists() or profile.achievements.exists()
    has_preferences = hasattr(profile, 'career_preferences') and any([
        profile.career_preferences.target_roles.strip(),
        profile.career_preferences.preferred_locations.strip(),
        profile.career_preferences.work_arrangement,
        profile.career_preferences.opportunity_kinds.strip(),
        profile.career_preferences.industries.strip(),
        profile.career_preferences.short_career_goal.strip(),
    ])

    sections = [
        ('Personal details', has_personal, 10),
        ('Location and contact', bool(profile.location or profile.phone), 10),
        ('Education', profile.education_records.exists(), 15),
        ('Skills', bool(projection['skill_names']), 15),
        ('Interests', bool(projection['interest_names']), 10),
        ('Career preferences', has_preferences, 15),
        ('Experience or projects', has_experience, 10),
        ('Certifications or achievements', has_credentials, 5),
        ('Volunteering', profile.volunteering_records.exists(), 3),
        ('Languages', profile.languages.exists(), 4),
        ('Portfolio links', profile.profile_links.exists(), 3),
    ]
    score = sum(weight for _, complete, weight in sections if complete)
    return score, [
        {'label': label, 'complete': complete, 'weight': weight}
        for label, complete, weight in sections
    ]
