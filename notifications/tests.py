"""Push: subscribing, sending, and — mostly — not sending."""

import json
from datetime import timedelta
from unittest.mock import patch

from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from accounts.models import University, User
from events.models import Event, Registration
from organizations.models import Membership, Organization

from .models import PushSubscription
from .push import send_to_user
from .tasks import send_door_reminders

# Not real keys — a valid-looking pair so is_configured() is satisfied and every
# actual send is mocked. Generating a genuine pair per test run is slow and buys
# nothing: pywebpush's own encryption is not what these tests are about.
WITH_KEYS = override_settings(
    VAPID_PUBLIC_KEY="BJp3Test0000000000000000000000000000000000000000000000000000000000",
    VAPID_PRIVATE_KEY="-----BEGIN PRIVATE KEY-----\nnot-a-real-key\n-----END PRIVATE KEY-----",
    VAPID_ADMIN_EMAIL="mailto:admin@varsityevents.test",
)


class PushTestCase(TestCase):
    def setUp(self):
        self.uz = University.objects.create(name="University of Zimbabwe", short_name="UZ")
        self.organizer = User.objects.create_user(
            username="organizer", email="o@uz.test", password="pw", role=User.Role.ORGANIZER
        )
        self.student = User.objects.create_user(
            username="student", email="s@uz.test", password="pw", university=self.uz
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
            starts_at=now + timedelta(days=2),
            ends_at=now + timedelta(days=2, hours=3),
            status=Event.Status.PUBLISHED,
            is_free=True,
        )

    def subscribe(self, user=None, endpoint="https://push.example/abc"):
        return PushSubscription.objects.create(
            user=user or self.student,
            endpoint=endpoint,
            p256dh="key-material",
            auth="auth-secret",
        )


class SubscribeViewTests(PushTestCase):
    def post(self, url, body):
        return self.client.post(url, data=json.dumps(body), content_type="application/json")

    def valid_body(self, endpoint="https://push.example/abc"):
        return {"endpoint": endpoint, "keys": {"p256dh": "pk", "auth": "au"}}

    def test_signing_in_is_required(self):
        response = self.post(reverse("notifications:subscribe"), self.valid_body())

        self.assertEqual(response.status_code, 302)
        self.assertEqual(PushSubscription.objects.count(), 0)

    @WITH_KEYS
    def test_a_subscription_is_stored(self):
        self.client.force_login(self.student)

        response = self.post(reverse("notifications:subscribe"), self.valid_body())

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["created"])
        self.assertEqual(PushSubscription.objects.get().user, self.student)

    @WITH_KEYS
    def test_resubscribing_the_same_device_updates_rather_than_duplicates(self):
        """A browser hands back the same endpoint after every refresh."""
        self.client.force_login(self.student)

        self.post(reverse("notifications:subscribe"), self.valid_body())
        response = self.post(reverse("notifications:subscribe"), self.valid_body())

        self.assertFalse(response.json()["created"])
        self.assertEqual(PushSubscription.objects.count(), 1)

    @WITH_KEYS
    def test_a_device_that_changes_hands_moves_to_whoever_is_signed_in(self):
        self.subscribe(user=self.organizer, endpoint="https://push.example/shared")
        self.client.force_login(self.student)

        self.post(reverse("notifications:subscribe"), self.valid_body("https://push.example/shared"))

        self.assertEqual(PushSubscription.objects.count(), 1)
        self.assertEqual(PushSubscription.objects.get().user, self.student)

    @WITH_KEYS
    def test_an_incomplete_subscription_is_refused(self):
        self.client.force_login(self.student)

        for body in ({}, {"endpoint": "https://p.example/x"}, {"keys": {"p256dh": "a"}}):
            with self.subTest(body=body):
                response = self.post(reverse("notifications:subscribe"), body)
                self.assertEqual(response.status_code, 400)

        self.assertEqual(PushSubscription.objects.count(), 0)

    def test_without_keys_the_endpoint_says_so_rather_than_storing_junk(self):
        self.client.force_login(self.student)

        response = self.post(reverse("notifications:subscribe"), self.valid_body())

        self.assertEqual(response.status_code, 503)
        self.assertEqual(PushSubscription.objects.count(), 0)

    @WITH_KEYS
    def test_unsubscribing_only_removes_your_own_device(self):
        """An endpoint is not a secret; it must not authorise deleting someone else's."""
        theirs = self.subscribe(user=self.organizer, endpoint="https://push.example/theirs")
        self.client.force_login(self.student)

        response = self.post(
            reverse("notifications:unsubscribe"), {"endpoint": theirs.endpoint}
        )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json()["deleted"])
        self.assertTrue(PushSubscription.objects.filter(pk=theirs.pk).exists())

    @WITH_KEYS
    def test_unsubscribing_removes_the_device(self):
        mine = self.subscribe()
        self.client.force_login(self.student)

        response = self.post(reverse("notifications:unsubscribe"), {"endpoint": mine.endpoint})

        self.assertTrue(response.json()["deleted"])
        self.assertEqual(PushSubscription.objects.count(), 0)


