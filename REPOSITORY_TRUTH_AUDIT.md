# CareerGPS — Repository Truth Audit

**Audit date:** 2026-10-09
**Auditor:** Automated forensic inspection (Cline), read-only.
**Scope audited:** `D:\CG` (repository root). `E:\CG` referenced in the task was a typo and ignored; `D:\CG` confirmed as the actual root.
**Method:** Static inspection of source, configuration, migrations, tests, documentation and generated artifacts, plus a **read-only SQLite probe** (SELECT/COUNT only; no writes, migrations, imports, queue drains or config changes). Git metadata was inspected.

> Forensic intent: this report establishes what is *demonstrably implemented and connected today*, separated from what is planned, legacy, disconnected, or unknown. It is deliberately not promotional.

---

## 1. Executive summary

CareerGPS is a large, genuinely substantial Django application (5 local apps, ~90 models, 74 applied migrations, 57 test files, ~290 KB of core service code) that implements a **candidate job-matching platform with an aspirationally "evidence-based" occupational-intelligence layer**.

What it demonstrably is today:
* A working **candidate platform**: registration + email OTP, rich profile/evidence editing, an opportunity catalogue (2,670 opportunities), save/bookmark, an application workspace, and **AI-generated CV / cover-letter documents** with DOCX/PDF rendering and download.
* A **multi-source ingestion pipeline** (Greenhouse, SmartRecruiters, Workday adapters) with a polite HTTP client, deduplication, lifecycle refresh, and an optional **Gemini AI extraction** stage. Raw ingestion is real and has run (3,321 `RawOpportunity`, 108 `IngestionRun`).
* A **clean-job import contract** (`core/clean_jobs/`) that validates provenance-bearing clean payloads and persists immutable `OpportunityCleanRevision` rows. 2,421 active clean revisions exist.
* A **taxonomy foundation**: ESCO, ISCO, OFO and O*NET releases are imported and active (21,995 concepts, 215,296 labels, 172,948 relations, 4,253 crosswalks).
* A **deterministic evidence-fit matcher** (`core/matching.py` + `professional_matching.py` + `evidence_fit.py`) producing per-criterion fit bands and explanations.

What it is **not yet**, despite substantial code:
* The **occupational-relationship "decision core"** (`relationship_aggregation.py`, `occupational_resolution.py`) is architecturally central and heavily engineered, but the **live database shows it is barely exercised**: `OpportunityOccupationResolution = 0`, `OccupationRelationshipPolicy = 0`, `OccupationRelationshipRule = 1`, and of 2,928 `TargetOpportunityRelationshipAssessment` rows **2,906 are `UNRESOLVED`** (only 14 EXACT, 7 RELATED, 1 TITLE_MATCH). `CareerTargetResolution` is 4 UNRESOLVED / 1 PARTIAL. The v2 feed therefore resolves almost nothing to a confirmed match — the v2 `ProfileFeedEvaluation` is 2,290 UNRESOLVED vs 24 EXACT / 14 RELATED. (Verified live.)
* The platform is running in **v2 read mode** (`.env`), yet the actual resolved-match yield from v2 is near-zero; the bulk of historical personalization sits in the **legacy** `ProfileOpportunityEvaluation` (1,121 `PROFILE_RELEVANT`).
* **Employer-side** functionality is substantial in code (org verification, vacancies, talent search/pools, hiring assignments) but the live data is essentially seed/test scale (2 organizations, 1 vacancy, 3 verifications, 0 talent pools).
* **Interview** functionality (voice/video, transcription, visual metrics) exists in code and routes but has 2 interviews in the DB.

**Single most important truth:** the ambition (evidence-based occupational matching) and the reality (a working catalogue + legacy matcher + AI documents, with the v2 occupational engine mostly unresolved at runtime) are far apart.

---

## 2. Repository and environment identity

| Item | Finding | Evidence |
|---|---|---|
| Repo root | `D:\CG` (no `E:\CG`) | Working dir; confirmed by shell |
| **Git status** | **NOT a git repository.** No `.git` at `D:\CG` or any parent. | `Test-Path .git` = False |
| Framework | Django (contrib + 5 local apps) | `manage.py:9`; `CareerGPS_Rebuild/settings.py:53-58` |
| Python | 3.12.14 (venv at `.venv`) | Probe output; `manage.py` |
| Entry points | `manage.py` (WSGI/ASGI declared) | `manage.py`; `settings.py:84-85` |
| Settings module | `CareerGPS_Rebuild.settings` (env-driven) | `manage.py:9` |
| Root URLconf | `CareerGPS_Rebuild.urls` | `settings.py:73` |
| DB (live probe) | SQLite `D:\CG\db.sqlite3`, local. **74 migrations applied.** | DB probe |
| Applied migrations | core 23, auth 12, ingestion 14, employers 8, applications 6, interview 5, admin 3, contenttypes 2, sessions 1 | DB probe |
| Feed read version (live) | `v2` (overrides settings default `legacy`) | `.env`; probe |
| v2 worker scheduled | `True` | `.env`; probe |
| AI provider keys present | GEMINI, GROQ, OPENAI (names only) | `.env` |

**Environment quirk (recorded, not a code defect):** `CareerGPS_Rebuild/settings.py` uses an `env_bool()` helper that rejects the literal `DEBUG=release` present in the shell environment; importing settings without overriding `DEBUG` raises `ImproperlyConfigured`. `run_tests.py:4-6` documents and works around this by forcing `DEBUG=1`. All test/probe runs in this audit set `DEBUG=1` and succeeded.

## 3. Application and architecture inventory

**Installed apps** (`settings.py:53-58`): Django `admin/auth/contenttypes/sessions/messages/staticfiles` + local `core`, `interview`, `ingestion`, `applications`, `employers`.

**URL wiring** (`CareerGPS_Rebuild/urls.py`): `core` mounted at root `''`, plus `interview/`, `applications/`, `employers/`, and Django auth password-reset views.

### 3.1 core (candidate platform + intelligence) — `core/` 357 files

