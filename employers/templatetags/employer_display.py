from django import template

register = template.Library()

EVIDENCE_LABELS = {
    'FOUND': 'Evidenced',
    'RELATED': 'Related evidence',
    'NOT_FOUND_ON_PROFILE': 'Not found in profile',
    'UNKNOWN': 'Not enough information',
}


@register.filter
def candidate_identity(user):
    """Employer-facing name for a hiring record, including closed accounts.

    A closed account keeps its anonymised user row so hiring history and audit
    chains stay intact. The internal generated username is never shown to an
    employer: the deleted state is a presentation label only.
    """

    from core.data_rights import ANONYMISED_USERNAME_PREFIX, DELETED_CANDIDATE_LABEL

    if user is None:
        return DELETED_CANDIDATE_LABEL
    if not user.is_active and str(user.username or '').startswith(ANONYMISED_USERNAME_PREFIX):
        return DELETED_CANDIDATE_LABEL
    return user.get_full_name().strip() or user.username


@register.filter
def evidence_label(value):
    return EVIDENCE_LABELS.get(str(value), 'Not enough information')
