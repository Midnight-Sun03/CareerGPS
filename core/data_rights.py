"""Self-service data export and account closure for signed-in candidates.

Owner-approved behaviour (locked 2026-09-26):

* closure is immediate and self-service; there is no approval queue, no
  cancellation period and no manual review step;
* candidate-owned records are erased, protected records are anonymised in
  place, and the account can never sign in again;
* the email address is released so the same person can register again;
* every export contains only the requester's own data and states its own
  limitations instead of silently omitting anything.

This module never invents retention periods. Records the employer side
genuinely needs stay with the employer and are flagged in the export
manifest and the closure summary for the later retention-policy decision.
"""

from __future__ import annotations

import hashlib
import json
import logging
import secrets
import tempfile
import zipfile
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal

from django.conf import settings
from django.contrib.auth.models import User
from django.core.mail import EmailMultiAlternatives
from django.db import transaction
from django.db.models import ProtectedError
from django.utils import timezone

from .models import (
    Achievement,
    Certification,
    DataRightsEvent,
    Education,
    Language,
    LegalAcceptanceEvent,
    Notification,
    NotificationPreference,
    OpportunityReport,
    PrivacyRequest,
    Profile,
    ProfileInterest,
    ProfileLink,
    ProfileSkill,
    Project,
    SavedOpportunity,
    Story,
    StoryReport,
    Volunteering,
    WorkExperience,
)

logger = logging.getLogger(__name__)

ANONYMISED_USERNAME_PREFIX = 'deleted-'
ANONYMISED_EMAIL_DOMAIN = 'careergps.invalid'
DELETED_CANDIDATE_LABEL = 'Deleted candidate'
EXPORT_SCHEMA_VERSION = '1.0'
EXPORT_SPOOL_MEMORY_BYTES = 5 * 1024 * 1024
EXPORT_HOURLY_LIMIT = 3
PASSWORD_FAILURE_LIMIT = 5
PASSWORD_FAILURE_WINDOW = timedelta(minutes=15)
CLOSURE_TOKEN_SESSION_KEY = 'account_closure_token'
CLOSURE_TOKEN_ISSUED_KEY = 'account_closure_token_issued_at'
CLOSURE_TOKEN_TTL = timedelta(minutes=5)
STATUS_MESSAGES = {
    'password_failed': 'Your password could not be verified.',
    'password_throttled': 'Your password could not be verified. Please try again later.',
    'token_required': 'For security, confirm your password again.',
}


def is_anonymised_account(user) -> bool:
    """True once an account has been closed and anonymised in place."""

    return bool(user) and str(user.username or '').startswith(ANONYMISED_USERNAME_PREFIX)


def is_deleted_candidate_label(user) -> bool:
    """Presentation-only check for employer-facing retained records."""

    return is_anonymised_account(user)


def _iso(value):
    if isinstance(value, datetime):
        if timezone.is_aware(value):
            value = timezone.localtime(value)
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    return value


def _row(instance, fields):
    return {name: _iso(getattr(instance, name)) for name in fields}


def _rows(queryset, fields):
    return [_row(instance, fields) for instance in queryset]


def _file_name(field_file) -> str:
    if field_file and getattr(field_file, 'name', ''):
        return field_file.name
    return ''


def _extension(name: str) -> str:
    clean = str(name or '')
    if '.' in clean.rsplit('/', 1)[-1]:
        return '.' + clean.rsplit('.', 1)[-1].lower()
    return '.bin'


# ---------------------------------------------------------------------------
# Export sections
# ---------------------------------------------------------------------------

