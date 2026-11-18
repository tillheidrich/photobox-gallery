"""One set of rules for what a booth profile may hold.

The profile travels from the gallery's admin form, the MCP tools, an import
and the booth's own admin page into ``profile.json`` on the booth, and from
there into the kiosk page every guest touches. Until the second review
(R2-U1, 15.11.2026) nothing checked the *contents* of lists such as
``modes`` or ``looks``: a value like ``photo" autofocus onfocus="…`` reached
an HTML attribute in the kiosk and ran script on the booth's own origin.

This module is the one place that decides what a value may look like. The
gallery uses it strictly (a change with a bad value is refused with a
sentence naming the field); the booth uses it leniently (a bad value from an
old or tampered ``profile.json`` is dropped, logged once, and the booth
starts with the default). The booth has a copy with the same rules in
``booth/boothcore/profile_rules.py``, kept byte-identical by a test.

The rules, by kind of field:

* enumerations (``modes``, ``languages``, formats, looks, key names, and every
  text whose default is a short identifier such as ``"crop"`` or ``"10x15"``)
  take only letters, digits and ``_ . : -`` (camera values also ``/ +``);
* colours are ``#rgb``/``#rrggbb``/``#rrggbbaa``, a CSS colour word or empty;
* addresses (``webhooks.url``, ``qr.url``, ``qr_url_template``) are empty or
  ``http(s)://`` without spaces, quotes or angle brackets;
* file names have no quotes, angle brackets, slashes or control characters;
* free text is text: no control characters, at most a few thousand
  characters; it is escaped where it is shown;
* numbers are finite, and within ``PROFILE_LIMITS`` where a bound exists;
* switches are ``true``/``false``;
* keys the defaults do not know are dropped (free dictionaries such as
  ``texts`` keep their keys if they are short identifiers).
"""
from __future__ import annotations

import math
import re
from typing import Any, Dict, List, Tuple

#: Bounds for numbers, path -> (min, max). Copy of PROFILE_LIMITS in
#: booth/boothcore/app.py (kept equal by a test).
PROFILE_LIMITS = {
    "countdown.first": (1, 20), "countdown.next": (1, 20), "countdown.review_seconds": (0, 10),
    "countdown.smile_ms": (200, 3000), "review_seconds": (3, 60), "flash.preview_gain": (0, 100),
    "gif.shots": (2, 10), "gif.interval": (0.5, 10), "gif.frame_ms": (80, 3000),
    "gif.video_seconds": (0, 60), "gif.from_collage_min": (2, 8),
    "gif.boomerang.fps": (5, 60), "gif.boomerang.loops": (1, 10),
    "boomerang.seconds": (1, 10), "boomerang.fps": (5, 60), "boomerang.loops": (1, 10),
    "boomerang.min_total_seconds": (5, 60), "boomerang.max_total_seconds": (5, 60),
    "video.seconds": (1, 60), "video.fps": (5, 60),
    "attract.after_seconds": (10, 3600), "attract.seconds_per_item": (2, 120), "attract.dim": (0, 80),
    "attract.hint_size": (100, 250),
    "sounds.volume": (0, 1), "looks_max": (0, 24),
    "aufraeumen.nach_tagen": (1, 365), "aufraeumen.behalten_mindestens": (0, 1000),
    "meldungen.wiederholen_minuten": (0, 1440),
    "print.queue_max": (1, 50), "print.queue_max_hours": (1, 24),
}

#: Lists of numbers, path -> (min, max, most entries). The boomerang lengths
#: a guest may choose: 15 s is one loop of 30 s, the longest film allowed.
NUMBER_LISTS = {"boomerang.seconds_choices": (1, 15, 4)}

MODES = ("photo", "collage", "gif", "boomerang", "video")
#: Lists whose entries must each be one of a fixed set.
ENUM_LISTS = {"modes": MODES, "guest_text.modes": MODES, "draw.modes": MODES}
#: Single values that name a run mode.
#: Where a boomerang or a film comes from: the live view, or a movie the
#: camera records on its card (camera/edsdk.py record_movie).
CLIP_SOURCES = ("live", "camera")
ENUM_VALUES = {"http_trigger.mode": MODES, "boomerang.source": CLIP_SOURCES, "video.source": CLIP_SOURCES,
               "start.text_tone": ("auto", "hell", "dunkel")}

