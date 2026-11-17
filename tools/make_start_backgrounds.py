"""Write the start-screen backgrounds that ship with the booth.

    python tools/make_start_backgrounds.py

Plain SVG gradients and patterns, drawn here and nowhere else: no photo, no
person, no third-party image, a few hundred bytes each. They go to the
booth (``booth/boothcore/web/static/start/``, served to the kiosk) and to the
gallery (``app/static/booth-start/``, previews in the booth profile form);
``booth/tests/test_start_backgrounds.py`` keeps both copies equal to this
script's output. The profile names one as ``builtin:<id>.svg`` in
``start.media``; the ids are ``START_BUILTINS`` in ``boothcore.config`` and
``app.ecosystem``.
"""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TARGETS = [ROOT / "booth" / "boothcore" / "web" / "static" / "start", ROOT / "app" / "static" / "booth-start"]

HEAD = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1920 1200" preserveAspectRatio="xMidYMid slice">'


def _linear(a: str, b: str, c: str) -> str:
    return (f'<defs><linearGradient id="g" x1="0" y1="0" x2="1" y2="1">'
            f'<stop offset="0" stop-color="{a}"/><stop offset=".55" stop-color="{b}"/>'
            f'<stop offset="1" stop-color="{c}"/></linearGradient></defs><rect width="1920" height="1200" fill="url(#g)"/>')


def _glow(base: str, glow: str, x: float, y: float) -> str:
    return (f'<defs><radialGradient id="r" cx="{x}" cy="{y}" r=".75">'
            f'<stop offset="0" stop-color="{glow}"/><stop offset="1" stop-color="{base}"/></radialGradient></defs>'
            f'<rect width="1920" height="1200" fill="url(#r)"/>')


def _dots(base: str, dot: str) -> str:
    return (f'<defs><pattern id="p" width="48" height="48" patternUnits="userSpaceOnUse">'
            f'<circle cx="24" cy="24" r="3" fill="{dot}"/></pattern>'
            f'<radialGradient id="v" cx=".5" cy=".45" r=".8"><stop offset="0" stop-color="{base}" stop-opacity="0"/>'
            f'<stop offset="1" stop-color="#000" stop-opacity=".55"/></radialGradient></defs>'
            f'<rect width="1920" height="1200" fill="{base}"/><rect width="1920" height="1200" fill="url(#p)"/>'
            f'<rect width="1920" height="1200" fill="url(#v)"/>')


def _waves(base: str, a: str, b: str) -> str:
    return (f'<rect width="1920" height="1200" fill="{base}"/>'
            f'<path d="M0 760 C 420 620 760 900 1180 760 S 1700 600 1920 700 V1200 H0Z" fill="{a}"/>'
            f'<path d="M0 930 C 380 820 820 1040 1240 920 S 1720 820 1920 900 V1200 H0Z" fill="{b}"/>')


#: id -> (German name for the pickers, SVG body)
BACKGROUNDS = {
    "abend": ("Abendblau", _linear("#141a3a", "#2b2257", "#4a2a4f")),
    "nacht": ("Nacht", _glow("#09090b", "#26262e", .3, .2)),
    "sand": ("Sand (hell)", _linear("#f4ead9", "#ead6b8", "#d9b88f")),
    "salbei": ("Salbei", _linear("#1d2b25", "#2f4a3d", "#5d7a63")),
    "punkte": ("Punkte", _dots("#16161b", "#2c2c35")),
    "wellen": ("Wellen", _waves("#101827", "#1b2a44", "#24395c")),
}


def svg(body: str) -> str:
    return HEAD + body + "</svg>\n"


def main() -> None:
    for folder in TARGETS:
        folder.mkdir(parents=True, exist_ok=True)
        for key, (_, body) in BACKGROUNDS.items():
            (folder / f"{key}.svg").write_text(svg(body), encoding="utf-8")
    print("wrote", len(BACKGROUNDS), "backgrounds to", len(TARGETS), "folders")


if __name__ == "__main__":
    main()
