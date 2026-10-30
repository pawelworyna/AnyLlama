"""Shared llama geometry for the dashboard SVG, Windows tray and executable icon."""

from pathlib import Path

from PIL import Image, ImageDraw

PURPLE = "#68217A"
INK = "#5C2D91"
LAVENDER = "#D8B7ED"
WHITE = "#FFFFFF"

# Coordinates use the same 48 × 48 canvas in both renderers.
FACE = [
    (13, 25),
    (13, 22),
    (15, 19),
    (12, 9),
    (15, 7),
    (20, 17),
    (22, 14),
    (24, 16),
    (26, 14),
    (28, 17),
    (33, 7),
    (36, 9),
    (33, 19),
    (35, 22),
    (35, 25),
    (32, 28),
    (31, 35),
    (27, 40),
    (21, 40),
    (17, 35),
    (16, 28),
]
EAR_LINES = [[(15, 11), (18, 18)], [(33, 11), (30, 18)]]
EYES = [(17.5, 22, 20.5, 25), (27.5, 22, 30.5, 25)]
MUZZLE = (19, 27, 29, 35)
NOSE = [(22, 30), (26, 30), (24, 32.5)]


def create_icon(paused=False, size=128):
    # Supersampling keeps the face recognizable in the 16 px notification area.
    canvas = max(192, size)
    scale = canvas / 48
    image = Image.new("RGBA", (canvas, canvas), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)

    def points(values):
        return [(round(x * scale), round(y * scale)) for x, y in values]

    def bounds(values):
        return tuple(round(v * scale) for v in values)

    draw.rounded_rectangle(
        (0, 0, canvas - 1, canvas - 1), radius=round(12 * scale), fill="#8490A3" if paused else PURPLE
    )
    draw.polygon(points(FACE), fill=WHITE)
    for line in EAR_LINES:
        draw.line(points(line), fill=LAVENDER, width=round(2 * scale))
    for eye in EYES:
        draw.ellipse(bounds(eye), fill=INK)
    draw.ellipse(bounds(MUZZLE), fill=LAVENDER)
    draw.polygon(points(NOSE), fill=INK)
    draw.line(points([(24, 32), (24, 34)]), fill=INK, width=round(1.3 * scale))
    return image.resize((size, size), Image.Resampling.LANCZOS)


def write_svg(path: Path):
    def points(values):
        return " ".join(f"{x},{y}" for x, y in values)

    shapes = [
        f'<rect width="48" height="48" rx="12" fill="{PURPLE}"/>',
        f'<polygon points="{points(FACE)}" fill="{WHITE}"/>',
    ]
    for line in EAR_LINES:
        shapes.append(
            f'<polyline points="{points(line)}" fill="none" stroke="{LAVENDER}" '
            'stroke-width="2" stroke-linecap="round"/>'
        )
    for x1, y1, x2, y2 in [*EYES, MUZZLE]:
        fill = LAVENDER if (x1, y1, x2, y2) == MUZZLE else INK
        shapes.append(
            f'<ellipse cx="{(x1 + x2) / 2}" cy="{(y1 + y2) / 2}" '
            f'rx="{(x2 - x1) / 2}" ry="{(y2 - y1) / 2}" fill="{fill}"/>'
        )
    shapes.extend(
        [
            f'<polygon points="{points(NOSE)}" fill="{INK}"/>',
            f'<path d="M24 32v2" stroke="{INK}" stroke-width="1.3" stroke-linecap="round"/>',
        ]
    )
    path.write_text(
        "<!-- Generated from anyllama/branding.py; edit geometry there. -->\n"
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 48 48">\n  '
        + "\n  ".join(shapes)
        + "\n</svg>\n",
        encoding="utf-8",
    )
