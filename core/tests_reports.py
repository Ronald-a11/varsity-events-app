"""Telling us something on the feed shouldn't be there.

The review queue catches a bad event before students see it, but only from a
society we haven't verified. This is the other direction — everything already
live, watched by the people standing in front of it. What matters here is that
a report reaches a human quickly, that twenty reports about one scam are one
decision rather than twenty, and that nothing is ever taken down automatically.
"""

from datetime import timedelta

from django.core import mail as django_mail
from django.db import IntegrityError, transaction
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from accounts import verification
from accounts.models import University, User
from core.models import Report, open_reports_for
from events.models import Event
from organizations.models import Membership, Organization
from varsity.testing import login_verified


def make_user(username, **extra):
    user = User.objects.create_user(
        username=username,
        email=f"{username}@varsity.test",
        password="testpass12345",
        **extra,
    )
    verification.mark_verified(user)
    return user


class ReportTestCase(TestCase):
    def setUp(self):
        self.university = University.objects.create(
            name="University of Zimbabwe", short_name="UZ"
        )
        self.organizer = make_user("organizer", role=User.Role.ORGANIZER)
        self.org = Organization.objects.create(
            name="Dodgy Society", university=self.university
        )
        Membership.objects.create(
            organization=self.org, user=self.organizer, role=Membership.Role.OWNER
        )

        now = timezone.now()
        self.event = Event.objects.create(
            title="Too Good To Be True",
            organization=self.org,
            created_by=self.organizer,
            starts_at=now + timedelta(days=3),
            ends_at=now + timedelta(days=3, hours=5),
            status=Event.Status.PUBLISHED,
            is_free=False,
            price=5,
        )
        self.student = make_user("student", university=self.university)
        self.staff = make_user("staff", is_staff=True, is_superuser=True)

    def event_url(self):
        return reverse("core:report", args=["event", self.event.slug])

    def society_url(self):
        return reverse("core:report", args=["society", self.org.slug])

    def payload(self, **extra):
        return {
            "reason": Report.Reason.SCAM,
            "detail": "They asked me to EcoCash 0771234567 directly and then blocked me.",
            **extra,
        }


class MakingAReportTests(ReportTestCase):
    def test_an_event_can_be_reported(self):
        self.client.force_login(self.student)
        self.client.post(self.event_url(), self.payload())

        item = Report.objects.get()
        self.assertEqual(item.event, self.event)
        self.assertIsNone(item.organization)
        self.assertTrue(item.is_open)

    def test_a_society_can_be_reported(self):
        self.client.force_login(self.student)
        self.client.post(self.society_url(), self.payload(reason=Report.Reason.IMPERSONATION))

        item = Report.objects.get()
        self.assertEqual(item.organization, self.org)
        self.assertIsNone(item.event)

    def test_the_form_renders(self):
        self.client.force_login(self.student)

        response = self.client.get(self.event_url())

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Too Good To Be True")

    def test_signing_in_is_required(self):
        """An anonymous queue is a spam queue: nobody can follow one up."""
        response = self.client.post(self.event_url(), self.payload())

        self.assertFalse(Report.objects.exists())
        self.assertEqual(response.status_code, 302)

    def test_a_reason_is_required(self):
        self.client.force_login(self.student)
        self.client.post(self.event_url(), {"detail": "just because"})

        self.assertFalse(Report.objects.exists())

    def test_the_detail_is_optional(self):
        self.client.force_login(self.student)
        self.client.post(self.event_url(), {"reason": Report.Reason.SPAM})

        self.assertTrue(Report.objects.exists())

    def test_reporting_the_same_thing_twice_does_not_stack(self):
        self.client.force_login(self.student)
        self.client.post(self.event_url(), self.payload())
        self.client.post(self.event_url(), self.payload(reason=Report.Reason.SPAM))

        self.assertEqual(Report.objects.count(), 1)

    def test_the_database_refuses_a_second_open_report_as_well(self):
        Report.objects.create(
            event=self.event, reporter=self.student, reason=Report.Reason.SCAM
        )

        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                Report.objects.create(
                    event=self.event, reporter=self.student, reason=Report.Reason.SPAM
                )

    def test_reporting_again_is_allowed_once_the_first_was_decided(self):
        first = Report.objects.create(
            event=self.event, reporter=self.student, reason=Report.Reason.SCAM
        )
        first.dismiss(self.staff)

        self.client.force_login(self.student)
        self.client.post(self.event_url(), self.payload())

        self.assertEqual(Report.objects.filter(status=Report.Status.OPEN).count(), 1)

    def test_an_organizer_reporting_their_own_event_is_pointed_at_the_edit_button(self):
        self.client.force_login(self.organizer)

        response = self.client.post(self.event_url(), self.payload())

        self.assertFalse(Report.objects.exists())
        self.assertRedirects(response, self.event.get_absolute_url())

    def test_an_unknown_kind_is_a_404(self):
        self.client.force_login(self.student)

        response = self.client.get(reverse("core:report", args=["banana", self.org.slug]))

        self.assertEqual(response.status_code, 404)

    def test_a_report_must_point_at_exactly_one_thing(self):
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                Report.objects.create(reporter=self.student, reason=Report.Reason.SPAM)

        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                Report.objects.create(
                    event=self.event,
                    organization=self.org,
                    reporter=self.student,
                    reason=Report.Reason.SPAM,
                )


