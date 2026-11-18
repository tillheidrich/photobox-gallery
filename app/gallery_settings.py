"""Galerie-Einstellungen je Event — angelehnt an Auftragsgalerien, zugeschnitten auf Fotobox-Gäste.

Alles hat einen sicheren Standard und ist einzeln abschaltbar. Gespeichert als
JSON in ``events.gallery_json``; unbekannte Schlüssel werden verworfen.
"""
from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Dict, Optional

TAGS = [("red", "Rot", "#e5484d"), ("orange", "Orange", "#e8830c"), ("green", "Grün", "#46a758"), ("final", "Final", "#3a3733")]
TAG_IDS = {t[0] for t in TAGS}

DEFAULTS: Dict[str, Any] = {
    # Modus
    "mode": "presentation",          # presentation | proofing (Abstimmung)
    "allow_mode_switch": False,
    "mode_pin": "",                  # 4–6 Ziffern, leer = ohne
    # Darstellung
    "layout": "masonry",             # grid | masonry | list | polaroid
    "thumb_size": "m",               # s | m | l
    "gap": "m",                      # s | m | l
    "corners": "round",              # round | square
    "theme": "auto",                 # auto | light | dark
    "tint": "",                      # dezente Färbung (#rrggbb) oder leer
    "opener_title": "",              # alternative Überschrift (max. 2 Zeilen)
    # Auftakt: Titelbild (cover_path) über die ganze Breite, mit Titel, Datum
    # und Untertitel. Schrift nur aus dem, was mitgeliefert wird.
    "opener": False,
    "opener_subtitle": "",
    "title_font": "",                # "" (wie die Seite) | display | serif | geometric | system
    "show_file_info": False,         # Dateiname & -größe
    "show_date": False,              # Aufnahmedatum
    # EXIF-Zeile in der Großansicht: Kamera, Brennweite, Zeit, Blende, ISO.
    # Nie eine Position (GPS).
    "exif": False,
    # Abstimmung
    "color_tags": True,              # Gäste dürfen farbig markieren (nur Abstimmungsmodus)
    "hide_red_in_presentation": True,
    "likes": True,
    "selection": True,               # „Meine Auswahl“ als ZIP
    # Kundenauswahl (app/picks.py): höchstens so viele Bilder je Auswahl;
    # leer = ohne Grenze. Gilt nur für angemeldete Kunden.
    "selection_max": "",
    # Kommentare von angemeldeten Kunden an Bild und Galerie (app/comments.py)
    "comments": False,
    # Layout approval by the customer (app/layout_freigabe.py): off = no
    # admin card, every /layout/<token> of this event answers 404.
    "layout_approval": False,
    # Betroffenenrechte: Wer den QR-Code hat, war an der Box und darf seinen
    # eigenen Durchgang löschen. Abschaltbar — auf manchen Veranstaltungen
    # gehören die Bilder dem Veranstalter.
    "guest_delete": True,
    # Teilen & Download — jedes einzeln
    "share_native": True,            # Teilen-Blatt am Handy
    # The WhatsApp button left the gallery on 17.11.2026 (native share sheet
    # and "Link kopieren" reach WhatsApp too). The key stays readable so old
    # settings still parse; nothing shows it any more.
    "share_whatsapp": False,
    "share_sharepic": True,
    "share_link_copy": True,
    "download_single": True,
    "download_original": True,
    "download_zip": False,           # „Alles herunterladen“
    "guest_upload": False,
    # Grace period of the guest upload (app/guest_upload.py): open until the
    # end of the N-th day after the event date. Empty = no window (as before).
    "upload_grace_days": "",
    # Filter chips by source (box, guest upload, admin upload, import) for
    # guests (app/media_source.py). The admin always has them.
    "show_source_filter": False,
    # "Live" badge while the event runs and photos arrive, with a "x neue
    # Fotos" notice instead of a reload (app/live.py)
    "live_badge": False,
    # Other sites may show the gallery in an iframe (?embed=1) only when this
    # is on (final review A11; before, any site could). Off for new events.
    # An event stored before the switch existed keeps embedding when it had
    # named sites in embed_origins (see clean()), otherwise it is off.
    "embed": False,
    # Web origins that may show the gallery in an iframe when "embed" is on,
    # space separated, at most five. Empty: any site may.
    "embed_origins": "",
    # Extras on the guest pages, each with its own switch (15.11.2026, for a
    # very clean gallery): the "QR-Code drucken" button in the gallery header,
    # the e-mail signup for new photos, "mit/ohne Rahmen" on the run page
    # /p/<code>, and the "Meine Aufnahmen" link (run page and gallery nav).
    # On by default: the page stays what it was.
    "print_qr": True,
    "notify_signup": True,
    "run_frame_choice": True,
    "my_shots_link": True,
    # Welche skalierten Größen der Gast wählen darf (das Original hängt weiter
    # an ``download_original``)
    "download_640": True,            # fürs Handy
    "download_1024": True,           # Messenger, Social
    "download_2048": True,           # Rechner, kleiner Druck
    # Zweiter Bildschirm: Beamer-Slideshow und Gästekiosk (iPad, Handy, zweiter PC)
    "kiosk": False,                  # Kiosk für Gäste unter /schau/<slug>
    "kiosk_share": True,             # Gäste dürfen ein Foto aufs eigene Gerät holen
    "show_order": "chrono",          # chrono | random — gilt für Slideshow und Kiosk
    # Wie die Datei heißt, die der Gast bekommt (siehe app/dateinamen.py).
    # Leer: der Name aus der Box. Gilt nur für die Auslieferung, nicht für
    # die Datei auf der Platte.
    "download_pattern": "",
    # The fixed rule (app/dateinamen.py, 15.11.2026): "" = the account's
    # default, "original" = as before (the box's name or the pattern above),
    # "event_datetime" = <Event>_<YYYY-MM-DD>_<HH-MM-SS>.<ext>.
    "file_naming": "",
    # Bewertung: die Bitte am Ende der Galerie (siehe app/bewertung.py).
    # „erben" nimmt die Vorgabe der Instanz; Adresse und Text leer heißt
    # ebenfalls „von der Instanz".
    "review_mode": "erben",          # erben | an | aus
    "review_url": "",
    "review_text": "",
    # Freigabe
    "moderation": False,             # neue Aufnahmen erst nach Freigabe sichtbar
    # Zugang
    # Das Passwort darf im QR- und Sharepic-Link mitreisen: Wer an der Box war,
    # muss dann nichts tippen, und trotzdem kommt niemand über die Adresse
    # allein hinein. Abschaltbar — auf manchen Veranstaltungen ist genau das
    # Tippen der Punkt.
    "link_password": False,
    "access_request": False,         # Knopf „Zugriff anfragen" auf der Passwortseite
    # Sicherheit
    "expires_at": "",                # ISO-Datum/Zeit; danach Statusseite
    "watermark": False,
    "watermark_text": "",
    # Stiller Betrieb: hochladen ja, zeigen nein (siehe app/silence.py)
    "silent": False,
}

