import hashlib
from datetime import timedelta
from decimal import Decimal
from io import StringIO
from unittest.mock import patch

from django.core.cache import cache
from django.core.management import call_command
from django.db import connection
from django.test import TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from accounts.models import University, User
from events.models import Category, Event, Registration
from organizations.models import Membership, Organization
from varsity.testing import login_verified

from .forms import CheckoutForm
from .models import Payment, expire_stale_payments, total_collected
from .tasks import release_abandoned_holds
from .paynow import PaynowClient, PaynowResponse, generate_hash, verify_hash
from .pesepay import PesepayClient, PesepayCrypto, PesepayResponse, classify

INTEGRATION_ID = "12345"
INTEGRATION_KEY = "3f4c1b2a-0000-4a1b-9c3d-6e7f8a9b0c1d"
# Pesepay encryption keys are 32 characters — anything else fails AES-256 outright.
ENCRYPTION_KEY = "0123456789abcdef0123456789abcdef"


def make_user(username, **extra):
    return User.objects.create_user(
        username=username, email=f"{username}@varsity.test", password="testpass12345", **extra
    )


# Whoever runs the suite may have live keys in .env. Without this, the tests
# would talk to the real gateways — and a checkout test would try to charge a
# real wallet. Blank credentials force the local simulator; any test that wants
# live behaviour mocks the client explicitly.
NO_LIVE_GATEWAYS = override_settings(
    PESEPAY_INTEGRATION_KEY="",
    PESEPAY_ENCRYPTION_KEY="",
    PAYNOW_INTEGRATION_ID="",
    PAYNOW_INTEGRATION_KEY="",
    # Pin the wallet codes too: which wallets a real merchant account offers
    # varies, and the suite shouldn't pass or fail on whose keys are in .env.
    PESEPAY_METHOD_CODES={
        "ecocash": "PZW211",
        "onemoney": "PZW204",
        "innbucks": "PZW212",
    },
    # Ships blank so a real wallet number never lands in the repo; the direct
    # transfer option hides without one, so the suite supplies its own.
    ECOCASH_MERCHANT_NUMBER="0771234567",
)


@NO_LIVE_GATEWAYS
class PaymentTestCase(TestCase):
    """A paid event at a Zimbabwean university, with one student ready to buy."""

    def setUp(self):
        self.uz = University.objects.create(
            name="University of Zimbabwe", short_name="UZ", city="Harare"
        )
        self.organizer = make_user("organizer", role=User.Role.ORGANIZER, university=self.uz)
        self.student = make_user("student", university=self.uz)
        self.other = make_user("other", university=self.uz)

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
            category=Category.objects.create(name="Social"),
            starts_at=now + timedelta(days=7),
            ends_at=now + timedelta(days=7, hours=4),
            capacity=2,
            is_free=False,
            price=Decimal("15.00"),
            currency="USD",
            status=Event.Status.PUBLISHED,
        )

    def make_payment(self, user=None, **extra):
        user = user or self.student
        registration = self.event.register(user)
        return Payment.objects.create(
            registration=registration,
            user=user,
            amount=self.event.price,
            currency=self.event.currency,
            **extra,
        )


class HashTests(TestCase):
    def test_hash_matches_paynow_algorithm(self):
        values = {"id": "1201", "reference": "TEST-REF", "amount": "10.00"}
        expected = hashlib.sha512(
            ("1201" + "TEST-REF" + "10.00" + INTEGRATION_KEY).encode()
        ).hexdigest().upper()

        self.assertEqual(generate_hash(values, INTEGRATION_KEY), expected)

    def test_hash_field_is_excluded_from_its_own_input(self):
        without = {"id": "1201", "amount": "10.00"}
        with_hash = {"id": "1201", "amount": "10.00", "hash": "SOMETHINGELSE"}

        self.assertEqual(
            generate_hash(without, INTEGRATION_KEY), generate_hash(with_hash, INTEGRATION_KEY)
        )

    def test_valid_signature_is_accepted(self):
        values = {"status": "Paid", "amount": "15.00"}
        values["hash"] = generate_hash(values, INTEGRATION_KEY)

        self.assertTrue(verify_hash(values, INTEGRATION_KEY))

    def test_tampered_payload_is_rejected(self):
        values = {"status": "Paid", "amount": "15.00"}
        values["hash"] = generate_hash(values, INTEGRATION_KEY)
        values["amount"] = "1.00"  # someone changed the price in flight

        self.assertFalse(verify_hash(values, INTEGRATION_KEY))

    def test_missing_signature_is_rejected(self):
        self.assertFalse(verify_hash({"status": "Paid"}, INTEGRATION_KEY))


class PesepayCryptoTests(TestCase):
    """The AES envelope Pesepay wraps every request and response in."""

    # 32 characters -> AES-256, matching a real Pesepay encryption key.
    KEY = "0123456789abcdef0123456789abcdef"

    def test_ciphertext_matches_pesepay_own_sdk(self):
        """Byte-for-byte against CryptoJS, which is what Pesepay's SDKs use.

        Captured from crypto-js with the same key, IV and padding; if this ever
        drifts, the gateway would silently reject every payload.
        """
        crypto = PesepayCrypto(self.KEY)
        payload = {
            "amountDetails": {"amount": "15.00", "currencyCode": "USD"},
            "merchantReference": "VE-PAY-ABCD1234",
            "reasonForPayment": "Ticket",
            "resultUrl": "https://x/r",
            "returnUrl": "https://x/t",
        }

        expected = (
            "wfK2XoQnisIu25dNtJitRm1uKbUyDYlDOGKzmRJaXFw3upJhTzpFzICadd0o7tXhYyNmweMLJIZh"
            "6KQJmKjb14Y/dZDV+VfmwAmdGKFEAWsZA41IMoRjUWF8H5KytKL6YDKUgT2xC8Cg8Y3gRh4ws1fd"
            "w5fUu7aztcW99ID1FwS0Ei2/In6roGgA+tVLKQn+5BG3KIF2c+J7q/3WAEwv3G12511/j6wTf63i"
            "jnK3TpU="
        )
        self.assertEqual(crypto.encrypt(payload), expected)

    def test_round_trip(self):
        crypto = PesepayCrypto(self.KEY)
        data = {"transactionStatus": "SUCCESS", "referenceNumber": "PSP-1"}

        self.assertEqual(crypto.decrypt(crypto.encrypt(data)), data)

    def test_iv_is_the_first_sixteen_characters_of_the_key(self):
        crypto = PesepayCrypto(self.KEY)

        self.assertEqual(crypto.iv, self.KEY[:16].encode())
        self.assertEqual(len(crypto.iv), 16)

    def test_a_wrong_key_cannot_read_the_payload(self):
        blob = PesepayCrypto(self.KEY).encrypt({"a": 1})

        with self.assertRaises(Exception):
            PesepayCrypto("ffffffffffffffffffffffffffffffff").decrypt(blob)


