from datetime import timedelta

from django.core.exceptions import ValidationError
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from accounts.models import University, User
from organizations.models import Membership, Organization
from varsity.testing import login_verified

from .models import Bookmark, Category, Event, Registration, TicketOutlet, TicketStatus


def make_user(username, **extra):
    return User.objects.create_user(
        username=username,
        email=f"{username}@varsity.test",
        password="testpass12345",
        **extra,
    )


class EventTestCase(TestCase):
    """Shared fixture: one Zimbabwean university, one society, one published event."""

    def setUp(self):
        self.uz = University.objects.create(
            name="University of Zimbabwe", short_name="UZ", city="Harare"
        )
        self.nust = University.objects.create(
            name="National University of Science and Technology",
            short_name="NUST",
            city="Bulawayo",
        )

        self.organizer = make_user("organizer", role=User.Role.ORGANIZER, university=self.uz)
        self.student = make_user("student", university=self.uz)
        self.other = make_user("other", university=self.nust)

        self.org = Organization.objects.create(
            name="Test Society", created_by=self.organizer, university=self.uz
        )
        Membership.objects.create(
            organization=self.org, user=self.organizer, role=Membership.Role.OWNER
        )

        self.category = Category.objects.create(name="Tech")
        now = timezone.now()
        self.event = Event.objects.create(
            title="Test Hackathon",
            organization=self.org,
            created_by=self.organizer,
            category=self.category,
            starts_at=now + timedelta(days=7),
            ends_at=now + timedelta(days=7, hours=8),
            capacity=2,
            status=Event.Status.PUBLISHED,
        )


class RegistrationFlowTests(EventTestCase):
    def test_registration_confirms_while_seats_remain(self):
        registration = self.event.register(self.student)

        self.assertEqual(registration.status, Registration.Status.CONFIRMED)
        self.assertTrue(registration.ticket_code.startswith("VE-"))
        self.assertEqual(self.event.attendee_count, 1)
        self.assertEqual(self.event.seats_left, 1)

    def test_registering_twice_returns_the_same_ticket(self):
        first = self.event.register(self.student)
        second = self.event.register(self.student)

        self.assertEqual(first.pk, second.pk)
        self.assertEqual(self.event.attendee_count, 1)

    def test_full_event_puts_the_next_person_on_the_waitlist(self):
        self.event.register(self.student)
        self.event.register(self.other)
        third = self.event.register(make_user("third"))

        self.assertTrue(self.event.is_full)
        self.assertEqual(third.status, Registration.Status.WAITLISTED)
        self.assertEqual(self.event.attendee_count, 2)

    def test_full_event_without_waitlist_refuses_registration(self):
        self.event.allow_waitlist = False
        self.event.save()
        self.event.register(self.student)
        self.event.register(self.other)

        with self.assertRaises(ValidationError):
            self.event.register(make_user("third"))

    def test_cancelling_promotes_the_longest_waiting_person(self):
        first = self.event.register(self.student)
        self.event.register(self.other)
        waitlisted = self.event.register(make_user("third"))

        first.cancel()
        waitlisted.refresh_from_db()

        self.assertEqual(waitlisted.status, Registration.Status.CONFIRMED)
        self.assertEqual(self.event.attendee_count, 2)

    def test_approval_required_events_start_pending(self):
        self.event.requires_approval = True
        self.event.save()

        registration = self.event.register(self.student)

        self.assertEqual(registration.status, Registration.Status.PENDING)
        self.assertEqual(self.event.attendee_count, 0)

    def test_registration_closes_after_the_deadline(self):
        self.event.registration_deadline = timezone.now() - timedelta(hours=1)
        self.event.save()

        self.assertFalse(self.event.registration_open)
        with self.assertRaises(ValidationError):
            self.event.register(self.student)

    def test_check_in_is_idempotent(self):
        registration = self.event.register(self.student)

        self.assertTrue(registration.check_in(by_user=self.organizer))
        self.assertFalse(registration.check_in(by_user=self.organizer))
        self.assertEqual(self.event.checked_in_count, 1)


