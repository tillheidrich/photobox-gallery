"""Admin: Fotobox fernsteuern — Profil, Assets, Aufträge, QR-Ziel je Event."""
from __future__ import annotations

import hashlib
import json
import re
import secrets
from pathlib import Path

from flask import (Blueprint, Response, abort, current_app, flash, g, jsonify, redirect,
                   render_template, request, session, url_for)
from werkzeug.utils import secure_filename

from app import booth_texts, db, ecosystem, frame_store, media
from app.blueprints.admin import _csrf_token, _require_admin

bp = Blueprint("admin_booth", __name__)


@bp.before_request
def _set_lang():
    # Same as the other admin pages: without it the booth pages ignored the
    # operator's language and the switch in the header showed "DE" in German.
    g.lang = session.get("lang", "de")


@bp.before_request
def _csrf_guard():
    """Alle schreibenden Admin-Aufrufe brauchen das Formular-Token (Schutz vor fremden Seiten)."""
    if request.method == "POST":
        from flask import session as _s
        import hmac
        sent = request.form.get("csrf_token") or request.headers.get("X-CSRF-Token") or ""
        if not _s.get("csrf_token") or not hmac.compare_digest(str(sent), str(_s.get("csrf_token"))):
            abort(400)

MODES = [("photo", "Foto"), ("collage", "Collage"), ("gif", "GIF"), ("boomerang", "Boomerang"), ("video", "Video")]
SCALES = [("native", "native (nicht hochskalieren)"), ("720p", "720p"), ("1080p", "1080p"), ("1440p", "1440p")]
FORMATS = [("4:5", "Hochformat 4:5"), ("9:16", "Story 9:16"), ("3:2", "Quer 3:2"), ("1:1", "Quadrat"), ("2:3", "Hoch 2:3")]
LOOKS = [("original", "Original"), ("warm", "Warm"), ("cool", "Kühl"), ("mono", "Schwarzweiß"), ("film", "Film"),
         ("punch", "Kräftig"), ("soft", "Weich")]
# Alle Texte der Kiosk-Oberfläche, gruppiert nach Bildschirm — siehe app/booth_texts.py.
# TEXT_KEYS bleibt als flache Liste erhalten, weil Speichern und Filtern sie so brauchen.
TEXT_GROUPS = booth_texts.GROUPS
TEXT_KEYS = [(key, label) for _, entries in booth_texts.GROUPS for key, label, _ in entries]
TEXT_DEFAULTS = booth_texts.DEFAULTS

# Aussehen — dieselben Werte kennt booth/boothcore/web/static/app.js
THEME_RADII = [("pill", "rund (Pille)"), ("round", "abgerundet"), ("soft", "leicht abgerundet"), ("square", "eckig")]
THEME_SIZES = [("s", "klein"), ("m", "mittel"), ("l", "groß")]
THEME_WEIGHTS = [("500", "normal"), ("600", "halbfett"), ("700", "fett")]
THEME_SECONDARY = [("solid", "gefüllt"), ("outline", "nur Umriss")]
THEME_FONTS = [("system", "Inter (Arbeitsschrift)"), ("display", "Archivo (Anzeigeschrift)"),
               ("serif", "Serif"), ("rounded", "rund"), ("mono", "Schreibmaschine")]
THEME_RADII_IDS = {v for v, _ in THEME_RADII}
THEME_SIZE_IDS = {v for v, _ in THEME_SIZES}
THEME_WEIGHT_IDS = {v for v, _ in THEME_WEIGHTS}
THEME_SECONDARY_IDS = {v for v, _ in THEME_SECONDARY}
THEME_FONT_IDS = {v for v, _ in THEME_FONTS}

# Fertige Vorlagen — ein Klick füllt die Felder, danach lässt sich alles einzeln ändern.
THEME_PRESETS = [
    # Fünf Vorlagen, alle aus derselben Palette. Die Zahlen dahinter sind
    # gerechnet, nicht geschätzt: Schrift auf Fläche immer über 4,5:1, der
    # Akzent gegen den Grund über 3:1 (AA für Bedienelemente).
    ("Bühne (Standard)", {"accent": "#e7ad59", "on_accent": "#0f0d09", "bg": "#0a0806", "panel": "#23201c",
                          "text": "#f5f3f0", "muted": "#898580", "radius": "pill", "size": "m", "weight": "600",
                          "uppercase": False, "secondary": "solid", "font": "system"}),
    ("Papier (hell)", {"accent": "#bf7b0f", "on_accent": "#0f0d09", "bg": "#f8f6f4", "panel": "#fefdfc",
                       "text": "#1d1a16", "muted": "#625d56", "radius": "round", "size": "m", "weight": "600",
                       "uppercase": False, "secondary": "solid", "font": "system"}),
    ("Hochzeit", {"accent": "#efc27e", "on_accent": "#321c04", "bg": "#1d1a16", "panel": "#2c2823",
                  "text": "#f8f6f4", "muted": "#a29e98", "radius": "soft", "size": "l", "weight": "500",
                  "uppercase": True, "secondary": "outline", "font": "serif"}),
    ("Kita & Schule", {"accent": "#9d6000", "on_accent": "#fdf5e9", "bg": "#fdf5e9", "panel": "#fefdfc",
                       "text": "#1d1a16", "muted": "#625d56", "radius": "round", "size": "l", "weight": "700",
                       "uppercase": False, "secondary": "solid", "font": "rounded"}),
    ("Schlicht", {"accent": "#1d1a16", "on_accent": "#fefdfc", "bg": "#fefdfc", "panel": "#efece9",
                  "text": "#0f0d09", "muted": "#625d56", "radius": "square", "size": "m", "weight": "500",
                  "uppercase": True, "secondary": "outline", "font": "mono"}),
]

ALLOWED_ASSET_EXT = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".mp4", ".webm", ".json", ".cube",
                     ".mp3", ".wav", ".ogg", ".woff2", ".woff", ".ttf", ".otf"}
FONT_EXT = {".woff2", ".woff", ".ttf", ".otf"}
# Rahmen-Textzeilen: drei feste Reihen im Formular reichen für Eventname, Datum
# und einen Zusatz. Leere Zeilen fallen beim Speichern weg.
PAPERS = [("10x15", "10 × 15 cm (6×4″) — Standard"), ("postcard", "Postkarte 10 × 14,8 cm (SELPHY)"),
          ("13x18", "13 × 18 cm"), ("15x20", "15 × 20 cm (8×6″)"),
          ("5x15", "Streifen 5 × 15 cm (2×6″)"), ("card", "Kartengröße 8,6 × 5,4 cm (SELPHY)")]
PAPER_IDS = {v for v, _ in PAPERS}

# Welcher Drucker kann was. Sechs Formate anzubieten, wenn der angeschlossene
# Drucker genau eines beherrscht, ist keine Freiheit, sondern eine Falle: Der
# Betreiber wählt 13 × 18, und am Abend kommt nichts heraus. Die Liste ist
# absichtlich klein — sie deckt die Geräte ab, die im Verleih vorkommen, und
# für alles andere bleibt es bei „alle Formate".
DRUCKER_FORMATE = {
    "selphy cp1300": ["postcard", "card"],
    "selphy cp1500": ["postcard", "card"],
    "selphy": ["postcard", "card"],
    "ds-rx1": ["10x15", "5x15", "15x20"],
    "ds620": ["10x15", "5x15", "13x18", "15x20"],
    "dnp": ["10x15", "5x15", "13x18", "15x20"],
    "cp-d70": ["10x15", "13x18", "15x20"],
    "mitsubishi": ["10x15", "13x18", "15x20"],
    "hiti": ["10x15", "13x18", "15x20"],
}


def papers_for(printer_name: str):
    """Die Formate, die dieser Drucker wirklich kann — oder alle, wenn unbekannt."""
    name = str(printer_name or "").lower()
    for muster, formate in DRUCKER_FORMATE.items():
        if muster in name:
            return formate
    return []
FITS = [("cover", "formatfüllend (beschneidet)"), ("contain", "ganz sichtbar (mit Rand)")]
FLASH_MODES = [("any", "egal"), ("M", "M (manuell)"), ("Av", "Av (Zeitautomatik)")]


def _hexfarbe(wert, fallback: str) -> str:
    """Eine Farbe aus dem Formular — oder der Rückfallwert.

    Ein Farbfeld liefert im Normalfall ``#rrggbb``; ein abgeschalteter Browser,
    ein altes Formular oder ein Skript liefern alles Mögliche. Was nicht passt,
    wird nicht repariert, sondern ersetzt.
    """
    v = (wert or "").strip()
    return v if re.fullmatch(r"#[0-9a-fA-F]{6}", v) else fallback
FRAME_LINES = 3
FRAME_ALIGNS = [("left", "links"), ("center", "mittig"), ("right", "rechts")]
FRAME_TARGETS = [("both", "Foto und Druck"), ("photo", "nur Foto"), ("print", "nur Druck")]
QR_CORNERS = [("bottom-right", "unten rechts"), ("bottom-left", "unten links"),
              ("top-right", "oben rechts"), ("top-left", "oben links")]
# The live view's own exposure with flash (booth: camera/exposure.py).
PREVIEW_ISO = ("auto", "400", "800", "1600", "3200", "6400")
PREVIEW_APERTURE = ("1.8", "2", "2.8", "3.5", "4", "5", "5.6")
PREVIEW_SHUTTER = ("1/15", "1/30", "1/60", "1/125")
# How far "auto" may take the live view: the ISO cap, then the slowest shutter
# (booth: exposure.ISO_MAX_CHOICES / SHUTTER_MIN_CHOICES, settings.js CHOICES).
PREVIEW_ISO_MAX = ("3200", "6400", "12800", "25600")
PREVIEW_SHUTTER_MIN = ("1/60", "1/30", "1/15")
SOUND_KEYS = [("countdown", "Countdown"), ("shutter", "Auslöser"), ("done", "Fertig"), ("print", "Druck")]
ATTRACT_SOURCES = [("recent", "letzte Aufnahmen"), ("media", "Startmedien"), ("own", "eigene Auswahl (unten)"),
                   ("text", "nur Text auf Fläche"), ("blank", "einfarbige Fläche"), ("off", "aus")]
ATTRACT_HINTS = [("bar", "Balken unten"), ("center", "groß in der Mitte"), ("none", "gar nicht")]
ATTRACT_FITS = [("cover", "formatfüllend (beschneidet)"), ("contain", "ganz sichtbar (Ränder)"),
                ("auto", "passend zur Ausrichtung (füllend nur bei gleicher Ausrichtung)")]
KIOSK_SIZES = [("m", "normal"), ("l", "groß"), ("xl", "sehr groß")]
# Screens of the run that take their own backdrop, and where its picture may come from.
BACKDROP_SCREENS = [("setup", "Vorbereitung / Format"), ("capture", "Countdown / Aufnahme"),
                    ("result", "Ergebnis")]
BACKDROP_KINDS = ("start", "background", "scene", "lock")
# Phases of the run that take their own medium (picture, GIF or video), see
# booth/boothcore/config.py "phase_media". Media come from the same kinds.
PHASE_SCREENS = [("processing", "Warten auf das Ergebnis"), ("between", "Zwischen zwei Aufnahmen"),
                 ("after", "Nach dem Ergebnis („Danke!“)"), ("error", "Fehlerbild")]
PHASE_EXT = (".jpg", ".jpeg", ".png", ".webp", ".gif", ".mp4", ".webm")


def _is_picture(name: str) -> bool:
    """A still or animated picture — a video or a JSON template is no backdrop."""
    return Path(name).suffix.lower() in (".jpg", ".jpeg", ".png", ".webp", ".gif")


# Spiegel von boothcore/webhooks.py EVENTS
WEBHOOK_EVENTS = [("session_started", "Durchgang gestartet"), ("shot_taken", "Auslösung"),
                  ("media_ready", "Aufnahme fertig"), ("printed", "gedruckt"),
                  ("mail_queued", "E-Mail eingereiht"), ("error", "Fehler")]
# Was nur an die Box gehört und nie in einer Datei oder auf einer Seite landet.
SECRET_FIELDS = [("mail", "password"), ("webhooks", "secret"), ("http_trigger", "token")]
PROFILE_MAX_BYTES = 1_000_000

_ON_OFF = [("ein", "an"), ("aus", "aus")]


def _hw(key, label, kind, choices=None, help="", **limits):
    """One field of the "Kamera, Drucker und Ablage" card; ``key`` is a REMOTE_LOCAL_KEYS key."""
    return {"key": key, "id": key.replace(".", "_"), "label": label, "kind": kind,
            "choices": choices or [], "help": help, **limits}


