"""Retired compatibility surface; persisted opportunity assignments were removed."""
import hashlib
import json

from core.models import TaxonomyRelease

ASSIGNMENT_VERSION = "retired-clean-job-v1"


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()


def taxonomy_versions():
    return dict(TaxonomyRelease.objects.filter(is_active=True).values_list("source", "version"))


def assignment_candidates(*args, **kwargs):
    del args, kwargs
    return []


def assignment_is_current(*args, **kwargs):
    del args, kwargs
    return False


def update_assignments(*args, **kwargs):
    del args, kwargs
    raise RuntimeError("Retired: resolve immutable clean revisions with professional_resolution.")
