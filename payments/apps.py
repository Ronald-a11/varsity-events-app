import logging

from django.apps import AppConfig
from django.db.models.signals import post_migrate

logger = logging.getLogger(__name__)


def ensure_schedules(sender, **kwargs):
    """Register the recurring sweep, once, after migrations.

    Doing it here rather than in a data migration keeps the schedule declared
    next to the task it runs, and makes it self-healing: delete the row in the
    admin and the next deploy puts it back.
    """
    from django.conf import settings
    from django_q.models import Schedule

    # No broker, no cluster, nothing to schedule against. Development and the
    # test suite land here.
    if settings.Q_CLUSTER.get("sync"):
        return

    try:
        Schedule.objects.update_or_create(
            name="release-abandoned-holds",
            defaults={
                "func": "payments.tasks.release_abandoned_holds",
                "schedule_type": Schedule.MINUTES,
                "minutes": 1,
                # Runs forever. A checkout times out every day of term.
                "repeats": -1,
            },
        )
    except Exception:
        # A failure here must not take the deploy down with it — the seats stay
        # correctly counted either way, they just aren't formally released.
        logger.exception("Could not register the abandoned-hold schedule")


class PaymentsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "payments"

    def ready(self):
        post_migrate.connect(ensure_schedules, sender=self)
