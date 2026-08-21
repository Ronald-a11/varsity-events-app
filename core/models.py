"""Cross-cutting models. Currently: telling us something is wrong.

Reports live in `core` rather than in `events` or `organizations` because they
point at both, and neither app should have to import the other to describe one.
"""

from django.conf import settings
from django.db import models
from django.utils import timezone


class Report(models.Model):
    """Somebody telling us an event or a society shouldn't be up.

    The review queue catches a bad event *before* students see it, but only for
    societies we haven't verified yet. This is the other direction: everything
    already on the feed, watched by the several thousand people best placed to
    notice — the students standing in front of it.

    A report is about a *thing*, not a conversation. Twenty people reporting the
    same scam is one problem, so staff judge the target once and every open
    report on it closes together.
    """

    class Reason(models.TextChoices):
        SCAM = "scam", "It's a scam, or the tickets aren't real"
        WRONG = "wrong", "The details are wrong or misleading"
        NOT_HAPPENING = "not_happening", "It's been cancelled, or it already happened"
        IMPERSONATION = "impersonation", "It's pretending to be someone else"
        OFFENSIVE = "offensive", "It's offensive or unsafe"
        SPAM = "spam", "It's spam, or it isn't a real event"
        OTHER = "other", "Something else"

    class Status(models.TextChoices):
        OPEN = "open", "Open"
        ACTIONED = "actioned", "Actioned"
        DISMISSED = "dismissed", "Dismissed"

    # The two things anybody can see and therefore the two things worth
    # reporting. Two nullable keys rather than a generic relation: there are
    # exactly two, and this way the queue can select_related its way to a
    # university in one query instead of losing that to a content type.
    event = models.ForeignKey(
        "events.Event", on_delete=models.CASCADE, null=True, blank=True, related_name="reports"
    )
    organization = models.ForeignKey(
        "organizations.Organization",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="reports",
    )

    reporter = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="reports_made"
    )
    reason = models.CharField(max_length=20, choices=Reason.choices)
    detail = models.TextField(
        max_length=800,
        blank=True,
        help_text="Anything that would help us check it — what you saw, and where.",
    )

    status = models.CharField(max_length=20, choices=Status.choices, default=Status.OPEN)
    created_at = models.DateTimeField(auto_now_add=True)
    reviewed_at = models.DateTimeField(null=True, blank=True)
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="reports_reviewed",
    )
    review_note = models.CharField(max_length=300, blank=True)

    class Meta:
        ordering = ["created_at"]
        indexes = [models.Index(fields=["status", "created_at"])]
        constraints = [
            # Exactly one target. A report pointing at both is meaningless and a
            # report pointing at neither is unactionable; the database refuses
            # both rather than leaving the queue to cope with them.
            models.CheckConstraint(
                condition=(
                    models.Q(event__isnull=False, organization__isnull=True)
                    | models.Q(event__isnull=True, organization__isnull=False)
                ),
                name="report_targets_exactly_one_thing",
            ),
            # One open report per person per event. Reporting the same thing
            # five times is not five problems, and it is the cheapest way to
            # bury a real report under a pile.
            models.UniqueConstraint(
                fields=["reporter", "event"],
                condition=models.Q(status="open", event__isnull=False),
                name="one_open_report_per_person_per_event",
            ),
            models.UniqueConstraint(
                fields=["reporter", "organization"],
                condition=models.Q(status="open", organization__isnull=False),
                name="one_open_report_per_person_per_society",
            ),
        ]

    def __str__(self):
        return f"{self.get_reason_display()} · {self.target_name}"

    # -- the thing being reported ---------------------------------------

    @property
    def target(self):
        return self.event or self.organization

    @property
    def target_name(self):
        target = self.target
        return target.title if self.event_id else (target.name if target else "—")

    @property
    def target_kind(self):
        return "event" if self.event_id else "society"

    @property
    def is_open(self):
        return self.status == self.Status.OPEN

    def siblings(self):
        """Every other open report about the same thing."""
        return open_reports_for(self.target).exclude(pk=self.pk)

    # -- verdicts, which apply to the target rather than to one complaint --

    def _close_target(self, by_user, status, note=""):
        """Close every open report on this target at once.

        Staff judge the event, not the complaint. Closing them one at a time
        would leave a queue full of decided-but-still-listed duplicates, which
        is how a queue stops being worked.
        """
        return open_reports_for(self.target).update(
            status=status,
            reviewed_at=timezone.now(),
            reviewed_by=by_user,
            review_note=note[:300],
        )

    def dismiss(self, by_user, note=""):
        """Looked at it; nothing wrong. Whatever it is stays up."""
        return self._close_target(by_user, self.Status.DISMISSED, note)

    def uphold(self, by_user, note=""):
        """Take it down, and close the reports that asked us to.

        An event comes off the feed as a draft rather than being deleted —
        deleting it would take its registrations and its payment records with
        it, and those are exactly what somebody will need if money moved. A
        society is suspended, which hides it and its events without destroying
        the record either.
        """
        from events.models import Event

        target = self.target
        if self.event_id:
            target.status = Event.Status.DRAFT
            target.save(update_fields=["status"])
        else:
            target.is_active = False
            target.save(update_fields=["is_active"])

        return self._close_target(by_user, self.Status.ACTIONED, note)


def open_reports_for(target):
    """Open reports about one event or one society."""
    from events.models import Event

    if target is None:
        return Report.objects.none()
    field = "event" if isinstance(target, Event) else "organization"
    return Report.objects.filter(status=Report.Status.OPEN, **{field: target})
