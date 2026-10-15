import importlib
import json
import sys
from io import StringIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from django.core.management import call_command
from django.contrib.staticfiles.handlers import StaticFilesHandler
from django.contrib.staticfiles.management.commands.runserver import Command as RunserverCommand
from django.test import SimpleTestCase, override_settings

from applications.services import semantic_relevance
from applications.services.semantic_model import load_local_model, validate_prepared_model, MANIFEST, embedding_dimension
from applications.services.semantic_warmup import warm_semantic_model, warm_semantic_model_on_startup
from applications.test_semantic_model import SemanticModelCase


class SemanticWarmupTests(SemanticModelCase):
    def test_warmup_uses_local_files_without_token_and_populates_selector_cache(self):
        self.prepared()
        with self.assertLogs('careergps.documents') as logs:
            self.assertTrue(warm_semantic_model())
        self.constructor.assert_called_once_with(str(self.path), local_files_only=True, token=False, trust_remote_code=False)
        model = self.constructor.return_value
        self.assertIs(semantic_relevance._model(), model)
        result = semantic_relevance.select_relevant_facts(self.bundle(), 'job')
        self.assertEqual(len(result), 5)
        self.assertEqual(self.constructor.call_count, 1)
        self.assertIn('Semantic model warmup | outcome=ok | source=local_disk', logs.output[-1])

    def test_repeated_warmup_reuses_same_model(self):
        self.prepared()
        warm_semantic_model()
        with self.assertLogs('careergps.documents') as logs:
            self.assertTrue(warm_semantic_model())
        self.assertEqual(self.constructor.call_count, 1)
        self.assertIn('source=memory', logs.output[-1])

    def test_missing_model_warmup_is_safe_and_selection_keeps_genuine_facts(self):
        with self.assertLogs('careergps.documents', level='WARNING') as logs:
            self.assertFalse(warm_semantic_model())
        self.assertIn('Server will continue', logs.output[0])
        self.constructor.assert_not_called()
        bundle = self.bundle()
        self.assertIs(semantic_relevance.select_relevant_facts(bundle, 'job'), bundle['facts'])

    def test_corrupt_local_load_does_not_crash_warmup_or_expose_exception(self):
        self.prepared()
        self.constructor.side_effect = ValueError('private-content credential-token')
        with self.assertLogs('careergps.documents') as logs:
            self.assertFalse(warm_semantic_model())
        self.assertNotIn('private-content', ' '.join(logs.output))
        self.assertNotIn('credential-token', ' '.join(logs.output))
        self.assertEqual(semantic_relevance._model.cache_info().currsize, 0)

    def test_runtime_validation_never_hashes_traverses_or_compares_embeddings(self):
        self.prepared()
        with (patch('applications.services.semantic_model.artifact_hashes', side_effect=AssertionError('Runtime hashing')),
                patch.object(Path, 'rglob', side_effect=AssertionError('Runtime traversal')),
                patch('numpy.testing.assert_allclose', side_effect=AssertionError('Runtime equivalence'))):
            self.assertTrue(warm_semantic_model())

    def test_missing_listed_artifact_fails_lightweight_validation(self):
        self.prepared()
        (self.path / 'model.safetensors').unlink()
        with self.assertRaises(ValueError):
            validate_prepared_model(self.path)
        self.assertFalse(warm_semantic_model())
        self.constructor.assert_not_called()

    def test_invalid_manifest_structures_are_rejected(self):
        self.prepared()
        original = json.loads((self.path / MANIFEST).read_text())
        variants = [[], {}, {**original, 'files': ['bad']}, {**original, 'embedding_dimension': True},
            {**original, 'embedding_dimension': 0}, {**original, 'files': {'../escape': 'a' * 64}},
            {**original, 'files': {'modules.json': 'not-a-hash'}}]
        for manifest in variants:
            with self.subTest(manifest=manifest):
                (self.path / MANIFEST).write_text(json.dumps(manifest), encoding='utf-8')
                with self.assertRaises(ValueError):
                    validate_prepared_model(self.path)

    def test_setup_deep_validation_detects_changed_bytes_without_library_loading(self):
        self.prepared()
        (self.path / 'model.safetensors').write_bytes(b'changed bytes')
        with self.assertRaises(ValueError):
            load_local_model(self.path, deep=True)
        self.constructor.assert_not_called()

    def test_prepare_command_still_hashes_and_checks_embedding_equivalence(self):
        from applications.services.semantic_model import artifact_hashes
        from numpy.testing import assert_allclose
        with (patch('applications.services.semantic_model.artifact_hashes', wraps=artifact_hashes) as hashes,
                patch('numpy.testing.assert_allclose', wraps=assert_allclose) as equivalent):
            call_command('prepare_semantic_model', stdout=StringIO())
        self.assertGreaterEqual(hashes.call_count, 1)
        equivalent.assert_called_once()

    def test_startup_option_can_defer_to_explicit_production_worker_hook(self):
        self.prepared()
        with override_settings(SENTENCE_TRANSFORMER_WARMUP_ON_START=False):
            self.assertFalse(warm_semantic_model_on_startup())
            self.constructor.assert_not_called()
            self.assertTrue(warm_semantic_model())


