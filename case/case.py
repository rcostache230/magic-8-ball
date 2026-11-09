"""Magic 8 Ball shell for the Waveshare ESP32-S3-Touch-AMOLED-1.75 (466 x 466 round AMOLED).

An 88 mm black ball in three printed parts. All three print without supports on one Bambu Lab
A1 mini plate (180 mm cube):

  front    the window half. The glass sits in a bore with FIT = 0.2 mm a side, 0.3 mm below a flat
           2.5 mm bezel: it reads as flush, but a ball set down face-first lands on the bezel, not
           the glass. A 45-degree bevel joins the bezel to the sphere, which is what lets this half
           print window-down on the textured plate. Behind the glass is a solid frame ring whose
           back face (the shelf) carries the board. The USB-C slot and the PWR/BOOT pin holes go
           through that ring; a spigot with three helical ridges stands up from the rim. On the
           PWR/BOOT side a sleeve holds the 20 x 30 x 5 mm box speaker face-out behind an oval
           grille: it slides down into the sleeve and a sprung latch arm clicks over its top edge.
  carrier  a flat plate behind the board. 3 x M2x6 into the board's own M2 standoffs pull the board
           onto it (that sets the glass depth), 3 x M2x8 fix it to the shelf, and the 35 x 65 x 9 mm
           cell sits on rails and ribs on its back between two side walls, held by a hook-and-loop
           strap that runs through slots in those walls (tape is optional).
  cap      the back half, with a raised "8" in a recessed badge. Three keyed lugs: it goes on one
           way only, and a ~40 degree clockwise turn (seen from the 8) draws the seam shut on
           helical ramps, so the seam is preloaded and nothing rattles when it is shaken.

No screw is visible from outside; the openings are the USB-C slot, two 2 mm pin holes and the grille.

Coordinates: Z is depth behind the window face (window at Z = 0, the 8 at Z ~ 79). X and Y lie in
the window plane and are the board STEP's x and z, so the USB-C points to -X and PWR/BOOT sit at
+X. The ball's centre is at Z = ZC. "u" is height above the seam (u = Z - ZC); "psi" is an angle
measured in the closing direction of the cap.

    .venv/bin/python case.py      # writes out/*.3mf, *.stl, *.step and runs the fit check

The board fit checks need Waveshare's STEP in vendor/model3d/ (see README); without it the parts are
still built and exported, and every other check still runs.
"""

import math
import os
import pathlib
import sys

from build123d import (
    Align, Box, Circle, Cylinder, Line, Location, Mesher, Plane, Polyline, Pos, Rot, SlotOverall,
    Text, ThreePointArc, Vector, export_step, export_stl, extrude, import_step, loft, make_face,
    revolve, Axis,
)

HERE = pathlib.Path(__file__).resolve().parent
OUT = HERE / "out"
BOARD_STEP = pathlib.Path(os.environ.get("BOARD_STEP", HERE / "vendor/model3d/ESP32-S3-Touch-AMOLED-1_75.stp"))
BOARD_STEP_URL = "https://www.waveshare.com/wiki/ESP32-S3-Touch-AMOLED-1.75"   # "3D drawing" zip
FONT = HERE.parent / "fonts/Barlow-Black.ttf"          # the firmware's "8" font

# --- parameters (mm) ------------------------------------------------------------------------
D = 88.0                    # ball diameter
WALL = 2.4                  # shell wall: 6 perimeters at 0.4 mm
FIT = 0.2                   # glass bore clearance per side (proven on an A1 mini print)
RECESS = 0.3                # glass front below the bezel face
BEZEL = 2.5                 # flat ring around the glass, then the 45-degree bevel

# Board: Waveshare's drawing (vendor/drawing) and STEP (vendor/model3d), standard version (no case).
GLASS_D = 48.96             # touch-panel cover glass, 1.10 thick, 0.5 mm round on its front edge
BOARD_FRONT_Y = 2.77        # STEP: the glass front face is at y = +2.77, the board looks along +y
STANDOFF_DEPTH = 10.40      # glass front to the M2 standoff ends (drawing: 10.40)
STANDOFFS = ((13.75, 14.70), (-13.75, 14.70), (0.0, -20.50))   # X, Y of the three M2 x H3.5 nuts
USB_DEPTH = 8.57            # USB-C opening centre behind the glass front; its mouth is at X = -23.79
USB_MOUTH_R = 23.79
BUTTONS = {"PWR": (-33.7, 22.85, 7.80), "BOOT": (33.9, 22.78, 7.80)}   # angle, tip radius, depth
# Parts on the back of the PCB that stand proud of the standoff plane: X0, X1, Y0, Y1, depth.
HEADER = (-10.43, 10.43, 17.48, 19.88, 12.70)
BAT_CONN = (-10.63, -2.98, -20.12, -15.92, 11.70)   # MX1.25 2P, vertical: the plug goes in from behind
SPK_CONN = (2.67, 10.32, -20.12, -15.92, 11.70)

