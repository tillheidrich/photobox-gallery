"""Konfiguration der Box.

Zwei Ebenen, bewusst getrennt:

* ``local``  — was nur an dieser Box stimmt: Kamera, Drucker, Pfade, Server-Zugang.
  Liegt in ``<data>/local.json`` und wird nie vom Server überschrieben.
* ``profile`` — was der Betreiber aus der Ferne steuert: Modi, Countdown,
  Hintergründe, Druck, QR. Kommt vom Galerie-Server (``/api/v2/booth/sync``),
  wird lokal gecacht und gilt auch offline weiter.

Unbekannte Schlüssel werden verworfen, fehlende mit Defaults gefüllt. So kann
ein älterer Server nie eine neuere Box kaputtkonfigurieren und umgekehrt.
"""
from __future__ import annotations

import copy
import json
import logging
import os
import sys
import threading
from pathlib import Path
from typing import Any, Dict, List, Tuple

# The PIN a booth ships with. It is printed in every manual, so the booth only
# accepts it as the key to choosing a new one (see Booth.factory_pin_active).
FACTORY_PIN = "2580"
#: Four digits are allowed (the operator types the PIN on a touch screen
#: between two guests). That is only acceptable because of the brake in
#: pinbrake.py: five wrong guesses, then a pause that doubles up to a quarter
#: of an hour and survives a restart.
MIN_PIN_LENGTH = 4


def weak_pin_reason(pin: Any) -> str:
    """Why a new admin PIN is refused, or "" when it is good enough.

    The admin is reachable from the touchscreen, so the PIN is the only thing
    between a curious guest and the settings. At least four digits and no
    pattern a thumb would try first: one repeated digit or a straight run up
    or down.
    """
    pin = str(pin or "")
    if not pin.isdigit() or not pin.isascii():
        return "Die PIN darf nur aus Ziffern bestehen."
    if pin == FACTORY_PIN:
        return f"{FACTORY_PIN} ist die Werks-PIN — bitte eine eigene wählen."
    if len(pin) < MIN_PIN_LENGTH:
        return f"Die PIN braucht mindestens {MIN_PIN_LENGTH} Ziffern."
    if len(set(pin)) == 1:
        return "Eine Ziffer immer wieder ist zu leicht zu erraten."
    steps = {(int(b) - int(a)) % 10 for a, b in zip(pin, pin[1:])}
    if steps in ({1}, {9}):
        return "Eine Zahlenreihe wie 1234 ist zu leicht zu erraten."
    return ""


#: Start-screen backgrounds that ship with the booth (tools/make_start_backgrounds.py),
#: id -> name in the pickers. The profile names one as "builtin:<id>.svg" in
#: start.media; the booth serves it from /static/start/<id>.svg, the gallery
#: previews it from /static/booth-start/<id>.svg. Same list in the other DEFAULT_PROFILE file.
START_BUILTINS = {"abend": "Abendblau", "nacht": "Nacht", "sand": "Sand (hell)", "salbei": "Salbei",
                  "punkte": "Punkte", "wellen": "Wellen"}
BUILTIN_PREFIX = "builtin:"


def builtin_start_id(name) -> str:
    """"builtin:abend.svg" -> "abend"; "" for anything else or an unknown id."""
    text = str(name or "")
    if not (text.startswith(BUILTIN_PREFIX) and text.endswith(".svg")):
        return ""
    key = text[len(BUILTIN_PREFIX):-4]
    return key if key in START_BUILTINS else ""