class PublicKeyTests(PushTestCase):
    def test_it_reports_when_push_is_not_set_up(self):
        body = self.client.get(reverse("notifications:public_key")).json()

        self.assertFalse(body["configured"])
        self.assertEqual(body["key"], "")

    @WITH_KEYS
    def test_it_hands_out_only_the_public_half(self):
        body = self.client.get(reverse("notifications:public_key")).json()

        self.assertTrue(body["configured"])
        self.assertTrue(body["key"])
        self.assertNotIn("PRIVATE", body["key"])


class SendingTests(PushTestCase):
    def test_without_keys_sending_is_a_quiet_no_op(self):
        self.subscribe()

        self.assertEqual(send_to_user(self.student, title="x", body="y"), 0)

    @WITH_KEYS
    def test_it_reaches_every_device_a_person_has(self):
        self.subscribe(endpoint="https://push.example/phone")
        self.subscribe(endpoint="https://push.example/laptop")

        with patch("pywebpush.webpush") as sent:
            landed = send_to_user(self.student, title="Doors", body="soon", url="/x/")

        self.assertEqual(landed, 2)
        self.assertEqual(sent.call_count, 2)

    @WITH_KEYS
    def test_a_dead_subscription_is_deleted_rather_than_retried(self):
        """404 and 410 mean the browser profile is gone. There is nothing to retry."""
        from pywebpush import WebPushException

        self.subscribe()

        class Gone:
            status_code = 410

        with patch("pywebpush.webpush", side_effect=WebPushException("gone", response=Gone())):
            landed = send_to_user(self.student, title="x", body="y")

        self.assertEqual(landed, 0)
        self.assertEqual(PushSubscription.objects.count(), 0)

    @WITH_KEYS
    def test_a_transient_failure_is_counted_not_deleted(self):
        from pywebpush import WebPushException

        subscription = self.subscribe()

        class Wobble:
            status_code = 503

        with patch("pywebpush.webpush", side_effect=WebPushException("later", response=Wobble())):
            send_to_user(self.student, title="x", body="y")

        subscription.refresh_from_db()
        self.assertEqual(subscription.failures, 1)

    @WITH_KEYS
    def test_a_send_that_raises_never_reaches_the_caller(self):
        """Best-effort by design: a push service must not unwind a payment."""
        self.subscribe()

        with patch("pywebpush.webpush", side_effect=OSError("dns")):
            self.assertEqual(send_to_user(self.student, title="x", body="y"), 0)

    @WITH_KEYS
    def test_the_payload_carries_a_tag_so_messages_replace_rather_than_stack(self):
        self.subscribe()

        with patch("pywebpush.webpush") as sent:
            send_to_user(self.student, title="x", body="y", url="/t/", tag="reminder-1")

        payload = json.loads(sent.call_args.kwargs["data"])
        self.assertEqual(payload["tag"], "reminder-1")