# USB-C cable: the slot takes an overmold up to this size (rounded ends), USB_CLEAR all round.
USB_PLUG = (12.4, 7.0)
USB_PLUG_LEN = 20.0
USB_CLEAR = 0.4
PIN_D = 2.0                 # PWR / BOOT: press with a straightened paperclip

# Carrier and its screws.
CAR_T = 3.0                 # M2x6 into the standoffs then engages 3.0 of their 3.5 mm thread
CAR_R = 32.5
FRAME_SCREW_R = 29.0
FRAME_SCREW_ANG = (55.0, 125.0, 270.0)   # clear of the USB-C slot, the pin holes and the speaker
FRAME_PILOT = 1.7           # M2 self-tapping into PLA
FRAME_PILOT_DEPTH = 6.0
M2_CLEAR = 2.4              # board screws
CAR_FLOAT_HOLE = 2.6        # frame screws: lets the carrier float so the glass centres in its bore
CAR_CUT_X = 25.0            # the carrier stops here on the speaker side

# Battery: the cell in hand, 35 x 65 x 9 mm (1S LiPo, MX1.25 plug). Envelope = cell + 0.5 mm of
# swell a side, + 1 mm of foam tape under it.
BAT_CELL = (35.0, 65.0, 9.0)
BAT = (BAT_CELL[0] + 1.0, BAT_CELL[1] + 1.0, BAT_CELL[2] + 1.0 + 0.5)   # X width, Y length, Z
BAT_LEAD = (18.0, 4.0)      # room for the lead where it leaves the -Y end: width, length
BAT_CLEAR = 1.0             # envelope corners to the shell, at least
BAT_RAIL_H = 4.0            # side walls rise this far past the tape face
# Battery strap: a 12 mm hook-and-loop strap (or a zip tie) under the cell, out through a slot in
# each side wall, up the sides and over the top. Snug, not tight: the cell keeps its swell room.
STRAP_SLOT = (13.0, 2.6)    # slot width (along the cell) and height
STRAP_ENV = (12.0, 1.5)     # strap width and thickness used by the fit check

# Speaker: an enclosed 20 x 30 x 5 mm box speaker on the SPK connector, its face against an oval
# grille in the side of the window half (the PWR/BOOT side). The face is vertical, so the speaker
# slides straight down into its sleeve through the open back of the ball, past a sprung latch arm
# that then holds its top edge; lips hold the face, ribs take up the play, nothing is glued.
SPK = (30.0, 20.0, 5.0)     # length (around the ball), height (along Z), thickness
SPK_AZ = 0.0                # azimuth of the grille, degrees from +X (the PWR/BOOT side)
SPK_X = 33.2                # face plane, mm from the ball's axis
SPK_Z = 27.0                # centre height (depth behind the window face)
SPK_FIT = 0.2
SPK_WALL = 1.6
SPK_LIP = 2.0               # lips over the face at each end
GRILLE = (1.6, 2.6, 11.5, 7.5, -2.0)   # hole dia, hex pitch, oval half-axes (around, along Z), centre

# Twist lock. The front part's spigot carries three helical ridges, the cap three lugs.
CAP_IN = 41.0               # cap wall inside radius at the seam
SPIG_IN, SPIG_ROOT, SPIG_PILOT = 37.2, 39.2, CAP_IN - FIT     # the pilot centres the cap, FIT a side
PILOT_H, SPIG_H = 0.8, 9.6
THREAD_H = 1.5              # ridge / lug height, 45-degree flanks both sides (no overhangs)
LUG_C = RIDGE_C = 1.2       # crest widths
LEAD = 10.0                 # helix lead: 0.028 mm of draw per degree of turn (self-locking)
TWIST = 40.0                # degrees from "pushed on" to "closed"
CLOSE = -1                  # closing turn is clockwise seen from the 8 (righty-tighty)
GAPS = ((30.0, 40.0, 34.0), (150.0, 28.0, 22.0), (270.0, 28.0, 22.0))   # psi centre, gap, lug width
A_NOM = 1.6                 # lug root height above the seam at its centre, when closed
FLAT_AFTER = 25.0           # ridges stop ramping this far past the nominal closed angle
PRELOAD = 0.0               # + lowers the ridges: the cap bites sooner and tighter (0.1 = 3.6 deg)

# Badge: a flat at the back pole, a recessed field and a raised 8 flush with the flat.
BADGE_R, BADGE_REC_R, BADGE_DEPTH, BADGE_FONT = 15.0, 12.5, 0.8, 23.5

# --- derived ---------------------------------------------------------------------------------
R = D / 2
RI = R - WALL
SQ2 = math.sqrt(2)
BORE_R = GLASS_D / 2 + FIT
R_W = BORE_R + BEZEL
ZC = R * SQ2 - R_W                 # the bevel is tangent to the sphere at 45 degrees
RT, ZT = R / SQ2, ZC - R / SQ2
SHELF = RECESS + STANDOFF_DEPTH    # carrier front face = frame back face = standoff ends
USB_Z = RECESS + USB_DEPTH
K = LEAD / 360.0
LUG_CREST = CAP_IN - THREAD_H
RIDGE_CREST = SPIG_ROOT + THREAD_H
B_NOM = A_NOM + THREAD_H + LUG_C - (LUG_CREST - SPIG_ROOT) - PRELOAD   # flanks touch when closed
U_F = math.sqrt(R ** 2 - BADGE_R ** 2)                        # badge flat, above the seam
U_CEIL = U_F - BADGE_DEPTH - 1.6