class AlertingStaffTests(ReportTestCase):
    def test_the_first_report_emails_staff(self):
        self.client.force_login(self.student)
        django_mail.outbox.clear()
        self.client.post(self.event_url(), self.payload())

        self.assertEqual(len(django_mail.outbox), 1)
        self.assertIn(self.staff.email, django_mail.outbox[0].to)
        self.assertIn("Too Good To Be True", django_mail.outbox[0].subject)

    def test_the_second_report_about_the_same_thing_does_not_email_again(self):
        """Twenty people flagging one scam is one alert. Twenty is a filter rule."""
        self.client.force_login(self.student)
        self.client.post(self.event_url(), self.payload())

        django_mail.outbox.clear()
        self.client.force_login(make_user("another"))
        self.client.post(self.event_url(), self.payload())

        self.assertEqual(Report.objects.count(), 2)
        self.assertEqual(len(django_mail.outbox), 0)

    def test_a_report_after_the_last_one_was_decided_alerts_again(self):
        first = Report.objects.create(
            event=self.event, reporter=make_user("earlier"), reason=Report.Reason.SCAM
        )
        first.dismiss(self.staff)

        self.client.force_login(self.student)
        django_mail.outbox.clear()
        self.client.post(self.event_url(), self.payload())

        self.assertEqual(len(django_mail.outbox), 1)

    def test_reporting_survives_a_dead_mail_server(self):
        """The report is the point; the alert is a convenience."""
        self.client.force_login(self.student)

        with self.settings(
            EMAIL_BACKEND="django.core.mail.backends.smtp.EmailBackend",
            EMAIL_HOST="127.0.0.1",
            EMAIL_PORT=1,
            EMAIL_TIMEOUT=1,
        ):
            self.client.post(self.event_url(), self.payload())

        self.assertTrue(Report.objects.exists())


