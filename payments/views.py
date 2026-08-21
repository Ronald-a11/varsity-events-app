import logging

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.http import Http404, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse  # noqa: F401  (used for redirects and breadcrumbs)
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST
from django_ratelimit.decorators import ratelimit

from decimal import Decimal

from accounts.twofactor import requires_second_factor
from core import mail
from events.models import Event, Registration
from organizations.models import Organization

from django.conf import settings

from .forms import CheckoutForm, ConfirmTransferForm
from .models import (
    Payment,
    Payout,
    ledger_for,
    prepare_payout,
    societies_owed,
)
from .paynow import PaynowClient, verify_hash
from .pesepay import PesepayClient, PesepayError

logger = logging.getLogger(__name__)


def _by_reference(group, request):
    """Throttle key: the payment in the URL.

    Per-IP alone isn't enough for the gateway callback, which anyone on the
    internet may POST to. Keyed on the reference as well, one payment can't be
    used to drive an unbounded number of outbound calls to Pesepay.
    """
    match = getattr(request, "resolver_match", None)
    return (match.kwargs.get("reference", "") if match else "") or request.path


def _absolute(request, name, **kwargs):
    return request.build_absolute_uri(reverse(name, kwargs=kwargs))


def _wants_json(request):
    """The checkout pop-up posts over fetch; a plain form post still redirects."""
    return request.headers.get("X-Requested-With") == "XMLHttpRequest"


def _push_payload(payment):
    """Everything the on-page prompt needs to render itself."""
    return {
        "reference": payment.reference,
        "method": payment.get_method_display(),
        "icon": payment.method_icon,
        "phone_masked": payment.phone_masked,
        "amount": payment.amount_display,
        "seconds_left": payment.seconds_left,
        "instructions": payment.instructions,
        "is_simulated": payment.is_simulated,
        "state_url": reverse("payments:status_json", kwargs={"reference": payment.reference}),
        "status_url": reverse("payments:status", kwargs={"reference": payment.reference}),
        "resend_url": reverse("payments:resend", kwargs={"reference": payment.reference}),
        "simulator_url": (
            reverse("payments:simulator", kwargs={"reference": payment.reference})
            if payment.is_simulated
            else ""
        ),
    }


def _resolve_locally(payment: Payment) -> Payment:
    """Act on what we already know, without asking anyone.

    A payment can have been settled by the gateway's callback, or simply run out
    of time, since the row was last looked at. Neither needs a network call to
    notice.
    """
    if payment.is_settled:
        payment.settle()
    elif payment.has_expired:
        payment.expire()
    return payment


def _asked_recently(payment: Payment, min_interval: int) -> bool:
    """Whether the gateway has been asked about this payment within `min_interval`.

    The status page polls every few seconds and each poll used to mean an
    outbound call, so a two-minute checkout asked Pesepay about forty times
    about one transaction. The gateway is the slowest thing in the request and
    the only one with a quota, and nothing it could say changes that fast.
    """
    if not min_interval or not payment.last_polled_at:
        return False
    age = (timezone.now() - payment.last_polled_at).total_seconds()
    return age < min_interval


def _sync(payment: Payment, min_interval: int = 0) -> Payment:
    """Bring a payment up to date with its gateway, and settle the ticket if paid.

    `min_interval` throttles the outbound call only — local transitions still
    happen every time, so a payment confirmed by callback a moment ago is picked
    up on the next poll whether or not we ask the gateway again. Callers acting
    on a nudge from the gateway itself pass nothing and always get a fresh read.
    """
    # Direct transfers are settled by a person, not a gateway — nothing to poll.
    if payment.is_manual:
        return _resolve_locally(payment)

    if payment.is_simulated or not payment.is_open:
        return _resolve_locally(payment)

    if _asked_recently(payment, min_interval):
        return _resolve_locally(payment)

    if payment.gateway == Payment.Gateway.PESEPAY:
        return _sync_pesepay(payment)

    result = PaynowClient().poll(payment.poll_url)

    if result.ok and result.status:
        payment.apply_paynow_status(result.status, result.paynow_reference)
        if payment.is_settled:
            payment.settle()
    elif payment.has_expired:
        payment.expire()

    return payment


def _sync_pesepay(payment: Payment) -> Payment:
    """Ask Pesepay for the current status of this transaction."""
    if payment.is_simulated or not payment.is_open:
        return _resolve_locally(payment)

    result = PesepayClient().check_payment(payment.paynow_reference)

    if result.ok and result.status:
        payment.apply_pesepay_status(result.status, result.reference)
        if payment.is_settled:
            payment.settle()
    elif payment.has_expired:
        payment.expire()

    return payment


