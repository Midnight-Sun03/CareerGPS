# MONSTER M3 IMPLEMENTATION REPORT

Workspace: `CareerGPS-Phase-2.6.2` inside the authorized open workspace. Implementation and validation: 8 September 2026.

## MONSTER M3 SUMMARY

Implemented organization/vacancy hiring analytics, published-vacancy requirement pressure and current talent supply, membership-based responsibilities, hiring tasks, centralized in-app task notifications, and a filtered activity feed. Employer overview now includes offers, hires and overdue tasks.

M3 performs deterministic calculations only. It introduces no LLM calls, generated summaries, natural-language search, automated decisions, invitations/outreach, Redis, Celery, or AI jobs. The existing Candidate Platform and M1/M2 matching/publication/submission/privacy services remain authoritative.

Entry points: Employer navigation **Analytics**, **Assignments**, **Tasks**, **Activity**; published vacancy **Vacancy intelligence**. Applicant pages link to application responsibility/task creation and show a recorded current stage age.

## HIRING FUNNEL ARCHITECTURE

`employers/analytics.py` reads organization-owned `HiringApplication` and `ApplicationStageEvent`. Optional vacancy selection is checked against the same organization. Current counts group the mutable stage, excluding withdrawals; withdrawn has its own count. Total submissions includes withdrawals. All nine stages are shown, including rejected and hired outcomes.

Historical reach counts distinct applications with a recorded `to_stage`. Conversion is applications with a source event followed by a later target event, divided by applications with a source event. Event order uses timestamp and primary-key order, including tied timestamps. Repeated visits do not multiply conversion. Skipped stages are not inferred. Withdrawals/rejections retain their previously recorded reach. Zero denominators display N/A. No stage event is changed by analytics.

Source reporting recognizes the actual existing `application_received` audit event from direct submission. Applications without that stored attribution display unavailable source history. Saved, pool and rediscovery relationships are not presented as application acquisition sources. No old history is guessed or backfilled.

## TIME METRICS

All durations are days (seconds / 86,400), rounded to two decimals. Mean and median include their sample counts.

- Submission to first recorded non-received stage is first review, including a recorded rejection.
- Submission to first recorded shortlist, interview, offer and hire is shown independently.
- Completed stage visits use adjacent events only when the next event's `from_stage` agrees with the entry stage. Reentries are separate visits. A recorded withdrawal closes the final known visit.
- Current stage age uses the latest recorded matching entry. Current visits are excluded from completed-visit averages; current-age medians have separate coverage. Individual age is also shown on the applicant page.
- Hired/rejected outcomes have no ongoing stage clock. Missing, inconsistent or future timestamps are not reconstructed.
- Vacancy age starts at creation. Days open sums recorded publish/reopen-to-close intervals, plus an ongoing published interval capped at the current closing date. Repeated publication within an open interval does not reset its start. Missing publication/closure evidence displays unavailable.

Days open describes recorded publication intervals, not verified exposure time: suspension durations and historical changes to closing dates were not separately recorded in M1. Those durations are not invented.

## REQUIREMENT PRESSURE

`employers/intelligence.py` loads the canonical published requirement projection once, then calls the existing `employer_evidence_match` for eligible profiles. It never scores raw advert prose or unpublished requirement edits. Taxonomy identity/related evidence, OR alternatives, qualification/NQF, contextual experience, numeric bounds, certification, language and eligibility retain shared-engine semantics.

Each criterion shows FOUND, RELATED, NOT_FOUND_ON_PROFILE and UNKNOWN using fixed evidence language. The comparable denominator excludes UNKNOWN. Missing profile evidence is not translated into an assertion that a person lacks a skill. Required pressure and preferred prevalence are separate.

The engine can project a qualification and its NQF bound as separate criteria. It deliberately remains conservative about ambiguous experience wording; structured numeric bounds alone do not establish whether the work context is comparable. Tests preserve this behavior rather than overriding it in analytics.

## CUMULATIVE RESTRICTIVENESS

A single pass calculates all-required, user-selected required combinations, required groups by category, and cumulative direct-evidence pool counts. Order is qualification, experience, skills, certifications, languages, eligibility, followed by stable engine-key order within a category. No powerset/subset explosion is performed.

