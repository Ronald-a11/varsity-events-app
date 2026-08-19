"""Work that shouldn't happen while a student waits for a page.

Sending mail was the worst offender: :meth:`events.models.Event.register` put
an SMTP round trip inside the request that issues a ticket, so a slow mail host
made buying a ticket feel broken. The send itself is unchanged — it still goes
through :mod:`core.mail`, which never raises — it just happens on the cluster
now.

Tasks take primary keys rather than model instances. A queued job is picked up
by a different process some seconds later, and the row may have moved on since;
re-reading it is both smaller on the wire and more likely to be right.

Without ``REDIS_URL`` there is no cluster, and everything here runs inline. That
is what development and the test suite want: the behaviour is identical, it just
blocks like it always did.
"""

import logging

from django.conf import settings
from django.utils.module_loading import import_string

logger = logging.getLogger(__name__)


def _run_now(dotted_path, *args, **kwargs):
    return import_string(dotted_path)(*args, **kwargs)


def enqueue(dotted_path, *args, **kwargs):
    """Hand a job to the cluster, or run it here if there isn't one.

    Falls back to running inline when the broker can't be reached. A Redis
    outage should slow ticket confirmation down, not stop it — the alternative
    is a student who has paid and never hears about it.
    """
    if settings.Q_CLUSTER.get("sync"):
        return _run_now(dotted_path, *args, **kwargs)

    try:
        from django_q.tasks import async_task

        return async_task(dotted_path, *args, **kwargs)
    except Exception:
        logger.exception("Could not queue %s — running it inline instead", dotted_path)
        return _run_now(dotted_path, *args, **kwargs)


# --------------------------------------------------------------------------
# The jobs themselves. These run on the cluster, so they take ids.
# --------------------------------------------------------------------------


def deliver_ticket_confirmed(registration_id):
    from events.models import Registration

    registration = Registration.objects.filter(pk=registration_id).select_related(
        "event", "event__organization", "user"
    ).first()
    if registration is None:
        logger.warning("Ticket email for registration %s: gone", registration_id)
        return False

    from core.mail import send_ticket_confirmed as _send

    return _send(registration)


def deliver_waitlist_promoted(registration_id):
    from events.models import Registration

    registration = Registration.objects.filter(pk=registration_id).select_related(
        "event", "event__organization", "user"
    ).first()
    if registration is None:
        logger.warning("Waitlist email for registration %s: gone", registration_id)
        return False

    from core.mail import send_waitlist_promoted as _send

    return _send(registration)


def deliver_payment_receipt(payment_id):
    from payments.models import Payment

    payment = Payment.objects.filter(pk=payment_id).select_related(
        "registration", "registration__event", "user"
    ).first()
    if payment is None:
        logger.warning("Receipt for payment %s: gone", payment_id)
        return False

    from core.mail import send_payment_receipt as _send

    return _send(payment)


def deliver_transfer_rejected(payment_id, rejected_code=""):
    from payments.models import Payment

    payment = Payment.objects.filter(pk=payment_id).select_related(
        "registration", "registration__event", "user"
    ).first()
    if payment is None:
        logger.warning("Rejection notice for payment %s: gone", payment_id)
        return False

    from core.mail import send_transfer_rejected as _send

    return _send(payment, rejected_code=rejected_code)


def deliver_transfer_awaiting_organizer(payment_id):
    from payments.models import Payment

    payment = Payment.objects.filter(pk=payment_id).select_related(
        "registration", "registration__event", "registration__event__organization"
    ).first()
    if payment is None:
        logger.warning("Organizer nudge for payment %s: gone", payment_id)
        return False

    from core.mail import send_transfer_awaiting_organizer as _send

    return _send(payment)


# --------------------------------------------------------------------------
# How the rest of the app asks for them.
#
# Same names and same arguments as the functions in core.mail, so a call site
# switches queue for direct send by changing which module it imports from.
# --------------------------------------------------------------------------


def send_ticket_confirmed(registration):
    enqueue("core.tasks.deliver_ticket_confirmed", registration.pk)


def send_waitlist_promoted(registration):
    enqueue("core.tasks.deliver_waitlist_promoted", registration.pk)


def send_payment_receipt(payment):
    enqueue("core.tasks.deliver_payment_receipt", payment.pk)


def send_transfer_rejected(payment, rejected_code=""):
    enqueue("core.tasks.deliver_transfer_rejected", payment.pk, rejected_code=rejected_code)


def send_transfer_awaiting_organizer(payment):
    enqueue("core.tasks.deliver_transfer_awaiting_organizer", payment.pk)
