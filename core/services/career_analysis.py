"""Target-by-target employer requirement comparison for Career Intelligence.

The personalised feed answers "which jobs should I look at next". This module
answers a different question: "for this one saved target, what do current
comparable jobs require, and what does my confirmed profile already show?"

It therefore builds its own cohort straight from the current, active,
validated Clean-Job v1.1 catalogue and the independent OFO/ESCO resolvers.
It never reads a feed generation, never ranks or admits anything, and never
writes. An updating feed therefore cannot present itself as an empty market:
the cohort is derived from the catalogue, not from a personal feed.
"""
from __future__ import annotations

from types import SimpleNamespace

from core.models import Opportunity, OpportunityCleanAttempt
from core.services.career_intent import profile_careers
from core.services.evidence_fit import candidate_evidence
from core.services.occupational_resolution import (
    SOURCE_VERSIONS, _active_release,
)
from core.professional_matching import (
    _criterion_status, _requirement_units, confirmed_evidence,
)
from core.professional_policies import (
    TITLE_MATCH_CLASS, TITLE_MATCH_TIER,
)
from core.services.relationship_aggregation import (
    stored_pair_verdicts,
)
from core.services.title_gate import evaluate_revision
from core.taxonomies.normalization import normalize_term

# Only these two aggregated verdicts are trusted enough to be called employer
# demand for a target.  UNRESOLVED and a taxonomy that answered differently both
# remain visible as uncertainty and are deliberately excluded from the
# comparison denominator.  A title-only resemblance is disclosed on its own and
# is never part of the cohort.
COHORT_CLASSES = {'EXACT', 'RELATED'}
UNRESOLVED_CLASS = 'UNRESOLVED'
TITLE_ONLY_CLASS = TITLE_MATCH_CLASS
SUPPORTED_STATUSES = {'FOUND', 'RELATED'}

# A requirement must recur across at least this many distinct comparable jobs
# before it is described as recurring, and before it can be called a gap.
MIN_JOBS_FOR_RECURRING = 3

MAX_LISTED_SKILLS = 8
MAX_LISTED_ITEMS = 4

TYPE_LABELS = {
    'SKILL': 'Skill', 'QUALIFICATION': 'Qualification', 'EXPERIENCE': 'Experience',
    'CERTIFICATION': 'Certification', 'LICENCE': 'Licence', 'LANGUAGE': 'Language',
    'ELIGIBILITY': 'Eligibility', 'OTHER': 'Requirement',
}

UPDATING_NOTE = (
    'Some current job records are still being checked, so the job picture for '
    'this target is incomplete. This is not an empty market.')
NO_JOBS_NOTE = (
    'No current job is linked to this target by a confirmed occupation '
    'relationship, so there is nothing trustworthy to compare against it yet.')
INCONCLUSIVE_NOTE = (
    'CareerGPS could not yet decide whether the current jobs relate to this '
    'target, so it will not claim there are none. The picture is incomplete.')
TITLE_ONLY_NOTE = (
    'No current job was confirmed as related to this target by ESCO or OFO. '
    '{count} current job{s} match the wording of this target by title only, '
    'which is not confirmation, so they are not treated as comparable employer '
    'demand yet. The picture is incomplete.')
UNMAPPED_NOTE = (
    'This wording does not match a single occupation CareerGPS trusts, so it '
    'cannot be compared to employer requirements yet. Try the exact job title '
    'you would search for.')
MULTI_OCCUPATION_NOTE = (
    'This wording matches {count} occupations in ESCO or OFO. Every one of them '
    'is compared against every job, and each confirmed relationship is listed '
    'with the job it belongs to, so no occupation is dropped and none is '
    'preferred over another.')
NO_REQUIREMENTS_NOTE = (
    'These comparable jobs do not carry structured requirements, so there is '
    'nothing to compare yet. CareerGPS does not infer demand from advert text.')
UNCOVERED_DETAIL = (
    '{count} of these current job{s} ha{verb} no stored comparison against '
    'this target yet, so CareerGPS leaves them out rather than guessing.')
