# MONSTER M1 IMPLEMENTATION REPORT

Date: 8 September 2026. Implemented in the user-authorized open workspace, `CareerGPS-Phase-2.6.2-ranking-fix/CareerGPS-Phase-2.6.2`. The separate stable ZIP was not opened, changed, or overwritten.

## MONSTER M1 SUMMARY

M1 adds employer organizations, direct vacancies and applications, a human-managed hiring pipeline, submitted document versions, private notes, notifications, and audit history. Employer entry: `/employers/`, also available through **Switch to Employer** in candidate navigation. The existing Django account serves both roles.

## ARCHITECTURE DECISIONS

The `employers` app owns authoring and hiring state. Existing candidate models remain authoritative and are not editable through employer services or routes. `Opportunity` stays the canonical catalogue identity; `Application` stays the unique candidate/opportunity identity.

A shared requirement projection distinguishes `employer_entered`, `ai_extracted`, and the deterministic `source_parsed` fallback. Employer criteria never become fictional AI extraction rows. Publication copies structured requirements and canonical fields in one transaction, retaining an input hash, revision, and raw source history. Matching does not read mutable draft requirements.

Submitted PDF/DOCX bytes are copied into private database rows because existing CV layout updates can rewrite original files in place. References alone would not preserve submitted content. Organization locks serialize publication, membership changes, submission, withdrawal, and hiring mutations on databases supporting row locks; SQLite retains its normal database-level write behavior.

## NEW DJANGO APPS

`employers`, registered with a small signal hook for Career Intelligence invalidation. No second authentication system or external service integration.

## NEW MODELS

| Model | Responsibility |
|---|---|
| Organization | Employer identity, independent verification and account states |
| OrganizationMembership | Unique user/organization membership, role and invitation state |
| EmployerVacancy | Editable source, canonical link, publication copy and revision |
| VacancyRequirement | Typed required/preferred authoring rows, experience bounds and NQF |
| HiringApplication | One-to-one candidate Application extension, hiring stage and withdrawal |
| ApplicationStageEvent | Actor, transition, time, candidate visibility and controlled reason category |
| EmployerApplicationNote | Organization-private, append-only notes through the application services |
| SubmittedDocument | Exact submitted file bytes, original-version reference and SHA-256 hashes |
| EmployerAuditEvent | Actor, organization, action and small object context |

History has no edit/delete UI; admin history is read-only. Protected relationships retain applications and published history. These are application-level controls, not a claim of tamper-proof database storage against a database administrator.

## CHANGED EXISTING MODELS

`IngestionSource.source_type` adds `direct`. `Opportunity` gains behavior for direct application links and organization-aware discovery; it has no new database columns. Existing `Application`, `GeneratedDocument`, candidate profile/evidence/preferences, and AI extraction schemas are unchanged.

## CANONICAL OPPORTUNITY INTEGRATION

Publishing requires a verified, active organization. It creates or updates one canonical Opportunity, preserving platform moderation. Candidate discovery, detail, preparation, and matching use existing routes. The `careergps-direct` source records provenance and raw revisions; it is inactive for scheduled scraping. External deduplication does not merge incoming listings into direct publications, and its canonical field writer cannot overwrite or reopen direct listings.

Published edits synchronize immediately and invalidate derived evidence. Close marks the canonical listing inactive/removed without deletion. Reopen requires verification and an unexpired closing date. Suspension or revoked verification removes direct listings from discovery and blocks employer access; verification is not a safety guarantee for every vacancy.

## STRUCTURED REQUIREMENT INTEGRATION

Authoring supports skills, qualifications, NQF, experience, certifications, languages and work eligibility. Required and preferred stay separate, including preferred NQF and eligibility. Explicit skill/certification `or` and `and/or` alternatives now use the same matcher as individual requirements, with one result for the alternatives; trusted aliases and narrower relationships retain their existing meaning. Qualification and language alternatives use the existing conservative comparisons.

Experience minimum, maximum, context, and the existing distinction between an ordinary range and a restrictive maximum-only constraint are retained. A qualification row has one NQF threshold applying to that entire row. Text with complex conditional alternatives is preserved; it is not a new logical-expression language.

Direct criteria bypass Gemini extraction even on forced batch processing. Shared document job context includes authored numeric bounds, NQF and application instructions. Existing candidate document generation may still call its configured generation provider.