ID_RE = re.compile(r"[A-Za-z0-9_.:-]{1,80}")
#: An identifier-like value; camera settings also use "/" and "+" (1/125, +1/3).
VALUE_RE = re.compile(r"[A-Za-z0-9_.:/+ -]{0,80}")
LANG_RE = re.compile(r"[a-z]{2}(-[A-Z]{2})?")
FORMAT_RE = re.compile(r"\d{1,2}:\d{1,2}")
COLOR_RE = re.compile(r"#(?:[0-9A-Fa-f]{3,4}|[0-9A-Fa-f]{6}|[0-9A-Fa-f]{8})|[A-Za-z]{1,20}|"
                      r"rgba?\(\s*[0-9.%\s,]{1,40}\)")
URL_RE = re.compile(r"https?://[^\s\"'<>\\`]{1,2000}", re.IGNORECASE)
FILE_RE = re.compile(r"[^\x00-\x1f\x7f<>\"'`\\/]{1,200}")
CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")

#: Fields whose value is a colour (last path part, or the whole path).
COLOR_KEYS = {"color", "text_color", "bg_color", "colors"}
COLOR_PATHS = {"accent", "theme.accent", "theme.on_accent", "theme.bg", "theme.panel", "theme.text",
               "theme.muted", "collage_style.background", "collage_style.ink", "collage_style.muted",
               "collage_style.accent", "print_layout.background"}
URL_PATHS = {"webhooks.url", "qr.url", "qr_url_template", "event.base_url"}
#: Lists whose entries are identifiers.
ID_LISTS = {"looks", "luts.show", "webhooks.events", "controls.keys_start", "controls.keys_back",
            "controls.keys_print", "collage_builtins", "boomerang.frame_builtins", "video.frame_builtins",
            "gif.frame_builtins"}
FORMAT_PATHS = {"formats", "default_format", "boomerang.format", "video.format", "gif.format",
                "gif.boomerang.format"}
#: Fields (last path part) that hold the name of an uploaded file.
FILE_KEYS = {"smile_media", "overlay", "print_overlay", "logo", "image", "media", "font_file",
             "print_template", "frame_default"}
#: The same by full path where the last part also names something else
#: (``sounds.shutter`` is a file, ``camera.shutter`` is "1/125").
FILE_PATHS = {"sounds.countdown", "sounds.shutter", "sounds.done", "sounds.print"}
FILE_LISTS = {"attract.media", "start.media", "backgrounds", "chroma.backgrounds", "collage_templates",
              "boomerang.frame_templates", "video.frame_templates", "gif.frame_templates"}
#: Longer free text.
TEXT_LIMITS = {"mail.body": 20000, "privacy.notice": 4000}
TEXT_LIMIT = 2000
LIST_LIMIT = 200
FREE_DEPTH = 4

Problem = Tuple[str, str, str]          # (path, reason, value as shown)


def _show(value: Any) -> str:
    text = repr(value)
    return text if len(text) <= 60 else text[:57] + "…"


def _color_ok(value: str) -> bool:
    return value == "" or bool(COLOR_RE.fullmatch(value))


def _text(path: str, value: Any, problems: List[Problem], limit: int = TEXT_LIMIT):
    """Free text: a string without control characters, length-limited."""
    if isinstance(value, bool) or value is None:
        problems.append((path, "Text erwartet", _show(value)))
        return None
    if isinstance(value, (int, float)):
        value = str(value)
    if not isinstance(value, str):
        problems.append((path, "Text erwartet", _show(value)))
        return None
    if CONTROL_RE.search(value):
        problems.append((path, "Steuerzeichen sind nicht erlaubt", _show(value)))
        return None
    if len(value) > limit:
        problems.append((path, f"höchstens {limit} Zeichen", _show(value)))
        return None
    return value


