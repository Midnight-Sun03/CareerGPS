"""Refresh the candidate-facing occupational relationship policy data.

This is a read-only verification of the imported ESCO relation class,
followed by an idempotent write of the approved rule.  It never rewrites an
imported taxonomy release.

The ESCO bundle stores `broader` rows from two different sources:

* `metadata.relationship == 'source_broader'` rows from the occupation
  pillar file, whose object is another *occupation* -- a direct
  occupation-to-occupation link;
* `metadata.relationship == 'isco_group'` rows and `source_broader` rows
  whose object is an `occupation_group` -- broad group membership, which is
  not a direct occupational relationship and must not be admitted.

The command reports the split and then seeds a single rule narrowed to the
verified direct class.  With --dry-run it reports only.
"""
from collections import Counter

from django.core.management.base import BaseCommand
from django.db import transaction

from core.models import OccupationRelationshipRule, TaxonomyConcept, TaxonomyRelation
from core.professional_policies import CANDIDATE_RELATIONSHIP_POLICY_VERSION

RULE_NAME = 'esco-occupation-pillar-direct-broader'
RULE_FILTER = {
    'subject_kind': 'occupation',
    'object_kind': 'occupation',
    'metadata_relationship': 'source_broader',
}


class Command(BaseCommand):
    help = 'Verify the ESCO direct occupation-link class and refresh the rule.'

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true')

    def handle(self, *args, **options):
        esco = TaxonomyConcept.objects.filter(
            release__source='esco', kind='occupation').values_list(
                'pk', flat=True)
        rows = TaxonomyRelation.objects.filter(
            relation_type='broader', subject__in=esco).select_related(
            'object', 'subject__release')
        split = Counter()
        for row in rows:
            key = (
                (row.metadata or {}).get('relationship'),
                row.object.kind,
            )
            split[key] += 1

        self.stdout.write('ESCO occupation "broader" rows, by (provenance, object kind):')
        for key, count in sorted(split.items(), key=lambda item: -item[1]):
            self.stdout.write(f'  {key}: {count}')

        direct = split.get(('source_broader', 'occupation'), 0)
        excluded = sum(
            count for key, count in split.items()
            if key != ('source_broader', 'occupation'))
        self.stdout.write(
            f'  verified direct occupation links: {direct}; '
            f'group/broad links excluded: {excluded}')

        if not direct:
            self.stdout.write(self.style.WARNING(
                'No verified direct ESCO occupation links found; nothing seeded.'))
            return

        if options['dry_run']:
            self.stdout.write(self.style.SUCCESS('Dry run: no policy data written.'))
            return

        with transaction.atomic():
            rule, created = OccupationRelationshipRule.objects.update_or_create(
                policy_version=CANDIDATE_RELATIONSHIP_POLICY_VERSION,
                name=RULE_NAME,
                defaults={
                    'evidence_source': 'taxonomy_relation',
                    'taxonomy_source': 'esco',
                    'evidence_relation_type': 'broader',
                    'candidate_relationship_type': 'SAFE_BROADER_NARROWER',
                    # ESCO's occupation-pillar `broader` edge asserts hierarchy
                    # adjacency.  Which side it calls broader is an import
                    # convention, not a career judgement, so relatedness is
                    # read in both directions.
                    'direction': 'bidirectional',
                    'candidate_class': 'RELATED',
                    'admission_allowed': True,
                    'rationale': (
                        'ESCO broaderRelationsOccPillar rows between two '
                        'occupation concepts are direct occupation-to-occupation '
                        'links verified by provenance and object kind. ISCO group '
                        'membership rows, occupation_group objects, skill links, '
                        'contextual links and unapproved crosswalks are excluded.'),
                    'provenance': {
                        'evidence_filter': RULE_FILTER,
                        'verified_direct_link_count': direct,
                        'excluded_row_count': excluded,
                    },
                    'active': True,
                },
            )
        self.stdout.write(self.style.SUCCESS(
            f'{"Seeded" if created else "Updated"} {RULE_NAME} for '
            f'policy {CANDIDATE_RELATIONSHIP_POLICY_VERSION} '
            f'({direct} verified direct links).'))