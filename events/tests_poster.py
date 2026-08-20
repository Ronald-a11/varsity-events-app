"""Reading an event off its poster.

Every call to the model is mocked. What matters here is who may reach the
route, what happens to what comes back, and — most of all — that nothing gets
published without a person seeing it first.
"""

import json
from datetime import timedelta
from unittest.mock import MagicMock, patch

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from accounts.models import University, User
from events import poster, poster_import
from events.models import Event, Venue
from organizations.models import Membership, Organization

WITH_KEY = override_settings(ANTHROPIC_API_KEY="sk-ant-test", ANTHROPIC_MODEL="claude-opus-5")

# A 1x1 PNG. The bytes never reach a model — they only have to survive upload.
PNG = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06"
    b"\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00\x01\x00\x00\x05"
    b"\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
)


def read_result(**overrides):
    """What a poster read comes back as, before anything maps it."""
    base = {
        "title": "Midnight Jazz Party",
        "summary": "A late night of live music",
        "description": "Bring your friends.",
        "starts_at": (timezone.localtime() + timedelta(days=10)).strftime("%Y-%m-%dT18:00"),
        "ends_at": None,
        "venue_name": "Beit Hall",
        "university": "UZ",
        "organizer": "Jazz Society",
        "is_free": False,
        "price": 5,
        "currency": "USD",
        "tags": ["music", "jazz"],
        "confidence": "high",
        "notes": None,
    }
    base.update(overrides)
    return base


def fake_response(payload, input_tokens=1800, output_tokens=250):
    block = MagicMock()
    block.type = "text"
    block.text = json.dumps(payload)
    response = MagicMock()
    response.content = [block]
    response.usage.input_tokens = input_tokens
    response.usage.output_tokens = output_tokens
    return response


class PosterTestCase(TestCase):
    def setUp(self):
        self.uz = University.objects.create(name="University of Zimbabwe", short_name="UZ")
        self.organizer = User.objects.create_user(
            username="organizer", email="o@uz.test", password="pw", role=User.Role.ORGANIZER
        )
        self.student = User.objects.create_user(
            username="student", email="s@uz.test", password="pw", university=self.uz
        )
        self.org = Organization.objects.create(
            name="Jazz Society", created_by=self.organizer, university=self.uz
        )
        Membership.objects.create(
            organization=self.org, user=self.organizer, role=Membership.Role.OWNER
        )
        self.hall = Venue.objects.create(name="Beit Hall", university=self.uz)

    def upload(self, name="poster.png", content=PNG, content_type="image/png"):
        return SimpleUploadedFile(name, content, content_type=content_type)