class EventModelTests(EventTestCase):
    def test_slug_is_generated_and_kept_unique(self):
        duplicate = Event.objects.create(
            title="Test Hackathon",
            organization=self.org,
            starts_at=self.event.starts_at,
            ends_at=self.event.ends_at,
        )
        self.assertEqual(self.event.slug, "test-hackathon")
        self.assertEqual(duplicate.slug, "test-hackathon-2")

    def test_end_before_start_is_rejected(self):
        self.event.ends_at = self.event.starts_at - timedelta(hours=1)
        with self.assertRaises(ValidationError):
            self.event.full_clean()

    def test_free_events_are_forced_to_zero_price(self):
        self.event.is_free = True
        self.event.price = 500
        self.event.save()
        self.assertEqual(self.event.price, 0)
        self.assertEqual(self.event.price_display, "Free")

    def test_drafts_are_hidden_from_other_users(self):
        self.event.status = Event.Status.DRAFT
        self.event.save()

        self.assertTrue(self.event.can_be_seen_by(self.organizer))
        self.assertFalse(self.event.can_be_seen_by(self.student))

    def test_only_society_admins_can_manage(self):
        self.assertTrue(self.event.can_manage(self.organizer))
        self.assertFalse(self.event.can_manage(self.student))


class EventViewTests(EventTestCase):
    def test_university_filter_narrows_to_one_institution(self):
        response = self.client.get(reverse("events:list"), {"university": self.uz.slug})
        self.assertContains(response, "Test Hackathon")

        response = self.client.get(reverse("events:list"), {"university": self.nust.slug})
        self.assertNotContains(response, "Test Hackathon")

    def test_ticket_availability_filter(self):
        available = self.client.get(reverse("events:list"), {"tickets": "available"})
        self.assertContains(available, "Test Hackathon")

        sold_out = self.client.get(reverse("events:list"), {"tickets": "soldout"})
        self.assertNotContains(sold_out, "Test Hackathon")

        self.event.ticket_status = TicketStatus.SOLD_OUT
        self.event.save()

        available = self.client.get(reverse("events:list"), {"tickets": "available"})
        self.assertNotContains(available, "Test Hackathon")

        sold_out = self.client.get(reverse("events:list"), {"tickets": "soldout"})
        self.assertContains(sold_out, "Test Hackathon")

    def test_public_pages_render(self):
        for url in [
            reverse("core:home"),
            reverse("core:about"),
            reverse("events:list"),
            reverse("events:detail", args=[self.event.slug]),
            reverse("organizations:list"),
            reverse("organizations:detail", args=[self.org.slug]),
            reverse("accounts:login"),
            reverse("accounts:register"),
        ]:
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 200)

    def test_search_and_filters_narrow_results(self):
        response = self.client.get(reverse("events:list"), {"q": "Hackathon"})
        self.assertContains(response, "Test Hackathon")

        response = self.client.get(reverse("events:list"), {"q": "nothing matches this"})
        self.assertNotContains(response, "Test Hackathon")

        response = self.client.get(reverse("events:list"), {"category": self.category.slug})
        self.assertContains(response, "Test Hackathon")

    def test_registering_through_the_view_issues_a_ticket(self):
        self.client.force_login(self.student)
        response = self.client.post(reverse("events:register", args=[self.event.slug]))

        registration = Registration.objects.get(event=self.event, user=self.student)
        self.assertRedirects(response, registration.get_absolute_url())
        self.assertEqual(registration.status, Registration.Status.CONFIRMED)

    def test_anonymous_users_are_sent_to_sign_in(self):
        response = self.client.post(reverse("events:register", args=[self.event.slug]))
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("accounts:login"), response.url)

    def test_bookmark_toggles_on_and_off(self):
        self.client.force_login(self.student)
        url = reverse("events:toggle_bookmark", args=[self.event.slug])

        self.client.post(url, headers={"x-requested-with": "XMLHttpRequest"})
        self.assertTrue(Bookmark.objects.filter(user=self.student, event=self.event).exists())

        self.client.post(url, headers={"x-requested-with": "XMLHttpRequest"})
        self.assertFalse(Bookmark.objects.filter(user=self.student, event=self.event).exists())

    def test_ticket_is_private_to_its_owner_and_the_organizer(self):
        registration = self.event.register(self.student)
        url = reverse("events:ticket", args=[registration.ticket_code])

        self.client.force_login(self.student)
        self.assertEqual(self.client.get(url).status_code, 200)

        self.client.force_login(self.organizer)
        self.assertEqual(self.client.get(url).status_code, 200)

        self.client.force_login(self.other)
        self.assertEqual(self.client.get(url).status_code, 404)

    def test_management_pages_reject_non_organizers(self):
        self.client.force_login(self.student)
        for name in ["events:edit", "events:manage_attendees", "events:check_in"]:
            with self.subTest(view=name):
                response = self.client.get(reverse(name, args=[self.event.slug]))
                self.assertEqual(response.status_code, 404)

    def test_organizer_can_open_management_pages(self):
        self.client.force_login(self.organizer)
        for name in ["events:edit", "events:manage_attendees", "events:check_in", "events:dashboard"]:
            with self.subTest(view=name):
                args = [] if name == "events:dashboard" else [self.event.slug]
                self.assertEqual(self.client.get(reverse(name, args=args)).status_code, 200)

    def test_check_in_desk_accepts_a_valid_code(self):
        registration = self.event.register(self.student)
        self.client.force_login(self.organizer)

        response = self.client.post(
            reverse("events:check_in", args=[self.event.slug]),
            {"ticket_code": registration.ticket_code.lower()},
        )
        registration.refresh_from_db()

        self.assertEqual(response.status_code, 200)
        self.assertTrue(registration.is_checked_in)

    def test_check_in_desk_rejects_an_unknown_code(self):
        self.client.force_login(self.organizer)
        response = self.client.post(
            reverse("events:check_in", args=[self.event.slug]), {"ticket_code": "VE-0000-0000"}
        )
        self.assertContains(response, "No ticket")

    def test_attendee_export_returns_csv(self):
        self.event.register(self.student)
        self.client.force_login(self.organizer)

        response = self.client.get(reverse("events:export_attendees", args=[self.event.slug]))

        self.assertEqual(response["Content-Type"], "text/csv")
        self.assertIn("student@varsity.test", response.content.decode())

    def test_calendar_export_returns_ics(self):
        response = self.client.get(reverse("events:ics", args=[self.event.slug]))
        body = response.content.decode()

        self.assertEqual(response["Content-Type"], "text/calendar")
        self.assertIn("BEGIN:VEVENT", body)
        self.assertIn("Test Hackathon", body)

    def test_ticket_qr_renders_a_png(self):
        registration = self.event.register(self.student)
        self.client.force_login(self.student)

        response = self.client.get(reverse("events:ticket_qr", args=[registration.ticket_code]))

        self.assertEqual(response["Content-Type"], "image/png")
        self.assertTrue(response.content.startswith(b"\x89PNG"))