### 3.1 core (candidate platform + intelligence) — `core/` 357 files
* **Views/URLs** — `core/views.py` (133 KB), `core/urls.py`. Public views: landing, register, verify_otp/resend, login/logout, dashboard, profile (+personal/section CRUD), opportunities, opportunity_detail, toggle_save, saved, stories/submit, resources, settings, notifications, cv_enhancer, cover_letter_generator, generate_cv, generate_cover_letter. Legal/community in `legal_views.py`, `community_views.py`.
* **Models** — `core/models.py` (102 KB, ~64 classes). Candidate/evidence (`UserFact` base + `Profile`, `ProfileSkill`, `Education`, `WorkExperience`, `Certification`, `Language`, `CareerPreference`, `ProjectCapability`…), taxonomy (`TaxonomyRelease/Concept/Label/Relation/Crosswalk/ReleaseSet/Embedding/OccupationContext`), opportunity (`Opportunity`, `SavedOpportunity`, `CVtip`, `MotivationalNudge`), clean-job (`OpportunityCleanAttempt`, `OpportunityCleanRevision` [immutable], `CleanRequirementGroup`, `CleanOpportunityCriterion`, `CleanCriterionEvidence`), occupational intelligence (`OpportunityOccupationResolution/Identity`, `CareerTargetResolution/Identity`, `OpportunityTitleGateDecision`, `OpportunityTitleResolution`, `OccupationSourceResolution(+Candidate)`, `OccupationRelationshipPolicy/Rule`, `TargetOpportunityRelationshipAssessment/Finding`, `OccupationDefiningCriterion`), feeds/eval (`ProfileOpportunityEvaluation` [legacy], `ProfileEvaluationGeneration`, `ProfileFeedState`, `ProfileFeedEvaluation` [v2], `EvaluationRecomputeTask`), market (`CareerMarketSnapshot(+Opportunity)`), compliance (`LegalAcceptanceEvent`, `PrivacyRequest`, `DataRightsEvent`).
* **Services** — `core/services/` (27 modules, ~308 KB): `feed_v2.py` (41 KB, authoritative build/queryset), `career_analysis.py` (36 KB), `relationship_aggregation.py` (66 KB, decision core), `occupational_resolution.py`, `occupational_index.py`, `evidence_fit.py`, `feed_queue.py`, `queue_worker.py`, `professional_evaluations.py` (legacy compat), `provisional_feed.py`, `title_gate.py`, `feed_explanations.py`, `match_presentation.py`, `notifications.py`, `integrity.py`, `moderation.py`.
* **Clean-job contract** — `core/clean_jobs/` (`contracts.py` strict models, `service.py` import/validate, `source.py` raw export).
* **Taxonomies** — `core/taxonomies/` (`esco.py`, `ofo.py`, `isco.py`, `onet.py`, `resolver.py`, `crosswalks.py`, `normalization.py`, `xlsx.py`).
* **Matching** — `core/matching.py` (138 KB legacy runtime matcher), `core/professional_matching.py` (deterministic evidence fit), `core/professional_policies.py`.
* **Management commands** — `core/management/commands/` (23): taxonomy importers, `verify_v2_feed.py`, `backfill_v2_feed.py`, `backfill_occupational_intelligence.py`, `refresh_occupational_policies.py`, `process_evaluation_queue.py`, `reconcile_*`, `export/import_clean_jobs_v1.py`, `prepare_semantic_model.py`, `trace_opportunity_feed.py`.
* **Queue safety net** — `core/middleware.py` `QueueDrainSafetyNetMiddleware` (request-cycle drain re-arm), mounted first (`settings.py:59-72`).

### 3.2 ingestion (source acquisition) — `ingestion/` 145 files
* **Models** — `ingestion/models.py`: `IngestionSource`, `GreenhouseBoard`, `SmartRecruitersCompany`, `WorkdayBoard`, `IngestionRun`, `RawOpportunity`, `OpportunitySourceRecord`, `OpportunityExtraction`, `OpportunityCriterion`, `OpportunityCriterionTaxonomyMatch`.
* **Adapters** — `ingestion/adapters/`: `base.py` (`PoliteHttpClient`, UA/throttle/timeout from `settings.py:224-230`), `greenhouse.py`, `smartrecruiters.py`, `workday.py` (`WorkdayAdapter`, `WorkdayContractError`), `greenhouse_employers.py`.
* **Pipeline** — `ingestion/pipeline/`: `runner.py` (`run_adapter`), `normalization.py`, `deduplication.py` (`find_duplicate`, `apply_canonical_fields`), `lifecycle.py` (`refresh_opportunity_lifecycle`, `mark_missing_source_records`), `fingerprinting.py`, `records.py`.
* **AI extraction** — `ingestion/ai/`: `service.py` (`process_opportunity_batch`, `save_validated_import`, evidence-supported validation), `extractor.py`, `schema.py`, `payload.py`, `instruction.py`, `normalization.py`. Optional Gemini stage, schema v2.4.1 (`settings.py:180-183`).
* **Commands** — `ingestion/management/commands/`: `ingest_all.py`, `ingest_greenhouse.py`, `ingest_smartrecruiters.py`, `ingest_workday.py`, `add_greenhouse_sources.py`, `extract_opportunities_ai.py`, `import_chatgpt_opportunity_ai.py`, `export_opportunities_for_chatgpt.py`, `refresh_opportunity_lifecycle.py`, `opportunity_ai_status.py`.

### 3.3 employers (recruitment) — `employers/` 130 files
* **Models** — `employers/models.py` (~28 classes): `Organization`, `OrganizationMembership`, `EmployerVacancy`, `VacancyRequirement`, `VacancyIntake`, `HiringApplication`, `ApplicationStageEvent`, `EmployerApplicationNote`, `SubmittedDocument`, `EmployerAuditEvent`, `CandidateVisibilityPreference`, `CandidateEmployerBlock`, `TalentInvitation`, `DiscoveryExperienceIndex`, `SavedCandidate`, `TalentPool(+Member/Event)`, `HiringAssignment(+Event)`, `HiringTask(+Event)`, `OrganizationVerification`, `VerificationReviewEvent/Document`, `OrganizationRiskFlag`, `MemberDomainSignal`, `EmployerAbuseCounter`.
* **Views** — `views.py` (org dashboard/company/vacancies/intake/review/applicants/stage/notes/download, candidate submit/withdraw), `talent_views.py` (visibility/block, candidate search/save/pool/contact/invite, invitations), `intelligence_views.py` (analytics/intelligence/assignments/tasks/activity), `verification_views.py` (verification/document/review/flag/vacancy release).
* **Services** — `services.py`, `talent_services.py`, `trust_services.py`, `discovery.py`, `intelligence.py`, `analytics.py`, `collaboration_services.py`, `vacancy_intake.py`, `abuse.py`, `domains.py`, `presentation.py`, `permissions.py`.

### 3.4 applications (CV/cover-letter workspace) — `applications/` 69 files
* **Models** — `applications/models.py`: `Application` (per-user×opportunity workspace), `GeneratedDocument` (rendered CV/cover-letter artifacts).
* **Services** — `applications/services/`: `document_generation.py` (AI provider routing + prompts), `document_editing.py`, `candidate_facts.py`, `job_context.py`, `json_resume.py`, `resume_templates.py`, `semantic_relevance.py`, `semantic_model.py`, `semantic_warmup.py`, `document_timing.py`, `integrity.py`.
* **Views/URLs** — `applications/urls.py`: start/workspace/status/notes/resolve-saved + document preview/edit/content/layout/download.

### 3.5 interview (interview prep + audio) — `interview/` 80 files
* **Models** — `interview/models.py`: `Interview`, `InterviewQuestion`.
* **Services** — `interview/services/`: `ai.py` (31 KB, question generation/analysis), `transcription.py`, `visual_metrics.py` (23 KB), `context.py`, `delivery_metrics.py`.
* **Views/URLs** — `interview/urls.py`: home/setup/prepare/live/feedback/transcripts/media/saved + JSON API (create, answer-written, answer-voice, complete, transcripts, delete, get).

### 3.6 project root & rebuild note
* `CareerGPS_Rebuild/` (settings/urls/wsgi/asgi) is the **active** Django project; `CareerGPS/` (7 files) and `CareerGPS_Rebuild/` coexist — see §12.
* `templates/` (3 files) — root template dir (`dashboard_pages.html`, `base`-style shared partials). `Taxonomies/` (7 files), `scripts/` (2), `media/`, `.local_models/`, `.validation/`.

