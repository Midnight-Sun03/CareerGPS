from __future__ import annotations

import hashlib
import html
import json
import re
from datetime import date, datetime
from decimal import Decimal

from django.core.exceptions import ObjectDoesNotExist

from .contracts import CLEAN_SCHEMA_VERSION


EXPORT_SCHEMA_VERSION = 'careergps-clean-job-export-v1.1'
SELECTION_POLICY_VERSION = 'current-vacancies-v1'
DIRECT_PUBLICATION_SOURCE = 'careergps-direct'

_NON_VACANCY_TITLE = re.compile(
    r'\b(?:talent\s*pool|talentpool|hiring\s+pool|future\s+talent|talent\s+community|'
    r'future\s+opportunit(?:y|ies)|general\s+application|submit\s+(?:your\s+)?cv|'
    r'join\s+our\s+talent)\b', re.IGNORECASE)
_PLACEHOLDER_SOURCE = re.compile(
    r'copy\s+and\s+past(?:e)?|latest\s+role\s+description\s+from\s+hiring\s+portal|'
    r'insert\s+(?:job|role)\s+description', re.IGNORECASE)
_TAG = re.compile(r'<[^>]+>')
_SPACE = re.compile(r'\s+')


def _json_default(value):
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    raise TypeError(f'Unsupported canonical JSON value: {type(value).__name__}')


def canonical_json(value) -> str:
    return json.dumps(
        value, sort_keys=True, separators=(',', ':'), ensure_ascii=False,
        default=_json_default,
    )


def canonical_hash(value) -> str:
    return hashlib.sha256(canonical_json(value).encode('utf-8')).hexdigest()


def source_export_payload(source_record):
    raw = source_record.current_raw
    if raw is None:
        raise ValueError('Source record has no current raw source version.')
    source_url = source_record.source_url or raw.source_url or raw.listing_url
    return {
        'export_schema_version': EXPORT_SCHEMA_VERSION,
        'opportunity_id': source_record.opportunity_id,
        'source': {
            'source_name': source_record.source.code,
            'external_opportunity_id': source_record.source_external_id,
            'source_url': source_url or '',
            'captured_at': raw.fetched_at.isoformat(),
        },
        'job_data': {
            'raw_content': raw.raw_content or '',
            'raw_payload': raw.raw_payload or {},
            'listing_url': source_record.listing_url or raw.listing_url or '',
            'source_url': source_url or '',
            'apply_url': source_record.apply_url or raw.apply_url or '',
            'reference_number': source_record.reference_number or '',
            # Domain context is transformed metadata, not raw vacancy prose. It
            # makes the already-enforced export boundary auditable and supplies
            # an exact publisher value for sources whose raw payload omits it.
            'source_context': {
                'publisher_name': (source_record.source.company
                                   or source_record.opportunity.company or ''),
                'listing_kind': source_record.opportunity.listing_kind,
                'opportunity_kind': source_record.opportunity.opportunity_kind,
            },
        },
    }


def source_input_hash(source_record) -> str:
    return canonical_hash(source_export_payload(source_record))


def export_record(source_record):
    payload = source_export_payload(source_record)
    payload['source_input_hash'] = canonical_hash(payload)
    return payload


def source_sections(source_record) -> dict[str, str]:
    data = source_export_payload(source_record)['job_data']
    sections: dict[str, str] = {}

    def walk(value, path):
        if isinstance(value, str):
            sections[path] = value
        elif isinstance(value, (int, float, Decimal)) and not isinstance(value, bool):
            # Contract 1.1 permits compact provenance to point at exported
            # numeric scalars (notably structured salary bounds).
            sections[path] = canonical_json(value)
        elif isinstance(value, dict):
            for key, item in value.items():
                walk(item, f'{path}.{key}' if path else str(key))
        elif isinstance(value, list):
            for index, item in enumerate(value):
                walk(item, f'{path}[{index}]')

    walk(data, '')
    return sections


def visible_source_text(value) -> str:
    """Deterministic HTML/entity/whitespace cleanup for quality checks only."""
    return _SPACE.sub(' ', _TAG.sub(' ', html.unescape(str(value or '')))).strip()


def source_export_candidates():
    from ingestion.models import OpportunitySourceRecord
    return OpportunitySourceRecord.objects.filter(
        current_raw__isnull=False,
    ).exclude(source__code__iexact='adzuna').select_related(
        'source', 'current_raw', 'opportunity').order_by('opportunity_id', 'pk')


def _direct_publication_exclusion_reason(source_record) -> str | None:
    """Validate the controlled publisher without treating it as a pollable source."""
    if source_record.source.code != DIRECT_PUBLICATION_SOURCE:
        return 'ingestion source is disabled'
    if source_record.source.source_type != 'direct':
        return 'controlled direct source has an invalid source type'
    try:
        vacancy = source_record.opportunity.employer_vacancy
    except (AttributeError, ObjectDoesNotExist):
        return 'controlled direct source has no employer vacancy'
    organization = vacancy.organization
    if organization.account_status != 'active':
        return 'employer organization is inactive'
    if organization.verification_status != 'approved':
        return 'employer organization is not approved'
    if vacancy.status != 'published':
        return f'employer vacancy is {vacancy.status!r}, not published'
    if vacancy.trust_hold:
        return 'employer vacancy is held for trust review'
    if not vacancy.no_candidate_fees:
        return 'employer vacancy has not confirmed the no-candidate-fee policy'
    if vacancy.opportunity_id != source_record.opportunity_id:
        return 'employer vacancy does not own this opportunity'
    return None


