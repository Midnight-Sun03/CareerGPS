from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time, timezone as datetime_timezone
from decimal import Decimal, InvalidOperation
import re

from django.db import transaction
from django.db.models import Max
from django.utils import timezone
from pydantic import ValidationError as PydanticValidationError
from dateutil import parser as date_parser

from core.models import (
    CleanCriterionEvidence, CleanOpportunityCriterion, CleanRequirementGroup,
    Opportunity, OpportunityCleanAttempt,
    OpportunityCleanRevision, OpportunityOccupationResolution,
    ProfileOpportunityEvaluation,
)
from ingestion.models import OpportunitySourceRecord

from .contracts import CLEAN_SCHEMA_VERSION, CleanJobPayload
from .source import (
    EXPORT_SCHEMA_VERSION, SELECTION_POLICY_VERSION, canonical_hash,
    source_input_hash, source_record_eligibility_error, source_sections,
    visible_source_text,
)
from core.taxonomies.normalization import normalize_term


class CleanImportError(ValueError):
    def __init__(self, errors, *, attempt=None):
        self.errors = list(errors)
        self.attempt = attempt
        super().__init__('; '.join(self.errors[:5]))


@dataclass(frozen=True)
class CleanImportResult:
    attempt_id: int
    revision_id: int
    opportunity_id: int
    created: bool
    activated: bool


@dataclass(frozen=True)
class ValidatedCleanPayload:
    contract: CleanJobPayload
    source_record: OpportunitySourceRecord
    payload_hash: str


@dataclass(frozen=True)
class ValidatedCleanBatch:
    manifest: dict
    rows: tuple[tuple[int, dict, ValidatedCleanPayload], ...]


def _preliminary_identity(payload):
    source = payload.get('source') if isinstance(payload, dict) else None
    if not isinstance(source, dict):
        return None
    name = str(source.get('source_name') or '').strip()
    external_id = str(source.get('external_opportunity_id') or '').strip()
    if not name or not external_id:
        return None
    return name, external_id


def _find_source_record(identity):
    if identity is None:
        return None
    return OpportunitySourceRecord.objects.select_related(
        'source', 'opportunity', 'current_raw').filter(
            source__code=identity[0], source_external_id=identity[1]).first()


def _quarantine(attempt, errors):
    attempt.status = 'quarantined'
    attempt.validation_errors = list(errors)
    attempt.validated_at = timezone.now()
    attempt.save(update_fields=['status', 'validation_errors', 'validated_at'])
    return CleanImportError(errors, attempt=attempt)


def _validate_evidence(contract, source_record):
    errors = []
    sections = source_sections(source_record)

    def check(items, source_section, owner):
        section = sections.get(source_section)
        if section is None:
            errors.append(f'{owner}: source_section {source_section!r} was not exported')
            return
        for index, evidence in enumerate(items):
            label = f'{owner}.evidence[{index}]'
            if evidence.start_char is not None:
                if evidence.end_char > len(section):
                    errors.append(f'{label}: offsets exceed source section length')
                elif section[evidence.start_char:evidence.end_char] != evidence.text:
                    errors.append(f'{label}: offsets do not identify the exact evidence text')
            elif evidence.text not in section:
                errors.append(f'{label}: evidence text is not present in its source section')

    for criterion in contract.criteria:
        check(criterion.evidence, criterion.source_section, f'criterion {criterion.criterion_id}')
    for field_name, references in contract.job.provenance:
        for index, reference in enumerate(references):
            check([reference.evidence], reference.source_section,
                  f'job.provenance.{field_name}[{index}]')
    # Seniority evidence may come from any exported section. It has no separate
    # source_section field in Contract v1, so every quote must occur somewhere.
    all_sections = tuple(sections.values())
    for index, evidence in enumerate(contract.job.seniority.evidence):
        if evidence.start_char is not None:
            matching = any(
                evidence.end_char <= len(section)
                and section[evidence.start_char:evidence.end_char] == evidence.text
                for section in all_sections)
        else:
            matching = any(evidence.text in section for section in all_sections)
        if not matching:
            errors.append(f'job.seniority.evidence[{index}] is not present in exported source data')
    return errors