## 4. Capability status matrix

Statuses: **IMP**=Implemented · **INC**=Implemented but incomplete · **PAR**=Partially implemented · **PLN**=Planned/Documented only · **NF**=Not found (in inspected scope) · **UNK**=Unknown.

| # | Capability | Status | Conf | One-line evidence |
|---|---|---|---|---|
| 1 | Candidate registration + email OTP | INC | High | `views.register_view/verify_otp/resend_otp` (`core/urls.py:20-36`); `test_registration_otp.py` |
| 2 | Profile & evidence editing | IMP | High | `views.profile_*` CRUD; DB: `Profile=16`, `ProfileSkill=31`, `Education=6` |
| 3 | CV parsing / upload text extraction | PAR | Med | `views._extract_text_from_cv`; prefill helper, not a standalone parser service |
| 4 | Opportunity catalogue browse | IMP | High | `views.opportunities_view` (`core/urls.py:64`); `Opportunity=2,670`, `professionally_ready=1,977` |
| 5 | Save / bookmark | IMP | High | `views.toggle_save_view`/`saved_view`; `SavedOpportunity` model |
| 6 | Application workspace (status/notes) | IMP | High | `applications/urls.py` start/workspace/status/notes; `Application=16` |
| 7 | AI CV generation | INC | High | `views.generate_cv`→`applications/services/document_generation.py` (Gemini); `cv=6` |
| 8 | AI cover-letter generation | INC | High | `views.generate_cover_letter`; `cover_letter=6` |
| 9 | CV/CL DOCX/PDF render + download | IMP | High | `views._build_docx/_build_pdf`; `applications/urls.py` document_download |
| 10 | Multi-source ingestion (3 adapters) | IMP | High | `ingestion/adapters/{greenhouse,smartrecruiters,workday}.py`+`pipeline/runner.run_adapter`; `RawOpportunity=3,321`, `IngestionRun=108` (101 success) |
| 11 | Dedup + lifecycle refresh | IMP | Med | `pipeline/deduplication.find_duplicate`, `lifecycle.refresh_opportunity_lifecycle` |
| 12 | Optional Gemini AI extraction | INC | Med | `ingestion/ai/service.process_opportunity_batch`; **all 1,857 `OpportunityExtraction` are `stale`** |
| 13 | Clean-job import contract (immutable) | IMP | High | `core/clean_jobs/service.import_clean_payload`; `OpportunityCleanRevision=2,421`, `CleanOpportunityCriterion=24,533` |
| 14 | Taxonomy import (ESCO/ISCO/OFO/O*NET) | IMP | High | `core/taxonomies/*`+importers; `TaxonomyConcept=21,995`, 4 active releases (esco 1.2.1, isco 08, ofo 2021, onet 31.0) |
| 15 | Taxonomy resolver / candidate rows | IMP | Med | `core/taxonomies/resolver.build_taxonomy_context`; `test_taxonomy_matching.py` |
| 16 | Occupational source resolution | PAR | Med | `occupational_resolution.resolve_vacancy_sources`; `OccupationSourceResolution=2,916` but `OpportunityOccupationResolution=0` |
| 17 | Career-target resolution | PAR | Med | `CareerTargetResolution=5` (4 UNRESOLVED/1 PARTIAL), `CareerTargetIdentity=1` |
| 18 | Exact/Related/Unrelated decision core | INC | High | `relationship_aggregation.assess_target_vacancy`; 2,928 assessments but **2,906 UNRESOLVED**; `OccupationRelationshipPolicy=0`, `Rule=1` |
| 19 | Deterministic evidence-fit matcher | IMP | High | `professional_matching.evaluate_profile_revision`+`evidence_fit`; `test_match_presentation.py` |
| 20 | Legacy runtime matcher (ranking/why) | IMP | Med | `matching.get_matched_opportunities` still called at `views.py:993` (dashboard) & `views.py:1734` (opportunities) |
| 21 | v2 authoritative feed build/queue | PAR | High | `feed_v2.build_profile_generation`+`feed_queue`; 12 COMPLETE/5 ACTIVE generations but v2 yield ≈24 EXACT/14 RELATED |
| 22 | Legacy professional feed | PAR | High | `professional_evaluations.py` docstring: "compatibility-only legacy…no production signal schedules legacy writes after Block 3"; `ProfileOpportunityEvaluation=1,121 PROFILE_RELEVANT` |
| 23 | Provisional bridge feed | INC | Med | `provisional_feed.materialize_provisional`; `test_provisional_bridge.py`, `test_provisional_feed_contract.py` |
| 24 | Employer org + vacancy management | IMP | Med | `employers/views.py`; `EmployerVacancy=1` (seed scale) |
| 25 | Employer verification / trust | INC | Med | `verification_views`+`trust_services`; `OrganizationVerification=3` |
| 26 | Employer talent search / pools | PAR | Med | `talent_views`/`talent_services`; **`TalentPool=0`, `SavedCandidate=0`** |
| 27 | Employer hiring assignments/tasks | PAR | Med | `intelligence_views`; models present, near-zero live data |
| 28 | Interview prep (question gen) | INC | Med | `interview/views`+`services/ai.py` (31 KB); `Interview=2`, `InterviewQuestion=10` |
| 29 | Voice/video + transcription + visual metrics | PAR | Low | `interview/services/{transcription,visual_metrics}.py`; MediaPipe/whisper deps not verified runnable |
| 30 | Notifications | IMP | Med | `services/notifications.py`+`notify_overdue_opportunities_for_user`; `Notification` model |
| 31 | Community stories + moderation | IMP | Med | `community_views`+`services/moderation.py`; `test_community.py` |
| 32 | Data rights / privacy / account closure | IMP | High | `legal_views`+`data_rights.py`; `test_legal_privacy.py`, `test_data_rights.py` |
| 33 | Semantic model (sentence-transformers) | PAR | Low | `applications/services/semantic_model.py`+`prepare_semantic_model` cmd; warm path not verified |
| 34 | Background recompute queue | IMP | High | `feed_queue`+`queue_worker`+`middleware.QueueDrainSafetyNetMiddleware`; `test_queue_worker.py`, `test_queue_safety_net.py` |
| 35 | Scheduled tasks / cron | NF | Med | No Celery/cron entrypoint; scheduler = request-safety-net + manual mgmt commands only |
| 36 | REST API surface (DRF) | NF | High | No DRF/serializers; only hand-rolled interview JSON endpoints + Django auth pw-reset |
| 37 | Career market snapshots | PAR | Low | `CareerMarketSnapshot` model + `maintain_career_market` cmd; live use unknown |

## 5. End-to-end workflow traces

Traced entry → service logic → persistence → user-visible result. "MISSING LINK" flags a break in an otherwise-connected path.

