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


# The eighteen, and the eight kinds of thing they put on. Both are real: the
# abbreviations students actually use, and the category colours the app already
# files events under. The picture is only worth drawing if it is about this
# platform rather than about events in general.
UNIVERSITY_MARKS = [
    "UZ", "NUST", "MSU", "CUT", "GZU", "HIT", "BUSE", "LSU", "AU",
    "ZOU", "WUA", "SOLUSI", "CUZ", "MSUAS", "GSU", "MUAST", "ZEGU", "RCU",
]

CATEGORY_COLOURS = ["brand", "azure", "violet", "emerald", "amber", "rose", "teal", "orange"]


def _curve(start, end, bend, steps=64):
    """Points along a quadratic bezier, for a line that arcs rather than points.

    Pillow has no curve primitive, so the control point is computed off the
    midpoint's perpendicular and the curve is drawn as a polyline. Straight
    lines converging on a point read as a diagram; arcs read as movement.
    """
    (x0, y0), (x2, y2) = start, end
    mx, my = (x0 + x2) / 2, (y0 + y2) / 2
    dx, dy = x2 - x0, y2 - y0
    length = math.hypot(dx, dy) or 1
    # Perpendicular offset, so every arc bows the same way round the hub.
    cx, cy = mx - dy / length * bend, my + dx / length * bend

    points = []
    for i in range(steps + 1):
        t = i / steps
        u = 1 - t
        points.append(
            (
                u * u * x0 + 2 * u * t * cx + t * t * x2,
                u * u * y0 + 2 * u * t * cy + t * t * y2,
            )
        )
    return points


def _glow(canvas, center, radius, colour, alpha, layers=4):
    """A round pool of light built from stacked passes.

    One ellipse at high alpha blows out into a hard-edged square once JPEG has
    had it. Several softer passes at increasing radius fall off the way real
    light does and survive compression.
    """
    for step in range(layers):
        scale = 1 + step * 0.9
        _add_blob(
            canvas,
            center,
            int(radius * scale),
            colour,
            max(8, int(alpha / (step + 1) ** 1.5)),
        )


