"""Generate the built-in colour looks of the booth as .cube files.

    python tools/make_luts.py            # writes booth/boothcore/media/luts_builtin/<name>.cube
    python tools/make_luts.py --check    # exit 1 if a file differs from what this script makes

Nine of the ten built-in looks arrived without a documented origin (final
review V4). They are replaced by looks defined here, each by its own small,
explicit piece of maths: tone curves, a saturation factor, a channel matrix
and split toning, with parameters chosen for this project. Nothing is copied
from a film-emulation pack or any other file; the names say what a look does,
not which film it imitates. ``warm-film.cube`` said it was "generated
programmatically" but not how; it could not be reproduced byte for byte, so
since the second review (M-series, 15.11.2026) it is made here too, from the
same description its header gave (lifted blacks, soft shoulder, mild
desaturation, warm mids, cool deep shadows), fitted to the old file: mean
difference 0.008 per channel. It looks the same, the bytes differ.

The output is deterministic: the same script gives the same bytes on every
machine (float64 maths, values clamped to 0…1 and printed with six decimals),
and a test regenerates every file and compares byte for byte. Sums of
products are written out element by element (``weighted_sum``), never as
``@``: BLAS adds in its own order, and on Linux x86_64 that flipped one
warm-film value that sits exactly on a six-decimal tie (0.6703185). The only
library maths left are ``np.exp`` and ``np.power``, whose results are not
decimal ties. The files are part of this project under its licence
(AGPL-3.0-or-later).

Format: Adobe/Resolve ``.cube``, red changes fastest, the same file names,
titles and grid sizes as before, so every profile keeps working.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Callable, Dict, Tuple

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "booth" / "boothcore" / "media" / "luts_builtin"

Rgb = np.ndarray      # shape (..., 3), floats in 0…1

LUMA = np.array([0.2126, 0.7152, 0.0722])          # Rec. 709 weights


# ── building blocks ─────────────────────────────────────────────────────

def weighted_sum(c: Rgb, w) -> np.ndarray:
    """``w[0]*r + w[1]*g + w[2]*b``, always added in this order.

    Not ``c @ w``: that goes to BLAS, and BLAS adds the products in an order
    of its own (Accelerate on macOS and OpenBLAS on Linux x86_64 differ in the
    last bit). Values that sit exactly on a six-decimal tie then print
    differently on different machines. Element-wise numpy arithmetic is IEEE
    exact, so this sum has the same bits everywhere.
    """
    return (c[..., 0] * w[0] + c[..., 1] * w[1]) + c[..., 2] * w[2]


def luma(c: Rgb) -> np.ndarray:
    return weighted_sum(c, LUMA)


def s_curve(x: np.ndarray, amount: float) -> np.ndarray:
    """Blend towards smoothstep: >0 adds contrast around mid grey, <0 flattens."""
    smooth = x * x * (3.0 - 2.0 * x)
    return x + amount * (smooth - x)


def levels(x: np.ndarray, black: float, white: float) -> np.ndarray:
    """Map 0…1 onto black…white: a raised black is a matte look, a lowered white a soft one."""
    return black + x * (white - black)


def gamma(x: np.ndarray, g: float) -> np.ndarray:
    return np.power(np.clip(x, 0.0, 1.0), g)


def saturate(c: Rgb, factor: float) -> Rgb:
    y = luma(c)[..., None]
    return y + factor * (c - y)


def matrix(c: Rgb, m) -> Rgb:
    m = np.asarray(m, dtype=np.float64)
    return np.stack([weighted_sum(c, row) for row in m], axis=-1)


def split_tone(c: Rgb, shadows, highlights, strength: float = 1.0) -> Rgb:
    """Add a tint to the shadows and another to the highlights, weighted by brightness."""
    y = np.clip(luma(c), 0.0, 1.0)[..., None]
    low = (1.0 - y) ** 2
    high = y ** 2
    return c + strength * (low * np.asarray(shadows) + high * np.asarray(highlights))


def soft_shoulder(x: np.ndarray, knee: float) -> np.ndarray:
    """Roll highlights off above ``knee`` instead of clipping them."""
    over = np.maximum(x - knee, 0.0)
    span = 1.0 - knee
    return np.where(x > knee, knee + span * (1.0 - np.exp(-over / span)) / (1.0 - np.exp(-1.0)), x)


# ── the looks ───────────────────────────────────────────────────────────
# Each takes the grid colour and returns the new one. Parameters are this
# project's own; comments say what the look is meant to do for a guest.

def bleach_bypass(c: Rgb) -> Rgb:
    """Hard, silvery: much less colour, strong contrast, slightly cool."""
    c = saturate(c, 0.45)
    c = s_curve(c, 0.85)
    c = matrix(c, [[0.98, 0.02, 0.0], [0.0, 1.0, 0.0], [0.0, 0.02, 1.02]])
    return gamma(c, 1.05)


def earthy(c: Rgb) -> Rgb:
    """Warm olive and clay: blues pulled back, greens towards olive, gentle contrast."""
    c = matrix(c, [[1.04, 0.0, -0.02], [0.03, 0.97, 0.0], [0.03, 0.04, 0.86]])
    c = saturate(c, 0.88)
    c = split_tone(c, shadows=(0.02, 0.025, -0.015), highlights=(0.03, 0.015, -0.02))
    return s_curve(c, 0.25)


def vivid_colour(c: Rgb) -> Rgb:
    """Punchy party colours: more saturation and contrast, whites stay clean."""
    c = saturate(c, 1.35)
    c = s_curve(c, 0.35)
    return soft_shoulder(c, 0.9)


def soft_matte(c: Rgb) -> Rgb:
    """Calm and flat: lifted blacks, lowered whites, a little less colour."""
    c = saturate(c, 0.85)
    c = s_curve(c, -0.12)
    return levels(c, 0.055, 0.95)


def faded_print(c: Rgb) -> Rgb:
    """An old print: faded, cool lifted shadows, slightly creamy highlights."""
    c = saturate(c, 0.62)
    c = s_curve(c, -0.1)
    c = levels(c, 0.10, 0.93)
    return split_tone(c, shadows=(-0.02, 0.025, 0.045), highlights=(0.045, 0.03, -0.04))


def natural_portrait(c: Rgb) -> Rgb:
    """Kind to skin: a touch warmer mids, softer highlights, colour slightly calmer."""
    c = matrix(c, [[1.025, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 0.97]])
    c = saturate(c, 0.92)
    c = s_curve(c, 0.15)
    return soft_shoulder(c, 0.85)


def soft_cinema(c: Rgb) -> Rgb:
    """Cinema evening: teal shadows, amber highlights, gentle contrast, a lifted floor."""
    c = saturate(c, 0.85)
    c = split_tone(c, shadows=(-0.07, 0.02, 0.075), highlights=(0.075, 0.03, -0.06))
    c = s_curve(c, 0.35)
    return levels(c, 0.025, 0.98)


def golden_warm(c: Rgb) -> Rgb:
    """Late sun: warm and golden, a little more colour, soft highlights."""
    c = matrix(c, [[1.07, 0.02, 0.0], [0.0, 1.02, 0.0], [0.04, 0.0, 0.88]])
    c = saturate(c, 1.1)
    c = s_curve(c, 0.2)
    return soft_shoulder(c, 0.88)


def classic_mono(c: Rgb) -> Rgb:
    """Black and white with weight: red-leaning grey mix (kind to skin), contrast, a deep but not crushed black."""
    y = weighted_sum(c, (0.38, 0.52, 0.10))
    y = s_curve(y, 0.45)
    y = levels(y, 0.02, 1.0)
    return np.repeat(y[..., None], 3, axis=-1)


def warm_film(c: Rgb) -> Rgb:
    """Gentle warm film: a little less colour, warm reds, cool deep shadows, soft shoulder, whites just below white.

    Parameters fitted to the earlier, undocumented file (mean difference per
    channel 0.008 on a 0…1 scale), so a profile that used it looks the same.
    """
    c = saturate(c, 0.92)
    c = matrix(c, [[1.03, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]])
    c = split_tone(c, shadows=(0.0, 0.0, 0.02), highlights=(0.0, 0.0, -0.01))
    c = soft_shoulder(c, 0.9)
    return levels(c, 0.0, 0.97)


#: file stem → (TITLE, grid size, function, one line for the header)
LOOKS: Dict[str, Tuple[str, int, Callable[[Rgb], Rgb], str]] = {
    "bleach-bypass": ("Bleach Bypass", 17, bleach_bypass, "less colour, strong contrast, slightly cool"),
    "earthy": ("Earthy", 33, earthy, "warm olive and clay, blues pulled back"),
    "vivid-colour": ("Vivid Colour", 17, vivid_colour, "more saturation and contrast, clean whites"),
    "soft-matte": ("Soft Matte", 17, soft_matte, "lifted blacks, lowered whites, calmer colour"),
    "faded-print": ("Faded Print", 17, faded_print, "faded, cool shadows, creamy highlights"),
    "natural-portrait": ("Natural Portrait", 17, natural_portrait, "warmer mids, soft highlights, kind to skin"),
    "soft-cinema": ("Soft Cinema", 17, soft_cinema, "teal shadows, amber highlights, gentle contrast"),
    "golden-warm": ("Golden Warm", 17, golden_warm, "warm and golden, a little more colour"),
    "classic-mono": ("Classic Mono", 17, classic_mono, "black and white with contrast"),
    "warm-film": ("Warm Film", 17, warm_film, "gentle warm film look, warm reds, cool deep shadows"),
}


def grid(size: int) -> Rgb:
    """Every grid colour, red changing fastest (the .cube order)."""
    steps = np.linspace(0.0, 1.0, size)
    b, g, r = np.meshgrid(steps, steps, steps, indexing="ij")
    return np.stack([r, g, b], axis=-1).reshape(-1, 3)


def cube_text(name: str) -> str:
    title, size, fn, line = LOOKS[name]
    values = np.clip(fn(grid(size)), 0.0, 1.0)
    head = [f"# {title} -- {line}",
            "# Generated by tools/make_luts.py for this project (AGPL-3.0-or-later); regenerate, do not edit.",
            f'TITLE "{title}"', f"LUT_3D_SIZE {size}",
            "DOMAIN_MIN 0.0 0.0 0.0", "DOMAIN_MAX 1.0 1.0 1.0"]
    body = [f"{r:.6f} {g:.6f} {b:.6f}" for r, g, b in values]
    return "\n".join(head + body) + "\n"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--check", action="store_true", help="only compare, write nothing")
    ap.add_argument("--out", type=Path, default=OUT)
    args = ap.parse_args(argv)
    differ = []
    for name in LOOKS:
        text = cube_text(name)
        path = args.out / f"{name}.cube"
        if args.check:
            if not path.is_file() or path.read_bytes() != text.encode("utf-8"):
                differ.append(path.name)
        else:
            args.out.mkdir(parents=True, exist_ok=True)
            path.write_bytes(text.encode("utf-8"))
    if differ:
        print("differs from tools/make_luts.py: " + ", ".join(differ))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
