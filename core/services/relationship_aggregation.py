"""Conservative aggregation of independent occupational resolver evidence."""
from __future__ import annotations

from hashlib import sha256
import json
import re
from functools import lru_cache
import unicodedata

from django.db import transaction
from django.db.models import Count, Max, Q

from core.models import (
    OccupationRelationshipPolicy,
    OccupationRelationshipRule,
    OccupationSourceResolutionCandidate,
    TargetOpportunityRelationshipAssessment,
    TargetOpportunityRelationshipFinding,
    TaxonomyCrosswalk,
    TaxonomyRelation,
)
from core.professional_policies import (
    CANDIDATE_RELATIONSHIP_POLICY_VERSION,
    DENIED_PAIRING_CLASS,
    INCONCLUSIVE_PAIRING_CLASS,
    RELATIONSHIP_AGGREGATION_VERSION,
    RETAINED_RELATIONSHIP_CLASSES,
    TITLE_MATCH_CLASS,
    TITLE_MATCH_TIER,
    UNRELATED_CLASS,
    UNRELATED_REASON_CODE,
)
from core.services.occupational_resolution import (
    SOURCE_VERSIONS,
    resolve_target_sources,
    resolve_vacancy_sources,
)
from core.services.title_gate import evaluate_revision, evaluate_title


def _digest(value: object) -> str:
    return sha256(json.dumps(
        value, sort_keys=True, separators=(',', ':'), default=str,
    ).encode()).hexdigest()


def _policy_for(target_concept_id, vacancy_concept_id, relationship_types=None):
    direct = Q(
        subject_concept_id=target_concept_id,
        object_concept_id=vacancy_concept_id,
        direction__in=('forward', 'bidirectional'),
    )
    reverse = Q(
        subject_concept_id=vacancy_concept_id,
        object_concept_id=target_concept_id,
        direction__in=('reverse', 'bidirectional'),
    )
    rows = OccupationRelationshipPolicy.objects.filter(
        direct | reverse,
        active=True,
        policy_version=CANDIDATE_RELATIONSHIP_POLICY_VERSION,
    )
    if relationship_types:
        rows = rows.filter(relationship_type__in=relationship_types)
    order = {
        'SAME_OCCUPATION': 0,
        'SAME_OCCUPATION_SPECIALISATION': 1,
        'CLOSE_NEIGHBOUR': 2,
        'SAFE_BROADER_NARROWER': 3,
        'WEAK_ADJACENCY': 4,
        'UNSUPPORTED': 5,
    }
    return min(rows, key=lambda row: (order[row.relationship_type], row.pk), default=None)


def _exact_crosswalk(target_concept_id, vacancy_concept_id):
    """Approved exact evidence is aggregation-only and never changes a resolver row."""
    direct = Q(
        source_concept_id=target_concept_id,
        target_concept_id=vacancy_concept_id,
        direction__in=('forward', 'bidirectional'),
    )
    reverse = Q(
        source_concept_id=vacancy_concept_id,
        target_concept_id=target_concept_id,
    ) & (Q(direction='bidirectional') | Q(relation_type__in=('exact', 'exact_isco')))
    return TaxonomyCrosswalk.objects.filter(
        direct | reverse,
        active=True,
        approval_status='approved_identity',
        relation_type__in=('exact', 'exact_isco'),
    ).order_by('pk').first()


def _explicit_unsupported(comparison):
    """True only for explicit negative evidence from one source.

    Explicit negative evidence means an active policy row whose relationship type
    is UNSUPPORTED, which does not admit the relationship, and which belongs to
    the current candidate relationship policy version.  Absence of evidence,
    ambiguity, an unresolved source, or a non-admitting policy of another type
    (for example WEAK_ADJACENCY) is never explicit negative evidence.

    Both denial reasons are accepted: the single-pairing route and the
    candidate-set route report the same explicit contradiction differently.
    """
    if not comparison or comparison.get('outcome') != 'INCOMPATIBLE':
        return False
    if comparison.get('reason') not in {
            'relationship_policy_does_not_admit',
            'candidate_pairing_denied_by_policy'}:
        return False
    policy = comparison.get('policy')
    if not isinstance(policy, dict):
        return False
    return (
        policy.get('relationship_type') == 'UNSUPPORTED'
        and policy.get('admission_allowed') is False
        and policy.get('policy_version') == CANDIDATE_RELATIONSHIP_POLICY_VERSION
    )


def _policy_evidence(row):
    return {
        'policy_id': row.pk,
        'relationship_type': row.relationship_type,
        'direction': row.direction,
        'admission_allowed': row.admission_allowed,
        'policy_version': row.policy_version,
        'taxonomy_relation_id': row.taxonomy_relation_id,
        'crosswalk_id': row.crosswalk_id,
        'provenance': row.provenance,
    }


def _evidence_filter_rejects(rule, fact, target_concept):
    """Apply a rule's recorded evidence filter to one candidate fact.

    ESCO stores `broader` rows from two different imports: a genuine
    occupation-pillar link between two occupations, and an ISCO group
    membership row pointing at an occupation *group*.  Only the first is a
    direct occupation-to-occupation link, so the seeded rule narrows to it
    by provenance instead of approving the whole relation class.
    """
    criteria = (rule.provenance or {}).get('evidence_filter') or {}
    if not criteria:
        return False
    if criteria.get('subject_kind') and target_concept.kind != criteria['subject_kind']:
        return True
    metadata = getattr(fact, 'metadata', None) or {}
    required = criteria.get('metadata_relationship')
    if required and metadata.get('relationship') not in (
            required if isinstance(required, (list, tuple)) else (required,)):
        return True
    required_object_kind = criteria.get('object_kind')
    if required_object_kind:
        if getattr(fact.object, 'kind', None) != required_object_kind:
            return True
    return False


def _reusable_rule_evidence(target_concept, vacancy_concept):
    """Return one explicitly approved rule over one direct source fact.

    A rule may also declare an `evidence_filter`.  That filter is what lets
    a broad imported relation class be narrowed to the rows that were
    actually verified to be direct occupation-to-occupation links, instead
    of approving the whole class on trust.
    """
    source = target_concept.release.source
    if vacancy_concept.release.source != source:
        return None
    rules = OccupationRelationshipRule.objects.filter(
        active=True,
        admission_allowed=True,
        policy_version=CANDIDATE_RELATIONSHIP_POLICY_VERSION,
        taxonomy_source__in=('', source),
    ).order_by('pk')
    for rule in rules:
        if rule.evidence_source == 'taxonomy_relation':
            direct = TaxonomyRelation.objects.filter(
                subject=target_concept, object=vacancy_concept,
                relation_type=rule.evidence_relation_type,
            ).order_by('pk').first()
            reverse = TaxonomyRelation.objects.filter(
                subject=vacancy_concept, object=target_concept,
                relation_type=rule.evidence_relation_type,
            ).order_by('pk').first()
            fact = (direct if rule.direction in ('forward', 'bidirectional') else None)
            fact = fact or (reverse if rule.direction in ('reverse', 'bidirectional') else None)
        else:
            direct = TaxonomyCrosswalk.objects.filter(
                source_concept=target_concept, target_concept=vacancy_concept,
                relation_type=rule.evidence_relation_type, active=True,
                direction__in=('forward', 'bidirectional'),
            ).order_by('pk').first()
            reverse = TaxonomyCrosswalk.objects.filter(
                source_concept=vacancy_concept, target_concept=target_concept,
                relation_type=rule.evidence_relation_type, active=True,
                direction__in=('reverse', 'bidirectional'),
            ).order_by('pk').first()
            fact = (direct if rule.direction in ('forward', 'bidirectional') else None)
            fact = fact or (reverse if rule.direction in ('reverse', 'bidirectional') else None)
        if fact and not _evidence_filter_rejects(rule, fact, target_concept):
            return {
                'rule_id': rule.pk,
                'rule_name': rule.name,
                'relationship_type': rule.candidate_relationship_type,
                'candidate_class': rule.candidate_class,
                'direction': rule.direction,
                'admission_allowed': rule.admission_allowed,
                'policy_version': rule.policy_version,
                'evidence_source': rule.evidence_source,
                'evidence_relation_type': rule.evidence_relation_type,
                'source_fact_id': fact.pk,
                'rationale': rule.rationale,
                'provenance': rule.provenance,
            }
    return None


