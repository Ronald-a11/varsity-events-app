"""Claiming a society that is already listed.

This is the supply side of the directory: societies get listed from public
information so a campus page isn't empty, and their real committees take the
page over afterwards. What matters is that handing one over is deliberate — it
carries the society's events, its attendee lists and its money.
"""

from django.core import mail as django_mail
from django.db import IntegrityError, transaction
from django.test import TestCase
from django.urls import reverse

from accounts import verification
from accounts.models import University, User
from organizations.models import Membership, Organization, OrganizationClaim
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


class ClaimSubmissionTests(TestCase):
    def setUp(self):
        self.university = University.objects.create(
            name="University of Zimbabwe", short_name="UZ"
        )
        # Listed by us from public information: no owner, nobody signed up.
        self.org = Organization.objects.create(
            name="UZ Debate Union", university=self.university
        )
        self.claimant = make_user("tanaka", university=self.university)

    def url(self):
        return reverse("organizations:claim", args=[self.org.slug])

    def payload(self, **extra):
        return {
            "role_title": "Secretary",
            "evidence": "I'm the 2026 secretary, student number R2011234, and I run "
            "the @uzdebateunion account.",
            **extra,
        }

    def test_a_listed_society_with_no_owner_reads_as_unclaimed(self):
        self.assertTrue(self.org.is_unclaimed)

    def test_the_claim_form_renders(self):
        self.client.force_login(self.claimant)

        response = self.client.get(self.url())

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "UZ Debate Union")
        self.assertContains(response, "Nobody runs this page yet")

    def test_the_form_says_something_different_when_it_is_already_owned(self):
        """Claiming an owned society is asking somebody to be replaced, and the
        page should not pretend otherwise."""
        Membership.objects.create(
            organization=self.org, user=make_user("incumbent"), role=Membership.Role.OWNER
        )
        self.client.force_login(self.claimant)

        response = self.client.get(self.url())

        self.assertContains(response, "Somebody already runs this page")

    def test_a_claim_can_be_sent(self):
        self.client.force_login(self.claimant)
        self.client.post(self.url(), self.payload())

        claim = OrganizationClaim.objects.get(organization=self.org, user=self.claimant)
        self.assertTrue(claim.is_pending)
        self.assertEqual(claim.role_title, "Secretary")

    def test_sending_a_claim_acknowledges_it_by_email(self):
        self.client.force_login(self.claimant)
        django_mail.outbox.clear()
        self.client.post(self.url(), self.payload())

        self.assertEqual(len(django_mail.outbox), 1)
        self.assertIn(self.claimant.email, django_mail.outbox[0].to)

    def test_thin_evidence_is_refused(self):
        """A claim nobody can check turns review into a rubber stamp."""
        self.client.force_login(self.claimant)
        self.client.post(self.url(), self.payload(evidence="its mine"))

        self.assertFalse(OrganizationClaim.objects.exists())

    def test_an_unconfirmed_address_cannot_claim(self):
        self.claimant.email_verified_at = None
        self.claimant.save(update_fields=["email_verified_at"])
        self.client.force_login(self.claimant)

        response = self.client.post(self.url(), self.payload())

        self.assertFalse(OrganizationClaim.objects.exists())
        self.assertRedirects(response, reverse("accounts:profile"))

    def test_signing_in_is_required(self):
        response = self.client.post(self.url(), self.payload())

        self.assertFalse(OrganizationClaim.objects.exists())
        self.assertEqual(response.status_code, 302)

    def test_somebody_who_already_runs_it_is_sent_back(self):
        Membership.objects.create(
            organization=self.org, user=self.claimant, role=Membership.Role.OWNER
        )
        self.client.force_login(self.claimant)

        response = self.client.post(self.url(), self.payload())

        self.assertFalse(OrganizationClaim.objects.exists())
        self.assertRedirects(response, self.org.get_absolute_url())

    def test_a_second_open_claim_is_not_created(self):
        self.client.force_login(self.claimant)
        self.client.post(self.url(), self.payload())
        self.client.post(self.url(), self.payload(role_title="Chairperson"))

        self.assertEqual(OrganizationClaim.objects.count(), 1)

    def test_the_database_refuses_two_open_claims_as_well(self):
        """Belt and braces: the view checks, and so does the constraint."""
        OrganizationClaim.objects.create(
            organization=self.org, user=self.claimant, role_title="Secretary", evidence="x" * 40
        )

        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                OrganizationClaim.objects.create(
                    organization=self.org,
                    user=self.claimant,
                    role_title="Treasurer",
                    evidence="y" * 40,
                )

    def test_a_rejected_claim_can_be_followed_by_a_new_one(self):
        """The constraint is on open claims only — people do come back with more."""
        staff = make_user("staff", is_staff=True, is_superuser=True)
        first = OrganizationClaim.objects.create(
            organization=self.org, user=self.claimant, role_title="Secretary", evidence="x" * 40
        )
        first.reject(staff, "Couldn't confirm it.")

        second = OrganizationClaim.objects.create(
            organization=self.org, user=self.claimant, role_title="Secretary", evidence="y" * 40
        )
        self.assertTrue(second.is_pending)