#: The settings that live at the box and that the gallery may change (the job
#: ``set_local_config``): exactly ``ecosystem.REMOTE_LOCAL_KEYS``, grouped and
#: put into words. tests/test_booth_form_covers_every_setting.py fails when the
#: two drift apart, so a key added to the box cannot stay unreachable here.
HARDWARE_GROUPS = [
    ("Box", [
        _hw("booth_name", "Name der Box an der Box", "text",
            help="So heißt die Box auf ihrem eigenen Bildschirm. Der Name in der Galerie bleibt, wie er ist.", maxlen=60),
        _hw("kiosk", "Vollbild ohne Fensterrahmen (Kiosk)", "bool", _ON_OFF,
            help="Aus: Die Box läuft in einem gewöhnlichen Fenster."),
    ]),
    ("Kamera", [
        _hw("camera.backend", "Anschluss der Kamera", "enum",
            [("auto", "automatisch erkennen"), ("edsdk", "Canon über USB (EDSDK)"), ("ccapi", "Canon über WLAN (CCAPI)"),
             ("digicamcontrol", "digiCamControl (Windows)"), ("gphoto2", "gPhoto2 (Linux)"),
             ("folder", "Ordner der Tethering-Software"), ("webcam", "Webcam"), ("mock", "Testkamera (ohne Kamera)")],
            help="Gilt nach „App neu starten“."),
        _hw("camera.webcam_index", "Nummer der Webcam", "int", help="0 ist die erste Webcam, 1 die zweite.", lo=0, hi=9),
        _hw("camera.rotate", "Bild drehen", "enum",
            [(0, "nicht drehen"), (90, "um 90 Grad"), (180, "um 180 Grad"), (270, "um 270 Grad")],
            help="Für eine Kamera, die hochkant montiert ist."),
        _hw("camera.mirror_preview", "Live-Bild spiegeln", "bool", _ON_OFF,
            help="Wie im Spiegel: Wer nach links geht, sieht sich nach links gehen. Die Fotos werden nie gespiegelt."),
        _hw("camera.ccapi_liveview", "Live-Bild bei Canon über WLAN", "enum",
            [("small", "klein (flüssiger)"), ("medium", "mittel"), ("off", "aus")],
            help="Klein läuft flüssiger, wenn viele Geräte im selben WLAN sind."),
        _hw("camera.ccapi_display", "Display der Kamera während des Live-Bilds", "enum",
            [("on", "anlassen"), ("off", "ausschalten")]),
    ]),
    ("Drucker", [
        _hw("printer.backend", "Wie die Box druckt", "enum",
            [("auto", "automatisch"), ("windows", "Windows-Druckertreiber"), ("cups", "CUPS (Mac, Linux)"),
             ("none", "kein Drucker")]),
        _hw("printer.name", "Name des Druckers", "text", help="So, wie er in Windows heißt.", maxlen=120),
        _hw("printer.paper", "Papierformat im Treiber", "text", help="Zum Beispiel postcard für die SELPHY.", maxlen=30),
        _hw("printer.model", "Druckermodell", "text",
            help="Zum Beispiel selphy-cp, dnp-ds620 oder dnp-rx1. Daraus kennt die Box die Blätter einer Kassette.", maxlen=40),
        _hw("printer.medium", "Papiersorte", "text", help="Zum Beispiel 10x15, 10x15-cut oder postcard.", maxlen=40),
        _hw("printer.sheets_total", "Blätter je Kassette", "int", help="0 = die Box leitet es vom Druckermodell ab.",
            lo=0, hi=2000),
        _hw("printer.tray_total", "Blätter je Papierfach", "int",
            help="Für Drucker mit Papierfach und Farbband (SELPHY). 0 = Wert des Druckermodells.", lo=0, hi=2000),
        _hw("printer.ribbon_total", "Drucke je Farbband", "int", help="0 = Wert des Druckermodells.", lo=0, hi=2000),
        _hw("printer.follow_jobs", "Druckaufträge in Windows verfolgen", "bool", _ON_OFF,
            help="An: Ein Druck zählt erst, wenn er die Windows-Warteschlange verlassen hat. "
                 "Aus: sobald Windows ihn angenommen hat."),
        _hw("print_routing", "Mehrere Drucker", "enum",
            [("balance", "gleichmäßig verteilen"), ("failover", "den zweiten nur bei Ausfall nehmen")]),
    ]),
    ("Druckkorrektur", [
        _hw(f"printers.{n}.{k}", f"Drucker {n}: {label}", "int", lo=-50, hi=50,
            help="Von −50 bis +50, 0 = keine Korrektur. Gilt ab dem nächsten Druck." if (n, k) == (1, "brightness") else "")
        for n in (1, 2, 3, 4)
        for k, label in (("brightness", "Helligkeit"), ("contrast", "Kontrast"), ("saturation", "Sättigung"))
    ]),
    ("Ablage", [
        _hw("storage.layout", "Ordner für die Aufnahmen", "enum",
            [("event/date", "je Event und Tag"), ("event", "je Event"), ("flat", "alles in einem Ordner")]),
        _hw("storage.originals", "Einzelaufnahmen mitkopieren", "bool", _ON_OFF),
        _hw("storage.usb.eject_hint", "Hinweis zum sicheren Entfernen des USB-Sticks", "bool", _ON_OFF),
    ]),
]


def _check_csrf() -> None:
    """Formular-Absender prüfen. Ohne gültiges Zeichen kein Schreibzugriff."""
    import hmac
    gesendet = str(request.form.get("csrf_token") or "")
    if not session.get("csrf_token") or not hmac.compare_digest(gesendet, str(session["csrf_token"])):
        abort(400)


def _check_csrf_json() -> None:
    """Dasselbe für den Editor, der seine Vorlage als JSON schickt."""
    import hmac
    gesendet = str(request.headers.get("X-CSRF-Token") or "")
    if not session.get("csrf_token") or not hmac.compare_digest(gesendet, str(session["csrf_token"])):
        abort(400)


def _assets_dir() -> Path:
    d = media.assets_root() / "booth"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _asset_files(*kinds: str) -> set:
    """Dateinamen aller Assets dieser Arten — Grundlage jeder Auswahlliste im Formular."""
    return {a["file"] for a in ecosystem.list_assets() if a["kind"] in kinds}


def _without_secrets(profile: dict) -> dict:
    """Kopie ohne Passwort, Webhook-Geheimnis und Trigger-Token."""
    out = json.loads(json.dumps(profile, ensure_ascii=False))
    for block, key in SECRET_FIELDS:
        if isinstance(out.get(block), dict):
            out[block][key] = ""
    return out


def _lut_choices() -> list:
    """Kennungen aller LUTs: mitgeliefert plus hochgeladene ``.cube``-Dateien."""
    uploaded = [Path(a["file"]).stem.lower() for a in ecosystem.list_assets("lut")]
    return sorted(set(ecosystem.BUILTIN_LUTS) | set(uploaded))


def _weak_pin_reason(pin: str) -> str:
    """The booth's PIN rule (boothcore.config.weak_pin_reason), kept in step by a test.

    The gallery does not import the booth package, so the rule lives twice.
    """
    if pin == "2580":
        return "2580 ist die Werks-PIN der Box"
    if len(pin) < 4:
        return "PIN braucht mindestens 4 Ziffern"
    if len(set(pin)) == 1:
        return "PIN aus einer einzigen Ziffer ist zu leicht zu erraten"
    if {(int(b) - int(a)) % 10 for a, b in zip(pin, pin[1:])} in ({1}, {9}):
        return "PIN als Zahlenreihe wie 1234 ist zu leicht zu erraten"
    return ""


def _frame_fields(f, clip: str) -> dict:
    """The frames of boomerang or video from the profile form ("Rahmen für Boomerang & Video").

    Only names the gallery lists are kept. All built-ins ticked and no own
    frame is stored as two empty lists ("every built-in"), as the collage
    layouts do, so a built-in added later is offered too.
    """
    own_known = set(frame_store.own_files())
    builtin_all = frame_store.builtin_ids()
    own = [x for x in f.getlist(f"{clip}_frame_templates") if x in own_known]
    ids = [x for x in f.getlist(f"{clip}_frame_builtins") if x in builtin_all]
    if not own and sorted(ids) == sorted(builtin_all):
        ids = []
    default = (f.get(f"{clip}_frame_default") or "").strip()
    if default not in set(own) | set(ids or builtin_all) | {"none"}:
        default = ""
    return {"frames": bool(f.get(f"{clip}_frames")), "frame_templates": own, "frame_builtins": ids,
            "frame_none": bool(f.get(f"{clip}_frame_none")), "frame_default": default,
            "frame_guest_choice": bool(f.get(f"{clip}_frame_guest_choice"))}


def _num(name, default, cast=int, lo=None, hi=None):
    try:
        v = cast(request.form.get(name, default))
    except (TypeError, ValueError):
        v = default
    if lo is not None:
        v = max(lo, v)
    if hi is not None:
        v = min(hi, v)
    return v