def _bat_z():
    """Battery face depth: as far forward as the corner clearance to the shell allows."""
    half_diag = math.hypot(BAT[0] / 2, BAT[1] / 2) + BAT_CLEAR
    z = ZC - math.sqrt(RI ** 2 - half_diag ** 2)
    return math.ceil(z * 10) / 10


BAT_Z = _bat_z()


def r_inner(z):
    return math.sqrt(RI ** 2 - (ZC - z) ** 2)


def r_outer(z):
    """Outside radius of the front half at depth z (bevel, then sphere)."""
    return R_W + z if z < ZT else math.sqrt(R ** 2 - (ZC - z) ** 2)


def P(r, z):
    return Vector(r, 0, z)


def arc(radius, zc, a0, a1, sign):
    """Arc of a sphere's meridian: angle a from the equator, z = zc + sign * radius * sin(a)."""
    pts = [P(radius * math.cos(a), zc + sign * radius * math.sin(a)) for a in (a0, (a0 + a1) / 2, a1)]
    return ThreePointArc(*pts)


def poly(*pts):
    return [Line(a, b) for a, b in zip(pts, pts[1:])]


def spin(edges):
    return revolve(make_face(edges), axis=Axis.Z)


def gbox(x0, x1, y0, y1, z0, z1):
    return Pos((x0 + x1) / 2, (y0 + y1) / 2, (z0 + z1) / 2) * Box(x1 - x0, y1 - y0, z1 - z0)


def theta(psi):
    return CLOSE * psi


# --- twist lock ------------------------------------------------------------------------------
def _profile(r_root, r_crest, base, crest, into_wall):
    """Trapezoid (45-degree flanks) in the XZ plane, sunk 0.4 into its wall so it fuses."""
    H = abs(r_crest - r_root)
    w = r_root + into_wall
    pts = [(w, base), (r_root, base), (r_crest, base + H), (r_crest, base + H + crest),
           (r_root, base + 2 * H + crest), (w, base + 2 * H + crest)]
    return make_face(Polyline(*[(x, 0, ZC + z) for x, z in pts], close=True))


def _helical(r_root, r_crest, crest, into_wall, psi0, psi1, base_at, step=3.0):
    n = max(3, int(abs(psi1 - psi0) / step) + 1)
    secs = []
    for i in range(n):
        psi = psi0 + (psi1 - psi0) * i / (n - 1)
        secs.append(Rot(0, 0, theta(psi)) * _profile(r_root, r_crest, base_at(psi), crest, into_wall))
    return loft(secs)


def ridge_plan():
    """(start, nominal, end) psi of each spigot ridge: they fill the circle between the gaps."""
    plans = []
    for i, (g, gw, _) in enumerate(GAPS):
        g2, gw2, _ = GAPS[(i + 1) % 3]
        g2 += 360 if g2 < g else 0
        plans.append((g + gw / 2, g + TWIST, g2 - gw2 / 2))
    return plans


def ridge_base(psi, nominal):
    return B_NOM - K * min(psi - nominal, FLAT_AFTER)


def build_ridges():
    solids = []
    for start, nom, end in ridge_plan():
        mid = min(nom + FLAT_AFTER, end)
        solids.append(_helical(SPIG_ROOT, RIDGE_CREST, RIDGE_C, -0.4, start, mid,
                               lambda p, n=nom: ridge_base(p, n)))
        if end > mid + 0.5:        # constant-height tail: a plain revolve
            face = Rot(0, 0, theta(mid)) * _profile(SPIG_ROOT, RIDGE_CREST, ridge_base(mid, nom), RIDGE_C, -0.4)
            solids.append(revolve(face, axis=Axis.Z, revolution_arc=CLOSE * (end - mid)))
    return solids


def build_lugs():
    solids = []
    for g, _, lw in GAPS:
        c = g + TWIST
        solids.append(_helical(CAP_IN, LUG_CREST, LUG_C, 0.4, c - lw / 2, c + lw / 2,
                               lambda p, c=c: A_NOM - K * (p - c)))
    return solids


def cap_band():
    """Height up to which the cap's wall must stay at CAP_IN so every ridge crest passes."""
    top = max(ridge_base(s, n) for s, n, _ in ridge_plan()) + THREAD_H + RIDGE_C
    return top + 0.4


