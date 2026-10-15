# Block 3 dependency audit

The live occupational authority is the Block 1 independent OFO/ESCO resolution
plus conservative relationship aggregation. Candidate admission reads only an
ACTIVE v2 generation with `EXACT` or approved `RELATED` assessments.

| Legacy area | Previous live caller | Block 3 replacement | Status |
| --- | --- | --- | --- |
| `core.matching.build_user_matching_profile`, `build_opportunity_matching_input`, `employer_evidence_match` | Employer talent discovery | `core.services.evidence_fit` compares confirmed evidence with the active Clean-Job revision; target alignment is a separate active v2 assessment | No live caller; retained for historical tests/compatibility |
| Same legacy matcher functions | Employer applicant comparison | Pinned application Clean-Job revision plus `compare_profile_to_revision` | No live caller |
| Same legacy matcher functions and taxonomy run context | Employer supply intelligence | Clean-Job `evidence_schema` plus deterministic evidence comparison | No live caller |
| Legacy matching internals | Market comparison | `compare_market_requirement` from the reusable evidence-fit service | No live caller |
| `evaluate_personalized_feed` discovery provenance, assignment candidates, direct-broader paths | Career brief | Active `ProfileFeedEvaluation`, its Block 1 relationship assessment/admission reasons, and persisted criterion results | Removed from live career intelligence |
| `ProfileOpportunityEvaluation` `TARGET`/`RELATED`/`PROFILE_RELEVANT` counts | Career-intelligence view | `build_career_brief` over the active v2 generation | Removed from live view |
| `core.matching.calculate_completion` / `get_progress_items` | Candidate profile/dashboard views | Direct `core.profile_projection.get_profile_progress` adapter | Removed from live matcher dependency |
| `get_matched_opportunities` and legacy occupational ranking | Historical tests and compatibility code | `professional_feed_queryset` with explicit `CAREERGPS_FEED_READ_VERSION=v2` | No live route; retained temporarily for audit/rollback tests |
| `core.services.professional_evaluations` legacy materializer | Signals, Clean-Job activation, legacy worker | Feed-v2 queue hooks only | Legacy writes retired; explicit reader retained |
| `core.services.match_presentation` and `feed_explanations` | No production caller | Current v2 card construction in `core.views` | Test-only presentation compatibility retained |
| `core.services.career_assignments` | No production caller | Block 1 vacancy resolution and relationship assessment | Retired compatibility stub retained |

`core.professional_matching` still contains historical materializer functions so
the explicit legacy reader remains reversible, but production signals and the
default worker cannot write legacy evaluations. Its deterministic criterion/fit
primitives remain reused by v2 and by `core.services.evidence_fit`; those
primitives do not admit occupations.

The remaining rollback reader is safe to delete only after production has run a
healthy persistent v2 worker through the agreed rollback window and audit users
no longer require the historical legacy evaluation rows. Table/model deletion is
intentionally deferred to a separate destructive migration.
