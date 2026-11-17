"""Layout approval by the customer: a personal link, a few choices, the owner's yes.

The booth owner offers some of his own print templates to his customer (a
wedding couple, a company). The customer opens ``/layout/<token>``, picks one,
fills in the fields the owner allows, chooses one of the allowed colour sets
and fonts, may upload a logo, sees the server's render and submits. The
owner approves or sends it back with a note. Approval turns the choice into
a plain template through :func:`app.vorlagen.speichern` and assigns it to the
event; the booth gets it with its next profile sync like any other template.

How a template declares customer fields — and why nothing changes at the booth
-------------------------------------------------------------------------------
A text of a template may contain ``{feld_<key>}`` (``{feld_namen}``,
``{feld_datum}``, ``{feld_spruch}``). That is the placeholder style the
renderer already has (``{event_name}``, ``{date}``): ``pruefe()`` keeps it as
plain text, and a booth that prints the template unresolved reads an unknown
key as "" (``_SafeDict``), so nothing breaks. Labels, maximum lengths and
defaults are chosen per invitation by the owner, not stored in the template:
``pruefe()`` and the byte-identical ``vorlagen_kern.py`` at the booth stay
untouched. A colon form (``{feld:namen}``) was not taken: ``format_map``
would read "namen" as a format spec and raise at the booth.

At approval the placeholders are replaced by the customer's text in a copy of
the template. The customer's text passes the renderer's ``format_map`` later
on, so braces are doubled first: ``{event_name}`` or ``{0.__class__}`` typed
by a customer prints as typed. A text that would exceed the 120 characters
``pruefe()`` keeps is refused, never cut — a cut between two doubled braces
would leave a lone brace that the booth cannot format.

Colours are one of the renderer's style presets (``STYLE_PRESETS``): the
tokens ``@hintergrund``, ``@schrift``, ``@gedaempft``, ``@akzent`` in texts and
shapes become that preset's hex values, and a full-sheet rectangle in the
background colour goes to the bottom of the layer order (``pruefe()`` always
writes ``"background": "@hintergrund"``, so the sheet colour cannot be set
otherwise). What stays with the booth's style: rounded photo corners
(``@rund``), the style's frame line and the margin colour of a photo that is
fitted rather than cropped.

A layer named "Logo" (a shape or a picture) is the logo spot: with a logo it
becomes a picture of the same box; without one a shape spot is dropped and a
picture spot keeps the owner's picture.
"""
from __future__ import annotations

import hashlib
import hmac
import io
import json
import re
import secrets
import shutil
import tempfile
import threading
import unicodedata
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from app import db

TABLE = "layout_invitations"
TOKEN_BYTES = 24                          # 192 bit, like the booth status page
_TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{32}$")

STATES = ("offen", "entwurf", "eingereicht", "freigegeben", "zurueck")
EDITABLE = ("offen", "entwurf", "zurueck")

#: Misses per address: requests whose token opens nothing. A valid link is
#: never counted (behind a proxy all visitors may share one address).
RATE_LIMIT = 60
RATE_WINDOW = 900
#: Renders per invitation (tiles and previews) in the window; one at a time.
PREVIEW_LIMIT = 240
PREVIEW_WINDOW = 600
RENDER_TIMEOUT = 20.0
#: How long a render waits for the one before it (the page asks for the tiles
#: and the first preview at once); after that it answers 429.
LOCK_WAIT = 8.0
PREVIEW_EDGE = 900
TILE_EDGE = 480

DEFAULT_DAYS = 30
MAX_DAYS = 365
MAX_FIELDS = 6
FIELD_MAX = 80
TEXT_MAX = 120                            # what pruefe() keeps of a text
LOGO_MAX_BYTES = 5 * 1024 * 1024
LOGO_MAX_PIXELS = 25_000_000
LOGO_EDGE = 1600
LOGO_FORMATS = {"PNG", "JPEG", "WEBP"}

DEFAULT_FIELDS = [
    {"key": "namen", "label": "Namen", "max": 40, "default": ""},
    {"key": "datum", "label": "Datum", "max": 20, "default": ""},
    {"key": "spruch", "label": "Spruch", "max": 60, "default": ""},
]

#: The colour sets on offer: the renderer's own presets, named for people.
PALETTE_LABELS = {"hochzeit": "Hochzeit (Creme und Gold)", "hell": "Hell", "clean": "Dunkel",
                  "feier": "Feier (Nacht und Pink)", "geburtstag": "Geburtstag (Nacht und Gelb)",
                  "firma": "Firma (Weiß und Blau)"}
