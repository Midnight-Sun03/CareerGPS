"""The only CareerGPS entry point permitted to acquire the embedding model."""
import json
import os
from pathlib import Path
import shutil
import tempfile
from uuid import uuid4

from django.core.management.base import BaseCommand, CommandError

from applications.services.semantic_model import (
    MANIFEST, artifact_hashes, load_local_model, local_model_path, model_identifier, embedding_dimension,
)


class Command(BaseCommand):
    help = 'Prepare and verify the configured Sentence Transformer for offline document requests.'

    def add_arguments(self, parser):
        parser.add_argument('--force', action='store_true', help='Prepare a verified replacement even if one already exists.')

    def handle(self, *args, **options):
        try:
            self.prepare(force=options['force'])
        except Exception:
            # Provider exceptions can contain credentials or request details.
            # Do not attach the original exception, including with --traceback.
            raise CommandError('Semantic model preparation failed. Check setup network access, HF_TOKEN permissions, '
                'local disk space and directory permissions. Any previous prepared model is retained.') from None

    def prepare(self, *, force):
        target = local_model_path()
        target.parent.mkdir(parents=True, exist_ok=True)
        lock = target.parent / f'.{target.name}.prepare.lock'
        # Serialize setup commands without affecting request-side model caches.
        with lock.open('x'):
            pass
        stage = None
        backup = None
        published = False
        try:
            if not force:
                try:
                    load_local_model(target, deep=True)
                except Exception:
                    pass
                else:
                    self.stdout.write(self.style.SUCCESS('The configured semantic model is already prepared and verified locally.'))
                    return
            # Never replace an unrelated nonempty directory (even with --force).
            if target.exists() and (not target.is_dir() or
                    (any(target.iterdir()) and not (target / MANIFEST).is_file())):
                raise ValueError('Destination is not a managed model directory.')
            token = os.environ.get('HF_TOKEN', '').strip()
            self.stdout.write('Preparing the configured semantic model; authenticated access enabled.' if token else
                'HF_TOKEN is not set. Authenticated access is preferred; attempting public model access.')
            from sentence_transformers import SentenceTransformer
            model = SentenceTransformer(model_identifier(), token=token or False, trust_remote_code=False)
            stage = Path(tempfile.mkdtemp(prefix=f'.{target.name}.prepare-', dir=target.parent))
            model.save(str(stage), create_model_card=False)
            manifest = {'format': 1, 'model_id': model_identifier(),
                'embedding_dimension': embedding_dimension(model), 'files': artifact_hashes(stage)}
            (stage / MANIFEST).write_text(json.dumps(manifest, indent=2), encoding='utf-8')
            local = load_local_model(stage, deep=True)
            # Fixed non-private probes verify a save/reload preserves embeddings.
            # No precision, dimensions, sequence limits or encode options change.
            import numpy as np
            probes = ['CareerGPS semantic model verification.', 'A second sentence for local model verification.']
            np.testing.assert_allclose(model.encode(probes), local.encode(probes), rtol=1e-5, atol=1e-6)
            del local, model
            if target.exists():
                backup = target.parent / f'.{target.name}.backup-{uuid4().hex}'
                target.rename(backup)
            try:
                stage.rename(target)
                stage = None
                published = True
            except Exception:
                if backup is not None:
                    backup.rename(target)
                    backup = None
                raise
            self.stdout.write(self.style.SUCCESS('Semantic model prepared. Local-only reload, model identity, artifact hashes '
                'and embedding equivalence verified. Restart Django processes to use it.'))
        finally:
            # All cleanup targets are generated siblings of the configured model
            # directory; the working target is never recursively deleted.
            for temporary in (stage, backup if published else None):
                if temporary is not None and temporary.parent == target.parent and temporary != target:
                    shutil.rmtree(temporary, ignore_errors=True)
            lock.unlink(missing_ok=True)
