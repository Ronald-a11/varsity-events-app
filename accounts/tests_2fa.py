"""Two-factor: who it applies to, and that it can't lock anyone out."""

from datetime import timedelta

from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from django_otp import DEVICE_ID_SESSION_KEY
from django_otp.oath import TOTP
from django_otp.plugins.otp_static.models import StaticDevice
from django_otp.plugins.otp_totp.models import TOTPDevice

from accounts.models import University, User
from accounts.twofactor import handles_money, has_second_factor, issue_recovery_codes
from events.models import Event, Registration
from organizations.models import Membership, Organization
from payments.models import Payment
from varsity.testing import login_verified


def current_code(device):
    """The code the user's app would be showing right now."""
    totp = TOTP(device.bin_key, device.step, device.t0, device.digits, device.drift)
    totp.time = timezone.now().timestamp()
    return str(totp.token()).zfill(device.digits)


class TwoFactorTestCase(TestCase):
    def setUp(self):
        self.uz = University.objects.create(name="University of Zimbabwe", short_name="UZ")
        self.student = User.objects.create_user(
            username="student", email="s@uz.test", password="pw", university=self.uz
        )
        self.organizer = User.objects.create_user(
            username="organizer", email="o@uz.test", password="pw", role=User.Role.ORGANIZER
        )
        self.staff = User.objects.create_user(
            username="curator", email="c@uz.test", password="pw", is_staff=True
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
            is_free=False,
            price=10,
        )


class ScopeTests(TwoFactorTestCase):
    """Only the accounts that can release money are in scope."""

    def test_a_student_is_not(self):
        self.assertFalse(handles_money(self.student))

    def test_an_organizer_who_runs_a_society_is(self):
        self.assertTrue(handles_money(self.organizer))

    def test_platform_staff_are(self):
        self.assertTrue(handles_money(self.staff))

    def test_an_organizer_who_runs_nothing_yet_is_not(self):
        """The role alone doesn't let you verify anything."""
        lonely = User.objects.create_user(
            username="new", email="n@uz.test", password="pw", role=User.Role.ORGANIZER
        )
        self.assertFalse(handles_money(lonely))

    def test_being_made_a_society_admin_brings_you_into_scope(self):
        helper = User.objects.create_user(username="helper", email="h@uz.test", password="pw")
        self.assertFalse(handles_money(helper))

        Membership.objects.create(
            organization=self.org, user=helper, role=Membership.Role.ADMIN
        )

        self.assertTrue(handles_money(helper))


class GateTests(TwoFactorTestCase):
    """Nobody is refused — they're sent to set one up."""

    def test_an_unenrolled_organizer_is_sent_to_setup_not_turned_away(self):
        self.client.force_login(self.organizer)

        response = self.client.get(reverse("payments:verify"))

        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("accounts:two_factor_setup"), response["Location"])

    def test_the_redirect_remembers_where_they_were_going(self):
        self.client.force_login(self.organizer)

        response = self.client.get(reverse("payments:verify"))

        self.assertIn("next=", response["Location"])
        self.assertIn("verify", response["Location"])

    def test_an_enrolled_but_unverified_session_is_challenged(self):
        TOTPDevice.objects.create(user=self.organizer, name="app", confirmed=True)
        self.client.force_login(self.organizer)

        response = self.client.get(reverse("payments:verify"))

        self.assertIn(reverse("accounts:two_factor_verify"), response["Location"])

    def test_a_verified_session_gets_through(self):
        login_verified(self.client, self.organizer)

        self.assertEqual(self.client.get(reverse("payments:verify")).status_code, 200)

    def test_staff_curation_is_gated_too(self):
        self.client.force_login(self.staff)

        response = self.client.get(reverse("core:staff_dashboard"))

        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("accounts:two_factor_setup"), response["Location"])

    def test_a_student_is_never_asked(self):
        """Adding 2FA must not make the site harder for the people it isn't for."""
        self.client.force_login(self.student)

        for name in ("core:discover", "accounts:tickets", "events:list"):
            with self.subTest(view=name):
                self.assertEqual(self.client.get(reverse(name)).status_code, 200)

    def test_a_student_who_is_not_staff_still_cannot_reach_staff_pages(self):
        """The 2FA gate must not have loosened the permission check underneath it."""
        login_verified(self.client, self.student)

        response = self.client.get(reverse("core:staff_dashboard"))

        self.assertEqual(response.status_code, 302)
        self.assertNotIn("/security/", response["Location"])


