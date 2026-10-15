# MONSTER M2 IMPLEMENTATION REPORT

Date: 8 September 2026. Implemented in the authorized open workspace, `CareerGPS-Phase-2.6.2-ranking-fix/CareerGPS-Phase-2.6.2`. The separate stable ZIP was not opened, modified or overwritten. M2 extends the existing `employers` app.

## MONSTER M2 SUMMARY

Implemented opt-in employer discovery, structured candidate search, allowlisted candidate cards/detail, vacancy-context evidence ranking, saved candidates, talent pools with source/history, consented prior-applicant rediscovery, explicit audited contact reveal, and candidate organization blocking.

Candidate entry: **Settings > Employer visibility** (`/employers/visibility/`). Employer entry: **Talent**, with Search candidates, Saved candidates and Talent pools. Published vacancies link to **Find matching candidates** and **Rediscover prior applicants**. M1 application access and frozen submitted documents remain separate.

## CANDIDATE PRIVACY MODEL

Seven additive models:

| Model | Ownership and purpose |
|---|---|
| CandidateVisibilityPreference | Candidate-owned opt-ins and a separate city/region to share |
| CandidateEmployerBlock | Candidate-owned organization restriction |
| DiscoveryExperienceIndex | Derived numeric months/date for SQL filtering; no profile snapshot |
| SavedCandidate | Organization-owned active saved state, actor and optional vacancy |
| TalentPool | Organization-owned name/description and active/archive state |
| TalentPoolMember | Organization-owned membership, source, actor and optional application/vacancy |
| TalentPoolMemberEvent | Append-only membership action/source/timestamp history through services |

Absence of a visibility row means private. Existing candidates are not backfilled into discovery. User/profile evidence, documents, preferences and visibility remain candidate-owned; employers have no evidence-edit endpoints. Discovery always excludes inactive users and blocked organizations. No protected-attribute inference or ranking component was added, and the UI makes no legal-compliance claim.

## DISCOVERY AUTHORIZATION

M1's single capability map is extended; active membership, verified organization and active organization are mandatory on Talent routes. Staff/superuser status does not bypass organization membership. Candidate visibility and block routes instead require the authenticated candidate's own profile.

| Role | Search/detail/rediscovery | Save and pools | Contact reveal |
|---|---|---|---|
| Owner | Yes | Yes | Yes, subject to purpose/consent |
| Recruiter | Yes | Yes | Yes, subject to purpose/consent |
| Hiring manager | Yes | Yes | No |
| Interviewer | No | No | No |
| Viewer | No | No | No |

M1 application permissions remain intact; M2 does not invent interview assignments. Organization-scoped service helpers recheck authorization for mutations. Saved/pool/vacancy/application IDs are checked against the authorized organization. Hidden and nonexistent discovery profiles both return 404; no hidden-candidate reason is disclosed.

## VISIBILITY SETTINGS

Defaults: discovery off, contact reveal off, saving off, prior-applicant rediscovery off; verified-employers-only true. M2 always requires verified employers, even if a client attempts to disable that restriction.

Each consent is independent. Enabling discovery does not enable saving/contact/rediscovery. The candidate explicitly enters a city or region to share; the legacy free-form Profile.location is never used as discovery location. Settings explain shared evidence, contact access, prior applications and saving behavior. POST/CSRF is required; GET does not create or enable preference rows.

Saving is a separate global participation choice: both publicly discoverable and consented prior-applicant candidates must also allow talent-pool saving. This stricter policy gives the saving checkbox the same meaning in every discovery route.

## EMPLOYER SEARCH ARCHITECTURE

`employers/discovery.py` applies privacy gates before filters, counts, direct-object lookup and pagination. Public search requires general discovery consent. Prior-applicant search requires consent plus an actual HiringApplication belonging to this organization. Saved/pool/detail authorization can use either valid discovery relationship. All require an active user and no organization block.