# --- front (window half) ---------------------------------------------------------------------
def front_profiles():
    """Solids of revolution: the window half's outside envelope (with the spigot) and its cavity."""
    be = math.asin(0.5 / R)
    outer = (poly(P(0, 0), P(R_W, 0), P(RT, ZT))
             + [arc(R, ZC, math.pi / 4, be, -1)]
             + poly(P(R * math.cos(be), ZC - 0.5), P(R - 0.5, ZC), P(SPIG_PILOT, ZC),
                    P(SPIG_PILOT, ZC + PILOT_H), P(SPIG_ROOT, ZC + PILOT_H + SPIG_PILOT - SPIG_ROOT),
                    P(SPIG_ROOT, ZC + SPIG_H - 0.5), P(SPIG_ROOT - 0.5, ZC + SPIG_H),
                    P(0, ZC + SPIG_H), P(0, 0)))
    # Cavity: the bore (0.4 chamfer at the face), the frame's back face at SHELF, the inner sphere,
    # then a 45-degree thickening that turns the shell into the spigot at the seam.
    tb = (-SPIG_IN + math.sqrt(2 * RI ** 2 - SPIG_IN ** 2)) / 2
    cavity = (poly(P(0, -1), P(BORE_R + 1.4, -1), P(BORE_R, 0.4), P(BORE_R, SHELF), P(r_inner(SHELF), SHELF))
              + [arc(RI, ZC, math.asin((ZC - SHELF) / RI), math.asin(tb / RI), -1)]
              + poly(P(SPIG_IN + tb, ZC - tb), P(SPIG_IN, ZC), P(SPIG_IN, ZC + SPIG_H + 1),
                     P(0, ZC + SPIG_H + 1), P(0, -1)))
    return spin(outer), spin(cavity)


def build_front():
    envelope, cavity = front_profiles()
    front = envelope - cavity
    for r in build_ridges():
        front += r
    front = add_speaker_mount(front, envelope, cavity)

    # USB-C: a stadium slot from inside the bore out through the side.
    w, h = USB_PLUG[0] + 2 * USB_CLEAR, USB_PLUG[1] + 2 * USB_CLEAR
    slot = Plane.YZ.offset(-(USB_MOUTH_R - 1.5)) * Pos(0, USB_Z) * SlotOverall(w, h)
    front -= extrude(slot, amount=40, dir=(-1, 0, 0))
    # PWR / BOOT: radial pin holes aimed at the side-press switch tips.
    for ang, rtip, depth in BUTTONS.values():
        d = Vector(math.cos(math.radians(ang)), math.sin(math.radians(ang)), 0)
        pl = Plane(origin=d * (rtip + 0.4) + Vector(0, 0, RECESS + depth), x_dir=(0, 0, 1), z_dir=d)
        front -= extrude(pl * Circle(PIN_D / 2), amount=30)
    # Pilot holes for the carrier's M2 self-tapping screws.
    for a in FRAME_SCREW_ANG:
        x, y = FRAME_SCREW_R * math.cos(math.radians(a)), FRAME_SCREW_R * math.sin(math.radians(a))
        front -= Pos(x, y, SHELF - FRAME_PILOT_DEPTH / 2 + 0.05) * Cylinder(FRAME_PILOT / 2, FRAME_PILOT_DEPTH + 0.1)
    return front


# --- speaker sleeve --------------------------------------------------------------------------
def spk_loc():
    """Speaker frame: x around the ball, y up (+Z), z out through the grille; origin at the face centre."""
    a = math.radians(SPK_AZ)
    n = Vector(math.cos(a), math.sin(a), 0)
    return Location(Plane(origin=n * SPK_X + Vector(0, 0, SPK_Z), x_dir=(-math.sin(a), math.cos(a), 0), z_dir=n))


def lbox(x0, x1, y0, y1, z0, z1):
    return spk_loc() * gbox(x0, x1, y0, y1, z0, z1)


def speaker(dy=0.0, dz=0.0, dx=0.0):
    """The speaker seated (on the bottom stop, face on the lips), optionally moved in its frame."""
    L, H, T = SPK[0] / 2, SPK[1] / 2, SPK[2]
    return lbox(-L + dx, L + dx, -H + dy, H + dy, -T + dz, dz)


def grille_holes():
    """Hex-packed holes inside an oval, as (x, y) in the speaker frame."""
    d, pitch, rx, ry, yc = GRILLE
    pts, row, y = [], 0, -ry
    while y <= ry + 1e-6:
        off = pitch / 2 if row % 2 else 0.0
        x = -rx - pitch + off
        while x <= rx + pitch:
            if (x / rx) ** 2 + (y / ry) ** 2 <= 1.0:
                pts.append((x, y + yc))
            x += pitch
        row += 1
        y += pitch * math.sqrt(3) / 2
    return pts