EVIDENCE_MODELS = (
    (Education, 'education_records', ('institution', 'qualification', 'field_of_study',
        'nqf_level', 'start_date', 'end_date', 'currently_studying', 'description',
        'source', 'confirmed_by_user', 'created_at', 'updated_at')),
    (WorkExperience, 'work_experiences', ('title', 'organisation', 'location',
        'employment_type', 'dates', 'start_date', 'end_date', 'currently_working',
        'description', 'bullets', 'source', 'confirmed_by_user', 'created_at', 'updated_at')),
    (Project, 'projects', ('title', 'organisation', 'dates', 'start_date', 'end_date',
        'currently_ongoing', 'description', 'bullets', 'project_url',
        'source', 'confirmed_by_user', 'created_at', 'updated_at')),
    (Certification, 'certifications', ('title', 'dates', 'issuer', 'issue_date',
        'expiry_date', 'does_not_expire', 'credential_id', 'credential_url', 'description',
        'source', 'confirmed_by_user', 'created_at', 'updated_at')),
    (Achievement, 'achievements', ('title', 'organisation', 'date', 'description',
        'source', 'confirmed_by_user', 'created_at', 'updated_at')),
    (Volunteering, 'volunteering_records', ('role', 'organisation', 'location',
        'start_date', 'end_date', 'currently_volunteering', 'description',
        'source', 'confirmed_by_user', 'created_at', 'updated_at')),
    (ProfileSkill, 'profile_skills', ('entered_name', 'normalized_name', 'source',
        'confirmed_by_user', 'created_at', 'updated_at')),
    (ProfileInterest, 'profile_interests', ('entered_name', 'normalized_name', 'source',
        'confirmed_by_user', 'created_at', 'updated_at')),
    (Language, 'languages', ('language', 'proficiency', 'source', 'confirmed_by_user',
        'created_at', 'updated_at')),
    (ProfileLink, 'profile_links', ('label', 'link_type', 'url', 'source',
        'confirmed_by_user', 'created_at', 'updated_at')),
)

INTERVIEW_FIELDS = (
    'source_type', 'mode', 'question_delivery', 'camera_mode', 'camera_consent',
    'visual_analysis_version', 'question_count', 'job_info_status', 'status',
    'ai_model_name', 'popia_consent', 'occupation', 'experience_level', 'cv_text',
    'overall_presence_score', 'eye_contact_score', 'posture_score', 'gesture_score',
    'communication_score', 'confidence_score', 'structure_score', 'relevance_score',
    'strengths', 'improvements', 'feedback_summary', 'feedback_data',
    'candidate_fact_ids', 'generated_document_ids', 'context_snapshot',
    'error_message', 'created_at', 'updated_at',
)

QUESTION_FIELDS = (
    'question_index', 'question_text', 'question_type', 'focus', 'answer_mode',
    'answer_text', 'raw_transcript', 'corrected_transcript', 'provisional_transcript',
    'transcription_status', 'transcription_error', 'word_timestamps',
    'voice_analysis_status', 'delivery_metrics', 'visual_analysis_status',
    'visual_metrics', 'visual_analysis_error', 'evaluation', 'answer_feedback',
    'answer_strengths', 'answer_improvements', 'eye_contact_score', 'posture_score',
    'gesture_score', 'presence_score', 'created_at',
)


def _account_section(user: User) -> dict:
    return _row(user, ('first_name', 'last_name', 'email', 'username', 'date_joined',
        'last_login', 'is_active'))


def _profile_section(profile: Profile) -> dict:
    return {
        'qualification': profile.qualification,
        'institution': profile.institution,
        'location': profile.location,
        'phone': profile.phone,
        'custom_skills': profile.custom_skills,
        'profile_complete': profile.profile_complete,
        'created_at': _iso(profile.created_at),
        'updated_at': _iso(profile.updated_at),
        'skills': sorted(skill.name for skill in profile.skills.all()),
        'interests': sorted(interest.name for interest in profile.interests.all()),
    }


def _evidence_section(profile: Profile) -> dict:
    return {
        name: _rows(getattr(profile, name).all(), fields)
        for model, name, fields in EVIDENCE_MODELS
    }


def _career_section(profile: Profile) -> dict:
    preference = getattr(profile, 'career_preferences', None)
    resolutions = []
    for resolution in profile.target_resolutions.order_by('ordinal', 'pk'):
        resolutions.append({
            **_row(resolution, ('ordinal', 'authored_text', 'normalized_text', 'state',
                'resolver_version', 'diagnostics', 'resolved_at')),
            'identities': [
                {'concept_id': identity.concept_id, 'route': identity.route,
                 'provenance': identity.provenance}
                for identity in resolution.identities.order_by('pk')
            ],
        })
    return {
        'career_preference': _row(preference, ('target_roles', 'preferred_locations',
            'work_arrangement', 'opportunity_kinds', 'industries', 'short_career_goal',
            'created_at', 'updated_at')) if preference else None,
        'career_targets': resolutions,
    }


