from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

from django.core import mail
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from accounts.models import University, User
from core.mail import send_mail, send_ticket_confirmed
from events.models import Event, Registration
from organizations.models import Membership, Organization
from payments.models import Payment

BASE_URL = "https://varsity.test"


@override_settings(SITE_BASE_URL=BASE_URL)
class MailTestCase(TestCase):
    def setUp(self):
        self.uz = University.objects.create(
            name="University of Zimbabwe", short_name="UZ", city="Harare"
        )
        self.organizer = User.objects.create_user(
            username="organizer", email="organizer@uz.test", password="testpass12345",
            role=User.Role.ORGANIZER, university=self.uz,
        )
        self.student = User.objects.create_user(
            username="student", email="student@uz.test", password="testpass12345",
            first_name="Tino", university=self.uz,
        )
        self.org = Organization.objects.create(
            name="Test Society", created_by=self.organizer, university=self.uz
        )
        Membership.objects.create(
            organization=self.org, user=self.organizer, role=Membership.Role.OWNER
        )
        self.event = Event.objects.create(
            title="Jazz Night",
            organization=self.org,
            created_by=self.organizer,
            starts_at=timezone.now() + timedelta(days=7),
            ends_at=timezone.now() + timedelta(days=7, hours=3),
            status=Event.Status.PUBLISHED,
            is_free=True,
        )


class SendMailTests(MailTestCase):
    def test_a_missing_address_is_not_an_error(self):
        """Plenty of accounts have no email; that must not raise mid-payment."""
        self.assertFalse(send_mail(to="", subject="x", template="test"))
        self.assertEqual(len(mail.outbox), 0)

    def test_blank_addresses_are_dropped_from_a_list(self):
        self.assertTrue(send_mail(to=["", "real@uz.test", ""], subject="x", template="test"))
        self.assertEqual(mail.outbox[0].to, ["real@uz.test"])

    def test_a_broken_template_is_swallowed_and_logged(self):
        with self.assertLogs("core.mail", level="ERROR"):
            sent = send_mail(to="a@uz.test", subject="x", template="does_not_exist")

        self.assertFalse(sent)
        self.assertEqual(len(mail.outbox), 0)

    def test_a_failing_mail_server_is_swallowed_and_logged(self):
        with patch("django.core.mail.EmailMultiAlternatives.send", side_effect=OSError("smtp down")):
            with self.assertLogs("core.mail", level="ERROR"):
                sent = send_mail(to="a@uz.test", subject="x", template="test")

        self.assertFalse(sent)

    def test_every_email_carries_a_plain_text_and_an_html_part(self):
        send_mail(to="a@uz.test", subject="x", template="test")

        message = mail.outbox[0]
        self.assertTrue(message.body.strip())
        self.assertEqual(message.alternatives[0][1], "text/html")

    def test_links_are_absolute_so_they_work_from_an_inbox(self):
        registration = self.event.register(self.student)
        mail.outbox.clear()

        send_ticket_confirmed(registration)

        html = mail.outbox[0].alternatives[0][0]
        self.assertIn(f"{BASE_URL}/events/tickets/{registration.ticket_code}", html)
        self.assertNotIn('href="/', html)


class TicketEmailTests(MailTestCase):
    def test_a_free_signup_sends_the_ticket_immediately(self):
        registration = self.event.register(self.student)

        self.assertEqual(len(mail.outbox), 1)
        message = mail.outbox[0]
        self.assertEqual(message.to, ["student@uz.test"])
        self.assertIn("Jazz Night", message.subject)
        self.assertIn(registration.ticket_code, message.body)

    def test_a_paid_signup_waits_for_the_money(self):
        self.event.is_free = False
        self.event.price = Decimal("5.00")
        self.event.save(update_fields=["is_free", "price"])

        self.event.register(self.student)

        self.assertEqual(len(mail.outbox), 0)

    def test_settling_a_payment_sends_the_ticket_and_a_receipt(self):
        self.event.is_free = False
        self.event.price = Decimal("5.00")
        self.event.save(update_fields=["is_free", "price"])
        registration = self.event.register(self.student)
        payment = Payment.objects.create(
            registration=registration, user=self.student, amount=self.event.price,
            status=Payment.Status.PAID,
        )
        mail.outbox.clear()

        payment.settle()

        subjects = sorted(m.subject for m in mail.outbox)
        self.assertEqual(len(subjects), 2)
        self.assertTrue(any("ticket" in s.lower() for s in subjects))
        self.assertTrue(any("receipt" in s.lower() for s in subjects))

    def test_a_dead_mail_server_still_confirms_the_ticket(self):
        """The whole reason sending is best-effort. Money moved; the ticket is real."""
        self.event.is_free = False
        self.event.price = Decimal("5.00")
        self.event.save(update_fields=["is_free", "price"])
        registration = self.event.register(self.student)
        payment = Payment.objects.create(
            registration=registration, user=self.student, amount=self.event.price,
            status=Payment.Status.PAID,
        )

        with patch("django.core.mail.EmailMultiAlternatives.send", side_effect=OSError("smtp down")):
            with self.assertLogs("core.mail", level="ERROR"):
                settled = payment.settle()

        registration.refresh_from_db()
        self.assertTrue(settled)
        self.assertEqual(registration.status, Registration.Status.CONFIRMED)


