"""Independent, exact-label OFO and ESCO resolution in shadow state."""
from __future__ import annotations

from collections import defaultdict
from hashlib import sha256
import json

from django.db import transaction

from core.models import (
    OccupationSourceResolution,
    OccupationSourceResolutionCandidate,
    OpportunityTitleResolution,
    ProfileFeedEvaluation,
    TaxonomyConcept,
    TaxonomyLabel,
    TaxonomyRelease,
)
from core.professional_policies import (
    ESCO_RESOLVER_VERSION,
    LABEL_ROUTE_POLICY_VERSION,
    OFO_RESOLVER_VERSION,
    TARGET_CONTAINER_VERSION,
)
from core.services.professional_resolution import parse_target_roles
from core.services.title_gate import evaluate_revision
from core.taxonomies.normalization import normalize_term


SOURCE_VERSIONS = {
    'ofo': OFO_RESOLVER_VERSION,
    'esco': ESCO_RESOLVER_VERSION,
}
LABEL_TYPES = ('preferred', 'alternative', 'specialisation')
LABEL_PRIORITY = {value: index for index, value in enumerate(LABEL_TYPES)}


def _digest(value: object) -> str:
    return sha256(json.dumps(
        value, sort_keys=True, separators=(',', ':'), default=str,
    ).encode()).hexdigest()


def _active_release(source: str):
    return TaxonomyRelease.objects.filter(source=source, is_active=True).first()


def _concept_candidate(concept, release):
    return {
        'concept': concept,
        'matched_label': concept.preferred_label,
        'label_type': 'preferred',
        'label_route': 'exact_preferred_concept_label',
        'provenance': {
            'taxonomy_release_id': release.pk,
            'taxonomy_source': release.source,
            'taxonomy_version': release.version,
            'concept_external_id': concept.external_id,
            'concept_code': concept.code,
            'concept_label': concept.preferred_label,
            'concept_kind': concept.kind,
            'concept_field': 'preferred_label',
        },
    }


def _label_candidate(label, release):
    return {
        'concept': label.concept,
        'matched_label': label.label,
        'label_type': label.label_type,
        'label_route': f'exact_{label.label_type}_label',
        'provenance': {
            'taxonomy_release_id': release.pk,
            'taxonomy_source': release.source,
            'taxonomy_version': release.version,
            'concept_external_id': label.concept.external_id,
            'concept_code': label.concept.code,
            'concept_label': label.concept.preferred_label,
            'concept_kind': label.concept.kind,
            'taxonomy_label_id': label.pk,
            'language': label.language,
        },
    }


def _select_one_per_concept(entries):
    """Keep the best-labelled candidate for each concept, in concept order.

    `entries` must be ordered concept-preferred rows first, then label rows by
    (concept_id, pk), which is what makes a concept's own preferred label win
    over an alternative label on the same concept.
    """
    by_concept = {}
    for concept_pk, candidate in entries:
        existing = by_concept.get(concept_pk)
        existing_priority = LABEL_PRIORITY.get(
            (existing or {}).get('label_type'), len(LABEL_PRIORITY))
        if existing is None or LABEL_PRIORITY[candidate['label_type']] < existing_priority:
            by_concept[concept_pk] = candidate
    return [by_concept[key] for key in sorted(by_concept)]


def _candidate_rows(release, normalized_input):
    if release is None or not normalized_input:
        return []

    entries = [
        (concept.pk, _concept_candidate(concept, release))
        for concept in TaxonomyConcept.objects.filter(
            release=release, kind='occupation', normalized_label=normalized_input,
        ).order_by('pk')
    ]
    entries += [
        (label.concept_id, _label_candidate(label, release))
        for label in TaxonomyLabel.objects.filter(
            concept__release=release,
            concept__kind='occupation',
            normalized_label=normalized_input,
            label_type__in=LABEL_TYPES,
        ).select_related('concept').order_by('concept_id', 'pk')
    ]
    return _select_one_per_concept(entries)


def candidate_rows_bulk(release, normalized_inputs):
    """Read-only batched form of `_candidate_rows` for many labels at once.

    The selection rules are shared with `_candidate_rows`; only the query is
    widened, so an analytical cohort can resolve a whole catalogue of cleaned
    titles without one query per title. This creates and writes nothing.
    """
    if release is None:
        return {}
    wanted = {value for value in normalized_inputs if value}
    if not wanted:
        return {}
    entries_by_label = defaultdict(list)
    for concept in TaxonomyConcept.objects.filter(
        release=release, kind='occupation', normalized_label__in=wanted,
    ).order_by('pk'):
        entries_by_label[concept.normalized_label].append(
            (concept.pk, _concept_candidate(concept, release)))
    for label in TaxonomyLabel.objects.filter(
        concept__release=release,
        concept__kind='occupation',
        normalized_label__in=wanted,
        label_type__in=LABEL_TYPES,
    ).select_related('concept').order_by('concept_id', 'pk'):
        entries_by_label[label.normalized_label].append(
            (label.concept_id, _label_candidate(label, release)))
    return {
        normalized: _select_one_per_concept(entries)
        for normalized, entries in entries_by_label.items()
    }


