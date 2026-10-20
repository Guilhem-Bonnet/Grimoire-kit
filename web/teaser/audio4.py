"""Bande-son du teaser v4 (48 s, 90 BPM, ré majeur) : façon bande-annonce.

Montée (acte 1) → coupe au noir → calme du terminal et « chargement » → relâche (acte 3) → signature.
Tout est synthétisé (numpy), déterministe, calé sur cues.json exporté par la scène.
Usage : python3 audio4.py [<dossier teaser>]
"""
# ruff: noqa: N802, N806 (B() et X : notation du signal)
import json
import sys
import wave
from pathlib import Path

import numpy as np

V = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parent
C = json.loads((V / "cues.json").read_text())
SR = 48000
DUR = 48.0
BEAT = 60 / 90
BAR = 4 * BEAT
rng = np.random.default_rng(20261018)


def B(bar, beat=1.0):
    return (bar - 1) * BAR + (beat - 1) * BEAT


def hz(note):
    names = {"C": -9, "D": -7, "E": -5, "F": -4, "G": -2, "A": 0, "B": 2}
    n, acc, octv = note[0], note[1:-1], int(note[-1])
    semi = names[n] + (1 if acc == "#" else -1 if acc == "b" else 0) + (octv - 4) * 12
    return 440.0 * 2 ** (semi / 12)


N = int(SR * DUR)
L = np.zeros(N)
R = np.zeros(N)


def env(n, a=0.005, d=0.3, sustain=0.0, release=0.05):
    t = np.arange(n) / SR
    e = np.minimum(1, t / max(a, 1e-4)) * (sustain + (1 - sustain) * np.exp(-t / max(d, 1e-4)))
    r = int(release * SR)
    if r and r < n:
        e[-r:] *= np.linspace(1, 0, r)
    return e


def put(sig, t, gain=1.0, pan=0.0, cut=None):
    i = int(t * SR)
    if i >= N:
        return
    if cut is not None:
        sig = sig[: max(0, int((cut - t) * SR))].copy()
        f = min(len(sig), int(0.012 * SR))
        if f:
            sig[-f:] *= np.linspace(1, 0, f)
    j = min(N, i + len(sig))
    if j <= i:
        return
    s = sig[: j - i] * gain
    L[i:j] += s * np.sqrt((1 - pan) / 2)
    R[i:j] += s * np.sqrt((1 + pan) / 2)


def mx(*parts):
    n = max(len(p) for p in parts)
    out = np.zeros(n)
    for p in parts:
        out[: len(p)] += p
    return out


def tone(freq, dur, partials=((1, 1.0),), a=0.005, d=0.4, sustain=0.0, release=0.05, vib=0.0, glide=None):
    n = int(dur * SR)
    t = np.arange(n) / SR
    if glide:
        f = freq * (glide / freq) ** (t / dur)
        ph = 2 * np.pi * np.cumsum(f) / SR
    else:
        ph = 2 * np.pi * freq * t
    if vib:
        ph = ph + vib * np.sin(2 * np.pi * 5.2 * t) * (1 - np.exp(-t / 0.25))
    s = sum(amp * np.sin(k * ph) * np.exp(-t * (k - 1) * 2.5) for k, amp in partials)
    return s * env(n, a, d, sustain, release)


def noise(dur, lp=None, hp=None):
    n = int(dur * SR)
    x = rng.standard_normal(n)
    X = np.fft.rfft(x)
    f = np.fft.rfftfreq(n, 1 / SR)
    m = np.ones_like(f)
    if lp:
        m *= 1 / (1 + (f / lp) ** 4)
    if hp:
        m *= 1 / (1 + (hp / np.maximum(f, 1)) ** 4)
    return np.fft.irfft(X * m, n) / 3


def square(freq, dur, duty=0.5):
    n = int(dur * SR)
    t = np.arange(n) / SR
    return np.where((t * freq) % 1 < duty, 1.0, -1.0) * env(n, 0.002, dur * 0.6, 0.2, 0.01)


# ---- timbres ----
def hum(note, dur):
    s = tone(hz(note), dur, ((1, 1.0), (2, 0.18), (3, 0.06)), a=0.07, d=9, sustain=1, release=0.12, vib=0.9)
    br = noise(dur, lp=2200, hp=500) * env(int(dur * SR), 0.08, 9, 1, 0.12) * 0.12
    return mx(s, br)