class PesepayStatusTests(TestCase):
    def test_success_statuses_are_treated_as_paid(self):
        for status in ("SUCCESS", "PARTIALLY_PAID"):
            self.assertEqual(classify(status), "paid", status)

    def test_in_flight_statuses_are_pending(self):
        for status in ("INITIATED", "PENDING", "PROCESSING"):
            self.assertEqual(classify(status), "pending", status)

    def test_every_failure_status_is_recognised(self):
        for status in (
            "AUTHORIZATION_FAILED", "CANCELLED", "CLOSED", "CLOSED_PERIOD_ELAPSED",
            "DECLINED", "ERROR", "FAILED", "INSUFFICIENT_FUNDS", "REVERSED",
            "SERVICE_UNAVAILABLE", "TERMINATED",
        ):
            self.assertEqual(classify(status), "failed", status)

    def test_status_matching_ignores_case_and_padding(self):
        self.assertEqual(classify("  success  "), "paid")

    def test_an_unknown_status_changes_nothing(self):
        self.assertEqual(classify("SOMETHING_NEW"), "")


class OfferedMethodTests(PaymentTestCase):
    """A wallet the merchant account can't take must never reach a student."""

    def offered(self):
        return {code for code, _ in CheckoutForm().fields["method"].choices}

    @override_settings(
        PESEPAY_METHOD_CODES={"ecocash": "PZW211", "onemoney": "", "innbucks": "PZW212"}
    )
    def test_a_wallet_without_a_code_is_not_offered(self):
        offered = self.offered()

        self.assertNotIn(Payment.Method.ONEMONEY, offered)
        self.assertIn(Payment.Method.ECOCASH, offered)
        self.assertIn(Payment.Method.INNBUCKS, offered)

    @override_settings(
        PESEPAY_METHOD_CODES={"ecocash": "", "onemoney": "", "innbucks": ""}
    )
    def test_the_hosted_page_and_direct_transfer_survive_with_no_wallet_codes(self):
        """Neither needs a method code, so they're the floor we never drop below."""
        offered = self.offered()

        self.assertEqual(offered, {Payment.Method.WEB, Payment.Method.ECOCASH_DIRECT})

    @override_settings(
        PESEPAY_METHOD_CODES={"ecocash": "", "onemoney": "", "innbucks": ""},
        ECOCASH_DIRECT_ENABLED=False,
    )
    def test_the_default_falls_back_to_something_actually_offered(self):
        form = CheckoutForm()

        self.assertEqual(form.fields["method"].initial, Payment.Method.WEB)

    @override_settings(
        PESEPAY_METHOD_CODES={"ecocash": "PZW211", "onemoney": "", "innbucks": "PZW212"}
    )
    def test_choosing_a_withdrawn_wallet_is_rejected(self):
        """Hiding it in the template isn't enough — a posted value must fail too."""
        form = CheckoutForm({"method": Payment.Method.ONEMONEY, "phone": "0711234567"})

        self.assertFalse(form.is_valid())
        self.assertIn("method", form.errors)

    @override_settings(
        PESEPAY_METHOD_CODES={"ecocash": "PZW211", "onemoney": "", "innbucks": "PZW212"}
    )
    def test_checkout_page_does_not_show_the_withdrawn_wallet(self):
        self.event.register(self.student)
        self.client.force_login(self.student)

        response = self.client.get(reverse("payments:checkout", args=[self.event.slug]))

        # Match the radio itself — the word alone also appears in site copy.
        self.assertNotContains(response, 'value="onemoney"')
        self.assertContains(response, 'value="innbucks"')

    @override_settings(
        PESEPAY_METHOD_CODES={"ecocash": "PZW211", "onemoney": "", "innbucks": "PZW212"}
    )
    def test_site_copy_does_not_advertise_a_wallet_we_cannot_take(self):
        """Promising OneMoney and then refusing it is worse than staying quiet."""
        response = self.client.get(reverse("core:home"))

        self.assertNotContains(response, "OneMoney")
        self.assertContains(response, "InnBucks")


@NO_LIVE_GATEWAYS
class WalletPushTests(PaymentTestCase):
    """The 'check your phone' waiting room."""

    def make_push(self, **extra):
        defaults = {
            "method": Payment.Method.ECOCASH,
            "phone": "0771234567",
            "gateway": Payment.Gateway.PESEPAY,
            "status": Payment.Status.SENT,
            "is_simulated": True,
        }
        return self.make_payment(**{**defaults, **extra})

    def test_a_wallet_payment_is_a_push_but_a_transfer_is_not(self):
        push = self.make_push()
        direct = self.make_payment(
            user=self.other,
            method=Payment.Method.ECOCASH_DIRECT,
            gateway=Payment.Gateway.DIRECT,
        )

        self.assertTrue(push.is_wallet_push)
        self.assertFalse(direct.is_wallet_push)

    def test_the_number_is_masked(self):
        self.assertEqual(self.make_push().phone_masked, "077 *** 4567")

    def test_masking_leaves_an_unusable_number_alone(self):
        self.assertEqual(self.make_push(phone="0771").phone_masked, "0771")

    def test_seconds_left_counts_down_and_floors_at_zero(self):
        payment = self.make_push()
        self.assertGreater(payment.seconds_left, 0)

        payment.expires_at = timezone.now() - timedelta(minutes=1)
        self.assertEqual(payment.seconds_left, 0)

    def test_the_waiting_room_shows_the_phone_and_never_asks_for_a_pin(self):
        payment = self.make_push()
        self.client.force_login(self.student)

        response = self.client.get(reverse("payments:status", args=[payment.reference]))

        self.assertContains(response, "Check your phone")
        self.assertContains(response, "077 *** 4567")
        # The PIN belongs on the handset. A field for it here would be a
        # credential-harvesting pattern, and the gateway never needs it.
        self.assertNotContains(response, 'type="password"')
        self.assertNotContains(response, 'name="pin"')

    def test_a_settled_payment_does_not_show_the_waiting_room(self):
        payment = self.make_push(status=Payment.Status.PAID)
        self.client.force_login(self.student)

        response = self.client.get(reverse("payments:status", args=[payment.reference]))

        self.assertNotContains(response, "Check your phone")
        self.assertContains(response, "Payment received")

    def test_resend_pushes_again_without_a_second_payment(self):
        payment = self.make_push(is_simulated=False)
        self.client.force_login(self.student)

        with patch("payments.views.PesepayClient.make_seamless_payment") as push, \
             patch("payments.views._sync"):
            push.return_value = PesepayResponse(
                ok=True, status="PENDING", reference="PSP-RESENT"
            )
            response = self.client.post(reverse("payments:resend", args=[payment.reference]))

        payment.refresh_from_db()
        self.assertRedirects(response, reverse("payments:status", args=[payment.reference]))
        self.assertEqual(Payment.objects.count(), 1)
        self.assertEqual(payment.paynow_reference, "PSP-RESENT")

    def test_resend_does_nothing_once_the_payment_is_paid(self):
        """Re-pushing a settled transaction would charge the student twice."""
        payment = self.make_push(status=Payment.Status.PAID)
        self.client.force_login(self.student)

        with patch("payments.views.PesepayClient.make_seamless_payment") as push:
            self.client.post(reverse("payments:resend", args=[payment.reference]))

        push.assert_not_called()

    def test_resend_is_refused_to_anyone_but_the_payer(self):
        payment = self.make_push()
        self.client.force_login(self.other)

        response = self.client.post(reverse("payments:resend", args=[payment.reference]))

        self.assertEqual(response.status_code, 404)

    def test_resend_rejects_a_get(self):
        payment = self.make_push()
        self.client.force_login(self.student)

        response = self.client.get(reverse("payments:resend", args=[payment.reference]))

        self.assertEqual(response.status_code, 405)