def _normal_text(value):
    return ' '.join(visible_source_text(value).casefold().split())


def _provenance_texts(contract, sections, field):
    # The exact cited quote, already verified against its exported section, is
    # the support boundary. Merely naming a large section must not license an
    # unrelated fact elsewhere in that section.
    return [reference.evidence.text
            for reference in getattr(contract.job.provenance, field)
            if reference.source_section in sections]


def _validate_job_provenance(contract, source_record):
    """Apply deterministic linkage checks without pretending to do NLP."""
    errors = []
    job = contract.job
    sections = source_sections(source_record)

    def joined(field):
        return _normal_text(' '.join(_provenance_texts(contract, sections, field)))

    for field, value in (
            ('cleaned_title', job.cleaned_title), ('company', job.company),
            ('cleaned_description', job.cleaned_description)):
        source_text = joined(field)
        if _normal_text(value) not in source_text:
            errors.append(
                f'job.{field}: value is not textually supported by its provenance')

    location_source = joined('location')
    for name, value in job.location.model_dump().items():
        if value not in (None, '') and _normal_text(value) not in location_source:
            errors.append(f'job.location.{name}: value is not textually supported by its provenance')

    aliases = {
        'employment_type': {
            'FULL_TIME': ('full time', 'full-time', 'permanent'),
            'PART_TIME': ('part time', 'part-time'), 'CONTRACT': ('contract', 'fixed term'),
            'TEMPORARY': ('temporary', 'temp '), 'INTERNSHIP': ('internship', 'intern '),
            'LEARNERSHIP': ('learnership',), 'APPRENTICESHIP': ('apprenticeship',),
            'CASUAL': ('casual',), 'VOLUNTEER': ('volunteer',), 'OTHER': ('other',),
        },
        'work_arrangement': {
            'ONSITE': ('onsite', 'on-site', 'on site'), 'HYBRID': ('hybrid',),
            'REMOTE': ('remote', 'work from home'), 'FIELD_BASED': ('field based', 'field-based'),
            'OTHER': ('other',),
        },
        'opportunity_kind': {
            'VACANCY': ('vacancy',), 'INTERNSHIP': ('internship',),
            'LEARNERSHIP': ('learnership',), 'APPRENTICESHIP': ('apprenticeship',),
            'GRADUATE_PROGRAMME': ('graduate programme', 'graduate program'),
            'BURSARY': ('bursary',), 'OTHER': ('other',),
        },
    }
    employment_source = joined('employment_type')
    for value in job.employment_type:
        if value != 'UNKNOWN' and not any(alias in employment_source for alias in aliases['employment_type'][value]):
            errors.append(f'job.employment_type: {value} is not supported by its provenance')
    if job.work_arrangement != 'UNKNOWN' and not any(
            alias in joined('work_arrangement') for alias in aliases['work_arrangement'][job.work_arrangement]):
        errors.append(f'job.work_arrangement: {job.work_arrangement} is not supported by its provenance')
    if not any(alias in joined('opportunity_kind')
               for alias in aliases['opportunity_kind'][job.opportunity_kind]):
        errors.append(f'job.opportunity_kind: {job.opportunity_kind} is not supported by its provenance')

    for field in ('posted_at', 'closing_at'):
        value = getattr(job, field)
        if not value:
            continue
        expected = _sortable_datetime(value)
        matched = False
        for text in _provenance_texts(contract, sections, field):
            try:
                parsed = date_parser.parse(visible_source_text(text), fuzzy=True)
                if parsed.tzinfo is None:
                    parsed = parsed.replace(tzinfo=datetime_timezone.utc)
                if 'T' in value:
                    matched = parsed == expected
                else:
                    matched = parsed.date() == expected.date()
            except (ValueError, OverflowError):
                continue
            if matched:
                break
        if not matched:
            errors.append(f'job.{field}: date is not supported by its provenance')

    if job.salary:
        salary_texts = _provenance_texts(contract, sections, 'salary')
        salary_source = _normal_text(' '.join(salary_texts))
        if _normal_text(job.salary.raw) not in salary_source:
            errors.append('job.salary.raw is not textually supported by its provenance')
        currency_patterns = {
            'ZAR': (r'\bzar\b', r'(?<![a-z])r\s*\d'),
            'USD': (r'\busd\b', r'\$\s*\d'),
            'GBP': (r'\bgbp\b', r'£\s*\d'),
            'EUR': (r'\beur\b', r'€\s*\d'),
            'PLN': (r'\bpln\b',), 'HUF': (r'\bhuf\b',), 'RON': (r'\bron\b',),
        }
        if job.salary.currency:
            accepted = currency_patterns.get(
                job.salary.currency.upper(),
                (rf'\b{re.escape(job.salary.currency.casefold())}\b',))
            if not any(re.search(pattern, salary_source) for pattern in accepted):
                errors.append('job.salary.currency is not supported by its provenance')
        numbers = []
        for text in salary_texts:
            for token in re.findall(r'(?<!\w)[0-9][0-9 ,.]*', str(text)):
                try:
                    numbers.append(Decimal(token.replace(' ', '').replace(',', '')))
                except InvalidOperation:
                    pass
        for name in ('minimum', 'maximum'):
            bound = getattr(job.salary, name)
            if bound is not None and bound not in numbers:
                errors.append(f'job.salary.{name} is not supported by its provenance')
        period_patterns = {
            'HOUR': (r'\b(?:hour|hourly|per hour)\b',),
            'DAY': (r'\b(?:day|daily|per day)\b',),
            'WEEK': (r'\b(?:week|weekly|per week)\b',),
            'MONTH': (r'\b(?:month|monthly|per month)\b',),
            'YEAR': (r'\b(?:year|annual|annually|per annum|p\.a\.)\b',),
            'PROJECT': (r'\b(?:project|per project)\b',),
            'OTHER': (r'\bother\b',),
        }
        if (job.salary.period != 'UNKNOWN'
                and not any(re.search(pattern, salary_source)
                            for pattern in period_patterns[job.salary.period])):
            errors.append('job.salary.period is not supported by its provenance')
        compensation_marker = re.search(
            r'\b(?:salary|pay|compensation|remuneration|wage)\b', salary_source)
        explicit_true = re.search(
            r'(?<!non-)(?<!non )\bnegotiable\b', salary_source)
        explicit_false = re.search(
            r'\b(?:non[- ]negotiable|not negotiable)\b|'
            r'\bfixed (?:salary|pay|compensation|remuneration|wage)\b',
            salary_source)
        if job.salary.is_negotiable is True and not (compensation_marker and explicit_true):
            errors.append('job.salary.is_negotiable=true is not supported by its provenance')
        if job.salary.is_negotiable is False and not (compensation_marker and explicit_false):
            errors.append('job.salary.is_negotiable=false is not supported by its provenance')
    return errors


