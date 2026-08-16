"""Drive believable traffic through the platform so the live board actually moves.

This does not fake the feed. It performs the same domain calls a real student
would trigger — registering, paying through the Paynow simulator, checking in,
following a society, leaving a review — so capacity, waitlists, revenue and the
activity stream all move together and stay consistent with each other.

    python manage.py simulate_activity                 # ~30 actions/min, runs until Ctrl-C
    python manage.py simulate_activity --rate 90       # busier
    python manage.py simulate_activity --burst 200     # one batch, no waiting
    python manage.py simulate_activity --duration 300  # stop after five minutes
    python manage.py simulate_activity --clean         # remove simulated rows and stop
"""

import random
import time
from decimal import Decimal

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from accounts.models import University, User
from activity.models import Activity, record
from events.models import Event, Registration, Review
from organizations.models import Membership, Organization
from payments.models import Payment

REVIEW_COMMENTS = [
    "Genuinely well organised — started on time, which never happens.",
    "Great turnout and the speakers actually knew their stuff.",
    "Best thing I've been to all semester. Do it again next term.",
    "Worth the trip. The Q&A was the strongest part.",
    "Good content, but it ran over by almost an hour.",
]

NOTES = ["", "", "", "Vegetarian please", "Travelling in from Gweru", "Might be 10 minutes late"]

FIRST_NAMES = [
    "Tendai", "Rutendo", "Tafadzwa", "Chiedza", "Farai", "Nyasha", "Tanaka", "Rumbidzai",
    "Takudzwa", "Anesu", "Kudzai", "Munashe", "Simbarashe", "Vimbai", "Tinashe", "Panashe",
    "Nokuthula", "Sibusiso", "Thandeka", "Bongani", "Nomsa", "Lindiwe", "Blessing", "Tapiwa",
]
LAST_NAMES = [
    "Moyo", "Ncube", "Sibanda", "Dube", "Chikwanha", "Mutasa", "Marufu", "Gwenzi",
    "Nyoni", "Chirwa", "Madziva", "Mangwiro", "Zvobgo", "Muchena", "Bhebhe", "Mpofu",
]
COURSES = [
    "BSc Computer Science", "LLB Law", "BSc Civil Engineering", "BSc Economics",
    "BSc Nursing Science", "BCom Accounting", "BA Media Studies", "BSc Agriculture",
    "BEng Electronic Engineering", "BSc Mining Engineering", "BA Development Studies",
]


