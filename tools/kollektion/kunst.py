"""Decorative artwork for the template collection, drawn from scratch.

Every picture here is computed from polygons and strokes with Pillow and
numpy — no traced clip art, no third-party illustration. Drawn at three times
the target size and scaled down, so edges are clean at 300 dpi. Strokes are
never thinner than 0.25 mm on paper (3 px at 300 dpi): finer lines vanish
on a dye-sublimation print.
"""
from __future__ import annotations

import math
import random
from typing import List, Tuple

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

S = 3                     # supersampling factor
MIN_STRICH = 3            # px at 300 dpi = 0.25 mm


def rgba(farbe: str, alpha: int = 255) -> Tuple[int, int, int, int]:
    farbe = farbe.lstrip("#")
    return (int(farbe[0:2], 16), int(farbe[2:4], 16), int(farbe[4:6], 16), alpha)


def bezier(p0, p1, p2, p3, n: int = 90) -> List[Tuple[float, float]]:
    aus = []
    for i in range(n + 1):
        t = i / n
        a, b, c, d = (1 - t) ** 3, 3 * (1 - t) ** 2 * t, 3 * (1 - t) * t ** 2, t ** 3
        aus.append((a * p0[0] + b * p1[0] + c * p2[0] + d * p3[0],
                    a * p0[1] + b * p1[1] + c * p2[1] + d * p3[1]))
    return aus


def tangente(punkte, i) -> float:
    j0, j1 = max(0, i - 1), min(len(punkte) - 1, i + 1)
    return math.atan2(punkte[j1][1] - punkte[j0][1], punkte[j1][0] - punkte[j0][0])


def blatt(basis, winkel: float, laenge: float, breite: float, rund: float = 0.9,
          n: int = 40) -> List[Tuple[float, float]]:
    """An almond (rund < 1) or fuller (rund > 1) leaf from its base along ``winkel``."""
    oben, unten = [], []
    for i in range(n + 1):
        t = i / n
        halb = breite / 2 * (math.sin(math.pi * t) ** rund)
        oben.append((t * laenge, -halb))
        unten.append((t * laenge, halb))
    form = oben + unten[::-1]
    c, s = math.cos(winkel), math.sin(winkel)
    return [(basis[0] + x * c - y * s, basis[1] + x * s + y * c) for x, y in form]


def ellipse(mitte, rx, ry, winkel=0.0, n=64):
    c, s = math.cos(winkel), math.sin(winkel)
    aus = []
    for i in range(n):
        a = 2 * math.pi * i / n
        x, y = rx * math.cos(a), ry * math.sin(a)
        aus.append((mitte[0] + x * c - y * s, mitte[1] + x * s + y * c))
    return aus


def linie(draw, punkte, farbe, breite):
    draw.line(punkte, fill=farbe, width=max(1, int(breite)), joint="curve")
    r = breite / 2
    for p in (punkte[0], punkte[-1]):
        draw.ellipse((p[0] - r, p[1] - r, p[0] + r, p[1] + r), fill=farbe)