def _sortable_datetime(value):
    if not value:
        return None
    parsed = datetime.fromisoformat(value.replace('Z', '+00:00')) if 'T' in value else datetime.combine(
        datetime.fromisoformat(value).date(), time.max, tzinfo=datetime_timezone.utc)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=datetime_timezone.utc)
    return parsed


def _revision_values(contract, source_record, attempt, payload_hash, revision_number):
    job = contract.job
    return {
        'opportunity': source_record.opportunity,
        'attempt': attempt,
        'source_record': source_record,
        'raw_opportunity': source_record.current_raw,
        'revision_number': revision_number,
        'schema_version': CLEAN_SCHEMA_VERSION,
        'source_input_hash': contract.source.source_input_hash,
        'payload_hash': payload_hash,
        'cleaner_name': contract.cleaner.name,
        'cleaner_version': contract.cleaner.version,
        'cleaner_cleaned_at': contract.cleaner.cleaned_at,
        'cleaner_run_id': contract.cleaner.run_id,
        'source_name': contract.source.source_name,
        'external_opportunity_id': contract.source.external_opportunity_id,
        'source_url': contract.source.source_url,
        'source_captured_at': contract.source.captured_at,
        'cleaned_title': job.cleaned_title,
        'normalized_title': normalize_term(job.cleaned_title),
        'company': job.company,
        'location': job.location.model_dump(mode='json'),
        'employment_type': list(job.employment_type),
        'work_arrangement': job.work_arrangement,
        'opportunity_kind': job.opportunity_kind,
        'posted_at': job.posted_at or '',
        'posted_sort_at': _sortable_datetime(job.posted_at),
        'closing_at': job.closing_at or '',
        'closing_sort_at': _sortable_datetime(job.closing_at),
        'salary': job.salary.model_dump(mode='json') if job.salary else None,
        'cleaned_description': job.cleaned_description,
        'seniority_label': job.seniority.label,
        'seniority_reason': job.seniority.reason,
        'seniority_evidence': [row.model_dump(mode='json') for row in job.seniority.evidence],
        'canonical_payload': contract.model_dump(mode='json'),
    }