def _candidate_ids(row):
    """Every candidate concept a source returned, never a chosen one.

    An ambiguous resolution deliberately keeps `selected_concept` empty;
    the retained candidate ids are what an all-pairs comparison needs.
    """
    if row is None:
        return ()
    ids = getattr(row, 'candidate_concept_ids', None)
    if ids is None:
        diagnostics = getattr(row, 'diagnostics', None) or {}
        ids = diagnostics.get('candidate_concept_ids')
    if ids:
        return tuple(sorted(set(ids)))
    selected = getattr(row, 'selected_concept_id', None)
    return (selected,) if selected else ()


@lru_cache(maxsize=8192)
def _retained_results(resolution_pk, source, dependency_hash):
    """Every retained occupation result of one stored source resolution.

    Each candidate row keeps its own code, label, label route, status, and
    taxonomy version, so a multi-result answer stays fully described instead of
    collapsing into the parent row.  Cached under the resolution's own
    dependency hash, so a re-resolved title can never be read through a stale
    entry.
    """
    from core.models import OccupationSourceResolution

    row = OccupationSourceResolution.objects.filter(
        pk=resolution_pk).select_related('taxonomy_release').first()
    if row is None:
        return ()
    release_version = row.taxonomy_release.version if row.taxonomy_release else ''
    results = tuple(sorted((
        {
            'concept_id': candidate.concept_id,
            'code': candidate.code or candidate.concept.code,
            'label': candidate.preferred_label or candidate.concept.preferred_label,
            'matched_label': candidate.matched_label,
            'label_type': candidate.label_type,
            'label_route': candidate.label_route,
            'taxonomy_source': candidate.taxonomy_source or source,
            'taxonomy_version': candidate.taxonomy_version or release_version,
            'resolver_version': candidate.resolver_version,
            'status': candidate.status or OccupationSourceResolutionCandidate.RETAINED,
            'resolution_state': row.state,
        } for candidate in row.candidates.select_related(
            'concept').order_by('ordinal', 'pk')), key=lambda item: item['concept_id']))
    if results:
        return results
    # A row written before per-result rows existed still carries its candidate
    # ids or its single selected concept; keep using them instead of losing it.
    concept_ids = tuple((row.diagnostics or {}).get('candidate_concept_ids')
                        or ()) or ((row.selected_concept_id,)
                                   if row.selected_concept_id else ())
    single = len(concept_ids) == 1
    return tuple(sorted((
        {
            'concept_id': concept_id,
            'code': '', 'label': '',
            'matched_label': row.matched_label if single else '',
            'label_type': row.label_type if single else '',
            'label_route': row.label_route if single else '',
            'taxonomy_source': source,
            'taxonomy_version': release_version,
            'resolver_version': row.resolver_version,
            'status': 'LEGACY',
            'resolution_state': row.state,
        } for concept_id in concept_ids), key=lambda item: item['concept_id']))


def _result_set(row):
    """Every occupation result one source produced, stored or mirrored."""
    if row is None:
        return ()
    if getattr(row, 'pk', None) and getattr(row, 'dependency_hash', None):
        return _retained_results(row.pk, row.source, row.dependency_hash)
    mirrored = getattr(row, 'candidate_results', None)
    if mirrored:
        # A read-only mirror of a stored resolution, already fully described:
        # the same shape the stored candidate rows produce.
        state = getattr(row, 'state', '') or ''
        return tuple(sorted((
            {**item, 'resolution_state': item.get('resolution_state') or state}
            for item in mirrored), key=lambda item: item['concept_id']))
    concept_ids = _candidate_ids(row)
    single = len(concept_ids) == 1
    return tuple(sorted((
        {
            'concept_id': concept_id,
            'code': '', 'label': '',
            'matched_label': getattr(row, 'matched_label', '') if single else '',
            'label_type': (getattr(row, 'label_type', '') or '') if single else '',
            'label_route': '',
            'taxonomy_source': getattr(row, 'source', '') or '',
            'taxonomy_version': '',
            'resolver_version': '',
            'status': OccupationSourceResolutionCandidate.RETAINED,
            'resolution_state': getattr(row, 'state', '') or '',
        } for concept_id in concept_ids), key=lambda item: item['concept_id']))


def _pairing_relationship_class(pairing):
    """Separate a retained relationship from silence and from a denial."""
    outcome = pairing.get('outcome')
    if outcome in RETAINED_RELATIONSHIP_CLASSES:
        return outcome
    if outcome == 'INCOMPATIBLE' and _explicit_unsupported(pairing):
        return DENIED_PAIRING_CLASS
    return INCONCLUSIVE_PAIRING_CLASS


def _trusted(item):
    """True when a relationship confirms an occupation, not merely resembles it.

    `_pairing_tier` gives a policy-free same-concept label collision the
    `TITLE_ONLY` tier, because a side that kept several results never committed
    to that one concept.  Everything else is a confirmation that stands on its
    own: an approved policy, an approved exact crosswalk, an approved reusable
    rule, or a same-concept match where both sides returned exactly one result.
    Retaining several results can therefore only demote a possibility; it never
    weakens a confirmation.
    """
    return item.get('evidence_tier') != TITLE_MATCH_TIER


def _title_only_disclosure(item):
    """One retained but unconfirmed relationship, shaped for disclosure.

    A policy-free same-concept label collision was really compared, so it is
    kept and disclosed with the tier that marks it as a possibility.  It is not
    a finding: the pairings behind it stay in full in the pair's stored
    comparison payload, and this is the compact record the pages render, always
    labelled as title-only evidence that is not taxonomy-confirmed.
    """
    return {
        'source': item.get('source'),
        'relationship_class': item['relationship_class'],
        'evidence_tier': item['evidence_tier'],
        'tier_rank': item['tier_rank'],
        'route': item.get('route', ''),
        'reason_code': item['reason'],
        'target_concept_id': item['target_concept_id'],
        'target_code': item['target_code'],
        'target_label': item['target_label'],
        'target_matched_label': item['target_matched_label'],
        'target_label_type': item['target_label_type'],
        'target_label_route': item['target_label_route'],
        'job_concept_id': item['vacancy_concept_id'],
        'job_code': item['vacancy_code'],
        'job_label': item['vacancy_label'],
        'job_matched_label': item['vacancy_matched_label'],
        'job_label_type': item['vacancy_label_type'],
        'job_label_route': item['vacancy_label_route'],
    }


