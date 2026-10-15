"""Stable raw-version fingerprints without altering stored source evidence."""

from .normalization import content_hash


def fingerprint_material(adapter, record):
    """Return adapter-specific stable material, falling back to exact raw data."""
    hook = getattr(adapter, "fingerprint_material", None)
    if hook is None:
        return record.payload, record.raw_content
    material = hook(record)
    if not isinstance(material, tuple) or len(material) != 2:
        raise TypeError("fingerprint_material() must return (payload, raw_content).")
    return material


def stable_content_hash(adapter, record):
    payload, raw_content = fingerprint_material(adapter, record)
    return content_hash(payload, raw_content)
