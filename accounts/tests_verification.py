"""Confirming an email address, and what an unconfirmed one costs.

The rule under test is deliberately narrow: an unconfirmed address browses,
saves and gets tickets exactly as before, and is stopped only where it could
put something in front of the whole country.
"""

from datetime import timedelta

from django.core import mail as django_mail
from django.core import signing
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from accounts import verification
from accounts.models import University, User
from events.models import Event
from organizations.models import Membership, Organization
from varsity.testing import login_verified


def make_user(username, **extra):
    return User.objects.create_user(
        username=username,
        email=f"{username}@varsity.test",
        password="testpass12345",
        **extra,
    )


class TokenTests(TestCase):
    def setUp(self):
        self.user = make_user("student")

    def test_a_fresh_token_reads_back_as_its_user(self):
        token = verification.make_token(self.user)
        self.assertEqual(verification.read_token(token), self.user)

    def test_a_tampered_token_is_refused(self):
        token = verification.make_token(self.user)
        swapped = token[:-1] + ("a" if token[-1] != "a" else "b")
        self.assertIsNone(verification.read_token(swapped))

    def test_an_expired_token_is_refused(self):
        token = verification.make_token(self.user)
        original = verification.MAX_AGE_SECONDS
        verification.MAX_AGE_SECONDS = -1
        try:
            self.assertIsNone(verification.read_token(token))
        finally:
            verification.MAX_AGE_SECONDS = original

    def test_changing_the_address_kills_a_link_already_sent(self):
        """The point of signing the address: a link mailed to the old inbox
        must not confirm the new one."""
        token = verification.make_token(self.user)
        self.user.email = "somewhere-else@varsity.test"
        self.user.save(update_fields=["email"])

        self.assertIsNone(verification.read_token(token))

    def test_a_token_for_a_deleted_account_is_refused(self):
        token = verification.make_token(self.user)
        self.user.delete()
        self.assertIsNone(verification.read_token(token))

    def test_a_token_signed_with_another_salt_is_refused(self):
        forged = signing.dumps(
            {"pk": self.user.pk, "email": self.user.email}, salt="something-else"
        )
        self.assertIsNone(verification.read_token(forged))


class VerifyViewTests(TestCase):
    def setUp(self):
        self.user = make_user("student")

    def test_the_link_confirms_the_address(self):
        token = verification.make_token(self.user)
        self.client.get(reverse("accounts:verify_email", kwargs={"token": token}))

        self.user.refresh_from_db()
        self.assertTrue(self.user.email_is_verified)

    def test_the_link_works_without_signing_in(self):
        """Email is read on a different device from the one that signed up."""
        token = verification.make_token(self.user)
        response = self.client.get(reverse("accounts:verify_email", kwargs={"token": token}))

        self.assertEqual(response.status_code, 302)
        self.user.refresh_from_db()
        self.assertTrue(self.user.email_is_verified)

    def test_a_bad_token_confirms_nothing(self):
        self.client.get(reverse("accounts:verify_email", kwargs={"token": "not-a-token"}))

        self.user.refresh_from_db()
        self.assertFalse(self.user.email_is_verified)

    def test_confirming_twice_keeps_the_first_timestamp(self):
        token = verification.make_token(self.user)
        self.client.get(reverse("accounts:verify_email", kwargs={"token": token}))
        self.user.refresh_from_db()
        first = self.user.email_verified_at

        self.client.get(reverse("accounts:verify_email", kwargs={"token": token}))
        self.user.refresh_from_db()
        self.assertEqual(self.user.email_verified_at, first)