def pluck(note, dur=0.9):
    return tone(hz(note), dur, ((1, 1.0), (2, 0.5), (3, 0.28), (4, 0.15), (5, 0.08)), a=0.002, d=0.32)


def piano(note, dur=2.2):
    return tone(hz(note), dur, ((1, 1.0), (2, 0.45), (3, 0.2), (4, 0.09), (6, 0.03)), a=0.003, d=0.9, release=0.3)


def wood(note, dur=0.7):
    return tone(hz(note), dur, ((1, 1.0), (4, 0.35), (10, 0.08)), a=0.002, d=0.18)


def pad(notes, dur, a=1.2):
    out = 0
    for nt in notes:
        for det in (-0.05, 0.05):
            out = out + tone(hz(nt) * 2 ** (det / 12), dur, ((1, 1.0), (2, 0.3), (3, 0.12)), a=a, d=99, sustain=1, release=1.0)
    return out / (2 * len(notes))


def kick(dur=0.35):
    n = int(dur * SR)
    t = np.arange(n) / SR
    f = 120 * np.exp(-t * 18) + 48
    return np.sin(2 * np.pi * np.cumsum(f) / SR) * np.exp(-t / 0.12)


def hat(dur=0.05):
    n = int(dur * SR)
    return noise(dur, hp=6500)[:n] * np.exp(-np.arange(n) / SR / 0.012) * 2.0


def click(hard=False):
    d = 0.045 if hard else 0.03
    n = int(d * SR)
    t = np.arange(n) / SR
    body = np.sin(2 * np.pi * (1900 if hard else 2600) * t) * np.exp(-t / 0.004)
    return noise(d, lp=9000, hp=1800)[:n] * np.exp(-t / (0.006 if hard else 0.004)) * 2.2 + body * 0.5


def pop(f0=620, f1=300, d=0.09):
    n = int(d * SR)
    t = np.arange(n) / SR
    f = f0 * (f1 / f0) ** (t / d)
    return np.sin(2 * np.pi * np.cumsum(f) / SR) * np.exp(-t / 0.03)


def tick(f=3200, d=0.025):
    n = int(d * SR)
    t = np.arange(n) / SR
    return np.sin(2 * np.pi * f * t) * np.exp(-t / 0.005)


def boom(dur=1.6):
    n = int(dur * SR)
    t = np.arange(n) / SR
    f = 70 * np.exp(-t * 6) + 38
    return np.sin(2 * np.pi * np.cumsum(f) / SR) * np.exp(-t / 0.45)


def riser(dur):
    n = int(dur * SR)
    t = np.arange(n) / SR
    x = t / dur
    nz = noise(dur, hp=300)[:n] * (x ** 2.2)
    f = 110 * 2 ** (x * 3.2)
    tn = np.sin(2 * np.pi * np.cumsum(f) / SR) * (x ** 1.6) * 0.5
    return nz + tn


def whoosh(dur=0.5):
    n = int(dur * SR)
    t = np.arange(n) / SR
    x = t / dur
    return noise(dur, lp=6000, hp=400)[:n] * np.sin(np.pi * x) ** 1.5 * 1.4


def chime(notes, d=0.9):
    return mx(*[tone(hz(nt), d, ((1, 1.0), (3, 0.15)), a=0.002, d=0.35) * g for nt, g in zip(notes, (1.0, 0.7, 0.5), strict=False)])


# ---- fond de pièce constant ----
put(noise(DUR, lp=450, hp=40) * 0.045, 0)

# ================= ACTE 1 : la montée =================
for ty in C["type"]:
    if ty["t"] >= C["black"] and ty["t"] < C["term"]:
        continue
    n = ty["n"]
    for i in range(n):
        tt = ty["t"] + ty["d"] * i / max(1, n - 1) + rng.uniform(-0.004, 0.004)
        soft = ty["t"] >= C["end"]
        put(click(), tt, (0.16 if ty["t"] < C["black"] else 0.11) * (0.8 + 0.4 * rng.random()), rng.uniform(-0.15, 0.15))