def _pairing_side_payload(prefix, result):
    return {
        f'{prefix}_concept_id': result['concept_id'],
        f'{prefix}_code': result['code'],
        f'{prefix}_label': result['label'],
        f'{prefix}_matched_label': result['matched_label'],
        f'{prefix}_label_type': result['label_type'],
        f'{prefix}_label_route': result['label_route'],
        f'{prefix}_taxonomy_version': result['taxonomy_version'],
        f'{prefix}_resolution_state': result['resolution_state'],
    }


def _pairing_tier(pairing):
    """The evidence tier of one pairing, read from its own route and policy."""
    if pairing['outcome'] == 'EXACT':
        return _exact_tier(pairing)
    if pairing['outcome'] == 'RELATED':
        return _related_tier(pairing)
    return 'NONE', 99


def _pairing_row(pairing, ordinal):
    """One evaluated pairing in the shape the rows, the JSON, and pages share.

    Every pairing keeps its own concept codes and labels, its own route, reason,
    tier, and evidence, so a reader can see which occupation of the target
    connected to which occupation of the job and why.
    """
    evidence = {}
    if pairing.get('policy'):
        evidence['policy'] = pairing['policy']
    if pairing.get('crosswalk'):
        evidence['crosswalk'] = pairing['crosswalk']
    return {
        **pairing,
        'ordinal': ordinal,
        'relationship_class': pairing['relationship_class'],
        'relationship_type': (pairing.get('policy') or {}).get(
            'relationship_type', ''),
        'evidence': evidence,
    }


def _concepts_for(concept_ids):
    """Fetch the candidate concepts once per comparison, not once per pairing."""
    from core.models import TaxonomyConcept
    return {
        concept.pk: concept for concept in
        TaxonomyConcept.objects.filter(pk__in=set(concept_ids)).select_related(
            'release')
    }


def _compare_pairing(target_result, vacancy_result, base, concepts):
    """One target result against one job result under the applicable rules.

    This never widens to a sibling and never decides in place of another
    pairing: `compare_source` keeps the answer of every pairing.
    """
    target_id = target_result['concept_id']
    vacancy_id = vacancy_result['concept_id']
    target_concept = concepts.get(target_id)
    vacancy_concept = concepts.get(vacancy_id)
    base = {
        **base,
        **_pairing_side_payload('target', target_result),
        **_pairing_side_payload('vacancy', vacancy_result),
    }
    if target_concept is None or vacancy_concept is None:
        return {**base, 'outcome': 'UNRESOLVED',
                'reason': 'candidate_concept_unavailable'}

    if target_id == vacancy_id:
        if 'specialisation' in {target_result['label_type'],
                                vacancy_result['label_type']}:
            policy = _policy_for(
                target_id, vacancy_id, ('SAME_OCCUPATION_SPECIALISATION',))
            if not policy or not policy.admission_allowed:
                return {
                    **base,
                    'outcome': 'UNRESOLVED',
                    'reason': 'specialisation_requires_explicit_policy',
                }
            return {
                **base,
                'outcome': 'EXACT',
                'reason': 'approved_same_occupation_specialisation',
                'route': 'specialisation',
                'policy': _policy_evidence(policy),
            }
        route = 'alias' if 'alternative' in {
            target_result['label_type'],
            vacancy_result['label_type']} else 'preferred'
        return {
            **base,
            'outcome': 'EXACT',
            'reason': f'same_source_concept_{route}',
            'route': route,
        }

    policy = _policy_for(target_id, vacancy_id)
    if policy:
        evidence = _policy_evidence(policy)
        if policy.admission_allowed and policy.relationship_type in {
                'SAME_OCCUPATION', 'SAME_OCCUPATION_SPECIALISATION'}:
            return {
                **base, 'outcome': 'EXACT', 'reason': 'approved_same_occupation_policy',
                'route': 'policy', 'policy': evidence,
            }
        if policy.admission_allowed and policy.relationship_type in {
                'CLOSE_NEIGHBOUR', 'SAFE_BROADER_NARROWER'}:
            return {
                **base, 'outcome': 'RELATED', 'reason': 'approved_candidate_relationship',
                'route': policy.relationship_type.casefold(), 'policy': evidence,
            }
        return {
            **base, 'outcome': 'INCOMPATIBLE',
            'reason': 'relationship_policy_does_not_admit', 'policy': evidence,
        }

    crosswalk = _exact_crosswalk(target_id, vacancy_id)
    if crosswalk:
        return {
            **base,
            'outcome': 'EXACT',
            'reason': 'approved_exact_crosswalk_compatibility',
            'route': 'exact_crosswalk',
            'crosswalk': {
                'id': crosswalk.pk,
                'relation_type': crosswalk.relation_type,
                'mapping_name': crosswalk.mapping_name,
                'mapping_version': crosswalk.mapping_version,
            },
        }
    rule_evidence = _reusable_rule_evidence(target_concept, vacancy_concept)
    if rule_evidence:
        outcome = rule_evidence['candidate_class']
        if outcome in RETAINED_RELATIONSHIP_CLASSES:
            return {
                **base,
                'outcome': outcome,
                'reason': 'approved_reusable_relationship_rule',
                'route': rule_evidence['relationship_type'].casefold(),
                'policy': rule_evidence,
            }
    return {
        **base,
        'outcome': 'INCOMPATIBLE',
        'reason': 'different_resolved_occupations_without_approved_policy',
    }