Filters use SQL querysets and Exists annotations for confirmed desired careers, skills, qualifications, certifications, shared location, numeric experience and saved/not-saved status. Search never scans raw CV prose, Stories, private notes, uploaded documents or interview practice data. Names are display identity, not ranking input. Results paginate at 20 candidates; no private-profile total or autocomplete is exposed.

Confirmed evidence is prefetched in fixed batches using a shared matching loader, with explicit selected columns. Experience uses the existing overlap-safe dated-work calculation; unknown dates are not invented as zero. A derived months/date index enables SQL numeric filtering. Normal WorkExperience saves/deletes invalidate it; experience-filtered searches refresh stale authorized IDs in batches of 100, including day changes for ongoing employment.

No external search engine or LLM search was added. Indexes include visibility flags, experience months/date, existing foreign keys, and organization/candidate or pool/candidate uniqueness constraints.

## PRIVACY-SAFE CANDIDATE PROJECTION

Templates receive `DiscoveryCard` data rather than internal Profile/User instances. Pagination retains only counts/ranges and projected cards, not a hidden Profile queryset. Explicitly shared fields:

- Full display name, or `Candidate <id>` when no name exists; never username/email fallback.
- Candidate-designated city/region and confirmed desired-career labels.
- Confirmed skill names.
- Qualification, field of study and NQF, excluding institution and free-form education notes.
- Confirmed employment title, organization and dates/current flag; no descriptions, bullets or work address.
- Confirmed certification titles and language names.
- Deterministically derived experience months and current availability/saving state.
- Optional existing-engine evidence summary and carefully worded criteria.

Email, phone, private Profile.location, career notes, private employer/candidate notes, raw CVs, document links, unrelated applications and practice interviews are outside the projection. Discovery evidence queries avoid fetching contact fields, private addresses, career notes and work/education descriptions. Talent responses use `private, no-store`.

## VACANCY MATCHING REUSE

A selected vacancy must be published, belong to the authorized organization and remain discoverable through its canonical Opportunity. M1's structured requirement projection and the existing deterministic matcher supply all criterion evaluation.

`core.matching.employer_evidence_match` is shared by M1 applicant views and M2. The loader can omit candidate preferences; the employer result excludes preference criteria and uses the existing summary calculator over job evidence. Skills, qualifications, NQF, experience, certifications, languages and conservative eligibility semantics are shared. Candidate-only target-role/location/industry/opportunity/work-arrangement preference ordering is excluded. General search shows no score.

Vacancy searches evaluate every SQL-eligible candidate in batches of 100, share one taxonomy context per batch and retain a heap of only the required page's leading IDs/scores. Sorting uses existing evidence score descending, then profile ID for deterministic ties. The selected page rechecks the privacy queryset. Low scores are not removed; ranking is not a shortlist or rejection decision. Tests cover the best match beyond the first 100 candidates and retrieval of the last page.

FOUND, RELATED, NOT_FOUND_ON_PROFILE and UNKNOWN use the requested careful labels. No raw taxonomy IDs or new AI score is shown. Current evidence is recalculated; saved state does not freeze the profile.

## SAVED CANDIDATES

Unique organization/candidate state records actor, source, optional vacancy and timestamps. Save is idempotent; unsave marks inactive and audits the change. Saving requires current discovery access and saving consent. Saved lists and saved filters reapply visibility, blocks, account activity and saving consent, so stored IDs never grant ongoing access.

Removal of retained state is still allowed after consent revocation; it reveals no profile data. Later opt-in/unblock does not silently reactivate a previously deactivated save. A new explicit save is required.

## TALENT POOLS

Pools support create, organization-scoped browse and archive. Memberships support add, duplicate prevention, remove and explicit re-add. Candidates can belong to multiple pools. Archive hides pool discovery without deleting history. The candidate's current shared evidence is displayed, not a saved full-profile copy.

Sources are search, saved candidate, prior applicant, vacancy rediscovery and applicant view. Context-dependent sources require their actual saved/application relationship. Saved-list navigation preserves saved-candidate origin. Prior/rediscovery entries retain the source HiringApplication and selected current vacancy where applicable. Membership events preserve every add/remove/unavailable action with actor and timestamp; re-add does not erase earlier source events.