@bp.route("/devices/<int:device_id>/booth", methods=["GET", "POST"])
def booth_profile(device_id: int):
    redir = _require_admin()
    if redir:
        return redir
    ecosystem.init_schema()
    device = db.get_device_by_id(device_id)
    if not device:
        abort(404)
    prof = ecosystem.device_profile(device)
    languages = ["de", "en"]

    if request.method == "POST":
        f = request.form
        p = dict(prof)
        p["modes"] = [m for m, _ in MODES if f.get(f"mode_{m}")] or ["photo"]
        p["formats"] = [x for x, _ in FORMATS if f.get(f"format_{x}")] or ["4:5"]
        p["default_format"] = f.get("default_format") if f.get("default_format") in p["formats"] else p["formats"][0]
        p["languages"] = [l.strip() for l in f.get("languages", "de,en").split(",") if re.fullmatch(r"[a-z]{2}", l.strip())] or ["de"]
        p["countdown"] = {"first": _num("cd_first", 3, int, 1, 20), "next": _num("cd_next", 2, int, 1, 20),
                          "style": "fullscreen" if f.get("cd_style") == "fullscreen" else "overlay",
                          "review_seconds": _num("cd_review", 1.5, float, 0, 10), "sound": bool(f.get("cd_sound")),
                          "live_view": f.get("cd_live_view") if f.get("cd_live_view") in ("frame", "off") else "fullscreen",
                          "crop_guide": bool(f.get("cd_crop_guide")),
                          "smile_media": f.get("cd_smile_media", "").strip()[:120],
                          "smile_text": f.get("cd_smile_text", "").strip()[:40],
                          "poses": bool(f.get("cd_poses")),
                          "smile_ms": _num("cd_smile_ms", 700, int, 200, 3000),
                          "cancel": bool(f.get("cd_cancel")),
                          "display": "ring" if f.get("cd_display") == "ring" else "number",
                          "color": _hexfarbe(f.get("cd_color"), ""),
                          "size": f.get("cd_size") if f.get("cd_size") in ("m", "l", "xl") else "l"}
        p["retake"] = bool(f.get("retake"))
        # Gruß vom Gast: „modes" bleibt bewusst eine Liste — bei GIF und Video
        # steht kein Text auf dem Ergebnis, da wäre die Frage nur eine Hürde.
        p["guest_text"] = {"enabled": bool(f.get("gt_enabled")),
                           "max_len": _num("gt_max_len", 40, int, 10, 120),
                           "required": bool(f.get("gt_required")),
                           "modes": [m for m in ("photo", "collage") if f.get(f"gt_mode_{m}")]}
        # Drawing on the picture: the colours the guest picks from. Only
        # #rrggbb survives (the booth refuses anything else anyway); none left
        # means the shipped six, never an empty palette.
        draw_colors = []
        for c in (f.get("draw_colors") or "").replace(";", ",").split(","):
            c = _hexfarbe(c, "").lower()
            if c and c not in draw_colors:
                draw_colors.append(c)
        p["draw"] = {"enabled": bool(f.get("draw_enabled")),
                     "modes": [m for m in ("photo", "collage") if f.get(f"draw_mode_{m}")],
                     "colors": draw_colors[:8] or list(ecosystem.DEFAULT_PROFILE["draw"]["colors"])}
        p["review_seconds"] = _num("review_seconds", 12, int, 3, 60)
        for clip in ("boomerang", "video"):
            p[clip] = {"format": f.get(f"{clip}_format") if f.get(f"{clip}_format") in dict(FORMATS) or f.get(f"{clip}_format") == "16:9" else "9:16",
                       # The boomerang fills its frame unless "blur" is chosen (owner's
                       # decision 15.11.2026); the film keeps the whole frame unless "crop".
                       "layout": ("blur" if f.get(f"{clip}_layout") == "blur" else "crop") if clip == "boomerang"
                                 else ("crop" if f.get(f"{clip}_layout") == "crop" else "blur"),
                       "seconds": _num(f"{clip}_seconds", 2 if clip == "boomerang" else 8, float, 1,
                                       10 if clip == "boomerang" else 60),
                       "fps": _num(f"{clip}_fps", 20 if clip == "boomerang" else 25, int, 5, 60),
                       "width": "auto", "max_width": 1080,
                       "scale": f.get(f"{clip}_scale") if f.get(f"{clip}_scale") in dict(SCALES) else "native",
                       # "camera": the camera films onto its card (higher resolution);
                       # anything else is the live view, the default.
                       "source": "camera" if f.get(f"{clip}_source") == "camera" else "live"}
            if clip == "boomerang":
                p[clip]["loops"] = _num("boomerang_loops", 3, int, 1, 10)
                p[clip]["guest_choice"] = bool(f.get("boomerang_guest_choice"))
                p[clip]["print"] = bool(f.get("boomerang_print"))
            p[clip].update(_frame_fields(f, clip))
        p["gif"] = {"shots": _num("gif_shots", 6, int, 2, 10), "interval": _num("gif_interval", 1.0, float, 0.5, 10),
                    "frame_ms": _num("gif_frame_ms", 200, int, 80, 3000),
                    "video_seconds": _num("gif_video_seconds", 15, int, 0, 60),
                    "pingpong": bool(f.get("gif_pingpong")), "loops": 0, "also_mp4": bool(f.get("gif_mp4")),
                    "format": f.get("gif_format") if f.get("gif_format") in dict(FORMATS) else "4:5",
                    "scale": f.get("gif_scale") if f.get("gif_scale") in dict(SCALES) else "native",
                    "max_width": 1080,
                    # Zugabe zur Collage: dieselben Aufnahmen, zusätzlich als GIF.
                    "from_collage": bool(f.get("gif_from_collage")),
                    "from_collage_min": _num("gif_from_collage_min", 4, int, 2, 8),
                    # Own sheet for printing a GIF run — only a template the
                    # gallery lists, by its file name; anything else = built-in.
                    "print_template": next((a["file"] for a in ecosystem.list_assets("template")
                                            if a["file"] == (f.get("gif_print_template") or "").strip()), ""),
                    # Boomerang-Fassung fürs Handy, mit eigenen Maßen.
                    "boomerang": {
                        "enabled": bool(f.get("gifboom_enabled")),
                        "format": f.get("gifboom_format") if f.get("gifboom_format") in dict(FORMATS)
                                  or f.get("gifboom_format") == "16:9" else "9:16",
                        "layout": "crop" if f.get("gifboom_layout") == "crop" else "blur",
                        "fps": _num("gifboom_fps", 15, int, 5, 60),
                        "loops": _num("gifboom_loops", 3, int, 1, 10),
                        "max_width": 1080, "scale": "native",
                        "guest_choice": bool(f.get("gifboom_choice"))}}
        p["looks"] = [x for x, _ in LOOKS if f.get(f"look_{x}")] or ["original"]
        # Wie viele davon der Gast sieht — und ob er überhaupt wählen soll.
        p["looks_mode"] = f.get("looks_mode") if f.get("looks_mode") in ("ask", "auto", "off") else "ask"
        p["looks_max"] = _num("looks_max", 0, int, 0, 24)
        p["looks_auto"] = f.get("looks_auto") if f.get("looks_auto") in dict(LOOKS) else "original"
        lut_ids = _lut_choices()
        # A form opened before the 0.3 rename still posts the old keys.
        lut_show = [ecosystem.canonical_lut(x) for x in f.getlist("lut_show")]
        lut_baked = ecosystem.canonical_lut(f.get("lut_baked") or "")
        p["luts"] = {"enabled": bool(f.get("luts_enabled")),
                     "strength": _num("lut_strength", 1.0, float, 0, 1),
                     "show": [x for x in lut_show if x in lut_ids],
                     "baked": lut_baked if lut_baked in lut_ids else "",
                     "baked_strength": _num("lut_baked_strength", 1.0, float, 0, 1)}
        scene_files = {a["file"] for a in ecosystem.list_assets() if a["kind"] in ("scene", "background")}
        colours = [c.strip() for c in (f.get("chroma_colors") or "").splitlines()
                   if re.fullmatch(r"#[0-9a-fA-F]{6}", c.strip())]
        p["chroma"] = {"enabled": bool(f.get("chroma_enabled")),
                       "guest_choice": bool(f.get("chroma_guest_choice")),
                       "color": f.get("chroma_color") if f.get("chroma_color") in ("auto", "green", "blue") else "auto",
                       "tolerance": _num("chroma_tolerance", 0.35, float, 0.05, 1),
                       "softness": _num("chroma_softness", 0.08, float, 0, 0.5),
                       "spill": _num("chroma_spill", 0.4, float, 0, 1), "fit": "cover",
                       "backgrounds": [b for b in f.getlist("chroma_backgrounds") if b in scene_files] + colours}
        old_mail = prof.get("mail") or {}
        p["mail"] = {
            "transport": "smtp" if f.get("mail_transport") == "smtp" else "gallery",
                     "enabled": bool(f.get("mail_enabled")), "host": f.get("mail_host", "").strip()[:200],
                     "port": _num("mail_port", 587, int, 1, 65535),
                     "security": f.get("mail_security") if f.get("mail_security") in ("starttls", "ssl", "none") else "starttls",
                     "user": f.get("mail_user", "").strip()[:200],
                     "password": f.get("mail_password") or old_mail.get("password", ""),
                     "from_addr": f.get("mail_from_addr", "").strip()[:200],
                     "from_name": f.get("mail_from_name", "").strip()[:100],
                     "subject": f.get("mail_subject", "").strip()[:200],
                     "body": f.get("mail_body", "").strip()[:2000],
                     "attach": f.get("mail_attach") if f.get("mail_attach") in ("web", "original", "none") else "web",
                     "bcc": old_mail.get("bcc", ""), "dry_run": bool(f.get("mail_dry_run"))}
        privacy_mode = f.get("privacy_mode") if f.get("privacy_mode") in ("normal", "print_only") else "normal"
        p["privacy"] = {
            "mode": privacy_mode,
            "delete_after": f.get("privacy_delete_after") if f.get("privacy_delete_after") in ("print", "done", "seconds") else "print",
            "delete_seconds": _num("privacy_delete_seconds", 120, int, 10, 3600),
            "notice": f.get("privacy_notice", "").strip()[:400],
        }
        if privacy_mode == "print_only":
            # Der Modus überträgt nichts — dann darf auch kein Event daran hängen,
            # das auf Bilder wartet. Lieber einmal deutlich sagen als still schlucken.
            if f.get("event_id"):
                flash("Datensparsamer Betrieb: Diese Box lädt nichts hoch — das zugewiesene Event "
                      "bleibt leer. Das ist so gewollt, sonst Event entfernen.", "warning")
            if not p["print"]["enabled"]:
                flash("Datensparsamer Betrieb ohne Drucken: Die Gäste bekommen dann gar nichts.", "warning")
        p["mirror"] = {"horizontal": bool(f.get("mirror_horizontal")),
                       "vertical": bool(f.get("mirror_vertical"))}
        p["qr"] = {"mode": f.get("qr_mode") if f.get("qr_mode") in ("auto", "gallery", "custom", "off") else "auto",
                   "url": f.get("qr_url", "").strip()[:300], "utm": f.get("qr_utm", "").strip()[:300],
                   "label": (prof.get("qr") or {}).get("label", "")}
        # Download without internet (Wi-Fi at the booth, guest page served by the booth).
        p["wifi_share"] = {
            "enabled": bool(f.get("wifi_enabled")),
            "mode": f.get("wifi_mode") if f.get("wifi_mode") in ("auto", "always", "off") else "auto",
            "ssid": (f.get("wifi_ssid") or "").strip()[:32],
            "password": (f.get("wifi_password") or "")[:63],
            "security": f.get("wifi_security") if f.get("wifi_security") in ("WPA", "WEP", "nopass") else "WPA",
            "hidden": bool(f.get("wifi_hidden")),
            "link_hours": _num("wifi_link_hours", 24, int, 1, 168),
        }
        frame_files = _asset_files("frame", "overlay")
        old_lines = [l for l in ((prof.get("frame") or {}).get("texts") or []) if isinstance(l, dict)]
        lines = []
        for i in range(FRAME_LINES):
            value = f.get(f"frame_text{i}_value", "").strip()[:120]
            if not value:
                continue                      # leere Zeile = keine Zeile
            colour = (f.get(f"frame_text{i}_color") or "").strip()
            align, target = f.get(f"frame_text{i}_align"), f.get(f"frame_text{i}_target")
            old_line = old_lines[i] if i < len(old_lines) else {}
            lines.append({"value": value,
                          "x": _num(f"frame_text{i}_x", 50, float, 0, 100),
                          "y": _num(f"frame_text{i}_y", 92, float, 0, 100),
                          "size": _num(f"frame_text{i}_size", 4, float, 1, 40),
                          "color": colour if re.fullmatch(r"#[0-9a-fA-F]{6}", colour) else "#ffffff",
                          "align": align if align in dict(FRAME_ALIGNS) else "center",
                          "target": target if target in dict(FRAME_TARGETS) else "both",
                          "bold": bool(f.get(f"frame_text{i}_bold")),
                          "shadow": bool(old_line.get("shadow", True))})
        p["frame"] = {"enabled": bool(f.get("frame_enabled")),
                      "overlay": f.get("frame_overlay") if f.get("frame_overlay") in frame_files else "",
                      "print_overlay": f.get("frame_print_overlay") if f.get("frame_print_overlay") in frame_files else "",
                      "on_photo": bool(f.get("frame_on_photo")), "on_print": bool(f.get("frame_on_print")),
                      "texts": lines}
        p["print_qr"] = {"size": _num("print_qr_size", 220, int, 60, 600),
                         "position": f.get("print_qr_position") if f.get("print_qr_position") in dict(QR_CORNERS) else "bottom-right",
                         "margin": _num("print_qr_margin", 40, int, 0, 400)}
        sound_files = _asset_files("sound")
        p["sounds"] = {"enabled": bool(f.get("sounds_enabled")), "volume": _num("sounds_volume", 0.8, float, 0, 1),
                       **{k: (f.get(f"sound_{k}") if f.get(f"sound_{k}") in sound_files else "") for k, _ in SOUND_KEYS}}
        attract_color = f.get("attract_color", "").strip()
        attract_text_color = f.get("attract_text_color", "").strip()
        p["attract"] = {"enabled": bool(f.get("attract_enabled")),
                        "after_seconds": _num("attract_after", 120, int, 10, 3600),
                        "source": f.get("attract_source") if f.get("attract_source") in dict(ATTRACT_SOURCES) else "recent",
                        "media": f.getlist("attract_media"),
                        "seconds_per_item": _num("attract_seconds", 6, int, 2, 120),
                        "show_hint": bool(f.get("attract_show_hint")),
                        "hint_style": f.get("attract_hint_style") if f.get("attract_hint_style") in dict(ATTRACT_HINTS) else "bar",
                        "color": attract_color if re.fullmatch(r"#[0-9a-fA-F]{6}", attract_color) else "#000000",
                        "text_color": attract_text_color if re.fullmatch(r"#[0-9a-fA-F]{6}", attract_text_color) else "#ffffff",
                        "dim": _num("attract_dim", 25, int, 0, 80),
                        "fit": f.get("attract_fit") if f.get("attract_fit") in dict(ATTRACT_FITS) else "auto",
                        "mute": bool(f.get("attract_mute")),
                        "hint_size": _num("attract_hint_size", 100, int, 100, 250)}
        old_hook = prof.get("webhooks") or {}
        hook_url = f.get("webhook_url", "").strip()[:300]
        if hook_url and not re.match(r"https?://", hook_url):
            flash("Webhook-Adresse muss mit http:// oder https:// beginnen — ignoriert.", "warning")
            hook_url = ""
        p["webhooks"] = {"enabled": bool(f.get("webhooks_enabled")), "url": hook_url,
                         # Geheimnis wie das Mail-Passwort: leeres Feld lässt das gespeicherte stehen.
                         "secret": f.get("webhook_secret", "").strip()[:200] or old_hook.get("secret", ""),
                         "events": [e for e, _ in WEBHOOK_EVENTS if f.get(f"webhook_event_{e}")]}
        trigger_on = bool(f.get("http_trigger_enabled"))
        token = f.get("http_trigger_token", "").strip()[:200] or (prof.get("http_trigger") or {}).get("token", "")
        if trigger_on and not token:
            token = secrets.token_urlsafe(24)
            flash(f"Trigger-Token erzeugt: {token} — jetzt notieren, es wird hier nicht wieder angezeigt.", "success")
        trigger_mode = f.get("http_trigger_mode")
        p["http_trigger"] = {"enabled": trigger_on, "token": token,
                             # Die Box lehnt einen Modus ab, den sie gar nicht anbietet.
                             "mode": trigger_mode if trigger_mode in p["modes"] else p["modes"][0]}
        positions = {}
        try:
            for key, v in (json.loads(f.get("start_positions") or "{}") or {}).items():
                if re.fullmatch(r"[a-z_]{1,20}", str(key)) and isinstance(v, dict):
                    positions[key] = {k: round(min(100.0, max(0.0, float(v.get(k, 0)))), 2) for k in ("x", "y", "w") if k in v}
        except (ValueError, TypeError, AttributeError):
            positions = {}
        p["camera"] = {k: f.get(f"cam_{k}", "").strip()[:12] for k in ("iso", "aperture", "shutter", "wb")}
        # The live view's own exposure (used only with the flash): a value from
        # the lists, else "auto" for the ISO and "same as the shot" for the rest.
        _vorschau_iso = f.get("cam_preview_iso", "auto").strip()
        p["camera"]["preview_iso"] = _vorschau_iso if _vorschau_iso in PREVIEW_ISO else "auto"
        for _k, _liste in (("aperture", PREVIEW_APERTURE), ("shutter", PREVIEW_SHUTTER)):
            _v = f.get(f"cam_preview_{_k}", "").strip()
            p["camera"][f"preview_{_k}"] = _v if _v in _liste else ""
        _v = f.get("cam_preview_iso_max", "").strip()
        p["camera"]["preview_iso_max"] = _v if _v in PREVIEW_ISO_MAX else "12800"
        _v = f.get("cam_preview_shutter_min", "").strip()
        p["camera"]["preview_shutter_min"] = _v if _v in PREVIEW_SHUTTER_MIN else "1/30"
        p["start"] = {"media": f.getlist("start_media"), "media_seconds": _num("start_media_seconds", 8, int, 2, 120),
                      "positions": positions,
                      "layout": f.get("start_layout") if f.get("start_layout") in ("bottom", "left", "right", "center", "custom") else "bottom",
                      "button_size": f.get("button_size") if f.get("button_size") in ("m", "l", "xl") else "l",
                      "show_gallery_button": bool(f.get("show_gallery_button")),
                      "gallery_style": "prominent" if f.get("gallery_style") == "prominent" else "pill",
                      "corner_size": f.get("corner_size") if f.get("corner_size") in dict(KIOSK_SIZES) else "l",
                      "show_mode_hints": bool(f.get("show_mode_hints")),
                      # Anleitung, letzte Aufnahme, QR zur Galerie, Logo
                      "steps": f.get("start_steps") if f.get("start_steps") in ("aus", "immer", "knopf") else "aus",
                      "show_last": bool(f.get("start_last")),
                      "show_qr": bool(f.get("start_qr")),
                      "logo": (f.get("start_logo") or "").strip()[:120],
                      "logo_size": _num("start_logo_size", 18, int, 4, 50),
                      "bg_color": _hexfarbe(f.get("start_bg_color"), ""),
                      # -1 = the old gradient; 0–80 = a flat dim
                      "dim": _num("start_dim", -1, int, -1, 80),
                      "show_title": bool(f.get("start_show_title")),
                      "show_status": bool(f.get("start_show_status")),
                      "fit": f.get("start_fit") if f.get("start_fit") in ("drift", "cover", "contain") else "drift"}
        # Backdrop per screen: a picture from the media or a colour, plus a dim.
        backdrop_files = {a for a in _asset_files(*BACKDROP_KINDS) if _is_picture(a)}

        def _backdrop(screen: str) -> dict:
            image = (f.get(f"bd_{screen}_image") or "").strip()
            return {"image": image if image in backdrop_files else "",
                    "color": _hexfarbe(f.get(f"bd_{screen}_color"), ""),
                    "dim": _num(f"bd_{screen}_dim", 0, int, 0, 80)}
        p["backdrops"] = {screen: _backdrop(screen) for screen, _ in BACKDROP_SCREENS}
        # Medium per phase of the run; only "after" has a duration (0 = off).
        phase_files = {a for a in _asset_files(*BACKDROP_KINDS) if Path(a).suffix.lower() in PHASE_EXT}

        def _phase(key: str) -> dict:
            media = (f.get(f"pm_{key}_media") or "").strip()
            out = {"media": media if media in phase_files else "",
                   "fit": "contain" if f.get(f"pm_{key}_fit") == "contain" else "cover",
                   "text": bool(f.get(f"pm_{key}_text")), "loop": bool(f.get(f"pm_{key}_loop"))}
            if key == "after":
                out["seconds"] = _num("pm_after_seconds", 0, int, 0, 15)
            return out
        p["phase_media"] = {key: _phase(key) for key, _ in PHASE_SCREENS}
        p["backgrounds"] = f.getlist("backgrounds")
        p["collage_templates"] = f.getlist("collage_templates")
        gewaehlt = [x for x in f.getlist("collage_builtins") if x in ecosystem.BUILTIN_COLLAGE_IDS]
        p["collage_exact"] = bool(f.get("collage_exact"))
        p["collage_plain_qr"] = bool(f.get("collage_plain_qr"))
        # Alle angehakt heißt dasselbe wie keine: alle anbieten. So steht in der
        # Datenbank kein Zustand, der beim nächsten Update falsch wäre. "All"
        # is the catalogue without the plain layouts, which come only ticked;
        # with "exactly these" nothing is collapsed.
        standard = [c["id"] for c in ecosystem.BUILTIN_COLLAGES if not c.get("plain")]
        p["collage_builtins"] = ([] if not p["collage_exact"] and sorted(gewaehlt) == sorted(standard)
                                 else gewaehlt)
        # Das Aussehen der Vorlagen: ein Anlass plus das, was der Betreiber
        # davon abweichen will.
        p["collage_upload"] = (f.get("collage_upload")
                               if f.get("collage_upload") in ("collage", "photos", "both") else "both")
        p["collage_style"] = ecosystem.clean_collage_style(
            {k: f.get("cs_" + k, "") for k in ecosystem.COLLAGE_STYLE_KEYS})
        p["print"] = {
            "reprint_from_gallery": bool(f.get("reprint_from_gallery")),
                      "enabled": bool(f.get("print_enabled")), "auto": bool(f.get("print_auto")),
                      "copies_max": _num("copies_max", 2, int, 1, 10), "limit_session": _num("limit_session", 2, int, 0, 99),
                      "limit_event": _num("limit_event", 0, int, 0, 5000), "qr_on_print": bool(f.get("qr_on_print")),
                      "seconds_per_print": _num("seconds_per_print", 47, int, 0, 600),
                      "sheets_per_cassette": _num("sheets_per_cassette", 0, int, 0, 2000),
                      "sharpen": f.get("print_sharpen") if f.get("print_sharpen") in ("off", "light", "medium", "strong") else "light",
                      "number_keys": bool(f.get("print_number_keys")),
                      "queue_while_empty": bool(f.get("print_queue_while_empty")),
                      "queue_max": _num("print_queue_max", 12, int, 1, 50),
                      "queue_max_hours": _num("print_queue_max_hours", 6, int, 1, 24),
                      "fit_to_paper": bool(f.get("print_fit_to_paper"))}
        paper = f.get("paper") if f.get("paper") in PAPER_IDS else "10x15"
        per_sheet = 2 if f.get("per_sheet") == "2" else 1
        p["print_layout"] = {
            "paper": paper, "per_sheet": per_sheet,
            "margin_mm": _num("print_margin", 0, float, 0, 20),
            "dpi": _num("print_dpi", 300, int, 150, 600),
            "fit": f.get("print_fit") if f.get("print_fit") in dict(FITS) else "cover",
            "rotate_to_fit": bool(f.get("rotate_to_fit")),
            "background": (f.get("print_bg") if re.fullmatch(r"#[0-9a-fA-F]{6}", f.get("print_bg", "")) else "#ffffff"),
        }
        if per_sheet == 2 and paper != "5x15":
            flash("Zwei Stück auf ein Blatt ergeben nur bei Streifen Sinn — Format auf Streifen gestellt.", "warning")
            p["print_layout"]["paper"] = "5x15"
        p["flash"] = {"enabled": bool(f.get("flash_enabled")),
                      "recycle_seconds": _num("flash_recycle", 2.5, float, 0, 30),
                      "preview_gain": _num("flash_gain", 0, int, 0, 100),
                      "preview_gain_auto": bool(f.get("flash_gain_auto")),
                      "expected_mode": f.get("flash_mode") if f.get("flash_mode") in dict(FLASH_MODES) else "any"}
        p["screen_flash"] = {"enabled": bool(f.get("sf_enabled")),
                             "brightness": _num("sf_brightness", 100, int, 20, 100),
                             "lead_ms": _num("sf_lead", 250, int, 0, 2000),
                             "hold_ms": _num("sf_hold", 400, int, 0, 2000),
                             "color": _hexfarbe(f.get("sf_color"), "#ffffff")}
        gif_interval = float(p.get("gif", {}).get("interval") or 0)
        if p["flash"]["enabled"] and "gif" in p["modes"] and gif_interval and gif_interval < p["flash"]["recycle_seconds"]:
            flash("GIF nimmt schneller auf, als der Blitz nachlädt — die späteren Bilder werden dunkel.", "warning")
        p["guest_choice"] = {"format": bool(f.get("choice_format")), "look": bool(f.get("choice_look")),
                             "collage_template": bool(f.get("choice_template")),
                             "background": bool(f.get("choice_background"))}
        p["qr_on_screen"] = bool(f.get("qr_on_screen"))
        p["result_seconds"] = _num("result_seconds", 45, int, 5, 600)
        p["idle_seconds"] = _num("idle_seconds", 60, int, 10, 1800)
        p["gallery_on_booth"] = bool(f.get("gallery_on_booth"))
        p["allow_delete"] = bool(f.get("allow_delete"))
        p["disclaimer"] = {"enabled": bool(f.get("disclaimer_enabled"))}
        # Consent as a choice per run. An emptied note falls back to the default
        # text: an empty line under the buttons would explain nothing.
        consent_default = ecosystem.DEFAULT_PROFILE["consent"]
        p["consent"] = {
            "enabled": bool(f.get("consent_enabled")),
            "print_only": bool(f.get("consent_print_only")),
            "days": _num("consent_days", int(consent_default["days"]), int, 1, 3650),
            "note": {l: " ".join((f.get(f"consent_note_{l}") or "").split())[:400] or consent_default["note"][l]
                     for l in ("de", "en")},
        }
        # The field is masked and never filled: empty keeps the stored PIN, and
        # so does a PIN that is refused. Only "access_pin_clear" hands the box
        # back its own PIN (an empty access.pin, T-C4).
        bisher_pin = str((prof.get("access") or {}).get("pin") or "")
        roh_pin = f.get("access_pin", "").strip()
        pin = re.sub(r"\D", "", roh_pin)[:8]
        if roh_pin and (not pin or pin != roh_pin):
            flash("PIN darf nur Ziffern enthalten — nicht übernommen.", "warning")
            pin = bisher_pin
        # A PIN stored before the current rule stays as long as nobody edits it.
        weak = _weak_pin_reason(pin) if pin and pin != bisher_pin else ""
        if weak:
            flash(f"{weak} — nicht übernommen.", "warning")
            pin = bisher_pin
        if not roh_pin:
            pin = "" if f.get("access_pin_clear") else bisher_pin
        p["access"] = {"pin": pin,
                       "lock_on_booth": bool(f.get("lock_on_booth")),
                       "hotspot_seconds": _num("hotspot_seconds", 3, int, 1, 15),
                       "lock_icon": bool(f.get("lock_icon"))}
        def _uhrzeit(name: str) -> str:
            """„19:00" oder leer — alles andere wäre eine Falle im Betrieb."""
            wert = (f.get(name) or "").strip()
            if not wert:
                return ""
            teile = wert.split(":")
            try:
                stunde, minute = int(teile[0]), int(teile[1]) if len(teile) > 1 else 0
            except (ValueError, IndexError):
                return ""
            return f"{max(0, min(23, stunde)):02d}:{max(0, min(59, minute)):02d}"

        p["lock"] = {"active": bool(f.get("lock_active")), "image": f.get("lock_image") or None,
                     "schedule": {"enabled": bool(f.get("lock_schedule")),
                                  "from": _uhrzeit("lock_from"), "until": _uhrzeit("lock_until"),
                                  "days": [int(x) for x in f.getlist("lock_days") if x.isdigit() and int(x) < 7]}}
        # Auslösen: Buzzer, Tastatur, Touch
        def _keys(name: str, fallback: list) -> list:
            raw = [k.strip() for k in (f.get(name) or "").replace(",", " ").split() if k.strip()]
            # Browser-Tastennamen (KeyboardEvent.code) fangen groß an: Space,
            # Enter, KeyP, F13, Digit1, NumpadEnter. Alles andere ist ein
            # Vertipper und würde stillschweigend nie auslösen.
            good = [k for k in raw if re.fullmatch(r"[A-Z][A-Za-z0-9]{1,19}", k)]
            return good or fallback
        c = ecosystem.DEFAULT_PROFILE["controls"]

        def _aktion(wert) -> str:
            """Eine Aufgabe für einen Knopf — oder ‚off‘, wenn sie es nicht gibt.

            Lieber nichts tun als etwas Falsches: Ein Vertipper wie
            ``mode:kollage`` wird abgewiesen, statt still ins Leere zu laufen.
            """
            wert = str(wert or "off").strip()
            if wert in ("off", "trigger", "back", "print", "gallery", "next", "prev", "select"):
                return wert
            if wert.startswith("mode:") and wert[5:] in [m for m, _ in MODES]:
                return wert
            return "off"

        def _tastenaktionen(roh) -> dict:
            """``KeyF=mode:photo KeyG=mode:gif`` → Zuordnung. Unfug fällt raus."""
            aus = {}
            for stueck in str(roh or "").replace(",", " ").split():
                taste, _, aufgabe = stueck.partition("=")
                if not re.fullmatch(r"[A-Z][A-Za-z0-9]{1,19}", taste):
                    continue
                gepruefte = _aktion(aufgabe)
                if gepruefte != "off":
                    aus[taste] = gepruefte
            return dict(list(aus.items())[:12])

        p["controls"] = {
            "touch": bool(f.get("ctl_touch")),
            "keys_start": _keys("keys_start", c["keys_start"]),
            "keys_back": _keys("keys_back", c["keys_back"]),
            "keys_print": _keys("keys_print", c["keys_print"]),
            "mouse_start": f.get("mouse_start") if f.get("mouse_start") in ("off", "left", "right", "middle", "any") else "off",
            "mouse_swallow": bool(f.get("mouse_swallow")),
            "hide_cursor": bool(f.get("hide_cursor")),
            "onscreen_keyboard": bool(f.get("onscreen_keyboard")),
            "repeat_ms": _num("repeat_ms", 1200, int, 0, 5000),
            "start_mode": f.get("start_mode") if f.get("start_mode") in [m for m, _ in MODES] else "",
            "mouse_right": _aktion(f.get("mouse_right")),
            "mouse_middle": _aktion(f.get("mouse_middle")),
            "keys_actions": _tastenaktionen(f.get("keys_actions", "")),
            "arrow_navigation": bool(f.get("arrow_navigation")),
        }
        # Bildschirm: Ränder, Größe, Ecke für die Verwaltung
        p["layout"] = {
            "safe_top": _num("safe_top", 0, int, 0, 20),
            "safe_right": _num("safe_right", 0, int, 0, 20),
            "safe_bottom": _num("safe_bottom", 0, int, 0, 20),
            "safe_left": _num("safe_left", 0, int, 0, 20),
            "scale": _num("ui_scale", 100, int, 70, 140),
            "corner": f.get("corner") if f.get("corner") in ("top-left", "top-right", "bottom-left", "bottom-right") else "top-right",
            "guides": bool(f.get("guides")),
            "gallery_size": f.get("gallery_size") if f.get("gallery_size") in dict(KIOSK_SIZES) else "m",
            "back_size": f.get("back_size") if f.get("back_size") in dict(KIOSK_SIZES) else "m",
        }
        p["upload_originals"] = bool(f.get("upload_originals"))
        # Once only at the booth, where the next sync overwrote them with the
        # gallery's value (the booth takes the gallery's profile whole). Same
        # rules as the booth reads them (aufraeumen.py, meldungen.py); the
        # number bounds are the booth editor's (PROFILE_LIMITS / LIMITS).
        p["collage_fit"] = f.get("collage_fit") if f.get("collage_fit") in ("auto", "beschneiden", "einpassen") else "auto"
        p["aufraeumen"] = {
            "enabled": bool(f.get("aufraeumen_enabled")),
            "nach_tagen": _num("aufraeumen_nach_tagen", 14, int, 1, 365),
            "nur_bestaetigte": bool(f.get("aufraeumen_nur_bestaetigte")),
            "behalten_mindestens": _num("aufraeumen_behalten_mindestens", 20, int, 0, 1000),
        }
        stufen = ("info", "warnung", "kritisch")
        p["meldungen"] = {
            "mail": {"enabled": bool(f.get("meldungen_mail_enabled")),
                     "to": f.get("meldungen_mail_to", "").strip()[:200],
                     "ab_stufe": f.get("meldungen_mail_ab_stufe") if f.get("meldungen_mail_ab_stufe") in stufen else "warnung"},
            "box": {"enabled": bool(f.get("meldungen_box_enabled")),
                    "ab_stufe": f.get("meldungen_box_ab_stufe") if f.get("meldungen_box_ab_stufe") in stufen else "kritisch"},
            "wiederholen_minuten": _num("meldungen_wiederholen_minuten", 60, int, 0, 1440),
        }
        # Aussehen: Farben und Form der Bedienelemente
        def _color(name: str, fallback: str) -> str:
            return _hexfarbe(f.get(name), fallback)

        def _pick(name: str, allowed, fallback: str) -> str:
            v = f.get(name, "").strip()
            return v if v in allowed else fallback

        # Fallbacks are the shipped theme, not colours of an older design
        # (they were the red "#e5484d" set while the default had become amber).
        dt = ecosystem.DEFAULT_PROFILE["theme"]
        p["theme"] = {
            "mode": _pick("theme_mode", {"dunkel", "hell", "eigene"}, dt["mode"]),
            "accent": _color("theme_accent", dt["accent"]),
            "on_accent": _color("theme_on_accent", dt["on_accent"]),
            "bg": _color("theme_bg", dt["bg"]),
            "panel": _color("theme_panel", dt["panel"]),
            "text": _color("theme_text", dt["text"]),
            "muted": _color("theme_muted", dt["muted"]),
            "radius": _pick("theme_radius", THEME_RADII_IDS, "pill"),
            "size": _pick("theme_size", THEME_SIZE_IDS, "m"),
            "weight": _pick("theme_weight", THEME_WEIGHT_IDS, "600"),
            "uppercase": bool(f.get("theme_uppercase")),
            "secondary": _pick("theme_secondary", THEME_SECONDARY_IDS, "solid"),
            "font": _pick("theme_font", THEME_FONT_IDS, "system"),
            "font_file": f.get("theme_font_file") if f.get("theme_font_file") in _asset_files("font") else "",
        }
        p["accent"] = p["theme"]["accent"]   # alte Fassungen der Box lesen noch dieses Feld
        p["qr_url_template"] = f.get("qr_url_template", "").strip()[:300]
        texts = {}
        for lang in p["languages"]:
            for key, _ in TEXT_KEYS:
                val = f.get(f"text_{lang}_{key}", "").strip()
                if val:
                    texts.setdefault(lang, {})[key] = val[:400]
        extra = f.get("texts_json", "").strip()
        if extra:
            try:
                for lang, vals in json.loads(extra).items():
                    if isinstance(vals, dict):
                        texts.setdefault(lang, {}).update({str(k): str(v)[:400] for k, v in vals.items()})
            except ValueError:
                flash("Zusätzliche Texte: ungültiges JSON, ignoriert.", "warning")
        p["texts"] = texts
        event_id = f.get("event_id")
        try:
            ecosystem.save_device_profile(device_id, p, int(event_id) if event_id else None)
        except ecosystem.ProfileError as e:
            flash(str(e), "error")
            return redirect(url_for("admin_booth.booth_profile", device_id=device_id))
        flash("Gespeichert. Die Box übernimmt das beim nächsten Kontakt.", "success")
        return redirect(url_for("admin_booth.booth_profile", device_id=device_id))

    device = db.get_device_by_id(device_id)
    # Looks saved under a name from before 0.3 show up ticked under the new one.
    lut_cfg = dict(prof.get("luts") or {})
    lut_cfg["show"] = [ecosystem.canonical_lut(x) for x in lut_cfg.get("show") or []]
    lut_cfg["baked"] = ecosystem.canonical_lut(lut_cfg.get("baked") or "")
    prof = dict(prof, luts=lut_cfg)
    texts_extra = {lang: {k: v for k, v in vals.items() if k not in dict(TEXT_KEYS)} for lang, vals in (prof.get("texts") or {}).items()}
    texts_extra = {k: v for k, v in texts_extra.items() if v}
    return render_template(
        "admin/booth_profile.html", device=device, p=prof, events=db.list_events() or [],
        heute=_heute(device_id), motion_offer=frame_store.offer(_assets_dir()),
        assets=ecosystem.list_assets(), modes=MODES, formats=FORMATS, looks=LOOKS, text_keys=TEXT_KEYS,
        text_groups=TEXT_GROUPS, shipped=booth_texts.shipped, scales=SCALES,
        theme_radii=THEME_RADII, theme_sizes=THEME_SIZES, theme_weights=THEME_WEIGHTS,
        theme_secondary=THEME_SECONDARY, theme_fonts=THEME_FONTS, theme_presets=THEME_PRESETS,
        flash_modes=FLASH_MODES, papers=PAPERS, print_fits=FITS, preview_iso=PREVIEW_ISO,
        preview_aperture=PREVIEW_APERTURE, preview_shutter=PREVIEW_SHUTTER,
        preview_iso_max=PREVIEW_ISO_MAX, preview_shutter_min=PREVIEW_SHUTTER_MIN,
        builtin_collages=ecosystem.BUILTIN_COLLAGES,
        start_builtins=ecosystem.START_BUILTINS,
        # Was der angeschlossene Drucker wirklich kann. Leer = unbekannt, dann
        # bleibt es bei der vollen Liste.
        drucker_formate=papers_for((db.get_device_status(device_id) or {}).get("printer_name", "")),
        drucker_name=(db.get_device_status(device_id) or {}).get("printer_name", ""),
        font_files=_asset_files('font'),
        # Ein Logo kann als Overlay oder als Startmedium hochgeladen worden
        # sein — beides steht hier zur Wahl, damit niemand neu hochladen muss.
        logo_files=sorted(_asset_files('overlay') | _asset_files('start')),
        backdrop_screens=BACKDROP_SCREENS,
        backdrop_files=sorted(a for a in _asset_files(*BACKDROP_KINDS) if _is_picture(a)),
        phase_screens=PHASE_SCREENS,
        phase_files=sorted(a for a in _asset_files(*BACKDROP_KINDS) if Path(a).suffix.lower() in PHASE_EXT),
        lut_choices=_lut_choices(), frame_lines=FRAME_LINES, frame_aligns=FRAME_ALIGNS, frame_targets=FRAME_TARGETS,
        qr_corners=QR_CORNERS, sound_keys=SOUND_KEYS, attract_sources=ATTRACT_SOURCES, attract_hints=ATTRACT_HINTS, attract_fits=ATTRACT_FITS, kiosk_sizes=KIOSK_SIZES, webhook_events=WEBHOOK_EVENTS,
        languages=prof.get("languages") or languages, jobs=[_job_view(j) for j in ecosystem.recent_jobs(device_id)], job_types=ecosystem.JOB_TYPES,
        hardware_groups=HARDWARE_GROUPS,
        status_page_since=_status_page_since(device_id),
        second_state=_second_state_of(device_id),
        silence=_silence_view(device_id),
        status=db.get_device_status(device_id), texts_extra=json.dumps(texts_extra, ensure_ascii=False, indent=2) if texts_extra else "",
        active_nav="booths", csrf_token=_csrf_token())


