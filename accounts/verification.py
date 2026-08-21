"""Proving an email address reaches the person who typed it.

A signed, expiring token rather than a row in a table. Nothing here needs to be
revoked individually, the link is single-purpose, and a token that carries its
own expiry can't be left behind by a cleanup job that never ran.

The signature covers the *current* address, so changing your email invalidates
any link already sent to the old one — which is the whole point of the exercise.
"""

import logging

from django.conf import settings
from django.core import signing
from django.urls import reverse
from django.utils import timezone

from core.mail import absolute_url, send_mail

logger = logging.getLogger(__name__)

SALT = "accounts.verify-email"

# Long enough to survive a night without data and a spam folder, short enough
# that a link forwarded to a group chat months later is dead.
MAX_AGE_SECONDS = 60 * 60 * 24 * 3


def make_token(user) -> str:
    return signing.dumps({"pk": user.pk, "email": user.email}, salt=SALT)


def read_token(token: str):
    """Return the user this token proves, or None.

    None covers every failure — tampered, expired, user deleted, address
    changed since — because the page has one thing to say about all of them:
    ask for a fresh link.
    """
    from .models import User

    try:
        data = signing.loads(token, salt=SALT, max_age=MAX_AGE_SECONDS)
    except signing.SignatureExpired:
        return None
    except signing.BadSignature:
        return None

    user = User.objects.filter(pk=data.get("pk")).first()
    if user is None:
        return None
    # The address is part of the signature, but compare it again against the
    # row: the token could have been minted before an email change that the
    # signature has no way of knowing about.
    if (user.email or "").lower() != (data.get("email") or "").lower():
        return None
    return user


def mark_verified(user) -> bool:
    """Stamp the address as proved. Returns whether this changed anything."""
    if user.email_verified_at:
        return False
    user.email_verified_at = timezone.now()
    user.save(update_fields=["email_verified_at"])
    return True


def send_verification(user) -> bool:
    """Email the link. Best-effort, like every other send in this app."""
    if not user.email:
        return False

    path = reverse("accounts:verify_email", kwargs={"token": make_token(user)})
    return send_mail(
        to=user.email,
        subject=f"Confirm your email · {settings.SITE_NAME}",
        template="verify_email",
        context={
            "user": user,
            "verify_url": absolute_url(path),
            "expires_in": "3 days",
        },
    )
