from tempfile import TemporaryDirectory

from django.contrib.auth.models import User
from django.core.files.base import ContentFile
from django.db import transaction
from django.test import TestCase, TransactionTestCase, override_settings

from applications.models import Application, GeneratedDocument
from employers.models import EmployerVacancy, HiringApplication, Organization, SubmittedDocument
from interview.models import Interview, InterviewQuestion
from .models import Opportunity


class MediaRootMixin:
    def setUp(self):
        super().setUp()
        self.media = TemporaryDirectory()
        self.addCleanup(self.media.cleanup)
        setting = override_settings(MEDIA_ROOT=self.media.name)
        setting.enable()
        self.addCleanup(setting.disable)
        self.user = User.objects.create_user('media-owner', password='test-password')


class OwnedFileCleanupTests(MediaRootMixin, TestCase):
    def test_interview_cascade_removes_only_unreferenced_audio(self):
        interview = Interview.objects.create(user=self.user)
        other = Interview.objects.create(user=self.user)
        first = InterviewQuestion.objects.create(
            interview=interview, question_index=1, question_text='Example?')
        first.audio_file.save('owned.webm', ContentFile(b'owned audio'))
        second = InterviewQuestion.objects.create(
            interview=other, question_index=1, question_text='Other?')
        second.audio_file.save('other.webm', ContentFile(b'other audio'))
        owned_name = first.audio_file.name
        other_name = second.audio_file.name
        storage = first.audio_file.storage
        with self.captureOnCommitCallbacks(execute=True):
            interview.delete()
        self.assertFalse(storage.exists(owned_name))
        self.assertTrue(storage.exists(other_name))

    def test_shared_audio_name_is_not_deleted_while_referenced(self):
        first_interview = Interview.objects.create(user=self.user)
        other_user = User.objects.create_user('other-media-owner', password='test-password')
        second_interview = Interview.objects.create(user=other_user)
        first = InterviewQuestion.objects.create(
            interview=first_interview, question_index=1, question_text='Example?')
        first.audio_file.save('shared.webm', ContentFile(b'audio'))
        second = InterviewQuestion.objects.create(
            interview=second_interview, question_index=1, question_text='Other?',
            audio_file=first.audio_file.name)
        with self.captureOnCommitCallbacks(execute=True):
            first_interview.delete()
        self.assertTrue(second.audio_file.storage.exists(second.audio_file.name))

    def test_generated_artifacts_clean_without_touching_submitted_binary_copy(self):
        opportunity = Opportunity.objects.create(title='Role', company='Example',
            opportunity_type='entry_level', location='Cape Town', description='Work')
        application = Application.objects.create(user=self.user, opportunity=opportunity)
        document = GeneratedDocument.objects.create(user=self.user, application=application,
            opportunity=opportunity, document_type='cv')
        document.pdf_file.save('original.pdf', ContentFile(b'%PDF original'))
        document.docx_file.save('original.docx', ContentFile(b'PK original'))
        pdf_name, docx_name = document.pdf_file.name, document.docx_file.name
        storage = document.pdf_file.storage
        org = Organization.objects.create(name='Example Org', slug='example-org', created_by=self.user)
        vacancy = EmployerVacancy.objects.create(organization=org, opportunity=opportunity,
            title='Role', location='Cape Town', description='Work', created_by=self.user)
        hiring = HiringApplication.objects.create(application=application, vacancy=vacancy)
        submitted = SubmittedDocument.objects.create(hiring_application=hiring,
            original_document=document, document_type='cv', pdf_bytes=b'%PDF frozen')
        with self.captureOnCommitCallbacks(execute=True):
            document.delete()
        submitted.refresh_from_db()
        self.assertIsNone(submitted.original_document)
        self.assertEqual(bytes(submitted.pdf_bytes), b'%PDF frozen')
        self.assertFalse(storage.exists(pdf_name))
        self.assertFalse(storage.exists(docx_name))


class RollbackCleanupTests(MediaRootMixin, TransactionTestCase):
    def test_rolled_back_interview_deletion_keeps_audio_and_row(self):
        interview = Interview.objects.create(user=self.user)
        question = InterviewQuestion.objects.create(
            interview=interview, question_index=1, question_text='Example?')
        question.audio_file.save('keep.webm', ContentFile(b'audio'))
        name, storage = question.audio_file.name, question.audio_file.storage
        interview_id = interview.pk
        try:
            with transaction.atomic():
                interview.delete()
                raise ValueError('rollback')
        except ValueError:
            pass
        self.assertTrue(Interview.objects.filter(pk=interview_id).exists())
        self.assertTrue(storage.exists(name))