DEFAULT_LOCAL: Dict[str, Any] = {
    "booth_name": "Fotobox",
    # Clean-up may delete runs the gallery never confirmed (profile
    # aufraeumen.nur_bestaetigte = false) only when this booth says so here.
    # The profile comes from the gallery; one changed field there must not
    # delete guest pictures that exist nowhere else (second review R2-N1).
    # Only in local.json on this PC, never set from the gallery.
    "aufraeumen_ohne_bestaetigung": False,
    # Ein Event, das an der Box angelegt wurde, während keine Verbindung zur
    # Galerie bestand. Es gilt sofort — Aufnahmen, QR-Codes, Drucke —, und beim
    # nächsten Kontakt trägt die Box es nach. Danach ist der Zettel leer.
    # {"name": "…", "slug": "…", "share_key": "…", "angelegt": "2026-09-20T18:03:00"}
    "event_claim": {},
    # Vorlagen, die an dieser Box entstanden sind und noch in die Galerie
    # müssen. Sie wirken sofort hier; der Eintrag verschwindet erst, wenn die
    # Galerie den Empfang bestätigt hat — auch über einen Neustart hinweg.
    "template_push": [],
    # The template files the gallery's last profile named (collage_templates).
    # The gallery sends them again with every sync, so the templates page
    # offers "hide" for them instead of a delete that would not last.
    "gallery_templates": [],
    "camera": {
        "backend": "auto",          # auto | edsdk (Canon, USB) | ccapi (Canon, WLAN) | digicamcontrol
                                    # (Nikon/Sony/Canon/Fuji unter Windows) | gphoto2 (Linux, 2500+ Modelle)
                                    # | folder (Hersteller-Tethering) | webcam | mock
        "webcam_index": 0,
        "rotate": 0,                # 0 | 90 | 180 | 270 — Kamera hochkant montiert?
        "video_source": "",         # optional andere Quelle für Live-Bild/Video (z. B. "webcam:1"); leer = Kamera per USB
        "mirror_preview": True,     # Live-Bild spiegeln (wie ein Spiegel), Ergebnis nie
        "edsdk_dll": "",            # Pfad zu EDSDK.dll (Canon-SDK, nicht mitgeliefert)
        "ccapi_url": "",            # Canon über WLAN (CCAPI), z. B. http://192.168.1.2:8080
        "ccapi_liveview": "medium", # small | medium | off — kleiner heißt flüssiger im vollen WLAN
        "ccapi_display": "on",      # Kameradisplay während des Live-Bildes an lassen?
        "digicam_url": "http://127.0.0.1:5513",
        "folder": "",               # digicamcontrol: Sitzungsordner · folder: Ausgabeordner der Tethering-Software
        "preview_webcam": "",       # folder: Live-Bild von Webcam-Index (leer = keins)
        "trigger_url": "",          # folder: optionaler HTTP-Auslöser
    },
    "printer": {
        "backend": "auto",          # auto | windows | cups | none
        "name": "",                 # leer = Standarddrucker
        "paper": "postcard",        # 100x148 mm (SELPHY CP1300)
        "uri": "",                  # nur ipp
        "folder": "",               # nur hotfolder
        "media": "",
        # Welches Gerät hier steht (siehe printing/models.py) und was drin ist.
        # Daraus kennt die Box die Blattzahl einer vollen Kassette — die kein
        # Fotodrucker von sich aus meldet. "sheets_total" überschreibt sie.
        "model": "",                # z. B. selphy-cp, dnp-ds620, dnp-rx1, citizen-cx02
        "medium": "",               # z. B. 10x15, 10x15-cut, postcard
        "sheets_total": 0,          # 0 = aus dem Modell ableiten
        # Printers that need the operator at two moments (SELPHY: empty paper
        # tray, used-up ink ribbon) count both separately. 0 = the model's
        # default (printing/models.py, "supplies"); both 0 there too = the
        # single count above.
        "tray_total": 0, "ribbon_total": 0,
        # Windows only: follow each job in the spooler queue until it left it
        # (and take it back on a lasting error). False = count a print as soon
        # as the spooler has accepted it (behaviour before 14.11.2026). Local
        # setting, also in each "printers" entry; not in the profile.
        "follow_jobs": True,
        # Print correction of this printer (printing/korrektur.py), -50 … +50,
        # 0 = the file goes out unchanged. Also in each "printers" entry.
        "brightness": 0, "contrast": 0, "saturation": 0,
    },
    "printer2": {"backend": "none", "name": "", "paper": "postcard"},   # veraltet, siehe printers
    # Beliebig viele Drucker. Leer = nur "printer". Einträge:
    # {"label": "SELPHY links", "backend": "windows|cups|ipp|hotfolder|folder", "name": "…", "uri": "ipp://…",
    #  "folder": "D:/HotFolder", "media": "", "only": "any|portrait|landscape", "enabled": true,
    #  "model": "dnp-ds620", "medium": "10x15", "sheets_total": 0, "tray_total": 0, "ribbon_total": 0, "follow_jobs": true}
    "printers": [],
    "print_routing": "balance",     # balance | failover
    "server": {
        "url": "",                  # https://photobox.example.de
        "api_key": "",
        "verify_tls": True,
        # Written by pairing with a code: what the gallery called itself and
        # this booth. Shown in the admin card, never used to connect.
        "gallery_name": "",
        "device_name": "",
    },
    # Random id of this installation, made at the first pairing from the
    # phone: the gallery keeps one open request per booth with it.
    "install_id": "",
    "targets": [],                  # zusätzliche Ziele: [{"type":"sftp",...},{"type":"ftps",...}]
    # Dreifache Ablage: Arbeitsordner der Box + Kopie in einen Ordner + Kopie auf einen Stick.
    # Der Sync mit Galerie/FTPS/SFTP läuft davon unabhängig weiter.
    "storage": {
        "folder": "",               # zweite Ablage auf diesem Rechner (leer = aus)
        "layout": "event/date",     # event/date | event | flat
        "originals": True,          # Einzelaufnahmen mitkopieren
        # "trusted": sticks confirmed at the box with the PIN (S-7), as
        # [{"id", "label", "since"}]; the id is also written onto the stick.
        "usb": {"enabled": False, "path": "", "label": "", "originals": True, "eject_hint": True,
                "trusted": []},
    },
    # Ohne Galerie betreiben: kein Server nötig, Profil wird lokal gepflegt.
    "standalone": False,
    # Keep the profile as it was set at this booth: a profile from the gallery
    # waits until this is released. For a venue without reception, where a
    # change made on site must not be taken back by the next contact. Set at
    # the booth only (not on REMOTE_LOCAL_KEYS).
    "profile_hold": False,
    "admin_pin": FACTORY_PIN,      # only good for choosing a new one, see weak_pin_reason
    # Fingerprint of a gallery PIN that the operator replaced at the booth
    # (Booth.set_admin_pin). A profile that brings the same PIN again does not
    # undo the change; a new PIN set in the gallery still wins.
    "pin_overridden": "",
    # Set by a new pairing: offer this booth's profile to the gallery as the
    # starting point at the next sync (Booth.profile_seed). Cleared by the answer.
    "profile_seed_pending": False,
    "port": 8765,
    "kiosk": True,
    # The screen (window.py): "own" = the booth's own program window (WebView2
    # on Windows), "chrome" = Chrome/Edge in kiosk mode, "none" = server only.
    # Chrome is also the automatic fallback when the own window cannot start.
    # screen: -1 = primary monitor, 0.. = that monitor. debug: dev tools,
    # context menu and a closable window. Set at the booth only (not on
    # REMOTE_LOCAL_KEYS): a wrong value from afar could leave a dark screen.
    "window": {"mode": "own", "screen": -1, "on_top": False, "debug": False, "hide_console": True},
    # Only matters with --host: lets the guest routes answer the network too
    # (a second screen on a tablet). Off, the network gets the admin and the
    # trigger, never the camera or the photos. Not settable from the gallery.
    "lan_guest_access": False,
    # Download without internet (profile "wifi_share"): the guest page gets a
    # listener of its own on this port, on all addresses, while the booth's
    # interface stays on 127.0.0.1. "address" = how phones in the Wi-Fi reach
    # this machine; empty = found from the local addresses.
    # "allowed" is the lock on the booth itself: the profile (which can be
    # changed from afar) may ask for the guest page, but a second listener on
    # all addresses only ever opens if this machine says so. Off by default,
    # and deliberately not on REMOTE_LOCAL_KEYS.
    "guest_share": {"allowed": False, "address": "", "port": 8766},
    # Live remote control from the gallery (job "remote_control", MCP right
    # "control"; boothcore/fernsteuerung.py). Off by default: a rented booth is
    # not driveable from outside unless its operator switched this on here,
    # with the PIN. Never on REMOTE_LOCAL_KEYS, so the gallery cannot switch it.
    # "indicator": a small "Fernsteuerung aktiv" mark on the kiosk while a
    # session runs.
    "remote_control": {"allowed": False, "indicator": True},
    # The second upload target ("Zweites Ziel im Netz") is set from the
    # gallery without a switch here since 17.11.2026 (owner's decision; the
    # former "remote_targets.allowed" is ignored where it is still stored).
    # What protects the password: boothcore/second_target.py, rule B2.
    # The last "Verbindung testen" of that target: ok, message (no password), at.
    "second_target_check": {},
}

#: Local settings of a booth that the gallery may change (job
#: ``set_local_config``): camera, printer and storage choices, nothing else.
#: Nothing on this list names a server, a key, a PIN, an address or a path —
#: those decide where pictures go and which code runs (an SDK DLL), so they
#: are set at the booth. The same dict lives in app/ecosystem.py and is
#: checked there again; a test in each suite compares the two.
REMOTE_LOCAL_KEYS = {
    "booth_name": ("text", 60),
    "kiosk": ("bool",),
    "camera.backend": ("enum", ("auto", "edsdk", "ccapi", "digicamcontrol", "gphoto2", "folder", "webcam", "mock")),
    "camera.webcam_index": ("int", 0, 9),
    "camera.rotate": ("enum", (0, 90, 180, 270)),
    "camera.mirror_preview": ("bool",),
    "camera.ccapi_liveview": ("enum", ("small", "medium", "off")),
    "camera.ccapi_display": ("enum", ("on", "off")),
    "printer.backend": ("enum", ("auto", "windows", "cups", "none")),
    "printer.name": ("text", 120),
    "printer.paper": ("text", 30),
    "printer.model": ("text", 40),
    "printer.medium": ("text", 40),
    "printer.sheets_total": ("int", 0, 2000),
    # Counted supplies of the first printer (SELPHY: tray and ribbon) and the
    # Windows job following; the operator corrects them from a phone.
    "printer.tray_total": ("int", 0, 2000),
    "printer.ribbon_total": ("int", 0, 2000),
    "printer.follow_jobs": ("bool",),
    "print_routing": ("enum", ("balance", "failover")),
    # Print correction per printer entry (1 = first entry of "printers", or
    # the single "printer" section). Takes effect at the next print.
    "printers.1.brightness": ("int", -50, 50), "printers.1.contrast": ("int", -50, 50), "printers.1.saturation": ("int", -50, 50),
    "printers.2.brightness": ("int", -50, 50), "printers.2.contrast": ("int", -50, 50), "printers.2.saturation": ("int", -50, 50),
    "printers.3.brightness": ("int", -50, 50), "printers.3.contrast": ("int", -50, 50), "printers.3.saturation": ("int", -50, 50),
    "printers.4.brightness": ("int", -50, 50), "printers.4.contrast": ("int", -50, 50), "printers.4.saturation": ("int", -50, 50),
    "storage.layout": ("enum", ("event/date", "event", "flat")),
    "storage.originals": ("bool",),
    # storage.usb.enabled and storage.usb.originals left this list on
    # 15.11.2026 (S-7): where guest pictures are copied is decided at the box.
    "storage.usb.eject_hint": ("bool",),
}


