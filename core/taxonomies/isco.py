from __future__ import annotations

from pathlib import Path

from django.db import transaction

from core.models import TaxonomyConcept, TaxonomyLabel, TaxonomyRelation, TaxonomyRelease
from .normalization import normalize_term
from .xlsx import iter_rows


ISCO_SOURCE_URL = 'https://www.ilo.org/publications/international-standard-classification-occupations-2008-isco-08-structure'
EXPECTED_COUNTS = {1: 10, 2: 43, 3: 130, 4: 436}


def _clean(value):
    return ' '.join(str(value or '').split())


def import_isco_package(directory, *, version='08', activate=True, replace=True):
    directory = Path(directory)
    structure_path = directory / 'ISCO-08 EN Structure and definitions.xlsx'
    index_path = directory / 'ISCO-08 -88 EN Index.xlsx'
    rows = list(iter_rows(structure_path, 'ISCO-08 EN Struct and defin'))
    headers = rows[0]
    items = [dict(zip(headers, row)) for row in rows[1:] if any(row)]
    counts = {level: 0 for level in EXPECTED_COUNTS}
    for row in items:
        level = int(row['Level'])
        counts[level] += 1
    if counts != EXPECTED_COUNTS:
        raise ValueError(f'ISCO-08 structure audit failed: expected {EXPECTED_COUNTS}, found {counts}')

    with transaction.atomic():
        existing = TaxonomyRelease.objects.filter(source='isco', version=version).first()
        if existing and replace:
            existing.delete()
        release, _ = TaxonomyRelease.objects.get_or_create(
            source='isco', version=version, defaults={'source_url': ISCO_SOURCE_URL})
        TaxonomyConcept.objects.bulk_create([
            TaxonomyConcept(
                release=release,
                external_id=f'isco:{row["ISCO 08 Code"]}',
                code=str(row['ISCO 08 Code']),
                kind='occupation_group',
                preferred_label=_clean(row['Title EN']),
                normalized_label=normalize_term(row['Title EN']),
                definition=str(row.get('Definition') or '').strip(),
                metadata={
                    'level': int(row['Level']),
                    'tasks': str(row.get('Tasks include') or '').strip(),
                    'included_occupations': str(row.get('Included occupations') or '').strip(),
                    'excluded_occupations': str(row.get('Excluded occupations') or '').strip(),
                    'notes': str(row.get('Notes') or '').strip(),
                },
            ) for row in items
        ], batch_size=1000)
        by_code = {row.code: row for row in TaxonomyConcept.objects.filter(release=release)}
        labels = [TaxonomyLabel(
            concept=concept, label=concept.preferred_label,
            normalized_label=concept.normalized_label, label_type='preferred')
            for concept in by_code.values()]
        index_rows = list(iter_rows(index_path, 'ISCO-08-88-Index'))
        for row in index_rows[1:]:
            if len(row) < 3:
                continue
            concept = by_code.get(str(row[0]).strip())
            label = _clean(row[2])
            if concept and label:
                labels.append(TaxonomyLabel(
                    concept=concept, label=label,
                    normalized_label=normalize_term(label), label_type='title_index'))
        TaxonomyLabel.objects.bulk_create(labels, batch_size=3000, ignore_conflicts=True)

        relations = []
        for code, concept in by_code.items():
            if len(code) == 1:
                continue
            parent_code = code[:-1]
            parent = by_code.get(parent_code)
            if parent:
                relations.append(TaxonomyRelation(
                    subject=concept, object=parent, relation_type='broader',
                    metadata={'relationship': 'isco_hierarchy', 'release': version}))
        TaxonomyRelation.objects.bulk_create(relations, batch_size=1000)

        # Group-level skill information is retained as context metadata only.
        skills_path = directory / 'ISCO-08 88 EN Skills .xlsx'
        skill_rows = list(iter_rows(skills_path, 'ISCO_SKILLS'))
        current_skill = None
        for row in skill_rows[2:]:
            if len(row) > 1 and row[0]:
                current_skill = {'level': str(row[0]), 'label': _clean(row[1])}
            if len(row) > 7 and row[6] and str(row[6]) in by_code:
                concept = by_code[str(row[6])]
                metadata = dict(concept.metadata)
                metadata['skill_level_context'] = current_skill
                concept.metadata = metadata
                concept.save(update_fields=['metadata'])

        release.notes = (
            f'ISCO-08 audited import: major={counts[1]}, sub_major={counts[2]}, '
            f'minor={counts[3]}, unit={counts[4]}, '
            f'labels={TaxonomyLabel.objects.filter(concept__release=release).count()}'
        )
        release.save(update_fields=['notes'])
        if activate:
            TaxonomyRelease.objects.filter(source='isco', is_active=True).exclude(pk=release.pk).update(is_active=False)
            release.is_active = True
            release.save(update_fields=['is_active', 'imported_at'])
        return release