class ClaimReviewTests(TestCase):
    def setUp(self):
        self.university = University.objects.create(
            name="University of Zimbabwe", short_name="UZ"
        )
        self.org = Organization.objects.create(
            name="UZ Debate Union", university=self.university
        )
        self.claimant = make_user("tanaka", university=self.university)
        self.claim = OrganizationClaim.objects.create(
            organization=self.org,
            user=self.claimant,
            role_title="Secretary",
            evidence="Student number R2011234, confirmed by the Dean of Students.",
        )
        self.staff = make_user("staff", is_staff=True, is_superuser=True)

    def test_approving_makes_the_claimant_the_owner(self):
        login_verified(self.client, self.staff)
        self.client.post(reverse("core:staff_claim_action", args=[self.claim.pk, "approve"]))

        self.claim.refresh_from_db()
        self.claimant.refresh_from_db()

        self.assertEqual(self.claim.status, OrganizationClaim.Status.APPROVED)
        self.assertTrue(self.org.can_manage(self.claimant))
        self.assertFalse(self.org.is_unclaimed)

    def test_approving_turns_a_student_into_an_organizer(self):
        login_verified(self.client, self.staff)
        self.client.post(reverse("core:staff_claim_action", args=[self.claim.pk, "approve"]))

        self.claimant.refresh_from_db()
        self.assertEqual(self.claimant.role, User.Role.ORGANIZER)
        self.assertTrue(self.claimant.can_organize)

    def test_approving_does_not_verify_the_society(self):
        """Proving you run it isn't the same as us vouching for it — their first
        events still go through review."""
        login_verified(self.client, self.staff)
        self.client.post(reverse("core:staff_claim_action", args=[self.claim.pk, "approve"]))

        self.org.refresh_from_db()
        self.assertFalse(self.org.is_verified)

    def test_approving_emails_the_claimant(self):
        login_verified(self.client, self.staff)
        django_mail.outbox.clear()
        self.client.post(reverse("core:staff_claim_action", args=[self.claim.pk, "approve"]))

        self.assertEqual(len(django_mail.outbox), 1)
        self.assertIn(self.claimant.email, django_mail.outbox[0].to)

    def test_rejecting_carries_the_reason_to_the_claimant(self):
        login_verified(self.client, self.staff)
        django_mail.outbox.clear()
        self.client.post(
            reverse("core:staff_claim_action", args=[self.claim.pk, "reject"]),
            {"note": "The current committee says otherwise."},
        )

        self.claim.refresh_from_db()
        self.assertEqual(self.claim.status, OrganizationClaim.Status.REJECTED)
        self.assertEqual(self.claim.review_note, "The current committee says otherwise.")
        self.assertIn("The current committee says otherwise.", django_mail.outbox[0].body)
        self.assertFalse(self.org.can_manage(self.claimant))

    def test_an_ordinary_user_cannot_approve_a_claim(self):
        self.client.force_login(self.claimant)
        self.client.post(reverse("core:staff_claim_action", args=[self.claim.pk, "approve"]))

        self.claim.refresh_from_db()
        self.assertTrue(self.claim.is_pending)
        self.assertFalse(self.org.can_manage(self.claimant))

    def test_a_decided_claim_is_not_decided_twice(self):
        login_verified(self.client, self.staff)
        self.client.post(reverse("core:staff_claim_action", args=[self.claim.pk, "reject"]),
                         {"note": "No."})
        self.client.post(reverse("core:staff_claim_action", args=[self.claim.pk, "approve"]))

        self.claim.refresh_from_db()
        self.assertEqual(self.claim.status, OrganizationClaim.Status.REJECTED)
        self.assertFalse(self.org.can_manage(self.claimant))

    def test_the_queue_page_renders(self):
        login_verified(self.client, self.staff)
        response = self.client.get(reverse("core:staff_claims"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "UZ Debate Union")

    def test_the_queue_is_staff_only(self):
        self.client.force_login(self.claimant)
        response = self.client.get(reverse("core:staff_claims"))

        self.assertNotEqual(response.status_code, 200)


class UnclaimedTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(name="Society")
        self.person = make_user("person")

    def test_an_inactive_owner_leaves_it_unclaimed(self):
        """A committee that graduated and deactivated is the case this is for."""
        Membership.objects.create(
            organization=self.org,
            user=self.person,
            role=Membership.Role.OWNER,
            is_active=False,
        )

        self.assertTrue(self.org.is_unclaimed)

    def test_an_admin_alone_still_counts_as_unclaimed(self):
        Membership.objects.create(
            organization=self.org, user=self.person, role=Membership.Role.ADMIN
        )

        self.assertTrue(self.org.is_unclaimed)

    def test_the_society_page_offers_the_claim(self):
        response = self.client.get(self.org.get_absolute_url())

        self.assertContains(response, "Nobody is running this page yet")