def _retry_in(payment: Payment) -> int:
    """How many seconds the client should wait before asking again. 0 means stop.

    A wallet prompt is usually approved in the first half-minute or not for a
    while, so the client leans in early and backs off after. Sending the interval
    from here rather than hard-coding it in three templates means the pace can be
    changed without a deploy of the front end, and a struggling gateway can be
    given room by widening it.
    """
    if payment.is_settled or not payment.is_open:
        return 0

    waiting_for = (timezone.now() - payment.created_at).total_seconds()

    if waiting_for < 30:
        return 2
    if waiting_for < 90:
        return 5
    return 10


@login_required
@ratelimit(key="user", rate="12/m", method="POST", block=True)
def checkout(request, slug):
    """Pick a payment method and hand off to Pesepay."""
    event = get_object_or_404(Event, slug=slug)

    if event.is_free:
        messages.info(request, "This event is free — no payment needed.")
        return redirect(event.get_absolute_url())

    registration = Registration.objects.filter(event=event, user=request.user).first()
    if registration is None or registration.status == Registration.Status.CANCELLED:
        messages.error(request, "Register for the event first, then pay to confirm your place.")
        return redirect(event.get_absolute_url())

    if registration.status == Registration.Status.CONFIRMED:
        messages.info(request, "This ticket is already paid for.")
        return redirect(registration.get_absolute_url())

    if registration.status == Registration.Status.WAITLISTED:
        messages.info(request, "You're on the waitlist — we'll ask you to pay if a place opens.")
        return redirect(event.get_absolute_url())

    # Reuse an in-flight checkout rather than stacking up abandoned attempts.
    existing = registration.open_payment
    if existing and existing.is_open and request.method == "GET":
        return redirect("payments:status", reference=existing.reference)

    form = CheckoutForm(request.POST or None)

    if request.method == "POST" and not form.is_valid() and _wants_json(request):
        # Surface the field error in the pop-up rather than reloading under it.
        first = next(iter(form.errors.values()))[0]
        return JsonResponse({"ok": False, "error": first, "fields": form.errors})

    if request.method == "POST" and form.is_valid():
        method = form.cleaned_data["method"]
        phone = form.cleaned_data.get("phone", "")
        client = PesepayClient()

        payment = Payment.objects.create(
            registration=registration,
            user=request.user,
            amount=event.price,
            currency=event.currency,
            method=method,
            phone=phone,
            gateway=(
                Payment.Gateway.DIRECT
                if method == Payment.Method.ECOCASH_DIRECT
                else Payment.Gateway.PESEPAY
            ),
            is_simulated=client.is_simulated,
        )

        # Direct transfers never touch the gateway: the student sends the money
        # from their own handset and we wait for the confirmation code.
        if method == Payment.Method.ECOCASH_DIRECT:
            payment.status = Payment.Status.AWAITING_TRANSFER
            payment.instructions = (
                f"Send {payment.amount_display} to {settings.ECOCASH_MERCHANT_NUMBER} "
                f"({settings.ECOCASH_MERCHANT_NAME}) on EcoCash, then enter the "
                f"confirmation code from the SMS."
            )
            payment.save(update_fields=["status", "instructions", "updated_at"])
            transfer_url = reverse("payments:transfer", kwargs={"reference": payment.reference})
            if _wants_json(request):
                return JsonResponse({"ok": True, "redirect": transfer_url})
            return redirect(transfer_url)

        reason = f"{event.title} — {event.organization.name}"
        return_url = _absolute(request, "payments:return", reference=payment.reference)
        result_url = _absolute(request, "payments:result", reference=payment.reference)

        try:
            if method == Payment.Method.WEB:
                # Pesepay's hosted page: card, Zimswitch and every wallet.
                result = client.initiate(
                    amount=payment.amount,
                    currency=payment.currency,
                    reason=reason,
                    return_url=return_url,
                    result_url=result_url,
                    reference=payment.reference,
                )
            else:
                # Seamless: push a prompt straight to the payer's wallet.
                result = client.make_seamless_payment(
                    amount=payment.amount,
                    currency=payment.currency,
                    reason=reason,
                    result_url=result_url,
                    method_code=settings.PESEPAY_METHOD_CODES.get(method, method),
                    phone=phone,
                    email=request.user.email,
                    name=request.user.display_name,
                    reference=payment.reference,
                )
        except PesepayError as exc:
            payment.status = Payment.Status.FAILED
            payment.error = str(exc)
            payment.save(update_fields=["status", "error", "updated_at"])
            if _wants_json(request):
                return JsonResponse({"ok": False, "error": f"We couldn't reach Pesepay: {exc}"})
            messages.error(request, f"We couldn't reach Pesepay: {exc}")
            return redirect("payments:checkout", slug=event.slug)

        if not result.ok:
            payment.status = Payment.Status.FAILED
            payment.error = result.error
            payment.save(update_fields=["status", "error", "updated_at"])
            reason = result.error or "Pesepay declined the transaction."
            if _wants_json(request):
                return JsonResponse({"ok": False, "error": reason})
            messages.error(request, reason)
            return redirect("payments:checkout", slug=event.slug)

        payment.paynow_reference = result.reference
        payment.poll_url = result.poll_url
        payment.browser_url = result.redirect_url
        payment.instructions = result.instructions
        payment.status = Payment.Status.SENT
        payment.save(
            update_fields=[
                "paynow_reference", "poll_url", "browser_url",
                "instructions", "status", "updated_at",
            ]
        )

        # Hosted page goes off to Pesepay; seamless waits on the phone prompt.
        if method == Payment.Method.WEB and result.redirect_url:
            if _wants_json(request):
                return JsonResponse({"ok": True, "redirect": result.redirect_url})
            return redirect(result.redirect_url)

        if _wants_json(request):
            # The pop-up needs enough to render itself without another round trip.
            return JsonResponse({"ok": True, "payment": _push_payload(payment)})

        return redirect("payments:status", reference=payment.reference)

    return render(
        request,
        "payments/checkout.html",
        {
            "event": event,
            "registration": registration,
            "form": form,
            "is_simulated": PesepayClient().is_simulated,
            "ecocash_number": settings.ECOCASH_MERCHANT_NUMBER,
            "ecocash_name": settings.ECOCASH_MERCHANT_NAME,
            "ecocash_enabled": settings.ECOCASH_DIRECT_ENABLED,
        },
    )