def _persist_criteria(revision, contract):
    groups = {}
    for criterion in contract.criteria:
        group = criterion.requirement_group
        if group and group.group_id not in groups:
            groups[group.group_id] = CleanRequirementGroup.objects.create(
                revision=revision, group_id=group.group_id, operator=group.operator)
    for item in contract.criteria:
        criterion = CleanOpportunityCriterion.objects.create(
            revision=revision,
            criterion_id=item.criterion_id,
            criterion_type=item.criterion_type,
            normalized_value=item.normalized_value,
            raw_value=item.raw_value,
            requirement_level=item.requirement_level,
            significance=item.significance,
            source_section=item.source_section,
            requirement_group=(groups[item.requirement_group.group_id]
                               if item.requirement_group else None),
            qualification=(item.qualification.model_dump(mode='json')
                           if item.qualification else None),
            experience=(item.experience.model_dump(mode='json')
                        if item.experience else None),
            language=(item.language.model_dump(mode='json') if item.language else None),
        )
        CleanCriterionEvidence.objects.bulk_create([
            CleanCriterionEvidence(
                criterion=criterion, ordinal=index, text=evidence.text,
                start_char=evidence.start_char, end_char=evidence.end_char)
            for index, evidence in enumerate(item.evidence)
        ])


class _SourceChangedDuringImport(RuntimeError):
    pass


