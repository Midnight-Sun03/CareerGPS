import json
import os
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from numpy.testing import assert_allclose  # Load NumPy helpers before isolating provider modules.

from django.core.management import call_command, CommandError
from django.test import SimpleTestCase, override_settings

from applications.services import semantic_relevance
from applications.services.semantic_model import MANIFEST, artifact_hashes, model_identifier, load_local_model


class FakeModel:
    def save(self, path, **kwargs):
        path = Path(path)
        (path / 'modules.json').write_text('[]', encoding='utf-8')
        (path / 'model.safetensors').write_bytes(b'unchanged mock weights')

    def get_embedding_dimension(self):
        return 3

    def encode(self, values, **kwargs):
        return [[0.1, 0.2, 0.3] for _ in values]


class SemanticModelCase(SimpleTestCase):
    def setUp(self):
        self.temporary = TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.path = Path(self.temporary.name) / 'semantic'
        self.settings_override = override_settings(SENTENCE_TRANSFORMER_MODEL='all-MiniLM-L6-v2',
            SENTENCE_TRANSFORMER_LOCAL_PATH=str(self.path))
        self.settings_override.enable()
        self.addCleanup(self.settings_override.disable)
        semantic_relevance._model.cache_clear()
        self.addCleanup(semantic_relevance._model.cache_clear)
        self.constructor = MagicMock(return_value=FakeModel())
        self.cosine = MagicMock()
        self.cosine.return_value.__getitem__.return_value.detach.return_value.cpu.return_value.tolist.return_value = [0.1, 0.9, 0.9]
        self.module = SimpleNamespace(SentenceTransformer=self.constructor, util=SimpleNamespace(cos_sim=self.cosine))
        self.modules = patch.dict('sys.modules', {'sentence_transformers': self.module})
        self.modules.start()
        self.addCleanup(self.modules.stop)
        # No test is allowed to acquire a real model, even accidentally.
        self.network = patch('socket.socket.connect', side_effect=AssertionError('Unexpected network access'))
        self.network.start()
        self.addCleanup(self.network.stop)

    def prepared(self):
        self.path.mkdir(exist_ok=True)
        FakeModel().save(self.path)
        manifest = {'format': 1, 'model_id': model_identifier(), 'embedding_dimension': 3,
            'files': artifact_hashes(self.path)}
        (self.path / MANIFEST).write_text(json.dumps(manifest), encoding='utf-8')

    def bundle(self):
        return {'facts': [
            {'id': 'basic:1', 'type': 'basic', 'text': 'private name'},
            {'id': 'education:1', 'type': 'education', 'text': 'private education'},
            {'id': 'skill:1', 'type': 'skill', 'text': 'private skill'},
            {'id': 'work:1', 'type': 'work', 'text': 'private work'},
            {'id': 'project:1', 'type': 'project', 'text': 'private project'},
        ]}


