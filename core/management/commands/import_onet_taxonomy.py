from django.core.management.base import BaseCommand
from core.taxonomies.onet import import_onet_directory


class Command(BaseCommand):
    help = 'Import the frozen official O*NET 31.0 CSV package.'

    def add_arguments(self, parser):
        parser.add_argument('directory')
        parser.add_argument('--no-replace', action='store_true')

    def handle(self, *args, **options):
        release = import_onet_directory(
            options['directory'], replace=not options['no_replace'])
        self.stdout.write(self.style.SUCCESS(f'Imported {release}'))

