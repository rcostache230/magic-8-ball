"""Preview renders with the real board, the battery, the speaker and the firmware's idle screen.

A small z-buffer rasterizer (orthographic, 2x supersampled, smooth shaded) with an id buffer so
the screen disc can show the firmware's idle ball, plus an exploded view and a cut-away. Without
Waveshare's STEP in vendor/ the board is left out and everything else still renders.

    .venv/bin/python tools/render.py
"""
import sys

sys.path.insert(0, ".")
import numpy as np  # noqa: E402
from build123d import Box, Cylinder, Pos  # noqa: E402
from PIL import Image  # noqa: E402

import case as K  # noqa: E402

SHELL = np.array([0.24, 0.24, 0.26])     # black PLA, lifted so the shading reads
CARRIER = np.array([0.34, 0.34, 0.36])
GLASS = np.array([0.03, 0.035, 0.05])
PCB = np.array([0.10, 0.24, 0.58])       # Waveshare blue
PARTS = np.array([0.40, 0.40, 0.42])
CELL = np.array([0.70, 0.73, 0.78])
SPEAKER = np.array([0.55, 0.50, 0.42])
STRAP = np.array([0.20, 0.32, 0.62])
BG = np.array([0.94, 0.94, 0.95])
SIZE, SS = 900, 2
SCREEN_R = 440 * 43.76 / 466 / 2         # the 440 px ball on a 466 px, 43.76 mm panel


