"""Coins des écrans verts (fond vert ou vert-jaune) : composante principale, arêtes ajustées par moindres carrés,
coins = intersections (donc valables même si un coin sort du cadre). Écrit screens.json.
Usage : python3 -I detect_green.py [<dossier teaser>]"""

# ruff: noqa: N806 (L, Rr, T, Bt : arêtes gauche, droite, haut, bas)
import json
import pathlib
import subprocess
import sys
from collections import deque

import numpy as np

V = pathlib.Path(sys.argv[1]) if len(sys.argv) > 1 else pathlib.Path(__file__).resolve().parent
# par écran : rush, et pour chaque arête la part des points utilisée (1.0 = tous ; 0.4 = les 40 % côté coin propre)
JOBS = {
    "scr_game": ("r_jeu_screen", {}),
    "scr_shop": ("r_shop", {"right": 0.4, "bottom": 0.4}),
    "scr_site": ("r_site", {}),
}
MARGIN = 0.02  # le quad déborde de 2 % : l'incrustation par clé verte cache le surplus
W, H = 1920, 1080


def load(f):
    raw = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", str(f), "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
        capture_output=True,
        check=True,
    ).stdout
    return np.frombuffer(raw, np.uint8).reshape(H, W, 3).astype(float) / 255


def green_mask(im):
    r, g, b = im[..., 0], im[..., 1], im[..., 2]
    mx = im.max(2)
    mn = im.min(2)
    sat = np.where(mx > 0, (mx - mn) / np.maximum(mx, 1e-6), 0)
    # teinte : vert dominant, bleu faible (vert pur ou vert-jaune)
    return (g > 0.45) & (sat > 0.55) & (g > r * 1.05) & (g > b * 1.6)


def main_component(mask):
    ys, xs = np.nonzero(mask)
    cy, cx = int(np.median(ys)), int(np.median(xs))
    # point de départ : le pixel vrai le plus proche de la médiane
    if not mask[cy, cx]:
        d = (ys - cy) ** 2 + (xs - cx) ** 2
        i = d.argmin()
        cy, cx = ys[i], xs[i]
    seen = np.zeros_like(mask)
    q = deque([(cy, cx)])
    seen[cy, cx] = True
    while q:
        y, x = q.popleft()
        for yy, xx in ((y - 1, x), (y + 1, x), (y, x - 1), (y, x + 1)):
            if 0 <= yy < H and 0 <= xx < W and mask[yy, xx] and not seen[yy, xx]:
                seen[yy, xx] = True
                q.append((yy, xx))
    return seen


def fit_edges(m, part):
    ys, xs = np.nonzero(m)
    y0, y1, x0, x1 = ys.min(), ys.max(), xs.min(), xs.max()
    rows = range(y0, y1 + 1)
    cols = range(x0, x1 + 1)
    left, right, top, bot = [], [], [], []
    for y in rows:
        r = np.nonzero(m[y])[0]
        if len(r) == 0:
            continue
        if r[0] > 0:
            left.append((r[0], y))
        if r[-1] < W - 1:
            right.append((r[-1], y))
    for x in cols:
        c = np.nonzero(m[:, x])[0]
        if len(c) == 0:
            continue
        if c[0] > 0:
            top.append((x, c[0]))
        if c[-1] < H - 1:
            bot.append((x, c[-1]))

    def trim(pts, k):  # on retire 8 % à chaque bout (coins arrondis, biseaux)
        pts = sorted(pts, key=lambda p: p[k])
        n = len(pts)
        cut = int(n * 0.08)
        return np.array(pts[cut : n - cut], float)

    L = trim(left, 1)
    Rr = trim(right, 1)
    T = trim(top, 0)
    Bt = trim(bot, 0)

    def head(
        pts, frac
    ):  # garde la première fraction (côté haut pour une arête verticale, côté gauche pour une horizontale)
        n = max(8, int(len(pts) * frac))
        return pts[:n]

    L = head(L, part.get("left", 1.0))
    Rr = head(Rr, part.get("right", 1.0))
    T = head(T, part.get("top", 1.0))
    Bt = head(Bt, part.get("bottom", 1.0))
    # x = a*y + b pour gauche/droite ; y = c*x + d pour haut/bas
    aL, bL = np.polyfit(L[:, 1], L[:, 0], 1)
    aR, bR = np.polyfit(Rr[:, 1], Rr[:, 0], 1)
    cT, dT = np.polyfit(T[:, 0], T[:, 1], 1)
    cB, dB = np.polyfit(Bt[:, 0], Bt[:, 1], 1)

    def inter(a, b, c, d):  # x = a y + b ; y = c x + d
        y = (c * b + d) / (1 - a * c)
        x = a * y + b
        return [round(float(x), 1), round(float(y), 1)]

    q = [inter(aL, bL, cT, dT), inter(aR, bR, cT, dT), inter(aR, bR, cB, dB), inter(aL, bL, cB, dB)]
    cx = sum(p[0] for p in q) / 4
    cy = sum(p[1] for p in q) / 4
    return [[round(cx + (x - cx) * (1 + MARGIN), 1), round(cy + (y - cy) * (1 + MARGIN), 1)] for x, y in q]


out = {}
for key, (cid, part) in JOBS.items():
    frames = sorted((V / "frames" / cid).glob("*.jpg"))
    if not frames:
        print("absent", cid)
        continue
    quads = []
    for f in (frames[0], frames[-1]):
        m = green_mask(load(f))
        if m.sum() < 2000:
            print("pas de vert", cid, f.name)
            break
        mc = main_component(m)
        im = load(f)
        col = [round(float(v), 3) for v in im[mc].mean(0)]
        quads.append((fit_edges(mc, part), int(mc.sum()), int(m.sum()), col))
    if len(quads) < 2:
        continue
    q0, n0, tot, col = quads[0]
    q1 = quads[1][0]
    drift = max(abs(a - b) for p, q in zip(q0, q1, strict=False) for a, b in zip(p, q, strict=False))
    out[key] = {"quad": q0, "key": col}
    print(
        key, cid, "composante", n0, "/", tot, "px ; coins", q0, "; couleur clé", col, "; dérive", round(drift, 1), "px"
    )
(V / "screens.json").write_text(json.dumps(out, indent=1))
