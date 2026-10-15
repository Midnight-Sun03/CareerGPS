"""Shared, deterministic senior-role gate for the candidate-facing pages.

One decision is produced per clean revision and consumed by BOTH the
Opportunities feed and Career Intelligence, so the two pages can never
disagree about whether a role is senior.

Two independent signals are combined and the signal that caused an
exclusion is always recorded:

1. The **title construction** (this module).  Titles are read for role
   meaning, not for the mere presence of a word.  ``lead generation`` is an
   activity, ``management accountant`` is a domain, ``account executive``
   is a client-facing sales role, and ``non-senior`` states the absence of
   senior authority: none of those are senior.
2. The **cleaner's description-based seniority evidence** on the clean
   revision.  The cleaner reads the whole posting, so it can see genuine
   senior authority that no title can express.  ``UNCERTAIN`` on its own
   is never an exclusion, and an ambiguous occupational concept is never
   an input to this decision at all.

A taxonomy concept such as ESCO ``financial controller`` can never make a
job senior here; this module reads only the cleaned title and the cleaner's
own recorded evidence.

Nothing in this module is persisted.  The decision is made read-only every
time it is needed, immediately before an opportunity may be admitted to a
feed generation or selected into a Career Intelligence cohort, and it is
re-checked when stored admissions are displayed.  Historical
``OpportunityTitleGateDecision`` rows may remain in the database; they are
never written, read, or depended on by this module or its callers.
"""
from __future__ import annotations

from dataclasses import dataclass
import re
import unicodedata

from core.models import OpportunityCleanRevision


@dataclass(frozen=True)
class TitleGateResult:
    decision: str
    reason_code: str
    matched_construction: str = ''
    exception_applied: str = ''

    @property
    def blocked(self) -> bool:
        return self.decision == 'BLOCK'


# Multi-word constructions that are senior in every context.
_BLOCKED_PHRASES = (
    ('vice president', 'VICE_PRESIDENT'),
    ('general manager', 'GENERAL_MANAGER'),
    ('executive director', 'EXECUTIVE_DIRECTOR'),
    ('executive head', 'EXECUTIVE_HEAD'),
    ('chief executive', 'CHIEF_EXECUTIVE'),
    ('managing director', 'MANAGING_DIRECTOR'),
    ('deputy director', 'DEPUTY_DIRECTOR'),
    ('associate director', 'ASSOCIATE_DIRECTOR'),
)

# Unambiguously senior regardless of position.
_ALWAYS_AUTHORITY = {
    'senior': 'SENIOR',
    'sr': 'SENIOR_ABBREVIATION',
    'principal': 'PRINCIPAL',
    'director': 'DIRECTOR',
    'directors': 'DIRECTOR',
    'chief': 'CHIEF',
    'vp': 'VICE_PRESIDENT_ABBREVIATION',
    'controller': 'TOP_MANAGEMENT_CONTROLLER',
    'supervisor': 'SUPERVISOR',
    'managing': 'MANAGING',
    'overseer': 'SUPERVISOR',
}

# `lead` / `leader` mark a leadership role EXCEPT as part of the activity
# phrase "lead generation", which names a prospecting/marketing function.
_LEAD_TOKENS = {'lead', 'leader'}
_LEAD_ACTIVITY_NEXT = {'generation'}

# `head` is senior as a leadership head noun ("Head of Finance", "Head,
# Analytics").
_HEAD_TOKEN = 'head'

# `executive` is senior only as a role head.  "Account Executive" and
# "Executive Assistant" are not; "Executive: Sales" is.
_EXECUTIVE_TOKEN = 'executive'
_EXECUTIVE_NON_SENIOR_NEXT = {'assistant'}

_MANAGEMENT_TOKEN = 'management'
_MANAGER_TOKEN = 'manager'
_ASSISTANT_TOKEN = 'assistant'

