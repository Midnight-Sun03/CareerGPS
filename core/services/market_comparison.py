"""Compare persisted market requirements with confirmed evidence, without a score."""
from core.services.evidence_fit import EvidenceStatus, compare_market_requirement


def compare_profile_to_market(profile, snapshot):
    requirements = snapshot.metrics.get('requirements', {})
    # A bounded dashboard summary. Full counts remain in the persisted snapshot.
    selected = {kind: data.get('items', [])[:20] for kind, data in requirements.items()}
    groups = {'supported': [], 'missing': [], 'unknown': []}
    for kind, items in selected.items():
        for item in items:
            if item['level'] in {'required', 'preferred'}:
                status, evidence, explanation = compare_market_requirement(
                    profile, kind, item['label'])
            else:
                status, evidence, explanation = (
                    EvidenceStatus.UNKNOWN, (),
                    'CareerGPS cannot safely compare this requirement.')
            row = dict(item, kind=kind, status=status.value,
                explanation=explanation, profile_evidence=list(evidence))
            if status in {EvidenceStatus.FOUND, EvidenceStatus.RELATED}:
                groups['supported'].append(row)
            elif status == EvidenceStatus.NOT_FOUND_ON_PROFILE:
                row['explanation'] = 'No supporting evidence found in your CareerGPS profile.'
                groups['missing'].append(row)
            else:
                groups['unknown'].append(row)
    return groups
