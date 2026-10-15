"""Request-cycle safety net for the application-owned recompute queue.

The save-time trigger (``core.services.integrity`` → ``queue_worker``) starts
a drain in the process that handled the save.  If that process exits or the
dev server autoreloads before the drain finishes, PENDING tasks would sit
forever without an operator.  This middleware closes that gap without a
scheduler and without a second queue:

* on each request, at most once per ``CAREERGPS_QUEUE_SAFETY_NET_INTERVAL``
  seconds per process, it runs one cheap ``EXISTS``-style read against the
  existing ``EvaluationRecomputeTask`` table (``claimable_work_exists``);
* if claimable work exists it calls the very same ``request_queue_drain``
  entry point the save-time trigger uses, which coalesces with any in-flight
  drain and spawns at most one background thread;
* the request itself is never blocked by rebuild work -- the drain runs on
  the worker thread, and when no work is pending the middleware costs one
  throttled query and returns.

Reliability model (stated honestly): execution is recovered by *any* request
served by *any* running Django process after a reload/crash.  If no request
ever arrives, no drain runs -- the same bound as the request/response
architecture itself.  Everything else (claim, lease, retry, supersession,
stale-build guards) lives in the existing queue machinery.
"""
from __future__ import annotations

import logging
import threading
import time

from django.conf import settings

logger = logging.getLogger(__name__)

_state_lock = threading.Lock()
_last_check = 0.0


def _reset_throttle():
    """Testing hook: forget the throttle timestamp."""
    global _last_check
    with _state_lock:
        _last_check = 0.0


def _throttled():
    """True when this process already checked the queue within the interval."""
    global _last_check
    interval = float(getattr(
        settings, 'CAREERGPS_QUEUE_SAFETY_NET_INTERVAL', 5) or 0)
    now = time.monotonic()
    with _state_lock:
        if now - _last_check < interval:
            return True
        _last_check = now
        return False


def maybe_drain_from_request():
    """Re-arm the queue drain if claimable work is waiting.  Never raises."""
    try:
        if not getattr(settings, 'CAREERGPS_QUEUE_SAFETY_NET', False):
            return False
        if _throttled():
            return False
        from core.services.queue_worker import (
            claimable_work_exists,
            request_queue_drain,
        )
        if not claimable_work_exists():
            return False
        request_queue_drain(reason='request-safety-net')
        return True
    except Exception:  # noqa: BLE001 - the safety net must never break a request
        logger.exception('careergps queue safety-net check failed')
        return False


class QueueDrainSafetyNetMiddleware:
    """Re-arm the application-owned drain on request cycles (see module doc)."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        # Pre-response: work is recovered even if the view raises, and the
        # check itself never waits on rebuild execution (thread spawn only).
        maybe_drain_from_request()
        return self.get_response(request)