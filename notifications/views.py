"""Subscribing to, and unsubscribing from, push notifications.

There is no "enable notifications?" prompt on page load anywhere in this app,
and there should never be one. A browser gives a site exactly one chance to ask:
deny it and the permission is buried three menus deep, effectively forever. So
the ask is attached to a moment where the answer is obviously yes — you have just
got a ticket, and you would like to be told when doors open.
"""

import json
import logging

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.views.decorators.http import require_POST
from django_ratelimit.decorators import ratelimit

from .models import PushSubscription
from .push import is_configured

logger = logging.getLogger(__name__)


def _payload(request):
    try:
        return json.loads(request.body or "{}")
    except json.JSONDecodeError:
        return {}


@login_required
@require_POST
@ratelimit(key="user", rate="20/h", method="POST", block=True)
def subscribe(request):
    """Store the subscription a browser has just handed us."""
    if not is_configured():
        return JsonResponse({"ok": False, "error": "Push isn't set up here."}, status=503)

    data = _payload(request)
    endpoint = (data.get("endpoint") or "").strip()
    keys = data.get("keys") or {}

    if not endpoint or not keys.get("p256dh") or not keys.get("auth"):
        return JsonResponse({"ok": False, "error": "Incomplete subscription."}, status=400)

    # The endpoint is unique per device, and a browser hands back the same one
    # after a refresh — so this is an update as often as it is a create. Keyed
    # on the endpoint alone, with the user in the defaults, a device that
    # changes hands moves to whoever is signed in on it now.
    subscription, created = PushSubscription.objects.update_or_create(
        endpoint=endpoint,
        defaults={
            "user": request.user,
            "p256dh": keys["p256dh"],
            "auth": keys["auth"],
            "user_agent": request.headers.get("User-Agent", "")[:200],
            "failures": 0,
        },
    )

    logger.info(
        "Push subscription %s for %s", "created" if created else "refreshed", request.user
    )
    return JsonResponse({"ok": True, "created": created})


@login_required
@require_POST
def unsubscribe(request):
    """Forget a device. Called when someone turns notifications off."""
    endpoint = (_payload(request).get("endpoint") or "").strip()
    if not endpoint:
        return JsonResponse({"ok": False, "error": "No endpoint given."}, status=400)

    # Scoped to the signed-in user: an endpoint is not a secret worth trusting
    # as authorisation to delete somebody else's subscription.
    deleted, _ = PushSubscription.objects.filter(
        user=request.user, endpoint=endpoint
    ).delete()

    return JsonResponse({"ok": True, "deleted": bool(deleted)})


def public_key(request):
    """The VAPID public key, which the browser needs before it can subscribe.

    Public by name and by nature — it is handed to every push service on every
    send. The private half never leaves the server.
    """
    return JsonResponse(
        {
            "configured": is_configured(),
            "key": settings.VAPID_PUBLIC_KEY if is_configured() else "",
        }
    )