def _status_page_since(device_id: int):
    """When the booth's public status link was made; None while it is off."""
    from app import status_page
    return status_page.created_at(device_id)


def _silence_view(device_id: int):
    """The card "Warnen, wenn die Box nicht mehr antwortet"."""
    from app import silence_alarm
    try:
        return silence_alarm.view(device_id)
    except Exception:                                 # noqa: BLE001 — a card costs no page
        current_app.logger.exception("Funkstille-Warnung nicht lesbar")
        return None


def _heute(device_id: int):
    """The booth's counters of its latest reported day, for the card "Heute an der Box"."""
    from app import stats as stats_mod
    try:
        return stats_mod.booth_today(device_id)
    except Exception:                                 # noqa: BLE001 — a card costs no page
        current_app.logger.exception("Abendzahlen nicht lesbar")
        return None


@bp.get("/devices/<int:device_id>/booth/export")
def booth_profile_export(device_id: int):
    """Profil dieser Box als JSON — zum Sichern und um eine zweite Box gleich einzustellen."""
    redir = _require_admin()
    if redir:
        return redir
    ecosystem.init_schema()
    device = db.get_device_by_id(device_id)
    if not device:
        abort(404)
    body = json.dumps(_without_secrets(ecosystem.device_profile(device)), ensure_ascii=False, indent=2)
    name = secure_filename(device.get("name") or "") or f"box-{device_id}"
    return Response(body + "\n", mimetype="application/json",
                    headers={"Content-Disposition": f'attachment; filename="profil-{name}.json"',
                             "Cache-Control": "no-store"})


