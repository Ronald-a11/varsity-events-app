"""
Django settings for the Varsity Events platform.
"""

import os
import sys
from pathlib import Path

from django.core.exceptions import ImproperlyConfigured
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent

load_dotenv(BASE_DIR / ".env")


def env_bool(name, default=False):
    return os.getenv(name, str(default)).strip().lower() in {"1", "true", "yes", "on"}


def env_list(name, default=""):
    return [item.strip() for item in os.getenv(name, default).split(",") if item.strip()]


DEBUG = env_bool("DJANGO_DEBUG", True)

# The old default lived in this file, which means it lives in the repository's
# history and in every clone. Anyone holding it can forge session cookies and
# password-reset tokens, so a production deploy that forgets to set one must
# fail loudly at boot rather than run on a key the whole world has.
SECRET_KEY = os.getenv("DJANGO_SECRET_KEY", "").strip()
if not SECRET_KEY:
    if DEBUG:
        SECRET_KEY = "django-insecure-local-development-only-not-for-deployment"
    else:
        raise ImproperlyConfigured(
            "DJANGO_SECRET_KEY must be set when DJANGO_DEBUG is False. Generate one "
            "with:  python -c \"from django.core.management.utils import "
            "get_random_secret_key as k; print(k())\""
        )

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
    # For the search vector on Event. Inert on SQLite — every hook it installs
    # is guarded on the connection being Postgres — so development is unaffected.
    "django.contrib.postgres",
    "django_q",
    "accounts",
    "organizations",
    "events",
    "payments",
    "activity",
    "notifications",
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
    # Turns the throttle's PermissionDenied into a 429 with a Retry-After —
    # see RATELIMIT_VIEW below and core.views.ratelimited.
    "django_ratelimit.middleware.RatelimitMiddleware",
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

# Persistent connections reused per worker, or a real pool. Pooling scales
# better once there are several web workers plus a task cluster all holding
# connections, but Django forbids combining it with CONN_MAX_AGE, and it only
# pays off if Postgres has the max_connections headroom — so it stays opt-in.
DB_POOL = env_bool("DJANGO_DB_POOL", False)

if DATABASE_URL:
    import dj_database_url

    DATABASES["default"] = dj_database_url.parse(
        DATABASE_URL,
        conn_max_age=0 if DB_POOL else 600,
        conn_health_checks=not DB_POOL,
        ssl_require=not DEBUG,
    )
    if DB_POOL:
        DATABASES["default"].setdefault("OPTIONS", {})["pool"] = {
            "min_size": int(os.getenv("DJANGO_DB_POOL_MIN", "2")),
            "max_size": int(os.getenv("DJANGO_DB_POOL_MAX", "8")),
            "timeout": 10,
        }


# Cache
#
# The login throttle in accounts/views.py counts failed attempts in the cache.
# With local-memory caching each Gunicorn worker keeps its own tally, so eight
# allowed attempts really means eight per worker — Redis makes the count shared
# and the lockout mean what it says. It is also the better broker for the task
# queue below, which uses it when it's there.

REDIS_URL = os.getenv("REDIS_URL", "").strip()

if REDIS_URL:
    CACHES = {
        "default": {
            "BACKEND": "django.core.cache.backends.redis.RedisCache",
            "LOCATION": REDIS_URL,
            "KEY_PREFIX": "ve",
            "OPTIONS": {"pool_class": "redis.connection.BlockingConnectionPool"},
        }
    }
else:
    CACHES = {
        "default": {
            "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
            "LOCATION": "varsity-events",
        }
    }


# Background work
#
# Two jobs must not happen while a student waits for a page: sending mail, and
# releasing seats held by abandoned checkouts.
#
# Off by default, and deliberately not inferred from anything. A queue with no
# worker behind it accepts tasks and silently never runs them, so "is there a
# cluster?" is a question about what was actually deployed, and guessing at it
# from the presence of a Redis URL would fail quietly and cost students their
# tickets. Turn it on in the same change that starts the worker:
#
#     DJANGO_TASKS_ASYNC=True
#     python manage.py qcluster
#
# Left off, every task runs inline at the point it is asked for — exactly the
# behaviour this app had before there was a queue at all.

TASKS_ASYNC = env_bool("DJANGO_TASKS_ASYNC", False)

