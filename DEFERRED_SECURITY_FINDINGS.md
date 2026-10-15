# Deferred security findings

These findings from the inspection remain outside the authorized four-feature
implementation. Interview remains parked; none of its files were changed.

| Finding | Existing area | Remaining work |
| --- | --- | --- |
| Interview question / answer JSON is embedded with `safe` inside a script | `interview/templates/interview/live.html` | Use Django `json_script` and parse the resulting element; test script-closing input. |
| Generated documents and recordings use media paths without a complete authenticated delivery boundary | Project media serving, applications, interview | Add owner-checked downloads and production media access controls. Deployment protection was not established by the audit. |
| Registration treats any inactive account as unverified and can reactivate an intentionally disabled account after OTP | `core/views.py` | Distinguish pending verification from administrative suspension. |
| OTP rate limits depend on a resettable browser session | `core/views.py` | Add durable account / network abuse controls without breaking verification. |
| Saved-opportunity matching omits optional certification/language loading | `core/views.py` | Bring saved comparisons into parity with catalogue/detail matching in a separate focused change. |
| Application creation can run on GET | `applications/` | Review mutation boundaries while preserving one workspace per user/opportunity. |
| Document numeric guards do not validate every prose claim; cover-letter ReportLab text is not consistently escaped | Application document services and legacy rendering helpers | Add targeted claim validation and safe rendering. |
| Interview completion / answer editing is not an immutable, idempotent workflow | `interview/` | Review when the parked interview work resumes. |
| Legacy redirect paths can trust `HTTP_REFERER` | `core/views.py` | Validate redirect destinations at each remaining legacy action. New report, preference and career actions use fixed internal destinations. |
| Old interview prototype and unused document helpers remain | Archived interview directory; `core/views.py` | Separate cleanup with regression coverage. |

This phase addresses notification destination validation, notification read mutations,
report ownership/privacy, explicit moderation events, suppression surviving ingestion,
expired vacancy discovery and extraction invalidation.
It does not claim to resolve the findings above.