@transaction.atomic
def _activate_valid_contract(attempt, source_record, contract, payload_hash):
    locked_source = OpportunitySourceRecord.objects.select_for_update().select_related(
        'source', 'current_raw').get(pk=source_record.pk)
    opportunity = Opportunity.objects.select_for_update().get(pk=source_record.opportunity_id)
    attempt = OpportunityCleanAttempt.objects.select_for_update().get(pk=attempt.pk)
    if locked_source.current_raw_id != source_record.current_raw_id:
        raise _SourceChangedDuringImport
    existing = OpportunityCleanRevision.objects.filter(
        source_record=locked_source,
        source_input_hash=contract.source.source_input_hash,
        payload_hash=payload_hash,
    ).first()
    if existing:
        activated = opportunity.active_clean_revision_id == existing.pk
        attempt.status = 'validated'
        attempt.validated_at = timezone.now()
        attempt.validation_errors = []
        attempt.save(update_fields=['status', 'validated_at', 'validation_errors'])
        return CleanImportResult(attempt.pk, existing.pk, opportunity.pk, False, activated)

    revision_number = (OpportunityCleanRevision.objects.filter(
        opportunity=opportunity).aggregate(value=Max('revision_number'))['value'] or 0) + 1
    revision = OpportunityCleanRevision.objects.create(
        **_revision_values(contract, locked_source, attempt, payload_hash, revision_number))
    _persist_criteria(revision, contract)

    previous_id = opportunity.active_clean_revision_id
    if previous_id:
        OpportunityCleanRevision.objects.filter(pk=previous_id).update(
            status='superseded', superseded_at=timezone.now())
    revision.status = 'active'
    revision.activated_at = timezone.now()
    revision.save(update_fields=['status', 'activated_at'])
    opportunity.active_clean_revision = revision
    opportunity.save(update_fields=['active_clean_revision'])
    attempt.status = 'activated'
    attempt.validated_at = timezone.now()
    attempt.validation_errors = []
    attempt.save(update_fields=['status', 'validated_at', 'validation_errors'])

    OpportunityOccupationResolution.objects.filter(revision__opportunity=opportunity).delete()
    ProfileOpportunityEvaluation.objects.filter(opportunity=opportunity).update(state='STALE')
    from core.services.feed_queue import enqueue_opportunity_rebuild
    transaction.on_commit(lambda: enqueue_opportunity_rebuild(
        opportunity.pk, 'active_clean_revision_changed'))
    return CleanImportResult(attempt.pk, revision.pk, opportunity.pk, True, True)


def validate_clean_payload(payload) -> ValidatedCleanPayload:
    """Validate one payload against the current source without writing anything."""
    identity = _preliminary_identity(payload)
    source_record = _find_source_record(identity)
    try:
        contract = CleanJobPayload.model_validate(payload)
    except PydanticValidationError as exc:
        errors = [
            f"{'.'.join(str(bit) for bit in row['loc'])}: {row['msg']}"
            for row in exc.errors()
        ]
        raise CleanImportError(errors)
    if source_record is None:
        raise CleanImportError(['source identity does not match an OpportunitySourceRecord'])
    if source_record.current_raw_id is None:
        raise CleanImportError(['source record has no current raw source version'])

    errors = []
    if source_record.source.code == 'careergps-direct':
        eligibility_error = source_record_eligibility_error(source_record)
        if eligibility_error:
            errors.append(eligibility_error)
    expected_hash = source_input_hash(source_record)
    if contract.source.source_input_hash != expected_hash:
        errors.append('source_input_hash does not match the current exported source payload')
    expected_source_url = source_record.source_url or source_record.current_raw.source_url or source_record.current_raw.listing_url or ''
    if contract.source.source_url != expected_source_url:
        errors.append('source.source_url does not match the current source record')
    if contract.source.captured_at != source_record.current_raw.fetched_at:
        errors.append('source.captured_at does not match the current raw source version')
    errors.extend(_validate_evidence(contract, source_record))
    errors.extend(_validate_job_provenance(contract, source_record))
    canonical_payload = contract.model_dump(mode='json')
    payload_hash = canonical_hash(canonical_payload)
    if errors:
        raise CleanImportError(errors)
    return ValidatedCleanPayload(contract, source_record, payload_hash)