UNANALYSED_NOTE = (
    'This target has no stored occupational analysis against the current '
    'taxonomy releases yet, so CareerGPS cannot compare it today. It will be '
    'compared once the analysis has been rebuilt; nothing is guessed here.')


def _display_label(value):
    return ' '.join(str(value or '').split())


# What one taxonomy said about two occupations, in the words the page uses.  A
# pair can carry several of these at once, from one or both taxonomies.
RELATIONSHIP_KIND_LABELS = {
    'EXACT': 'is the same occupation as',
    'RELATED': 'is related to',
}
SOURCE_DISPLAY_NAMES = {'esco': 'ESCO', 'ofo': 'OFO'}

# Retained title-only evidence is never presented as a relationship.  It is
# disclosed with its own tier and this wording, so a possibility can never be
# read as a taxonomy confirmation or counted as one.
TITLE_ONLY_TIER_LABEL = {
    TITLE_MATCH_TIER: 'Title-only evidence, not taxonomy-confirmed',
}
TITLE_ONLY_EVIDENCE_NOTE = (
    'ESCO or OFO kept {count} title-only match{plural} for these jobs. A '
    'title-only match is not a confirmed occupation relationship, so it is '
    'listed separately here and is not counted in the relationship total '
    'above. The full comparison of every retained candidate stays stored.')


def _occupation_name(item, side, fallback=''):
    """One occupation of one side of a relationship, named and coded so two stay two.

    A computed pairing names the job side `vacancy_`, a stored finding names it
    `job_`; both are read here so one display helper serves both paths.

    The concept's own label and code are used, never the label the user happened
    to type.  One word can match two different concepts: "accountant" is both
    ESCO `2411.1.8 financial controller` and `2411.1 accountant`, so reading
    `matched_label` first printed the same word on both sides of all four
    pairings and made them indistinguishable.  The matched label is kept only as
    a last resort, when the concept itself has no name of its own.
    """
    prefix = side
    if not any(key in item for key in (f'{side}_label', f'{side}_matched_label',
                                       f'{side}_code')):
        prefix = 'vacancy' if side == 'job' else side
    label = _display_label(item.get(f'{prefix}_label'))
    code = _display_label(item.get(f'{prefix}_code'))
    if label and code:
        return f'{label} ({code})'
    if label:
        return label
    if code:
        return code
    matched = _display_label(item.get(f'{prefix}_matched_label'))
    return matched or _display_label(fallback)


def _relationship_display(relationships, target_text='', job_text=''):
    """Every confirmed relationship one target and one job established.

    Only established relationships ever reach this function, because a stored
    finding row is one: a retained title-only collision is disclosed from
    `title_only_evidence` instead, so it can never be listed as a relationship.

    One entry per relationship, never one merged sentence: a job connected to a
    target by three ESCO occupations and one OFO occupation is shown with all
    four connections, each naming the taxonomy, the two occupations it linked,
    and the kind of relationship that taxonomy gave.  Two occupations that share
    a label stay two entries.
    """
    rows = []
    for item in sorted(relationships or (),
                       key=lambda row: (row.get('source') or '',
                                        row.get('ordinal') or 0)):
        kind = RELATIONSHIP_KIND_LABELS.get(item.get('relationship_class'))
        if kind is None:
            # Pairings that established nothing are not relationships and are
            # never presented as ones; they stay in the evidence, not the list.
            continue
        rows.append({
            'source': SOURCE_DISPLAY_NAMES.get(
                item.get('source'), (item.get('source') or '').upper()),
            'class': item['relationship_class'],
            'kind': kind,
            'target_occupation': _occupation_name(item, 'target', target_text),
            'job_occupation': _occupation_name(item, 'job', job_text),
            'relationship_type': (item.get('relationship_type') or '')
                                 .replace('_', ' ').casefold(),
            'reason_code': item.get('reason') or item.get('reason_code') or '',
        })
    return rows


