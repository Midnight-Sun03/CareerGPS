from __future__ import annotations

import csv
from collections import defaultdict
from decimal import Decimal, InvalidOperation
from pathlib import Path

from django.db import transaction

from core.models import (
    TaxonomyConcept, TaxonomyLabel, TaxonomyOccupationContext,
    TaxonomyRelation, TaxonomyRelease,
)
from .normalization import normalize_term


ONET_SOURCE_URL = 'https://www.onetcenter.org/database.html'
EXPECTED_OCCUPATIONS = 1016


def _rows(path):
    with Path(path).open('r', encoding='utf-8-sig', newline='') as handle:
        yield from csv.DictReader(handle)


def _clean(value):
    return ' '.join(str(value or '').split())


def import_onet_directory(directory, *, version='31.0', activate=True, replace=True):
    directory = Path(directory)
    occupations = list(_rows(directory / 'occupation_data.csv'))
    if len(occupations) != EXPECTED_OCCUPATIONS:
        raise ValueError(
            f'O*NET {version} audit failed: expected {EXPECTED_OCCUPATIONS} occupations, '
            f'found {len(occupations)}.')
    with transaction.atomic():
        existing = TaxonomyRelease.objects.filter(source='onet', version=version).first()
        if existing and replace:
            existing.delete()
        release, _ = TaxonomyRelease.objects.get_or_create(
            source='onet', version=version,
            defaults={'source_url': ONET_SOURCE_URL})
        TaxonomyConcept.objects.bulk_create([
            TaxonomyConcept(
                release=release,
                external_id=row['O*NET-SOC Code'],
                code=row['O*NET-SOC Code'],
                kind='occupation',
                preferred_label=_clean(row['Title']),
                normalized_label=normalize_term(row['Title']),
                definition=_clean(row['Description']),
                metadata={'source_package': 'db_31_0_csv.zip', 'release': version},
            ) for row in occupations
        ], batch_size=1000)
        by_code = {
            row.code: row for row in TaxonomyConcept.objects.filter(release=release)
        }
        labels = []
        for concept in by_code.values():
            labels.append(TaxonomyLabel(
                concept=concept, label=concept.preferred_label,
                normalized_label=concept.normalized_label, label_type='preferred'))
        for filename, column, label_type in (
            ('job_titles.csv', 'Job Title', 'alternative'),
            ('sample_of_reported_titles.csv', 'Reported Job Title', 'reported'),
        ):
            for row in _rows(directory / filename):
                concept = by_code.get(row.get('O*NET-SOC Code'))
                label = _clean(row.get(column))
                if concept and label:
                    labels.append(TaxonomyLabel(
                        concept=concept, label=label,
                        normalized_label=normalize_term(label), label_type=label_type))
        TaxonomyLabel.objects.bulk_create(labels, batch_size=3000, ignore_conflicts=True)

        relations = []
        for row in _rows(directory / 'related_occupations.csv'):
            subject = by_code.get(row.get('O*NET-SOC Code'))
            target = by_code.get(row.get('Related O*NET-SOC Code'))
            if subject and target:
                relations.append(TaxonomyRelation(
                    subject=subject, object=target, relation_type='contextual_related',
                    metadata={
                        'tier': _clean(row.get('Relatedness Tier')),
                        'index': _clean(row.get('Index')),
                        'authority': 'onet_31_context_only',
                    }))
        TaxonomyRelation.objects.bulk_create(relations, batch_size=3000, ignore_conflicts=True)

        context_rows = []
        context_files = (
            ('essential_skills.csv', 'skill', 'essential_skill'),
            ('transferable_skills.csv', 'skill', 'transferable_skill'),
            ('knowledge.csv', 'knowledge', 'knowledge'),
            ('work_activities.csv', 'activity', 'work_activity'),
        )
        seen = set()
        for filename, context_type, domain in context_files:
            for row in _rows(directory / filename):
                concept = by_code.get(row.get('O*NET-SOC Code'))
                if not concept:
                    continue
                key = (concept.pk, context_type, row.get('Element ID'), row.get('Scale ID'))
                if key in seen:
                    continue
                seen.add(key)
                try:
                    value = Decimal(row['Data Value']) if row.get('Data Value') else None
                except InvalidOperation:
                    value = None
                context_rows.append(TaxonomyOccupationContext(
                    occupation=concept,
                    context_type=context_type,
                    element_id=_clean(row.get('Element ID')),
                    element_name=_clean(row.get('Element Name')),
                    scale_id=_clean(row.get('Scale ID')),
                    value=value,
                    provenance={'domain': domain, 'source_file': filename},
                ))
        TaxonomyOccupationContext.objects.bulk_create(
            context_rows, batch_size=3000, ignore_conflicts=True)

        release.notes = (
            f'O*NET audited import: occupations={len(by_code)}, '
            f'labels={TaxonomyLabel.objects.filter(concept__release=release).count()}, '
            f'related_context={TaxonomyRelation.objects.filter(subject__release=release, relation_type="contextual_related").count()}, '
            f'context_rows={TaxonomyOccupationContext.objects.filter(occupation__release=release).count()}'
        )
        release.save(update_fields=['notes'])
        if activate:
            TaxonomyRelease.objects.filter(source='onet', is_active=True).exclude(pk=release.pk).update(is_active=False)
            release.is_active = True
            release.save(update_fields=['is_active', 'imported_at'])
        return release

