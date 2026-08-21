"""The door: reading a ticket by camera as well as by keyboard.

The QR on every ticket encodes the ticket's URL, so anything that scans one —
our own in-page scanner, a phone's camera, a generic scanner app — hands over a
link rather than a bare code. The door is the worst possible place to ask
somebody to retype the interesting part of it, so the form takes either.
"""

from datetime import timedelta

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import University, User
from events.forms import CheckInForm
from events.models import Event, Registration
from organizations.models import Membership, Organization


def make_user(username, **extra):
    return User.objects.create_user(
        username=username,
        email=f"{username}@varsity.test",
        password="testpass12345",
        **extra,
    )


class CheckInFormTests(TestCase):
    def clean(self, value):
        form = CheckInForm({"ticket_code": value})
        self.assertTrue(form.is_valid(), form.errors)
        return form.cleaned_data["ticket_code"]

    def test_a_typed_code_is_taken_as_it_is(self):
        self.assertEqual(self.clean("VE-8F3K-2QD7"), "VE-8F3K-2QD7")

    def test_a_lowercase_code_is_normalised(self):
        self.assertEqual(self.clean("ve-8f3k-2qd7"), "VE-8F3K-2QD7")

    def test_surrounding_whitespace_is_dropped(self):
        self.assertEqual(self.clean("  VE-8F3K-2QD7 "), "VE-8F3K-2QD7")

    def test_a_scanned_ticket_url_yields_the_code(self):
        """What the QR actually encodes."""
        self.assertEqual(
            self.clean("https://varsityevents.app/events/tickets/VE-8F3K-2QD7/"),
            "VE-8F3K-2QD7",
        )

    def test_a_url_from_another_host_still_yields_the_code(self):
        """A ticket issued before the domain moved is still a valid ticket."""
        self.assertEqual(
            self.clean("http://localhost:8000/events/tickets/VE-8F3K-2QD7/"),
            "VE-8F3K-2QD7",
        )

    def test_a_long_url_is_not_rejected_before_the_code_is_found(self):
        """The field's own max_length runs first; it has to allow for a URL."""
        long_url = "https://varsityevents.app/events/tickets/VE-8F3K-2QD7/?utm_source=" + "x" * 100
        self.assertEqual(self.clean(long_url), "VE-8F3K-2QD7")

    def test_something_that_is_not_a_code_is_passed_through_to_be_refused(self):
        """The view says "no ticket like that"; the form doesn't guess."""
        self.assertEqual(self.clean("https://example.com/"), "HTTPS://EXAMPLE.COM/")

    def test_look_alike_characters_are_not_read_as_a_code(self):
        """The alphabet excludes O, 0, I and 1 — so this cannot be one of ours."""
        self.assertEqual(self.clean("VE-0OI1-2QD7"), "VE-0OI1-2QD7")


class CheckInDeskTests(TestCase):
    def setUp(self):
        self.university = University.objects.create(
            name="University of Zimbabwe", short_name="UZ"
        )
        self.organizer = make_user("organizer", role=User.Role.ORGANIZER)
        self.org = Organization.objects.create(name="Society", university=self.university)
        Membership.objects.create(
            organization=self.org, user=self.organizer, role=Membership.Role.OWNER
        )

        now = timezone.now()
        self.event = Event.objects.create(
            title="Doors",
            organization=self.org,
            created_by=self.organizer,
            starts_at=now + timedelta(hours=2),
            ends_at=now + timedelta(hours=6),
            status=Event.Status.PUBLISHED,
        )
        self.attendee = make_user("attendee", university=self.university)
        self.registration = Registration.objects.create(
            event=self.event, user=self.attendee, status=Registration.Status.CONFIRMED
        )
        self.url = reverse("events:check_in", args=[self.event.slug])

    def test_a_scanned_url_checks_somebody_in(self):
        self.client.force_login(self.organizer)
        scanned = f"https://varsityevents.app/events/tickets/{self.registration.ticket_code}/"

        self.client.post(self.url, {"ticket_code": scanned})

        self.registration.refresh_from_db()
        self.assertTrue(self.registration.is_checked_in)

    def test_scanning_the_same_ticket_twice_reports_it_rather_than_double_counting(self):
        """A ticket held in front of a lens is many frames; the second is not entry."""
        self.client.force_login(self.organizer)
        self.client.post(self.url, {"ticket_code": self.registration.ticket_code})
        first_time = Registration.objects.get(pk=self.registration.pk).checked_in_at

        response = self.client.post(self.url, {"ticket_code": self.registration.ticket_code})

        self.registration.refresh_from_db()
        self.assertEqual(self.registration.checked_in_at, first_time)
        self.assertEqual(self.event.checked_in_count, 1)
        self.assertContains(response, "Already checked in")

    def test_a_ticket_for_another_event_is_refused(self):
        other = Event.objects.create(
            title="Elsewhere",
            organization=self.org,
            created_by=self.organizer,
            starts_at=timezone.now() + timedelta(days=2),
            ends_at=timezone.now() + timedelta(days=2, hours=3),
            status=Event.Status.PUBLISHED,
        )
        self.client.force_login(self.organizer)

        response = self.client.post(
            reverse("events:check_in", args=[other.slug]),
            {"ticket_code": self.registration.ticket_code},
        )

        self.registration.refresh_from_db()
        self.assertFalse(self.registration.is_checked_in)
        self.assertContains(response, "No ticket")

    def test_the_desk_is_closed_to_people_who_do_not_run_the_event(self):
        self.client.force_login(self.attendee)

        self.assertEqual(self.client.get(self.url).status_code, 404)

    def test_the_page_carries_the_scanner(self):
        self.client.force_login(self.organizer)

        response = self.client.get(self.url)

        self.assertContains(response, "data-scanner")
        self.assertContains(response, "js/scanner.js")

    def test_the_scanner_panel_starts_hidden(self):
        """Revealed by JS only where the browser can actually read a QR — a dead
        camera button at a door is worse than none."""
        self.client.force_login(self.organizer)

        response = self.client.get(self.url)

        self.assertContains(response, "data-scanner hidden")

    def test_the_input_is_marked_for_the_scanner_to_fill(self):
        self.client.force_login(self.organizer)

        response = self.client.get(self.url)

        self.assertContains(response, "data-scanner-target")
