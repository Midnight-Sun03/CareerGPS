from django.core.management.base import BaseCommand
from core.taxonomies.isco import import_isco_package


class Command(BaseCommand):
    help = 'Import the independent official ISCO-08 package.'

    def add_arguments(self, parser):
        parser.add_argument('directory')
        parser.add_argument('--no-replace', action='store_true')

    def handle(self, *args, **options):
        release = import_isco_package(
            options['directory'], replace=not options['no_replace'])
        self.stdout.write(self.style.SUCCESS(f'Imported {release}'))

