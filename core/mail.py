"""Transactional email for Varsity Events.

Every send goes through :func:`send_mail`, which renders a matching HTML and
plain-text pair and — crucially — never raises. A ticket is confirmed the
moment the money lands; if the mail server happens to be down at that instant,
the student still gets their ticket and we log the failure instead of unwinding
a payment over an SMTP timeout.
"""

import logging

from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils.html import strip_tags

logger = logging.getLogger(__name__)


def absolute_url(path: str) -> str:
    """Build a link that works from an inbox, where there is no current request."""
    base = settings.SITE_BASE_URL.rstrip("/")
    return f"{base}{path}"


def send_mail(*, to, subject, template, context=None, reply_to=None) -> bool:
    """Render `emails/<template>.html` and send it. Returns whether it went.

    `to` may be a single address or a list. Addresses that are blank are
    dropped — plenty of seeded accounts have no email, and that is not an error
    worth raising in the middle of a payment.
    """
    recipients = [to] if isinstance(to, str) else list(to)
    recipients = [address for address in recipients if address]
    if not recipients:
        return False

    context = {
        "SITE_NAME": settings.SITE_NAME,
        "SITE_BASE_URL": settings.SITE_BASE_URL.rstrip("/"),
        "subject": subject,
        **(context or {}),
    }

    try:
        html = render_to_string(f"emails/{template}.html", context)
    except Exception:
        # A broken template is our bug, not the student's problem — log it with
        # the traceback and let the caller carry on.
        logger.exception("Could not render the %s email", template)
        return False

    try:
        text = render_to_string(f"emails/{template}.txt", context)
    except Exception:
        # Not every email needs a hand-written text part; fall back to the HTML.
        text = strip_tags(html)

    message = EmailMultiAlternatives(
        subject=subject,
        body=text,
        from_email=settings.DEFAULT_FROM_EMAIL,
        to=recipients,
        reply_to=[reply_to] if reply_to else None,
    )
    message.attach_alternative(html, "text/html")

    try:
        message.send(fail_silently=False)
    except Exception:
        logger.exception("Could not send the %s email to %s", template, recipients)
        return False

    logger.info("Sent %s to %s", template, recipients)
    return True


# -- the actual messages ------------------------------------------------


def send_ticket_confirmed(registration) -> bool:
    """The one email that really matters: proof of a ticket."""
    event = registration.event
    return send_mail(
        to=registration.user.email,
        subject=f"Your ticket for {event.title}",
        template="ticket_confirmed",
        context={
            "registration": registration,
            "event": event,
            "user": registration.user,
            "ticket_url": absolute_url(registration.get_absolute_url()),
            "event_url": absolute_url(event.get_absolute_url()),
        },
        reply_to=event.organization.email or None,
    )


def send_waitlist_promoted(registration) -> bool:
    """Time-critical: a place opened up and the hold expires."""
    event = registration.event
    path = (
        event.get_absolute_url()
        if event.is_free
        else reverse("payments:checkout", kwargs={"slug": event.slug})
    )
    return send_mail(
        to=registration.user.email,
        subject=f"A place opened up for {event.title}",
        template="waitlist_promoted",
        context={
            "registration": registration,
            "event": event,
            "user": registration.user,
            "action_url": absolute_url(path),
        },
        reply_to=event.organization.email or None,
    )


def send_payment_receipt(payment) -> bool:
    event = payment.registration.event
    return send_mail(
        to=payment.user.email,
        subject=f"Receipt · {payment.amount_display} for {event.title}",
        template="payment_receipt",
        context={
            "payment": payment,
            "event": event,
            "user": payment.user,
            "ticket_url": absolute_url(payment.registration.get_absolute_url()),
        },
    )


def send_transfer_rejected(payment, rejected_code="") -> bool:
    """Their EcoCash code didn't match — say so plainly, and how to fix it."""
    event = payment.registration.event
    return send_mail(
        to=payment.user.email,
        subject=f"We couldn't match your payment for {event.title}",
        template="transfer_rejected",
        context={
            "payment": payment,
            "event": event,
            "user": payment.user,
            "rejected_code": rejected_code or payment.confirmation_code,
            "retry_url": absolute_url(
                reverse("payments:checkout", kwargs={"slug": event.slug})
            ),
        },
        reply_to=event.organization.email or None,
    )