Direct means FOUND on every included criterion. Related-combination coverage means all included criteria are FOUND/RELATED and at least one is RELATED. Comparable means no included criterion is UNKNOWN. Preferred criteria cannot be selected as required and never reduce the required pool. Cumulative impact describes evidence, not candidate eligibility or advice to remove a requirement.

## TALENT SUPPLY

The current pool is M2 `eligible_profiles(scope='all')`: active accounts with public discovery consent, or an existing organization-specific application plus prior-applicant rediscovery consent; organization blocks take precedence. A private current applicant can count in the employer's own funnel while being excluded from supply. Supply does not silently prefilter weak matches.

A bounded confirmed-evidence projection supports skill, qualification, dated/overlap-merged experience, certification, explicitly shared location and confirmed desired-career distributions. Each candidate counts once per normalized label. Coverage and unknown/not-supplied counts are separate. Labels may overlap across candidates' multiple qualifications/skills/careers, so totals are not additive. Only the top 30 publishable labels per distribution are displayed.

Prior-applicant, active saved-candidate and active talent-pool candidate counts are scoped to the current organization and deduplicated with EXISTS. Saving consent is checked for saved/pool counts. Archived pools and inactive memberships do not contribute. No employer's relationship information is reused for another employer.

## CAREER MARKET CONTEXT

Observed labour demand is a separate section and separate projection, never mixed into a supply score. It reuses only a non-stale, compatible Career Market Snapshot with exactly one current exact occupation assignment, matching title/taxonomy/assignment/aggregation versions and all-observed geography. Snapshot observation dates, generated time, vacancy total, skill/qualification/certification coverage and item denominators are shown.

Missing, stale, related-only, ambiguous or incompatible assignment/snapshot context is unavailable. No snapshot is built during a request. Frozen demand can include previously stored structured evidence; M3 does not generate or extract new evidence. No shortage percentage, forecast or combined demand/supply score is introduced.

## TEAM ASSIGNMENTS

Four additive models cover responsibilities and tasks with history. `HiringAssignment` records organization, vacancy, optional hiring application, responsibility, assigned member, assigning member, assignment time and active state. References use existing `OrganizationMembership`, never duplicate users.

Responsibilities: primary recruiter, hiring manager, additional collaborator, application assignee. Primary recruiters require owner/recruiter membership; hiring managers require owner/hiring-manager membership. Other active roles may be collaborators/application assignees, without gaining extra capabilities. Database conditional uniqueness protects active primary responsibilities, one application assignee and duplicate collaborators.

Services lock the organization, recheck current actor/assignee membership, validate all organization and vacancy/application relationships, and call model validation. Cross-organization endpoint/service assignments are denied. Reassignment deactivates the old responsibility, retains it and its history, and creates a new responsibility. `HiringAssignmentEvent` records assignments/removals. Repeated identical actions are idempotent. Stage history is untouched.

Disabled members retain historical references but lose access immediately. Assignment history and current member status remain inspectable. Team membership editing remains M1 owner-only.

## HIRING TASKS

`HiringTask` stores organization, optional vacancy/application, title, description, membership assignee/creator, due date, open/completed/cancelled status, timestamps and completion time. `HiringTaskEvent` records actor, assignee, action and time. Terminal tasks cannot be edited/reassigned; repeated identical completion/cancellation is idempotent.

Owner/recruiter/hiring-manager can create, edit, assign, reassign, complete and cancel. Interviewers can list/read only their own tasks and complete only their own open tasks; they cannot create, edit, reassign or cancel. Viewers can read but cannot mutate or be task assignees. All mutation endpoints use POST, CSRF and organization-scoped lookups. Application context is validated and supplies its vacancy when no vacancy is selected.

Task assignment/reassignment calls centralized `notify` with a unique event key. Recipients are active assigned employer members. Notification text is generic, with a protected internal task link and no candidate identifiers, task description or free text. No candidate notification, email or SMS is sent. Overdue open-task counts are implemented; due-soon reminders are not added because no existing employer task reminder mechanism exists.

## ACTIVITY FEED

A paginated allowlist projects audit event label and timestamp only: publication/reopening/closure, recorded application/stage/outcome/withdrawal, responsibility changes, task assignments/completions/cancellations and talent-pool actions. It does not expose audit context, contact reveal, candidate views, notes or search queries. Talent activity requires the Talent capability; interviewer activity excludes team task/responsibility events. Dashboard uses the same projection for its recent activity.

