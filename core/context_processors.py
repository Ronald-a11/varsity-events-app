from django.conf import settings

from accounts.models import University
from events.models import Category


def site_context(request):
    """Values every template can rely on."""
    return {
        "SITE_NAME": settings.SITE_NAME,
        "SITE_TAGLINE": settings.SITE_TAGLINE,
        "SITE_COUNTRY": settings.SITE_COUNTRY,
        "SITE_NAME_LEAD": settings.SITE_NAME_LEAD,
        "SITE_NAME_TAIL": settings.SITE_NAME_TAIL,
        "nav_categories": Category.objects.all()[:8],
        "nav_universities": University.objects.all()[:20],
        "pending_verifications": _pending_verifications(request),
        "wallets_accepted": _wallets_accepted(),
        # So the organizer templates can offer poster reading only where it
        # actually works, rather than linking to a route that redirects away.
        "poster_reader_on": _poster_reader_on(),
    }


def _poster_reader_on():
    from events.poster import is_configured

    return is_configured()


def _wallets_accepted():
    """Which wallets this merchant account can actually take.

    The footer and home page advertise payment methods; promising one the
    gateway will refuse is worse than not mentioning it at all.
    """
    labels = {"ecocash": "EcoCash", "onemoney": "OneMoney", "innbucks": "InnBucks"}
    return [
        label
        for wallet, label in labels.items()
        if settings.PESEPAY_METHOD_CODES.get(wallet)
    ]


def _pending_verifications(request):
    """Badge count for the Manage menu — only queried for people who'd act on it."""
    user = getattr(request, "user", None)
    if not user or not user.is_authenticated or not user.can_organize:
        return 0

    from payments.models import Payment

    payments = Payment.objects.filter(status=Payment.Status.AWAITING_VERIFICATION)
    if not user.is_platform_staff:
        payments = payments.filter(
            registration__event__organization__in=user.managed_organizations()
        )
    return payments.count()
