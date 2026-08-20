"""Reading an event off its poster.

A campus event in Zimbabwe *is* a poster — a JPEG that went round a WhatsApp
group. The society already has it; what they don't have is the patience to
retype it into a twenty-field form. So this reads the poster and hands them a
form that is already filled in, leaving them to check it rather than write it.

Extraction only. Nothing here saves anything: the organizer still reviews every
field and presses the button, and the event still goes through the same
validation and the same moderation queue as one typed by hand. A model that
misreads "7pm" as "7am" must cost somebody ten seconds, not sell a hundred
tickets to the wrong time.

Inert without ANTHROPIC_API_KEY. The upload route hides itself, and the ordinary
create-an-event form is untouched — the same rule the rest of this app's
infrastructure follows.
"""

import base64
import json
import logging

from django.conf import settings
from django.utils import timezone

logger = logging.getLogger(__name__)

# Posters are photographs of print, or exports from Canva. Both are big; neither
# needs to be. Anything past this is refused rather than silently truncated.
MAX_BYTES = 5 * 1024 * 1024

ACCEPTED_TYPES = {
    "image/jpeg": "image/jpeg",
    "image/jpg": "image/jpeg",
    "image/png": "image/png",
    "image/webp": "image/webp",
    "image/gif": "image/gif",
}

# Every field is required by the schema but nullable in type, so the model has a
# way of saying "the poster doesn't tell me" instead of inventing something. A
# guessed venue is worse than a blank one: blank gets filled in, wrong gets
# published.
SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": ["string", "null"], "description": "The event's name, as printed."},
        "summary": {
            "type": ["string", "null"],
            "description": "One line, under 140 characters, in the poster's own words.",
        },
        "description": {
            "type": ["string", "null"],
            "description": "Any further detail the poster gives: line-up, dress code, what to bring.",
        },
        "starts_at": {
            "type": ["string", "null"],
            "description": "Local start, ISO 8601 without a timezone, e.g. 2026-08-21T18:00. Null if the poster gives no date or no year.",
        },
        "ends_at": {
            "type": ["string", "null"],
            "description": "Local end, same format. Null unless the poster actually states an end time.",
        },
        "venue_name": {
            "type": ["string", "null"],
            "description": "The place, exactly as printed, e.g. 'Beit Hall' or 'Student Union Grounds'.",
        },
        "university": {
            "type": ["string", "null"],
            "description": "University name or abbreviation if the poster shows one, e.g. UZ, NUST, MSU.",
        },
        "organizer": {
            "type": ["string", "null"],
            "description": "The society or club putting it on, as printed.",
        },
        "is_free": {
            "type": ["boolean", "null"],
            "description": "True only if the poster says free entry. Null if it says nothing about price.",
        },
        "price": {
            "type": ["number", "null"],
            "description": "Entry price as a number, if one is printed.",
        },
        "currency": {
            "type": ["string", "null"],
            "description": "USD or ZWG, whichever the poster shows.",
        },
        "tags": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Two to five lowercase keywords, e.g. music, careers, sport.",
        },
        "confidence": {
            "type": "string",
            "enum": ["high", "medium", "low"],
            "description": "How legible the poster was. Low means the organizer should check every field.",
        },
        "notes": {
            "type": ["string", "null"],
            "description": "Anything ambiguous worth flagging to the person reviewing.",
        },
    },
    "required": [
        "title", "summary", "description", "starts_at", "ends_at", "venue_name",
        "university", "organizer", "is_free", "price", "currency", "tags",
        "confidence", "notes",
    ],
    "additionalProperties": False,
}


def is_configured() -> bool:
    """Whether poster reading can work at all."""
    return bool(getattr(settings, "ANTHROPIC_API_KEY", ""))


def _prompt() -> str:
    # The current year, because posters routinely print "Friday 21 August" with
    # no year at all and the model has no other way to resolve it.
    today = timezone.localdate()
    return (
        "You are reading a poster for a university event in Zimbabwe, so that its "
        "organizer can check the details rather than retype them.\n\n"
        f"Today is {today.isoformat()}. Posters often omit the year — assume the next "
        "occurrence of the date shown, which is usually this year.\n\n"
        "Transcribe, do not embellish. Use the poster's own wording. Where the poster "
        "does not say something, return null for that field: a blank the organizer "
        "fills in is far better than a plausible guess they fail to notice.\n\n"
        "Times are local (Africa/Harare). Do not convert them. If only a start time is "
        "printed, leave ends_at null.\n\n"
        "Set confidence to low if the image is blurred, cropped, at an angle, or if you "
        "are reading the key details with any difficulty at all."
    )


def read_poster(image_bytes: bytes, content_type: str) -> dict:
    """Pull event details off a poster image.

    Returns the parsed fields. Raises PosterError with something worth showing a
    person for anything that goes wrong.
    """
    if not is_configured():
        raise PosterError("Poster reading isn't set up on this deployment.")

    media_type = ACCEPTED_TYPES.get((content_type or "").lower())
    if media_type is None:
        raise PosterError("That file isn't an image we can read. Try a JPEG or PNG.")

    if len(image_bytes) > MAX_BYTES:
        raise PosterError("That image is over 5 MB. Send a smaller copy of the poster.")
    if not image_bytes:
        raise PosterError("That file is empty.")

    import anthropic

    client = anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY, timeout=60.0)

    try:
        response = client.messages.create(
            model=settings.ANTHROPIC_MODEL,
            max_tokens=2000,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": media_type,
                                "data": base64.standard_b64encode(image_bytes).decode("ascii"),
                            },
                        },
                        {"type": "text", "text": _prompt()},
                    ],
                }
            ],
            output_config={"format": {"type": "json_schema", "schema": SCHEMA}},
        )
    except anthropic.RateLimitError:
        raise PosterError("Busy right now — give it a few seconds and try again.")
    except anthropic.APIConnectionError:
        raise PosterError("Couldn't reach the reader. Check the connection and retry.")
    except anthropic.APIStatusError as exc:
        logger.exception("Poster read failed", extra={"status": exc.status_code})
        raise PosterError("Couldn't read that poster. Fill the form in by hand instead.")

    # output_config.format guarantees the first text block is valid JSON.
    try:
        text = next(block.text for block in response.content if block.type == "text")
        data = json.loads(text)
    except (StopIteration, json.JSONDecodeError):
        logger.exception("Poster read returned nothing usable")
        raise PosterError("Couldn't read that poster. Fill the form in by hand instead.")

    logger.info(
        "Read a poster",
        extra={
            "confidence": data.get("confidence"),
            "input_tokens": response.usage.input_tokens,
            "output_tokens": response.usage.output_tokens,
        },
    )
    return data


class PosterError(Exception):
    """Something a person can act on, safe to show them."""