class WaitlistEmailTests(MailTestCase):
    def setUp(self):
        super().setUp()
        self.event.capacity = 1
        self.event.allow_waitlist = True
        self.event.save(update_fields=["capacity", "allow_waitlist"])
        self.other = User.objects.create_user(
            username="other", email="other@uz.test", password="testpass12345",
            university=self.uz,
        )

    def test_being_promoted_off_the_waitlist_sends_an_email(self):
        first = self.event.register(self.student)
        self.event.register(self.other)  # waitlisted — the event holds one seat
        mail.outbox.clear()

        first.cancel()

        recipients = [address for m in mail.outbox for address in m.to]
        self.assertIn("other@uz.test", recipients)
        self.assertTrue(any("place opened up" in m.subject.lower() for m in mail.outbox))


class TransferEmailTests(MailTestCase):
    def setUp(self):
        super().setUp()
        self.event.is_free = False
        self.event.price = Decimal("5.00")
        self.event.save(update_fields=["is_free", "price"])
        self.registration = self.event.register(self.student)
        self.payment = Payment.objects.create(
            registration=self.registration, user=self.student, amount=self.event.price,
            method=Payment.Method.ECOCASH_DIRECT, status=Payment.Status.AWAITING_TRANSFER,
        )
        mail.outbox.clear()

    def test_submitting_a_code_tells_the_organizers(self):
        self.payment.submit_confirmation("MP240816.1423.A12345")

        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ["organizer@uz.test"])
        self.assertIn("MP240816.1423.A12345", mail.outbox[0].body)

    def test_a_rejection_quotes_the_code_that_did_not_match(self):
        self.payment.submit_confirmation("MP240816.1423.WRONG")
        mail.outbox.clear()

        self.payment.reject(by_user=self.organizer, reason="No transfer for that code.")

        self.assertEqual(mail.outbox[0].to, ["student@uz.test"])
        html = mail.outbox[0].alternatives[0][0]
        self.assertIn("MP240816.1423.WRONG", html)
        self.assertIn("No transfer for that code.", html)


class PasswordResetTests(MailTestCase):
    def test_the_reset_flow_emails_a_working_link(self):
        response = self.client.post(
            reverse("accounts:password_reset"), {"email": "student@uz.test"}
        )

        self.assertRedirects(response, reverse("accounts:password_reset_done"))
        self.assertEqual(len(mail.outbox), 1)

        message = mail.outbox[0]
        self.assertIn("password", message.subject.lower())
        self.assertIn("/accounts/password/reset/", message.body)

        # Follow the link the way a person would.
        link = [word for word in message.body.split() if "/password/reset/" in word][-1]
        path = link.replace("http://testserver", "").replace("https://testserver", "")
        self.assertEqual(self.client.get(path, follow=True).status_code, 200)

    def test_an_unknown_address_reveals_nothing(self):
        """Never confirm whether an address has an account."""
        response = self.client.post(
            reverse("accounts:password_reset"), {"email": "nobody@uz.test"}
        )

        self.assertRedirects(response, reverse("accounts:password_reset_done"))
        self.assertEqual(len(mail.outbox), 0)

    def test_the_reset_email_never_contains_a_password(self):
        self.client.post(reverse("accounts:password_reset"), {"email": "student@uz.test"})

        body = mail.outbox[0].body.lower()
        self.assertIn("never ask for your password", body)
        self.assertNotIn("testpass12345", body)

    def test_the_sign_in_page_offers_the_way_out(self):
        response = self.client.get(reverse("accounts:login"))

        self.assertContains(response, reverse("accounts:password_reset"))
        self.assertContains(response, "Forgot password?")
