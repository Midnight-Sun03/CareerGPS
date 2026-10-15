"""In-app delivery only. Account verification and password reset are separate."""
from urllib.parse import unquote, urlsplit

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q
from django.urls import reverse
from django.utils import timezone
from core.models import Notification, NotificationPreference


CLOSING_SOON_DAYS = 7  # Shared with the existing dashboard/saved-list "this week" window.


def _selected_revision(opportunity, application=None):
    return (application.clean_revision if application and application.clean_revision_id
            else opportunity.active_clean_revision)


def published_closing_date(opportunity, application=None):
    revision = _selected_revision(opportunity, application)
    return revision.closing_sort_at.date() if revision and revision.closing_sort_at else None


def validate_target_url(value):
    value = str(value or '')
    decoded = value
    for _ in range(5):
        next_value = unquote(decoded)
        if next_value == decoded:
            break
        decoded = next_value
    if not value:
        return ''
    if (len(value) > 500 or not decoded.startswith('/') or decoded.startswith('//')
            or '\\' in decoded or '%' in decoded
            or any(ord(char) < 32 or ord(char) == 127 for char in decoded)):
        raise ValidationError('Notification destinations must be internal paths.')
    try:
        parsed = urlsplit(decoded)
    except ValueError as exc:
        raise ValidationError('Invalid notification destination.') from exc
    if parsed.scheme or parsed.netloc:
        raise ValidationError('Notification destinations must be internal paths.')
    return value


@transaction.atomic
def notify(*, user, category, event_key, title, message='', target_url='', notification_type='system'):
    """Callers supply the intended owner, never a recipient ID from a request.

    A logical event requires a stable key. Database uniqueness also protects
    concurrent retries. Optional preferences govern future creation only.
    """
    target_url = validate_target_url(target_url)
    if category not in dict(Notification.CATEGORY_CHOICES):
        raise ValidationError('Unknown notification category.')
    if not event_key or len(event_key) > 200 or not title or len(title) > 200:
        raise ValidationError('A valid event key and title are required.')
    if not user.pk:
        raise ValidationError('A persisted recipient is required.')
    existing = Notification.objects.filter(user=user, event_key=event_key).first()
    if existing:
        return existing
    preference = NotificationPreference.objects.filter(user=user).first()
    if category != 'system' and preference and not getattr(preference, category):
        return None
    notification, _ = Notification.objects.get_or_create(user=user, event_key=event_key, defaults={
        'category': category, 'title': title, 'message': message,
        'target_url': target_url, 'notification_type': notification_type})
    return notification


def notify_closed_opportunity_for_user(*, user, opportunity, application=None):
    revision = _selected_revision(opportunity, application)
    closing_date = published_closing_date(opportunity, application)
    return notify(
        user=user, category='opportunities',
        event_key=f'opportunity-closed:{opportunity.pk}:{closing_date}',
        title=f'Opportunity closed: {revision.cleaned_title if revision else "Saved opportunity"}'[:200],
        message=(
            'The published closing date has passed. You had saved or started preparing '
            'for this opportunity, but had not marked it as applied.'
        ),
        target_url=(reverse('applications:workspace', args=[application.pk])
                    if application else reverse('saved')),
        notification_type='opportunity_deadline',
    )


def notify_closing_soon_for_user(*, user, opportunity, application=None):
    revision = _selected_revision(opportunity, application)
    closing_date = published_closing_date(opportunity, application)
    if closing_date is None:
        return None
    return notify(
        user=user, category='deadlines',
        event_key=f'opportunity-closing-soon:{CLOSING_SOON_DAYS}:{opportunity.pk}:{closing_date}',
        title=f'{revision.cleaned_title if revision else "Saved opportunity"} closes soon'[:200],
        message=(
            f'This opportunity closes on {closing_date:%d %b}. You saved or started '
            'preparing for it but have not marked an application as submitted.'
        ),
        target_url=(reverse('applications:workspace', args=[application.pk])
                    if application else reverse('saved')),
        notification_type='opportunity_deadline',
    )


def notify_overdue_opportunities_for_user(user):
    """Create missing in-app closure notices when an interested user returns."""
    from applications.models import Application
    from core.models import Opportunity, SavedOpportunity

    applications = {row.opportunity_id: row for row in Application.objects.filter(
        user=user).select_related('clean_revision')}
    interested_ids = set(SavedOpportunity.objects.filter(user=user).values_list('opportunity_id', flat=True))
    interested_ids.update(applications)
    if not interested_ids:
        return
    today = timezone.localdate()
    closed = Opportunity.objects.filter(
        pk__in=interested_ids, listing_kind='vacancy').select_related('active_clean_revision')
    for opportunity in closed:
        application = applications.get(opportunity.pk)
        closing_date = published_closing_date(opportunity, application)
        if not closing_date or closing_date >= today:
            continue
        if application and (application.applied_at is not None or application.status not in {'planning', 'ready_to_apply'}):
            continue
        notify_closed_opportunity_for_user(user=user, opportunity=opportunity, application=application)