def _applications_section(user: User) -> list:
    from applications.models import Application

    rows = []
    queryset = (Application.objects.filter(user=user)
                .select_related('opportunity')
                .order_by('created_at', 'pk'))
    for application in queryset:
        hiring = getattr(application, 'hiring_application', None)
        row = _row(application, ('status', 'job_info_status', 'notes', 'applied_at',
            'created_at', 'updated_at'))
        row['opportunity'] = {
            'id': application.opportunity_id,
            'title': application.opportunity.title,
            'company': application.opportunity.company,
        }
        row['employer_stage_history'] = [
            _row(event, ('to_stage', 'reason_category', 'created_at'))
            for event in (hiring.stage_events.filter(candidate_visible=True).order_by('created_at', 'pk')
                          if hiring else [])
        ]
        row['submitted_documents'] = [
            {**_row(document, ('document_type', 'pdf_sha256', 'docx_sha256', 'created_at')),
             'held_by_employer': True}
            for document in (hiring.submitted_documents.defer('pdf_bytes', 'docx_bytes').order_by('created_at', 'pk')
                             if hiring else [])
        ]
        rows.append(row)
    return rows


def _interviews_section(user: User) -> list:
    from interview.models import Interview

    interviews = []
    for interview in Interview.objects.filter(user=user).order_by('created_at', 'pk'):
        data = _row(interview, INTERVIEW_FIELDS)
        data['opportunity_id'] = interview.opportunity_id
        data['application_id'] = interview.application_id
        data['questions'] = []
        for question in interview.questions.order_by('question_index', 'pk'):
            row = _row(question, QUESTION_FIELDS)
            row['audio_file'] = _file_name(question.audio_file)
            row['video_file'] = _file_name(question.video_file)
            data['questions'].append(row)
        interviews.append(data)
    return interviews


def _documents_section(user: User) -> list:
    from applications.models import GeneratedDocument

    rows = []
    for document in GeneratedDocument.objects.filter(user=user).order_by('created_at', 'pk'):
        rows.append({
            **_row(document, ('document_type', 'template_key', 'renderer_backend',
                'job_info_status', 'created_at')),
            'application_id': document.application_id,
            'opportunity_id': document.opportunity_id,
            'pdf_file': _file_name(document.pdf_file),
            'docx_file': _file_name(document.docx_file),
        })
    return rows


def _saved_section(user: User) -> list:
    rows = []
    queryset = (SavedOpportunity.objects.filter(user=user)
                .select_related('opportunity')
                .order_by('-saved_at', '-pk'))
    for saved in queryset:
        rows.append({
            'saved_at': _iso(saved.saved_at),
            'opportunity_id': saved.opportunity_id,
            'title': saved.opportunity.title,
            'company': saved.opportunity.company,
        })
    return rows


def build_export_sections(user: User) -> dict:
    """JSON-able export sections, keyed by their archive file name."""

    profile = getattr(user, 'profile', None)
    return {
        'account.json': {'account': _account_section(user)},
        'profile.json': {'profile': _profile_section(profile) if profile else None},
        'evidence.json': _evidence_section(profile) if profile else {},
        'career.json': _career_section(profile) if profile else {
            'career_preference': None, 'career_targets': []},
        'applications.json': {'applications': _applications_section(user)},
        'interviews.json': {'interviews': _interviews_section(user)},
        'documents.json': {'generated_documents': _documents_section(user)},
        'saved_opportunities.json': {'saved_opportunities': _saved_section(user)},
        'notifications.json': {'notifications': _rows(
            Notification.objects.filter(user=user).order_by('-created_at', '-pk'),
            ('category', 'event_key', 'notification_type', 'title', 'message',
             'target_url', 'is_read', 'created_at', 'read_at'))},
        'story.json': {'stories': _rows(
            Story.objects.filter(user=user).order_by('-submitted_at', '-pk'),
            ('title', 'display_name', 'qualification', 'institution', 'content',
             'status', 'moderation_note', 'submitted_at', 'approved_at'))},
        'legal_acceptances.json': {'legal_acceptances': _rows(
            LegalAcceptanceEvent.objects.filter(user=user).order_by('-created_at', '-pk'),
            ('document_type', 'document_version', 'content_sha256', 'action',
             'context', 'created_at'))},
        'privacy_requests.json': {'privacy_requests': _rows(
            PrivacyRequest.objects.filter(requester=user).order_by('-created_at', '-pk'),
            ('category', 'description', 'status', 'contact_email', 'created_at',
             'updated_at', 'handled_at'))},
    }