def _target_occupation_count(resolutions):
    """How many occupations the taxonomies retained for this target, in total.

    Counted across both sources and across every retained result, because
    several results are several occupation connections rather than a set to be
    narrowed down.  Stored rows written before per-result rows existed still
    report their candidate ids, so they are counted from those too.
    """
    total = 0
    for row in resolutions.values():
        results = getattr(row, 'candidate_results', None)
        if results:
            total += len(results)
            continue
        total += len(getattr(row, 'candidate_concept_ids', None) or ())
    return total


def _target_resolution_state(resolutions):
    """resolved | unmapped, plus how many occupations that resolution holds.

    A target whose wording matches several concepts in one taxonomy is still
    `resolved`.  Several results are several occupation connections: each is
    compared against every job, each confirmed relationship is displayed, and
    the target keeps its place in the market cohort.  It is never downgraded to
    an ambiguous or candidate-set state just for having several results, because
    that would remove a target's jobs from comparison for a reason that no
    evidence supports.

    Only a target with no retained occupation in any source is `unmapped`, and
    only such a target is refused a comparison.
    """
    states = {row.state for row in resolutions.values()}
    if 'RESOLVED' in states or 'AMBIGUOUS' in states:
        return 'resolved'
    return 'unmapped'


def _eligible_revisions():
    """Current active validated Clean-Job v1.1 revisions passing the shared check.

    Uses the same read-only `evaluate_revision` decision, including the
    cleaner's description-based evidence, that the Opportunities feed uses, so
    the two pages can never disagree about a senior role.  Reading this page
    writes no seniority row and reads none.
    """
    revisions = []
    for opportunity in Opportunity.objects.professionally_ready(
            include_obvious_senior=False).select_related(
        'active_clean_revision', 'active_clean_revision__source_record',
    ).order_by('-active_clean_revision__posted_sort_at', 'pk'):
        revision = opportunity.active_clean_revision
        if not evaluate_revision(revision).blocked:
            revisions.append(revision)
    return revisions


def _criterion_rows(revision, evidence, status_cache):
    """`criterion_results` with a per-criterion-content status cache.

    Row shape is identical to core.professional_matching.criterion_results so
    `_requirement_units` collapses OR groups exactly as the feed does. Caching
    by criterion content is safe: status depends on the criterion and on the
    candidate's evidence, never on which revision supplied the criterion.
    """
    rows = []
    criteria = revision.criteria.select_related('requirement_group').order_by('pk')
    for criterion in criteria:
        key = (criterion.criterion_type, criterion.normalized_value,
               repr(criterion.qualification), repr(criterion.experience),
               repr(criterion.language))
        if key not in status_cache:
            status_cache[key] = _criterion_status(criterion, evidence)
        status, profile_evidence = status_cache[key]
        rows.append({
            'criterion_id': criterion.criterion_id,
            'criterion_type': criterion.criterion_type,
            'raw_value': criterion.raw_value,
            'normalized_value': criterion.normalized_value,
            'requirement_level': criterion.requirement_level,
            'significance': criterion.significance,
            'requirement_group': (
                criterion.requirement_group.group_id
                if criterion.requirement_group else None),
            'status': status,
            'profile_evidence': profile_evidence,
        })
    return rows


def _bucket(label, items, limit):
    """A listed group whose heading count always matches what it renders.

    The full count is kept for the heading; when the list is shortened the
    template is told how many are hidden, so a count can never disagree with
    the number of values shown.
    """
    values = [item for item in items if item]
    return {
        'label': label, 'count': len(values), 'items': values[:limit],
        'hidden': max(0, len(values) - limit),
    }


def profile_summary(profile):
    """The confirmed profile the comparison is made against. Always available."""
    evidence = candidate_evidence(profile)
    return {
        'buckets': [
            row for row in (
                _bucket('Skills', evidence.skills, MAX_LISTED_SKILLS),
                _bucket('Qualifications',
                        tuple(item.display for item in evidence.education),
                        MAX_LISTED_ITEMS),
                _bucket('Work experience',
                        tuple(item.display for item in evidence.work_experiences),
                        MAX_LISTED_ITEMS),
                _bucket('Certifications', evidence.certifications, MAX_LISTED_ITEMS),
                _bucket('Languages', evidence.languages, MAX_LISTED_ITEMS),
            ) if row['count']
        ],
        'total_count': (
            len(evidence.skills) + len(evidence.education)
            + len(evidence.work_experiences) + len(evidence.certifications)
            + len(evidence.languages)),
    }


