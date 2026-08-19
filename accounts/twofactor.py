"""Second factor for the accounts that can move money.

Who this is for: platform staff, and organizers who verify EcoCash transfers.
Those accounts can mark a payment as received and release a ticket against real
money that was never actually sent. A stolen organizer password is worth more
than a stolen student one, and students are the only accounts this leaves alone.

Two rules shape the design.

**Nobody gets locked out by a deploy.** A privileged account without a second
factor is redirected to *set one up*, never refused. The alternative — turning
enforcement on and finding the only superuser can't reach the admin — is an
outage you cause yourself, at the worst moment, with no way back in. Enforcement
on Django's own admin is a separate switch that defaults off for the same
reason.

**TOTP, not SMS.** An authenticator app works on a handset with no airtime and
no signal, which matters rather a lot here. SMS costs money to send, can be
intercepted, and fails exactly when somebody is standing at a venue trying to
verify a transfer.
"""

import base64
from functools import wraps
from io import BytesIO

import qrcode
from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect
from django_otp import user_has_device
from django_otp.plugins.otp_static.models import StaticDevice, StaticToken
from django_otp.plugins.otp_totp.models import TOTPDevice

# How many single-use codes a person gets for the day their phone is lost,
# stolen, wiped or simply flat.
RECOVERY_CODE_COUNT = 10


def handles_money(user) -> bool:
    """Whether this account can release a ticket against a payment.

    Platform staff can verify anything. An organizer can verify transfers for
    the societies they run — so the moment somebody is made an owner or admin
    of a society, they come into scope.
    """
    if not user.is_authenticated:
        return False
    if user.is_platform_staff:
        return True
    return user.managed_organizations().exists()


def has_second_factor(user) -> bool:
    """A *confirmed* device. An abandoned half-finished setup doesn't count."""
    return user.is_authenticated and user_has_device(user, confirmed=True)


def is_verified(request) -> bool:
    """Whether this session has actually presented the second factor."""
    user = request.user
    return user.is_authenticated and user.is_verified()


def unconfirmed_device(user) -> TOTPDevice:
    """The in-progress enrolment, creating one if setup has just started.

    Unconfirmed so that abandoning the page halfway doesn't leave an account
    holding a device it can't produce codes for — `has_second_factor` would say
    yes and the challenge would be unanswerable.
    """
    device = TOTPDevice.objects.filter(user=user, confirmed=False).first()
    if device is None:
        device = TOTPDevice.objects.create(user=user, name="Authenticator app", confirmed=False)
    return device


def provisioning_qr(device) -> str:
    """The otpauth:// URI as an inline PNG, for scanning into an app.

    Inline rather than an endpoint: this image is the shared secret in visual
    form, and a URL serving it is one more place it can be fetched from.
    """
    image = qrcode.make(device.config_url, box_size=6, border=2)
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode("ascii")


def issue_recovery_codes(user, count=RECOVERY_CODE_COUNT):
    """Replace this account's recovery codes and return the new ones in clear.

    Returned once and never again — they are stored as tokens django-otp
    consumes on use, and there is no screen anywhere that shows them a second
    time. Somebody who loses both their phone and these needs a superuser.
    """
    device, _ = StaticDevice.objects.get_or_create(user=user, name="Recovery codes")
    device.token_set.all().delete()

    codes = []
    for _ in range(count):
        token = StaticToken.random_token()
        device.token_set.create(token=token)
        codes.append(token)
    return codes


def remaining_recovery_codes(user) -> int:
    device = StaticDevice.objects.filter(user=user, name="Recovery codes").first()
    return device.token_set.count() if device else 0


def admin_requires_second_factor() -> bool:
    """Whether Django's own admin demands a verified session.

    Off unless asked for. Turning it on with no enrolled superuser locks the
    only account that could fix it out of the only place it could be fixed.
    """
    return getattr(settings, "ADMIN_REQUIRE_2FA", False)


def requires_second_factor(view):
    """Gate a money-handling view behind a verified session.

    Students never see this. A privileged account without a device is sent to
    set one up rather than turned away, and comes straight back here afterwards.
    """

    @wraps(view)
    def wrapped(request, *args, **kwargs):
        if not handles_money(request.user):
            return view(request, *args, **kwargs)

        if not has_second_factor(request.user):
            messages.info(
                request,
                "This account can release payments, so it needs a second factor "
                "before you can go any further. It takes a minute.",
            )
            return redirect_with_next("accounts:two_factor_setup", request)

        if not is_verified(request):
            return redirect_with_next("accounts:two_factor_verify", request)

        return view(request, *args, **kwargs)

    return login_required(wrapped)


def redirect_with_next(url_name, request):
    from django.urls import reverse
    from django.utils.http import urlencode

    return redirect(f"{reverse(url_name)}?{urlencode({'next': request.get_full_path()})}")