def add_speaker_mount(front, envelope, cavity):
    L, H, T = SPK[0] / 2, SPK[1] / 2, SPK[2]
    f, w = SPK_FIT, SPK_WALL
    xi, xo = L + f, L + f + w                  # cavity half-length, outside of the side walls
    zb = -T - f                                # back of the cavity
    arm0 = zb - 1.0 - 1.4                      # latch arm: 1.4 thick, 1.0 behind the speaker
    top = 14.0                                 # anything "out" is trimmed by the envelope
    yb = -H - w                                # underside of the bottom stop
    body = (lbox(-xo, -xi, yb, H + 2.5, zb - 1.2, top) + lbox(xi, xo, yb, H + 2.5, zb - 1.2, top)   # sides
            + lbox(-xo, xo, yb, -H, arm0, top)                                          # bottom stop
            + lbox(-xi, -(xi - SPK_LIP), -H, H + 2.5, 0, top)                           # face lips, out
            + lbox(xi - SPK_LIP, xi, -H, H + 2.5, 0, top)                               #   to the shell
            + lbox(-xi, -(xi - 2.5), -H, H, zb - 1.2, zb) + lbox(xi - 2.5, xi, -H, H, zb - 1.2, zb)  # back rails
            + lbox(-xo, xo, H + f, H + f + w, 0.2, top)                                 # chamber roof
            + lbox(-4, 4, yb, H + 3.2, arm0, arm0 + 1.4))                               # latch arm
    # Foot: fills from the shelf up to the bottom stop so the sleeve is printed on something.
    foot = lbox(-xo, xo, -60, yb + 0.01, arm0, top) & gbox(-60, 60, -60, 60, SHELF - 0.1, 60)
    body = (body + foot) & envelope
    front += body
    # The speaker's seat and the path it slides down; the chamber in front of its face.
    front -= lbox(-xi, xi, -H, H + 40, zb, 0)
    front -= lbox(-(xi - SPK_LIP), xi - SPK_LIP, -H, H + f, 0, top) & cavity
    # Lead notches (whichever end the lead leaves from), at the back half of the thickness.
    for sx in (1, -1):
        front -= lbox(min(sx * (xi - 0.1), sx * (xo + 0.1)), max(sx * (xi - 0.1), sx * (xo + 0.1)), -3, 3, zb, -2.2)
    front -= lbox(-5, 5, yb - 0.1, -H + 0.1, zb, -2.2)
    # Grille.
    d = GRILLE[0]
    for x, y in grille_holes():
        front -= spk_loc() * Pos(x, y, top / 2) * Cylinder(d / 2, top + 1)
    # Crush ribs (touching the speaker: they take up the print's slack) and the latch hook.
    for sx in (1, -1):
        def span(a, b):
            return (min(sx * a, sx * b), max(sx * a, sx * b))
        front += lbox(*span(xi - 1.6, xi - 0.8), -H + 1, H - 1.5, zb, -T)               # back
        for y0, y1 in ((-H + 1, -4.0), (4.0, H - 1.5)):
            front += lbox(*span(L, xi), y0, y1, -4.0, -1.0)                             # sides
    # Hook: a square face 0.05 mm above the speaker's top edge (its underside is a 2 mm overhang
    # off the arm, never a floating island when printed window-down), with a 45-degree lead-in on
    # top so the speaker pushes the arm back on its way in.
    hook = [(H + 0.05, arm0 + 1.4), (H + 0.05, -T + 0.8), (H + 0.9, -T + 0.8), (H + 3.2, arm0 + 1.4)]
    face = make_face(Polyline(*[(-4, y, z) for y, z in hook], close=True))
    front += spk_loc() * extrude(face, amount=8, dir=(1, 0, 0))
    return front


