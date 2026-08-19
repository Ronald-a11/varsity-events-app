"""Scheduled work for the payments app.

One job: retire checkouts nobody finished. Until now this happened as a side
effect of reading an event's capacity, which meant a page render could issue
writes — see the note on :attr:`events.models.Event.reserved_count`. Counting
no longer needs it, but the transitions themselves still have to happen: the
registration has to be cancelled, and whoever is next on the waitlist has to be
told a place opened up. Nobody is refreshing a page waiting for that, so it
belongs on a schedule.
"""

import logging

logger = logging.getLogger(__name__)


def release_abandoned_holds():
    """Cancel timed-out checkouts and offer their seats to the waitlist."""
    from .models import expire_stale_payments

    released = expire_stale_payments()
    if released:
        logger.info(
            "Released %s abandoned checkout%s", released, "" if released == 1 else "s",
            extra={"released": released},
        )
    return released
