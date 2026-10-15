"""Warm the document selector's own cache before a web worker accepts requests."""
import logging
from threading import Lock
from time import perf_counter

from django.conf import settings

from .semantic_relevance import _model

logger = logging.getLogger('careergps.documents')
_warmup_lock = Lock()


def warm_semantic_model():
    """Safe explicit worker hook; no separate model or setup verification."""
    start = perf_counter()
    with _warmup_lock:
        source = 'memory' if _model.cache_info().currsize else 'local_disk'
        try:
            _model()
        except Exception:
            logger.warning('Semantic model warmup | outcome=failed | source=local_disk | model_load=%.3fs | '
                'Server will continue with the genuine-facts fallback if local loading remains unavailable. '
                'Check local model preparation and permissions; no download is attempted.', perf_counter() - start)
            return False
    logger.info('Semantic model warmup | outcome=ok | source=%s | model_load=%.3fs', source, perf_counter() - start)
    return True


def warm_semantic_model_on_startup():
    if settings.SENTENCE_TRANSFORMER_WARMUP_ON_START:
        return warm_semantic_model()
    logger.info('Semantic model warmup | outcome=disabled')
    return False
