"""3D colour lookup tables (.cube) for the booth.

Loads Adobe ``.cube`` files -- the interchange format used by dslrBooth,
LumaBooth, Lightroom, Resolve and most LUT packs -- and applies them to
Pillow images with vectorised trilinear interpolation.

Only numpy and Pillow are required. A grade is a pure function of the
pixel values, so a LUT can be wrapped as a booth look (see :func:`as_look`).

Typical use::

    luts = load_folder(Path(data) / "luts")
    img = apply_lut(img, luts["warm-film"], strength=0.8)
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, Tuple, Union

import numpy as np
from PIL import Image

__all__ = [
    "Lut",
    "parse_cube",
    "apply_lut",
    "load_folder",
    "builtin_folder",
    "load_builtin",
    "slugify",
    "canonical_key",
    "LEGACY_KEYS",
    "as_look",
    "looks_from_folder",
]

#: Extension of the files :func:`load_folder` picks up.
CUBE_EXT = ".cube"

#: Folder with the LUTs shipped inside the package.
BUILTIN_DIR = Path(__file__).resolve().parent / "luts_builtin"

#: Keys the built-in looks had until 0.3. They were named after Kodak and
#: Fujifilm films; those names are trademarks and the looks are not those
#: films, so they were renamed after what they do. Profiles, house looks and
#: printed menus still carry the old keys, so they keep resolving. The tables
#: themselves did not change. The gallery keeps a copy in
#: ``app/ecosystem.py`` (``LEGACY_LUT_KEYS``); a test keeps the two in step.
LEGACY_KEYS: Dict[str, str] = {
    "film-ektar-100": "vivid-colour",
    "film-fuji-800z": "soft-matte",
    "film-gold-200": "faded-print",
    "film-portra-400": "natural-portrait",
    "fuji-eterna": "soft-cinema",
    "kodak-gold": "golden-warm",
    "mono-trix-400": "classic-mono",
}

# Pixels processed per numpy pass. Keeps peak memory bounded (a 24 MP image
# would otherwise need several hundred MB per intermediate array) without
# ever looping over single pixels.
_CHUNK = 1 << 21

RGBTriple = Tuple[float, float, float]


@dataclass
class Lut:
    """A colour lookup table normalised to a 3D grid.

    ``table`` is indexed ``table[r, g, b]`` -> output RGB, with all values
    in 0..1. 1D LUTs are expanded to an equivalent 3D table on load, so
    callers never have to care which flavour a file contained.
    """

    name: str
    size: int
    table: np.ndarray
    domain_min: RGBTriple = (0.0, 0.0, 0.0)
    domain_max: RGBTriple = (1.0, 1.0, 1.0)
    #: Extra metadata from the file (currently the raw ``TITLE``).
    meta: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.table = np.ascontiguousarray(self.table, dtype=np.float32)
        expected = (self.size, self.size, self.size, 3)
        if self.table.shape != expected:
            raise ValueError(f"LUT '{self.name}': table shape {self.table.shape}, expected {expected}")

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"Lut(name={self.name!r}, size={self.size})"


def slugify(value: str) -> str:
    """File name or title -> stable, URL-safe key (``Warm Film!.cube`` -> ``warm-film``)."""
    slug = re.sub(r"[^a-z0-9]+", "-", str(value).strip().lower())
    return slug.strip("-") or "lut"


def canonical_key(key: str) -> str:
    """Current key for a look, with or without the ``lut-`` prefix.

    ``kodak-gold`` -> ``golden-warm``, ``lut-kodak-gold`` -> ``lut-golden-warm``;
    anything that is not an old built-in key comes back unchanged.
    """
    key = str(key or "")
    prefix = "lut-" if key.startswith("lut-") else ""
    bare = key[len(prefix):]
    return prefix + LEGACY_KEYS.get(bare, bare)


# ── Parsing ───────────────────────────────────────────────


def _read_source(text_or_path: Union[str, Path]) -> Tuple[str, str]:
    """Return ``(text, name)`` for a path, a path-like string or raw .cube text."""
    if isinstance(text_or_path, Path):
        return text_or_path.read_text(encoding="utf-8", errors="replace"), text_or_path.stem
    text = str(text_or_path)
    if "\n" not in text and "\r" not in text and len(text) < 4096:
        candidate = Path(text)
        try:
            is_file = candidate.is_file()
        except OSError:
            is_file = False
        if is_file:
            return candidate.read_text(encoding="utf-8", errors="replace"), candidate.stem
    return text, "lut"


def _parse_triple(parts, keyword: str, name: str) -> RGBTriple:
    if len(parts) != 3:
        raise ValueError(f"LUT '{name}': {keyword} needs three values, got {len(parts)}")
    try:
        return (float(parts[0]), float(parts[1]), float(parts[2]))
    except ValueError as exc:
        raise ValueError(f"LUT '{name}': {keyword} has non-numeric values: {parts}") from exc


def parse_cube(text_or_path: Union[str, Path]) -> Lut:
    """Parse an Adobe ``.cube`` file (or its text) into a :class:`Lut`.

    Understands ``LUT_3D_SIZE``, ``LUT_1D_SIZE``, ``TITLE``, ``DOMAIN_MIN``
    and ``DOMAIN_MAX``. Comments (``#``), blank lines and CRLF line endings
    are ignored, table values outside 0..1 are clipped. Anything genuinely
    broken -- no size, wrong number of entries, non-numeric data -- raises
    :class:`ValueError` naming the problem.
    """
    text, name = _read_source(text_or_path)

    size_3d = size_1d = None
    title = ""
    domain_min: RGBTriple = (0.0, 0.0, 0.0)
    domain_max: RGBTriple = (1.0, 1.0, 1.0)
    rows: list[list[float]] = []

    for lineno, raw in enumerate(text.splitlines(), start=1):
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        head, *rest = line.split()
        key = head.upper()
        if key == "TITLE":
            title = line[len(head):].strip().strip('"')
        elif key in ("LUT_3D_SIZE", "LUT_1D_SIZE"):
            if not rest:
                raise ValueError(f"LUT '{name}': {key} without a value (line {lineno})")
            try:
                value = int(float(rest[0]))
            except ValueError as exc:
                raise ValueError(f"LUT '{name}': {key} is not a number: {rest[0]!r}") from exc
            if value < 2:
                raise ValueError(f"LUT '{name}': {key} must be at least 2, got {value}")
            if key == "LUT_3D_SIZE":
                size_3d = value
            else:
                size_1d = value
        elif key == "DOMAIN_MIN":
            domain_min = _parse_triple(rest, key, name)
        elif key == "DOMAIN_MAX":
            domain_max = _parse_triple(rest, key, name)
        elif key in ("LUT_3D_INPUT_RANGE", "LUT_1D_INPUT_RANGE"):
            if len(rest) == 2:
                lo, hi = float(rest[0]), float(rest[1])
                domain_min, domain_max = (lo, lo, lo), (hi, hi, hi)
        elif len(head) and (head[0].isdigit() or head[0] in "+-."):
            if len(rest) != 2:
                raise ValueError(f"LUT '{name}': data line {lineno} has {len(rest) + 1} values, expected 3")
            try:
                rows.append([float(head), float(rest[0]), float(rest[1])])
            except ValueError as exc:
                raise ValueError(f"LUT '{name}': non-numeric data on line {lineno}: {line!r}") from exc
        # Unknown keywords are ignored on purpose -- LUT packs add their own.

    if size_3d is None and size_1d is None:
        raise ValueError(f"LUT '{name}': neither LUT_3D_SIZE nor LUT_1D_SIZE found")
    if not rows:
        raise ValueError(f"LUT '{name}': no table entries found")
    if any(hi <= lo for lo, hi in zip(domain_min, domain_max)):
        raise ValueError(f"LUT '{name}': DOMAIN_MAX must be greater than DOMAIN_MIN")

    data = np.clip(np.asarray(rows, dtype=np.float32), 0.0, 1.0)

    if size_3d is not None:
        expected = size_3d ** 3
        if data.shape[0] != expected:
            raise ValueError(
                f"LUT '{name}': LUT_3D_SIZE {size_3d} needs {expected} entries, found {data.shape[0]}"
            )
        # .cube stores the red index changing fastest, so the natural reshape
        # is [b][g][r]; transpose to the [r][g][b] order the Lut promises.
        table = data.reshape(size_3d, size_3d, size_3d, 3).transpose(2, 1, 0, 3)
        size = size_3d
    else:
        if data.shape[0] != size_1d:
            raise ValueError(
                f"LUT '{name}': LUT_1D_SIZE {size_1d} needs {size_1d} entries, found {data.shape[0]}"
            )
        table = _table_from_1d(data)
        size = int(size_1d)

    return Lut(
        name=title or name,
        size=size,
        table=np.ascontiguousarray(table, dtype=np.float32),
        domain_min=domain_min,
        domain_max=domain_max,
        meta={"title": title, "kind": "1d" if size_3d is None else "3d"},
    )


def _table_from_1d(curves: np.ndarray) -> np.ndarray:
    """Expand per-channel curves (n, 3) into an equivalent 3D table (n, n, n, 3)."""
    n = curves.shape[0]
    table = np.empty((n, n, n, 3), dtype=np.float32)
    table[..., 0] = curves[:, 0].reshape(n, 1, 1)
    table[..., 1] = curves[:, 1].reshape(1, n, 1)
    table[..., 2] = curves[:, 2].reshape(1, 1, n)
    return table


# ── Applying ──────────────────────────────────────────────


def _index_tables(lut: "Lut"):
    """Per-channel 8-bit -> (grid index, fraction) tables, domain folded in.

    Input pixels are bytes, so the position inside the cube only ever takes
    256 values per channel. Precomputing them turns the per-pixel divide,
    clip and floor into a small gather.
    """
    base = np.empty((3, 256), dtype=np.intp)
    frac = np.empty((3, 256), dtype=np.float32)
    values = np.arange(256, dtype=np.float32) / 255.0
    for ch in range(3):
        span = lut.domain_max[ch] - lut.domain_min[ch]
        pos = np.clip((values - lut.domain_min[ch]) / span, 0.0, 1.0) * (lut.size - 1)
        base[ch] = np.clip(np.floor(pos), 0, lut.size - 2).astype(np.intp)
        frac[ch] = pos - base[ch]
    return base, frac


def _sample_trilinear(block: np.ndarray, flat: np.ndarray, size: int,
                      base: np.ndarray, frac: np.ndarray) -> np.ndarray:
    """Trilinear lookup for an (n, 3) block of uint8 pixels -> (n, 3) float32."""
    n = block.shape[0]
    r, g, b = block[:, 0], block[:, 1], block[:, 2]
    idx = (base[0][r] * size + base[1][g]) * size + base[2][b]
    fr, fg, fb = frac[0][r][:, None], frac[1][g][:, None], frac[2][b][:, None]
    stride_r, stride_g = size * size, size

    out = np.zeros((n, 3), dtype=np.float32)
    corner = np.empty_like(out)
    weight = np.empty((n, 1), dtype=np.float32)
    for dr in (0, 1):
        wr = fr if dr else 1.0 - fr
        for dg in (0, 1):
            wg = fg if dg else 1.0 - fg
            wrg = wr * wg
            for db in (0, 1):
                np.multiply(wrg, fb if db else 1.0 - fb, out=weight)
                np.take(flat, idx + dr * stride_r + dg * stride_g + db, axis=0, out=corner)
                corner *= weight
                out += corner
    return out


def apply_lut(image: Image.Image, lut: Lut, strength: float = 1.0) -> Image.Image:
    """Apply ``lut`` to ``image`` with trilinear interpolation.

    ``strength`` blends the graded result with the original: 0 returns the
    image untouched, 1.0 the full grade, values in between a linear mix.
    Non-RGB images are converted first; size, EXIF and ICC profile survive.
    """
    strength = float(min(1.0, max(0.0, strength)))
    if strength == 0.0:
        return image

    src = image if image.mode == "RGB" else image.convert("RGB")
    arr = np.asarray(src, dtype=np.uint8)
    flat_src = arr.reshape(-1, 3)
    result = np.empty_like(flat_src)

    flat_table = np.ascontiguousarray(lut.table.reshape(-1, 3))
    base, frac = _index_tables(lut)

    # Chunked so peak memory stays bounded on 24 MP frames; still no per-pixel loop.
    for start in range(0, flat_src.shape[0], _CHUNK):
        block = flat_src[start:start + _CHUNK]
        graded = _sample_trilinear(block, flat_table, lut.size, base, frac)
        if strength < 1.0:
            original = block.astype(np.float32)
            original /= 255.0
            graded -= original
            graded *= strength
            graded += original
        graded *= 255.0
        graded += 0.5
        np.clip(graded, 0.0, 255.0, out=graded)
        result[start:start + _CHUNK] = graded.astype(np.uint8)

    out = Image.fromarray(result.reshape(arr.shape), mode="RGB")
    for key in ("exif", "icc_profile", "dpi"):
        if key in image.info:
            out.info[key] = image.info[key]
    return out


# ── Folders ───────────────────────────────────────────────

# folder -> (signature, luts). The signature is the set of file stats, so a
# repeated call only costs one directory scan.
_CACHE: Dict[Path, Tuple[tuple, Dict[str, Lut]]] = {}


def _signature(folder: Path) -> tuple:
    entries = []
    for path in sorted(folder.glob("*")):
        if path.suffix.lower() != CUBE_EXT or not path.is_file():
            continue
        stat = path.stat()
        entries.append((path.name, stat.st_mtime_ns, stat.st_size))
    return tuple(entries)


def load_folder(folder: Union[str, Path]) -> Dict[str, Lut]:
    """Load every ``*.cube`` in ``folder``, keyed by a slug of the file name.

    Cached on path plus file mtime/size, so calling this per photo is cheap.
    Unreadable or broken files are skipped instead of breaking the shoot.
    """
    folder = Path(folder)
    if not folder.is_dir():
        return {}
    signature = _signature(folder)
    cached = _CACHE.get(folder)
    if cached and cached[0] == signature:
        return cached[1]

    luts: Dict[str, Lut] = {}
    for name, _mtime, _size in signature:
        path = folder / name
        try:
            luts[slugify(path.stem)] = parse_cube(path)
        except (ValueError, OSError):
            continue
    _CACHE[folder] = (signature, luts)
    return luts


def builtin_folder() -> Path:
    """Folder with the LUTs shipped with the booth."""
    return BUILTIN_DIR


def load_builtin() -> Dict[str, Lut]:
    """Load the LUTs shipped with the booth."""
    return load_folder(BUILTIN_DIR)


# ── Booth integration ─────────────────────────────────────

LookFn = Callable[[Image.Image], Image.Image]


def as_look(lut: Lut, strength: float = 1.0) -> LookFn:
    """Wrap a LUT as a booth look function ``Image -> Image``."""

    def _look(img: Image.Image) -> Image.Image:
        return apply_lut(img, lut, strength)

    _look.__name__ = f"lut_{slugify(lut.name)}"
    _look.__doc__ = f"LUT look '{lut.name}' at strength {strength:g}."
    return _look


def looks_from_folder(folder: Union[str, Path], strength: float = 1.0, prefix: str = "lut-") -> Dict[str, LookFn]:
    """``{look_id: look_fn}`` for every LUT in a folder, ready to register."""
    return {f"{prefix}{key}": as_look(lut, strength) for key, lut in load_folder(folder).items()}
