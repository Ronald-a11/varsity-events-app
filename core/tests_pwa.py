"""Installability, and the offline ticket the whole thing exists for."""

import json
from datetime import timedelta
from pathlib import Path

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import University, User
from core.pwa import NEVER_CACHE, version
from events.models import Event, Registration
from events.qr import data_uri, payload
from organizations.models import Membership, Organization


class PWATestCase(TestCase):
    def setUp(self):
        self.uz = University.objects.create(name="University of Zimbabwe", short_name="UZ")
        self.organizer = User.objects.create_user(
            username="organizer", email="o@uz.test", password="pw", role=User.Role.ORGANIZER
        )
        self.student = User.objects.create_user(
            username="student", email="s@uz.test", password="pw", university=self.uz
        )
        self.org = Organization.objects.create(
            name="Test Society", created_by=self.organizer, university=self.uz
        )
        Membership.objects.create(
            organization=self.org, user=self.organizer, role=Membership.Role.OWNER
        )
        now = timezone.now()
        self.event = Event.objects.create(
            title="Jazz Night",
            organization=self.org,
            created_by=self.organizer,
            starts_at=now + timedelta(days=3),
            ends_at=now + timedelta(days=3, hours=3),
            status=Event.Status.PUBLISHED,
            is_free=True,
        )


