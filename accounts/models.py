from django.contrib.auth.models import AbstractUser
from django.db import models
from django.urls import reverse
from django.utils.text import slugify


class University(models.Model):
    """A Zimbabwean university or one of its campuses."""

    class Kind(models.TextChoices):
        STATE = "state", "State university"
        PRIVATE = "private", "Private university"
        CHURCH = "church", "Church-related university"
        POLYTECHNIC = "polytechnic", "Polytechnic / college"

    name = models.CharField(max_length=140, unique=True)
    short_name = models.CharField(
        max_length=20, blank=True, help_text="Abbreviation students actually use, e.g. UZ, NUST"
    )
    slug = models.SlugField(max_length=160, unique=True, blank=True)
    kind = models.CharField(max_length=20, choices=Kind.choices, default=Kind.STATE)
    city = models.CharField(max_length=80, blank=True)
    province = models.CharField(max_length=80, blank=True)
    website = models.URLField(blank=True)
    logo = models.ImageField(upload_to="universities/", blank=True, null=True)

    class Meta:
        verbose_name_plural = "universities"
        ordering = ["name"]

    def __str__(self):
        return self.short_name or self.name

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = slugify(self.short_name or self.name)[:160]
        super().save(*args, **kwargs)

    def get_absolute_url(self):
        return f"{reverse('events:list')}?university={self.slug}"

    @property
    def label(self):
        """'UZ — University of Zimbabwe', or just the name when there's no abbreviation."""
        return f"{self.short_name} — {self.name}" if self.short_name else self.name


class User(AbstractUser):
    """Platform user. Students attend, organizers run societies and events."""

    class Role(models.TextChoices):
        STUDENT = "student", "Student"
        ORGANIZER = "organizer", "Organizer"
        STAFF = "staff", "Staff"

    email = models.EmailField(unique=True)
    role = models.CharField(max_length=20, choices=Role.choices, default=Role.STUDENT)
    university = models.ForeignKey(
        University, on_delete=models.SET_NULL, null=True, blank=True, related_name="members"
    )
    student_id = models.CharField(max_length=40, blank=True)
    course = models.CharField(max_length=120, blank=True)
    year_of_study = models.PositiveSmallIntegerField(null=True, blank=True)
    phone = models.CharField(max_length=30, blank=True)
    bio = models.TextField(max_length=500, blank=True)
    avatar = models.ImageField(upload_to="avatars/", blank=True, null=True)
    interests = models.ManyToManyField(
        "events.Category", blank=True, related_name="interested_users"
    )
    is_verified_organizer = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    USERNAME_FIELD = "username"
    REQUIRED_FIELDS = ["email"]

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return self.get_full_name() or self.username

    def get_absolute_url(self):
        return reverse("accounts:public_profile", kwargs={"username": self.username})

    @property
    def display_name(self):
        return self.get_full_name() or self.username

    @property
    def initials(self):
        first = (self.first_name or self.username or "?")[:1]
        last = (self.last_name or "")[:1]
        return (first + last).upper()

    @property
    def can_organize(self):
        """Organizers, staff and superusers may create events."""
        return self.is_superuser or self.role in {self.Role.ORGANIZER, self.Role.STAFF}

    @property
    def is_platform_staff(self):
        """May curate events across every university."""
        return self.is_superuser or self.is_staff

    @property
    def profile_is_complete(self):
        """Enough detail for organizers to know who is turning up."""
        return bool(self.first_name and self.last_name and self.university_id)

    def managed_organizations(self):
        """Organizations where this user is an owner or an admin."""
        from organizations.models import Membership, Organization

        if self.is_superuser:
            return Organization.objects.all()
        return Organization.objects.filter(
            memberships__user=self,
            memberships__role__in=[Membership.Role.OWNER, Membership.Role.ADMIN],
            memberships__is_active=True,
        ).distinct()
