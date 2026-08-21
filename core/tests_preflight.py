"""The go-live checklist, and the settings checks that back it.

Both exist to catch the failures that look like success: mail that evaporates,
uploads wiped by the next deploy, a demo password left on a superuser. So the
thing worth testing is that each one actually fires — a checklist that passes
unconditionally is worse than none, because it gets trusted.
"""

from datetime import timedelta
from io import StringIO

from django.core.management import call_command
from django.test import TestCase, override_settings
from django.utils import timezone

from accounts.models import University, User
from core import checks
from events.models import Category, Event
from organizations.models import Organization


def run_preflight():
    """Returns (output, exit_code). SystemExit is the command's failure signal."""
    out = StringIO()
    try:
        call_command("preflight", stdout=out, stderr=out)
    except SystemExit as exc:
        return out.getvalue(), exc.code
    return out.getvalue(), 0


class PreflightTests(TestCase):
    def setUp(self):
        self.university = University.objects.create(
            name="University of Zimbabwe", short_name="UZ"
        )
        Category.objects.create(name="Tech")
        self.admin = User.objects.create_superuser(
            username="realadmin", email="admin@varsity.test", password="a-real-passphrase-42"
        )

    def test_a_clean_deployment_passes(self):
        output, code = run_preflight()

        self.assertEqual(code, 0, output)
        self.assertIn("Ready for real events", output)

    def test_a_superuser_with_the_demo_password_is_a_blocker(self):
        User.objects.create_superuser(
            username="admin", email="seeded@varsity.test", password="demo12345"
        )

        output, code = run_preflight()

        self.assertEqual(code, 1)
        self.assertIn("admin", output)
        self.assertIn("demo password", output)

    def test_renaming_the_seeded_admin_does_not_hide_it(self):
        """Checked by hashing the password, not by matching the username."""
        User.objects.create_superuser(
            username="totally-not-the-demo-account",
            email="sneaky@varsity.test",
            password="demo12345",
        )

        output, code = run_preflight()

        self.assertEqual(code, 1)
        self.assertIn("totally-not-the-demo-account", output)

    def test_seeded_students_are_counted_rather_than_listed(self):
        for index in range(3):
            User.objects.create_user(
                username=f"seeded{index}",
                email=f"seeded{index}@varsity.test",
                password="demo12345",
            )

        output, code = run_preflight()

        self.assertEqual(code, 1)
        self.assertIn("3 non-staff account(s)", output)

    def test_no_universities_blocks_because_nobody_could_sign_up(self):
        University.objects.all().delete()

        output, code = run_preflight()

        self.assertEqual(code, 1)
        self.assertIn("No universities", output)

    def test_no_superuser_blocks(self):
        User.objects.all().delete()

        output, code = run_preflight()

        self.assertEqual(code, 1)
        self.assertIn("No active superuser", output)

    @override_settings(
        PESEPAY_INTEGRATION_KEY="", PESEPAY_ENCRYPTION_KEY="", ECOCASH_MERCHANT_NUMBER=""
    )
    def test_a_paid_event_with_no_way_to_take_money_blocks(self):
        org = Organization.objects.create(name="Society", university=self.university)
        now = timezone.now()
        Event.objects.create(
            title="Paid Night",
            organization=org,
            starts_at=now + timedelta(days=3),
            ends_at=now + timedelta(days=3, hours=3),
            is_free=False,
            price=5,
        )

        output, code = run_preflight()

        self.assertEqual(code, 1)
        self.assertIn("no payment route is configured", output)

    def test_strict_turns_warnings_into_a_failure(self):
        out = StringIO()
        Category.objects.all().delete()  # a warning, not a blocker

        try:
            call_command("preflight", "--strict", stdout=out, stderr=out)
            code = 0
        except SystemExit as exc:
            code = exc.code

        self.assertEqual(code, 1)

    def test_it_writes_nothing_to_the_database(self):
        """Safe to point at production, which is the only place it's useful."""
        before = (User.objects.count(), Event.objects.count(), Organization.objects.count())
        run_preflight()
        after = (User.objects.count(), Event.objects.count(), Organization.objects.count())

        self.assertEqual(before, after)


@override_settings(DEBUG=False, TESTING=False)
class DeployCheckTests(TestCase):
    """The three settings that fail silently rather than crashing."""

    @override_settings(EMAIL_HOST="")
    def test_missing_email_host_is_reported(self):
        found = checks.email_is_configured(None)
        self.assertEqual([w.id for w in found], ["varsity.W001"])

    @override_settings(EMAIL_HOST="smtp.example.com")
    def test_a_configured_mail_server_is_quiet(self):
        self.assertEqual(checks.email_is_configured(None), [])

    @override_settings(AWS_STORAGE_BUCKET_NAME="")
    def test_media_inside_the_project_is_reported(self):
        from django.conf import settings

        with self.settings(MEDIA_ROOT=settings.BASE_DIR / "media"):
            found = checks.uploads_survive_a_deploy(None)

        self.assertEqual([w.id for w in found], ["varsity.W002"])

    @override_settings(AWS_STORAGE_BUCKET_NAME="varsity-uploads")
    def test_object_storage_settles_it(self):
        self.assertEqual(checks.uploads_survive_a_deploy(None), [])

    @override_settings(MEDIA_ROOT="/data/media", AWS_STORAGE_BUCKET_NAME="")
    def test_a_mounted_volume_settles_it_too(self):
        self.assertEqual(checks.uploads_survive_a_deploy(None), [])

    @override_settings(SITE_BASE_URL="http://localhost:8000")
    def test_localhost_in_emailed_links_is_reported(self):
        found = checks.links_in_email_point_somewhere_real(None)
        self.assertEqual([w.id for w in found], ["varsity.W003"])

    @override_settings(SITE_BASE_URL="https://varsityevents.app")
    def test_a_real_base_url_is_quiet(self):
        self.assertEqual(checks.links_in_email_point_somewhere_real(None), [])

    @override_settings(DEBUG=True, EMAIL_HOST="", SITE_BASE_URL="http://localhost:8000")
    def test_development_is_left_alone(self):
        """None of this matters until DEBUG is off."""
        self.assertEqual(checks.email_is_configured(None), [])
        self.assertEqual(checks.links_in_email_point_somewhere_real(None), [])


class ReferenceOnlySeedTests(TestCase):
    """What a production database actually wants from seed_demo: the true half."""

    def test_it_loads_reference_data_and_invents_nothing(self):
        out = StringIO()
        call_command("seed_demo", "--reference-only", "--no-images", stdout=out)

        self.assertEqual(University.objects.count(), 18)
        self.assertTrue(Category.objects.exists())
        self.assertFalse(Organization.objects.exists())
        self.assertFalse(Event.objects.exists())
        self.assertFalse(User.objects.exists())