def _attach_title_only(panel, jobs, count):
    """Retained title-only evidence, attached as its own disclosed list.

    These matches are kept and shown, with their tier and the statement that
    they are not taxonomy-confirmed, and they are deliberately absent from the
    confirmed relationship total above.
    """
    panel['title_only_evidence_count'] = count
    panel['title_only_jobs'] = jobs
    panel['title_only_evidence_note'] = (
        TITLE_ONLY_EVIDENCE_NOTE.format(
            count=count, plural='' if count == 1 else 'es') if count else '')
    return panel


def _empty_panel(text, ordinal, resolution, state, note,
                 occupation_count=0):
    return {
        'label': text, 'ordinal': ordinal, 'resolution': resolution,
        'state': state, 'state_note': note, 'jobs': [], 'job_count': 0,
        'analysable_job_count': 0, 'undecided_job_count': 0,
        'title_only_job_count': 0, 'uncovered_job_count': 0,
        # Retained title-only evidence is disclosed on its own and never counted
        # as a confirmed relationship.
        'title_only_evidence_count': 0, 'title_only_jobs': [],
        'title_only_evidence_note': '',
        'required_shown': [],
        'relationship_count': 0, 'multi_relationship_job_count': 0,
        # How many occupations ESCO and OFO retained for this target in total.
        # Greater than one is a fact to disclose, never a reason to hold the
        # target back from comparison.
        'occupation_count': occupation_count,
        'required_not_shown': [], 'required_cannot_confirm': [],
        'preferred': [], 'less_common': [], 'observed': [],
    }


def _group_sort_key(row):
    return (-row['job_count'], row['label'].casefold())


def _stored_source_results(revisions, releases):
    """Read persisted per-title source resolutions for these revisions.

    One query per source returns every retained candidate set, so a page GET
    never re-resolves a title it already has a stored answer for.  A stored row
    counts only when it was produced against the taxonomy release that is
    active right now; a row left over from an older release is treated as
    missing coverage instead of being presented as current.  Nothing is written.
    """
    from core.models import OccupationSourceResolution

    revision_ids = [revision.pk for revision in revisions]
    if not revision_ids:
        return {}
    rows = OccupationSourceResolution.objects.filter(
        opportunity_resolution__revision_id__in=revision_ids,
    ).values(
        'opportunity_resolution__revision__cleaned_title',
        'source', 'state', 'selected_concept_id', 'label_type',
        'matched_label', 'taxonomy_release_id', 'diagnostics',
    )
    active_releases = {
        source: release.pk for source, release in releases.items() if release}
    by_title = {}
    for row in rows:
        source = row['source']
        title = row['opportunity_resolution__revision__cleaned_title']
        if active_releases.get(source) is None:
            continue
        if row['taxonomy_release_id'] != active_releases[source]:
            continue
        existing = by_title.setdefault(title, {})
        if source in existing:
            # Several revisions can share one cleaned title; the resolution of
            # that title is the same for all of them, so keep the first.
            continue
        diagnostics = row['diagnostics'] or {}
        # The stored per-result descriptions are what a page needs: every
        # retained occupation with its own code, label, and label evidence.
        results = [item for item in (diagnostics.get('results') or [])
                   if item.get('concept_id')]
        candidates = ([item['concept_id'] for item in results]
                      or list(diagnostics.get('candidate_concept_ids') or []))
        existing[source] = SimpleNamespace(
            source=source, pk=None, state=row['state'],
            selected_concept_id=row['selected_concept_id'],
            selected_concept=None,
            # The full retained result set, so an all-pairs comparison can run
            # without ever choosing one concept out of several.
            candidate_concept_ids=candidates,
            candidate_results=results,
            label_type=row['label_type'] or '',
            matched_label=row['matched_label'] or '',
            concept_count=len(candidates),
        )
    return {title: values for title, values in by_title.items()
            if all(source in values for source in SOURCE_VERSIONS)}


