"""Versioned public copy and its canonical acceptance identifier.

Keep the versions in settings and change one when the corresponding copy changes.
The hash also covers the operator facts displayed alongside the document.
"""

import hashlib
import json
from dataclasses import dataclass

from django.conf import settings


@dataclass(frozen=True)
class LegalDocument:
    kind: str
    title: str
    version: str
    sections: tuple
    content_sha256: str


TERMS_SECTIONS = (
    ("The service", (
        "CareerGPS helps candidates organise professional evidence, discover opportunities, prepare application documents, practise interviews, and track applications. Approved employer organisations can publish vacancies, review submitted applications, use opt-in talent discovery, send invitations, and coordinate hiring work.",
        "CareerGPS provides information and decision support. It does not itself make a final employment decision or guarantee an interview, job, match, or outcome.",
    )),
    ("Accounts and accurate information", (
        "Use your own account, keep its credentials private, and keep the information you submit as accurate and current as you reasonably can. Do not impersonate another person or organisation, submit fabricated candidate evidence, or use another person's account without authority.",
        "Organisation owners and members must be authorised to act for their organisation. Available employer actions depend on the account's role, organisation status, and CareerGPS verification and access controls.",
    )),
    ("Candidate and employer actions", (
        "Saving an opportunity or opening an application workspace does not submit an application to an employer. An employer invitation is an invitation to consider a vacancy; accepting it opens a planning workspace. A separate submission action shares the selected CV and optional cover-letter versions with the receiving organisation.",
        "Employers remain responsible for their vacancy information, access by their authorised members, communications, and human hiring decisions. CareerGPS matching and evidence comparisons do not automatically hire, reject, or advance a candidate.",
    )),
    ("Opportunities and external links", (
        "CareerGPS includes listings from supported external sources and vacancies published by approved organisations. An external listing may change, close, contain errors, or lead to another website. Check the vacancy and that site's terms and privacy information before applying. CareerGPS does not control an external site's practices.",
    )),
    ("AI and practice tools", (
        "Some CV, cover-letter, interview-question, and interview-feedback content is generated with external AI services. Matching and employer evidence comparisons use the information available to CareerGPS. Outputs can be incomplete or incorrect; review generated claims and evidence before using them.",
        "Interview coaching is practice feedback, not a measure of personality, psychological state, employability, or a hiring decision. Optional camera and voice observations are candidate coaching information and are not employer ranking evidence in the current service.",
    )),
    ("Appropriate use", (
        "Do not post unlawful, abusive, discriminatory, misleading, or fraudulent material; attempt to access another user's private information; scrape or overload the service; evade access controls; or use CareerGPS to collect candidate information outside the permissions shown to you.",
        "CareerGPS may review reported material, restrict an organisation or account, or remove a listing when needed to operate the service and address misuse. Records of prior applications, verification, moderation, or audit activity may need separate handling; a visibility change does not erase them.",
    )),
    ("Content and availability", (
        "You remain responsible for the material you enter and for reviewing generated documents before use. CareerGPS uses submitted information to provide the features you choose, as described in the Privacy Notice. This page does not transfer ownership of your original material to CareerGPS.",
        "Features and availability can change as the service develops. Contact CareerGPS through the Contact page if you need help with the service or your personal information.",
    )),
    ("Privacy", (
        "The Privacy Notice explains current personal-information handling, including AI providers, interview media, employer access, browser storage, and privacy-request options. It is a separate document from these Terms.",
    )),
)