class RuntimeSemanticModelTests(SemanticModelCase):
    def test_loader_uses_local_path_and_offline_flags_without_token(self):
        self.prepared()
        with patch.dict(os.environ, {'HF_TOKEN': 'private-runtime-token'}):
            semantic_relevance._model()
        self.constructor.assert_called_once_with(str(self.path), local_files_only=True, token=False, trust_remote_code=False)

    def test_successful_model_is_cached_in_memory(self):
        self.prepared()
        first = semantic_relevance._model()
        self.assertIs(first, semantic_relevance._model())
        self.assertEqual(self.constructor.call_count, 1)
        self.assertEqual(semantic_relevance._model.cache_info().hits, 1)

    def test_runtime_does_not_require_hf_token(self):
        self.prepared()
        with patch.dict(os.environ, {}, clear=True):
            self.assertIsNotNone(semantic_relevance._model())

    def test_missing_model_falls_back_without_importing_or_downloading(self):
        bundle = self.bundle()
        with self.assertLogs('careergps.documents', level='WARNING') as logs:
            self.assertIs(semantic_relevance.select_relevant_facts(bundle, 'private job'), bundle['facts'])
        self.constructor.assert_not_called()
        self.assertIn('prepare_semantic_model', logs.output[0])
        self.assertNotIn('private', ' '.join(logs.output))

    def test_corrupt_weights_rejected_by_local_library_preserve_fallback(self):
        self.prepared()
        (self.path / 'model.safetensors').write_bytes(b'corrupt')
        self.constructor.side_effect = ValueError('Invalid local weights')
        bundle = self.bundle()
        self.assertIs(semantic_relevance.select_relevant_facts(bundle, 'job'), bundle['facts'])
        self.constructor.assert_called_once_with(str(self.path), local_files_only=True, token=False, trust_remote_code=False)

    def test_different_configured_model_is_not_silently_used(self):
        self.prepared()
        with override_settings(SENTENCE_TRANSFORMER_MODEL='other/model'), self.assertRaises(ValueError):
            load_local_model(self.path)
        self.constructor.assert_not_called()

    def test_load_failure_does_not_leak_exception_or_secret(self):
        self.prepared()
        self.constructor.side_effect = RuntimeError('private-token and private details')
        bundle = self.bundle()
        with self.assertLogs('careergps.documents') as logs:
            self.assertIs(semantic_relevance.select_relevant_facts(bundle, 'job'), bundle['facts'])
        self.assertNotIn('private', ' '.join(logs.output))

    def test_semantic_ranking_cosine_limits_and_always_included_facts_unchanged(self):
        self.prepared()
        model = MagicMock()
        model.get_embedding_dimension.return_value = 3
        model.encode.side_effect = ['query-embedding', 'fact-embeddings']
        self.constructor.return_value = model
        bundle = self.bundle()
        result = semantic_relevance.select_relevant_facts(bundle, 'job query', limit=4)
        self.assertEqual([fact['id'] for fact in result], ['basic:1', 'education:1', 'work:1', 'project:1'])
        self.cosine.assert_called_once_with('query-embedding', 'fact-embeddings')
        self.assertEqual(model.encode.call_args_list[0].args, ('job query',))
        self.assertEqual(model.encode.call_args_list[1].args, (['private skill', 'private work', 'private project'],))
        for call in model.encode.call_args_list:
            self.assertEqual(call.kwargs, {'convert_to_tensor': True})
        self.assertTrue(all(any(fact is original for original in bundle['facts']) for fact in result))

    def test_encoding_failure_preserves_all_original_facts(self):
        self.prepared()
        self.constructor.return_value = MagicMock(get_embedding_dimension=lambda: 3)
        self.constructor.return_value.encode.side_effect = RuntimeError('private failure')
        bundle = self.bundle()
        self.assertIs(semantic_relevance.select_relevant_facts(bundle, 'job'), bundle['facts'])

    def test_no_job_or_no_rankable_facts_does_not_initialize_model(self):
        self.assertEqual(semantic_relevance.select_relevant_facts({'facts': []}, 'job'), [])
        bundle = self.bundle()
        self.assertIs(semantic_relevance.select_relevant_facts(bundle, ''), bundle['facts'])
        only_basic = {'facts': bundle['facts'][:2]}
        self.assertEqual(semantic_relevance.select_relevant_facts(only_basic, 'job'), only_basic['facts'])
        self.constructor.assert_not_called()