def compare_source(target, vacancy):
    """One taxonomy's complete relationship set for one target and one job.

    Every occupation result the target kept in this taxonomy is compared with
    every occupation result the job kept in the same taxonomy, under the
    relationship rules that apply to each pairing.  Nothing is chosen and
    nothing stops at the first match:

    * every pairing that establishes a relationship is retained, so a pair can
      hold several `EXACT` and several `RELATED` relationships at once;
    * `outcome` is the strongest confirmed relationship of those, kept so an
      already-built feed can rank a job without reading children;
    * a pairing with no evidence at all is `NOT_ESTABLISHED`, while a pairing an
      active policy explicitly refuses is `DENIED`.  Only the latter is a
      negative finding, and a taxonomy with no result stays silent instead of
      answering "no";
    * one trusted relationship answers for the whole set, even when other
      pairings of the same two texts established nothing, so several confirmed
      connections are several relationships and never an ambiguity that withholds
      the job;
    * a policy-free same-concept label collision is a possibility rather than a
      confirmation (`_pairing_tier` gives it the title tier).  When those
      collisions are the only connections there is, the retained readings
      disagree, so the taxonomy says `AMBIGUOUS` instead of guessing one
      candidate.  Keeping several readings is not itself ambiguity: when none of
      them connected, the taxonomy reports `UNRESOLVED` rather than claiming a
      difference it cannot support.
    """
    base = {
        'source': target.source if target else vacancy.source if vacancy else None,
        'target_resolution_id': target.pk if target else None,
        'vacancy_resolution_id': vacancy.pk if vacancy else None,
        'target_state': target.state if target else 'MISSING',
        'vacancy_state': vacancy.state if vacancy else 'MISSING',
        'target_concept_id': target.selected_concept_id if target else None,
        'vacancy_concept_id': vacancy.selected_concept_id if vacancy else None,
    }
    if target is None or vacancy is None:
        # One side has no answer from this taxonomy at all.  That is silence, not
        # a negative finding, and it never touches the other taxonomy's answer.
        return {**base, 'outcome': 'UNRESOLVED', 'reason': 'missing_source_result',
                'relationships': [], 'pairings': [],
                'target_result_count': 0, 'vacancy_result_count': 0,
                'pairing_count': 0, 'relationship_count': 0,
                'exact_pairing_count': 0, 'related_pairing_count': 0,
                'denied_pairing_count': 0, 'inconclusive_pairing_count': 0}

    target_results = _result_set(target)
    vacancy_results = _result_set(vacancy)
    base = {
        **base,
        'target_candidate_concept_ids': [row['concept_id']
                                         for row in target_results],
        'vacancy_candidate_concept_ids': [row['concept_id']
                                          for row in vacancy_results],
        'target_result_count': len(target_results),
        'vacancy_result_count': len(vacancy_results),
    }
    if not target_results or not vacancy_results:
        # A taxonomy with no result for this text is silent, never
        # contradictory, and never blocks the other taxonomy's answer.
        return {**base, 'outcome': 'UNRESOLVED', 'reason': 'source_unresolved',
                'pairings': [], 'relationships': [], 'pairing_count': 0,
                'relationship_count': 0, 'exact_pairing_count': 0,
                'related_pairing_count': 0, 'denied_pairing_count': 0,
                'inconclusive_pairing_count': 0}

    concepts = _concepts_for(
        [row['concept_id'] for row in target_results]
        + [row['concept_id'] for row in vacancy_results])
    pairings = []
    for target_result in target_results:
        for vacancy_result in vacancy_results:
            pairing = _compare_pairing(
                target_result, vacancy_result, base, concepts)
            pairing['relationship_class'] = _pairing_relationship_class(pairing)
            pairing['evidence_tier'], pairing['tier_rank'] = _pairing_tier(pairing)
            pairings.append(_pairing_row(pairing, len(pairings)))

    evidence = [row['policy'] for row in pairings if row.get('policy')]
    evidence += [{'crosswalk': row['crosswalk']} for row in pairings
                 if row.get('crosswalk')]
    relationships = [row for row in pairings
                     if row['relationship_class'] in RETAINED_RELATIONSHIP_CLASSES]
    denied = [row for row in pairings
              if row['relationship_class'] == DENIED_PAIRING_CLASS]
    inconclusive = [row for row in pairings
                    if row['relationship_class'] == INCONCLUSIVE_PAIRING_CLASS]
    summary = {
        **base,
        'pairings': pairings,
        'relationships': relationships,
        'pairing_count': len(pairings),
        'relationship_count': len(relationships),
        'exact_pairing_count': sum(1 for row in pairings
                                   if row['outcome'] == 'EXACT'),
        'related_pairing_count': sum(1 for row in pairings
                                     if row['outcome'] == 'RELATED'),
        'denied_pairing_count': len(denied),
        'inconclusive_pairing_count': len(inconclusive),
        'policy_evidence': evidence,
    }

    # One trusted relationship is enough to answer for the whole set.  A
    # policy-free same-concept label collision is only a possibility, so it can
    # neither confirm the pair nor stand for the taxonomy's own answer.
    trusted = [row for row in relationships if _trusted(row)]
    connected = all(row['outcome'] in RETAINED_RELATIONSHIP_CLASSES
                    for row in pairings)

    if trusted and connected:
        if len(pairings) == 1:
            # One pairing made and one relationship established: `outcome` is
            # that relationship, with its own route, reason, and evidence.
            strongest = trusted[0]
            return {
                **summary,
                'outcome': strongest['relationship_class'],
                'reason': strongest['reason'],
                'route': strongest.get('route', ''),
                'policy': strongest.get('policy'),
                'crosswalk': strongest.get('crosswalk'),
            }
        # Every candidate pairing connected, so nothing contradicts the reading.
        # The class is the strongest confirmed one, never the weakest: one
        # trusted exact pairing makes the pair the same occupation, and the set
        # is only RELATED when no confirmed pairing is exact.
        outcome = ('EXACT' if any(row['relationship_class'] == 'EXACT'
                                  for row in trusted) else 'RELATED')
        supporting = [row for row in trusted
                      if row['relationship_class'] == outcome] or trusted
        strongest = min(supporting,
                        key=lambda row: (row['tier_rank'], row['ordinal']))
        return {
            **summary,
            'outcome': outcome,
            'reason': 'every_candidate_pairing_is_exact_or_approved_related',
            'route': strongest.get('route', ''),
            # `policy` carries the strongest supporting evidence so the tier
            # selection and the stored assessment can both read it; every
            # pairing keeps its own evidence on its own row.
            'policy': strongest.get('policy'),
            'crosswalk': strongest.get('crosswalk'),
        }

    if trusted:
        # Some pairings established a relationship while others established
        # nothing at all.  Every confirmed relationship stands on its own: all of
        # them stay in `relationships` and the strongest of them answers for this
        # taxonomy.  Several confirmed connections are still a confirmation, so
        # this is never reported as ambiguity that would withhold the job.
        strongest = min(trusted, key=lambda row: (
            0 if row['relationship_class'] == 'EXACT' else 1,
            row['tier_rank'], row['ordinal']))
        return {
            **summary,
            'outcome': strongest['relationship_class'],
            'reason': strongest['reason'],
            'route': strongest.get('route', ''),
            'policy': strongest.get('policy'),
            'crosswalk': strongest.get('crosswalk'),
        }

    if relationships:
        # Only policy-free same-concept label collisions were retained, and the
        # retained readings disagree about which occupation either text
        # denotes.  The disagreement is carried by evidence that was actually
        # compared: each side kept several readings and the collisions do not
        # line up, so no candidate may be guessed into the taxonomy's answer and
        # the set is reported as ambiguity.  A pair with nothing retained never
        # reaches here, and neither does one whose confirmations were already
        # answered above.
        return {
            **summary,
            'outcome': 'AMBIGUOUS',
            'reason': 'candidate_pairings_do_not_agree',
        }

    if denied and not inconclusive:
        # Every pairing this taxonomy could make was explicitly refused by an
        # active policy.  That is a negative finding, and it is the only kind.
        return {
            **summary,
            'outcome': 'INCOMPATIBLE',
            'reason': 'every_pairing_denied_by_policy',
            'policy': denied[0].get('policy'),
        }
    if denied:
        # Some pairings were explicitly refused while others simply had no
        # evidence.  Refusing one pairing is not a refusal of the pair, so this
        # stays a non-finding that still discloses the refusal.
        return {
            **summary,
            'outcome': 'UNRESOLVED',
            'reason': 'partial_explicit_negative_evidence',
            'policy': denied[0].get('policy'),
        }
    # Nothing connected any pairing and nothing refused any pairing.  Two
    # different resolved occupations with no approved policy are no established
    # connection at all: not a denial, and never proof that the two texts are
    # unrelated.  A single reading on each side can still answer with both
    # occupations, because that is what the taxonomy looked at.  Several retained
    # readings cannot: no candidate was committed to, so the honest answer is
    # ambiguity rather than a difference nobody could support.
    if pairings and all(row['outcome'] == 'INCOMPATIBLE' for row in pairings):
        if _kept_several_results(summary):
            # Several readings were kept and none of them connected.  Retaining
            # several readings is exactly why no candidate can be committed to,
            # but it is not evidence that the candidate readings disagree:
            # nothing was compared that could disagree.  Multiplicity on its own
            # is therefore an inconclusive answer, never an ambiguity that
            # withholds the job, and never a difference this taxonomy cannot
            # support.  Only retained, conflicting evidence makes a source
            # ambiguous, and that is the `candidate_pairings_do_not_agree` case
            # above.
            return {
                **summary,
                'outcome': 'UNRESOLVED',
                'reason': 'several_results_none_connected',
            }
        first = pairings[0]
        return {
            **summary,
            'outcome': 'INCOMPATIBLE',
            'reason': first.get('reason') or 'resolved_occupations_are_different',
            'policy': first.get('policy'),
        }
    # Absent evidence, never an "unrelated" finding.
    return {
        **summary,
        'outcome': 'UNRESOLVED',
        'reason': 'no_relationship_between_retained_results',
    }


