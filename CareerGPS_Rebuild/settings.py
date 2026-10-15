"""Settings for the active CareerGPS Django project."""
import mimetypes
import os
import sys
from pathlib import Path

import dj_database_url
from django.core.exceptions import ImproperlyConfigured
from dotenv import load_dotenv

# The vendored MediaPipe Tasks Vision runtime is served from Django static
# files and loaded through a worker dynamic import(). Module scripts require
# a JavaScript MIME type; Python's mimetypes table maps .mjs to text/plain,
# which browsers reject under strict MIME checking. Register the correct
# type here so every static server path (runserver, WhiteNoise, test client)
# serves .mjs as JavaScript.
mimetypes.add_type("text/javascript", ".mjs", strict=True)

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")


def env_bool(name, default=False):
    raw_value = os.environ.get(name)
    if raw_value is None:
        return bool(default)

    value = raw_value.strip().lower()
    if value in {"1", "true", "yes", "on"}:
        return True
    if value in {"0", "false", "no", "off"}:
        return False
    raise ImproperlyConfigured(
        f"{name} must be one of: 1, true, yes, on, 0, false, no, off."
    )


def env_list(name, default=""):
    return [value.strip() for value in os.environ.get(name, default).split(",") if value.strip()]


DEBUG = env_bool("DEBUG", True)
SECRET_KEY = os.environ.get("SECRET_KEY")
if not SECRET_KEY:
    if DEBUG:
        SECRET_KEY = "django-insecure-careergps-local-development-only"
    else:
        raise ImproperlyConfigured("SECRET_KEY must be set when DEBUG is disabled.")

ALLOWED_HOSTS = env_list("ALLOWED_HOSTS", "localhost,127.0.0.1")
CSRF_TRUSTED_ORIGINS = env_list("CSRF_TRUSTED_ORIGINS")

INSTALLED_APPS = [
    "django.contrib.admin", "django.contrib.auth", "django.contrib.contenttypes",
    "django.contrib.sessions", "django.contrib.messages", "django.contrib.staticfiles",
    "core.apps.CoreConfig", "interview.apps.InterviewConfig", "ingestion.apps.IngestionConfig",
    "applications.apps.ApplicationsConfig", "employers.apps.EmployersConfig",
]
MIDDLEWARE = [
    # Application-owned queue safety net: re-arms the drain on request cycles
    # so PENDING tasks survive a dev-server reload or the exit of the process
    # that handled the original save.  Outermost so no inner middleware
    # short-circuit can skip it.
    "core.middleware.QueueDrainSafetyNetMiddleware",
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]
ROOT_URLCONF = "CareerGPS_Rebuild.urls"
TEMPLATES = [{
    "BACKEND": "django.template.backends.django.DjangoTemplates",
    "DIRS": [BASE_DIR / "templates"],
    "APP_DIRS": True,
    "OPTIONS": {"context_processors": [
        "django.template.context_processors.request",
        "django.contrib.auth.context_processors.auth",
        "django.contrib.messages.context_processors.messages",
    ]},
}]
WSGI_APPLICATION = "CareerGPS_Rebuild.wsgi.application"
ASGI_APPLICATION = "CareerGPS_Rebuild.asgi.application"

database_url = os.environ.get("DATABASE_URL")
if database_url:
    DATABASES = {"default": dj_database_url.parse(
        database_url,
        conn_max_age=int(os.environ.get("DB_CONN_MAX_AGE", "600")),
        ssl_require=env_bool("DB_SSL_REQUIRE", not DEBUG),
    )}
else:
    DATABASES = {"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": BASE_DIR / "db.sqlite3"}}

# SQLite contention hardening (eradicates observed "database is locked"
# failures).  The request thread and the background queue-drain thread are
# concurrent connections in one process:
#   * WAL journal mode lets readers and the single writer proceed without
#     blocking each other, so a long catalogue read can no longer abort the
#     drain's COMMIT, and the drain's build transactions can no longer abort
#     a request's on_commit writes.  WAL also recovers automatically after a
#     crashed process (no hot-journal leftovers).
#   * The busy timeout (Python's default is only 5s) makes a writer wait for
#     its turn instead of raising OperationalError("database is locked").
if DATABASES["default"]["ENGINE"] == "django.db.backends.sqlite3":
    DATABASES["default"].setdefault("OPTIONS", {})
    DATABASES["default"]["OPTIONS"]["timeout"] = float(
        os.environ.get("CAREERGPS_SQLITE_BUSY_TIMEOUT", "30"))

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]
LANGUAGE_CODE = "en-us"
TIME_ZONE = os.environ.get("TIME_ZONE", "Africa/Johannesburg")
USE_I18N = True
USE_TZ = True
STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
MEDIA_URL = "/media/"
MEDIA_ROOT = BASE_DIR / "media"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
LOGIN_URL = "login"
LOGIN_REDIRECT_URL = "dashboard"
LOGOUT_REDIRECT_URL = "landing"

