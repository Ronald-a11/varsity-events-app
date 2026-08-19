"""The background-work layer: queueing, the jobs themselves, and log output."""

import json
import logging
from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

from django.core import mail
from django.test import TestCase, override_settings
from django.utils import timezone

from accounts.models import University, User
from core import tasks
from events.models import Event, Registration
from organizations.models import Membership, Organization
from payments.models import Payment
from varsity.logformat import JSONFormatter

BASE_URL = "https://varsity.test"


@override_settings(SITE_BASE_URL=BASE_URL)
class TaskTestCase(TestCase):
    def setUp(self):
        self.uz = University.objects.create(
            name="University of Zimbabwe", short_name="UZ", city="Harare"
        )
        self.organizer = User.objects.create_user(
            username="organizer", email="organizer@uz.test", password="pw",
            role=User.Role.ORGANIZER, university=self.uz,
        )
        self.student = User.objects.create_user(
            username="student", email="student@uz.test", password="pw",
            first_name="Tino", university=self.uz,
        )
        self.org = Organization.objects.create(
            name="Test Society", created_by=self.organizer, university=self.uz
        )
        Membership.objects.create(
            organization=self.org, user=self.organizer, role=Membership.Role.OWNER
        )
        now = timezone.now()
        self.event = Event.objects.create(
            title="Jazz Night",
            organization=self.org,
            created_by=self.organizer,
            starts_at=now + timedelta(days=7),
            ends_at=now + timedelta(days=7, hours=3),
            status=Event.Status.PUBLISHED,
            is_free=True,
        )


class EnqueueTests(TaskTestCase):
    def test_without_a_broker_the_job_runs_here_and_now(self):
        """The test suite and development both live on this path."""
        registration = self.event.register(self.student)
        mail.outbox.clear()

        tasks.enqueue("core.tasks.deliver_ticket_confirmed", registration.pk)

        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("Jazz Night", mail.outbox[0].subject)

    @override_settings(Q_CLUSTER={"name": "varsity", "sync": False})
    def test_a_broker_that_is_down_falls_back_to_running_inline(self):
        """A Redis outage should slow ticket confirmation, never lose it.

        The alternative is a student who has paid and is never told so.
        """
        registration = self.event.register(self.student)
        mail.outbox.clear()

        with patch("django_q.tasks.async_task", side_effect=ConnectionError("no redis")):
            with self.assertLogs("core.tasks", level="ERROR"):
                tasks.enqueue("core.tasks.deliver_ticket_confirmed", registration.pk)

        self.assertEqual(len(mail.outbox), 1)

    @override_settings(Q_CLUSTER={"name": "varsity", "sync": False})
    def test_with_a_broker_the_job_is_handed_over_rather_than_run(self):
        registration = self.event.register(self.student)
        mail.outbox.clear()

        with patch("django_q.tasks.async_task", return_value="task-id") as queued:
            tasks.enqueue("core.tasks.deliver_ticket_confirmed", registration.pk)

        queued.assert_called_once_with("core.tasks.deliver_ticket_confirmed", registration.pk)
        self.assertEqual(len(mail.outbox), 0)


class DeliveryTests(TaskTestCase):
    """The jobs take ids, so each has to cope with the row having gone."""

    def test_a_deleted_registration_is_logged_not_raised(self):
        registration = self.event.register(self.student)
        pk = registration.pk
        registration.delete()
        mail.outbox.clear()

        with self.assertLogs("core.tasks", level="WARNING"):
            self.assertFalse(tasks.deliver_ticket_confirmed(pk))

        self.assertEqual(len(mail.outbox), 0)

    def test_a_deleted_payment_is_logged_not_raised(self):
        with self.assertLogs("core.tasks", level="WARNING"):
            self.assertFalse(tasks.deliver_payment_receipt(99999))

    def test_the_receipt_reaches_the_payer(self):
        registration = Registration.objects.create(
            event=self.event, user=self.student, status=Registration.Status.CONFIRMED
        )
        payment = Payment.objects.create(
            registration=registration, user=self.student, amount=Decimal("15.00")
        )
        mail.outbox.clear()

        self.assertTrue(tasks.deliver_payment_receipt(payment.pk))
        self.assertEqual(mail.outbox[0].to, ["student@uz.test"])


class RegistrationQueuesItsEmailTests(TaskTestCase):
    def test_signing_up_for_a_free_event_still_sends_the_ticket(self):
        """Behaviour has to be identical to before there was a queue."""
        mail.outbox.clear()

        self.event.register(self.student)

        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("ticket", mail.outbox[0].subject.lower())

    @override_settings(Q_CLUSTER={"name": "varsity", "sync": False})
    def test_the_send_leaves_the_request_when_there_is_a_cluster(self):
        """The point of the exercise: no SMTP round trip inside register()."""
        with patch("django_q.tasks.async_task") as queued:
            registration = self.event.register(self.student)

        self.assertEqual(registration.status, Registration.Status.CONFIRMED)
        self.assertEqual(len(mail.outbox), 0)
        queued.assert_called_once_with(
            "core.tasks.deliver_ticket_confirmed", registration.pk
        )


class JSONFormatterTests(TestCase):
    def format(self, record):
        return json.loads(JSONFormatter().format(record))

    def make_record(self, **extra):
        record = logging.LogRecord(
            "payments", logging.INFO, "views.py", 42, "Callback for %s", ("VE-PAY-1",), None
        )
        record.__dict__.update(extra)
        return record

    def test_it_emits_one_json_object_per_line(self):
        payload = self.format(self.make_record())

        self.assertEqual(payload["level"], "INFO")
        self.assertEqual(payload["logger"], "payments")
        self.assertEqual(payload["message"], "Callback for VE-PAY-1")

    def test_extra_context_is_carried_through(self):
        """Whatever a caller passes as extra={} is the part worth filtering on."""
        payload = self.format(self.make_record(reference="VE-PAY-1", released=3))

        self.assertEqual(payload["reference"], "VE-PAY-1")
        self.assertEqual(payload["released"], 3)

    def test_a_traceback_is_included(self):
        try:
            raise ValueError("gateway said no")
        except ValueError:
            import sys

            payload = self.format(self.make_record(exc_info=sys.exc_info()))

        self.assertIn("gateway said no", payload["exception"])

    def test_something_unserialisable_does_not_break_logging(self):
        """Logging must never become the thing that raises."""
        payload = self.format(self.make_record(event=object()))

        self.assertIn("object object at", payload["event"])
