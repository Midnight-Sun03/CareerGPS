from __future__ import annotations

import hashlib
import json
import re

from django.db import transaction

from core.models import (
    CareerPreference, CareerTargetIdentity, CareerTargetResolution,
    OpportunityCleanRevision, OpportunityOccupationIdentity,
    OpportunityOccupationResolution, TaxonomyConcept, TaxonomyCrosswalk,
    TaxonomyLabel, TaxonomyRelease, TaxonomyReleaseSet,
)
from core.professional_policies import (
    OPPORTUNITY_RESOLVER_VERSION, TARGET_RESOLVER_VERSION,
)
from core.taxonomies.normalization import normalize_term


IDENTITY_LABEL_TYPES = ('preferred', 'alternative', 'specialisation', 'reported', 'title_index')
HARMLESS_MODIFIERS = frozenset({
    'junior', 'jr', 'graduate', 'trainee', 'intern', 'internship', 'entry level',
})
FORBIDDEN_MODIFIERS = frozenset({
    'senior', 'manager', 'lead', 'head', 'principal', 'consultant', 'specialist',
})


def _digest(value):
    return hashlib.sha256(json.dumps(
        value, sort_keys=True, separators=(',', ':'), default=str).encode()).hexdigest()


def parse_target_roles(value):
    return tuple(
        part.strip() for part in re.split(r'[,;\n]+', value or '') if part.strip()
    )


def taxonomy_fingerprint():
    release_set = TaxonomyReleaseSet.objects.filter(is_active=True).first()
    if release_set:
        return release_set.fingerprint
    releases = list(TaxonomyRelease.objects.filter(is_active=True).order_by(
        'source').values_list('source', 'version', 'pk'))
    crosswalks = list(TaxonomyCrosswalk.objects.filter(active=True).values_list(
        'mapping_name', 'mapping_version', 'source_external_id',
        'target_external_id', 'relation_type', 'approval_status').order_by('pk'))
    return _digest({'releases': releases, 'crosswalks': crosswalks})


def _direct_label_candidates(text):
    normalized = normalize_term(text)
    labels = TaxonomyLabel.objects.filter(
        concept__release__is_active=True,
        concept__kind__in=('occupation', 'occupation_group'),
        normalized_label=normalized,
        label_type__in=IDENTITY_LABEL_TYPES,
    ).select_related('concept', 'concept__release').order_by(
        'concept__release__source', 'concept_id')
    per_source = {}
    ambiguous_sources = []
    grouped = {}
    for row in labels:
        grouped.setdefault(row.concept.release.source, []).append(row)
    for source, source_labels in grouped.items():
        concept_ids = {row.concept_id for row in source_labels}
        if len(concept_ids) == 1:
            row = min(source_labels, key=lambda item: IDENTITY_LABEL_TYPES.index(item.label_type))
            per_source[row.concept_id] = {
                'route': f'exact_{row.label_type}_label',
                'provenance': {'label': row.label, 'source': source},
            }
        elif len(concept_ids) > 1:
            ambiguous_sources.append(source)
    return per_source, ambiguous_sources


def _expand_approved_identity(identities):
    initial_ids = set(identities)
    rows = TaxonomyCrosswalk.objects.filter(
        active=True, approval_status='approved_identity',
    ).filter(
        models.Q(source_concept_id__in=initial_ids) |
        models.Q(target_concept_id__in=initial_ids)
    ).select_related('source_concept', 'target_concept')
    expanded = dict(identities)
    for row in rows:
        if row.target_concept_id is None:
            continue
        if row.source_concept_id in initial_ids:
            concept = row.target_concept
        elif row.direction == 'bidirectional' or row.relation_type in {'exact', 'exact_isco'}:
            concept = row.source_concept
        else:
            continue
        expanded.setdefault(concept.pk, {
            'route': 'approved_exact_crosswalk',
            'provenance': {
                'crosswalk_id': row.pk,
                'relation': row.relation_type,
                'mapping': row.mapping_name,
                'mapping_version': row.mapping_version,
            },
        })
    return expanded


# Import locally to avoid polluting model import order at Django startup.
from django.db import models  # noqa: E402


def resolve_text_identities(text):
    identities, ambiguous_sources = _direct_label_candidates(text)
    identities = _expand_approved_identity(identities)
    if identities and ambiguous_sources:
        state = 'PARTIAL'
    elif identities:
        state = 'RESOLVED'
    elif ambiguous_sources:
        state = 'AMBIGUOUS'
    else:
        state = 'UNRESOLVED'
    return identities, state, {'ambiguous_sources': ambiguous_sources}