Opting out of participation or blocking deactivates relevant saves/memberships and retains an unavailable history event. Dynamic query gates also hide entries and counts if privacy changes through a direct database update. The employer is not told the reason. M1 private notes remain application-owned; no talent note or free-form scoring system was added.

## REDISCOVERY

Prior-applicant searches use only this organization's real HiringApplication relationships and current rediscovery consent. General discovery may remain off. Blocks and inactive accounts still exclude the candidate. Results use current confirmed evidence and current published requirements; no stored/stale prior match is reused. Other organizations cannot use that relationship or inspect its application history.

Published vacancies link directly to rediscovery. A deliberate vacancy rediscovery request is audited once per request, not once per result. Candidate display cards do not show rejection histories or other employers' applications.

## CONTACT REVEAL

Contact is absent from every search/card/profile/saved/pool GET response. Reveal requires a verified active organization, owner/recruiter capability, POST, CSRF, explicit purpose and fresh ownership/access checks.

Two separate permitted paths:

1. Discovery: current authorized discovery access, contact-reveal consent and a published owned vacancy for `vacancy_review`.
2. Application: this candidate's own organization's non-withdrawn, nonterminal HiringApplication for `application_followup`. `hired` and `rejected` do not authorize this exception. M1 applicant detail exposes the action to permitted roles.

A candidate organization block overrides both contact paths. Foreign candidate/application/vacancy IDs do not authorize reveal. Only existing email and phone are returned in that POST response. Contact values are not saved to URLs, session state, audit payloads, notifications or a persistent reveal cache. No message is generated or sent.

## CANDIDATE BLOCKING

Candidates can find an organization by name in their own visibility settings, block it and unblock it. Blocks are unique per candidate/organization and cannot be changed by employers. Blocking stops discovery, new saves/pool additions and all contact reveal. Existing application history and frozen documents remain available through M1 permissions. Employers see generic unavailability, never an explicit block reason. Unblocking restores eligibility only when the remaining consents permit it.

## APPLICATION RELATIONSHIP ACCESS

Discovery and application permissions remain separate. Opting out or blocking does not remove M1 applicant views, private hiring notes/stages or already-submitted frozen documents. Live talent views reflect current confirmed evidence, while submitted files remain frozen. Rediscovery permission does not mutate candidate Application.status or employer HiringApplication.stage.

Inactive/deleted candidates are excluded from discovery. New saved/pool candidate references use SET_NULL on profile deletion so historical source/action records can retain minimal identifiers without a discoverable ghost. M1's existing protected application/audit retention still applies.

## AUDIT EVENTS

Existing EmployerAuditEvent records candidate detail access, save/unsave, pool creation/archive, add/remove, deliberate vacancy rediscovery and every successful contact reveal. Context contains IDs, source and controlled purpose, never email/phone or serialized candidate objects. Pool history has its own append-only source/action records.

General search result rendering does not create per-candidate audit events. Employer logs do not state that the candidate blocked them. No M2 notifications are sent for appearance, views, saves, pool actions or contact reveal. M1 application notifications remain unchanged. No outreach, invitations, auto-shortlist or automated rejection was added.

## ADMIN

Platform admin can inspect visibility choices, blocks, saved candidates, pools/members/history and the derived index. These use read-only administration to preserve candidate/service ownership. Existing employer audit admin gains event/organization filters and actor search for contact/discovery access investigations. Broad lists do not expose private candidate contact values or employer-note contents. Platform authority remains distinct from employer role authorization.

## FILES CREATED