@login_required
def transfer(request, reference):
    """Send-us-the-money screen, plus the form to hand back the EcoCash code."""
    payment = get_object_or_404(
        Payment.objects.select_related("registration", "registration__event"),
        reference=reference,
    )
    if payment.user != request.user:
        raise Http404("No payment matches the given query.")

    if payment.is_settled:
        return redirect("payments:status", reference=payment.reference)

    form = ConfirmTransferForm(request.POST or None)

    if request.method == "POST" and form.is_valid():
        payment.submit_confirmation(
            form.cleaned_data["confirmation_code"], form.cleaned_data.get("paid_from", "")
        )
        messages.success(
            request,
            "Thanks — we've got your code. The organizer will confirm it shortly and "
            "your ticket will activate automatically.",
        )
        return redirect("payments:status", reference=payment.reference)

    return render(
        request,
        "payments/transfer.html",
        {
            "payment": payment,
            "event": payment.registration.event,
            "form": form,
            "ecocash_number": settings.ECOCASH_MERCHANT_NUMBER,
            "ecocash_name": settings.ECOCASH_MERCHANT_NAME,
        },
    )


# Marking a transfer verified releases a ticket against money somebody says
# they sent. It is the highest-value action a non-superuser can take.
@requires_second_factor
def verification_queue(request):
    """Transfers waiting on a human. Organizers see their own events; staff see all."""
    payments = (
        Payment.objects.filter(status=Payment.Status.AWAITING_VERIFICATION)
        .select_related("registration", "registration__event", "user")
        .order_by("created_at")
    )

    if not request.user.is_platform_staff:
        managed = request.user.managed_organizations()
        payments = payments.filter(registration__event__organization__in=managed)
        if not managed.exists():
            raise Http404("Nothing to verify.")

    if request.method == "POST":
        payment = get_object_or_404(
            Payment, pk=request.POST.get("payment_id"), status=Payment.Status.AWAITING_VERIFICATION
        )
        if not payment.registration.event.can_manage(request.user):
            raise Http404("No payment matches the given query.")

        if request.POST.get("decision") == "verify":
            payment.verify(by_user=request.user)
            messages.success(
                request,
                f"{payment.amount_display} confirmed — {payment.user.display_name}'s ticket is live.",
            )
        else:
            payment.reject(by_user=request.user, reason=request.POST.get("reason", ""))
            messages.info(request, "Marked as unmatched. The payer has been asked to try again.")

        return redirect("payments:verify")

    return render(
        request,
        "payments/verify.html",
        {
            "payments": payments,
            "ecocash_number": settings.ECOCASH_MERCHANT_NUMBER,
            "crumbs": [{"label": "Manage", "url": reverse("events:dashboard")}, {"label": "Verify EcoCash"}],
            "page_subtitle": (
                f"Students who sent EcoCash straight to {settings.ECOCASH_MERCHANT_NUMBER}. "
                f"Check each code against your wallet statement before confirming."
            ),
            "actions": [
                {"label": "Dashboard", "url": reverse("events:dashboard"), "style": "secondary"}
            ],
        },
    )


