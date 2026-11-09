"""Prove the fit check can fail: move each part by a little and show that the overlap appears.

    .venv/bin/python tools/validate_fit.py

The board cases need Waveshare's STEP in vendor/model3d/ (see README) and are skipped without it.
"""
import sys

sys.path.insert(0, ".")
import case as K  # noqa: E402
from build123d import Pos, Rot  # noqa: E402

front, carrier, cap = K.build_front(), K.build_carrier(), K.build_cap()
board = K.place_board()


def line(label, value, expect):
    ok = (value > 0.001) == expect.startswith(">")
    print(f"  {'ok ' if ok else 'BAD'} {label:62s} {value:9.3f} mm^3   expected {expect}")
    return ok


results = []
if board is not None:
    print("board in its bore and on the carrier")
    results.append(line("board as placed vs carrier", K.board_overlap(board, carrier), "0: touching"))
    results.append(line("board pushed 0.05 back vs carrier (standoffs land)", K.board_overlap(Pos(0, 0, 0.05) * board, carrier), "> 0"))
    hits = [i for i, s in enumerate(board.solids()) if K.overlap(Pos(0, 0, 0.05) * s, carrier) > 0.001]
    print(f"      solids that touch the carrier: {hits} (648, 649, 650 are the three standoffs)")
    for dx, dy, exp in ((0.15, 0, "0: inside the 0.2 clearance"), (0.25, 0, "> 0"), (0, -0.25, "> 0"),
                        (-0.18, 0.18, "> 0")):
        results.append(line(f"board shifted ({dx:+.2f}, {dy:+.2f}) vs front (glass in bore)",
                            K.board_overlap(Pos(dx, dy, 0) * board, front), exp))
    results.append(line("speaker vs board", K.board_overlap(board, K.speaker()), "0"))

print("twist lock (cap turned from closed; positive = back toward push-on)")
for psi, exp in ((0, "0: flanks just touch"), (-4, "> 0: tighter than nominal"), (4, "0"), (15, "0"),
                 (30, "0"), (K.TWIST, "0: pushed on, lugs in their gaps")):
    results.append(line(f"cap at {psi:+.0f} deg vs front", K.overlap(K.cap_at(cap, psi), front), exp))
results.append(line("cap 0.1 lower than closed (seam faces meet)", K.overlap(Pos(0, 0, -0.1) * cap, front), "> 0"))
for turn in (120, 240):
    wrong = Rot(0, 0, turn) * K.cap_at(cap, K.TWIST)
    results.append(line(f"cap pushed on rotated {turn} deg (keying)", K.overlap(wrong, front), "> 0: blocked"))

print("battery and strap")
cell = K.cell()
results.append(line("bare 35x65x9 cell resting on the rails and ribs vs carrier", K.overlap(cell, carrier), "0: resting"))
results.append(line("cell 0.45 off-centre vs carrier", K.overlap(K.cell(0.45), carrier), "0: swell room"))
results.append(line("cell 0.7 off-centre vs carrier (side walls stop it)", K.overlap(K.cell(0.7), carrier), "> 0"))
bat = K.battery()
results.append(line("battery envelope corners grown 1.1 mm vs front", K.overlap(bat + K.gbox(
    -K.BAT[0] / 2 - 1.1, K.BAT[0] / 2 + 1.1, -K.BAT[1] / 2 - 1.1, K.BAT[1] / 2 + 1.1, K.BAT_Z, K.BAT_Z + 2), front), "> 0"))
st = K.strap()
results.append(line("strap through its slots vs carrier", K.overlap(st, carrier), "0: passes"))
results.append(line("strap 1.2 mm thicker vs carrier (slot roofs)", K.overlap(K.strap(grow=1.2), carrier), "> 0"))
results.append(line("strap vs front and speaker", K.overlap(st, front) + K.overlap(st, K.speaker()), "0"))

print("speaker in its sleeve")
results.append(line("speaker seated vs front", K.overlap(K.speaker(), front), "0: touching lips, ribs, stop"))
for label, kw, exp in (("0.15 out, toward the grille (face lips)", dict(dz=0.15), "> 0"),
                       ("0.15 in, away from the grille (back ribs)", dict(dz=-0.15), "> 0"),
                       ("0.15 sideways (side ribs)", dict(dx=0.15), "> 0"),
                       ("0.1 down (bottom stop)", dict(dy=-0.1), "> 0"),
                       ("0.1 up (the latch hook's tip sits on its top edge)", dict(dy=0.1), "> 0: no play"),
                       ("2.5 up, on its way in (pushes the hook back)", dict(dy=2.5), "> 0: the arm flexes"),
                       ("26 up, above the hook (path through the spigot)", dict(dy=26.0), "0: clear")):
    results.append(line(f"speaker moved {label}", K.overlap(K.speaker(**kw), front), exp))
results.append(line("speaker vs carrier, cap (closed) and battery",
                    K.overlap(K.speaker(), carrier) + K.overlap(K.speaker(), cap) + K.overlap(K.speaker(), bat), "0"))

print("cable")
mold, _ = K.usb_plug()
results.append(line("USB-C overmold shifted 0.5 toward the window vs front", K.overlap(Pos(0, 0, -0.5) * mold, front), "> 0"))
results.append(line("USB-C overmold shifted 0.3 sideways vs front", K.overlap(Pos(0, 0.3, 0) * mold, front), "0: inside 0.4"))
print(f"{sum(results)} of {len(results)} as expected" + ("" if board is not None else " (board cases skipped)"))