# --- carrier ---------------------------------------------------------------------------------
def build_carrier():
    z0, z1 = SHELF, SHELF + CAR_T
    car = Pos(0, 0, z0) * Cylinder(CAR_R, CAR_T, align=(Align.CENTER, Align.CENTER, Align.MIN))
    m = 0.5                                                           # margin round board parts
    hx0, hx1, hy0, hy1, _ = HEADER
    car -= gbox(hx0 - 0.9, hx1 + 0.9, hy0 - m, hy1 + 0.7, z0 - 1, z1 + 1)
    bx0, bx1, by0, by1, _ = BAT_CONN
    car -= gbox(bx0 - 1.0, bx1 + 0.4, by0 - 1.1, by1 + 0.7, z0 - 1, z1 + 1)   # room for the plug
    car -= gbox(-9.6, -5.6, -CAR_R - 2, by1, z0 - 1, z1 + 1)                  # wire slot to the edge
    sx0, sx1, sy0, sy1, _ = SPK_CONN
    car -= gbox(sx0 - m, sx1 + 0.9, sy0 - 1.1, sy1 + 0.7, z0 - 1, z1 + 1)
    car -= gbox(5.6, 9.6, -CAR_R - 2, sy1, z0 - 1, z1 + 1)                    # speaker lead slot
    car -= gbox(CAR_CUT_X, CAR_R + 2, -CAR_R - 2, CAR_R + 2, z0 - 1, z1 + 1)  # room for the speaker
    half = USB_PLUG[0] / 2 + 0.8                                              # cable overmold
    car -= gbox(-CAR_R - 2, -(USB_MOUTH_R - 0.8), -half, half, z0 - 1, z1 + 1)
    for x, y in STANDOFFS:
        car -= Pos(x, y, (z0 + z1) / 2) * Cylinder(M2_CLEAR / 2, CAR_T + 2)
    for a in FRAME_SCREW_ANG:
        x, y = FRAME_SCREW_R * math.cos(math.radians(a)), FRAME_SCREW_R * math.sin(math.radians(a))
        car -= Pos(x, y, (z0 + z1) / 2) * Cylinder(CAR_FLOAT_HOLE / 2, CAR_T + 2)
    # Battery cradle. The cell rests on two edge rails and three split ribs; two side walls stop
    # it sliding sideways and sit at the swell envelope, so a cell that swells 0.5 mm a side still
    # fits. A strap goes under the cell (in the gap between the rib halves), out through a slot at
    # the foot of each wall, up the sides and over the top. All of it stays clear of the board
    # screws' heads, and the lead runs back under the cell's -Y end to the BAT cutout.
    wall_in = BAT[0] / 2
    sw, sh = STRAP_SLOT
    for sx in (1, -1):
        def span(a, b):
            return (min(sx * a, sx * b), max(sx * a, sx * b))
        car += gbox(*span(16.0, wall_in + 1.2), -14, 14, z1 - 0.1, BAT_Z)
        car += gbox(*span(wall_in, wall_in + 1.2), -14, 14, z1 - 0.1, BAT_Z + BAT_RAIL_H)
        car -= gbox(*span(15.5, wall_in + 1.5), -sw / 2, sw / 2, z1 - 0.2, z1 + sh)
    for x in (-9.0, 0.0, 9.0):
        car += gbox(x - 2.0, x + 2.0, -14, -sw / 2 - 0.5, z1 - 0.1, BAT_Z)
        car += gbox(x - 2.0, x + 2.0, sw / 2 + 0.5, 16, z1 - 0.1, BAT_Z)
    return car


# --- cap (back half) -------------------------------------------------------------------------
def build_cap():
    ge = math.asin(0.5 / R)
    outer = (poly(P(0, ZC), P(R - 0.5, ZC), P(R * math.cos(ge), ZC + 0.5))
             + [arc(R, ZC, ge, math.acos(BADGE_R / R), +1)]
             + poly(P(BADGE_R, ZC + U_F), P(0, ZC + U_F), P(0, ZC)))
    band = cap_band()
    rb = math.sqrt(RI ** 2 - band ** 2)
    rc = math.sqrt(RI ** 2 - U_CEIL ** 2)
    cavity = (poly(P(0, ZC - 1), P(CAP_IN + 1.4, ZC - 1), P(CAP_IN, ZC + 0.4), P(CAP_IN, ZC + band),
                   P(rb, ZC + band))
              + [arc(RI, ZC, math.asin(band / RI), math.asin(U_CEIL / RI), +1)]
              + poly(P(rc, ZC + U_CEIL), P(0, ZC + U_CEIL), P(0, ZC - 1)))
    cap = spin(outer) - spin(cavity)
    for lug in build_lugs():
        cap += lug
    # Badge: recessed field, raised 8 flush with the flat.
    zf = ZC + U_F
    cap -= Pos(0, 0, zf - BADGE_DEPTH) * Cylinder(BADGE_REC_R, 5, align=(Align.CENTER, Align.CENTER, Align.MIN))
    if not FONT.exists():
        print(f"  note: {FONT} not found, so the 8 uses build123d's default font instead of Barlow Black")
    txt = Text("8", font_size=BADGE_FONT, font_path=str(FONT) if FONT.exists() else None)
    c = txt.bounding_box().center()
    txt = Pos(-c.X, -c.Y) * txt
    cap += extrude(Pos(0, 0, zf - BADGE_DEPTH - 0.05) * txt, amount=BADGE_DEPTH + 0.05)
    return cap


# --- board, battery, cable -------------------------------------------------------------------
def place_board():
    """Waveshare's STEP, in place; None (with a note) if vendor/ hasn't been downloaded."""
    if not BOARD_STEP.exists():
        print(f"  board STEP not found at {BOARD_STEP}: skipping the board checks.\n"
              f"  To run them, download the \"3D drawing\" zip from {BOARD_STEP_URL}\n"
              f"  and put ESP32-S3-Touch-AMOLED-1_75.stp in vendor/model3d/.")
        return None
    board = import_step(str(BOARD_STEP))
    return Pos(0, 0, RECESS + BOARD_FRONT_Y) * Rot(-90, 0, 0) * board


def battery():
    bw, bl, bt = BAT
    cell = gbox(-bw / 2, bw / 2, -bl / 2, bl / 2, BAT_Z, BAT_Z + bt)
    lw, ll = BAT_LEAD                                                     # the lead bending out
    lead = gbox(-lw / 2, lw / 2, -bl / 2 - ll, -bl / 2 + 0.1, BAT_Z + 1.5, BAT_Z + bt - 1.5)
    return cell + lead


def cell(dx=0.0):
    """The bare 35 x 65 x 9 cell resting on the rails and ribs (no tape)."""
    cw, cl, ct = BAT_CELL
    return gbox(-cw / 2 + dx, cw / 2 + dx, -cl / 2, cl / 2, BAT_Z, BAT_Z + ct)