for i, tp in enumerate(C["pop"]):
    put(pop(620 + i * 25, 300), tp, 0.22, (-0.5 + (i % 4) / 3), None)
    put(noise(0.4, lp=3000, hp=200) * env(int(0.4 * SR), 0.01, 0.2) * 0.08, tp, 1.0, 0)
# pulsation : noire à partir de M3, croche à partir de M4, double-croche à M5 ; montée jusqu'à la coupe
t = B(3)
while t < C["black"]:
    put(kick(), t, 0.42 if t < B(5) else 0.5, 0)
    t += BEAT
t = B(4)
while t < C["black"]:
    put(hat(), t, 0.10, 0.3)
    t += BEAT / 2
t = B(5)
while t < C["black"]:
    put(hat(0.03), t, 0.07, -0.3)
    t += BEAT / 4
t = B(5, 3)
while t < C["black"]:
    put(hat(0.02), t, 0.06, 0)
    t += BEAT / 8
put(riser(C["black"] - B(4, 3)), B(4, 3), 0.55, 0, cut=C["black"])
# bourdon tendu qui monte (ré2 → la2), coupé net
put(tone(hz("D2"), C["black"] - B(3), ((1, 1), (2, .35), (3, .15)), a=1.0, d=99, sustain=1, release=0.01, glide=hz("A2")), B(3), 0.26, 0, cut=C["black"])
# ================= coupe au noir =================
put(boom(), C["black"], 0.9, 0)
# ================= ACTE 2 : terminal =================
put(click(hard=True), C["term"] - 10 / 30, 0.26, 0)  # J-cut : le son précède l'image
put(pad(["D3", "A3", "E4"], C["merge"] - C["term"], a=1.8), C["term"], 0.13, 0, cut=C["merge"])
for te in C["enter"]:
    put(click(hard=True), te, 0.22, 0)
# ticks de sortie : un par ligne (doux) pendant les inondations
for (a, b) in C["flood"]:
    n = 16
    for i in range(n):
        x = i / (n - 1)
        put(tick(2600 + 900 * x, 0.02), a + (b - a) * (x ** 1.6), 0.05, 0.1)
    put(whoosh(min(0.55, b - a + 0.15)), a - 0.05, 0.25, 0)
# chargement : ticks réguliers qui montent, puis carillon
a, b = C["bar"]
i = 0
tt = a
while tt < b:
    put(tick(1800 + 1400 * (tt - a) / (b - a), 0.03), tt, 0.11, 0.2)
    tt += BEAT / 4
    i += 1
put(chime(["D5", "A5", "D6"], 0.8), b, 0.22, 0)
# ================= chargement : cockpit =================
for i, tc in enumerate(C["cuts"]):
    put(kick(), tc, 0.4, 0)
    put(hat(), tc, 0.08, 0.2)
    put(pluck(["D4", "A4", "E5"][i], 0.6), tc, 0.14, -0.2 + 0.2 * i)
put(click(hard=True), C["merge"], 0.3, 0)
put(chime(["F#5", "A5"], 0.7), C["merge"] + 0.05, 0.2, 0)
put(riser(C["act3"] - C["merge"] - 0.1), C["merge"] + 0.1, 0.28, 0, cut=C["act3"])
put(kick(0.5), C["act3"], 0.7, 0)  # la chute
# ================= ACTE 3 : relâche =================
act3, note2, end = C["act3"], C["note2"], C["end"]
put(pad(["D3", "A3", "F#4", "E4"], note2 - act3 + 1.0, a=0.6), act3, 0.34, 0)
# le motif complet, enfin : ré la mi fa#
for k_, (nt, dt) in enumerate(zip(["D4", "A4", "E5", "F#5"], (0, BEAT / 2, BEAT, BEAT * 1.5), strict=False)):
    put(mx(hum(nt, 1.4 if k_ < 3 else 2.6) * 0.7, pluck(nt, 1.0) * 0.5), act3 + 0.1 + dt, 0.28, -0.3 + 0.2 * k_)
