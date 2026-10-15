"""Legacy assignment-based market snapshots are retired."""

AGGREGATION_VERSION = "retired-clean-job-v1"


def build_snapshots(*args, **kwargs):
    del args, kwargs
    raise RuntimeError("Legacy market snapshots are retired pending a clean-revision aggregation policy.")