@WITH_KEYS
class DoorReminderTests(PushTestCase):
    def confirmed_ticket_for(self, starts_in_minutes):
        now = timezone.now()
        event = Event.objects.create(
            title="Soon",
            organization=self.org,
            created_by=self.organizer,
            starts_at=now + timedelta(minutes=starts_in_minutes),
            ends_at=now + timedelta(minutes=starts_in_minutes + 120),
            status=Event.Status.PUBLISHED,
            is_free=True,
        )
        return Registration.objects.create(
            event=event, user=self.student, status=Registration.Status.CONFIRMED
        )

    def test_it_nudges_an_hour_before_doors(self):
        registration = self.confirmed_ticket_for(55)
        self.subscribe()

        with patch("pywebpush.webpush") as sent:
            self.assertEqual(send_door_reminders(), 1)

        self.assertEqual(sent.call_count, 1)
        registration.refresh_from_db()
        self.assertIsNotNone(registration.reminded_at)

    def test_it_leaves_something_next_week_alone(self):
        self.confirmed_ticket_for(60 * 24 * 7)
        self.subscribe()

        self.assertEqual(send_door_reminders(), 0)

    def test_it_leaves_something_already_started_alone(self):
        self.confirmed_ticket_for(-30)
        self.subscribe()

        self.assertEqual(send_door_reminders(), 0)

    def test_nobody_is_reminded_twice(self):
        """A cluster that restarts or catches up must not nag."""
        self.confirmed_ticket_for(55)
        self.subscribe()

        with patch("pywebpush.webpush"):
            self.assertEqual(send_door_reminders(), 1)
            self.assertEqual(send_door_reminders(), 0)
            self.assertEqual(send_door_reminders(), 0)

    def test_a_waitlisted_place_is_not_a_ticket(self):
        registration = self.confirmed_ticket_for(55)
        registration.status = Registration.Status.WAITLISTED
        registration.save()
        self.subscribe()

        self.assertEqual(send_door_reminders(), 0)

    def test_someone_with_no_device_is_marked_done_rather_than_reconsidered(self):
        """Otherwise they're looked at again every ten minutes for the whole hour."""
        registration = self.confirmed_ticket_for(55)

        self.assertEqual(send_door_reminders(), 1)
        registration.refresh_from_db()
        self.assertIsNotNone(registration.reminded_at)


@WITH_KEYS
class TriggerTests(PushTestCase):
    """The events that are worth interrupting somebody for."""

    def test_a_waitlist_promotion_pushes(self):
        self.event.capacity = 1
        self.event.is_free = False
        self.event.price = 10
        self.event.save()

        self.event.register(self.organizer)
        waiting = self.event.register(self.student)
        self.assertEqual(waiting.status, Registration.Status.WAITLISTED)
        self.subscribe()

        with patch("pywebpush.webpush") as sent:
            self.event.registrations.filter(user=self.organizer).first().cancel()

        waiting.refresh_from_db()
        self.assertEqual(waiting.status, Registration.Status.AWAITING_PAYMENT)
        self.assertEqual(sent.call_count, 1)

    def test_nothing_is_pushed_for_an_ordinary_free_sign_up(self):
        """A ticket you just asked for is not news. That's an email."""
        self.subscribe()

        with patch("pywebpush.webpush") as sent:
            self.event.register(self.student)

        self.assertEqual(sent.call_count, 0)


class VapidKeyCommandTests(TestCase):
    """The keys we tell an operator to use have to actually work."""

    def test_both_halves_fit_on_one_line(self):
        """They go into an environment variable; a PEM would need escaping."""
        from .management.commands.make_vapid_keys import generate

        public, private = generate()

        self.assertNotIn("\n", public)
        self.assertNotIn("\n", private)
        self.assertNotIn("BEGIN", private)

    def test_the_private_half_can_sign(self):
        """The real check: pywebpush's own loader has to accept what we emit."""
        from py_vapid import Vapid01

        from .management.commands.make_vapid_keys import generate

        _, private = generate()
        header = Vapid01.from_string(private).sign(
            {"aud": "https://push.example", "sub": "mailto:admin@varsityevents.test"}
        )

        self.assertTrue(header.get("Authorization"))

    def test_the_public_half_is_the_form_a_browser_expects(self):
        """applicationServerKey wants a 65-byte uncompressed EC point."""
        import base64

        from .management.commands.make_vapid_keys import generate

        public, _ = generate()
        raw = base64.urlsafe_b64decode(public + "=" * (-len(public) % 4))

        self.assertEqual(len(raw), 65)
        self.assertEqual(raw[0], 0x04)
