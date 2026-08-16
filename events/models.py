import secrets

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.db.models import Count, Q
from django.urls import reverse
from django.utils import dateformat, timezone
from django.utils.text import slugify


def generate_ticket_code():
    """Human-readable, collision-resistant ticket reference, e.g. VE-8F3K-2QD7."""
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # no look-alike characters
    block = lambda: "".join(secrets.choice(alphabet) for _ in range(4))  # noqa: E731
    return f"VE-{block()}-{block()}"


class Category(models.Model):
    name = models.CharField(max_length=60, unique=True)
    slug = models.SlugField(max_length=70, unique=True, blank=True)
    description = models.CharField(max_length=200, blank=True)
    icon = models.CharField(max_length=8, default="🎓", help_text="Emoji shown in filters")
    color = models.CharField(
        max_length=20,
        default="indigo",
        help_text="Tailwind colour name: indigo, rose, amber, emerald, sky, violet, orange",
    )

    class Meta:
        verbose_name_plural = "categories"
        ordering = ["name"]

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = slugify(self.name)[:70]
        super().save(*args, **kwargs)

    def get_absolute_url(self):
        return f"{reverse('events:list')}?category={self.slug}"


class Venue(models.Model):
    name = models.CharField(max_length=140)
    university = models.ForeignKey(
        "accounts.University",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="venues",
    )
    address = models.CharField(max_length=255, blank=True)
    capacity = models.PositiveIntegerField(null=True, blank=True)
    map_url = models.URLField(blank=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return f"{self.name}, {self.university}" if self.university else self.name


class TicketStatus(models.TextChoices):
    """Organizer override for whether tickets can still be got hold of."""

    AUTO = "auto", "Automatic — from capacity and closing date"
    ON_SALE = "on_sale", "On sale"
    SOLD_OUT = "sold_out", "Sold out"
    UNAVAILABLE = "unavailable", "Not currently available"


class EventQuerySet(models.QuerySet):
    def published(self):
        return self.filter(status=Event.Status.PUBLISHED)

    def upcoming(self):
        return self.filter(ends_at__gte=timezone.now()).order_by("starts_at")

    def past(self):
        return self.filter(ends_at__lt=timezone.now()).order_by("-starts_at")

    def with_counts(self):
        """Annotate attendance figures so listings stay a single query.

        `confirmed_count` drives popularity; `reserved_total` also counts seats held
        by in-flight payments, which is what capacity should be measured against.
        """
        return self.annotate(
            confirmed_count=Count(
                "registrations",
                filter=Q(registrations__status=Registration.Status.CONFIRMED),
                distinct=True,
            ),
            reserved_total=Count(
                "registrations",
                filter=Q(
                    registrations__status__in=[
                        Registration.Status.CONFIRMED,
                        Registration.Status.AWAITING_PAYMENT,
                    ]
                ),
                distinct=True,
            ),
        )


class Event(models.Model):
    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        PUBLISHED = "published", "Published"
        CANCELLED = "cancelled", "Cancelled"

    class Visibility(models.TextChoices):
        PUBLIC = "public", "Anyone can see it"
        STUDENTS = "students", "Signed-in students only"
        MEMBERS = "members", "Society members only"

    title = models.CharField(max_length=180)
    slug = models.SlugField(max_length=200, unique=True, blank=True)
    summary = models.CharField(
        max_length=220, blank=True, help_text="One-line hook shown on cards"
    )
    description = models.TextField(blank=True)
    banner = models.ImageField(upload_to="events/banners/", blank=True, null=True)

    organization = models.ForeignKey(
        "organizations.Organization",
        on_delete=models.CASCADE,
        related_name="events",
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name="events_created",
    )
    category = models.ForeignKey(
        Category, on_delete=models.SET_NULL, null=True, blank=True, related_name="events"
    )
    tags = models.CharField(max_length=200, blank=True, help_text="Comma-separated keywords")

    venue = models.ForeignKey(
        Venue, on_delete=models.SET_NULL, null=True, blank=True, related_name="events"
    )
    location_note = models.CharField(
        max_length=200, blank=True, help_text="Room, floor or meeting point"
    )
    is_online = models.BooleanField(default=False)
    online_url = models.URLField(blank=True)

    starts_at = models.DateTimeField()
    ends_at = models.DateTimeField()
    registration_deadline = models.DateTimeField(
        null=True, blank=True, help_text="Defaults to the event start time"
    )

    capacity = models.PositiveIntegerField(
        null=True, blank=True, help_text="Leave blank for unlimited places"
    )
    allow_waitlist = models.BooleanField(default=True)
    requires_approval = models.BooleanField(
        default=False, help_text="Organizer approves each request manually"
    )

    is_free = models.BooleanField(default=True)
    price = models.DecimalField(max_digits=9, decimal_places=2, default=0)
    currency = models.CharField(max_length=3, default="USD")
    ticket_status = models.CharField(
        max_length=20,
        choices=TicketStatus.choices,
        default=TicketStatus.AUTO,
        help_text=(
            "Leave on automatic and we work it out from capacity and the closing date. "
            "Override it when tickets are sold somewhere we can't see."
        ),
    )
    ticket_notes = models.CharField(
        max_length=200,
        blank=True,
        help_text="Anything buyers should know, e.g. 'Cash or EcoCash only, student ID required'",
    )

    status = models.CharField(max_length=20, choices=Status.choices, default=Status.DRAFT)
    visibility = models.CharField(
        max_length=20, choices=Visibility.choices, default=Visibility.PUBLIC
    )
    is_featured = models.BooleanField(default=False)
    views_count = models.PositiveIntegerField(default=0)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = EventQuerySet.as_manager()

    class Meta:
        ordering = ["starts_at"]
        indexes = [
            models.Index(fields=["slug"]),
            models.Index(fields=["status", "starts_at"]),
            models.Index(fields=["starts_at"]),
        ]

    def __str__(self):
        return self.title

    def clean(self):
        if self.starts_at and self.ends_at and self.ends_at <= self.starts_at:
            raise ValidationError({"ends_at": "The end time must be after the start time."})
        if (
            self.registration_deadline
            and self.starts_at
            and self.registration_deadline > self.starts_at
        ):
            raise ValidationError(
                {"registration_deadline": "Registration must close before the event starts."}
            )
        if self.is_online and not self.online_url:
            raise ValidationError({"online_url": "Add the joining link for an online event."})
        if not self.is_free and self.price <= 0:
            raise ValidationError({"price": "Set a price above zero for a paid event."})

    def save(self, *args, **kwargs):
        if not self.slug:
            base = slugify(self.title)[:180] or "event"
            slug, counter = base, 2
            while Event.objects.filter(slug=slug).exclude(pk=self.pk).exists():
                slug = f"{base}-{counter}"
                counter += 1
            self.slug = slug
        if self.is_free:
            self.price = 0
        super().save(*args, **kwargs)

    def get_absolute_url(self):
        return reverse("events:detail", kwargs={"slug": self.slug})

    # --- derived state -------------------------------------------------

    @property
    def tag_list(self):
        return [t.strip() for t in self.tags.split(",") if t.strip()]

    @property
    def closes_at(self):
        return self.registration_deadline or self.starts_at

    @property
    def has_started(self):
        return timezone.now() >= self.starts_at

    @property
    def has_ended(self):
        return timezone.now() > self.ends_at

    @property
    def is_live(self):
        return self.starts_at <= timezone.now() <= self.ends_at

    @property
    def is_cancelled(self):
        return self.status == self.Status.CANCELLED

    @property
    def confirmed_registrations(self):
        return self.registrations.filter(status=Registration.Status.CONFIRMED)

    @property
    def attendee_count(self):
        return self.confirmed_registrations.count()

    @property
    def waitlist_count(self):
        return self.registrations.filter(status=Registration.Status.WAITLISTED).count()

    @property
    def checked_in_count(self):
        return self.registrations.filter(checked_in_at__isnull=False).count()

    @property
    def reserved_count(self):
        """Seats that are genuinely spoken for: confirmed, plus live checkouts.

        A paid registration holds its place while the student is on Paynow, so two
        people can't buy the last ticket at once. Abandoned checkouts are released
        first, so a hold can never outlive its window.
        """
        from payments.models import expire_stale_payments

        expire_stale_payments(event=self)
        return self.registrations.filter(
            status__in=[Registration.Status.CONFIRMED, Registration.Status.AWAITING_PAYMENT]
        ).count()

    @property
    def seats_left(self):
        if self.capacity is None:
            return None
        return max(self.capacity - self.reserved_count, 0)

    @property
    def is_full(self):
        return self.capacity is not None and self.reserved_count >= self.capacity

    @property
    def fill_percentage(self):
        if not self.capacity:
            return 0
        return min(int(self.attendee_count / self.capacity * 100), 100)

    @property
    def registration_open(self):
        return (
            self.status == self.Status.PUBLISHED
            and not self.has_ended
            and timezone.now() <= self.closes_at
            and self.ticket_status
            not in {TicketStatus.SOLD_OUT, TicketStatus.UNAVAILABLE}
        )

    @property
    def location_display(self):
        if self.is_online:
            return "Online"
        parts = [self.venue.name if self.venue else "", self.location_note]
        return " — ".join([p for p in parts if p]) or "Location to be announced"

    @property
    def price_display(self):
        return "Free" if self.is_free else f"{self.currency} {self.price:,.0f}"

    # --- ticket availability -------------------------------------------

    @property
    def availability(self):
        """How tickets stand right now, as a dict the templates can render directly.

        `state` is one of: free, on_sale, waitlist, sold_out, closed, unavailable.
        The organizer's `ticket_status` override always wins over the derived value.
        """
        def result(state, label, detail, tone):
            return {"state": state, "label": label, "detail": detail, "tone": tone}

        if self.is_cancelled:
            return result("unavailable", "Not available", "This event was cancelled.", "rose")

        if self.status == self.Status.DRAFT:
            return result("unavailable", "Not on sale yet", "This event hasn't been published.", "slate")

        if self.ticket_status == TicketStatus.SOLD_OUT:
            return result("sold_out", "Sold out", "Every ticket has gone.", "rose")

        if self.ticket_status == TicketStatus.UNAVAILABLE:
            return result(
                "unavailable",
                "Not currently available",
                "Tickets aren't on sale at the moment — check back with the organizers.",
                "amber",
            )

        if self.has_ended:
            return result("closed", "Event finished", "This event has already happened.", "slate")

        if timezone.now() > self.closes_at:
            return result(
                "closed",
                "Sales closed",
                # Django's own formatter, since strftime's %-d isn't portable to Windows.
                f"Ticket sales closed on {dateformat.format(timezone.localtime(self.closes_at), 'j M')}"
                f" at {dateformat.format(timezone.localtime(self.closes_at), 'H:i')}.",
                "slate",
            )

        if self.ticket_status == TicketStatus.ON_SALE:
            return result("on_sale", "On sale", "Tickets are available now.", "emerald")

        # Automatic, from capacity.
        if self.is_full:
            if self.allow_waitlist:
                return result(
                    "waitlist",
                    "Sold out — waitlist open",
                    "Every place is taken, but you can join the waitlist and we'll bump you up if someone drops out.",
                    "amber",
                )
            return result("sold_out", "Sold out", "Every place has been taken.", "rose")

        if self.is_free:
            detail = (
                f"{self.seats_left} free place{'' if self.seats_left == 1 else 's'} left."
                if self.capacity
                else "Free entry — no ticket needed in advance."
            )
            return result("free", "Free entry", detail, "emerald")

        detail = (
            f"{self.seats_left} ticket{'' if self.seats_left == 1 else 's'} left at {self.price_display}."
            if self.capacity
            else f"Tickets on sale at {self.price_display}."
        )
        return result("on_sale", "On sale", detail, "emerald")

    @property
    def can_still_get_tickets(self):
        return self.availability["state"] in {"free", "on_sale", "waitlist"}

    @property
    def tickets_left_display(self):
        """One short phrase covering how many tickets are actually left.

        Used on cards, listing rows and the event page so the answer to "can I
        still get in?" reads the same everywhere.
        """
        state = self.availability["state"]

        if state == "sold_out":
            return "Sold out"
        if state == "waitlist":
            return "Waitlist only"
        if state in {"unavailable", "closed"}:
            return ""
        if self.capacity is None:
            return "Unlimited places"

        left = self.seats_left
        if left == 0:
            return "Sold out"
        return f"{left} of {self.capacity} tickets left"

    @property
    def tickets_left_short(self):
        """The same figure, trimmed for tight spots like a card footer."""
        state = self.availability["state"]

        if state == "sold_out":
            return "Sold out"
        if state == "waitlist":
            return "Waitlist"
        if state in {"unavailable", "closed"} or self.capacity is None:
            return ""
        return f"{self.seats_left} left"

    @property
    def tickets_tone(self):
        """rose / amber / emerald, so the count is colour-coded consistently."""
        if self.capacity is None:
            return "emerald"
        if self.is_full:
            return "rose"
        if self.fill_percentage >= 85:
            return "rose"
        if self.fill_percentage >= 60:
            return "amber"
        return "emerald"

    @property
    def available_outlets(self):
        return self.outlets.filter(is_available=True)

    @property
    def has_sales_points(self):
        return self.outlets.exists()

    def registration_for(self, user):
        if not user.is_authenticated:
            return None
        return self.registrations.filter(user=user).exclude(
            status=Registration.Status.CANCELLED
        ).first()

    def can_manage(self, user):
        if not user.is_authenticated:
            return False
        return user.is_superuser or self.created_by_id == user.pk or self.organization.can_manage(user)

    def can_be_seen_by(self, user):
        if self.can_manage(user):
            return True
        if self.status == self.Status.DRAFT:
            return False
        if self.visibility == self.Visibility.PUBLIC:
            return True
        if not user.is_authenticated:
            return False
        if self.visibility == self.Visibility.STUDENTS:
            return True
        return self.organization.memberships.filter(user=user, is_active=True).exists()

    def register(self, user):
        """Register a user, moving them to the waitlist when the event is full.

        Returns the Registration. Raises ValidationError when registration is closed.
        """
        if not self.registration_open:
            raise ValidationError("Registration for this event is closed.")

        existing = self.registrations.filter(user=user).first()
        if existing and existing.status != Registration.Status.CANCELLED:
            return existing

        if self.requires_approval:
            status = Registration.Status.PENDING
        elif self.is_full:
            if not self.allow_waitlist:
                raise ValidationError("This event is fully booked.")
            status = Registration.Status.WAITLISTED
        elif not self.is_free:
            # Paid events hold the seat but stay unconfirmed until Paynow settles.
            status = Registration.Status.AWAITING_PAYMENT
        else:
            status = Registration.Status.CONFIRMED

        if existing:
            existing.status = status
            existing.cancelled_at = None
            existing.save(update_fields=["status", "cancelled_at"])
            self._record_signup(user, status)
            return existing

        registration = Registration.objects.create(event=self, user=user, status=status)
        self._record_signup(user, status)
        return registration

    def _record_signup(self, user, status):
        """Put the sign-up on the live stream, and flag the moment it sells out."""
        from activity.models import Activity, record

        verb = (
            Activity.Verb.WAITLISTED
            if status == Registration.Status.WAITLISTED
            else Activity.Verb.REGISTERED
        )
        record(verb, actor=user, event=self)

        if self.is_full and self.capacity:
            already = Activity.objects.filter(
                event=self, verb=Activity.Verb.SOLD_OUT
            ).exists()
            if not already:
                record(Activity.Verb.SOLD_OUT, event=self, organization=self.organization)

    def promote_from_waitlist(self):
        """Move the longest-waiting person into a freed seat.

        On a paid event they're promoted to *awaiting payment*, not confirmed —
        a freed seat is an invitation to buy, not a free ticket.
        """
        if self.is_full or not self.allow_waitlist:
            return None
        nxt = (
            self.registrations.filter(status=Registration.Status.WAITLISTED)
            .order_by("created_at")
            .first()
        )
        if nxt:
            nxt.status = (
                Registration.Status.CONFIRMED
                if self.is_free
                else Registration.Status.AWAITING_PAYMENT
            )
            nxt.save(update_fields=["status"])
        return nxt


class TicketOutlet(models.Model):
    """Somewhere a student can actually get hold of a ticket.

    Registration on this site is one route; societies also sell at the SU offices,
    on EcoCash, or on the door. Each outlet tracks its own stock so the event page
    can say precisely what has sold out and what hasn't.
    """

    class Kind(models.TextChoices):
        ONLINE = "online", "Online"
        CAMPUS = "campus", "On campus"
        PHONE = "phone", "Phone / mobile money"
        DOOR = "door", "On the door"
        PARTNER = "partner", "Partner outlet"

    ICONS = {
        Kind.ONLINE: "🌐",
        Kind.CAMPUS: "🏛️",
        Kind.PHONE: "📱",
        Kind.DOOR: "🚪",
        Kind.PARTNER: "🏪",
    }

    event = models.ForeignKey(Event, on_delete=models.CASCADE, related_name="outlets")
    kind = models.CharField(max_length=20, choices=Kind.choices, default=Kind.CAMPUS)
    name = models.CharField(
        max_length=140, help_text="e.g. SRC Offices, Great Hall foyer, or Ticketmaster Zimbabwe"
    )
    detail = models.CharField(
        max_length=200,
        blank=True,
        help_text="Opening hours, directions, or the mobile money code to dial",
    )
    url = models.URLField(blank=True, help_text="Link, if tickets are sold online")
    phone = models.CharField(max_length=40, blank=True)
    price_note = models.CharField(
        max_length=80, blank=True, help_text="e.g. 'USD 5 students / USD 8 general'"
    )
    is_available = models.BooleanField(
        default=True, help_text="Untick the moment this outlet runs out."
    )
    sort_order = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ["sort_order", "pk"]

    def __str__(self):
        return f"{self.name} ({self.get_kind_display()})"

    @property
    def icon(self):
        return self.ICONS.get(self.kind, "🎫")


class Registration(models.Model):
    """A person's ticket for an event."""

    class Status(models.TextChoices):
        PENDING = "pending", "Awaiting approval"
        AWAITING_PAYMENT = "awaiting_payment", "Awaiting payment"
        CONFIRMED = "confirmed", "Confirmed"
        WAITLISTED = "waitlisted", "Waitlisted"
        CANCELLED = "cancelled", "Cancelled"

    event = models.ForeignKey(Event, on_delete=models.CASCADE, related_name="registrations")
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="registrations"
    )
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.CONFIRMED)
    ticket_code = models.CharField(
        max_length=20, unique=True, default=generate_ticket_code, editable=False
    )
    notes = models.CharField(
        max_length=200, blank=True, help_text="Dietary needs, accessibility, questions"
    )
    checked_in_at = models.DateTimeField(null=True, blank=True)
    checked_in_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="check_ins_performed",
    )
    cancelled_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ("event", "user")
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["ticket_code"]), models.Index(fields=["status"])]

    def __str__(self):
        return f"{self.ticket_code} · {self.user} · {self.event}"

    def get_absolute_url(self):
        return reverse("events:ticket", kwargs={"code": self.ticket_code})

    @property
    def is_active(self):
        return self.status in {
            self.Status.CONFIRMED,
            self.Status.WAITLISTED,
            self.Status.PENDING,
            self.Status.AWAITING_PAYMENT,
        }

    @property
    def needs_payment(self):
        return self.status == self.Status.AWAITING_PAYMENT

    @property
    def open_payment(self):
        """The checkout the student should be sent back to, if there is one."""
        from payments.models import Payment

        return self.payments.filter(status__in=Payment.OPEN_STATUSES).first()

    @property
    def settled_payment(self):
        return self.payments.filter(
            status__in=["paid", "awaiting_delivery", "delivered"]
        ).first()

    @property
    def is_checked_in(self):
        return self.checked_in_at is not None

    def cancel(self):
        held_a_seat = self.status in {self.Status.CONFIRMED, self.Status.AWAITING_PAYMENT}
        self.status = self.Status.CANCELLED
        self.cancelled_at = timezone.now()
        self.save(update_fields=["status", "cancelled_at"])

        # Abandon any checkout still in flight so it can't settle onto a dead ticket.
        from payments.models import Payment

        self.payments.filter(status__in=Payment.OPEN_STATUSES).update(
            status=Payment.Status.CANCELLED
        )

        if held_a_seat:
            self.event.promote_from_waitlist()

    def check_in(self, by_user=None):
        from activity.models import Activity, record

        if self.checked_in_at:
            return False
        self.checked_in_at = timezone.now()
        self.checked_in_by = by_user
        self.save(update_fields=["checked_in_at", "checked_in_by"])

        record(Activity.Verb.CHECKED_IN, actor=self.user, event=self.event)
        return True


class Bookmark(models.Model):
    """A saved event ("interested")."""

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="bookmarks"
    )
    event = models.ForeignKey(Event, on_delete=models.CASCADE, related_name="bookmarks")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ("user", "event")
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.user} saved {self.event}"


class EventUpdate(models.Model):
    """An announcement posted by the organizer on the event page."""

    event = models.ForeignKey(Event, on_delete=models.CASCADE, related_name="updates")
    author = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name="event_updates"
    )
    title = models.CharField(max_length=140)
    body = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.event}: {self.title}"


class Review(models.Model):
    """Post-event feedback from an attendee."""

    event = models.ForeignKey(Event, on_delete=models.CASCADE, related_name="reviews")
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="reviews"
    )
    rating = models.PositiveSmallIntegerField(
        validators=[MinValueValidator(1), MaxValueValidator(5)]
    )
    comment = models.TextField(max_length=800, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ("event", "user")
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.rating}★ for {self.event} by {self.user}"
