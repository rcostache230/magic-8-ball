#!/usr/bin/env python3
"""Screen gallery for the README: what the round AMOLED shows, one PNG per state.

Draws each screen from the same art, fonts and layout the firmware uses (assets/, fonts/,
answers.txt, and the label styles in magic-8-ball.yaml), so it matches the device closely.
These are renders, not photos: the firmware's motion, bubbles and glow pulses are frozen.

    python3 tools/make_screens.py      # needs Pillow and numpy; run make_assets.py first

Writes docs/screens/<state>.png (466 x 466, transparent outside the round screen) and
docs/screens/gallery.png (all of them on one dark sheet, with captions).
"""
from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

sys.path.insert(0, str(Path(__file__).resolve().parent))
import make_assets as A  # noqa: E402  (shares the answer parser, layout and drawing code)

OUT = A.ROOT / "docs" / "screens"
C = A.SCREEN // 2            # screen centre
SPEC_XY = (-58, -69)         # assets_gen.h: SPEC_X, SPEC_Y
BATT_Y = 166                 # w_batt in magic-8-ball.yaml: align CENTER, y 166


def load(name: str) -> Image.Image:
    return Image.open(A.ASSETS / name).convert("RGBA")


def blank() -> Image.Image:
    return Image.new("RGBA", (A.SCREEN, A.SCREEN), (0, 0, 0, 255))


def centred(canvas: Image.Image, im: Image.Image, dx: float = 0, dy: float = 0) -> None:
    canvas.alpha_composite(im, (round(C + dx - im.size[0] / 2), round(C + dy - im.size[1] / 2)))


def ball(disc: Image.Image) -> Image.Image:
    """The idle 8-ball with its disc rolled a little toward the lower right (as in preview.png)."""
    s = blank()
    centred(s, load("sphere.png"))
    centred(s, disc.resize((A.DISC_IMG, int(A.DISC_IMG * 0.93)), Image.LANCZOS), 18, 22)
    centred(s, load("spec.png"), *SPEC_XY)
    return s


def status_pill(s: Image.Image, text: str, rgb: str) -> None:
    """The battery / toast label: Barlow Condensed Bold 17, letter space 4, 1 px border, radius 14."""
    font = ImageFont.truetype(str(A.FONT_PILL), 17)
    asc, desc = font.getmetrics()
    tw = sum(font.getlength(ch) + 4 for ch in text)      # LVGL letter space trails every glyph
    w, h = 12 + tw + 8, asc + desc + 6
    x0, y0 = C - w / 2, C + BATT_Y - h / 2
    d = ImageDraw.Draw(s)
    d.rounded_rectangle([x0, y0, x0 + w, y0 + h], radius=14, outline=rgb, width=1)
    x = x0 + 12
    for ch in text:
        d.text((x, y0 + 3), ch, font=font, fill=rgb)
        x += font.getlength(ch) + 4


def asking() -> Image.Image:
    """Mid-churn: the die tumbling up through the murk, with a few bubbles (styles from magic8.h)."""
    s = blank()
    centred(s, load("window.png"))
    die = load("die/die_07.png")
    die.putalpha(die.getchannel("A").point(lambda a: a * 0.9))
    centred(s, die, 10, 78)
    layer = Image.new("RGBA", s.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    for x, y, r in ((-84, 40, 7), (-60, -22, 4), (96, 6, 6), (70, -58, 3), (-28, -86, 5), (40, 150, 4), (-110, 120, 3)):
        d.ellipse([C + x - r, C + y - r, C + x + r, C + y + r], fill=(0x7E, 0x95, 0xFF, 34),
                  outline=(0xC2, 0xCE, 0xFF, 170), width=1)
    s.alpha_composite(layer)
    return s


def answer(a, tris) -> Image.Image:
    s = blank()
    centred(s, load("window.png"))
    A.draw_answer(s, C, C, a, tris)
    return s


def round_cut(img: Image.Image, ss: int = 4) -> Image.Image:
    """Clip to the round screen with an anti-aliased edge, transparent outside."""
    n = A.SCREEN * ss
    m = Image.new("L", (n, n), 0)
    ImageDraw.Draw(m).ellipse([0, 0, n - 1, n - 1], fill=255)
    out = img.copy()
    out.putalpha(m.resize(img.size, Image.LANCZOS))
    return out


def main() -> None:
    answers = A.parse_answers()
    A.fit_answers(answers)
    tris = {k: load(f"tri_{k}.png") for k in A.TRI_STYLES}

    def first(rarity, mode=None):
        return next(a for a in answers if a["rarity"] == rarity and (mode is None or a["mode"] == mode))

    idle = ball(load("disc8.png"))
    charging, low = idle.copy(), idle.copy()
    status_pill(charging, "CHARGING 64%", "#6DFFB0")
    status_pill(low, "LOW BATTERY 12%", "#FFB424")
    screens = [
        ("idle", "Idle: the 8 rolls as you move it", idle),
        ("asking", "Shaken: the die rises through the murk", asking()),
        ("answer-common", "Oracle: a common answer", answer(first("common"), tris)),
        ("answer-rare", "Oracle: RARE", answer(first("rare"), tris)),
        ("answer-legendary", "Oracle: LEGENDARY", answer(first("legendary"), tris)),
        ("answer-glitch", "Oracle: GLITCH", answer(first("glitch"), tris)),
        ("idle-roast", "Roast mode's idle ball", ball(load("disc8_roast.png"))),
        ("answer-roast", "Roast: teases the question", answer(first("roast"), tris)),
        ("mode-card", "Double-tap: switch mode", answer(first("card", "oracle"), tris)),
        ("skill-issue", "Swipe up: the rigged SKILL ISSUE", answer(first("skill"), tris)),
        ("charging", "Plugged in: charging", charging),
        ("low-battery", "Low battery warning", low),
    ]

    OUT.mkdir(parents=True, exist_ok=True)
    for name, _, img in screens:
        round_cut(img).save(OUT / f"{name}.png", optimize=True)

    cols, gap, cap = 4, 36, 58
    rows = -(-len(screens) // cols)
    sheet = Image.new("RGBA", (cols * (A.SCREEN + gap) + gap, rows * (A.SCREEN + cap + gap) + gap), (24, 24, 28, 255))
    font = ImageFont.truetype(str(A.FONTS / "BarlowCondensed-SemiBold.ttf"), 30)
    d = ImageDraw.Draw(sheet)
    for i, (_, caption, img) in enumerate(screens):
        x = gap + (i % cols) * (A.SCREEN + gap)
        y = gap + (i // cols) * (A.SCREEN + cap + gap)
        sheet.alpha_composite(round_cut(img), (x, y))
        d.text((x + A.SCREEN / 2, y + A.SCREEN + 14), caption, font=font, fill=(214, 214, 222), anchor="mt")
    sheet.convert("RGB").save(OUT / "gallery.png", optimize=True)
    print(f"wrote {len(screens)} screens and gallery.png to {OUT.relative_to(A.ROOT)}/")


if __name__ == "__main__":
    main()