# ---------------------------------------------------------------------------
# Export archive
# ---------------------------------------------------------------------------

EXPORT_EXCLUSIONS = (
    "Frozen copies of CVs and cover letters already submitted to an employer are "
    "held in that employer's application record. They are listed with their SHA-256 "
    "fingerprints in applications.json instead of being repeated in this archive.",
    "Account credentials, sign-in sessions, internal security, audit and moderation "
    "records, employer-private notes and any other person's information are not part "
    "of this export.",
)


@dataclass
class ExportEntry:
    """One storage file offered to the archive."""

    arcname: str
    name: str
    storage: object


def collect_export_files(user: User) -> list:
    """Candidate-owned media that belongs in the export archive."""

    from applications.models import GeneratedDocument
    from interview.models import InterviewQuestion

    entries = []
    documents = GeneratedDocument.objects.filter(user=user).order_by('created_at', 'pk')
    for document in documents:
        base = f'files/generated_documents/{document.document_type}-{document.pk}'
        for attribute in ('pdf_file', 'docx_file'):
            field_file = getattr(document, attribute)
            if _file_name(field_file):
                entries.append(ExportEntry(f'{base}{_extension(field_file.name)}',
                    field_file.name, field_file.storage))

    questions = (InterviewQuestion.objects.filter(interview__user=user)
                 .order_by('interview_id', 'question_index', 'pk'))
    for question in questions:
        for attribute, folder in (('audio_file', 'interview_audio'),
                                  ('video_file', 'interview_video')):
            field_file = getattr(question, attribute)
            if _file_name(field_file):
                arcname = (f'files/{folder}/interview-{question.interview_id}'
                           f'-question-{question.pk}{_extension(field_file.name)}')
                entries.append(ExportEntry(arcname, field_file.name, field_file.storage))
    return entries


def _export_limitations(missing: list) -> list:
    limitations = [
        'This archive is a point-in-time copy. Records an employer retains for a '
        'submitted application, and any record kept to meet a legal obligation, are '
        'described in this archive but are not cancelled by downloading it.',
    ]
    if missing:
        limitations.append(
            'Some files recorded in this account are no longer present in storage. '
            'They are listed under "missing_files" so that nothing is omitted silently.')
    return limitations


def write_export_zip(user: User, target, *, generated_at=None) -> dict:
    """Stream a complete export into ``target`` and return its manifest.

    Media is copied in fixed-size chunks so it is never held in memory as a
    whole, and the archive is written to ``target`` (a spooled temporary file
    supplied by the caller) rather than being assembled in memory.
    """

    generated_at = generated_at or timezone.now()
    sections = build_export_sections(user)
    available, missing = [], []
    for entry in collect_export_files(user):
        (available if entry.storage.exists(entry.name) else missing).append(entry)

    manifest = {
        'schema_version': EXPORT_SCHEMA_VERSION,
        'generated_at': _iso(generated_at),
        'sections': sorted(sections),
        'files': [],
        'missing_files': sorted({entry.name for entry in missing}),
        'excluded': list(EXPORT_EXCLUSIONS),
        'limitations': _export_limitations(missing),
    }

    with zipfile.ZipFile(target, 'w', zipfile.ZIP_DEFLATED) as archive:
        for name, payload in sections.items():
            archive.writestr(name, json.dumps(payload, indent=2, ensure_ascii=False))
        for entry in available:
            digest, size = hashlib.sha256(), 0
            with archive.open(entry.arcname, 'w', force_zip64=True) as destination:
                with entry.storage.open(entry.name, 'rb') as source:
                    for chunk in iter(lambda: source.read(65536), b''):
                        size += len(chunk)
                        digest.update(chunk)
                        destination.write(chunk)
            manifest['files'].append({
                'path': entry.arcname, 'bytes': size, 'sha256': digest.hexdigest()})
        archive.writestr('manifest.json', json.dumps(manifest, indent=2, ensure_ascii=False))
    return manifest