def strap(grow=0.0):
    """The strap's path: under the cell, out through the wall slots, up the sides, over the top
    of a cell sitting on 1 mm of optional tape (the highest it can be)."""
    w, t = STRAP_ENV
    t += grow
    z1 = SHELF + CAR_T
    xo = BAT[0] / 2 + 1.3                      # just outside the side walls
    top = BAT_Z + 1.0 + BAT_CELL[2]
    return (gbox(-xo - t, xo + t, -w / 2, w / 2, z1, z1 + t)
            + gbox(xo, xo + t, -w / 2, w / 2, z1, top + t) + gbox(-xo - t, -xo, -w / 2, w / 2, z1, top + t)
            + gbox(-xo - t, xo + t, -w / 2, w / 2, top, top + t))


def usb_plug():
    """Overmold (rounded ends) from the receptacle mouth outward, then 25 mm of cable."""
    x0 = -(USB_MOUTH_R + 0.3)
    mold = extrude(Plane.YZ.offset(x0) * Pos(0, USB_Z) * SlotOverall(*USB_PLUG), amount=USB_PLUG_LEN, dir=(-1, 0, 0))
    cable = Pos(x0 - USB_PLUG_LEN - 12.5, 0, USB_Z) * Rot(0, 90, 0) * Cylinder(2.25, 25)
    return mold, cable


def cap_at(cap, psi):
    """The cap turned psi degrees back from closed, along the closing direction (psi=TWIST: just pushed on)."""
    return Rot(0, 0, -theta(psi)) * cap


# --- checks ----------------------------------------------------------------------------------
def overlap(a, b):
    # build123d returns None for an empty intersection; real errors must surface, not read as 0.
    r = a & b
    return 0.0 if r is None else r.volume


def board_overlap(board, part, worst=None):
    pbb = part.bounding_box()
    total = 0.0
    for s in board.solids():
        sbb = s.bounding_box()
        if (sbb.max.X < pbb.min.X or sbb.min.X > pbb.max.X or sbb.max.Y < pbb.min.Y
                or sbb.min.Y > pbb.max.Y or sbb.max.Z < pbb.min.Z or sbb.min.Z > pbb.max.Z):
            continue
        v = overlap(s, part)
        if v > 0.001:
            total += v
            if worst is not None:
                worst.append((v, sbb))
    return total


def fit_check(front, carrier, cap, board=None):
    print("fit check (mm^3 of overlap; ~0 is a pass)")
    board = board or place_board()
    parts = (("front", front), ("carrier", carrier), ("cap", cap))
    if board is not None:
        bb = board.bounding_box()
        print(f"  board placed: glass front Z {bb.min.Z:.2f} (recess {RECESS}), back Z {bb.max.Z:.2f}, "
              f"glass dia {bb.size.X:.2f}")
        for name, part in parts:
            worst = []
            print(f"  board vs {name:8s}: {board_overlap(board, part, worst):.3f}")
            for v, sbb in sorted(worst, key=lambda r: -r[0])[:4]:
                print(f"    {v:.3f} at x {sbb.min.X:.1f}..{sbb.max.X:.1f} y {sbb.min.Y:.1f}..{sbb.max.Y:.1f} "
                      f"z {sbb.min.Z:.1f}..{sbb.max.Z:.1f}")
    print(f"  carrier vs front: {overlap(carrier, front):.3f}   cap vs front (closed): {overlap(cap, front):.3f}"
          f"   cap vs carrier: {overlap(cap, carrier):.3f}")
    bat, spk, st = battery(), speaker(), strap()
    others = (("battery", bat), ("speaker", spk), ("strap", st))
    for name, env in others:
        line = "  ".join(f"vs {n} {overlap(env, part):.3f}" for n, part in parts)
        line += "".join(f"  vs {n} {overlap(env, o):.3f}" for n, o in others
                        if n != name and {n, name} != {"battery", "strap"})   # the strap sits on the cell
        if board is not None:
            line += f"  vs board {board_overlap(board, env):.3f}"
        print(f"  {name:8s} {line}")
    print(f"    battery envelope {BAT[0]}x{BAT[1]}x{BAT[2]} at Z {BAT_Z:.1f}..{BAT_Z + BAT[2]:.1f}: nearest shell "
          f"{bat.distance_to(front):.2f} mm, nearest cap {bat.distance_to(cap):.2f} mm")
    print(f"    speaker: Z {SPK_Z - SPK[1] / 2:.1f}..{SPK_Z + SPK[1] / 2:.1f}, {len(grille_holes())} grille holes, "
          f"to the battery strap {spk.distance_to(st):.2f} mm, to the carrier {spk.distance_to(carrier):.2f} mm")
    path = speaker(dy=SPK[1] + 6)                       # on its way in, above the latch hook
    print(f"    speaker sliding in from above (past the latch, through the spigot): vs front {overlap(path, front):.3f}")
    print(f"    strap vs the bare cell on 1 mm of tape: {overlap(st, Pos(0, 0, 1.0) * cell()):.3f} (it rests on top)")
    mold, cable = usb_plug()
    print(f"  USB-C overmold {USB_PLUG[0]}x{USB_PLUG[1]}: vs front {overlap(mold, front):.3f}  vs carrier "
          f"{overlap(mold, carrier):.3f}   cable vs front {overlap(cable, front):.3f}")
    print(f"    overmold to the slot wall {mold.distance_to(front):.2f} mm, to the carrier {mold.distance_to(carrier):.2f} mm")
    for name, (ang, rtip, depth) in BUTTONS.items():
        d = Vector(math.cos(math.radians(ang)), math.sin(math.radians(ang)), 0)
        pl = Plane(origin=d * (rtip + 0.05) + Vector(0, 0, RECESS + depth), x_dir=(0, 0, 1), z_dir=d)
        pin = extrude(pl * Circle(0.5), amount=30)
        print(f"  {name} pin path (1 mm) vs front {overlap(pin, front):.3f}; the switch tip is "
              f"{r_outer(RECESS + depth) - rtip:.1f} mm in from the surface")
    # Screws: tips must stop short of the PCB / display.
    pcb_back = RECESS + 6.90
    print(f"  M2x6 board screws: tip at Z {SHELF + CAR_T - 6:.2f}, PCB back face at Z {pcb_back:.2f} "
          f"(standoff thread {RECESS + 5.9:.2f}..{SHELF:.2f})")
    print(f"  M2x8 frame screws: tip at Z {SHELF + CAR_T - 8:.2f}, pilot bottom at Z {SHELF - FRAME_PILOT_DEPTH:.2f}")
    return board