Career Intelligence assignments and snapshots use existing maintenance services. Snapshot coverage distinguishes employer-structured observations from ready AI extractions. Publication and organization visibility changes invalidate current snapshots while preserving frozen historical inputs; publication does not build snapshots on a web request.

## ORGANIZATION PERMISSIONS

All organization/object routes resolve active membership and organization ownership. Staff/superuser status does not bypass employer authorization. Organization creation, invitation acceptance and candidate submission routes use their own authenticated ownership checks because there is no pre-existing active employer membership for those actions.

| Capability | Owner | Recruiter | Hiring manager | Interviewer | Viewer |
|---|---|---|---|---|---|
| Overview, vacancies, applicant evidence/history | Yes | Yes | Yes | Yes | Yes |
| Author/publish/close vacancies | Yes | Yes | No | No | No |
| Change hiring stage | Yes | Yes | Yes | No | No |
| Private notes and submitted downloads | Yes | Yes | Yes | Yes | No |
| Team and organization settings | Yes | No | No | No | No |

Invited users must accept for themselves. Disabled/invited memberships cannot access organization data. The last active owner cannot be removed. New mutations require POST and CSRF; organization IDs are resolved from authorized URL/object relationships rather than trusted form data.

## APPLICATION ARCHITECTURE

Candidate preparation retains the existing Application. Explicit consent plus a selected CV (optional cover letter) creates one HiringApplication and submitted versions. Duplicate submissions are idempotent. Documents must belong to the same user, Application and Opportunity. Missing, foreign, duplicate-type and oversized files are rejected atomically.

Submission sets the candidate Application to `applied`; employer progression changes only HiringApplication. The candidate cannot forge direct receipt or employer stages through the legacy status endpoint. The workspace displays the latest candidate-visible employer event separately. Candidate withdrawal sets `withdrawn`, preserves history/files, blocks further hiring progression and stops the live profile comparison. Resubmission after withdrawal is unavailable in M1.

## EMPLOYER PIPELINE

Stages: received, screening, shortlisted, assessment, interview, final_review, offer, hired, rejected. Authorized humans choose stages; each changed stage records actor and history. Repeating the same stage does not create duplicate events. There is no automatic rejection, decision recommendation, scheduling or interview scorecard.

Applicant lists are scoped and paginated (25 per page), showing identity, submission time, stage, evidence summary, recent stage time and permitted submitted-file links. Dashboard counts reflect stored vacancies/applications and current stages, with recent activity from the existing employer audit history.

## MATCH EXPLANATIONS

The employer view calls the existing deterministic matcher with confirmed job-relevant profile evidence. It excludes candidate career preferences from the supplied matching profile. FOUND, RELATED, NOT_FOUND_ON_PROFILE and UNKNOWN keep their existing meanings; missing evidence is not a claim that a candidate lacks a skill. Eligibility without comparable profile facts remains UNKNOWN. Employer notes never enter the match.

The explanation compares the current confirmed profile with the current published vacancy. It is not a frozen historical match or an AI hiring recommendation. Submitted documents are frozen independently.

## DOCUMENT SUBMISSION ARCHITECTURE

`SubmittedDocument` stores immutable-through-application PDF/DOCX bytes and hashes alongside a nullable original GeneratedDocument reference. Later generation, in-place layout rewrites and original document deletion do not replace the submitted bytes. Each file format is bounded to 10 MiB (up to four formats across a CV and cover letter). These files have no public MEDIA_URL.

Employer download routes require the organization's documents capability and its application relationship. Candidate downloads require ownership. Downloads use attachment headers, `private, no-store` and `nosniff`. Withdrawal retains the employer's already-submitted documents as application history.

## NOTIFICATION FLOW

The existing centralized `notify` service delivers application-category in-app notifications for receipt, assessment, interview, offer, hire and rejection, with unique stage-event keys and candidate Application links. Existing application preferences govern creation; disabling delivery does not erase stage history. Screening, shortlist, final review and private notes do not notify or disclose internal information. No routine email/SMS is sent.

Employer dashboard activity surfaces new applications and hiring events; there is no parallel notification platform.

## AUDIT FLOW

Audit events record organization creation/settings, membership/invitation changes, draft saves, publication/revisions/close/reopen, receipt/withdrawal, every changed hiring stage, private-note creation and platform verification/suspension changes. Note text and candidate objects are not serialized into audit context. Stage events retain transition history independently of the current mutable stage.