def _title_tokens(value):
    value = unicodedata.normalize('NFKC', str(value or '')).casefold()
    return tuple(re.findall(r'[a-z0-9]+', value))


def allowed_stage_variant(target_text, cleaned_title):
    """Return an exact-title/stage route without keyword containment."""
    if any(character in str(cleaned_title or '') for character in '/()'):
        return None
    if evaluate_title(target_text).decision == 'BLOCK':
        return None
    target = _title_tokens(target_text)
    title = _title_tokens(cleaned_title)
    if not target or not title:
        return None
    if target == title:
        return 'exact_authored_title'
    if len(target) < 2:
        return None
    variants = (
        ('junior',), ('jr',), ('graduate',), ('trainee',), ('intern',),
        ('internship',), ('entry', 'level'),
    )
    for variant in variants:
        size = len(variant)
        if title[:size] == variant and title[size:] == target:
            return 'allowed_stage_prefix'
        if title[-size:] == variant and title[:-size] == target:
            return 'allowed_stage_suffix'
    return None


def _kept_several_results(item):
    """True when either side of one comparison kept more than one result.

    Read from a single pairing or from a whole source comparison; both carry
    the same two counts.
    """
    return (item.get('target_result_count', 1) > 1
            or item.get('vacancy_result_count', 1) > 1)


def _exact_tier(item):
    """Exact tier for one confirming comparison, from its own route.

    A same-concept shortcut reached without any trusted policy evidence is a
    title-text observation, never a taxonomy confirmation.  When either side
    kept several results the two texts only collided on one label out of a set,
    so the pairing keeps TITLE_ONLY tier and rank 4 and can never present itself
    as taxonomy-confirmed EXACT; it stays a possible candidate, retained and
    disclosed but never promoted.  When both sides kept exactly one result the
    source did commit to that concept, and the same shortcut keeps its
    single-resolver tier.  A relationship backed by an approved policy, an
    approved exact crosswalk, or an approved reusable rule never reaches this
    collision test, so retaining several results can never weaken it.
    """
    if (item.get('route') in {'preferred', 'alias'}
            and not item.get('policy')
            and _kept_several_results(item)):
        return TITLE_MATCH_TIER, 4
    if item.get('route') in {'alias', 'specialisation'}:
        return 'EXACT_ALIAS_SPECIALISATION', 3
    return 'EXACT_SINGLE_RESOLVER', 2


def _related_tier(item):
    relationship_type = (item.get('policy') or {}).get('relationship_type')
    if relationship_type == 'SAFE_BROADER_NARROWER':
        return 'RELATED_SAFE_HIERARCHY', 4
    if relationship_type == 'CLOSE_NEIGHBOUR':
        return 'RELATED_CLOSE_NEIGHBOUR', 3
    return 'RELATED_SINGLE_RESOLVER', 2


def _source_disclosure(row):
    """One source's own answer, without the per-pairing payload it carries."""
    return {key: value for key, value in row.items()
            if key not in {'pairings', 'relationships', 'policy_evidence'}}


def _explicit_negative(row):
    """True only when one source's whole answer is explicit negative evidence.

    Every pairing that source could make was refused by an active policy, it
    established nothing, and it left nothing merely unproven.  That is the same
    condition that makes the source's own answer
    `INCOMPATIBLE`/`every_pairing_denied_by_policy`, so the disclosure and the
    verdict can never disagree.  Anything less is absent evidence: two different
    resolved occupations with no approved policy, an unresolved source, or a
    partial refusal.  Absent evidence is never disagreement, so it can never
    turn a confirmed relationship into a conflict.
    """
    return bool(
        row.get('outcome') == 'INCOMPATIBLE'
        and row.get('denied_pairing_count')
        and not row.get('inconclusive_pairing_count')
        and not row.get('relationship_count'))


