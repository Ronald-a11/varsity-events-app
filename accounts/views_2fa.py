"""Enrolling in, presenting, and removing the second factor."""

import logging

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST
from django_otp import devices_for_user, login as otp_login
from django_otp.plugins.otp_static.models import StaticDevice
from django_otp.plugins.otp_totp.models import TOTPDevice
from django_ratelimit.decorators import ratelimit

from .twofactor import (
    handles_money,
    has_second_factor,
    issue_recovery_codes,
    provisioning_qr,
    remaining_recovery_codes,
    unconfirmed_device,
)

logger = logging.getLogger(__name__)


def _safe_next(request, fallback="accounts:profile"):
    """Where to go afterwards, without becoming an open redirect.

    `next` arrives on the querystring of a page that gates money. Following it
    blindly would let a link land somebody on an attacker's site immediately
    after they have proved who they are.
    """
    target = request.POST.get("next") or request.GET.get("next") or ""
    if target and url_has_allowed_host_and_scheme(
        target, allowed_hosts={request.get_host()}, require_https=request.is_secure()
    ):
        return target
    return reverse(fallback)


@login_required
@ratelimit(key="user", rate="20/h", method="POST", block=True)
def two_factor_setup(request):
    """Show the QR, then confirm the device with a code from the app."""
    if has_second_factor(request.user):
        messages.info(request, "This account already has a second factor.")
        return redirect("accounts:two_factor_manage")

    device = unconfirmed_device(request.user)
    error = ""

    if request.method == "POST":
        token = (request.POST.get("token") or "").strip().replace(" ", "")

        if device.verify_token(token):
            device.confirmed = True
            device.name = "Authenticator app"
            device.save(update_fields=["confirmed", "name"])

            # Verify the session immediately: they have just proved they hold
            # the device, and making them do it twice in a row reads as broken.
            otp_login(request, device)

            codes = issue_recovery_codes(request.user)
            request.session["fresh_recovery_codes"] = codes
            logger.info("Second factor enrolled for %s", request.user)

            messages.success(request, "Second factor on. Save your recovery codes.")
            return redirect(f"{reverse('accounts:two_factor_codes')}?{request.GET.urlencode()}")

        error = "That code didn't match. Check your phone's clock is right and try again."

    return render(
        request,
        "accounts/two_factor_setup.html",
        {
            "qr": provisioning_qr(device),
            # For anyone whose camera won't cooperate.
            "secret": device.key,
            "error": error,
            "next": _safe_next(request),
            "handles_money": handles_money(request.user),
        },
    )


@login_required
def two_factor_codes(request):
    """Show recovery codes once, immediately after they are issued."""
    codes = request.session.pop("fresh_recovery_codes", None)
    if not codes:
        messages.info(
            request,
            "Recovery codes are shown once, when they're made. Generate a new set if you need them.",
        )
        return redirect("accounts:two_factor_manage")

    return render(
        request,
        "accounts/two_factor_codes.html",
        {"codes": codes, "next": _safe_next(request)},
    )


@login_required
# Tight: this is a guessing surface for a six-digit number. django-otp throttles
# the device itself as well, so a wrong code also costs an escalating delay.
@ratelimit(key="user", rate="10/m", method="POST", block=True)
@ratelimit(key="ip", rate="30/m", method="POST", block=True)
def two_factor_verify(request):
    """Ask for a code before letting a privileged session through."""
    if not has_second_factor(request.user):
        return redirect("accounts:two_factor_setup")

    error = ""

    if request.method == "POST":
        token = (request.POST.get("token") or "").strip().replace(" ", "").replace("-", "")

        for device in devices_for_user(request.user, confirmed=True):
            if device.verify_token(token):
                otp_login(request, device)

                if isinstance(device, StaticDevice):
                    left = remaining_recovery_codes(request.user)
                    messages.warning(
                        request,
                        f"That was a recovery code — {left} left. "
                        "Set up your authenticator app again when you can.",
                    )
                logger.info("Second factor accepted for %s", request.user)
                return redirect(_safe_next(request))

        logger.warning("Second factor rejected for %s", request.user)
        error = "That code didn't match. Try the next one your app shows."

    return render(
        request,
        "accounts/two_factor_verify.html",
        {"error": error, "next": _safe_next(request)},
    )


@login_required
def two_factor_manage(request):
    """What's enrolled, and how to change it."""
    return render(
        request,
        "accounts/two_factor_manage.html",
        {
            "enrolled": has_second_factor(request.user),
            "verified": request.user.is_verified(),
            "codes_left": remaining_recovery_codes(request.user),
            "handles_money": handles_money(request.user),
        },
    )


@login_required
@require_POST
def two_factor_regenerate_codes(request):
    if not has_second_factor(request.user):
        return redirect("accounts:two_factor_setup")

    # Proving current possession first, or a hijacked session could mint itself
    # a fresh set of codes and keep the account for good.
    if not request.user.is_verified():
        return redirect(f"{reverse('accounts:two_factor_verify')}?next={reverse('accounts:two_factor_manage')}")

    request.session["fresh_recovery_codes"] = issue_recovery_codes(request.user)
    messages.success(request, "New recovery codes. The old ones no longer work.")
    return redirect("accounts:two_factor_codes")


@login_required
@require_POST
def two_factor_disable(request):
    """Remove the second factor — only from a session that has presented it."""
    if not request.user.is_verified():
        return redirect(f"{reverse('accounts:two_factor_verify')}?next={reverse('accounts:two_factor_manage')}")

    TOTPDevice.objects.filter(user=request.user).delete()
    StaticDevice.objects.filter(user=request.user).delete()
    logger.warning("Second factor removed for %s", request.user)

    if handles_money(request.user):
        messages.warning(
            request,
            "Second factor removed. This account can release payments, so you'll "
            "be asked to set one up again next time you verify a transfer.",
        )
    else:
        messages.success(request, "Second factor removed.")

    return redirect("accounts:two_factor_manage")