class AccessTests(PosterTestCase):
    """Organizers and staff only — the people who could publish this by typing."""

    @WITH_KEY
    def test_signing_in_is_required(self):
        response = self.client.get(reverse("events:from_poster"))

        self.assertEqual(response.status_code, 302)
        self.assertIn("login", response["Location"])

    @WITH_KEY
    def test_a_student_is_sent_away(self):
        """A student runs no society, so there is nothing for them to publish."""
        self.client.force_login(self.student)

        response = self.client.get(reverse("events:from_poster"))

        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("organizations:create"), response["Location"])

    @WITH_KEY
    def test_an_organizer_gets_the_page(self):
        self.client.force_login(self.organizer)

        response = self.client.get(reverse("events:from_poster"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Start from a poster")

    def test_without_a_key_the_route_stands_aside(self):
        """No key, no feature — and the ordinary form is still right there."""
        self.client.force_login(self.organizer)

        response = self.client.get(reverse("events:from_poster"))

        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("events:create"), response["Location"])

    def test_without_a_key_nothing_advertises_it(self):
        """A link to a route that redirects away is worse than no link."""
        self.client.force_login(self.organizer)

        response = self.client.get(reverse("events:create"))

        self.assertNotContains(response, reverse("events:from_poster"))

    @WITH_KEY
    def test_with_a_key_the_create_form_offers_it(self):
        self.client.force_login(self.organizer)

        response = self.client.get(reverse("events:create"))

        self.assertContains(response, reverse("events:from_poster"))


@WITH_KEY
class ReadingTests(PosterTestCase):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.organizer)

    def post_poster(self, payload=None, **upload_kwargs):
        with patch("anthropic.Anthropic") as client:
            client.return_value.messages.create.return_value = fake_response(
                payload if payload is not None else read_result()
            )
            return self.client.post(
                reverse("events:from_poster"), {"poster": self.upload(**upload_kwargs)}
            )

    def test_a_read_poster_comes_back_as_a_filled_in_form(self):
        response = self.post_poster()

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Midnight Jazz Party")
        self.assertContains(response, "A late night of live music")

    def test_nothing_is_saved(self):
        """The whole safety story: extraction never writes an event."""
        self.post_poster()

        self.assertEqual(Event.objects.count(), 0)

    def test_the_society_is_matched_so_they_do_not_have_to_pick_it(self):
        response = self.post_poster()

        self.assertEqual(response.context["form"].initial["organization"], self.org.pk)

    def test_a_known_venue_is_matched(self):
        response = self.post_poster()

        self.assertEqual(response.context["form"].initial["venue"], self.hall.pk)

    def test_an_unknown_venue_is_kept_as_a_note_rather_than_dropped(self):
        response = self.post_poster(read_result(venue_name="Somebody's Back Garden"))

        self.assertEqual(
            response.context["form"].initial["location_note"], "Somebody's Back Garden"
        )

    def test_choosing_no_file_says_so(self):
        response = self.client.post(reverse("events:from_poster"), {})

        self.assertContains(response, "Choose a poster image first")

    def test_a_model_failure_is_explained_not_swallowed(self):
        with patch("events.poster.read_poster", side_effect=poster.PosterError("Couldn't read that poster.")):
            response = self.client.post(
                reverse("events:from_poster"), {"poster": self.upload()}
            )

        self.assertContains(response, "Couldn&#x27;t read that poster")
        self.assertEqual(Event.objects.count(), 0)


@WITH_KEY
class WarningTests(PosterTestCase):
    """What the poster didn't say has to be visible, not silently defaulted."""

    def setUp(self):
        super().setUp()
        self.client.force_login(self.organizer)

    def warnings_for(self, payload):
        with patch("anthropic.Anthropic") as client:
            client.return_value.messages.create.return_value = fake_response(payload)
            response = self.client.post(
                reverse("events:from_poster"), {"poster": self.upload()}
            )
        return response.context["poster_warnings"]

    def test_a_missing_date_is_flagged(self):
        warnings = self.warnings_for(read_result(starts_at=None))

        self.assertTrue(any("couldn't read a date" in w.lower() for w in warnings))

    def test_an_assumed_end_time_is_admitted_to(self):
        warnings = self.warnings_for(read_result(ends_at=None))

        self.assertTrue(any("assumed three hours" in w for w in warnings))

    def test_a_date_in_the_past_is_flagged_as_a_likely_wrong_year(self):
        """Posters print 'Friday 21 August' with no year, and get it wrong."""
        stale = (timezone.localtime() - timedelta(days=40)).strftime("%Y-%m-%dT18:00")

        warnings = self.warnings_for(read_result(starts_at=stale))

        self.assertTrue(any("in the past" in w for w in warnings))

    def test_a_low_confidence_read_says_so_first(self):
        warnings = self.warnings_for(read_result(confidence="low"))

        self.assertIn("hard to read", warnings[0])

    def test_an_unmatched_society_asks_them_to_pick(self):
        warnings = self.warnings_for(read_result(organizer="Some Other Club"))

        self.assertTrue(any("Some Other Club" in w for w in warnings))

    def test_a_silent_price_is_flagged_rather_than_assumed_paid(self):
        warnings = self.warnings_for(read_result(is_free=None, price=None))

        self.assertTrue(any("didn't mention price" in w for w in warnings))


