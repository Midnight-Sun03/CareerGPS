from django.core.management.base import BaseCommand, CommandError

from ingestion.models import OpportunityExtraction


class Command(BaseCommand):
    help = "Print the stored Phase 2.4 AI criteria for one opportunity."

    def add_arguments(self, parser):
        parser.add_argument("opportunity_id", type=int)

    def handle(self, *args, **options):
        opportunity_id = options["opportunity_id"]
        try:
            extraction = (
                OpportunityExtraction.objects.select_related("opportunity")
                .prefetch_related("criteria")
                .get(opportunity_id=opportunity_id)
            )
        except OpportunityExtraction.DoesNotExist as exc:
            raise CommandError(
                f"No AI extraction exists for opportunity {opportunity_id}."
            ) from exc

        self.stdout.write(
            f"opportunity={opportunity_id} title={extraction.opportunity.title!r} "
            f"status={extraction.status} completeness={extraction.source_completeness} "
            f"model={extraction.model_name} prompt={extraction.prompt_version}"
        )
        for criterion in extraction.criteria.all().order_by("id"):
            years = ""
            if criterion.minimum_years is not None or criterion.maximum_years is not None:
                years = f" | years={criterion.minimum_years}-{criterion.maximum_years}"
            context = (criterion.metadata or {}).get("context", "")
            context_text = f" | context={context}" if context else ""
            self.stdout.write(
                f"{criterion.criterion_type} | {criterion.requirement_level} | "
                f"{criterion.raw_value}{years}{context_text} | EVIDENCE: {criterion.evidence_text}"
            )
