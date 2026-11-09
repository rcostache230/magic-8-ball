#!/usr/bin/env python3
"""Magic 8 Ball asset generator.

Renders every image the firmware draws into ../assets, checks that each answer in
../answers.txt fits inside its triangle, and writes the two headers the firmware includes:

    ../answers_gen.h   the answer table (text, rarity, font, vertical offset)
    ../assets_gen.h    geometry shared between the art and the animation code

It also saves ../assets/preview.png (mock-ups of the round screen) and
../assets/answers_sheet.png (every answer in its triangle) for a visual check.

    python3 tools/make_assets.py

Colours are pre-dithered to RGB565 because ESPHome truncates 8-bit channels (r >> 3);
without it the dark gradients band visibly on the AMOLED.
"""
from __future__ import annotations

import math
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

ROOT = Path(__file__).resolve().parent.parent
ASSETS = ROOT / "assets"
FONTS = ROOT / "fonts"
SCREEN = 466

# ----------------------------------------------------------------------------------------
# Geometry (all in screen pixels; the screen centre is (233, 233))
# ----------------------------------------------------------------------------------------
BALL_IMG = 440          # sphere and window images are BALL_IMG square, centred on screen
BALL_R = 200            # radius of the black ball
WINDOW_R = 212          # radius of the liquid window
DISC_R = 76             # the white "8" disc
DISC_IMG = 160
LIGHT = np.array([-0.55, -0.65, 0.52])
LIGHT = LIGHT / np.linalg.norm(LIGHT)

TRI_SIDE = 340
TRI_H = TRI_SIDE * math.sqrt(3) / 2              # 294.4
TRI_MARGIN = 22                                   # room for the outer glow
TRI_W, TRI_IMG_H = 384, 340
TRI_TOP = TRI_MARGIN
TRI_CENTROID_Y = TRI_TOP + TRI_H / 3              # in image coordinates
TRI_IMG_DY = round(TRI_IMG_H / 2 - TRI_CENTROID_Y)  # image offset that centres the centroid
TRI_CORNER = 18
# The triangle without its glow margin, as a box inside the full image (even sizes).
TRI_TIGHT_BOX = (TRI_MARGIN, TRI_TOP, TRI_W - TRI_MARGIN, TRI_TOP + 296)

# Answer text
ANSWER_GLYPHS = " !'()+,-./0123456789:?%ABCDEFGHIJKLMNOPQRSTUVWXYZÉ…"
FONT_ANSWER = FONTS / "BarlowCondensed-SemiBold.ttf"
FONT_PILL = FONTS / "BarlowCondensed-Bold.ttf"
FONT_EIGHT = FONTS / "Barlow-Black.ttf"
ANSWER_SIZES = (34, 29)   # big, small fallback (must match magic-8-ball.yaml)
LINE_SPACE = -6           # text_line_space in the yaml
TEXT_PAD = 16             # clearance between text and the triangle edge

RARITY_IDS = {"common": 0, "rare": 1, "legendary": 2, "glitch": 3, "secret": 4, "classic": 5, "roast": 6,
              "card": 7, "skill": 8, "rickrolled": 9}
MOODS = ("none", "yes", "no", "maybe")    # index = Mood enum; swipes rig yes / no
MODES = ("oracle", "classic", "roast")     # index = Mode enum
EGGS = ("hard", "spam", "night")           # index = the tag of a secret answer
ORACLE_KINDS = ("common", "rare", "legendary", "glitch")

# ----------------------------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------------------------
BAYER8 = (np.array([
    [0, 32, 8, 40, 2, 34, 10, 42],
    [48, 16, 56, 24, 50, 18, 58, 26],
    [12, 44, 4, 36, 14, 46, 6, 38],
    [60, 28, 52, 20, 62, 30, 54, 22],
    [3, 35, 11, 43, 1, 33, 9, 41],
    [51, 19, 59, 27, 49, 17, 57, 25],
    [15, 47, 7, 39, 13, 45, 5, 37],
    [63, 31, 55, 23, 61, 29, 53, 21],
]) + 0.5) / 64.0