# A trainee route into a management role ("Trainee Manager Stores",
# "Trainee Finance Manager") is an entry path into that role, not established
# senior authority: the trainee modifier governs the management noun.  The
# exception is decided on the construction, never on the isolated word.
_TRAINEE_TOKEN = 'trainee'
_MANAGEMENT_NOUNS = frozenset({_MANAGER_TOKEN, _MANAGEMENT_TOKEN})

_TITLE_NOISE = frozenset({
    'a', 'an', 'the', 'and', 'of', 'for', 'to', 'in', 'at', 'on',
    'with', 'new', 'we', 'are', 'role', 'job', 'position', 'vacancy',
})

# `non` / `not` immediately before an authority word states the ABSENCE of that
# authority: ``Non-Senior Warehouse Assistant`` and ``Non-Manager Coordinator``
# claim no senior authority at all.  The negation is read on the construction
# and only ever governs the one word it precedes, so every other authority word
# in the title still blocks on its own.
_NEGATION_TOKENS = frozenset({'non', 'not'})

# Read-only seniority policy version.  The decision is computed in code and
# never persisted as an admission decision; only the computed reason travels
# with the caller.  Historical ``OpportunityTitleGateDecision`` rows (v1/v2)
# may remain in the database unused.
READ_ONLY_SENIORITY_POLICY_VERSION = 'read-only-seniority-v1'


def _tokens(title: object) -> tuple[str, ...]:
    value = unicodedata.normalize('NFKC', str(title or '')).casefold()
    return tuple(re.findall(r'[a-z0-9]+', value))


def _is_negated(tokens: tuple[str, ...], index: int) -> bool:
    """True when the token at `index` is governed by a preceding negation."""
    return index > 0 and tokens[index - 1] in _NEGATION_TOKENS


def _lead_is_activity(tokens: tuple[str, ...], index: int) -> bool:
    """`lead generation` names an activity; a bare `lead` names a role."""
    nxt = tokens[index + 1] if index + 1 < len(tokens) else None
    return nxt in _LEAD_ACTIVITY_NEXT


def _is_authority_word(token: str) -> bool:
    return (
        token in _ALWAYS_AUTHORITY
        or token in _LEAD_TOKENS
        or token == _HEAD_TOKEN
        or token == _EXECUTIVE_TOKEN
    )


def _manager_positions_are_assistants(tokens: tuple[str, ...],
                                      positions: tuple[int, ...] | list[int]) -> bool:
    """True when every `manager` is governed by a preceding `assistant`.

    `positions` holds the `manager` tokens that carry senior meaning: a manager
    word negated by `non` / `not` is not in it.  The assistant may sit anywhere
    to the left as long as nothing between it and `manager` is itself an
    authority word, so ``Assistant Finance Manager`` qualifies while ``Senior
    Assistant Manager`` is still caught by its own ``senior`` token before this
    function is consulted.
    """
    for index in positions:
        governed = False
        for back in range(index - 1, -1, -1):
            left = tokens[back]
            if _is_authority_word(left) and not _is_negated(tokens, back):
                break
            if left == _ASSISTANT_TOKEN:
                governed = True
                break
        if not governed:
            return False
    return True


def _is_trainee_management_path(tokens: tuple[str, ...]) -> bool:
    """True when a `trainee` modifier governs a following management noun.

    The management noun must follow the `trainee` token, so the trainee
    modifier reads as qualifying the role rather than the role qualifying the
    trainee (``Trainee Manager Stores``, ``Trainee Finance Manager``).  An
    authority word between them has already returned above, so nothing senior
    can slip through this exception.
    """
    for index, token in enumerate(tokens):
        if token != _TRAINEE_TOKEN:
            continue
        if any(noun in _MANAGEMENT_NOUNS for noun in tokens[index + 1:]):
            return True
    return False