def clean_remote_local(settings: Any) -> Tuple[Dict[str, Any], List[str]]:
    """Keep what is on ``REMOTE_LOCAL_KEYS`` with a value of the right kind.

    Returns the accepted settings and one sentence per refused one. Booleans
    are not numbers here: ``True`` is no rotation of 1 degree.
    """
    out: Dict[str, Any] = {}
    refused: List[str] = []
    if not isinstance(settings, dict):
        return out, ["settings must be an object"]
    for key, value in list(settings.items())[:40]:
        rule = REMOTE_LOCAL_KEYS.get(str(key))
        if not rule:
            refused.append(f"{key}: not changeable remotely")
            continue
        kind = rule[0]
        if kind == "bool":
            ok = isinstance(value, bool)
        elif kind == "int":
            ok = isinstance(value, int) and not isinstance(value, bool) and rule[1] <= value <= rule[2]
        elif kind == "enum":
            ok = not isinstance(value, bool) and value in rule[1]
        else:
            ok = isinstance(value, str) and len(value) <= rule[1] and value.isprintable()
            value = value.strip() if ok else value
        if ok:
            out[str(key)] = value
        else:
            refused.append(f"{key}: value not allowed")
    return out, refused


def nest_remote_local(local: Dict[str, Any], clean: Dict[str, Any]) -> Dict[str, Any]:
    """Dotted keys into whole sections for ``Store.save_local``.

    ``save_local`` merges one level deep; ``storage.usb`` handed over alone
    would replace the stick's path with nothing. So each touched section is
    copied from the current settings and changed in place.
    """
    patch: Dict[str, Any] = {}
    for dotted, value in clean.items():
        parts = dotted.split(".")
        if parts[0] == "printers" and len(parts) == 3 and parts[1].isdigit():
            # A printer entry by its number as the operator sees it in the
            # form. Without a printer list the booth has one printer: the
            # "printer" section. A number beyond the list changes nothing.
            index = int(parts[1]) - 1
            liste = patch.get("printers", copy.deepcopy(local.get("printers") or []))
            if liste and 0 <= index < len(liste) and isinstance(liste[index], dict):
                liste[index][parts[2]] = value
                patch["printers"] = liste
            elif not liste and index == 0:
                patch.setdefault("printer", copy.deepcopy(local.get("printer") or {}))[parts[2]] = value
            continue
        if len(parts) == 1:
            patch[parts[0]] = value
            continue
        node = patch.setdefault(parts[0], copy.deepcopy(local.get(parts[0]) or {}))
        for part in parts[1:-1]:
            if not isinstance(node.get(part), dict):
                node[part] = {}
            node = node[part]
        node[parts[-1]] = value
    return patch


# Every profile field that names a file in the asset folder. One table, copied
# verbatim in app/ecosystem.py (a test keeps both equal and scans both schemas
# for file-like fields that are missing here): the gallery sends exactly these
# files, the self-check reports exactly these when they are missing. "*" stands
# for any key at that level. A value may be a name or a list of names; colours
# ("#rrggbb") and empty values are not files. LUTs are not in here: they are
# addressed by look key, not by file name.
MEDIA_FIELDS = (
    "start.media", "start.logo", "countdown.smile_media", "attract.media",
    "backdrops.*.image", "phase_media.*.media", "frame.overlay", "frame.print_overlay",
    "sounds.countdown", "sounds.shutter", "sounds.done", "sounds.print",
    "theme.font_file", "lock.image", "collage_templates", "backgrounds",
    "chroma.backgrounds", "gif.print_template", "boomerang.frame_templates", "video.frame_templates",
)


def media_files(profile: Dict[str, Any]) -> Dict[str, str]:
    """``{file name: field}`` for every file the profile points at."""
    out: Dict[str, str] = {}

    def walk(node: Any, parts: List[str], path: str) -> None:
        if not parts:
            for value in (node if isinstance(node, list) else [node]):
                if isinstance(value, str) and value.strip() and not value.startswith("#"):
                    out.setdefault(value.strip(), path)
            return
        if not isinstance(node, dict):
            return
        head, rest = parts[0], parts[1:]
        for key in (list(node) if head == "*" else [head]):
            if key in node:
                walk(node[key], rest, f"{path}.{key}" if path else key)

    for field in MEDIA_FIELDS:
        walk(profile or {}, field.split("."), "")
    return out


_CORNER_WORDS = {"top-left": "oben links", "top-right": "oben rechts",
                 "bottom-left": "unten links", "bottom-right": "unten rechts"}


def admin_corner_hint(profile: Dict[str, Any]) -> str:
    """Where the operator presses to reach the booth menu, e.g. "3 Sekunden oben rechts".

    Taken from the profile (layout.corner, access.hotspot_seconds): the
    console texts said "oben links" for years while the default had moved to
    the top right.
    """
    corner = str(((profile or {}).get("layout") or {}).get("corner") or "top-right")
    try:
        seconds = int(((profile or {}).get("access") or {}).get("hotspot_seconds") or 3)
    except (TypeError, ValueError):
        seconds = 3
    return f"{seconds} Sekunden {_CORNER_WORDS.get(corner, _CORNER_WORDS['top-right'])}"