class SignUpTests(TestCase):
    def setUp(self):
        self.university = University.objects.create(
            name="University of Zimbabwe", short_name="UZ"
        )

    def sign_up(self, username="tanaka"):
        return self.client.post(
            reverse("accounts:register"),
            {
                "first_name": "Tanaka",
                "last_name": "Moyo",
                "username": username,
                "email": f"{username}@varsity.test",
                "university": self.university.pk,
                "role": User.Role.STUDENT,
                "password1": "a-strong-passphrase-42",
                "password2": "a-strong-passphrase-42",
            },
        )

    def test_signing_up_sends_the_link_and_leaves_the_address_unconfirmed(self):
        django_mail.outbox.clear()
        self.sign_up()

        created = User.objects.get(username="tanaka")
        self.assertFalse(created.email_is_verified)
        self.assertEqual(len(django_mail.outbox), 1)
        self.assertIn("confirm", django_mail.outbox[0].subject.lower())

    def test_a_dead_mail_server_still_lets_somebody_sign_up(self):
        """An SMTP outage must not close the front door."""
        with self.settings(
            EMAIL_BACKEND="django.core.mail.backends.smtp.EmailBackend",
            EMAIL_HOST="127.0.0.1",
            EMAIL_PORT=1,
            EMAIL_TIMEOUT=1,
        ):
            self.sign_up("rudo")

        self.assertTrue(User.objects.filter(username="rudo").exists())

    def test_changing_your_email_un_confirms_it(self):
        user = make_user("student", university=self.university)
        verification.mark_verified(user)
        self.client.force_login(user)

        self.client.post(
            reverse("accounts:profile_edit"),
            {
                "first_name": "Student",
                "last_name": "One",
                "email": "moved@varsity.test",
                "university": self.university.pk,
                "student_id": "",
                "course": "",
                "year_of_study": "",
                "phone": "",
                "bio": "",
            },
        )

        user.refresh_from_db()
        self.assertEqual(user.email, "moved@varsity.test")
        self.assertFalse(user.email_is_verified)


class StaffExemptionTests(TestCase):
    def test_staff_count_as_verified_without_clicking_anything(self):
        """`createsuperuser` sends no mail; locking the admin out is not a policy."""
        admin = make_user("admin", is_staff=True, is_superuser=True)

        self.assertTrue(admin.email_is_verified)
        self.assertTrue(admin.can_publish)


class PublishGateTests(TestCase):
    """Who may put an event in front of students, and whether a human sees it first."""

    def setUp(self):
        self.university = University.objects.create(
            name="University of Zimbabwe", short_name="UZ"
        )
        self.organizer = make_user(
            "organizer", role=User.Role.ORGANIZER, university=self.university
        )
        verification.mark_verified(self.organizer)

        self.org = Organization.objects.create(
            name="New Society", created_by=self.organizer, university=self.university
        )
        Membership.objects.create(
            organization=self.org, user=self.organizer, role=Membership.Role.OWNER
        )

    def make_event(self, **extra):
        now = timezone.now()
        return Event.objects.create(
            title=extra.pop("title", "First Night"),
            organization=self.org,
            created_by=self.organizer,
            starts_at=now + timedelta(days=7),
            ends_at=now + timedelta(days=7, hours=4),
            **extra,
        )

    def test_an_unverified_society_goes_to_the_queue(self):
        event = self.make_event()

        self.assertEqual(event.submit_for_publication(self.organizer), Event.Status.REVIEW)
        self.assertIsNotNone(event.submitted_at)

    def test_a_verified_society_goes_straight_up(self):
        self.org.is_verified = True
        self.org.save(update_fields=["is_verified"])

        event = self.make_event()
        self.assertEqual(event.submit_for_publication(self.organizer), Event.Status.PUBLISHED)

    def test_a_verified_organizer_goes_straight_up_from_any_society(self):
        self.organizer.is_verified_organizer = True
        self.organizer.save(update_fields=["is_verified_organizer"])

        event = self.make_event()
        self.assertEqual(event.submit_for_publication(self.organizer), Event.Status.PUBLISHED)

    def test_an_unconfirmed_address_cannot_publish_at_all(self):
        self.organizer.email_verified_at = None
        self.organizer.save(update_fields=["email_verified_at"])

        event = self.make_event()
        self.assertEqual(event.submit_for_publication(self.organizer), Event.Status.DRAFT)

    def test_editing_a_live_event_does_not_pull_it_off_the_feed(self):
        """An organizer fixing a typo must not silently unpublish their own event."""
        event = self.make_event(status=Event.Status.PUBLISHED)

        self.assertEqual(event.submit_for_publication(self.organizer), Event.Status.PUBLISHED)

    def test_a_queued_event_is_invisible_to_students(self):
        event = self.make_event(status=Event.Status.REVIEW)
        student = make_user("student", university=self.university)

        self.assertFalse(event.can_be_seen_by(student))
        self.assertNotIn(event, Event.objects.published())

    def test_a_queued_event_is_visible_to_the_staff_who_must_review_it(self):
        event = self.make_event(status=Event.Status.REVIEW)
        staff = make_user("staff", is_staff=True)

        self.assertTrue(event.can_be_seen_by(staff))

    def test_resubmitting_clears_the_previous_verdict(self):
        staff = make_user("staff", is_staff=True)
        event = self.make_event(status=Event.Status.REVIEW)
        event.send_back(staff, "Venue doesn't exist.")

        event.submit_for_publication(self.organizer)

        self.assertEqual(event.status, Event.Status.REVIEW)
        self.assertEqual(event.review_note, "")
        self.assertIsNone(event.reviewed_at)

    def test_the_queue_is_oldest_first(self):
        now = timezone.now()
        older = self.make_event(
            title="Older", status=Event.Status.REVIEW, submitted_at=now - timedelta(hours=5)
        )
        newer = self.make_event(
            title="Newer", status=Event.Status.REVIEW, submitted_at=now
        )

        self.assertEqual(list(Event.objects.awaiting_review()), [older, newer])