@bp.post("/devices/<int:device_id>/booth/import")
def booth_profile_import(device_id: int):
    """Profil aus einer Datei übernehmen. Event, Passwort und Geheimnisse dieser Box bleiben."""
    redir = _require_admin()
    if redir:
        return redir
    ecosystem.init_schema()
    device = db.get_device_by_id(device_id)
    if not device:
        abort(404)
    back = redirect(url_for("admin_booth.booth_profile", device_id=device_id) + "#profil-datei")
    up = request.files.get("profile")
    if not up or not up.filename:
        flash("Keine Datei gewählt.", "warning")
        return back
    raw = up.read(PROFILE_MAX_BYTES + 1)     # ein Profil sind ein paar Kilobyte, nie ein Megabyte
    if len(raw) > PROFILE_MAX_BYTES:
        flash("Die Datei ist zu groß für ein Profil.", "error")
        return back
    try:
        data = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        flash("Das ist keine lesbare JSON-Datei.", "error")
        return back
    if not isinstance(data, dict):
        flash("Die Datei enthält kein Profil — erwartet wird ein JSON-Objekt.", "error")
        return back
    old = ecosystem.device_profile(device)
    # Nur bekannte Blöcke, und „version"/„event" stehen gar nicht erst darin:
    # die Version zählt der Server hoch, das Event hängt an dieser Box.
    new = ecosystem.deep_merge(old, {k: v for k, v in data.items() if k in ecosystem.DEFAULT_PROFILE})
    for block, key in SECRET_FIELDS:
        if not isinstance(new.get(block), dict):
            new[block] = dict(old.get(block) or {})
        if not new[block].get(key):
            new[block][key] = (old.get(block) or {}).get(key, "")
    try:
        ecosystem.save_device_profile(device_id, new, device.get("event_id"))
    except ecosystem.ProfileError as e:
        flash(str(e), "error")
        return back
    flash("Profil übernommen. Event, Passwort und Geheimnisse dieser Box sind unverändert.", "success")
    return back


@bp.post("/devices/<int:device_id>/job")
def booth_job(device_id: int):
    redir = _require_admin()
    if redir:
        return redir
    ecosystem.init_schema()
    jt = request.form.get("type", "")
    if jt in ("set_second_target", "backfill_second_target", "accept_second_target_host_key"):  # own card
        flash("Das zweite Ziel wird auf der Sync-Seite der Box eingestellt.", "error")
        return redirect(url_for("admin_booth.sync_device", device_id=device_id) + "#zweites-ziel")
    try:
        ecosystem.add_job(device_id, jt)
        flash(f"Auftrag „{ecosystem.JOB_TYPES.get(jt, jt)}“ wartet auf die Box.", "success")
    except ValueError:
        flash("Unbekannter Auftrag.", "error")
    return redirect(url_for("admin_booth.booth_profile", device_id=device_id) + "#fernwartung")


def _hardware_value(field: dict, raw: str):
    """(ok, value) for one field of the hardware card, typed as the box wants it."""
    kind = field["kind"]
    if kind == "bool":
        return (raw in ("ein", "aus")), raw == "ein"
    if kind == "int":
        try:
            return True, int(raw)
        except ValueError:
            return False, None
    if kind == "enum":
        for option, _label in field["choices"]:
            if str(option) == raw:
                return True, option           # keeps the type: rotate is 90, not "90"
        return False, None
    return True, raw


@bp.post("/devices/<int:device_id>/booth/hardware")
def booth_hardware(device_id: int):
    """Send camera, printer and storage settings to the box as one job.

    Only fields that were filled in are sent: the gallery does not know what is
    set at the box now, and a field left alone must not overwrite it with a
    default. The whitelist is the box's own (``REMOTE_LOCAL_KEYS``); a field
    outside it is not even looked at.
    """
    redir = _require_admin()
    if redir:
        return redir
    ecosystem.init_schema()
    if not db.get_device_by_id(device_id):
        abort(404)
    settings, refused = {}, []
    for _group, fields in HARDWARE_GROUPS:
        for field in fields:
            raw = (request.form.get(f"hw_{field['id']}") or "").strip()
            if not raw:
                continue
            ok, value = _hardware_value(field, raw)
            if not ok:
                refused.append(field["label"])
                continue
            settings[field["key"]] = value
    clean, rejected = ecosystem.clean_remote_local(settings)
    by_key = {f["key"]: f["label"] for _g, fs in HARDWARE_GROUPS for f in fs}
    refused += [by_key.get(r.split(":", 1)[0], r) for r in rejected]
    for label in refused:
        flash(f"{label}: Dieser Wert ist nicht erlaubt und wurde nicht geschickt.", "warning")
    if clean:
        ecosystem.add_job(device_id, "set_local_config", clean)
        flash("Die Einstellungen warten auf die Box und gelten ab ihrem nächsten Kontakt. "
              "Kamera und Drucker erst nach „App neu starten“.", "success")
    elif not refused:
        flash("Nichts zu schicken: Alle Felder waren leer.", "warning")
    return redirect(url_for("admin_booth.booth_profile", device_id=device_id) + "#hardware")


# ── Sync-Programm aus der Ferne ───────────────────────────────

# Was diese Seite auslösen darf. Bewusst eine kurze Liste: die Fernwartung
# schickt Aufträge, keine Befehle — ein kompromittierter Admin-Zugang soll auf
# dem Event-PC nichts ausführen können, was hier nicht steht.
SYNC_JOBS = {"check_targets", "rescan", "retry_failed", "sync_now", "set_sync_config",
             "browse_remote", "restart_app",
             # The booth's queues on this page: single entries by id, "all" with confirm.
             "discard_prints", "discard_failed_uploads"}


def _second_state_of(device_id: int) -> dict:
    from app import second_target
    return second_target.state(db.get_device_status(device_id) or {})


@bp.get("/devices/<int:device_id>/sync")
def sync_device(device_id: int):
    """Alles, was das Sync-Programm über sich meldet — und die Knöpfe dazu."""
    redir = _require_admin()
    if redir:
        return redir
    ecosystem.init_schema()
    device = db.get_device_by_id(device_id)
    if not device:
        abort(404)
    report = ecosystem.sync_report(device_id)
    # A booth syncs by itself and sends no report of the separate sync
    # program; it still reports every 30 s. Without the light the page said
    # "has never reported" about a box that had just reported (register C4).
    from app import booth as booth_mod
    light = booth_mod.light(device) if device.get("last_heartbeat_at") else None
    status = db.get_device_status(device_id) or {}
    queues = {"prints": [j for j in status.get("print_jobs") or [] if isinstance(j, dict)],
              "failed": [f for f in status.get("failed_uploads") or [] if isinstance(f, dict)],
              "reasons": [r for r in status.get("failed_reasons") or [] if isinstance(r, dict)]}
    from app import second_target
    second = second_target.state(status)
    second_jobs = [_job_view(j) for j in ecosystem.recent_jobs(device_id, 20) if j.get("type") in second_target.JOBS][:3]
    profile_event = {}
    try:
        ev_row = db.get_event_by_id(device["event_id"]) if device.get("event_id") else None
        profile_event = {"slug": ev_row["slug"], "name": ev_row["name"]} if ev_row else {}
    except Exception:                       # noqa: BLE001 — the example falls back to a sample event
        profile_event = {}
    second_path_example = second_target.path_example(second.get("path") or "", second.get("flat", True)
                                                     if second.get("configured") else True, profile_event)
    second_changes = second_target.last_changes(device_id)
    return render_template("admin/sync_device.html", device=device, report=report, light=light, queues=queues,
                           jobs=[_job_view(j) for j in ecosystem.recent_jobs(device_id, 10)], csrf_token=_csrf_token(),
                           second=second, second_jobs=second_jobs, second_changes=second_changes,
                           second_path_example=second_path_example,
                           upload_counts=status.get("upload_targets") or {}, active_nav="devices")


#: The buttons of the second target card and what each one sends.
SECOND_TARGET_ACTIONS = ("save", "clear", "check", "backfill", "accept_host_key")


def _second_target_form(form) -> dict:
    """The card's fields as the booth's job wants them. The password is not part of this."""
    port_raw = str(form.get("port") or "").strip()
    if port_raw and not port_raw.isdigit():
        raise ValueError("Port: nur Ziffern (leer = üblich)")
    return {"type": str(form.get("type") or ""), "host": str(form.get("host") or ""),
            "port": int(port_raw) if port_raw else 0, "user": str(form.get("user") or ""),
            "path": str(form.get("path") or ""), "flat": form.get("flat") == "1",
            "enabled": form.get("enabled") == "1", "originals": form.get("originals") == "1",
            "print_sheets": form.get("print_sheets") == "1"}