if os.environ.get("EMAIL_HOST"):
    EMAIL_BACKEND = "django.core.mail.backends.smtp.EmailBackend"
    EMAIL_HOST = os.environ["EMAIL_HOST"]
    EMAIL_PORT = int(os.environ.get("EMAIL_PORT", "587"))
    EMAIL_USE_TLS = env_bool("EMAIL_USE_TLS", True)
    EMAIL_HOST_USER = os.environ.get("EMAIL_HOST_USER", "")
    EMAIL_HOST_PASSWORD = os.environ.get("EMAIL_HOST_PASSWORD", "")
else:
    EMAIL_BACKEND = "django.core.mail.backends.console.EmailBackend"
DEFAULT_FROM_EMAIL = os.environ.get("DEFAULT_FROM_EMAIL", "noreply@careergps.local")

# Public operator facts are supplied by the owner, not inferred from the brand.
CAREERGPS_OPERATOR = {
    "name": os.environ.get("CAREERGPS_OPERATOR_NAME", "").strip(),
    "legal_status": os.environ.get("CAREERGPS_OPERATOR_LEGAL_STATUS", "").strip(),
    "registration_number": os.environ.get("CAREERGPS_OPERATOR_REGISTRATION_NUMBER", "").strip(),
    "physical_address": os.environ.get("CAREERGPS_OPERATOR_PHYSICAL_ADDRESS", "").strip(),
    "legal_service_address": os.environ.get("CAREERGPS_OPERATOR_LEGAL_SERVICE_ADDRESS", "").strip(),
    "telephone": os.environ.get("CAREERGPS_OPERATOR_TELEPHONE", "").strip(),
    "contact_email": os.environ.get("CAREERGPS_CONTACT_EMAIL", "").strip(),
    "privacy_email": os.environ.get("CAREERGPS_PRIVACY_EMAIL", "").strip(),
    "information_officer": os.environ.get("CAREERGPS_INFORMATION_OFFICER", "").strip(),
}
LEGAL_TERMS_VERSION = "2026-09-16.1"
LEGAL_PRIVACY_VERSION = "2026-09-26.2"

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")
OPENAI_MODEL = os.environ.get("OPENAI_MODEL", "gpt-5.6-terra")
GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "")
GROQ_MODEL = os.environ.get("GROQ_MODEL", "openai/gpt-oss-120b")

# CareerGPS has two Gemini keys:
# - GEMINI_API_KEY: primary/default
# - GEMuINI_API_KEY: fallback
# The alternate GEMINI_API_KEY_FALLBACK name is accepted for compatibility.
# Keep the fallback generic so opportunity extraction and controlled
# CV/cover-letter generation follow the same primary -> fallback
# rule when the provider failure is one another key can realistically solve.
GEMINI_FALLBACK_API_KEY = (
    os.environ.get("GEMuINI_API_KEY", "")
    or os.environ.get("GEMINI_API_KEY_FALLBACK", "")
)
# Backward-compatible alias used by the established Phase 2.4 extractor.
OPPORTUNITY_AI_FALLBACK_API_KEY = GEMINI_FALLBACK_API_KEY

# Phase 2.4 opportunity AI settings are retained for explicit legacy/manual
# extraction and the separate export/import workflow. Source ingestion does not
# use them. Candidate/profile data is never sent to opportunity extraction.
OPPORTUNITY_AI_MODEL = os.environ.get("OPPORTUNITY_AI_MODEL", "gemini-3.5-flash")
OPPORTUNITY_AI_THINKING_LEVEL = os.environ.get("OPPORTUNITY_AI_THINKING_LEVEL", "medium")
OPPORTUNITY_AI_SCHEMA_VERSION = os.environ.get("OPPORTUNITY_AI_SCHEMA_VERSION", "2.4.1")
OPPORTUNITY_AI_PROMPT_VERSION = os.environ.get("OPPORTUNITY_AI_PROMPT_VERSION", "2.4.1")

# Controlled CV / cover-letter writing. CareerGPS supplies candidate facts and
# verified/limited job context; the model is not a source of candidate truth.
DOCUMENT_AI_MODEL = os.environ.get("DOCUMENT_AI_MODEL", OPPORTUNITY_AI_MODEL)
DOCUMENT_AI_THINKING_LEVEL = os.environ.get("DOCUMENT_AI_THINKING_LEVEL", "low")
DOCUMENT_AI_TIMEOUT_SECONDS = int(os.environ.get("DOCUMENT_AI_TIMEOUT_SECONDS", "90"))