class DecidingTests(ReportTestCase):
    def report_from(self, username, **extra):
        return Report.objects.create(
            event=self.event,
            reporter=make_user(username),
            reason=Report.Reason.SCAM,
            **extra,
        )

    def test_dismissing_closes_every_report_on_the_same_thing(self):
        """Staff judge the event, not each complaint about it."""
        first = self.report_from("one")
        self.report_from("two")
        self.report_from("three")

        login_verified(self.client, self.staff)
        self.client.post(
            reverse("core:staff_report_action", args=[first.pk, "dismiss"]),
            {"note": "Checked with the society, it's real."},
        )

        self.assertEqual(Report.objects.filter(status=Report.Status.OPEN).count(), 0)
        self.assertEqual(Report.objects.filter(status=Report.Status.DISMISSED).count(), 3)

    def test_dismissing_leaves_the_event_up(self):
        first = self.report_from("one")
        login_verified(self.client, self.staff)

        self.client.post(reverse("core:staff_report_action", args=[first.pk, "dismiss"]))

        self.event.refresh_from_db()
        self.assertEqual(self.event.status, Event.Status.PUBLISHED)

    def test_upholding_takes_the_event_off_the_feed(self):
        first = self.report_from("one")
        login_verified(self.client, self.staff)

        self.client.post(reverse("core:staff_report_action", args=[first.pk, "uphold"]))

        self.event.refresh_from_db()
        self.assertEqual(self.event.status, Event.Status.DRAFT)
        self.assertNotIn(self.event, Event.objects.published())

    def test_upholding_keeps_the_registrations_and_payments(self):
        """A draft, not a delete — if money moved, that record is the evidence."""
        from events.models import Registration

        Registration.objects.create(event=self.event, user=self.student)
        first = self.report_from("one")
        login_verified(self.client, self.staff)

        self.client.post(reverse("core:staff_report_action", args=[first.pk, "uphold"]))

        self.assertTrue(Event.objects.filter(pk=self.event.pk).exists())
        self.assertEqual(self.event.registrations.count(), 1)

    def test_upholding_a_society_report_suspends_it(self):
        item = Report.objects.create(
            organization=self.org, reporter=self.student, reason=Report.Reason.IMPERSONATION
        )
        login_verified(self.client, self.staff)

        self.client.post(reverse("core:staff_report_action", args=[item.pk, "uphold"]))

        self.org.refresh_from_db()
        self.assertFalse(self.org.is_active)

    def test_a_verdict_records_who_gave_it(self):
        first = self.report_from("one")
        login_verified(self.client, self.staff)

        self.client.post(
            reverse("core:staff_report_action", args=[first.pk, "dismiss"]),
            {"note": "Spoke to the committee."},
        )

        first.refresh_from_db()
        self.assertEqual(first.reviewed_by, self.staff)
        self.assertIsNotNone(first.reviewed_at)
        self.assertEqual(first.review_note, "Spoke to the committee.")

    def test_a_decided_report_is_not_decided_twice(self):
        first = self.report_from("one")
        login_verified(self.client, self.staff)
        self.client.post(reverse("core:staff_report_action", args=[first.pk, "dismiss"]))

        self.client.post(reverse("core:staff_report_action", args=[first.pk, "uphold"]))

        self.event.refresh_from_db()
        self.assertEqual(self.event.status, Event.Status.PUBLISHED)

    def test_deciding_one_thing_leaves_reports_about_another_alone(self):
        other = Event.objects.create(
            title="Something Else",
            organization=self.org,
            created_by=self.organizer,
            starts_at=timezone.now() + timedelta(days=9),
            ends_at=timezone.now() + timedelta(days=9, hours=2),
            status=Event.Status.PUBLISHED,
        )
        first = self.report_from("one")
        elsewhere = Report.objects.create(
            event=other, reporter=make_user("two"), reason=Report.Reason.SPAM
        )

        login_verified(self.client, self.staff)
        self.client.post(reverse("core:staff_report_action", args=[first.pk, "uphold"]))

        elsewhere.refresh_from_db()
        self.assertTrue(elsewhere.is_open)

    def test_an_ordinary_user_cannot_decide_anything(self):
        first = self.report_from("one")
        self.client.force_login(self.student)

        self.client.post(reverse("core:staff_report_action", args=[first.pk, "uphold"]))

        first.refresh_from_db()
        self.event.refresh_from_db()
        self.assertTrue(first.is_open)
        self.assertEqual(self.event.status, Event.Status.PUBLISHED)