@bp.post("/devices/<int:device_id>/sync/second-target")
def sync_second_target(device_id: int):
    """The card "Zweites Ziel im Netz" (app/second_target.py). Admin only, CSRF by the guard above.

    The password field is sealed here, before anything is written, to the
    key the booth reported; it is never stored, logged or shown in clear.
    """
    redir = _require_admin()
    if redir:
        return redir
    ecosystem.init_schema()
    device = db.get_device_by_id(device_id)
    if not device:
        abort(404)
    from app import second_target
    from app.i18n import t
    back = redirect(url_for("admin_booth.sync_device", device_id=device_id) + "#zweites-ziel")
    action = request.form.get("action", "")
    if action not in SECOND_TARGET_ACTIONS:
        abort(400)
    status = db.get_device_status(device_id) or {}
    st = second_target.state(status)
    if not st["supported"]:
        flash(t("sync.second_target.too_old"), "error")
        return back
    try:
        if action == "save":
            fields = _second_target_form(request.form)
            password = request.form.get("password") or None
            second_target.set_job(device_id, status, fields, password=password)
            # Rule B4: who (the change log knows the session), which fields, never the password.
            g._protokoll_was = second_target.audit_text(device_id, st, second_target.clean_fields(fields),
                                                        password_given=bool(password), source="Formular")
            if second_target.drops_password(st, second_target.clean_fields(fields), bool(password)) \
                    and (st.get("password_set") or not st.get("configured")):
                flash(t("sync.second_target.sent_needs_password"), "warning")
            else:
                flash(t("sync.second_target.sent"), "success")
        elif action == "clear":
            second_target.set_job(device_id, status, clear=True)
            g._protokoll_was = second_target.audit_text(device_id, st, None, source="Formular", action="clear")
            flash(t("sync.second_target.cleared"), "success")
        elif action == "accept_host_key":
            # Its own button, never part of saving the form (A-3c); never through MCP.
            second_target.accept_host_key_job(device_id, status, str(request.form.get("fingerprint") or ""))
            g._protokoll_was = second_target.audit_text(device_id, st, None, source="Formular",
                                                        action="accept_host_key")
            flash(t("sync.second_target.host_key_sent"), "success")
        elif action == "check":
            ecosystem.add_job(device_id, "check_second_target", {})
            g._protokoll_was = "Zweites Ziel prüfen"
            flash(t("sync.second_target.check_sent"), "success")
        else:
            ecosystem.add_job(device_id, "backfill_second_target", {})
            g._protokoll_was = "Bisherige Aufnahmen an das zweite Ziel nachsenden"
            flash(t("sync.second_target.backfill_sent"), "success")
        g._protokoll_ergebnis = f"Gerät #{device_id}"
    except (ValueError, PermissionError, LookupError) as e:
        flash(t("sync.second_target.not_sent", reason=str(e)), "error")
    return back


@bp.post("/devices/<int:device_id>/sync/job")
def sync_job(device_id: int):
    redir = _require_admin()
    if redir:
        return redir
    ecosystem.init_schema()
    body = request.get_json(silent=True) or {}
    jt = str(body.get("type") or "")
    if jt not in SYNC_JOBS:
        return {"ok": False, "error": "unbekannter Auftrag"}, 400
    payload = body.get("payload") if isinstance(body.get("payload"), dict) else {}
    if jt == "set_sync_config":
        # Target passwords are sealed to the PC's key here, before anything is
        # written; a version without a key gets none (app/sync_secrets.py).
        from app import sync_secrets
        try:
            payload = sync_secrets.seal_payload(payload, ecosystem.sync_report(device_id))
        except sync_secrets.TooOld as e:
            return {"ok": False, "error": str(e)}, 409
        except ValueError as e:
            return {"ok": False, "error": str(e)}, 400
        if "targets" in payload:
            con = db.get_db()
            sync_secrets.supersede_open(con, device_id)
            con.commit()
            con.close()
    return {"ok": True, "job_id": ecosystem.add_job(device_id, jt, payload)}


#: What a job's answer may show in a page. A screenshot job answers with
#: ``image_base64`` of 30-45 thousand characters, one unbroken string: printed
#: as it is, it made the Fernwartung table 376,000 px wide.
RESULT_SHOWN_MAX = 200
_IMAGE_FIELD = re.compile(r'"image_base64"\s*:\s*"([A-Za-z0-9+/=_-]*)')
_LONG_RUN = re.compile(r"[A-Za-z0-9+/=_-]{80,}")


def _short_result(raw: str) -> str:
    """The answer of a booth as one short human line: never base64, long text cut."""
    from app.i18n import t
    text = str(raw or "")
    image = _IMAGE_FIELD.search(text)
    if image:
        return t("booth.job.result_image", kb=max(1, round(len(image.group(1)) * 3 / 4 / 1000)))
    text = re.sub(r"\s+", " ", _LONG_RUN.sub("…", text)).strip()
    return text if len(text) <= RESULT_SHOWN_MAX else text[:RESULT_SHOWN_MAX].rstrip() + "…"


def _job_view(job: dict) -> dict:
    """A job as the admin sees it: one of four states, in the admin's language."""
    from app.i18n import t
    state = job.get("state") or ecosystem.job_state(job)
    result = t("booth.job.expired_result") if state == "expired" else (job.get("result") or "")
    # result_text stays whole for the pickers that parse it (browse_remote);
    # every table that only lists answers shows result_short.
    return {**job, "state": state, "state_label": t(f"booth.job.state.{state}"), "result_text": result,
            "result_short": result if state == "expired" else _short_result(result)}


@bp.get("/devices/<int:device_id>/sync/job/<int:job_id>")
def sync_job_result(device_id: int, job_id: int):
    """Antwort abholen. Die Box meldet sich alle fünf Sekunden, solange etwas offen ist."""
    redir = _require_admin()
    if redir:
        return redir
    row = ecosystem.job_result(device_id, job_id)
    if not row:
        return {"ok": False, "error": "unbekannt"}, 404
    view = _job_view(row)
    return {"ok": True, "done": bool(row.get("done_at")), "success": bool(row.get("ok")),
            "result": view["result_text"], "delivered": bool(row.get("delivered_at")),
            "state": view["state"], "state_label": view["state_label"]}


@bp.post("/devices/<int:device_id>/status-page")
def status_page_switch(device_id: int):
    """Switch the public status link of a booth on, make a new one, or switch it off."""
    redir = _require_admin()
    if redir:
        return redir
    device = db.get_device_by_id(device_id)
    if not device:
        abort(404)
    from app import status_page
    from app.i18n import t
    action = request.form.get("action")
    if action in ("on", "renew"):
        token = status_page.enable(device_id)
        base = db.base_url() or request.host_url.rstrip("/")
        # Only the hash is stored: this message is the one time the link exists.
        flash(t("statuspage.admin.created", url=f"{base}/status/{token}"), "success")
        # The change log (app/protokoll.py) snapshots settings, events and devices,
        # not this table, so it would file the request as "no change". Say what
        # happened; never the token or its hash.
        g._protokoll_was = "Statusseite eingeschaltet" if action == "on" else "Statusseite: neuer Link"
        g._protokoll_ergebnis = f"Gerät #{device_id}"
    elif action == "off":
        status_page.disable(device_id)
        flash(t("statuspage.admin.disabled"), "success")
        g._protokoll_was = "Statusseite ausgeschaltet"
        g._protokoll_ergebnis = f"Gerät #{device_id}"
    return redirect(url_for("admin_booth.booth_profile", device_id=device_id) + "#statusseite")


@bp.post("/devices/<int:device_id>/silence-alarm")
def silence_alarm_switch(device_id: int):
    """Card "Warnen, wenn die Box nicht mehr antwortet" (app/silence_alarm.py). Off by default."""
    redir = _require_admin()
    if redir:
        return redir
    if not db.get_device_by_id(device_id):
        abort(404)
    from app import silence_alarm
    f = request.form
    problems = silence_alarm.save_settings(
        device_id, enabled=f.get("enabled") == "1", minutes=f.get("minutes", ""),
        recipient=f.get("recipient", ""), only_event=f.get("only_event") == "1",
        quiet_from=f.get("quiet_from", ""), quiet_to=f.get("quiet_to", ""))
    if problems:
        flash("Nicht gespeichert: " + "; ".join(problems), "error")
    else:
        flash("Warnung gespeichert." if f.get("enabled") == "1" else "Warnung ist aus.", "success")
        # The change log snapshots devices, not this table: say what happened.
        g._protokoll_was = "Warnung bei Funkstille " + ("eingeschaltet" if f.get("enabled") == "1" else "aus")
        g._protokoll_ergebnis = f"Gerät #{device_id}"
    return redirect(url_for("admin_booth.booth_profile", device_id=device_id) + "#schweigen")


@bp.post("/devices/<int:device_id>/pairing")
def device_pairing(device_id: int):
    """Kopplungscode für diesen PC — kurz zum Abtippen, lang für ohne Netz."""
    redir = _require_admin()
    if redir:
        return redir
    ecosystem.init_schema()
    device = db.get_device_by_id(device_id)
    if not device:
        abort(404)
    from urllib.parse import urlparse

    from app import pairing

    # The code tells the booth where to go; a host taken from an untrusted
    # Host header would send it (and its key) elsewhere (S-5).
    base = db.link_base()
    if not base:
        return {"ok": False, "error": "Keine Adresse der Galerie eingerichtet (BASE_URL) — "
                                      "ohne sie gibt es keinen Kopplungscode."}, 409
    made = ecosystem.new_pairing(device_id)
    parsed = urlparse(base)
    host = parsed.hostname or ""
    # Unter photobox-gallery.com genügt der Name der Galerie vor dem Code.
    # Sonst muss die Adresse dazu — und zwar vollständig: Ein abgeschnittenes
    # Schema oder ein fehlender Port führt den PC ins Leere.
    tenant = host[:-len(".photobox-gallery.com")] if host.endswith(".photobox-gallery.com") else ""
    plain = parsed.scheme == "https" and parsed.port in (None, 443)
    where = host if plain else f"{parsed.scheme}://{parsed.netloc}"
    short = f"{tenant}-{made['pretty']}" if tenant else f"{where} {made['pretty']}"
    antwort = {"ok": True, "short": short, "minutes": made["minutes"], "url": base, "long": ""}
    # The long code carries the key itself. Only its hash is stored (S-6), so
    # making one means a new key: asked for explicitly, never as a side effect
    # of the short code, because it disconnects a box running with the old key.
    body = request.get_json(silent=True) or {}
    if body.get("long") is True or request.form.get("long") == "1":
        fresh = db.regenerate_device_key(device_id)
        antwort["long"] = pairing.make_code(base, fresh["api_key"], device["name"])
        g._protokoll_was = "Langer Kopplungscode erzeugt (Schlüssel erneuert)"
        g._protokoll_ergebnis = f"Gerät #{device_id}"
    return antwort


@bp.get("/devices/<int:device_id>/sync/state")
def sync_state(device_id: int):
    redir = _require_admin()
    if redir:
        return redir
    ecosystem.init_schema()
    device = db.get_device_by_id(device_id)
    if not device:
        abort(404)
    return {"report": ecosystem.sync_report(device_id), "last_seen": device.get("last_seen_at") or "",
            "jobs_waiting": ecosystem.jobs_waiting(device_id),
            "recent": [{"type": j["type"], "created": (j.get("created_at") or "")[:16],
                        "done": bool(j.get("done_at")), "delivered": bool(j.get("delivered_at")),
                        "ok": bool(j.get("ok")), "result": j["result_short"][:160],
                        "state": j["state"], "state_label": j["state_label"]}
                       for j in map(_job_view, ecosystem.recent_jobs(device_id, 10))]}


# ── Box-Software verteilen ────────────────────────────────
# Ein Update ist: Datei hochladen, freigeben, fertig. Die Boxen holen sie sich
# beim nächsten Sync und tauschen, wenn der Betreiber es sagt — nie von selbst
# mitten im Abend.

@bp.route("/software", methods=["GET", "POST"])
def software():
    redir = _require_admin()
    if redir:
        return redir
    from app import software as software_mod
    software_mod.init_schema()
    if request.method == "POST":
        _check_csrf()
        if request.form.get("delete"):
            software_mod.loeschen(int(request.form["delete"]))
            flash("Fassung gelöscht.", "success")
            return redirect(url_for("admin_booth.software"))
        if request.form.get("release"):
            software_mod.freigeben(int(request.form["release"]), request.form.get("on") == "1")
            flash("Freigabe geändert.", "success")
            return redirect(url_for("admin_booth.software"))
        datei = request.files.get("file")
        if not datei or not datei.filename:
            flash("Keine Datei gewählt.", "warning")
            return redirect(url_for("admin_booth.software"))
        try:
            software_mod.hinzufuegen(program=request.form.get("program", "booth"),
                                     version=request.form.get("version", ""),
                                     dateiname=datei.filename, daten=datei.read(),
                                     notes=request.form.get("notes", ""),
                                     released=bool(request.form.get("released")))
            flash("Fassung abgelegt.", "success")
        except ValueError as e:
            flash(str(e), "error")
        return redirect(url_for("admin_booth.software"))

    geraete = []
    for d in db.list_devices() or []:
        status = db.get_device_status(d["id"]) or {}
        geraete.append({"name": d["name"], "id": d["id"],
                        "version": (str(status.get("booth_version") or "").split() or [""])[-1],
                        "update": status.get("update") or {}})
    return render_template("admin/software.html",
                           programme=software_mod.PROGRAMME,
                           fassungen=software_mod.liste(),
                           geraete=geraete,
                           galerie_version=_galerie_version(),
                           csrf_token=_csrf_token(), active_nav="software")


def _galerie_version() -> dict:
    """Was hier läuft — und woran man erkennt, ob es aktuell ist."""
    from pathlib import Path as _P
    wurzel = _P(current_app.root_path).parent
    version = ""
    try:
        version = (wurzel / "VERSION").read_text(encoding="utf-8").strip()
    except OSError:
        pass
    commit = ""
    kopf = wurzel / ".git" / "HEAD"
    try:
        zeile = kopf.read_text(encoding="utf-8").strip()
        if zeile.startswith("ref: "):
            commit = (wurzel / ".git" / zeile[5:]).read_text(encoding="utf-8").strip()[:7]
        else:
            commit = zeile[:7]
    except OSError:
        pass
    return {"version": version, "commit": commit}


# ── Vorlageneditor ────────────────────────────────────────
# Derselbe Editor läuft an der Box. Gespeichert wird hier, weil die Galerie
# die Medien ohnehin an alle Boxen verteilt: speichern, Profilversion hoch,
# beim nächsten Sync liegt die Vorlage auf jeder Box.

@bp.get("/vorlagen")
def templates_list():
    redir = _require_admin()
    if redir:
        return redir
    ecosystem.init_schema()
    from app import vorlagen as vorlagen_mod
    return render_template("admin/templates.html",
                           eigene=vorlagen_mod.eigene(_assets_dir()),
                           mitgeliefert=ecosystem.BUILTIN_COLLAGES,
                           papiere=vorlagen_mod.PAPIERE,
                           active_nav="templates", csrf_token=_csrf_token())


# ── Rahmen für Boomerang & Video ─────────────────────────
# Own frames are media of the kind "motion" (app/frame_store.py). This page
# lists them with the built-ins, takes a JSON file and has a small form that
# writes the JSON, with a live picture from the same renderer the booth uses.

