# Copyright (C) 2026 free-the-software
# SPDX-License-Identifier: AGPL-3.0-or-later
# This file is part of Save More Money, under the GNU AGPL v3 or later: see LICENSE.
import os
import mimetypes

# Fetch from env, provide a fallback for local dev
DOMAIN_NAME = os.getenv("DOMAIN_NAME", "localhost")
SITE_URL = os.getenv("SITE_URL", f"https://{DOMAIN_NAME}")

# Allow all hosts so Docker and your other computer can connect
ALLOWED_HOSTS = ["localhost", DOMAIN_NAME, 'backend', 'finance_dev']

# --- QUICKSTART DEVELOPMENT SETTINGS ---

# SECURITY WARNING: keep the secret key used in production secret!
SECRET_KEY = os.environ.get('SECRET_KEY')

# SECURITY WARNING: don't run with debug turned on in production!
# Only on when the .env says DEBUG=True (desktop dev); prod leaves it unset.
DEBUG = os.environ.get('DEBUG') == 'True'

# --- APPS ---
INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'django_apscheduler',
    
    # Third Party Apps
    'rest_framework',
    'mozilla_django_oidc',
    
    # Local Apps
    'finance',
]

# --- MIDDLEWARE ---

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'whitenoise.middleware.WhiteNoiseMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'finance.throttles.SignInRateLimit',
    'finance.idle.IdleTimeout',
    'finance.auth.PasswordChangeRequired',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

# Serve the Django admin's CSS/JS straight from the installed apps, so it
# works with DEBUG=False without a collectstatic step.
WHITENOISE_USE_FINDERS = True

ROOT_URLCONF = 'config.urls'
WSGI_APPLICATION = 'config.wsgi.application'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.debug',
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ],
        },
    },
]

# --- DATABASE ---
# Configured to look for Docker environment variables first, 
# with defaults that match your docker-compose.dev.yml

DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.postgresql',
        'NAME': os.environ.get('DB_NAME', 'finance_dev'),
        'USER': os.environ.get('DB_USER', 'dev_user'),
        'PASSWORD': os.environ.get('DB_PASSWORD', 'dev_password'),
        'HOST': os.environ.get('DB_HOST', 'finance_dev'),
        'PORT': os.environ.get('DB_PORT', '5432'),
    }
}

# --- PASSWORD VALIDATION ---

AUTH_PASSWORD_VALIDATORS = [
    { 'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator', },
    { 'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator', },
    { 'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator', },
    { 'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator', },
]

# --- INTERNATIONALIZATION ---

LANGUAGE_CODE = 'en-us'
TIME_ZONE = 'UTC'
USE_I18N = True
USE_TZ = True

# --- STATIC FILES ---

STATIC_URL = '/static/'
DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

# ==============================================
#      CUSTOM CONFIGURATION (DRF, OIDC, CORS)
# ==============================================

# --- REST FRAMEWORK CONFIG ---
# Every endpoint requires a logged-in session unless a view opts out.
REST_FRAMEWORK = {
    'DEFAULT_PERMISSION_CLASSES': [
        'rest_framework.permissions.IsAuthenticated',
    ],
    'DEFAULT_AUTHENTICATION_CLASSES': [
        'rest_framework.authentication.SessionAuthentication',
    ],
    'DEFAULT_THROTTLE_CLASSES': [
        'finance.throttles.VisitorLimit',
        'finance.throttles.UserLimit',
    ],
    # How many reverse proxies sit in front of this server (Traefik is one).
    # It decides which address in X-Forwarded-For is the visitor's. Set
    # NUM_PROXIES=0 if the server is reached directly, or visitors could
    # dodge the limits by inventing that header.
    'NUM_PROXIES': int(os.environ.get('NUM_PROXIES', 1)),
}

# --- RATE LIMITS ---
# A backstop against scripts, set well above what the app's own pages use.
# Each is "number/period" (second, minute, hour or day); empty turns it off.
def _rate_limit(name, default):
    return os.environ.get(name, default).strip() or None

API_RATE_LIMITS = {
    'anon': _rate_limit('RATE_LIMIT_VISITOR', '60/minute'),      # not signed in, per address
    'user': _rate_limit('RATE_LIMIT_USER', '600/minute'),        # everything a signed-in user does
    'uploads': _rate_limit('RATE_LIMIT_UPLOADS', '120/minute'),  # files sent to the dropzone
    'heavy': _rate_limit('RATE_LIMIT_HEAVY', '20/hour'),         # backups and storage clean-ups
    'sign_in': _rate_limit('RATE_LIMIT_SIGN_IN', '30/minute'),   # sign-in attempts, per address
}

# The counts behind the limits. Kept in files so that all the server's worker
# processes share one count; the default in-memory store is per process.
CACHES = {
    'default': {
        'BACKEND': 'django.core.cache.backends.filebased.FileBasedCache',
        'LOCATION': os.environ.get('CACHE_DIR', '/tmp/finance-cache'),
    }
}

