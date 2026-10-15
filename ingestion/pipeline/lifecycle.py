from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from applications.models import Application
from core.models import Opportunity, SavedOpportunity
from core.services.notifications import (
    CLOSING_SOON_DAYS, notify_closed_opportunity_for_user,
    notify_closing_soon_for_user, published_closing_date,
)
from ingestion.models import OpportunitySourceRecord


def mark_missing_source_records(source, seen_external_ids):
    now = timezone.now()
    missing = OpportunitySourceRecord.objects.filter(source=source, active_at_source=True)
    if seen_external_ids:
        missing = missing.exclude(source_external_id__in=seen_external_ids)
    missing.filter(missing_since__isnull=True).update(missing_since=now)


def _notify_interested_users(opportunity, notify_user):
    planning = {"planning", "ready_to_apply"}
    applications = list(Application.objects.filter(opportunity=opportunity).select_related("user"))
    already_applied = {
        application.user_id for application in applications
        if application.applied_at is not None or application.status not in planning
    }
    preparation = {
        application.user_id: application for application in applications
        if application.user_id not in already_applied
    }
    saved_users = set(SavedOpportunity.objects.filter(
        opportunity=opportunity
    ).values_list("user_id", flat=True))
    interested_ids = (saved_users | set(preparation)) - already_applied
    from django.contrib.auth import get_user_model
    users = get_user_model().objects.filter(pk__in=interested_ids)
    for user in users:
        application = preparation.get(user.pk)
        notify_user(
            user=user, opportunity=opportunity, application=application,
        )


@transaction.atomic
def refresh_opportunity_lifecycle():
    now = timezone.now()
    today = timezone.localdate()
    grace = now - timedelta(days=getattr(settings, "INGESTION_MISSING_GRACE_DAYS", 7))

    closing = Opportunity.objects.filter(
        Q(closing_date__lt=today) | Q(application_deadline__lt=today),
        listing_kind="vacancy",
        is_active=True,
        lifecycle_status="active",
    )
    for opportunity in closing.select_for_update().iterator():
        _notify_interested_users(opportunity, notify_closed_opportunity_for_user)
        opportunity.is_active = False
        opportunity.lifecycle_status = "expired"
        opportunity.save(update_fields=["is_active", "lifecycle_status"])

    stale_records = OpportunitySourceRecord.objects.filter(
        missing_since__lte=grace, active_at_source=True
    )
    affected_ids = set(stale_records.values_list("opportunity_id", flat=True))
    stale_records.update(active_at_source=False)
    for opportunity in Opportunity.objects.filter(id__in=affected_ids):
        if not opportunity.source_records.filter(active_at_source=True).exists():
            opportunity.is_active = False
            opportunity.lifecycle_status = "removed"
            opportunity.save(update_fields=["is_active", "lifecycle_status"])

    soon = Opportunity.objects.discoverable().filter(
        listing_kind='vacancy',
    ).filter(
        Q(closing_date__range=(today, today + timedelta(days=CLOSING_SOON_DAYS))) |
        Q(application_deadline__range=(today, today + timedelta(days=CLOSING_SOON_DAYS)))
    )
    for opportunity in soon.iterator():
        deadline = published_closing_date(opportunity)
        if deadline is not None and today <= deadline <= today + timedelta(days=CLOSING_SOON_DAYS):
            _notify_interested_users(opportunity, notify_closing_soon_for_user)