## PRIVACY PROTECTIONS

Hiring analytics are employer-owned application aggregates. Supply authorization is separately verified and uses M2 consent/block gates before reading evidence. Owner/recruiter/hiring-manager have analytics and collaboration; viewer has read-only analytics; interviewer has limited task access and retains existing M1 rights. Supply requires verified Talent access. Suspended/disabled/foreign memberships receive no bypass.

M3 supply exposes aggregate dictionaries, never raw Profile/User model instances. It excludes contact values, legacy street/location fields, institutions, work/education prose, private goals, notes and protected-trait breakdowns. Task descriptions remain within the authorized team/task boundary and never become analytics evidence.

Small-group rule: all supply breakdown counts, including zero, use bands of three (`<3`, `3-5`, `6-8`, etc.); profile-derived labels supported by fewer than three candidates are withheld. No attribute cross-tabs exist. The pool total remains exact, as in M2 discovery. Bands reduce simple subtraction disclosure but are not formal differential privacy and do not prevent every inference across repeated queries over time. Own applicant pipeline counts remain exact.

Analytics, intelligence, collaboration and activity responses use `Cache-Control: private, no-store`. No persisted supply snapshot can outlive consent. History/admin records use existing inspect-only administration; append-only service history is not a defense against direct privileged database tampering.

## PERFORMANCE

Supply runs once per intelligence request and feeds requirements, combinations, relationships and distributions from one complete scan. Profiles/evidence are prefetched in batches of 100 with one taxonomy context per batch and one canonical opportunity input per request. EXISTS prevents relationship duplicates. Query-count regression tests compare one and sixteen candidates and enforce a ceiling; a separate test includes 106 candidates across the batch boundary.

Local isolated profile: 300 discoverable candidates, four vacancy criteria, 32 queries, 0.312 seconds on the final browser/profiling run (an earlier run was 0.379 seconds). This does not establish production throughput. No profiling evidence justifies persistent supply snapshots yet. Cost remains O(candidates x criteria); label counters retain distinct aggregate labels, not full profiles. Funnel processing also prefetches stage events in batches of 100 applications. Analytics is computed on demand without a new cache/service.

## FILES CREATED

- `employers/analytics.py`
- `employers/intelligence.py`
- `employers/collaboration_services.py`
- `employers/intelligence_forms.py`
- `employers/intelligence_views.py`
- `employers/test_intelligence.py`
- `employers/migrations/0003_hiringassignment_hiringtask_hiringtaskevent_and_more.py`
- `employers/templates/employers/analytics.html`
- `employers/templates/employers/funnel.html`
- `employers/templates/employers/intelligence.html`
- `employers/templates/employers/assignments.html`
- `employers/templates/employers/tasks.html`
- `employers/templates/employers/task.html`
- `employers/templates/employers/activity.html`
- `MONSTER_M3_IMPLEMENTATION_REPORT.md`

Ignored `.validation` artifacts include the isolated browser/profiling helper, HTML fixtures, 18 screenshots, logs, a pre-migration SQLite backup and preservation manifests. No fixture rows are written to the working database.

## FILES MODIFIED

- `employers/models.py`
- `employers/permissions.py`
- `employers/urls.py`
- `employers/views.py`
- `employers/admin.py`
- `employers/static/employers/employers.css`
- `employers/templates/employers/base.html`
- `employers/templates/employers/dashboard.html`
- `employers/templates/employers/vacancies.html`
- `employers/templates/employers/applicant.html`

No Candidate Platform source files, M1/M2 migrations/reports, actual secrets, document files, candidate practice Interview, or stable ZIP were modified. The working SQLite database receives only the additive migration and Django metadata.

## MIGRATIONS

`employers.0003_hiringassignment_hiringtask_hiringtaskevent_and_more` adds four tables and three conditional unique constraints. No destructive schema change or data backfill.

Applied successfully to the working SQLite database; `manage.py migrate --check` confirms no pending migrations. Backup: `.validation/before-monster-m3.sqlite3`. SHA-256 row manifests verify **all pre-existing rows across 77 tables are unchanged**. The only additions to pre-existing tables are 16 permissions, 4 content types and 1 migration record. All four new M3 tables are empty in the working database. All **4,404 opportunities** are preserved. SQLite integrity check: `ok`; foreign-key errors: 0. Existing backups and stable ZIP remain untouched.

