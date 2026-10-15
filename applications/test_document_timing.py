from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from django.contrib.auth.models import User
from django.http import HttpResponse
from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse

from applications.models import GeneratedDocument
from applications.services.document_generation import CVWritingResult, CoverLetterResult
from applications.services.document_timing import document_timing, timed_stage, STAGES
from applications.services.semantic_relevance import _model
from core.models import Profile, WorkExperience


class DocumentTimerTests(SimpleTestCase):
    def test_monotonic_stage_totals_and_initialization_are_separate(self):
        @document_timing('cv')
        def view():
            with timed_stage('semantic_selection'):
                with timed_stage('semantic_model_load'):
                    pass
            return HttpResponse()
        with patch('applications.services.document_timing.perf_counter', side_effect=[0, 1, 2, 4, 7, 10]), self.assertLogs('careergps.documents') as logs:
            view()
        self.assertIn('semantic_model_load=2.000s', logs.output[0])
        self.assertIn('semantic_selection=4.000s', logs.output[0])
        self.assertIn('total=10.000s', logs.output[0])

    def test_failed_request_logs_timing_without_exception_details(self):
        @document_timing('cover_letter')
        def view():
            with timed_stage('gemini'):
                raise RuntimeError('private document and secret token')
        with self.assertLogs('careergps.documents') as logs, self.assertRaises(RuntimeError):
            view()
        self.assertIn('outcome=failed', logs.output[0])
        self.assertNotIn('private', logs.output[0])
        self.assertNotIn('secret', logs.output[0])

    def test_nested_contexts_and_following_requests_do_not_share_timings(self):
        @document_timing('cv')
        def inner():
            return HttpResponse()
        @document_timing('cover_letter')
        def outer():
            inner()
            return HttpResponse()
        with self.assertLogs('careergps.documents') as logs:
            outer()
            inner()
        self.assertEqual(len(logs.output), 3)
        self.assertIn('type=cv', logs.output[0])
        self.assertIn('type=cover_letter', logs.output[1])
        self.assertIn('type=cv', logs.output[2])


@override_settings(PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'])
class DocumentRequestPerformanceTests(TestCase):
    def setUp(self):
        self.temporary = TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.override = override_settings(MEDIA_ROOT=self.temporary.name,
            SENTENCE_TRANSFORMER_LOCAL_PATH=str(Path(self.temporary.name) / 'missing-model'))
        self.override.enable()
        self.addCleanup(self.override.disable)
        user = User.objects.create_user('document-test', password='test')
        profile = Profile.objects.create(user=user)
        self.work = WorkExperience.objects.create(profile=profile, title='Private job title',
            organisation='Private employer', description='Genuine private candidate evidence.')
        self.client.force_login(user)
        _model.cache_clear()
        self.addCleanup(_model.cache_clear)
        self.cv = patch('core.views.generate_cv_writing', return_value=CVWritingResult(summary='Grounded summary.'))
        self.cover = patch('core.views.generate_controlled_cover_letter', return_value=CoverLetterResult(
            opening='Grounded opening.', body_paragraphs=['Grounded body.'], closing='Grounded closing.', signature_name='Candidate'))
        self.cv.start()
        self.cover.start()
        self.addCleanup(self.cv.stop)
        self.addCleanup(self.cover.stop)

    def generate(self, kind):
        route = '/generate-cv/' if kind == 'cv' else '/generate-cover-letter/'
        with self.assertLogs('careergps.documents') as logs:
            response = self.client.post(route, {'job_description': 'Private vacancy content', 'cv_text': 'Private supplied evidence'})
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(
            response.json()["preview_url"],
            reverse(
                "applications:document_preview",
                args=[GeneratedDocument.objects.latest("pk").pk],
            ),
        )
        timing = next(record for record in logs.output if 'Document timing' in record)
        self.assertIn(f'type={kind}', timing)
        self.assertIn('outcome=ok', timing)
        for stage in (*STAGES, 'total'):
            self.assertIn(f'{stage}=', timing)
        self.assertNotIn('Private', ' '.join(logs.output))
        document = GeneratedDocument.objects.filter(document_type=kind).latest('pk')
        preview = self.client.get(response.json()["preview_url"])
        self.assertEqual(preview.status_code, 200)
        self.assertContains(preview, document.get_document_type_display())
        self.assertTrue(Path(document.pdf_file.path).read_bytes().startswith(b'%PDF'))
        self.assertTrue(Path(document.docx_file.path).read_bytes().startswith(b'PK'))
        self.assertIn(f'work:{self.work.pk}', document.candidate_fact_ids)
        return timing

    def test_both_document_requests_render_and_persist_with_missing_local_model(self):
        # Real fact building, JSON resume assembly, PDF/DOCX renderers and DB/file
        # persistence; only Gemini is mocked. Missing files never import ST.
        provider = MagicMock()
        with patch.dict('sys.modules', {'sentence_transformers': provider}), patch('socket.socket.connect', side_effect=AssertionError('Network forbidden')):
            for kind in ('cv', 'cover_letter'):
                with self.subTest(kind=kind):
                    self.assertIn('model_source=fallback', self.generate(kind))
        provider.SentenceTransformer.assert_not_called()

    def test_cv_then_cover_letter_share_the_in_memory_model(self):
        model = MagicMock()
        cosine = MagicMock()
        # Work + supplied CV facts are ranked; every selected object is genuine.
        cosine.return_value.__getitem__.return_value.detach.return_value.cpu.return_value.tolist.return_value = [0.9, 0.8]
        provider = SimpleNamespace(util=SimpleNamespace(cos_sim=cosine))
        with patch.dict('sys.modules', {'sentence_transformers': provider}), patch('applications.services.semantic_relevance.load_local_model', return_value=model) as load, patch('socket.socket.connect', side_effect=AssertionError('Network forbidden')):
            self.assertIn('model_source=local_disk', self.generate('cv'))
            second = self.generate('cover_letter')
            self.assertIn('model_source=memory', second)
            self.assertIn('semantic_model_load=0.000s', second)
        load.assert_called_once()
        self.assertEqual(cosine.call_count, 2)

    def test_warmup_makes_first_cover_letter_and_second_cv_memory_only(self):
        from applications.services.semantic_warmup import warm_semantic_model
        model = MagicMock()
        cosine = MagicMock()
        cosine.return_value.__getitem__.return_value.detach.return_value.cpu.return_value.tolist.return_value = [0.9, 0.8]
        provider = SimpleNamespace(util=SimpleNamespace(cos_sim=cosine))
        with patch.dict('sys.modules', {'sentence_transformers': provider}), patch('applications.services.semantic_relevance.load_local_model', return_value=model) as load, patch('socket.socket.connect', side_effect=AssertionError('Network forbidden')):
            self.assertTrue(warm_semantic_model())
            for kind in ('cover_letter', 'cv'):
                timing = self.generate(kind)
                self.assertIn('model_source=memory', timing)
                self.assertIn('semantic_model_load=0.000s', timing)
        load.assert_called_once()

    def test_rejected_requests_also_emit_total_timing(self):
        with self.assertLogs('careergps.documents') as logs:
            response = self.client.post('/generate-cv/', {})
        self.assertEqual(response.status_code, 400)
        self.assertIn('outcome=failed', logs.output[0])
        self.assertIn('total=', logs.output[0])
