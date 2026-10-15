from __future__ import annotations

from functools import lru_cache

import logging

from .document_timing import timed_stage, semantic_model_status
from .semantic_model import load_local_model, local_model_path

logger = logging.getLogger('careergps.documents')


@lru_cache(maxsize=1)
def _model():
    with timed_stage('semantic_model_load'):
        model = load_local_model(local_model_path())
    semantic_model_status('local_disk')
    logger.info('Semantic model | source=local_disk | local_files_only=true | verified=true')
    return model


def select_relevant_facts(candidate_bundle, job_text, *, limit=18):
    """Use Sentence Transformers when available; fail open to all user facts.

    Relevance is used only for selection/order. It never creates a candidate fact.
    """
    facts = candidate_bundle["facts"]
    if not facts or not job_text.strip():
        return facts

    always = [fact for fact in facts if fact["type"] in {"basic", "education"}]
    candidates = [fact for fact in facts if fact not in always]
    if not candidates:
        return always

    try:
        semantic_model_status('memory')
        model = _model()
        from sentence_transformers import util
        query_embedding = model.encode(job_text, convert_to_tensor=True)
        fact_embeddings = model.encode([fact["text"] for fact in candidates], convert_to_tensor=True)
        scores = util.cos_sim(query_embedding, fact_embeddings)[0].detach().cpu().tolist()
        ranked = [
            fact for _, fact in sorted(zip(scores, candidates), key=lambda pair: pair[0], reverse=True)
        ]
        return always + ranked[: max(0, limit - len(always))]
    except Exception:
        semantic_model_status('fallback')
        logger.warning('Semantic selection unavailable locally; using all genuine candidate facts. '
            'Run manage.py prepare_semantic_model and restart Django. No runtime model download is attempted.')
        # Integrity-first fallback: more genuine user facts are safer than a
        # brittle ranking failure or fabricated substitutes.
        return facts