FONT_LABELS = {"serif": "Klassisch (Serif)", "system": "Klar (Inter)", "display": "Kräftig (Archivo)"}

PLACEHOLDER = re.compile(r"\{feld_([a-z0-9_]{1,20})\}")
_KEY = re.compile(r"^[a-z0-9_]{1,20}$")
_TOKEN_COLOURS = {"@hintergrund": "background", "@schrift": "ink", "@gedaempft": "muted", "@akzent": "accent"}
_BIDI = {chr(c) for c in list(range(0x202A, 0x202F)) + list(range(0x2066, 0x206A)) + [0x200E, 0x200F]}

_LOCKS: Dict[int, threading.Lock] = {}
_LOCKS_GUARD = threading.Lock()


class StateError(Exception):
    """The invitation is not in a state that allows this step."""


# ── Schema ────────────────────────────────────────────────────

def init_schema(cur) -> None:
    cur.execute(f"""
        CREATE TABLE IF NOT EXISTS {TABLE} (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_id INTEGER NOT NULL UNIQUE REFERENCES events(id) ON DELETE CASCADE,
            token_hash TEXT NOT NULL UNIQUE,
            status TEXT NOT NULL DEFAULT 'offen',
            templates_json TEXT NOT NULL DEFAULT '[]',
            fields_json TEXT NOT NULL DEFAULT '[]',
            palettes_json TEXT NOT NULL DEFAULT '[]',
            fonts_json TEXT NOT NULL DEFAULT '[]',
            logo_allowed INTEGER NOT NULL DEFAULT 0,
            values_json TEXT NOT NULL DEFAULT '{{}}',
            logo_file TEXT NOT NULL DEFAULT '',
            note TEXT NOT NULL DEFAULT '',
            result_file TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at TEXT NOT NULL DEFAULT (datetime('now')),
            submitted_at TEXT,
            approved_at TEXT,
            expires_at TEXT NOT NULL,
            revoked_at TEXT,
            approved_by TEXT NOT NULL DEFAULT ''
        );""")


def rate_key(addr: Optional[str]) -> str:
    return f"layoutlink:{addr or '-'}"


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _ready() -> None:
    from app import ecosystem
    ecosystem.init_schema()


def _utc() -> datetime:
    """Naive UTC, the way SQLite's datetime('now') writes it."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _now() -> str:
    return _utc().strftime("%Y-%m-%d %H:%M:%S")


def _assets_dir() -> Path:
    from app.blueprints.admin_booth import _assets_dir as booth_assets
    return booth_assets()


def private_dir(inv_id: int) -> Path:
    """Where a customer's logo waits for approval: outside every served folder."""
    from app import media
    return media.data_dir() / "layout_freigabe" / str(int(inv_id))


def switched_on(event: Optional[Dict[str, Any]]) -> bool:
    from app import gallery_settings as gsm
    return bool(event) and bool(gsm.for_event(event).get("layout_approval"))


# ── What the owner may offer ──────────────────────────────────

def fields_in(vorlage: Dict[str, Any]) -> List[str]:
    """The ``{feld_…}`` keys a template's texts use, in order of appearance."""
    seen: List[str] = []
    for t in vorlage.get("texts") or []:
        for key in PLACEHOLDER.findall(str((t or {}).get("value") or "")):
            if key not in seen:
                seen.append(key)
    return seen


def own_templates() -> List[Dict[str, Any]]:
    """Own templates the owner can offer, with the fields each one declares."""
    from app import vorlagen
    aus = []
    ergebnisse = approved_files()
    for e in vorlagen.eigene(_assets_dir()):
        if e["asset"]["file"] in ergebnisse:
            continue                      # an approved customer layout is not offered again
        aus.append({"file": e["asset"]["file"], "name": str(e["template"].get("name") or e["asset"]["file"]),
                    "fields": fields_in(e["template"]), "logo_spot": _logo_spot(e["template"]) is not None,
                    "template": e["template"]})
    return aus


def approved_files() -> set:
    """Template files that are the result of an approval."""
    _ready()
    con = db.get_db()
    try:
        return {r[0] for r in con.execute(f"SELECT result_file FROM {TABLE} WHERE result_file <> ''")}
    finally:
        con.close()


def font_choices() -> Dict[str, str]:
    """Built-in families plus uploaded TTF/OTF fonts among the booth media."""
    from app import ecosystem, vorlagen_kern
    aus = dict(FONT_LABELS)
    for a in ecosystem.list_assets("font"):
        if vorlagen_kern.datei_name(a["file"], vorlagen_kern.SCHRIFT_ENDUNGEN):
            aus[a["file"]] = str(a.get("name") or a["file"])
    return aus


