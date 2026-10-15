"""Register validated public Greenhouse boards without ingesting their jobs."""

import re
from html import unescape
from pathlib import Path
from urllib.parse import urlparse

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.db.models import Q
from requests import RequestException

from ingestion.adapters.base import PoliteHttpClient
from ingestion.adapters.greenhouse import GreenhouseAdapter
from ingestion.models import GreenhouseBoard, IngestionSource


TOKEN_PATTERN = re.compile(r'^[a-z0-9][a-z0-9_-]*$')
TEST_BOARD_PATTERN = re.compile(r'\b(?:test|testing|sandbox|demo|sample|staging)\b', re.I)
SOURCE_PRIORITY = 15  # The existing Greenhouse source convention.


def source_identity(token):
    return f'greenhouse-{token}', GreenhouseAdapter.API_ROOT.format(token=token)


def existing_board_or_source(token):
    """Use canonical token, source code and API URL, never display name."""
    board = GreenhouseBoard.objects.select_related('source').filter(board_token__iexact=token).first()
    if board is not None:
        return 'board', board
    code, root = source_identity(token)
    source = IngestionSource.objects.filter(
        Q(code__iexact=code) | Q(base_url__iexact=root) | Q(base_url__iexact=root + '/')
    ).first()
    return ('source', source) if source is not None else (None, None)


def validate_board(token, client):
    """Use the public endpoints also used by the configured Greenhouse adapter."""
    _, root = source_identity(token)
    board = client.get_json(root, respect_robots=False)
    if not isinstance(board, dict):
        raise ValueError('board response is not an object')
    name = board.get('name')
    if not isinstance(name, str):
        raise ValueError('board has no canonical organization name')
    name = ' '.join(unescape(name).split())
    if not name or '<' in name or '>' in name:
        raise ValueError('board has an invalid organization name')
    if TEST_BOARD_PATTERN.search(name) or TEST_BOARD_PATTERN.search(token):
        raise ValueError('obvious test or sandbox board')
    if len(name) > 160 or len(name + ' Greenhouse') > 120:
        raise ValueError('board name exceeds existing model limits')

    listing = client.get_json(root + '/jobs', respect_robots=False)
    if not isinstance(listing, dict) or not isinstance(listing.get('jobs'), list):
        raise ValueError('jobs response is malformed')
    for job in listing['jobs']:
        if not isinstance(job, dict) or not isinstance(job.get('id'), int) or isinstance(job['id'], bool):
            raise ValueError('jobs response contains an invalid job ID')
        if not isinstance(job.get('title'), str) or not job['title'].strip():
            raise ValueError('jobs response contains a job without a title')
        url = job.get('absolute_url')
        parsed = urlparse(url) if isinstance(url, str) else None
        if not parsed or parsed.scheme not in {'http', 'https'} or not parsed.netloc:
            raise ValueError('jobs response contains an invalid job URL')
    return name


@transaction.atomic
def register_board(token, name):
    """Create both Admin-visible records or neither; never repair existing data implicitly."""
    kind, existing = existing_board_or_source(token)
    if kind:
        return kind, existing
    code, root = source_identity(token)
    source = IngestionSource(
        name=f'{name} Greenhouse', code=code, source_type='api', active=True,
        company=name, base_url=root, priority=SOURCE_PRIORITY,
    )
    source.full_clean()
    source.save()
    board = GreenhouseBoard(
        source=source, company_name=name, board_token=token,
        careers_url=f'https://job-boards.greenhouse.io/{token}',
        country_codes=['ZA'], active=True,
    )
    board.full_clean()
    board.save()
    return 'created', board


class Command(BaseCommand):
    help = 'Validate public Greenhouse board tokens and register complete source/board pairs.'

    def add_arguments(self, parser):
        parser.add_argument('token_file', help='UTF-8 file with one Greenhouse board token per line.')
        parser.add_argument('--dry-run', action='store_true', help='Validate without creating records.')

    def handle(self, *args, **options):
        try:
            lines = Path(options['token_file']).read_text(encoding='utf-8-sig').splitlines()
        except OSError as exc:
            raise CommandError(f'Cannot read token file: {exc}') from exc

        tokens, seen, duplicates = [], set(), []
        for line in lines:
            token = line.split('#', 1)[0].strip().lower()
            if not token:
                continue
            if token in seen:
                duplicates.append(token)
            else:
                seen.add(token)
                tokens.append(token)
        if not tokens:
            raise CommandError('The token file contains no board tokens.')

        counts = {'existing': 0, 'valid_new': 0, 'created': 0, 'failed': 0}
        client = PoliteHttpClient(delay=0.25)
        self.stdout.write(f"Mode: {'DRY RUN' if options['dry_run'] else 'IMPORT'}")
        for token in tokens:
            if not TOKEN_PATTERN.fullmatch(token) or len(token) > 120 or len(source_identity(token)[0]) > 80:
                counts['failed'] += 1
                self.stdout.write(f'FAILED {token} — invalid or overlong board token')
                continue
            kind, existing = existing_board_or_source(token)
            if kind == 'board':
                counts['existing'] += 1
                self.stdout.write(f'SKIPPED {token} — already exists ({existing.company_name})')
                continue
            if kind == 'source':
                counts['failed'] += 1
                self.stdout.write(f'FAILED {token} — source identity already exists without its matching board; manual review required')
                continue
            try:
                name = validate_board(token, client)
            except (RequestException, ValueError, TypeError) as exc:
                counts['failed'] += 1
                self.stdout.write(f'FAILED {token} — board validation failed: {exc}')
                continue
            counts['valid_new'] += 1
            if options['dry_run']:
                self.stdout.write(f'VALID {token} — {name} — would create source and board')
                continue
            try:
                result, board = register_board(token, name)
            except Exception as exc:
                # A failed board must not leave a source behind or stop later tokens.
                counts['failed'] += 1
                self.stdout.write(f'FAILED {token} — registration rolled back: {exc}')
                continue
            if result == 'created':
                counts['created'] += 1
                self.stdout.write(f'CREATED {token} — {name} — {board.source.code}')
            elif result == 'board':
                counts['existing'] += 1
                self.stdout.write(f'SKIPPED {token} — already exists (created concurrently)')
            else:
                counts['failed'] += 1
                self.stdout.write(f'FAILED {token} — source identity appeared concurrently; manual review required')

        self.stdout.write(
            f"SUMMARY candidates={len(tokens)} existing={counts['existing']} "
            f"valid_new={counts['valid_new']} created={counts['created']} "
            f"invalid_or_failed={counts['failed']} duplicate_input={len(duplicates)}"
        )
        if duplicates:
            self.stdout.write('DUPLICATE INPUT ' + ', '.join(duplicates))