def make_hero(size=(2400, 1000), seed: str = "varsity-events-hero") -> io.BytesIO:
    """The homepage picture: every campus, every kind of event, one place.

    Deliberately a diagram rather than a photograph. A photograph of a crowd at
    a gig says "music night", and this platform is also careers fairs, moot
    courts, blood drives and robotics showcases — the one thing every event on
    it has in common is that it ends up here. So: eighteen labelled nodes in
    the eight category colours, each arcing down into a single lit hub.

    Drawn rather than sourced, for the reason the rest of this module exists.
    It is reproducible, it costs nothing, and there is no licence to honour and
    no photograph of somebody else's campus standing in for a Zimbabwean one.

    Composed for type on top: the nodes sit high, the hub sits low, and the
    band across the middle carries only the curves, which is where the headline
    lands.

    Drawn at 2.4:1 with everything held inside the middle 80% and the inner
    80% across. A hero is `object-cover`, so the frame is cropped to whatever
    the viewport is, and anything closer to an edge than that is a label some
    visitor never sees.
    """
    width, height = size
    rng = _rng(seed)

    canvas = _linear_gradient(size, (18, 19, 43), (35, 36, 78)).convert("RGBA")
    _add_grid(canvas, spacing=int(width / 26), alpha=9)

    hub = (width * 0.5, height * 0.86)

    _add_blob(canvas, (width * 0.12, height * 0.18), int(width * 0.24), (79, 86, 168), 85)
    _add_blob(canvas, (width * 0.90, height * 0.24), int(width * 0.22), (65, 128, 166), 75)
    _add_blob(canvas, (width * 0.50, height * 0.52), int(width * 0.26), (61, 65, 137), 55)

    # Two rows, the lower one inset, so eighteen labels fit without colliding
    # and the fan reads as depth rather than as a single rank.
    nodes = []
    count = len(UNIVERSITY_MARKS)
    for index, mark in enumerate(UNIVERSITY_MARKS):
        row = index % 2
        across = (index + 0.5) / count
        # The lower row is pulled toward the middle; the upper row spans wider.
        spread = 0.80 if row == 0 else 0.60
        x = width * (0.5 + (across - 0.5) * spread) + rng.uniform(-12, 12)
        # Both rows sit above where a centred headline lands. At 0.19/0.35 the
        # lower row's labels ran straight through the words, which reads as
        # noise rather than as artwork behind type.
        y = height * (0.13 + row * 0.13) + rng.uniform(-14, 14)
        colour = _palette(CATEGORY_COLOURS[index % len(CATEGORY_COLOURS)], mark)[2]
        nodes.append({"mark": mark, "pos": (x, y), "colour": colour})

    # The arcs, drawn segment by segment so each one brightens as it approaches
    # the hub. A line of constant alpha reads as a wiring diagram; one that
    # gathers light reads as everything arriving somewhere.
    lines = Image.new("RGBA", size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(lines)
    thickness = max(2, width // 800)

    for node in nodes:
        x, _ = node["pos"]
        # Bend is capped rather than proportional: scaling it with distance
        # threw the outermost arcs clean off the canvas, which is why half the
        # universities appeared to have no line at all.
        offset = (x - hub[0]) / (width * 0.5)
        bend = max(-1.0, min(1.0, offset)) * height * 0.16

        points = _curve(node["pos"], hub, bend, steps=72)
        for i in range(len(points) - 1):
            t = i / (len(points) - 1)
            # Starts bright enough to be seen from the far edge of the frame:
            # at 40 the first two-thirds of the longest arcs were invisible,
            # which read as half the universities not being connected at all.
            alpha = int(75 + 165 * (t ** 1.35))
            draw.line(
                [points[i], points[i + 1]],
                fill=(*node["colour"], alpha),
                width=thickness,
            )

        # A few events in transit, so the middle of the frame has something in
        # it besides empty curve.
        for _ in range(rng.randint(1, 3)):
            t = rng.uniform(0.25, 0.85)
            px, py = points[int(t * (len(points) - 1))]
            r = thickness * rng.uniform(1.1, 2.0)
            draw.ellipse([px - r, py - r, px + r, py + r], fill=(*node["colour"], 190))

    lines = lines.filter(ImageFilter.GaussianBlur(width / 1600))
    canvas.alpha_composite(lines)

    # The hub, before the nodes, so node halos stay crisp over it.
    _glow(canvas, hub, int(width * 0.035), (249, 140, 40), 120, layers=5)
    _glow(canvas, hub, int(width * 0.014), (255, 232, 205), 150, layers=3)

    rings = Image.new("RGBA", size, (0, 0, 0, 0))
    ring_draw = ImageDraw.Draw(rings)
    for step, spread in enumerate((0.075, 0.115, 0.16)):
        r = width * spread
        ring_draw.ellipse(
            [hub[0] - r, hub[1] - r, hub[0] + r, hub[1] + r],
            outline=(253, 186, 116, 70 - step * 18),
            width=max(1, width // 1100),
        )
    canvas.alpha_composite(rings.filter(ImageFilter.GaussianBlur(width / 1500)))

    # Nodes and their labels last, so nothing washes over them.
    label_font = _font(max(13, width // 100))
    marks = Image.new("RGBA", size, (0, 0, 0, 0))
    mark_draw = ImageDraw.Draw(marks)

    for node in nodes:
        x, y = node["pos"]
        colour = node["colour"]
        radius = width / 230

        _glow(canvas, (x, y), int(radius * 3), colour, 90, layers=3)
        mark_draw.ellipse([x - radius, y - radius, x + radius, y + radius], fill=(*colour, 255))
        mark_draw.ellipse(
            [x - radius * 2.2, y - radius * 2.2, x + radius * 2.2, y + radius * 2.2],
            outline=(*colour, 120),
            width=max(1, width // 1400),
        )

        text = node["mark"]
        text_width = mark_draw.textlength(text, font=label_font)
        mark_draw.text(
            (x - text_width / 2, y + radius * 3.2),
            text,
            font=label_font,
            # Quiet enough to stay behind the headline rather than compete
            # with it. These are texture; the words are the message.
            fill=(255, 255, 255, 135),
        )

    canvas.alpha_composite(marks)
    _add_vignette(canvas, strength=95)

    buffer = io.BytesIO()
    canvas.convert("RGB").save(buffer, format="JPEG", quality=88, optimize=True)
    buffer.seek(0)
    return buffer
