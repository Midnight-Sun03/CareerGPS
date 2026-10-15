from django.core.management.base import BaseCommand, CommandError

from core.taxonomies.esco import import_esco_csv_bundle


class Command(BaseCommand):
    help = "Import an official English ESCO CSV bundle into CareerGPS."

    def add_arguments(self, parser):
        parser.add_argument("directory")
        parser.add_argument("--taxonomy-version", required=True)
        parser.add_argument("--no-activate", action="store_true")
        parser.add_argument("--keep-existing", action="store_true")

    def handle(self, *args, **options):
        try:
            release = import_esco_csv_bundle(
                options["directory"],
                version=options["taxonomy_version"],
                activate=not options["no_activate"],
                replace=not options["keep_existing"],
            )
        except (FileNotFoundError, ValueError) as exc:
            raise CommandError(str(exc)) from exc
        self.stdout.write(self.style.SUCCESS(
            f"Imported ESCO {release.version}; active={release.is_active}."
        ))