@transaction.atomic
def resolve_opportunity_revision(revision):
    revision = OpportunityCleanRevision.objects.select_for_update().get(pk=revision.pk)
    fingerprint = _digest({
        'payload_hash': revision.payload_hash,
        'taxonomy': taxonomy_fingerprint(),
        'version': OPPORTUNITY_RESOLVER_VERSION,
    })
    current = OpportunityOccupationResolution.objects.filter(revision=revision).first()
    if current and current.dependency_hash == fingerprint:
        return current
    if current:
        current.delete()
    identities, state, diagnostics = resolve_text_identities(revision.cleaned_title)
    resolution = OpportunityOccupationResolution.objects.create(
        revision=revision, state=state,
        resolver_version=OPPORTUNITY_RESOLVER_VERSION,
        dependency_hash=fingerprint, diagnostics=diagnostics)
    OpportunityOccupationIdentity.objects.bulk_create([
        OpportunityOccupationIdentity(
            resolution=resolution,
            concept_id=concept_id,
            route=value['route'],
            source_text=revision.cleaned_title,
            provenance=value['provenance'],
        ) for concept_id, value in identities.items()
    ])
    return resolution


@transaction.atomic
def resolve_preference_targets(preference):
    preference = CareerPreference.objects.select_for_update().get(pk=preference.pk)
    roles = parse_target_roles(preference.target_roles)
    existing = {row.ordinal: row for row in preference.target_resolutions.all()}
    results = []
    taxonomy = taxonomy_fingerprint()
    for ordinal, role in enumerate(roles):
        fingerprint = _digest({
            'role': role, 'taxonomy': taxonomy, 'version': TARGET_RESOLVER_VERSION})
        current = existing.pop(ordinal, None)
        if current and current.dependency_hash == fingerprint and current.authored_text == role:
            results.append(current)
            continue
        if current:
            current.delete()
        identities, state, diagnostics = resolve_text_identities(role)
        resolution = CareerTargetResolution.objects.create(
            preference=preference, profile=preference.profile, ordinal=ordinal,
            authored_text=role, normalized_text=normalize_term(role), state=state,
            resolver_version=TARGET_RESOLVER_VERSION,
            dependency_hash=fingerprint, diagnostics=diagnostics)
        CareerTargetIdentity.objects.bulk_create([
            CareerTargetIdentity(
                resolution=resolution, concept_id=concept_id,
                route=value['route'], provenance=value['provenance'])
            for concept_id, value in identities.items()
        ])
        results.append(resolution)
    for stale in existing.values():
        stale.delete()
    return results


def safe_title_target(target_text, cleaned_title):
    target = normalize_term(target_text)
    title = normalize_term(cleaned_title)
    if not target or not title:
        return False
    if target == title:
        return True
    target_tokens = target.split()
    if len(target_tokens) < 2 or '/' in cleaned_title or '(' in cleaned_title or ')' in cleaned_title:
        return False
    title_tokens = title.split()
    if not title_tokens or title_tokens[0] in FORBIDDEN_MODIFIERS or title_tokens[-1] in FORBIDDEN_MODIFIERS:
        return False
    candidates = []
    for modifier in HARMLESS_MODIFIERS:
        modifier_tokens = modifier.split()
        if title_tokens[:len(modifier_tokens)] == modifier_tokens:
            candidates.append(title_tokens[len(modifier_tokens):])
        if title_tokens[-len(modifier_tokens):] == modifier_tokens:
            candidates.append(title_tokens[:-len(modifier_tokens)])
    return any(' '.join(candidate) == target for candidate in candidates)


def discovery_relationship(targets, opportunity_resolution):
    """Return (category, reasons, triggering target ids) without multi-hop expansion."""
    opportunity_ids = set(opportunity_resolution.identities.values_list('concept_id', flat=True))
    for target in targets:
        target_ids = set(target.identities.values_list('concept_id', flat=True))
        shared = target_ids & opportunity_ids
        if shared:
            return 'TARGET', [{'route': 'same_validated_identity', 'concept_ids': sorted(shared),
                               'target': target.authored_text}], [target.pk]
        if safe_title_target(target.authored_text, opportunity_resolution.revision.cleaned_title):
            return 'TARGET', [{'route': 'safe_cleaned_title', 'target': target.authored_text}], [target.pk]

    for target in targets:
        target_ids = set(target.identities.values_list('concept_id', flat=True))
        mappings = TaxonomyCrosswalk.objects.filter(
            active=True, approval_status='approved_related',
            source_concept_id__in=target_ids,
            target_concept_id__in=opportunity_ids,
        )
        row = mappings.order_by('pk').first()
        if row:
            return 'RELATED', [{
                'route': 'official_one_hop_crosswalk', 'crosswalk_id': row.pk,
                'relation': row.relation_type, 'target': target.authored_text,
            }], [target.pk]

        isco_targets = TaxonomyConcept.objects.filter(
            pk__in=target_ids, release__source='isco', kind='occupation_group')
        isco_codes = set(isco_targets.values_list('code', flat=True))
        if not isco_codes:
            continue
        for identity in opportunity_resolution.identities.select_related('concept__release'):
            concept = identity.concept
            code = str((concept.metadata or {}).get('isco_group') or '')
            if concept.release.source in {'ofo', 'esco'} and code in isco_codes:
                return 'RELATED', [{
                    'route': f'{concept.release.source}_native_isco_parent',
                    'isco_code': code, 'target': target.authored_text,
                }], [target.pk]
    return None, [], []