class TicketAvailabilityTests(EventTestCase):
    def test_free_event_with_places_reads_as_free(self):
        self.assertEqual(self.event.availability["state"], "free")
        self.assertTrue(self.event.can_still_get_tickets)

    def test_paid_event_with_places_is_on_sale(self):
        self.event.is_free = False
        self.event.price = 5
        self.event.save()
        self.assertEqual(self.event.availability["state"], "on_sale")

    def test_full_event_offers_a_waitlist(self):
        self.event.register(self.student)
        self.event.register(self.other)

        self.assertEqual(self.event.availability["state"], "waitlist")
        self.assertTrue(self.event.can_still_get_tickets)

    def test_full_event_without_waitlist_is_sold_out(self):
        self.event.allow_waitlist = False
        self.event.save()
        self.event.register(self.student)
        self.event.register(self.other)

        self.assertEqual(self.event.availability["state"], "sold_out")
        self.assertFalse(self.event.can_still_get_tickets)

    def test_organizer_override_wins_over_capacity(self):
        self.event.ticket_status = TicketStatus.SOLD_OUT
        self.event.save()

        self.assertEqual(self.event.availability["state"], "sold_out")
        self.assertFalse(self.event.registration_open)
        with self.assertRaises(ValidationError):
            self.event.register(self.student)

    def test_unavailable_override_blocks_registration(self):
        self.event.ticket_status = TicketStatus.UNAVAILABLE
        self.event.save()

        self.assertEqual(self.event.availability["state"], "unavailable")
        with self.assertRaises(ValidationError):
            self.event.register(self.student)

    def test_finished_event_reads_as_closed(self):
        now = timezone.now()
        self.event.starts_at = now - timedelta(days=2)
        self.event.ends_at = now - timedelta(days=1)
        self.event.save()

        self.assertEqual(self.event.availability["state"], "closed")

    def test_closed_sales_message_formats_on_every_platform(self):
        """Regression: strftime's %-d isn't portable, so this used to blow up on Windows."""
        self.event.registration_deadline = timezone.now() - timedelta(hours=2)
        self.event.save()

        availability = self.event.availability

        self.assertEqual(availability["state"], "closed")
        self.assertIn("Ticket sales closed on", availability["detail"])
        self.assertEqual(self.client.get(self.event.get_absolute_url()).status_code, 200)

    def test_cancelled_event_is_unavailable(self):
        self.event.status = Event.Status.CANCELLED
        self.event.save()
        self.assertEqual(self.event.availability["state"], "unavailable")

    def test_outlets_track_their_own_stock(self):
        TicketOutlet.objects.create(event=self.event, name="SRC Offices", is_available=True)
        TicketOutlet.objects.create(event=self.event, name="Book Café", is_available=False)

        self.assertEqual(self.event.outlets.count(), 2)
        self.assertEqual(self.event.available_outlets.count(), 1)

    def test_event_page_names_the_outlets_and_flags_sold_out_ones(self):
        TicketOutlet.objects.create(event=self.event, name="SRC Offices", is_available=True)
        TicketOutlet.objects.create(event=self.event, name="Book Café", is_available=False)

        response = self.client.get(self.event.get_absolute_url())

        self.assertContains(response, "Where to get tickets")
        self.assertContains(response, "SRC Offices")
        self.assertContains(response, "Book Café")
        self.assertContains(response, "Sold out")

    def test_sold_out_event_page_says_so_loudly(self):
        self.event.ticket_status = TicketStatus.SOLD_OUT
        self.event.save()

        response = self.client.get(self.event.get_absolute_url())
        self.assertContains(response, "ALL SOLD OUT")

    def test_withdrawn_event_page_says_not_currently_available(self):
        self.event.ticket_status = TicketStatus.UNAVAILABLE
        self.event.save()

        response = self.client.get(self.event.get_absolute_url())
        self.assertContains(response, "NOT CURRENTLY AVAILABLE")


