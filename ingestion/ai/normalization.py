from __future__ import annotations

from core.taxonomies.normalization import normalize_term
from core.taxonomies.resolver import build_taxonomy_context

from ingestion.models import OpportunityCriterionTaxonomyMatch


_KINDS = {
    "skill": ("skill", "skill_group"),
    "occupation": ("occupation", "occupation_group"),
    # Experience remains an experience criterion. We only normalize its
    # subject/value so future experience-context matching can use trusted
    # occupation/skill concepts without pretending the employer asked for a
    # different requirement type.
    "experience": ("occupation", "occupation_group", "skill", "skill_group"),
}


def normalize_extraction_taxonomies(extraction):
    """Persist conservative taxonomy identity/label normalization once per job."""
    criteria = list(extraction.criteria.filter(criterion_type__in=_KINDS).order_by("id"))
    if not criteria:
        return 0

    terms_by_kind = {kind: [] for kinds in _KINDS.values() for kind in kinds}
    for criterion in criteria:
        for kind in _KINDS[criterion.criterion_type]:
            terms_by_kind[kind].append(criterion.raw_value)

    context = build_taxonomy_context(terms_by_kind)
    created = 0
    for criterion in criteria:
        kinds = _KINDS[criterion.criterion_type]
        identity = context.resolve_identity(criterion.raw_value, kinds)
        candidates = identity or context.resolve(criterion.raw_value, kinds)
        match_type = "exact" if identity else "related"
        for concept in candidates:
            _, was_created = OpportunityCriterionTaxonomyMatch.objects.get_or_create(
                criterion=criterion,
                concept_id=concept.id,
                defaults={"match_type": match_type},
            )
            created += int(was_created)
        criterion.normalized_value = normalize_term(criterion.raw_value)
        criterion.save(update_fields=["normalized_value"])
    return created