def _stored_pair_verdicts(profile, revisions):
    """Stored, still-fresh pair verdicts keyed by (ordinal, revision id).

    Career Intelligence serves stored evidence only, so it reads the verdicts
    the occupational pipeline persisted.  The reader itself lives with the
    aggregation that writes it, so a verdict means exactly the same thing on
    the personalised feed and on this page, and neither page ever re-runs a
    comparison on GET.
    """
    return stored_pair_verdicts(profile, revisions)


def _title_only_display(evidence, target_text='', job_text=''):
    """Retained title-only evidence of one pair, in readable form.

    A policy-free same-concept label collision was really compared and is kept,
    but it established nothing.  It is disclosed here as its own evidence, with
    the tier that marks it as a possibility and wording that never reads as a
    taxonomy confirmation.
    """
    rows = []
    for item in evidence or ():
        tier = item.get('evidence_tier') or TITLE_MATCH_TIER
        rows.append({
            'source': SOURCE_DISPLAY_NAMES.get(
                item.get('source'), (item.get('source') or '').upper()),
            'tier': tier,
            'tier_label': TITLE_ONLY_TIER_LABEL.get(tier, 'Title-only evidence'),
            'reason_code': item.get('reason_code') or '',
            'target_occupation': _occupation_name(item, 'target', target_text),
            'job_occupation': _occupation_name(item, 'job', job_text),
        })
    return rows