class CheckoutPopupTests(PaymentTestCase):
    """The checkout posts over fetch so the prompt can open in place."""

    AJAX = {"HTTP_X_REQUESTED_WITH": "XMLHttpRequest"}

    def setUp(self):
        super().setUp()
        self.event.register(self.student)
        self.client.force_login(self.student)
        self.url = reverse("payments:checkout", args=[self.event.slug])

    def test_a_wallet_push_answers_with_everything_the_popup_needs(self):
        response = self.client.post(
            self.url, {"method": "ecocash", "phone": "0771234567"}, **self.AJAX
        )

        body = response.json()
        payment = Payment.objects.get()
        self.assertTrue(body["ok"])
        self.assertEqual(body["payment"]["reference"], payment.reference)
        self.assertEqual(body["payment"]["phone_masked"], "077 *** 4567")
        self.assertEqual(body["payment"]["amount"], payment.amount_display)
        self.assertGreater(body["payment"]["seconds_left"], 0)
        self.assertIn(payment.reference, body["payment"]["state_url"])
        self.assertIn(payment.reference, body["payment"]["resend_url"])

    def test_the_popup_is_never_handed_a_pin_field_or_a_secret(self):
        response = self.client.post(
            self.url, {"method": "ecocash", "phone": "0771234567"}, **self.AJAX
        )

        raw = response.content.decode().lower()
        for forbidden in ("pin", "password", "encryption", "integration"):
            self.assertNotIn(forbidden, raw)

    def test_a_form_error_comes_back_as_json_not_a_page(self):
        response = self.client.post(
            self.url, {"method": "ecocash", "phone": "12345"}, **self.AJAX
        )

        body = response.json()
        self.assertFalse(body["ok"])
        self.assertIn("valid Zimbabwean mobile number", body["error"])
        self.assertIn("phone", body["fields"])
        self.assertFalse(Payment.objects.exists())

    def test_a_gateway_refusal_comes_back_as_json(self):
        with patch("payments.views.PesepayClient.make_seamless_payment") as push:
            push.return_value = PesepayResponse(ok=False, error="Wallet unreachable.")
            response = self.client.post(
                self.url, {"method": "ecocash", "phone": "0771234567"}, **self.AJAX
            )

        self.assertFalse(response.json()["ok"])
        self.assertEqual(response.json()["error"], "Wallet unreachable.")
        self.assertEqual(Payment.objects.get().status, Payment.Status.FAILED)

    def test_the_hosted_page_gets_a_redirect_not_a_popup(self):
        with patch("payments.views.PesepayClient.initiate") as initiate:
            initiate.return_value = PesepayResponse(
                ok=True, status="PENDING", reference="PSP-1",
                redirect_url="https://pesepay.test/checkout/abc",
            )
            response = self.client.post(self.url, {"method": "web"}, **self.AJAX)

        self.assertEqual(response.json()["redirect"], "https://pesepay.test/checkout/abc")

    def test_a_direct_transfer_gets_a_redirect_not_a_popup(self):
        response = self.client.post(self.url, {"method": "ecocash_direct"}, **self.AJAX)

        payment = Payment.objects.get()
        self.assertEqual(
            response.json()["redirect"],
            reverse("payments:transfer", args=[payment.reference]),
        )

    def test_a_plain_post_still_redirects_for_anyone_without_javascript(self):
        response = self.client.post(self.url, {"method": "ecocash", "phone": "0771234567"})

        payment = Payment.objects.get()
        self.assertRedirects(response, reverse("payments:status", args=[payment.reference]))


class ClientModeTests(TestCase):
    def test_client_simulates_without_credentials(self):
        self.assertTrue(PaynowClient(integration_id="", integration_key="").is_simulated)

    @override_settings(
        PAYNOW_INTEGRATION_ID=INTEGRATION_ID, PAYNOW_INTEGRATION_KEY=INTEGRATION_KEY
    )
    def test_client_goes_live_once_credentials_are_set(self):
        self.assertFalse(PaynowClient().is_simulated)

    def test_pesepay_simulates_without_credentials(self):
        self.assertTrue(PesepayClient(integration_key="", encryption_key="").is_simulated)

    @override_settings(
        PESEPAY_INTEGRATION_KEY=INTEGRATION_ID, PESEPAY_ENCRYPTION_KEY=ENCRYPTION_KEY
    )
    def test_pesepay_goes_live_once_both_keys_are_set(self):
        self.assertFalse(PesepayClient().is_simulated)

    @override_settings(PESEPAY_INTEGRATION_KEY=INTEGRATION_ID, PESEPAY_ENCRYPTION_KEY="")
    def test_pesepay_needs_both_keys_not_just_one(self):
        """Half a credential pair can only produce payloads Pesepay will reject."""
        self.assertTrue(PesepayClient().is_simulated)

    @override_settings(
        PESEPAY_INTEGRATION_KEY=INTEGRATION_ID,
        PESEPAY_ENCRYPTION_KEY="your-32-character-encryption-key",  # 32 chars, but placeholder
    )
    def test_a_key_of_the_right_length_is_accepted_whatever_it_says(self):
        """We can't tell a real key from a fake one — only Pesepay can."""
        self.assertFalse(PesepayClient().is_simulated)

    @override_settings(
        PESEPAY_INTEGRATION_KEY=INTEGRATION_ID, PESEPAY_ENCRYPTION_KEY="too-short"
    )
    def test_a_wrong_length_key_falls_back_instead_of_crashing_checkout(self):
        """AES would raise mid-payment; better to catch it before a student waits."""
        with self.assertLogs("payments.pesepay", level="ERROR") as logs:
            client = PesepayClient()

        self.assertTrue(client.is_simulated)
        self.assertIn("32", logs.output[0])