def open_export_spool(user: User):
    """Write the export to a spooled file (RAM, spilling to disk) and rewind it."""

    spool = tempfile.SpooledTemporaryFile(max_size=EXPORT_SPOOL_MEMORY_BYTES, suffix='.zip')
    manifest = write_export_zip(user, spool)
    spool.seek(0)
    return spool, manifest


# ---------------------------------------------------------------------------
# Accountability trail, throttling and closure tokens
# ---------------------------------------------------------------------------

def record_event(user, action, detail=None) -> DataRightsEvent:
    """Write an accountability row that never contains personal information."""

    return DataRightsEvent.objects.create(user=user, action=action, detail=detail or {})


def record_password_failure(user) -> None:
    record_event(user, DataRightsEvent.Action.CLOSURE_PASSWORD_FAILED)


def password_failure_is_throttled(user) -> bool:
    failures = DataRightsEvent.objects.filter(
        user=user,
        action=DataRightsEvent.Action.CLOSURE_PASSWORD_FAILED,
        created_at__gte=timezone.now() - PASSWORD_FAILURE_WINDOW,
    ).count()
    return failures >= PASSWORD_FAILURE_LIMIT


def export_is_throttled(user) -> bool:
    recent = DataRightsEvent.objects.filter(
        user=user,
        action=DataRightsEvent.Action.EXPORT_CREATED,
        created_at__gte=timezone.now() - timedelta(hours=1),
    ).count()
    return recent >= EXPORT_HOURLY_LIMIT


def issue_closure_token(request) -> str:
    token = secrets.token_urlsafe(32)
    request.session[CLOSURE_TOKEN_SESSION_KEY] = token
    request.session[CLOSURE_TOKEN_ISSUED_KEY] = timezone.now().isoformat()
    return token


def clear_closure_token(request) -> None:
    request.session.pop(CLOSURE_TOKEN_SESSION_KEY, None)
    request.session.pop(CLOSURE_TOKEN_ISSUED_KEY, None)


def closure_token_is_valid(request) -> bool:
    """A correct password alone can never delete: the one-time token must be live."""

    token = request.session.get(CLOSURE_TOKEN_SESSION_KEY)
    issued = request.session.get(CLOSURE_TOKEN_ISSUED_KEY)
    if not token or not issued:
        return False
    try:
        issued_at = datetime.fromisoformat(issued)
    except (TypeError, ValueError):
        return False
    if timezone.is_naive(issued_at):
        issued_at = timezone.make_aware(issued_at)
    return timezone.now() - issued_at <= CLOSURE_TOKEN_TTL


def closure_blockers(user) -> list:
    """Reasons this account must not be closed automatically."""

    from employers.models import Organization, OrganizationMembership

    names = {
        membership.organization.name
        for membership in OrganizationMembership.objects.filter(user=user, role='owner')
            .exclude(status='disabled').select_related('organization')
    }
    names.update(Organization.objects.filter(created_by=user).values_list('name', flat=True))
    if not names:
        return []
    return [
        'This account manages the employer organisation(s) '
        + ', '.join(sorted(names))
        + '. Transfer ownership to another member before closing your account, or use the '
        'privacy request channel if you need help.',
    ]


# ---------------------------------------------------------------------------
# Erasure and anonymisation
# ---------------------------------------------------------------------------

PUBLISHED_STORY_STATUS = 'approved'
CLOSURE_REQUEST_DESCRIPTION = (
    'Self-service account closure completed automatically from the account settings. '
    'No staff review, approval queue or delay was involved. Personal information was '
    'erased or anonymised immediately; this record exists only for accountability.'
)


def _erase(queryset) -> int:
    count = queryset.count()
    if count:
        queryset.delete()
    return count


