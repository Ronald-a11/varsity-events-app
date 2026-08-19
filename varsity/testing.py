"""Helpers the test suite shares across apps.

Small enough not to warrant a package, and it lives beside the settings that
decide what a privileged session even means.
"""

from django_otp import DEVICE_ID_SESSION_KEY
from django_otp.plugins.otp_totp.models import TOTPDevice


def enrol_second_factor(user, name="Test authenticator"):
    """Give an account a confirmed TOTP device, without going through setup."""
    return TOTPDevice.objects.create(user=user, name=name, confirmed=True)


def login_verified(client, user):
    """Sign in *and* present the second factor.

    Any account that can release money needs both now, so a test driving those
    views has to arrive the way a real privileged session does. `force_login`
    alone leaves the session unverified and every such view redirects to the
    challenge — which is the behaviour, not a broken test.

    Setting the session key directly rather than posting a real TOTP code: the
    code depends on the wall clock, and a suite that fails on a slow machine
    because a 30-second window rolled over is worse than useless. The real
    enrol-and-challenge flow is covered end to end in accounts/tests_2fa.py.
    """
    device = enrol_second_factor(user)
    client.force_login(user)

    session = client.session
    session[DEVICE_ID_SESSION_KEY] = device.persistent_id
    session.save()
    return device