#: "Schlichte Galerie" in the event form switches these off at once and
#: "Alles anzeigen" back on. Downloads stay: a clean gallery still hands the
#: guests their pictures.
PRESET_CLEAN = ("print_qr", "notify_signup", "share_sharepic", "share_native",
                "share_link_copy", "likes", "run_frame_choice", "my_shots_link")

def new_event_defaults(kind: str = "booth") -> Dict[str, Any]:
    """Settings a new event starts with, from the account setting
    ``new_event_gallery_default`` (normal | schlicht, default normal).

    Schlicht = ``PRESET_CLEAN`` off; downloads stay. A client gallery
    (``kind == "client"``) does not follow: it is a photographer's delivery,
    not a booth gallery. Empty dict = start as always.
    """
    if kind == "client":
        return {}
    from app import db
    try:
        mode = (db.get_setting("new_event_gallery_default") or "normal").strip()
    except Exception:                       # noqa: BLE001 — no database: the plain default
        mode = "normal"
    return {k: False for k in PRESET_CLEAN} if mode == "schlicht" else {}


_ENUMS = {"mode": {"presentation", "proofing"}, "layout": {"grid", "masonry", "list", "polaroid"}, "thumb_size": {"s", "m", "l"},
          "gap": {"s", "m", "l"}, "corners": {"round", "square"}, "theme": {"auto", "light", "dark"},
          "show_order": {"chrono", "random"}, "review_mode": {"erben", "an", "aus"},
          "title_font": {"", "display", "serif", "geometric", "system"},
          "file_naming": {"", "original", "event_datetime"}}

#: Felder, für die 120 Zeichen zu knapp sind. Eine Bewertungsadresse bei Google
#: ist länger als jede Überschrift.
_LAENGEN = {"review_url": 300, "review_text": 200, "download_pattern": 120, "embed_origins": 400}

#: Design presets for the event form (no. 66): named sets of the existing
#: theme settings only - no new font, no new colour system. The buttons in
#: the form tick the fields; saving stays a click.
DESIGN_PRESETS: Dict[str, Dict[str, Any]] = {
    "hell": {"theme": "light", "corners": "round", "gap": "m", "tint": "", "title_font": "serif"},
    "dunkel": {"theme": "dark", "corners": "round", "gap": "m", "tint": "", "title_font": "display"},
    "warm": {"theme": "light", "corners": "round", "gap": "l", "tint": "#c8743a", "title_font": "serif"},
    "schlicht": {"theme": "light", "corners": "square", "gap": "s", "tint": "", "title_font": "system"},
}
DESIGN_PRESET_LABELS = {"hell": "Hell klassisch", "dunkel": "Dunkel", "warm": "Warm", "schlicht": "Schlicht"}


def embed_origins(raw: str) -> list:
    """The web origins of ``embed_origins``: http(s) only, scheme+host, five at most."""
    from urllib.parse import urlsplit
    out = []
    for part in str(raw or "").replace(",", " ").split():
        u = urlsplit(part)
        if u.scheme in ("http", "https") and u.netloc and "*" not in u.netloc and "@" not in u.netloc:
            origin = f"{u.scheme}://{u.netloc.lower()}"
            if origin not in out:
                out.append(origin)
    return out[:5]


