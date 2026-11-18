"""The folder on a booth's second upload target, with placeholders (17.11.2026).

One copy in the gallery (``app/zielordner.py``) and one in the sync package
the booth uploads with (``sync/photobox_sync/zielordner.py``), byte for byte
the same: ``tools/sync_editor.py`` copies it, a test in each suite compares.
The gallery uses it to check the folder field and to show an example under
it; the booth checks the field again and builds the folder of every upload
with it, offline.

The rule, as the help text states it:

* Empty: the folder the login starts in (an FTP-fed proofing gallery: usually the gallery's own folder).
* Otherwise a folder on the server, created when it is missing, sub-folders
  included. Forward slashes; a backslash becomes one. A leading ``/`` means
  "from the top of the server"; without it the folder is inside the start
  folder. ``..``, control characters and unknown ``{…}`` are refused.
* Placeholders, filled in per file when it is sent, so one setting serves
  every event: ``{event}`` (the event's short name), ``{event_name}`` (its
  name, made safe for a folder), ``{datum}`` (YYYY-MM-DD of the capture),
  ``{jahr}``, ``{monat}``, ``{tag}``. The names are those of the gallery's
  file-name patterns (``app/dateinamen.py``).
"""
from __future__ import annotations

import datetime as _dt
import re
import unicodedata
from typing import Any, List, Optional, Tuple

#: Placeholder, what it means, an example. Same names as app/dateinamen.py.
PLACEHOLDERS = (
    ("{event}", "Kürzel des Events", "sommerfest-2026"),
    ("{event_name}", "Name des Events", "Sommerfest-2026"),
    ("{datum}", "Aufnahmedatum", "2026-07-20"),
    ("{jahr}", "Jahr", "2026"),
    ("{monat}", "Monat", "07"),
    ("{tag}", "Tag", "20"),
)
NAMES = tuple(p[0] for p in PLACEHOLDERS)
MAX_LEN = 255
#: The two sub-folders of the layout with folders per event and day.
PHOTOS, ORIGINALS = "fotos", "originale"

_CONTROL = re.compile(r"[\x00-\x1f\x7f-\x9f]")
_BRACES = re.compile(r"\{[^{}]*\}")


def check(value: Any) -> str:
    """The folder field, normalised. Raises ValueError with a sentence for the form."""
    if value is None:
        return ""
    if not isinstance(value, str):
        raise ValueError("Ordner: muss Text sein")
    if _CONTROL.search(value):
        raise ValueError("Ordner: Steuerzeichen sind nicht erlaubt")
    raw = value.strip().replace("\\", "/")
    if len(raw) > MAX_LEN:
        raise ValueError(f"Ordner: höchstens {MAX_LEN} Zeichen")
    unknown = [m for m in _BRACES.findall(raw) if m not in NAMES]
    if unknown:
        raise ValueError("Ordner: unbekannter Platzhalter " + ", ".join(unknown)
                         + " (möglich: " + " ".join(NAMES) + ")")
    if "{" in _BRACES.sub("", raw) or "}" in _BRACES.sub("", raw):
        raise ValueError("Ordner: Klammer { oder } ohne Gegenstück")
    parts = [p.strip() for p in raw.split("/")]
    parts = [p for p in parts if p not in ("", ".")]
    if any(p == ".." for p in parts):
        raise ValueError("Ordner: „..“ ist nicht erlaubt")
    out = "/".join(parts)
    # "/" alone is the top of the server, which is not the same as empty (the start folder).
    return ("/" + out) if raw.startswith("/") else out


def safe_part(text: Any, fallback: str = "Event", limit: int = 60) -> str:
    """A name as one folder: umlauts written out, [A-Za-z0-9_-] only."""
    value = str(text or "")
    for char, repl in (("ä", "ae"), ("ö", "oe"), ("ü", "ue"), ("ß", "ss"),
                       ("Ä", "Ae"), ("Ö", "Oe"), ("Ü", "Ue"), ("ẞ", "SS")):
        value = value.replace(char, repl)
    value = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode()
    value = re.sub(r"[^A-Za-z0-9_-]+", "-", value)
    value = re.sub(r"-{2,}", "-", value).strip("-_")[:limit].strip("-_")
    return value or fallback


def _date(when: Any) -> Optional[_dt.date]:
    if isinstance(when, _dt.datetime):
        return when.date()
    if isinstance(when, _dt.date):
        return when
    try:
        return _dt.date.fromisoformat(str(when or "")[:10])
    except ValueError:
        return None


def expand(path: Any, *, slug: Any = "", event_name: Any = "", when: Any = None) -> str:
    """The folder with its placeholders filled in for one file. Checked again afterwards."""
    base = check(path)
    day = _date(when)
    values = {
        "{event}": safe_part(slug, "ohne-event").lower(),
        "{event_name}": safe_part(event_name or slug, "Event"),
        "{datum}": day.isoformat() if day else "undatiert",
        "{jahr}": f"{day.year:04d}" if day else "undatiert",
        "{monat}": f"{day.month:02d}" if day else "undatiert",
        "{tag}": f"{day.day:02d}" if day else "undatiert",
    }
    out = _BRACES.sub(lambda m: values.get(m.group(0), m.group(0)), base)
    return check(out)


def parts(path: Any, flat: bool, *, slug: Any = "", event_name: Any = "", when: Any = None,
          original: bool = False) -> Tuple[bool, List[str]]:
    """(absolute, folders) where one file goes.

    ``flat``: the folder itself. Otherwise the layout with a folder per
    event and day below it: ``<folder>/<event>/<YYYY-MM-DD>/fotos`` (or
    ``originale`` for camera originals and full-size copies).
    """
    base = expand(path, slug=slug, event_name=event_name, when=when)
    absolute = base.startswith("/")
    out = [p for p in base.split("/") if p]
    if not flat:
        day = _date(when)
        out += [safe_part(slug, "ohne-event").lower() if slug else "ohne-event",
                day.isoformat() if day else "undatiert", ORIGINALS if original else PHOTOS]
    return absolute, out


def example(path: Any, flat: bool, *, slug: Any = "sommerfest-2026", event_name: Any = "Sommerfest 2026",
            when: Any = "2026-07-20", file: str = "Sommerfest-2026_2026-07-20_21-14-03.jpg",
            start: str = "(Startordner)") -> str:
    """The path a picture would get, for the line under the form field."""
    absolute, folders = parts(path, flat, slug=slug, event_name=event_name, when=when)
    return ("/" if absolute else start + "/") + "/".join(folders + [file])
