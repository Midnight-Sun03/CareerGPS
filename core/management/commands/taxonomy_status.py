from django.core.management.base import BaseCommand
from django.db.models import Count

from core.models import TaxonomyRelease


class Command(BaseCommand):
    help = "Show locally imported CareerGPS taxonomy releases and counts."

    def handle(self, *args, **options):
        releases = TaxonomyRelease.objects.annotate(
            concept_count=Count("concepts", distinct=True),
            label_count=Count("concepts__labels", distinct=True),
            relation_count=Count(
                "concepts__outgoing_taxonomy_relations", distinct=True
            ),
        )
        if not releases:
            self.stdout.write("No taxonomy releases imported yet.")
            return
        for release in releases:
            active = "ACTIVE" if release.is_active else "inactive"
            self.stdout.write(
                f"{release.source.upper()} {release.version} [{active}] "
                f"concepts={release.concept_count} labels={release.label_count} "
                f"relations={release.relation_count}"
            )