@transaction.atomic
def resolve_source(*, source, original_input, target_resolution=None,
                   opportunity_resolution=None):
    """Resolve within exactly one source. Crosswalks are intentionally absent."""
    if source not in SOURCE_VERSIONS:
        raise ValueError(f'Unsupported occupational source: {source}')
    if (target_resolution is None) == (opportunity_resolution is None):
        raise ValueError('Exactly one resolution parent is required.')

    normalized_input = normalize_term(original_input)
    release = _active_release(source)
    resolver_version = SOURCE_VERSIONS[source]
    candidates = _candidate_rows(release, normalized_input)
    dependency_hash = _digest({
        'source': source,
        'original_input': original_input,
        'normalized_input': normalized_input,
        'taxonomy_release': (release.pk, release.version) if release else None,
        'resolver_version': resolver_version,
        'label_policy_version': LABEL_ROUTE_POLICY_VERSION,
        'candidate_evidence': [
            {
                'concept_id': row['concept'].pk,
                'external_id': row['concept'].external_id,
                'matched_label': row['matched_label'],
                'label_type': row['label_type'],
                'label_route': row['label_route'],
                'provenance': row['provenance'],
            }
            for row in candidates
        ],
    })
    parent_filter = {
        'source': source,
        'target_resolution': target_resolution,
        'opportunity_resolution': opportunity_resolution,
    }
    current = OccupationSourceResolution.objects.filter(**parent_filter).first()
    if current and current.dependency_hash == dependency_hash:
        return current

    if candidates:
        # Every matched concept is a retained result of this source.  Several
        # results are not ambiguity, so all of them are kept and used.  No
        # favourite is chosen: `selected_concept` records the source's answer
        # only when the source gave exactly one, which keeps it honest as a
        # single-result pointer rather than a preferred occupation.
        state = 'RESOLVED'
        selected = candidates[0] if len(candidates) == 1 else None
    else:
        state = 'UNRESOLVED'
        selected = None

    values = {
        'taxonomy_release': release,
        'resolver_version': resolver_version,
        'label_policy_version': LABEL_ROUTE_POLICY_VERSION,
        'original_input': original_input,
        'normalized_input': normalized_input,
        'input_hash': _digest({'source': source, 'input': original_input}),
        'state': state,
        'result_count': len(candidates),
        'selected_concept': selected['concept'] if selected else None,
        'matched_label': selected['matched_label'] if selected else '',
        'label_type': selected['label_type'] if selected else '',
        'label_route': selected['label_route'] if selected else '',
        'provenance': selected['provenance'] if selected else {
            'taxonomy_release_id': release.pk if release else None,
            'taxonomy_source': source,
            'taxonomy_version': release.version if release else None,
            'result_count': len(candidates),
        },
        'diagnostics': {
            'result_count': len(candidates),
            'multi_result': len(candidates) > 1,
            # `candidate_count` and `candidate_concept_ids` are the retained
            # result set under the keys already read by Career Intelligence.
            'candidate_count': len(candidates),
            'candidate_concept_ids': [row['concept'].pk for row in candidates],
            'results': [{
                'concept_id': row['concept'].pk,
                'code': row['concept'].code,
                'label': row['concept'].preferred_label,
                'matched_label': row['matched_label'],
                'label_type': row['label_type'],
                'label_route': row['label_route'],
                'taxonomy_source': source,
                'taxonomy_version': release.version if release else None,
                'resolver_version': resolver_version,
                'label_policy_version': LABEL_ROUTE_POLICY_VERSION,
                'status': OccupationSourceResolutionCandidate.RETAINED,
            } for row in candidates],
            'reason': (
                'one_source_local_exact_label' if len(candidates) == 1 else
                'several_source_local_exact_labels' if candidates else
                'no_source_local_exact_label'
            ),
        },
        'dependency_hash': dependency_hash,
    }
    resolution, _created = OccupationSourceResolution.objects.update_or_create(
        **parent_filter, defaults=values)
    resolution.candidates.all().delete()
    OccupationSourceResolutionCandidate.objects.bulk_create([
        OccupationSourceResolutionCandidate(
            resolution=resolution,
            concept=row['concept'],
            ordinal=ordinal,
            matched_label=row['matched_label'],
            label_type=row['label_type'],
            label_route=row['label_route'],
            code=row['concept'].code,
            preferred_label=row['concept'].preferred_label,
            taxonomy_source=source,
            taxonomy_version=release.version if release else '',
            resolver_version=resolver_version,
            label_policy_version=LABEL_ROUTE_POLICY_VERSION,
            status=OccupationSourceResolutionCandidate.RETAINED,
            selected=len(candidates) == 1,
            provenance=row['provenance'],
        )
        for ordinal, row in enumerate(candidates)
    ])
    return resolution


