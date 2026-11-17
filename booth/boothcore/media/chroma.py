"""Classic chroma keying (green screen) for the photo booth.

The booth shoots a guest in front of a coloured backdrop and wants the
backdrop replaced by something nicer. This module does exactly that, with
numpy and Pillow only -- no machine learning, no model downloads, no GPU.

Two public functions:

* :func:`key_out`   -- photo in, RGBA photo with a transparent backdrop out.
* :func:`composite` -- the same, then placed on a new background.

Both are pure functions: no global state, no caches, and no file system
access apart from loading a background path in :func:`composite`.

How the mask is built
---------------------
The key colour and every pixel are reduced to *chromaticity*
``(R, G, B) / (R + G + B)``, which is largely independent of how bright a
pixel is. A backdrop fold in shadow therefore lands on virtually the same
chromaticity as the brightly lit part next to it, so uneven lighting does
not tear holes into the mask. A small epsilon is added to every channel
before normalising, which pulls nearly black pixels (dark hair, pupils)
towards neutral grey and thus safely towards the foreground, where they
belong.

The distance between a pixel and the key colour is measured in that
chromaticity plane and then normalised by the distance from the key colour
to neutral grey. A value of ``0`` means "exactly the backdrop colour" and
``1`` means "as far from the backdrop as plain grey is" -- which is why the
default ``tolerance`` of 0.35 is a sensible middle ground and why the number
means the same thing for a pale and for a vivid backdrop.

Safety rule
-----------
If keying would make more than 95% of the image transparent (guest stood in
front of a wall, backdrop fell over, wrong key colour), the result is the
unchanged photo with a fully opaque alpha channel instead of an almost empty
frame. The same happens when no colourful backdrop can be found at all in
``color="auto"`` mode. A guest never ends up invisible.
"""
from __future__ import annotations

import math
from typing import Dict, Tuple

import numpy as np
from PIL import Image, ImageColor, ImageFilter

__all__ = ["key_out", "composite", "estimate_key_color", "FITS"]

#: Backdrops we sell with the box, as plain RGB.
NAMED_KEY_COLORS: Dict[str, Tuple[int, int, int]] = {
    "green": (0, 177, 64),   # classic chroma green
    "blue": (0, 71, 187),    # classic chroma blue
}

#: Accepted values for ``fit`` in :func:`composite`.
FITS = ("cover", "contain", "stretch")

# Added to every channel before normalising. Keeps the chromaticity of very
# dark pixels stable and biases them towards neutral (= foreground).
_DARK_GUARD = 0.05

# The mask is computed on a copy of at most this many pixels and the alpha is
# scaled back up afterwards. Keeps a 24 megapixel photo in the seconds range;
# the mask carries no detail that would survive at full resolution anyway.
_MASK_MAX_PIXELS = 1_600_000

# Fraction of the shorter edge sampled per side when estimating the key colour.
_BORDER_FRACTION = 0.06

# Below this distance between key colour and neutral grey there is no backdrop
# colour worth keying (grey or white wall).
_MIN_KEY_CHROMA = 0.03

# More transparency than this triggers the safety rule.
_MAX_TRANSPARENT_RATIO = 0.95

# Rows are processed in blocks of roughly this many pixels in the full
# resolution stage, so peak memory stays modest on huge photos.
_CHUNK_PIXELS = 4_000_000


# --------------------------------------------------------------------------- #
# key colour
# --------------------------------------------------------------------------- #
def _chromaticity(rgb: np.ndarray) -> np.ndarray:
    """Normalise ``rgb`` (float, 0..1, last axis = channel) to chromaticity."""
    total = rgb.sum(axis=-1, keepdims=True) + 3.0 * _DARK_GUARD
    return (rgb + _DARK_GUARD) / total


def _border_pixels(small: np.ndarray) -> np.ndarray:
    """Return the outer border of ``small`` as a flat ``(n, 3)`` array."""
    height, width = small.shape[:2]
    band_y = max(1, int(round(height * _BORDER_FRACTION)))
    band_x = max(1, int(round(width * _BORDER_FRACTION)))
    strips = (
        small[:band_y].reshape(-1, 3),
        small[height - band_y:].reshape(-1, 3),
        small[:, :band_x].reshape(-1, 3),
        small[:, width - band_x:].reshape(-1, 3),
    )
    return np.concatenate(strips, axis=0)