def aggregate_relationship_set(comparisons, target_text, cleaned_title):
    """The full multi-occupation verdict for one target and one job.

    Every relationship every taxonomy established is kept.  The aggregate is the
    strongest of them, so a job can be ranked and shown once, and it never
    erases a weaker or differently-sourced relationship:

    * any trusted `EXACT` relationship makes the pair EXACT for ranking, because
      one same-occupation link is enough to call it the same occupation, and
      every other relationship of the pair is still recorded and displayed;
    * only `RELATED` relationships make it RELATED;
    * `confirmed_relationships` holds exactly those trusted relationships, and
      they are the only rows stored as findings or counted as relationships;
    * `title_only_evidence` holds the retained relationships that are not
      confirmations, each with its own tier, so title resemblance stays visible
      without ever being counted or promoted;
    * `UNRELATED` requires every configured source to have answered with
      explicit negative evidence on every pairing it could make.  A silent
      source, an unresolved source, or absent evidence can never produce it;
    * a title that merely resembles the target, including an allowed stage
      variant, is never confirmation and keeps its own class.

    ESCO and OFO never have to agree: each source's relationships are kept as
    they are, and one source's answer can never delete the other's.
    """
    if hasattr(comparisons, 'get'):
        rows = [comparisons[source] for source in SOURCE_VERSIONS]
    else:
        rows = [row for row in comparisons if row]
    relationships, denied, inconclusive, policy_evidence = [], [], [], []
    for row in rows:
        source = row.get('source')
        for item in row.get('relationships', []):
            relationships.append({**item, 'source': item.get('source') or source})
        for item in row.get('pairings', []):
            item = {**item, 'source': item.get('source') or source}
            if item['relationship_class'] == DENIED_PAIRING_CLASS:
                denied.append(item)
            elif item['relationship_class'] == INCONCLUSIVE_PAIRING_CLASS:
                inconclusive.append(item)
        policy_evidence += list(row.get('policy_evidence', []))
    # Dissent means a source answered with genuine explicit negative evidence:
    # an active policy refused every pairing it could make.  A source that merely
    # put the two texts in different occupations without any approved policy
    # established nothing, and absent evidence is never disagreement, so it can
    # never mark another source's confirmation as a conflict.
    dissent = [_source_disclosure(row) for row in rows if _explicit_negative(row)]

    # `_trusted` is the single definition of the boundary between a confirmation
    # and a possibility.  Only confirmed relationships are stored as findings,
    # presented as confirmed relationships on either page, or counted as
    # relationships; every distinct one of them is kept, including concepts that
    # share a label.  A policy-free same-concept label collision is kept here in
    # `relationships` and in the stored comparison payload, and is disclosed as
    # title-only evidence with its own tier, but it can neither confirm the pair
    # nor appear in a confirmed count.
    confirmed = [item for item in relationships if _trusted(item)]
    title_only_evidence = [_title_only_disclosure(item)
                           for item in relationships if not _trusted(item)]
    exact = [item for item in confirmed
             if item['relationship_class'] == 'EXACT']
    related = [item for item in confirmed
               if item['relationship_class'] == 'RELATED']
    exact_sources = {item['source'] for item in exact}
    related_sources = {item['source'] for item in related}

    trusted = confirmed

    if trusted:
        # Every retained relationship stays in the result below. The aggregate
        # tier only reports the strongest trusted reading of them, upgraded to
        # the multi-resolver tier when both taxonomies confirmed on their own.
        strongest = min(trusted,
                        key=lambda item: (item['tier_rank'], item['ordinal']))
        tier, rank = _pairing_tier(strongest)
        if exact:
            final_class = 'EXACT'
            if len(exact_sources) > 1:
                tier, rank = 'EXACT_MULTI_RESOLVER', 1
                reason = 'independent_exact_agreement'
            else:
                # The other taxonomy is silent, produced no relationship, or
                # objected.  Only an explicit negative answer is disclosed as
                # disagreement; silence is disclosed as silence, so the page
                # never claims a taxonomy objected when it did not.
                reason = ('one_exact_other_source_disagrees' if dissent
                          else 'one_exact_other_unresolved')
        else:
            final_class = 'RELATED'
            if len(related_sources) > 1:
                tier, rank = 'RELATED_MULTI_RESOLVER', 1
                reason = 'independent_approved_relationship_support'
            else:
                reason = ('one_related_other_source_disagrees' if dissent
                          else 'one_approved_relationship_other_unresolved')

    elif rows and all(row.get('denied_pairing_count')
                      and not row.get('relationship_count')
                      and not row.get('inconclusive_pairing_count')
                      for row in rows):
        # Exclusion is warranted only when every configured source supplies
        # explicit negative evidence for every pairing it could make.  A silent
        # source, an unresolved source, or missing evidence can never produce
        # this verdict.  Nothing is in conflict here: every source gave the same
        # negative answer.
        final_class, tier, rank = UNRELATED_CLASS, 'NONE', 99
        reason = UNRELATED_REASON_CODE
    else:
        #TITLE_MATCH is title evidence, never confirmation. This branch runs
        # only when no trusted taxonomy relationship exists (`trusted` empty),
        # so a title-only rule can add a visible note but can never invent or
        # promote a taxonomy confirmation.
        stage_route = allowed_stage_variant(target_text, cleaned_title)
        if stage_route:
            final_class, tier, rank = TITLE_MATCH_CLASS, TITLE_MATCH_TIER, 4
            reason = stage_route
        else:
            # Nothing connected any pairing.  That is absent evidence, and it is
            # reported as uncertainty, never as an unrelated finding.
            final_class, tier, rank = 'UNRESOLVED', 'NONE', 99
            reason = 'no_approved_relationship'

    return {
        'final_class': final_class,
        'evidence_tier': tier,
        'tier_rank': rank,
        'reason_code': reason,
        'policy_evidence': policy_evidence,
        'conflicting_evidence': dissent,
        # The full set, not a summary: every established relationship of the
        # pair, plus every pairing that produced nothing or was refused.
        'relationships': relationships,
        # Only the confirmations: the rows that may be stored as findings, shown
        # as confirmed relationships on either page, and counted.
        'confirmed_relationships': confirmed,
        # Retained title-only evidence, disclosed with its tier and never
        # counted: a policy-free same-concept label collision.
        'title_only_evidence': title_only_evidence,
        'denied_pairings': denied,
        'inconclusive_pairings': inconclusive,
        'source_summaries': {row.get('source'): _source_disclosure(row)
                             for row in rows},
    }


def aggregate_comparisons(comparisons, target_text, cleaned_title):
    """The six-value aggregate of one pair's full relationship set.

    Kept for ranking and for existing readers: it is the strongest reading of
    the set.  A caller that needs every relationship reads
    ``aggregate_relationship_set`` or the stored finding rows.
    """
    result = aggregate_relationship_set(comparisons, target_text, cleaned_title)
    return (result['final_class'], result['evidence_tier'], result['tier_rank'],
            result['reason_code'], result['policy_evidence'],
            result['conflicting_evidence'])


@lru_cache(maxsize=32)
def _policy_rules_fingerprint(policy_version, row_count, latest_update):
    """Every candidate relationship rule of one policy version, cached.

    This component is the only part of the relevant-policy fingerprint that
    does not depend on the pair being assessed: it is a global read of the rule
    table for one `policy_version`, identical for every target and every job,
    so a rebuild would otherwise re-read and re-serialise exactly the same rows
    once per pair.  It is cached under its own validity token rather than under
    `policy_version` alone, so an approved, retired, or edited rule is still
    seen: the token changes whenever the row set changes, and a different
    `policy_version` is a different key entirely.

    `row_count` and `latest_update` are passed in rather than read here so that
    deciding whether the cache is valid never requires the query this function
    exists to avoid.  The result is copied on the way out so a caller can never
    mutate the cached value.
    """
    return tuple(OccupationRelationshipRule.objects.filter(
        policy_version=policy_version,
    ).order_by('pk').values_list(
        'pk', 'name', 'evidence_source', 'taxonomy_source',
        'evidence_relation_type', 'candidate_relationship_type', 'direction',
        'candidate_class', 'admission_allowed', 'active', 'updated_at'))


def _relevant_policy_rules(policy_version):
    """The rule rows of one policy version, reused while that set is unchanged.

    The cache is keyed on the policy version together with a token describing
    the current rule set, so it is reused only while the exact same rows are
    still current, and it is re-read as soon as any rule is added, changed, or
    removed.  An approved rule therefore still invalidates every stored verdict
    that depends on it, exactly as an uncached read would.
    """
    summary = OccupationRelationshipRule.objects.filter(
        policy_version=policy_version,
    ).aggregate(
        row_count=Count('pk'), latest_update=Max('updated_at'))
    return list(_policy_rules_fingerprint(
        policy_version, summary['row_count'], summary['latest_update']))


def _relevant_policy_fingerprint(source_rows):
    """Every policy fact that could change any pairing of these result sets.

    Keyed on the full retained result set of each source rather than on a chosen
    concept, because a retained result that is not connected today can become
    connected the moment a policy or crosswalk row is approved.
    """
    concept_ids = sorted({
        concept_id for row in source_rows if row
        for concept_id in _candidate_ids(row)
    } | {
        row.selected_concept_id for row in source_rows
        if row and row.selected_concept_id
    })
    policies = list(OccupationRelationshipPolicy.objects.filter(
        policy_version=CANDIDATE_RELATIONSHIP_POLICY_VERSION,
        subject_concept_id__in=concept_ids,
        object_concept_id__in=concept_ids,
    ).order_by('pk').values_list(
        'pk', 'relationship_type', 'direction', 'admission_allowed', 'active', 'updated_at'))
    crosswalks = list(TaxonomyCrosswalk.objects.filter(
        active=True,
        approval_status='approved_identity',
        relation_type__in=('exact', 'exact_isco'),
        source_concept_id__in=concept_ids,
        target_concept_id__in=concept_ids,
    ).order_by('pk').values_list(
        'pk', 'relation_type', 'direction', 'mapping_name', 'mapping_version'))
    rules = _relevant_policy_rules(CANDIDATE_RELATIONSHIP_POLICY_VERSION)
    relations = list(TaxonomyRelation.objects.filter(
        subject_id__in=concept_ids,
        object_id__in=concept_ids,
    ).order_by('pk').values_list('pk', 'subject_id', 'relation_type', 'object_id'))
    return {
        'policies': policies,
        'rules': rules,
        'direct_relations': relations,
        'exact_crosswalks': crosswalks,
    }


