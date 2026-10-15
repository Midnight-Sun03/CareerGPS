"""Retired compatibility surface; discovery comes from materialized evaluations."""


def discovery_reasons(*args, **kwargs):
    del args, kwargs
    return []


def evidence_fallback_reason(*args, **kwargs):
    del args, kwargs
    return None