# Spoken interview ASR. ``base.en`` on CPU/int8 is the current local-development
# balance between English transcription quality, memory and response time. Model
# loading is process-cached. Local-only mode prevents request-time model downloads.
INTERVIEW_WHISPER_MODEL = os.environ.get("INTERVIEW_WHISPER_MODEL", "base.en")
INTERVIEW_WHISPER_MODEL_PATH = os.environ.get("INTERVIEW_WHISPER_MODEL_PATH", "")
INTERVIEW_WHISPER_DEVICE = os.environ.get("INTERVIEW_WHISPER_DEVICE", "cpu")
INTERVIEW_WHISPER_COMPUTE_TYPE = os.environ.get(
    "INTERVIEW_WHISPER_COMPUTE_TYPE", "int8"
)
INTERVIEW_WHISPER_LOCAL_FILES_ONLY = env_bool(
    "INTERVIEW_WHISPER_LOCAL_FILES_ONLY", True
)

# Open-source semantic relevance helper. Preparation acquires the unchanged
# configured model; document requests load only verified local files.
SENTENCE_TRANSFORMER_MODEL = os.environ.get("SENTENCE_TRANSFORMER_MODEL", "all-MiniLM-L6-v2")
SENTENCE_TRANSFORMER_LOCAL_PATH = os.environ.get(
    "SENTENCE_TRANSFORMER_LOCAL_PATH", str(BASE_DIR / ".local_models" / "semantic")
)

# Warm only in WSGI/ASGI entry points, never AppConfig.ready().
SENTENCE_TRANSFORMER_WARMUP_ON_START = env_bool("SENTENCE_TRANSFORMER_WARMUP_ON_START", True)

# Content-free timing records only; do not enable verbose provider HTTP logging.
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {"document_console": {"class": "logging.StreamHandler"}},
    "loggers": {"careergps.documents": {
        "handlers": ["document_console"], "level": "INFO", "propagate": False,
    }},
}

INGESTION_USER_AGENT = os.environ.get(
    "INGESTION_USER_AGENT",
    "CareerGPSOpportunityBot/1.0 (+https://careergps.alwaysdata.net; contact: admin@careergps.local)",
)
INGESTION_TIMEOUT_SECONDS = int(os.environ.get("INGESTION_TIMEOUT_SECONDS", "20"))
INGESTION_REQUEST_DELAY_SECONDS = float(os.environ.get("INGESTION_REQUEST_DELAY_SECONDS", "1.0"))
INGESTION_MISSING_GRACE_DAYS = int(os.environ.get("INGESTION_MISSING_GRACE_DAYS", "7"))
CACHES = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache", "LOCATION": "careergps-local", "TIMEOUT": 300}}

# Personalized feed cutover is explicit. Keep legacy until the v2 worker is
# deployed and ``verify_v2_feed --strict`` passes in that environment.
CAREERGPS_FEED_READ_VERSION = os.environ.get(
    "CAREERGPS_FEED_READ_VERSION", "legacy"
).strip().lower()
if CAREERGPS_FEED_READ_VERSION not in {"legacy", "v2"}:
    raise ImproperlyConfigured(
        "CAREERGPS_FEED_READ_VERSION must be either legacy or v2."
    )
CAREERGPS_V2_WORKER_SCHEDULED = env_bool("CAREERGPS_V2_WORKER_SCHEDULED", False)
CAREERGPS_V2_TASK_MAX_ATTEMPTS = int(os.environ.get(
    "CAREERGPS_V2_TASK_MAX_ATTEMPTS", "3"))
CAREERGPS_V2_TASK_LEASE_SECONDS = int(os.environ.get(
    "CAREERGPS_V2_TASK_LEASE_SECONDS", "900"))

# Application-owned queue execution safety net (core.middleware).
# On each request, at most once per interval per process, the middleware reads
# the existing EvaluationRecomputeTask table for claimable work and re-arms the
# same coalesced drain the save-time trigger uses.  This is what lets a
# target-change rebuild survive a dev-server autoreload or the exit of the
# process that handled the save: the next request any Django process serves
# picks the work up.  It is NOT a second queue -- one read-gate query plus the
# existing drain entry point.  Disabled under the test runner by default (tests
# drive the drain explicitly); tests can re-enable via override_settings.
TESTING_MODE = "test" in sys.argv[:2]
CAREERGPS_QUEUE_SAFETY_NET = env_bool(
    "CAREERGPS_QUEUE_SAFETY_NET", default=not TESTING_MODE)
CAREERGPS_QUEUE_SAFETY_NET_INTERVAL = float(os.environ.get(
    "CAREERGPS_QUEUE_SAFETY_NET_INTERVAL", "5"))


if not DEBUG:
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SECURE_SSL_REDIRECT = env_bool("SECURE_SSL_REDIRECT", True)
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
    SECURE_CONTENT_TYPE_NOSNIFF = True
    SECURE_HSTS_SECONDS = int(os.environ.get("SECURE_HSTS_SECONDS", "0"))
    SECURE_HSTS_INCLUDE_SUBDOMAINS = env_bool("SECURE_HSTS_INCLUDE_SUBDOMAINS", False)
    SECURE_HSTS_PRELOAD = env_bool("SECURE_HSTS_PRELOAD", False)
    X_FRAME_OPTIONS = "DENY"
