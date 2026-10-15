"""Consent-gated supply evidence, separate from occupational relevance."""
from collections import Counter
from django.core.exceptions import ValidationError
from django.db.models import Exists, OuterRef
from core.services.evidence_fit import (
    candidate_evidence, compare_profile_to_revision, evidence_schema,
)
from .discovery import eligible_profiles, published_vacancy, profile_batch, BATCH_SIZE, STATUS_LABELS
from .models import SavedCandidate, TalentPoolMember

CATEGORY = {'eligibility': 'eligibility', 'skill': 'skills',
    'qualification': 'qualification', 'experience': 'experience',
    'certification': 'certifications', 'licence': 'certifications',
    'language': 'languages', 'other': 'eligibility'}
ORDER = ['qualification', 'experience', 'skills', 'certifications', 'languages', 'eligibility']


def private_count(value):
    # Bucket every breakdown count, including zero, to reduce subtraction attacks.
    return '<3' if value < 3 else f'{value // 3 * 3}-{value // 3 * 3 + 2}'


def requirement_schema(vacancy):
    revision = vacancy.opportunity.active_clean_revision
    criteria = sorted(evidence_schema(revision), key=lambda c: (
        ORDER.index(CATEGORY[c.evidence_type.value]), c.key))
    return revision, criteria


def supply(member, vacancy, selected=()):
    vacancy = published_vacancy(member, vacancy.pk)
    revision, schema = requirement_schema(vacancy)
    required = [i for i,c in enumerate(schema) if c.importance.value == 'required']
    selected = set(str(x) for x in selected)
    if not selected.issubset({str(i) for i in required}):
        raise ValidationError('Select only required criteria from this vacancy.')
    combinations = [('All required criteria', required)] if required else []
    if selected:
        combinations.append(('Selected required combination', [i for i in required if str(i) in selected]))
    for category in ORDER:
        ids = [i for i in required if CATEGORY[schema[i].evidence_type.value] == category]
        if ids:
            combinations.append((category.capitalize(), ids))
    counters = [Counter() for _ in schema]
    cumulative = [0] * len(required)
    combination_counts = [Counter() for _ in combinations]
    distributions = {name: Counter() for name in ['Skills', 'Qualifications', 'Experience', 'Certifications', 'Location', 'Derived career occupations']}
    coverage = Counter()
    relations = Counter()
    rows = eligible_profiles(member)
    saved = SavedCandidate.objects.filter(organization=member.organization, candidate_id=OuterRef('pk'), active=True,
        candidate__employer_visibility__allow_talent_pool_saving=True)
    pooled = TalentPoolMember.objects.filter(pool__organization=member.organization, pool__active=True,
        candidate_id=OuterRef('pk'), active=True, candidate__employer_visibility__allow_talent_pool_saving=True)
    rows = rows.annotate(_supply_saved=Exists(saved), _supply_pooled=Exists(pooled))
    total = 0
    last = 0
    while True:
        profiles = list(profile_batch(rows.filter(pk__gt=last).order_by('pk')[:BATCH_SIZE]))
        if not profiles:
            break
        facts = [candidate_evidence(p) for p in profiles]
        for profile, evidence in zip(profiles, facts):
            total += 1
            result = compare_profile_to_revision(profile, revision)
            statuses = {c.key: c.status.value for c in result.criteria}
            states = [statuses.get(c.key, 'UNKNOWN') for c in schema]
            for i, state in enumerate(states):
                counters[i][state] += 1
            passing = True
            for step, i in enumerate(required):
                passing = passing and states[i] == 'FOUND'
                cumulative[step] += passing
            for counts, (_, ids) in zip(combination_counts, combinations):
                counts['direct'] += all(states[i] == 'FOUND' for i in ids)
                counts['related'] += all(states[i] in {'FOUND', 'RELATED'} for i in ids) and any(states[i] == 'RELATED' for i in ids)
                counts['comparable'] += all(states[i] != 'UNKNOWN' for i in ids)
            months = evidence.experience_months
            band = None if months is None else 'Under 1 year' if months < 12 else '1-2 years' if months < 36 else '3-5 years' if months < 72 else '6+ years'
            values = {'Skills': evidence.skills, 'Qualifications': [e.qualification for e in evidence.education],
                'Experience': [band] if band else [], 'Certifications': evidence.certifications,
                'Location': [profile.employer_visibility.discovery_location],
                'Derived career occupations': [
                    row.authored_text for row in profile._resolved_target_resolutions]}
            for name, items in values.items():
                labels = {str(x).strip().casefold() for x in items if x and str(x).strip()}
                coverage[name] += bool(labels)
                distributions[name].update(labels)
            relations['Prior applicants'] += profile._has_prior
            relations['Saved candidates'] += profile._supply_saved
            relations['Talent-pool candidates'] += profile._supply_pooled
        last = profiles[-1].pk
    required_rows, preferred_rows = [], []
    previous = total
    impact = dict(zip(required, cumulative))
    for i, criterion in enumerate(schema):
        counts = counters[i]
        row = {'choice': str(i), 'label': criterion.criterion, 'category': CATEGORY[criterion.evidence_type.value],
            'selected': str(i) in selected, 'importance': criterion.importance.value,
            'counts': [(label, private_count(counts[state])) for state,label in STATUS_LABELS.items()],
            'comparable': private_count(total-counts['UNKNOWN'])}
        if i in impact:
            row.update(before=private_count(previous), after=private_count(impact[i]))
            previous = impact[i]
            required_rows.append(row)
        else:
            preferred_rows.append(row)
    distribution_rows = []
    for name, values in distributions.items():
        # Rare labels are withheld too: a unique career or qualification is identifying.
        visible = [(label, private_count(n)) for label,n in sorted(values.items(), key=lambda item: (-item[1], item[0])) if n >= 3]
        distribution_rows.append({'label': name, 'coverage': private_count(coverage[name]),
            'unknown': private_count(total-coverage[name]), 'items': visible[:30],
            'limited': len(visible) > 30, 'suppressed': any(n < 3 for n in values.values())})
    return {'total': total, 'required': required_rows, 'preferred': preferred_rows,
        'combinations': [{'label': label, **{key: private_count(counts[key]) for key in ['direct','related','comparable']}}
            for (label,_),counts in zip(combinations, combination_counts)],
        'distributions': distribution_rows, 'relations': [(label,private_count(n)) for label,n in relations.items()]}


def market_context(vacancy):
    """Legacy market snapshots are not authoritative in the clean architecture."""
    del vacancy
    return None