def _assessment_dependency_hash(target_resolution, revision, target_sources,
                                vacancy_sources):
    """The one dependency hash that makes a stored pair verdict fresh.

    Shared with Career Intelligence, which reads stored pair assessments and
    must be able to tell a current verdict from a stale one without writing
    anything.
    """
    source_rows = list(target_sources.values()) + list(vacancy_sources.values())
    return _digest({
        'target_source_dependencies': {
            source: row.dependency_hash for source, row in target_sources.items()},
        'vacancy_source_dependencies': {
            source: row.dependency_hash for source, row in vacancy_sources.items()},
        'relevant_policy': _relevant_policy_fingerprint(source_rows),
        'target_text': target_resolution.authored_text,
        'cleaned_title': revision.cleaned_title,
        'aggregation_version': RELATIONSHIP_AGGREGATION_VERSION,
        'relationship_policy_version': CANDIDATE_RELATIONSHIP_POLICY_VERSION,
    })


def _finding_values(source, relationship):
    """One established relationship of the pair, as one stored row."""
    return {
        'source': source,
        'ordinal': relationship['ordinal'],
        'target_concept_id': relationship['target_concept_id'],
        'target_code': relationship['target_code'],
        'target_label': relationship['target_label'],
        'target_matched_label': relationship['target_matched_label'],
        'target_label_type': relationship['target_label_type'],
        'target_label_route': relationship['target_label_route'],
        'target_resolution_state': relationship['target_resolution_state'],
        'target_taxonomy_version': relationship['target_taxonomy_version'],
        'job_concept_id': relationship['vacancy_concept_id'],
        'job_code': relationship['vacancy_code'],
        'job_label': relationship['vacancy_label'],
        'job_matched_label': relationship['vacancy_matched_label'],
        'job_label_type': relationship['vacancy_label_type'],
        'job_label_route': relationship['vacancy_label_route'],
        'job_resolution_state': relationship['vacancy_resolution_state'],
        'job_taxonomy_version': relationship['vacancy_taxonomy_version'],
        'relationship_class': relationship['relationship_class'],
        'relationship_type': relationship['relationship_type'],
        'route': relationship.get('route', ''),
        'reason_code': relationship['reason'],
        'evidence_tier': relationship['evidence_tier'],
        'tier_rank': relationship['tier_rank'],
        'evidence': relationship['evidence'],
        'aggregation_version': RELATIONSHIP_AGGREGATION_VERSION,
        'relationship_policy_version': CANDIDATE_RELATIONSHIP_POLICY_VERSION,
    }


def _replace_findings(assessment, comparisons, relationships=None):
    """Store every established relationship this pair produced, and only those.

    A finding row is a relationship CareerGPS established, so only a trusted
    relationship becomes a row.  The rows are the pair's confirmed relationship
    set: an `EXACT` from ESCO and a `RELATED` from OFO are two rows, and
    neither overwrites the other; two occupations that share a label stay two
    relationships.  Pairings that established nothing get no row, because
    absent evidence is not a finding, and neither does a retained title-only
    collision: that one established nothing, so it stays in the pair's stored
    comparison payload with all of its evidence and is disclosed from
    `title_only_evidence`.
    """
    if relationships is None:
        relationships = aggregate_relationship_set(
            comparisons, assessment.target_resolution.authored_text,
            assessment.revision.cleaned_title)['confirmed_relationships']
    established = [item for item in relationships if _trusted(item)]
    assessment.findings.all().delete()
    TargetOpportunityRelationshipFinding.objects.bulk_create([
        TargetOpportunityRelationshipFinding(
            assessment=assessment, **_finding_values(item['source'], item))
        for item in established])
    return established


def _assessment_summary(comparisons, relationships):
    """The stored counts of one pair's confirmed relationship set.

    The counts summarise the finding rows, so they never include a retained
    title-only collision: a possibility is disclosed with its tier instead of
    being counted as a relationship.
    """
    sources = list(comparisons.values())
    established = [item for item in relationships if _trusted(item)]
    return {
        'target_result_count': sum(row.get('target_result_count', 0) for row in sources),
        'job_result_count': sum(row.get('vacancy_result_count', 0) for row in sources),
        'pairing_count': sum(row.get('pairing_count', 0) for row in sources),
        'relationship_count': len(established),
        'exact_relationship_count': sum(
            1 for item in established if item['relationship_class'] == 'EXACT'),
        'related_relationship_count': sum(
            1 for item in established if item['relationship_class'] == 'RELATED'),
        'denied_pairing_count': sum(
            row.get('denied_pairing_count', 0) for row in sources),
        'inconclusive_pairing_count': sum(
            row.get('inconclusive_pairing_count', 0) for row in sources),
    }


@transaction.atomic
def assess_target_vacancy(target_resolution, revision, target_sources=None):
    """Assess one authored target against one clean revision.

    `target_sources` may carry the target's already-resolved per-source rows,
    which lets a caller that has resolved them for a whole pass (every
    revision is compared against the same authored text) pass them in instead of
    paying for the same resolution once per revision.  It is exactly the value
    `resolve_target_sources` returns for this target, and it is only read here,
    so omitting it resolves the target exactly as before.
    """
    if evaluate_revision(revision).blocked:
        # Senior roles are excluded read-only, before any assessment is
        # written; no stored admission decision is consulted or produced.
        return None
    if target_sources is None:
        target_sources = resolve_target_sources(target_resolution)
    vacancy_container = resolve_vacancy_sources(revision)
    vacancy_sources = {
        row.source: row for row in vacancy_container.source_resolutions.all()
    }
    # The dependency hash is the one freshness authority, and it is built only
    # from data this function has already resolved above: the two source
    # resolution hashes, the relevant policy fingerprint, the authored target
    # text, the cleaned title, and both policy versions.  Nothing inside
    # `compare_source` or `aggregate_relationship_set` is read that the hash does
    # not already cover, so the stored verdict can be validated before any
    # comparison runs rather than after it.
    dependency_hash = _assessment_dependency_hash(
        target_resolution, revision, target_sources, vacancy_sources)
    current = TargetOpportunityRelationshipAssessment.objects.filter(
        target_resolution=target_resolution, revision=revision).first()
    if current and current.dependency_hash == dependency_hash:
        # The verdict is current.  Its confirmed relationship rows are the other
        # half of that verdict, and `relationship_count` is written from the
        # same trusted set that `_replace_findings` stores one row per, so the two
        # agreeing is exactly the condition that the rows are present and in step
        # with the summary.  When they agree the stored assessment is already the
        # answer this function would recompute, and no comparison or aggregation
        # is run for it.
        if current.relationship_count == current.findings.count():
            return current
        # The verdict itself is current, but its confirmed relationship rows are
        # missing or out of step with the summary.  Fall through and rebuild them
        # from freshly computed comparisons instead of trusting the counts.
    comparisons = {
        source: compare_source(target_sources.get(source), vacancy_sources.get(source))
        for source in SOURCE_VERSIONS
    }
    result = aggregate_relationship_set(
        comparisons,
        target_resolution.authored_text, revision.cleaned_title,
    )
    relationships = result['confirmed_relationships']
    if current and current.dependency_hash == dependency_hash:
        _replace_findings(current, comparisons, relationships)
        return current

    assessment, _created = TargetOpportunityRelationshipAssessment.objects.update_or_create(
        target_resolution=target_resolution,
        revision=revision,
        defaults={
            'final_class': result['final_class'],
            'evidence_tier': result['evidence_tier'],
            'tier_rank': result['tier_rank'],
            'reason_code': result['reason_code'],
            **_assessment_summary(comparisons, relationships),
            'ofo_comparison': comparisons['ofo'],
            'esco_comparison': comparisons['esco'],
            'policy_evidence': result['policy_evidence'],
            'conflicting_evidence': result['conflicting_evidence'],
            'aggregation_version': RELATIONSHIP_AGGREGATION_VERSION,
            'relationship_policy_version': CANDIDATE_RELATIONSHIP_POLICY_VERSION,
            'dependency_hash': dependency_hash,
            'diagnostics': {
                'shadow_only': True,
                'source_outcomes': {
                    source: value['outcome'] for source, value in comparisons.items()},
                'source_relationship_counts': {
                    source: value['relationship_count']
                    for source, value in comparisons.items()},
                # The retained title-only collisions of this pair.  They are not
                # findings and are not counted as relationships, so they are
                # disclosed here with their tier, while the full per-pairing
                # evidence stays in the two comparison payloads above.
                'confirmed_relationship_count': len(relationships),
                'title_only_evidence': result['title_only_evidence'],
                'title_only_evidence_count': len(result['title_only_evidence']),
            },
        },
    )
    _replace_findings(assessment, comparisons, relationships)
    return assessment