class PaymentModelTests(PaymentTestCase):
    def test_paid_event_holds_the_seat_without_confirming_it(self):
        registration = self.event.register(self.student)

        self.assertEqual(registration.status, Registration.Status.AWAITING_PAYMENT)
        self.assertTrue(registration.needs_payment)
        self.assertEqual(self.event.attendee_count, 0)
        self.assertEqual(self.event.reserved_count, 1)
        self.assertEqual(self.event.seats_left, 1)

    def test_free_event_still_confirms_immediately(self):
        self.event.is_free = True
        self.event.save()

        registration = self.event.register(self.student)
        self.assertEqual(registration.status, Registration.Status.CONFIRMED)

    def test_held_seats_count_towards_capacity(self):
        self.event.register(self.student)
        self.event.register(self.other)

        self.assertTrue(self.event.is_full)
        self.assertEqual(self.event.availability["state"], "waitlist")

    def test_paid_status_confirms_the_ticket(self):
        payment = self.make_payment()
        payment.apply_paynow_status("Paid", "PN-123")
        payment.settle()

        payment.registration.refresh_from_db()
        self.assertEqual(payment.status, Payment.Status.PAID)
        self.assertEqual(payment.paynow_reference, "PN-123")
        self.assertIsNotNone(payment.paid_at)
        self.assertEqual(payment.registration.status, Registration.Status.CONFIRMED)
        self.assertEqual(self.event.attendee_count, 1)

    def test_awaiting_delivery_also_counts_as_settled(self):
        payment = self.make_payment()
        payment.apply_paynow_status("Awaiting Delivery")
        payment.settle()

        payment.registration.refresh_from_db()
        self.assertTrue(payment.is_settled)
        self.assertEqual(payment.registration.status, Registration.Status.CONFIRMED)

    def test_cancelled_status_leaves_the_ticket_unconfirmed(self):
        payment = self.make_payment()
        payment.apply_paynow_status("Cancelled")
        payment.settle()

        payment.registration.refresh_from_db()
        self.assertEqual(payment.status, Payment.Status.CANCELLED)
        self.assertEqual(payment.registration.status, Registration.Status.AWAITING_PAYMENT)
        self.assertEqual(self.event.attendee_count, 0)

    def test_unknown_status_is_ignored(self):
        payment = self.make_payment()
        self.assertFalse(payment.apply_paynow_status("Nonsense"))
        self.assertEqual(payment.status, Payment.Status.CREATED)

    def test_expired_checkout_releases_the_seat(self):
        payment = self.make_payment(status=Payment.Status.SENT)
        payment.expires_at = timezone.now() - timedelta(minutes=1)
        payment.save()

        self.assertTrue(payment.has_expired)
        self.assertEqual(expire_stale_payments(), 1)

        payment.refresh_from_db()
        payment.registration.refresh_from_db()
        self.assertEqual(payment.status, Payment.Status.EXPIRED)
        self.assertEqual(payment.registration.status, Registration.Status.CANCELLED)
        self.assertEqual(self.event.reserved_count, 0)

    def test_settled_payment_is_never_expired(self):
        payment = self.make_payment()
        payment.apply_paynow_status("Paid")
        payment.settle()
        payment.expires_at = timezone.now() - timedelta(minutes=1)
        payment.save()

        self.assertEqual(expire_stale_payments(), 0)
        payment.refresh_from_db()
        self.assertEqual(payment.status, Payment.Status.PAID)

    def test_cancelling_a_registration_kills_its_open_checkout(self):
        payment = self.make_payment(status=Payment.Status.SENT)
        payment.registration.cancel()

        payment.refresh_from_db()
        self.assertEqual(payment.status, Payment.Status.CANCELLED)

    def test_waitlist_promotion_on_a_paid_event_asks_for_payment(self):
        first = self.event.register(self.student)
        self.event.register(self.other)
        third = self.event.register(make_user("third", university=self.uz))
        self.assertEqual(third.status, Registration.Status.WAITLISTED)

        first.cancel()
        third.refresh_from_db()

        self.assertEqual(third.status, Registration.Status.AWAITING_PAYMENT)

    def test_revenue_only_counts_settled_payments(self):
        paid = self.make_payment()
        paid.apply_paynow_status("Paid")

        pending = self.make_payment(user=self.other)
        pending.apply_paynow_status("Sent")

        self.assertEqual(total_collected([self.event]), Decimal("15.00"))

    def test_reference_is_unique_and_prefixed(self):
        a = self.make_payment()
        b = self.make_payment(user=self.other)

        self.assertTrue(a.reference.startswith("VE-PAY-"))
        self.assertNotEqual(a.reference, b.reference)


