from pathlib import Path
import tempfile

import requests
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from core.taxonomies.ofo import OFO_2021_OFFICIAL_URL, import_ofo_file


class Command(BaseCommand):
    help = "Import South Africa's OFO taxonomy from the official DHET .xls or a CSV export."

    def add_arguments(self, parser):
        parser.add_argument("path", nargs="?")
        parser.add_argument("--taxonomy-version", default="2021")
        parser.add_argument("--download-official", action="store_true")
        parser.add_argument("--no-activate", action="store_true")
        parser.add_argument("--keep-existing", action="store_true")

    def handle(self, *args, **options):
        path = options.get("path")
        temp_path = None
        try:
            if options["download_official"]:
                response = requests.get(
                    OFO_2021_OFFICIAL_URL,
                    timeout=60,
                    headers={"User-Agent": settings.INGESTION_USER_AGENT},
                )
                response.raise_for_status()
                if len(response.content) < 1024:
                    raise CommandError(
                        "The DHET OFO download returned an unexpectedly small file."
                    )
                handle = tempfile.NamedTemporaryFile(suffix=".xls", delete=False)
                handle.write(response.content)
                handle.close()
                temp_path = Path(handle.name)
                path = temp_path
            if not path:
                raise CommandError("Provide a path or use --download-official.")
            release = import_ofo_file(
                path,
                version=options["taxonomy_version"],
                activate=not options["no_activate"],
                replace=not options["keep_existing"],
            )
        except (OSError, ValueError, RuntimeError, requests.RequestException) as exc:
            raise CommandError(str(exc)) from exc
        finally:
            if temp_path:
                temp_path.unlink(missing_ok=True)
        self.stdout.write(self.style.SUCCESS(
            f"Imported OFO {release.version}; active={release.is_active}."
        ))
