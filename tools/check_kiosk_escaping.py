"""Fail when the kiosk builds markup from a value that is not escaped.

The second review (R2-U1) found ``data-mode="${m}"`` in app.js: a profile
entry went into an HTML attribute as it was. This check reads the booth's
page scripts, finds every template literal that builds markup (it contains
``<`` or ``="``) and looks at each ``${…}`` in it. An interpolation is fine
when it is escaped or provably harmless (``esc(…)``, ``hex(…)``, ``num(…)``,
``iconFor(…)``, a number from a loop); every other one must be listed in
``REVIEWED`` below with the reason it is safe. A new unescaped one makes this
script exit 1 and ``booth/tests/test_kiosk_escaping.py`` fail.

Usage: python tools/check_kiosk_escaping.py
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

WEB = Path(__file__).resolve().parents[1] / "booth" / "boothcore" / "web" / "static"
#: The kiosk page: what every guest touches. The admin pages sit behind the
#: PIN and were reviewed by hand (R2-U1); the CSP covers all of them.
FILES = ("app.js",)

#: Interpolations that need no escaping, as a pattern over the expression.
SAFE = re.compile(
    r"^(?:esc|hex|num|iconFor|layoutOutline|layoutCard|CSS\.escape|encodeURIComponent)\(.*\)$"  # helpers
    r"|^Date\.now\(\)$"
    r"|^[A-Z_]+\.\w+$"                                     # constants (ICONS.play)
    r"|^(?:i|n|k|idx|bust|muted|range|hold|data|pic|id|rects|r|cw|ch|fs)$"  # loop counters, built locally
    r"|^\(?[\w.]+\s*[+\-*/]\s*[\w.\s+\-*/()]+\)?$"          # arithmetic on locals
    r"|^Math\.\w+\(.*\)$|^\w+\.toFixed\(\d\)$|^String\(\w+\)$"
    r"|^[^?`]+\?\s*(?:\"[^\"]*\"|`[^`]*`)\s*:\s*(?:\"[^\"]*\"|`[^`]*`)$"  # cond ? "a" : `<b>` (inner one checked itself)
    r"|^!!?[\w.()]+$|^[\w.]+ === [\w.\"]+$",                 # booleans
    re.S)

#: Reviewed by hand (expression -> why it is safe). Keep it short.
REVIEWED = {
    'cam': "a fixed class name (ok/bad)",
    'pr': "a fixed class name",
    'sync': "a fixed class name",
    'pend': "built with esc() a few lines above",
    'prWait': "built with esc() a few lines above",
    'meldungHtml': "built with esc() above",
    'Math.min(i, 12)': "a number",
    'item.uid': "uid inside a URL path; the booth makes uids [a-z0-9-]",
    'flow.uid': "same",
    'k === "OK" ? " is-go" : ""': "fixed text",
    '(it.is_animated ?? it.is_video) ? `<span class="tile-play">${ICONS.play}</span>` : ""': "a constant icon",
    'o.attr': "layoutCard's attribute name, always a constant (template/format)",
}


def _regex_end(src: str, i: int) -> int:
    """If a regex literal starts at ``i`` (a ``/`` after an operator), its end; else -1."""
    k = i - 1
    while k >= 0 and src[k] in " \t\n":
        k -= 1
    if k >= 0 and src[k] not in "(,=:[!&|?{};+" and not src[max(0, k - 5):k + 1].endswith("return"):
        return -1
    j, in_class = i + 1, False
    while j < len(src) and src[j] != "\n":
        if src[j] == "\\":
            j += 2
            continue
        if src[j] == "[":
            in_class = True
        elif src[j] == "]":
            in_class = False
        elif src[j] == "/" and not in_class:
            return j + 1
        j += 1
    return -1


def template_literals(src: str):
    """Yield (static_text, [expressions]) for every template literal."""
    i, n = 0, len(src)
    while i < n:
        c = src[i]
        if c in "\"'":                                # skip ordinary strings
            j = i + 1
            while j < n and src[j] != c:
                j += 2 if src[j] == "\\" else 1
            i = j + 1
            continue
        if src.startswith("//", i):
            i = src.find("\n", i)
            i = n if i < 0 else i
            continue
        if src.startswith("/*", i):
            i = src.find("*/", i) + 2
            continue
        if c == "/":
            end = _regex_end(src, i)
            if end > 0:
                i = end
                continue
        if c == "`":
            i = yield from _literal(src, i)
            continue
        i += 1


def _literal(src: str, start: int):
    """Parse one template literal starting at ``start``; yield it, return the end."""
    i, n = start + 1, len(src)
    text, exprs = [], []
    while i < n and src[i] != "`":
        if src[i] == "\\":
            text.append(src[i:i + 2])
            i += 2
            continue
        if src.startswith("${", i):
            depth, j = 1, i + 2
            while j < n and depth:
                if src[j] == "`":                     # nested literal inside the expression
                    j = yield from _literal(src, j)
                    continue
                if src[j] == "/" and _regex_end(src, j) > 0:
                    j = _regex_end(src, j)
                    continue
                if src[j] in "\"'":
                    q, j = src[j], j + 1
                    while j < n and src[j] != q:
                        j += 2 if src[j] == "\\" else 1
                elif src[j] == "{":
                    depth += 1
                elif src[j] == "}":
                    depth -= 1
                j += 1
            exprs.append(src[i + 2:j - 1].strip())
            text.append("\x00")
            i = j
            continue
        text.append(src[i])
        i += 1
    yield "".join(text), exprs
    return i + 1


def findings() -> list:
    out = []
    for name in FILES:
        path = WEB / name
        if not path.exists():
            continue
        src = path.read_text(encoding="utf-8")
        for text, exprs in template_literals(src):
            if "<" not in text and '="' not in text and "='" not in text:
                continue                              # not markup
            for expr in exprs:
                if SAFE.match(expr) or expr in REVIEWED:
                    continue
                out.append(f"{name}: ${{{expr}}}")
    return out


def main() -> int:
    bad = findings()
    for line in bad:
        print("unescaped in markup:", line)
    if bad:
        print("Wrap the value in esc(…) (or hex/num/iconFor), or add it to REVIEWED with a reason.")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
