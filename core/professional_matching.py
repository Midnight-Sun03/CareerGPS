from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from datetime import date
from decimal import Decimal

from django.db.models import Q

from core.models import (
    CareerPreference, Certification, Education, Language, OccupationDefiningCriterion,
    ProfileOpportunityEvaluation, ProfileSkill, ProjectCapability, WorkExperience,
)
from core.professional_policies import (
    BAND_POLICY_VERSION, BAND_RANK, CURRENT_FIT_WEIGHTS, DISCOVERY_RANK,
    MATCHING_POLICY_VERSION, PROFILE_RELEVANT_POLICY_VERSION,
    RELATIONSHIP_POLICY_VERSION, NON_SCORING_CRITERIA,
    UNWEIGHTED_PENDING_CALIBRATION,
)
from core.services.professional_resolution import (
    discovery_relationship, resolve_opportunity_revision,
    resolve_preference_targets, taxonomy_fingerprint,
)
from core.taxonomies.normalization import normalize_term
from core.taxonomies.resolver import build_taxonomy_context


POSITIVE = {'FOUND', 'RELATED'}
IMPORTANT = {'CRUCIAL', 'CORE'}
STATUS_PRIORITY = {'FOUND': 4, 'RELATED': 3, 'UNKNOWN': 2, 'NOT_FOUND_ON_PROFILE': 1}


def _hash(value):
    return hashlib.sha256(json.dumps(
        value, sort_keys=True, separators=(',', ':'), default=str).encode()).hexdigest()


def _months(start, end, current=False):
    if not start:
        return None
    end = date.today() if current else end
    if not end or end < start:
        return None
    return max(0, (end.year - start.year) * 12 + end.month - start.month)


def confirmed_evidence(profile):
    prefetched_skills = getattr(profile, '_evidence_profile_skills', None)
    skills = ([{'pk': row.pk, 'entered_name': row.entered_name,
                'normalized_name': row.normalized_name}
               for row in prefetched_skills]
              if prefetched_skills is not None else list(ProfileSkill.objects.filter(
                  profile=profile, confirmed_by_user=True).values(
                      'pk', 'entered_name', 'normalized_name')))
    prefetched_project_rows = getattr(
        profile, '_evidence_project_capabilities', None)
    project_rows = (prefetched_project_rows if prefetched_project_rows is not None
                    else ProjectCapability.objects.filter(
        project__profile=profile, project__confirmed_by_user=True,
        confirmed_by_user=True).values('pk', 'entered_name', 'normalized_name'))
    for row in project_rows:
        skills.append({**row, 'project_capability': True})
    prefetched_education = getattr(profile, '_evidence_education_records', None)
    education = ([{'pk': row.pk, 'qualification': row.qualification,
                   'field_of_study': row.field_of_study, 'nqf_level': row.nqf_level}
                  for row in prefetched_education]
                 if prefetched_education is not None else list(Education.objects.filter(
                     profile=profile, confirmed_by_user=True).values(
                         'pk', 'qualification', 'field_of_study', 'nqf_level')))
    prefetched_experience = getattr(profile, '_evidence_work_experiences', None)
    experience = ([{'pk': row.pk, 'title': row.title, 'description': row.description,
                    'bullets': row.bullets, 'start_date': row.start_date,
                    'end_date': row.end_date, 'currently_working': row.currently_working}
                   for row in prefetched_experience]
                  if prefetched_experience is not None else list(WorkExperience.objects.filter(
                      profile=profile, confirmed_by_user=True).values(
                          'pk', 'title', 'description', 'bullets', 'start_date', 'end_date',
                          'currently_working')))
    prefetched_certifications = getattr(profile, '_evidence_certifications', None)
    certifications = ([{'pk': row.pk, 'title': row.title, 'description': row.description}
                        for row in prefetched_certifications]
                       if prefetched_certifications is not None else list(Certification.objects.filter(
                           profile=profile, confirmed_by_user=True).values(
                               'pk', 'title', 'description')))
    prefetched_languages = getattr(profile, '_evidence_languages', None)
    languages = ([{'pk': row.pk, 'language': row.language,
                   'proficiency': row.proficiency} for row in prefetched_languages]
                 if prefetched_languages is not None else list(Language.objects.filter(
                     profile=profile, confirmed_by_user=True).values(
                         'pk', 'language', 'proficiency')))
    return {
        'SKILL': skills,
        'QUALIFICATION': education,
        'EXPERIENCE': experience,
        'CERTIFICATION': certifications,
        'LICENCE': [row for row in certifications if any(
            token in normalize_term(row['title']).split() for token in ('licence', 'license'))],
        'LANGUAGE': languages,
        'ELIGIBILITY': [],
        'OTHER': [],
    }