def _title_construction(title: object) -> TitleGateResult:
    """Evaluate role meaning in the cleaned title alone."""
    tokens = _tokens(title)
    normalized = ' '.join(tokens)

    for phrase, reason in _BLOCKED_PHRASES:
        phrase_tokens = phrase.split()
        size = len(phrase_tokens)
        if any(list(tokens[index:index + size]) == phrase_tokens
               for index in range(max(0, len(tokens) - size + 1))):
            return TitleGateResult('BLOCK', reason, phrase)

    for index, token in enumerate(tokens):
        if _is_negated(tokens, index):
            # A negated authority word states the absence of that authority, so
            # it is not a senior claim.  Any other authority word in the same
            # title is still evaluated and still blocks.
            continue
        if token in _ALWAYS_AUTHORITY:
            return TitleGateResult('BLOCK', _ALWAYS_AUTHORITY[token], token)
        if token in _LEAD_TOKENS and not _lead_is_activity(tokens, index):
            return TitleGateResult('BLOCK', 'LEAD', token)
        if token == _HEAD_TOKEN and (
                index == 0 or tokens[index + 1:index + 2] == ('of',)):
            return TitleGateResult('BLOCK', 'HEAD', 'head of')

    # A trainee path toward management is an entry route into that role, so
    # the trainee modifier governs the management noun.  This is judged
    # before the bare-manager rule so the construction is read as a whole
    # rather than as the isolated word "manager".  An explicit authority word
    # (senior, head, lead, director, chief, ...) is still caught above and
    # still blocks, so "Senior Trainee Manager" is unaffected.
    if _is_trainee_management_path(tokens):
        return TitleGateResult(
            'ALLOW', 'NO_BLOCKED_CONSTRUCTION', normalized,
            'trainee_management_path',
        )

    manager_positions = [
        index for index, token in enumerate(tokens)
        if token == _MANAGER_TOKEN and not _is_negated(tokens, index)]
    if manager_positions:
        if _manager_positions_are_assistants(tokens, manager_positions):
            return TitleGateResult(
                'ALLOW', 'ASSISTANT_MANAGER_EXCEPTION', 'assistant manager',
                'assistant_manager',
            )
        return TitleGateResult('BLOCK', 'MANAGER', 'manager')

    for index, token in enumerate(tokens):
        if token != _EXECUTIVE_TOKEN:
            continue
        if _is_negated(tokens, index):
            continue
        nxt = tokens[index + 1] if index + 1 < len(tokens) else None
        if nxt in _EXECUTIVE_NON_SENIOR_NEXT:
            continue
        if index == 0:
            return TitleGateResult('BLOCK', 'EXECUTIVE_LEADERSHIP', 'executive')
        return TitleGateResult(
            'ALLOW', 'NON_AUTHORITY_EXECUTIVE', 'executive',
            'executive_without_senior_construction',
        )

    meaningful = [token for token in tokens if token not in _TITLE_NOISE]
    if meaningful == [_MANAGEMENT_TOKEN]:
        return TitleGateResult('BLOCK', 'TOP_MANAGEMENT', 'management')

    return TitleGateResult('ALLOW', 'NO_BLOCKED_CONSTRUCTION', normalized)


def _evidence_texts(seniority_evidence) -> list[str]:
    if not seniority_evidence:
        return []
    texts = []
    for row in seniority_evidence:
        value = row.get('text') if isinstance(row, dict) else row
        if value:
            texts.append(str(value))
    return texts


def _is_title_echo(text: str, cleaned_title: str) -> bool:
    """True when the evidence is the title repeated rather than prose.

    The cleaner sometimes cites the title itself as its evidence.  That
    shows it saw the *word* "manager" and nothing more, so it cannot
    establish senior responsibility beyond the title rule.
    """
    haystack = ' '.join(_tokens(cleaned_title))
    needle = ' '.join(_tokens(text))
    if not needle:
        return True
    return needle in haystack or haystack in needle


