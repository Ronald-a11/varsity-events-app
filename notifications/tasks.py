"""Push messages, sent off the request thread.

Same shape as core/tasks.py: the callers hand over an id, the cluster does the
network work. Without a cluster these run inline, which is what development and
the tests want.
"""

import logging

from django.urls import reverse
from django.utils import timezone

logger = logging.getLogger(__name__)

# How far ahead the reminder looks. Run every ten minutes, a one-hour window
# with a ten-minute tail means everyone gets exactly one nudge, somewhere
# between fifty and sixty minutes before doors.
REMINDER_LEAD_MINUTES = 60
REMINDER_WINDOW_MINUTES = 10


# --------------------------------------------------------------------------
# The jobs
# --------------------------------------------------------------------------


def deliver_waitlist_promoted(registration_id):
    """A seat opened up, and the hold on it expires. The most urgent one there is."""
    from events.models import Registration

    from .push import send_to_user

    registration = Registration.objects.filter(pk=registration_id).select_related(
        "event", "user"
    ).first()
    if registration is None:
        return 0

    event = registration.event
    return send_to_user(
        registration.user,
        title=f"A place opened up — {event.title}",
        body=(
            "Your seat is held, but not for long. Tap to claim it."
            if not event.is_free
            else "You're in. Tap to see your ticket."
        ),
        url=registration.get_absolute_url(),
        tag=f"waitlist-{event.pk}",
    )


def deliver_ticket_ready(registration_id):
    """The money landed. They were watching a spinner for this."""
    from events.models import Registration

    from .push import send_to_user

    registration = Registration.objects.filter(pk=registration_id).select_related(
        "event", "user"
    ).first()
    if registration is None:
        return 0

    return send_to_user(
        registration.user,
        title="Your ticket is ready",
        body=f"{registration.event.title} · {registration.ticket_code}",
        url=registration.get_absolute_url(),
        tag=f"ticket-{registration.pk}",
    )


def deliver_event_cancelled(event_id):
    from events.models import Event, Registration

    from .push import send_to_user

    event = Event.objects.filter(pk=event_id).first()
    if event is None:
        return 0

    sent = 0
    holders = event.registrations.filter(
        status__in=[Registration.Status.CONFIRMED, Registration.Status.WAITLISTED]
    ).select_related("user")

    for registration in holders:
        sent += send_to_user(
            registration.user,
            title=f"Cancelled: {event.title}",
            body="The organizers have called this off. Tap for details.",
            url=event.get_absolute_url(),
            tag=f"cancelled-{event.pk}",
        )
    return sent


def send_door_reminders():
    """Nudge everyone holding a ticket for something starting within the hour.

    Scheduled — see notifications/apps.py. The window is deliberately narrow and
    keyed off a flag on the registration, so a cluster that restarts, catches up,
    or runs twice cannot send the same person the same reminder again. Being
    nagged is how you get notifications turned off for good.
    """
    from events.models import Registration

    from .push import is_configured, send_to_user

    if not is_configured():
        return 0

    now = timezone.now()
    opens_from = now + timezone.timedelta(
        minutes=REMINDER_LEAD_MINUTES - REMINDER_WINDOW_MINUTES
    )
    opens_to = now + timezone.timedelta(minutes=REMINDER_LEAD_MINUTES)

    due = (
        Registration.objects.filter(
            status=Registration.Status.CONFIRMED,
            reminded_at__isnull=True,
            event__status="published",
            event__starts_at__gte=opens_from,
            event__starts_at__lte=opens_to,
        )
        .select_related("event", "user")
    )

    sent = 0
    for registration in due:
        event = registration.event
        send_to_user(
            registration.user,
            title=f"Starting soon — {event.title}",
            body=f"{timezone.localtime(event.starts_at).strftime('%H:%M')} · {event.location_display}",
            url=registration.get_absolute_url(),
            tag=f"reminder-{event.pk}",
        )
        # Stamped whether or not it landed. A student with no subscription
        # shouldn't be reconsidered every ten minutes for the rest of the hour.
        registration.reminded_at = timezone.now()
        registration.save(update_fields=["reminded_at"])
        sent += 1

    if sent:
        logger.info("Sent %s door reminder(s)", sent, extra={"reminders": sent})
    return sent


# --------------------------------------------------------------------------
# How the rest of the app asks for them
# --------------------------------------------------------------------------


def _enqueue(path, *args):
    from core.tasks import enqueue

    return enqueue(path, *args)


def push_waitlist_promoted(registration):
    _enqueue("notifications.tasks.deliver_waitlist_promoted", registration.pk)


def push_ticket_ready(registration):
    _enqueue("notifications.tasks.deliver_ticket_ready", registration.pk)


def push_event_cancelled(event):
    _enqueue("notifications.tasks.deliver_event_cancelled", event.pk)