class EnrolmentTests(TwoFactorTestCase):
    """The real flow, end to end, with codes an app would actually produce."""

    def test_setup_offers_a_qr_and_a_typeable_secret(self):
        self.client.force_login(self.organizer)

        response = self.client.get(reverse("accounts:two_factor_setup"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "data:image/png;base64,")
        self.assertContains(response, "Can't scan it?")

    def test_a_wrong_code_does_not_enrol(self):
        self.client.force_login(self.organizer)
        self.client.get(reverse("accounts:two_factor_setup"))

        response = self.client.post(reverse("accounts:two_factor_setup"), {"token": "000000"})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "didn&#x27;t match")
        self.assertFalse(has_second_factor(self.organizer))

    def test_the_right_code_enrols_and_issues_recovery_codes(self):
        self.client.force_login(self.organizer)
        self.client.get(reverse("accounts:two_factor_setup"))
        device = TOTPDevice.objects.get(user=self.organizer, confirmed=False)

        response = self.client.post(
            reverse("accounts:two_factor_setup"), {"token": current_code(device)}
        )

        self.assertRedirects(response, reverse("accounts:two_factor_codes"))
        self.assertTrue(has_second_factor(self.organizer))
        self.assertEqual(StaticDevice.objects.get(user=self.organizer).token_set.count(), 10)

    def test_enrolling_verifies_the_session_immediately(self):
        """They just proved they hold the device; asking again reads as broken."""
        self.client.force_login(self.organizer)
        self.client.get(reverse("accounts:two_factor_setup"))
        device = TOTPDevice.objects.get(user=self.organizer, confirmed=False)

        self.client.post(reverse("accounts:two_factor_setup"), {"token": current_code(device)})

        self.assertEqual(self.client.get(reverse("payments:verify")).status_code, 200)

    def test_an_abandoned_setup_leaves_no_unusable_device(self):
        """A half-finished enrolment must not satisfy has_second_factor."""
        self.client.force_login(self.organizer)
        self.client.get(reverse("accounts:two_factor_setup"))

        self.assertTrue(TOTPDevice.objects.filter(user=self.organizer).exists())
        self.assertFalse(has_second_factor(self.organizer))

    def test_recovery_codes_are_shown_once(self):
        self.client.force_login(self.organizer)
        self.client.get(reverse("accounts:two_factor_setup"))
        device = TOTPDevice.objects.get(user=self.organizer, confirmed=False)
        self.client.post(reverse("accounts:two_factor_setup"), {"token": current_code(device)})

        first = self.client.get(reverse("accounts:two_factor_codes"))
        self.assertEqual(first.status_code, 200)

        second = self.client.get(reverse("accounts:two_factor_codes"))
        self.assertRedirects(second, reverse("accounts:two_factor_manage"))