def profile_has_signal(profile, evidence=None):
    evidence = evidence or confirmed_evidence(profile)
    preference = CareerPreference.objects.filter(profile=profile).first()
    return bool(
        preference and preference.target_roles.strip()
        or any(evidence[kind] for kind in evidence)
    )


def profile_evidence_fingerprint(profile, evidence=None):
    evidence = evidence or confirmed_evidence(profile)
    preference = CareerPreference.objects.filter(profile=profile).values_list(
        'target_roles', flat=True).first() or ''
    return _hash({'evidence': evidence, 'target_roles': preference})


def _text_support(needle, values):
    needle = normalize_term(needle)
    if not needle:
        return None
    for value, label, pk in values:
        normalized = normalize_term(value)
        if normalized == needle or (
            len(needle.split()) >= 2 and needle in normalized
        ):
            return {'id': pk, 'label': label}
    return None


def _criterion_status(criterion, evidence):
    kind = criterion.criterion_type
    dimension = evidence[kind]
    if not dimension:
        return 'UNKNOWN', []
    if kind == 'SKILL':
        support = _text_support(criterion.normalized_value, [
            (row['normalized_name'], row['entered_name'], row['pk']) for row in dimension])
        if not support:
            context = build_taxonomy_context({'skill': [
                criterion.normalized_value,
                *(row['normalized_name'] for row in dimension),
            ]})
            criterion_concepts = context.resolve(criterion.normalized_value, ('skill',))
            for row in dimension:
                evidence_concepts = context.resolve(row['normalized_name'], ('skill',))
                relation = context.narrower_supports_broader(
                    evidence_concepts, criterion_concepts, max_depth=1)
                if relation:
                    return 'RELATED', [{
                        'id': row['pk'], 'label': row['entered_name'],
                        'route': 'trusted_one_hop_broader',
                        'evidence_concept_id': relation[0].id,
                        'criterion_concept_id': relation[1].id,
                    }]
    elif kind == 'QUALIFICATION':
        q = criterion.qualification or {}
        supports = []
        for row in dimension:
            if q.get('nqf_level') is not None and row['nqf_level'] is not None and row['nqf_level'] >= q['nqf_level']:
                supports.append({'id': row['pk'], 'label': f"NQF {row['nqf_level']}"})
                continue
            text = ' '.join(filter(None, (row['qualification'], row['field_of_study'])))
            if _text_support(criterion.normalized_value, [(text, text, row['pk'])]):
                supports.append({'id': row['pk'], 'label': text})
        support = supports[0] if supports else None
    elif kind == 'EXPERIENCE':
        context = (criterion.experience or {}).get('context') or criterion.normalized_value
        matching = []
        for row in dimension:
            text = ' '.join(filter(None, (row['title'], row['description'], row['bullets'])))
            if _text_support(context, [(text, row['title'], row['pk'])]):
                matching.append(row)
        required = (criterion.experience or {}).get('minimum_months')
        total = sum(_months(
            row['start_date'], row['end_date'], row['currently_working']) or 0
            for row in matching)
        support = ({'id': matching[0]['pk'], 'label': matching[0]['title'], 'months': total}
                   if matching and (required is None or total >= required) else None)
    elif kind in {'CERTIFICATION', 'LICENCE'}:
        support = _text_support(criterion.normalized_value, [
            (row['title'], row['title'], row['pk']) for row in dimension])
    elif kind == 'LANGUAGE':
        required_name = (criterion.language or {}).get('name') or criterion.normalized_value
        support = _text_support(required_name, [
            (row['language'], row['language'], row['pk']) for row in dimension])
    else:
        support = None
    return ('FOUND', [support]) if support else ('NOT_FOUND_ON_PROFILE', [])


def criterion_results(profile, revision, evidence=None):
    evidence = evidence or confirmed_evidence(profile)
    rows = []
    criteria = getattr(revision, '_evidence_criteria', None)
    if criteria is None:
        criteria = revision.criteria.select_related('requirement_group').order_by('pk')
    for criterion in criteria:
        status, profile_evidence = _criterion_status(criterion, evidence)
        rows.append({
            'criterion_id': criterion.criterion_id,
            'criterion_type': criterion.criterion_type,
            'raw_value': criterion.raw_value,
            'normalized_value': criterion.normalized_value,
            'requirement_level': criterion.requirement_level,
            'significance': criterion.significance,
            'requirement_group': criterion.requirement_group.group_id if criterion.requirement_group else None,
            'status': status,
            'profile_evidence': profile_evidence,
        })
    return rows


