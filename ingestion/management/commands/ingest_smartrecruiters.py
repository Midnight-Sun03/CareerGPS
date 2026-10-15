from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from ingestion.adapters.smartrecruiters import SmartRecruitersAdapter
from ingestion.models import SmartRecruitersCompany
from ingestion.pipeline import run_adapter
from ._output import add_explain_arguments, explain_options, render_summary


class Command(BaseCommand):
    help = (
        "Ingest every public South African posting from configured SmartRecruiters companies."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--company",
            help="Only ingest this SmartRecruiters companyIdentifier.",
        )
        parser.add_argument(
            "--max-jobs", type=int,
            help="Bound postings per company (useful for validation).",
        )
        parser.add_argument("--dry-run", action="store_true")
        add_explain_arguments(parser)

    def handle(self, *args, **options):
        companies = SmartRecruitersCompany.objects.select_related("source").filter(
            active=True, source__active=True,
        )
        if options.get("company"):
            companies = companies.filter(company_identifier=options["company"])
        if not companies.exists():
            raise CommandError(
                "No matching active SmartRecruiters companies are configured."
            )
        diagnostics = explain_options(options)
        for company in companies:
            counters = run_adapter(
                SmartRecruitersAdapter(company, max_jobs=options.get("max_jobs")),
                company.source,
                dry_run=options["dry_run"],
                **diagnostics,
            )
            if not options["dry_run"]:
                company.last_checked_at = timezone.now()
                if counters["errors_count"]:
                    company.last_error = "\n".join(
                        str(item.get("error", item)) for item in counters.get("errors", [])
                    )[:4000]
                else:
                    company.last_success_at = timezone.now()
                    company.last_error = ""
                company.save(
                    update_fields=["last_checked_at", "last_success_at", "last_error"]
                )
            render_summary(self, company.source.code, counters, options["dry_run"])