def estimate_key_color(image: Image.Image) -> Tuple[int, int, int] | None:
    """Guess the backdrop colour of ``image`` from its border.

    Samples the outer ~6% on every side, decides whether green or blue
    dominates there and returns the median RGB of that cluster. Returns
    ``None`` when the border holds no colourful backdrop at all.
    """
    small = _downscale(image.convert("RGB"))
    return _estimate_key_color(np.asarray(small, dtype=np.float32) / 255.0)


def _estimate_key_color(arr: np.ndarray) -> Tuple[int, int, int] | None:
    """Border based key colour estimate on an already normalised array."""
    border = _border_pixels(arr)
    chroma = _chromaticity(border)
    red, green, blue = chroma[:, 0], chroma[:, 1], chroma[:, 2]

    # How much does a pixel lean towards green / towards blue?
    greenness = green - 0.5 * (red + blue)
    blueness = blue - 0.5 * (red + green)

    # Whichever colour the *saturated* part of the border leans to wins.
    scores = greenness if np.percentile(greenness, 75) >= np.percentile(blueness, 75) else blueness

    # Median over the most saturated fifth of the border: robust against a
    # guest's shoulder, a lamp or a chair sticking into the sampled band.
    cluster = border[scores >= np.percentile(scores, 80)]
    if cluster.size == 0:
        return None
    median = np.median(cluster, axis=0)
    if _key_chroma_strength(median) < _MIN_KEY_CHROMA:
        return None
    return tuple(int(round(float(c) * 255.0)) for c in median)  # type: ignore[return-value]


def _key_chroma_strength(key_rgb: np.ndarray) -> float:
    """Distance of the key colour from neutral grey in the chromaticity plane."""
    chroma = _chromaticity(np.asarray(key_rgb, dtype=np.float32))
    return float(np.linalg.norm(chroma - 1.0 / 3.0))


def _resolve_key_color(arr: np.ndarray, color: str) -> np.ndarray | None:
    """Turn the ``color`` argument into a normalised RGB triple.

    Accepts ``"auto"``, ``"green"``, ``"blue"`` and ``"#rrggbb"``. Returns
    ``None`` only for ``"auto"`` when no backdrop colour can be found.
    """
    if not isinstance(color, str):
        raise TypeError(f"color must be a string, got {type(color).__name__}")
    key = color.strip().lower()
    if key == "auto":
        estimate = _estimate_key_color(arr)
        if estimate is None:
            return None
        return np.asarray(estimate, dtype=np.float32) / 255.0
    if key in NAMED_KEY_COLORS:
        return np.asarray(NAMED_KEY_COLORS[key], dtype=np.float32) / 255.0
    try:
        rgb = ImageColor.getrgb(key)[:3]
    except ValueError as exc:
        raise ValueError(
            f"unknown key colour {color!r}: expected 'auto', "
            f"{', '.join(sorted(NAMED_KEY_COLORS))} or '#rrggbb'"
        ) from exc
    return np.asarray(rgb, dtype=np.float32) / 255.0


# --------------------------------------------------------------------------- #
# mask
# --------------------------------------------------------------------------- #
def _downscale(image: Image.Image) -> Image.Image:
    """Shrink ``image`` to at most ``_MASK_MAX_PIXELS`` for mask work."""
    pixels = image.width * image.height
    if pixels <= _MASK_MAX_PIXELS:
        return image
    factor = int(math.ceil(math.sqrt(pixels / _MASK_MAX_PIXELS)))
    return image.reduce(factor)  # box averaging, very fast


