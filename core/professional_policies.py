CLEAN_SCHEMA_VERSION = '1.1'
TARGET_RESOLVER_VERSION = 'target-resolver-v1.1'
OPPORTUNITY_RESOLVER_VERSION = 'opportunity-resolver-v1.1'
RELATIONSHIP_POLICY_VERSION = 'relationship-v1'
PROFILE_RELEVANT_POLICY_VERSION = 'profile-relevant-v1'
MATCHING_POLICY_VERSION = 'matching-v1'
BAND_POLICY_VERSION = 'band-v1'

# Occupational-intelligence v2 is built in shadow mode.  These versions own
# only their derived decisions; changing one must not mutate or invalidate the
# imported taxonomy releases themselves.
# Title-gate v2 owns historical ``OpportunityTitleGateDecision`` rows only.
# No new seniority row is written: the senior decision is now made read-only in
# ``core.services.title_gate`` (see ``READ_ONLY_SENIORITY_POLICY_VERSION``).
TITLE_GATE_POLICY_VERSION = 'title-gate-v2'
TARGET_CONTAINER_VERSION = 'target-container-v1'
OFO_RESOLVER_VERSION = 'ofo-resolver-v2'
ESCO_RESOLVER_VERSION = 'esco-resolver-v2'
LABEL_ROUTE_POLICY_VERSION = 'occupation-label-routes-v1'
# v7 keeps everything v6 kept and corrects two boundaries.  A stored finding
# row is now an *established* relationship only: a policy-free same-concept
# label collision is retained and disclosed with its tier instead of being
# persisted and counted as a relationship.  And several retained readings that
# connected nothing are an inconclusive answer, not an ambiguity, so a source
# can no longer withhold a job for having kept more than one candidate.
# Bumping the version makes every stored v6 verdict and finding set stale rather
# than current, so the pages report missing coverage until the occupational
# pipeline rebuilds them.  v6 kept every retained occupation result per source
# and every resulting relationship instead of collapsing a source to one
# uniform verdict.  v5 let
# either taxonomy confirm on its own and kept the other source's disagreement
# as disclosed evidence instead of a veto; that behaviour is retained.
RELATIONSHIP_AGGREGATION_VERSION = 'relationship-aggregation-v7'
CANDIDATE_RELATIONSHIP_POLICY_VERSION = 'candidate-relationship-v3'
FEED_GENERATION_VERSION = 'feed-v2.4'
FEED_ADMISSION_VERSION = 'feed-admission-v5'
FEED_RANKING_VERSION = 'feed-ranking-v2.2'

# A resolved title may return any number of occupation results per taxonomy and
# every one of them stays usable.  "Several results" is therefore never a
# discard reason, never ambiguity, and never a reason to pick a favourite.
RETAINED_RELATIONSHIP_CLASSES = ('EXACT', 'RELATED')
# A pairing that produced no relationship at all is `NOT_ESTABLISHED`: the
# evidence is simply absent.  Only an explicit active UNSUPPORTED policy makes a
# pairing `DENIED`.  These two must stay distinguishable on every page.
INCONCLUSIVE_PAIRING_CLASS = 'NOT_ESTABLISHED'
DENIED_PAIRING_CLASS = 'DENIED'

# Candidate-feed survivor contract. A vacancy enters the personalised feed only on
# trusted occupational admission: EXACT, RELATED or TITLE_MATCH.  UNRELATED is never
# a feed category, only an assessment verdict.  UNRESOLVED is likewise an assessment
# verdict that never admits: missing proof of a relationship is not proof of
# unrelatedness, so it is neither confirmed nor excluded-as-unrelated — it simply
# fails the occupational admission gate.
UNRELATED_CLASS = 'UNRELATED'
UNRELATED_REASON_CODE = 'explicit_unsupported_policy_all_sources'
# Title resemblance is discovery evidence, never ESCO/OFO confirmation, so it
# gets its own class, its own tier, and its own clearly-labelled feed area.
TITLE_MATCH_CLASS = 'TITLE_MATCH'
TITLE_MATCH_TIER = 'TITLE_ONLY'
# ``CONFLICT`` is retained in the tuples below only so stored historical rows
# keep rendering and sorting.  The aggregator never produces it: a taxonomy that
# answers differently from the other is disclosed as ``conflicting_evidence`` on
# the assessment that confirmed, and the stronger verdict stands.
FEED_SURVIVOR_CATEGORIES = (
    'EXACT', 'RELATED', 'CONFLICT', TITLE_MATCH_CLASS)
# Only a confirmed occupational relationship from at least one taxonomy may
# appear in the main personalised feed. CONFLICT and TITLE_MATCH are NOT proved
# unrelated, so they stay discoverable in a clearly separate area and are never
# labelled as confirmed matches.  UNRESOLVED carries no trusted occupational
# evidence at all, so it never reaches either area: it falls through to the
# unassessed/uncertain catalogue surface, which is not personalised matching.
FEED_CONFIRMED_CATEGORIES = ('EXACT', 'RELATED')
FEED_DISCOVERY_CATEGORIES = ('CONFLICT', 'UNRESOLVED', TITLE_MATCH_CLASS)
FEED_DISCOVERY_RANK = {
    'EXACT': 1, 'RELATED': 2, 'CONFLICT': 3, TITLE_MATCH_CLASS: 4, 'UNRESOLVED': 5}
SENIORITY_EXCLUDED_LABEL = 'OBVIOUS_SENIOR'

CURRENT_FIT_WEIGHTS = {
    'QUALIFICATION': 25,
    'EXPERIENCE': 25,
    'SKILL': 25,
    'CERTIFICATION': 10,
    'ELIGIBILITY': 10,
    'LANGUAGE': 5,
}

# These states are deliberately different from a numeric zero weight. LICENCE
# remains matchable and can still be a crucial gate, but it is excluded from
# the provisional scalar until a human-approved weight is calibrated. OTHER is
# intentionally non-scoring by policy.
UNWEIGHTED_PENDING_CALIBRATION = frozenset({'LICENCE'})
NON_SCORING_CRITERIA = frozenset({'OTHER'})

DISCOVERY_RANK = {'TARGET': 1, 'RELATED': 2, 'PROFILE_RELEVANT': 3}
BAND_RANK = {'A': 1, 'B': 2, 'C': 3, 'E': 4, 'D': 5, 'F': 6}
