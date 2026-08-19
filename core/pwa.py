"""Installability and offline support.

The one thing this app produces that genuinely has to work without a network is
a ticket. A student arrives at a hall on the edge of campus, in a crowd, on a
prepaid bundle that ran out on the walk over, and needs to show a QR code. Every
part of the design below exists to serve that moment; the rest — installing to a
home screen, an offline shell — falls out of it for free.

Both the manifest and the service worker are rendered rather than served as
static files, because both need values only Django knows: the site name, the
hashed URL of the current stylesheet, and a version string that has to change on
every deploy or browsers will keep running last week's worker forever.
"""

import hashlib

from django.conf import settings
from django.http import HttpResponse
from django.shortcuts import render
from django.templatetags.static import static
from django.urls import reverse
from django.views.decorators.cache import cache_control

# Paths the service worker must never store or answer from a cache.
#
# Money first: a stale payment status is worse than no payment status — it can
# tell somebody a transaction failed when it succeeded, or the reverse. Then
# anything to do with identity, where a cached page on a shared campus laptop is
# the next person's problem.
NEVER_CACHE = [
    "/pay/",
    "/admin/",
    "/staff/",
    "/accounts/login",
    "/accounts/logout",
    "/accounts/register",
    "/accounts/password",
    "/healthz",
]


def version() -> str:
    """A string that changes whenever anything the worker caches changes.

    On Railway the commit SHA is exactly right. Failing that, the stylesheet's
    hashed URL moves whenever the CSS is rebuilt, which is the next best proxy
    for "the front end changed". Both fall back to the site name so a local
    `runserver` gets a stable worker rather than a new one every reload.
    """
    seed = (
        settings.__dict__.get("RAILWAY_GIT_COMMIT_SHA")
        or _static_url("css/app.css")
        or settings.SITE_NAME
    )
    return hashlib.sha256(seed.encode("utf-8")).hexdigest()[:12]


def _static_url(path):
    try:
        return static(path)
    except ValueError:
        # Manifest storage raises for a file it hasn't hashed; not worth failing
        # a page render over.
        return ""


@cache_control(max_age=60 * 60 * 24, immutable=False)
def manifest(request):
    """The web app manifest. Makes the site installable to a home screen."""
    context = {
        "icon_192": _static_url("img/icon-192.png"),
        "icon_512": _static_url("img/icon-512.png"),
        "icon_maskable": _static_url("img/icon-maskable-512.png"),
        "start_url": reverse("core:home"),
        "tickets_url": reverse("accounts:tickets"),
        "discover_url": reverse("core:discover"),
    }
    return render(
        request, "pwa/manifest.webmanifest", context, content_type="application/manifest+json"
    )


# Deliberately not cached. A worker that browsers hold on to is a worker you
# cannot replace, and the file is a couple of kilobytes.
@cache_control(max_age=0, no_cache=True, no_store=True, must_revalidate=True)
def service_worker(request):
    """The service worker itself, served from the root so its scope is the site.

    It has to answer from `/sw.js` rather than from `/static/`, because a worker
    can only control pages at or below its own path — one served out of
    `/static/` could cache the stylesheet and nothing else.
    """
    context = {
        "version": version(),
        "offline_url": reverse("core:offline"),
        "css_url": _static_url("css/app.css"),
        "icon_192": _static_url("img/icon-192.png"),
        "never_cache": NEVER_CACHE,
        # Kept out of the versioned caches: a ticket is still a ticket after a
        # deploy, and clearing it would take away the offline copy of the one
        # thing this is all for.
        "ticket_prefix": "/events/tickets/",
    }
    return render(
        request, "pwa/sw.js", context, content_type="application/javascript; charset=utf-8"
    )


def offline(request):
    """Shown when a page is asked for that isn't cached and can't be fetched."""
    response = render(request, "pwa/offline.html", {"tickets_url": reverse("accounts:tickets")})
    # No point storing this under its own URL; the worker precaches it directly.
    response["Cache-Control"] = "no-store"
    return response