@bp.get("/rahmen")
def frames_list():
    redir = _require_admin()
    if redir:
        return redir
    ecosystem.init_schema()
    from app import motion_frames
    bilder = [a for a in ecosystem.list_assets() if a["kind"] in ("background", "overlay", "frame", "start")
              and Path(a["file"]).suffix.lower() in (".png", ".jpg", ".jpeg", ".webp")]
    vorlage = None
    datei = (request.args.get("datei") or "").strip()
    if datei:
        vorlage = frame_store.load(datei, _assets_dir())
    return render_template("admin/booth_frames.html", eigene=frame_store.own(_assets_dir()),
                           mitgeliefert=frame_store.builtins(), bilder=bilder, formate=motion_frames.FORMATS,
                           vorlage=vorlage, active_nav="booth_assets", csrf_token=_csrf_token())


@bp.get("/rahmen/bild/<path:ident>")
def frame_picture(ident: str):
    redir = _require_admin()
    if redir:
        return redir
    png = frame_store.picture(ident, _assets_dir())
    if png is None:
        abort(404)
    return Response(png, mimetype="image/png", headers={"Cache-Control": "private, max-age=300"})


def _frame_json_or_error():
    """The frame the page sends, or a German error for the page to show."""
    raw = request.get_json(silent=True)
    if not isinstance(raw, dict):
        return None, "Der Rahmen kam nicht als JSON an."
    return raw, None


@bp.post("/rahmen/vorschau")
def frame_preview():
    redir = _require_admin()
    if redir:
        return redir
    raw, fehler = _frame_json_or_error()
    if fehler:
        return jsonify({"error": fehler}), 400
    try:
        png = frame_store.preview_png(raw, _assets_dir())
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    return Response(png, mimetype="image/png", headers={"Cache-Control": "no-store"})


@bp.post("/rahmen/speichern")
def frame_save():
    redir = _require_admin()
    if redir:
        return redir
    raw, fehler = _frame_json_or_error()
    if fehler:
        return jsonify({"error": fehler}), 400
    try:
        rahmen, hinweise = frame_store.check(raw, _assets_dir())
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    row = frame_store.save(rahmen, _assets_dir())
    return jsonify({"file": row["file"], "frame": rahmen, "hints": hinweise})


@bp.post("/rahmen/loeschen")
def frame_delete():
    redir = _require_admin()
    if redir:
        return redir
    datei = (request.form.get("datei") or "").strip()
    row = next((a for a in ecosystem.list_assets(frame_store.ASSET_KIND) if a["file"] == datei), None)
    if row:
        ecosystem.delete_asset(int(row["id"]))
        (_assets_dir() / row["file"]).unlink(missing_ok=True)
        for d in db.list_devices() or []:
            ecosystem.bump_profile(d["id"])
        flash("Rahmen gelöscht.", "success")
    return redirect(url_for("admin_booth.frames_list"))


@bp.get("/vorlagen/editor")
def templates_editor():
    redir = _require_admin()
    if redir:
        return redir
    ecosystem.init_schema()
    from app import vorlagen as vorlagen_mod
    from app.i18n import t as _t
    datei = (request.args.get("datei") or "").strip()
    vorlage = vorlagen_mod.laden(datei, _assets_dir()) if datei else None
    von = (request.args.get("von") or "").strip()
    if vorlage is None and von:
        # Eine mitgelieferte Vorlage als Ausgangspunkt — kopiert, nicht verändert.
        katalog = json.loads((Path(current_app.root_path) / "static" / "data" /
                              "collage_templates.json").read_text(encoding="utf-8"))
        quelle = next((t for t in katalog["templates"] if t["id"] == von), None)
        if quelle:
            vorlage = dict(quelle, id=f"{quelle['id']}-eigen", name=f"{quelle['name']} (eigene Fassung)")
            vorlage.pop("shots", None)
    token = _csrf_token()
    # Everything the editor script needs travels as one JSON block, so the
    # page carries no inline script.
    editor_daten = {
        "vorlage": vorlage, "datei": datei, "csrf": token,
        "sprache": getattr(g, "lang", "de") or "de",
        "speichernUrl": url_for("admin_booth.templates_save"),
        "vorschauUrl": url_for("admin_booth.templates_preview"),
        # Pictures and fonts from the booth media — the only sources a
        # template may name; the booth receives them with the template.
        "medien": [{"file": a["file"], "name": a["name"],
                    "url": url_for("admin_booth.asset_preview", name=a["file"])}
                   for a in ecosystem.list_assets()
                   if a["kind"] in ("overlay", "frame", "background", "start")
                   and vorlagen_mod.datei_name(a["file"], vorlagen_mod.BILD_ENDUNGEN)],
        "schriften": [{"file": a["file"], "name": a["name"],
                       "url": url_for("admin_booth.asset_preview", name=a["file"])}
                      for a in ecosystem.list_assets("font")
                      if vorlagen_mod.datei_name(a["file"], vorlagen_mod.SCHRIFT_ENDUNGEN)],
        # Test print at a booth: only the saved version, on a booth the
        # gallery knows; the answer is the job's result, polled like the
        # remote-maintenance page does.
        "testdruckUrl": url_for("admin_booth.templates_test_print"),
        "boxen": [{"id": d["id"], "name": d["name"]} for d in (db.list_devices() or [])],
        "texte": {k: _t(f"tpl.testprint.{k}") for k in TESTDRUCK_TEXTE},
    }
    return render_template("admin/template_editor.html", vorlage=vorlage, datei=datei,
                           editor_daten=editor_daten, active_nav="templates", csrf_token=token)


@bp.post("/vorlagen/speichern")
def templates_save():
    redir = _require_admin()
    if redir:
        return jsonify({"ok": False, "error": "Anmeldung nötig"}), 403
    _check_csrf_json()
    from app import vorlagen as vorlagen_mod
    roh = request.get_json(silent=True) or {}
    try:
        vorlage, hinweise = vorlagen_mod.annehmen(roh)
    except vorlagen_mod.VorlageAbgelehnt as e:
        return jsonify({"ok": False, "error": str(e), "fields": e.gruende}), 400
    eintrag = vorlagen_mod.speichern(vorlage, _assets_dir())
    return jsonify({"ok": True, "template": vorlage, "hinweise": hinweise, "asset": eintrag})


#: Texts of the test-print control in the editor (keys tpl.testprint.*).
TESTDRUCK_TEXTE = ("button", "booth", "mark", "unsaved", "sent", "waiting", "done", "failed", "expired",
                   "no_booth")


@bp.post("/vorlagen/testdruck")
def templates_test_print():
    """A saved template as a test print at one booth (job ``test_print``).

    Same function as the MCP tool (vorlagen.testdruck). Answers with the job
    and the address to poll its result.
    """
    redir = _require_admin()
    if redir:
        return jsonify({"ok": False, "error": "Anmeldung nötig"}), 403
    _check_csrf_json()
    from app import vorlagen as vorlagen_mod
    body = request.get_json(silent=True) or {}
    try:
        auftrag = vorlagen_mod.testdruck(int(body.get("device_id") or 0), str(body.get("datei") or ""),
                                         _assets_dir(), mark=body.get("mark", True) is not False)
    except (TypeError, ValueError) as e:
        return jsonify({"ok": False, "error": str(e)}), 400
    return jsonify({"ok": True, **auftrag,
                    "result_url": url_for("admin_booth.sync_job_result", device_id=auftrag["booth_id"],
                                          job_id=auftrag["job_id"])})


@bp.post("/vorlagen/duplizieren")
def templates_duplicate():
    """A copy of a saved template under a new name, to change without touching the original."""
    redir = _require_admin()
    if redir:
        return jsonify({"ok": False, "error": "Anmeldung nötig"}), 403
    _check_csrf_json()
    from app import vorlagen as vorlagen_mod
    datei = str((request.get_json(silent=True) or {}).get("datei") or "")
    quelle = next((e["template"] for e in vorlagen_mod.eigene(_assets_dir()) if e["asset"]["file"] == datei), None)
    if quelle is None:
        return jsonify({"ok": False, "error": "unbekannte Vorlage"}), 404
    vorhanden = {e["asset"]["file"] for e in vorlagen_mod.eigene(_assets_dir())}
    basis = (str(quelle.get("name") or "Vorlage")[:50] + " (Kopie)")
    name, n = basis, 2
    # The id comes from the name, and the id is the file name: a second copy
    # needs a name of its own, or it would replace the first copy.
    while vorlagen_mod.dateiname(vorlagen_mod.pruefe({**quelle, "id": "", "name": name})[0]) in vorhanden:
        name, n = f"{basis} {n}", n + 1
    vorlage, _ = vorlagen_mod.pruefe({**quelle, "id": "", "name": name})
    eintrag = vorlagen_mod.speichern(vorlage, _assets_dir())
    return jsonify({"ok": True, "template": vorlage, "asset": eintrag,
                    "editor_url": url_for("admin_booth.templates_editor", datei=eintrag["file"])})


@bp.post("/vorlagen/vorschau")
def templates_preview():
    """The template as the booth would print it, with sample photos.

    Same check as saving, same renderer as the booth (vorlagen_render, one
    file in both programs) — only the photos are stand-ins and the style is
    the house style unless the editor names one of the occasions.
    """
    redir = _require_admin()
    if redir:
        return jsonify({"ok": False, "error": "Anmeldung nötig"}), 403
    _check_csrf_json()
    import datetime
    import io
    from app import vorlagen as vorlagen_mod, vorlagen_render
    roh = request.get_json(silent=True) or {}
    vorlage, _ = vorlagen_mod.pruefe(roh.get("template") if isinstance(roh.get("template"), dict) else roh)
    stil = str(roh.get("stil") or "clean")
    bild = vorlagen_render.render_template(
        vorlage, vorlagen_mod.beispielbilder(vorlage.get("shots") or 1), assets_dir=_assets_dir(),
        qr_url="https://example.invalid/vorschau",
        texts={"event_name": "Mia & Tom", "date": datetime.date.today().strftime("%d.%m.%Y")},
        style_cfg={"preset": stil if stil in vorlagen_render.STYLE_PRESETS else "clean"})
    bild.thumbnail((1200, 1200))
    puffer = io.BytesIO()
    bild.save(puffer, "PNG", optimize=True)
    return Response(puffer.getvalue(), mimetype="image/png", headers={"Cache-Control": "no-store"})


@bp.get("/vorlagen/export")
def templates_export():
    """Vorlagen als Paket herunterladen — mit den Bildern, auf die sie zeigen.

    Ohne ``datei`` kommen alle eigenen Vorlagen. Mitgelieferte Vorlagen lassen
    sich ebenfalls mitnehmen (``von=``): Wer eine davon als Ausgangspunkt
    weitergeben will, soll sie nicht erst abspeichern müssen.
    """
    redir = _require_admin()
    if redir:
        return redir
    ecosystem.init_schema()
    from app import vorlagen as vorlagen_mod, vorlagen_paket

    ordner = _assets_dir()
    dateien = [d for d in request.args.getlist("datei") if d.strip()]
    eigene = vorlagen_mod.eigene(ordner)
    if dateien:
        vorlagen = [e["template"] for e in eigene if e["asset"]["file"] in dateien]
    else:
        vorlagen = [e["template"] for e in eigene]
    for vid in request.args.getlist("von"):
        katalog = json.loads((Path(current_app.root_path) / "static" / "data" /
                              "collage_templates.json").read_text(encoding="utf-8"))
        quelle = next((t for t in katalog["templates"] if t["id"] == vid), None)
        if quelle:
            vorlagen.append(quelle)
    if not vorlagen:
        flash("Nichts zum Ausgeben — erst eine Vorlage anlegen.", "warning")
        return redirect(url_for("admin_booth.templates_list"))

    def lies(name: str):
        pfad = ordner / name
        return pfad.read_bytes() if pfad.is_file() else None

    # Die Hintergründe gehören nicht einer Vorlage, sondern der Box — wer
    # Vorlagen weitergibt, will sie fast immer mitgeben.
    hintergruende = [a["file"] for a in ecosystem.list_assets("background")]
    daten = vorlagen_paket.packen(vorlagen, lies, hintergruende=hintergruende,
                                  programm="Abzug Galerie")
    name = vorlagen_paket.paketname(vorlagen)
    return Response(daten, mimetype="application/zip",
                    headers={"Content-Disposition": f'attachment; filename="{name}"',
                             "Content-Length": str(len(daten))})


@bp.post("/vorlagen/import")
def templates_import():
    """Ein Paket einlesen. Die Bilder wandern zu den Medien, die Vorlagen in die Liste."""
    redir = _require_admin()
    if redir:
        return redir
    _check_csrf()
    ecosystem.init_schema()
    from app import vorlagen as vorlagen_mod, vorlagen_paket

    datei = request.files.get("paket")
    if not datei or not datei.filename:
        flash("Keine Datei ausgewählt.", "error")
        return redirect(url_for("admin_booth.templates_list"))
    try:
        paket = vorlagen_paket.lesen(datei.read())
    except ValueError as e:
        flash(str(e), "error")
        return redirect(url_for("admin_booth.templates_list"))

    vorlagen, medien, hinweise = paket.vorlagen, paket.medien, list(paket.hinweise)
    ordner = _assets_dir()
    ordner.mkdir(parents=True, exist_ok=True)
    for name, roh in medien.items():
        ziel = ordner / name
        # Ein gleichnamiges Bild, das schon da ist, wird nicht überschrieben:
        # Es könnte zu einer anderen Vorlage gehören.
        if ziel.exists() and ziel.read_bytes() != roh:
            hinweise.append(f"{name}: Ein Bild mit diesem Namen war schon da — behalten.")
            continue
        if ziel.exists():
            continue
        ziel.write_bytes(roh)
        # Wofür das Bild da ist, steht im Paket: Hintergrund für Collagen oder
        # Überlagerung über dem fertigen Abzug. Falsch einsortiert wäre es in
        # der Verwaltung nicht wiederzufinden.
        art = ("font" if name.lower().endswith(tuple(vorlagen_paket.SCHRIFT_ENDUNGEN))
               else "background" if name in paket.hintergruende else "overlay")
        vorhanden = next((a for a in ecosystem.list_assets(art) if a["file"] == name), None)
        if not vorhanden:
            ecosystem.add_asset(art, name, name, vorlagen_paket.pruefsumme(roh), len(roh))
    for v in vorlagen:
        vorlagen_mod.speichern(v, ordner)
    flash(f"{len(vorlagen)} Vorlage(n) und {len(medien)} Bild(er) übernommen.", "success")
    for h in hinweise[:8]:
        flash(h, "warning")
    return redirect(url_for("admin_booth.templates_list"))


# ── Vorlagen-Bibliothek ───────────────────────────────────
# The collection that ships with the gallery: browse by family and category,
# take one over with a click. It lands among the own templates exactly like
# an imported package, so the booths get it on their next sync.