def parse(raw: Optional[str]) -> Dict[str, Any]:
    try:
        data = json.loads(raw or "{}")
    except ValueError:
        data = {}
    return clean(data)


def clean(data: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(DEFAULTS)
    for k, default in DEFAULTS.items():
        if k not in data:
            continue
        v = data[k]
        if isinstance(default, bool):
            out[k] = v in (True, 1, "1", "true", "on", "yes")
        elif k in _ENUMS:
            out[k] = v if v in _ENUMS[k] else default
        else:
            out[k] = str(v or "").strip()[:_LAENGEN.get(k, 120)]
    if out["mode_pin"] and not (out["mode_pin"].isdigit() and 4 <= len(out["mode_pin"]) <= 6):
        out["mode_pin"] = ""
    if out["selection_max"] and not (out["selection_max"].isdigit() and 0 < int(out["selection_max"]) <= 10000):
        out["selection_max"] = ""
    if out["upload_grace_days"] and not (out["upload_grace_days"].isdigit()
                                         and int(out["upload_grace_days"]) <= 365):
        out["upload_grace_days"] = ""
    if out["embed_origins"]:
        out["embed_origins"] = " ".join(embed_origins(out["embed_origins"]))
    if "embed" not in data:
        # Stored before the switch existed: an event that had named sites
        # embedded on purpose and keeps doing so; every other one is off.
        out["embed"] = bool(out["embed_origins"])
    if out["tint"] and not (len(out["tint"]) == 7 and out["tint"].startswith("#")):
        out["tint"] = ""
    if out["review_url"]:
        from app import bewertung
        out["review_url"] = bewertung.gueltige_url(out["review_url"])
    if out["download_pattern"]:
        from app import dateinamen
        if not dateinamen.gueltig(out["download_pattern"]):
            out["download_pattern"] = ""
    return out


def for_event(event: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """The settings that apply to this event.

    Every reader that has an event goes through here rather than parsing
    ``gallery_json`` itself, so rules that depend on more than the event's
    own JSON live in one place.

    A folder (``parent_id``, see app/folders.py) takes the parent's settings
    for every key it does not store itself. Silent mode, expiry and
    moderation are never looser than the parent's: whoever may not see the
    parent may not see its folders either.
    """
    event = event or {}
    if not event.get("parent_id"):
        return parse(event.get("gallery_json"))
    from app import db
    parent = db.get_event_by_id(int(event["parent_id"]))
    if not parent:
        return parse(event.get("gallery_json"))
    base = parse(parent.get("gallery_json"))
    own = _raw(event.get("gallery_json"))
    out = clean({**base, **own})
    out["silent"] = bool(out["silent"] or base["silent"])
    out["moderation"] = bool(out["moderation"] or base["moderation"])
    out["expires_at"] = _earliest(out["expires_at"], base["expires_at"])
    return out


def _raw(raw: Optional[str]) -> Dict[str, Any]:
    """Only the keys an event stores itself — what a folder overrides."""
    try:
        data = json.loads(raw or "{}")
    except ValueError:
        return {}
    return {k: v for k, v in data.items() if k in DEFAULTS} if isinstance(data, dict) else {}


def _earliest(a: str, b: str) -> str:
    def when(s):
        try:
            return datetime.fromisoformat(s.replace("Z", "")) if s else None
        except ValueError:
            return None
    da, db_ = when(a), when(b)
    if da and db_:
        return a if da <= db_ else b
    return a if da else (b if db_ else "")


def store(event: Optional[Dict[str, Any]], cfg: Dict[str, Any]) -> str:
    """The JSON to save for these settings.

    A gallery stores everything. A folder stores only what differs from its
    parent, so a later change on the parent reaches every folder that did
    not decide otherwise.
    """
    cfg = clean(cfg)
    event = event or {}
    if event.get("parent_id"):
        from app import db
        parent = db.get_event_by_id(int(event["parent_id"]))
        if parent:
            base = parse(parent.get("gallery_json"))
            return json.dumps({k: v for k, v in cfg.items() if v != base.get(k)})
    return json.dumps(cfg)


def from_form(form, base: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Einstellungen aus einem Formular lesen.

    Textfelder, die das Formular gar nicht enthält, bleiben auf dem Wert aus
    ``base`` stehen. So darf ein Feld (z. B. ``expires_at``) in einem anderen
    Formular wohnen, ohne beim Speichern hier verloren zu gehen. Kästchen sind
    davon ausgenommen: nicht angekreuzt heißt aus.
    """
    base = base or {}
    data = {}
    for k, default in DEFAULTS.items():
        if isinstance(default, bool):
            data[k] = bool(form.get(k))
        elif k in form:
            data[k] = form.get(k)
        elif k in base:
            data[k] = base[k]
    return clean(data)


def is_expired(cfg: Dict[str, Any], now: Optional[datetime] = None) -> bool:
    raw = (cfg.get("expires_at") or "").strip()
    if not raw:
        return False
    try:
        until = datetime.fromisoformat(raw.replace("Z", ""))
    except ValueError:
        return False
    return (now or datetime.now()) >= until