# --- AUTHENTIK / OIDC CONFIG ---
# AUTH_DOMAIN is the Authentik base URL (e.g. https://auth.example.com) and
# OIDC_APP_SLUG is the slug of the Authentik application for this site.
AUTH_DOMAIN = os.environ.get('AUTH_DOMAIN', '').rstrip('/')
OIDC_APP_SLUG = os.environ.get('OIDC_APP_SLUG', '')
OIDC_RP_CLIENT_ID = os.environ.get('OIDC_CLIENT_ID', '')
OIDC_RP_CLIENT_SECRET = os.environ.get('OIDC_CLIENT_SECRET', '')

# With no OIDC credentials, people sign in with a username and password on
# the app's own sign-in page (finance/auth.py), and users and their
# passwords are managed in Settings. The API requires a session either way.
OIDC_ENABLED = bool(AUTH_DOMAIN and OIDC_RP_CLIENT_ID)
PASSWORD_LOGIN = not OIDC_ENABLED

# The Django admin has its own username/password login, with no second
# factor or idle sign-out, and a session made there is good for the whole
# API, so it is off unless asked for with ENABLE_ADMIN=True. In development
# without OIDC (DEBUG=True) it is on unless ENABLE_ADMIN=False.
ADMIN_ENABLED = os.environ.get('ENABLE_ADMIN', 'True' if DEBUG and not OIDC_ENABLED else 'False') == 'True'

AUTHENTICATION_BACKENDS = []
if OIDC_ENABLED:
    AUTHENTICATION_BACKENDS.append('mozilla_django_oidc.auth.OIDCAuthenticationBackend')
if PASSWORD_LOGIN or ADMIN_ENABLED:
    # Password login against local Django users
    AUTHENTICATION_BACKENDS.append('django.contrib.auth.backends.ModelBackend')

OIDC_OP_AUTHORIZATION_ENDPOINT = f"{AUTH_DOMAIN}/application/o/authorize/"
OIDC_OP_TOKEN_ENDPOINT = f"{AUTH_DOMAIN}/application/o/token/"
OIDC_OP_USER_ENDPOINT = f"{AUTH_DOMAIN}/application/o/userinfo/"
OIDC_OP_JWKS_ENDPOINT = os.environ.get('OIDC_JWKS_URL') or f"{AUTH_DOMAIN}/application/o/{OIDC_APP_SLUG}/jwks/"
OIDC_OP_END_SESSION_ENDPOINT = f"{AUTH_DOMAIN}/application/o/{OIDC_APP_SLUG}/end-session/"

OIDC_RP_SIGN_ALGO = "RS256"
OIDC_RP_SCOPES = "openid email profile"
OIDC_STORE_ID_TOKEN = True  # Sent as id_token_hint when logging out of Authentik
# Ask the identity provider for the password (and second factor) at every
# sign-in, instead of reusing a session it may still have. Without this, a
# sign-out here would be undone silently on the next visit.
OIDC_FORCE_LOGIN = os.environ.get('OIDC_FORCE_LOGIN', 'True') != 'False'
OIDC_AUTH_REQUEST_EXTRA_PARAMS = {'prompt': 'login'} if OIDC_FORCE_LOGIN else {}

# --- LOGIN / LOGOUT REDIRECTS ---
# The app's own sign-in page is a page of the React app
LOGIN_URL = '/oidc/authenticate/' if OIDC_ENABLED else '/sign-in'
LOGIN_REDIRECT_URL = SITE_URL
LOGIN_REDIRECT_URL_FAILURE = f"{SITE_URL}/?auth_error=1"
LOGOUT_REDIRECT_URL = SITE_URL

# --- SESSION / CSRF CONFIG ---
SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')

CSRF_TRUSTED_ORIGINS = [
    SITE_URL,  # This will allow the main domain check .env file
]
if DEBUG:
    # The Vite dev server, reached as localhost or 127.0.0.1 on its port
    # (DEV_APP_PORT; another one for a second stack). Never trusted on a production site.
    _DEV_PORT = os.environ.get('DEV_APP_PORT', '5173')
    CSRF_TRUSTED_ORIGINS += [f"http://127.0.0.1:{_DEV_PORT}", f"http://localhost:{_DEV_PORT}"]

