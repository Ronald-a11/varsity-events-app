"""Deployment checks for the settings that fail quietly.

Everything here is a thing that lets the app start, serve pages and look
completely healthy while doing the wrong thing — mail that goes nowhere,
uploads that vanish on the next deploy, links that point at localhost. A
crash would be kinder; since these don't crash, they get a check instead.

Registered under the `deploy` tag, so `manage.py check --deploy` picks them up
and CI fails the build on them exactly as it does for Django's own list.
"""

from pathlib import Path

from django.conf import settings
from django.core.checks import Warning, register


@register("deploy")
def email_is_configured(app_configs, **kwargs):
    """Mail that isn't configured doesn't bounce — it evaporates.

    `core.mail.send_mail` catches everything by design, because an SMTP timeout
    must not unwind a payment. The cost of that decision is that a completely
    unconfigured mail server is indistinguishable from a working one until a
    student asks where their ticket is.
    """
    if settings.DEBUG or getattr(settings, "TESTING", False):
        return []
    if settings.EMAIL_HOST:
        return []

    return [
        Warning(
            "EMAIL_HOST is not set, so every transactional email is discarded.",
            hint=(
                "Tickets, receipts, waitlist promotions, email confirmation and "
                "password resets all go through SMTP. Set EMAIL_HOST, EMAIL_HOST_USER "
                "and EMAIL_HOST_PASSWORD, then prove it with "
                "`manage.py check_email --to you@example.com`."
            ),
            id="varsity.W001",
        )
    ]


@register("deploy")
def uploads_survive_a_deploy(app_configs, **kwargs):
    """A container filesystem is rebuilt on every push. Posters are not.

    Railway, Heroku and every buildpack-style host throw the disk away between
    releases. Uploads written to the project directory go with it, and the only
    symptom is that last term's posters are 404s.
    """
    if settings.DEBUG or getattr(settings, "TESTING", False):
        return []
    if getattr(settings, "AWS_STORAGE_BUCKET_NAME", ""):
        return []

    media_root = Path(settings.MEDIA_ROOT).resolve()
    base_dir = Path(settings.BASE_DIR).resolve()

    if base_dir not in media_root.parents and media_root != base_dir:
        # Pointed somewhere outside the code tree — almost certainly a mounted
        # volume, which is the other correct answer.
        return []

    return [
        Warning(
            f"MEDIA_ROOT ({media_root}) is inside the application directory and no "
            "object storage is configured, so uploads are lost on every deploy.",
            hint=(
                "Either mount a volume and point DJANGO_MEDIA_ROOT at it, or set "
                "AWS_STORAGE_BUCKET_NAME and its credentials for S3, R2, B2 or Spaces."
            ),
            id="varsity.W002",
        )
    ]


@register("deploy")
def links_in_email_point_somewhere_real(app_configs, **kwargs):
    """An email is read long after the request that sent it, on another machine."""
    if settings.DEBUG or getattr(settings, "TESTING", False):
        return []

    base = (settings.SITE_BASE_URL or "").lower()
    if base and "localhost" not in base and "127.0.0.1" not in base:
        return []

    return [
        Warning(
            f"SITE_BASE_URL is {settings.SITE_BASE_URL!r}, so every link we email "
            "points at the machine that sent it.",
            hint="Set SITE_BASE_URL to the public address, e.g. https://varsityevents.app",
            id="varsity.W003",
        )
    ]
