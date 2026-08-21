"""Is this deployment actually ready to carry real students and real money?

`manage.py check --deploy` answers the settings half of that question. This
answers the half that lives in the database and can only be asked of a running
deployment: demo accounts nobody deleted, reference data nobody loaded, a
search index nobody rebuilt.

Read-only. It changes nothing, so it is safe to run against production as often
as you like, and it exits non-zero when something would actually hurt — which
makes it usable as the last step of a deploy pipeline.
"""

from django.contrib.auth.hashers import check_password
from django.core.management.base import BaseCommand
from django.conf import settings
from django.db import connection

from accounts.models import University, User
from events.models import Category, Event
from organizations.models import Organization
from payments.models import Payout, societies_owed

# The password `seed_demo` gives every account it creates. Anybody who has read
# the README knows it, which is the entire problem.
DEMO_PASSWORD = "demo12345"


class Result:
    def __init__(self):
        self.blockers = []
        self.warnings = []
        self.notes = []


class Command(BaseCommand):
    help = "Check whether this deployment is ready for real events."

    def add_arguments(self, parser):
        parser.add_argument(
            "--strict",
            action="store_true",
            help="Exit non-zero on warnings too, not just blockers.",
        )

    def handle(self, *args, **options):
        result = Result()

        self._check_demo_accounts(result)
        self._check_demo_content(result)
        self._check_reference_data(result)
        self._check_trust(result)
        self._check_money(result)
        self._check_optional(result)
        self._check_search(result)
        self._check_payouts(result)

        self.stdout.write("")
        self._report("Blockers", result.blockers, self.style.ERROR)
        self._report("Warnings", result.warnings, self.style.WARNING)
        self._report("Notes", result.notes, self.style.HTTP_INFO)

        self.stdout.write("")
        if result.blockers:
            self.stdout.write(
                self.style.ERROR(f"Not ready: {len(result.blockers)} blocker(s).")
            )
            raise SystemExit(1)
        if result.warnings and options["strict"]:
            self.stdout.write(self.style.WARNING("Warnings, and --strict was passed."))
            raise SystemExit(1)

        self.stdout.write(self.style.SUCCESS("Ready for real events."))

    # -- the questions --------------------------------------------------

    def _check_demo_accounts(self, result):
        """An account whose password is printed in a public README is a way in.

        Checked by hashing rather than by username, because renaming `admin` to
        `admin2` is exactly the sort of thing that feels like a fix and isn't.
        Privileged accounts are named individually; the rest are counted, since
        eighty identical lines is a way of not being read.
        """
        privileged, ordinary = [], 0

        for user in User.objects.filter(is_active=True).only(
            "username", "password", "is_superuser", "is_staff"
        ):
            if not user.password or not check_password(DEMO_PASSWORD, user.password):
                continue
            if user.is_superuser or user.is_staff:
                privileged.append(user.username)
            else:
                ordinary += 1

        for username in privileged:
            result.blockers.append(
                f"Staff account `{username}` still has the demo password from the README. "
                f"Change it or delete the account."
            )
        if ordinary:
            result.blockers.append(
                f"{ordinary} non-staff account(s) still have the demo password. "
                f"`manage.py clear_demo --yes` removes seeded accounts."
            )

    def _check_demo_content(self, result):
        """Seeded events are indistinguishable from real ones to a student."""
        seeded_banner = Event.objects.filter(
            banner__contains="-banner"
        ).exclude(banner="").count()
        if seeded_banner:
            result.warnings.append(
                f"{seeded_banner} event(s) still carry generated seed artwork. "
                f"`manage.py clear_demo --yes` removes seeded content."
            )

        if not Event.objects.exists():
            result.notes.append(
                "No events at all. The landing page will read zero - worth having "
                "a handful of real ones in before you send anybody the link."
            )

    def _check_reference_data(self, result):
        """clear_demo keeps these on purpose; a fresh database has neither."""
        if not University.objects.exists():
            result.blockers.append(
                "No universities. Nobody can sign up - the form requires one. "
                "Load them with `manage.py seed_demo --reference-only`."
            )
        if not Category.objects.exists():
            result.warnings.append(
                "No categories. Events can't be filed and the filters render empty."
            )

    def _check_trust(self, result):
        if not User.objects.filter(is_superuser=True, is_active=True).exists():
            result.blockers.append("No active superuser. Nobody can reach /staff/.")

        queued = Event.objects.awaiting_review().count()
        if queued:
            result.notes.append(f"{queued} event(s) waiting on review at /staff/?status=review.")

        unverified = Organization.objects.filter(is_verified=False, is_active=True).count()
        if unverified:
            result.notes.append(
                f"{unverified} society(ies) unverified - their events go through review."
            )

        unconfirmed = User.objects.filter(
            email_verified_at__isnull=True, is_staff=False, is_active=True
        ).count()
        if unconfirmed:
            result.notes.append(f"{unconfirmed} account(s) have not confirmed their email.")

    def _check_money(self, result):
        """Two ways to take money; having neither means every paid event is dead."""
        pesepay = bool(settings.PESEPAY_INTEGRATION_KEY and settings.PESEPAY_ENCRYPTION_KEY)
        direct = bool(settings.ECOCASH_DIRECT_ENABLED and settings.ECOCASH_MERCHANT_NUMBER)

        if not pesepay and not direct:
            if Event.objects.filter(is_free=False).exists():
                result.blockers.append(
                    "Paid events exist but no payment route is configured - "
                    "checkout would run the simulator, which takes no money."
                )
            else:
                result.warnings.append("No payment route configured; only free events will work.")
            return

        if not pesepay:
            result.warnings.append(
                "Pesepay is not configured, so card and wallet checkout falls back to "
                "the built-in simulator. Direct EcoCash still works."
            )
        if not direct:
            result.notes.append("Direct EcoCash transfer is off; Pesepay only.")

    def _check_optional(self, result):
        """The features that are inert without a key. Each one is a note, not a fault."""
        if not getattr(settings, "ANTHROPIC_API_KEY", ""):
            result.notes.append(
                "Poster import is off (no ANTHROPIC_API_KEY) - organizers must type "
                "every event in by hand."
            )
        if not (settings.VAPID_PUBLIC_KEY and settings.VAPID_PRIVATE_KEY):
            result.notes.append(
                "Push is off (no VAPID keys), so waitlist and doors-open alerts are email only."
            )
        if not getattr(settings, "SENTRY_DSN", ""):
            result.notes.append("No SENTRY_DSN - errors go to the log and nowhere else.")
        if not getattr(settings, "TASK_TOKEN", "") and not settings.TASKS_ASYNC:
            result.warnings.append(
                "Nothing runs the recurring jobs: no task cluster and no TASK_TOKEN for "
                "an outside scheduler. Abandoned checkouts will hold seats and door "
                "reminders will not go out."
            )

    def _check_search(self, result):
        """Postgres only: a null vector is an event that cannot be found."""
        if connection.vendor != "postgresql":
            result.notes.append(
                f"Running on {connection.vendor}. Full-text search needs Postgres; "
                "this deployment falls back to LIKE matching."
            )
            return

        missing = Event.objects.filter(search_vector__isnull=True).count()
        if missing:
            result.warnings.append(
                f"{missing} event(s) have no search vector and cannot be found by search. "
                f"Run `manage.py rebuild_search`."
            )

    def _check_payouts(self, result):
        """Money the platform is holding on somebody else's behalf."""
        owed = societies_owed()
        if owed:
            total = sum(row["outstanding"] for row in owed)
            result.notes.append(
                f"{total:.2f} owed to {len(owed)} society(ies) - see /pay/payouts/."
            )

        stuck = Payout.objects.filter(status=Payout.Status.PENDING).count()
        if stuck:
            result.warnings.append(
                f"{stuck} payout(s) prepared but never marked as sent. Either send the "
                f"money and confirm it, or cancel them so the tickets are claimable again."
            )

    # -- output ---------------------------------------------------------

    def _report(self, heading, items, style):
        if not items:
            return
        self.stdout.write(style(f"{heading}"))
        for item in items:
            self.stdout.write(style(f"  - {item}"))
        self.stdout.write("")