def _analyse_target(*, text, ordinal, resolutions, revisions,
                    evidence, status_cache, updating, stored=None):
    """Build one target's panel from stored pair verdicts only.

    A GET never runs a resolver or a pair comparison.  `stored` holds the
    assessments the occupational backfill already persisted; a job without a
    fresh stored verdict is counted as uncovered and disclosed, never
    compared live.
    """
    resolution = _target_resolution_state(resolutions)
    occupation_count = _target_occupation_count(resolutions)
    if resolution == 'unmapped':
        return _empty_panel(
            text, ordinal, resolution, 'needs_clarification', UNMAPPED_NOTE,
            occupation_count)

    # A target holding several occupations is compared on all of them.  It is
    # not short-circuited here: each retained occupation takes part in the
    # all-pairs comparison below, and the count is disclosed to the page.
    stored = stored or {}
    cohort = []
    connections = {}
    title_only_jobs = []
    title_only_count = 0
    undecided = 0
    title_only = 0
    uncovered = 0
    for revision in revisions:
        verdict = stored.get((ordinal, revision.pk))
        if verdict is None:
            # No fresh stored assessment for this pair.  The page must not
            # invent one on GET, and it must not claim the pair was compared.
            uncovered += 1
            continue
        # Retained title-only evidence belongs to its own list and to no
        # confirmed relationship count, whether or not this pair was also
        # confirmed by another reading.
        evidence_rows = _title_only_display(
            verdict.get('title_only'), text, revision.cleaned_title)
        if evidence_rows:
            title_only_count += len(evidence_rows)
            title_only_jobs.append({
                'id': revision.pk, 'title': revision.cleaned_title,
                'company': revision.company, 'evidence': evidence_rows,
            })
        if verdict['class'] in COHORT_CLASSES:
            cohort.append(revision)
            # What the taxonomies actually said about this job and this target.
            connections[revision.pk] = _relationship_display(
                verdict.get('relationships'), text, revision.cleaned_title)
        elif verdict['class'] == UNRESOLVED_CLASS:
            undecided += 1
        elif verdict['class'] == TITLE_ONLY_CLASS:
            title_only += 1

    if not cohort:
        # An empty cohort is only 'no market' when the coverage was actually
        # conclusive.  If any job stayed undecided, or only matched by title,
        # the honest statement is that the picture is incomplete.
        #
        # Having several occupations is never a reason to stop early and never a
        # reason to call the target ambiguous.  Such a target is reported as
        # having no confirmed comparable job, and the several-occupation fact is
        # still disclosed, because it explains what was compared.
        if updating:
            state, note = 'updating', UPDATING_NOTE
        elif title_only:
            state, note = 'title_only', TITLE_ONLY_NOTE.format(
                count=title_only, s='' if title_only == 1 else 's')
        elif undecided or uncovered:
            # A resolved target, but some current jobs stayed undecided or have
            # no stored verdict yet. Say the picture is incomplete rather than
            # claiming there is no market.
            state, note = 'no_jobs', INCONCLUSIVE_NOTE
        else:
            state, note = 'no_jobs', NO_JOBS_NOTE
        if uncovered:
            note = f'{note} ' + UNCOVERED_DETAIL.format(
                count=uncovered, s='' if uncovered == 1 else 's',
                verb='s' if uncovered == 1 else 've')
        if occupation_count > 1:
            # Say what was compared, so an empty cohort is never mistaken for a
            # target that CareerGPS could not identify.
            note = f'{note} ' + MULTI_OCCUPATION_NOTE.format(
                count=occupation_count)
        panel = _empty_panel(text, ordinal, resolution, state, note,
                             occupation_count)
        panel['undecided_job_count'] = undecided
        panel['title_only_job_count'] = title_only
        panel['uncovered_job_count'] = uncovered
        _attach_title_only(panel, title_only_jobs, title_only_count)
        return panel

    groups = {}
    analysable = 0
    for revision in cohort:
        units = _requirement_units(_criterion_rows(revision, evidence, status_cache))
        if not units:
            continue
        analysable += 1
        for unit in units:
            label = _display_label(unit['raw_value'])
            if not label:
                continue
            key = (unit['criterion_type'], unit['requirement_level'],
                   normalize_term(label))
            group = groups.get(key)
            if group is None:
                group = groups[key] = {
                    'label': label, 'type': unit['criterion_type'],
                    'type_label': TYPE_LABELS.get(unit['criterion_type'], 'Requirement'),
                    'level': unit['requirement_level'], 'job_ids': set(),
                    'supported': set(), 'unknown': set(), 'evidence': [],
                    'jobs': {},
                }
            group['job_ids'].add(revision.pk)
            group['jobs'][revision.pk] = {
                'id': revision.pk, 'title': revision.cleaned_title,
                'company': revision.company,
                'relationships': connections.get(revision.pk, []),
            }
            if unit['status'] in SUPPORTED_STATUSES:
                group['supported'].add(revision.pk)
            elif unit['status'] == 'UNKNOWN':
                group['unknown'].add(revision.pk)
            for item in unit.get('profile_evidence') or ():
                value = item.get('label')
                if value and value not in group['evidence']:
                    group['evidence'].append(value)

    panel = _empty_panel(
        text, ordinal, resolution,
        'no_requirements' if not analysable else 'compared', '',
        occupation_count)
    panel['jobs'] = [
        {'id': revision.pk, 'title': revision.cleaned_title,
         'company': revision.company,
         'relationships': connections.get(revision.pk, [])}
        for revision in cohort]
    panel['job_count'] = len(cohort)
    panel['analysable_job_count'] = analysable
    panel['title_only_job_count'] = title_only
    panel['undecided_job_count'] = undecided
    panel['uncovered_job_count'] = uncovered
    _attach_title_only(panel, title_only_jobs, title_only_count)
    if uncovered:
        # Some current jobs had no stored verdict at GET time.  The comparison
        # below still stands for the jobs that were compared, but the page must
        # say how many were left out rather than implying full coverage.
        panel['coverage_note'] = UNCOVERED_DETAIL.format(
            count=uncovered, s='' if uncovered == 1 else 's',
            verb='s' if uncovered == 1 else 've')
    # How much the taxonomies actually said: jobs linked through several
    # occupations at once are common, and collapsing them to one label hides it.
    panel['relationship_count'] = sum(
        len(rows) for rows in connections.values())
    panel['multi_relationship_job_count'] = sum(
        1 for rows in connections.values() if len(rows) > 1)
    if not analysable:
        panel['state_note'] = NO_REQUIREMENTS_NOTE
        return panel

    for group in groups.values():
        group['job_count'] = len(group['job_ids'])
        group['total_jobs'] = analysable
        group['supported_count'] = len(group['supported'])
        group['unknown_count'] = len(group['unknown'])
        group['missing_count'] = (
            group['job_count'] - group['supported_count'] - group['unknown_count'])
        group['jobs'] = sorted(group['jobs'].values(), key=lambda row: row['title'])
        group['evidence'] = group['evidence'][:3]
        if group['supported_count']:
            group['state'] = 'shown'
        elif group['unknown_count'] == group['job_count']:
            group['state'] = 'cannot_confirm'
        else:
            group['state'] = 'not_shown'

    required = [row for row in groups.values() if row['level'] == 'REQUIRED']
    recurring = [row for row in required
                 if row['job_count'] >= MIN_JOBS_FOR_RECURRING]
    panel['required_shown'] = sorted(
        (row for row in recurring if row['state'] == 'shown'), key=_group_sort_key)
    panel['required_not_shown'] = sorted(
        (row for row in recurring if row['state'] == 'not_shown'), key=_group_sort_key)
    panel['required_cannot_confirm'] = sorted(
        (row for row in recurring if row['state'] == 'cannot_confirm'),
        key=_group_sort_key)
    panel['preferred'] = sorted(
        (row for row in groups.values() if row['level'] == 'PREFERRED'),
        key=_group_sort_key)
    panel['less_common'] = sorted(
        (row for row in required if row['job_count'] < MIN_JOBS_FOR_RECURRING),
        key=_group_sort_key)
    panel['observed'] = sorted(groups.values(), key=_group_sort_key)

    if analysable < MIN_JOBS_FOR_RECURRING:
        panel['state'] = 'insufficient_coverage'
        panel['state_note'] = (
            f'Only {analysable} of these {len(cohort)} comparable current '
            f'job{"s" if len(cohort) != 1 else ""} carried structured '
            f'requirements. CareerGPS needs at least {MIN_JOBS_FOR_RECURRING} '
            'before it treats a requirement as recurring, so no recurring-gap '
            'conclusion is drawn here.')
    return panel