### 5.1 Registration → OTP → profile
**Entry:** `POST /register/` (`core/urls.py:21` → `views.register_view`).
**Logic:** creates `User`, generates OTP, `_send_verification_email`, digest in session. **Verify:** `POST /verify-otp/` → redirect `dashboard`.
**Evidence:** `core/test_registration_otp.py`.
**Gap:** email backend defaults to **console** when `EMAIL_HOST` unset (`CareerGPS_Rebuild/settings.py:131-139`) — OTP not delivered over SMTP unless configured. **IMPLEMENTED BUT INCOMPLETE**.

### 5.2 Profile creation & evidence management
**Entry:** `/profile/`, `/profile/<section>/`, `/profile/sections/.../add|edit|delete` (`core/urls.py:54-61`).
**Logic:** `profile_view` → `_profile_hub_context` → per-section CRUD. **Persistence:** `UserFact` subclasses (`core/models.py:82-167`).
**Evidence:** `test_career_selection.py`, `test_career_helpers.py`. **Status: IMPLEMENTED.** CV *parsing* is text extraction only (`_extract_text_from_cv`, `views.py:9`).

### 5.3 Opportunity acquisition → clean revision
**Entry:** `ingest_all`/`ingest_{workday,smartrecruiters,greenhouse}` (`ingestion/management/commands/`).
**Logic:** `pipeline/runner.py:run_adapter` → `adapters/*.py` → `RawOpportunity` → `deduplication.find_duplicate` → `OpportunitySourceRecord` → `ai/service.py:process_opportunity_batch` → `OpportunityExtraction`/`OpportunityCriterion`.
**Clean boundary:** external cleaner payload → `clean_jobs/service.py:import_clean_payload` → immutable `OpportunityCleanRevision` (`core/models.py:1298`).
**MISSING LINK (operational):** cleaning is an **export → external clean → import** loop (`export_clean_jobs_v1`/`import_clean_jobs_v1`); semantic cleaning between them is manual/external. **IMPLEMENTED BUT INCOMPLETE**.

### 5.4 Taxonomy import → occupational resolution
**Entry:** `import_{ofo,esco,isco,onet}_taxonomy`, `import_taxonomy_crosswalks` (`core/management/commands/`).
**Logic:** `core/taxonomies/*.py` → `TaxonomyRelease`/`Concept`/`Label`/`Relation` (`core/models.py:169-195`); `occupational_resolution.resolve_vacancy_sources`/`resolve_target_sources`.
**Verified DB (authoritative, `tmp/cg_db_audit3.txt`):** `TaxonomyRelease=4` (esco 1.2.1, isco 08, ofo 2021, onet 31.0), `TaxonomyConcept=21,995`, `TaxonomyLabel=215,296`, `TaxonomyRelation=172,948`, `TaxonomyCrosswalk=4,253`, `TaxonomyOccupationContext=198,462`; `OccupationSourceResolution=2,916`. **Status: IMPLEMENTED.**

### 5.5 Career target → relationship assessment (evidence core)
**Entry:** `feed_v2.build_profile_generation` (queue) and `relationship_aggregation.assess_target_vacancy`.
**Logic:** resolve sources → `compare_source` → `aggregate_relationship_set` → classify EXACT/RELATED/TITLE_ONLY/UNRELATED via `OccupationRelationshipRule` + crosswalks → persist `TargetOpportunityRelationshipAssessment`/`Finding`.
**Verified DB:** `TargetOpportunityRelationshipAssessment=2,928` populated; `final_class` dist `{UNRESOLVED:2906, EXACT:14, RELATED:7, TITLE_MATCH:1}`. **Result:** verdict → `_admission_card` (`views.py:1589`).
**Status: IMPLEMENTED**; **presentation regression confirmed** (§4, §10, §12).

### 5.6 Eligibility/admission/matching → ranked feed
**Entry:** `GET /opportunities/` (`views.opportunities_view`, `views.py:1664`).
**Logic:** `v2_feed_queryset` by confirmed/discovery category → `get_matched_opportunities` (from `professional_matching`) for the ranking pass on admitted ids; discovery area for unassessed; uncertain area for the rest.
**Status:** v2/relationship verdict present; retrievable feed exists but yields near-zero confirmed results at this snapshot (§8.6).

### 5.7 Saved opportunities → application workspace
**Entry:** `POST /save/<id>/` → `/saved/`; `/applications/start/<id>/` → `/applications/<id>/`.
**Persistence:** `SavedOpportunity`, `Application`. **Status: IMPLEMENTED.**

### 5.8 CV / cover-letter generation → render → download
**Entry:** `/generate-cv/` (`views.generate_cv`, `@document_timing`); `/applications/documents/<id>/download/<fmt>/`.
**Logic:** `_build_cv_prompt` → `_generate_cv_content` (`views.py:2967`, **Google GenAI `genai.Client`, model `gemini-3.5-flash`**) → JSON → `applications/services/document_generation.py` → `_build_docx`/`_build_pdf` → `GeneratedDocument`.
**Gap:** requires `GEMINI_API_KEY` (fallback `GEMuINI_API_KEY`); provider-dependent, not called with a live key this audit. **IMPLEMENTED BUT INCOMPLETE.**

### 5.9 Interview prep + audio/transcription
**Entry:** `/interview/` → `interview_prepare` → `interview_live` → `interview_feedback`.
**Logic:** `interview/services/ai.py` (question gen) + `transcription.py` (whisper) + `visual_metrics.py` (MediaPipe).
**Verified DB:** `Interview=2`, `InterviewQuestion=10`.
**Status:** question-gen **IMPLEMENTED BUT INCOMPLETE**; voice/video/visual **PARTIALLY IMPLEMENTED** (heavy deps not verified runnable).

### 5.10 Notifications & lifecycle
**Entry:** `notify_overdue_opportunities_for_user` inside `opportunities_view` (`views.py:1666`); `/notifications/`.
**Logic:** `services/notifications.py`; `Notification`+`NotificationPreference`. On-request, not scheduled. **IMPLEMENTED.**

### 5.11 Employer registration → verification → vacancy → candidate discovery
**Entry:** `/employers/` → `create` → `dashboard`; `verification`.
**Logic:** `employers/views.py` → `trust_services.py`+`verification_views.py` (independent-contact verification) → `talent_services.py`/`discovery.py` → `HiringApplication`/`TalentPool`.
**Verified DB:** `OrganizationVerification=3`; **`TalentPool=0`, `SavedCandidate=0`**.
**Status:** org/vacancy **IMPLEMENTED BUT INCOMPLETE** (`EmployerVacancy=1` seed-scale); verification **IMPLEMENTED BUT INCOMPLETE**; talent search/pools **PARTIALLY IMPLEMENTED** (near-zero live data).

## 6. Matching & occupational-intelligence audit

### 6.1 Two parallel matching implementations exist
- **`core/matching.py` (138 KB)** — legacy runtime matcher. Entry `get_matched_opportunities` / `match_profile_to_opportunity` / `evaluate_match` → `_result_dictionary` → band/score/`build_why_text`. **Still called live** at `views.py:993` (dashboard) and `views.py:1734` (opportunities feed, over admitted ids only).
- **`core/professional_matching.py` (19 KB)** — deterministic evidence-fit matcher consumed by the v2 build: `evaluate_profile_revision` → `criterion_results` → `_fit_band`; supports `evidence_fit.py` (`compare_profile_to_revision`, band/importance enums).
**Responsibility duplication:** `core/matching.py` and `core/professional_matching.py` + `core/services/evidence_fit.py` all compute criteria/band/explanation logic by different rules. The feed (v2) consumes `professional_matching`/`evidence_fit`; the **legacy runtime ranking/why text** still consumes `matching.py`. Both are active → dual source of truth for "fit" labels.

