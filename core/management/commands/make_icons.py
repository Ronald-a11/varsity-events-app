"""Draw the app icons a home screen needs.

Generated rather than designed, for the same reason `seed_demo` draws its own
artwork: it keeps the repo free of binary assets nobody can regenerate, and the
output is deterministic, so re-running this produces the same bytes and no
spurious diff.

    python manage.py make_icons

Writes into static/img/. Commit what it produces — the production image has no
Node and no Pillow step at build time, exactly as with static/css/app.css.

A maskable icon is drawn separately, with its lettering pulled well inside the
frame: Android crops these to whatever shape the launcher uses, and a design
that fills the square loses its edges to a circle.
"""

from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand
from PIL import Image, ImageDraw, ImageFilter

from core.imagegen import _add_blob, _font, _linear_gradient

# The brand gradient, matching the header wordmark.
INK = (79, 70, 229)
DEEP = (30, 27, 90)
LIGHT = (129, 140, 248)

SIZES = {
    "icon-192.png": (192, "any"),
    "icon-512.png": (512, "any"),
    # Apple ignores the manifest and reads a <link> instead; 180 is what it wants.
    "apple-touch-icon.png": (180, "any"),
    "icon-maskable-512.png": (512, "maskable"),
}


def draw_icon(size: int, purpose: str) -> Image.Image:
    canvas = _linear_gradient((size, size), DEEP, INK).convert("RGBA")

    # Blurred, not a flat disc — _add_blob is what draws the light in every
    # other piece of generated art here, and a hard-edged circle on an icon
    # reads as a mistake rather than as lighting.
    _add_blob(canvas, (size * 0.86, size * 0.10), size * 0.52, LIGHT, 120)
    _add_blob(canvas, (size * 0.08, size * 0.96), size * 0.42, INK, 90)

    draw = ImageDraw.Draw(canvas, "RGBA")

    # Maskable icons are cropped to a circle, a squircle or a rounded square
    # depending on the launcher, and only the middle ~80% is guaranteed to
    # survive. Shrink the lettering rather than lose the edges of it.
    inset = 0.60 if purpose == "maskable" else 0.74
    letters = "VE"
    font = _font(int(size * inset * 0.52))

    box = draw.textbbox((0, 0), letters, font=font)
    width, height = box[2] - box[0], box[3] - box[1]
    position = ((size - width) / 2 - box[0], (size - height) / 2 - box[1])

    # A soft drop shadow, so the letters hold against the lighter corner.
    shadow = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    ImageDraw.Draw(shadow).text(
        (position[0], position[1] + size * 0.018), letters, font=font, fill=(10, 8, 34, 110)
    )
    canvas.alpha_composite(shadow.filter(ImageFilter.GaussianBlur(size * 0.012)))

    draw.text(position, letters, font=font, fill=(255, 255, 255, 255))

    # No border and no rounded corners on purpose. Every platform masks these
    # itself — iOS rounds them, Android crops to whatever the launcher uses — so
    # drawing our own edge only risks a stray arc inside theirs.
    return canvas.convert("RGB")


class Command(BaseCommand):
    help = "Draw the PWA and Apple touch icons into static/img/."

    def handle(self, *args, **options):
        target = Path(settings.BASE_DIR) / "static" / "img"
        target.mkdir(parents=True, exist_ok=True)

        for name, (size, purpose) in SIZES.items():
            path = target / name
            draw_icon(size, purpose).save(path, format="PNG", optimize=True)
            self.stdout.write(f"  {name:<26} {size}×{size}  {path.stat().st_size // 1024} KB")

        self.stdout.write(
            self.style.SUCCESS(f"Wrote {len(SIZES)} icons to {target}. Commit them.")
        )