- `employers/discovery.py`
- `employers/talent_forms.py`
- `employers/talent_services.py`
- `employers/talent_views.py`
- `employers/test_talent.py`
- `employers/migrations/0002_talentpool_talentpoolmember_talentpoolmemberevent_and_more.py`
- `employers/static/employers/visibility.css`
- `employers/templates/employers/talent_candidate.html`
- `employers/templates/employers/talent_card.html`
- `employers/templates/employers/talent_contact.html`
- `employers/templates/employers/talent_nav.html`
- `employers/templates/employers/talent_pagination.html`
- `employers/templates/employers/talent_pool.html`
- `employers/templates/employers/talent_pools.html`
- `employers/templates/employers/talent_saved.html`
- `employers/templates/employers/talent_search.html`
- `employers/templates/employers/visibility.html`
- `MONSTER_M2_IMPLEMENTATION_REPORT.md`

Local ignored validation artifacts include `.validation/check_monster_m2_ui.py`, screenshots, test logs, preservation manifests and `before-monster-m2.sqlite3`. These do not modify the stable ZIP or M1 backup/report.

## FILES MODIFIED

- `core/matching.py`
- `core/templates/core/settings.html`
- `employers/admin.py`
- `employers/models.py`
- `employers/permissions.py`
- `employers/signals.py`
- `employers/urls.py`
- `employers/views.py`
- `employers/static/employers/employers.css`
- `employers/templates/employers/base.html`
- `employers/templates/employers/vacancies.html`
- `employers/templates/employers/applicant.html`

The working database receives the additive migration and Django registration metadata described below. Candidate evidence/document rows are not migrated or rewritten.

## MIGRATIONS

`employers.0002_talentpool_talentpoolmember_talentpoolmemberevent_and_more` creates seven new tables and three uniqueness constraints. No old migration was rewritten; no existing candidate is opted in and no Application conversion occurs.

The migration was applied successfully to the authorized working database. Post-migration checks report **0 pending migrations** and **0 visibility opt-ins**. A separate SQLite backup remains at `.validation/before-monster-m2.sqlite3`.

Row-hash verification compared every pre-existing row, in stable row order, across **70 existing tables**: **70/70 preserved**, no changed or missing existing rows. Only Django registration metadata was appended: 28 permissions, 7 content types and 1 migration record. All seven new M2 tables remain empty; test fixtures were never seeded into live data.

| Preserved data | Before | After |
|---|---:|---:|
| Opportunities | 4,404 | 4,404 |
| Candidate profiles | 8 | 8 |
| Applications | 13 | 13 |
| Generated documents | 15 | 15 |
| M1 organizations / vacancies / hiring applications | 0 / 0 / 0 | 0 / 0 / 0 |

All other M1/candidate/source/history rows were included in the same 70-table check. SQLite `integrity_check`: **ok**; `foreign_key_check`: **0 errors**. Live ORM smoke check: 4,286 currently discoverable opportunities; anonymous visibility access redirects to login. Evidence is retained in `.validation/monster-m2-preservation-before.json` and `.validation/monster-m2-preservation-after.json`.

## TEST RESULTS

| Validation | Passing | Failures | Errors | Skips |
|---|---:|---:|---:|---:|
| M2 targeted tests | 73 | 0 | 0 | 0 |
| Complete suite, including all 497 pre-M2 tests | 570 | 0 | 0 | 0 |

Final targeted run: **12.296s**. Complete suite: **116.716s**. `manage.py check`: **0 issues**. `makemigrations --check --dry-run`: **no changes detected**. Logs: `.validation/monster-m2-targeted.log` and `.validation/monster-m2-full-suite.log`. Expected failure/fallback messages inside semantic-model negative-path tests do not indicate test failures.

Coverage includes defaults/independent opt-ins, all roles, suspended/unverified/disabled membership, confirmed structured filters, overlap-safe/current experience, privacy projection, direct URL/count/pagination gates, blocked organizations, shared FOUND/RELATED/NOT_FOUND/UNKNOWN semantics, preference exclusion, current/global ranking, save/pool uniqueness and consent/history, cross-org source IDs, contact consent/purpose/application exceptions, CSRF and GET denial, deactivation/deletion, M1 document access, no notification spam and the real HTTP vertical slice.