def _stored_target_results(profile, authored, releases):
    """Stored per-source target resolutions keyed by authored target text.

    The occupational backfill already resolved every authored target against
    the active OFO and ESCO releases, so the page reads those rows instead of
    resolving the wording again on GET.  A row counts only when it belongs to
    the taxonomy release active right now and to the current resolver version;
    anything older is treated as missing coverage and disclosed, never
    presented as a current answer.  Texts without fresh stored rows are absent
    from the returned dict, so `not stored` stays distinguishable from `stored
    and unmapped`.
    """
    from core.models import CareerTargetResolution, OccupationSourceResolution

    containers = list(CareerTargetResolution.objects.filter(
        profile=profile).only('pk', 'ordinal', 'authored_text'))
    if not containers:
        return {}
    active_releases = {
        source: (release.pk if release else None)
        for source, release in releases.items()}
    rows = {}
    for row in OccupationSourceResolution.objects.filter(
            target_resolution_id__in=[container.pk for container in containers],
    ).only('target_resolution_id', 'source', 'state', 'resolver_version',
           'taxonomy_release_id', 'selected_concept_id', 'diagnostics',
           'matched_label', 'label_type'):
        if row.resolver_version != SOURCE_VERSIONS.get(row.source):
            continue
        if row.taxonomy_release_id != active_releases.get(row.source):
            continue
        rows.setdefault(row.target_resolution_id, {})[row.source] = row

    results = {}
    for container in containers:
        source_rows = rows.get(container.pk) or {}
        if len(source_rows) < len(SOURCE_VERSIONS):
            # Partial or absent stored coverage: this target has not been
            # analysed against the current releases, so it is not answered here.
            continue
        values = {}
        for source, row in source_rows.items():
            diagnostics = row.diagnostics or {}
            results_payload = diagnostics.get('results') or []
            candidate_ids = diagnostics.get('candidate_concept_ids') or [
                item.get('concept_id') for item in results_payload
                if item.get('concept_id')]
            # No favourite is chosen: `selected` follows the single-result rule
            # the resolver itself used.
            selected = candidate_ids[0] if len(candidate_ids) == 1 else None
            values[source] = SimpleNamespace(
                source=source, pk=container.pk, state=row.state,
                selected_concept_id=row.selected_concept_id or selected,
                selected_concept=None,
                candidate_concept_ids=candidate_ids,
                candidate_results=results_payload,
                label_type=row.label_type or '',
                matched_label=row.matched_label or '',
                concept_count=len(candidate_ids),
            )
        results[container.authored_text] = values
    return results



