"""Explicit, permission-checked transitions with private, retained audit history."""
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone

from core.models import (Story, StoryReport, StoryModerationEvent, Opportunity,
    OpportunityReport, OpportunityModerationEvent)
from .notifications import notify


def require_moderator(actor, model):
    if not actor.is_active or not actor.is_staff or not actor.has_perm(f'core.change_{model._meta.model_name}'):
        raise PermissionDenied('Moderator permission required.')


@transaction.atomic
def moderate_story(*, story_id, actor, status, note=''):
    require_moderator(actor, Story)
    if status not in dict(Story.STATUS_CHOICES):
        raise ValidationError('Invalid story moderation state.')
    story = Story.objects.select_for_update().get(pk=story_id)
    if story.status == status:
        if story.moderation_note != note:
            Story.objects.filter(pk=story.pk).update(moderation_note=note)
        return story
    event = StoryModerationEvent.objects.create(story=story, actor=actor,
        from_status=story.status, to_status=status, note=note)
    story.status = status
    story.moderation_note = note
    # Keep the original publication timestamp on removal/restoration for audit.
    if status == 'approved' and not story.approved_at:
        story.approved_at = timezone.now()
    story.save(update_fields=['status', 'moderation_note', 'approved_at'])
    titles = {'approved': 'Your story was approved', 'rejected': "Your story wasn't approved",
        'removed': 'Your story was removed', 'pending': 'Your story is awaiting review'}
    message = {
        'approved': f'"{story.title}" has been approved and is now live on CareerGPS.',
        'rejected': f'"{story.title}" was not approved. {note.strip() or "You can review your story and submit again later."}',
        'removed': f'"{story.title}" was removed from the Stories feed following moderation.',
        'pending': f'"{story.title}" is awaiting moderation.',
    }[status]
    if story.user_id:
        notify(user=story.user, category='stories', event_key=f'story-moderation:{event.pk}',
            title=titles[status], message=message, target_url='/stories/',
            notification_type=f'story_{status}' if status in {'approved', 'rejected'} else 'system')
    return story


@transaction.atomic
def moderate_opportunity(*, opportunity_id, actor, status, note=''):
    require_moderator(actor, Opportunity)
    if status not in {'clear', 'under_review', 'suppressed'}:
        raise ValidationError('Invalid opportunity moderation state.')
    opportunity = Opportunity.objects.select_for_update().get(pk=opportunity_id)
    if opportunity.moderation_status == status:
        return opportunity
    OpportunityModerationEvent.objects.create(opportunity=opportunity, actor=actor,
        from_status=opportunity.moderation_status, to_status=status, note=note)
    opportunity.moderation_status = status
    opportunity.save(update_fields=['moderation_status'])
    return opportunity


@transaction.atomic
def submit_report(*, reporter, target, reason, details=''):
    if not reporter.is_authenticated or not reporter.is_active:
        raise PermissionDenied
    if isinstance(target, Story):
        target = Story.objects.select_for_update().get(pk=target.pk)
        if target.status != 'approved':
            raise ValidationError('Only published Stories can be reported.')
        if target.user_id == reporter.pk:
            raise ValidationError('You cannot report your own Story.')
        model, relation = StoryReport, {'story': target}
    elif isinstance(target, Opportunity):
        from employers.models import Organization
        # Serialize organization-wide report signals before locking its vacancy.
        list(Organization.objects.select_for_update().filter(vacancies__opportunity=target))
        target = Opportunity.objects.select_for_update().get(pk=target.pk)
        # Saved/application history can still be reported after a listing expires.
        if not target.is_catalogue_available and not (
            target.savedopportunity_set.filter(user=reporter).exists()
            or target.applications.filter(user=reporter).exists()
        ):
            raise PermissionDenied
        model, relation = OpportunityReport, {'opportunity': target}
    else:
        raise ValidationError('Unsupported report target.')
    if model.objects.filter(reporter=reporter, status='open', **relation).exists():
        raise ValidationError('You already have an open report for this item.')
    report = model(reporter=reporter, reason=reason, details=details, **relation)
    report.full_clean()
    report.save()
    return report


@transaction.atomic
def resolve_report(*, report, actor, action, note=''):
    model = type(report)
    require_moderator(actor, model)
    if action not in {'dismiss', 'remove', 'resolve'}:
        raise ValidationError('Invalid moderation action.')
    # Target first, consistently with report submission, then report.
    if isinstance(report, StoryReport):
        Story.objects.select_for_update().get(pk=report.story_id)
    else:
        from employers.models import Organization
        list(Organization.objects.select_for_update().filter(vacancies__opportunity_id=report.opportunity_id))
        Opportunity.objects.select_for_update().get(pk=report.opportunity_id)
    report = model.objects.select_for_update().get(pk=report.pk)
    if report.status != 'open':
        return report
    if action == 'remove':
        if isinstance(report, StoryReport):
            moderate_story(story_id=report.story_id, actor=actor, status='removed', note=note)
        else:
            moderate_opportunity(opportunity_id=report.opportunity_id, actor=actor, status='suppressed', note=note)
    elif action == 'resolve' and not note.strip():
        raise ValidationError('Record the action taken in the moderator note.')
    report.status = 'resolved'
    report.resolution = 'dismissed' if action == 'dismiss' else 'action_taken'
    report.reviewer = actor
    report.reviewed_at = timezone.now()
    report.moderator_note = note
    report.save(update_fields=['status', 'resolution', 'reviewer', 'reviewed_at', 'moderator_note'])
    is_story = isinstance(report, StoryReport)
    notify(user=report.reporter, category='stories' if is_story else 'opportunities',
        event_key=f'{model._meta.model_name}:resolved:{report.pk}', title='Your report was reviewed',
        message=('A moderator reviewed your report. ' + (
            'The report was dismissed.' if action == 'dismiss' else 'Moderation action was recorded.')),
        target_url='/stories/' if is_story else '/opportunities/')
    return report
