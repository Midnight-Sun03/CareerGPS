import hashlib
import json

from django.core.management.base import BaseCommand
from django.db import transaction

from core.models import TaxonomyCrosswalk, TaxonomyRelease, TaxonomyReleaseSet


class Command(BaseCommand):
    help = "Fingerprint and activate the current OFO/ESCO/O*NET/ISCO release set."

    def add_arguments(self, parser):
        parser.add_argument("--set-version", default="professional-v1")

    @transaction.atomic
    def handle(self, *args, **options):
        releases = list(TaxonomyRelease.objects.filter(is_active=True).order_by("source"))
        sources = {row.source for row in releases}
        required = {"ofo", "esco", "onet", "isco"}
        if sources != required:
            missing = ", ".join(sorted(required - sources)) or "none"
            raise ValueError(f"Active taxonomy release set is incomplete; missing: {missing}")
        crosswalk_rows = list(TaxonomyCrosswalk.objects.filter(active=True).order_by("pk").values_list(
            "mapping_name", "mapping_version", "source_external_id", "target_external_id",
            "relation_type", "approval_status"))
        crosswalk_hash = hashlib.sha256(json.dumps(crosswalk_rows, separators=(",", ":")).encode()).hexdigest()
        payload = [(row.source, row.version, row.pk) for row in releases]
        fingerprint = hashlib.sha256(json.dumps(
            {"releases": payload, "crosswalk": crosswalk_hash},
            sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        TaxonomyReleaseSet.objects.filter(is_active=True).update(is_active=False)
        release_set, _ = TaxonomyReleaseSet.objects.update_or_create(
            version=options["set_version"], defaults={
                "fingerprint": fingerprint, "crosswalk_fingerprint": crosswalk_hash,
                "is_active": True,
            })
        release_set.releases.set(releases)
        self.stdout.write(self.style.SUCCESS(
            f"Activated {release_set.version} ({release_set.fingerprint})"))