def resolve_target_sources(target_resolution):
    return {
        source: resolve_source(
            source=source,
            original_input=target_resolution.authored_text,
            target_resolution=target_resolution,
        )
        for source in SOURCE_VERSIONS
    }


@transaction.atomic
def maintain_target_resolution_containers(preference):
    """Mirror authored targets without populating crosswalk-expanded identities."""
    preference = type(preference).objects.select_for_update().get(pk=preference.pk)
    existing = {row.ordinal: row for row in preference.target_resolutions.all()}
    results = []
    for ordinal, authored_text in enumerate(parse_target_roles(preference.target_roles)):
        dependency_hash = _digest({
            'authored_text': authored_text,
            'ordinal': ordinal,
            'version': TARGET_CONTAINER_VERSION,
        })
        current = existing.pop(ordinal, None)
        if current is None:
            current = preference.target_resolutions.create(
                profile=preference.profile,
                ordinal=ordinal,
                authored_text=authored_text,
                normalized_text=normalize_term(authored_text),
                state='UNRESOLVED',
                resolver_version=TARGET_CONTAINER_VERSION,
                dependency_hash=dependency_hash,
                diagnostics={'authority': 'independent_source_resolutions'},
            )
        elif (current.authored_text != authored_text
              or current.resolver_version == TARGET_CONTAINER_VERSION
              and current.dependency_hash != dependency_hash):
            # This container is derived and mutable. Existing compatibility
            # identities are removed only when their authored target changed,
            # together with the target-specific occupational rows that were
            # derived from the previous wording: source resolutions, their
            # retained candidates, and every pair verdict they produced.  The
            # next build re-resolves the new text from scratch, so no stale
            # concept can drive candidate retrieval and no old verdict can
            # survive into the new target's feed.
            #
            # Stored feed admissions PROTECT their assessments (so a feed row
            # can never lose its evidence), therefore admissions keyed to this
            # container are released first -- the same ordering as
            # ``feed_v2._purge_target_specific_state``, scoped to one target.
            ProfileFeedEvaluation.objects.filter(
                relationship_assessment__target_resolution=current,
            ).delete()
            current.identities.all().delete()
            current.source_resolutions.all().delete()
            current.opportunity_relationships.all().delete()
            current.authored_text = authored_text
            current.normalized_text = normalize_term(authored_text)
            current.state = 'UNRESOLVED'
            current.resolver_version = TARGET_CONTAINER_VERSION
            current.dependency_hash = dependency_hash
            current.diagnostics = {'authority': 'independent_source_resolutions'}
            current.save()
        results.append(current)
    for stale in existing.values():
        stale.delete()
    return results


@transaction.atomic
def resolve_vacancy_sources(revision, *, readonly=False):
    """Resolve a vacancy's title against each taxonomy, independently.

    No seniority decision is written here.  A senior role is excluded
    read-only, before admission, by ``core.services.title_gate``; this
    container only records how the title was resolved per source, and its
    dependency hash therefore covers the title and the resolver versions.

    ``readonly=True`` returns the stored source resolutions without writing,
    so target-change generation can reuse catalogue intelligence instead of
    re-resolving every job.
    """
    if readonly:
        container = OpportunityTitleResolution.objects.filter(
            revision=revision).prefetch_related('source_resolutions').first()
        return container
    dependency_hash = _digest({
        'cleaned_title': revision.cleaned_title,
        'source_versions': SOURCE_VERSIONS,
        'label_policy_version': LABEL_ROUTE_POLICY_VERSION,
    })
    container, _created = OpportunityTitleResolution.objects.update_or_create(
        revision=revision,
        defaults={
            'state': 'READY',
            'dependency_hash': dependency_hash,
            'diagnostics': {
                'authority': 'independent_source_resolutions',
                'seniority_gate': 'read_only_not_persisted',
            },
        },
    )
    for source in SOURCE_VERSIONS:
        resolve_source(
            source=source,
            original_input=revision.cleaned_title,
            opportunity_resolution=container,
        )
    return container