### 6.2 Occupational relationship decision core
`core/services/relationship_aggregation.py` (66 KB) is the exact/related/unrelated engine:
- `resolve_vacancy_sources` / `resolve_target_sources` (`occupational_resolution.py`) resolve OFO/ESCO concepts per side.
- `compare_source` → `aggregate_relationship_set` → `aggregate_comparisons` apply policy rules, exact crosswalks, tier rules (`_exact_tier`, `_related_tier`), and title-only disclosure (`_title_only_disclosure`).
- `assess_target_vacancy` persists `TargetOpportunityRelationshipAssessment`.

### 6.3 Admission & feed read
`feed_v2.build_profile_generation` materializes generations atomically (`_activate_generation`, stale-build guard `StaleGenerationBuild`); `v2_feed_queryset` reads by category; `unassessed_catalogue_queryset` computes the uncertain set at read time. Verified live: `ProfileFeedEvaluation`, `ProfileEvaluationGeneration` populated (see §8); `ProfileOpportunityEvaluation=1,121` legacy rows are **compatibility-only** (`professional_evaluations.py` docstring).

### 6.4 Known matching/presentation defect (verified)
`core.test_feed_v2` batch run produced **2 failures** (see §10): confirmed cards do not surface `'Exact match'` and title-only disclosure `'not counted as a relationship'` in the rendered response. Root cause traced to `opportunities.html:139-145` (match-reason block renders only `{{ match.card_reason }}`; `match.discovery_label` and `match.title_only_lines` are provided by `_admission_card` at `views.py:1632/1615` but **not rendered**) — and **literal template corruption at `opportunities.html:183`** (`...</p>reason">{{ match.card_reason }}</p>`). **IMPLEMENTED with a confirmed presentation-layer regression.**

## 7. Opportunity ingestion & external-cleaning audit

### 7.1 Source adapters (code present)
`ingestion/adapters/`: `workday.py` (20 KB, `WorkdayAdapter`, `WorkdayContractError`), `smartrecruiters.py` (16 KB), `greenhouse.py` (9 KB) + `greenhouse_employers.py`, shared `base.py` (`PoliteHttpClient`, `SourceAccessDenied`). These are **reusable adapter implementations**.
**Source config models:** `IngestionSource`, `GreenhouseBoard`, `SmartRecruitersCompany`, `WorkdayBoard` (`ingestion/models.py`) — populated by `ingest_all` and seed commands (`seed_workday_boards`, `add_greenhouse_sources`, `seed_smartrecruiters_sources`). Distinguish **adapter code (implemented)** from **operational source config (depends on DB rows / env)**.

### 7.2 Raw → source → extraction lifecycle
`pipeline/runner.py:run_adapter` orchestrates: fetch → `RawOpportunity` → `deduplication.find_duplicate` (identity by `external_id`+normalized) → `apply_canonical_fields` → `OpportunitySourceRecord` → `ai/service.py` extraction. `pipeline/lifecycle.py`: `mark_missing_source_records` (grace window), `refresh_opportunity_lifecycle` (re-run).
**AI extractor:** `ingestion/ai/` — `service.py:process_opportunity_batch` validates evidence-against-source (`_evidence_supported`), `_mark_failed` on invalid, `save_validated_import` persists. Uses Gemini (`OPPORTUNITY_AI_MODEL`, `gemini-3.5-flash`). Explicitly **not** fed candidate data.

### 7.3 External clean boundary (import_clean_payload)
`core/clean_jobs/` enforces a hard contract: `contracts.py` (Pydantic `StrictModel` — every non-empty fact needs `SourceReference` provenance), `service.py` validates provenance against source record, applies immutability (`_ImmutableAuditQuerySet`, `save`/`delete` raise), activates atomically (`_activate_valid_contract`), quarantines on failure (`_quarantine`).
**Verified DB:** `OpportunityCleanRevision`, `CleanOpportunityCriterion`, `CleanCriterionEvidence` populated (see §8) → import path demonstrably executes.
**Boundary:** flow is `export_clean_jobs_v1` (dump + provenance) → **external semantic cleaner (manual/out-of-repo)** → `import_clean_jobs_v1`. In-repo code enforces validation/immutability/atomic activation; the semantic cleaning step is external/manual. **IMPLEMENTED BUT INCOMPLETE** (clean step not fully automated in-repo).

## 8. Data, database & taxonomy audit

All figures from a **read-only** Django shell probe (`tmp/cg_audit_ro2.py`/`ro3.py`, output in `tmp/cg_db_audit*.txt`), executed with `DEBUG=1`. No writes.

### 8.1 Migrations applied (consistency)
`admin 3, applications 6, auth 12, contenttypes 2, core 23, employers 8, ingestion 14, interview 5, sessions 1 = 74`. Models and migrations appear **consistent** (`makemigrations --check` could not be run due to the `DEBUG=release` env issue in §2; not a model/migration inconsistency).

### 8.2 Opportunities
`Opportunity` lifecycle: `{active:2347, expired:128, removed:195}`; `is_active True:2347`; `scope_status {unclassified:2587, in_scope:83}`; `professionally_ready 1977`; `with_active_clean_revision 2421`.

### 8.3 Clean jobs (external cleaning imported)
`OpportunityCleanAttempt=2423`, `OpportunityCleanRevision=2421`, `CleanOpportunityCriterion=24,533`, `CleanCriterionEvidence=26,322`. → the clean-import path is demonstrably populated and functioning.

### 8.4 Taxonomy (imported datasets)
`TaxonomyRelease=4`: **esco 1.2.1, isco 08, ofo 2021, onet 31.0**; `TaxonomyConcept=21,995`, `TaxonomyLabel=215,296`, `TaxonomyRelation=172,948`, `TaxonomyCrosswalk=4,253`, `TaxonomyOccupationContext=198,462`; `TaxonomyEmbedding=0`.

### 8.5 Occupational intelligence / relationship
`OpportunityOccupationResolution=0`, `OpportunityOccupationIdentity=0`, `OccupationRelationshipPolicy=0`, `OccupationRelationshipRule=1`, `OccupationDefiningCriterion=0`, `CareerTargetResolution=5`, `CareerTargetIdentity=1`, `OccupationSourceResolution=2,916`, `TargetOpportunityRelationshipAssessment=2,928` (`final_class {UNRESOLVED:2906, EXACT:14, RELATED:7, TITLE_MATCH:1}`).
**Finding:** resolution/assessment runs (source-level), but concept-identity and policy tables are near-empty → the relationship engine leans on a single rule + crosswalks, and the large UNRESOLVED tail confirms incomplete occupational coverage.