class Command(BaseCommand):
    help = "Generate live platform activity by performing real domain actions."

    def add_arguments(self, parser):
        parser.add_argument("--rate", type=int, default=30, help="Actions per minute (default 30).")
        parser.add_argument("--duration", type=int, default=0, help="Seconds to run; 0 = forever.")
        parser.add_argument("--burst", type=int, default=0, help="Fire N actions at once and exit.")
        parser.add_argument("--clean", action="store_true", help="Delete simulated activity and exit.")
        parser.add_argument(
            "--force",
            action="store_true",
            help="Allow running against a non-DEBUG database. Think carefully.",
        )

    def handle(self, *args, **options):
        # This writes real rows. Never let it loose on production by accident.
        if not settings.DEBUG and not options["force"]:
            raise CommandError(
                "simulate_activity writes real registrations and payments and is refusing to "
                "run with DEBUG=False. Pass --force if you genuinely mean it."
            )

        if options["clean"]:
            return self.clean()

        self.students = list(
            User.objects.filter(role=User.Role.STUDENT, is_active=True).select_related("university")
        )
        self.organizations = list(Organization.objects.filter(is_active=True))

        if not self.students:
            raise CommandError("No student accounts. Run `manage.py seed_demo --reset` first.")

        burst = options["burst"]
        if burst:
            self.stdout.write(f"Firing {burst} actions…")
            performed = sum(int(bool(self.act())) for _ in range(burst))
            self.stdout.write(self.style.SUCCESS(f"Done — {performed} actions landed."))
            return

        rate = max(options["rate"], 1)
        interval = 60 / rate
        deadline = time.monotonic() + options["duration"] if options["duration"] else None

        self.stdout.write(
            self.style.SUCCESS(f"Simulating ~{rate} actions/minute. Ctrl-C to stop.")
        )
        self.stdout.write("Watch it at /live/\n")

        count = 0
        try:
            while deadline is None or time.monotonic() < deadline:
                description = self.act()
                if description:
                    count += 1
                    self.stdout.write(f"  {timezone.localtime():%H:%M:%S}  {description}")
                # Jitter, so the stream doesn't arrive on a metronome.
                time.sleep(interval * random.uniform(0.4, 1.6))
        except KeyboardInterrupt:
            self.stdout.write("")

        self.stdout.write(self.style.SUCCESS(f"Stopped after {count} actions."))

    # -- one unit of traffic --------------------------------------------

    def act(self):
        """Pick a weighted action and carry it out. Returns a log line, or None."""
        action = random.choices(
            [
                self.register,
                self.pay,
                self.check_in,
                self.follow,
                self.join,
                self.review,
                self.sign_up,
            ],
            weights=[38, 20, 13, 9, 6, 6, 8],
            k=1,
        )[0]

        try:
            with transaction.atomic():
                return action()
        except Exception as exc:  # a simulator must never die on one bad draw
            self.stderr.write(self.style.WARNING(f"  skipped: {exc}"))
            return None

    # -- individual actions ---------------------------------------------

    def _open_events(self):
        return list(
            Event.objects.published()
            .upcoming()
            .filter(starts_at__gt=timezone.now())
            .select_related("organization")
        )

    def sign_up(self):
        """A brand new student joins the platform."""
        first = random.choice(FIRST_NAMES)
        last = random.choice(LAST_NAMES)
        universities = list(University.objects.all())
        if not universities:
            return None

        # Keep usernames unique without a lookup loop.
        suffix = User.objects.count() + random.randint(1, 999)
        username = f"{first[0].lower()}{last.lower()}{suffix}"

        student = User.objects.create_user(
            username=username,
            email=f"{username}@students.ac.zw",
            password="demo12345",
            first_name=first,
            last_name=last,
            role=User.Role.STUDENT,
            university=random.choice(universities),
            course=random.choice(COURSES),
            year_of_study=random.randint(1, 5),
        )
        self.students.append(student)

        record(Activity.Verb.SIGNED_UP, actor=student, is_simulated=True)
        return f"{student.display_name} joined from {student.university}"

    def register(self):
        events = self._open_events()
        if not events:
            return None

        event = random.choice(events)
        # Bias towards events that already have momentum — that's how crowds behave.
        if random.random() < 0.4:
            event = max(random.sample(events, min(3, len(events))), key=lambda e: e.views_count)

        if not event.registration_open:
            return None

        # Demo data saturates quickly, so look for somebody who hasn't signed up
        # yet before giving up — and recruit a new student if nobody is left.
        student = None
        for candidate in random.sample(self.students, min(12, len(self.students))):
            if not event.registrations.filter(user=candidate).exists():
                student = candidate
                break

        if student is None:
            self.sign_up()
            student = self.students[-1]

        registration = event.register(student)
        if registration.notes == "":
            note = random.choice(NOTES)
            if note:
                registration.notes = note
                registration.save(update_fields=["notes"])

        Event.objects.filter(pk=event.pk).update(views_count=event.views_count + random.randint(1, 6))

        label = {
            Registration.Status.WAITLISTED: "joined the waitlist for",
            Registration.Status.AWAITING_PAYMENT: "reserved a paid place at",
        }.get(registration.status, "registered for")
        return f"{student.display_name} {label} {event.title}"

    def pay(self):
        """Settle a held seat through the Paynow simulator."""
        pending = list(
            Registration.objects.filter(status=Registration.Status.AWAITING_PAYMENT)
            .select_related("event", "user")[:40]
        )
        if not pending:
            return None

        registration = random.choice(pending)
        event = registration.event

        payment = registration.open_payment or Payment.objects.create(
            registration=registration,
            user=registration.user,
            amount=event.price or Decimal("1.00"),
            currency=event.currency,
            method=random.choices(
                [
                    Payment.Method.ECOCASH,
                    Payment.Method.ONEMONEY,
                    Payment.Method.INNBUCKS,
                    Payment.Method.WEB,
                ],
                weights=[58, 16, 14, 12],
            )[0],
            phone=f"07{random.choice('781')}{random.randint(1000000, 9999999)}",
            status=Payment.Status.SENT,
            is_simulated=True,
        )

        # Not everyone completes a checkout.
        if random.random() < 0.12:
            payment.apply_paynow_status("Cancelled")
            return f"{registration.user.display_name} abandoned payment for {event.title}"

        payment.apply_paynow_status("Paid", f"SIM{payment.pk:06d}")
        payment.settle()
        return f"{registration.user.display_name} paid {payment.amount_display} for {event.title}"

    def check_in(self):
        """Check people in at events that are running now or just started."""
        now = timezone.now()
        registration = (
            Registration.objects.filter(
                status=Registration.Status.CONFIRMED,
                checked_in_at__isnull=True,
                event__starts_at__lte=now + timezone.timedelta(hours=2),
                event__ends_at__gte=now,
            )
            .select_related("event", "user")
            .order_by("?")
            .first()
        )
        if registration is None:
            return None

        registration.check_in(by_user=registration.event.created_by)
        return f"{registration.user.display_name} checked in at {registration.event.title}"

    def follow(self):
        if not self.organizations:
            return None
        organization = random.choice(self.organizations)
        student = random.choice(self.students)

        if organization.followers.filter(pk=student.pk).exists():
            return None

        organization.followers.add(student)
        record(Activity.Verb.FOLLOWED, actor=student, organization=organization, is_simulated=True)
        return f"{student.display_name} followed {organization.name}"

    def join(self):
        if not self.organizations:
            return None
        organization = random.choice(self.organizations)
        student = random.choice(self.students)

        membership, created = Membership.objects.get_or_create(
            organization=organization, user=student, defaults={"role": Membership.Role.MEMBER}
        )
        if not created:
            return None

        record(Activity.Verb.JOINED, actor=student, organization=organization, is_simulated=True)
        return f"{student.display_name} joined {organization.name}"

    def review(self):
        """Only attendees of finished events can review, same as the real rule."""
        registration = (
            Registration.objects.filter(
                status=Registration.Status.CONFIRMED, event__ends_at__lt=timezone.now()
            )
            .select_related("event", "user")
            .order_by("?")
            .first()
        )
        if registration is None:
            return None
        if Review.objects.filter(event=registration.event, user=registration.user).exists():
            return None

        rating = random.choices([5, 4, 3, 2], weights=[5, 4, 2, 1])[0]
        Review.objects.create(
            event=registration.event,
            user=registration.user,
            rating=rating,
            comment=random.choice(REVIEW_COMMENTS),
        )
        record(
            Activity.Verb.REVIEWED,
            actor=registration.user,
            event=registration.event,
            rating=rating,
            is_simulated=True,
        )
        return f"{registration.user.display_name} gave {registration.event.title} {rating}★"

    # -- cleanup ---------------------------------------------------------

    def clean(self):
        activities, _ = Activity.objects.filter(is_simulated=True).delete()
        payments, _ = Payment.objects.filter(is_simulated=True).delete()
        self.stdout.write(
            self.style.SUCCESS(
                f"Removed {activities} simulated activity row(s) and {payments} simulated payment(s)."
            )
        )
        self.stdout.write(
            "Registrations were left alone — they're indistinguishable from real sign-ups by design."
        )