def _retained_summary(user) -> dict:
    """Counts of records that survive closure, without any personal information."""

    from employers.models import HiringApplication, SubmittedDocument

    submitted = HiringApplication.objects.filter(application__user=user)
    return {
        'employer_hiring_records': submitted.count(),
        'frozen_submitted_documents': SubmittedDocument.objects.filter(
            hiring_application__in=submitted).count(),
        'legal_acceptance_events': LegalAcceptanceEvent.objects.filter(user=user).count(),
        'privacy_requests': PrivacyRequest.objects.filter(requester=user).count(),
        'reports_filed': (StoryReport.objects.filter(reporter=user).count()
                          + OpportunityReport.objects.filter(reporter=user).count()),
    }


def _anonymise_story(story, *, keep_content: bool) -> None:
    story.user = None
    story.display_name = ''
    story.qualification = ''
    story.institution = ''
    if not keep_content:
        story.content = ''
    story.save(update_fields=['user', 'display_name', 'qualification', 'institution', 'content'])


def _erase_or_anonymise_stories(user) -> dict:
    """Published stories keep their published words; everything else is removed."""

    result = {'removed': 0, 'anonymised': 0, 'kept_for_moderation': 0}
    for story in Story.objects.filter(user=user):
        if story.status == PUBLISHED_STORY_STATUS:
            _anonymise_story(story, keep_content=True)
            result['anonymised'] += 1
            continue
        try:
            story.delete()
            result['removed'] += 1
        except ProtectedError:
            # Moderation history protects the row; keep the audit meaning and
            # blank every unnecessary personal field instead.
            _anonymise_story(story, keep_content=False)
            result['kept_for_moderation'] += 1
    return result


def _clear_domain_signals(user) -> int:
    from employers.models import MemberDomainSignal

    return MemberDomainSignal.objects.filter(membership__user=user).exclude(
        email_domain='').update(email_domain='', matches_organization=False,
        free_email=False, checked_at=timezone.now())


def purge_candidate_records(user, profile) -> dict:
    """Erase candidate-owned records.

    Deleting rows (rather than blanking them) is what makes the existing
    ``post_delete`` hooks unlink generated CVs, cover letters and interview
    audio once the transaction commits. Submitted workspaces an employer
    already received are kept as hiring history, with candidate-authored
    notes erased.
    """

    from applications.models import Application, GeneratedDocument
    from employers.models import HiringApplication
    from interview.models import Interview

    counts = {}
    submitted_ids = list(HiringApplication.objects.filter(
        application__user=user).values_list('application_id', flat=True))

    counts['interviews'] = _erase(Interview.objects.filter(user=user))
    counts['application_workspaces'] = _erase(
        Application.objects.filter(user=user).exclude(pk__in=submitted_ids))
    counts['retained_application_notes_cleared'] = (
        Application.objects.filter(user=user, pk__in=submitted_ids)
        .exclude(notes='').update(notes='', updated_at=timezone.now()))
    counts['generated_documents'] = _erase(GeneratedDocument.objects.filter(user=user))
    counts['saved_opportunities'] = _erase(SavedOpportunity.objects.filter(user=user))
    counts['notifications'] = _erase(Notification.objects.filter(user=user))
    counts['notification_preferences'] = _erase(NotificationPreference.objects.filter(user=user))

    stories = _erase_or_anonymise_stories(user)
    counts['stories_removed'] = stories['removed']
    counts['stories_anonymised'] = stories['anonymised'] + stories['kept_for_moderation']
    counts['employer_domain_signals_cleared'] = _clear_domain_signals(user)

    if profile is not None:
        profile.delete()
        counts['profile_deleted'] = 1
    return counts


def _anonymised_username(user) -> str:
    for _ in range(5):
        candidate = f'{ANONYMISED_USERNAME_PREFIX}{user.pk}-{secrets.token_hex(4)}'
        if not User.objects.filter(username=candidate).exists():
            return candidate
    return f'{ANONYMISED_USERNAME_PREFIX}{user.pk}-{secrets.token_hex(8)}'


