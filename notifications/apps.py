import logging

from django.apps import AppConfig
from django.db.models.signals import post_migrate

logger = logging.getLogger(__name__)


def ensure_schedules(sender, **kwargs):
    """Register the door-reminder sweep, once, after migrations.

    Every ten minutes, matching the window in tasks.REMINDER_WINDOW_MINUTES —
    change one and the other has to move with it, or students get reminded twice
    or not at all.
    """
    from django.conf import settings
    from django_q.models import Schedule

    if settings.Q_CLUSTER.get("sync"):
        return

    try:
        Schedule.objects.update_or_create(
            name="door-reminders",
            defaults={
                "func": "notifications.tasks.send_door_reminders",
                "schedule_type": Schedule.MINUTES,
                "minutes": 10,
                "repeats": -1,
            },
        )
    except Exception:
        logger.exception("Could not register the door-reminder schedule")


class NotificationsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "notifications"

    def ready(self):
        post_migrate.connect(ensure_schedules, sender=self)
