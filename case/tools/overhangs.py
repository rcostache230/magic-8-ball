"""List downward-facing surfaces steeper than 45 degrees in print orientation (support candidates).

    ../../case/.venv/bin/python tools/overhangs.py
"""
import sys

sys.path.insert(0, ".")
import numpy as np  # noqa: E402

import case as K  # noqa: E402

front, carrier, cap = K.build_front(), K.build_carrier(), K.build_cap()
f, c, k = K.print_layout(front, carrier, cap)

for name, part in (("front (window down)", f), ("cap (rim down)", c), ("carrier (flat face down)", k)):
    verts, faces = part.tessellate(0.05, 0.2)
    v = np.array([[p.X, p.Y, p.Z] for p in verts])
    t = v[np.array(faces)]
    n = np.cross(t[:, 1] - t[:, 0], t[:, 2] - t[:, 0])
    area = np.linalg.norm(n, axis=1) / 2
    n /= (2 * area[:, None] + 1e-12)
    zc = t[:, :, 2].mean(1)
    bad = (n[:, 2] < -np.cos(np.radians(45)) - 0.01) & (zc > 0.3)
    print(f"{name}: {area[bad].sum():.0f} mm^2 facing down steeper than 45 deg (off the bed)")
    zs = np.round(zc[bad] / 2) * 2
    for z in np.unique(zs):
        sel = bad.copy()
        sel[bad] = zs == z
        pts = t[sel].reshape(-1, 3)
        lo, hi = pts.min(0), pts.max(0)
        flat = (n[sel, 2] < -0.98).mean()
        print(f"  z~{z:5.1f}: {area[sel].sum():6.1f} mm^2  x {lo[0]:6.1f}..{hi[0]:6.1f}  y {lo[1]:6.1f}..{hi[1]:6.1f}"
              f"  ({flat:.0%} flat: bridge)")