Q_CLUSTER = {
    "name": "varsity",
    "workers": int(os.getenv("Q_WORKERS", "2")),
    "recycle": 500,
    "timeout": 90,
    # Must exceed timeout, or the broker hands the same task to a second worker
    # while the first is still running it and the student gets two emails.
    "retry": 120,
    "max_attempts": 3,
    "queue_limit": 100,
    "bulk": 5,
    "save_limit": 250,
    "catch_up": False,
    "sync": not TASKS_ASYNC,
    "label": "Background tasks",
}

# One broker only: django_q picks the ORM over Redis whenever both are
# configured, so setting both would quietly ignore the Redis one. Redis is the
# better queue, but the database works and saves a deploy without Redis from
# having to add it purely to send mail off the request thread.
if REDIS_URL:
    Q_CLUSTER["redis"] = REDIS_URL
else:
    Q_CLUSTER["orm"] = "default"


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
# Only used when there's no object store. Point it at a mounted volume, because
# a container's own filesystem is rebuilt on every deploy and every poster and
# society logo would go with it.
MEDIA_ROOT = Path(os.getenv("DJANGO_MEDIA_ROOT", BASE_DIR / "media"))


# Object storage for uploads.
#
# Set a bucket and posters and logos survive a deploy; leave it blank and they
# stay on the local disk as before. S3-compatible, so the same four variables
# work for Cloudflare R2, Backblaze B2 or DigitalOcean Spaces — just point
# AWS_S3_ENDPOINT_URL at the provider.

AWS_STORAGE_BUCKET_NAME = os.getenv("AWS_STORAGE_BUCKET_NAME", "").strip()
USE_S3 = bool(AWS_STORAGE_BUCKET_NAME)

if USE_S3:
    AWS_ACCESS_KEY_ID = os.getenv("AWS_ACCESS_KEY_ID", "")
    AWS_SECRET_ACCESS_KEY = os.getenv("AWS_SECRET_ACCESS_KEY", "")
    AWS_S3_REGION_NAME = os.getenv("AWS_S3_REGION_NAME", "auto")
    AWS_S3_ENDPOINT_URL = os.getenv("AWS_S3_ENDPOINT_URL", "") or None
    # Uploads are posters and logos — public, read far more than written, and
    # cached hard. Signed URLs would defeat both the CDN and the browser cache.
    AWS_QUERYSTRING_AUTH = False
    AWS_S3_FILE_OVERWRITE = False
    AWS_DEFAULT_ACL = None
    AWS_S3_OBJECT_PARAMETERS = {"CacheControl": "public, max-age=2592000"}
    # A CDN or custom domain in front of the bucket, if there is one.
    AWS_S3_CUSTOM_DOMAIN = os.getenv("AWS_S3_CUSTOM_DOMAIN", "") or None
    MEDIA_URL = os.getenv("DJANGO_MEDIA_URL", "") or MEDIA_URL

# Django only has to serve uploads itself when nothing else will. With a bucket
# configured it never should — that traffic shouldn't touch the web workers.
SERVE_MEDIA = env_bool("DJANGO_SERVE_MEDIA", not USE_S3)