# Cookies are HTTPS-only whenever the site itself is served over HTTPS
_SITE_IS_HTTPS = SITE_URL.startswith('https://')
SESSION_COOKIE_SAMESITE = 'Lax' # Important for OIDC redirect flow
# Browsers share cookies across ports, so two copies on one machine need
# different names, or signing in to one signs you out of the other
SESSION_COOKIE_NAME = os.environ.get('SESSION_COOKIE_NAME', 'sessionid')
SESSION_COOKIE_SECURE = _SITE_IS_HTTPS
CSRF_COOKIE_SECURE = _SITE_IS_HTTPS
# Ask browsers to use HTTPS for this host from now on (HSTS). It covers this
# host only, not other subdomains. Set HSTS_SECONDS=0 to turn it off.
SECURE_HSTS_SECONDS = int(os.environ.get('HSTS_SECONDS', 60 * 60 * 24 * 365 if _SITE_IS_HTTPS else 0))
# How long a login lasts before the browser is sent back through Authentik
SESSION_COOKIE_AGE = int(os.environ.get('SESSION_COOKIE_AGE', 60 * 60 * 12))
# Signed out after this many minutes without activity (0 = never). The page
# warns a minute before; finance/idle.py enforces it on the server.
SESSION_IDLE_MINUTES = int(os.environ.get('SESSION_IDLE_MINUTES', 10))
# Add these to handle the Traefik proxy headers correctly
USE_X_FORWARDED_HOST = True
USE_X_FORWARDED_PORT = True

# --- LOGGING ---
# Django only prints error tracebacks to the console when DEBUG is on.
# Send them to the container log in prod too (docker compose logs backend).
LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'handlers': {
        'console': {'class': 'logging.StreamHandler'},
    },
    'loggers': {
        'django.request': {'handlers': ['console'], 'level': 'ERROR', 'propagate': False},
        'mozilla_django_oidc': {'handlers': ['console'], 'level': 'WARNING'},
        # The app's own warnings and errors, with tracebacks, for problems
        # whose detail is deliberately kept out of API responses
        'finance': {'handlers': ['console'], 'level': 'WARNING'},
    },
}

# Scheduler Configuration
APSCHEDULER_DATETIME_FORMAT = "N j, Y, f:s a"
APSCHEDULER_RUN_NOW_TIMEOUT = 25  # Seconds

mimetypes.add_type("application/javascript", ".js", True)
mimetypes.add_type("text/css", ".css", True)
mimetypes.add_type("image/svg+xml", ".svg", True)
mimetypes.add_type("image/png", ".png", True)
mimetypes.add_type("image/jpeg", ".jpg", True)

# --- MEDIA FILES (Images, Uploads) ---
MEDIA_URL = '/media/'
MEDIA_ROOT = '/data/media'
# --- NEW INSTALLS ---
# A brand-new, empty ledger gets a starter set of categories (finance/starter.py).
STARTER_CATEGORIES = os.environ.get('STARTER_CATEGORIES', 'True') != 'False'

# --- UPLOADS ---
# Where uploaded files wait for the watcher to import them
DROPZONE_DIR = os.environ.get('DROPZONE_DIR', '/data/dropzone')
# Only the file types the import pipeline knows how to process (see watcher.py)
UPLOAD_ALLOWED_EXTENSIONS = ('.qfx', '.ofx', '.csv', '.png', '.jpg', '.jpeg', '.heic')
# Receipt photos, which need the receipt reader (finance/receipt_reader.py)
RECEIPT_IMAGE_EXTENSIONS = ('.png', '.jpg', '.jpeg', '.heic')
# The receipt reader is an optional add-on (beta): installed when .env has
# COMPOSE_PROFILES=receipts, which also tells docker compose to run it
RECEIPT_READER_INSTALLED = 'receipts' in [p.strip() for p in os.environ.get('COMPOSE_PROFILES', '').split(',')]
# Where it answers, on the Docker network
RECEIPT_READER_URL = os.environ.get('RECEIPT_READER_URL', 'http://ai-worker:5000')
# Largest single upload, in megabytes. Bank files are tiny; phone photos of
# receipts are a few MB.
UPLOAD_MAX_MB = int(os.environ.get('UPLOAD_MAX_MB', 25))

# Where processed bank files and receipt images are moved after import
ARCHIVE_ROOT = os.environ.get('ARCHIVE_ROOT', '/data/archive')
# Where backups are written. Point it at another disk or a network share by
# mounting that location here (see BACKUP_DIR_HOST in docker-compose.yaml).
BACKUP_DIR = os.environ.get('BACKUP_DIR', '/data/backups')
# --- Version, updates and disk space (finance/system.py) ---
# The release this image was built from (set at build time from the git tag;
# empty when built from code that isn't a release)
APP_VERSION = os.environ.get('APP_VERSION', '').strip()
# Where the app is installed on the host (setup.sh writes it), so the update
# command shown in Settings is the exact one to paste
INSTALL_DIR = os.environ.get('INSTALL_DIR', '').strip()
# The GitHub repository ("owner/name") whose releases to check once a day for
# a newer version; set it empty to switch the check off entirely
UPDATE_CHECK_REPO = os.environ.get('UPDATE_CHECK_REPO', 'free-the-software/self-hosted-household-finance').strip()
# A warning shows on every page when the disk holding the app's data has less free
DISK_WARN_GB = float(os.environ.get('DISK_WARN_GB', 5))
DISK_CHECK_PATH = os.environ.get('DISK_CHECK_PATH', '/data')