def _string(path: str, key: str, default: str, value: Any, problems: List[Problem]):
    """A string field, checked by what kind of field it is."""
    text = _text(path, value, problems, TEXT_LIMITS.get(path, TEXT_LIMIT))
    if text is None:
        return None
    if path in ENUM_VALUES:
        if text not in ENUM_VALUES[path]:
            problems.append((path, "nur " + ", ".join(ENUM_VALUES[path]), _show(text)))
            return None
    elif path in FORMAT_PATHS:
        if not FORMAT_RE.fullmatch(text):
            problems.append((path, "ein Format wie 4:5", _show(text)))
            return None
    elif path in URL_PATHS:
        if text and not URL_RE.fullmatch(text.strip()):
            problems.append((path, "nur eine vollständige http(s)-Adresse ohne Leerzeichen", _show(text)))
            return None
        text = text.strip()
    elif path in COLOR_PATHS or key in COLOR_KEYS:
        if not _color_ok(text.strip()):
            problems.append((path, "eine Farbe wie #ff8800", _show(text)))
            return None
        text = text.strip()
    elif (key in FILE_KEYS or path in FILE_PATHS) and default == "":
        if text and (not FILE_RE.fullmatch(text) or text.startswith("..")):
            problems.append((path, "ein Dateiname ohne Anführungszeichen, spitze Klammern und Schrägstriche",
                             _show(text)))
            return None
    elif default and VALUE_RE.fullmatch(default):
        # The default is an identifier ("crop", "10x15", "bottom-right"): so
        # is every value the booth understands here.
        if not VALUE_RE.fullmatch(text):
            problems.append((path, "nur Buchstaben, Ziffern und _ . : - /", _show(text)))
            return None
    return text


def _number(path: str, default: Any, value: Any, problems: List[Problem], strict: bool):
    if isinstance(value, bool) or value is None:
        problems.append((path, "Zahl erwartet", _show(value)))
        return None
    if isinstance(value, str):
        try:
            value = float(value.strip().replace(",", "."))
        except ValueError:
            problems.append((path, "Zahl erwartet", _show(value)))
            return None
    if not isinstance(value, (int, float)) or not math.isfinite(value) or abs(value) > 1e7:
        problems.append((path, "Zahl erwartet", _show(value)))
        return None
    bounds = PROFILE_LIMITS.get(path)
    if bounds and not (bounds[0] <= value <= bounds[1]):
        if strict:
            problems.append((path, f"zwischen {bounds[0]} und {bounds[1]}", _show(value)))
            return None
        value = min(bounds[1], max(bounds[0], value))
    if isinstance(default, int):
        return int(round(value))
    return float(value)


def _number_entry(path: str, item: Any, problems: List[Problem]):
    """One entry of a list of numbers (NUMBER_LISTS); None means "drop it", in- or outside the bounds."""
    low, high, _most = NUMBER_LISTS[path]
    value = item
    if isinstance(value, str):
        try:
            value = float(value.strip().replace(",", "."))
        except ValueError:
            value = None
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        problems.append((path, "Zahl erwartet", _show(item)))
        return None
    if not low <= value <= high:
        problems.append((path, f"jede zwischen {low} und {high}", _show(item)))
        return None
    return int(value) if float(value).is_integer() else float(value)


def _list_entry(path: str, item: Any, problems: List[Problem]):
    """One entry of a list field; None means "drop it"."""
    if path in NUMBER_LISTS:
        return _number_entry(path, item, problems)
    if path in ENUM_LISTS:
        if item not in ENUM_LISTS[path]:
            problems.append((path, "nur " + ", ".join(ENUM_LISTS[path]), _show(item)))
            return None
        return item
    if path == "languages":
        if not (isinstance(item, str) and LANG_RE.fullmatch(item)):
            problems.append((path, "Sprachkürzel wie de oder en", _show(item)))
            return None
        return item
    if path in FORMAT_PATHS:
        if not (isinstance(item, str) and FORMAT_RE.fullmatch(item)):
            problems.append((path, "ein Format wie 4:5", _show(item)))
            return None
        return item
    if path in ID_LISTS:
        if not (isinstance(item, str) and ID_RE.fullmatch(item)):
            problems.append((path, "nur Buchstaben, Ziffern und _ . : -", _show(item)))
            return None
        return item
    if path.rsplit(".", 1)[-1] in COLOR_KEYS:
        if not (isinstance(item, str) and item and _color_ok(item)):
            problems.append((path, "eine Farbe wie #ff8800", _show(item)))
            return None
        return item
    if path in FILE_LISTS:
        if isinstance(item, str) and item.startswith("#") and _color_ok(item):
            return item                              # chroma: a plain colour as a scene
        if not (isinstance(item, str) and FILE_RE.fullmatch(item) and not item.startswith("..")):
            problems.append((path, "ein Dateiname ohne Anführungszeichen, spitze Klammern und Schrägstriche",
                             _show(item)))
            return None
        return item
    return _free(path, item, problems, 1)