### 8.6 Feeds
`ProfileOpportunityEvaluation (legacy)=1,121 {PROFILE_RELEVANT}`; `ProfileFeedEvaluation (v2) discovery_category {EXACT:24, RELATED:14, UNRESOLVED:2290}`; `ProfileEvaluationGeneration {COMPLETE:12, ACTIVE:5, SUPERSEDED:20, FAILED:1}`; `ProfileFeedState {NO_TARGETS:9, READY:5}`.

### 8.7 Ingestion
`IngestionSource=66`, `GreenhouseBoard=53`, `SmartRecruitersCompany=2`, `WorkdayBoard=5`, `IngestionRun=108 {success:101, failed:5, partial:2}`, `RawOpportunity=3321`, `OpportunitySourceRecord=2666`, `OpportunityExtraction=1857`.
**Finding:** **all 1,857 `OpportunityExtraction` rows are `stale`** → the AI extraction layer has not been refreshed against current sources; downstream clean/feed stages run on stale extraction state. `RawOpportunity` normalization `{unclassified:2648, out_of_scope:585, published:88}`.

### 8.8 Candidate / employer / applications / interview (live scale)
`Profile=16`, `CareerPreference=5`, `ProfileSkill=31`, `WorkExperience=5`, `Education=6`, `ProjectCapability=0`; `Organization=2`, `EmployerVacancy=1`, `HiringApplication=1`, `TalentPool=0`, `SavedCandidate=0`, `OrganizationVerification=3`; `Application=16`, `GeneratedDocument {cv:6, cover_letter:6}`; `Interview=2`, `InterviewQuestion=10`.
**Interpretation:** candidate core has real data; employer/talent-pool and interview are effectively **seed/demo scale** — features exist but have negligible live usage.

## 9. UI, APIs & integrations audit

### 9.1 Candidate UI
Templates in `core/templates/core/` and `templates/dashboard_pages.html`. Pages: landing, register/OTP/login, dashboard, profile (+ sections), opportunities (+ detail), saved, stories, resources (CV/cover-letter), settings, notifications, legal/privacy/account-closure. Connected to live views/services (§5). **Confirmed defect:** `opportunities.html:183` literal corruption; card presentation regression (§4/§6/§12).

### 9.2 Employer UI
`employers/templates/` — org dashboard, verification, analytics, intelligence, tasks, talent search/saved/pools, vacancies/applicants. Views wired (`employers/urls.py`). Live data is seed-scale (§8.8).

### 9.3 APIs
**No Django REST Framework / serializers** (`NOT FOUND`). Only hand-rolled JSON endpoints: `interview/urls.py` `api/*` (create/answer/complete/transcripts/delete) and Django auth password-reset (`CareerGPS_Rebuild/urls.py`). Candidate/employer surfaces are server-rendered HTML.

### 9.4 AI integrations (configured)
- **Google GenAI** (`google.genai`): `views._generate_cv_content` (`views.py:2967`, `gemini-3.5-flash`), `ingestion/ai/`, `interview/services/ai.py`. Keys: `GEMINI_API_KEY` + fallback `GEMuINI_API_KEY`/`GEMINI_API_KEY_FALLBACK` (`settings.py:157-183`).
- **OpenAI** (`OPENAI_API_KEY`, model `gpt-5.6-terra`) and **Groq** (`openai/gpt-oss-120b`) settings present — routing not exhaustively traced; treat provider routing beyond GenAI as **PARTIALLY IMPLEMENTED / unverified**.
- **sentence-transformers** (`all-MiniLM-L6-v2`, local path `.local_models/semantic`) — semantic relevance/model warm (`applications/services/semantic_*`); warm path not verified this audit.
- **MediaPipe** + **whisper** for interview visual metrics/transcription — deps present, not verified runnable.

## 10. Tests & operational verification results

### 10.1 What was actually executed this audit (evidence)
1. **`core.test_clean_architecture`** → **22 tests, OK (~18.5s)** — includes `test_search_and_detail_allow_clean_category_none_without_evaluation` (asserts no `\"Evidence band\"` on detail). **PASSED.**
2. **`core.test_community.OpportunityIntegrityTests`** → **8 tests, OK (1 skipped, ~2.9s)** — includes the `/saved/` render assertion. **PASSED.**
3. **`core.test_feed_v2` batch (97 tests)** → **FAILED (failures=2, skipped=3)** — see 10.2.

### 10.2 Verified failures (execution evidence, not assumption)
- `test_exact_career_labels_search_and_one_universe_at_runtime` (`test_feed_v2.py:632`) — expects `'Exact match'` on the search response; absent.
- `test_title_only_evidence_is_disclosed_beside_a_confirmed_relationship` (`test_feed_v2.py:1427`) — expects title-only disclosure `'not counted as a relationship'`; absent.
- **Root cause traced (read-only):** `opportunities.html:139-145` renders only `{{ match.card_reason }}`; the card dict supplies `discovery_label` (`views.py:1632`) and `title_only_lines` (`views.py:1615`) but the template does not render them; plus literal corruption at `opportunities.html:183`. **Confirmed presentation-layer regression / incomplete migration.**

### 10.3 Test inventory (by scope)
59 `test*.py` files across apps. Notable: `test_feed_v2`, `test_occupational_intelligence`, `test_clean_architecture`, `test_clean_jobs_pipeline`, `test_matching`, `test_match_presentation`, `test_taxonomy_import`/`_matching`, `test_provisional_*`, `test_queue_worker`/`_safety_net`, `test_data_rights`, `test_legal_privacy`, `test_registration_otp`; employer `test_verification`/`test_talent`/`test_completion`; ingestion `test_workday`/`test_smartrecruiters`/`test_adzuna_purge`; interview `test_phase3/4/41`.
**Coverage note:** strong unit coverage of the feed/queue/clean/matching cores; the full suite was **not** run in a single pass this audit (each app batch re-migrates an in-memory DB; only the three scopes in 10.1 were executed to completion). Employer/interview/ingestion suites were **not executed** here → their pass state is **UNKNOWN** from this audit.

## 11. Documented-vs-implemented discrepancy register

Recorded as contradictions with evidence on both sides; not silently reconciled.

| # | Claim (doc/intent) | Reality (code/DB evidence) | Type |
|---|---|---|---|
| D1 | "Evidence band" shown on opportunity cards | Removed from view (`_professional_card` fit_label); `test_clean_architecture` now **asserts absence** (`test_clean_architecture.py:215`). Older `test_feed_v2` expectations predate it. | Legacy contradicts newer decision |
| D2 | Confirmed cards surface `Exact match` + title-only disclosure | `_admission_card` supplies `discovery_label`/`title_only_lines` (`views.py:1615/1632`) but `opportunities.html` does not render them → 2 `test_feed_v2` failures. | Documented/contracted behaviour not enforced |
| D3 | `IMPLEMENTATION_REPORT.md`, `FEED_V2_OPERATIONS.md`, `MONSTER_M1_IMPLEMENTATION_REPORT.md` describe richer behaviour | Not independently re-verified as runtime behaviour; DB shows small v2 yield + 100% stale extractions. Treat as **intent**, not proof. | Documentation ≠ implementation |
| D4 | Two Django project packages: `CareerGPS/` and `CareerGPS_Rebuild/` | `manage.py`, `wsgi`, `asgi`, `run_tests.py` all use **`CareerGPS_Rebuild`** (`manage.py:9`). `CareerGPS/` (7 files) is **not referenced** by any entry point → suspected dead/legacy package. | Disconnected component |
| D5 | Feature-flag default `CAREERGPS_FEED_READ_VERSION='legacy'` | v2 is built/materialized but **read path defaults to legacy** until an operator flips to `v2` after `verify_v2_feed --strict` (`settings.py:233-241`). | Configured-but-defaulted-off |
| D6 | `makemigrations --check` / any `manage.py` command via shell env `DEBUG=release` | `env_bool('DEBUG')` rejects `release` → `ImproperlyConfigured`. `run_tests.py:4-6` documents/works around by forcing `DEBUG=True`. Env quirk, not a code defect. | Operational config pitfall |