@bp.get("/vorlagen/bibliothek")
def templates_library():
    redir = _require_admin()
    if redir:
        return redir
    ecosystem.init_schema()
    from app import vorlagen_bibliothek, vorlagen as vorlagen_mod
    q = (request.args.get("q") or "").strip()[:60]
    kategorie = (request.args.get("kategorie") or "").strip()[:60]
    eigene = {a["file"] for a in ecosystem.list_assets("template")}
    return render_template("admin/template_library.html",
                           familien=vorlagen_bibliothek.suche(q, kategorie),
                           kategorien=vorlagen_bibliothek.kategorien(), q=q, kategorie=kategorie,
                           eigene=eigene, papiere=vorlagen_mod.PAPIERE,
                           active_nav="templates", csrf_token=_csrf_token())


@bp.get("/vorlagen/bibliothek/vorschau/<path:name>")
def templates_library_preview(name: str):
    redir = _require_admin()
    if redir:
        return redir
    from flask import send_file
    from app import vorlagen_bibliothek
    pfad = vorlagen_bibliothek.vorschau_pfad(name)
    if pfad is None:
        abort(404)
    return send_file(pfad, conditional=True, max_age=3600)


@bp.post("/vorlagen/bibliothek/uebernehmen")
def templates_library_take():
    redir = _require_admin()
    if redir:
        return redir
    _check_csrf()
    ecosystem.init_schema()
    from app import vorlagen_bibliothek
    datei = (request.form.get("datei") or "").strip()
    try:
        neu, hinweise = vorlagen_bibliothek.uebernehmen(datei, _assets_dir())
    except (KeyError, OSError, ValueError):
        flash("Diese Vorlage gibt es in der Bibliothek nicht.", "error")
        return redirect(url_for("admin_booth.templates_library"))
    for d in db.list_devices() or []:
        ecosystem.bump_profile(d["id"])
    flash(f"Übernommen: {neu}. Zuweisen in den Box-Einstellungen unter „Eigene Collage-Vorlagen“.", "success")
    for h in hinweise[:6]:
        flash(h, "warning")
    return redirect(url_for("admin_booth.templates_library", q=request.form.get("q") or None,
                            kategorie=request.form.get("kategorie") or None))


@bp.post("/vorlagen/loeschen")
def templates_delete():
    redir = _require_admin()
    if redir:
        return redir
    _check_csrf()
    datei = (request.form.get("datei") or "").strip()
    row = next((a for a in ecosystem.list_assets("template") if a["file"] == datei), None)
    if row:
        ecosystem.delete_asset(int(row["id"]))
        (_assets_dir() / datei).unlink(missing_ok=True)
        for d in db.list_devices() or []:
            ecosystem.bump_profile(d["id"])
        flash("Vorlage gelöscht.", "success")
    return redirect(url_for("admin_booth.templates_list"))


@bp.route("/booth-assets", methods=["GET", "POST"])
def assets():
    redir = _require_admin()
    if redir:
        return redir
    ecosystem.init_schema()
    if request.method == "POST":
        if request.form.get("delete"):
            row = ecosystem.delete_asset(int(request.form["delete"]))
            if row:
                (_assets_dir() / row["file"]).unlink(missing_ok=True)
                for d in db.list_devices() or []:
                    ecosystem.bump_profile(d["id"])
                flash("Gelöscht.", "success")
            return redirect(url_for("admin_booth.assets"))
        kind = request.form.get("kind", "start")
        if kind not in ecosystem.ASSET_KINDS:
            abort(400)
        count = 0
        for up in request.files.getlist("files"):
            if not up or not up.filename:
                continue
            ext = Path(up.filename).suffix.lower()
            if kind == frame_store.ASSET_KIND:
                # A frame for boomerang/video: checked like the editor's, stored as rahmen-<id>.json.
                if ext != ".json":
                    flash(f"{up.filename}: Ein Rahmen ist eine JSON-Datei.", "warning")
                    continue
                try:
                    rahmen, _hinweise = frame_store.check(json.loads(up.read()), _assets_dir())
                except ValueError as e:
                    flash(f"{up.filename}: {e}", "warning")
                    continue
                frame_store.save(rahmen, _assets_dir())
                count += 1
                continue
            sound_ext = ext in (".mp3", ".wav", ".ogg")
            font_ext = ext in FONT_EXT
            if ext not in ALLOWED_ASSET_EXT or (kind == "template") != (ext == ".json") \
                    or (kind == "lut") != (ext == ".cube") or (kind == "sound") != sound_ext \
                    or (kind == "font") != font_ext:
                flash(f"{up.filename}: Dateityp passt nicht zu „{ecosystem.ASSET_KINDS[kind]}“.", "warning")
                continue
            data = up.read()
            if ext == ".json":
                # The full check every template passes (vorlagen_kern.annehmen);
                # the booth gets the checked file, never the raw upload.
                from app import vorlagen as vorlagen_mod
                try:
                    tpl, _hinweise = vorlagen_mod.annehmen(data)
                except vorlagen_mod.VorlageAbgelehnt as e:
                    flash(f"{up.filename}: {e}", "warning")
                    continue
                data = vorlagen_mod.inhalt(tpl)
            name = secure_filename(Path(up.filename).stem)[:60] or "asset"
            file = f"{kind}-{name}-{secrets.token_hex(3)}{ext}"
            (_assets_dir() / file).write_bytes(data)
            ecosystem.add_asset(kind, Path(up.filename).stem[:80], file, hashlib.sha256(data).hexdigest(), len(data))
            count += 1
        flash(f"{count} Datei(en) hochgeladen. In den Box-Einstellungen zuweisen.", "success" if count else "warning")
        return redirect(url_for("admin_booth.assets"))
    return render_template("admin/booth_assets.html", assets=ecosystem.list_assets(), kinds=ecosystem.ASSET_KINDS,
                           active_nav="booth_assets", csrf_token=_csrf_token())


@bp.get("/booth-assets/<name>")
def asset_preview(name: str):
    redir = _require_admin()
    if redir:
        return redir
    from flask import send_file
    p = _assets_dir() / secure_filename(name)
    if not p.exists():
        abort(404)
    return send_file(p, conditional=True)


@bp.post("/events/<int:event_id>/qr-target")
def event_qr(event_id: int):
    redir = _require_admin()
    if redir:
        return redir
    ecosystem.init_schema()
    if not db.get_event_by_id(event_id):
        abort(404)
    from app import redirects
    qr_target = request.form.get("qr_target", "gallery")
    external_url = request.form.get("external_url", "")
    # The forward of the gallery link (app/redirects.py). Only http(s) is
    # stored; anything else is refused with a message, never saved half.
    raw_forward = (request.form.get("redirect_url") or "").strip()
    forward = redirects.clean_url(raw_forward)
    if raw_forward and not forward:
        flash("Weiterleitung nicht gespeichert: Bitte eine vollständige Adresse mit https:// angeben.", "error")
        forward = redirects.clean_url(db.get_event_by_id(event_id).get("redirect_url"))
    elif "redirect_url" in request.form:
        db.update_event(event_id, redirect_url=forward or None)
    # "Alles weiterleiten": one address for the gallery link and every QR code.
    if request.form.get("redirect_all") and forward:
        qr_target, external_url = "external", forward
    raw_external = (external_url or "").strip()
    # {uid} is a placeholder the booth's QR code fills in; check the rest.
    if raw_external and not redirects.clean_url(raw_external.replace("{uid}", "uid")):
        flash("QR-Weiterleitung nicht gespeichert: Bitte eine vollständige Adresse mit https:// angeben.", "error")
        external_url = ""
        if qr_target == "external":
            qr_target = "gallery"
    ecosystem.set_event_qr(event_id, qr_target, external_url,
                           request.form.get("status_message", ""),
                           utm={k: request.form.get(k, "") for k in ecosystem.UTM_KEYS})
    flash("QR-Ziel gespeichert. Gilt sofort für alle bereits gedruckten Codes.", "success")
    return redirect(url_for("admin.edit_event", event_id=event_id) + "#qr-ziel")


def _sharepic_hintergrund(form, datei, alt_pfad: str, prefix: str = "sharepic-bg") -> str:
    """Hintergrundbild speichern, entfernen oder behalten.

    Das Bild wird auf Story-Maß gebracht, bevor es liegen bleibt: Ein Foto aus
    der Kamera hätte 8 MB, gebraucht werden 1080 × 1920.
    """
    if form.get("remove_bg"):
        if alt_pfad:
            media.delete_rel_path(alt_pfad)
        return ""
    if not datei or not datei.filename:
        return alt_pfad
    from PIL import Image, ImageOps
    ziel = media.branding_dir() / f"{prefix}.jpg"
    with Image.open(datei.stream) as im:
        im = ImageOps.exif_transpose(im).convert("RGB")
        im = ImageOps.fit(im, (1080, 1920), Image.Resampling.LANCZOS)
        im.save(ziel, "JPEG", quality=88, optimize=True)
    return ziel.relative_to(media.data_dir()).as_posix()


@bp.route("/settings/qr", methods=["GET", "POST"])
def settings_qr():
    redir = _require_admin()
    if redir:
        return redir
    from app import qrstyle, sharepic
    if request.method == "POST":
        f = request.form
        style = qrstyle.normalize({"dark": f.get("dark"), "light": f.get("light"), "modules": f.get("modules"),
                                   "eyes": f.get("eyes"), "logo_scale": f.get("logo_scale"),
                                   "logo_pad": bool(f.get("logo_pad"))})
        # Die Adresse, die auf jedem QR steht. Läuft die Galerie unter mehreren
        # Domains, muss eine davon gewählt sein — sonst landet auf dem Ausdruck,
        # unter welcher gerade jemand im Admin war.
        base = (f.get("app_base_url") or "").strip().rstrip("/")
        if base and not base.startswith(("http://", "https://")):
            base = "https://" + base
        db.set_setting("app_base_url", base)
        db.set_setting("qr_style_json", json.dumps(style))
        gestaltung = {k: f.get(k) for k in sharepic.FELDER if k in f}
        gestaltung["show_url"] = bool(f.get("show_url"))
        gestaltung["bg_path"] = _sharepic_hintergrund(f, request.files.get("bg_image"),
                                                      sharepic.global_design().get("bg_path", ""))
        sharepic.save_global(gestaltung)
        if f.get("remove_logo"):
            db.set_setting("qr_logo_path", "")
        elif request.files.get("logo") and request.files["logo"].filename:
            from PIL import Image, ImageOps
            out = media.branding_dir() / "qr-logo.png"
            with Image.open(request.files["logo"].stream) as im:
                im = ImageOps.exif_transpose(im).convert("RGBA")
                im.thumbnail((600, 600))
                im.save(out, "PNG")
            db.set_setting("qr_logo_path", out.relative_to(media.data_dir()).as_posix())
        flash("QR & Sharepic gespeichert.", "success")
        return redirect(url_for("admin_booth.settings_qr"))
    events = db.list_events() or []
    return render_template("admin/settings/qr.html", cfg=sharepic.settings(), events=events, tab="qr",
                           app_base_url=db.get_setting("app_base_url") or "",
                           request_base=request.host_url.rstrip("/"),
                           csrf_token=_csrf_token(), active_nav="settings")


@bp.get("/settings/sharepic-preview.png")
def settings_sharepic_preview():
    """Das ganze Sharepic, wie es ein Gast bekäme — mit echtem Event, wenn es eins gibt."""
    redir = _require_admin()
    if redir:
        return redir
    import io as _io
    from flask import send_file
    from app import sharepic
    events = db.list_events() or []
    event = events[0] if events else {"id": 0, "name": "Beispiel-Event", "slug": "beispiel"}
    png = sharepic.render(event, f"{db.base_url() or request.host_url.rstrip('/')}/gallery/{event.get('slug', 'beispiel')}")
    return send_file(_io.BytesIO(png), mimetype="image/png", max_age=0)


@bp.get("/settings/qr/preview.png")
def settings_qr_preview():
    redir = _require_admin()
    if redir:
        return redir
    import io as _io
    from flask import send_file
    from app import qrstyle, sharepic
    cfg = sharepic.settings()
    img = qrstyle.render("https://example.org/p/abcd2345xy-12345678", 420, cfg["qr_style"], sharepic.logo_path(cfg))
    buf = _io.BytesIO()
    img.save(buf, "PNG")
    buf.seek(0)
    return send_file(buf, mimetype="image/png", max_age=0)


# ── Eigene Domains ───────────────────────────────────────────
#
# Eine gehostete Instanz für einen Kunden soll unter dessen Adresse laufen.
# Was hier passiert, ist die Hälfte der Arbeit: eintragen, dem Kunden den
# DNS-Eintrag nennen, prüfen, ob er zeigt. Das Zertifikat besorgt die
# Betriebsebene (Coolify/Traefik/Caddy) — das sagt die Seite auch so.

@bp.route("/domains", methods=["GET", "POST"])
def domains_page():
    redir = _require_admin()
    if redir:
        return redir
    ecosystem.init_schema()
    from app import domains as domains_mod

    if request.method == "POST":
        _check_csrf()
        tat = request.form.get("tat") or "anlegen"
        try:
            if tat == "anlegen":
                event_id = request.form.get("event_id") or ""
                eintrag = domains_mod.anlegen(request.form.get("domain", ""),
                                              int(event_id) if event_id.isdigit() else None,
                                              request.form.get("note", ""))
                # Gleich einmal nachsehen: Oft ist der Eintrag längst gemacht.
                ergebnis = domains_mod.bestaetigen(int(eintrag["id"]))
                flash(f"{eintrag['domain']} eingetragen. {ergebnis['grund']}",
                      "success" if ergebnis["ok"] else "warning")
            elif tat == "pruefen":
                ergebnis = domains_mod.bestaetigen(int(request.form.get("id") or 0))
                flash(ergebnis["grund"], "success" if ergebnis["ok"] else "warning")
            elif tat == "loeschen":
                domains_mod.loeschen(int(request.form.get("id") or 0))
                flash("Domain entfernt. Links laufen wieder über die Hauptadresse.", "success")
        except ValueError as e:
            flash(str(e), "error")
        return redirect(url_for("admin_booth.domains_page"))

    eintraege = domains_mod.liste()
    for e in eintraege:
        e["anleitung"] = domains_mod.anleitung(e["domain"])
        e["event"] = db.get_event_by_id(e["event_id"]) if e["event_id"] else None
    return render_template("admin/domains.html", domains=eintraege,
                           events=db.list_events() or [],
                           eigener_host=domains_mod.eigener_host(),
                           active_nav="domains", csrf_token=_csrf_token())