def fresh_assessment_for(target_resolution, revision, *, target_sources=None):
    """Stored assessment when it is exactly what assess_target_vacancy returns.

    Mirrors the reuse gate inside ``assess_target_vacancy``: the dependency
    hash must match the current target/vacancy source hashes and policies,
    and the stored summary must still agree with its persisted findings.
    Anything stale or out of step returns None so the caller recomputes.
    """
    existing = TargetOpportunityRelationshipAssessment.objects.filter(
        target_resolution=target_resolution, revision=revision).first()
    if existing is None:
        return None
    try:
        from core.models import OpportunityTitleResolution
        container = OpportunityTitleResolution.objects.filter(
            revision=revision).prefetch_related('source_resolutions').first()
        vacancy_sources = (
            {row.source: row for row in container.source_resolutions.all()}
            if container else {})
        expected = _assessment_dependency_hash(
            target_resolution, revision,
            target_sources or resolve_target_sources(target_resolution),
            vacancy_sources)
    except Exception:
        return None
    if existing.dependency_hash != expected:
        return None
    if existing.relationship_count != existing.findings.count():
        return None
    return existing


def stored_pair_verdicts(profile, revisions):
    """Stored, still-fresh pair verdicts keyed by (ordinal, revision id).

    The occupational pipeline already assessed every authored target against
    every current revision, so a page GET reads those verdicts and never runs a
    comparison.  A verdict counts only when its recorded dependency hash still
    equals what the stored resolutions would produce now; a stale or
    half-refreshed pair is reported as missing coverage by the caller rather
    than presented as current, and nothing is computed or written here.

    One reader serves both pages.  Each verdict carries its class, its confirmed
    relationships (the stored findings, which are the trusted ones only), and
    the retained title-only evidence with its tier.
    """
    from core.models import (
        CareerTargetResolution, OccupationSourceResolution,
        OpportunityTitleResolution,
        TargetOpportunityRelationshipAssessment,
        TargetOpportunityRelationshipFinding,
    )

    revision_ids = [revision.pk for revision in revisions]
    if not revision_ids:
        return {}
    containers = {
        row.ordinal: row for row in CareerTargetResolution.objects.filter(
            profile=profile).only('pk', 'ordinal', 'authored_text')}
    if not containers:
        return {}
    revisions_by_id = {revision.pk: revision for revision in revisions}
    target_sources = {}
    # `diagnostics` carries the retained result set, which is what the
    # freshness fingerprint is keyed on; selecting it here keeps the check from
    # issuing one query per stored resolution.
    for row in OccupationSourceResolution.objects.filter(
            target_resolution_id__in=[row.pk for row in containers.values()],
    ).only('target_resolution_id', 'source', 'dependency_hash',
           'selected_concept_id', 'diagnostics'):
        target_sources.setdefault(row.target_resolution_id, {})[row.source] = row
    vacancy_sources = {}
    # `.only()` defers concrete fields; it cannot traverse a relation to create
    # an attribute, so `opportunity_resolution__revision_id` was never available
    # on the row and reading it raised. The FK column is loaded instead, and the
    # revision each container points at is read from a small map keyed by that
    # FK. `OpportunityTitleResolution` is the container these rows hang off.
    revision_by_container = dict(
        OpportunityTitleResolution.objects.filter(
            revision_id__in=revision_ids,
        ).values_list('pk', 'revision_id'))
    for row in OccupationSourceResolution.objects.filter(
            opportunity_resolution__revision_id__in=revision_ids,
    ).only('opportunity_resolution_id', 'source', 'dependency_hash',
           'selected_concept_id', 'diagnostics'):
        revision_id = revision_by_container.get(row.opportunity_resolution_id)
        if revision_id is None:
            continue
        vacancy_sources.setdefault(revision_id, {})[row.source] = row

    verdicts = {}
    rows = TargetOpportunityRelationshipAssessment.objects.filter(
        target_resolution__profile=profile,
        revision_id__in=revision_ids,
        aggregation_version=RELATIONSHIP_AGGREGATION_VERSION,
        relationship_policy_version=CANDIDATE_RELATIONSHIP_POLICY_VERSION,
    ).values(
        'pk', 'target_resolution__ordinal', 'target_resolution__authored_text',
        'revision_id', 'final_class', 'evidence_tier', 'tier_rank',
        'reason_code', 'dependency_hash', 'diagnostics')
    fresh = {}
    for row in rows:
        ordinal = row['target_resolution__ordinal']
        container = containers.get(ordinal)
        revision = revisions_by_id.get(row['revision_id'])
        if container is None or revision is None:
            continue
        if row['target_resolution__authored_text'] != container.authored_text:
            continue
        target_rows = target_sources.get(container.pk) or {}
        vacancy_rows = vacancy_sources.get(row['revision_id']) or {}
        if not all(source in target_rows and source in vacancy_rows
                   for source in SOURCE_VERSIONS):
            continue
        if _assessment_dependency_hash(
                container, revision, target_rows, vacancy_rows,
        ) != row['dependency_hash']:
            continue
        diagnostics = row['diagnostics'] or {}
        verdicts[(ordinal, row['revision_id'])] = fresh[row['pk']] = {
            'class': row['final_class'],
            'tier': row['evidence_tier'],
            'rank': row['tier_rank'],
            'reason': row['reason_code'],
            # Every confirmed relationship the pair established, not only the
            # strongest: the aggregate class ranks the job, these describe it.
            'relationships': [],
            # Retained title-only evidence, disclosed with its tier and never
            # counted as a relationship.
            'title_only': list(diagnostics.get('title_only_evidence') or []),
        }
    if fresh:
        findings = TargetOpportunityRelationshipFinding.objects.filter(
            assessment_id__in=list(fresh),
        ).order_by('source', 'ordinal', 'pk').values(
            'assessment_id', 'source', 'ordinal', 'relationship_class',
            'relationship_type', 'reason_code',
            'target_code', 'target_label', 'target_matched_label',
            'job_code', 'job_label', 'job_matched_label')
        for finding in findings:
            if finding['relationship_class'] not in RETAINED_RELATIONSHIP_CLASSES:
                continue
            fresh[finding['assessment_id']]['relationships'].append(finding)
    return verdicts
