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
        "settings_nav": _settings_nav(request),
    }


# The account pages that share the settings shell, in the order they appear.
SETTINGS_PAGES = [
    ("profile", "Profile", "👤"),
    ("profile_edit", "Edit profile", "✏️"),
    ("password_change", "Password", "🔑"),
    ("two_factor_manage", "Security", "🛡️"),
]

# The security pages are steps within Security, so the rail keeps that item lit
# rather than going blank halfway through enrolling.
SECURITY_STEPS = {
    "two_factor_manage",
    "two_factor_setup",
    "two_factor_verify",
    "two_factor_codes",
    "two_factor_regenerate_codes",
}


def _settings_nav(request):
    """The rail down the side of the account pages.

    Built here rather than in each view so that adding a settings page is a
    template change and a line in the list above, not four edits. Returns None
    everywhere else, which costs one dictionary lookup on pages that don't use
    it.
    """
    match = getattr(request, "resolver_match", None)
    user = getattr(request, "user", None)
    if match is None or not user or not user.is_authenticated:
        return None
    if match.namespace != "accounts":
        return None

    current = match.url_name
    if current not in {name for name, _, _ in SETTINGS_PAGES} | SECURITY_STEPS:
        return None

    from django.urls import reverse

    items = []
    for name, label, icon in SETTINGS_PAGES:
        active = current == name or (name == "two_factor_manage" and current in SECURITY_STEPS)
        items.append(
            {
                "label": label,
                "icon": icon,
                "url": reverse(f"accounts:{name}"),
                "active": active,
                # One quiet dot, on the one thing an account can be behind on.
                "badge": (
                    "Email not confirmed"
                    if name == "profile_edit" and not user.email_is_verified
                    else ""
                ),
            }
        )
    return items


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