class MappingTests(PosterTestCase):
    """poster_import on its own — no view, no model, no network."""

    def test_a_society_matches_despite_the_poster_adding_the_university(self):
        matched = poster_import.match_organization(
            "UZ Jazz Society", Organization.objects.all()
        )
        self.assertEqual(matched, self.org)

    def test_matching_is_case_insensitive(self):
        self.assertEqual(
            poster_import.match_organization("jazz society", Organization.objects.all()),
            self.org,
        )

    def test_an_ambiguous_name_matches_nothing(self):
        """Two candidates means we must not choose; the field is a dropdown."""
        Organization.objects.create(
            name="Jazz Society Bulawayo", created_by=self.organizer, university=self.uz
        )

        # "Jazz" is inside both names, so there is no honest answer here.
        self.assertIsNone(
            poster_import.match_organization("Jazz", Organization.objects.all())
        )

    def test_a_longer_poster_credit_still_resolves_to_the_one_society(self):
        """"UZ Jazz Society" contains "Jazz Society" and nothing else matches."""
        Organization.objects.create(
            name="Debating Union", created_by=self.organizer, university=self.uz
        )

        self.assertEqual(
            poster_import.match_organization("UZ Jazz Society", Organization.objects.all()),
            self.org,
        )

    def test_a_missing_name_matches_nothing(self):
        for value in (None, "", "   "):
            with self.subTest(value=value):
                self.assertIsNone(
                    poster_import.match_organization(value, Organization.objects.all())
                )

    def test_a_nonsense_date_is_dropped_not_guessed(self):
        initial, warnings = poster_import.to_form_initial(
            read_result(starts_at="next Friday sometime"), Organization.objects.all()
        )

        self.assertNotIn("starts_at", initial)
        self.assertTrue(any("couldn't read a date" in w.lower() for w in warnings))

    def test_a_negative_price_is_ignored(self):
        initial, _ = poster_import.to_form_initial(
            read_result(is_free=False, price=-20), Organization.objects.all()
        )

        self.assertNotIn("price", initial)

    def test_a_free_poster_maps_to_free(self):
        initial, _ = poster_import.to_form_initial(
            read_result(is_free=True, price=None), Organization.objects.all()
        )

        self.assertTrue(initial["is_free"])

    def test_only_currencies_we_take_are_accepted(self):
        initial, _ = poster_import.to_form_initial(
            read_result(currency="GBP"), Organization.objects.all()
        )

        self.assertNotIn("currency", initial)


class ExtractionGuardTests(TestCase):
    """events/poster.py refuses obvious rubbish before spending a model call."""

    @WITH_KEY
    def test_a_non_image_is_refused(self):
        with self.assertRaises(poster.PosterError):
            poster.read_poster(b"%PDF-1.4", "application/pdf")

    @WITH_KEY
    def test_an_oversized_image_is_refused(self):
        with self.assertRaises(poster.PosterError) as caught:
            poster.read_poster(b"x" * (poster.MAX_BYTES + 1), "image/png")

        self.assertIn("5 MB", str(caught.exception))

    @WITH_KEY
    def test_an_empty_file_is_refused(self):
        with self.assertRaises(poster.PosterError):
            poster.read_poster(b"", "image/png")

    def test_without_a_key_it_refuses_rather_than_calling_out(self):
        with self.assertRaises(poster.PosterError):
            poster.read_poster(PNG, "image/png")

    @WITH_KEY
    def test_the_schema_forbids_inventing_fields(self):
        """additionalProperties False, and every field nullable so it can say 'I don't know'."""
        self.assertFalse(poster.SCHEMA["additionalProperties"])
        for field in ("title", "starts_at", "venue_name", "price"):
            with self.subTest(field=field):
                self.assertIn("null", poster.SCHEMA["properties"][field]["type"])

    @WITH_KEY
    def test_it_asks_for_the_configured_model(self):
        with patch("anthropic.Anthropic") as client:
            client.return_value.messages.create.return_value = fake_response(read_result())
            poster.read_poster(PNG, "image/png")

        kwargs = client.return_value.messages.create.call_args.kwargs
        self.assertEqual(kwargs["model"], "claude-opus-5")
        self.assertEqual(
            kwargs["output_config"]["format"]["schema"]["additionalProperties"], False
        )