class CheckoutViewTests(PaymentTestCase):
    def test_registering_for_a_paid_event_redirects_to_checkout(self):
        self.client.force_login(self.student)
        response = self.client.post(reverse("events:register", args=[self.event.slug]))

        self.assertRedirects(response, reverse("payments:checkout", args=[self.event.slug]))

    def test_checkout_requires_an_existing_registration(self):
        self.client.force_login(self.student)
        response = self.client.get(reverse("payments:checkout", args=[self.event.slug]))

        self.assertRedirects(response, self.event.get_absolute_url())

    def test_checkout_page_renders_for_a_held_seat(self):
        self.event.register(self.student)
        self.client.force_login(self.student)

        response = self.client.get(reverse("payments:checkout", args=[self.event.slug]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Complete your payment")
        self.assertContains(response, "EcoCash")

    def test_free_events_have_no_checkout(self):
        self.event.is_free = True
        self.event.save()
        self.client.force_login(self.student)

        response = self.client.get(reverse("payments:checkout", args=[self.event.slug]))
        self.assertRedirects(response, self.event.get_absolute_url())

    def test_checkout_rejects_a_non_zimbabwean_number(self):
        self.event.register(self.student)
        self.client.force_login(self.student)

        response = self.client.post(
            reverse("payments:checkout", args=[self.event.slug]),
            {"method": "ecocash", "phone": "12345"},
        )
        self.assertContains(response, "valid Zimbabwean mobile number")
        self.assertEqual(Payment.objects.count(), 0)

    def test_checkout_rejects_a_wallet_prefix_mismatch(self):
        self.event.register(self.student)
        self.client.force_login(self.student)

        response = self.client.post(
            reverse("payments:checkout", args=[self.event.slug]),
            {"method": "onemoney", "phone": "0771234567"},  # 077 is EcoCash
        )
        self.assertContains(response, "doesn&#x27;t look like a OneMoney line")

    def test_express_checkout_creates_a_payment_and_waits(self):
        self.event.register(self.student)
        self.client.force_login(self.student)

        response = self.client.post(
            reverse("payments:checkout", args=[self.event.slug]),
            {"method": "ecocash", "phone": "0771234567"},
        )

        payment = Payment.objects.get()
        self.assertRedirects(response, reverse("payments:status", args=[payment.reference]))
        self.assertEqual(payment.status, Payment.Status.SENT)
        self.assertEqual(payment.method, Payment.Method.ECOCASH)
        self.assertEqual(payment.phone, "0771234567")
        self.assertTrue(payment.is_simulated)
        # Online methods now go through Pesepay.
        self.assertEqual(payment.gateway, Payment.Gateway.PESEPAY)

    def test_direct_transfers_are_not_routed_to_a_gateway(self):
        self.event.register(self.student)
        self.client.force_login(self.student)

        self.client.post(
            reverse("payments:checkout", args=[self.event.slug]),
            {"method": "ecocash_direct"},
        )

        payment = Payment.objects.get()
        self.assertEqual(payment.gateway, Payment.Gateway.DIRECT)
        self.assertEqual(payment.status, Payment.Status.AWAITING_TRANSFER)

    def test_pesepay_status_confirms_the_ticket(self):
        registration = self.event.register(self.student)
        payment = Payment.objects.create(
            registration=registration,
            user=self.student,
            amount=self.event.price,
            gateway=Payment.Gateway.PESEPAY,
            status=Payment.Status.SENT,
        )

        self.assertTrue(payment.apply_pesepay_status("SUCCESS", "PSP-9911"))
        payment.settle()
        registration.refresh_from_db()

        self.assertEqual(payment.status, Payment.Status.PAID)
        self.assertEqual(payment.paynow_reference, "PSP-9911")
        self.assertIsNotNone(payment.paid_at)
        self.assertEqual(registration.status, Registration.Status.CONFIRMED)

    def test_a_failed_pesepay_status_leaves_the_ticket_unconfirmed(self):
        registration = self.event.register(self.student)
        payment = Payment.objects.create(
            registration=registration,
            user=self.student,
            amount=self.event.price,
            gateway=Payment.Gateway.PESEPAY,
            status=Payment.Status.SENT,
        )

        payment.apply_pesepay_status("INSUFFICIENT_FUNDS")
        registration.refresh_from_db()

        self.assertEqual(payment.status, Payment.Status.FAILED)
        self.assertEqual(registration.status, Registration.Status.AWAITING_PAYMENT)

    def test_an_unknown_pesepay_status_is_ignored(self):
        registration = self.event.register(self.student)
        payment = Payment.objects.create(
            registration=registration,
            user=self.student,
            amount=self.event.price,
            gateway=Payment.Gateway.PESEPAY,
            status=Payment.Status.SENT,
        )

        self.assertFalse(payment.apply_pesepay_status("BRAND_NEW_STATUS"))
        self.assertEqual(payment.status, Payment.Status.SENT)

    def test_status_page_is_private_to_the_payer(self):
        payment = self.make_payment()
        url = reverse("payments:status", args=[payment.reference])

        self.client.force_login(self.student)
        self.assertEqual(self.client.get(url).status_code, 200)

        self.client.force_login(self.other)
        self.assertEqual(self.client.get(url).status_code, 404)

    def test_organizer_can_see_a_payment_against_their_event(self):
        payment = self.make_payment()
        self.client.force_login(self.organizer)

        response = self.client.get(reverse("payments:status", args=[payment.reference]))
        self.assertEqual(response.status_code, 200)

    def test_status_json_reports_settlement(self):
        payment = self.make_payment()
        self.client.force_login(self.student)

        payload = self.client.get(
            reverse("payments:status_json", args=[payment.reference])
        ).json()
        self.assertFalse(payload["settled"])

        payment.apply_paynow_status("Paid")
        payload = self.client.get(
            reverse("payments:status_json", args=[payment.reference])
        ).json()

        self.assertTrue(payload["settled"])
        self.assertIn(payment.registration.ticket_code, payload["ticket_url"])


@override_settings(ECOCASH_MERCHANT_NUMBER="0771234567", ECOCASH_DIRECT_ENABLED=True)
class EcoCashDirectTests(PaymentTestCase):
    """Money sent straight to the merchant wallet, confirmed by a human."""

    def start_transfer(self, user=None):
        user = user or self.student
        self.event.register(user)
        self.client.force_login(user)
        self.client.post(
            reverse("payments:checkout", args=[self.event.slug]), {"method": "ecocash_direct"}
        )
        return Payment.objects.get(user=user, method=Payment.Method.ECOCASH_DIRECT)

    def test_checkout_shows_the_merchant_wallet(self):
        self.event.register(self.student)
        self.client.force_login(self.student)

        response = self.client.get(reverse("payments:checkout", args=[self.event.slug]))

        self.assertContains(response, "0771234567")
        self.assertContains(response, "send straight to us")

    def test_choosing_direct_waits_for_the_transfer(self):
        payment = self.start_transfer()

        self.assertEqual(payment.status, Payment.Status.AWAITING_TRANSFER)
        self.assertTrue(payment.is_manual)
        self.assertTrue(payment.is_open)
        self.assertIn("0771234567", payment.instructions)

    def test_direct_transfers_get_a_longer_seat_hold(self):
        payment = self.start_transfer()
        held_for = (payment.expires_at - payment.created_at).total_seconds() / 60

        # 120 minutes by default, versus 30 for a gateway push.
        self.assertGreater(held_for, 60)

    def test_the_seat_is_held_while_the_transfer_is_pending(self):
        self.start_transfer()

        self.assertEqual(self.event.reserved_count, 1)
        self.assertEqual(self.event.attendee_count, 0)

    def test_transfer_page_shows_the_amount_and_dial_code(self):
        payment = self.start_transfer()

        response = self.client.get(reverse("payments:transfer", args=[payment.reference]))

        self.assertContains(response, "0771234567")
        self.assertContains(response, "*151#")
        self.assertContains(response, payment.reference)

    def test_submitting_a_code_queues_it_for_checking(self):
        payment = self.start_transfer()

        self.client.post(
            reverse("payments:transfer", args=[payment.reference]),
            {"confirmation_code": "mp260816.1423.a12345", "paid_from": "0779876543"},
        )
        payment.refresh_from_db()

        self.assertEqual(payment.status, Payment.Status.AWAITING_VERIFICATION)
        self.assertEqual(payment.confirmation_code, "MP260816.1423.A12345")  # upper-cased
        self.assertEqual(payment.paid_from, "0779876543")
        self.assertTrue(payment.needs_verification)

    def test_a_short_code_is_rejected(self):
        payment = self.start_transfer()

        response = self.client.post(
            reverse("payments:transfer", args=[payment.reference]), {"confirmation_code": "abc"}
        )
        payment.refresh_from_db()

        self.assertContains(response, "too short")
        self.assertEqual(payment.status, Payment.Status.AWAITING_TRANSFER)

    def test_a_bad_paid_from_number_is_rejected(self):
        payment = self.start_transfer()

        response = self.client.post(
            reverse("payments:transfer", args=[payment.reference]),
            {"confirmation_code": "MP260816.1423.A12345", "paid_from": "12345"},
        )
        self.assertContains(response, "valid Zimbabwean number")

    def test_verifying_confirms_the_ticket(self):
        payment = self.start_transfer()
        payment.submit_confirmation("MP260816.1423.A12345")

        self.assertTrue(payment.verify(by_user=self.organizer))
        payment.refresh_from_db()
        payment.registration.refresh_from_db()

        self.assertEqual(payment.status, Payment.Status.PAID)
        self.assertEqual(payment.verified_by, self.organizer)
        self.assertIsNotNone(payment.paid_at)
        self.assertEqual(payment.registration.status, Registration.Status.CONFIRMED)
        self.assertEqual(self.event.attendee_count, 1)

    def test_rejecting_sends_the_payer_back_to_try_again(self):
        payment = self.start_transfer()
        payment.submit_confirmation("MP260816.1423.A12345")

        self.assertTrue(payment.reject(by_user=self.organizer, reason="No match on the statement."))
        payment.refresh_from_db()
        payment.registration.refresh_from_db()

        self.assertEqual(payment.status, Payment.Status.AWAITING_TRANSFER)
        self.assertEqual(payment.confirmation_code, "")
        self.assertIn("No match", payment.rejection_reason)
        # The seat is still theirs while they sort it out.
        self.assertEqual(payment.registration.status, Registration.Status.AWAITING_PAYMENT)

    def test_organizer_sees_their_own_pending_transfers(self):
        payment = self.start_transfer()
        payment.submit_confirmation("MP260816.1423.A12345")

        login_verified(self.client, self.organizer)
        response = self.client.get(reverse("payments:verify"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "MP260816.1423.A12345")

    def test_a_plain_student_cannot_open_the_queue(self):
        self.client.force_login(self.student)
        self.assertEqual(self.client.get(reverse("payments:verify")).status_code, 404)

    def test_an_organizer_cannot_verify_someone_else_event(self):
        payment = self.start_transfer()
        payment.submit_confirmation("MP260816.1423.A12345")

        outsider = make_user("outsider", role=User.Role.ORGANIZER, university=self.uz)
        other_org = Organization.objects.create(name="Other Society", created_by=outsider)
        Membership.objects.create(
            organization=other_org, user=outsider, role=Membership.Role.OWNER
        )

        self.client.force_login(outsider)
        self.client.post(
            reverse("payments:verify"), {"payment_id": payment.pk, "decision": "verify"}
        )
        payment.refresh_from_db()

        self.assertEqual(payment.status, Payment.Status.AWAITING_VERIFICATION)

    def test_verifying_through_the_view_releases_the_ticket(self):
        payment = self.start_transfer()
        payment.submit_confirmation("MP260816.1423.A12345")

        login_verified(self.client, self.organizer)
        self.client.post(
            reverse("payments:verify"), {"payment_id": payment.pk, "decision": "verify"}
        )
        payment.refresh_from_db()
        payment.registration.refresh_from_db()

        self.assertEqual(payment.status, Payment.Status.PAID)
        self.assertEqual(payment.registration.status, Registration.Status.CONFIRMED)

    def test_an_abandoned_transfer_expires_and_frees_the_seat(self):
        payment = self.start_transfer()
        payment.expires_at = timezone.now() - timedelta(minutes=1)
        payment.save()

        self.assertEqual(expire_stale_payments(), 1)
        payment.refresh_from_db()

        self.assertEqual(payment.status, Payment.Status.EXPIRED)
        self.assertEqual(self.event.reserved_count, 0)

    @override_settings(ECOCASH_DIRECT_ENABLED=False)
    def test_the_method_can_be_switched_off(self):
        self.event.register(self.student)
        self.client.force_login(self.student)

        response = self.client.get(reverse("payments:checkout", args=[self.event.slug]))
        self.assertNotContains(response, "send straight to us")


class SimulatorTests(PaymentTestCase):
    def test_approving_in_the_simulator_confirms_the_ticket(self):
        payment = self.make_payment(status=Payment.Status.SENT, is_simulated=True)
        self.client.force_login(self.student)

        # Simulator -> return handler -> ticket, so follow the whole chain.
        response = self.client.post(
            reverse("payments:simulator", args=[payment.reference]),
            {"outcome": "paid"},
            follow=True,
        )

        payment.refresh_from_db()
        payment.registration.refresh_from_db()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.redirect_chain[-1][0], payment.registration.get_absolute_url()
        )
        self.assertEqual(payment.status, Payment.Status.PAID)
        self.assertEqual(payment.registration.status, Registration.Status.CONFIRMED)

    def test_declining_in_the_simulator_leaves_it_unpaid(self):
        payment = self.make_payment(status=Payment.Status.SENT, is_simulated=True)
        self.client.force_login(self.student)

        self.client.post(
            reverse("payments:simulator", args=[payment.reference]), {"outcome": "cancelled"}
        )

        payment.refresh_from_db()
        payment.registration.refresh_from_db()
        self.assertEqual(payment.status, Payment.Status.CANCELLED)
        self.assertEqual(payment.registration.status, Registration.Status.AWAITING_PAYMENT)

    @override_settings(
        PESEPAY_INTEGRATION_KEY=INTEGRATION_ID, PESEPAY_ENCRYPTION_KEY=ENCRYPTION_KEY
    )
    def test_simulator_is_off_when_credentials_are_live(self):
        payment = self.make_payment(status=Payment.Status.SENT)
        self.client.force_login(self.student)

        response = self.client.get(reverse("payments:simulator", args=[payment.reference]))
        self.assertEqual(response.status_code, 404)


class PesepayCallbackTests(PaymentTestCase):
    """Pesepay's result URL. We treat the POST as a nudge, never as evidence."""

    def make_pesepay_payment(self, **extra):
        return self.make_payment(
            status=Payment.Status.SENT,
            gateway=Payment.Gateway.PESEPAY,
            paynow_reference="PSP-4471",
            **extra,
        )

    @patch("payments.views.PesepayClient.check_payment")
    def test_callback_reverifies_and_confirms(self, mock_check):
        mock_check.return_value = PesepayResponse(
            ok=True, status="SUCCESS", reference="PSP-4471"
        )
        payment = self.make_pesepay_payment()

        response = self.client.post(
            reverse("payments:result", args=[payment.reference]), {"status": "SUCCESS"}
        )

        payment.refresh_from_db()
        payment.registration.refresh_from_db()
        self.assertEqual(response.status_code, 200)
        mock_check.assert_called_once_with("PSP-4471")
        self.assertEqual(payment.status, Payment.Status.PAID)
        self.assertEqual(payment.registration.status, Registration.Status.CONFIRMED)

    @patch("payments.views.PesepayClient.check_payment")
    def test_a_forged_success_body_cannot_confirm_a_ticket(self, mock_check):
        """The whole point of re-verifying: anyone can POST to this URL."""
        mock_check.return_value = PesepayResponse(
            ok=True, status="INSUFFICIENT_FUNDS", reference="PSP-4471"
        )
        payment = self.make_pesepay_payment()

        self.client.post(
            reverse("payments:result", args=[payment.reference]),
            {"status": "SUCCESS", "transactionStatus": "SUCCESS", "amount": "0.01"},
        )

        payment.refresh_from_db()
        payment.registration.refresh_from_db()
        self.assertEqual(payment.status, Payment.Status.FAILED)
        self.assertEqual(payment.registration.status, Registration.Status.AWAITING_PAYMENT)

    @patch("payments.views.PesepayClient.check_payment")
    def test_callback_is_idempotent(self, mock_check):
        mock_check.return_value = PesepayResponse(
            ok=True, status="SUCCESS", reference="PSP-4471"
        )
        payment = self.make_pesepay_payment()
        url = reverse("payments:result", args=[payment.reference])

        self.client.post(url, {"status": "SUCCESS"})
        self.client.post(url, {"status": "SUCCESS"})

        payment.registration.refresh_from_db()
        self.assertEqual(payment.registration.status, Registration.Status.CONFIRMED)
        self.assertEqual(self.event.attendee_count, 1)

    def test_callback_for_an_unknown_reference_404s(self):
        response = self.client.post(
            reverse("payments:result", args=["VE-PAY-NOPE0000"]), {"status": "SUCCESS"}
        )
        self.assertEqual(response.status_code, 404)


@override_settings(PAYNOW_INTEGRATION_ID=INTEGRATION_ID, PAYNOW_INTEGRATION_KEY=INTEGRATION_KEY)
class CallbackTests(PaymentTestCase):
    """The legacy Paynow callback, still honoured for payments taken before the switch."""

    def make_payment(self, **extra):
        extra.setdefault("gateway", Payment.Gateway.PAYNOW)
        return super().make_payment(**extra)

    def signed(self, payment, status="Paid"):
        data = {
            "reference": payment.reference,
            "paynowreference": "PN-99887",
            "amount": "15.00",
            "status": status,
            "pollurl": "https://www.paynow.co.zw/interface/pollstatus/x",
        }
        data["hash"] = generate_hash(data, INTEGRATION_KEY)
        return data

    def test_signed_callback_confirms_the_ticket(self):
        payment = self.make_payment(status=Payment.Status.SENT)

        response = self.client.post(
            reverse("payments:result", args=[payment.reference]), self.signed(payment)
        )

        payment.refresh_from_db()
        payment.registration.refresh_from_db()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(payment.status, Payment.Status.PAID)
        self.assertEqual(payment.paynow_reference, "PN-99887")
        self.assertEqual(payment.registration.status, Registration.Status.CONFIRMED)

    def test_unsigned_callback_is_refused(self):
        payment = self.make_payment(status=Payment.Status.SENT)

        response = self.client.post(
            reverse("payments:result", args=[payment.reference]),
            {"reference": payment.reference, "status": "Paid"},
        )

        payment.refresh_from_db()
        self.assertEqual(response.status_code, 400)
        self.assertEqual(payment.status, Payment.Status.SENT)

    def test_forged_callback_is_refused(self):
        payment = self.make_payment(status=Payment.Status.SENT)
        data = self.signed(payment)
        data["amount"] = "0.01"  # tampered after signing

        response = self.client.post(reverse("payments:result", args=[payment.reference]), data)

        payment.refresh_from_db()
        self.assertEqual(response.status_code, 400)
        self.assertEqual(payment.status, Payment.Status.SENT)

    def test_callback_for_an_unknown_reference_404s(self):
        response = self.client.post(
            reverse("payments:result", args=["VE-PAY-NOPE0000"]), {"status": "Paid"}
        )
        self.assertEqual(response.status_code, 404)

    def test_callback_is_idempotent(self):
        payment = self.make_payment(status=Payment.Status.SENT)
        url = reverse("payments:result", args=[payment.reference])

        self.client.post(url, self.signed(payment))
        self.client.post(url, self.signed(payment))

        payment.registration.refresh_from_db()
        self.assertEqual(payment.registration.status, Registration.Status.CONFIRMED)
        self.assertEqual(self.event.attendee_count, 1)


@override_settings(PAYNOW_INTEGRATION_ID=INTEGRATION_ID, PAYNOW_INTEGRATION_KEY=INTEGRATION_KEY)
class LivePollTests(PaymentTestCase):
    @patch("payments.paynow.requests.get")
    def test_poll_reads_a_signed_response(self, mock_get):
        data = {
            "reference": "VE-PAY-ABC",
            "paynowreference": "PN-1",
            "amount": "15.00",
            "status": "Paid",
            "pollurl": "https://www.paynow.co.zw/x",
        }
        data["hash"] = generate_hash(data, INTEGRATION_KEY)
        body = "&".join(f"{k}={v}" for k, v in data.items())

        mock_get.return_value.text = body
        mock_get.return_value.raise_for_status.return_value = None

        result = PaynowClient().poll("https://www.paynow.co.zw/x")

        self.assertTrue(result.ok)
        self.assertEqual(result.status, "Paid")
        self.assertEqual(result.amount, Decimal("15.00"))

    @patch("payments.paynow.requests.get")
    def test_poll_rejects_an_unsigned_response(self, mock_get):
        mock_get.return_value.text = "status=Paid&amount=15.00"
        mock_get.return_value.raise_for_status.return_value = None

        result = PaynowClient().poll("https://www.paynow.co.zw/x")

        self.assertFalse(result.ok)
        self.assertIn("signature", result.error)

    @patch("payments.paynow.requests.post")
    def test_initiate_surfaces_a_paynow_error(self, mock_post):
        mock_post.return_value.text = "status=Error&error=Invalid+integration+id"
        mock_post.return_value.raise_for_status.return_value = None

        result = PaynowClient().initiate(
            reference="VE-PAY-X",
            amount=Decimal("15.00"),
            additional_info="Test",
            auth_email="a@b.test",
            return_url="https://example.test/r",
            result_url="https://example.test/x",
        )

        self.assertFalse(result.ok)
        self.assertIn("Invalid integration id", result.error)

    @patch("payments.paynow.requests.post")
    def test_initiate_returns_the_browser_url(self, mock_post):
        mock_post.return_value.text = (
            "status=Ok&browserurl=https%3A%2F%2Fpaynow.co.zw%2Fpay%2F1&pollurl=https%3A%2F%2Fpaynow.co.zw%2Fpoll%2F1"
        )
        mock_post.return_value.raise_for_status.return_value = None

        result = PaynowClient().initiate(
            reference="VE-PAY-X",
            amount=Decimal("15.00"),
            additional_info="Test",
            auth_email="a@b.test",
            return_url="https://example.test/r",
            result_url="https://example.test/x",
        )

        self.assertTrue(result.ok)
        self.assertEqual(result.browser_url, "https://paynow.co.zw/pay/1")
        self.assertEqual(result.poll_url, "https://paynow.co.zw/poll/1")


class ReservedCountIsAReadTests(PaymentTestCase):
    """Counting seats must not write to the database.

    `Event.reserved_count` used to release timed-out holds before counting,
    which meant that rendering an event card — on a listing, nine at a time —
    issued UPDATEs on a GET request. It now discounts them in the query and
    leaves the retiring to payments.tasks.release_abandoned_holds.
    """

    def timed_out_payment(self):
        payment = self.make_payment(status=Payment.Status.SENT)
        payment.expires_at = timezone.now() - timedelta(minutes=1)
        payment.save()
        return payment

    def test_counting_seats_issues_no_writes(self):
        self.timed_out_payment()

        with CaptureQueriesContext(connection) as captured:
            self.event.reserved_count

        written = [
            q["sql"]
            for q in captured.captured_queries
            if q["sql"].strip().upper().startswith(("UPDATE", "INSERT", "DELETE"))
        ]
        self.assertEqual(written, [], f"reading capacity wrote to the database: {written}")

    def test_a_timed_out_hold_stops_counting_immediately(self):
        """Before anything has swept for it — the seat is free the moment it lapses."""
        payment = self.timed_out_payment()

        self.assertEqual(self.event.reserved_count, 0)
        self.assertEqual(self.event.seats_left, 2)
        self.assertFalse(self.event.is_full)

        # ...and the rows are untouched until the sweep runs.
        payment.refresh_from_db()
        self.assertEqual(payment.status, Payment.Status.SENT)
        self.assertEqual(payment.registration.status, Registration.Status.AWAITING_PAYMENT)

    def test_a_live_hold_still_counts(self):
        self.make_payment(status=Payment.Status.SENT)

        self.assertEqual(self.event.reserved_count, 1)
        self.assertEqual(self.event.seats_left, 1)

    def test_a_confirmed_ticket_counts_whatever_its_payment_says(self):
        payment = self.make_payment()
        payment.apply_paynow_status("Paid")
        payment.settle()
        payment.expires_at = timezone.now() - timedelta(minutes=1)
        payment.save()

        self.assertEqual(self.event.reserved_count, 1)

    def test_a_registration_with_no_payment_yet_holds_its_seat(self):
        """They've clicked register and haven't reached checkout. Still theirs."""
        self.event.register(self.student)

        self.assertEqual(self.event.reserved_count, 1)


class ReleaseAbandonedHoldsTests(PaymentTestCase):
    """The scheduled job that does what reading capacity used to do."""

    def test_it_retires_the_hold_and_frees_the_waitlist(self):
        payment = self.make_payment(status=Payment.Status.SENT)
        self.event.register(self.other)
        waiting = self.event.register(make_user("third", university=self.uz))
        self.assertEqual(waiting.status, Registration.Status.WAITLISTED)

        payment.expires_at = timezone.now() - timedelta(minutes=1)
        payment.save()

        self.assertEqual(release_abandoned_holds(), 1)

        payment.refresh_from_db()
        payment.registration.refresh_from_db()
        waiting.refresh_from_db()
        self.assertEqual(payment.status, Payment.Status.EXPIRED)
        self.assertEqual(payment.registration.status, Registration.Status.CANCELLED)
        self.assertEqual(waiting.status, Registration.Status.AWAITING_PAYMENT)

    def test_it_is_quiet_when_there_is_nothing_to_do(self):
        self.make_payment(status=Payment.Status.SENT)

        self.assertEqual(release_abandoned_holds(), 0)

    def test_the_management_command_reports_what_it_released(self):
        payment = self.make_payment(status=Payment.Status.SENT)
        payment.expires_at = timezone.now() - timedelta(minutes=1)
        payment.save()

        out = StringIO()
        call_command("expire_holds", stdout=out)

        self.assertIn("Released 1 abandoned checkout", out.getvalue())


@override_settings(RATELIMIT_ENABLE=True)
class ThrottleTests(PaymentTestCase):
    """The gateway callback is open to the internet and costs us an API call."""

    def setUp(self):
        super().setUp()
        cache.clear()
        self.addCleanup(cache.clear)

    def test_hammering_one_reference_is_throttled_with_a_429(self):
        payment = self.make_payment(status=Payment.Status.SENT)
        url = reverse("payments:result", args=[payment.reference])

        with patch("payments.views._sync_pesepay", side_effect=lambda p: p):
            statuses = [self.client.post(url, {}).status_code for _ in range(14)]

        self.assertIn(200, statuses)
        self.assertEqual(statuses[-1], 429, "the callback was never throttled")

    def test_a_throttled_caller_is_told_when_to_come_back(self):
        """429 and Retry-After, not 403 — a gateway retries one and gives up on the other."""
        payment = self.make_payment(status=Payment.Status.SENT)
        url = reverse("payments:result", args=[payment.reference])

        with patch("payments.views._sync_pesepay", side_effect=lambda p: p):
            for _ in range(14):
                response = self.client.post(url, {})

        self.assertEqual(response.status_code, 429)
        self.assertEqual(response["Retry-After"], "60")
        self.assertFalse(response.json()["ok"])

    def test_the_limit_is_per_payment_not_across_the_platform(self):
        """One noisy checkout must not stop every other student's payment settling."""
        first = self.make_payment(status=Payment.Status.SENT)
        second = self.make_payment(user=self.other, status=Payment.Status.SENT)

        with patch("payments.views._sync_pesepay", side_effect=lambda p: p):
            for _ in range(14):
                self.client.post(reverse("payments:result", args=[first.reference]), {})
            response = self.client.post(
                reverse("payments:result", args=[second.reference]), {}
            )

        self.assertEqual(response.status_code, 200)


@NO_LIVE_GATEWAYS
class PollDebounceTests(PaymentTestCase):
    """The status page polls on a timer; the gateway must not be asked every tick.

    Local state still updates on every poll, so a payment confirmed by callback
    a second ago is noticed straight away. Only the outbound call is rationed.
    """

    def make_live_push(self):
        payment = self.make_payment(
            status=Payment.Status.SENT,
            gateway=Payment.Gateway.PESEPAY,
            method=Payment.Method.ECOCASH,
            phone="0771234567",
        )
        payment.paynow_reference = "PSP-1"
        payment.expires_at = timezone.now() + timedelta(minutes=20)
        payment.save()
        return payment

    def poll_once(self, payment):
        return self.client.get(
            reverse("payments:status_json", args=[payment.reference]),
            headers={"x-requested-with": "XMLHttpRequest"},
        )

    def test_a_burst_of_polls_asks_the_gateway_once(self):
        payment = self.make_live_push()
        self.client.force_login(self.student)

        with patch("payments.views.PesepayClient") as client:
            client.return_value.check_payment.return_value = PesepayResponse(
                ok=True, status="PROCESSING", reference="PSP-1"
            )
            for _ in range(10):
                self.poll_once(payment)

            self.assertEqual(
                client.return_value.check_payment.call_count,
                1,
                "ten polls in one second should be one question to Pesepay",
            )

    def test_the_gateway_is_asked_again_once_the_interval_has_passed(self):
        payment = self.make_live_push()
        self.client.force_login(self.student)

        with patch("payments.views.PesepayClient") as client:
            client.return_value.check_payment.return_value = PesepayResponse(
                ok=True, status="PROCESSING", reference="PSP-1"
            )
            self.poll_once(payment)

            Payment.objects.filter(pk=payment.pk).update(
                last_polled_at=timezone.now() - timedelta(seconds=30)
            )
            self.poll_once(payment)

            self.assertEqual(client.return_value.check_payment.call_count, 2)

    def test_a_payment_settled_by_callback_is_seen_without_asking_again(self):
        """The debounce must not delay the one thing the student is waiting for."""
        payment = self.make_live_push()
        self.client.force_login(self.student)

        with patch("payments.views.PesepayClient") as client:
            client.return_value.check_payment.return_value = PesepayResponse(
                ok=True, status="PROCESSING", reference="PSP-1"
            )
            self.poll_once(payment)

            # The gateway's callback lands between polls and marks it paid.
            Payment.objects.filter(pk=payment.pk).update(
                status=Payment.Status.PAID, paid_at=timezone.now()
            )

            response = self.poll_once(payment)

            # Still within the debounce window, so no second question...
            self.assertEqual(client.return_value.check_payment.call_count, 1)

        # ...but the ticket is confirmed and the student is sent to it.
        body = response.json()
        self.assertTrue(body["settled"])
        self.assertTrue(body["ticket_url"])
        payment.registration.refresh_from_db()
        self.assertEqual(payment.registration.status, Registration.Status.CONFIRMED)

    def test_the_gateway_callback_itself_is_never_debounced(self):
        """A nudge from the gateway means something changed — always go and look."""
        payment = self.make_live_push()

        with patch("payments.views.PesepayClient") as client:
            client.return_value.check_payment.return_value = PesepayResponse(
                ok=True, status="PROCESSING", reference="PSP-1"
            )
            url = reverse("payments:result", args=[payment.reference])
            self.client.post(url, {})
            self.client.post(url, {})

            self.assertEqual(client.return_value.check_payment.call_count, 2)


@NO_LIVE_GATEWAYS
class PollIntervalTests(PaymentTestCase):
    """The server tells the client when to come back."""

    def state_of(self, payment):
        self.client.force_login(self.student)
        return self.client.get(
            reverse("payments:status_json", args=[payment.reference]),
            headers={"x-requested-with": "XMLHttpRequest"},
        ).json()

    def open_payment(self, age_seconds=0):
        payment = self.make_payment(status=Payment.Status.SENT)
        payment.expires_at = timezone.now() + timedelta(minutes=20)
        payment.save()
        if age_seconds:
            Payment.objects.filter(pk=payment.pk).update(
                created_at=timezone.now() - timedelta(seconds=age_seconds)
            )
            payment.refresh_from_db()
        return payment

    def test_it_leans_in_while_a_prompt_is_likely_to_be_answered(self):
        self.assertEqual(self.state_of(self.open_payment())["retry_in"], 2)

    def test_it_eases_off_once_the_prompt_has_been_sitting(self):
        self.assertEqual(self.state_of(self.open_payment(age_seconds=45))["retry_in"], 5)

    def test_it_backs_right_off_after_a_couple_of_minutes(self):
        self.assertEqual(self.state_of(self.open_payment(age_seconds=200))["retry_in"], 10)

    def test_a_settled_payment_tells_the_client_to_stop(self):
        payment = self.make_payment()
        payment.apply_paynow_status("Paid")
        payment.settle()

        self.assertEqual(self.state_of(payment)["retry_in"], 0)

    def test_a_dead_payment_tells_the_client_to_stop(self):
        payment = self.make_payment(status=Payment.Status.SENT)
        payment.expires_at = timezone.now() - timedelta(minutes=1)
        payment.save()

        self.assertEqual(self.state_of(payment)["retry_in"], 0)
