from datetime import timedelta
from decimal import Decimal
from io import StringIO

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from accounts.models import University, User
from events.models import Category, Event, Registration
from organizations.models import Membership, Organization
from payments.models import Payment

from .models import Activity, record, timesince_short


def make_user(username, **extra):
    return User.objects.create_user(
        username=username, email=f"{username}@varsity.test", password="testpass12345", **extra
    )


class ActivityTestCase(TestCase):
    def setUp(self):
        self.uz = University.objects.create(
            name="University of Zimbabwe", short_name="UZ", city="Harare"
        )
        self.organizer = make_user("organizer", role=User.Role.ORGANIZER, university=self.uz)
        self.student = make_user(
            "student", first_name="Tanaka", last_name="Ncube", university=self.uz
        )

        self.org = Organization.objects.create(
            name="Test Society", created_by=self.organizer, university=self.uz
        )
        Membership.objects.create(
            organization=self.org, user=self.organizer, role=Membership.Role.OWNER
        )

        now = timezone.now()
        self.event = Event.objects.create(
            title="Test Hackathon",
            organization=self.org,
            created_by=self.organizer,
            category=Category.objects.create(name="Tech"),
            starts_at=now + timedelta(days=3),
            ends_at=now + timedelta(days=3, hours=8),
            capacity=2,
            status=Event.Status.PUBLISHED,
        )


class RecordingTests(ActivityTestCase):
    def test_registering_lands_on_the_stream(self):
        self.event.register(self.student)

        entry = Activity.objects.get(verb=Activity.Verb.REGISTERED)
        self.assertEqual(entry.actor, self.student)
        self.assertEqual(entry.event, self.event)

    def test_waitlisting_is_recorded_distinctly(self):
        self.event.register(self.student)
        self.event.register(make_user("two", university=self.uz))
        self.event.register(make_user("three", university=self.uz))

        self.assertTrue(Activity.objects.filter(verb=Activity.Verb.WAITLISTED).exists())

    def test_selling_out_is_announced_once(self):
        self.event.register(self.student)
        self.event.register(make_user("two", university=self.uz))
        self.event.register(make_user("three", university=self.uz))
        self.event.register(make_user("four", university=self.uz))

        self.assertEqual(Activity.objects.filter(verb=Activity.Verb.SOLD_OUT).count(), 1)

    def test_check_in_is_recorded(self):
        registration = self.event.register(self.student)
        registration.check_in(by_user=self.organizer)

        self.assertTrue(
            Activity.objects.filter(verb=Activity.Verb.CHECKED_IN, actor=self.student).exists()
        )

    def test_settled_payment_is_recorded_with_the_amount(self):
        self.event.is_free = False
        self.event.price = Decimal("15.00")
        self.event.save()

        registration = self.event.register(self.student)
        payment = Payment.objects.create(
            registration=registration,
            user=self.student,
            amount=Decimal("15.00"),
            is_simulated=True,
        )
        payment.apply_paynow_status("Paid")
        payment.settle()

        entry = Activity.objects.get(verb=Activity.Verb.PAID)
        self.assertEqual(entry.amount, Decimal("15.00"))
        self.assertTrue(entry.is_simulated)
        self.assertEqual(entry.detail, "USD 15.00")

    def test_following_a_society_is_recorded(self):
        self.client.force_login(self.student)
        self.client.post(reverse("organizations:toggle_follow", args=[self.org.slug]))

        self.assertTrue(
            Activity.objects.filter(verb=Activity.Verb.FOLLOWED, organization=self.org).exists()
        )

    def test_unfollowing_does_not_add_noise(self):
        self.client.force_login(self.student)
        url = reverse("organizations:toggle_follow", args=[self.org.slug])
        self.client.post(url)
        self.client.post(url)

        self.assertEqual(Activity.objects.filter(verb=Activity.Verb.FOLLOWED).count(), 1)

    def test_a_broken_record_never_breaks_the_action(self):
        """The feed is decorative; a write failure must be logged, not raised."""
        with self.assertLogs("activity.models", level="ERROR"):
            entry = record(Activity.Verb.REGISTERED, actor="definitely-not-a-user")

        self.assertIsNone(entry)