def screen_image():
    """The firmware's idle screen: assets/sphere.png with the 8 disc on it (plain black if absent)."""
    try:
        ball = Image.open(K.HERE.parent / "assets/sphere.png").convert("RGBA")
        disc = Image.open(K.HERE.parent / "assets/disc8.png").convert("RGBA")
    except OSError:
        return np.zeros((8, 8, 3))
    ball.alpha_composite(disc, ((ball.width - disc.width) // 2 + 10, (ball.height - disc.height) // 2 + 12))
    return np.asarray(ball.convert("RGB"), float) / 255


SCREEN_IMG = screen_image()


def tris(shape, tol):
    """Triangles plus per-corner normals averaged within each B-rep face (smooth, creases kept)."""
    verts, faces = shape.tessellate(tol, 0.1)
    if not faces:
        return np.zeros((0, 3, 3)), np.zeros((0, 3, 3))
    v = np.array([[p.X, p.Y, p.Z] for p in verts])
    f = np.array(faces)
    t = v[f]
    fn = np.cross(t[:, 1] - t[:, 0], t[:, 2] - t[:, 0])         # area-weighted
    vn = np.zeros_like(v)
    for j in range(3):
        np.add.at(vn, f[:, j], fn)
    vn /= np.linalg.norm(vn, axis=1, keepdims=True) + 1e-12
    return t, vn[f]


def moved(tn, dz):
    t, n = tn
    return t + np.array([0, 0, dz]), n


def board_parts(board, cut=None):
    out = []
    if board is None:
        return out
    for i, s in enumerate(board.solids()):
        if s.volume < 0.3:
            continue
        if cut is not None:
            sb = s.bounding_box()
            if sb.max.Y <= 0:
                continue
            if sb.min.Y < 0:
                s = s & cut
                if s is None or s.volume < 0.01:
                    continue
        col = PCB if i == 0 else GLASS if i in (664, 665) else PARTS
        out.append((tris(s, 0.12), col, 0))
    return out


def render(parts, path, view, up=(0, 1, 0), light=None, screen=None):
    view = np.asarray(view, float); view /= np.linalg.norm(view)       # object -> camera
    right = np.cross(up, view); right /= np.linalg.norm(right)
    upv = np.cross(view, right)
    light = view * 0.55 + np.array([-0.35, 0.45, -0.6]) if light is None else np.asarray(light, float)
    light /= np.linalg.norm(light)
    parts = [p for p in parts if len(p[0][0])]
    t = np.concatenate([tn[0] for tn, _, _ in parts])
    vn = np.concatenate([tn[1] for tn, _, _ in parts])
    base = np.concatenate([np.repeat(c[None], len(tn[0]), 0) for tn, c, _ in parts])
    pid = np.concatenate([np.full(len(tn[0]), k) for tn, _, k in parts])
    fn = np.cross(t[:, 1] - t[:, 0], t[:, 2] - t[:, 0])
    vn = np.where((fn @ view < 0)[:, None, None], -vn, vn)              # light the side we see
    half = (light + view) / np.linalg.norm(light + view)
    k = 0.30 + 0.52 * np.clip(vn @ light, 0, 1) + 0.18 * np.clip(vn @ view, 0, 1)
    spec = np.clip(vn @ half, 0, 1) ** 36 * 0.30
    col = np.clip(base[:, None, :] * k[..., None] + spec[..., None], 0, 1)   # per corner

    proj = np.stack([t @ right, t @ upv, t @ view], axis=-1)
    lo, hi = proj[..., :2].reshape(-1, 2).min(0), proj[..., :2].reshape(-1, 2).max(0)
    res = SIZE * SS
    scale = 0.86 * res / (hi - lo).max()
    off = (res - (hi - lo) * scale) / 2
    px = (proj[..., 0] - lo[0]) * scale + off[0]
    py = res - ((proj[..., 1] - lo[1]) * scale + off[1])
    z = proj[..., 2]

    img = np.tile(BG, (res, res, 1))
    zbuf = np.full((res, res), -np.inf)
    ibuf = np.full((res, res), -1)
    for i in range(len(t)):
        x, y, d = px[i], py[i], z[i]
        x0, x1 = int(max(np.floor(x.min()), 0)), int(min(np.ceil(x.max()), res - 1))
        y0, y1 = int(max(np.floor(y.min()), 0)), int(min(np.ceil(y.max()), res - 1))
        if x1 < x0 or y1 < y0:
            continue
        gx, gy = np.meshgrid(np.arange(x0, x1 + 1) + 0.5, np.arange(y0, y1 + 1) + 0.5)
        den = (y[1] - y[2]) * (x[0] - x[2]) + (x[2] - x[1]) * (y[0] - y[2])
        if abs(den) < 1e-9:
            continue
        a = ((y[1] - y[2]) * (gx - x[2]) + (x[2] - x[1]) * (gy - y[2])) / den
        b = ((y[2] - y[0]) * (gx - x[2]) + (x[0] - x[2]) * (gy - y[2])) / den
        c = 1 - a - b
        inside = (a >= -1e-6) & (b >= -1e-6) & (c >= -1e-6)
        if not inside.any():
            continue
        depth = a * d[0] + b * d[1] + c * d[2]
        sub = zbuf[y0:y1 + 1, x0:x1 + 1]
        win = inside & (depth > sub)
        sub[win] = depth[win]
        img[y0:y1 + 1, x0:x1 + 1][win] = (a[win, None] * col[i, 0] + b[win, None] * col[i, 1]
                                          + c[win, None] * col[i, 2])
        ibuf[y0:y1 + 1, x0:x1 + 1][win] = pid[i]

    if screen is not None:                                   # paint the idle ball on the glass
        ys, xs = np.nonzero(ibuf == screen)
        if len(ys):
            u = (xs + 0.5 - off[0]) / scale + lo[0]
            v = (res - (ys + 0.5) - off[1]) / scale + lo[1]
            p3 = u[:, None] * right + v[:, None] * upv + zbuf[ys, xs][:, None] * view
            # Looking at the window from -Z, the viewer's right is -X.
            cu = (-p3[:, 0] / (2 * SCREEN_R) + 0.5) * SCREEN_IMG.shape[1]
            cv = (-p3[:, 1] / (2 * SCREEN_R) + 0.5) * SCREEN_IMG.shape[0]
            ok = (cu >= 0) & (cu < SCREEN_IMG.shape[1]) & (cv >= 0) & (cv < SCREEN_IMG.shape[0])
            pix = np.zeros((len(ys), 3))
            pix[ok] = SCREEN_IMG[cv[ok].astype(int), cu[ok].astype(int)]
            img[ys, xs] = pix * 0.92 + 0.04
    out = Image.fromarray((img * 255).astype(np.uint8)).resize((SIZE, SIZE), Image.LANCZOS)
    out.save(path)
    print("wrote", path)


def main():
    front, carrier, cap = K.build_front(), K.build_carrier(), K.build_cap()
    board = K.place_board()
    bat, spk, st = Pos(0, 0, 1.0) * K.cell(), K.speaker(), K.strap()   # cell on 1 mm of tape
    screen_disc = Pos(0, 0, K.RECESS - 0.02) * Cylinder(43.76 / 2, 0.01)
    SCREEN = 9
    shell = [(tris(front, 0.05), SHELL, 1), (tris(cap, 0.05), SHELL, 2)]
    brd = board_parts(board)
    scr = [(tris(screen_disc, 0.02), GLASS, SCREEN)]
    out = K.OUT

    render(shell + brd + scr, out / "preview-front.png", view=(0.62, 0.30, -1.0), screen=SCREEN)
    render(shell + brd + scr, out / "preview-face.png", view=(0, 0, -1), screen=SCREEN)
    render(shell + brd, out / "preview-side.png", view=(-1, 0, 0))
    render(shell + [(tris(spk, 0.05), SPEAKER, 5)], out / "preview-speaker.png", view=(1, -0.12, 0.05),
           light=(1.0, 0.3, -0.4))
    render(shell, out / "preview-back.png", view=(0.12, 0.16, 1.0), light=(-0.45, 0.55, 1.0))

    # Exploded along the assembly axis: board, speaker, carrier, battery and cap come in from the back.
    ex = [(tris(front, 0.05), SHELL, 1)]
    for p, c, k in board_parts(board):
        ex.append((moved(p, 40.0), c, k))
    ex += [(tris(Pos(0, 0, 52) * spk, 0.05), SPEAKER, 5),
           (tris(Pos(0, 0, 68) * carrier, 0.05), CARRIER, 3),
           (tris(Pos(0, 0, 92) * bat, 0.05), CELL, 4),
           (tris(Pos(0, 0, 92) * st, 0.05), STRAP, 6),
           (tris(Pos(0, 0, 128) * K.cap_at(cap, K.TWIST), 0.05), SHELL, 2)]
    render(ex, out / "preview-exploded.png", view=(-1.0, 0.42, 0.62))

    # Cut-away on the Y = 0 plane (through the USB-C slot and the speaker), looking at the cut.
    cut = Pos(0, 60, 40) * Box(200, 120, 200)
    sec = [(tris(front & cut, 0.05), SHELL, 1), (tris(cap & cut, 0.05), SHELL, 2),
           (tris(carrier & cut, 0.05), CARRIER, 3), (tris(bat & cut, 0.05), CELL, 4),
           (tris(spk & cut, 0.05), SPEAKER, 5), (tris(st & cut, 0.05), STRAP, 6)]
    sec += board_parts(board, cut)
    render(sec, out / "preview-section.png", view=(0.0, -1.0, 0.0), up=(-1, 0, 0),
           light=(0.3, -1.0, -0.4))


if __name__ == "__main__":
    main()