@login_required
@require_POST
# One prompt at a time on a real phone. Without a limit this is a button that
# spams somebody's handset for as long as you hold it down.
@ratelimit(key="user", rate="5/m", method="POST", block=True)
def resend_prompt(request, reference):
    """Push the wallet prompt again — same payment, no second charge.

    The first prompt can be swallowed by a flat battery or a dropped network,
    and a student shouldn't have to start over (or risk paying twice) for that.
    """
    payment = get_object_or_404(
        Payment.objects.select_related("registration", "registration__event"),
        reference=reference,
    )
    if payment.user != request.user:
        raise Http404("No payment matches the given query.")

    if not (payment.is_wallet_push and payment.is_open):
        messages.info(request, "There's no prompt waiting to be sent again.")
        return redirect("payments:status", reference=payment.reference)

    # Re-check first: the prompt may have been approved while they were reaching
    # for the button, and re-pushing a paid transaction would be a second charge.
    _sync(payment)
    if not payment.is_open:
        return redirect("payments:status", reference=payment.reference)

    event = payment.registration.event
    client = PesepayClient()

    try:
        result = client.make_seamless_payment(
            amount=payment.amount,
            currency=payment.currency,
            reason=f"{event.title} — {event.organization.name}",
            result_url=_absolute(request, "payments:result", reference=payment.reference),
            method_code=settings.PESEPAY_METHOD_CODES.get(payment.method, payment.method),
            phone=payment.phone,
            email=request.user.email,
            name=request.user.display_name,
            reference=payment.reference,
        )
    except PesepayError as exc:
        messages.error(request, f"We couldn't reach Pesepay: {exc}")
        return redirect("payments:status", reference=payment.reference)

    if not result.ok:
        messages.error(request, result.error or "Pesepay wouldn't send the prompt again.")
        return redirect("payments:status", reference=payment.reference)

    payment.paynow_reference = result.reference or payment.paynow_reference
    payment.instructions = result.instructions
    payment.save(update_fields=["paynow_reference", "instructions", "updated_at"])

    messages.success(request, f"Sent again — check {payment.phone_masked}.")
    return redirect("payments:status", reference=payment.reference)


@login_required
def payment_status(request, reference):
    """The waiting room: polls until Paynow settles, then shows the ticket."""
    payment = get_object_or_404(
        Payment.objects.select_related("registration", "registration__event", "user"),
        reference=reference,
    )
    if payment.user != request.user and not payment.registration.event.can_manage(request.user):
        raise Http404("No payment matches the given query.")

    _sync(payment)

    return render(
        request,
        "payments/status.html",
        {"payment": payment, "event": payment.registration.event},
    )


@login_required
@ratelimit(key="user", rate="60/m", block=True)
def payment_status_json(request, reference):
    """Polled by the status page so it can move on the moment payment lands."""
    payment = get_object_or_404(Payment, reference=reference)
    if payment.user != request.user:
        raise Http404("No payment matches the given query.")

    # Throttled: this is the one caller that fires on a timer rather than in
    # response to something happening, so it is the one that has to be polite
    # about how often it reaches Pesepay.
    _sync(payment, min_interval=settings.PAYMENT_POLL_MIN_SECONDS)

    return JsonResponse(
        {
            "status": payment.status,
            "label": payment.get_status_display(),
            "settled": payment.is_settled,
            "open": payment.is_open,
            "expired": payment.has_expired,
            "ticket_url": payment.registration.get_absolute_url() if payment.is_settled else "",
            # The client asks again after this many seconds. Zero means there is
            # nothing left to wait for.
            "retry_in": _retry_in(payment),
        }
    )