def koernung(bild: Image.Image, seed: int, staerke: float = 0.25) -> Image.Image:
    """Watercolour granulation: the alpha breathes a little, like pigment on paper."""
    rng = np.random.default_rng(seed)
    w, h = bild.size
    klein = rng.random((max(2, h // 40), max(2, w // 40)))
    rausch = Image.fromarray((klein * 255).astype("uint8")).resize((w, h), Image.Resampling.BICUBIC)
    rausch = np.asarray(rausch.filter(ImageFilter.GaussianBlur(6)), dtype=np.float32) / 255.0
    a = np.asarray(bild.getchannel("A"), dtype=np.float32)
    a = a * (1.0 - staerke + staerke * rausch)
    bild.putalpha(Image.fromarray(np.clip(a, 0, 255).astype("uint8")))
    return bild


def _klein(gross: Image.Image, w: int, h: int) -> Image.Image:
    return gross.resize((w, h), Image.Resampling.LANCZOS)


# ── 1 · Greenery: a watercolour sprig with a gold line ─────────────────────

def zweig(w: int, h: int, *, seed: int = 1, gruen=("#6f9278", "#4c6e58", "#97b39a"),
          gold: str = "#b8975a", spiegeln: bool = False, art: str = "eukalyptus") -> Image.Image:
    """A sprig rising from the bottom-left corner towards the top-right.

    ``art`` "eukalyptus" has round paired leaves, "olive" long narrow ones.
    A few leaves are gold outlines only — the gold accent of the family.
    """
    rnd = random.Random(seed)
    W, H = w * S, h * S
    lage = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    gold_lage = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(lage)
    dg = ImageDraw.Draw(gold_lage)
    stiel = bezier((0.06 * W, 0.97 * H), (0.30 * W, 0.70 * H), (0.48 * W, 0.30 * H), (0.93 * W, 0.08 * H))
    linie(dg, stiel, rgba(gold), 4 * S)
    n = 9 if art == "eukalyptus" else 13
    gold_zaehler = 0
    for k in range(n):
        i = int(8 + k * (len(stiel) - 14) / n)
        p, t = stiel[i], tangente(stiel, i)
        groesse = (1.0 - 0.5 * k / n) * min(W, H) * rnd.uniform(0.9, 1.1)
        for seite in (-1, 1):
            if art == "eukalyptus":
                # Round leaves on a short petiole, alternating a little.
                laenge, breite, rund = 0.20 * groesse, 0.19 * groesse, 1.6
                winkel = t + seite * (1.05 + rnd.uniform(-0.2, 0.2))
                versatz = 0.025 * groesse
                p2 = (p[0] + math.cos(winkel) * versatz, p[1] + math.sin(winkel) * versatz)
                d.line([p, p2], fill=rgba(gruen[1], 200), width=3 * S)
                basis = p2
            else:
                laenge, breite, rund = 0.27 * groesse, 0.08 * groesse, 0.8
                winkel = t + seite * (0.6 + rnd.uniform(-0.12, 0.12))
                basis = p
            gold_zaehler += 1
            if gold_zaehler % 7 == 4:
                form = blatt(basis, winkel, laenge, breite, rund)
                dg.line(form + [form[0]], fill=rgba(gold), width=3 * S, joint="curve")
                continue
            farbe = gruen[rnd.randrange(len(gruen))]
            form = blatt(basis, winkel, laenge, breite, rund)
            d.polygon(form, fill=rgba(farbe, 185))
            # A second, offset wash — the darker pooling of a watercolour edge.
            innen = blatt(basis, winkel + 0.06 * seite, laenge * 0.9, breite * 0.5, rund)
            d.polygon(innen, fill=rgba(gruen[1], 80))
            ader = blatt(basis, winkel, laenge * 0.8, 0.0, 1.0)
            d.line([ader[0], ader[len(ader) // 2 - 4]], fill=rgba(gruen[1], 120), width=2 * S)
    lage = lage.filter(ImageFilter.GaussianBlur(1.2 * S))
    lage = koernung(lage, seed)
    gesamt = Image.alpha_composite(lage, gold_lage)
    if spiegeln:
        gesamt = gesamt.transpose(Image.Transpose.FLIP_LEFT_RIGHT)
    return _klein(gesamt, w, h)


# ── 6 · Line-art flowers on a colour block ─────────────────────────────────

def linienblume(w: int, h: int, *, farbe: str = "#f4efe4", seed: int = 3, strich: int = 4,
                spiegeln: bool = False, bluete: bool = True) -> Image.Image:
    """A single-weight line drawing: one stem, leaves as outlines, one open flower."""
    rnd = random.Random(seed)
    W, H = w * S, h * S
    lage = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(lage)
    f = rgba(farbe)
    b = max(MIN_STRICH, strich) * S
    stiel = bezier((0.12 * W, 0.98 * H), (0.22 * W, 0.62 * H), (0.55 * W, 0.50 * H), (0.62 * W, 0.22 * H))
    linie(d, stiel, f, b)
    for k, i in enumerate((22, 38, 55, 70)):
        p, t = stiel[i], tangente(stiel, i)
        seite = -1 if k % 2 else 1
        form = blatt(p, t + seite * 0.75, 0.24 * min(W, H) * (1 - 0.12 * k), 0.10 * min(W, H), 0.85)
        d.line(form + [form[0]], fill=f, width=b, joint="curve")
        mitte = blatt(p, t + seite * 0.75, 0.20 * min(W, H) * (1 - 0.12 * k), 0.0, 1)
        d.line([mitte[0], mitte[len(mitte) // 4]], fill=f, width=max(S, b // 2 + S))
    # A side shoot with three buds.
    ast = bezier(stiel[48], (0.70 * W, 0.55 * H), (0.82 * W, 0.50 * H), (0.88 * W, 0.40 * H), 50)
    linie(d, ast, f, b)
    for i in (25, 50):
        p = ast[i]
        r = 0.035 * min(W, H)
        d.ellipse((p[0] - r, p[1] - r * 1.3, p[0] + r, p[1] + r * 1.3), outline=f, width=b)
    if bluete:
        z = stiel[-1]
        r = 0.16 * min(W, H)
        rr = r * 0.20
        for k in range(7):
            a = k * 2 * math.pi / 7 + rnd.uniform(-0.06, 0.06) - math.pi / 2
            start = (z[0] + math.cos(a) * rr * 1.25, z[1] + math.sin(a) * rr * 1.25)
            form = blatt(start, a, r * 1.0, r * 0.42, 1.1)
            d.line(form + [form[0]], fill=f, width=b, joint="curve")
        d.ellipse((z[0] - rr, z[1] - rr, z[0] + rr, z[1] + rr), outline=f, width=b)
        for k in range(5):
            a = k * 2 * math.pi / 5
            q = (z[0] + math.cos(a) * rr * 0.45, z[1] + math.sin(a) * rr * 0.45)
            d.ellipse((q[0] - b * 0.6, q[1] - b * 0.6, q[0] + b * 0.6, q[1] + b * 0.6), fill=f)
    if spiegeln:
        lage = lage.transpose(Image.Transpose.FLIP_LEFT_RIGHT)
    return _klein(lage, w, h)


# ── 2 · Boho: dried grasses and an arch ────────────────────────────────────

def graeser(w: int, h: int, *, farben=("#c9a27a", "#b5603f", "#d9c2a0", "#8a5a3c"), seed: int = 5,
            spiegeln: bool = False) -> Image.Image:
    """A small bunch of dried grasses: feathery plumes and two seed heads."""
    rnd = random.Random(seed)
    W, H = w * S, h * S
    lage = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(lage)
    fuss = (0.20 * W, 0.98 * H)
    ziele = [(0.30, 0.06), (0.55, 0.10), (0.78, 0.22), (0.92, 0.42), (0.12, 0.18)]
    for n, (zx, zy) in enumerate(ziele):
        farbe = farben[n % len(farben)]
        ende = (zx * W, zy * H)
        stiel = bezier(fuss, (fuss[0] + 0.05 * W, 0.6 * H), (ende[0] - 0.1 * W, ende[1] + 0.25 * H), ende, 70)
        linie(d, stiel, rgba(farben[3], 230), 3.5 * S)
        if n in (0, 2):         # pampas plume
            for i in range(30, 71):
                p, t = stiel[i], tangente(stiel, i)
                for seite in (-1, 1):
                    laenge = (0.05 + 0.08 * math.sin(math.pi * (i - 30) / 40)) * min(W, H)
                    a = t + seite * rnd.uniform(0.35, 0.7)
                    q = (p[0] + math.cos(a) * laenge, p[1] + math.sin(a) * laenge)
                    d.line([p, q], fill=rgba(farbe, 120), width=int(3 * S))
        elif n in (1, 3):       # wheat-like seed head
            for i in range(40, 70, 4):
                p, t = stiel[i], tangente(stiel, i)
                for seite in (-1, 1):
                    form = blatt(p, t + seite * 0.5, 0.06 * min(W, H), 0.025 * min(W, H), 0.9)
                    d.polygon(form, fill=rgba(farbe, 220))
        else:                   # a bare twig with small round seeds
            for i in range(35, 71, 7):
                p = stiel[i]
                r = 0.012 * min(W, H)
                d.ellipse((p[0] - r, p[1] - r, p[0] + r, p[1] + r), fill=rgba(farbe, 230))
    lage = lage.filter(ImageFilter.GaussianBlur(0.4 * S))
    if spiegeln:
        lage = lage.transpose(Image.Transpose.FLIP_LEFT_RIGHT)
    return _klein(lage, w, h)


def bogen(w: int, h: int, farbe: str) -> Image.Image:
    """A full arch (semicircle on a rectangle), filled — the boho frame shape."""
    W, H = w * S, h * S
    lage = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(lage)
    d.ellipse((0, 0, W - 1, W - 1), fill=rgba(farbe))
    d.rectangle((0, W // 2, W - 1, H - 1), fill=rgba(farbe))
    return _klein(lage, w, h)


def sonne(w: int, farbe: str, strahlen: int = 11, strich: int = 4) -> Image.Image:
    """A setting sun: half disc with short rays, line only."""
    h = w // 2 + 4
    W, H = w * S, h * S
    lage = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(lage)
    f = rgba(farbe)
    b = max(MIN_STRICH, strich) * S
    m = (W / 2, H - b)
    r = W * 0.24
    d.pieslice((m[0] - r, m[1] - r, m[0] + r, m[1] + r), 180, 360, fill=f)
    for k in range(strahlen):
        a = math.pi + math.pi * (k + 0.5) / strahlen
        p = (m[0] + math.cos(a) * r * 1.35, m[1] + math.sin(a) * r * 1.35)
        q = (m[0] + math.cos(a) * r * 1.9, m[1] + math.sin(a) * r * 1.9)
        linie(d, [p, q], f, b)
    return _klein(lage, w, h)


# ── 5 · Soft stripes ───────────────────────────────────────────────────────

def streifen(w: int, h: int, farbe_a: str, farbe_b: str, breite: int = 60) -> Image.Image:
    """Even vertical stripes, edge to edge (the background of the garden-party family).

    A coloured stripe is centred on the middle of the sheet, so both edges
    show the same thing — and, with a period of 100 px, also the middle of
    each half, which is where a two-up strip sheet is cut.
    """
    bild = Image.new("RGB", (w, h), rgba(farbe_a)[:3])
    d = ImageDraw.Draw(bild)
    x = w / 2 - breite / 2
    while x > -breite:
        x -= 2 * breite
    while x < w:
        d.rectangle((round(x), 0, round(x + breite) - 1, h), fill=rgba(farbe_b)[:3])
        x += 2 * breite
    return bild


# ── 7 · Film: a perforated band ─────────────────────────────────────────────

def perforation(w: int, h: int, *, farbe: str = "#f2ebdd", loch: int = 30, abstand: int = 52,
                senkrecht: bool = False) -> Image.Image:
    """A row of rounded film perforations on transparent ground, edge to edge."""
    W, H = w * S, h * S
    lage = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(lage)
    f = rgba(farbe)
    lang = W if not senkrecht else H
    quer = H if not senkrecht else W
    l, a = loch * S, abstand * S
    breite = int(quer * 0.62)
    n = max(1, int(lang // a))
    start = (lang - (n - 1) * a - l) / 2
    for i in range(n):
        p = start + i * a
        if senkrecht:
            box = ((W - breite) / 2, p, (W + breite) / 2, p + l)
        else:
            box = (p, (H - breite) / 2, p + l, (H + breite) / 2)
        d.rounded_rectangle(box, radius=6 * S, fill=f)
    return _klein(lage, w, h)


# ── 8 / 9 / 12 · Celebration: sparkles, balloons, confetti ─────────────────

def funken(w: int, h: int, *, farben=("#c9a45c", "#e4c98f"), seed: int = 8, anzahl: int = 26,
           dichte_oben: bool = False) -> Image.Image:
    """Four-pointed sparkles and small dots scattered over the box, never touching each other."""
    rnd = random.Random(seed)
    W, H = w * S, h * S
    lage = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(lage)
    gesetzt = []
    versuche = 0
    while len(gesetzt) < anzahl and versuche < anzahl * 40:
        versuche += 1
        r = rnd.uniform(0.012, 0.045) * min(W, H) if rnd.random() < 0.45 else rnd.uniform(0.006, 0.012) * min(W, H)
        x, y = rnd.uniform(r, W - r), rnd.uniform(r, H - r)
        if dichte_oben and rnd.random() < y / H:
            continue
        if any((x - gx) ** 2 + (y - gy) ** 2 < (r + gr + 0.03 * min(W, H)) ** 2 for gx, gy, gr in gesetzt):
            continue
        gesetzt.append((x, y, r))
        f = rgba(farben[rnd.randrange(len(farben))])
        if r > 0.012 * min(W, H):
            # A sparkle: two thin diamonds crossed.
            t = r * 0.22
            d.polygon([(x, y - r), (x + t, y), (x, y + r), (x - t, y)], fill=f)
            d.polygon([(x - r, y), (x, y - t), (x + r, y), (x, y + t)], fill=f)
        else:
            d.ellipse((x - r, y - r, x + r, y + r), fill=f)
    return _klein(lage, w, h)


def ballons(w: int, h: int, *, farben=("#f4b6b0", "#f7d6a3", "#bcd7e8", "#d8c7ec"), seed: int = 9,
            anzahl: int = 3, schnur: str = "#9a8f86") -> Image.Image:
    """A small bunch of balloons with a soft highlight and curled strings."""
    rnd = random.Random(seed)
    W, H = w * S, h * S
    lage = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(lage)
    knoten = (W * 0.5, H * 0.97)
    r = min(W / (anzahl * 1.35), H * 0.24)
    mitten = []
    for i in range(anzahl):
        mx = W * (i + 0.5) / anzahl + rnd.uniform(-0.04, 0.04) * W
        my = r * 1.25 + (0.12 * H if i % 2 else 0) + rnd.uniform(0, 0.04) * H
        mitten.append((mx, my))
    for (mx, my), i in zip(mitten, range(anzahl)):
        unten = (mx, my + r * 1.18)
        linie(d, bezier(unten, (unten[0] + 0.05 * W, unten[1] + 0.12 * H),
                        (knoten[0] - 0.05 * W, knoten[1] - 0.2 * H), knoten, 60), rgba(schnur), 3 * S)
    for (mx, my), i in zip(mitten, range(anzahl)):
        f = farben[i % len(farben)]
        d.ellipse((mx - r, my - r * 1.18, mx + r, my + r * 1.18), fill=rgba(f))
        d.polygon([(mx - 0.1 * r, my + 1.16 * r), (mx + 0.1 * r, my + 1.16 * r), (mx, my + 1.3 * r)], fill=rgba(f))
        g = ellipse((mx - 0.38 * r, my - 0.5 * r), 0.16 * r, 0.32 * r, -0.5)
        d.polygon(g, fill=(255, 255, 255, 120))
    return _klein(lage, w, h)


def konfetti(w: int, h: int, *, farben=("#ff4f79", "#ffc93c", "#2ec4b6", "#4361ee"), seed: int = 12,
             anzahl: int = 40) -> Image.Image:
    """Flat confetti: small turned rectangles, dots and squiggles, spaced out."""
    rnd = random.Random(seed)
    W, H = w * S, h * S
    lage = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(lage)
    gesetzt = []
    versuche = 0
    while len(gesetzt) < anzahl and versuche < anzahl * 50:
        versuche += 1
        g = rnd.uniform(0.012, 0.028) * min(W, H)
        x, y = rnd.uniform(g * 2, W - g * 2), rnd.uniform(g * 2, H - g * 2)
        if any((x - gx) ** 2 + (y - gy) ** 2 < (3.2 * g + 3.2 * gg) ** 2 / 4 for gx, gy, gg in gesetzt):
            continue
        gesetzt.append((x, y, g))
        f = rgba(farben[rnd.randrange(len(farben))])
        art = rnd.random()
        if art < 0.45:
            form = blatt((x - g, y), rnd.uniform(0, math.pi), 2 * g, g * 0.9, 0.05)
            d.polygon(form, fill=f)
        elif art < 0.8:
            d.ellipse((x - g * 0.6, y - g * 0.6, x + g * 0.6, y + g * 0.6), fill=f)
        else:
            a = rnd.uniform(0, math.pi)
            pts = [(x + math.cos(a) * t * g * 1.6 - math.sin(a) * math.sin(t * 3) * g * 0.6,
                    y + math.sin(a) * t * g * 1.6 + math.cos(a) * math.sin(t * 3) * g * 0.6)
                   for t in [k / 10 - 1 for k in range(21)]]
            d.line(pts, fill=f, width=int(g * 0.45), joint="curve")
    return _klein(lage, w, h)


# ── 11 · Winter: snowflakes ─────────────────────────────────────────────────

def schneeflocken(w: int, h: int, *, farbe: str = "#e9eef6", seed: int = 11, anzahl: int = 9,
                  strich: int = 4) -> Image.Image:
    """Six-armed line snowflakes in a few sizes, loosely scattered."""
    rnd = random.Random(seed)
    W, H = w * S, h * S
    lage = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(lage)
    f = rgba(farbe)
    b = max(MIN_STRICH, strich) * S
    gesetzt = []
    versuche = 0
    while len(gesetzt) < anzahl and versuche < anzahl * 60:
        versuche += 1
        r = rnd.uniform(0.06, 0.16) * min(W, H)
        x, y = rnd.uniform(r, W - r), rnd.uniform(r, H - r)
        if any((x - gx) ** 2 + (y - gy) ** 2 < (r + gr + 0.02 * min(W, H)) ** 2 for gx, gy, gr in gesetzt):
            continue
        gesetzt.append((x, y, r))
        dreh = rnd.uniform(0, math.pi / 3)
        for k in range(6):
            a = dreh + k * math.pi / 3
            ende = (x + math.cos(a) * r, y + math.sin(a) * r)
            linie(d, [(x, y), ende], f, b)
            for t, lang in ((0.55, 0.32), (0.8, 0.2)):
                p = (x + math.cos(a) * r * t, y + math.sin(a) * r * t)
                for seite in (-1, 1):
                    q = (p[0] + math.cos(a + seite * 0.8) * r * lang, p[1] + math.sin(a + seite * 0.8) * r * lang)
                    linie(d, [p, q], f, b * 0.8)
    return _klein(lage, w, h)


# ── 5 · Salbei (redrawn): a slender botanical line sprig ───────────────────

def zweig_linie(w: int, h: int, *, farbe: str = "#ece5d5", strich: int = 3, seed: int = 5,
                spiegeln: bool = False, paare: int = 9) -> Image.Image:
    """One arching stem with paired leaf outlines that shrink towards the tip.

    Single line weight, no flower — quiet enough to sit beside script.
    """
    rnd = random.Random(seed)
    W, H = w * S, h * S
    lage = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(lage)
    f = rgba(farbe)
    b = max(MIN_STRICH, strich) * S
    stiel = bezier((0.04 * W, 0.96 * H), (0.28 * W, 0.80 * H), (0.62 * W, 0.42 * H), (0.95 * W, 0.06 * H), 120)
    linie(d, stiel, f, b)
    for k in range(paare):
        i = int(10 + k * (len(stiel) - 22) / paare)
        p, t = stiel[i], tangente(stiel, i)
        laenge = min(W, H) * (0.17 - 0.1 * k / paare) * rnd.uniform(0.92, 1.05)
        for seite in (-1, 1):
            versatz = 4 * seite
            q = stiel[min(len(stiel) - 1, i + (versatz if seite > 0 else 0))]
            form = blatt(q, t + seite * (0.78 + rnd.uniform(-0.06, 0.06)), laenge, laenge * 0.36, 0.85)
            d.line(form + [form[0]], fill=f, width=b, joint="curve")
            ader = blatt(q, t + seite * 0.78, laenge * 0.7, 0.0, 1.0)
            d.line([ader[0], ader[len(ader) // 2 - 3]], fill=f, width=max(S, b - S))
    spitze = stiel[-1]
    form = blatt(spitze, tangente(stiel, len(stiel) - 1), min(W, H) * 0.05, min(W, H) * 0.02, 0.9)
    d.line(form + [form[0]], fill=f, width=b, joint="curve")
    if spiegeln:
        lage = lage.transpose(Image.Transpose.FLIP_LEFT_RIGHT)
    return _klein(lage, w, h)


# ── 9 · Rosé: soft watercolour washes ───────────────────────────────────────

def aquarell(w: int, h: int, *, farben=("#f2c4c0", "#ebb0ae", "#f7dcd3"), seed: int = 21,
             flecken: int = 5, staerke: int = 120) -> Image.Image:
    """Overlapping washes with a slightly darker, pooled edge and paper grain."""
    rnd = random.Random(seed)
    W, H = w * S, h * S
    gesamt = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    for n in range(flecken):
        lage = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        d = ImageDraw.Draw(lage)
        # Kept well inside the box: a wash cut by the picture's edge shows a straight line.
        mx, my = rnd.uniform(0.38, 0.62) * W, rnd.uniform(0.38, 0.62) * H
        rx, ry = rnd.uniform(0.16, 0.26) * W, rnd.uniform(0.16, 0.26) * H
        pts = []
        for i in range(48):
            a = 2 * math.pi * i / 48
            r = 1 + 0.12 * math.sin(3 * a + n) + 0.06 * math.sin(7 * a + 2 * n) + rnd.uniform(-0.03, 0.03)
            pts.append((mx + math.cos(a) * rx * r, my + math.sin(a) * ry * r))
        farbe = farben[n % len(farben)]
        d.polygon(pts, fill=rgba(farbe, staerke))
        d.line(pts + [pts[0]], fill=rgba(farbe, min(255, staerke + 60)), width=5 * S, joint="curve")
        lage = lage.filter(ImageFilter.GaussianBlur(9 * S))
        gesamt = Image.alpha_composite(gesamt, koernung(lage, seed + n, 0.15))
    return _klein(gesamt, w, h)


# ── 10 · Winter wedding: evergreen twigs ────────────────────────────────────

def tannenzweig(w: int, h: int, *, gruen=("#2f5a3c", "#3d6b4a", "#5b8364"), seed: int = 31,
                spiegeln: bool = False, ast: bool = True) -> Image.Image:
    """A fir twig: a brown-green stem with short needles in three greens."""
    rnd = random.Random(seed)
    W, H = w * S, h * S
    lage = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(lage)

    def zweig(stiel, laenge0):
        linie(d, stiel, rgba("#5a4a3a"), 4 * S)
        for i in range(2, len(stiel) - 1):
            p, t = stiel[i], tangente(stiel, i)
            laenge = laenge0 * (0.5 + 0.5 * math.sin(math.pi * min(1.0, (i + 6) / len(stiel))))
            for seite in (-1, 1):
                a = t + seite * rnd.uniform(0.7, 0.95) - 0.2
                q = (p[0] + math.cos(a) * laenge, p[1] + math.sin(a) * laenge)
                d.line([p, q], fill=rgba(gruen[rnd.randrange(len(gruen))]), width=int(3 * S))

    haupt = bezier((0.05 * W, 0.95 * H), (0.30 * W, 0.70 * H), (0.55 * W, 0.40 * H), (0.93 * W, 0.10 * H), 70)
    zweig(haupt, 0.075 * min(W, H))
    if ast:
        start = haupt[30]
        seiten = bezier(start, (start[0] + 0.12 * W, start[1] + 0.02 * H), (0.70 * W, 0.62 * H),
                        (0.82 * W, 0.58 * H), 34)
        zweig(seiten, 0.05 * min(W, H))
    if spiegeln:
        lage = lage.transpose(Image.Transpose.FLIP_LEFT_RIGHT)
    return _klein(lage, w, h)
