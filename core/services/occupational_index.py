"""Indexed occupational candidate retrieval."""
from __future__ import annotations

from django.db.models import Q

from core.models import (
    OccupationRelationshipPolicy,
    OccupationRelationshipRule,
    OccupationSourceResolutionCandidate,
    Opportunity,
    TaxonomyCrosswalk,
)
from core.professional_policies import CANDIDATE_RELATIONSHIP_POLICY_VERSION


def target_concept_ids(targets, *, target_sources=None):
    """Every retained concept id across all targets and both taxonomies.

    Reads the persisted candidate rows for the given target containers, so
    every retained ESCO and OFO result counts regardless of which source
    produced it.  ``target_sources`` is accepted for caller symmetry with
    ``assess_target_vacancy``; the persisted rows are authoritative.
    """
    target_ids = [target.pk for target in targets or ()]
    if not target_ids:
        return set()
    return {
        int(value) for value in OccupationSourceResolutionCandidate.objects
        .filter(resolution__target_resolution_id__in=target_ids)
        .values_list('concept_id', flat=True) if value}


def expand_concept_ids(concept_ids, *, include_non_admitting=True):
    """Target concepts plus every concept linked by active policy evidence.

    Candidate retrieval must reach every pair whose assessment could ever
    matter:

    * admitting policy rows (EXACT/RELATED admission);
    * explicitly UNSUPPORTED policy rows -- negative evidence must still be
      assessed so UNRELATED verdicts are produced and retained;
    * approved identity crosswalks (the EXACT confirmation path);
    * relation/crosswalk facts that an admitting reusable rule accepts.

    ``include_non_admitting=False`` narrows to admitting links only; callers
    that must preserve negative-evidence coverage keep the default.  Over-
    inclusion is safe: ``assess_target_vacancy`` still decides admission.
    """
    concept_ids = {int(v) for v in concept_ids or () if v}
    if not concept_ids:
        return set()
    expanded = set(concept_ids)
    policy_rows = OccupationRelationshipPolicy.objects.filter(
        active=True,
        policy_version=CANDIDATE_RELATIONSHIP_POLICY_VERSION,
    )
    if not include_non_admitting:
        policy_rows = policy_rows.filter(admission_allowed=True)
    pairs = policy_rows.filter(
        Q(subject_concept_id__in=concept_ids)
        | Q(object_concept_id__in=concept_ids),
    ).values_list('subject_concept_id', 'object_concept_id')
    for subject_id, object_id in pairs:
        expanded.add(int(subject_id))
        expanded.add(int(object_id))
    links = TaxonomyCrosswalk.objects.filter(
        active=True, approval_status='approved_identity',
        relation_type__in=('exact', 'exact_isco'),
    ).filter(
        Q(source_concept_id__in=concept_ids)
        | Q(target_concept_id__in=concept_ids),
    ).values_list('source_concept_id', 'target_concept_id')
    for source_id, target_id in links:
        if source_id:
            expanded.add(int(source_id))
        if target_id:
            expanded.add(int(target_id))
    expanded.update(_rule_linked_concept_ids(concept_ids))
    return expanded


def _rule_linked_concept_ids(concept_ids):
    """Concepts joined by facts an admitting reusable rule accepts."""
    linked = set()
    relation_types = set(OccupationRelationshipRule.objects.filter(
        active=True,
        policy_version=CANDIDATE_RELATIONSHIP_POLICY_VERSION,
        admission_allowed=True,
        evidence_source='taxonomy_relation',
    ).values_list('evidence_relation_type', flat=True))
    if relation_types:
        from core.models import TaxonomyRelation
        rows = TaxonomyRelation.objects.filter(
            relation_type__in=relation_types,
        ).filter(
            Q(subject_id__in=concept_ids) | Q(object_id__in=concept_ids),
        ).values_list('subject_id', 'object_id')
        for subject_id, object_id in rows:
            if subject_id:
                linked.add(int(subject_id))
            if object_id:
                linked.add(int(object_id))
    crosswalk_types = set(OccupationRelationshipRule.objects.filter(
        active=True,
        policy_version=CANDIDATE_RELATIONSHIP_POLICY_VERSION,
        admission_allowed=True,
        evidence_source='crosswalk',
    ).values_list('evidence_relation_type', flat=True))
    if crosswalk_types:
        rows = TaxonomyCrosswalk.objects.filter(
            active=True, relation_type__in=crosswalk_types,
        ).filter(
            Q(source_concept_id__in=concept_ids)
            | Q(target_concept_id__in=concept_ids),
        ).values_list('source_concept_id', 'target_concept_id')
        for source_id, target_id in rows:
            if source_id:
                linked.add(int(source_id))
            if target_id:
                linked.add(int(target_id))
    return linked


def title_clauses(target_texts):
    """Bounded normalized-title clauses for TITLE_MATCH discovery parity."""
    from core.services.relationship_aggregation import _title_tokens
    from core.taxonomies.normalization import normalize_term
    clauses = []
    for text in target_texts or ():
        tokens = _title_tokens(text)
        if not tokens:
            continue
        clauses.append(Q(
            active_clean_revision__normalized_title=normalize_term(text)))
        # Word-level containment recall: a title that merely embeds the target
        # text ("Entry-Level Data Analyst", "Data Analyst Role 00") carries
        # enough title evidence to deserve an assessment.  Retrieval only --
        # ``assess_target_vacancy`` still decides what the pair establishes.
        clauses.append(Q(
            active_clean_revision__normalized_title__contains=normalize_term(text)))
        if len(tokens) < 2:
            continue
        joined = ' '.join(tokens)
        for variant in ('junior', 'jr', 'graduate', 'trainee', 'intern',
                        'internship', 'entry level'):
            clauses.append(Q(active_clean_revision__normalized_title=(
                f'{variant} {joined}')))
            clauses.append(Q(active_clean_revision__normalized_title=(
                f'{joined} {variant}')))
    return clauses



def candidate_revision_ids(*, concept_ids, target_texts=(), base_qs=None):
    """Unique READY revision ids sharing target occupational evidence."""
    concept_ids = {int(v) for v in concept_ids or () if v}
    base = (base_qs if base_qs is not None
            else Opportunity.objects.professionally_ready(
                include_obvious_senior=True))
    ready_ids = base.values('active_clean_revision_id')
    found: set[int] = set()
    if concept_ids:
        found.update(OccupationSourceResolutionCandidate.objects.filter(
            concept_id__in=concept_ids,
            resolution__opportunity_resolution__revision_id__in=ready_ids,
        ).values_list(
            'resolution__opportunity_resolution__revision_id', flat=True))
    for clause in title_clauses(target_texts):
        found.update(base.filter(clause).values_list(
            'active_clean_revision_id', flat=True))
    return {int(v) for v in found if v}


def retrieve_candidates(targets, *, target_sources=None, base_qs=None):
    """Target concepts (expanded) to unique READY revision ids."""
    expanded = expand_concept_ids(
        target_concept_ids(targets, target_sources=target_sources))
    texts = [getattr(t, 'authored_text', '') for t in targets or ()]
    return candidate_revision_ids(
        concept_ids=expanded, target_texts=texts, base_qs=base_qs)

