"""Career intelligence from current Block 1 relationships and active v2 rows."""
from collections import Counter, defaultdict

from core.services.career_intent import profile_careers
from core.services.evidence_fit import candidate_evidence
from core.services.feed_v2 import v2_feed_queryset
from core.taxonomies.normalization import normalize_term


DEMAND_TYPES = {
    'SKILL': 'Skills', 'QUALIFICATION': 'Qualifications',
    'EXPERIENCE': 'Experience', 'CERTIFICATION': 'Certifications and licences',
    'LICENCE': 'Certifications and licences', 'LANGUAGE': 'Languages',
    'ELIGIBILITY': 'Work eligibility', 'OTHER': 'Other',
}


def _candidate_evidence(profile):
    evidence = candidate_evidence(profile)
    categories = [
        {'label': 'Skills', 'items': evidence.skills[:6], 'count': len(evidence.skills)},
        {'label': 'Qualifications',
         'items': tuple(row.display for row in evidence.education[:4]),
         'count': len(evidence.education)},
        {'label': 'Work experience',
         'items': tuple(row.display for row in evidence.work_experiences[:4]),
         'count': len(evidence.work_experiences)},
        {'label': 'Certifications', 'items': evidence.certifications[:4],
         'count': len(evidence.certifications)},
        {'label': 'Languages', 'items': evidence.languages[:4],
         'count': len(evidence.languages)},
    ]
    return [row for row in categories if row['count']]


def _criterion_groups(evaluations):
    groups = {}
    for evaluation in evaluations:
        for criterion in evaluation.criterion_results:
            if criterion['requirement_level'] not in {'REQUIRED', 'PREFERRED'}:
                continue
            category = DEMAND_TYPES.get(criterion['criterion_type'])
            label = ' '.join(str(criterion.get('raw_value') or '').split())
            if not category or not label:
                continue
            key = (criterion['criterion_type'], criterion['requirement_level'],
                   normalize_term(label))
            group = groups.setdefault(key, {
                'label': label, 'category': category,
                'importance': criterion['requirement_level'].casefold(),
                'type': criterion['criterion_type'],
                'by_opportunity': defaultdict(list),
            })
            group['by_opportunity'][evaluation.opportunity_id].append(criterion)

    result = []
    for group in groups.values():
        counts = Counter()
        major_count = 0
        evidence_labels = []
        for criteria in group['by_opportunity'].values():
            states = {row['status'] for row in criteria}
            if states <= {'FOUND', 'RELATED'}:
                state = 'FOUND' if 'FOUND' in states else 'RELATED'
            else:
                state = next(iter(states)) if len(states) == 1 else 'UNKNOWN'
            counts[state] += 1
            if state == 'NOT_FOUND_ON_PROFILE' and any(
                    row['significance'] == 'CRUCIAL' for row in criteria):
                major_count += 1
            if state in {'FOUND', 'RELATED'}:
                for row in criteria:
                    for item in row.get('profile_evidence') or ():
                        label = item.get('label')
                        if label and label not in evidence_labels:
                            evidence_labels.append(label)
        result.append({
            'label': group['label'], 'category': group['category'],
            'importance': group['importance'], 'type': group['type'],
            'count': len(group['by_opportunity']),
            'found_count': counts['FOUND'], 'related_count': counts['RELATED'],
            'supported_count': counts['FOUND'] + counts['RELATED'],
            'missing_count': counts['NOT_FOUND_ON_PROFILE'],
            'unknown_count': counts['UNKNOWN'], 'major_count': major_count,
            'profile_evidence': evidence_labels[:3],
        })
    return sorted(result, key=lambda row: (
        -row['count'], row['category'], normalize_term(row['label'])))


