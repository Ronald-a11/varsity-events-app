"""Generate the VAPID key pair Web Push needs.

    python manage.py make_vapid_keys

Both halves come out as single-line base64url, which is what a browser's
`subscribe()` wants for the public key and what fits in an environment variable
for the private one. A PEM would need its newlines escaped into `.env` and
un-escaped again on the way out, which is a footgun for no benefit.

Run this once per deployment and then leave the pair alone. Rotating it
invalidates every subscription anybody has ever granted — a browser ties its
subscription to the key that created it — and a permission, once granted, is
never asked for a second time.
"""

import base64

from cryptography.hazmat.primitives import serialization
from django.core.management.base import BaseCommand


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def generate():
    """Return (public, private), both base64url. Split out so a test can call it."""
    from py_vapid import Vapid01

    vapid = Vapid01()
    vapid.generate_keys()

    # The uncompressed EC point, which is the form applicationServerKey takes.
    public = _b64(
        vapid.public_key.public_bytes(
            encoding=serialization.Encoding.X962,
            format=serialization.PublicFormat.UncompressedPoint,
        )
    )
    # The raw 32-byte scalar. pywebpush accepts this directly.
    private = _b64(vapid.private_key.private_numbers().private_value.to_bytes(32, "big"))

    return public, private


class Command(BaseCommand):
    help = "Generate a VAPID key pair for Web Push."

    def handle(self, *args, **options):
        public, private = generate()

        self.stdout.write(self.style.SUCCESS("VAPID key pair generated.\n"))
        self.stdout.write("Add these to your environment:\n")
        self.stdout.write(f"\n  VAPID_PUBLIC_KEY={public}")
        self.stdout.write(f"  VAPID_PRIVATE_KEY={private}\n")
        self.stdout.write(
            self.style.WARNING(
                "\nThe private half is a secret — Railway variables, never the repo.\n"
            )
        )
        self.stdout.write(
            "Keep the pair stable. Changing it silently breaks every existing\n"
            "subscription, and browsers do not re-ask for a permission already granted.\n"
        )
