from django.core.management.base import BaseCommand
from core.taxonomies.crosswalks import import_esco_onet_crosswalk


class Command(BaseCommand):
    help = 'Import the official versioned ESCO/O*NET/ISCO crosswalk rows.'

    def add_arguments(self, parser):
        parser.add_argument('path')
        parser.add_argument('--no-replace', action='store_true')

    def handle(self, *args, **options):
        counts = import_esco_onet_crosswalk(
            options['path'], replace=not options['no_replace'])
        self.stdout.write(self.style.SUCCESS(f'Imported crosswalk: {counts}'))