def validate_clean_batch(manifest, payload_rows) -> ValidatedCleanBatch:
    """Validate and reconcile a complete exported batch with zero writes."""
    errors = []
    required_manifest = {
        'record_type': 'manifest',
        'format_version': EXPORT_SCHEMA_VERSION,
        'contract_version': CLEAN_SCHEMA_VERSION,
        'data_boundary': 'job_source_data_only',
        'selection_policy_version': SELECTION_POLICY_VERSION,
    }
    if not isinstance(manifest, dict):
        errors.append('batch manifest is missing')
    else:
        allowed_manifest_keys = set(required_manifest) | {
            'record_count', 'reconciliation_digest',
        }
        extra_manifest_keys = sorted(set(manifest) - allowed_manifest_keys)
        if extra_manifest_keys:
            errors.append(
                'manifest contains unknown field(s): '
                + ', '.join(extra_manifest_keys))
        for key, expected in required_manifest.items():
            if manifest.get(key) != expected:
                errors.append(f'manifest.{key} must be {expected!r}')
        if (not isinstance(manifest.get('record_count'), int)
                or isinstance(manifest.get('record_count'), bool)
                or manifest.get('record_count') < 0):
            errors.append('manifest.record_count must be a non-negative integer')
        digest = manifest.get('reconciliation_digest')
        if not isinstance(digest, str) or not re.fullmatch(r'[0-9a-f]{64}', digest):
            errors.append('manifest.reconciliation_digest must be 64 lowercase hexadecimal characters')

    validated = []
    identities = {}
    for line_number, payload in payload_rows:
        if not isinstance(payload, dict):
            errors.append(f'line {line_number}: clean payload must be a JSON object')
            continue
        try:
            item = validate_clean_payload(payload)
        except CleanImportError as exc:
            errors.extend(f'line {line_number}: {message}' for message in exc.errors)
            continue
        eligibility_error = source_record_eligibility_error(item.source_record)
        if eligibility_error:
            errors.append(f'line {line_number}: {eligibility_error}')
            continue
        identity = (item.contract.source.source_name,
                    item.contract.source.external_opportunity_id)
        if identity in identities:
            errors.append(
                f'line {line_number}: duplicate cleaned result for '
                f'{identity[0]}:{identity[1]} (first on line {identities[identity]})')
        else:
            identities[identity] = line_number
        validated.append((line_number, payload, item))

    if isinstance(manifest, dict):
        if manifest.get('record_count') != len(identities):
            errors.append(
                f"manifest.record_count={manifest.get('record_count')!r} does not match "
                f'{len(identities)} valid unique result(s)')
        ordered = sorted(validated, key=lambda row: (
            row[2].source_record.opportunity_id, row[2].source_record.pk))
        digest = canonical_hash([
            {'opportunity_id': row[2].source_record.opportunity_id,
             'source_input_hash': row[2].contract.source.source_input_hash}
            for row in ordered
        ])
        if manifest.get('reconciliation_digest') != digest:
            errors.append('manifest reconciliation digest does not match cleaned source identities/hashes')
    if errors:
        raise CleanImportError(errors)
    return ValidatedCleanBatch(manifest, tuple(validated))


def import_clean_payload(payload) -> CleanImportResult:
    """Validate one complete v1 payload and atomically activate its immutable revision."""
    identity = _preliminary_identity(payload)
    source_record = _find_source_record(identity)
    attempt = OpportunityCleanAttempt.objects.create(
        opportunity=(source_record.opportunity if source_record else None),
        source_record=source_record,
        raw_opportunity=(source_record.current_raw if source_record else None),
        schema_version=str(payload.get('schema_version') or '') if isinstance(payload, dict) else '',
        source_input_hash=str((payload.get('source') or {}).get('source_input_hash') or '')
            if isinstance(payload, dict) and isinstance(payload.get('source'), dict) else '',
        raw_payload=payload if isinstance(payload, dict) else {'invalid_payload': repr(payload)},
    )
    try:
        validated = validate_clean_payload(payload)
    except CleanImportError as exc:
        raise _quarantine(attempt, exc.errors)
    contract = validated.contract
    source_record = validated.source_record
    payload_hash = validated.payload_hash
    attempt.cleaner_name = contract.cleaner.name
    attempt.cleaner_version = contract.cleaner.version
    attempt.cleaner_run_id = contract.cleaner.run_id
    attempt.payload_hash = payload_hash
    attempt.save(update_fields=[
        'cleaner_name', 'cleaner_version', 'cleaner_run_id', 'payload_hash'])

    try:
        return _activate_valid_contract(attempt, source_record, contract, payload_hash)
    except _SourceChangedDuringImport:
        attempt.refresh_from_db()
        raise _quarantine(
            attempt, ['source changed while the clean payload was being imported'])