DEFAULT_PROFILE: Dict[str, Any] = {
    "version": 0,
    # "published": None = the gallery did not say (an older gallery, or an
    # event made at the booth); the self-check then cannot tell whether the
    # start QR leads to an open gallery.
    # "layouts_fixed": the gallery says the assigned event sets the layouts
    # ("Layouts für dieses Event"); the templates page then shows its switches
    # as "vom Event vorgegeben" instead of letting a change vanish at the next sync.
    # "frames_fixed": the same for the frames of boomerang and video.
    "event": {"slug": "", "name": "", "share_key": "", "base_url": "", "published": None,
              "layouts_fixed": False, "frames_fixed": False},
    # Was der Gast selbst wählen darf. Aus heißt nicht „fehlt“, sondern: die Box
    # nimmt die Voreinstellung und überspringt den Schritt — ein Bildschirm weniger
    # für Gäste, die mit Auswahl nichts anfangen können.
    # Ein Gruß vom Gast auf dem Abzug: „Anna & Ben, 12.7." statt der Unterzeile
    # des Anlasses. Gefragt wird vor der Aufnahme, damit der Text schon beim
    # Entwickeln dasteht und niemand zweimal wartet.
    "guest_text": {
        "enabled": False,
        "max_len": 40,              # auf einem Abzug ist mehr nicht lesbar
        "required": False,          # aus heißt: „ohne" ist eine gültige Antwort
        "modes": ["photo", "collage"],   # bei GIF und Video steht kein Text
    },
    # Guests draw or sign on the finished picture with a finger before it is
    # printed and shared. Off until the operator wants it. Not for GIF and
    # video: there is no single still to draw on. The colours are what the
    # guest can pick; anything else is refused by the booth.
    "draw": {
        "enabled": False,
        "modes": ["photo", "collage"],
        "colors": ["#ffffff", "#111111", "#e5484d", "#f5b700", "#2f80ed", "#27ae60"],
    },
    "guest_choice": {
        "format": True,             # Hoch, Story, Quer …
        "look": True,               # Filter nach der Aufnahme
        "collage_template": True,   # Collage-Vorlage
        "background": True,         # Collage-Hintergrund
    },
    # Wie viele Filter der Gast überhaupt zu sehen bekommt. Nicht dasselbe wie
    # "guest_choice.look": Das schaltet die Auswahl ganz ab, das hier begrenzt
    # sie. Auf einer Feier mit Schlange sind drei Filter besser als zwölf —
    # jede weitere Kachel ist eine Entscheidung mehr, die niemand treffen will.
    # 0 = alle, die eingeschaltet sind.
    "looks_max": 0,
    # Und was mit dem Filter passiert, wenn der Gast keinen wählen soll:
    #   "ask"  — Gast wählt (Standard)
    #   "auto" — die Box nimmt "looks_auto" und zeigt die Auswahl gar nicht
    #   "off"  — kein Filter, das Bild bleibt, wie es aus der Kamera kam
    "looks_mode": "ask",
    "looks_auto": "original",
    "languages": ["de", "en"],                    # erste = Standard; Gäste können umschalten
    "modes": ["photo", "collage", "gif", "boomerang", "video"],
    "formats": ["4:5", "9:16", "3:2"],            # Framing-Auswahl für Einzelfotos
    "default_format": "4:5",
    # Aufsteckblitz. Der Grund für diesen Block ist die Ladezeit: löst die Box
    # schneller aus, als der Blitz nachlädt, wird das zweite Bild einer Collage
    # dunkel — und niemand merkt es, bis der Druck herauskommt.
    "flash": {
        "enabled": False,
        "recycle_seconds": 2.5,     # Pause nach jeder Auslösung, bis der Blitz wieder kann
        "preview_gain": 0,          # Live-Bild aufhellen (0–100 %): bei Blitzbelichtung ist es sonst fast schwarz
        # Instead of the fixed percentage: stretch each live frame towards a
        # mean brightness (at most 4×). Live view only, never the pictures.
        "preview_gain_auto": False,
        # Beide Betriebsarten sind vorgesehen. M heißt: gleiche Helligkeit den
        # ganzen Abend, der Blitz macht das Licht. Av heißt: der Raum bleibt
        # sichtbar, dafür wandert die Belichtungszeit mit dem Umgebungslicht.
        # "egal" schaltet die Prüfung ab.
        "expected_mode": "any",     # any | M | Av
    },
    # Der Bildschirm als Licht: Bei Webcam- und Handy-Boxen ohne Blitzgerät ist
    # die weiße Fläche das einzige, was den Gast beleuchtet. Sie muss *vor* der
    # Belichtung stehen — deshalb der Vorlauf — und danach kurz bleiben.
    # Nicht zu verwechseln mit dem kurzen Weißschimmer nach dem Auslösen: der
    # ist Effekt, kein Licht.
    "screen_flash": {
        "enabled": False,
        "brightness": 100,          # 20–100 % — dunkler heißt weniger Licht, nicht nur blasser
        "lead_ms": 250,             # Vorlauf, bevor ausgelöst wird
        "hold_ms": 400,             # Nachlauf, damit die Belichtung sicher im Licht liegt
        "color": "#ffffff",         # reinweiß leuchtet am hellsten, warmes Weiß schmeichelt der Haut
    },
    "countdown": {"first": 3, "next": 2, "style": "overlay", "review_seconds": 1.5, "sound": True,
                  "live_view": "fullscreen", "crop_guide": True,
                  # live_view: "fullscreen" | "frame" | "off" (no live picture while
                  # counting; a video recording still shows it).
                  # Kurz vor dem Auslösen ein Bild einblenden — „Bitte lächeln!".
                  # Ohne es schauen die Gäste beim letzten Zaehlschritt nach unten
                  # aufs Display statt in die Kamera. Dateiname eines Startmediums,
                  # leer = kein Bild.
                  "smile_media": "", "smile_ms": 700,
                  # No picture at hand: a short text or an emoji instead
                  # ("Bitte lächeln!", "😀"). The picture wins if both are set.
                  "smile_text": "",
                  # A line between the pictures of a series ("Jetzt alle
                  # winken!"), texts pose.1 to pose.5. For booths without sound.
                  "poses": False,
                  # A button to step out during countdown and recording; it
                  # throws the run away and goes back to the start.
                  "cancel": False,
                  # How the number looks: "number" (as before) | "ring" (the
                  # number inside a ring that runs out over the countdown);
                  # colour of number and ring ("" = number as before, ring in
                  # the main colour); size m | l | xl, l is the size so far.
                  "display": "number", "color": "", "size": "l"},
    "retake": True,
    # Sekunden, die der Durchsehen-Schirm nach einer Serie offen bleibt, bevor
    # es von allein weitergeht — sonst steht die Box, wenn jemand weggeht.
    "review_seconds": 12,
    # "format" is the operator's; the guest picks among "formats" like for a
    # photo (guest_choice.format), or with "guest_choice" even when that is
    # off. "seconds": how long the camera records (1-10). "print": a print
    # sheet on the collage layouts from key frames of the recording (off).
    # "layout" "crop" fills a portrait film; "blur" keeps the whole frame on a
    # blurred backdrop. Crop by default (owner's decision 15.11.2026); a
    # stored profile that says "blur" keeps it.
    # "frames" (17.11.2026): the film plays in the window of a motion frame
    # ("Rahmen", media/motion_frames.py), off by default. "frame_templates"
    # are own frame files, "frame_builtins" ids of the shipped ones (both
    # empty: every built-in), "frame_none" offers "Ohne Rahmen",
    # "frame_default" is pre-selected ("" = the first, "none" = without),
    # "frame_guest_choice" off applies the default without asking.
    # "source" (17.11.2026, boomerang and video): "live" films from the live
    # view (default); "camera" has the camera record a real movie onto its
    # SD card, downloads it and builds the clip from it (1080x1920 for 9:16
    # instead of 360x640). Needs a camera that films over USB (Canon SDK)
    # and a card in it; any failure films from the live view instead.
    "boomerang": {"format": "9:16", "layout": "crop", "source": "live", "seconds": 2.0, "fps": 20, "loops": 3, "width": "auto",
                  "max_width": 1080, "scale": "native", "guest_choice": False, "print": False,
                  "frames": False, "frame_templates": [], "frame_builtins": [], "frame_none": True,
                  "frame_default": "", "frame_guest_choice": True},
    # GIF: several stills in a row, like dslrBooth. Guests keep the single photos too.
    # Six pictures shown 200 ms each: four at 400 ms looked like a slide show,
    # not like movement. "video_seconds" is how long the MP4 next to the GIF
    # runs at least — it repeats whole passes until then (0 = three passes).
    "gif": {"shots": 6, "interval": 1.0, "frame_ms": 200, "pingpong": True, "format": "4:5",
            "loops": 0, "also_mp4": True, "video_seconds": 15, "scale": "native", "max_width": 1080,
            # Aus einer Collage zusätzlich ein GIF bauen — aus denselben
            # Aufnahmen, ohne dass der Gast ein zweites Mal posieren muss. Der
            # Druck bleibt die Collage; das GIF ist die Zugabe für das Telefon.
            # Erst ab genügend Einzelbildern, sonst zuckt es nur.
            "from_collage": False, "from_collage_min": 4,
            # The sheet a GIF run is printed on: one of the own templates (file
            # name), "" = the built-in sheet for that number of pictures.
            "print_template": "",
            # Dasselbe GIF zusätzlich als Boomerang fürs Handy. Eigene Maße:
            # Aufgenommen wird meist 4:5, gebraucht wird 9:16 — und dabei darf
            # niemand am Rand wegfallen, deshalb „blur" als Voreinstellung
            # (ganzes Bild mittig, dahinter vergrößert und unscharf).
            # „guest_choice" heißt: Der Gast entscheidet am Bildschirm, ob er
            # ihn haben will; aus heißt, er entsteht immer mit.
            # On by default and without asking since 14.11.2026: every film
            # is to be in the gallery in 9:16 as well. It runs at least
            # "video_seconds", like the film next to the GIF.
            "boomerang": {"enabled": True, "format": "9:16", "layout": "blur",
                          "fps": 15, "loops": 3, "max_width": 1080, "scale": "native",
                          "guest_choice": False}},
    "video": {"format": "9:16", "layout": "blur", "source": "live", "seconds": 8, "fps": 25, "width": "auto",
              "max_width": 1080, "scale": "native",
              "frames": False, "frame_templates": [], "frame_builtins": [], "frame_none": True,
              "frame_default": "", "frame_guest_choice": True},
    "looks": ["original", "warm", "cool", "mono", "film", "punch", "soft"],
    # 3D LUTs (.cube). Built-in ones ship with the app, more can be dropped into
    # <data>/luts or uploaded in the gallery as an asset of kind "lut".
    "luts": {
        "enabled": True,          # offer LUT filters to the guest at all
        "strength": 1.0,          # 0..1 for the guest-selectable LUT filters
        "show": [],               # ids to offer (empty = every LUT found on the box)
        "baked": "",              # id of a LUT burned into every photo before the filter
        "baked_strength": 1.0,
    },
    # Greenscreen. Chromaticity keying, no AI. Backgrounds are assets.
    "chroma": {
        "enabled": False,
        "color": "auto",          # auto | green | blue | #rrggbb
        "tolerance": 0.35,
        "softness": 0.08,
        "spill": 0.4,
        "fit": "cover",           # cover | contain | stretch
        "backgrounds": [],        # asset files or #rrggbb, guest picks one
        "guest_choice": True,     # guest may pick; otherwise the first one is used
    },
    # E-mail delivery straight from the booth. Queued, so it survives being offline.
    "mail": {
        "enabled": False,
        # Wer verschickt: "gallery" lässt die Galerie senden — dann braucht die
        # Box kein Postfach, am Veranstaltungsort keinen offenen SMTP-Port, und
        # der Absender ist überall derselbe (SPF/DKIM an einer Stelle).
        # "smtp" ist der alte Weg für Boxen, die ohne Galerie laufen.
        "transport": "gallery",
        "host": "", "port": 587, "security": "starttls",   # starttls | ssl | none
        "user": "", "password": "",
        "from_addr": "", "from_name": "",
        "subject": "", "body": "",
        "attach": "web",          # web | original | none (link only)
        "bcc": "",
        "dry_run": False,
    },
    # Rahmen und Textzeilen auf jedem Bild — ohne eigene Vorlage zu schreiben.
    # x/y stehen in Prozent, damit dieselbe Einstellung für 4:5, 9:16 und Druck passt.
    "frame": {
        "enabled": False,
        "overlay": "",            # PNG über das ganze Bild (Asset-Datei)
        "print_overlay": "",      # optional eigener Rahmen für die Druckkarte
        "on_photo": True,
        "on_print": True,
        "texts": [],              # [{"value","size","color","align","x","y","target","bold","shadow"}]
    },
    # QR auf dem Druck: Größe und Ecke frei wählbar
    "print_qr": {"size": 220, "position": "bottom-right", "margin": 40},
    # Datensparsamer Betrieb (Kita, Schule, Verein): Bild machen, drucken, löschen.
    # Kein Upload, keine Galerie, kein QR, keine E-Mail — und zwar erzwungen, nicht
    # nur ausgeblendet. Was nie das Gerät verlässt, muss niemand später löschen.
    "privacy": {
        "mode": "normal",          # normal | print_only
        "delete_after": "print",   # print (nach dem Druck) | done (nach dem Ergebnis) | seconds
        "delete_seconds": 120,     # Sicherheitsnetz: spätestens dann ist alles weg
        "keep_originals": False,   # auch die Einzelaufnahmen gehen sofort
        "notice": "",              # Hinweis auf dem Startbildschirm, leer = Standardtext
    },
    # Mirror-Booth: Die Box steht hinter einem halbdurchlässigen Spiegel, der Gast
    # sieht sein Spiegelbild. Damit die Oberfläche richtig herum erscheint, wird
    # sie gespiegelt — Texte bleiben lesbar, dafür sorgt die Oberfläche selbst.
    "mirror": {"horizontal": False, "vertical": False},
    # Töne. Leer = eingebauter Piepser; Asset-Dateien überschreiben ihn.
    "sounds": {"enabled": True, "volume": 0.8, "countdown": "", "shutter": "", "done": "", "print": ""},
    # Bildschirmschoner: was läuft, wenn niemand da ist
    # Bildschirmschoner: was die Box zeigt, wenn niemand da ist. Ganz abschaltbar
    # (enabled=False oder source="off"); jede Quelle lässt sich einzeln fahren.
    "attract": {
        "enabled": True,
        "after_seconds": 120,       # wie lange Ruhe, bevor er anspringt
        "source": "recent",         # recent | media | own | text | blank | off
        "media": [],                # eigene Auswahl für source="own" (Bild, GIF, Video)
        "seconds_per_item": 6,
        "show_hint": True,
        "hint_style": "bar",        # bar (unten) | center (mittig groß) | none
        "color": "#000000",         # Fläche bei source="blank"/"text"
        "text_color": "#ffffff",
        "dim": 25,                  # Bilder/Videos abdunkeln (0–80 %), damit der Hinweis lesbar bleibt
        # cover (formatfüllend) | contain (ganz sichtbar) | auto (formatfüllend
        # nur, wenn Bild und Bildschirm gleich ausgerichtet sind). Default auto
        # (owner's decision 15.11.2026): a portrait shot on a landscape screen is
        # shown whole. A stored profile that says "cover" keeps it.
        "fit": "auto",
        "mute": True,               # Ton bei Videos
        # Size of the hint "Bildschirm berühren …" in percent (100–250).
        "hint_size": 100,
    },
    # Ausgehende Webhooks: eigene URL bekommt Bescheid, wenn etwas passiert
    # Was die Box dem Betreiber meldet — und wie. Drei Stufen: info (es
    # laeuft), warnung (koennte gleich kritisch werden), kritisch (geht
    # nicht mehr). Mail und Anzeige an der Box getrennt schaltbar, jede mit
    # eigener Schwelle. Die Anzeige erscheint nur auf dem Startbildschirm —
    # nie ueber dem Gesicht eines Gastes.
    # Dauerbetrieb: alte Aufnahmen von der Platte nehmen. Geloescht wird
    # nur, was die Galerie bestaetigt hat — lieber eine Woche zu lang
    # behalten als eine Aufnahme zu frueh weg. Fasst die Galerie nicht an;
    # dort gilt die Aufbewahrungsfrist des Events.
    "aufraeumen": {
        "enabled": False,
        "nach_tagen": 14,
        # Ohne Bestaetigung wird nichts geloescht. Wer das abschaltet, loescht
        # nach Zeit — auch das, was nie angekommen ist.
        "nur_bestaetigte": True,
        # Die juengsten N Durchgaenge bleiben immer liegen, damit am Morgen
        # danach noch nachgedruckt werden kann.
        "behalten_mindestens": 20,
    },
    "meldungen": {
        "mail": {"enabled": False, "to": "", "ab_stufe": "warnung"},
        "box": {"enabled": True, "ab_stufe": "kritisch"},
        # Dieselbe Ursache geht erst nach dieser Zeit erneut hinaus. Die
        # Kamera meldet ihren Verlust alle fuenf Sekunden; der Betreiber
        # braucht das einmal.
        "wiederholen_minuten": 60,
    },
    "webhooks": {"enabled": False, "url": "", "secret": "", "events": ["media_ready"]},
    # Auslösen von außen (Buzzer über HTTP, Leitstand, Fernwartung)
    "http_trigger": {"enabled": False, "token": "", "mode": "photo"},
    "collage_templates": [],
    # Wie ein Foto in ein Bildfeld der Vorlage kommt, wenn die Lagen nicht
    # zusammenpassen — hochkant montierte Kamera, quer entworfene Vorlage.
    #   "auto"        — passt: beschneiden. Passt nicht: ganzes Foto einpassen.
    #   "beschneiden" — immer formatfuellend (das Verhalten bis 20.09.2026)
    #   "einpassen"   — immer ganz sichtbar, der Rest bleibt Blattfarbe
    "collage_fit": "auto",
    # Welche der mitgelieferten Collagen-Vorlagen angeboten werden. Leer = alle.
    # Eigene Vorlagen (hochgeladene JSON-Dateien) stehen in "collage_templates"
    # und kommen dazu; sind dort welche, gelten nur noch diese.
    "collage_builtins": [],                      # Asset-Dateien (JSON-Vorlagen)
    # The plain layouts ("Schlicht", 15.11.2026) are photos only; their QR
    # code shows only with this switch (off: no code on the sheet).
    "collage_plain_qr": False,
    # On: offer exactly the ticked own templates and built-ins together (an
    # event's "Layouts für dieses Event" sets it). Off: as before, own
    # templates replace the built-ins.
    "collage_exact": False,
    # Was aus einem Collagen-Durchgang in der Galerie landet:
    #   "collage" — nur das fertige Blatt (Standard)
    #   "photos"  — nur die Einzelbilder, jedes als eigenes Foto
    #   "both"    — beides
    # Der Abzug auf Papier bleibt davon unberührt; hier geht es nur um die Galerie.
    "collage_upload": "both",
    # Wie die Vorlagen aussehen. Die Vorlage beschreibt die Anordnung, dieser
    # Block die Farben, die Schrift und die beiden Zeilen — einmal gesetzt, gilt
    # er für alle Vorlagen. „preset" ist der Anlass (clean, hell, geburtstag,
    # hochzeit, feier, firma); alles darunter darf einzeln überschrieben werden.
    "collage_style": {
        "preset": "clean",
        "background": "", "ink": "", "muted": "", "accent": "",
        "headline": "", "subline": "", "font": "", "radius": "", "frame": "",
    },
    "backgrounds": [],                            # Asset-Dateien, vom Gast wählbar (Collage-Hintergrund)
    "start": {
        "media": [],                              # Bilder/GIFs/Videos; mehrere = Wechsel
        "media_seconds": 8,
        "layout": "bottom",                       # bottom | left | right | center | custom
        "button_size": "l",                       # m | l | xl
        "show_gallery_button": True,
        # pill (as before, beside the language) | prominent (filled in the
        # accent colour and larger, still without a picture)
        "gallery_style": "pill",
        # Gallery button, language button and lock on the start screen, sized
        # together: m (the old look) | l | xl. "l" by default (owner's
        # decision 15.11.2026: on a 14" touch screen they were too small).
        "corner_size": "l",
        # Die zweite Zeile auf den Modus-Kacheln („Mehrere Bilder, ein Druck").
        # Aus heißt: nur die Namen. Der Text selbst steht unter „Texte" und darf
        # auch leer sein — dann fällt er ebenfalls weg.
        "show_mode_hints": True,
        # Die Anleitung, die an fast jeder Box im Einsatz steht: „1. Foto
        # antippen, 2. Layout wählen, 3. Buzzer drücken …". Der Text steht
        # unter „Texte" (start.steps, eine Zeile je Schritt) und ist damit
        # übersetzbar und frei formulierbar.
        # aus | immer | knopf — „knopf" blendet sie erst auf Tippen ein; dann
        # steht sie nicht den ganzen Abend im Weg.
        "steps": "aus",
        # Die letzte Aufnahme als kleine Kachel — Gäste sehen, dass die Box
        # arbeitet, und erkennen ihren Abend wieder.
        "show_last": False,
        # QR zur Galerie des Events, schon bevor jemand ausgelöst hat.
        "show_qr": False,
        # Logo oder Signatur (Dateiname aus den Medien der Box, am besten PNG
        # mit Transparenz). Leer = keins.
        "logo": "",
        "logo_size": 18,                          # Breite in Prozent der Bildschirmbreite
        # Frei gesetzte Elemente: Werte in Prozent der Bildschirmfläche.
        # Setzbar sind Titel, jede Modus-Kachel, Galerie-Knopf, Anleitung,
        # letzte Aufnahme, QR, Logo und die Statuszeile.
        "positions": {},                          # {"photo": {"x": 8, "y": 62, "w": 26}, "status": {…}}
        # Colour behind the start screen when no start media is set ("" = the
        # blurred live picture, as before) and a flat dim over picture or
        # colour in percent (0–80). -1 keeps the old gradient.
        "bg_color": "",
        "dim": -1,
        # An own start picture with headline, instructions and QR burned in:
        # the title (event name + "Tippen & lächeln") and the status line can
        # go — the operator notice then stays as a single dot. "fit": drift
        # (filled, slowly moving, as before) | cover (filled, still) |
        # contain (the whole picture, still).
        "show_title": True,
        "show_status": True,
        "fit": "drift",
    },
    # A backdrop per screen of the run: a picture from the booth's media or a
    # colour, dimmed so text stays readable. Empty = theme.bg, as before. On
    # the capture screen the camera picture stays in front.
    "backdrops": {
        "setup": {"image": "", "color": "", "dim": 0},
        "capture": {"image": "", "color": "", "dim": 0},
        "result": {"image": "", "color": "", "dim": 0},
    },
    # One optional medium per phase of the run (picture, GIF or video from the
    # booth's media; videos play muted). "processing": while the result is
    # made, "between": between the shots of a series, "after": after the
    # result, before the start screen, "error": behind the error message.
    # fit: cover | contain; text: the phase's own text on top; loop: off keeps
    # the last frame of a video standing. A phase never waits for its medium;
    # only "after" with seconds > 0 adds time (0 = off). The idle picture is
    # the attract screen (attract.media), so there is no "idle" here.
    "phase_media": {
        "processing": {"media": "", "fit": "cover", "text": True, "loop": True},
        "between": {"media": "", "fit": "cover", "text": True, "loop": True},
        "after": {"media": "", "fit": "cover", "text": True, "loop": True, "seconds": 0},
        "error": {"media": "", "fit": "cover", "text": True, "loop": True},
    },
    "texts": {},                                  # {"de": {"start.title": "…"}, "en": {…}} überschreibt Standardtexte
    # Was aus dem Drucker kommt. Streifen entstehen als zwei Stück auf einem
    # 10x15-Blatt, das der Drucker in der Mitte schneidet — anders können
    # Fotodrucker das nicht.
    "print_layout": {
        "paper": "10x15",           # 10x15 | postcard | 13x18 | 15x20 | 5x15 | card
        "per_sheet": 1,             # 2 = zwei Streifen nebeneinander
        "margin_mm": 0,
        "dpi": 300,
        "fit": "cover",             # cover (beschneiden) | contain (ganz sichtbar, mit Rand)
        "rotate_to_fit": True,
        "background": "#ffffff",
    },
    "print": {"enabled": True, "auto": False, "copies_max": 2, "limit_session": 2, "limit_event": 0,
              "qr_on_print": True,
              # Sublimationsdrucker sind langsam: eine SELPHY CP1300 braucht laut Canon
              # rund 47 Sekunden je Postkarte. Bei fünf Leuten in der Schlange sind das
              # vier Minuten für den letzten — das soll die Box sagen können, statt den
              # Gast raten zu lassen.
              "seconds_per_print": 47,
              # Blatt je Kassette. 0 = keine Warnung. Die Box kann den Vorrat nicht
              # messen, nur mitzählen, was sie selbst gedruckt hat.
              "sheets_per_cassette": 0,
              # Nachschärfen vor dem Druck. Thermosublimation weicht Kanten auf;
              # ein Hauch Schärfe gleicht das aus. Zu viel sieht nach Handy aus.
              # off | light | medium | strong
              "sharpen": "light",
              # Nachdruck per Zifferntaste 1–9 am Ergebnisbildschirm — für den
              # Betreiber, der schnell drei Kopien braucht, ohne dreimal zu tippen.
              "number_keys": True,
              # Nachdrucken aus der Galerie an der Box. Getrennt schaltbar, weil
              # es zwei verschiedene Dinge sind: der Druck direkt nach der
              # Aufnahme und das spätere Nachdrucken durch irgendwen, der durch
              # die Galerie blättert. Die Kontingente gelten für beides.
              "reprint_from_gallery": True,
              # Paper or ribbon out: accept prints anyway and print them once the
              # printer is ready again (off = refuse, as before). At most
              # queue_max waiting prints; one older than queue_max_hours is dropped.
              "queue_while_empty": False, "queue_max": 12, "queue_max_hours": 6,
              # A template made for another paper of the same aspect family
              # (a 15x23 design on a SELPHY postcard) is scaled to the paper
              # of the first printer before it is rendered (vorlagen_kern.
              # fuer_papier). Off = the template's own canvas, as before.
              "fit_to_paper": False},
    "qr_on_screen": True,
    "result_seconds": 45,
    "idle_seconds": 60,
    "gallery_on_booth": True,
    "allow_delete": True,
    "disclaimer": {"enabled": False},
    # Consent as a choice per run (wedding size): before the shot the guest picks
    # "In die Galerie" or "Nur drucken". Off by default, so a profile without this
    # block behaves as before. "Nur drucken" is offered only while printing is on
    # and keeps that one run on the booth (data-minimal mode for one run). With
    # fewer than two options the screen is skipped. The note sits under the
    # buttons; {tage} becomes "days". The booth records option, text hash and
    # time per run, nothing about the person.
    "consent": {
        "enabled": False,
        "print_only": True,
        "days": 30,
        "note": {"de": "Galerie: nur mit Passwort, gelöscht nach {tage} Tagen. "
                       "Löschen jederzeit über euren QR-Code.",
                 "en": "Gallery: password protected, deleted after {tage} days. "
                       "Delete any time with your QR code."},
    },
    # Zugang an der Box: PIN für Verwaltung und Sperre. Die PIN steht im Profil,
    # damit sie aus der Ferne gesetzt werden kann — sie wird aber NIE an die
    # Gast-Oberfläche ausgeliefert (siehe public_profile), sonst stünde sie im
    # Browser jedes Gastes.
    "access": {
        "pin": "",                  # leer = die an der Box eingestellte PIN gilt weiter
        "lock_on_booth": True,      # Betreiber darf an der Box sperren und entsperren
        "hotspot_seconds": 3,       # so lange die Ecke (layout.corner) drücken, bis das Menü kommt
        # A visible lock in that corner instead of the hidden one: one tap or
        # click opens the menu (PIN first). With start.show_status off it is
        # the only thing on the start screen that is not for guests.
        "lock_icon": False,
    },
    # Auslösen: Buzzer, Tastatur, Touch. Ein Buzzer ist meistens eine
    # ausgeschlachtete Maus oder eine Tastatur mit einer Taste — beides kommt
    # hier an, ohne Treiber. Der Auslöser drückt immer die Hauptschaltfläche
    # des Bildschirms, auf dem der Gast gerade steht: Start → Modus, Aufbau →
    # Auslösen, Ergebnis → fertig. So braucht es keine Extralogik je Bildschirm.
    "controls": {
        "touch": True,                # Tippen und Klicken auf die Schaltflächen
        "keys_start": ["Space", "Enter", "NumpadEnter", "PageDown", "ArrowRight", "F13"],
        "keys_back": ["Escape"],      # zurück zum Start
        "keys_print": ["KeyP"],       # Drucken auf dem Ergebnisbildschirm
        "mouse_start": "off",         # off | left | right | middle | any — „left" ist der Maus-Buzzer
        "mouse_swallow": True,        # Buzzer-Klick nicht zusätzlich an die Schaltfläche darunter geben
        "hide_cursor": True,          # Mauszeiger ausblenden — bei einem Maus-Buzzer steht er sonst im Bild
        # On-screen keyboard for every text field of the booth's pages (osk.js):
        # Windows opens none inside the kiosk window. Off for a booth with a
        # real keyboard that wants the whole screen.
        "onscreen_keyboard": True,
        "repeat_ms": 1200,            # Prellschutz: ein zweiter Druck in dieser Zeit zählt nicht
        # Welchen Modus der Auslöser auf dem Startbildschirm öffnet. Leer heißt:
        # den ersten der Liste. Wer nur einen Knopf hat, aber die Collage zuerst
        # zeigen will, stellt das hier — ohne die Reihenfolge der Kacheln zu ändern.
        "start_mode": "",
        # Ein zweiter und dritter Knopf mit eigener Aufgabe. Ein Buzzer mit zwei
        # Tasten hängt meistens als Maus am Rechner: links auslösen, rechts z. B.
        # gleich in die Collage. Werte: off | trigger | back | print | gallery |
        # mode:<photo|collage|gif|boomerang|video>
        "mouse_right": "off",
        "mouse_middle": "off",
        # Tasten mit eigener Aufgabe, für Buzzer und Fußschalter, die mehr als
        # eine Taste senden: {"KeyF": "mode:photo", "KeyG": "mode:gif"}.
        # Gilt vor keys_start — wer eine Taste hier einträgt, bestimmt selbst,
        # was sie tut.
        "keys_actions": {},
        # The arrow keys move a visible focus over the guest screens (in a
        # row of choices left/right choose at once); Enter/Space press what
        # the arrows reached. On, the arrows are never start keys, even if
        # ArrowRight stands in keys_start - off for a presenter whose
        # "next" key is ArrowRight and that is the buzzer.
        "arrow_navigation": True,
    },
    # Der Bildschirm, auf dem die Box läuft, ist selten der, für den jemand
    # entworfen hat: ein getauschtes Panel, ein Fernseher mit Overscan, ein
    # Gehäuse mit Rahmen — und schon liegt die Ecke außerhalb des Sichtbaren.
    # Deshalb sind die Ränder einstellbar, statt fest im Entwurf zu stehen.
    # Angaben in Prozent der jeweiligen Bildschirmseite.
    "layout": {
        "safe_top": 0,
        "safe_right": 0,
        "safe_bottom": 0,
        "safe_left": 0,
        "scale": 100,               # Größe aller Bedienelemente in Prozent (80–130)
        "corner": "top-right",      # wo Sperren/Menü sitzt: top-left | top-right | bottom-left | bottom-right
        "guides": False,            # Hilfsrahmen einblenden, um die Ränder einzustellen
        # Tiles of the booth's gallery and the "Zurück" buttons: m (as before) | l | xl.
        "gallery_size": "m",
        "back_size": "m",
    },
    # Pause: von Hand („active") oder nach Plan. Der Plan ist der Fall, den
    # jeder Vermieter kennt — die Box steht ab 14 Uhr im Saal, gebucht ist sie
    # ab 19. Außerhalb des Fensters zeigt sie das Pausenbild, statt dass die
    # Kinder des Caterers das Farbband leer drucken.
    #   "from"/"until": "HH:MM" (leer = keine Grenze), "days": [0..6], 0 = Montag
    "lock": {"active": False, "image": None,
             "schedule": {"enabled": False, "from": "", "until": "", "days": []}},
    # Belichtung aus der Ferne; leer = an der Kamera eingestellter Wert bleibt
    # preview_*: the live view's own exposure, used only with flash.enabled
    # (the camera's live view simulates the flash exposure without the flash:
    # almost black). "" = same as the shot; preview_iso "auto" = raised stop by
    # stop until the live frame is bright enough, up to preview_iso_max, then
    # the shutter slower down to preview_shutter_min (only with preview_shutter
    # empty); after that the served frames are brightened in software.
    "camera": {"iso": "", "aperture": "", "shutter": "", "wb": "",
               "preview_iso": "auto", "preview_aperture": "", "preview_shutter": "",
               "preview_iso_max": "12800", "preview_shutter_min": "1/30"},
    "upload_originals": True,
    "accent": "#e5484d",
    # Aussehen der Oberfläche — Farben und Form der Bedienelemente.
    # Die Box setzt daraus CSS-Variablen; "accent" oben bleibt aus Gründen der
    # Abwärtskompatibilität bestehen und wird von theme.accent gespiegelt.
    "theme": {
        # Die Bühne der Box: warm-dunkel, damit das Foto das einzig Helle im
        # Bild ist. Werte aus dem Erscheinungsbild „Abzug" (Kiosk-Ebene).
        "accent": "#e7ad59",        # Bernstein 400 — auf dunkel 10,0:1
        "on_accent": "#0f0d09",     # Schrift auf der Hauptfarbe — 9,7:1
        "bg": "#0a0806",            # Hintergrund
        "panel": "#23201c",         # Flächen: Kacheln, zweite Knöpfe
        "text": "#f5f3f0",
        "muted": "#898580",         # Unterzeilen, Hinweise
        # Dunkel oder hell. Dunkel ist die Voreinstellung: Im abgedunkelten Raum
        # blendet eine helle Fläche, und das Foto soll das Hellste im Bild sein.
        # Bei Tageslicht, im Schaufenster oder in einer hellen Markenwelt ist
        # hell besser lesbar. „eigene" nimmt die sechs Farben von oben.
        "mode": "dunkel",           # dunkel | hell | eigene
        "radius": "pill",           # pill | round | soft | square
        "size": "m",                # s | m | l — Höhe und Schriftgrad der Knöpfe
        "weight": "600",            # 500 | 600 | 700
        "uppercase": False,
        "secondary": "solid",       # solid | outline — Stil der zweiten Knöpfe
        "font": "system",           # system (Inter) | display (Archivo) | serif | rounded | mono
        "font_file": "",            # hochgeladene Schriftart (Medienart „Schriftart“); schlägt „font“
    },
    "qr_url_template": "",                        # z. B. Picdrop-Galerie statt eigener Galerie
    # Free QR destination: works without the gallery. {uid} and {event} are filled in.
    "qr": {"mode": "auto", "url": "", "utm": "", "label": ""},
    # Download without internet. A router or hotspot next to the booth spans a
    # Wi-Fi — the booth sets up nothing itself. The result screen then shows
    # "1. join the Wi-Fi" (WIFI: code) and "2. load your photo" (a signed link
    # to the booth's own guest page, valid link_hours). mode: auto (only while
    # the gallery cannot be reached) | always | off. security: WPA (WPA2/3) |
    # WEP | nopass; an empty password means an open network.
    "wifi_share": {"enabled": False, "mode": "auto", "ssid": "", "password": "", "security": "WPA",
                   "hidden": False, "link_hours": 24},
}