class PrepareSemanticModelTests(SemanticModelCase):
    def prepare(self, **kwargs):
        output = StringIO()
        call_command('prepare_semantic_model', stdout=output, stderr=output, **kwargs)
        return output.getvalue()

    def test_prepare_uses_token_and_exact_configured_model_without_exposing_token(self):
        with patch.dict(os.environ, {'HF_TOKEN': 'secret-test-token'}):
            output = self.prepare()
        self.assertNotIn('secret-test-token', output)
        first, local = self.constructor.call_args_list
        self.assertEqual(first.args, ('sentence-transformers/all-MiniLM-L6-v2',))
        self.assertEqual(first.kwargs, {'token': 'secret-test-token', 'trust_remote_code': False})
        self.assertTrue(local.kwargs['local_files_only'])
        self.assertIs(local.kwargs['token'], False)
        self.assertTrue(Path(local.args[0]).name.startswith('.semantic.prepare-'))
        manifest = json.loads((self.path / MANIFEST).read_text())
        self.assertEqual(manifest['model_id'], 'sentence-transformers/all-MiniLM-L6-v2')
        self.assertEqual(manifest['embedding_dimension'], 3)
        self.assertEqual(manifest['files'], artifact_hashes(self.path))
        self.assertNotIn('secret-test-token', (self.path / MANIFEST).read_text())

    def test_public_preparation_without_token_reports_auth_preferred(self):
        with patch.dict(os.environ, {}, clear=True):
            output = self.prepare()
        self.assertIn('Authenticated access is preferred', output)
        self.assertIs(self.constructor.call_args_list[0].kwargs['token'], False)

    def test_preparation_is_idempotent_and_offline_when_valid(self):
        self.prepare()
        before = (self.path / MANIFEST).read_bytes()
        self.constructor.reset_mock()
        self.assertIn('already prepared', self.prepare())
        self.constructor.assert_called_once_with(str(self.path), local_files_only=True, token=False, trust_remote_code=False)
        self.assertEqual((self.path / MANIFEST).read_bytes(), before)

    def test_force_prepares_a_verified_replacement(self):
        self.prepared()
        self.prepare(force=True)
        self.assertEqual(self.constructor.call_count, 2)
        self.assertEqual(list(self.path.parent.glob('.semantic.*')), [])

    def test_failed_download_preserves_working_model_and_hides_exception(self):
        self.prepared()
        before = artifact_hashes(self.path)
        self.constructor.side_effect = RuntimeError('HF secret-token in exception')
        output = StringIO()
        with self.assertRaises(CommandError) as caught:
            call_command('prepare_semantic_model', force=True, stdout=output, stderr=output)
        self.assertNotIn('secret-token', str(caught.exception) + output.getvalue())
        self.assertTrue(caught.exception.__suppress_context__)
        self.assertEqual(artifact_hashes(self.path), before)

    def test_failed_local_validation_preserves_working_model(self):
        self.prepared()
        before = artifact_hashes(self.path)
        self.constructor.side_effect = [FakeModel(), RuntimeError('broken local load')]
        with self.assertRaises(CommandError):
            self.prepare(force=True)
        self.assertEqual(artifact_hashes(self.path), before)
        self.assertEqual(list(self.path.parent.glob('.semantic.*')), [])

    def test_embedding_change_blocks_replacement(self):
        self.prepared()
        changed = FakeModel()
        changed.encode = lambda values: [[9.0, 9.0, 9.0] for _ in values]
        self.constructor.side_effect = [FakeModel(), changed]
        before = (self.path / MANIFEST).read_bytes()
        with self.assertRaises(CommandError):
            self.prepare(force=True)
        self.assertEqual((self.path / MANIFEST).read_bytes(), before)

    def test_publish_failure_restores_previous_model(self):
        self.prepared()
        before = (self.path / MANIFEST).read_bytes()
        rename = Path.rename
        def fail_publish(path, destination):
            if '.prepare-' in path.name:
                raise OSError('simulated rename failure')
            return rename(path, destination)
        with patch.object(Path, 'rename', fail_publish), self.assertRaises(CommandError):
            self.prepare(force=True)
        self.assertEqual((self.path / MANIFEST).read_bytes(), before)

    def test_failed_restore_retains_backup_for_recovery(self):
        self.prepared()
        rename = Path.rename
        def fail_publish_and_restore(path, destination):
            if '.prepare-' in path.name or '.backup-' in path.name:
                raise OSError('simulated rename failure')
            return rename(path, destination)
        with patch.object(Path, 'rename', fail_publish_and_restore), self.assertRaises(CommandError):
            self.prepare(force=True)
        backup = next(self.path.parent.glob('.semantic.backup-*'))
        self.assertEqual((backup / 'model.safetensors').read_bytes(), b'unchanged mock weights')

    def test_unrelated_directory_is_never_replaced(self):
        self.path.mkdir()
        (self.path / 'important.txt').write_text('keep')
        with self.assertRaises(CommandError):
            self.prepare(force=True)
        self.assertEqual((self.path / 'important.txt').read_text(), 'keep')
        self.constructor.assert_not_called()

    def test_concurrent_preparation_does_not_remove_existing_lock(self):
        lock = self.path.parent / '.semantic.prepare.lock'
        lock.write_text('locked')
        with self.assertRaises(CommandError):
            self.prepare()
        self.assertEqual(lock.read_text(), 'locked')
        self.constructor.assert_not_called()
