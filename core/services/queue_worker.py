"""Self-triggered background drain for the evaluation recompute queues.

A target change must never leave a user in UPDATING_INTENT just because no
operator ran a drain command.  This module owns the smallest safe bridge
between the application event (enqueue) and background execution:

* every successful enqueue of a feed-v2, provisional-v1, or opportunity task
  schedules one coalesced background drain pass;
* the drain pass reuses the existing ``drain_v2_queue`` /
  ``drain_provisional_queue`` claim-and-process machinery untouched, so
  claiming, leases, retries, supersession, idempotency, and the v2/provisional
  separation are exactly what the management command uses;
* duplicate triggers collapse: a single ``_drain_running`` flag means N rapid
  saves produce at most one in-flight drain, and the drain itself loops until
  both queues report no immediately-claimable work;
* the request-cycle safety net (``core.middleware``) re-arms this same entry
  point on later requests, so PENDING tasks survive the exit or autoreload of
  the process that handled the original save.

Locking rule (regression: synchronous-mode self-deadlock): ``_lock`` guards
only the tiny flag/worker state and is NEVER held while ``_drain_loop`` runs.
``_drain_loop`` acquires ``_lock`` itself at every pass boundary, and the
lock is non-reentrant, so calling it from inside ``with _lock:`` would block
the calling thread forever.  The synchronous path therefore claims drain
ownership (``_drain_running = True``) under the lock, releases the lock, and
only then runs the loop.

Runs outside the request thread in production (one daemon thread per process)
and does nothing synchronous in the request path beyond setting a flag.
"""
from __future__ import annotations

import logging
import threading

logger = logging.getLogger(__name__)

_lock = threading.Lock()
_drain_requested = False
_drain_running = False
_worker = None
_synchronous_mode = False


def set_synchronous_mode(enabled):
    """Allow tests to run the drain in the current thread (no background thread).

    The background worker thread is what makes the production request path
    fast, but in tests it collides with the test's own database transaction
    on SQLite (SELECT FOR UPDATE holds a lock the test needs).  Turning on
    synchronous mode runs ``_drain_loop`` in the test's thread so the
    enqueue → trigger → drain lifecycle is deterministic and lock-free.
    """
    global _synchronous_mode
    _synchronous_mode = bool(enabled)


def join_worker(*, timeout=60):
    """Block until any in-flight drain finishes (tests and scripts).

    Returns True when no worker is running.  Production request code never
    calls this; it exists so tests can deterministically await the
    background lifecycle instead of sleeping.
    """
    import time
    deadline = time.monotonic() + max(0, timeout)
    while True:
        with _lock:
            worker = _worker
            running = _drain_running or (
                worker is not None and worker.is_alive())
        if not running:
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.2)


def claimable_work_exists(now=None):
    """True when any queue row is claimable right now.

    Used by the request-cycle safety net so a drain thread is only re-armed
    when genuinely claimable work exists (PENDING and due, or RUNNING with an
    expired lease awaiting reclaim).  It reads the same
    ``EvaluationRecomputeTask`` table the drain claims from -- it is a read
    gate, not a second queue.
    """
    from django.db.models import Q
    from django.utils import timezone
    from core.models import EvaluationRecomputeTask
    now = now or timezone.now()
    return EvaluationRecomputeTask.objects.filter(
        Q(state='PENDING', available_at__lte=now)
        | Q(state='RUNNING', lease_expires_at__lt=now)
    ).exists()


def request_queue_drain(*, reason=''):
    """Schedule one coalesced drain of both recompute queues.

    Safe to call any number of times from any thread: concurrent requests
    collapse into the drain already running (or about to run).  Never raises
    into the caller -- queue processing must not break the request that
    scheduled it.

    Tests can call ``set_synchronous_mode(True)`` first, in which case the
    drain loop runs in the current thread and blocks until it completes.  This
    keeps tests lock-free on SQLite while preserving the exact enqueue →
    drain lifecycle.  The loop is always invoked OUTSIDE ``_lock``: calling it
    while holding the non-reentrant ``_lock`` self-deadlocks, because
    ``_drain_loop`` acquires the same lock at its pass boundary.
    """
    global _drain_requested, _drain_running
    run_inline = False
    try:
        with _lock:
            _drain_requested = True
            if _drain_running:
                # A drain is already in flight (a background thread, or this
                # very thread in synchronous mode).  Every pass re-reads
                # _drain_requested, so this request is coalesced into it.
                return
            if _synchronous_mode:
                # Claim ownership under the lock; run the loop after release.
                _drain_running = True
                run_inline = True
            else:
                # Ownership (not thread aliveness) is the spawn gate: it is
                # claimed and released atomically with the flag check inside
                # _drain_loop, so a trigger that sees _drain_running False
                # knows the previous worker has committed to exiting and will
                # not consume this flag -- spawning here closes the wakeup.
                _start_locked(reason)
        if run_inline:
            _drain_loop(reason)
    except Exception:  # noqa: BLE001 - scheduling must never break callers
        logger.exception(
            'careergps queue-drain trigger failed (reason=%s)', reason)
        if run_inline:
            # _drain_loop clears _drain_running on its normal and error exits;
            # if it failed before reaching any cleanup, release ownership here
            # so the queue is not wedged in a permanent "running" state.
            with _lock:
                _drain_running = False


def _start_locked(reason):
    """Spawn the daemon drain thread.  Caller must hold ``_lock``.

    ``_drain_running`` is claimed here, under the lock, so a second trigger
    arriving before the thread reaches its first pass boundary coalesces
    instead of spawning a duplicate worker.  ``_drain_loop`` releases it.
    """
    global _worker, _drain_running
    _drain_running = True
    _worker = threading.Thread(
        target=_drain_loop, name='careergps-queue-drain',
        args=(reason,), daemon=True)
    _worker.start()


def _drain_loop(reason):
    """Run drain passes until both queues are quiet, then release ownership.

    Ownership (``_drain_running``) is checked and released atomically with the
    ``_drain_requested`` flag under ``_lock``, so a save that lands between
    the last pass and the exit check is never dropped: either it sets the
    flag before we look (we run another pass) or after we release (the next
    trigger spawns a fresh drain).
    """
    global _drain_requested, _drain_running
    from django.db import connection
    try:
        while True:
            with _lock:
                if not _drain_requested:
                    _drain_running = False
                    return
                _drain_requested = False
            try:
                from core.services.feed_queue import drain_v2_queue
                from core.services.provisional_feed import drain_provisional_queue
                v2_done = drain_v2_queue(
                    limit=100, worker_id='auto-drain')
                provisional_done = drain_provisional_queue(
                    limit=100, worker_id='auto-drain')
                if v2_done or provisional_done:
                    # Work was claimed: loop once more in case follow-up tasks
                    # (or a newer target save) became claimable meanwhile.
                    with _lock:
                        _drain_requested = True
            except Exception:  # noqa: BLE001 - one bad pass must not kill the worker
                logger.exception(
                    'careergps queue-drain pass failed (reason=%s)', reason)
                with _lock:
                    _drain_running = False
                return
            finally:
                # Only the background thread closes its own connection.  In
                # synchronous mode the loop borrows the caller's connection
                # (often a test transaction) and must never close it.
                if not _synchronous_mode:
                    try:
                        connection.close()
                    except Exception:  # noqa: BLE001 - best effort per pass
                        pass
    except BaseException:
        # Never leave ownership stuck on an unexpected failure escaping the
        # loop head (e.g. an interrupt): release it before propagating.
        with _lock:
            _drain_running = False
        raise
