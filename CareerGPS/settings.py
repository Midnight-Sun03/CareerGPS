

import os
from pathlib import Path
from dotenv import load_dotenv
import dj_database_url

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent.parent

# ============================================================
# SECURITY - IMPORTANT FOR PRODUCTION
# ============================================================

# SECURITY WARNING: keep the secret key used in production secret!
# Use environment variable for production
SECRET_KEY = os.environ.get('SECRET_KEY', 'django-insecure-o4n48-mst^z@tljmb=f5afu-fm!#g(cqm&ua+jqpr(+^y5r@4(')

# SECURITY WARNING: don't run with debug turned on in production!
DEBUG = os.environ.get('DEBUG', 'False') == 'True'

# Add your Alwaysdata domain here
ALLOWED_HOSTS = [
    'localhost',
    '127.0.0.1',
    os.environ.get('DOMAIN', 'careergps.alwaysdata.net'),
    'careergps.alwaysdata.net',  # Replace with your actual subdomain
]

# ============================================================
# DATABASE - PostgreSQL (Production)
# ============================================================

def get_database_config():
    """Get database configuration based on environment."""
    # Production: Use PostgreSQL from DATABASE_URL
    if os.environ.get('DATABASE_URL'):
        return dj_database_url.config(
            default=os.environ.get('DATABASE_URL'),
            conn_max_age=600,
            ssl_require=True  # Alwaysdata requires SSL for PostgreSQL
        )
    
    # Development: Use local PostgreSQL
    return {
        'ENGINE': 'django.db.backends.postgresql',
        'NAME': os.environ.get('DB_NAME', 'careergps_db'),
        'USER': os.environ.get('DB_USER', 'postgres'),
        'PASSWORD': os.environ.get('DB_PASSWORD', ''),
        'HOST': os.environ.get('DB_HOST', 'localhost'),
        'PORT': os.environ.get('DB_PORT', '5432'),
    }

DATABASES = {
    'default': get_database_config()
}

# ============================================================
# EMAIL - IMPORTANT FOR DEPLOYMENT
# ============================================================

# For production on Alwaysdata, use their SMTP server
if os.environ.get('PRODUCTION') == 'True':
    # Alwaysdata SMTP configuration
    EMAIL_BACKEND = 'django.core.mail.backends.smtp.EmailBackend'
    EMAIL_HOST = 'smtp.alwaysdata.com'
    EMAIL_PORT = 587
    EMAIL_USE_TLS = True
    EMAIL_HOST_USER = os.environ.get('EMAIL_HOST_USER', '')  # Your alwaysdata email
    EMAIL_HOST_PASSWORD = os.environ.get('EMAIL_HOST_PASSWORD', '')  # Your email password
    DEFAULT_FROM_EMAIL = os.environ.get('DEFAULT_FROM_EMAIL', 'noreply@your-domain.alwaysdata.net')
else:
    # Development: Console backend (prints emails to terminal)
    EMAIL_BACKEND = 'django.core.mail.backends.console.EmailBackend'

# ============================================================
# STATIC & MEDIA FILES
# ============================================================

STATIC_URL = '/static/'
STATIC_ROOT = os.path.join(BASE_DIR, 'staticfiles')

MEDIA_URL = '/media/'
MEDIA_ROOT = os.path.join(BASE_DIR, 'media')

# Alwaysdata uses a specific path for static files
# You'll need to collect static files to this directory
# STATIC_ROOT = '/home/your-username/www/static/'

# ============================================================
# SESSION & SECURITY
# ============================================================

SESSION_ENGINE = 'django.contrib.sessions.backends.db'
SESSION_COOKIE_AGE = 1209600  # 2 weeks
SESSION_COOKIE_SECURE = True  # Set to True in production with HTTPS
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = 'Lax'

CSRF_COOKIE_SECURE = True
CSRF_COOKIE_HTTPONLY = True
CSRF_COOKIE_SAMESITE = 'Lax'

SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')

# ============================================================
# APPLICATION DEFINITION
# ============================================================

INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'core.apps.CoreConfig',
    'interview.apps.InterviewConfig',
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'CareerGPS.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ],
        },
    },
]

WSGI_APPLICATION = 'CareerGPS.wsgi.application'

# ============================================================
# AUTHENTICATION
# ============================================================

AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator'},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
]

AUTHENTICATION_BACKENDS = [
    'django.contrib.auth.backends.ModelBackend',
]

# ============================================================
# INTERNATIONALIZATION
# ============================================================

LANGUAGE_CODE = 'en-us'
TIME_ZONE = 'UTC'
USE_I18N = True
USE_TZ = True

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

# ============================================================
# API KEYS
# ============================================================

GEMINI_API_KEY = os.environ.get('GEMINI_API_KEY', '')
ADZUNA_APP_ID = os.environ.get('ADZUNA_APP_ID', 'dc866afc')
ADZUNA_API_KEY = os.environ.get('ADZUNA_API_KEY', '')

SITE_URL = os.environ.get('SITE_URL', 'http://127.0.0.1:8000')

# ============================================================
# CACHE
# ============================================================

CACHES = {
    'default': {
        'BACKEND': 'django.core.cache.backends.locmem.LocMemCache',
        'LOCATION': 'unique-snowflake',
        'TIMEOUT': 300,
        'OPTIONS': {'MAX_ENTRIES': 1000},
    }
}