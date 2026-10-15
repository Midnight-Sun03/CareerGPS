# Privacy / POPIA Compliance Actions — CareerGPS

Date: 2026-09-26. Scope: privacy-notice accuracy, in-app legal access, and the operator-facts
production gate. Validated by the full test suite (see section 4).

## 1. Completed in this pass

| # | Change | Location |
|---|--------|----------|
| 1 | Privacy notice now discloses that **signed-in pages also request fonts from external hosts**, and that those requests disclose the visitor's IP address/browser details to the font host (previous copy said only "public pages"). | `core/legal.py` (privacy §"Storage, cookies, and security") |
| 2 | `LEGAL_PRIVACY_VERSION` bumped `2026-09-16.1` → `2026-09-26.1`. The content SHA-256 is computed at import time, so new registrations store the new version + hash automatically. | `CareerGPS_Rebuild/settings.py`; `core/legal.py::current_document` |
| 3 | Candidate app shell now renders the shared legal footer (Privacy, Terms, About, Contact, Accessibility) at the end of the content column. | `templates/dashboard_pages.html` (includes `partials/public_footer.html`); `core/static/core/css/main.css` |
| 4 | Employer shell renders the same footer. | `employers/templates/employers/base.html`; `employers/static/employers/employers.css` |
| 5 | Production launch gate (`careergps.W001`) now also requires the Information Officer field. | `core/checks.py` |
| 6 | Regression test: signed-in dashboard, settings, and employer home must expose the legal links. | `core/test_legal_privacy.py::PublicLegalPagesTests.test_signed_in_shells_offer_legal_links` |

Verified live: privacy version `2026-09-26.1`; content hash prefix `80a35b79…`; corrected
fonts sentence present in the rendered document sections.

Note: users who accepted the earlier privacy version keep their original versioned
`LegalAcceptanceEvent`; there is still **no re-consent flow** when a version changes (section 3).

## 2. Launch blockers — owner input required

1. **Operator facts are still unset.** `CAREERGPS_OPERATOR_NAME`, `_LEGAL_STATUS`,
   `_REGISTRATION_NUMBER`, `_PHYSICAL_ADDRESS`, `_LEGAL_SERVICE_ADDRESS`, `_TELEPHONE`,
   plus `CAREERGPS_CONTACT_EMAIL`, `CAREERGPS_PRIVACY_EMAIL`, `CAREERGPS_INFORMATION_OFFICER`
   (documented in `.env.example`). Until supplied, `/privacy/`, `/terms/` and `/contact/`
   render "Not configured for local development" (DEBUG) or "Not supplied", and
   `careergps.W001` warns in production mode.
2. **Information Officer** designation/registration with the Information Regulator to confirm.
3. **AI + email processors:** confirm DPAs and the POPIA s72 cross-border transfer basis for
   Google Gemini, Groq, OpenAI and the SMTP provider (`.env` already has `EMAIL_HOST` set).
4. **Retention schedule (POPIA s14):** approve periods per record class — profile evidence,
   interview audio, generated documents, employer verification documents, submitted
   applications, moderation/audit history, notifications, privacy requests.

## 3. Open engineering items from the audit (not changed in this pass)

- **No self-service account deletion or data export.** The manual `PrivacyRequest` workflow
  (statuses, staff notes, 5/hour per-user limit) exists in `core/legal_views.py`. A hard
  `user.delete()` is blocked by `PROTECT` relations (legal acceptance, hiring history,
  verification authorship), so deletion must deactivate + anonymise + selectively erase.
  Interview media files already clean up on cascade (`interview/signals.py`).
- **No re-consent/re-acknowledgement** when `LEGAL_*_VERSION` changes.
- **Legacy/unused schema:** `InterviewQuestion.video_file` and the `eye_contact_score`,
  `posture_score`, `gesture_score`, `presence_score` columns (session and question level) are
  written only by tests — remove by migration (data minimisation) or document as legacy.
- **Session/admin hardening:** no `SESSION_COOKIE_AGE` (Django default 14 days), admin at
  `/admin/` with no MFA, `SECURE_HSTS_SECONDS` defaults to 0.
- **Two document tools bypass the AI gateway:** direct `google-genai` calls in
  `core/views.py` (`cv_enhancer_view`, `cover_letter_generator_view`).
- **Keep media private in deployment:** `MEDIA_URL` is declared but not routed; raw interview
  audio must stay reachable only through the owner-scoped `interview_media` view.
- **Public request channel for non-users** (the contact form POST currently requires sign-in).
- **Information Regulator complaint details** (link/e-form) in the privacy notice.
- **PAIA manual decision, employer/candidate data-protection roles statement, breach runbook.**

## 4. Validation performed (2026-09-26)

Interpreter note for this machine: `E:\CG\.venv` has no packages installed, and
`E:\CG\.venv_old`'s launcher points at a removed base Python. Working command:

```powershell
cd E:\CG
$env:PYTHONPATH='E:\CG\.venv_old\Lib\site-packages'
$py = "$env:APPDATA\uv\python\cpython-3.12.14-windows-x86_64-none\python.exe"
& $py manage.py check
& $py manage.py test core.test_legal_privacy -v 2   # 10 tests, OK
& $py manage.py test -v 1                           # Ran 799 tests, OK (skipped=45), 669s
```

Logs retained: `full-tests.log`, `full-tests.err.log`.

Pre-existing environment messages seen during the run (not caused by this change): semantic
model warmup falls back because the local model is not prepared
(`manage.py prepare_semantic_model`), and `careergps.W001` warns whenever `DEBUG=False`
without operator facts.

## 5. Recommended next steps

1. Supply the operator facts; confirm the legal pages render real details and `W001` clears.
2. Approve the retention schedule and deletion semantics, then implement export + deletion UX.
3. Add the re-consent flow keyed on the document content hash.
4. Remove the legacy media/score columns.
5. Session expiry, admin hardening, HSTS.
6. Route document generation through the AI gateway.
7. Regulator complaint details, PAIA decision, employer data-protection statement, breach runbook.