def send_transfer_awaiting_organizer(payment) -> bool:
    """Nudge whoever has to check the wallet, so students aren't left waiting."""
    event = payment.registration.event
    recipients = [
        member.email for member in event.organization.managers() if member.email
    ]
    return send_mail(
        to=recipients,
        subject=f"EcoCash code to check · {event.title}",
        template="transfer_awaiting",
        context={
            "payment": payment,
            "event": event,
            "queue_url": absolute_url(reverse("payments:verify")),
        },
    )


def send_event_approved(event) -> bool:
    """A queued event cleared review. Tell whoever has to promote it."""
    recipients = [member.email for member in event.organization.managers() if member.email]
    return send_mail(
        to=recipients,
        subject=f"“{event.title}” is live",
        template="event_approved",
        context={
            "event": event,
            "event_url": absolute_url(event.get_absolute_url()),
            "manage_url": absolute_url(
                reverse("events:manage_attendees", kwargs={"slug": event.slug})
            ),
        },
    )


def send_event_sent_back(event, note="") -> bool:
    """Review said no. The reason travels with it, or this is just a shrug."""
    recipients = [member.email for member in event.organization.managers() if member.email]
    return send_mail(
        to=recipients,
        subject=f"“{event.title}” needs another look",
        template="event_sent_back",
        context={
            "event": event,
            "note": note or event.review_note,
            "edit_url": absolute_url(reverse("events:edit", kwargs={"slug": event.slug})),
        },
    )


def send_claim_received(claim) -> bool:
    """Acknowledge a society claim, so it doesn't feel like shouting into a well."""
    return send_mail(
        to=claim.user.email,
        subject=f"We've got your claim on {claim.organization.name}",
        template="claim_received",
        context={
            "claim": claim,
            "organization": claim.organization,
            "user": claim.user,
            "organization_url": absolute_url(claim.organization.get_absolute_url()),
        },
    )


def send_claim_approved(claim) -> bool:
    return send_mail(
        to=claim.user.email,
        subject=f"{claim.organization.name} is yours",
        template="claim_approved",
        context={
            "claim": claim,
            "organization": claim.organization,
            "user": claim.user,
            "organization_url": absolute_url(claim.organization.get_absolute_url()),
            "create_url": absolute_url(reverse("events:create")),
        },
    )


def send_claim_rejected(claim, note="") -> bool:
    """A refusal that doesn't say why is a refusal somebody argues with."""
    return send_mail(
        to=claim.user.email,
        subject=f"About your claim on {claim.organization.name}",
        template="claim_rejected",
        context={
            "claim": claim,
            "organization": claim.organization,
            "user": claim.user,
            "note": note or claim.review_note,
        },
    )


def send_payout_sent(payout) -> bool:
    """The money left. Tell the society, with the code to check against."""
    recipients = [member.email for member in payout.organization.managers() if member.email]
    return send_mail(
        to=recipients,
        subject=f"{payout.amount_display} sent to {payout.organization.name}",
        template="payout_sent",
        context={
            "payout": payout,
            "organization": payout.organization,
            "statement_url": absolute_url(
                reverse("payments:payout_detail", kwargs={"reference": payout.reference})
            ),
        },
    )


def send_report_alert(report) -> bool:
    """Tell platform staff something on the feed has been reported.

    Sent on the *first* open report about a thing, not on every one. Twenty
    people reporting the same scam is one alert; twenty emails is a filter rule
    and then a missed alert.
    """
    from accounts.models import User

    recipients = [
        person.email
        for person in User.objects.filter(is_staff=True, is_active=True)
        if person.email
    ]
    return send_mail(
        to=recipients,
        subject=f"Reported: {report.target_name}",
        template="report_alert",
        context={
            "report": report,
            "target_name": report.target_name,
            "target_kind": report.target_kind,
            "target_url": absolute_url(report.target.get_absolute_url()),
            "queue_url": absolute_url(reverse("core:staff_reports")),
        },
    )