def build_career_analysis(profile):
    """Compare the confirmed profile with current employer demand, per target.

    Stored evidence only: every resolution and pair verdict shown here was
    written by the occupational backfill.  A GET runs no resolver and no pair
    comparison; missing, stale or still-rebuilding coverage is disclosed
    through the analysis coverage fields instead of being computed live.
    """
    authored = profile_careers(profile)
    analysis = {
        'authored_targets': [], 'selected': None, 'selected_ordinal': 0,
        'evidence': profile_summary(profile), 'has_targets': bool(authored),
        'min_jobs': MIN_JOBS_FOR_RECURRING,
    }
    if not authored:
        return analysis

    releases = {source: _active_release(source) for source in SOURCE_VERSIONS}
    revisions = _eligible_revisions()

    # Stored per-title source resolutions written by the backfill.  A title
    # with no fresh stored row counts as unresolved coverage: the page does not
    # resolve it live and does not pretend it was checked.
    stored_vacancies = _stored_source_results(revisions, releases)
    unresolved_titles = sorted({
        revision.cleaned_title for revision in revisions
        if revision.cleaned_title not in stored_vacancies
    })
    stored_targets = _stored_target_results(profile, authored, releases)
    analysis['stored_resolution_titles'] = len(stored_vacancies)
    analysis['unresolved_title_count'] = len(unresolved_titles)
    analysis['unanalysed_target_count'] = sum(
        1 for text in authored if text not in stored_targets)

    # "Updating" is reserved for the analysis inputs themselves: a clean result
    # that has been submitted but not yet validated/activated. A pending feed
    # task is deliberately ignored, because this page never reads the feed and
    # must never present an out-of-date feed as an empty market.
    updating = OpportunityCleanAttempt.objects.filter(status='received').exists()
    evidence = confirmed_evidence(profile)
    status_cache = {}
    stored_pairs = _stored_pair_verdicts(profile, revisions)
    analysis['stored_pair_count'] = len(stored_pairs)
    analysis['eligible_job_count'] = len(revisions)
    # Coverage disclosure: how much of the full target x job matrix actually
    # has a stored, still-fresh verdict behind it.  The gap is shown, never
    # filled in during the request.
    analysis['expected_pair_count'] = len(authored) * len(revisions)
    analysis['missing_pair_count'] = max(
        0, analysis['expected_pair_count'] - len(stored_pairs))
    for ordinal, text in enumerate(authored):
        if text not in stored_targets:
            analysis['authored_targets'].append(_empty_panel(
                text, ordinal, 'unanalysed', 'needs_clarification',
                UNANALYSED_NOTE))
            continue
        analysis['authored_targets'].append(_analyse_target(
            text=text, ordinal=ordinal,
            resolutions=stored_targets[text],
            revisions=revisions,
            evidence=evidence, status_cache=status_cache,
            updating=updating,
            stored={key: value for key, value in stored_pairs.items()
                    if key[0] == ordinal}))

    analysis['selected'] = analysis['authored_targets'][0]
    return analysis