_LOCK = threading.RLock()


def data_dir() -> Path:
    base = os.environ.get("BOOTH_DATA")
    if base:
        p = Path(base)
    elif os.name == "nt":
        p = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "PhotoboxBooth"
    elif sys.platform == "darwin":
        # Dorthin gehören Programmdaten auf einem Mac; ~/.local/share funktioniert
        # zwar auch, ist dort aber an keiner Stelle sichtbar, wo jemand nachsieht.
        p = Path.home() / "Library" / "Application Support" / "PhotoboxBooth"
    else:
        p = Path.home() / ".local" / "share" / "photobox-booth"
    p.mkdir(parents=True, exist_ok=True)
    return p


def _merge(defaults: Dict[str, Any], incoming: Any) -> Dict[str, Any]:
    """Rekursiv mergen, nur bekannte Schlüssel, Typen der Defaults erzwingen."""
    out = copy.deepcopy(defaults)
    if not isinstance(incoming, dict):
        return out
    for key, dval in defaults.items():
        if key not in incoming:
            continue
        val = incoming[key]
        if isinstance(dval, dict) and dval:
            out[key] = _merge(dval, val)
        elif isinstance(dval, dict):                 # freie Wörterbücher (z. B. Texte)
            out[key] = val if isinstance(val, dict) else dval
        elif isinstance(dval, bool):
            out[key] = bool(val)
        elif isinstance(dval, int) and not isinstance(dval, bool):
            try:
                out[key] = int(val)
            except (TypeError, ValueError):
                pass
        elif isinstance(dval, float):
            try:
                out[key] = float(val)
            except (TypeError, ValueError):
                pass
        elif isinstance(dval, str):
            out[key] = str(val) if val is not None else dval
        elif isinstance(dval, list):
            out[key] = list(val) if isinstance(val, list) else dval
        else:  # None-Default (z. B. start_screen) oder dict ohne Vorgabe
            out[key] = val
    return out


