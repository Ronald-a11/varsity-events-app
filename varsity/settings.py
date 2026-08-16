"""
Django settings for the Varsity Events platform.
"""

import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent

load_dotenv(BASE_DIR / ".env")


def env_bool(name, default=False):
    return os.getenv(name, str(default)).strip().lower() in {"1", "true", "yes", "on"}


def env_list(name, default=""):
    return [item.strip() for item in os.getenv(name, default).split(",") if item.strip()]


SECRET_KEY = os.getenv(
    "DJANGO_SECRET_KEY",
    "django-insecure-*x@+2%y4dv71@7t(utln59ae5me9k_$6s7ovt28g%fgo)$69(3",
)

DEBUG = env_bool("DJANGO_DEBUG", True)

ALLOWED_HOSTS = env_list("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1,[::1]")

CSRF_TRUSTED_ORIGINS = env_list("DJANGO_CSRF_TRUSTED_ORIGINS")

# Railway assigns the domain at deploy time and injects it here, so trust it
# without having to hard-code a host that changes on every environment.
RAILWAY_DOMAIN = os.getenv("RAILWAY_PUBLIC_DOMAIN", "").strip()
if RAILWAY_DOMAIN:
    # The platform's own health check probes the container from an internal
    # host, not the public domain, and Django answers a 400 to any Host it
    # doesn't know — which reads as "unhealthy" and rolls the deploy back.
    # The wildcard covers both, and only ever matches Railway's own domains.
    for host in (RAILWAY_DOMAIN, ".railway.app", "healthcheck.railway.app"):
        if host not in ALLOWED_HOSTS:
            ALLOWED_HOSTS.append(host)

    railway_origin = f"https://{RAILWAY_DOMAIN}"
    if railway_origin not in CSRF_TRUSTED_ORIGINS:
        CSRF_TRUSTED_ORIGINS.append(railway_origin)


# Application definition

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.humanize",
    "accounts",
    "organizations",
    "events",
    "payments",
    "activity",
    "core",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "varsity.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "core.context_processors.site_context",
            ],
        },
    },
]

WSGI_APPLICATION = "varsity.wsgi.application"


# Database
#
# SQLite locally; DATABASE_URL in production. On Railway the filesystem is
# rebuilt on every deploy, so a SQLite file there would silently lose every
# ticket and payment — attach a Postgres service and it sets DATABASE_URL.

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": BASE_DIR / "db.sqlite3",
    }
}

DATABASE_URL = os.getenv("DATABASE_URL", "")
if DATABASE_URL:
    import dj_database_url

    DATABASES["default"] = dj_database_url.parse(
        DATABASE_URL,
        conn_max_age=600,
        conn_health_checks=True,
        ssl_require=not DEBUG,
    )


# Authentication

AUTH_USER_MODEL = "accounts.User"

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LOGIN_URL = "accounts:login"
# Signing in drops students straight into the nationwide events feed.
LOGIN_REDIRECT_URL = "core:discover"
LOGOUT_REDIRECT_URL = "core:home"


# Internationalization

LANGUAGE_CODE = "en-us"

TIME_ZONE = os.getenv("DJANGO_TIME_ZONE", "Africa/Harare")

USE_I18N = True

USE_TZ = True


# Static files and media

STATIC_URL = "static/"
STATICFILES_DIRS = [BASE_DIR / "static"]
STATIC_ROOT = BASE_DIR / "staticfiles"

MEDIA_URL = "media/"
# Point this at a mounted volume in production. Uploaded posters and society
# logos live here, and anywhere ephemeral they'd disappear on the next deploy.
MEDIA_ROOT = Path(os.getenv("DJANGO_MEDIA_ROOT", BASE_DIR / "media"))

# There's no separate web server in front of us on Railway, so Django has to
# serve uploaded files itself unless something else is configured to.
SERVE_MEDIA = env_bool("DJANGO_SERVE_MEDIA", True)

STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {
        "BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage"
        if not DEBUG
        else "django.contrib.staticfiles.storage.StaticFilesStorage"
    },
}

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

FILE_UPLOAD_MAX_MEMORY_SIZE = 5 * 1024 * 1024  # 5 MB


# Messages -> Tailwind alert classes

from django.contrib.messages import constants as message_constants  # noqa: E402

MESSAGE_TAGS = {
    message_constants.DEBUG: "debug",
    message_constants.INFO: "info",
    message_constants.SUCCESS: "success",
    message_constants.WARNING: "warning",
    message_constants.ERROR: "error",
}


# Email
#
# Keyed off whether a host is configured, not off DEBUG. That way real SMTP can
# be tested locally before going live, and a production deploy that forgets its
# mail settings prints to the log instead of raising on every ticket sold.