class QueueTests(ReportTestCase):
    def test_the_queue_renders_and_is_staff_only(self):
        Report.objects.create(
            event=self.event, reporter=self.student, reason=Report.Reason.SCAM
        )

        self.client.force_login(self.student)
        self.assertNotEqual(self.client.get(reverse("core:staff_reports")).status_code, 200)

        login_verified(self.client, self.staff)
        response = self.client.get(reverse("core:staff_reports"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Too Good To Be True")

    def test_the_most_reported_thing_leads_the_queue(self):
        """A scam twenty people flagged outranks one grumble about a venue."""
        quiet = Event.objects.create(
            title="Only One Complaint",
            organization=self.org,
            created_by=self.organizer,
            starts_at=timezone.now() + timedelta(days=1),
            ends_at=timezone.now() + timedelta(days=1, hours=2),
            status=Event.Status.PUBLISHED,
        )
        # The lone complaint is filed first, so arrival order would lead with it.
        Report.objects.create(
            event=quiet, reporter=make_user("early"), reason=Report.Reason.WRONG
        )
        for name in ("a", "b", "c"):
            Report.objects.create(
                event=self.event, reporter=make_user(name), reason=Report.Reason.SCAM
            )

        login_verified(self.client, self.staff)
        groups = self.client.get(reverse("core:staff_reports")).context["groups"]

        self.assertEqual(groups[0]["lead"].target, self.event)
        self.assertEqual(groups[0]["count"], 3)

    def test_reports_about_one_thing_are_shown_together(self):
        for name in ("a", "b"):
            Report.objects.create(
                event=self.event, reporter=make_user(name), reason=Report.Reason.SCAM
            )

        login_verified(self.client, self.staff)
        groups = self.client.get(reverse("core:staff_reports")).context["groups"]

        self.assertEqual(len(groups), 1)
        self.assertEqual(len(groups[0]["reports"]), 2)

    def test_an_empty_queue_renders(self):
        login_verified(self.client, self.staff)

        response = self.client.get(reverse("core:staff_reports"))

        self.assertContains(response, "Nothing reported")

    def test_the_decided_tabs_render(self):
        item = Report.objects.create(
            event=self.event, reporter=self.student, reason=Report.Reason.SCAM
        )
        item.dismiss(self.staff, "Fine.")
        login_verified(self.client, self.staff)

        for tab in ("dismissed", "actioned"):
            response = self.client.get(reverse("core:staff_reports"), {"show": tab})
            self.assertEqual(response.status_code, 200)


class HelperTests(ReportTestCase):
    def test_open_reports_for_finds_an_events_reports(self):
        Report.objects.create(
            event=self.event, reporter=self.student, reason=Report.Reason.SCAM
        )

        self.assertEqual(open_reports_for(self.event).count(), 1)
        self.assertEqual(open_reports_for(self.org).count(), 0)

    def test_the_target_helpers_read_either_kind(self):
        for_event = Report.objects.create(
            event=self.event, reporter=self.student, reason=Report.Reason.SCAM
        )
        for_society = Report.objects.create(
            organization=self.org, reporter=make_user("other"), reason=Report.Reason.SPAM
        )

        self.assertEqual(for_event.target_name, "Too Good To Be True")
        self.assertEqual(for_event.target_kind, "event")
        self.assertEqual(for_society.target_name, "Dodgy Society")
        self.assertEqual(for_society.target_kind, "society")


class ReportLinkTests(ReportTestCase):
    def test_the_event_page_offers_the_link_to_a_signed_in_stranger(self):
        self.client.force_login(self.student)

        response = self.client.get(self.event.get_absolute_url())

        self.assertContains(response, reverse("core:report", args=["event", self.event.slug]))

    def test_the_organizer_is_not_offered_it_on_their_own_event(self):
        self.client.force_login(self.organizer)

        response = self.client.get(self.event.get_absolute_url())

        self.assertNotContains(
            response, reverse("core:report", args=["event", self.event.slug])
        )

    def test_the_society_page_offers_it(self):
        self.client.force_login(self.student)

        response = self.client.get(self.org.get_absolute_url())

        self.assertContains(response, reverse("core:report", args=["society", self.org.slug]))