## TEST RESULTS

- `python manage.py check`: no issues (0 silenced).
- `python manage.py makemigrations --check --dry-run`: no changes detected.
- `python manage.py test employers.test_intelligence --noinput`: **80 passed**, 0 failures, 0 errors, 0 skips, 9.876 seconds.
- Full Candidate Platform + M1 + M2 + M3 suite (`python manage.py test --noinput`): **650 passed**, 0 failures, 0 errors, 0 skips, **140.411 seconds** (570 existing + 80 M3).
- Final headless Edge desktop/mobile validation: **18 passed**, widths 1440 and 390, zero horizontal overflow or fixture privacy leaks. Nine pages/sections: dashboard, analytics, intelligence overview, requirements, supply, tasks, task detail, assignments and activity.

Tests cover recorded/current funnel separation, historical conversions/reentry/timestamp ties, missing/future/inconsistent timing, outcomes/withdrawals, all four matching statuses, required/preferred separation, published semantics/bounds/OR, cumulative/selected/category comparisons, current-applicant versus discovery consent, confirmed distributions/coverage, relationship isolation, rare-label suppression, current snapshot boundaries, responsibility history, role/object authorization, task mutations/CSRF/notifications, activity filtering, no candidate fact/stage rewrites, pagination/batch boundaries and full HTTP workflow.

Logs: `.validation/monster-m3-targeted.log`, `monster-m3-full-suite.log`, `monster-m3-browser-checks.log`, `monster-m3-profile.json`.

## MANUAL END-TO-END TEST

No claim of a human-operated authenticated production browser walkthrough. The implemented HTTP vertical-slice test creates an assignment, creates/assigns/completes a task and renders dashboard, analytics, intelligence, task detail, assignments and activity in an isolated test database. Other tests exercise real M1 submission, source attribution, stage events, notifications and M2 privacy lifecycle.

Browser validation renders actual authorized Django responses with test fixtures into static local pages, loads the real project stylesheet in headless Edge, inspects desktop/mobile widths and visible privacy sentinels, and captures screenshots. Desktop dashboard, mobile requirements and desktop supply screenshots were visually inspected. This validates rendering, not browser-driven form submission; POST/CSRF flows are covered by real Django client tests.

Reproducible human walkthrough: use an active organization owner, publish a structured vacancy, obtain voluntary candidate discovery consent, open Vacancy intelligence, select required combinations, compare Talent supply with Applicant analytics, assign recruiter/application responsibility, create/reassign/complete a task, inspect recipient notification and history, then verify interviewer and viewer restrictions. Revoke discovery/block the organization to confirm supply removal while the own submitted application history remains available under M1.

## KNOWN LIMITATIONS

- Synchronous full-pool matching and event processing are appropriate for measured local scale; production-scale/load/concurrency and PostgreSQL were not benchmarked.
- Historical publication exposure/suspension periods and unrecorded application acquisition sources cannot be reconstructed. Conversion cohorts are all recorded submissions, without a new date/cohort forecasting model.
- No email/SMS, due-soon scheduler, task recurrence, attachments, arbitrary task workflow or full project management.
- Responsibility removal/reassignment preserves history; disabled assignees are visible as disabled but are not automatically replaced.
- Task history records assignment/action transitions, not full previous copies of free-text descriptions or due dates.
- Small-group bands and rare-label suppression are simple privacy controls, not a formal privacy budget.
- Demand context requires an existing compatible exact career assignment/snapshot; M3 does not run career maintenance on request.
- Matching remains conservative and cannot safely infer missing/ambiguous candidate evidence. No requirement-removal advice or automated hiring decisions.

## MONSTER M4 READINESS

Employer vacancies, HiringApplication, membership references, immutable stage events, responsibility history and task context provide separate anchors for a future employer interview schedule, panel, competency, scorecard and evaluation domain. A future panel can reference existing memberships and hiring applications; evaluations can have their own permissions, provenance and history. M3 does not add placeholder scorecard tables, repurpose candidate practice Interview, or mix tasks/notes into candidate evidence.

## FINAL VERDICT

Monster M3 is implemented and validated in the authorized workspace. All 650 tests pass, the additive migration is applied, all pre-existing database rows are preserved, and all 18 desktop/mobile render checks pass. No AI functionality or automatic hiring decision was added. The stated operational, historical-data and browser-validation limits remain explicit.