# arpège doux en croches, panoramique large
arp = ["D4", "A4", "D5", "E5", "F#5", "A5", "F#5", "E5"]
t = act3 + BAR
i = 0
while t < note2:
    put(pluck(arp[i % 8], 0.7), t, 0.09 + 0.03 * ((i // 8) % 2), -0.6 + (i % 8) / 7 * 1.2)
    t += BEAT / 2
    i += 1
# jeu vidéo : son du jeu (carré), pièces et sauts
for tc in C["coins"]:
    put(mx(square(hz("B5"), 0.06), np.concatenate([np.zeros(int(0.06 * SR)), square(hz("E6"), 0.12)])), tc, 0.07, 0.2)
for tj in C["jumps"]:
    put(square(hz("A4"), 0.14, 0.25) * np.linspace(1, 0.3, int(0.14 * SR)), tj, 0.05, -0.1)
# notification de commande, accord de piano (ré majeur, joué), photos
put(chime(["A5", "D6"], 0.6), C["toast"], 0.18, 0.35)
for nt, g in (("D3", 0.6), ("D4", 1.0), ("F#4", 0.85), ("A4", 0.8)):
    put(piano(nt, 2.4), C["chord"] + rng.uniform(0, 0.02), 0.22 * g, -0.1)
for tp in C["photos"]:
    put(tick(2200, 0.02), tp, 0.05, rng.uniform(-0.5, 0.5))
put(whoosh(0.7), C["photos"][0] - 0.05, 0.18, 0)
# note finale : résolution tenue
put(pad(["D3", "A3", "F#4", "A4"], end - note2 + 0.8, a=0.4), note2, 0.3, 0)
# ================= signature =================
for v in range(3):  # canon à 3 voix, décalées d'une croche, qui se rejoignent sur fa#5
    base = end + 0.15 + v * BEAT / 2
    for k_, nt in enumerate(["D4", "A4", "E5"]):
        put(mx(pluck(nt, 0.7) * 0.7, wood(nt, 0.5) * 0.4), base + k_ * BEAT / 2, 0.2, (-0.5, 0, 0.5)[v])
join = end + 0.15 + 2 * BEAT / 2 + 2 * BEAT / 2
put(mx(hum("F#5", 1.6), pluck("F#5", 1.2) * 0.6), join, 0.3, 0)
put(pad(["D3", "A3", "F#4"], DUR - end, a=0.3), end, 0.22, 0)

# creux avant la chute : la nappe du terminal s'éteint à la fusion
# (la respiration que recommandent les monteurs de bandes-annonces)
# ---- réverbération courte, fin en silence ----
irn = int(1.4 * SR)
ir = rng.standard_normal(irn) * np.exp(-np.arange(irn) / SR / 0.32)
ir /= np.sqrt((ir ** 2).sum())


def conv(x):
    n = len(x) + irn
    m = 1 << (n - 1).bit_length()
    return np.fft.irfft(np.fft.rfft(x, m) * np.fft.rfft(ir, m), m)[: len(x)]


Lw = L + 0.22 * conv(L)
Rw = R + 0.22 * conv(R)
# la coupe au noir doit être sèche : on étouffe la queue de réverbération juste après
i0, i1 = int(C["black"] * SR), int((C["black"] + 0.05) * SR)
g = np.ones(N)
g[i0:i1] = np.linspace(1, 0.08, i1 - i0)
g[i1:int((C["term"]) * SR)] = 0.08
g[int(C["term"] * SR):int((C["term"] + 0.4) * SR)] = np.linspace(0.08, 1, int(0.4 * SR))
boomsig = np.zeros(N)
bb = boom()
boomsig[i0:i0 + len(bb)] = bb * 0.9
Lw = Lw * g + boomsig
Rw = Rw * g + boomsig
fade = int(0.6 * SR)
Lw[-fade:] *= np.linspace(1, 0, fade) ** 2
Rw[-fade:] *= np.linspace(1, 0, fade) ** 2
peak = max(np.abs(Lw).max(), np.abs(Rw).max())
st = (np.stack([Lw, Rw], 1) / peak * 0.8 * 32767).astype(np.int16)
with wave.open(str(V / "bande-son-brute.wav"), "wb") as w:
    w.setnchannels(2)
    w.setsampwidth(2)
    w.setframerate(SR)
    w.writeframes(st.tobytes())
print("ok", round(peak, 3))