def _free(path: str, value: Any, problems: List[Problem], depth: int):
    """A value without a fixed shape (texts, positions, frame texts)."""
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value if math.isfinite(value) and abs(value) <= 1e7 else None
    if isinstance(value, str):
        key = path.rsplit(".", 1)[-1]
        if key in COLOR_KEYS:
            return _string(path, key, "", value, problems)
        return _text(path, value, problems, TEXT_LIMITS.get(path, TEXT_LIMIT))
    if depth > FREE_DEPTH:
        problems.append((path, "zu tief verschachtelt", _show(value)))
        return None
    if isinstance(value, list):
        out = []
        for item in value[:LIST_LIMIT]:
            clean = _free(path, item, problems, depth + 1)
            if clean is not None:
                out.append(clean)
        if len(value) > LIST_LIMIT:
            problems.append((path, f"höchstens {LIST_LIMIT} Einträge", f"{len(value)} Einträge"))
        return out
    if isinstance(value, dict):
        out = {}
        for k, v in list(value.items())[:LIST_LIMIT]:
            if not (isinstance(k, str) and ID_RE.fullmatch(k)):
                problems.append((path, "Schlüssel nur aus Buchstaben, Ziffern und _ . : -", _show(k)))
                continue
            clean = _free(f"{path}.{k}", v, problems, depth + 1)
            if clean is not None or v is None:
                out[k] = clean
        return out
    problems.append((path, "unbekannter Wert", _show(value)))
    return None


def _walk(defaults: Dict[str, Any], incoming: Any, prefix: str, problems: List[Problem],
          strict: bool) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    if not isinstance(incoming, dict):
        return out
    for key, value in incoming.items():
        if key not in defaults:
            continue                                 # unknown keys are dropped
        path = f"{prefix}.{key}" if prefix else key
        default = defaults[key]
        if isinstance(default, dict) and default:
            if not isinstance(value, dict):
                problems.append((path, "Abschnitt erwartet", _show(value)))
                continue
            out[key] = _walk(default, value, path, problems, strict)
        elif isinstance(default, dict):
            if not isinstance(value, dict):
                problems.append((path, "Abschnitt erwartet", _show(value)))
                continue
            out[key] = _free(path, value, problems, 1)
        elif isinstance(default, bool):
            if isinstance(value, bool):
                out[key] = value
            elif strict:
                problems.append((path, "ja oder nein (true/false)", _show(value)))
            else:
                out[key] = value not in (0, "0", "", "false", "False", "off", None)
        elif isinstance(default, (int, float)):
            clean = _number(path, default, value, problems, strict)
            if clean is not None:
                out[key] = clean
        elif isinstance(default, str):
            if value is None:
                continue
            clean = _string(path, key, default, value, problems)
            if clean is not None:
                out[key] = clean
        elif isinstance(default, list):
            if not isinstance(value, list):
                problems.append((path, "Liste erwartet", _show(value)))
                continue
            items = []
            for item in value[:LIST_LIMIT]:
                clean = _list_entry(path, item, problems)
                if clean is not None:
                    items.append(clean)
            if len(value) > LIST_LIMIT:
                problems.append((path, f"höchstens {LIST_LIMIT} Einträge", f"{len(value)} Einträge"))
            most = NUMBER_LISTS.get(path, (0, 0, LIST_LIMIT))[2]
            if len(items) > most:
                problems.append((path, f"höchstens {most} Einträge", f"{len(items)} Einträge"))
                items = items[:most]
            out[key] = items
        else:                                        # None default: shape not fixed
            clean = _free(path, value, problems, 1)
            if clean is not None or value is None:
                out[key] = clean
    return out


def check(profile: Any, defaults: Dict[str, Any], strict: bool = True) -> Tuple[Dict[str, Any], List[Problem]]:
    """Return the profile with only valid values, and what was wrong.

    ``strict``: out-of-range numbers and non-boolean switches are problems
    (gallery). Not strict: they are clamped or read as a switch (booth).
    Invalid values are never in the returned profile; a caller that refuses
    on problems (the gallery) shows them, one that cannot refuse (the booth
    reading ``profile.json``) logs them and goes on with the defaults.
    """
    problems: List[Problem] = []
    clean = _walk(defaults, profile, "", problems, strict)
    return clean, problems


def describe(problems: List[Problem], limit: int = 6) -> str:
    """The problems as one German sentence for an error message."""
    parts = [f"{path}: {reason} (war {value})" for path, reason, value in problems[:limit]]
    if len(problems) > limit:
        parts.append(f"und {len(problems) - limit} weitere")
    return "Profil nicht übernommen — " + "; ".join(parts)