class DiscoverFeedTests(EventTestCase):
    def test_signing_in_lands_on_the_nationwide_feed(self):
        response = self.client.post(
            reverse("accounts:login"),
            {"username": "student", "password": "testpass12345"},
        )
        self.assertRedirects(response, reverse("core:discover"))

    def test_home_page_serves_the_feed_in_place_for_signed_in_users(self):
        """The feed is rendered at / itself now — no redirect hop."""
        self.client.force_login(self.student)
        response = self.client.get(reverse("core:home"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Across Zimbabwe")

    def test_feed_requires_sign_in(self):
        response = self.client.get(reverse("core:discover"))
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("accounts:login"), response.url)

    def test_feed_leads_with_the_student_own_university(self):
        self.client.force_login(self.student)
        response = self.client.get(reverse("core:discover"))

        self.assertContains(response, "At UZ")
        self.assertContains(response, "Test Hackathon")

    def test_feed_shows_events_from_other_universities_too(self):
        """A NUST student still sees the UZ event — the point is nationwide reach."""
        self.client.force_login(self.other)
        response = self.client.get(reverse("core:discover"))

        self.assertContains(response, "Across Zimbabwe")
        self.assertContains(response, "Test Hackathon")


class HomePageTests(EventTestCase):
    """One URL, two jobs: explain the app, or show the events."""

    def test_signed_out_home_explains_the_app(self):
        response = self.client.get(reverse("core:home"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "A single calendar for every campus")
        self.assertContains(response, "Three taps from bored to booked")
        self.assertContains(response, "Run the whole event from one page")

    def test_signed_in_home_shows_the_events_feed(self):
        self.client.force_login(self.student)
        response = self.client.get(reverse("core:home"))

        # Rendered in place, not bounced through a redirect. The greeting is
        # literal template text, so it is not HTML-escaped.
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "here's what's on")
        self.assertContains(response, "Across Zimbabwe")

    def test_signed_out_home_does_not_leak_the_feed(self):
        response = self.client.get(reverse("core:home"))
        self.assertNotContains(response, "Across Zimbabwe")


class EventPaginationTests(EventTestCase):
    def setUp(self):
        super().setUp()
        now = timezone.now()
        for day in range(1, 20):
            Event.objects.create(
                title=f"Filler Event {day}",
                organization=self.org,
                created_by=self.organizer,
                starts_at=now + timedelta(days=day),
                ends_at=now + timedelta(days=day, hours=2),
                status=Event.Status.PUBLISHED,
            )

    def test_default_page_holds_twelve(self):
        response = self.client.get(reverse("events:list"))
        self.assertEqual(len(response.context["events"]), 12)

    def test_page_size_can_be_changed(self):
        response = self.client.get(reverse("events:list"), {"per_page": 24})
        self.assertEqual(len(response.context["events"]), 20)  # all of them

    def test_a_junk_page_size_falls_back(self):
        response = self.client.get(reverse("events:list"), {"per_page": "9999"})
        self.assertEqual(len(response.context["events"]), 12)

    def test_the_result_range_is_reported(self):
        response = self.client.get(reverse("events:list"))
        self.assertEqual(response.context["range_start"], 1)
        self.assertEqual(response.context["range_end"], 12)

        second = self.client.get(reverse("events:list"), {"page": 2})
        self.assertEqual(second.context["range_start"], 13)

    def test_events_are_grouped_under_day_headings(self):
        response = self.client.get(reverse("events:list"))
        groups = response.context["event_groups"]

        self.assertTrue(groups)
        # Each group is one calendar day, and days run forwards.
        days = [g["day"] for g in groups]
        self.assertEqual(days, sorted(days))
        self.assertEqual(len(days), len(set(days)))

        # Every event on the page belongs to exactly one group.
        grouped = sum(len(g["events"]) for g in groups)
        self.assertEqual(grouped, len(response.context["events"]))

    def test_grouping_labels_today_and_tomorrow(self):
        now = timezone.now()

        # Keep this close to now. A fixed offset of a few hours rolls past
        # midnight when the suite runs in the evening, and the event lands
        # under "Tomorrow" — the test then fails on the clock, not the code.
        starts_at = now + timedelta(minutes=5)
        self.assertEqual(
            timezone.localtime(starts_at).date(),
            timezone.localdate(),
            "the fixture must fall on today for this test to mean anything",
        )

        Event.objects.create(
            title="Later Today",
            organization=self.org,
            created_by=self.organizer,
            starts_at=starts_at,
            ends_at=starts_at + timedelta(hours=2),
            status=Event.Status.PUBLISHED,
        )

        response = self.client.get(reverse("events:list"))
        labels = [g["label"] for g in response.context["event_groups"]]

        self.assertIn("Today", labels)
        self.assertIn("Tomorrow", labels)


class QuickSearchTests(EventTestCase):
    """The ⌘K command palette endpoint."""

    def test_short_queries_return_nothing(self):
        payload = self.client.get(reverse("core:quick_search"), {"q": "a"}).json()
        self.assertEqual(payload["results"], [])

    def test_events_are_searchable(self):
        payload = self.client.get(reverse("core:quick_search"), {"q": "hackathon"}).json()

        titles = [r["title"] for r in payload["results"]]
        self.assertIn("Test Hackathon", titles)

    def test_societies_and_universities_are_searchable(self):
        societies = self.client.get(reverse("core:quick_search"), {"q": "Test Society"}).json()
        self.assertIn("Test Society", [r["title"] for r in societies["results"]])

        universities = self.client.get(reverse("core:quick_search"), {"q": "NUST"}).json()
        titles = [r["title"] for r in universities["results"]]
        self.assertIn("National University of Science and Technology", titles)

    def test_results_carry_a_working_url_and_availability_badge(self):
        payload = self.client.get(reverse("core:quick_search"), {"q": "hackathon"}).json()
        result = payload["results"][0]

        self.assertTrue(result["badge"])
        self.assertEqual(self.client.get(result["url"]).status_code, 200)


class StaffCurationTests(EventTestCase):
    def setUp(self):
        super().setUp()
        self.staff = make_user("curator", role=User.Role.STAFF, university=self.uz)
        self.staff.is_staff = True
        self.staff.save()

    def test_students_cannot_reach_the_admin_dashboard(self):
        self.client.force_login(self.student)
        response = self.client.get(reverse("core:staff_dashboard"))
        self.assertEqual(response.status_code, 302)

    def test_staff_see_every_event_nationwide(self):
        login_verified(self.client, self.staff)
        response = self.client.get(reverse("core:staff_dashboard"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Test Hackathon")

    def test_staff_can_pick_and_unpick_an_event(self):
        login_verified(self.client, self.staff)

        self.client.post(reverse("core:staff_event_action", args=[self.event.slug, "pick"]))
        self.event.refresh_from_db()
        self.assertTrue(self.event.is_featured)

        self.client.post(reverse("core:staff_event_action", args=[self.event.slug, "unpick"]))
        self.event.refresh_from_db()
        self.assertFalse(self.event.is_featured)

    def test_staff_can_mark_an_event_sold_out(self):
        login_verified(self.client, self.staff)
        self.client.post(reverse("core:staff_event_action", args=[self.event.slug, "sold_out"]))

        self.event.refresh_from_db()
        self.assertEqual(self.event.ticket_status, TicketStatus.SOLD_OUT)
        self.assertEqual(self.event.availability["state"], "sold_out")

    def test_staff_can_pull_an_event_offline(self):
        login_verified(self.client, self.staff)
        self.client.post(reverse("core:staff_event_action", args=[self.event.slug, "unpublish"]))

        self.event.refresh_from_db()
        self.assertEqual(self.event.status, Event.Status.DRAFT)

    def test_staff_can_verify_a_society(self):
        login_verified(self.client, self.staff)
        self.client.post(reverse("core:staff_society_action", args=[self.org.slug, "verify"]))

        self.org.refresh_from_db()
        self.assertTrue(self.org.is_verified)

    def test_staff_can_suspend_a_society(self):
        login_verified(self.client, self.staff)
        self.client.post(reverse("core:staff_society_action", args=[self.org.slug, "suspend"]))

        self.org.refresh_from_db()
        self.assertFalse(self.org.is_active)

    def test_society_admin_page_renders(self):
        login_verified(self.client, self.staff)
        self.assertEqual(self.client.get(reverse("core:staff_societies")).status_code, 200)


class OrganizationTests(EventTestCase):
    def test_creating_a_society_makes_the_creator_its_owner(self):
        self.student.email_verified_at = timezone.now()
        self.student.save(update_fields=["email_verified_at"])
        self.client.force_login(self.student)
        self.client.post(
            reverse("organizations:create"),
            {"name": "Chess Club", "kind": "club", "tagline": "", "description": ""},
        )

        org = Organization.objects.get(name="Chess Club")
        self.student.refresh_from_db()

        self.assertTrue(org.can_manage(self.student))
        self.assertEqual(self.student.role, User.Role.ORGANIZER)

    def test_an_unconfirmed_address_cannot_register_a_society(self):
        """The cheapest way to sell a ticket to nothing is an invented committee."""
        self.client.force_login(self.student)
        response = self.client.post(
            reverse("organizations:create"),
            {"name": "Ghost Club", "kind": "club", "tagline": "", "description": ""},
        )

        self.assertFalse(Organization.objects.filter(name="Ghost Club").exists())
        self.assertRedirects(response, reverse("accounts:profile"))

    def test_follow_toggles_on_and_off(self):
        self.client.force_login(self.student)
        url = reverse("organizations:toggle_follow", args=[self.org.slug])

        self.client.post(url)
        self.assertEqual(self.org.follower_count, 1)

        self.client.post(url)
        self.assertEqual(self.org.follower_count, 0)


class AccountTests(EventTestCase):
    def test_sign_up_creates_an_account_and_logs_in(self):
        response = self.client.post(
            reverse("accounts:register"),
            {
                "first_name": "New",
                "last_name": "Student",
                "username": "newstudent",
                "email": "New@Varsity.test",
                "university": self.uz.pk,
                "role": User.Role.STUDENT,
                "password1": "a-strong-passphrase-42",
                "password2": "a-strong-passphrase-42",
            },
        )

        self.assertRedirects(response, f"{reverse('accounts:profile_edit')}?welcome=1")
        user = User.objects.get(username="newstudent")
        self.assertEqual(user.email, "new@varsity.test")
        self.assertEqual(user.university, self.uz)

    def test_finishing_the_welcome_profile_lands_on_the_feed(self):
        self.client.force_login(self.student)
        response = self.client.post(
            f"{reverse('accounts:profile_edit')}?welcome=1",
            {
                "first_name": "Tanaka",
                "last_name": "Ncube",
                "email": "student@varsity.test",
                "university": self.uz.pk,
                "course": "BSc Computer Science",
            },
        )
        self.assertRedirects(response, reverse("core:discover"))

    def test_sign_up_requires_a_university(self):
        response = self.client.post(
            reverse("accounts:register"),
            {
                "first_name": "No",
                "last_name": "Uni",
                "username": "nouni",
                "email": "nouni@varsity.test",
                "role": User.Role.STUDENT,
                "password1": "a-strong-passphrase-42",
                "password2": "a-strong-passphrase-42",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(User.objects.filter(username="nouni").exists())

    def test_sign_in_works_with_an_email_address(self):
        response = self.client.post(
            reverse("accounts:login"),
            {"username": "student@varsity.test", "password": "testpass12345"},
        )
        self.assertEqual(response.status_code, 302)
        self.assertIn("_auth_user_id", self.client.session)

    def test_duplicate_email_is_rejected(self):
        response = self.client.post(
            reverse("accounts:register"),
            {
                "first_name": "Copy",
                "last_name": "Cat",
                "username": "copycat",
                "email": "student@varsity.test",
                "university": self.uz.pk,
                "role": User.Role.STUDENT,
                "password1": "a-strong-passphrase-42",
                "password2": "a-strong-passphrase-42",
            },
        )
        self.assertContains(response, "already exists")

    def test_ticket_list_pages_require_sign_in(self):
        response = self.client.get(reverse("accounts:tickets"))
        self.assertEqual(response.status_code, 302)

    def test_username_availability_is_reported_live(self):
        url = reverse("accounts:check_availability")

        taken = self.client.get(url, {"field": "username", "value": "student"}).json()
        self.assertTrue(taken["checked"])
        self.assertFalse(taken["available"])

        free = self.client.get(url, {"field": "username", "value": "brandnewname"}).json()
        self.assertTrue(free["available"])

    def test_email_availability_is_reported_live(self):
        url = reverse("accounts:check_availability")

        taken = self.client.get(url, {"field": "email", "value": "student@varsity.test"}).json()
        self.assertFalse(taken["available"])

        free = self.client.get(url, {"field": "email", "value": "nobody@varsity.test"}).json()
        self.assertTrue(free["available"])

    def test_availability_ignores_short_and_unknown_fields(self):
        url = reverse("accounts:check_availability")

        self.assertFalse(self.client.get(url, {"field": "username", "value": "ab"}).json()["checked"])
        self.assertFalse(self.client.get(url, {"field": "wat", "value": "abcdef"}).json()["checked"])

    def test_an_invalid_username_is_flagged(self):
        payload = self.client.get(
            reverse("accounts:check_availability"), {"field": "username", "value": "no spaces!"}
        ).json()
        self.assertFalse(payload["available"])

    def test_remember_me_keeps_the_session_alive(self):
        self.client.post(
            reverse("accounts:login"),
            {"username": "student", "password": "testpass12345", "remember_me": "on"},
        )
        self.assertGreater(self.client.session.get_expiry_age(), 60 * 60 * 24)

    def test_without_remember_me_the_session_ends_with_the_browser(self):
        self.client.post(
            reverse("accounts:login"),
            {"username": "student", "password": "testpass12345"},
        )
        self.assertTrue(self.client.session.get_expire_at_browser_close())

    def test_repeated_failures_are_throttled(self):
        from django.core.cache import cache

        cache.clear()
        for _ in range(9):
            self.client.post(
                reverse("accounts:login"), {"username": "student", "password": "wrong-password"}
            )

        # Even the correct password is refused while the lockout holds.
        response = self.client.post(
            reverse("accounts:login"), {"username": "student", "password": "testpass12345"}
        )
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("_auth_user_id", self.client.session)
        cache.clear()


class ListingQueryCountTests(EventTestCase):
    """A listing must not get more expensive as it gets longer.

    Every card asks for `availability`, which asks `is_full`, which asks
    `reserved_count`. That used to be a query per card — and, before the read
    was separated from the write, a set of UPDATEs per card as well. The counts
    now come from `with_counts()`, so the page costs the same whether it shows
    one event or twenty.
    """

    def make_events(self, count, **extra):
        now = timezone.now()
        for index in range(count):
            event = Event.objects.create(
                title=f"Event {index}",
                organization=self.org,
                created_by=self.organizer,
                category=self.category,
                starts_at=now + timedelta(days=index + 1),
                ends_at=now + timedelta(days=index + 1, hours=3),
                status=Event.Status.PUBLISHED,
                capacity=50,
                **extra,
            )
            Registration.objects.create(
                event=event, user=self.student, status=Registration.Status.CONFIRMED
            )

    def count_queries_for(self, count):
        Event.objects.all().delete()
        self.make_events(count)

        with CaptureQueriesContext(connection) as captured:
            response = self.client.get(reverse("events:list"))

        self.assertEqual(response.status_code, 200)
        return len(captured.captured_queries)

    def test_the_listing_does_not_get_dearer_with_more_events(self):
        few = self.count_queries_for(3)
        many = self.count_queries_for(12)

        self.assertEqual(
            few,
            many,
            f"listing 12 events cost {many} queries against {few} for 3 — "
            "the per-card count is querying again",
        )

    def test_a_listing_issues_no_writes(self):
        self.make_events(6)

        with CaptureQueriesContext(connection) as captured:
            self.client.get(reverse("events:list"))

        written = [
            q["sql"]
            for q in captured.captured_queries
            if q["sql"].strip().upper().startswith(("UPDATE", "INSERT", "DELETE"))
        ]
        self.assertEqual(written, [], f"a listing wrote to the database: {written}")

    def test_the_annotated_figure_matches_the_unannotated_one(self):
        """The two ways of counting a seat have to agree, or a card and its page differ."""
        self.make_events(1)
        event = Event.objects.get(title="Event 0")
        Registration.objects.create(
            event=event, user=self.other, status=Registration.Status.AWAITING_PAYMENT
        )

        annotated = Event.objects.with_counts().get(pk=event.pk)
        plain = Event.objects.get(pk=event.pk)

        self.assertEqual(annotated.reserved_count, plain.reserved_count)
        self.assertEqual(annotated.attendee_count, plain.attendee_count)
        self.assertEqual(annotated.reserved_count, 2)


class StaleCountTests(EventTestCase):
    """An annotated instance is carrying numbers from when its query ran."""

    def setUp(self):
        super().setUp()
        self.event.capacity = 2
        self.event.is_free = True
        self.event.save()

    def test_registering_against_an_annotated_event_sees_its_own_effect(self):
        """Otherwise the last seat sells twice: is_full would still read the old count."""
        event = Event.objects.with_counts().get(pk=self.event.pk)

        event.register(self.student)
        event.register(self.other)
        third = event.register(make_user("third", university=self.uz))

        self.assertEqual(third.status, Registration.Status.WAITLISTED)

    def test_forgetting_the_counts_falls_back_to_asking_the_database(self):
        event = Event.objects.with_counts().get(pk=self.event.pk)
        self.assertEqual(event.reserved_count, 0)

        Registration.objects.create(
            event=event, user=self.student, status=Registration.Status.CONFIRMED
        )

        self.assertEqual(event.reserved_count, 0, "the annotation should still be cached")
        event.forget_counts()
        self.assertEqual(event.reserved_count, 1)

    def test_availability_is_recomputed_after_the_counts_are_dropped(self):
        event = Event.objects.with_counts().get(pk=self.event.pk)
        self.assertEqual(event.availability["state"], "free")

        for name in ("one", "two"):
            Registration.objects.create(
                event=event,
                user=make_user(name, university=self.uz),
                status=Registration.Status.CONFIRMED,
            )
        event.forget_counts()

        self.assertEqual(event.availability["state"], "waitlist")
