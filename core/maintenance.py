"""A way for an outside scheduler to run the recurring jobs.

The task cluster normally does this. Running one needs a second service, and
Railway's free plan has no room for one — so this lets anything that can make an
HTTP request play that part instead: GitHub Actions on a cron, a free uptime
pinger, a laptop with crontab.

The alternative was giving a CI runner the production database URL. This keeps
those credentials inside Railway, where they belong, and works from any
scheduler without changing anything here.

Deliberately small. It runs two named, idempotent jobs and returns counts. It
takes no arguments, so there is nothing to inject, and running it twice by
mistake is harmless: the sweep finds nothing the second time, and reminders are
stamped on the registration so nobody is nagged twice.
"""

import logging
import secrets

from django.conf import settings
from django.http import Http404, JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST
from django_ratelimit.decorators import ratelimit

logger = logging.getLogger(__name__)


def is_configured() -> bool:
    return bool(getattr(settings, "TASK_TOKEN", ""))


def _authorised(request) -> bool:
    """Constant-time check of the shared secret.

    compare_digest rather than ==, because a plain comparison returns as soon as
    two bytes differ and that timing is enough to recover a token one character
    at a time.
    """
    presented = request.headers.get("X-Task-Token", "")
    if not presented:
        return False
    return secrets.compare_digest(presented, settings.TASK_TOKEN)


@csrf_exempt
@require_POST
@ratelimit(key="ip", rate="30/h", method="POST", block=True)
def run_scheduled_jobs(request):
    """Release abandoned checkouts and send door reminders.

    404 for both a missing token and a wrong one — the same answer an
    unconfigured deployment gives. A 403 would confirm to whoever is probing
    that the endpoint exists and only the secret is missing.
    """
    if not is_configured():
        raise Http404

    if not _authorised(request):
        # Logged, because the operator does want to know somebody is trying.
        logger.warning(
            "Rejected an unauthorised scheduled-jobs call",
            extra={"ip": request.META.get("REMOTE_ADDR", "")},
        )
        raise Http404

    from notifications.tasks import send_door_reminders
    from payments.tasks import release_abandoned_holds

    results = {}

    # Each job is allowed to fail on its own. A push service having a bad day
    # must not stop seats being released to a waitlist.
    for name, job in (
        ("released_holds", release_abandoned_holds),
        ("door_reminders", send_door_reminders),
    ):
        try:
            results[name] = job()
        except Exception:
            logger.exception("Scheduled job %s failed", name)
            results[name] = None

    logger.info("Ran the scheduled jobs", extra=results)
    return JsonResponse({"ok": True, **results})
