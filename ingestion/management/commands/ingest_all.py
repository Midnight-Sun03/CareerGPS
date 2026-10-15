from django.core.management import call_command
from django.core.management.base import BaseCommand

from ._output import add_explain_arguments, explain_options


class Command(BaseCommand):
    help = "Run all Greenhouse boards and direct-company scrapers independently."

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true")
        parser.add_argument("--max-jobs", type=int)
        add_explain_arguments(parser)

    def handle(self, *args, **options):
        diagnostics = explain_options(options)
        commands = []
        commands.extend([
            ("ingest_greenhouse", {"max_jobs": options.get("max_jobs")}),
            ("ingest_smartrecruiters", {"max_jobs": options.get("max_jobs")}),
            ("ingest_workday", {"max_jobs": options.get("max_jobs")}),
            ("ingest_company_scrapers", {"max_jobs": options.get("max_jobs")}),
        ])
        for name, kwargs in commands:
            if options["dry_run"]:
                kwargs["dry_run"] = True
            kwargs.update(diagnostics)
            try:
                call_command(name, stdout=self.stdout, stderr=self.stderr, **kwargs)
            except Exception as exc:
                # A provider failure must not prevent independent sources from running.
                self.stderr.write(self.style.ERROR(f"{name} failed: {exc}"))
        if not options["dry_run"]:
            # Materialize trusted title assignments for newly ingested vacancies
            # before they are used as structured desired-career discovery signals.
            call_command("maintain_career_market", assign_only=True,
                         stdout=self.stdout, stderr=self.stderr)
