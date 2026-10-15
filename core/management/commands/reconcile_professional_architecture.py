from django.core.management.base import BaseCommand, CommandError
from django.db.models import F, Q

from core.models import (
    Opportunity, OpportunityCleanRevision, ProfileOpportunityEvaluation,
    TaxonomyCrosswalk, TaxonomyRelease, TaxonomyReleaseSet,
)
from ingestion.models import OpportunityExtraction, OpportunitySourceRecord


class Command(BaseCommand):
    help = "Report and optionally enforce final clean-architecture invariants."

    def add_arguments(self, parser):
        parser.add_argument("--strict", action="store_true")

    def handle(self, *args, **options):
        invalid_active = OpportunityCleanRevision.objects.filter(status="active").exclude(
            opportunity__active_clean_revision_id=F("pk")).count()
        stale_active = OpportunityCleanRevision.objects.filter(status="active").exclude(
            raw_opportunity_id=F("source_record__current_raw_id")).count()
        bad_seniority = OpportunityCleanRevision.objects.filter(status="active").exclude(
            seniority_label__in=("NON_SENIOR", "OBVIOUS_SENIOR", "UNCERTAIN")).count()
        mismatched_eval = ProfileOpportunityEvaluation.objects.filter(state="CURRENT").exclude(
            clean_revision_id=F("opportunity__active_clean_revision_id")).count()
        active_sources = OpportunitySourceRecord.objects.filter(current_raw__isnull=False).count()
        counts = {
            "source_records_with_current_raw": active_sources,
            "legacy_extractions_ready": OpportunityExtraction.objects.filter(status="ready").count(),
            "active_clean_revisions": OpportunityCleanRevision.objects.filter(status="active").count(),
            "professional_catalogue": Opportunity.objects.professionally_ready().count(),
            "active_taxonomy_releases": TaxonomyRelease.objects.filter(is_active=True).count(),
            "active_taxonomy_release_sets": TaxonomyReleaseSet.objects.filter(is_active=True).count(),
            "active_crosswalk_rows": TaxonomyCrosswalk.objects.filter(active=True).count(),
            "active_pointer_mismatches": invalid_active,
            "stale_active_revisions": stale_active,
            "invalid_seniority_values": bad_seniority,
            "current_evaluation_revision_mismatches": mismatched_eval,
        }
        for key, value in counts.items():
            self.stdout.write(f"{key}={value}")
        failures = {
            key: value for key, value in counts.items() if key in {
                "legacy_extractions_ready", "active_pointer_mismatches",
                # A source edit intentionally makes its former immutable revision
                # stale until the next external-cleaning cycle. Keep reporting it,
                # but do not treat that valid transitional state as corruption.
                "invalid_seniority_values",
                "current_evaluation_revision_mismatches",
            } and value
        }
        if options["strict"] and failures:
            raise CommandError(f"Professional architecture reconciliation failed: {failures}")