## CANDIDATE PRIVACY

No candidate directory, talent search, pool, or pre-application employer access exists. Employer reads require an application to the member organization's vacancy. Candidate private notes, phone, career preferences and unrelated data are not shown in employer applicant views. Employer routes cannot edit the candidate workspace notes, CV layouts or candidate-owned evidence. Submitted files and job-related confirmed evidence are disclosed only after candidate consent. Platform Django admin remains a separate administrative boundary.

## ADMIN

Organization admin supports platform verification and suspension with audit records. Memberships, vacancies, requirements, hiring applications, stage history, notes, audit logs and submitted version identifiers/hashes are inspectable. History has no add/delete or editable fields. Submitted binary contents are excluded from admin forms. Organization logos were deferred; M1 does not introduce public employer uploads.

## FILES CREATED

- `employers/__init__.py`
- `employers/admin.py`
- `employers/apps.py`
- `employers/forms.py`
- `employers/migrations/0001_initial.py`
- `employers/migrations/__init__.py`
- `employers/models.py`
- `employers/permissions.py`
- `employers/services.py`
- `employers/signals.py`
- `employers/static/employers/employers.css`
- `employers/static/employers/vacancy_form.js`
- `employers/templates/employers/applicant.html`
- `employers/templates/employers/applicants.html`
- `employers/templates/employers/base.html`
- `employers/templates/employers/candidate_submission.html`
- `employers/templates/employers/dashboard.html`
- `employers/templates/employers/form.html`
- `employers/templates/employers/home.html`
- `employers/templates/employers/pagination.html`
- `employers/templates/employers/team.html`
- `employers/templates/employers/vacancies.html`
- `employers/templates/employers/vacancy_form.html`
- `employers/tests.py`
- `employers/urls.py`
- `employers/views.py`
- `core/services/requirement_projection.py`
- `ingestion/migrations/0009_alter_ingestionsource_source_type.py`
- `MONSTER_M1_IMPLEMENTATION_REPORT.md`

Ignored local validation artifacts include the isolated browser script/screenshots, test logs, preservation hashes and `before-monster-m1.sqlite3`; they are not product source files or the stable ZIP.

## FILES MODIFIED

- `CareerGPS_Rebuild/settings.py`
- `CareerGPS_Rebuild/urls.py`
- `core/models.py`
- `core/matching.py`
- `core/services/career_market.py`
- `core/services/market_comparison.py`
- `core/templates/core/career_intelligence.html`
- `templates/dashboard_pages.html`
- `applications/views.py`
- `applications/services/job_context.py`
- `applications/templates/applications/workspace.html`
- `applications/static/applications/css/workspace.css`
- `ingestion/models.py`
- `ingestion/ai/service.py`
- `ingestion/pipeline/deduplication.py`

The working `db.sqlite3` receives only the additive migration changes documented below. Earlier Candidate Platform/performance work is retained.

## MIGRATIONS

- `employers.0001_initial`: nine new tables and employer relationship/uniqueness constraints; no conversion of candidate Applications.
- `ingestion.0009_alter_ingestionsource_source_type`: adds the `direct` choice; no source data conversion.
- Old migration files were not rewritten.

Both migrations were applied successfully to the authorized working database. The post-migration executor reports **0 pending migrations**. A separate SQLite backup is retained at `.validation/before-monster-m1.sqlite3`.

Preservation verification hashed every pre-existing row, in row order, across all **61 existing tables** before and after migration: **61/61 preserved**, no differences in existing rows. The only additions to existing tables are 36 permissions, 9 content types and 2 migration records. The nine new employer tables remain empty in the working database; test fixtures were isolated.

| Existing data | Before | After |
|---|---:|---:|
| Opportunities | 4,404 | 4,404 |
| Candidate profiles | 8 | 8 |
| Applications | 13 | 13 |
| Generated documents | 15 | 15 |
| Notifications | 4 | 4 |

SQLite `integrity_check`: **ok**. `foreign_key_check`: **0 errors**. Live ORM smoke check: **4,286 currently discoverable opportunities**, 0 employer organizations, and an anonymous employer-entry redirect to login. The total catalogue and original source/candidate records are retained. Evidence: `.validation/monster-preservation-before.json` and `.validation/monster-preservation-after.json`.

## TEST RESULTS

