"""Request-local monotonic timings. Never accept document content as metadata."""
from contextlib import contextmanager
from contextvars import ContextVar
from functools import wraps
import logging
from time import perf_counter

logger = logging.getLogger('careergps.documents')
_current = ContextVar('document_timing', default=None)
STAGES = ('job_context', 'candidate_bundle', 'semantic_model_load',
          'semantic_selection', 'gemini', 'pdf', 'docx', 'persistence')


@contextmanager
def timed_stage(name):
    timing = _current.get()
    start = perf_counter()
    try:
        yield
    finally:
        if timing is not None:
            timing[name] += perf_counter() - start


def semantic_model_status(status):
    timing = _current.get()
    if timing is not None:
        timing['model_source'] = status


def document_timing(document_type):
    def decorate(view):
        @wraps(view)
        def wrapped(*args, **kwargs):
            timing = dict.fromkeys(STAGES, 0.0)
            timing['model_source'] = 'not_needed'
            token = _current.set(timing)
            start = perf_counter()
            outcome = 'failed'
            try:
                response = view(*args, **kwargs)
                outcome = 'ok' if response.status_code < 400 else 'failed'
                return response
            finally:
                total = perf_counter() - start
                _current.reset(token)
                # Selection wraps the cached loader. Report inference/order time
                # separately, without counting initialization twice.
                timing['semantic_selection'] = max(0.0,
                    timing['semantic_selection'] - timing['semantic_model_load'])
                stages = ' | '.join(f'{key}={timing[key]:.3f}s' for key in STAGES)
                logger.info('Document timing | type=%s | outcome=%s | model_source=%s | %s | total=%.3fs',
                    document_type, outcome, timing['model_source'], stages, total)
        return wrapped
    return decorate