def _clean_fields(raw: Any) -> List[Dict[str, Any]]:
    aus, keys = [], set()
    for f in (raw if isinstance(raw, list) else [])[:MAX_FIELDS]:
        if not isinstance(f, dict):
            continue
        key = str(f.get("key") or "").strip().lower()
        if not _KEY.match(key) or key in keys:
            continue
        try:
            laenge = max(1, min(FIELD_MAX, int(f.get("max") or 40)))
        except (TypeError, ValueError):
            laenge = 40
        label = clean_text(f.get("label"), 40) or key
        aus.append({"key": key, "label": label, "max": laenge, "default": clean_text(f.get("default"), laenge)})
        keys.add(key)
    return aus


def clean_offer(offer: Dict[str, Any]) -> Dict[str, Any]:
    from app import vorlagen_render
    eigene = {t["file"] for t in own_templates()}
    templates = []
    for name in offer.get("templates") or []:
        name = str(name)
        if name in eigene and name not in templates:
            templates.append(name)
    if not templates:
        raise ValueError("Mindestens eine eigene Vorlage anbieten.")
    fonts_ok = font_choices()
    return {
        "templates": templates[:12],
        "fields": _clean_fields(offer.get("fields")),
        "palettes": [p for p in dict.fromkeys(str(x) for x in offer.get("palettes") or [])
                     if p in vorlagen_render.STYLE_PRESETS],
        "fonts": [f for f in dict.fromkeys(str(x) for x in offer.get("fonts") or []) if f in fonts_ok],
        "logo": bool(offer.get("logo")),
    }


# ── Invitations ───────────────────────────────────────────────

def _row(row) -> Optional[Dict[str, Any]]:
    if not row:
        return None
    inv = dict(row)
    for col, key in (("templates_json", "templates"), ("fields_json", "fields"),
                     ("palettes_json", "palettes"), ("fonts_json", "fonts")):
        try:
            inv[key] = json.loads(inv.get(col) or "[]")
        except ValueError:
            inv[key] = []
    try:
        inv["values"] = json.loads(inv.get("values_json") or "{}")
    except ValueError:
        inv["values"] = {}
    inv["logo"] = bool(inv.get("logo_allowed"))
    return inv


def create(event_id: int, offer: Dict[str, Any], *, days: int = DEFAULT_DAYS, by: str = "") -> str:
    """A new link for this event; an older one of the same event stops working.

    Returns the token. Only its SHA-256 is stored; the caller shows it once.
    """
    _ready()
    if not db.get_event_by_id(int(event_id)):
        raise ValueError("Unbekanntes Event")
    sauber = clean_offer(offer)
    try:
        tage = max(1, min(MAX_DAYS, int(days)))
    except (TypeError, ValueError):
        tage = DEFAULT_DAYS
    token = secrets.token_urlsafe(TOKEN_BYTES)
    alt = for_event(event_id)
    if alt:
        shutil.rmtree(private_dir(alt["id"]), ignore_errors=True)
    con = db.get_db()
    try:
        con.execute(f"DELETE FROM {TABLE} WHERE event_id=?", (int(event_id),))
        con.execute(
            f"""INSERT INTO {TABLE}(event_id, token_hash, status, templates_json, fields_json, palettes_json,
                    fonts_json, logo_allowed, expires_at) VALUES (?,?,?,?,?,?,?,?,?)""",
            (int(event_id), _hash(token), "offen", json.dumps(sauber["templates"]),
             json.dumps(sauber["fields"], ensure_ascii=False), json.dumps(sauber["palettes"]),
             json.dumps(sauber["fonts"]), 1 if sauber["logo"] else 0,
             (_utc() + timedelta(days=tage)).strftime("%Y-%m-%d %H:%M:%S")))
        con.commit()
    finally:
        con.close()
    return token


def by_token(token: str) -> Optional[Dict[str, Any]]:
    """The invitation behind a token — whatever its state; see :func:`live`."""
    if not _TOKEN_RE.match(token or ""):
        return None
    _ready()
    gesucht = _hash(token)
    con = db.get_db()
    try:
        row = con.execute(f"SELECT * FROM {TABLE} WHERE token_hash=?", (gesucht,)).fetchone()
    finally:
        con.close()
    # The lookup goes through the index; the comparison afterwards is the
    # constant-time one, so a match never depends on how much of it agreed.
    if not row or not hmac.compare_digest(str(row["token_hash"]), gesucht):
        return None
    return _row(row)