def _requirement_units(rows):
    groups = defaultdict(list)
    for row in rows:
        groups[row['requirement_group'] or f"criterion:{row['criterion_id']}"].append(row)
    units = []
    for key, members in groups.items():
        best = max(members, key=lambda row: STATUS_PRIORITY[row['status']])
        units.append({
            **best,
            'unit_key': key,
            'member_criterion_ids': [row['criterion_id'] for row in members],
            'status': best['status'],
        })
    return units


def _fit_band(units):
    important = [row for row in units if row['significance'] in IMPORTANT]
    required = [row for row in important if row['requirement_level'] == 'REQUIRED']
    preferred = [row for row in important if row['requirement_level'] == 'PREFERRED']
    positive = [row for row in important if row['status'] in POSITIVE]
    comparable_required = [row for row in required if row['status'] != 'UNKNOWN']
    comparable_preferred = [row for row in preferred if row['status'] != 'UNKNOWN']
    required_coverage = (
        100 * sum(row['status'] in POSITIVE for row in comparable_required) / len(comparable_required)
        if comparable_required else Decimal('0'))
    preferred_coverage = (
        100 * sum(row['status'] in POSITIVE for row in comparable_preferred) / len(comparable_preferred)
        if comparable_preferred else Decimal('0'))
    matched_categories = {row['criterion_type'] for row in positive}
    found_categories = {row['criterion_type'] for row in positive if row['status'] == 'FOUND'}
    strength = ('HIGH' if len(positive) >= 2 and len(matched_categories) >= 2
                else 'MEDIUM' if len(positive) >= 2 else 'LOW')
    if strength == 'HIGH' and len(found_categories) >= 2 and required_coverage >= 80:
        band = 'A'
    elif strength in {'HIGH', 'MEDIUM'} and len(matched_categories) >= 2 and found_categories and required_coverage >= 60:
        band = 'B'
    elif len(matched_categories) >= 2 and required_coverage >= 30 and (
            strength in {'HIGH', 'MEDIUM'} or len(important) >= 4):
        band = 'C'
    elif any(row['status'] == 'FOUND' for row in units):
        band = 'D'
    else:
        band = 'F'

    crucial_unknown = any(
        row['requirement_level'] == 'REQUIRED' and row['significance'] == 'CRUCIAL'
        and row['status'] == 'UNKNOWN' for row in units)
    if crucial_unknown and band in {'A', 'B'}:
        band = 'C'
    blockers = [row for row in units if (
        row['requirement_level'] == 'REQUIRED'
        and row['significance'] == 'CRUCIAL'
        and row['status'] == 'NOT_FOUND_ON_PROFILE'
        and row['criterion_type'] in {
            'QUALIFICATION', 'EXPERIENCE', 'CERTIFICATION', 'LICENCE',
            'LANGUAGE', 'ELIGIBILITY',
        })]
    if blockers:
        band = 'E' if band in {'A', 'B', 'C'} else 'F'

    weighted_total = weighted_positive = 0
    for row in units:
        if row['status'] == 'UNKNOWN':
            continue
        criterion_type = row['criterion_type']
        if criterion_type in UNWEIGHTED_PENDING_CALIBRATION:
            continue
        if criterion_type in NON_SCORING_CRITERIA:
            continue
        weight = CURRENT_FIT_WEIGHTS[criterion_type]
        weighted_total += weight
        if row['status'] in POSITIVE:
            weighted_positive += weight
    score = Decimal(str(round(100 * weighted_positive / weighted_total, 2))) if weighted_total else Decimal('0')
    return {
        'band': band,
        'required_coverage': Decimal(str(round(float(required_coverage), 2))),
        'preferred_coverage': Decimal(str(round(float(preferred_coverage), 2))),
        'matched_categories': len(matched_categories),
        'found_categories': len(found_categories),
        'positive_important': len(positive),
        'supporting_found': sum(
            row['status'] == 'FOUND' and row['significance'] == 'SUPPORTING' for row in units),
        'strongest': strength,
        'major_gaps': len(blockers),
        'gate_gaps': len(blockers),
        'foundation_gaps': 0,
        'score': score,
    }