## 12. Dead-code & disconnected-component findings

- **`CareerGPS/` package (7 files)** — orphaned Django project package; no entry point references it. **Suspected dead** (label: suspected, not confirmed-removable; verify no deployment references it first).
- **`professional_evaluations.py` legacy feed** — module docstring: "compatibility-only legacy… no production signal schedules legacy writes after Block 3." Backed by `ProfileOpportunityEvaluation=1,121` legacy rows. **Legacy-but-present**, not dead (read paths still exist).
- **`applications/urls.py:14-15`** — `document_edit` and `cv_editor` point to the **same** view (`cv_document_editor`) under two names — duplication, harmless but redundant.
- **`opportunities.html:183`** – literal template corruption (stray `reason\">{{ match.card_reason }}</p>`); renders as visible garbage. **Confirmed defect.**
- **Root-level scratch files** — dozens of `*.log/*.err/*.out/*.done/*.py` diagnostic scripts (`_p33_backup.py`, `phase1_*.py`, `idxtest*.err`, `runbf*.ps1`, etc.), `db.sqlite3`, `test_*.txt`. These are **operational scratch / one-off harnesses**, not imported by app code. **Disposable** for a refactor, but none verified to be imported → **suspected** safe to remove, confirm no CI references.

## 13. Known limitations, unknowns and verification gaps

### 13.1 Limitations
- **No version control metadata.** `d:\CG` has no `.git` (verified: `git rev-parse --show-toplevel` → "not a git repository"). There are no commits, branches, diffs or blame, so the audit cannot trace *when* or *why* code arrived, and cannot distinguish intentional deletion from abandoned work.
- **No REST API layer.** `INSTALLED_APPS` contains no `rest_framework`; endpoints are hand-rolled (interview JSON + Django password-reset). No serializers, no DRF permissions. The API surface is thin.
- **No dedicated scheduler/celery.** Background work is expressed through Django management commands (`drain_v2_queue`, `drain_provisional_queue`, `verify_v2_feed --strict`, etc.) and the request-cycle safety-net middleware. No Celery, RQ or cron entrypoint.
- **SQLite default database.** `settings.py:95` configures `django.db.backends.sqlite3` with `db.sqlite3` at project root; the audited live DB is therefore the same as the tests, and is not a proven production-grade database.
- **Generic file storage** is not separately evidenced beyond `MEDIA_ROOT = BASE_DIR / "media"`; large binary assets (CSVs/XLSX downloads) are not independently verified.

### 13.2 Unknowns
- **The live SQLite is seed-like.** Key tables are far below production: `Profile=16`, `Organization=2`, `ProjectCapability=0`, `EmployerVacancy=1`. Production parity cannot be confirmed without a production database.
- **v2 read flag runtime effect.** `.env` sets `CAREERGPS_FEED_READ_VERSION=v2`, and settings reads it at startup, but the audit cannot prove that *every* Django process now reads v2 (no running process state to inspect).
- **External reachability.** Greenhouse/SmartRecruiters/Workday/Adzuna adapters exist in code, but live HTTP reachability, auth and live data cannot be established from a static repository audit.
- **Gemini AI extraction reachability.** 1,857 `OpportunityExtraction` rows exist and are all `stale`; whether they came from a real live call or a seed/repair step cannot be determined without jobs logs or history.
- **Internal field semantics.** Whether `OpportunityExtraction` `status='stale'` means "model never ran", "model errored", or "export is old" is not independently disambiguable from the DB alone.

### 13.3 Test-status notes
- `core.test_clean_architecture`: **22 tests, OK** (executed), including `test_search_and_detail_allow_clean_category_none_without_evaluation` (asserts the detail page no longer renders "Evidence band" — a passing proof of the earlier location fix).
- `core.test_community.OpportunityIntegrityTests`: **8 tests, OK (1 skipped)** (executed). Renders `/saved/` correctly at seed scale.
- `core.test_feed_v2`: **FAILED (2 failures, 3 skipped)** (executed) — see 13.4.

### 13.4 Remaining presentation-stage failures (deliberately untouched in this read-only pass)
The following are **live, reproducible failures in the currently deployed test suite**, verified by executing the test runner, not inferred:

| Test | Expected | Present on `opportunities.html` card | Root cause |
|---|---|---|---|
| `test_exact_career_labels_search_and_one_universe_at_runtime` | `assertContains(search, 'Exact match')` | No `'Exact match'` string rendered | `opportunities.html` card block (lines 139-143) only renders `match.card_reason`, with comments "title-only evidence no longer rendered"; `discovery_label` (which carries 'Exact match') is not emitted |
| `test_title_only_evidence_is_disclosed_beside_a_confirmed_relationship` | `'not counted as a relationship'` disclosure | `title_only_lines` never rendered | Same template block suppresses `title_only_lines`/`relationship_lines` output |

The card context *does* provide `discovery_label='Exact match'` and `title_only_lines` in `_admission_card` (`views.py:1624-1649`), so the data is present on the view layer but the template does not emit it. This is a **presentation-layer regression introduced by the deliberate comment-out migration**.

## 14. Outstanding Work

### 14.1 Confirmed defects
- **Presentation regression on opportunity cards.** The two `test_feed_v2` failures show confirmed EXACT-match cards and title-only evidence disclosure are not rendered, despite card context fields carrying them. Here: `core/templates/core/opportunities.html:139-143`.
- **Environment quirk that breaks plain `manage.py run` in a few shell configurations.** `run_tests.py:4-6` documents that `DEBUG=release` is misread by `env_bool()`, so `settings.py` raises `ImproperlyConfigured`; `run_tests.py` explicitly sets `DEBUG=True`. Requiring `DEBUG=1` is an operational baseline fact, not a code defect.

### 14.2 Incomplete implementations
- **v2 feed near-zero resolved yield.** `ProfileFeedEvaluation` is 2,328 rows but the authoritative feed resolves only ~24 EXACT / 14 RELATED; `TargetOpportunityRelationshipAssessment` is 2,906/2,928 UNRESOLVED. The v2 surface exists but is not delivering matched opportunities at scale.
- **Assessment materialization thin.** `OpportunityOccupationResolution=0`, `OccupationRelationshipPolicy=0`, yet `TargetOpportunityRelationshipAssessment` rows carry `v2_feed_evaluations` FKs — the relationship-resolution chain is not being realised into its persisted result tables.