class DimensionCompatibilityTests(SimpleTestCase):
    def test_current_method_preferred_without_deprecation_warning(self):
        model = SimpleNamespace(get_embedding_dimension=lambda: 384,
            get_sentence_embedding_dimension=lambda: self.fail('Deprecated method called'))
        self.assertEqual(embedding_dimension(model), 384)

    def test_older_model_method_remains_compatible(self):
        self.assertEqual(embedding_dimension(SimpleNamespace(get_sentence_embedding_dimension=lambda: 384)), 384)


@override_settings(SENTENCE_TRANSFORMER_WARMUP_ON_START=True)
class ServingLifecycleTests(SimpleTestCase):
    def fresh_entrypoint(self, name):
        old = sys.modules.pop(name, None)
        def restore():
            sys.modules.pop(name, None)
            if old is not None:
                sys.modules[name] = old
        self.addCleanup(restore)

    def test_wsgi_and_asgi_entrypoints_warm_after_django_application_initialization(self):
        for kind in ('wsgi', 'asgi'):
            with self.subTest(kind=kind):
                name = f'CareerGPS_Rebuild.{kind}'
                self.fresh_entrypoint(name)
                order = []
                application = object()
                def initialize():
                    order.append('application')
                    return application
                with (patch(f'django.core.{kind}.get_{kind}_application', side_effect=initialize),
                        patch('applications.services.semantic_warmup.warm_semantic_model', side_effect=lambda: order.append('warmup'))):
                    module = importlib.import_module(name)
                self.assertIs(module.application, application)
                self.assertEqual(order, ['application', 'warmup'])

    def test_runserver_autoreloader_parent_does_not_build_handler_or_warm(self):
        command = RunserverCommand()
        with (patch('django.utils.autoreload.run_with_reloader') as reloader,
                patch.object(command, 'get_handler') as handler,
                patch('applications.services.semantic_warmup.warm_semantic_model') as warm):
            command.run(use_reloader=True)
        reloader.assert_called_once_with(command.inner_run, use_reloader=True)
        handler.assert_not_called()
        warm.assert_not_called()

    @override_settings(DEBUG=True)
    def test_runserver_serving_process_warms_before_server_accepts_requests(self):
        self.fresh_entrypoint('CareerGPS_Rebuild.wsgi')
        order = []
        def serve(address, port, handler, **kwargs):
            self.assertIsInstance(handler, StaticFilesHandler)
            order.append('serve')
        with (patch('django.core.management.commands.runserver.run', side_effect=serve),
                patch.object(RunserverCommand, 'check_migrations'),
                patch('applications.services.semantic_warmup.warm_semantic_model', side_effect=lambda: order.append('warmup'))):
            call_command('runserver', use_reloader=False, skip_checks=True, stdout=StringIO())
        self.assertEqual(order, ['warmup', 'serve'])

    def test_wsgi_still_available_when_model_cannot_be_loaded(self):
        self.fresh_entrypoint('CareerGPS_Rebuild.wsgi')
        with patch('applications.services.semantic_warmup._model', side_effect=ValueError('Missing model')):
            module = importlib.import_module('CareerGPS_Rebuild.wsgi')
        self.assertTrue(callable(module.application))

    def test_setup_and_arbitrary_management_commands_do_not_warm(self):
        import django
        from django.core.management import get_commands, load_command_class
        with (patch('applications.services.semantic_warmup.warm_semantic_model') as warm,
                patch('applications.services.semantic_relevance.load_local_model', side_effect=AssertionError('Real model load'))):
            django.setup()
            for name in ('migrate', 'makemigrations', 'test', 'shell', 'collectstatic', 'check'):
                command = load_command_class(get_commands()[name], name)
                command.create_parser('manage.py', name)
            call_command('check', stdout=StringIO())
            call_command('shell', command='pass', stdout=StringIO())
        warm.assert_not_called()