def scope_exclusion_reason(source_record) -> str | None:
    opportunity = source_record.opportunity
    if source_record.source.code == DIRECT_PUBLICATION_SOURCE:
        reason = _direct_publication_exclusion_reason(source_record)
        if reason:
            return reason
    elif not source_record.source.active:
        return 'ingestion source is disabled'
    if not source_record.active_at_source:
        return 'source record is no longer active at source'
    if not opportunity.is_active:
        return 'opportunity is inactive'
    if opportunity.lifecycle_status != 'active':
        return f'opportunity lifecycle is {opportunity.lifecycle_status!r}, not active'
    if opportunity.listing_kind != 'vacancy':
        return f'listing_kind is {opportunity.listing_kind!r}, not vacancy'
    if opportunity.scope_status not in {'in_scope', 'unclassified'}:
        return f'scope_status is {opportunity.scope_status!r}'
    if opportunity.moderation_status == 'suppressed':
        return 'opportunity is moderation-suppressed'
    if _NON_VACANCY_TITLE.search(opportunity.title or ''):
        return 'title deterministically identifies a talent pool/general/future application'
    return None


def _source_title(raw_payload):
    if not isinstance(raw_payload, dict):
        return ''
    listing = raw_payload.get('listing') or {}
    job = raw_payload.get('job') or {}
    return listing.get('title') or job.get('title') or raw_payload.get('title') or ''


def _description_material(payload):
    data = payload['job_data']
    raw_payload = data.get('raw_payload') or {}
    candidates = []
    if isinstance(raw_payload, dict):
        direct = [raw_payload.get('description'), raw_payload.get('responsibilities')]
        job = raw_payload.get('job') or {}
        if isinstance(job, dict):
            direct.append(job.get('content'))
        sections = raw_payload.get('sections') or {}
        if isinstance(sections, dict):
            direct.extend(value for value in sections.values() if isinstance(value, str))
        candidates.extend(value for value in direct if isinstance(value, str) and value.strip())
    if not candidates and data.get('raw_content'):
        candidates.append(data['raw_content'])
    return candidates


def source_quality_issue(source_record) -> str | None:
    payload = source_export_payload(source_record)
    raw_payload = payload['job_data']['raw_payload']
    if not str(_source_title(raw_payload)).strip():
        return 'no source title is available in the exported raw payload'
    publisher = payload['job_data']['source_context']['publisher_name']
    raw_company = ((raw_payload.get('job') or {}).get('company_name')
                   if isinstance(raw_payload, dict) else '')
    raw_company = raw_company or (raw_payload.get('company') if isinstance(raw_payload, dict) else '')
    if not str(publisher or raw_company or '').strip():
        return 'no source-backed company/publisher value is available'
    candidates = _description_material(payload)
    combined = ' '.join(visible_source_text(value) for value in candidates).strip()
    if not combined:
        return 'no vacancy description/source material is available'
    if _PLACEHOLDER_SOURCE.search(combined):
        return 'vacancy description is a deterministic placeholder'
    if len(combined) < 40 or len(re.findall(r'[A-Za-z]{2,}', combined)) < 6:
        return 'vacancy description is link-only or too short to clean safely'
    return None


def source_record_eligibility_error(source_record) -> str | None:
    reason = scope_exclusion_reason(source_record)
    if reason:
        return f'not in cleaning scope: {reason}'
    reason = source_quality_issue(source_record)
    if reason:
        return f'insufficient source material: {reason}'
    return None


def build_export_batch(opportunity_ids=None):
    rows = []
    exclusions = []
    ids = set(opportunity_ids or [])
    records = source_export_candidates()
    if ids:
        records = records.filter(opportunity_id__in=ids)
    for record in records.iterator(chunk_size=500):
        reason = scope_exclusion_reason(record)
        category = 'scope'
        if reason is None:
            reason = source_quality_issue(record)
            category = 'source_quality'
        if reason:
            exclusions.append({
                'category': category,
                'source_name': record.source.code,
                'external_opportunity_id': record.source_external_id,
                'opportunity_id': record.opportunity_id,
                'reason': reason,
            })
            continue
        rows.append(export_record(record))
    digest = canonical_hash([
        {'opportunity_id': row['opportunity_id'], 'source_input_hash': row['source_input_hash']}
        for row in rows
    ])
    manifest = {
        'record_type': 'manifest',
        'format_version': EXPORT_SCHEMA_VERSION,
        'contract_version': CLEAN_SCHEMA_VERSION,
        'record_count': len(rows),
        'reconciliation_digest': digest,
        'data_boundary': 'job_source_data_only',
        'selection_policy_version': SELECTION_POLICY_VERSION,
    }
    return manifest, rows, exclusions
