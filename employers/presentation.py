"""Employer-facing availability derived from the canonical listing, never persisted."""
from datetime import timedelta

from django.utils import timezone

from core.models import Opportunity
from .permissions import organization_can_use_employer_platform


def vacancy_availability(vacancy, *, today=None, discoverable_ids=None):
    today = today or timezone.localdate()
    if vacancy.status == 'draft':
        return {'label': 'Draft', 'group': 'draft', 'live': False}
    if vacancy.status == 'closed':
        return {'label': 'Closed', 'group': 'closed', 'live': False}
    if vacancy.trust_hold or not organization_can_use_employer_platform(vacancy.organization):
        return {'label': 'On hold / action required', 'group': 'hold', 'live': False}
    opportunity = vacancy.opportunity
    if opportunity is None:
        return {'label': 'Action required', 'group': 'hold', 'live': False}
    deadline = min((date for date in (vacancy.closing_date, opportunity.application_deadline) if date), default=None)
    if deadline and deadline < today or opportunity.lifecycle_status in {'expired', 'removed'}:
        return {'label': 'Closed', 'group': 'closed', 'live': False}
    live = (opportunity.pk in discoverable_ids if discoverable_ids is not None else
            Opportunity.objects.professionally_ready().filter(pk=opportunity.pk).exists())
    if not live:
        return {'label': 'On hold / action required', 'group': 'hold', 'live': False}
    if deadline and deadline <= today + timedelta(days=7):
        return {'label': 'Closing soon', 'group': 'closing', 'live': True}
    return {'label': 'Active', 'group': 'active', 'live': True}