@login_required
def payment_return(request, reference):
    """Where Paynow sends the student's browser back to after the redirect flow."""
    payment = get_object_or_404(Payment, reference=reference)
    if payment.user != request.user:
        raise Http404("No payment matches the given query.")

    _sync(payment)

    if payment.is_settled:
        messages.success(
            request, f"Payment received — {payment.amount_display}. Your ticket is confirmed."
        )
        return redirect(payment.registration.get_absolute_url())

    return redirect("payments:status", reference=payment.reference)


@csrf_exempt
@require_POST
# Unauthenticated by necessity, and each call makes us ask Pesepay about the
# transaction — so it is a free lever on our gateway quota for anyone who finds
# it. Both limits sit far above what a real gateway sends: it retries a handful
# of times per payment, not hundreds.
@ratelimit(key="ip", rate="120/m", method="POST", block=True)
@ratelimit(key=_by_reference, rate="12/m", method="POST", block=True)
def payment_result(request, reference):
    """The gateway's server-to-server callback.

    Exempt from CSRF because it isn't a browser request. Pesepay's callback carries no
    signature, so we don't authenticate it at all — we re-ask Pesepay instead (below).
    Paynow's does sign its payload, and that legacy path still verifies it.
    """
    payment = Payment.objects.filter(reference=reference).first()
    if payment is None:
        logger.warning("Gateway callback for unknown reference %s", reference)
        return HttpResponse("unknown reference", status=404)

    if payment.gateway == Payment.Gateway.PESEPAY:
        # The callback is only a nudge: we never take the posted body's word for
        # it, we ask Pesepay directly. That sidesteps having to authenticate an
        # unsigned POST, and means a forged callback can't confirm a ticket.
        with transaction.atomic():
            _sync_pesepay(payment)

        logger.info("Pesepay callback for %s: now %s", reference, payment.status)
        return HttpResponse("ok")

    # Paynow signs its callbacks, so verify and trust the payload.
    data = request.POST.dict()
    client = PaynowClient()

    if not client.is_simulated and not verify_hash(data, client.integration_key):
        logger.error("Paynow callback for %s failed signature verification", reference)
        return HttpResponse("bad hash", status=400)

    with transaction.atomic():
        changed = payment.apply_paynow_status(
            data.get("status", ""), data.get("paynowreference", "")
        )
        if payment.is_settled:
            payment.settle()

    logger.info("Paynow callback for %s: %s (changed=%s)", reference, data.get("status"), changed)
    return HttpResponse("ok")


# --------------------------------------------------------------------------
# Local simulator — only reachable when no Pesepay credentials are configured
# --------------------------------------------------------------------------


@login_required
def simulator(request, reference):
    """Stands in for the gateway's hosted page so the flow is testable offline."""
    if not PesepayClient().is_simulated:
        raise Http404("The simulator is disabled when live gateway credentials are set.")

    payment = get_object_or_404(
        Payment.objects.select_related("registration", "registration__event"), reference=reference
    )
    if payment.user != request.user:
        raise Http404("No payment matches the given query.")

    if request.method == "POST":
        outcome = request.POST.get("outcome")
        if outcome == "paid":
            with transaction.atomic():
                payment.apply_pesepay_status("SUCCESS", f"SIM{payment.pk:06d}")
                payment.settle()
            messages.success(
                request, f"Simulated payment of {payment.amount_display} approved."
            )
        elif outcome == "cancelled":
            payment.apply_pesepay_status("CANCELLED")
            messages.info(request, "Simulated payment cancelled.")
        else:
            payment.apply_pesepay_status("FAILED")
            messages.error(request, "Simulated payment failed.")

        return redirect("payments:return", reference=payment.reference)

    return render(
        request,
        "payments/simulator.html",
        {"payment": payment, "event": payment.registration.event},
    )


# --------------------------------------------------------------------------
# Payouts — what the platform owes the societies whose events it sold
# --------------------------------------------------------------------------