class ChallengeTests(TwoFactorTestCase):
    def enrolled(self):
        device = TOTPDevice.objects.create(user=self.organizer, name="app", confirmed=True)
        self.client.force_login(self.organizer)
        return device

    def test_the_right_code_verifies_the_session(self):
        device = self.enrolled()

        response = self.client.post(
            reverse("accounts:two_factor_verify"),
            {"token": current_code(device), "next": reverse("payments:verify")},
        )

        self.assertRedirects(response, reverse("payments:verify"))

    def test_a_wrong_code_does_not(self):
        self.enrolled()

        response = self.client.post(reverse("accounts:two_factor_verify"), {"token": "000000"})

        self.assertEqual(response.status_code, 200)
        self.assertNotIn(DEVICE_ID_SESSION_KEY, self.client.session)

    def test_a_recovery_code_works_once(self):
        self.enrolled()
        codes = issue_recovery_codes(self.organizer)

        first = self.client.post(reverse("accounts:two_factor_verify"), {"token": codes[0]})
        self.assertEqual(first.status_code, 302)

        # Same code again, from a fresh session.
        self.client.logout()
        self.client.force_login(self.organizer)
        again = self.client.post(reverse("accounts:two_factor_verify"), {"token": codes[0]})
        self.assertEqual(again.status_code, 200)

    def test_next_cannot_be_used_to_bounce_somebody_off_site(self):
        """`next` rides on a querystring, so it must not be followed blindly."""
        device = self.enrolled()

        response = self.client.post(
            reverse("accounts:two_factor_verify"),
            {"token": current_code(device), "next": "https://evil.test/steal"},
        )

        self.assertEqual(response.status_code, 302)
        self.assertNotIn("evil.test", response["Location"])


class DisableTests(TwoFactorTestCase):
    def test_an_unverified_session_cannot_turn_it_off(self):
        """Otherwise a hijacked session strips the protection and keeps the account."""
        TOTPDevice.objects.create(user=self.organizer, name="app", confirmed=True)
        self.client.force_login(self.organizer)

        response = self.client.post(reverse("accounts:two_factor_disable"))

        self.assertIn(reverse("accounts:two_factor_verify"), response["Location"])
        self.assertTrue(has_second_factor(self.organizer))

    def test_a_verified_session_can(self):
        login_verified(self.client, self.organizer)

        self.client.post(reverse("accounts:two_factor_disable"))

        self.assertFalse(has_second_factor(self.organizer))

    def test_an_unverified_session_cannot_mint_fresh_recovery_codes(self):
        TOTPDevice.objects.create(user=self.organizer, name="app", confirmed=True)
        issue_recovery_codes(self.organizer)
        self.client.force_login(self.organizer)

        response = self.client.post(reverse("accounts:two_factor_regenerate_codes"))

        self.assertIn(reverse("accounts:two_factor_verify"), response["Location"])


class LockoutSafetyTests(TwoFactorTestCase):
    """The failure mode that matters: an outage you cause yourself."""

    def test_admin_enforcement_is_off_unless_asked_for(self):
        from accounts.twofactor import admin_requires_second_factor

        self.assertFalse(admin_requires_second_factor())

    @override_settings(ADMIN_REQUIRE_2FA=True)
    def test_admin_enforcement_can_be_turned_on(self):
        from accounts.twofactor import admin_requires_second_factor

        self.assertTrue(admin_requires_second_factor())

    def test_setup_is_reachable_without_a_second_factor(self):
        """The one page that must never be behind the thing it sets up."""
        self.client.force_login(self.organizer)

        self.assertEqual(self.client.get(reverse("accounts:two_factor_setup")).status_code, 200)

    def test_a_privileged_account_can_always_reach_its_own_security_page(self):
        self.client.force_login(self.staff)

        self.assertEqual(self.client.get(reverse("accounts:two_factor_manage")).status_code, 200)


class MoneyStillFlowsTests(TwoFactorTestCase):
    """The gate must not have broken what it guards."""

    def test_a_verified_organizer_can_still_release_a_transfer(self):
        registration = Registration.objects.create(
            event=self.event, user=self.student, status=Registration.Status.AWAITING_PAYMENT
        )
        payment = Payment.objects.create(
            registration=registration,
            user=self.student,
            amount=10,
            status=Payment.Status.AWAITING_VERIFICATION,
            method=Payment.Method.ECOCASH_DIRECT,
            confirmation_code="MP123456",
        )
        login_verified(self.client, self.organizer)

        self.client.post(
            reverse("payments:verify"), {"payment_id": payment.pk, "decision": "verify"}
        )

        payment.refresh_from_db()
        registration.refresh_from_db()
        self.assertEqual(payment.status, Payment.Status.PAID)
        self.assertEqual(registration.status, Registration.Status.CONFIRMED)