STORAGES = {
    "default": {
        "BACKEND": "storages.backends.s3.S3Storage"
        if USE_S3
        else "django.core.files.storage.FileSystemStorage"
    },
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

# Web Push.
#
# Generate a pair with:  python manage.py make_vapid_keys
#
# Keep them stable once set. A browser ties its subscription to the key that
# created it, so rotating the pair silently invalidates every permission anybody
# has granted — and a permission, once granted, is never re-requested. Leave
# them blank and push is a no-op everywhere; nothing else changes.
VAPID_PUBLIC_KEY = os.getenv("VAPID_PUBLIC_KEY", "").strip()
VAPID_PRIVATE_KEY = os.getenv("VAPID_PRIVATE_KEY", "").strip().replace("\\n", "\n")
# Where a push service should complain if we start misbehaving. Must be a
# mailto: or https: URL that reaches someone.
VAPID_ADMIN_EMAIL = os.getenv("VAPID_ADMIN_EMAIL", "mailto:admin@varsityevents.app")


# The floor on how often the status page may make us ask a gateway about one
# payment. The page polls on a timer, and without this every tick was an
# outbound call — roughly forty per student per checkout, all about the same
# transaction. Local state still updates on every poll, so a payment confirmed
# by callback is noticed immediately; only the network call is rationed. Raise
# it to give a struggling gateway room.
PAYMENT_POLL_MIN_SECONDS = int(os.getenv("PAYMENT_POLL_MIN_SECONDS", "4"))


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


# Logging
#
# The interesting lines already exist — payments logs every gateway callback,
# every signature failure and every unknown reference. Until now they went to
# stderr with no configuration behind them, which on Railway means they scroll
# past and are gone. Structured output in production makes them searchable;
# development keeps the readable one-liner.

LOG_LEVEL = os.getenv("DJANGO_LOG_LEVEL", "DEBUG" if DEBUG else "INFO").upper()
LOG_FORMAT = os.getenv("DJANGO_LOG_FORMAT", "console" if DEBUG else "json").lower()

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "console": {
            "format": "{asctime} {levelname:<8} {name:<24} {message}",
            "style": "{",
            "datefmt": "%H:%M:%S",
        },
        "json": {"()": "varsity.logformat.JSONFormatter"},
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "stream": sys.stdout,
            "formatter": LOG_FORMAT if LOG_FORMAT in {"console", "json"} else "json",
        },
    },
    "root": {"handlers": ["console"], "level": "WARNING"},
    "loggers": {
        "django": {"level": "INFO"},
        # Django logs 4xx and 5xx here. Without it a 500 in production is a
        # bare traceback on stderr and nothing that says which URL caused it.
        "django.request": {"level": "WARNING"},
        "django.security": {"level": "WARNING"},
        # Off unless asked for: it prints every query, which is how you find an
        # N+1, and unbearable the rest of the time.
        "django.db.backends": {
            "level": "DEBUG" if env_bool("DJANGO_LOG_SQL", False) else "WARNING",
        },
        # Our own code — payments especially, where the log is the audit trail.
        "accounts": {"level": LOG_LEVEL},
        "activity": {"level": LOG_LEVEL},
        "core": {"level": LOG_LEVEL},
        "events": {"level": LOG_LEVEL},
        "organizations": {"level": LOG_LEVEL},
        "payments": {"level": LOG_LEVEL},
        # The task cluster is a second process with no request log of its own.
        "django_q": {"level": "INFO"},
    },
}


# Error reporting
#
# Inert without a DSN, so development and CI never phone home.

SENTRY_DSN = os.getenv("SENTRY_DSN", "").strip()

if SENTRY_DSN:
    import sentry_sdk

    sentry_sdk.init(
        dsn=SENTRY_DSN,
        environment=os.getenv("SENTRY_ENVIRONMENT", "production" if not DEBUG else "development"),
        release=os.getenv("RAILWAY_GIT_COMMIT_SHA", "") or None,
        # Sampled, not exhaustive: a student's ticket purchase is worth tracing,
        # a health check every ten seconds is not.
        traces_sample_rate=float(os.getenv("SENTRY_TRACES_SAMPLE_RATE", "0.1")),
        # Names and email addresses of students are not ours to ship to a third
        # party for the convenience of debugging.
        send_default_pii=False,
    )


# Throttling
#
# Applied per view — see the decorators in payments/views.py, accounts/views.py
# and core/views.py. Backed by the cache above, so it only counts correctly
# across workers once REDIS_URL is set.

RATELIMIT_ENABLE = env_bool("DJANGO_RATELIMIT_ENABLE", True)
RATELIMIT_USE_CACHE = "default"
RATELIMIT_VIEW = "core.views.ratelimited"


# Running the tests
#
# PBKDF2 is deliberately slow — that is the entire point of it, and it stays on
# everywhere that matters. But the suite creates a handful of users in almost
# every setUp, and at roughly a second of hashing apiece (much worse on a slow
# box) that alone put the full run into the tens of minutes. A suite nobody can
# afford to run is a suite nobody runs, so tests hash with MD5 and finish.
#
# Detected here rather than kept in a separate settings module so that the
# `python manage.py test` in the README is the fast path, with nothing to
# remember and nothing for CI to pass.

TESTING = "test" in sys.argv

if TESTING:
    PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]

    # Local memory even if the developer has REDIS_URL exported, so a test run
    # can't read or evict anything real, and each run starts empty.
    CACHES = {
        "default": {
            "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
            "LOCATION": "varsity-events-tests",
        }
    }

    # Tasks run inline. Assertions can then look at mail.outbox straight after
    # the call that triggers the send, exactly as they did before there was a
    # queue at all.
    Q_CLUSTER = {**Q_CLUSTER, "sync": True}

    # Off by default: much of the suite posts the same form repeatedly and
    # would otherwise trip a throttle. The tests that cover throttling turn it
    # back on with @override_settings(RATELIMIT_ENABLE=True).
    RATELIMIT_ENABLE = False

    # Keep the noise down; a failing test shows its own output.
    LOGGING["root"]["level"] = "CRITICAL"
    for _logger in LOGGING["loggers"].values():
        _logger["level"] = "CRITICAL"
