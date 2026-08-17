"""Remove seeded demo content from a live deployment.

Deliberately narrower than `flush`: universities and categories are real
reference data, and superusers are how you get back into the admin. Both
survive. Everything a demo invents — societies, events, students, tickets,
payments — goes.
"""

from django.core.management.base import BaseCommand
from django.db import transaction

from accounts.models import University, User
from activity.models import Activity
from events.models import Bookmark, Category, Event, Registration, TicketOutlet, Venue
from organizations.models import Membership, Organization
from payments.models import Payment


class Command(BaseCommand):
    help = "Delete seeded demo content, keeping universities, categories and staff."

    def add_arguments(self, parser):
        parser.add_argument(
            "--yes",
            action="store_true",
            help="Required. Deleting production data should not be one keystroke.",
        )
        parser.add_argument(
            "--keep-reference",
            action="store_true",
            default=True,
            help="Keep universities and categories (default).",
        )
        parser.add_argument(
            "--purge-reference",
            action="store_true",
            help="Also delete universities and categories.",
        )

    def handle(self, *args, **options):
        if not options["yes"]:
            self.stdout.write(
                self.style.ERROR("Refusing to delete anything without --yes.")
            )
            return

        self.stdout.write(self.style.MIGRATE_HEADING("Before"))
        self._counts()

        with transaction.atomic():
            # Order matters only for readability; the FKs cascade either way.
            Activity.objects.all().delete()
            Payment.objects.all().delete()
            Registration.objects.all().delete()
            Bookmark.objects.all().delete()
            TicketOutlet.objects.all().delete()
            Event.objects.all().delete()
            Membership.objects.all().delete()
            Organization.objects.all().delete()
            Venue.objects.all().delete()

            # Keep anyone who can administer the platform. Deleting the last
            # superuser locks you out of your own admin.
            doomed = User.objects.filter(is_superuser=False, is_staff=False)
            # Count first: delete() returns the cascade total across every
            # model, which is a much bigger number than the users removed.
            people = doomed.count()
            doomed.delete()
            self.stdout.write(f"  removed {people} non-staff account(s)")

            if options["purge_reference"]:
                Category.objects.all().delete()
                University.objects.all().delete()

        self.stdout.write("")
        self.stdout.write(self.style.MIGRATE_HEADING("After"))
        self._counts()

        # Staff are deliberately never deleted — but `seed_demo` creates an
        # `admin` superuser with a well-known password, and silently keeping it
        # on a live site is how a demo account becomes a way in. Name every
        # account that survived, so nothing hides in a count.
        self.stdout.write("")
        self.stdout.write(self.style.MIGRATE_HEADING("Staff accounts kept"))
        for user in User.objects.filter(is_staff=True).order_by("username"):
            seeded = user.username == "admin"
            style = self.style.ERROR if seeded else self.style.SUCCESS
            note = "  <-- seeded by seed_demo, DELETE IT" if seeded else ""
            self.stdout.write(
                style(f"  {user.username} ({user.email or 'no email'}){note}")
            )

        self.stdout.write("")
        self.stdout.write(self.style.SUCCESS("Demo content cleared."))

    def _counts(self):
        for label, qs in (
            ("universities", University.objects.all()),
            ("categories", Category.objects.all()),
            ("venues", Venue.objects.all()),
            ("societies", Organization.objects.all()),
            ("events", Event.objects.all()),
            ("registrations", Registration.objects.all()),
            ("payments", Payment.objects.all()),
            ("activity", Activity.objects.all()),
            ("users", User.objects.all()),
            ("  of which staff", User.objects.filter(is_staff=True)),
        ):
            self.stdout.write(f"  {label:<18} {qs.count()}")
