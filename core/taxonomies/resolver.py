"""Runtime-local taxonomy resolution for Phase 2.3.

This module is deliberately non-AI. A term resolves only when an active trusted
release contains the same normalized preferred/alternative label. Relationships
come only from imported taxonomy assertions.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable

from django.db.models import Q

from core.models import TaxonomyLabel, TaxonomyRelation
from .normalization import normalize_term


@dataclass(frozen=True)
class ConceptRef:
    id: int
    source: str
    version: str
    external_id: str
    kind: str
    code: str
    preferred_label: str
    metadata: dict


@dataclass
class TaxonomyMatchContext:
    """A bounded in-memory view of just the concepts needed for one match run."""

    by_label: dict[tuple[str, str], tuple[ConceptRef, ...]] = field(default_factory=dict)
    identity_concept_ids_by_label: dict[tuple[str, str], frozenset[int]] = field(
        default_factory=dict
    )
    concepts: dict[int, ConceptRef] = field(default_factory=dict)
    broader: dict[int, set[int]] = field(default_factory=dict)
    _resolve_cache: dict[tuple[str, tuple[str, ...]], tuple[ConceptRef, ...]] = field(
        default_factory=dict, repr=False, compare=False
    )
    _identity_cache: dict[tuple[str, tuple[str, ...]], tuple[ConceptRef, ...]] = field(
        default_factory=dict, repr=False, compare=False
    )

    @classmethod
    def empty(cls):
        return cls()

    def resolve(self, term: object, kinds: Iterable[str]) -> tuple[ConceptRef, ...]:
        """Resolve an exact trusted label without guessing through ambiguity.

        A label may legitimately exist in more than one taxonomy (for example an
        OFO and an ESCO occupation). That is useful, so we keep one unambiguous
        candidate *per source*. If one active source maps the same label to two
        different concepts, that source is ambiguous for this term and contributes
        nothing. This prevents a shared ambiguous label from creating a false
        canonical identity.
        """
        kinds = tuple(kinds)
        cache_key = (str(term or ""), kinds)
        cached = self._resolve_cache.get(cache_key)
        if cached is not None:
            return cached
        normalized = normalize_term(term)
        if not normalized:
            self._resolve_cache[cache_key] = ()
            return ()

        by_source = {}
        for kind in kinds:
            for concept in self.by_label.get((kind, normalized), ()):
                by_source.setdefault(concept.source, {})[concept.id] = concept

        result = []
        seen = set()
        for concepts in by_source.values():
            if len(concepts) != 1:
                continue
            concept = next(iter(concepts.values()))
            if concept.id not in seen:
                seen.add(concept.id)
                result.append(concept)
        resolved = tuple(result)
        self._resolve_cache[cache_key] = resolved
        return resolved

    def resolve_identity(self, term: object, kinds: Iterable[str]) -> tuple[ConceptRef, ...]:
        """Resolve only labels that the source treats as identity labels.

        OFO's public workbook combines specialisations and alternative titles in
        one source field. CareerGPS imports those conservatively as
        ``specialisation`` labels: useful relationship evidence, but not strong
        enough to declare two differently worded role titles canonically identical.
        """
        kinds = tuple(kinds)
        cache_key = (str(term or ""), kinds)
        cached = self._identity_cache.get(cache_key)
        if cached is not None:
            return cached
        normalized = normalize_term(term)
        if not normalized:
            self._identity_cache[cache_key] = ()
            return ()
        resolved = self.resolve(term, kinds)
        identity_ids = set()
        for kind in kinds:
            identity_ids.update(
                self.identity_concept_ids_by_label.get((kind, normalized), ())
            )
        result = tuple(concept for concept in resolved if concept.id in identity_ids)
        self._identity_cache[cache_key] = result
        return result

    def same_concept(self, left, right) -> tuple[ConceptRef, ...]:
        right_ids = {item.id for item in right}
        return tuple(item for item in left if item.id in right_ids)

    def is_ancestor(self, narrower_id: int, broader_id: int, *, max_depth: int = 1) -> bool:
        """True only for a direct source-asserted broader edge.

        Phase 2.3 deliberately does not chain two or three hierarchy hops. A
        direct relationship may support RELATED; longer semantic paths abstain.
        """
        if narrower_id == broader_id:
            return True
        frontier = {narrower_id}
        visited = {narrower_id}
        for _ in range(max_depth):
            next_frontier = set()
            for concept_id in frontier:
                for parent_id in self.broader.get(concept_id, ()):  # narrow -> broad
                    if parent_id == broader_id:
                        return True
                    if parent_id not in visited:
                        visited.add(parent_id)
                        next_frontier.add(parent_id)
            if not next_frontier:
                break
            frontier = next_frontier
        return False

    def narrower_supports_broader(self, evidence, criterion, *, max_depth=1):
        for evidence_concept in evidence:
            for criterion_concept in criterion:
                if evidence_concept.id == criterion_concept.id:
                    continue
                if self.is_ancestor(
                    evidence_concept.id, criterion_concept.id, max_depth=max_depth
                ):
                    return evidence_concept, criterion_concept
        return None

    def occupations_share_isco_group(self, left, right) -> bool:
        def groups(concepts):
            values = set()
            for concept in concepts:
                if concept.kind != "occupation":
                    continue
                metadata = concept.metadata or {}
                code = str(
                    metadata.get("isco_group")
                    or metadata.get("iscoGroup")
                    or ""
                ).strip()
                if code:
                    values.add(code)
            return values
        return bool(groups(left) & groups(right))


def _chunks(values, size=500):
    values = list(values)
    for index in range(0, len(values), size):
        yield values[index:index + size]


def build_taxonomy_context(terms_by_kind: dict[str, Iterable[object]]) -> TaxonomyMatchContext:
    """Load exact-label concepts + just enough trusted relations for the run.

    This intentionally avoids loading the whole ESCO/OFO graph on every web
    request. Matching stays local and query count grows by chunks, not by
    opportunity.
    """
    normalized_by_kind = {
        kind: {normalize_term(value) for value in values if normalize_term(value)}
        for kind, values in terms_by_kind.items()
    }
    if not any(normalized_by_kind.values()):
        return TaxonomyMatchContext.empty()

    labels = []
    for kind, normalized_terms in normalized_by_kind.items():
        for chunk in _chunks(sorted(normalized_terms)):
            labels.extend(
                TaxonomyLabel.objects.filter(
                    concept__release__is_active=True,
                    concept__kind=kind,
                    normalized_label__in=chunk,
                    language="en",
                ).select_related("concept", "concept__release")
            )

    context = TaxonomyMatchContext()
    by_label_mutable = {}
    identity_ids_mutable = {}
    for label in labels:
        concept = label.concept
        ref = ConceptRef(
            id=concept.id,
            source=concept.release.source,
            version=concept.release.version,
            external_id=concept.external_id,
            kind=concept.kind,
            code=concept.code,
            preferred_label=concept.preferred_label,
            metadata=concept.metadata or {},
        )
        context.concepts[ref.id] = ref
        key = (concept.kind, label.normalized_label)
        by_label_mutable.setdefault(key, {})[ref.id] = ref
        if label.label_type != "specialisation":
            identity_ids_mutable.setdefault(key, set()).add(ref.id)
    context.by_label = {
        key: tuple(items.values()) for key, items in by_label_mutable.items()
    }
    context.identity_concept_ids_by_label = {
        key: frozenset(ids) for key, ids in identity_ids_mutable.items()
    }

    resolved_ids = set(context.concepts)
    if not resolved_ids:
        return context

    frontier = set(context.concepts)
    visited_subjects = set()
    for _ in range(1):
        subject_ids = frontier - visited_subjects
        if not subject_ids:
            break
        visited_subjects.update(subject_ids)
        relations = list(
            TaxonomyRelation.objects.filter(
                subject_id__in=subject_ids,
                relation_type="broader",
                subject__release__is_active=True,
                object__release__is_active=True,
            ).select_related("object", "object__release")
        )
        frontier = set()
        for relation in relations:
            context.broader.setdefault(relation.subject_id, set()).add(relation.object_id)
            obj = relation.object
            if obj.id not in context.concepts:
                context.concepts[obj.id] = ConceptRef(
                    id=obj.id,
                    source=obj.release.source,
                    version=obj.release.version,
                    external_id=obj.external_id,
                    kind=obj.kind,
                    code=obj.code,
                    preferred_label=obj.preferred_label,
                    metadata=obj.metadata or {},
                )
                frontier.add(obj.id)

    return context
