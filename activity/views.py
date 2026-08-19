"""The live pulse board.

Everything this needs already existed on the model — `public()`, `since()`,
`for_feed()`, `as_dict()` — and the README has been advertising `/live/` for a
while. This is the part that was missing.

The feed reads strictly forward from the last id the client saw. No timestamps
to reconcile, no overlap window to tune, and no way to get a duplicate or a gap:
ids are monotonic and the index is on `-id`.
"""

from django.db.models import Count, Q
from django.http import JsonResponse
from django.shortcuts import render
from django.utils import timezone
from django_ratelimit.decorators import ratelimit

from events.models import Event, Registration

from .models import Activity

# How many rows the board opens with. Enough to look alive on a quiet Tuesday,
# few enough that the page isn't a scroll through last term.
INITIAL_ROWS = 40

# The most any one poll will hand back. A client that has been asleep in a
# background tab for an hour gets the most recent slice, not all of it.
MAX_NEW_ROWS = 30


def _board_queryset():
    return Activity.objects.public().for_feed()


@ratelimit(key="ip", rate="60/m", block=True)
def live_board(request):
    """What's happening across every campus, as it happens."""
    rows = list(_board_queryset()[:INITIAL_ROWS])
    now = timezone.now()

    context = {
        "activities": rows,
        # The client polls forward from here. Zero when the stream is empty,
        # which `since()` reads as "everything".
        "last_id": rows[0].pk if rows else 0,
        "stats": {
            "happening_now": Event.objects.published()
            .filter(starts_at__lte=now, ends_at__gte=now)
            .count(),
            "today": Activity.objects.public().filter(created_at__date=now.date()).count(),
            "tickets_today": Registration.objects.filter(
                created_at__date=now.date(), status=Registration.Status.CONFIRMED
            ).count(),
        },
        "busiest": Event.objects.published()
        .upcoming()
        .annotate(
            recent=Count(
                "activities",
                filter=Q(activities__created_at__gte=now - timezone.timedelta(days=1)),
            )
        )
        .filter(recent__gt=0)
        .select_related("organization", "organization__university", "category")
        .order_by("-recent")[:4],
        "crumbs": [{"label": "Live"}],
    }
    return render(request, "activity/live.html", context)


@ratelimit(key="ip", rate="120/m", block=True)
def feed_json(request):
    """Rows added since `?since=<id>`, oldest first.

    Oldest first on purpose: the client prepends each row to the top of the
    board, so handing them over newest-first would land them in reverse.
    """
    try:
        since = int(request.GET.get("since", 0))
    except (TypeError, ValueError):
        since = 0

    # Take the newest slice, then flip it. Ordering ascending and slicing would
    # hand back the *oldest* unseen rows, so a client that has been away comes
    # back to an hour-old feed and has to catch up one poll at a time.
    newest = list(Activity.objects.public().for_feed().since(since)[:MAX_NEW_ROWS])
    newest.reverse()

    return JsonResponse(
        {
            "rows": [row.as_dict() for row in newest],
            "last_id": newest[-1].pk if newest else since,
            # The board is a nice-to-have on somebody's data bundle, so it slows
            # down when nothing is happening. Same idea as the payment poller.
            "retry_in": 5 if newest else 15,
        }
    )