class VerifyingASocietyTests(TestCase):
    def setUp(self):
        self.organizer = make_user("organizer", role=User.Role.ORGANIZER)
        self.org = Organization.objects.create(name="Society", created_by=self.organizer)
        Membership.objects.create(
            organization=self.org, user=self.organizer, role=Membership.Role.OWNER
        )

    def test_verifying_a_society_also_trusts_the_people_running_it(self):
        staff = make_user("staff", is_staff=True, is_superuser=True)
        login_verified(self.client, staff)

        self.client.post(reverse("core:staff_society_action", args=[self.org.slug, "verify"]))

        self.organizer.refresh_from_db()
        self.assertTrue(self.organizer.is_verified_organizer)
        self.assertTrue(self.organizer.publishes_without_review)


class ReviewActionTests(TestCase):
    def setUp(self):
        self.organizer = make_user("organizer", role=User.Role.ORGANIZER)
        self.org = Organization.objects.create(name="Society", created_by=self.organizer)
        Membership.objects.create(
            organization=self.org, user=self.organizer, role=Membership.Role.OWNER
        )

        now = timezone.now()
        self.event = Event.objects.create(
            title="Queued Night",
            organization=self.org,
            created_by=self.organizer,
            starts_at=now + timedelta(days=5),
            ends_at=now + timedelta(days=5, hours=3),
            status=Event.Status.REVIEW,
            submitted_at=now,
        )

        self.staff = make_user("staff", is_staff=True, is_superuser=True)
        login_verified(self.client, self.staff)

    def test_approving_publishes_and_emails_the_society(self):
        django_mail.outbox.clear()
        self.client.post(reverse("core:staff_event_action", args=[self.event.slug, "publish"]))

        self.event.refresh_from_db()
        self.assertEqual(self.event.status, Event.Status.PUBLISHED)
        self.assertEqual(self.event.reviewed_by, self.staff)
        self.assertEqual(len(django_mail.outbox), 1)

    def test_sending_back_returns_a_draft_carrying_the_reason(self):
        django_mail.outbox.clear()
        self.client.post(
            reverse("core:staff_event_action", args=[self.event.slug, "send_back"]),
            {"note": "No such venue at UZ."},
        )

        self.event.refresh_from_db()
        self.assertEqual(self.event.status, Event.Status.DRAFT)
        self.assertEqual(self.event.review_note, "No such venue at UZ.")
        self.assertEqual(len(django_mail.outbox), 1)
        self.assertIn("No such venue at UZ.", django_mail.outbox[0].body)

    def test_an_ordinary_organizer_cannot_approve_their_own_event(self):
        self.client.logout()
        self.client.force_login(self.organizer)

        self.client.post(reverse("core:staff_event_action", args=[self.event.slug, "publish"]))

        self.event.refresh_from_db()
        self.assertEqual(self.event.status, Event.Status.REVIEW)
