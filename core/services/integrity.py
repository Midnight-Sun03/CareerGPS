"""Invalidate derived data while preserving its prior evidence and provenance."""
from django.db.models.signals import m2m_changed, post_save, post_delete, pre_save
from django.db import transaction
from django.dispatch import receiver
from django.utils import timezone
from core.models import (
    CareerMarketSnapshot, CareerPreference, Certification, Education, Language,
    OccupationDefiningCriterion, OccupationRelationshipPolicy,
    OccupationRelationshipRule, Opportunity, ProfileSkill, ProjectCapability,
    TaxonomyCrosswalk, TaxonomyRelease, TaxonomyReleaseSet, WorkExperience,
)
from ingestion.models import (
    OpportunityCriterion, OpportunityExtraction, OpportunitySourceRecord,
)
from ingestion.ai.payload import build_compact_opportunity_payload


@receiver(post_save, sender=Opportunity)
def opportunity_changed(sender, instance, raw=False, **kwargs):
    if raw:
        return
    # Read the persisted row: update_fields may exclude dirty in-memory fields.
    current = Opportunity.objects.get(pk=instance.pk)
    digest = build_compact_opportunity_payload(current)['input_hash']
    OpportunityExtraction.objects.filter(opportunity_id=instance.pk, status__in=['ready', 'processing']).exclude(
        input_hash=digest).update(status='stale', updated_at=timezone.now())
    # A new/changed listing can also enter a cohort it was not previously in.
    CareerMarketSnapshot.objects.filter(is_stale=False).update(is_stale=True)


@receiver(post_save, sender=OpportunityExtraction)
@receiver(post_delete, sender=OpportunityExtraction)
@receiver(post_save, sender=OpportunityCriterion)
@receiver(post_delete, sender=OpportunityCriterion)
@receiver(post_save, sender=TaxonomyRelease)
def market_evidence_changed(sender, raw=False, **kwargs):
    if not raw:
        CareerMarketSnapshot.objects.filter(is_stale=False).update(is_stale=True)


def _profile_id(instance):
    if isinstance(instance, ProjectCapability):
        return instance.project.profile_id
    return instance.profile_id


@receiver(post_save, sender=ProfileSkill)
@receiver(post_delete, sender=ProfileSkill)
@receiver(post_save, sender=Education)
@receiver(post_delete, sender=Education)
@receiver(post_save, sender=WorkExperience)
@receiver(post_delete, sender=WorkExperience)
@receiver(post_save, sender=Certification)
@receiver(post_delete, sender=Certification)
@receiver(post_save, sender=Language)
@receiver(post_delete, sender=Language)
@receiver(post_save, sender=ProjectCapability)
@receiver(post_delete, sender=ProjectCapability)
@receiver(post_save, sender=CareerPreference)
@receiver(post_delete, sender=CareerPreference)
def professional_profile_input_changed(sender, instance, raw=False, **kwargs):
    if raw:
        return
    from core.services.feed_queue import enqueue_profile_rebuild
    profile_id = _profile_id(instance)
    # Defense-in-depth: if the profile row was removed first (cascade or
    # direct delete), bail out rather than enqueueing a rebuild against a
    # None profile id, which would surface as a None lookup downstream.
    if not profile_id:
        return
    reason = f'{sender._meta.model_name}_changed'
    change_kind = 'intent' if sender is CareerPreference else 'evidence'
    transaction.on_commit(lambda: enqueue_profile_rebuild(
        profile_id, reason, change_kind=change_kind))
    if change_kind == 'intent':
        # A target edit also schedules the *separate* provisional legacy task.
        # Independent identity, independent queue version, independent failure:
        # it can neither block nor be blocked by the v2 generation task.
        from core.services.provisional_feed import enqueue_provisional_rebuild
        transaction.on_commit(lambda: enqueue_provisional_rebuild(
            profile_id, reason))
    # Wake the background drain only AFTER both enqueues have written.  The
    # old order (trigger between the two enqueues) started a competing writer
    # in the middle of this request's own on_commit chain; on SQLite that race
    # surfaced as OperationalError("database is locked") raised from the
    # provisional enqueue -- a user-visible 500 on a save that had already
    # committed.  Enqueue first, then wake.
    transaction.on_commit(lambda: _trigger_queue_drain(reason))
    if change_kind == 'intent':
        transaction.on_commit(lambda: _trigger_queue_drain(reason))


@receiver(pre_save, sender=OpportunitySourceRecord)
def remember_source_raw(sender, instance, raw=False, **kwargs):
    if raw:
        return
    instance._previous_current_raw_id = (
        sender.objects.filter(pk=instance.pk).values_list('current_raw_id', flat=True).first()
        if instance.pk else None)


def _trigger_queue_drain(reason):
    """Best-effort self-trigger: never let queue scheduling break a save."""
    try:
        from core.services.queue_worker import request_queue_drain
    except Exception:  # noqa: BLE001 - scheduling is auxiliary to the save
        return
    request_queue_drain(reason=reason)


@receiver(post_save, sender=OpportunitySourceRecord)
def source_version_changed(sender, instance, created=False, raw=False, **kwargs):
    if raw:
        return
    previous = getattr(instance, '_previous_current_raw_id', None)
    if not created and previous == instance.current_raw_id:
        return
    from core.services.feed_queue import enqueue_opportunity_rebuild
    opportunity_id = instance.opportunity_id
    transaction.on_commit(lambda: enqueue_opportunity_rebuild(
        opportunity_id, 'source_version_changed'))
    transaction.on_commit(
        lambda: _trigger_queue_drain('source_version_changed'))


@receiver(post_save, sender=TaxonomyCrosswalk)
@receiver(post_delete, sender=TaxonomyCrosswalk)
@receiver(post_save, sender=TaxonomyRelease)
@receiver(post_delete, sender=TaxonomyRelease)
@receiver(post_save, sender=TaxonomyReleaseSet)
@receiver(post_delete, sender=TaxonomyReleaseSet)
@receiver(post_save, sender=OccupationDefiningCriterion)
@receiver(post_delete, sender=OccupationDefiningCriterion)
@receiver(post_save, sender=OccupationRelationshipPolicy)
@receiver(post_delete, sender=OccupationRelationshipPolicy)
@receiver(post_save, sender=OccupationRelationshipRule)
@receiver(post_delete, sender=OccupationRelationshipRule)
def professional_policy_knowledge_changed(sender, instance=None, raw=False, **kwargs):
    if raw:
        return
    from core.models import Profile
    for profile in Profile.objects.iterator(chunk_size=500):
        if CareerPreference.objects.filter(profile=profile).exclude(target_roles='').exists():
            from core.services.feed_queue import enqueue_profile_rebuild
            profile_id = profile.pk
            reason = f'{sender._meta.model_name}_changed'
            causal = (
                f'{reason}:{getattr(instance, "pk", "deleted")}:'
                f'{getattr(instance, "updated_at", timezone.now())}')
            transaction.on_commit(lambda profile_id=profile_id, reason=reason, causal=causal: (
                enqueue_profile_rebuild(
                    profile_id, reason, change_kind='policy',
                    causal_dependency=causal)))
            transaction.on_commit(
                lambda reason=reason: _trigger_queue_drain(reason))


@receiver(m2m_changed, sender=TaxonomyReleaseSet.releases.through)
def taxonomy_release_set_membership_changed(sender, instance, action, **kwargs):
    if action in {'post_add', 'post_remove', 'post_clear'}:
        professional_policy_knowledge_changed(sender, raw=False)
