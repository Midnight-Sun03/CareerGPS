from __future__ import annotations

import re
import unicodedata


def _normalize_text(text: str) -> str:
    text = unicodedata.normalize("NFKC", text)
    return re.sub(r"\s+", " ", text.strip()).casefold()


def normalize_term(value: object) -> str:
    """Conservative label normalization used only for trusted exact identity.

    Punctuation is intentionally preserved: C, C++, C# and similarly named
    technologies must never collapse into one concept accidentally.
    """
    return _normalize_text(str(value or ""))


def split_multivalue_labels(value: object) -> tuple[str, ...]:
    """Split ESCO/OFO multi-value label cells without inventing aliases."""
    text = str(value or "").replace("\r\n", "\n").replace("\r", "\n")
    # ESCO alternative labels are commonly newline-separated. Some OFO exports
    # use semicolons. Commas are *not* split because they can be part of titles.
    values = re.split(r"\n+|\s*;\s*", text)
    seen = set()
    result = []
    for value in values:
        display = " ".join(value.split())
        normalized = normalize_term(display)
        if display and normalized not in seen:
            seen.add(normalized)
            result.append(display)
    return tuple(result)