class PresentationTests(ActivityTestCase):
    def test_actor_name_is_shortened_for_privacy(self):
        entry = record(Activity.Verb.REGISTERED, actor=self.student, event=self.event)
        self.assertEqual(entry.actor_name, "Tanaka N.")

    def test_anonymous_activity_reads_sensibly(self):
        entry = record(Activity.Verb.SOLD_OUT, event=self.event)
        self.assertEqual(entry.actor_name, "Someone")

    def test_university_is_derived_from_the_event(self):
        entry = record(Activity.Verb.REGISTERED, actor=self.student, event=self.event)
        self.assertEqual(entry.university, self.uz)

    def test_relative_times_are_short(self):
        now = timezone.now()
        self.assertEqual(timesince_short(now), "just now")
        self.assertEqual(timesince_short(now - timedelta(minutes=8)), "8m")
        self.assertEqual(timesince_short(now - timedelta(hours=5)), "5h")
        self.assertEqual(timesince_short(now - timedelta(days=2)), "2d")

    def test_serialised_row_has_everything_the_ticker_needs(self):
        entry = record(Activity.Verb.REGISTERED, actor=self.student, event=self.event)
        data = entry.as_dict()

        for key in ("id", "verb", "phrase", "actor", "target", "url", "icon", "tone", "ago"):
            self.assertIn(key, data)
        self.assertEqual(data["target"], "Test Hackathon")
        self.assertEqual(data["url"], self.event.get_absolute_url())


# The test runner forces DEBUG=False, which the simulator's own safety guard
# rejects — so the normal-operation tests opt back in explicitly.
@override_settings(DEBUG=True)
class SimulatorTests(ActivityTestCase):
    def run_command(self, **kwargs):
        out = StringIO()
        call_command("simulate_activity", stdout=out, stderr=StringIO(), **kwargs)
        return out.getvalue()

    def test_a_burst_generates_real_activity(self):
        before = Activity.objects.count()
        self.run_command(burst=25)

        self.assertGreater(Activity.objects.count(), before)

    def test_simulated_actions_create_real_registrations(self):
        self.run_command(burst=30)

        self.assertTrue(Registration.objects.exists())
        # Every registration must point at a real event — no orphan feed rows.
        for entry in Activity.objects.filter(verb=Activity.Verb.REGISTERED):
            self.assertIsNotNone(entry.event)

    def test_simulator_refuses_to_run_in_production(self):
        with override_settings(DEBUG=False):
            with self.assertRaises(CommandError):
                self.run_command(burst=5)

    def test_force_overrides_the_production_guard(self):
        with override_settings(DEBUG=False):
            self.run_command(burst=5, force=True)  # must not raise

    def test_clean_removes_simulated_rows_only(self):
        record(Activity.Verb.REGISTERED, actor=self.student, event=self.event, is_simulated=True)
        record(Activity.Verb.REGISTERED, actor=self.student, event=self.event, is_simulated=False)

        self.run_command(clean=True)

        self.assertEqual(Activity.objects.filter(is_simulated=True).count(), 0)
        self.assertEqual(Activity.objects.filter(is_simulated=False).count(), 1)

    def test_simulator_needs_students(self):
        User.objects.filter(role=User.Role.STUDENT).delete()
        with self.assertRaises(CommandError):
            self.run_command(burst=5)