_PROFILE_WARNED: set = set()


def safe_profile(profile: Dict[str, Any]) -> Dict[str, Any]:
    """The merged profile with every value outside the profile rules dropped.

    Same rules as the gallery (``profile_rules.py``, R2-U1): a profile from
    the gallery or a ``profile.json`` that is old or was edited by hand must
    not put markup into the kiosk. Nothing is refused here, the booth has to
    start: a bad entry is left out (a bad list entry alone, a bad single
    value falls back to its default) and logged once.
    """
    from . import profile_rules
    merged = _merge(DEFAULT_PROFILE, profile)
    clean, problems = profile_rules.check(merged, DEFAULT_PROFILE, strict=False)
    if problems:
        paths = ", ".join(sorted({p[0] for p in problems}))[:300]
        if paths not in _PROFILE_WARNED:
            _PROFILE_WARNED.add(paths)
            logging.getLogger(__name__).warning(
                "Profil: %d ungültige Werte verworfen, es gilt die Voreinstellung (%s)", len(problems), paths)
    return _merge(DEFAULT_PROFILE, clean)


class Store:
    """Thread-sicherer Zugriff auf local.json und profile.json."""

    def __init__(self, base: Path | None = None):
        self.base = base or data_dir()
        self.local_path = self.base / "local.json"
        self.profile_path = self.base / "profile.json"
        self.local = self._load(self.local_path, DEFAULT_LOCAL)
        self.profile = safe_profile(self._load(self.profile_path, DEFAULT_PROFILE))

    @staticmethod
    def _load(path: Path, defaults: Dict[str, Any]) -> Dict[str, Any]:
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            raw = {}
        return _merge(defaults, raw)

    @staticmethod
    def _atomic_write(path: Path, data: Dict[str, Any]) -> None:
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, path)

    def save_local(self, patch: Dict[str, Any]) -> Dict[str, Any]:
        with _LOCK:
            merged = copy.deepcopy(self.local)
            for k, v in patch.items():
                if v is None:
                    # Ausdrücklich zurücksetzen. Ein leeres dict genügt dafür
                    # nicht: Teil-Patches sollen ja gerade nichts wegwerfen.
                    merged[k] = copy.deepcopy(DEFAULT_LOCAL.get(k))
                elif isinstance(merged.get(k), dict) and isinstance(v, dict):
                    merged[k].update(v)
                else:
                    merged[k] = v
            self.local = _merge(DEFAULT_LOCAL, merged)
            self._atomic_write(self.local_path, self.local)
            return self.local

    def apply_profile(self, profile: Dict[str, Any]) -> bool:
        """Neues Profil vom Server übernehmen. True, wenn sich etwas geändert hat."""
        with _LOCK:
            new = safe_profile(profile)
            if new == self.profile:
                return False
            self.profile = new
            self._atomic_write(self.profile_path, new)
            return True