# --- export ----------------------------------------------------------------------------------
def print_layout(front, carrier, cap):
    """Parts in print orientation on the A1 mini's 180 x 180 plate."""
    f = Pos(46, 46, 0) * front                                      # window down on the plate
    c = Pos(133, 133, -ZC) * cap                                    # rim down, the 8 on top
    k = Pos(138, 40, -SHELF) * carrier                              # flat face down
    return f, c, k


def test_coupons(front, cap):
    """Slices of the fits, in their real print orientation, for checking a printer before the full
    print: the glass bore (with the bezel and bevel), both sides of the seam with the twist lock,
    and the speaker sleeve with its grille and latch."""
    window = front & gbox(-60, 60, -60, 60, -1, 4.0)
    seam_f = front & gbox(-60, 60, -60, 60, ZC - 2.5, ZC + SPIG_H + 1)
    seam_c = cap & gbox(-60, 60, -60, 60, ZC - 1, ZC + cap_band() + 2.5)
    a = SPK_AZ
    sleeve = front & (Rot(0, 0, a) * gbox(22, 48, -19.5, 19.5, SHELF - 1.7, ZC + SPIG_H + 1))
    sleeve = Rot(0, 0, -a) * sleeve
    sb = sleeve.bounding_box()
    return (Pos(46, 46, 0) * window,
            Pos(133, 46, -(ZC - 2.5)) * seam_f,
            Pos(90, 133, -ZC) * seam_c,
            Pos(162 - sb.center().X, 140 - sb.center().Y, -sb.min.Z) * sleeve)


def main():
    OUT.mkdir(exist_ok=True)
    front, carrier, cap = build_front(), build_carrier(), build_cap()
    for name, p in (("front", front), ("carrier", carrier), ("cap", cap)):
        print(f"{name:8s} {p.volume / 1000:6.1f} cm^3, bbox {p.bounding_box().size}, valid {p.is_valid}")
    print(f"ZC {ZC:.2f}  shelf {SHELF:.2f}  battery face {BAT_Z:.2f}  cap band {cap_band():.2f}  B_NOM {B_NOM:.2f}"
          f"  speaker face X {SPK_X:.1f} Z {SPK_Z:.1f}")
    board = None
    if "--no-check" not in sys.argv:
        board = fit_check(front, carrier, cap)
    export_step(front + carrier + cap, str(OUT / "assembly.step"))
    f, c, k = print_layout(front, carrier, cap)
    for name, part in (("front", f), ("cap", c), ("carrier", k)):
        export_stl(part, str(OUT / f"{name}.stl"), tolerance=0.01, angular_tolerance=0.05)
    mesher = Mesher()
    for part in (f, c, k):
        mesher.add_shape(part, linear_deflection=0.01, angular_deflection=0.05)
    mesher.write(str(OUT / "magic-8-ball-case.3mf"))
    mesher = Mesher()
    for part in test_coupons(front, cap):
        mesher.add_shape(part, linear_deflection=0.01, angular_deflection=0.05)
    mesher.write(str(OUT / "fit-test-coupons.3mf"))
    for p in sorted(OUT.iterdir()):
        if p.is_file():
            print(f"  wrote {p.name} ({p.stat().st_size // 1024} KB)")


if __name__ == "__main__":
    main()
