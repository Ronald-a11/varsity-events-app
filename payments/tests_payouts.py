"""The platform fee and the payout ledger.

Every ticket is paid into the platform's own account, so the platform owes each
society its takings less the fee. The tests that matter are the ones about
money not moving twice, and about history not being restated when the rate
changes.
"""

from datetime import timedelta
from decimal import Decimal

from django.core import mail as django_mail
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from accounts import verification
from accounts.models import University, User
from events.models import Event, Registration
from organizations.models import Membership, Organization
from payments.models import (
    Payment,
    Payout,
    ledger_for,
    prepare_payout,
    societies_owed,
    unpaid_payments_for,
)
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


class LedgerTestCase(TestCase):
    def setUp(self):
        self.university = University.objects.create(
            name="University of Zimbabwe", short_name="UZ"
        )
        self.organizer = make_user("organizer", role=User.Role.ORGANIZER)
        self.org = Organization.objects.create(
            name="UZ Music Collective", university=self.university
        )
        Membership.objects.create(
            organization=self.org, user=self.organizer, role=Membership.Role.OWNER
        )

        now = timezone.now()
        self.event = Event.objects.create(
            title="Album Launch",
            organization=self.org,
            created_by=self.organizer,
            starts_at=now + timedelta(days=5),
            ends_at=now + timedelta(days=5, hours=4),
            status=Event.Status.PUBLISHED,
            is_free=False,
            price=Decimal("10.00"),
        )
        self.student = make_user("student", university=self.university)

    def sell(self, amount="10.00", *, settle=True, user=None):
        """One ticket, paid for. Returns the Payment."""
        buyer = user or self.student
        registration = Registration.objects.create(
            event=self.event, user=buyer, status=Registration.Status.AWAITING_PAYMENT
        )
        payment = Payment.objects.create(
            registration=registration,
            user=buyer,
            amount=Decimal(amount),
            status=Payment.Status.PAID if settle else Payment.Status.CREATED,
        )
        if settle:
            payment.settle()
            payment.refresh_from_db()
        return payment


class PlatformFeeTests(LedgerTestCase):
    @override_settings(PLATFORM_FEE_PERCENT=0, PLATFORM_FEE_FIXED=0)
    def test_with_no_fee_configured_the_society_gets_everything(self):
        """The default, and what every deployment did before the ledger existed.

        Pinned rather than inherited: this asserts what a *zero* rate does, so
        it must not quietly become an assertion about whatever rate the machine
        running it happens to charge.
        """
        payment = self.sell("10.00")

        self.assertEqual(payment.platform_fee, Decimal("0.00"))
        self.assertEqual(payment.net_amount, Decimal("10.00"))

    @override_settings(PLATFORM_FEE_PERCENT=10)
    def test_a_percentage_is_taken(self):
        payment = self.sell("10.00")

        self.assertEqual(payment.platform_fee, Decimal("1.00"))
        self.assertEqual(payment.net_amount, Decimal("9.00"))

    @override_settings(PLATFORM_FEE_PERCENT=5, PLATFORM_FEE_FIXED="0.20")
    def test_percentage_and_fixed_compose(self):
        payment = self.sell("10.00")

        self.assertEqual(payment.platform_fee, Decimal("0.70"))

    @override_settings(PLATFORM_FEE_PERCENT=2.5)
    def test_a_half_cent_rounds_up_rather_than_to_even(self):
        """Banker's rounding would leave a society short and them right about it."""
        payment = self.sell("5.00")  # 0.125

        self.assertEqual(payment.platform_fee, Decimal("0.13"))

    @override_settings(PLATFORM_FEE_PERCENT=250)
    def test_the_fee_can_never_exceed_the_ticket(self):
        payment = self.sell("10.00")

        self.assertEqual(payment.platform_fee, Decimal("10.00"))
        self.assertEqual(payment.net_amount, Decimal("0.00"))

    @override_settings(PLATFORM_FEE_PERCENT=-5)
    def test_a_negative_rate_cannot_pay_a_society_extra(self):
        payment = self.sell("10.00")

        self.assertEqual(payment.platform_fee, Decimal("0.00"))

    @override_settings(PLATFORM_FEE_PERCENT=10)
    def test_the_fee_is_frozen_at_settlement(self):
        """Raising the rate next term must not restate what was owed last term."""
        payment = self.sell("10.00")

        with self.settings(PLATFORM_FEE_PERCENT=50):
            payment.settle()
            payment.refresh_from_db()

        self.assertEqual(payment.platform_fee, Decimal("1.00"))

    @override_settings(PLATFORM_FEE_PERCENT=10)
    def test_settling_twice_does_not_charge_twice(self):
        payment = self.sell("10.00")
        payment.settle()
        payment.refresh_from_db()

        self.assertEqual(payment.platform_fee, Decimal("1.00"))

    def test_an_unassessed_payment_counts_as_owed_in_full(self):
        """Rows taken before the ledger existed were collected under no fee."""
        payment = self.sell("10.00")
        Payment.objects.filter(pk=payment.pk).update(platform_fee=None)
        payment.refresh_from_db()

        self.assertEqual(payment.net_amount, Decimal("10.00"))

    def test_an_unsettled_payment_is_not_assessed_at_all(self):
        payment = self.sell("10.00", settle=False)

        self.assertIsNone(payment.platform_fee)


