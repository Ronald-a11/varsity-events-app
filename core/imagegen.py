"""Generate the sample imagery the demo data needs.

Drawing these locally rather than fetching stock photos keeps `seed_demo`
reproducible, offline and free of licensing questions. The output is abstract —
layered gradients, soft light blobs and a faint grid — which reads as deliberate
art direction behind the card text rather than a failed photo.

Everything is seeded off the event title, so the same event always gets the same
banner across reseeds.
"""

from __future__ import annotations

import hashlib
import io
import math
import random

from PIL import Image, ImageDraw, ImageFilter, ImageFont

# Palettes keyed by the category colour names the app already uses. Each is a
# (dark, mid, accent) triple that sits comfortably under white overlay text.
PALETTES = {
    "brand": ((26, 27, 51), (61, 65, 137), (139, 148, 220)),
    "azure": ((17, 39, 53), (51, 102, 138), (143, 189, 214)),
    "flame": ((60, 24, 10), (194, 65, 12), (253, 177, 116)),
    "emerald": ((10, 42, 32), (16, 122, 90), (110, 231, 183)),
    "amber": ((60, 38, 8), (180, 105, 15), (252, 211, 141)),
    "rose": ((60, 16, 32), (190, 45, 85), (253, 164, 190)),
    "violet": ((38, 20, 62), (109, 62, 187), (196, 168, 250)),
    "teal": ((11, 44, 45), (17, 122, 122), (110, 226, 219)),
    "sky": ((13, 36, 58), (25, 106, 168), (143, 200, 245)),
    "orange": ((58, 28, 8), (200, 90, 12), (253, 191, 130)),
    "slate": ((24, 26, 40), (68, 74, 105), (160, 168, 196)),
}

FONT_CANDIDATES = [
    "C:/Windows/Fonts/segoeuib.ttf",
    "C:/Windows/Fonts/arialbd.ttf",
    "C:/Windows/Fonts/calibrib.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
]


def _rng(seed: str) -> random.Random:
    """Deterministic per-subject randomness, stable across runs and machines."""
    digest = hashlib.sha256(seed.encode("utf-8")).hexdigest()
    return random.Random(int(digest[:16], 16))


def _palette(name: str, seed: str):
    if name in PALETTES:
        return PALETTES[name]
    # Unknown colour name: pick one consistently rather than always defaulting.
    keys = sorted(PALETTES)
    return PALETTES[keys[_rng(seed).randrange(len(keys))]]


def _font(size: int):
    for path in FONT_CANDIDATES:
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            continue
    return ImageFont.load_default()


def _linear_gradient(size, top, bottom, angle_shift=0.0):
    """Vertical gradient, optionally tilted.

    A tilt is drawn oversized and cropped back — rotating in place would fill
    the corners with black, which shows up as wedges along every edge.
    """
    width, height = size
    margin = 1.7 if angle_shift else 1.0
    build = (int(width * margin), int(height * margin))

    strip = Image.new("RGB", (1, build[1]))
    draw = ImageDraw.Draw(strip)
    for y in range(build[1]):
        t = y / max(build[1] - 1, 1)
        draw.point((0, y), fill=tuple(int(top[i] + (bottom[i] - top[i]) * t) for i in range(3)))

    gradient = strip.resize(build, Image.BILINEAR)

    if angle_shift:
        gradient = gradient.rotate(angle_shift, resample=Image.BICUBIC, expand=False)
        left = (build[0] - width) // 2
        top_edge = (build[1] - height) // 2
        gradient = gradient.crop((left, top_edge, left + width, top_edge + height))

    return gradient


def _add_blob(canvas, center, radius, colour, alpha):
    """A soft pool of light, the way a blurred spotlight falls."""
    layer = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    x, y = center
    draw.ellipse([x - radius, y - radius, x + radius, y + radius], fill=(*colour, alpha))
    layer = layer.filter(ImageFilter.GaussianBlur(radius * 0.55))
    canvas.alpha_composite(layer)


def _add_grid(canvas, spacing=48, alpha=16):
    layer = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    width, height = canvas.size

    for x in range(0, width, spacing):
        draw.line([(x, 0), (x, height)], fill=(255, 255, 255, alpha))
    for y in range(0, height, spacing):
        draw.line([(0, y), (width, y)], fill=(255, 255, 255, alpha))

    canvas.alpha_composite(layer)


def _add_bands(canvas, rng, colour, count=3):
    """Bold diagonal bands. Edges stay crisp so they read as deliberate shapes."""
    layer = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    width, height = canvas.size
    angle = rng.uniform(-0.9, -0.35)  # consistent slope across one image

    for _ in range(count):
        start = rng.uniform(-0.4, 1.0) * width
        thickness = rng.uniform(0.05, 0.16) * width
        reach = height / math.tan(abs(angle)) if angle else width
        draw.polygon(
            [
                (start, height),
                (start + thickness, height),
                (start + thickness + reach, 0),
                (start + reach, 0),
            ],
            fill=(*colour, rng.randint(28, 55)),
        )

    canvas.alpha_composite(layer.filter(ImageFilter.GaussianBlur(2)))