PRIVACY_SECTIONS = (
    ("What CareerGPS does and who operates it", (
        "CareerGPS is a career and hiring support service for candidates and approved employer organisations. The responsible operator's verified public details appear below when configured. Missing local-development details are not a substitute for production contact information.",
    )),
    ("Information you provide", (
        "Account creation uses your name, email address, and password. Your candidate profile can contain contact details, education, work and project history, skills, certifications, achievements, volunteering, languages, links, career goals, preferred locations, and other career preferences. You may provide CV text directly or upload a PDF/DOCX for local text extraction during a document request; the active flow does not keep a separate permanent copy of that raw upload.",
        "You may save opportunities, create application workspaces and notes, edit generated CVs or cover letters, submit selected document versions to an employer, write interview answers, record spoken answers, or submit stories and reports. Employers provide organisation, member, vacancy, verification, and hiring information.",
    )),
    ("Information CareerGPS creates or records", (
        "CareerGPS stores generated CV and cover-letter content and files, opportunity matches and evidence explanations, application and stage history, frozen copies of documents you submit to a receiving employer, interview questions and feedback, notifications, and moderation and employer audit records. It may derive an experience index and job-related matching information from confirmed profile evidence. External opportunity records come from public or supported sources.",
    )),
    ("Why information is used", (
        "Account and session information enables sign-in, email verification, password reset, and service security. Professional evidence and preferences support profile features, opportunity recommendations, documents, interview practice, and the employer access you choose. Hiring records support submitted applications and organisation workflows. Reports, verification evidence, and audit events support moderation and platform operation.",
        "Name, email, and password are needed to create an account. Other profile and feature information is generally supplied when you choose to use those features; missing information can limit matching, document generation, or interview context. Browser camera analysis is optional.",
    )),
    ("AI providers and local processing", (
        "Depending on the feature and configured provider availability, CareerGPS may send a structured request to Google Gemini, Groq, or OpenAI. CV and cover-letter writing may include selected candidate facts, selected supplied CV text, job information, and your notes. Interview question generation may include job information, professional profile facts, and relevant generated-document content. Final evaluation includes the interview questions and your written or corrected answer text.",
        "The gateway tries configured providers in order and only continues after eligible failures. More than one provider can therefore process substantially the same request. CV upload text extraction, semantic fact selection, PDF/DOCX rendering, spoken-answer transcription with faster-whisper/VAD, and camera frame analysis run locally in the reviewed application flow. Current code does not use Affinda for CV parsing.",
        "Provider processing locations, provider-side retention, contractual protections, and any international-transfer arrangements require operator review before public production use; this notice does not claim facts the repository cannot establish.",
    )),
    ("Written and spoken interview practice", (
        "Written answers are stored for your practice and feedback. In spoken mode, the browser uploads each recorded answer and CareerGPS stores the raw audio. Local transcription produces a raw transcript and word timing information; a browser-supplied provisional transcript may also be stored. You can review and correct the transcript. The raw transcript remains separate, while your corrected text is used for final AI evaluation.",
        "Local voice coaching records delivery measurements such as duration, pace, pauses, and filler words when available. Raw audio and voice-delivery metrics are not sent to employers as ranking evidence by the current workflow. Interview media access is restricted to the interview owner in the application.",
    )),
    ("Optional camera practice", (
        "Camera analysis requires a separate choice. The reviewed application sends webcam frames to a MediaPipe worker in your browser; it does not upload or store frames, video, or face/pose landmark streams. It sends compact measurements and coaching results, such as framing, head orientation, and optional visible shoulder, hip, or gesture observations, to CareerGPS for your feedback.",
        "Visual metrics are not included in the final AI interview-evaluation prompt or employer ranking. The active system does not infer emotion, personality, psychological state, deception, eye contact, competence, or employability from camera analysis. You can use audio-only practice.",
    )),
    ("Employer visibility and applications", (
        "Talent discovery is private by default. You can opt in to discovery by approved active organisations, choose a city/region to share, allow saving to talent pools, allow purpose-bound contact reveal, permit prior-applicant rediscovery, or block an organisation. Discovery cards do not automatically expose contact details or submitted documents. Contact reveal is gated and recorded.",
        "An invitation does not submit an application. When you separately submit an application, CareerGPS freezes the selected CV and optional cover-letter versions for the receiving organisation. Its authorised members can access that application and its submitted documents. Changing discovery settings or blocking an organisation does not automatically remove legitimate earlier application and hiring history. Practical fit displays logistics; evidence matching is decision support, not an automatic hiring decision.",
    )),
    ("Other recipients and outside sites", (
        "The receiving employer organisation can access a submitted application according to its member permissions. Approved organisations can access only candidate discovery information currently permitted by the candidate and the platform gates. Moderation and verification staff can access information needed for their assigned work. External application links lead to sites with their own practices, which CareerGPS does not control.",
    )),
    ("Storage, cookies, and security", (
        "CareerGPS uses Django session and CSRF cookies for account and request functions. The careergps-a11y-settings localStorage entry keeps display and reading choices on your browser/device. The reviewed application has no optional analytics, advertising, tracking-pixel, or marketing cookies, so it does not present an optional-tracking consent banner. Both public and signed-in pages request fonts from external hosts; those requests disclose your browser's network details, including your IP address, to the font host. The application adds no analytics, advertising, or tracking component of its own to those requests.",
        "The application uses authentication, ownership and organisation access checks, CSRF protection, and private download routes. No online service can promise perfect security, and deployment safeguards must be verified separately.",
    )),
    ("Retention and your choices", (
        "CareerGPS currently keeps information for the purposes for which it is used; the owner has not approved fixed retention periods. Hiring submissions, verification evidence, moderation, security, and audit records may need different treatment from editable candidate records. Deleting an interview, withdrawing an application, or disabling discovery does not mean every related record or provider copy is immediately erased.",
        "You can edit profile sections, correct interview transcripts, change employer visibility, and block organisations. Use the Contact page while signed in to request access, correction, deletion, restriction or objection, an export, or another privacy enquiry. A request starts a review; it does not automatically erase protected records. The operator must establish handling arrangements and verified contact details before production launch.",
        "Settings offers two self-service data rights. Download My Data produces a ZIP archive of your account, profile, evidence, applications, interviews, generated documents and account records, and it states its own limitations in the archive manifest. Delete Account requires your current password followed by a final confirmation, and then takes effect immediately: your profile, practice interviews and their recordings, generated documents, saved opportunities and notifications are erased, every signed-in session ends, your name and email address are removed from the account, and the email address is released so you can register again. CareerGPS emails a confirmation to the address on the account. Records an employer already received for an application you submitted, your acceptance of these documents, and privacy, moderation and security records are retained in limited form without your name or contact details. The owner has not yet approved fixed retention periods for those retained records, so this notice will be updated once that review is complete.",

    )),
    ("Decisions and complaints", (
        "Opportunity matching and employer evidence comparison can rank or explain available evidence; AI-generated content can be wrong or incomplete. CareerGPS does not automatically make final employment decisions, hire, reject, or advance candidates through its current matching, interview, camera, or voice systems. Employers change hiring stages through authorised human actions.",
        "For privacy questions, use the Contact page and the configured operator contact details. You may also raise concerns with South Africa's Information Regulator. The operator must confirm its own privacy contact and the final complaint information before public launch.",
    )),
)


def current_document(kind):
    if kind == "terms":
        title, version, sections = "Terms of Service", settings.LEGAL_TERMS_VERSION, TERMS_SECTIONS
    elif kind == "privacy":
        title, version, sections = "Privacy Notice", settings.LEGAL_PRIVACY_VERSION, PRIVACY_SECTIONS
    else:
        raise ValueError("Unknown legal document")
    canonical = json.dumps({
        "kind": kind, "title": title, "version": version,
        "sections": sections, "operator": settings.CAREERGPS_OPERATOR,
    }, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return LegalDocument(kind, title, version, sections,
                         hashlib.sha256(canonical.encode("utf-8")).hexdigest())


def public_operator_facts():
    labels = (
        ("name", "Operator name"), ("legal_status", "Legal status"),
        ("registration_number", "Registration number, if applicable"),
        ("physical_address", "Physical address"),
        ("legal_service_address", "Legal-service address"),
        ("telephone", "Telephone"), ("contact_email", "Contact email"),
        ("privacy_email", "Privacy email"),
        ("information_officer", "Information Officer"),
    )
    return [(label, settings.CAREERGPS_OPERATOR.get(field) or
             ("Not configured for local development" if settings.DEBUG else "Not supplied"))
            for field, label in labels]