def build_career_brief(profile, targets):
    """Summarize only current active v2 admission and its persisted fit evidence."""
    targets = list(targets)
    target_rows = [
        {'target': target, 'count': 0, 'exact_count': 0, 'related_count': 0}
        for target in targets
    ]
    result = {
        'targets': target_rows, 'candidate_evidence': _candidate_evidence(profile),
        'position_summary': '', 'opportunity_count': 0, 'exact_count': 0,
        'specialisation_count': 0, 'related_count': 0, 'strong_count': 0,
        'current_gap_count': 0, 'locations': [], 'work_modes': [],
        'demand_sections': [], 'strongest_evidence': [],
        'required_gaps': [], 'unknown_gaps': [], 'preferred_gaps': [],
        'related_paths': [], 'priorities': [],
    }
    if not targets:
        result['position_summary'] = (
            'Your saved career direction has no current trusted OFO or ESCO resolution; '
            'occupation-specific analysis remains unavailable.'
            if profile_careers(profile) else
            'Add target roles in Profile → Career Preferences to see occupation-specific analysis.')
        return result

    evaluations = list(v2_feed_queryset(profile))
    result['opportunity_count'] = len(evaluations)
    result['exact_count'] = sum(row.discovery_category == 'EXACT' for row in evaluations)
    result['related_count'] = sum(row.discovery_category == 'RELATED' for row in evaluations)
    result['specialisation_count'] = sum(
        row.relationship_tier == 'EXACT_ALIAS_SPECIALISATION' for row in evaluations)
    result['strong_count'] = sum(row.band in {'A', 'B'} for row in evaluations)
    result['current_gap_count'] = sum(row.major_gap_count > 0 for row in evaluations)

    target_index = {row['target'].pk: row for row in target_rows}
    related = Counter()
    for evaluation in evaluations:
        for reason in evaluation.admission_reasons:
            relationship_class = reason.get('relationship_class')
            if relationship_class not in {'EXACT', 'RELATED'}:
                # Conflicting or unresolved evidence keeps the listing in the
                # feed but never counts as an established relationship.
                continue
            target = target_index.get(reason.get('target_resolution_id'))
            if target is not None:
                target['count'] += 1
                target['exact_count' if relationship_class == 'EXACT'
                       else 'related_count'] += 1
            if relationship_class == 'RELATED':
                related[(reason.get('authored_target') or '',
                         evaluation.clean_revision.cleaned_title)] += 1
    result['related_paths'] = [{
        'label': title, 'kind': 'Related role', 'target_label': target, 'count': count,
    } for (target, title), count in related.most_common(8)]

    if len(evaluations) >= 3:
        locations = Counter()
        modes = Counter()
        for evaluation in evaluations:
            location = evaluation.clean_revision.location or {}
            label = (location.get('city') or location.get('region')
                     or location.get('country') or location.get('raw') or '')
            if label:
                locations[label] += 1
            if evaluation.clean_revision.work_arrangement != 'UNKNOWN':
                modes[evaluation.clean_revision.work_arrangement.replace('_', ' ').title()] += 1
        result['locations'] = [
            {'label': label, 'count': count} for label, count in locations.most_common(3)]
        result['work_modes'] = [
            {'label': label, 'count': count} for label, count in modes.most_common(3)]

    rows = _criterion_groups(evaluations)
    for category in dict.fromkeys(DEMAND_TYPES.values()):
        items = [row for row in rows if row['category'] == category]
        if items:
            result['demand_sections'].append({'label': category, 'items': items[:5]})
    result['strongest_evidence'] = sorted(
        (row for row in rows if row['importance'] == 'required'
         and row['supported_count'] >= 2),
        key=lambda row: (-row['supported_count'], -row['count'], normalize_term(row['label'])),
    )[:5]
    result['required_gaps'] = sorted(
        (row for row in rows if row['importance'] == 'required' and row['missing_count']),
        key=lambda row: (-row['missing_count'], -row['major_count'], -row['count']),
    )[:6]
    result['unknown_gaps'] = sorted(
        (row for row in rows if row['importance'] == 'required' and row['unknown_count']),
        key=lambda row: (-row['unknown_count'], -row['count']),
    )[:5]
    result['preferred_gaps'] = sorted(
        (row for row in rows if row['importance'] == 'preferred' and row['missing_count']),
        key=lambda row: (-row['missing_count'], -row['count']),
    )[:4]
    minimum = max(2, (len(evaluations) + 4) // 5)
    result['priorities'] = sorted(
        (row for row in rows if row['importance'] == 'required'
         and row['count'] >= minimum and row['missing_count'] >= minimum
         and row['supported_count'] == 0 and row['unknown_count'] == 0
         and row['type'] != 'ELIGIBILITY'),
        key=lambda row: (-row['missing_count'], -row['count'], normalize_term(row['label'])),
    )[:3]
    supported = sum(any(
        row['requirement_level'] == 'REQUIRED' and row['status'] in {'FOUND', 'RELATED'}
        for row in evaluation.criterion_results) for evaluation in evaluations)
    if not evaluations:
        result['position_summary'] = (
            'There are currently no listings with an established occupational '
            'relationship to these targets.')
    elif supported:
        result['position_summary'] = (
            f'Your confirmed profile has supporting evidence for at least one required '
            f'criterion in {supported} of {len(evaluations)} current vacancies. '
            'This is not an overall employability score.')
    else:
        result['position_summary'] = (
            'CareerGPS cannot yet verify supporting required evidence for the current '
            'vacancy cohort. Unknown evidence is not treated as missing.')
    return result
