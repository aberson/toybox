"""F.5-3b helper: generate placeholder cartoon action templates.

Produces 10 hand-drawn-look 256x256 RGBA PNGs under data/sprites/templates/
plus manifest.json and CREDITS.md. Each template depicts an action
archetype (arrow + speech bubble for pointing, magnifying glass for
looking, motion lines for jumping, etc.) with a designated toy_box
where the toy photo composites in.

The art is intentionally simple iconographic shapes drawn with Pillow
primitives so it ships under CC0 (operator-drawn placeholder) without
license risk. The operator can replace any individual template with
better art later — composite.py reads from disk per-call so swapping is
a file-replace operation, no code change.

Color palette: friendly cartoon (warm yellows for cheering, blue for
sleeping, etc.). Stroke weight is 4 px for visibility against the toy.
"""

from __future__ import annotations

import json
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

OUT_DIR = Path("data/sprites/templates")
SIZE = 256
TRANSPARENT = (0, 0, 0, 0)


def _new() -> tuple[Image.Image, ImageDraw.ImageDraw]:
    img = Image.new("RGBA", (SIZE, SIZE), TRANSPARENT)
    draw = ImageDraw.Draw(img)
    return img, draw


def _font(px: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    """Best-effort font load; falls back to default bitmap if no truetype."""
    candidates = [
        # Windows
        "C:/Windows/Fonts/segoeui.ttf",
        "C:/Windows/Fonts/arial.ttf",
        "C:/Windows/Fonts/calibri.ttf",
    ]
    for c in candidates:
        try:
            return ImageFont.truetype(c, px)
        except OSError:
            continue
    return ImageFont.load_default()


def _save(img: Image.Image, slot: str) -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    img.save(OUT_DIR / f"{slot}.png", format="PNG")
    print(f"wrote {OUT_DIR / slot}.png")


def template_idle() -> None:
    """Generic stage / floor — a simple ground-line + spotlight ellipse."""
    img, draw = _new()
    # spotlight on the ground
    draw.ellipse(
        [(48, 200), (208, 240)],
        fill=(255, 240, 180, 80),
        outline=(220, 180, 60, 200),
        width=3,
    )
    _save(img, "idle")


def template_pointing() -> None:
    """Big right-pointing arrow + small speech bubble."""
    img, draw = _new()
    # arrow shaft
    draw.rectangle([(140, 100), (220, 140)], fill=(255, 110, 80, 220))
    # arrow head
    draw.polygon(
        [(220, 80), (240, 120), (220, 160)],
        fill=(255, 110, 80, 220),
    )
    # speech bubble
    draw.ellipse(
        [(20, 30), (110, 90)],
        outline=(80, 80, 80, 220),
        width=4,
    )
    draw.polygon(
        [(50, 80), (60, 100), (70, 80)],
        outline=(80, 80, 80, 220),
        fill=(255, 255, 255, 220),
    )
    _save(img, "pointing")


def template_looking() -> None:
    """Magnifying glass — circle outline + handle, bottom-right corner."""
    img, draw = _new()
    # lens
    draw.ellipse(
        [(140, 140), (240, 240)],
        outline=(80, 60, 30, 240),
        width=6,
    )
    # inner glass tint
    draw.ellipse(
        [(150, 150), (230, 230)],
        fill=(180, 220, 255, 80),
    )
    # handle
    draw.line(
        [(220, 220), (255, 255)],
        fill=(110, 70, 30, 240),
        width=10,
    )
    _save(img, "looking")


def template_jumping() -> None:
    """Motion-blur lines beneath a jumping figure (curved arcs at base)."""
    img, draw = _new()
    # bottom ground hint with motion arcs
    for i, y in enumerate([200, 220, 240]):
        draw.arc(
            [(40 + i * 10, y - 30), (216 - i * 10, y + 10)],
            start=180, end=360,
            fill=(120, 200, 255, 200 - i * 40),
            width=4,
        )
    # action lines radiating
    for x in [60, 120, 180]:
        draw.line(
            [(x, 250), (x + (-5 if x < 128 else 5), 270)],
            fill=(120, 200, 255, 180),
            width=3,
        )
    _save(img, "jumping")


def template_cheering() -> None:
    """Confetti starburst — radial dots and stars from a center point."""
    img, draw = _new()
    cx, cy = 128, 60
    # bright starburst
    import math
    for i in range(12):
        angle = i * (360 / 12)
        rad = math.radians(angle)
        x1 = cx + 30 * math.cos(rad)
        y1 = cy + 30 * math.sin(rad)
        x2 = cx + 70 * math.cos(rad)
        y2 = cy + 70 * math.sin(rad)
        draw.line([(x1, y1), (x2, y2)], fill=(255, 200, 50, 230), width=4)
    # confetti dots
    confetti = [
        (40, 30, (255, 100, 100, 220)),
        (200, 50, (100, 255, 150, 220)),
        (220, 100, (100, 150, 255, 220)),
        (30, 90, (255, 200, 100, 220)),
        (180, 30, (200, 100, 255, 220)),
    ]
    for x, y, color in confetti:
        draw.ellipse([(x - 6, y - 6), (x + 6, y + 6)], fill=color)
    _save(img, "cheering")


def template_thinking() -> None:
    """Thought bubble — cloud-shape outline upper-right with question mark."""
    img, draw = _new()
    outline = (80, 80, 80, 220)
    fill = (255, 255, 255, 200)
    # main thought cloud + smaller trailing bubbles
    draw.ellipse([(140, 30), (240, 110)], outline=outline, width=4, fill=fill)
    draw.ellipse([(180, 70), (220, 100)], outline=outline, width=3, fill=fill)
    draw.ellipse([(150, 110), (170, 130)], outline=outline, width=3, fill=fill)
    draw.ellipse([(140, 130), (155, 145)], outline=outline, width=2, fill=fill)
    # question mark in main bubble
    font = _font(40)
    draw.text((178, 50), "?", fill=(80, 80, 80, 240), font=font)
    _save(img, "thinking")


def template_waving() -> None:
    """Hand-wave arc — curved motion lines, upper-left."""
    img, draw = _new()
    # arc lines suggesting wave motion
    for i, r in enumerate([40, 55, 70]):
        draw.arc(
            [(20 - i * 5, 30 - i * 5), (20 + r * 2 - i * 5, 30 + r * 2 - i * 5)],
            start=200, end=340,
            fill=(255, 180, 120, 220 - i * 30),
            width=4,
        )
    # smiling text "Hi!" mid-image
    font = _font(36)
    draw.text((150, 30), "Hi!", fill=(255, 100, 80, 240), font=font)
    _save(img, "waving")


def template_running() -> None:
    """Horizontal motion lines — rightward streaks."""
    img, draw = _new()
    for i, y in enumerate([90, 130, 170]):
        draw.line(
            [(20 + i * 10, y), (120 + i * 10, y)],
            fill=(100, 200, 255, 220 - i * 30),
            width=5,
        )
    # small dust puffs on the right
    for x, y in [(200, 220), (220, 230), (180, 235)]:
        draw.ellipse([(x - 8, y - 5), (x + 8, y + 5)], fill=(180, 180, 180, 180))
    _save(img, "running")


def template_sleeping() -> None:
    """ZZZ stack — three Z letters at increasing size, upper-right."""
    img, draw = _new()
    font_small = _font(28)
    font_mid = _font(40)
    font_big = _font(56)
    draw.text((150, 20), "Z", fill=(80, 120, 200, 240), font=font_small)
    draw.text((175, 40), "Z", fill=(80, 120, 200, 240), font=font_mid)
    draw.text((205, 70), "Z", fill=(80, 120, 200, 240), font=font_big)
    # crescent moon hint, bottom-left
    draw.ellipse([(20, 200), (80, 250)], fill=(255, 240, 180, 220))
    draw.ellipse([(40, 210), (90, 260)], fill=(0, 0, 0, 0))  # cut
    _save(img, "sleeping")


def template_confused() -> None:
    """Question mark + spiral — upper-right."""
    img, draw = _new()
    # big question mark
    font = _font(96)
    draw.text((155, 10), "?", fill=(255, 100, 100, 240), font=font)
    # spiral underneath the toy area (just hatched lines)
    cx, cy = 50, 60
    import math
    for t in range(0, 720, 8):
        rad = math.radians(t)
        r = t * 0.05
        x = cx + r * math.cos(rad)
        y = cy + r * math.sin(rad)
        draw.ellipse([(x - 1.5, y - 1.5), (x + 1.5, y + 1.5)], fill=(120, 100, 200, 200))
    _save(img, "confused")


# ---------------------------------------------------------------------
# Manifest — toy_box + behind + source per slot
# ---------------------------------------------------------------------

# toy_box [x0, y0, x1, y1] = upper-left to lower-right pixel coords (range 0..255)
# behind: True = toy under template; False = toy over template
_PLACEHOLDER_SOURCE = "operator-drawn (Pillow placeholder, CC0)"

MANIFEST = {
    "idle":     {"toy_box": [60, 40, 200, 200],  "behind": False, "source": _PLACEHOLDER_SOURCE},
    "pointing": {"toy_box": [20, 90, 140, 220],  "behind": False, "source": _PLACEHOLDER_SOURCE},
    "looking":  {"toy_box": [20, 30, 140, 180],  "behind": False, "source": _PLACEHOLDER_SOURCE},
    "jumping":  {"toy_box": [60, 30, 200, 180],  "behind": False, "source": _PLACEHOLDER_SOURCE},
    "cheering": {"toy_box": [50, 100, 200, 240], "behind": False, "source": _PLACEHOLDER_SOURCE},
    "thinking": {"toy_box": [20, 130, 140, 240], "behind": False, "source": _PLACEHOLDER_SOURCE},
    "waving":   {"toy_box": [80, 70, 220, 230],  "behind": False, "source": _PLACEHOLDER_SOURCE},
    "running":  {"toy_box": [40, 50, 180, 200],  "behind": False, "source": _PLACEHOLDER_SOURCE},
    "sleeping": {"toy_box": [40, 90, 180, 240],  "behind": False, "source": _PLACEHOLDER_SOURCE},
    "confused": {"toy_box": [30, 70, 160, 220],  "behind": False, "source": _PLACEHOLDER_SOURCE},
}


CREDITS = """# Tier C composite template credits

All templates are operator-drawn iconographic placeholders generated via
Pillow primitives in `scripts/f5_generate_templates.py`. They depict
generic action archetypes (arrows, motion lines, magnifying glass, etc.)
without copyrighted characters or logos.

License: CC0 (public domain dedication; no attribution required).

Each row records the source posture per template; "operator-drawn"
means generated by the script under CC0. If a template is replaced
with externally-sourced art later, update its entry to record the
source + license.

| slot | source |
|---|---|
| idle | operator-drawn (Pillow placeholder, CC0) |
| pointing | operator-drawn (Pillow placeholder, CC0) |
| looking | operator-drawn (Pillow placeholder, CC0) |
| jumping | operator-drawn (Pillow placeholder, CC0) |
| cheering | operator-drawn (Pillow placeholder, CC0) |
| thinking | operator-drawn (Pillow placeholder, CC0) |
| waving | operator-drawn (Pillow placeholder, CC0) |
| running | operator-drawn (Pillow placeholder, CC0) |
| sleeping | operator-drawn (Pillow placeholder, CC0) |
| confused | operator-drawn (Pillow placeholder, CC0) |

To replace a template with better art, drop a 256x256 RGBA PNG at
`data/sprites/templates/<slot>.png` and update its row above. Update
`manifest.json` if the new art needs a different `toy_box` or `behind`
value. The composite cache flushes per process restart.
"""


def main() -> None:
    template_idle()
    template_pointing()
    template_looking()
    template_jumping()
    template_cheering()
    template_thinking()
    template_waving()
    template_running()
    template_sleeping()
    template_confused()

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "manifest.json").write_text(json.dumps(MANIFEST, indent=2), encoding="utf-8")
    print(f"wrote {OUT_DIR / 'manifest.json'}")

    (OUT_DIR / "CREDITS.md").write_text(CREDITS, encoding="utf-8")
    print(f"wrote {OUT_DIR / 'CREDITS.md'}")

    # Verification: ensure all 10 PNGs exist and load via PIL
    slots = ["idle", "pointing", "looking", "jumping", "cheering",
             "thinking", "waving", "running", "sleeping", "confused"]
    for slot in slots:
        img = Image.open(OUT_DIR / f"{slot}.png")
        img.verify()
    print("all 10 templates valid")


if __name__ == "__main__":
    main()
