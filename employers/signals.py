from django.db.models.signals import post_save
from django.dispatch import receiver
from core.models import CareerMarketSnapshot
from .models import Organization


@receiver(post_save, sender=Organization)
def organization_visibility_changed(sender, raw=False, **kwargs):
    if not raw:
        # Suspension/verification affect direct discovery. Frozen observations
        # remain preserved but cannot be presented as a current market snapshot.
        CareerMarketSnapshot.objects.filter(is_stale=False).update(is_stale=True)


from django.db.models.signals import post_delete
from core.models import WorkExperience
from .models import DiscoveryExperienceIndex


@receiver(post_save, sender=WorkExperience)
@receiver(post_delete, sender=WorkExperience)
def work_evidence_changed(sender, instance, raw=False, **kwargs):
    if not raw:
        DiscoveryExperienceIndex.objects.filter(profile_id=instance.profile_id).delete()


from django.contrib.auth import get_user_model
from core.models import OpportunityReport

@receiver(post_save, sender=OpportunityReport)
def direct_report_security(sender, instance, raw=False, **kwargs):
    if not raw:
        from .trust_services import report_signals
        report_signals(instance)


@receiver(post_save, sender=get_user_model())
def member_email_security(sender, instance, raw=False, **kwargs):
    if not raw:
        from .trust_services import member_signal
        for member in instance.employer_memberships.select_related('organization','user'):
            member_signal(member,instance)
