from __future__ import annotations

import csv
from collections import Counter
from datetime import date
from pathlib import Path

from django.db import transaction

from core.models import TaxonomyConcept, TaxonomyCrosswalk, TaxonomyRelease


RELATION_MAP = {
    'exactMatch': 'exact', 'closeMatch': 'close', 'broadMatch': 'broad',
    'narrowMatch': 'narrow', 'exactISCO': 'exact_isco',
}


def _mapping_rows(path):
    handle = Path(path).open('r', encoding='utf-8-sig', newline='')
    reader = csv.reader(handle)
    for row in reader:
        if row and row[0] == 'O*NET Id':
            headers = row
            break
    else:
        handle.close()
        raise ValueError('Crosswalk CSV header was not found.')
    try:
        for row in reader:
            if any(row):
                yield dict(zip(headers, row))
    finally:
        handle.close()


def import_esco_onet_crosswalk(path, *, replace=True):
    rows = list(_mapping_rows(path))
    onet = TaxonomyRelease.objects.get(source='onet', version='31.0')
    esco = TaxonomyRelease.objects.get(source='esco', version='1.2.1')
    isco = TaxonomyRelease.objects.get(source='isco', version='08')
    onet_by_code = {row.code: row for row in TaxonomyConcept.objects.filter(release=onet)}
    esco_by_id = {row.external_id: row for row in TaxonomyConcept.objects.filter(release=esco)}
    isco_by_code = {row.code: row for row in TaxonomyConcept.objects.filter(release=isco)}
    exact_source_counts = Counter(
        row['O*NET Id'] for row in rows if row['Type of Match'] == 'exactMatch')

    with transaction.atomic():
        if replace:
            TaxonomyCrosswalk.objects.filter(
                mapping_name='ESCO-O*NET official crosswalk', mapping_version='1').delete()
        objects = []
        missing = 0
        for index, row in enumerate(rows, start=1):
            source = onet_by_code.get(row['O*NET Id'])
            if source is None:
                missing += 1
                continue
            relation = RELATION_MAP.get(row['Type of Match'])
            if relation is None:
                raise ValueError(f'Unsupported crosswalk relation: {row["Type of Match"]}')
            target_external = row['ESCO or ISCO URI']
            if relation == 'exact_isco':
                code = target_external.rsplit('C', 1)[-1]
                target_release = isco
                target = isco_by_code.get(code)
                approval = 'approved_identity' if target else 'quarantined'
            else:
                target_release = esco
                target = esco_by_id.get(target_external)
                if target is None:
                    approval = 'quarantined'
                elif relation == 'exact':
                    approval = ('approved_identity'
                                if exact_source_counts[row['O*NET Id']] == 1
                                else 'quarantined')
                else:
                    approval = 'approved_related'
            if target is None:
                missing += 1
            objects.append(TaxonomyCrosswalk(
                source_release=onet,
                source_concept=source,
                source_external_id=row['O*NET Id'],
                target_release=target_release,
                target_concept=target,
                target_external_id=target_external,
                relation_type=relation,
                direction='forward',
                approval_status=approval,
                mapping_name='ESCO-O*NET official crosswalk',
                mapping_version='1',
                published_at=date(2022, 9, 1),
                source_reference=f'csv-row-{index + 17}',
                provenance={
                    'mapping_source_release': 'O*NET-SOC 2019',
                    'mapping_target_release': 'ESCO 1.1.0' if relation != 'exact_isco' else 'ISCO-08',
                    'source_title': row['O*NET Title'],
                    'target_title': row['ESCO or ISCO Title'],
                    'original_relation': row['Type of Match'],
                },
            ))
        TaxonomyCrosswalk.objects.bulk_create(objects, batch_size=2000)
        counts = Counter(TaxonomyCrosswalk.objects.filter(
            mapping_name='ESCO-O*NET official crosswalk', mapping_version='1'
        ).values_list('approval_status', flat=True))
        return {'rows': len(objects), 'missing_current_identifier': missing, **counts}