def dither565(rgb: np.ndarray) -> np.ndarray:
    """float RGB 0..255 (H, W, 3) -> uint8 RGB whose low bits are zero (exact under >>3/>>2)."""
    h, w, _ = rgb.shape
    thr = np.tile(BAYER8, (h // 8 + 1, w // 8 + 1))[:h, :w]
    out = np.empty((h, w, 3), np.uint8)
    for ch, bits in ((0, 5), (1, 6), (2, 5)):
        step = 256 / (1 << bits)
        q = np.floor(rgb[..., ch] / step + thr)
        q = np.clip(q, 0, (1 << bits) - 1)
        out[..., ch] = (q * step).astype(np.uint8)
    return out


def finish_rgba(img: Image.Image) -> Image.Image:
    a = np.asarray(img).astype(np.float64)
    rgb = dither565(a[..., :3])
    alpha = a[..., 3].round().astype(np.uint8)
    rgb[alpha == 0] = 0
    return Image.fromarray(np.dstack([rgb, alpha]), "RGBA")


def finish_rgb(rgb: np.ndarray) -> Image.Image:
    return Image.fromarray(dither565(rgb), "RGB")


def hex_rgb(h: str) -> np.ndarray:
    h = h.lstrip("#")
    return np.array([int(h[i:i + 2], 16) for i in (0, 2, 4)], np.float64)


def disk_alpha(size: int, cx: float, cy: float, r: float) -> np.ndarray:
    """Anti-aliased disc coverage, 0..1."""
    yy, xx = np.mgrid[0:size, 0:size] + 0.5
    d = np.sqrt((xx - cx) ** 2 + (yy - cy) ** 2)
    return np.clip(r - d + 0.5, 0, 1)


# ----------------------------------------------------------------------------------------
# The ball (idle face), its specular highlight, and the "8" disc
# ----------------------------------------------------------------------------------------
def sphere_normals(size: int, r: float, ss: int):
    n = size * ss
    yy, xx = np.mgrid[0:n, 0:n] + 0.5
    x = (xx / ss - size / 2) / r
    y = (yy / ss - size / 2) / r
    r2 = x * x + y * y
    inside = r2 <= 1
    z = np.sqrt(np.clip(1 - r2, 0, 1))
    return x, y, z, inside


def make_ball():
    ss = 2
    x, y, z, inside = sphere_normals(BALL_IMG, BALL_R, ss)
    ndl = np.clip(x * LIGHT[0] + y * LIGHT[1] + z * LIGHT[2], 0, 1)
    half = LIGHT + np.array([0, 0, 1.0])
    half /= np.linalg.norm(half)
    ndh = np.clip(x * half[0] + y * half[1] + z * half[2], 0, 1)
    fres = (1 - z) ** 3.2
    bounce = np.clip(x * 0.35 + y * 0.8 + z * 0.2, 0, 1) ** 2.5

    base = hex_rgb("#060608") / 255
    white = np.ones(3)
    blue = np.array([0.30, 0.42, 1.0])
    col = (base[None, None, :]
           + (0.06 * ndl + 0.10 * ndh ** 12)[..., None] * white
           + (0.32 * fres)[..., None] * blue
           + (0.05 * bounce)[..., None] * np.array([0.5, 0.55, 1.0]))
    # Anti-aliased silhouette against black.
    cov = np.clip((1 - np.sqrt(x * x + y * y)) * BALL_R * ss + 0.5, 0, 1)
    col = col * cov[..., None]
    col = np.clip(col, 0, 1) * 255
    big = Image.fromarray(col.astype(np.uint8), "RGB").resize((BALL_IMG, BALL_IMG), Image.LANCZOS)
    sphere = finish_rgb(np.asarray(big).astype(np.float64))

    # Sharp specular highlight: separate overlay so it stays on top of the rolling disc.
    spec = np.clip(ndh ** 140 * 1.0 + ndh ** 34 * 0.28, 0, 1) * inside
    spec_img = Image.fromarray((spec * 255).astype(np.uint8), "L").resize((BALL_IMG, BALL_IMG), Image.LANCZOS)
    sa = np.asarray(spec_img)
    ys, xs = np.nonzero(sa > 3)
    x0, x1 = xs.min() - 2, xs.max() + 3
    y0, y1 = ys.min() - 2, ys.max() + 3
    x0 -= x0 % 2
    y0 -= y0 % 2
    x1 += (x1 - x0) % 2
    y1 += (y1 - y0) % 2
    crop = sa[y0:y1, x0:x1].astype(np.float64)
    rgba = np.dstack([np.full(crop.shape, 255.0)] * 3 + [crop])
    spec_rgba = finish_rgba(Image.fromarray(rgba.astype(np.uint8), "RGBA"))
    spec_x = round((x0 + x1) / 2 - BALL_IMG / 2)
    spec_y = round((y0 + y1) / 2 - BALL_IMG / 2)
    return sphere, spec_rgba, (spec_x, spec_y)


def make_disc(paper_hex: str = "#F4F4F1", ink=(14, 14, 18, 255)):
    ss = 4
    n = DISC_IMG * ss
    c = n / 2
    cov = disk_alpha(n, c, c, DISC_R * ss)
    yy, xx = np.mgrid[0:n, 0:n] + 0.5
    # Soft dome shading: brightest toward the light, a little cooler at the rim.
    dx = (xx - c) / (DISC_R * ss)
    dy = (yy - c) / (DISC_R * ss)
    d = np.sqrt(dx * dx + dy * dy)
    lit = np.clip(1 - 0.10 * d ** 2 - 0.06 * (dx * -LIGHT[0] + dy * -LIGHT[1]), 0, 1)
    paper = hex_rgb(paper_hex)
    col = paper[None, None, :] * lit[..., None]
    rim = np.clip(1 - np.abs(d - 0.985) * DISC_R / 1.6, 0, 1)
    col = col * (1 - 0.25 * rim[..., None])
    img = Image.fromarray(np.dstack([np.clip(col, 0, 255), cov * 255]).astype(np.uint8), "RGBA")

    # The 8, centred on its ink.
    font = ImageFont.truetype(str(FONT_EIGHT), 118 * ss)
    bbox = font.getbbox("8")
    ink_w, ink_h = bbox[2] - bbox[0], bbox[3] - bbox[1]
    draw = ImageDraw.Draw(img)
    draw.text((c - ink_w / 2 - bbox[0], c - ink_h / 2 - bbox[1]), "8", font=font, fill=ink)
    img = img.resize((DISC_IMG, DISC_IMG), Image.LANCZOS)
    return finish_rgba(img)


def make_window():
    ss = 2
    n = BALL_IMG * ss
    c = n / 2
    yy, xx = np.mgrid[0:n, 0:n] + 0.5
    d = np.sqrt((xx - c) ** 2 + (yy - c) ** 2) / ss / WINDOW_R      # 0 centre, 1 rim
    # Murky liquid: indigo centre falling to near-black, with slow cloudy variation.
    centre = hex_rgb("#0E1A5E")
    mid = hex_rgb("#081040")
    edge = hex_rgb("#02030E")
    t = np.clip(d, 0, 1)
    col = np.where(t[..., None] < 0.55,
                   centre + (mid - centre) * (t / 0.55)[..., None],
                   mid + (edge - mid) * np.clip((t - 0.55) / 0.45, 0, 1)[..., None] ** 1.3)
    rng = np.random.default_rng(8)
    noise = Image.fromarray((rng.random((n // 16, n // 16)) * 255).astype(np.uint8), "L")
    noise = noise.resize((n, n), Image.BICUBIC).filter(ImageFilter.GaussianBlur(10 * ss))
    nz = (np.asarray(noise).astype(np.float64) / 255 - 0.5) * 2
    col = col * (1 + 0.22 * nz[..., None])
    # Inner shadow just inside the rim, then a thin bezel ring.
    col = col * (1 - 0.55 * np.clip((d - 0.86) / 0.14, 0, 1) ** 2)[..., None]
    ring = np.clip(1 - np.abs(d - 1.0) * WINDOW_R / 1.8, 0, 1)
    col = col + ring[..., None] * hex_rgb("#2A3570") * 0.9
    cov = np.clip((1.0 - d) * WINDOW_R * ss + 1.5 * ss, 0, 1)
    col = np.clip(col * cov[..., None], 0, 255)
    big = Image.fromarray(col.astype(np.uint8), "RGB").resize((BALL_IMG, BALL_IMG), Image.LANCZOS)
    return finish_rgb(np.asarray(big).astype(np.float64))


# ----------------------------------------------------------------------------------------
# Answer triangles
# ----------------------------------------------------------------------------------------
TRI_STYLES = {
    #          top        bottom     rim        glow       glow a  text
    "blue":   ("#3555FF", "#0C1A8E", "#8EA2FF", "#2D4BFF", 0.55, "#FFFFFF"),
    "purple": ("#9B4DFF", "#3A0E92", "#D5B2FF", "#8A3DFF", 0.55, "#FFFFFF"),
    "gold":   ("#FFE27A", "#B9760C", "#FFF6CC", "#FFB424", 0.62, "#2B1A00"),
    "red":    ("#FF6A3D", "#A8170C", "#FFC2AE", "#FF3B1F", 0.58, "#FFFFFF"),
    "skill":  ("#25351F", "#060B05", "#8CFF6E", "#39FF14", 0.80, "#8CFF6E"),
}


def tri_points(scale=1.0, inset=0.0):
    """Point-down equilateral triangle in image coordinates, optionally shrunk toward its centroid."""
    cx, cy = TRI_W / 2, TRI_CENTROID_Y
    r_in = TRI_SIDE / (2 * math.sqrt(3))
    k = (r_in - inset) / r_in
    pts = [(cx - TRI_SIDE / 2, TRI_TOP), (cx + TRI_SIDE / 2, TRI_TOP), (cx, TRI_TOP + TRI_H)]
    return [((x - cx) * k * scale + cx, (y - cy) * k * scale + cy) for x, y in pts]


def rounded_triangle_mask(ss: int, inset: float = 0.0) -> Image.Image:
    """Rounded triangle = (triangle shrunk by the corner radius) dilated by a disc."""
    m = Image.new("L", (TRI_W * ss, TRI_IMG_H * ss), 0)
    d = ImageDraw.Draw(m)
    r = TRI_CORNER
    inner = [(x * ss, y * ss) for x, y in tri_points(inset=r + inset)]
    d.polygon(inner, fill=255)
    for i in range(3):
        a, b = inner[i], inner[(i + 1) % 3]
        d.line([a, b], fill=255, width=int(2 * r * ss))
        d.ellipse([a[0] - r * ss, a[1] - r * ss, a[0] + r * ss, a[1] + r * ss], fill=255)
    return m


def make_triangle(style: str, with_glow: bool = True) -> Image.Image:
    top, bottom, rim, glow, glow_a, _ = TRI_STYLES[style]
    ss = 4
    w, h = TRI_W * ss, TRI_IMG_H * ss
    mask = rounded_triangle_mask(ss)
    yy = (np.mgrid[0:h, 0:w][0] + 0.5) / ss
    t = np.clip((yy - TRI_TOP) / TRI_H, 0, 1)[..., None]
    col = hex_rgb(top) + (hex_rgb(bottom) - hex_rgb(top)) * t ** 0.9
    # Glassy sheen across the upper part.
    sheen = Image.new("L", (w, h), 0)
    ImageDraw.Draw(sheen).ellipse(
        [w / 2 - TRI_SIDE * 0.42 * ss, (TRI_TOP - 40) * ss, w / 2 + TRI_SIDE * 0.42 * ss, (TRI_TOP + 70) * ss],
        fill=255)
    sheen = np.asarray(sheen.filter(ImageFilter.GaussianBlur(18 * ss))).astype(np.float64) / 255
    col = col + (255 - col) * (0.22 * sheen)[..., None]
    # Bevel: a lighter rim inside the edge.
    inner = rounded_triangle_mask(ss, inset=3.2)
    rim_a = (np.asarray(mask).astype(np.float64) - np.asarray(inner).astype(np.float64)) / 255
    rim_a = np.clip(rim_a, 0, 1) * 0.75
    col = col + (hex_rgb(rim) - col) * rim_a[..., None]
    alpha = np.asarray(mask).astype(np.float64)
    big = Image.fromarray(np.dstack([np.clip(col, 0, 255), alpha]).astype(np.uint8), "RGBA")
    tri = big.resize((TRI_W, TRI_IMG_H), Image.LANCZOS)
    if not with_glow:
        return finish_rgba(tri.crop(TRI_TIGHT_BOX))

    # Outer glow from the 1x silhouette.
    a1 = tri.getchannel("A")
    g = np.asarray(a1.filter(ImageFilter.GaussianBlur(10))).astype(np.float64) * glow_a
    g2 = np.asarray(a1.filter(ImageFilter.GaussianBlur(3))).astype(np.float64) * glow_a * 0.5
    ga = np.clip(g + g2, 0, 255)
    gc = hex_rgb(glow)
    glow_img = Image.fromarray(np.dstack([np.broadcast_to(gc, ga.shape + (3,)), ga]).astype(np.uint8), "RGBA")
    out = Image.alpha_composite(glow_img, tri)
    return finish_rgba(out)


# ----------------------------------------------------------------------------------------
# The 20-sided die that tumbles in the liquid (pre-rendered: LVGL's software rotation is
# far too slow for a smooth tumble, but swapping small frames is cheap)
# ----------------------------------------------------------------------------------------
DIE_IMG = 168
DIE_R = 74             # circumradius in pixels
DIE_N = 16             # frames in one loop (120 degrees about a 3-fold axis = seamless)


def icosahedron():
    p = (1 + 5 ** 0.5) / 2
    v = []
    for a in (-1, 1):
        for b in (-p, p):
            v += [(0, a, b), (a, b, 0), (b, 0, a)]
    v = np.array(v, float)
    faces = []
    n = len(v)
    for i in range(n):
        for j in range(i + 1, n):
            for k in range(j + 1, n):
                if all(abs(np.linalg.norm(v[x] - v[y]) - 2) < 1e-6 for x, y in ((i, j), (j, k), (i, k))):
                    faces.append((i, j, k))
    assert len(faces) == 20
    return v / np.linalg.norm(v[0]), faces


def rotation(axis, ang):
    a = np.asarray(axis, float)
    a = a / np.linalg.norm(a)
    k = np.array([[0, -a[2], a[1]], [a[2], 0, -a[0]], [-a[1], a[0], 0]])
    return np.eye(3) + math.sin(ang) * k + (1 - math.cos(ang)) * (k @ k)


def make_die_frames():
    verts, faces = icosahedron()
    c = verts[list(faces[0])].mean(axis=0)
    c = c / np.linalg.norm(c)
    t = np.array([0.42, -0.50, 0.76])          # the spin axis, tilted toward the viewer
    t = t / np.linalg.norm(t)
    axis0 = np.cross(c, t)
    r0 = rotation(axis0, math.acos(np.clip(c @ t, -1, 1)))
    light = np.array([-0.45, 0.62, 0.64])
    light = light / np.linalg.norm(light)
    dark, lit = hex_rgb("#0A1258"), hex_rgb("#3A5CFF")
    ss = 4
    n = DIE_IMG * ss
    frames = []
    for i in range(DIE_N):
        rot = rotation(t, 2 * math.pi / 3 * i / DIE_N) @ r0
        v = verts @ rot.T
        img = Image.new("RGBA", (n, n), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        drawn = []
        for f in faces:
            a, b, cc = v[f[0]], v[f[1]], v[f[2]]
            nrm = np.cross(b - a, cc - a)
            nrm = nrm / np.linalg.norm(nrm)
            cen = (a + b + cc) / 3
            if nrm @ cen < 0:
                nrm = -nrm
            if nrm[2] <= 0.02:
                continue
            drawn.append((cen[2], f, nrm, cen))
        drawn.sort(key=lambda x: x[0])
        for _, f, nrm, cen in drawn:
            pts = [(n / 2 + v[k][0] * DIE_R * ss, n / 2 - v[k][1] * DIE_R * ss) for k in f]
            diff = max(0.0, float(nrm @ light))
            col = dark + (lit - dark) * (0.15 + 0.85 * diff)
            col = col * (0.55 + 0.45 * nrm[2])       # faces turning away fall into the murk
            d.polygon(pts, fill=tuple(int(x) for x in col) + (255,))
            # the little answer triangle on every face
            cx2 = sum(p[0] for p in pts) / 3
            cy2 = sum(p[1] for p in pts) / 3
            inset = [(cx2 + (px - cx2) * 0.58, cy2 + (py - cy2) * 0.58) for px, py in pts]
            ic = tuple(int(min(255, x * 1.35 + 18)) for x in col) + (255,)
            d.polygon(inset, outline=ic, width=int(1.3 * ss))
        for _, f, nrm, cen in drawn:      # crisp edges on top
            pts = [(n / 2 + v[k][0] * DIE_R * ss, n / 2 - v[k][1] * DIE_R * ss) for k in f]
            edge = tuple(int(x) for x in hex_rgb("#7088FF") * (0.45 + 0.55 * nrm[2])) + (255,)
            d.line(pts + [pts[0]], fill=edge, width=int(1.4 * ss), joint="curve")
        frames.append(finish_rgba(img.resize((DIE_IMG, DIE_IMG), Image.LANCZOS)))
    return frames


def write_die_yaml():
    rows = ["# Generated by tools/make_assets.py. Do not edit by hand.",
            "# The tumbling-die frames, merged into magic-8-ball.yaml as a package.",
            "image:"]
    for i in range(DIE_N):
        rows += [f"  - platform: file", f"    file: assets/die/die_{i:02d}.png", f"    id: img_die_{i:02d}",
                 f"    type: RGB565", f"    transparency: alpha_channel"]
    (ROOT / "die_frames.yaml").write_text("\n".join(rows) + "\n", encoding="utf-8")


# ----------------------------------------------------------------------------------------
# Answers: parse, fit-check, emit
# ----------------------------------------------------------------------------------------
def parse_answers():
    answers = []
    for ln, raw in enumerate((ROOT / "answers.txt").read_text(encoding="utf-8").splitlines(), 1):
        s = raw.strip()
        if not s or s.startswith("#"):
            continue
        parts = [p.strip() for p in s.split("|")]
        kind_words, lines = parts[0].split(), [p for p in parts[1:] if p]
        kind = kind_words[0] if kind_words else ""
        mood = kind_words[1] if len(kind_words) > 1 else "none"
        if mood not in MOODS or len(kind_words) > 2:
            sys.exit(f"answers.txt:{ln}: '{parts[0]}': expected 'kind [yes|no|maybe]'")
        head, _, sub = kind.partition(":")
        if head in ORACLE_KINDS and not sub:
            rarity, mode, tag = head, "oracle", 0
        elif head in ("secret", "roast") and sub:
            if sub not in EGGS:
                sys.exit(f"answers.txt:{ln}: unknown easter egg '{sub}' (use {', '.join(EGGS)})")
            rarity, mode, tag = "secret", "oracle" if head == "secret" else "roast", EGGS.index(sub)
        elif head in ("classic", "roast") and not sub:
            rarity, mode, tag = head, head, 0
        elif head == "mode" and sub in MODES:
            rarity, mode, tag = "card", sub, MODES.index(sub)
        elif head == "skill" and not sub:
            rarity, mode, tag = "skill", "oracle", 0
        elif head == "rickrolled" and not sub:
            rarity, mode, tag = "rickrolled", "roast", 0
        else:
            sys.exit(f"answers.txt:{ln}: unknown kind '{kind}'")
        needs_mood = rarity in ("common", "rare", "legendary", "glitch", "classic", "roast")
        if needs_mood and mood == "none":
            sys.exit(f"answers.txt:{ln}: '{kind}' needs a mood: yes, no or maybe")
        if not needs_mood and mood != "none":
            sys.exit(f"answers.txt:{ln}: '{kind}' doesn't take a mood")
        if not 1 <= len(lines) <= 3:
            sys.exit(f"answers.txt:{ln}: need 1 to 3 lines")
        bad = {c for c in "".join(lines) if c not in ANSWER_GLYPHS}
        if bad:
            sys.exit(f"answers.txt:{ln}: characters not in the font: {''.join(sorted(bad))}")
        answers.append({"rarity": rarity, "mode": mode, "tag": tag, "mood": mood, "lines": lines, "src": ln})
    return answers


def tri_width_at(y_rel: float) -> float:
    """Inside width of the triangle at y_rel pixels below the centroid."""
    return TRI_SIDE * (1 - (y_rel + TRI_H / 3) / TRI_H)


def layout(lines, size):
    """Best vertical offset for this text, or None if it can't fit at this size."""
    font = ImageFont.truetype(str(FONT_ANSWER), size)
    asc, desc = font.getmetrics()
    lh = asc + desc
    cap = font.getbbox("H")
    cap_top, cap_bot = cap[1], cap[3]          # relative to the line's top
    n = len(lines)
    block = n * lh + (n - 1) * LINE_SPACE
    widths = [font.getlength(t) for t in lines]
    best = None
    for dy in range(-40, 21):
        top = dy - block / 2
        slack = 1e9
        for i, w in enumerate(widths):
            lt = top + i * (lh + LINE_SPACE)
            ink_top, ink_bot = lt + cap_top, lt + cap_bot
            slack = min(slack, tri_width_at(ink_bot) - 2 * TEXT_PAD - w)
            slack = min(slack, (ink_top - (-TRI_H / 3)) - TEXT_PAD)
        if best is None or slack > best[0]:
            best = (slack, dy)
    if best[0] < 0:
        return None
    return best[1], best[0]


def fit_answers(answers):
    failures = []
    for a in answers:
        for idx, size in enumerate(ANSWER_SIZES):
            r = layout(a["lines"], size)
            if r:
                a["small"], a["dy"], a["slack"] = idx, r[0], r[1]
                break
        else:
            failures.append(a)
    if failures:
        for a in failures:
            print(f"answers.txt:{a['src']}: does not fit: {' / '.join(a['lines'])}", file=sys.stderr)
        sys.exit(1)


def c_str(s: str) -> str:
    out = []
    for ch in s:
        if ch == '"' or ch == "\\":
            out.append("\\" + ch)
        elif ord(ch) < 128:
            out.append(ch)
        else:
            out.append("".join(f"\\x{b:02X}" for b in ch.encode("utf-8")) + '""')
    return "".join(out)


def write_answers_h(answers):
    rows = []
    for a in answers:
        text = "\\n".join(c_str(t) for t in a["lines"])
        rows.append(f'    {{"{text}", {a["rarity"].upper()}, {a["small"]}, {a["dy"]}, '
                    f'{MODES.index(a["mode"])}, {a["tag"]}, {MOODS.index(a["mood"])}}},  // {" / ".join(a["lines"])}')
    counts = {k: sum(1 for a in answers if a["rarity"] == k) for k in RARITY_IDS}
    for k in ("common", "rare", "legendary", "glitch", "classic", "roast"):
        if counts[k] == 0:
            sys.exit(f"answers.txt: need at least one {k} answer")
    for mode, prefix in (("oracle", "secret"), ("roast", "roast")):
        for t, egg in enumerate(EGGS):
            if not any(a["rarity"] == "secret" and a["mode"] == mode and a["tag"] == t for a in answers):
                sys.exit(f"answers.txt: missing {prefix}:{egg}")
    for m in MODES:
        if not any(a["rarity"] == "card" and a["mode"] == m for a in answers):
            sys.exit(f"answers.txt: missing mode:{m}")
    for m, pool in (("oracle", ("common", "rare", "legendary")), ("classic", ("classic",)), ("roast", ("roast",))):
        for mood in ("yes", "no"):
            if not any(a["rarity"] in pool and a["mood"] == mood for a in answers):
                sys.exit(f"answers.txt: {m} mode needs at least one '{mood}' answer for the swipe rig")
    for k in ("skill", "rickrolled"):
        if counts[k] != 1:
            sys.exit(f"answers.txt: need exactly one '{k}' answer")
    body = "\n".join(rows)
    (ROOT / "answers_gen.h").write_text(f"""// Generated by tools/make_assets.py from answers.txt. Do not edit by hand.
#pragma once
#include <cstdint>

namespace m8 {{

enum Rarity : uint8_t {{
  COMMON = 0, RARE = 1, LEGENDARY = 2, GLITCH = 3,  // Oracle mode
  SECRET = 4,                                     // easter eggs (Oracle and Roast)
  CLASSIC = 5,                                    // Classic mode
  ROAST = 6,                                      // Roast mode
  CARD = 7,                                       // the card shown when switching modes
  SKILL = 8,                                      // the SKILL ISSUE special (swipe up)
  RICKROLLED = 9,                                 // the card after the rickroll clip
}};
enum Mood : uint8_t {{ MOOD_NONE = 0, MOOD_YES = 1, MOOD_NO = 2, MOOD_MAYBE = 3 }};
enum Mode : uint8_t {{ ORACLE = 0, CLASSIC_MODE = 1, ROAST_MODE = 2, MODE_COUNT = 3 }};
enum Egg : uint8_t {{ EGG_HARD = 0, EGG_SPAM = 1, EGG_NIGHT = 2, EGG_COUNT = 3 }};

struct Answer {{
  const char *text;  // lines separated by \\n
  uint8_t rarity;
  uint8_t small;     // 1 = use the smaller answer font
  int8_t dy;         // text block centre, pixels below the triangle's centroid
  uint8_t mode;      // Mode it belongs to
  uint8_t tag;       // SECRET: Egg; CARD: the Mode it announces
  uint8_t mood;      // Mood: what a swipe left (yes) or right (no) can rig
}};

static const Answer ANSWERS[] = {{
{body}
}};
static constexpr int N_ANSWERS = {len(answers)};

}}  // namespace m8
""", encoding="utf-8")
    return counts


def write_assets_h(spec_xy):
    (ROOT / "assets_gen.h").write_text(f"""// Generated by tools/make_assets.py. Do not edit by hand.
#pragma once

namespace m8 {{

static constexpr int BALL_R = {BALL_R};             // black ball radius
static constexpr int WINDOW_R = {WINDOW_R};         // liquid window radius
static constexpr int SPEC_X = {spec_xy[0]};          // specular highlight offset from centre
static constexpr int SPEC_Y = {spec_xy[1]};
static constexpr int TRI_IMG_DY = {TRI_IMG_DY};      // triangle image offset that centres its centroid
static constexpr int TRI_PIVOT_X = {TRI_W // 2};     // centroid inside the triangle image
static constexpr int TRI_PIVOT_Y = {round(TRI_CENTROID_Y)};
static constexpr float LIGHT_X = {LIGHT[0]:.4f}f, LIGHT_Y = {LIGHT[1]:.4f}f, LIGHT_Z = {LIGHT[2]:.4f}f;
static constexpr int DIE_N = {DIE_N};               // tumbling-die frames (die_frames.yaml)

}}  // namespace m8

// X-macro over the die frame image ids, for the on_boot lambda in magic-8-ball.yaml.
#define M8_DIE_FRAMES(X) {" ".join(f"X(img_die_{i:02d})" for i in range(DIE_N))}

namespace m8 {{

}}  // namespace m8
""", encoding="utf-8")


# ----------------------------------------------------------------------------------------
# Previews (Pillow approximations of what LVGL will draw)
# ----------------------------------------------------------------------------------------
PILL = {"rickrolled": ("ROASTED", "#FF9A6B"), "rare": ("RARE", "#C9A2FF"), "legendary": ("LEGENDARY", "#FFD86B"),
        "glitch": ("GLITCH", "#4DF3FF"), "secret": ("SECRET", "#6DFFB0"), "roast": ("ROASTED", "#FF9A6B")}
CARD_STYLE = {"oracle": "purple", "classic": "blue", "roast": "red"}


def style_for(a) -> str:
    """Which triangle an answer uses (mirrors start_rise_ in magic8.h)."""
    r = a["rarity"]
    if r == "card":
        return CARD_STYLE[a["mode"]]
    if r == "skill":
        return "skill"
    if r in ("roast", "rickrolled") or (r == "secret" and a["mode"] == "roast"):
        return "red"
    return {"legendary": "gold", "rare": "purple", "secret": "purple"}.get(r, "blue")


def pill_for(a):
    if a["rarity"] == "card":
        return f"MODE {MODES.index(a['mode']) + 1}/{len(MODES)}", "#FFFFFF"
    if a["rarity"] == "skill":
        return "DIAGNOSIS", "#8CFF6E"
    return PILL.get(a["rarity"])


def draw_answer(canvas: Image.Image, cx: float, cy: float, a, tris):
    """Draw triangle + text with the triangle centroid at (cx, cy)."""
    style = style_for(a)
    tri = tris[style]
    canvas.alpha_composite(tri, (round(cx - TRI_W / 2), round(cy + TRI_IMG_DY - TRI_IMG_H / 2)))
    size = ANSWER_SIZES[a["small"]]
    font = ImageFont.truetype(str(FONT_ANSWER), size)
    asc, desc = font.getmetrics()
    lh = asc + desc
    n = len(a["lines"])
    block = n * lh + (n - 1) * LINE_SPACE
    top = cy + a["dy"] - block / 2
    d = ImageDraw.Draw(canvas)
    color = TRI_STYLES[style][5]
    for i, t in enumerate(a["lines"]):
        w = font.getlength(t)
        d.text((cx - w / 2, top + i * (lh + LINE_SPACE)), t, font=font, fill=color)
    if pill_for(a):
        label, pc = pill_for(a)
        pf = ImageFont.truetype(str(FONT_PILL), 17)
        spaced = label
        tw = sum(pf.getlength(ch) for ch in spaced) + 5 * (len(spaced) - 1)
        py = cy - TRI_H / 3 - 34
        d.rounded_rectangle([cx - tw / 2 - 14, py - 13, cx + tw / 2 + 14, py + 13], radius=13, outline=pc, width=1)
        x = cx - tw / 2
        for ch in spaced:
            d.text((x, py - 10), ch, font=pf, fill=pc)
            x += pf.getlength(ch) + 5


def round_frame(img: Image.Image) -> Image.Image:
    m = Image.new("L", img.size, 0)
    ImageDraw.Draw(m).ellipse([0, 0, img.size[0] - 1, img.size[1] - 1], fill=255)
    out = Image.new("RGBA", img.size, (24, 24, 28, 255))
    out.paste(img, (0, 0), m)
    return out


def make_previews(answers, sphere, spec, spec_xy, disc, window, tris):
    screens = []
    # Idle: ball with the disc rolled slightly toward the viewer's lower right.
    s = Image.new("RGBA", (SCREEN, SCREEN), (0, 0, 0, 255))
    off = (SCREEN - BALL_IMG) // 2
    s.paste(sphere, (off, off))
    s.alpha_composite(disc.resize((DISC_IMG, int(DISC_IMG * 0.93))), (233 - DISC_IMG // 2 + 18, 233 - DISC_IMG // 2 + 22))
    s.alpha_composite(spec, (233 + spec_xy[0] - spec.size[0] // 2, 233 + spec_xy[1] - spec.size[1] // 2))
    screens.append(round_frame(s))
    picks = [next(a for a in answers if a["rarity"] == k)
             for k in ("common", "rare", "legendary", "glitch", "classic", "roast", "card", "skill")]
    for a in picks:
        s = Image.new("RGBA", (SCREEN, SCREEN), (0, 0, 0, 255))
        s.paste(window, (off, off))
        draw_answer(s, 233, 233, a, tris)
        screens.append(round_frame(s))
    gap = 24
    sheet = Image.new("RGBA", (len(screens) * (SCREEN + gap) + gap, SCREEN + 2 * gap), (24, 24, 28, 255))
    for i, sc in enumerate(screens):
        sheet.alpha_composite(sc, (gap + i * (SCREEN + gap), gap))
    sheet.convert("RGB").save(ASSETS / "preview.png")

    cols = 8
    cell_w, cell_h = 400, 360
    rows = math.ceil(len(answers) / cols)
    grid = Image.new("RGBA", (cols * cell_w, rows * cell_h), (0, 0, 0, 255))
    for i, a in enumerate(answers):
        cx = (i % cols) * cell_w + cell_w / 2
        cy = (i // cols) * cell_h + cell_h / 2 - 20
        draw_answer(grid, cx, cy, a, tris)
    grid.convert("RGB").save(ASSETS / "answers_sheet.png")


def main():
    ASSETS.mkdir(exist_ok=True)
    answers = parse_answers()
    fit_answers(answers)
    sphere, spec, spec_xy = make_ball()
    disc = make_disc()
    disc_roast = make_disc("#FF6A3D", (24, 6, 2, 255))
    window = make_window()
    tris = {k: make_triangle(k) for k in TRI_STYLES}
    sphere.save(ASSETS / "sphere.png")
    spec.save(ASSETS / "spec.png")
    disc.save(ASSETS / "disc8.png")
    disc_roast.save(ASSETS / "disc8_roast.png")
    window.save(ASSETS / "window.png")
    for k, im in tris.items():
        im.save(ASSETS / f"tri_{k}.png")
    (ASSETS / "die").mkdir(exist_ok=True)
    die = make_die_frames()
    for i, im in enumerate(die):
        im.save(ASSETS / "die" / f"die_{i:02d}.png")
    strip = Image.new("RGBA", (DIE_IMG * len(die), DIE_IMG), (0, 0, 0, 255))
    for i, im in enumerate(die):
        strip.alpha_composite(im, (i * DIE_IMG, 0))
    strip.convert("RGB").save(ASSETS / "die_sheet.png")
    write_die_yaml()
    counts = write_answers_h(answers)
    write_assets_h(spec_xy)
    make_previews(answers, sphere, spec, spec_xy, disc, window, tris)
    tight = sorted(answers, key=lambda a: a["slack"])[:3]
    print(f"{len(answers)} answers fit "
          f"({', '.join(f'{v} {k}' for k, v in counts.items())}); "
          f"{sum(a['small'] for a in answers)} use the small font")
    print("tightest: " + "; ".join(f"{' / '.join(a['lines'])} ({a['slack']:.0f}px)" for a in tight))
    print(f"spec overlay {spec.size} at {spec_xy}, triangle image dy {TRI_IMG_DY}")


if __name__ == "__main__":
    main()