Final validation on the implemented code:

| Check | Result |
|---|---|
| `python manage.py check` | Pass; 0 issues |
| `python manage.py makemigrations --check --dry-run` | Pass; no changes detected |
| `python manage.py test employers --noinput --verbosity 1` | **74 tests passed in 8.420s** |
| `python manage.py test --noinput --verbosity 1` | **497 tests passed in 109.188s** |
| Existing Candidate Platform regression coverage | **All 423 existing tests passed** within the complete suite |
| Applied migration plan | Both M1 migrations applied; no pending migrations |
| Existing-row preservation / SQLite checks | 61/61 tables preserved; integrity ok; 0 FK errors |

Employer tests cover all five roles, ownership and cross-organization denial, POST/CSRF, admin moderation, invitation acceptance/last owner, publication/edit/close/reopen/suspension, raw provenance, ingestion isolation, matching alternatives/aliases/RELATED/UNKNOWN, NQF and experience projection, real Career Intelligence snapshots/invalidation, consent and one application identity, stage history/notifications/preferences/private notes, document ownership/freezing/downloads, withdrawal, and the HTTP vertical slice.

Logs: `.validation/monster-targeted.log` and `.validation/monster-full-suite.log`. Expected semantic-model failure/fallback log lines belong to passing negative-path tests, not suite failures.

Browser validation: six rendered views (dashboard, vacancy editor, applicants, applicant detail, workspace before submission, workspace after submission) at 1440px and 390px: 12 viewport checks, no horizontal overflow. The requirement-add JavaScript control was exercised. Screenshots were visually inspected. Browser fixtures used an isolated Django test database, never live candidate records.

## MANUAL END-TO-END TEST

The complete HTTP path was exercised automatically using Django's test client, including actual admin verification and a separate CSRF-enforced candidate submission test. Headless Edge exercised rendered layouts and requirement JavaScript. A human-driven authenticated browser walkthrough and a live provider CV-generation call were not performed or claimed.

For a manual acceptance walkthrough:

1. Sign in with an existing account, use Switch to Employer, create an organization, and verify it through platform admin.
2. Create a draft with required/preferred skills, a qualification/NQF and experience bounds. Publish and check Candidate Opportunities/detail/matching.
3. As another candidate account, prepare the existing CV/cover letter, select exact versions, confirm sharing, and submit through the Application Workspace.
4. As an authorized employer member, open the dashboard/applicant, download the submitted CV, inspect evidence, add a private note, shortlist, then move to interview and offer/rejection.
5. As the candidate, confirm in-app updates and workspace stage; confirm the private note is absent. Regenerate/edit the original CV and verify the employer download remains unchanged.
6. Withdraw; confirm the employer cannot progress it. Close/reopen the vacancy and confirm historical applications remain. Check a foreign employer and a viewer cannot download the documents.

## KNOWN LIMITATIONS

Manual platform verification only; invitations require existing usernames. No logo upload, talent search/pools, AI hiring decisions, scheduling, scorecards, integrations, offer letters or advanced analytics. No withdrawn-application resubmission, document replacement after submission or note editing. Candidate deletion may be blocked by retained hiring history; retention/erasure policy needs explicit design before production rollout.

Database-backed frozen files are deliberately simple and private for M1, but increase database/backup size; move to private versioned object storage with equivalent access controls when scale warrants it. Applicant matching is bounded by pagination but has not been load-tested at production hiring volumes. PostgreSQL concurrency was not tested here; SQLite does not implement row-level `select_for_update` locking. Snapshot refresh remains an existing maintenance operation. No new live employer/candidate fixtures were seeded into the working database.

## MONSTER M2 READINESS

Organization ownership, canonical publication, one application identity, actor/stage history, frozen document versions and the requirement projection provide extension points. Future assignments, evaluations, consented discovery and storage/retention design can remain employer-owned without changing candidate evidence authority. No M2 features were implemented implicitly.

## FINAL VERDICT

**M1 implemented and validated in the open workspace.** The employer/application vertical slice works through real Django HTTP routes; 497 tests pass, both additive migrations are applied, all existing rows and 4,404 opportunities are preserved, and the separate stable ZIP remains untouched. Begin through **Switch to Employer** or `/employers/`; an organization needs platform verification before its first publication. This is a tested M1 implementation, not a claim of production load testing or completion of deferred M2 features.