class LiveBoardTests(ActivityTestCase):
    """The board the README has been advertising."""

    def test_it_renders(self):
        self.event.register(self.student)

        response = self.client.get(reverse("activity:live"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Test Hackathon")

    def test_it_is_public(self):
        """No sign-in wall: it's the shop window."""
        response = self.client.get(reverse("activity:live"))

        self.assertEqual(response.status_code, 200)

    def test_an_empty_stream_says_so_rather_than_looking_broken(self):
        response = self.client.get(reverse("activity:live"))

        self.assertContains(response, "Nothing yet")

    def test_private_rows_stay_off_it(self):
        record(Activity.Verb.REGISTERED, actor=self.student, event=self.event, is_public=False)

        response = self.client.get(reverse("activity:live"))

        self.assertNotContains(response, "Tanaka")

    def test_it_counts_what_is_on_right_now(self):
        now = timezone.now()
        Event.objects.create(
            title="Running Now",
            organization=self.org,
            created_by=self.organizer,
            starts_at=now - timedelta(hours=1),
            ends_at=now + timedelta(hours=1),
            status=Event.Status.PUBLISHED,
        )

        response = self.client.get(reverse("activity:live"))

        self.assertEqual(response.context["stats"]["happening_now"], 1)


class FeedTests(ActivityTestCase):
    """Incremental paging by id — no timestamps, no overlap, no duplicates."""

    def feed(self, since=None):
        params = {"since": since} if since is not None else {}
        return self.client.get(reverse("activity:feed"), params).json()

    def test_it_hands_back_everything_when_nothing_has_been_seen(self):
        self.event.register(self.student)

        body = self.feed(0)

        self.assertTrue(body["rows"])
        self.assertEqual(body["last_id"], Activity.objects.order_by("-pk").first().pk)

    def test_it_returns_only_what_is_new(self):
        self.event.register(self.student)
        seen = Activity.objects.order_by("-pk").first().pk

        self.assertEqual(self.feed(seen)["rows"], [])

        self.event.register(make_user("later", university=self.uz))
        rows = self.feed(seen)["rows"]

        self.assertTrue(rows)
        self.assertTrue(all(row["id"] > seen for row in rows))

    def test_rows_come_back_oldest_first_so_prepending_keeps_the_order(self):
        """Newest-first would land them upside down on the board."""
        for name in ("a", "b", "c"):
            self.event.register(make_user(name, university=self.uz))

        rows = self.feed(0)["rows"]
        ids = [row["id"] for row in rows]

        self.assertEqual(ids, sorted(ids))

    def test_a_client_that_has_been_away_gets_the_newest_slice(self):
        """Not the oldest unseen rows — it would crawl back an hour at a time."""
        from .views import MAX_NEW_ROWS

        for index in range(MAX_NEW_ROWS + 12):
            record(Activity.Verb.SAVED, actor=self.student, event=self.event)

        body = self.feed(0)
        newest = Activity.objects.order_by("-pk").first().pk

        self.assertEqual(len(body["rows"]), MAX_NEW_ROWS)
        self.assertEqual(body["rows"][-1]["id"], newest)

    def test_private_rows_never_reach_it(self):
        record(Activity.Verb.REGISTERED, actor=self.student, event=self.event, is_public=False)

        self.assertEqual(self.feed(0)["rows"], [])

    def test_a_nonsense_since_is_treated_as_the_beginning(self):
        """It comes off a querystring; it must not 500."""
        self.event.register(self.student)

        for bad in ("", "abc", "-1", "9e9", "'; DROP TABLE"):
            with self.subTest(since=bad):
                response = self.client.get(reverse("activity:feed"), {"since": bad})
                self.assertEqual(response.status_code, 200)

    def test_the_last_id_holds_when_nothing_is_new(self):
        """Otherwise the client would rewind to zero and replay the whole board."""
        self.event.register(self.student)
        seen = Activity.objects.order_by("-pk").first().pk

        self.assertEqual(self.feed(seen)["last_id"], seen)

    def test_it_slows_down_when_nothing_is_happening(self):
        self.event.register(self.student)
        seen = Activity.objects.order_by("-pk").first().pk

        self.assertGreater(self.feed(seen)["retry_in"], self.feed(0)["retry_in"])

    def test_a_row_carries_everything_the_ticker_needs(self):
        self.event.register(self.student)

        row = self.feed(0)["rows"][0]

        for key in ("id", "actor", "phrase", "target", "url", "icon", "tone", "ago"):
            self.assertIn(key, row)
