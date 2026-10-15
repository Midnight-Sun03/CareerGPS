from __future__ import annotations

import hashlib
import json
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db.models import Q
from django.utils import timezone

from core.models import Opportunity
from ingestion.ai.payload import build_compact_opportunity_payload
from ingestion.models import OpportunityExtraction


FORMAT_VERSION = "careergps-chatgpt-backfill-export-v1"


class Command(BaseCommand):
    help = (
        "Export opportunity-side CareerGPS data for a source-grounded ChatGPT "
        "Phase 2.4 backfill. User/profile data is never exported."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--output",
            default="careergps_opportunity_ai_export.jsonl",
            help="Output JSONL path.",
        )
        parser.add_argument(
            "--discoverable-only",
            action="store_true",
            help=(
                "Export only the live catalogue opportunities. By default every "
                "Opportunity row is exported so the catalogue snapshot is complete."
            ),
        )
        parser.add_argument(
            "--needs-processing",
            action="store_true",
            help=(
                "Export only opportunities without a READY extraction whose "
                "closing date is unknown or has not passed. CareerGPS Direct "
                "vacancies are excluded because their published employer "
                "requirements are authoritative."
            ),
        )

    def handle(self, *args, **options):
        raise CommandError(
            "Retired: use export_clean_jobs_v1 for the complete job-only Clean-Job v1 export."
        )
        output = Path(options["output"]).expanduser()
        if output.exists() and output.is_dir():
            raise CommandError(f"Output path is a directory: {output}")
        output.parent.mkdir(parents=True, exist_ok=True)

        queryset = Opportunity.objects.all()
        selection = "all_opportunities"
        if options["discoverable_only"]:
            queryset = Opportunity.objects.discoverable()
            selection = "discoverable_only"
        if options["needs_processing"]:
            queryset = queryset.exclude(
                source_platform="CareerGPS Direct"
            ).exclude(ai_extraction__status="ready").filter(
                Q(closing_date__isnull=True) | Q(closing_date__gte=timezone.localdate())
            )
            selection = (
                "needs_processing_and_discoverable_only_excluding_careergps_direct"
                if options["discoverable_only"] else "needs_processing"
            )
        queryset = queryset.select_related("ai_extraction").prefetch_related(
            "source_records__source"
        ).order_by("pk")

        rows = []
        digest_parts = []
        for opportunity in queryset.iterator(chunk_size=500):
            payload = build_compact_opportunity_payload(opportunity)
            digest_parts.append(f"{opportunity.pk}:{payload['input_hash']}")

            try:
                current = opportunity.ai_extraction
            except OpportunityExtraction.DoesNotExist:
                current = None

            existing_ai = None
            if current is not None:
                existing_ai = {
                    "status": current.status,
                    "input_hash": current.input_hash,
                    "extractor": current.extractor,
                    "model_name": current.model_name,
                    "schema_version": current.schema_version,
                    "prompt_version": current.prompt_version,
                    "criterion_count": current.criteria.count(),
                }

            rows.append(
                {
                    "record_type": "opportunity",
                    "opportunity_id": opportunity.pk,
                    # This is the exact Phase 2.4 payload. Only payload.sections
                    # may be used as extraction evidence.
                    "payload": payload,
                    # Audit metadata is deliberately outside payload.sections and
                    # must never be treated as employer evidence.
                    "catalogue_metadata": {
                        "title": opportunity.title,
                        "company": opportunity.company,
                        "source_platform": opportunity.source_platform,
                        "opportunity_kind": opportunity.opportunity_kind,
                        "career_stage": opportunity.career_stage,
                        "scope_status": opportunity.scope_status,
                        "lifecycle_status": opportunity.lifecycle_status,
                        "is_active": opportunity.is_active,
                        "posted_at": (
                            opportunity.posted_at.isoformat() if opportunity.posted_at else None
                        ),
                        "closing_date": (
                            opportunity.closing_date.isoformat() if opportunity.closing_date else None
                        ),
                        "source_records": [
                            {
                                "provider": record.source.code,
                                "source_job_id": record.source_external_id,
                                "listing_url": record.listing_url,
                                "source_url": record.source_url,
                                "apply_url": record.apply_url,
                            }
                            for record in opportunity.source_records.all()
                        ],
                    },
                    "existing_ai": existing_ai,
                }
            )

        digest = hashlib.sha256("\n".join(digest_parts).encode("utf-8")).hexdigest()
        manifest = {
            "record_type": "manifest",
            "format_version": FORMAT_VERSION,
            "generated_at": timezone.now().isoformat(),
            "selection": selection,
            "opportunity_count": len(rows),
            "catalogue_digest": digest,
            "schema_version": settings.OPPORTUNITY_AI_SCHEMA_VERSION,
            "prompt_version": settings.OPPORTUNITY_AI_PROMPT_VERSION,
            "evidence_policy": (
                "Only payload.sections is extraction evidence. catalogue_metadata and "
                "existing_ai are audit metadata only. Never infer missing requirements."
            ),
            "contains_user_or_profile_data": False,
        }

        with output.open("w", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(manifest, ensure_ascii=False) + "\n")
            for row in rows:
                handle.write(json.dumps(row, ensure_ascii=False) + "\n")

        self.stdout.write(
            self.style.SUCCESS(
                f"Exported {len(rows)} opportunities to {output} | "
                f"selection={selection} | digest={digest}"
            )
        )
        self.stdout.write(
            "No user/profile/CV data was exported. Upload this JSONL file to ChatGPT."
        )
