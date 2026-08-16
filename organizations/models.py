from django.conf import settings
from django.db import models
from django.urls import reverse
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