def _alpha_from_chroma(arr: np.ndarray, key: np.ndarray, tolerance: float, softness: float) -> np.ndarray:
    """Soft foreground mask (float32, 0..1) for a normalised RGB array."""
    key_chroma = _chromaticity(key)
    strength = float(np.linalg.norm(key_chroma - 1.0 / 3.0))

    total = arr.sum(axis=2) + 3.0 * _DARK_GUARD
    distance = np.zeros(arr.shape[:2], dtype=np.float32)
    for channel in range(3):
        diff = (arr[:, :, channel] + _DARK_GUARD) / total - key_chroma[channel]
        distance += diff * diff
    np.sqrt(distance, out=distance)

    # 0 = exactly the backdrop colour, 1 = as far away as neutral grey.
    distance /= strength

    # Ramp from "backdrop" to "subject" across the softness band, with a
    # smoothstep so the edge does not alias.
    band = max(float(softness), 1e-4)
    ramp = np.clip((distance - float(tolerance)) / band, 0.0, 1.0, out=distance)
    return ramp * ramp * (3.0 - 2.0 * ramp)


def _feather(alpha: np.ndarray, radius: float = 1.2) -> np.ndarray:
    """Blur the mask a touch so hair and fabric edges stay believable."""
    as_image = Image.fromarray(np.clip(alpha * 255.0, 0, 255).astype(np.uint8), "L")
    blurred = as_image.filter(ImageFilter.GaussianBlur(radius))
    return np.asarray(blurred, dtype=np.uint8)


# --------------------------------------------------------------------------- #
# spill
# --------------------------------------------------------------------------- #
def _suppress_spill(rgb: np.ndarray, alpha: np.ndarray, key_index: int, spill: float) -> None:
    """Remove backdrop colour bounced onto the subject, in place.

    The key channel is pulled down towards the average of the other two, but
    only where it sits *above* that average -- the definition of a colour cast
    from the backdrop. Skin, grey and warm tones never have their green (or
    blue) channel above the average of the other two, so they are left exactly
    as they are. Semi-transparent pixels, where spill is worst, get the
    treatment at up to full strength.
    """
    others = [c for c in range(3) if c != key_index]
    key_channel = rgb[:, :, key_index].astype(np.float32)
    limit = rgb[:, :, others[0]].astype(np.float32)
    limit += rgb[:, :, others[1]]
    limit *= 0.5

    excess = key_channel - limit
    np.maximum(excess, 0.0, out=excess)

    weight = alpha.astype(np.float32) / 255.0
    weight *= 1.0 - weight          # peaks at half transparency
    weight *= 4.0                   # ...where it reaches 1.0
    weight += 1.0
    weight *= float(spill)
    np.clip(weight, 0.0, 1.0, out=weight)

    excess *= weight
    key_channel -= excess
    np.clip(key_channel, 0.0, 255.0, out=key_channel)
    rgb[:, :, key_index] = key_channel.astype(np.uint8)


