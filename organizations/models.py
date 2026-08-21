from django.conf import settings
from django.db import models
from django.urls import reverse
from django.utils import timezone
from django.utils.text import slugify


class Organization(models.Model):
    """A student society, club, faculty body or campus department."""

    class Kind(models.TextChoices):
        SOCIETY = "society", "Society"
        CLUB = "club", "Club"
        FACULTY = "faculty", "Faculty / School"
        SPORTS = "sports", "Sports team"
        UNION = "union", "Student union"
        DEPARTMENT = "department", "Department"

    name = models.CharField(max_length=140)
    slug = models.SlugField(max_length=160, unique=True, blank=True)
    kind = models.CharField(max_length=20, choices=Kind.choices, default=Kind.SOCIETY)
    tagline = models.CharField(max_length=160, blank=True)
    description = models.TextField(blank=True)
    logo = models.ImageField(upload_to="orgs/logos/", blank=True, null=True)
    cover = models.ImageField(upload_to="orgs/covers/", blank=True, null=True)
    university = models.ForeignKey(
        "accounts.University",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="organizations",
    )
    email = models.EmailField(blank=True)
    website = models.URLField(blank=True)
    instagram = models.CharField(max_length=80, blank=True)
    twitter = models.CharField(max_length=80, blank=True)
    is_verified = models.BooleanField(
        default=False, help_text="Verified societies get a badge and can publish instantly."
    )
    is_active = models.BooleanField(default=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="organizations_created",
    )
    members = models.ManyToManyField(
        settings.AUTH_USER_MODEL, through="Membership", related_name="organizations"
    )
    followers = models.ManyToManyField(
        settings.AUTH_USER_MODEL, blank=True, related_name="followed_organizations"
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name"]
        indexes = [models.Index(fields=["slug"]), models.Index(fields=["kind"])]

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        if not self.slug:
            base = slugify(self.name)[:150] or "society"
            slug, counter = base, 2
            while Organization.objects.filter(slug=slug).exclude(pk=self.pk).exists():
                slug = f"{base}-{counter}"
                counter += 1
            self.slug = slug
        super().save(*args, **kwargs)

    def get_absolute_url(self):
        return reverse("organizations:detail", kwargs={"slug": self.slug})

    @property
    def initials(self):
        parts = [p for p in self.name.split() if p]
        return "".join(p[0] for p in parts[:2]).upper() or "S"

    @property
    def member_count(self):
        return self.memberships.filter(is_active=True).count()

    @property
    def follower_count(self):
        return self.followers.count()

    def upcoming_events(self):
        from events.models import Event

        return self.events.filter(status=Event.Status.PUBLISHED).upcoming()

    def can_manage(self, user):
        if not user.is_authenticated:
            return False
        if user.is_superuser:
            return True
        return self.memberships.filter(
            user=user, is_active=True, role__in=[Membership.Role.OWNER, Membership.Role.ADMIN]
        ).exists()

    @property
    def is_unclaimed(self):
        """No active owner — listed by us, or abandoned when a committee left.

        The distinction matters to the person reading the page: claiming an
        unclaimed society is a formality, claiming an owned one is asking
        somebody to be replaced.
        """
        return not self.memberships.filter(
            is_active=True, role=Membership.Role.OWNER
        ).exists()

    def open_claim_from(self, user):
        if not user.is_authenticated:
            return None
        return self.claims.filter(user=user, status=OrganizationClaim.Status.PENDING).first()

    def managers(self):
        """The people who can act on this society — who notifications go to."""
        from accounts.models import User

        return User.objects.filter(
            memberships__organization=self,
            memberships__is_active=True,
            memberships__role__in=[Membership.Role.OWNER, Membership.Role.ADMIN],
        ).distinct()


class OrganizationClaim(models.Model):
    """Somebody saying "this is my society, and I should be running it".

    The directory only fills up if it can be populated ahead of its members:
    staff list the societies a campus actually has, from public information,
    and the real committee arrives afterwards and takes the page over. Without
    this, every society page has to wait for the one person who happens to
    both run it and find us first, and a directory of empty campuses is not a
    directory anybody comes back to.

    Deliberately a request rather than a button. Handing over a society hands
    over its events, its attendee lists and its takings, so a person decides.
    """

    class Status(models.TextChoices):
        PENDING = "pending", "Awaiting review"
        APPROVED = "approved", "Approved"
        REJECTED = "rejected", "Rejected"

    organization = models.ForeignKey(
        Organization, on_delete=models.CASCADE, related_name="claims"
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="organization_claims"
    )
    role_title = models.CharField(
        max_length=80, help_text="Your position, e.g. Secretary, Chairperson, Treasurer"
    )
    evidence = models.TextField(
        max_length=800,
        help_text="How we can tell this is yours: student number, a staff member who "
        "can confirm it, the society's own social account, a photo of the minutes.",
    )
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    created_at = models.DateTimeField(auto_now_add=True)
    reviewed_at = models.DateTimeField(null=True, blank=True)
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="claims_reviewed",
    )
    review_note = models.CharField(max_length=300, blank=True)

    class Meta:
        ordering = ["created_at"]
        constraints = [
            # One open claim per person per society. Re-asking louder is not
            # evidence, and a queue full of duplicates is a queue nobody works.
            models.UniqueConstraint(
                fields=["organization", "user"],
                condition=models.Q(status="pending"),
                name="one_open_claim_per_person_per_society",
            )
        ]

    def __str__(self):
        return f"{self.user} claims {self.organization} ({self.get_status_display()})"

    @property
    def is_pending(self):
        return self.status == self.Status.PENDING

    def approve(self, by_user):
        """Hand the society over, and make the claimant an organizer.

        Owner rather than admin: whoever proved they run it needs to be able to
        add the rest of their committee without coming back to us.
        """
        from accounts.models import User

        membership, _ = Membership.objects.update_or_create(
            organization=self.organization,
            user=self.user,
            defaults={
                "role": Membership.Role.OWNER,
                "title": self.role_title,
                "is_active": True,
            },
        )
        self.organization.followers.add(self.user)

        if self.user.role == User.Role.STUDENT:
            self.user.role = User.Role.ORGANIZER
            self.user.save(update_fields=["role"])

        self.status = self.Status.APPROVED
        self.reviewed_at = timezone.now()
        self.reviewed_by = by_user
        self.save(update_fields=["status", "reviewed_at", "reviewed_by"])
        return membership

    def reject(self, by_user, note=""):
        self.status = self.Status.REJECTED
        self.reviewed_at = timezone.now()
        self.reviewed_by = by_user
        self.review_note = note[:300]
        self.save(update_fields=["status", "reviewed_at", "reviewed_by", "review_note"])


class Membership(models.Model):
    """Links a user to an organization with a role."""

    class Role(models.TextChoices):
        OWNER = "owner", "Owner"
        ADMIN = "admin", "Admin"
        MEMBER = "member", "Member"

    organization = models.ForeignKey(
        Organization, on_delete=models.CASCADE, related_name="memberships"
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="memberships"
    )
    role = models.CharField(max_length=20, choices=Role.choices, default=Role.MEMBER)
    title = models.CharField(max_length=80, blank=True, help_text="e.g. Secretary, Treasurer")
    is_active = models.BooleanField(default=True)
    joined_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ("organization", "user")
        ordering = ["role", "joined_at"]

    def __str__(self):
        return f"{self.user} — {self.get_role_display()} of {self.organization}"
