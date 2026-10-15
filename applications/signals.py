"""Delete generated artifacts only after their database owner is gone and committed."""

from django.db import transaction
from django.db.models import Q
from django.db.models.signals import post_delete
from django.dispatch import receiver

from .models import GeneratedDocument


@receiver(post_delete, sender=GeneratedDocument, dispatch_uid='generated_document_artifact_cleanup')
def cleanup_generated_document(sender, instance, using, **kwargs):
    for field_name in ('pdf_file', 'docx_file'):
        file = getattr(instance, field_name)
        if not file or not file.name:
            continue
        name, storage = file.name, file.storage

        def remove_unreferenced(name=name, storage=storage, using=using):
            if not GeneratedDocument.objects.using(using).filter(
                Q(pdf_file=name) | Q(docx_file=name)
            ).exists():
                storage.delete(name)

        transaction.on_commit(remove_unreferenced, using=using)