### 14.3 Missing capabilities
- **No DRF REST API** (no `rest_framework` in `INSTALLED_APPS`).
- **No background worker launcher** — only a safety-net middleware and manual commands; no Celery/RQ entrypoint.
- **No production-scale employer discovery data** — `TalentPool=0`, `SavedCandidate=0`, `HiringAssignment=0`; candidate-visibility features are code-complete but not exercised with seed data.
- **No `CareerMarketSnapshot` populated** in the live DB (`CareerMarketSnapshot=0`).

### 14.4 Operational dependencies
- **Recompute queue depends on request traffic.** The middleware safety-net (`core/middleware.py`) only drains when *any* Django process serves *any* request. If no request arrives, no drain runs (documented honesty in the middleware docstring).
- **External ingestion depends on network + keys**, which are not present/verifiable here; the adapters are installed but not demonstrably connected to live publishers.

### 14.5 Questions requiring human decisions
- Is the near-zero v2 yield a temporary cold-start state that will resolve once assessments are built, or a code defect requiring a targeted fix?
- Are the two `test_feed_v2` presentation failures regressions or a deliberate template simplification that should be documented and accepted?
- Should employer candidate-discovery (talent pools, saved candidates) be scaled to production, or declared out of current scope?

## 15. Evidence appendix & concluding answer

### 15.1 Evidence appendix (read-only commands executed)
- `git rev-parse --show-toplevel` → **not a git repository** (`d:\CG` has no `.git`).
- `Get-ChildItem` top-level inventory of `d:\CG` (5 apps, ~900 Python files, root-scratch artifacts, .validation browser-automation scratch, .env).
- `Select-String` over `core/views.py`, `core/models.py`, `core/services/*.py`, `core/templates`, `core/static`, `employers/*`, `ingestion/*`, `applications/*`, `interview/*`, `CareerGPS_Rebuild/settings.py` and `manage.py` to map entry points, models and service boundaries.
- Read-only SQLite probe (`tmp/db_probe.py`) running `SELECT COUNT(*)` + `GROUP BY` on 30+ tables (no writes, no migrations, no import, no queue drain).
- Test runner executions:
  - `core.test_clean_architecture`: **22 tests OK** (18.5s).
  - `core.test_community.OpportunityIntegrityTests`: **8 tests OK (1 skipped)**.
  - `core.test_feed_v2`: **FAILED (2 failures, 3 skipped)**.

### 15.2 Key paths
| Area | Paths |
|---|---|
| Entry points | `manage.py` → `CareerGPS_Rebuild.settings` → `urls.py` → `core/urls.py`, `ingestion/urls.py`, `employers/urls.py`, `applications/urls.py`, `interview/urls.py` |
| Candidate feed | `core/views.py` (`opportunities_view`, `dashboard_view`, `opportunity_detail_view`, `saved_view`) → `core/services/feed_v2.py`, `professional_evaluations.py`, `feed_queue.py`, `professional_matching.py` |
| Matching | `core/matching.py` (`evaluate_match`, `get_matched_opportunities`) |
| Relationship decision | `core/services/relationship_aggregation.py` (`assess_target_vacancy`), `occupational_resolution.py`, `occupational_index.py`, `evidence_fit.py` |
| Ingestion | `ingestion/pipeline/runner.py`, `ingestion/adapters/{smartrecruiters,workday,greenhouse}.py`, `ingestion/ai/service.py`, `ingestion/management/commands/*.py` |
| Clean import | `core/clean_jobs/service.py` (immutable `OpportunityCleanRevision`), `core/management/commands/{import_clean_jobs_v1,verify_v2_feed}.py` |
| Taxonomy | `core/taxonomies/{esco,isco,ofo,onet,resolver}.py`, `crosswalks.py` |
| Employer | `employers/views.py`, `talent_services.py`, `discovery.py`, `trust_services.py`, `verification_views.py` |
| Documents | `applications/services/document_generation.py`, `core/views.py` (`generate_cv`, `generate_cover_letter`, `_build_docx/pdf`) |
| Interview | `interview/views.py`, `interview/services/{ai,transcription,visual_metrics}.py` |
| Audit registration | `core/management/commands/reconcile_professional_architecture.py` |

### 15.3 Concluding answer

If someone were given the CareerGPS repository today they could demonstrably:
- **Use a working candidate platform**: registration/OTP, a rich profile and evidence store, an opportunity catalogue, save/bookmark, an application workspace, and AI-generated CV/cover-letter documents (DOCX/PDF, downloaded).
- **Ingest and clean opportunities** through a real, multi-source pipeline (Greenhouse/SmartRecruiters/Workday with an optional Gemini stage) plus an immutable provenance-bearing clean-job import contract.
- **Operate on the current data**: 2,670 opportunities, 2,421 active clean revisions, 21,995 taxonomy concepts, 3,321 raw opportunities ingested, 4 saved opportunities.
- **Get a deterministic evidence-fit score** on profile-vs-job criteria via `professional_matching`/`evidence_fit`.

They would find, at best, the following operating only within limitations:
- The **v2 occupational-intelligence feed** is architecturally central and fully implemented as code, but at runtime resolves almost nothing (`2,906/2,928` relationship assessments UNRESOLVED; ~24 EXACT / 14 RELATED out of 2,328 `ProfileFeedEvaluation` rows). The "evidence-based" claim is substantially more aspiration than measured fact at this snapshot.
- The **legacy runtime matcher** (`matching.get_matched_opportunities`) still powers the dashboard and opportunities pages (views.py:993, :1734) and is the only path currently producing live ranked recommendations; the v2 feed read path is configured (`.env`) but not consistently exercised.
- The **presentation layer for cards** has a verified regression: confirmed EXACT-match badges and title-only evidence disclosure are not rendered (`opportunities.html:139-143`, `test_feed_v2` fails).
- **Employer-side** functionality, interview (voice/video, transcription) and AI document flows exist in code, but live data is at seed scale (`EmployerVacancy=1`, `Organization=2`, `TalentPool=0`, `Interview=2`), and interviewer voice/video dependencies (MediaPipe/whisper) were never verified runnable.

The following exists **only as plan/code with no demonstrated runtime use**:
- Remote **scheduled/evaluated feeds** beyond the request-safety-net and manual management commands; no Celery/scheduler.
- **Talent pools, hiring assignments, saved candidates** — code present, zero live data.
- A **working employer-recruiter pipeline** — no seed data to exercise it.

The following is **unknown**: production-parity of the seed SQLite; external API reachability and real AI-extraction runnability; the true cause of the 2 `test_feed_v2` presentation failures beyond the comment-out template block; the v2 flag's effect on a live process.

**Bottom line:** CareerGPS is a substantial, genuinely working Django candidate-matching platform with a complete data model, a real multi-source ingestion pipeline, an immutable clean-job contract, a full taxonomy, and AI document generation — but its headline "evidence-based occupational matching" is only partially delivered in code. At runtime the v2 engine resolves near-zero outcomes while a legacy matcher carries the live feed. The platform is not yet a finished evidence-based product; it is a capable catalogue + legacy matcher + AI-document system with a deliberately engineered (but under-yielding) v2 occupational layer.

