"""The endpoint that lets an outside scheduler stand in for the task cluster.

It is reachable by anyone who finds the URL, so most of what matters here is
what it refuses.
"""

from datetime import timedelta
from unittest.mock import patch

from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from accounts.models import University, User
from events.models import Event, Registration
from organizations.models import Membership, Organization
from payments.models import Payment

TOKEN = "test-token-not-a-real-one"
WITH_TOKEN = override_settings(TASK_TOKEN=TOKEN)


class MaintenanceTestCase(TestCase):
    def setUp(self):
        self.uz = University.objects.create(name="University of Zimbabwe", short_name="UZ")
        self.organizer = User.objects.create_user(
            username="organizer", email="o@uz.test", password="pw", role=User.Role.ORGANIZER
        )
        self.student = User.objects.create_user(
            username="student", email="s@uz.test", password="pw", university=self.uz
        )
        self.other = User.objects.create_user(
            username="other", email="x@uz.test", password="pw", university=self.uz
        )
        self.org = Organization.objects.create(
            name="Test Society", created_by=self.organizer, university=self.uz
        )
        Membership.objects.create(
            organization=self.org, user=self.organizer, role=Membership.Role.OWNER
        )
        now = timezone.now()
        self.event = Event.objects.create(
            title="Paid Gala",
            organization=self.org,
            created_by=self.organizer,
            starts_at=now + timedelta(days=5),
            ends_at=now + timedelta(days=5, hours=3),
            status=Event.Status.PUBLISHED,
            capacity=1,
            is_free=False,
            price=10,
        )

    def call(self, token=TOKEN, method="post"):
        headers = {"x-task-token": token} if token is not None else {}
        return getattr(self.client, method)(
            reverse("core:run_scheduled_jobs"), headers=headers
        )


class AuthorisationTests(MaintenanceTestCase):
    def test_without_a_token_configured_the_url_does_not_exist(self):
        self.assertEqual(self.call().status_code, 404)

    @WITH_TOKEN
    def test_a_missing_token_is_refused(self):
        self.assertEqual(self.call(token=None).status_code, 404)

    @WITH_TOKEN
    def test_a_wrong_token_is_refused(self):
        self.assertEqual(self.call(token="wrong").status_code, 404)

    @WITH_TOKEN
    def test_a_near_miss_is_refused(self):
        """One character out is still out."""
        self.assertEqual(self.call(token=TOKEN[:-1] + "x").status_code, 404)

    @WITH_TOKEN
    def test_wrong_and_unconfigured_look_identical(self):
        """A 403 would confirm the endpoint exists to whoever is probing."""
        with_token = self.call(token="wrong")
        with override_settings(TASK_TOKEN=""):
            without = self.call(token="wrong")

        self.assertEqual(with_token.status_code, without.status_code)

    @WITH_TOKEN
    def test_the_right_token_gets_through(self):
        self.assertEqual(self.call().status_code, 200)

    @WITH_TOKEN
    def test_get_is_not_allowed(self):
        """It changes things; it must not be reachable by following a link."""
        self.assertEqual(self.call(method="get").status_code, 405)

    @WITH_TOKEN
    def test_a_rejected_call_is_logged_for_the_operator(self):
        with self.assertLogs("core.maintenance", level="WARNING"):
            self.call(token="wrong")


@WITH_TOKEN
class WorkTests(MaintenanceTestCase):
    def test_it_releases_an_abandoned_checkout(self):
        registration = self.event.register(self.student)
        payment = Payment.objects.create(
            registration=registration,
            user=self.student,
            amount=10,
            status=Payment.Status.SENT,
            expires_at=timezone.now() - timedelta(minutes=1),
        )

        body = self.call().json()

        payment.refresh_from_db()
        self.assertEqual(body["released_holds"], 1)
        self.assertEqual(payment.status, Payment.Status.EXPIRED)

    def test_it_promotes_the_waitlist_the_cluster_would_have(self):
        """The reason this endpoint exists: without it, a freed seat sits unclaimed."""
        first = self.event.register(self.student)
        waiting = self.event.register(self.other)
        self.assertEqual(waiting.status, Registration.Status.WAITLISTED)

        Payment.objects.create(
            registration=first,
            user=self.student,
            amount=10,
            status=Payment.Status.SENT,
            expires_at=timezone.now() - timedelta(minutes=1),
        )

        self.call()

        waiting.refresh_from_db()
        self.assertEqual(waiting.status, Registration.Status.AWAITING_PAYMENT)

    def test_running_it_twice_is_harmless(self):
        """Schedulers retry, and overlap. It has to be safe to call again."""
        registration = self.event.register(self.student)
        Payment.objects.create(
            registration=registration,
            user=self.student,
            amount=10,
            status=Payment.Status.SENT,
            expires_at=timezone.now() - timedelta(minutes=1),
        )

        self.assertEqual(self.call().json()["released_holds"], 1)
        self.assertEqual(self.call().json()["released_holds"], 0)

    def test_nothing_to_do_is_a_success_not_an_error(self):
        body = self.call().json()

        self.assertTrue(body["ok"])
        self.assertEqual(body["released_holds"], 0)

    def test_one_job_failing_does_not_stop_the_other(self):
        """A push service having a bad day must not hold seats hostage."""
        registration = self.event.register(self.student)
        Payment.objects.create(
            registration=registration,
            user=self.student,
            amount=10,
            status=Payment.Status.SENT,
            expires_at=timezone.now() - timedelta(minutes=1),
        )

        with patch(
            "notifications.tasks.send_door_reminders", side_effect=RuntimeError("push is down")
        ):
            with self.assertLogs("core.maintenance", level="ERROR"):
                body = self.call().json()

        self.assertEqual(body["released_holds"], 1)
        self.assertIsNone(body["door_reminders"])