@override_settings(PLATFORM_FEE_PERCENT=10)
class LedgerArithmeticTests(LedgerTestCase):
    def test_the_ledger_totals_what_is_owed(self):
        self.sell("10.00")
        self.sell("10.00", user=make_user("second"))

        ledger = ledger_for(self.org)

        self.assertEqual(ledger["gross"], Decimal("20.00"))
        self.assertEqual(ledger["fees"], Decimal("2.00"))
        self.assertEqual(ledger["outstanding"], Decimal("18.00"))
        self.assertEqual(ledger["tickets"], 2)

    def test_an_unsettled_payment_is_not_owed(self):
        self.sell("10.00", settle=False)

        self.assertEqual(ledger_for(self.org)["outstanding"], Decimal("0"))

    def test_a_society_with_no_sales_reads_zero_rather_than_none(self):
        self.assertEqual(ledger_for(self.org)["outstanding"], Decimal("0"))
        self.assertEqual(ledger_for(self.org)["gross"], Decimal("0"))


@override_settings(PLATFORM_FEE_PERCENT=10)
class PreparePayoutTests(LedgerTestCase):
    def test_preparing_claims_every_outstanding_sale(self):
        self.sell("10.00")
        self.sell("10.00", user=make_user("second"))

        payout = prepare_payout(self.org)

        self.assertEqual(payout.gross_amount, Decimal("20.00"))
        self.assertEqual(payout.fee_amount, Decimal("2.00"))
        self.assertEqual(payout.amount, Decimal("18.00"))
        self.assertEqual(payout.ticket_count, 2)

    def test_a_claimed_sale_is_no_longer_outstanding(self):
        self.sell("10.00")
        prepare_payout(self.org)

        self.assertEqual(ledger_for(self.org)["outstanding"], Decimal("0"))
        self.assertFalse(unpaid_payments_for(self.org).exists())

    def test_preparing_twice_cannot_pay_the_same_money_out_twice(self):
        self.sell("10.00")
        prepare_payout(self.org)

        self.assertIsNone(prepare_payout(self.org))
        self.assertEqual(Payout.objects.count(), 1)

    def test_nothing_owed_prepares_nothing(self):
        self.assertIsNone(prepare_payout(self.org))
        self.assertFalse(Payout.objects.exists())

    def test_a_sale_made_after_a_payout_goes_into_the_next_one(self):
        self.sell("10.00")
        first = prepare_payout(self.org)
        self.sell("10.00", user=make_user("second"))
        second = prepare_payout(self.org)

        self.assertNotEqual(first.pk, second.pk)
        self.assertEqual(second.amount, Decimal("9.00"))
        self.assertEqual(second.ticket_count, 1)

    def test_cancelling_returns_the_money_to_the_pool(self):
        self.sell("10.00")
        payout = prepare_payout(self.org)
        payout.cancel()

        self.assertEqual(ledger_for(self.org)["outstanding"], Decimal("9.00"))
        self.assertEqual(payout.status, Payout.Status.CANCELLED)

    def test_a_sent_payout_cannot_be_cancelled(self):
        self.sell("10.00")
        payout = prepare_payout(self.org)
        payout.mark_paid(external_reference="MP250821.1234.A56789")

        with self.assertRaises(ValueError):
            payout.cancel()

    def test_marking_paid_is_idempotent(self):
        self.sell("10.00")
        payout = prepare_payout(self.org)

        self.assertTrue(payout.mark_paid(external_reference="ABC123"))
        self.assertFalse(payout.mark_paid(external_reference="DEF456"))
        self.assertEqual(payout.external_reference, "ABC123")

    def test_societies_owed_lists_the_society(self):
        self.sell("10.00")

        owed = societies_owed()

        self.assertEqual(len(owed), 1)
        self.assertEqual(owed[0]["organization"], self.org)
        self.assertEqual(owed[0]["outstanding"], Decimal("9.00"))

    def test_societies_owed_drops_one_that_has_been_paid(self):
        self.sell("10.00")
        prepare_payout(self.org)

        self.assertEqual(societies_owed(), [])