def _cleaner_evidence_establishes_seniority(
        cleaned_title, seniority_reason, seniority_evidence) -> bool:
    """Does the cleaner cite prose that goes beyond the title words?"""
    for text in _evidence_texts(seniority_evidence):
        if not _is_title_echo(text, cleaned_title):
            return True
    reason = str(seniority_reason or '')
    if reason and not _is_title_echo(reason, cleaned_title):
        # A generic canned reason that merely lists the words it looked for
        # is not independent evidence.
        lowered = reason.casefold()
        return 'assigns senior' not in lowered \
            and 'senior, head, lead or executive' not in lowered
    return False


def evaluate_title(title: object, *, seniority_label: str = '',
                   seniority_reason: str = '',
                   seniority_evidence=None) -> TitleGateResult:
    """Return the one read-only senior decision shared by both pages.

    The decision is computed in code and never persisted as an admission
    decision.  Callers apply ``evaluate_title(...).blocked`` (or
    ``decision == 'ALLOW'``) before occupational assessment/admission or
    before cohort selection, and keep only the computed ``reason_code``
    for display.  Historical ``OpportunityTitleGateDecision`` rows remain
    unused; ``evaluate_revision`` and ``exclude_senior`` below are what both
    pages call.

    `seniority_label` is the cleaner's own recorded judgement over the
    whole posting, stored as part of the clean record -- it is existing
    clean data, not a newly persisted admission decision.
    `OBVIOUS_SENIOR` excludes on its own, which preserves
    genuine description-driven exclusions such as the inspected senior
    `Accountant` listing.  The one exception is an assistant-manager title
    whose only support is the cleaner echoing the title back: there the
    cleaner saw only the word "manager" and does not override the
    exception.  `UNCERTAIN` never excludes on its own.
    """
    result = _title_construction(title)

    if seniority_label != 'OBVIOUS_SENIOR':
        return result

    if result.exception_applied == 'assistant_manager' and not \
            _cleaner_evidence_establishes_seniority(
                title, seniority_reason, seniority_evidence):
        return result
    return TitleGateResult(
        'BLOCK', 'CLEANER_OBVIOUS_SENIOR', result.matched_construction,
        f'cleaner:{result.reason_code}',
    )


def evaluate_revision(revision) -> TitleGateResult:
    """The one read-only senior decision for a clean revision.

    Both pages call this immediately before a job may be admitted to a feed
    generation or selected into a Career Intelligence cohort.  Nothing is
    written and no stored decision is consulted, so a changed policy takes
    effect on the next read without a backfill.
    """
    return evaluate_title(
        getattr(revision, 'cleaned_title', ''),
        seniority_label=getattr(revision, 'seniority_label', '') or '',
        seniority_reason=getattr(revision, 'seniority_reason', '') or '',
        seniority_evidence=getattr(revision, 'seniority_evidence', None),
    )


def seniority_exclusions(revision_ids) -> dict[int, TitleGateResult]:
    """Computed exclusion reason per excluded revision, for these revisions.

    Only excluded revisions are returned, so the caller can keep the reason
    that caused the exclusion for honest disclosure without storing anything.
    """
    ids = [int(value) for value in revision_ids]
    if not ids:
        return {}
    excluded = {}
    rows = OpportunityCleanRevision.objects.filter(pk__in=ids).only(
        'pk', 'cleaned_title', 'seniority_label', 'seniority_reason',
        'seniority_evidence').order_by('pk')
    for revision in rows:
        result = evaluate_revision(revision)
        if result.blocked:
            excluded[revision.pk] = result
    return excluded


def exclude_senior(opportunities):
    """Filter a professional-ready Opportunity queryset to non-senior rows.

    The same read-only `evaluate_revision` decision is applied, so an
    opportunity can never appear on one page and be withheld from the other.
    """
    revision_ids = list(
        opportunities.values_list('active_clean_revision_id', flat=True))
    excluded = seniority_exclusions(revision_ids)
    if not excluded:
        return opportunities
    return opportunities.exclude(active_clean_revision_id__in=list(excluded))
