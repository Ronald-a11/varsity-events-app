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