EMAIL_HOST = os.getenv("EMAIL_HOST", "")
EMAIL_PORT = int(os.getenv("EMAIL_PORT", "587"))
EMAIL_HOST_USER = os.getenv("EMAIL_HOST_USER", "")
EMAIL_HOST_PASSWORD = os.getenv("EMAIL_HOST_PASSWORD", "")
EMAIL_USE_TLS = env_bool("EMAIL_USE_TLS", True)
EMAIL_USE_SSL = env_bool("EMAIL_USE_SSL", False)
EMAIL_TIMEOUT = int(os.getenv("EMAIL_TIMEOUT", "10"))

if EMAIL_HOST:
    EMAIL_BACKEND = "django.core.mail.backends.smtp.EmailBackend"
else:
    EMAIL_BACKEND = "django.core.mail.backends.console.EmailBackend"

DEFAULT_FROM_EMAIL = os.getenv("DEFAULT_FROM_EMAIL", "Varsity Events <no-reply@varsityevents.app>")
SERVER_EMAIL = os.getenv("SERVER_EMAIL", DEFAULT_FROM_EMAIL)

# Emails are read outside any request, so links can't be built from one.
if RAILWAY_DOMAIN:
    _default_base = f"https://{RAILWAY_DOMAIN}"
else:
    _default_base = "http://localhost:8000"
SITE_BASE_URL = os.getenv("SITE_BASE_URL", _default_base)


# Platform settings

SITE_NAME = os.getenv("SITE_NAME", "Varsity Events")
SITE_TAGLINE = "Every university event in Zimbabwe, one place."
SITE_COUNTRY = "Zimbabwe"
# Split for the two-tone wordmark in the header and footer.
SITE_NAME_LEAD = "VARSITY"
SITE_NAME_TAIL = "EVENTS"


# Paynow (https://paynow.co.zw) — Zimbabwe's payment gateway.
# Leave these blank in development: the payments app then runs a local simulator
# so the whole checkout flow works end to end without live credentials.
PAYNOW_INTEGRATION_ID = os.getenv("PAYNOW_INTEGRATION_ID", "")
PAYNOW_INTEGRATION_KEY = os.getenv("PAYNOW_INTEGRATION_KEY", "")


# Pesepay (https://pesepay.com) — the online gateway. Both keys come from the
# merchant dashboard; leave them blank in development and the payments app runs
# its local simulator instead.
PESEPAY_INTEGRATION_KEY = os.getenv("PESEPAY_INTEGRATION_KEY", "")
PESEPAY_ENCRYPTION_KEY = os.getenv("PESEPAY_ENCRYPTION_KEY", "")

# Pesepay's own method codes, per currency, from the merchant dashboard.
PESEPAY_METHOD_CODES = {
    "ecocash": os.getenv("PESEPAY_CODE_ECOCASH", "PZW201"),
    "onemoney": os.getenv("PESEPAY_CODE_ONEMONEY", "PZW204"),
    "innbucks": os.getenv("PESEPAY_CODE_INNBUCKS", "PZW211"),
}


# Direct EcoCash collection.
#
# Students send the money straight to this wallet and paste back their EcoCash
# confirmation code; an organizer then verifies it and the ticket confirms.
# This is the route that needs no merchant code — see the README for the
# trade-offs against a Paynow merchant account.
# No default: a real wallet number doesn't belong in a public repo, and a
# placeholder would quietly send students' money to a stranger.
ECOCASH_MERCHANT_NUMBER = os.getenv("ECOCASH_MERCHANT_NUMBER", "")
ECOCASH_MERCHANT_NAME = os.getenv("ECOCASH_MERCHANT_NAME", "Varsity Events")
ECOCASH_DIRECT_ENABLED = env_bool("ECOCASH_DIRECT_ENABLED", True)
# Direct transfers are done by hand, so they get a longer seat hold than a gateway push.
ECOCASH_DIRECT_HOLD_MINUTES = int(os.getenv("ECOCASH_DIRECT_HOLD_MINUTES", "120"))


# Production hardening

if not DEBUG:
    SECURE_SSL_REDIRECT = env_bool("DJANGO_SECURE_SSL_REDIRECT", True)
    # The load balancer probes the container directly over plain HTTP, with no
    # X-Forwarded-Proto to mark it as secure — so it would be met with a 301 and
    # read as unhealthy. Nothing sensitive is behind this path.
    SECURE_REDIRECT_EXEMPT = [r"^healthz$"]
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SECURE_HSTS_SECONDS = 60 * 60 * 24 * 30
    SECURE_HSTS_INCLUDE_SUBDOMAINS = True
    SECURE_HSTS_PRELOAD = True
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
    X_FRAME_OPTIONS = "DENY"
