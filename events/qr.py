"""The QR code on a ticket.

One module so the image endpoint and the inline copy on the ticket page can
never drift into encoding different things — a door scanner reading one and a
student holding the other is not a difference you want to discover at a gate.

The payload is the ticket's own URL, built from `SITE_BASE_URL` rather than from
the request. A QR is photographed, printed and screenshotted; what it points at
should not depend on which hostname happened to serve the page it was drawn on.
"""

import base64
from io import BytesIO

import qrcode
from django.conf import settings

# Big enough to scan off a cracked phone screen in a dark doorway, small enough
# to inline in the page without bloating it — this comes out around 3KB.
BOX_SIZE = 8
BORDER = 2


def payload(registration) -> str:
    """What the code actually encodes: a link to the ticket."""
    base = settings.SITE_BASE_URL.rstrip("/")
    return f"{base}{registration.get_absolute_url()}"


def png_bytes(registration) -> bytes:
    image = qrcode.make(payload(registration), box_size=BOX_SIZE, border=BORDER)
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def data_uri(registration) -> str:
    """The same PNG, inline.

    This is what makes a ticket work at a gate with no signal. An `<img src>`
    pointing at our own endpoint is a second request that can fail on its own;
    a data URI is part of the page, so whatever cached the page cached the code
    with it.
    """
    encoded = base64.b64encode(png_bytes(registration)).decode("ascii")
    return f"data:image/png;base64,{encoded}"