@login_required
def earnings(request):
    """A society's statement: what we hold, what we kept, what we've sent.

    Organizers see only their own societies. The whole point is that a society
    can check our arithmetic without asking us, so every figure here is
    reachable back to the individual tickets that make it up.
    """
    managed = request.user.managed_organizations().select_related("university")
    if not managed.exists():
        raise Http404("No societies to show earnings for.")

    societies = []
    for organization in managed:
        ledger = ledger_for(organization)
        ledger["organization"] = organization
        societies.append(ledger)

    payouts = (
        Payout.objects.filter(organization__in=managed)
        .select_related("organization")
        .exclude(status=Payout.Status.CANCELLED)[:25]
    )

    totals = {
        "outstanding": sum((row["outstanding"] for row in societies), Decimal("0")),
        "paid_out": sum((row["paid_out"] for row in societies), Decimal("0")),
        "fees": sum((row["fees"] for row in societies), Decimal("0")),
        "tickets": sum(row["tickets"] for row in societies),
    }

    return render(
        request,
        "payments/earnings.html",
        {
            "societies": societies,
            "payouts": payouts,
            "totals": totals,
            "fee_percent": getattr(settings, "PLATFORM_FEE_PERCENT", 0),
            "fee_fixed": getattr(settings, "PLATFORM_FEE_FIXED", 0),
            "crumbs": [
                {"label": "Manage", "url": reverse("events:dashboard")},
                {"label": "Earnings"},
            ],
            "page_subtitle": (
                "Ticket money is paid to us so a seat can be held the moment somebody "
                "starts paying. This is what we owe you, and what we've already sent."
            ),
        },
    )


@login_required
def payout_detail(request, reference):
    """One settlement, broken down to the tickets it covered.

    Visible to the society it belongs to and to platform staff — a statement
    nobody can open is a statement nobody trusts.
    """
    payout = get_object_or_404(
        Payout.objects.select_related("organization", "created_by"), reference=reference
    )

    if not (request.user.is_platform_staff or payout.organization.can_manage(request.user)):
        raise Http404("No payout matches the given query.")

    payments = payout.payments.select_related(
        "registration", "registration__event", "user"
    ).order_by("paid_at")

    return render(
        request,
        "payments/payout_detail.html",
        {
            "payout": payout,
            "payments": payments,
            "crumbs": [
                {"label": "Manage", "url": reverse("events:dashboard")},
                {"label": "Earnings", "url": reverse("payments:earnings")},
                {"label": payout.reference},
            ],
        },
    )


def _staff_only(request):
    if not request.user.is_platform_staff:
        raise Http404("Not found.")


@login_required
def payout_desk(request):
    """Staff view: every society with money waiting, and the button that sends it.

    Preparing a payout claims the sales it covers, so pressing this twice in a
    row cannot pay the same tickets out twice — the second press finds nothing
    left to claim.
    """
    _staff_only(request)

    return render(
        request,
        "payments/payout_desk.html",
        {
            "owed": societies_owed(),
            "prepared": Payout.objects.filter(status=Payout.Status.PENDING).select_related(
                "organization"
            ),
            "recent": Payout.objects.filter(status=Payout.Status.PAID).select_related(
                "organization"
            )[:15],
            "fee_percent": getattr(settings, "PLATFORM_FEE_PERCENT", 0),
        },
    )


@login_required
@require_POST
def payout_prepare(request, slug):
    _staff_only(request)

    organization = get_object_or_404(Organization, slug=slug)
    payout = prepare_payout(
        organization,
        by_user=request.user,
        method=request.POST.get("method") or Payout.Method.ECOCASH,
        destination=(request.POST.get("destination") or "").strip(),
    )

    if payout is None:
        messages.info(request, f"Nothing outstanding for {organization.name}.")
    else:
        messages.success(
            request,
            f"{payout.reference} prepared: {payout.amount_display} to {organization.name}, "
            f"covering {payout.ticket_count} ticket(s). Send the money, then mark it sent.",
        )

    return redirect("payments:payout_desk")


@login_required
@require_POST
def payout_mark_paid(request, reference):
    _staff_only(request)

    payout = get_object_or_404(Payout, reference=reference)
    external = (request.POST.get("external_reference") or "").strip()

    if not external:
        # Without the wallet's own code this row cannot be reconciled against a
        # statement later, which is most of what it is for.
        messages.error(
            request,
            "Enter the EcoCash or bank confirmation code before marking it sent.",
        )
    elif payout.mark_paid(
        by_user=request.user,
        external_reference=external,
        destination=(request.POST.get("destination") or "").strip(),
    ):
        mail.send_payout_sent(payout)
        messages.success(request, f"{payout.reference} marked as sent.")
    else:
        messages.info(request, "That payout was already sent.")

    return redirect("payments:payout_desk")


@login_required
@require_POST
def payout_cancel(request, reference):
    _staff_only(request)

    payout = get_object_or_404(Payout, reference=reference)
    try:
        payout.cancel()
    except ValueError as exc:
        messages.error(request, str(exc))
    else:
        messages.info(
            request,
            f"{payout.reference} cancelled — its {payout.ticket_count} ticket(s) are "
            "back in the outstanding pool.",
        )

    return redirect("payments:payout_desk")