@override_settings(PLATFORM_FEE_PERCENT=10)
class PayoutViewTests(LedgerTestCase):
    def setUp(self):
        super().setUp()
        self.staff = make_user("staff", is_staff=True, is_superuser=True)

    def test_an_organizer_sees_their_own_earnings(self):
        self.sell("10.00")
        self.client.force_login(self.organizer)

        response = self.client.get(reverse("payments:earnings"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "UZ Music Collective")

    def test_somebody_with_no_society_has_no_earnings_page(self):
        self.client.force_login(self.student)

        self.assertEqual(self.client.get(reverse("payments:earnings")).status_code, 404)

    def test_the_payout_desk_is_staff_only(self):
        self.client.force_login(self.organizer)

        self.assertEqual(self.client.get(reverse("payments:payout_desk")).status_code, 404)

    def test_the_payout_desk_renders_what_is_owed(self):
        self.sell("10.00")
        login_verified(self.client, self.staff)

        response = self.client.get(reverse("payments:payout_desk"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "UZ Music Collective")
        self.assertContains(response, "9.00")

    def test_the_desk_renders_a_prepared_payout_awaiting_its_transfer(self):
        self.sell("10.00")
        payout = prepare_payout(self.org)
        login_verified(self.client, self.staff)

        response = self.client.get(reverse("payments:payout_desk"))

        self.assertContains(response, payout.reference)
        self.assertContains(response, "Mark sent")

    def test_an_empty_desk_still_renders(self):
        login_verified(self.client, self.staff)

        response = self.client.get(reverse("payments:payout_desk"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Nothing outstanding")

    def test_staff_can_prepare_and_send_a_payout(self):
        self.sell("10.00")
        login_verified(self.client, self.staff)

        self.client.post(reverse("payments:payout_prepare", args=[self.org.slug]),
                         {"method": "ecocash", "destination": "0771234567"})
        payout = Payout.objects.get()
        self.assertEqual(payout.amount, Decimal("9.00"))

        django_mail.outbox.clear()
        self.client.post(
            reverse("payments:payout_mark_paid", args=[payout.reference]),
            {"external_reference": "MP250821.1234.A56789", "destination": "0771234567"},
        )

        payout.refresh_from_db()
        self.assertTrue(payout.is_paid)
        self.assertEqual(payout.external_reference, "MP250821.1234.A56789")
        self.assertEqual(len(django_mail.outbox), 1)

    def test_marking_sent_without_a_confirmation_code_is_refused(self):
        """Without the wallet's own code the row cannot be reconciled later."""
        self.sell("10.00")
        login_verified(self.client, self.staff)
        self.client.post(reverse("payments:payout_prepare", args=[self.org.slug]))
        payout = Payout.objects.get()

        self.client.post(reverse("payments:payout_mark_paid", args=[payout.reference]),
                         {"external_reference": "  "})

        payout.refresh_from_db()
        self.assertFalse(payout.is_paid)

    def test_a_society_can_open_its_own_statement(self):
        self.sell("10.00")
        payout = prepare_payout(self.org)
        self.client.force_login(self.organizer)

        response = self.client.get(reverse("payments:payout_detail", args=[payout.reference]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, payout.reference)

    def test_another_society_cannot_read_that_statement(self):
        self.sell("10.00")
        payout = prepare_payout(self.org)
        outsider = make_user("outsider", role=User.Role.ORGANIZER)
        other = Organization.objects.create(name="Somebody Else")
        Membership.objects.create(
            organization=other, user=outsider, role=Membership.Role.OWNER
        )
        self.client.force_login(outsider)

        response = self.client.get(reverse("payments:payout_detail", args=[payout.reference]))

        self.assertEqual(response.status_code, 404)

    def test_an_ordinary_organizer_cannot_prepare_a_payout_to_themselves(self):
        self.sell("10.00")
        self.client.force_login(self.organizer)

        self.client.post(reverse("payments:payout_prepare", args=[self.org.slug]))

        self.assertFalse(Payout.objects.exists())
