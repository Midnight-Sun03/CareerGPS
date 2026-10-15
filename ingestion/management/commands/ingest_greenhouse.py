from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from ingestion.adapters.greenhouse import GreenhouseAdapter
from ingestion.models import GreenhouseBoard
from ingestion.pipeline import run_adapter
from ._output import add_explain_arguments, explain_options, render_summary


class Command(BaseCommand):
    help = "Ingest every published job from configured active Greenhouse boards."

    def add_arguments(self, parser):
        parser.add_argument("--board", help="Only ingest this board token.")
        parser.add_argument("--max-jobs", type=int, help="Bound jobs per board (useful for validation).")
        parser.add_argument("--dry-run", action="store_true")
        add_explain_arguments(parser)

    def handle(self, *args, **options):
        boards = GreenhouseBoard.objects.select_related("source").filter(active=True, source__active=True)
        if options.get("board"):
            boards = boards.filter(board_token=options["board"])
        if not boards.exists():
            raise CommandError("No matching active Greenhouse boards are configured.")
        diagnostics = explain_options(options)
        for board in boards:
            counters = run_adapter(
                GreenhouseAdapter(board, max_jobs=options.get("max_jobs")),
                board.source,
                dry_run=options["dry_run"],
                **diagnostics,
            )
            if not options["dry_run"]:
                board.last_checked_at = timezone.now()
                if counters["errors_count"]:
                    board.last_error = "\n".join(item["error"] for item in counters.get("errors", []))[:4000]
                else:
                    board.last_success_at = timezone.now()
                    board.last_error = ""
                board.save(update_fields=["last_checked_at", "last_success_at", "last_error"])
            render_summary(self, board.source.code, counters, options["dry_run"])