def _add_rings(canvas, rng, colour, count=2):
    """Thin concentric arcs — a bit of structure without adding noise."""
    layer = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    width, height = canvas.size
    cx, cy = rng.uniform(0.55, 1.05) * width, rng.uniform(-0.15, 0.45) * height

    for i in range(count):
        r = (0.28 + i * 0.16) * width
        draw.ellipse([cx - r, cy - r, cx + r, cy + r], outline=(*colour, 60), width=3)

    canvas.alpha_composite(layer.filter(ImageFilter.GaussianBlur(1.2)))


def _add_vignette(canvas, strength=90):
    """Darken the corners so overlaid white text always has something to sit on."""
    width, height = canvas.size
    mask = Image.new("L", (width, height), 0)
    ImageDraw.Draw(mask).ellipse(
        [-width * 0.25, -height * 0.35, width * 1.25, height * 1.35], fill=255
    )
    mask = mask.filter(ImageFilter.GaussianBlur(min(width, height) * 0.22))

    shade = Image.new("RGBA", (width, height), (8, 8, 20, strength))
    shade.putalpha(Image.eval(mask, lambda v: int(strength * (1 - v / 255))))
    canvas.alpha_composite(shade)


def make_banner(seed: str, colour: str = "brand", size=(1200, 675)) -> io.BytesIO:
    """A 16:9 event banner built from bold, legible abstract shapes."""
    rng = _rng(seed)
    dark, mid, accent = _palette(colour, seed)

    # Start bright: a vivid diagonal wash, so the art has somewhere to fall from.
    canvas = _linear_gradient(size, accent, mid, angle_shift=rng.uniform(-8, 8)).convert("RGBA")
    width, height = size

    # Deepen one corner rather than flattening the whole frame.
    _add_blob(canvas, (rng.choice([0.05, 0.95]) * width, 1.05 * height), width * 0.85, dark, 190)

    _add_bands(canvas, rng, (255, 255, 255), count=rng.randint(2, 4))
    _add_rings(canvas, rng, (255, 255, 255), count=rng.randint(1, 3))

    # A couple of bright pools to lift the midtones.
    for _ in range(rng.randint(2, 3)):
        _add_blob(
            canvas,
            (rng.uniform(0.1, 0.9) * width, rng.uniform(0.0, 0.7) * height),
            rng.uniform(0.16, 0.3) * width,
            (255, 255, 255) if rng.random() < 0.5 else accent,
            rng.randint(50, 90),
        )

    _add_grid(canvas, spacing=rng.choice([48, 60, 72]), alpha=rng.randint(10, 18))
    _add_vignette(canvas, strength=rng.randint(70, 110))

    buffer = io.BytesIO()
    canvas.convert("RGB").save(buffer, format="JPEG", quality=88, optimize=True)
    buffer.seek(0)
    return buffer


def make_cover(seed: str, colour: str = "brand", size=(1600, 480)) -> io.BytesIO:
    """A wide, calmer version for society cover images."""
    rng = _rng(seed + "cover")
    dark, mid, accent = _palette(colour, seed)

    canvas = _linear_gradient(size, mid, dark, angle_shift=rng.uniform(-3, 3)).convert("RGBA")
    width, height = size

    for _ in range(rng.randint(2, 3)):
        _add_blob(
            canvas,
            (rng.uniform(0.0, 1.0) * width, rng.uniform(0.1, 0.9) * height),
            rng.uniform(0.15, 0.3) * width,
            accent,
            rng.randint(30, 55),
        )

    _add_grid(canvas, spacing=56, alpha=12)

    buffer = io.BytesIO()
    canvas.convert("RGB").save(buffer, format="JPEG", quality=86, optimize=True)
    buffer.seek(0)
    return buffer


def make_logo(initials: str, seed: str, colour: str = "brand", size=256) -> io.BytesIO:
    """A rounded-square society mark with the initials set in it."""
    rng = _rng(seed + "logo")
    dark, mid, accent = _palette(colour, seed)

    canvas = _linear_gradient((size, size), accent, mid).convert("RGBA")
    _add_blob(
        canvas,
        (rng.uniform(0.1, 0.9) * size, rng.uniform(0.1, 0.6) * size),
        size * 0.45,
        (255, 255, 255),
        40,
    )

    # Round the corners with an alpha mask.
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, size - 1, size - 1], radius=int(size * 0.22), fill=255)
    canvas.putalpha(mask)

    text = (initials or "?")[:2].upper()
    draw = ImageDraw.Draw(canvas)
    font = _font(int(size * 0.42))
    left, top, right, bottom = draw.textbbox((0, 0), text, font=font)
    draw.text(
        ((size - (right - left)) / 2 - left, (size - (bottom - top)) / 2 - top),
        text,
        font=font,
        fill=(255, 255, 255, 235),
    )

    buffer = io.BytesIO()
    canvas.save(buffer, format="PNG", optimize=True)
    buffer.seek(0)
    return buffer
