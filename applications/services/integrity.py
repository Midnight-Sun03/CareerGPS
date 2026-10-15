from __future__ import annotations

import re


_NUMBER_RE = re.compile(r"(?<!\w)\d+(?:[.,]\d+)?%?(?!\w)")


def _walk_strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for child in value.values():
            yield from _walk_strings(child)
    elif isinstance(value, (list, tuple)):
        for child in value:
            yield from _walk_strings(child)


def candidate_number_tokens(candidate_bundle):
    source = "\n".join(fact["text"] for fact in candidate_bundle["facts"])
    return set(_NUMBER_RE.findall(source))


def unsupported_numeric_claims(generated_data, candidate_bundle):
    allowed = candidate_number_tokens(candidate_bundle)
    generated = set()
    for text in _walk_strings(generated_data):
        generated.update(_NUMBER_RE.findall(text))
    return sorted(token for token in generated if token not in allowed)


def enforce_numeric_integrity(generated_data, candidate_bundle):
    unsupported = unsupported_numeric_claims(generated_data, candidate_bundle)
    if unsupported:
        raise ValueError(
            "Generated document introduced unsupported numeric claims: "
            + ", ".join(unsupported)
        )