def for_event(event_id: int) -> Optional[Dict[str, Any]]:
    _ready()
    con = db.get_db()
    try:
        row = con.execute(f"SELECT * FROM {TABLE} WHERE event_id=?", (int(event_id),)).fetchone()
    finally:
        con.close()
    return _row(row)


def expired(inv: Dict[str, Any]) -> bool:
    try:
        return _utc() >= datetime.fromisoformat(str(inv.get("expires_at")))
    except ValueError:
        return True


def live(inv: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """The event of an invitation the customer may still open, else None."""
    if not inv or inv.get("revoked_at") or expired(inv):
        return None
    event = db.get_event_by_id(int(inv["event_id"]))
    return event if switched_on(event) else None


def _update(inv_id: int, **cols) -> None:
    cols["updated_at"] = _now()
    sets = ", ".join(f"{k}=?" for k in cols)
    con = db.get_db()
    try:
        con.execute(f"UPDATE {TABLE} SET {sets} WHERE id=?", (*cols.values(), int(inv_id)))
        con.commit()
    finally:
        con.close()


def revoke(event_id: int) -> bool:
    inv = for_event(event_id)
    if not inv or inv.get("revoked_at"):
        return False
    _update(inv["id"], revoked_at=_now())
    return True


# ── The customer's values ─────────────────────────────────────

def clean_text(value: Any, limit: int) -> str:
    """One line of plain text: no control or direction characters, at most ``limit``."""
    teile = []
    for ch in str(value or ""):
        if ch in "\t\n\r":
            teile.append(" ")             # a line break is a space: every field is one line
        elif unicodedata.category(ch) in ("Cc", "Cs") or ch in _BIDI:
            continue                      # control and direction characters never reach a print
        else:
            teile.append(ch)
    text = re.sub(r"\s+", " ", "".join(teile)).strip()
    return text[:max(0, int(limit))].strip()


def clean_values(inv: Dict[str, Any], raw: Any) -> Dict[str, Any]:
    """What the customer sent, reduced to what this invitation allows.

    The template must be one this invitation offers — the only identifier
    the page sends, and checked against this row alone (no ids from the
    client are used for anything else).
    """
    raw = raw if isinstance(raw, dict) else {}
    template = str(raw.get("template") or "")
    if template not in inv["templates"]:
        raise ValueError("Diese Vorlage steht nicht zur Wahl.")
    felder_roh = raw.get("fields") if isinstance(raw.get("fields"), dict) else {}
    felder = {f["key"]: clean_text(felder_roh.get(f["key"]), f["max"]) for f in inv["fields"]
              if f["key"] in felder_roh}
    palette = str(raw.get("palette") or "")
    font = str(raw.get("font") or "")
    return {"template": template, "fields": felder,
            "palette": palette if palette in inv["palettes"] else "",
            "font": font if font in inv["fonts"] else "",
            "logo": bool(raw.get("logo")) and inv["logo"] and bool(logo_path(inv))}


def _escape(text: str) -> str:
    return text.replace("{", "{{").replace("}", "}}")


def _logo_spot(vorlage: Dict[str, Any]):
    for art in ("images", "shapes"):
        for e in vorlage.get(art) or []:
            if str(e.get("name") or "").strip().lower() == "logo" or str(e.get("id") or "").startswith("logo"):
                return art, e
    return None


def template_content(file: str) -> Optional[Dict[str, Any]]:
    from app import vorlagen
    return vorlagen.laden(file, _assets_dir())


def concrete(inv: Dict[str, Any], values: Dict[str, Any], event: Dict[str, Any], *,
             logo_name: str = "") -> Dict[str, Any]:
    """The customer's choice as a plain template that passes ``pruefe()`` unchanged.

    ``values`` is cleaned again here; ``logo_name`` is the file name the logo
    has (or will have) among the booth media.
    """
    import copy
    from app import vorlagen, vorlagen_kern, vorlagen_render

    werte = clean_values(inv, values)
    quelle = template_content(werte["template"])
    if not quelle:
        raise ValueError("Die Vorlage gibt es nicht mehr.")
    vorlage = copy.deepcopy(quelle)
    standard = {f["key"]: f["default"] for f in inv["fields"]}

    def einsetzen(treffer):
        key = treffer.group(1)
        return _escape(werte["fields"].get(key) or standard.get(key) or "")

    for t in vorlage.get("texts") or []:
        neu = PLACEHOLDER.sub(einsetzen, str(t.get("value") or ""))
        if len(neu.strip()) > TEXT_MAX:
            raise ValueError("Ein Text ist für dieses Layout zu lang. Bitte kürzer fassen.")
        t["value"] = neu
        if werte["font"]:
            t["font"] = werte["font"]

    farben = vorlagen_render.STYLE_PRESETS.get(werte["palette"]) if werte["palette"] else None
    if farben:
        def farbe(wert):
            key = _TOKEN_COLOURS.get(str(wert or ""))
            return farben[key] if key else wert
        for t in vorlage.get("texts") or []:
            t["color"] = farbe(t.get("color") or "@schrift")
        for s in vorlage.get("shapes") or []:
            s["fill"], s["stroke"] = farbe(s.get("fill")), farbe(s.get("stroke"))
        formen = vorlage.setdefault("shapes", [])
        if len(formen) < vorlagen_kern.MAX_FORMEN:
            breite, hoehe = vorlage["canvas"]
            folge = [e[1] for e in vorlagen_kern.ebenen(vorlage)]
            formen.insert(0, {"kind": "rect", "x": 0, "y": 0, "w": breite, "h": hoehe,
                              "fill": farben["background"], "stroke": "", "stroke_width": 0, "opacity": 100,
                              "radius": 0, "id": "kunde-grund", "name": "Hintergrund"})
            vorlage["order"] = ["kunde-grund"] + folge

    spot = _logo_spot(vorlage)
    if spot:
        art, element = spot
        if werte["logo"] and logo_name:
            bild = {"file": logo_name, "x": element["x"], "y": element["y"], "w": element["w"],
                    "h": element["h"], "opacity": element.get("opacity", 100), "id": element.get("id"),
                    "name": element.get("name") or "Logo"}
            if element.get("rotation"):
                bild["rotation"] = element["rotation"]
            vorlage[art].remove(element)
            vorlage.setdefault("images", []).append(bild)
        elif art == "shapes":
            vorlage["shapes"].remove(element)
            vorlage["order"] = [x for x in vorlage.get("order") or [] if x != element.get("id")]
    for leer in ("shapes", "images"):
        if leer in vorlage and not vorlage[leer]:
            vorlage.pop(leer)

    titel = str(event.get("name") or "").strip()
    vorlage["name"] = f"{quelle.get('name') or 'Layout'} – {titel}"[:60]
    vorlage["id"] = vorlagen_kern.slug(f"kunde-{inv['id']}-{event.get('slug') or titel}")
    vorlage.pop("updated_at", None)
    # Through the same gate as every other way in; the copy is built from a
    # checked template and one-line values, so a refusal here is a bug, said.
    sauber, _hinweise = vorlagen.annehmen(vorlage)
    return sauber


# ── State machine ─────────────────────────────────────────────

def save_draft(inv: Dict[str, Any], raw: Any) -> Dict[str, Any]:
    if inv["status"] not in EDITABLE:
        raise StateError(inv["status"])
    werte = clean_values(inv, raw)
    _update(inv["id"], values_json=json.dumps(werte, ensure_ascii=False), status="entwurf")
    return werte


def submit(inv: Dict[str, Any], raw: Any, event: Dict[str, Any]) -> Dict[str, Any]:
    if inv["status"] not in EDITABLE:
        raise StateError(inv["status"])
    werte = clean_values(inv, raw)
    concrete(inv, werte, event, logo_name="logo-pruefung.png")      # refuses what would not print
    _update(inv["id"], values_json=json.dumps(werte, ensure_ascii=False), status="eingereicht",
            submitted_at=_now())
    return werte


def send_back(event_id: int, note: str) -> Dict[str, Any]:
    inv = for_event(event_id)
    if not inv or inv["status"] != "eingereicht":
        raise StateError(inv["status"] if inv else "")
    _update(inv["id"], status="zurueck", note=clean_text(note, 500))
    return for_event(event_id)


def approve(event_id: int, by: str) -> Dict[str, Any]:
    """Make the concrete template, assign it to the event, tell the booths."""
    from app import branding, ecosystem, vorlagen
    inv = for_event(event_id)
    if not inv or inv["status"] != "eingereicht":
        raise StateError(inv["status"] if inv else "")
    event = db.get_event_by_id(int(event_id))
    logo_name = ""
    if inv["values"].get("logo") and logo_path(inv):
        logo_name = f"kunde-{inv['id']}-logo.png"
    vorlage = concrete(inv, inv["values"], event, logo_name=logo_name)
    if logo_name:
        daten = logo_path(inv).read_bytes()
        (_assets_dir() / logo_name).write_bytes(daten)
        vorhanden = next((a for a in ecosystem.list_assets("overlay") if a["file"] == logo_name), None)
        if vorhanden:
            ecosystem.delete_asset(int(vorhanden["id"]))
        ecosystem.add_asset("overlay", f"Logo {event.get('name') or ''}"[:60], logo_name,
                            hashlib.sha256(daten).hexdigest(), len(daten))
    eintrag = vorlagen.speichern(vorlage, _assets_dir())
    design = branding.parse_design(event.get("design_json"))
    boxsachen = design.get("booth") if isinstance(design.get("booth"), dict) else {}
    boxsachen["collage_templates"] = [eintrag["file"]]
    if werte_palette := (inv["values"].get("palette") or ""):
        boxsachen["collage_style"] = ecosystem.clean_collage_style({"preset": werte_palette})
    design["booth"] = boxsachen
    db.update_event(int(event_id), design_json=branding.dump_design(design))
    _bump_event_booths(int(event_id))
    _update(inv["id"], status="freigegeben", approved_at=_now(), approved_by=str(by or "")[:120],
            result_file=eintrag["file"])
    return for_event(event_id)


def unassign(event_id: int) -> bool:
    """Take the approved layout off the event again (the template itself stays)."""
    from app import branding
    event = db.get_event_by_id(int(event_id))
    design = branding.parse_design((event or {}).get("design_json"))
    boxsachen = design.get("booth") if isinstance(design.get("booth"), dict) else {}
    if not boxsachen.pop("collage_templates", None):
        return False
    if boxsachen:
        design["booth"] = boxsachen
    else:
        design.pop("booth", None)
    db.update_event(int(event_id), design_json=branding.dump_design(design))
    _bump_event_booths(int(event_id))
    return True


def _bump_event_booths(event_id: int) -> None:
    from app import ecosystem
    for d in db.list_devices() or []:
        if int(d.get("event_id") or 0) == event_id:
            ecosystem.bump_profile(d["id"])


# ── Logo ──────────────────────────────────────────────────────

def logo_path(inv: Dict[str, Any]) -> Optional[Path]:
    name = str(inv.get("logo_file") or "")
    if name != "logo.png":
        return None
    p = private_dir(inv["id"]) / name
    return p if p.is_file() else None


def save_logo(inv: Dict[str, Any], data: bytes) -> None:
    """PNG, JPEG or WebP by their first bytes, re-encoded as PNG without metadata.

    Raises ValueError for anything else. SVG and GIF never pass: SVG is
    script, and nothing here needs animation.
    """
    from PIL import Image, ImageOps

    if len(data) > LOGO_MAX_BYTES:
        raise ValueError("Die Datei ist größer als 5 MB.")
    kopf = data[:16]
    if not (kopf.startswith(b"\x89PNG\r\n\x1a\n") or kopf.startswith(b"\xff\xd8\xff")
            or (kopf[:4] == b"RIFF" and kopf[8:12] == b"WEBP")):
        raise ValueError("Bitte ein PNG, JPEG oder WebP hochladen.")
    try:
        with Image.open(io.BytesIO(data)) as probe:
            if probe.format not in LOGO_FORMATS:
                raise ValueError("Bitte ein PNG, JPEG oder WebP hochladen.")
            if probe.width * probe.height > LOGO_MAX_PIXELS:
                raise ValueError("Das Bild hat zu viele Pixel.")
            probe.verify()
        with Image.open(io.BytesIO(data)) as roh:
            roh.load()
            bild = ImageOps.exif_transpose(roh)
            bild = bild.convert("RGBA")
    except ValueError:
        raise
    except Exception as exc:              # noqa: BLE001 — anything Pillow cannot read is refused
        raise ValueError("Die Datei ließ sich nicht als Bild lesen.") from exc
    bild.thumbnail((LOGO_EDGE, LOGO_EDGE))
    # Pixels only: a fresh image carries no text chunks, no EXIF, no ICC profile.
    sauber = Image.frombytes("RGBA", bild.size, bild.tobytes())
    ziel = private_dir(inv["id"])
    ziel.mkdir(parents=True, exist_ok=True)
    tmp = ziel / "logo.part"
    sauber.save(tmp, "PNG", optimize=True)
    tmp.replace(ziel / "logo.png")
    _update(inv["id"], logo_file="logo.png")


def remove_logo(inv: Dict[str, Any]) -> None:
    shutil.rmtree(private_dir(inv["id"]), ignore_errors=True)
    werte = dict(inv.get("values") or {})
    werte["logo"] = False
    _update(inv["id"], logo_file="", values_json=json.dumps(werte, ensure_ascii=False))


# ── Rendering ─────────────────────────────────────────────────

def event_texts(event: Dict[str, Any]) -> Dict[str, str]:
    """What the booth will put into {event_name} and {date}: the event's own."""
    datum = str(event.get("date") or "")[:10]
    try:
        datum = datetime.fromisoformat(datum).strftime("%d.%m.%Y")
    except ValueError:
        datum = datetime.now().strftime("%d.%m.%Y")
    return {"event_name": str(event.get("name") or ""), "date": datum}


def _render_lock(inv_id: int) -> threading.Lock:
    with _LOCKS_GUARD:
        return _LOCKS.setdefault(int(inv_id), threading.Lock())


class Busy(Exception):
    """A render for this invitation is running, or it took too long."""


def bounded(inv_id: int, work: Callable[[], bytes]) -> bytes:
    """One render per invitation at a time, and never longer than RENDER_TIMEOUT.

    Per process: with several workers each has its own lock, which still
    caps a customer at one render per worker.
    """
    lock = _render_lock(inv_id)
    erhalten = lock.acquire(timeout=LOCK_WAIT) if LOCK_WAIT > 0 else lock.acquire(blocking=False)
    if not erhalten:
        raise Busy()
    ergebnis: Dict[str, Any] = {}

    def lauf():
        try:
            ergebnis["daten"] = work()
        except Exception as exc:          # noqa: BLE001 — handed to the caller below
            ergebnis["fehler"] = exc
        finally:
            lock.release()

    faden = threading.Thread(target=lauf, daemon=True)
    faden.start()
    faden.join(RENDER_TIMEOUT)
    if faden.is_alive():
        raise Busy()                      # the thread keeps the lock until it ends
    if "fehler" in ergebnis:
        raise ergebnis["fehler"]
    return ergebnis["daten"]


def style_for(palette: str, event: Dict[str, Any]) -> Dict[str, Any]:
    """The booth style a render uses: the chosen colour set, else the event's own style.

    Approval writes the chosen set as the event's ``collage_style`` too, so the
    parts a template cannot carry (rounded corners, frame line, the margin of a
    fitted photo) match the preview at the booth as well.
    """
    from app import ecosystem, vorlagen_render
    if palette in vorlagen_render.STYLE_PRESETS:
        return {"preset": palette}
    eigener = ecosystem.event_profile_overrides(event).get("collage_style")
    return dict(eigener) if isinstance(eigener, dict) else {}


def render(vorlage: Dict[str, Any], event: Dict[str, Any], *, edge: int, logo: Optional[Path] = None,
           logo_name: str = "", style: Optional[Dict[str, Any]] = None) -> bytes:
    """A JPEG of the template with the sample photos, by the booth's renderer.

    The renderer reads pictures and fonts from one folder. A logo that is
    not approved yet must not go into the booth media, so the render gets a
    folder of its own: links to the media the template names, plus the logo.
    """
    from app import vorlagen, vorlagen_paket, vorlagen_render

    quelle = _assets_dir()
    with tempfile.TemporaryDirectory(prefix="layout-") as tmp:
        ordner = Path(tmp)
        for name in vorlagen_paket.medien_von(vorlage):
            p = quelle / name
            if p.is_file() and name != logo_name:
                try:
                    (ordner / name).symlink_to(p)
                except OSError:
                    shutil.copyfile(p, ordner / name)
        if logo and logo_name:
            shutil.copyfile(logo, ordner / logo_name)
        bild = vorlagen_render.render_template(
            vorlage, vorlagen.beispielbilder(vorlage.get("shots") or 1), assets_dir=ordner,
            qr_url=vorlagen_render.BEISPIEL_QR, texts=event_texts(event), style_cfg=style or {})
    bild.thumbnail((edge, edge))
    puffer = io.BytesIO()
    bild.convert("RGB").save(puffer, "JPEG", quality=85, optimize=True)
    return puffer.getvalue()


def too_wide(vorlage: Dict[str, Any], event: Dict[str, Any], style: Optional[Dict[str, Any]] = None) -> bool:
    """Whether a text runs over the edge of the sheet, measured with the renderer's fonts.

    The preview shows it anyway; this only lets the page say so in words.
    """
    from app import vorlagen_render
    from PIL import Image, ImageDraw
    st = vorlagen_render.style(style or {})
    breite = int(vorlage["canvas"][0])
    messen = ImageDraw.Draw(Image.new("L", (1, 1)))
    for t in vorlage.get("texts") or []:
        if t.get("rotation"):
            continue
        wert, font = vorlagen_render._text_zeilen(t, event_texts(event), st, _assets_dir())
        for zeile in wert.split("\n"):
            w = messen.textlength(zeile, font=font)
            x = float(t.get("x", 0))
            links = {"center": x - w / 2, "right": x - w}.get(t.get("align", "left"), x)
            if links < 0 or links + w > breite:
                return True
    return False


def preview_with_note(inv: Dict[str, Any], raw: Any, event: Dict[str, Any], *,
                      edge: int = PREVIEW_EDGE) -> tuple:
    """(JPEG, whether a text is wider than the sheet)."""
    werte = clean_values(inv, raw)
    name = "kunde-vorschau-logo.png" if werte["logo"] else ""
    vorlage = concrete(inv, werte, event, logo_name=name)
    stil = style_for(werte["palette"], event)
    return (render(vorlage, event, edge=edge, logo=logo_path(inv), logo_name=name, style=stil),
            too_wide(vorlage, event, stil))


def preview(inv: Dict[str, Any], raw: Any, event: Dict[str, Any], *, edge: int = PREVIEW_EDGE) -> bytes:
    werte = clean_values(inv, raw)
    name = "kunde-vorschau-logo.png" if werte["logo"] else ""
    vorlage = concrete(inv, werte, event, logo_name=name)
    return render(vorlage, event, edge=edge, logo=logo_path(inv), logo_name=name,
                  style=style_for(werte["palette"], event))


_TILES: Dict[tuple, bytes] = {}


def tile(inv: Dict[str, Any], index: int, event: Dict[str, Any]) -> bytes:
    """One offered layout with the owner's defaults and first colour set, small — for step 1.

    Kept in memory per template version: the page asks for every tile on each load.
    """
    datei = inv["templates"][index]
    inhalt_ = template_content(datei) or {}
    roh = {"template": datei, "fields": {}, "palette": (inv["palettes"] or [""])[0],
           "font": (inv["fonts"] or [""])[0]}
    schluessel = (inv["id"], index, json.dumps(inhalt_, sort_keys=True), json.dumps(roh, sort_keys=True),
                  json.dumps(inv["fields"], sort_keys=True), json.dumps(event_texts(event)))
    if schluessel not in _TILES:
        if len(_TILES) > 64:
            _TILES.clear()
        _TILES[schluessel] = preview(inv, roh, event, edge=TILE_EDGE)
    return _TILES[schluessel]


def result(inv: Dict[str, Any], event: Dict[str, Any]) -> bytes:
    vorlage = template_content(inv.get("result_file") or "")
    if not vorlage:
        raise ValueError("Kein Ergebnis")
    return render(vorlage, event, edge=PREVIEW_EDGE, style=style_for(inv["values"].get("palette") or "", event))


# ── For the pages ─────────────────────────────────────────────

def _german_date(value: Any) -> str:
    try:
        return datetime.fromisoformat(str(value or "")[:10]).strftime("%d.%m.%Y")
    except ValueError:
        return ""


def view(inv: Dict[str, Any], event: Dict[str, Any]) -> Dict[str, Any]:
    """What the customer page needs — nothing that names other events or the token."""
    from app import vorlagen_render
    namen = {t["file"]: t["name"] for t in own_templates()}
    return {
        "status": inv["status"], "note": inv.get("note") or "",
        "event_name": event.get("name") or "", "event_date": event_texts(event)["date"],
        "templates": [{"index": i, "name": namen.get(f, "Layout")} for i, f in enumerate(inv["templates"])],
        "template_files": inv["templates"],
        "fields": inv["fields"],
        "palettes": [{"id": p, "label": PALETTE_LABELS.get(p, p),
                      "colours": [vorlagen_render.STYLE_PRESETS[p][k] for k in ("background", "ink", "accent")]}
                     for p in inv["palettes"] if p in vorlagen_render.STYLE_PRESETS],
        "fonts": [{"id": f, "label": font_choices().get(f, f)} for f in inv["fonts"]],
        "logo_allowed": inv["logo"], "has_logo": bool(logo_path(inv)),
        "current": inv.get("values") or {},
        "expires_at": _german_date(inv.get("expires_at")),
    }
