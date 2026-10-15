"""Local model identity and integrity shared by setup and request loading."""
import hashlib
import json
import logging
from pathlib import Path
from time import perf_counter
from contextlib import contextmanager

from django.conf import settings

MANIFEST = 'careergps-model.json'
logger = logging.getLogger('careergps.documents')


def model_identifier():
    identifier = settings.SENTENCE_TRANSFORMER_MODEL.strip()
    # Sentence Transformers expands this shorthand to the same official repo.
    return 'sentence-transformers/all-MiniLM-L6-v2' if identifier == 'all-MiniLM-L6-v2' else identifier


def local_model_path():
    path = Path(settings.SENTENCE_TRANSFORMER_LOCAL_PATH).expanduser()
    return (path if path.is_absolute() else settings.BASE_DIR / path).resolve()


def artifact_hashes(path):
    hashes = {}
    for file in sorted(path.rglob('*')):
        if file.is_file() and file.name != MANIFEST:
            with file.open('rb') as stream:
                hashes[file.relative_to(path).as_posix()] = hashlib.file_digest(stream, 'sha256').hexdigest()
    return hashes


def validate_prepared_model(path):
    """Lightweight identity/schema and file-existence checks, without hashing."""
    if not path.is_dir() or not (path / 'modules.json').is_file():
        raise ValueError('Prepared semantic model is missing.')
    manifest = json.loads((path / MANIFEST).read_text(encoding='utf-8'))
    if (not isinstance(manifest, dict) or manifest.get('format') != 1
            or manifest.get('model_id') != model_identifier()
            or type(manifest.get('embedding_dimension')) is not int
            or manifest['embedding_dimension'] <= 0
            or not isinstance(manifest.get('files'), dict) or not manifest['files']
            or 'modules.json' not in manifest['files']):
        raise ValueError('Prepared semantic model needs preparation or repair.')
    for name, digest in manifest['files'].items():
        relative = Path(name)
        if (not name or relative.is_absolute() or relative.drive or '..' in relative.parts
                or not isinstance(digest, str) or len(digest) != 64
                or any(character not in '0123456789abcdef' for character in digest)
                or not (path / relative).is_file()):
            raise ValueError('Prepared semantic model artifacts are missing or invalid.')
    return manifest


def embedding_dimension(model):
    """Use the installed current API, retaining compatibility with older models."""
    current = getattr(model, 'get_embedding_dimension', None)
    return current() if callable(current) else model.get_sentence_embedding_dimension()


def load_local_model(path, *, deep=False):
    timings = dict.fromkeys(('manifest_check', 'artifact_validation',
        'import_sentence_transformers', 'model_construct', 'dimension_check'), 0.0)
    start = perf_counter()
    outcome = 'failed'

    @contextmanager
    def measure(stage):
        before = perf_counter()
        try:
            yield
        finally:
            timings[stage] += perf_counter() - before

    try:
        with measure('manifest_check'):
            manifest = validate_prepared_model(path)
        if deep:
            with measure('artifact_validation'):
                if manifest['files'] != artifact_hashes(path):
                    raise ValueError('Prepared semantic model artifact hashes do not match.')
        with measure('import_sentence_transformers'):
            # Includes Torch and other transitive imports on a fresh process.
            from sentence_transformers import SentenceTransformer
        with measure('model_construct'):
            # Includes local weight loading and library initialization.
            model = SentenceTransformer(str(path), local_files_only=True, token=False, trust_remote_code=False)
        with measure('dimension_check'):
            if embedding_dimension(model) != manifest['embedding_dimension']:
                raise ValueError('Prepared semantic model dimensions do not match.')
        outcome = 'ok'
        return model
    finally:
        stages = ' | '.join(f'{name}={value:.3f}s' for name, value in timings.items())
        logger.info('Semantic startup timing | outcome=%s | validation=%s | %s | total=%.3fs',
            outcome, 'deep' if deep else 'lightweight', stages, perf_counter() - start)
