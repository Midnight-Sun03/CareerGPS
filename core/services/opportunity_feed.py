"""Compatibility facade for the materialized professional feed."""
from core.services.professional_evaluations import professional_feed_queryset


def _row(evaluation):
    return {
        "opportunity": evaluation.opportunity,
        "clean_revision": evaluation.clean_revision,
        "evaluation": evaluation,
        "discovery_category": evaluation.discovery_category,
        "recommendation_band": evaluation.band,
        "major_gap_count": evaluation.major_gap_count,
        "professional_score": evaluation.professional_score,
    }


def evaluate_personalized_feed(profile, *, queryset=None):
    rows = professional_feed_queryset(profile)
    if queryset is not None:
        rows = rows.filter(opportunity__in=queryset)
    values = [_row(row) for row in rows]
    return values, values


def personalized_opportunities(profile, *, queryset=None):
    return evaluate_personalized_feed(profile, queryset=queryset)[0]
