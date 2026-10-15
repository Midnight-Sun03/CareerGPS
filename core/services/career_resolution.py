"""Human career names resolved through the existing trusted taxonomy context.

Only one exact identity match opens automatically. Ambiguous identities and
specialisations remain choices, even across sources; cohorts are never merged.
GET callers may fill the short-lived resolver cache, never user database rows.
"""
from dataclasses import dataclass
from hashlib import sha256
import json

from django.core.cache import cache
from django.db.models import Q

from core.models import TaxonomyConcept, TaxonomyRelease
from core.taxonomies.normalization import normalize_term
from core.taxonomies.resolver import build_taxonomy_context


@dataclass
class CareerResolution:
    occupation: object = None
    choices: tuple = ()


def active_occupations():
    return TaxonomyConcept.objects.filter(kind='occupation', release__is_active=True).select_related('release')


def resolve_career(query):
    normalized = normalize_term(query)
    if not normalized:
        return CareerResolution()
    releases = list(TaxonomyRelease.objects.filter(is_active=True).order_by('pk').values_list(
        'pk', 'source', 'version', 'imported_at'))
    digest = sha256(json.dumps([normalized, releases], default=str).encode()).hexdigest()
    key = f'career-resolution-v1:{digest}'
    result = cache.get(key)
    if result is None:
        context = build_taxonomy_context({'occupation': [query]})
        candidates = context.by_label.get(('occupation', normalized), ())
        identity_ids = context.identity_concept_ids_by_label.get(('occupation', normalized), frozenset())
        ids = sorted({item.id for item in candidates})
        exact = len(ids) == 1 and ids[0] in identity_ids
        if not ids:
            # Suggestions are bounded and require an explicit choice, even when
            # there is only one substring match. They are never exact identities.
            ids = list(active_occupations().filter(
                Q(preferred_label__icontains=query) |
                Q(labels__label__icontains=query, labels__language='en')
            ).distinct().order_by('preferred_label', 'pk').values_list('pk', flat=True)[:12])
        result = (exact, ids)
        cache.set(key, result, timeout=300)
    exact, ids = result
    concepts = tuple(active_occupations().filter(pk__in=ids).order_by('preferred_label', 'pk'))
    if exact and len(concepts) == 1:
        return CareerResolution(occupation=concepts[0])
    return CareerResolution(choices=concepts)