class ManifestTests(PWATestCase):
    def test_it_is_valid_json_and_served_as_a_manifest(self):
        response = self.client.get(reverse("core:manifest"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/manifest+json")
        json.loads(response.content)  # raises if the template left a trailing comma

    def test_it_declares_what_a_home_screen_needs(self):
        manifest = json.loads(self.client.get(reverse("core:manifest")).content)

        self.assertTrue(manifest["name"])
        self.assertTrue(manifest["short_name"])
        self.assertEqual(manifest["display"], "standalone")
        self.assertEqual(manifest["scope"], "/")

    def test_it_offers_both_an_ordinary_and_a_maskable_icon(self):
        """Android crops maskable icons; one drawn for a square loses its edges."""
        manifest = json.loads(self.client.get(reverse("core:manifest")).content)
        purposes = {icon["purpose"] for icon in manifest["icons"]}
        sizes = {icon["sizes"] for icon in manifest["icons"]}

        self.assertIn("any", purposes)
        self.assertIn("maskable", purposes)
        self.assertIn("192x192", sizes)
        self.assertIn("512x512", sizes)

    def test_the_first_shortcut_is_the_tickets_page(self):
        manifest = json.loads(self.client.get(reverse("core:manifest")).content)

        self.assertEqual(manifest["shortcuts"][0]["url"], reverse("accounts:tickets"))


class ServiceWorkerTests(PWATestCase):
    def get(self):
        return self.client.get(reverse("core:service_worker"))

    def test_it_is_served_from_the_root(self):
        """A worker can only control pages at or below its own path."""
        self.assertEqual(reverse("core:service_worker"), "/sw.js")

    def test_it_is_served_as_javascript(self):
        response = self.get()

        self.assertEqual(response.status_code, 200)
        self.assertIn("javascript", response["Content-Type"])

    def test_it_is_never_itself_cached(self):
        """A worker the browser holds on to is a worker you cannot replace."""
        directives = self.get()["Cache-Control"]

        self.assertIn("no-store", directives)
        self.assertIn("no-cache", directives)

    def test_it_carries_a_version_that_moves_with_the_front_end(self):
        body = self.get().content.decode()

        self.assertIn(f'const VERSION = "{version()}"', body)
        self.assertTrue(version())

    def test_money_and_identity_are_on_the_never_cache_list(self):
        """A stale payment page can tell somebody their money failed when it didn't."""
        body = self.get().content.decode()

        for path in ("/pay/", "/admin/", "/accounts/login", "/accounts/logout"):
            with self.subTest(path=path):
                self.assertIn(path, NEVER_CACHE)
                self.assertIn(f'"{path}"', body)

    def test_tickets_are_cached_outside_the_versioned_caches(self):
        """A deploy must not delete somebody's offline ticket."""
        body = self.get().content.decode()

        self.assertIn('const TICKETS = "ve-tickets"', body)
        self.assertNotIn("ve-tickets-${VERSION}", body)

    def test_signing_out_clears_the_cached_pages(self):
        """Campus devices get shared; a cached ticket must not outlive its session."""
        body = self.get().content.decode()

        self.assertIn("forgetEverythingPrivate", body)
        self.assertIn("/accounts/logout", body)

    def test_the_precache_list_is_not_all_or_nothing(self):
        """One 404 in addAll fails the whole install, taking offline tickets with it."""
        body = self.get().content.decode()

        self.assertIn("allSettled", body)
        self.assertNotIn("cache.addAll", body)


class OfflinePageTests(PWATestCase):
    def test_it_renders_and_points_at_the_tickets(self):
        response = self.client.get(reverse("core:offline"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "offline")
        self.assertContains(response, reverse("accounts:tickets"))

    def test_it_is_not_stored_under_its_own_url(self):
        """The worker precaches it directly; a second copy would only go stale."""
        self.assertEqual(self.client.get(reverse("core:offline"))["Cache-Control"], "no-store")


class OfflineTicketTests(PWATestCase):
    """The QR has to be part of the page, not a second request that can fail."""

    def setUp(self):
        super().setUp()
        self.registration = self.event.register(self.student)
        self.client.force_login(self.student)

    def ticket_page(self):
        return self.client.get(
            reverse("events:ticket", args=[self.registration.ticket_code])
        )

    def test_the_qr_is_inline_rather_than_a_second_request(self):
        response = self.ticket_page()

        self.assertContains(response, "data:image/png;base64,")
        self.assertNotContains(
            response,
            reverse("events:ticket_qr", args=[self.registration.ticket_code]),
            msg_prefix="the ticket page should not depend on the QR endpoint",
        )

    def test_the_inline_code_is_a_real_png(self):
        response = self.ticket_page()
        body = response.content.decode()

        start = body.index("data:image/png;base64,") + len("data:image/png;base64,")
        encoded = body[start : body.index('"', start)]

        import base64

        self.assertTrue(base64.b64decode(encoded).startswith(b"\x89PNG"))

    def test_the_endpoint_and_the_inline_copy_encode_the_same_thing(self):
        """A scanner reading one and a student holding the other must agree."""
        from events import qr

        endpoint = self.client.get(
            reverse("events:ticket_qr", args=[self.registration.ticket_code])
        )

        self.assertEqual(endpoint.status_code, 200)
        self.assertEqual(endpoint["Content-Type"], "image/png")
        self.assertEqual(endpoint.content, qr.png_bytes(self.registration))

    def test_the_code_points_at_the_ticket(self):
        self.assertTrue(payload(self.registration).endswith(self.registration.get_absolute_url()))

    def test_the_payload_does_not_depend_on_the_host_that_served_it(self):
        """A QR gets photographed and printed; what it points at must be stable."""
        from django.test import override_settings

        with override_settings(SITE_BASE_URL="https://varsityevents.app"):
            self.assertTrue(payload(self.registration).startswith("https://varsityevents.app"))

    def test_a_ticket_is_still_private(self):
        """Making it work offline must not have made it readable by anyone."""
        other = User.objects.create_user(username="nosy", email="n@uz.test", password="pw")
        self.client.force_login(other)

        self.assertEqual(self.ticket_page().status_code, 404)

    def test_the_page_is_self_contained(self):
        """Nothing on it should need a network round trip to render the code."""
        body = self.ticket_page().content.decode()

        self.assertIn(self.registration.ticket_code, body)
        self.assertIn("data:image/png;base64,", body)


class InstallabilityTests(PWATestCase):
    def test_every_page_links_the_manifest_and_the_apple_icon(self):
        response = self.client.get(reverse("core:home"))

        self.assertContains(response, 'rel="manifest"')
        self.assertContains(response, "apple-touch-icon")

    def test_every_page_registers_the_worker(self):
        response = self.client.get(reverse("core:home"))

        self.assertContains(response, "serviceWorker")
        self.assertContains(response, "/sw.js")


class QRSizeTests(PWATestCase):
    def test_the_inline_code_stays_small_enough_to_embed(self):
        """It ships with every ticket page; a fat one is paid for on mobile data."""
        registration = self.event.register(self.student)

        self.assertLess(len(data_uri(registration)), 8_000)


class TemplateCommentTests(TestCase):
    """Django's `{# #}` is single-line only.

    Spread one over two lines and it stops being a comment: the text is emitted
    verbatim into the page. Nothing catches it — the template compiles, the view
    returns 200, and `assertContains` on the real content still passes — so it
    ships as developer commentary printed on a student's ticket. It did, once.
    """

    def test_no_template_spreads_a_hash_comment_over_lines(self):
        import re
        from pathlib import Path

        from django.conf import settings

        offenders = []
        for path in Path(settings.BASE_DIR).rglob("*.html"):
            if any(part in path.parts for part in ("node_modules", ".venv", "staticfiles")):
                continue
            text = path.read_text(encoding="utf-8")
            for match in re.finditer(r"\{#(.*?)#\}", text, re.S):
                if "\n" in match.group(1):
                    line = text[: match.start()].count("\n") + 1
                    offenders.append(
                        f"{path.relative_to(settings.BASE_DIR)}:{line} — use "
                        "{% comment %}...{% endcomment %} for more than one line"
                    )

        self.assertEqual(offenders, [], "\n" + "\n".join(offenders))


class TicketRendersNoCommentaryTests(OfflineTicketTests):
    def test_the_ticket_page_shows_no_template_syntax(self):
        """The page a student holds up at a gate, so look at what's on it."""
        body = self.ticket_page().content.decode()

        for leak in ("{#", "#}", "{%", "%}", "{{", "}}"):
            with self.subTest(leak=leak):
                self.assertNotIn(leak, body)


class HeroArtworkTests(TestCase):
    """The homepage picture is generated, so it can be checked rather than eyeballed."""

    def test_it_draws_a_usable_jpeg_at_the_size_asked_for(self):
        from PIL import Image

        from core.imagegen import make_hero

        image = Image.open(make_hero(size=(1200, 500)))

        self.assertEqual(image.size, (1200, 500))
        self.assertEqual(image.format, "JPEG")

    def test_it_is_deterministic(self):
        """Same bytes every run, or committing the output is a rolling diff."""
        from core.imagegen import make_hero

        self.assertEqual(
            make_hero(size=(600, 250)).getvalue(),
            make_hero(size=(600, 250)).getvalue(),
        )

    def test_the_generated_hero_is_committed(self):
        """CI has no Pillow step, so the drawn hero has to be in the repo even
        when a photograph is the one currently in use — swapping back is a
        one-line change and should not need a Pillow install to make good."""
        from django.conf import settings

        path = Path(settings.BASE_DIR) / "static" / "img" / "hero-universities.jpg"
        self.assertTrue(path.exists(), "Run `manage.py make_hero` and commit the result.")

    def test_the_homepage_hero_image_actually_exists_on_disk(self):
        """Asserts the invariant rather than which picture happens to be in.

        The hero has been a stock crowd, a drawn diagram and a festival
        photograph inside a day. What must never change is that the file the
        page asks for is one that ships.
        """
        import re

        from django.conf import settings

        response = self.client.get("/")
        match = re.search(rb'<img src="(/static/img/[^"]+)"', response.content)
        self.assertIsNotNone(match, "The homepage hero has no <img>.")

        name = match.group(1).decode().rsplit("/", 1)[-1]
        path = Path(settings.BASE_DIR) / "static" / "img" / name
        self.assertTrue(path.exists(), f"The homepage asks for {name}, which is not committed.")