Query regression tests compare 1 vs 20 general results (no per-candidate increase; at most 18 queries) and 1 vs 13 vacancy-ranked results (no per-candidate increase; at most 35 queries). The 106-candidate test verifies matching beyond the first batch and complete pagination. These are test bounds, not a production throughput claim.

Browser checks: headless Edge rendered six views at 1440px and 390px, **12 viewport checks passed**: general search, vacancy search, candidate detail, pool detail, vacancy Find-candidates entry and candidate visibility settings. No horizontal overflow, contact fixture leakage or raw ESCO/OFO identifiers was detected. Screenshots were visually inspected; all fixtures ran in isolated test databases.

## MANUAL END-TO-END TEST

Executed validation uses real Django HTTP routes through automated test clients (including CSRF checks) and headless browser rendering. A human-driven authenticated browser walkthrough was not performed or claimed.

Manual acceptance flow:

1. As a candidate, open Settings > Employer visibility. Confirm defaults are off. Enable discovery, enter a shared city/region, and independently choose saving/contact/rediscovery permissions.
2. As a verified owner or recruiter, use Talent. Filter by confirmed skill/career/location/qualification/experience. Confirm no contact details appear.
3. From a published vacancy, select Find matching candidates. Inspect careful evidence statuses and compare current profile facts; low scores remain accessible.
4. Save a candidate, create a pool, add the saved candidate and inspect membership source/history. Remove and re-add; archive without losing history.
5. Explicitly reveal contact for the selected vacancy. Confirm only the intended contact fields appear and an audit event is recorded.
6. As a candidate, block that organization or revoke saving/discovery. Confirm search, direct URLs, saved lists and pool counts stop exposing the candidate; no explicit block reason is shown.
7. For a prior applicant who allows rediscovery, use the new vacancy's prior-applicant search. Change confirmed evidence and repeat to see current matching. Confirm an unrelated employer cannot rediscover them.
8. Confirm M1 applicant detail and submitted files remain available after discovery opt-out, while a block prevents contact reveal even through the application exception.

## KNOWN LIMITATIONS

Exact vacancy ranking is synchronous and scans all authorized filtered results in bounded batches. Memory and queries are bounded per batch, but latency still grows with eligible cohort size; production hiring-volume load testing and PostgreSQL concurrency testing were not performed. A future ranking cache/background path must preserve current evidence and consent gates.

The numeric experience index is derived and rebuilt as needed. Normal ORM evidence saves/deletes and day changes are handled; bulk SQL updates that bypass model signals must invalidate the affected index rows. Unknown dates are not compared as verified experience. Search filters use structured text matching; no broad semantic career/skill search or external indexing engine was added.

Disclosure uses all confirmed fields in the documented allowlist; there is no per-record public-history/credential selector in M2. Shared location is candidate-entered city/region text. Verification remains manual. No historical contact-reveal cache grants ongoing access; already-viewed information cannot be recalled from an employer's memory or copies.

No automated outreach, invitation-to-apply, employer AI copilot, scorecards, interviews/scheduling, ATS sync, new free-form candidate scoring, advanced analytics or automated decisions. Talent search does not grant document downloads. Full legal retention/erasure policy remains deferred; M1 protected historical relationships may prevent account deletion, while account deactivation always stops discovery. No formal legal-compliance claim is made.

## MONSTER M3 READINESS

The shared authorization queryset, explicit discovery DTO, independent candidate controls, existing-engine employer evidence helper, pool source/history and audited contact action provide extension points. Later features should continue to recheck candidate access and keep employer metadata separate from candidate evidence. No M3 functionality was implicitly implemented.

## FINAL VERDICT

**M2 is implemented and validated in the current workspace.** All 570 tests pass (73 M2 plus all 497 existing tests), the additive migration is applied, browser/privacy checks pass, and existing data is preserved. Candidates remain private by default; start with **Settings > Employer visibility** and **Employer > Talent**. The separate stable ZIP remains untouched. Production scale/concurrency and full legal retention policy remain explicitly outside the validation performed here.
