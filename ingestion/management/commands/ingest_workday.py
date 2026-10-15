from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from ingestion.adapters.workday import WorkdayAdapter
from ingestion.models import WorkdayBoard
from ingestion.pipeline import run_adapter
from ._output import add_explain_arguments, explain_options, render_summary


class Command(BaseCommand):
    help = (
        "Ingest every public South African posting from configured public Workday boards."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--site",
            help="Only ingest this Workday site segment (e.g. ABSAcareersite).",
        )
        parser.add_argument(
            "--tenant",
            help="Only ingest boards for this Workday tenant segment (e.g. absa).",
        )
        parser.add_argument(
            "--max-jobs", type=int,
            help="Bound postings per board (useful for validation).",
        )
        parser.add_argument("--dry-run", action="store_true")
        add_explain_arguments(parser)

    def handle(self, *args, **options):
        boards = WorkdayBoard.objects.select_related("source").filter(
            active=True, source__active=True,
        )
        if options.get("site"):
            boards = boards.filter(site=options["site"])
        if options.get("tenant"):
            boards = boards.filter(tenant=options["tenant"])
        if not boards.exists():
            raise CommandError(
                "No matching active Workday boards are configured."
            )
        diagnostics = explain_options(options)
        for board in boards:
            counters = run_adapter(
                WorkdayAdapter(board, max_jobs=options.get("max_jobs")),
                board.source,
                dry_run=options["dry_run"],
                **diagnostics,
            )
            if not options["dry_run"]:
                board.last_checked_at = timezone.now()
                if counters["errors_count"]:
                    board.last_error = "\n".join(
                        str(item.get("error", item)) for item in counters.get("errors", [])
                    )[:4000]
                else:
                    board.last_success_at = timezone.now()
                    board.last_error = ""
                board.save(
                    update_fields=["last_checked_at", "last_success_at", "last_error"]
                )
            render_summary(self, board.source.code, counters, options["dry_run"])
