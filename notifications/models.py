"""Who has agreed to be notified, and on which device.

A student may be signed in on a phone and a laptop and want the alert on both,
so a subscription belongs to a (user, device) pair rather than to a user. The
endpoint the browser hands us is already unique per device, which is why it is
the primary key in all but name.

Nothing here is created without an explicit tap on "Notify me" — see
notifications/views.py for where that lives, and why it is not offered on page
load.
"""

from django.conf import settings
from django.db import models
from django.utils import timezone


class PushSubscription(models.Model):
    """One browser, on one device, that has agreed to be pushed to."""

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="push_subscriptions"
    )
    # The push service's URL for this device. Long, opaque, and unique — Chrome's
    # run past 200 characters and there is no documented ceiling, so this is
    # generous rather than guessed.
    endpoint = models.URLField(max_length=600, unique=True)
    # The two keys from the browser's subscription, used to encrypt each payload
    # so the push service itself can't read what we send.
    p256dh = models.CharField(max_length=200)
    auth = models.CharField(max_length=100)

    user_agent = models.CharField(
        max_length=200, blank=True, help_text="Only so a person can tell their devices apart"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    # Bumped on every successful send. A subscription that hasn't taken a
    # message in months is almost certainly a browser profile that no longer
    # exists, and worth pruning.
    last_used_at = models.DateTimeField(null=True, blank=True)
    failures = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["user", "created_at"])]

    def __str__(self):
        return f"{self.user} · {self.endpoint[:40]}…"

    def as_payload(self):
        """The shape pywebpush wants."""
        return {
            "endpoint": self.endpoint,
            "keys": {"p256dh": self.p256dh, "auth": self.auth},
        }

    def mark_used(self):
        self.last_used_at = timezone.now()
        self.failures = 0
        self.save(update_fields=["last_used_at", "failures"])
