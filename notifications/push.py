"""Sending a Web Push message.

Push is not email. There is no inbox to catch up on, no unread count, and a
notification a student didn't want is the fastest way to have them turn the
whole thing off — permanently, since browsers make a denied permission very hard
to grant again. So the rule this module is built around is that a push is only
worth sending when it is **time-critical and actionable**:

    - a seat opened up and the hold expires    (they must act now)
    - the money landed and the ticket is live  (they were waiting on it)
    - doors open in an hour                    (they wanted to be there)

Anything else is an email. See core/mail.py.

Delivery is best-effort, exactly like mail: a push service having a bad day must
never unwind a payment or a promotion. Failures are logged and, when the service
says the subscription is dead, the row goes with it.
"""

import json
import logging

from django.conf import settings

logger = logging.getLogger(__name__)

# The push service replies with one of these when the subscription no longer
# exists — an uninstalled browser, a cleared profile, a revoked permission.
# There is nothing to retry; the row is simply wrong and should go.
DEAD_SUBSCRIPTION = {404, 410}


def is_configured() -> bool:
    """Whether push can work at all. Without keys every send is a quiet no-op."""
    return bool(settings.VAPID_PRIVATE_KEY and settings.VAPID_PUBLIC_KEY)


def send_to_subscription(subscription, payload: dict) -> bool:
    """Push one message to one device. Returns whether it landed."""
    if not is_configured():
        logger.debug("Push not configured; dropping %s", payload.get("title"))
        return False

    from pywebpush import WebPushException, webpush

    try:
        webpush(
            subscription_info=subscription.as_payload(),
            data=json.dumps(payload),
            vapid_private_key=settings.VAPID_PRIVATE_KEY,
            # The push service uses this to reach us if something is wrong. It
            # must be a mailto: or an https: URL, and it must be ours.
            vapid_claims={"sub": settings.VAPID_ADMIN_EMAIL},
            timeout=10,
        )
    except WebPushException as exc:
        status = getattr(exc.response, "status_code", None)

        if status in DEAD_SUBSCRIPTION:
            logger.info(
                "Push subscription %s is gone (%s); removing it", subscription.pk, status
            )
            subscription.delete()
            return False

        subscription.failures += 1
        subscription.save(update_fields=["failures"])
        logger.warning(
            "Push to subscription %s failed",
            subscription.pk,
            extra={"status": status, "failures": subscription.failures},
        )
        return False
    except Exception:
        # A DNS blip, a TLS error, anything else. Best-effort by design.
        logger.exception("Push to subscription %s raised", subscription.pk)
        return False

    subscription.mark_used()
    return True


def send_to_user(user, *, title, body, url="", tag="") -> int:
    """Push to every device this person has agreed to. Returns how many landed.

    `tag` lets a later message replace an earlier one on the device rather than
    stacking beside it — three "doors open soon" notifications for the same
    event is how you get the permission revoked.
    """
    from .models import PushSubscription

    if not is_configured():
        return 0

    payload = {"title": title, "body": body, "url": url, "tag": tag or url}

    landed = 0
    for subscription in PushSubscription.objects.filter(user=user).select_related("user"):
        if send_to_subscription(subscription, payload):
            landed += 1

    return landed