def _profile_relevant(revision, resolution, units):
    occupation_ids = set(resolution.identities.values_list('concept_id', flat=True))
    if not occupation_ids:
        return None
    approved = OccupationDefiningCriterion.objects.filter(
        approved=True, policy_version=PROFILE_RELEVANT_POLICY_VERSION,
        occupation_id__in=occupation_ids)
    registry = {
        (row.criterion_type, row.normalized_value, row.route): row
        for row in approved
    }
    strong = [row for row in units if (
        row['status'] == 'FOUND' and row['requirement_level'] == 'REQUIRED'
        and row['significance'] in IMPORTANT)]
    for row in strong:
        if (row['criterion_type'], row['normalized_value'], 'strong_single') in registry:
            return {'route': 'strong_single', 'criterion_ids': row['member_criterion_ids']}
        if row['criterion_type'] in {'CERTIFICATION', 'LICENCE', 'QUALIFICATION'} and (
                row['criterion_type'], row['normalized_value'], 'credential') in registry:
            return {'route': 'specific_credential', 'criterion_ids': row['member_criterion_ids']}
        if row['criterion_type'] == 'EXPERIENCE' and (
                row['criterion_type'], row['normalized_value'], 'experience') in registry:
            return {'route': 'experience', 'criterion_ids': row['member_criterion_ids']}
    coherent = [row for row in units if (
        row['requirement_level'] == 'REQUIRED' and row['significance'] in IMPORTANT
        and row['status'] in POSITIVE)]
    distinctive = [row for row in coherent if any(
        (row['criterion_type'], row['normalized_value'], route) in registry
        for route in ('strong_single', 'credential', 'experience', 'multi_evidence'))]
    if len(coherent) >= 2 and any(row['status'] == 'FOUND' for row in coherent) and distinctive:
        return {'route': 'coherent_multi_evidence', 'criterion_ids': [
            row['criterion_id'] for row in coherent]}
    return None


def evaluation_dependency_hash(profile, revision, target_resolutions, opportunity_resolution, evidence=None):
    return _hash({
        'clean_revision': [revision.pk, revision.payload_hash, revision.seniority_label],
        'profile': profile_evidence_fingerprint(profile, evidence),
        'targets': [(row.pk, row.dependency_hash) for row in target_resolutions],
        'opportunity_resolution': opportunity_resolution.dependency_hash,
        'taxonomy': taxonomy_fingerprint(),
        'versions': [RELATIONSHIP_POLICY_VERSION, PROFILE_RELEVANT_POLICY_VERSION,
                     MATCHING_POLICY_VERSION, BAND_POLICY_VERSION],
    })


def evaluate_profile_revision(profile, revision):
    if revision.status != 'active' or revision.opportunity.active_clean_revision_id != revision.pk:
        return None
    if revision.source_record.current_raw_id != revision.raw_opportunity_id:
        return None
    evidence = confirmed_evidence(profile)
    preference = CareerPreference.objects.filter(profile=profile).first()
    targets = resolve_preference_targets(preference) if preference else []
    opportunity_resolution = resolve_opportunity_revision(revision)
    category, reasons, target_ids = discovery_relationship(targets, opportunity_resolution)
    rows = criterion_results(profile, revision, evidence)
    units = _requirement_units(rows)
    if category is None:
        relevant = _profile_relevant(revision, opportunity_resolution, units)
        if relevant:
            category = 'PROFILE_RELEVANT'
            reasons = [relevant]
    if category is None:
        return None
    fit = _fit_band(units)
    return {
        'profile': profile,
        'opportunity': revision.opportunity,
        'clean_revision': revision,
        'state': 'CURRENT',
        'dependency_hash': evaluation_dependency_hash(
            profile, revision, targets, opportunity_resolution, evidence),
        'discovery_category': category,
        'discovery_rank': DISCOVERY_RANK[category],
        'band': fit['band'],
        'band_rank': BAND_RANK[fit['band']],
        'required_important_coverage': fit['required_coverage'],
        'preferred_important_coverage': fit['preferred_coverage'],
        'matched_important_category_count': fit['matched_categories'],
        'found_important_category_count': fit['found_categories'],
        'positive_important_count': fit['positive_important'],
        'supporting_found_count': fit['supporting_found'],
        'strongest_important_match': fit['strongest'],
        'major_gap_count': fit['major_gaps'],
        'gate_gap_count': fit['gate_gaps'],
        'foundation_gap_count': fit['foundation_gaps'],
        'professional_score': fit['score'],
        'discovery_reasons': reasons,
        'triggering_target_resolution_ids': target_ids,
        'criterion_results': rows,
    }
