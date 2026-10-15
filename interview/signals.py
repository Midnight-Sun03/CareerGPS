"""Remove owned media after question deletion commits, including interview cascades."""

from django.db import transaction
from django.db.models import Q
from django.db.models.signals import post_delete
from django.dispatch import receiver

from .models import InterviewQuestion


@receiver(post_delete, sender=InterviewQuestion, dispatch_uid='interview_question_media_cleanup')
def cleanup_interview_media(sender, instance, using, **kwargs):
    for field_name in ('audio_file', 'video_file'):
        file = getattr(instance, field_name)
        if not file or not file.name:
            continue
        name, storage = file.name, file.storage

        def remove_unreferenced(name=name, storage=storage, using=using):
            if not InterviewQuestion.objects.using(using).filter(
                Q(audio_file=name) | Q(video_file=name)
            ).exists():
                storage.delete(name)

        transaction.on_commit(remove_unreferenced, using=using)