def anonymise_user(user) -> None:
    """Scrub the account in place, deactivate it and release the email address.

    ``set_unusable_password`` plus ``is_active = False`` both invalidate every
    existing session and make every future sign-in attempt fail, so no custom
    session handling is required.
    """

    user.username = _anonymised_username(user)
    user.email = f'deleted+{secrets.token_hex(8)}@{ANONYMISED_EMAIL_DOMAIN}'
    user.first_name = ''
    user.last_name = ''
    user.is_active = False
    user.is_staff = False
    user.is_superuser = False
    user.last_login = None
    user.set_unusable_password()
    user.save(update_fields=['username', 'email', 'first_name', 'last_name', 'is_active',
                             'is_staff', 'is_superuser', 'last_login', 'password'])
    user.groups.clear()
    user.user_permissions.clear()


def record_closure_request(user) -> PrivacyRequest:
    """Tracking record only: never an approval queue and never a delay."""

    return PrivacyRequest.objects.create(
        requester=user,
        contact_email=user.email,
        category=PrivacyRequest.Category.DELETION,
        description=CLOSURE_REQUEST_DESCRIPTION,
        status=PrivacyRequest.Status.COMPLETED,
        handled_at=timezone.now(),
    )


def send_closure_confirmation(email: str):
    """Send the final confirmation to the address on file before closure.

    Email trouble must never block or roll back a completed deletion, so every
    failure is logged and reported instead of raised.
    """

    if not email or '@' not in email:
        return False, 'no email address on file'
    text_body = (
        'Your CareerGPS account has been deleted.\n\n'
        'Your profile, practice interviews, generated documents, saved opportunities\n'
        'and notifications have been erased, and the account can no longer sign in.\n\n'
        'Records an employer already received for an application you submitted, and\n'
        'records kept to meet a legal obligation, are retained without your name or\n'
        'contact details. You can register again with this email address at any time.\n\n'
        'If you did not request this, contact us using the privacy details in our\n'
        'Privacy Notice as soon as possible.\n'
    )
    html_body = (
        '<p>Your CareerGPS account has been deleted.</p>'
        '<p>Your profile, practice interviews, generated documents, saved opportunities '
        'and notifications have been erased, and the account can no longer sign in.</p>'
        '<p>Records an employer already received for an application you submitted, and '
        'records kept to meet a legal obligation, are retained without your name or '
        'contact details. You can register again with this email address at any time.</p>'
        '<p>If you did not request this, contact us using the privacy details in our '
        'Privacy Notice as soon as possible.</p>'
    )
    try:
        message = EmailMultiAlternatives(
            subject='Your CareerGPS account has been deleted',
            body=text_body,
            from_email=settings.DEFAULT_FROM_EMAIL,
            to=[email],
        )
        message.attach_alternative(html_body, 'text/html')
        message.send(fail_silently=False)
    except Exception as exc:  # Deletion must never be blocked by email trouble.
        logger.warning('CareerGPS could not send the account closure confirmation: %s', exc)
        return False, exc.__class__.__name__
    return True, ''


@dataclass
class ClosureReport:
    user_id: int
    already_closed: bool = False
    erased: dict = field(default_factory=dict)
    retained: dict = field(default_factory=dict)
    request_id: int = None
    email_sent: bool = False
    email_error: str = ''


def close_account(user, *, reason='self_service') -> ClosureReport:
    """Erase, anonymise and deactivate an account immediately.

    Data changes happen in one transaction; the confirmation email is sent only
    after the commit, and calling this twice is a safe no-op.
    """

    if is_anonymised_account(user):
        return ClosureReport(user_id=user.pk, already_closed=True)

    confirmation_email = (user.email or '').strip()
    profile = getattr(user, 'profile', None)
    report = ClosureReport(user_id=user.pk)

    with transaction.atomic():
        report.retained = _retained_summary(user)
        report.erased = purge_candidate_records(user, profile)
        anonymise_user(user)
        request = record_closure_request(user)
        report.request_id = request.pk
        record_event(user, DataRightsEvent.Action.CLOSURE_COMPLETED, {
            'reason': reason,
            'erased': report.erased,
            'retained': report.retained,
        })

    report.email_sent, report.email_error = send_closure_confirmation(confirmation_email)
    return report