# --------------------------------------------------------------------------- #
# public API
# --------------------------------------------------------------------------- #
def key_out(image: Image.Image, *, color: str = "auto", tolerance: float = 0.35,
            softness: float = 0.08, spill: float = 0.4) -> Image.Image:
    """Return an RGBA copy of `image` with the backdrop removed.

    Args:
        image: The photo. Anything Pillow can convert; RGB is expected and
            other modes (including an existing alpha channel) are converted
            to RGB first.
        color: ``"auto"`` estimates the backdrop colour from the image border,
            ``"green"`` and ``"blue"`` use the classic chroma colours, and
            ``"#rrggbb"`` sets it explicitly.
        tolerance: How far a pixel may deviate from the key colour and still
            count as backdrop, measured in "distance from the key colour to
            neutral grey". Larger values key more away.
        softness: Width of the transition band above ``tolerance``. Larger
            values give a softer, less aliased edge.
        spill: Strength (0..1) of the backdrop colour cast removal on the
            subject. ``0`` disables it.

    Returns:
        A new RGBA image of the same size. Backdrop pixels have alpha 0,
        subject pixels alpha 255, edges something in between.

        **Safety rule:** if keying would leave more than 95% of the image
        transparent, or if ``color="auto"`` finds no colourful backdrop at
        all, the photo is returned with its pixels untouched and a fully
        opaque alpha channel. The return type is therefore always RGBA, also
        in that case -- callers do not have to check the mode.
    """
    source = image if image.mode == "RGB" else image.convert("RGB")

    small = _downscale(source)
    arr = np.asarray(small, dtype=np.float32) / 255.0

    key = _resolve_key_color(arr, color)
    if key is None:
        return _opaque(source)

    alpha_small = _feather(_alpha_from_chroma(arr, key, tolerance, softness))
    del arr

    # Safety rule: decided on the small mask, which has the same proportions
    # as the full sized one but is far cheaper to measure.
    transparent_ratio = float(np.count_nonzero(alpha_small < 128)) / alpha_small.size
    if transparent_ratio > _MAX_TRANSPARENT_RATIO:
        return _opaque(source)

    alpha_image = Image.fromarray(alpha_small, "L")
    if alpha_image.size != source.size:
        alpha_image = alpha_image.resize(source.size, Image.Resampling.BILINEAR)
    alpha_full = np.asarray(alpha_image, dtype=np.uint8)

    rgb = np.array(source, dtype=np.uint8, copy=True)
    if spill > 0.0:
        key_index = int(np.argmax(key))
        rows = max(1, _CHUNK_PIXELS // max(1, rgb.shape[1]))
        for start in range(0, rgb.shape[0], rows):
            stop = min(start + rows, rgb.shape[0])
            _suppress_spill(rgb[start:stop], alpha_full[start:stop], key_index, spill)

    out = Image.fromarray(rgb, "RGB")
    out.putalpha(alpha_image)
    return out


def _opaque(source: Image.Image) -> Image.Image:
    """The unchanged photo as RGBA with a fully opaque alpha channel."""
    out = source.convert("RGBA")
    out.putalpha(255)
    return out


def _prepare_background(background: Image.Image | str, size: Tuple[int, int], fit: str) -> Image.Image:
    """Build an RGB canvas of exactly ``size`` from an image or a colour."""
    if fit not in FITS:
        raise ValueError(f"unknown fit {fit!r}: expected one of {', '.join(FITS)}")

    if isinstance(background, str):
        text = background.strip()
        if text.startswith("#"):
            return Image.new("RGB", size, ImageColor.getrgb(text))
        with Image.open(text) as opened:      # the only file access in this module
            source = opened.convert("RGB")
    elif isinstance(background, Image.Image):
        source = background.convert("RGB")
    else:
        raise TypeError("background must be an image, a file path or a '#rrggbb' colour")

    target_w, target_h = size
    if fit == "stretch":
        return source.resize(size, Image.Resampling.LANCZOS)

    scale_w = target_w / source.width
    scale_h = target_h / source.height
    scale = max(scale_w, scale_h) if fit == "cover" else min(scale_w, scale_h)
    scaled = source.resize(
        (max(1, int(math.ceil(source.width * scale))), max(1, int(math.ceil(source.height * scale)))),
        Image.Resampling.LANCZOS,
    )

    if fit == "cover":
        left = (scaled.width - target_w) // 2
        top = (scaled.height - target_h) // 2
        return scaled.crop((left, top, left + target_w, top + target_h))

    # contain: keep the whole background visible, pad the rest with black
    canvas = Image.new("RGB", size, (0, 0, 0))
    canvas.paste(scaled, ((target_w - scaled.width) // 2, (target_h - scaled.height) // 2))
    return canvas


def composite(image: Image.Image, background: Image.Image | str, *, fit: str = "cover", **key_opts) -> Image.Image:
    """Key out the backdrop and place the subject on `background` (an image or a '#rrggbb' colour).

    Args:
        image: The photo, as for :func:`key_out`.
        background: A Pillow image, a path to an image file, or a
            ``"#rrggbb"`` colour.
        fit: How an image background fills the photo's canvas.
            ``"cover"`` scales it up and crops the overflow (no distortion),
            ``"contain"`` scales it to fit completely and pads with black,
            ``"stretch"`` resizes it to the exact canvas.
        **key_opts: Passed straight to :func:`key_out` (``color``,
            ``tolerance``, ``softness``, ``spill``).

    Returns:
        A new opaque RGB image the size of ``image``. When the safety rule in
        :func:`key_out` kicks in, the subject is fully opaque and the result
        is the original photo -- the background stays hidden rather than
        swallowing the guest.
    """
    subject = key_out(image, **key_opts)
    canvas = _prepare_background(background, subject.size, fit)
    canvas.paste(subject, (0, 0), subject)
    return canvas
