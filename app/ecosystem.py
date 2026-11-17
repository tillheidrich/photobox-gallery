"""Fotobox-Ökosystem: Datenmodell für Box-Profile, Assets, Aufträge und Durchgänge.

Bewusst in einem eigenen Modul neben ``db.py``: Hier liegt alles, was die
Photobox Booth (eigene Aufnahme-Software) mit der Galerie verbindet.

Leitplanken
* Die Box ruft uns an, nie umgekehrt (``/api/v2/booth/sync``).
* Uploads sind idempotent über ``uid`` + ``variant``.
* Profile sind JSON mit Whitelist — der Server schreibt nichts in eine Box,
  was sie nicht kennt; die Box verwirft Unbekanntes ohnehin.
"""
from __future__ import annotations

import base64
import copy
import hashlib
import hmac
import json
import logging
import re
import sqlite3
import secrets
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from app import db, redact

JOB_TYPES = {
    "sync_now": "Jetzt synchronisieren",
    "doctor": "Box prüfen (Selbstdiagnose)",
    "test_capture": "Testfoto",
    "test_print": "Testdruck",
    "retry_failed": "Fehlgeschlagene Uploads erneut",
    "reset_print_counter": "Druckzähler zurücksetzen",
    "release_prints": "Wartende Drucke erneut",
    "paper_changed": "Papier nachgelegt — Papierzähler auf voll, wartende Drucke erneut",
    "ribbon_changed": "Farbband gewechselt — Farbbandzähler auf voll, wartende Drucke erneut",
    "fetch_update": "Neue Fassung herunterladen (noch nicht einspielen)",
    "install_update": "Neue Fassung einspielen (Box startet neu)",
    "lock": "Box sperren",
    "unlock": "Box entsperren",
    "restart_app": "App neu starten",
    "retry_mail": "E-Mails erneut senden",
    # Sync-Programm aus der Ferne: prüfen, neu einlesen, einstellen, Ordner suchen.
    # One list, not two: the keys here must equal ALLOWED_JOBS in
    # sync/photobox_sync/agent.py (tests/test_jobs_arrive_or_expire.py checks it).
    "check_targets": "Ziele prüfen",
    "rescan": "Ordner neu prüfen",
    "set_sync_config": "Sync-Einstellungen ändern",
    "browse_remote": "Ordner am Ziel anzeigen",
    # Kamera, Drucker, Ablage an der Box — nur, was REMOTE_LOCAL_KEYS erlaubt.
    "set_local_config": "Hardware-Einstellungen ändern (Kamera, Drucker, Ablage)",
    # Live remote control (MCP extra right "control", app/remote_control.py):
    # one job type, the action inside comes from a fixed list on both sides,
    # and the booth refuses it unless remote control is allowed at the booth.
    "remote_control": "Fernsteuerung (Aktion aus fester Liste)",
    # Queues on the booth page: payload {"ids": [...]} for single entries,
    # {"confirm": "all"} for everything (two steps in the page, checked at the box).
    "discard_prints": "Drucke aus der Warteschlange verwerfen",
    "discard_failed_uploads": "Fehlgeschlagene Uploads verwerfen",
    # The booth's second upload target (app/second_target.py): refused by the
    # booth unless allowed there; the password travels sealed to the booth's
    # key, and the payload is emptied once the booth answered or the job expired.
    "set_second_target": "Zweites Ziel einstellen (Picdrop, FTP, SFTP)",
    "check_second_target": "Zweites Ziel prüfen",
    "backfill_second_target": "Bisherige Aufnahmen dieses Events an das zweite Ziel nachsenden",
    # A different SFTP host key, compared by a person, accepted on purpose (A-3c).
    "accept_second_target_host_key": "Neuen Server-Schlüssel des zweiten Ziels annehmen",
}

#: Local settings of a booth that the gallery may change (job
#: ``set_local_config``): camera, printer and storage choices, nothing else.
#: Nothing on this list names a server, a key, a PIN, an address or a path —
#: those decide where pictures go and which code runs (an SDK DLL), so they
#: are set at the booth. The same dict lives in booth/boothcore/config.py and is
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


ASSET_KINDS = {
    "start": "Startbildschirm (Bild, GIF, Video)",
    "background": "Collage-Hintergrund",
    "template": "Collage-Vorlage (JSON)",
    # Own asset kind, not "template": every list of print templates (editor,
    # event layouts, booth form, MCP) stays exactly as it was.
    "motion": "Rahmen für Boomerang/Video (JSON)",
    "overlay": "Overlay (PNG)",
    "lock": "Pausenbild",
    "lut": "LUT-Filter (.cube)",
    "scene": "Greenscreen-Hintergrund",
    "frame": "Rahmen über dem Bild (PNG)",
    "sound": "Ton (MP3/WAV)",
    "font": "Schriftart (WOFF2/TTF/OTF)",
}

# LUTs, die mit der Box ausgeliefert werden (Spiegel von
# booth/boothcore/media/luts_builtin). Dazu kommen hochgeladene Assets der Art
# "lut" — die Box findet beide unter derselben Kennung.
BUILTIN_LUTS = [
    "bleach-bypass", "classic-mono", "earthy", "faded-print", "golden-warm",
    "natural-portrait", "soft-cinema", "soft-matte", "vivid-colour", "warm-film",
]

# Old keys of built-in looks that were named after Kodak and Fujifilm films
# until 0.3 (trademarks; the looks are not those films). Copy of
# boothcore.media.luts.LEGACY_KEYS, kept in step by a test. Used to show old
# selections under their new names in the form. Profiles sent to a booth are
# not rewritten: an older booth knows only the old names.
LEGACY_LUT_KEYS: Dict[str, str] = {
    "film-ektar-100": "vivid-colour",
    "film-fuji-800z": "soft-matte",
    "film-gold-200": "faded-print",
    "film-portra-400": "natural-portrait",
    "fuji-eterna": "soft-cinema",
    "kodak-gold": "golden-warm",
    "mono-trix-400": "classic-mono",
}


def canonical_lut(key: str) -> str:
    """Current key of a look, with or without the ``lut-`` prefix."""
    key = str(key or "")
    prefix = "lut-" if key.startswith("lut-") else ""
    bare = key[len(prefix):]
    return prefix + LEGACY_LUT_KEYS.get(bare, bare)

# Spiegel der Box-Defaults (boothcore/config.py). Die Box merged ohnehin mit
# ihren eigenen Defaults — hier stehen sie, damit das Admin-Formular etwas zeigt.
DEFAULT_PROFILE: Dict[str, Any] = {
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
    "languages": ["de", "en"],
    "modes": ["photo", "collage", "gif", "boomerang", "video"],
    "formats": ["4:5", "9:16", "3:2"],
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
                  # Way out during countdown and recording (throws the run away).
                  "cancel": False,
                  # Look of the number: number | ring, colour ("" = as before),
                  # size m | l | xl (l = the size so far).
                  "display": "number", "color": "", "size": "l"},
    "retake": True,
    # Sekunden, die der Durchsehen-Schirm nach einer Serie offen bleibt, bevor
    # es von allein weitergeht — sonst steht die Box, wenn jemand weggeht.
    "review_seconds": 12,
    # layout: crop by default (owner's decision 15.11.2026); "blur" stays when stored.
    # Frames ("Rahmen") for boomerang and video: see boothcore/config.py.
    "boomerang": {"format": "9:16", "layout": "crop", "source": "live", "seconds": 2.0, "fps": 20, "loops": 3, "width": "auto", "max_width": 1080, "scale": "native", "guest_choice": False, "print": False,
                  "frames": False, "frame_templates": [], "frame_builtins": [], "frame_none": True,
                  "frame_default": "", "frame_guest_choice": True},
    "video": {"format": "9:16", "layout": "blur", "source": "live", "seconds": 8, "fps": 25, "width": "auto", "max_width": 1080, "scale": "native",
              "frames": False, "frame_templates": [], "frame_builtins": [], "frame_none": True,
              "frame_default": "", "frame_guest_choice": True},
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
    "looks": ["original", "warm", "cool", "mono", "film", "punch", "soft"],
    "luts": {"enabled": True, "strength": 1.0, "show": [], "baked": "", "baked_strength": 1.0},
    "chroma": {"enabled": False, "color": "auto", "tolerance": 0.35, "softness": 0.08, "spill": 0.4,
               "fit": "cover", "backgrounds": [], "guest_choice": True},
    # "transport": "gallery" lässt die Galerie senden — dann braucht die Box kein
    # Postfach, am Veranstaltungsort keinen offenen SMTP-Port, und der Absender
    # ist überall derselbe (SPF/DKIM an einer Stelle). "smtp" ist der alte Weg
    # für Boxen, die ohne Galerie laufen.
    "mail": {"enabled": False, "transport": "gallery",
             "host": "", "port": 587, "security": "starttls", "user": "", "password": "",
             "from_addr": "", "from_name": "", "subject": "", "body": "", "attach": "web", "bcc": "",
             "dry_run": False},
    "frame": {"enabled": False, "overlay": "", "print_overlay": "", "on_photo": True, "on_print": True,
              "texts": []},
    "print_qr": {"size": 220, "position": "bottom-right", "margin": 40},
    "privacy": {"mode": "normal", "delete_after": "print", "delete_seconds": 120,
                "keep_originals": False, "notice": ""},
    "mirror": {"horizontal": False, "vertical": False},
    "sounds": {"enabled": True, "volume": 0.8, "countdown": "", "shutter": "", "done": "", "print": ""},
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
    "collage_builtins": [],
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
    "backgrounds": [],
    # "show_mode_hints": die zweite Zeile auf den Modus-Kacheln („Mehrere Bilder,
    # ein Druck"). Aus heißt: nur die Namen. Der Text selbst steht unter „Texte"
    # und darf auch leer sein — dann fällt er ebenfalls weg.
    "start": {"media": [], "media_seconds": 8, "layout": "bottom", "button_size": "l", "show_gallery_button": True,
              "gallery_style": "pill",           # pill | prominent (filled, larger)
              # m (old look) | l | xl: gallery button, language button and lock
              # together; "l" by default (owner's decision 15.11.2026)
              "corner_size": "l",
              "show_mode_hints": True,
              # Anleitung („1. Foto antippen …", Text unter start.steps), die
              # letzte Aufnahme als Kachel, QR zur Event-Galerie und ein Logo.
              "steps": "aus", "show_last": False, "show_qr": False,
              "logo": "", "logo_size": 18,
              "positions": {},
              # Colour when no start media is set ("" = blurred live picture)
              # and a flat dim in percent; -1 keeps the old gradient.
              "bg_color": "", "dim": -1,
              # Own start picture: title and status line can go (the operator
              # notice stays as a dot); fit = drift | cover | contain.
              "show_title": True, "show_status": True, "fit": "drift"},
    # A backdrop per screen of the run: picture or colour plus a dim.
    # Empty = theme.bg, as before. Same block as booth/boothcore/config.py.
    "backdrops": {
        "setup": {"image": "", "color": "", "dim": 0},
        "capture": {"image": "", "color": "", "dim": 0},
        "result": {"image": "", "color": "", "dim": 0},
    },
    # One optional medium per phase of the run; only "after" with seconds > 0
    # adds time. Same block as booth/boothcore/config.py.
    "phase_media": {
        "processing": {"media": "", "fit": "cover", "text": True, "loop": True},
        "between": {"media": "", "fit": "cover", "text": True, "loop": True},
        "after": {"media": "", "fit": "cover", "text": True, "loop": True, "seconds": 0},
        "error": {"media": "", "fit": "cover", "text": True, "loop": True},
    },
    "texts": {},
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
              "reprint_from_gallery": True,
              # Paper or ribbon out: accept prints anyway and print them once the
              # printer is ready again (off = refuse, as before). At most
              # queue_max waiting prints; one older than queue_max_hours is dropped.
              "queue_while_empty": False, "queue_max": 12, "queue_max_hours": 6,
              # Same as booth/boothcore/config.py: fit a template to the paper
              # of the box's first printer (same aspect family only).
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
    "qr_url_template": "",
    "qr": {"mode": "auto", "url": "", "utm": "", "label": ""},
    # Download without internet: a router or hotspot at the booth (the booth
    # sets up nothing), the result screen shows "join the Wi-Fi" and "load
    # your photo" from the booth itself. Same block as booth/boothcore/config.py.
    "wifi_share": {"enabled": False, "mode": "auto", "ssid": "", "password": "", "security": "WPA",
                   "hidden": False, "link_hours": 24},
}

#: Welche Datenbanken dieser Prozess schon eingerichtet hat — nach Pfad, nicht
#: als einzelnes Ja/Nein. Ein Merker ohne Pfad weiß nicht, *wofür* er gilt:
#: Zeigt ``DATA_DIR`` danach woanders hin, überspringt die Einrichtung stumm,
#: und die Tabellen fehlen in der zweiten Datenbank. Im Betrieb gibt es nur
#: eine, im Testlauf mehrere — dort fiel es auf (19.09.2026), gelten würde es
#: aber auch für jeden Prozess, der die Datenbank wechselt.
_eingerichtet: set = set()


def init_schema() -> None:
    schluessel = str(db.db_path())
    if schluessel in _eingerichtet:
        return
    con = db.get_db()
    cur = con.cursor()
    cols = db._table_cols(cur, "media")
    for col, ddl in [
        ("uid", "TEXT"), ("session_uid", "TEXT"), ("variant", "TEXT"), ("width", "INTEGER"), ("height", "INTEGER"),
        ("path_poster", "TEXT"), ("storage", "TEXT NOT NULL DEFAULT 'local'"), ("remote_path", "TEXT"),
        ("meta_json", "TEXT"), ("taken_at", "TEXT"), ("hidden", "INTEGER NOT NULL DEFAULT 0"), ("color_tag", "TEXT"),
        ("device_id", "INTEGER"), ("review_state", "TEXT"),
        # Where a picture came from (app/media_source.py); NULL for older rows
        ("source", "TEXT"),
        # Image statistics for sorting out (app/sortout.py): dHash and edge variance
        ("phash", "TEXT"), ("sharpness", "REAL"),
    ]:
        if col not in cols:
            cur.execute(f"ALTER TABLE media ADD COLUMN {col} {ddl};")
    _install_moderation_trigger(cur)
    cur.execute("CREATE UNIQUE INDEX IF NOT EXISTS media_uid_variant ON media(uid, variant) WHERE uid IS NOT NULL;")
    cur.execute("CREATE INDEX IF NOT EXISTS media_session ON media(session_uid);")
    cur.execute("CREATE INDEX IF NOT EXISTS media_event_created ON media(event_id, created_at);")

    ecols = db._table_cols(cur, "events")
    for col, ddl in [("share_key", "TEXT"), ("qr_target", "TEXT NOT NULL DEFAULT 'gallery'"), ("gallery_json", "TEXT"),
                     ("external_url", "TEXT"), ("status_message", "TEXT"),
                     # Campaign parameters for the printed QR codes of this event
                     ("utm_source", "TEXT"), ("utm_medium", "TEXT"), ("utm_campaign", "TEXT"), ("utm_content", "TEXT"),
                     # Sub-galleries, one level deep (app/folders.py)
                     ("parent_id", "INTEGER"),
                     # booth | client, and an explicit deletion switch (app/retention.py)
                     ("kind", "TEXT NOT NULL DEFAULT 'booth'"), ("auto_delete", "INTEGER"),
                     # Guest upload PIN, hashed only (app/guest_upload.py)
                     ("upload_pin_hash", "TEXT")]:
        if col not in ecols:
            cur.execute(f"ALTER TABLE events ADD COLUMN {col} {ddl};")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_events_parent ON events(parent_id);")
    _install_moderation_trigger(cur, parent=True)

    dcols = db._table_cols(cur, "devices")
    for col, ddl in [("profile_json", "TEXT"), ("profile_version", "INTEGER NOT NULL DEFAULT 1"),
                     ("event_id", "INTEGER"),
                     # Operator pressed "Als gelesen markieren": errors and warnings up to this
                     # time no longer count on the dashboard and booth lists (the lines stay).
                     ("logs_seen_at", "TEXT")]:
        if col not in dcols:
            cur.execute(f"ALTER TABLE devices ADD COLUMN {col} {ddl};")

    # Welche Events eine Box je hatte: ihre Warteschlange darf noch nach dem Wechsel dorthin liefern
    cur.execute("""CREATE TABLE IF NOT EXISTS device_event_history (
        device_id INTEGER NOT NULL, event_id INTEGER NOT NULL, since TEXT NOT NULL DEFAULT (datetime('now')),
        PRIMARY KEY(device_id, event_id));""")
    cur.execute("INSERT OR IGNORE INTO device_event_history(device_id, event_id) SELECT id, event_id FROM devices WHERE event_id IS NOT NULL")

    from app import sharelinks
    sharelinks.init_schema(cur)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS device_jobs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            device_id INTEGER NOT NULL REFERENCES devices(id) ON DELETE CASCADE,
            type TEXT NOT NULL,
            payload_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            delivered_at TEXT,
            done_at TEXT,
            ok INTEGER,
            result TEXT
        );""")
    # 1 = nobody answered in time (see expire_jobs). Kept apart from ok=0 so the
    # admin can say "expired" instead of showing a made-up failure.
    if "expired" not in db._table_cols(cur, "device_jobs"):
        cur.execute("ALTER TABLE device_jobs ADD COLUMN expired INTEGER NOT NULL DEFAULT 0;")
    # Photobox Sync target passwords that older versions stored in clear in a
    # set_sync_config job: blanked once, at start, before anything reads them
    # (app/sync_secrets.py; idempotent, logs ids and a count, never the secret).
    from app import sync_secrets
    sync_secrets.blank_clear_passwords(con)
    # Short-lived pairing codes. A code is a second way to hand over a device
    # key: eight characters somebody can read out over the phone, valid for a
    # few minutes, usable once. The key itself never gets shorter.
    cur.execute("""
        CREATE TABLE IF NOT EXISTS device_pairings (
            code TEXT PRIMARY KEY,
            device_id INTEGER NOT NULL REFERENCES devices(id) ON DELETE CASCADE,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            expires_at TEXT NOT NULL,
            used_at TEXT,
            used_ip TEXT
        );""")
    # Pairing from the phone (app/phone_pairing.py): the booth opens a request,
    # the admin confirms it on the phone, the booth collects the key with the
    # verifier only it knows. Only the verifier's SHA-256 is stored.
    cur.execute("""
        CREATE TABLE IF NOT EXISTS pairing_requests (
            code TEXT PRIMARY KEY,
            verifier_hash TEXT NOT NULL,
            install_id TEXT NOT NULL DEFAULT '',
            booth_name TEXT NOT NULL DEFAULT '',
            version TEXT NOT NULL DEFAULT '',
            ip TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            expires_at TEXT NOT NULL,
            state TEXT NOT NULL DEFAULT 'open',
            device_id INTEGER REFERENCES devices(id) ON DELETE CASCADE,
            decided_by TEXT,
            decided_at TEXT,
            collected_at TEXT
        );""")
    # Who asked (S-2): the confirm page shows the requester's browser/program
    # next to its address, so a request from a stranger is easier to spot.
    if "user_agent" not in db._table_cols(cur, "pairing_requests"):
        cur.execute("ALTER TABLE pairing_requests ADD COLUMN user_agent TEXT NOT NULL DEFAULT '';")
    # The sync program's own report: queue, jobs, targets, log tail. One row per
    # device, overwritten on every heartbeat — this is a live view, not a history.
    cur.execute("""
        CREATE TABLE IF NOT EXISTS device_sync_state (
            device_id INTEGER PRIMARY KEY REFERENCES devices(id) ON DELETE CASCADE,
            updated_at TEXT NOT NULL DEFAULT (datetime('now')),
            report_json TEXT NOT NULL DEFAULT '{}'
        );""")
    # What a booth reports about itself: errors, warnings, plain events. This is
    # the remote view into a box that misbehaves at a venue nobody can reach.
    cur.execute("""
        CREATE TABLE IF NOT EXISTS device_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            device_id INTEGER NOT NULL REFERENCES devices(id) ON DELETE CASCADE,
            level TEXT NOT NULL DEFAULT 'info',
            code TEXT,
            message TEXT NOT NULL DEFAULT '',
            context_json TEXT,
            occurred_at TEXT NOT NULL DEFAULT (datetime('now')),
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );""")
    cur.execute("CREATE INDEX IF NOT EXISTS device_logs_device_time ON device_logs(device_id, occurred_at);")
    cur.execute("""
        CREATE TABLE IF NOT EXISTS booth_assets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            kind TEXT NOT NULL,
            name TEXT NOT NULL,
            file TEXT NOT NULL UNIQUE,
            sha256 TEXT NOT NULL,
            size_bytes INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );""")
    # Eigene Domains je Kunde: die Galerie unter „bilder.kunde.de" statt unter
    # der Adresse des Dienstleisters. Siehe app/domains.py.
    cur.execute("""
        CREATE TABLE IF NOT EXISTS event_domains (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            domain TEXT NOT NULL UNIQUE,
            event_id INTEGER,
            note TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL,
            verified_at TEXT,
            last_check TEXT,
            last_result TEXT NOT NULL DEFAULT ''
        );""")
    # Identifiers that were deleted (a whole run or a single item). The booth
    # delivers originals long after the web version; without this table a late
    # upload simply recreated what a guest or the operator had just deleted.
    # Rows are removed together with their event (see forget_deleted_for_event).
    cur.execute("""
        CREATE TABLE IF NOT EXISTS deleted_uids (
            uid TEXT PRIMARY KEY,
            session_uid TEXT,
            event_id INTEGER,
            scope TEXT NOT NULL DEFAULT 'session',
            reason TEXT NOT NULL DEFAULT '',
            deleted_at TEXT NOT NULL DEFAULT (datetime('now'))
        );""")
    cur.execute("CREATE INDEX IF NOT EXISTS deleted_uids_event ON deleted_uids(event_id, deleted_at);")
    # Which booth has confirmed which deleted run. Until it has, the run is
    # listed in every sync answer to that booth (see deletions_for_device).
    cur.execute("""
        CREATE TABLE IF NOT EXISTS device_deletion_acks (
            device_id INTEGER NOT NULL,
            uid TEXT NOT NULL,
            acked_at TEXT NOT NULL DEFAULT (datetime('now')),
            PRIMARY KEY(device_id, uid)
        );""")
    # Änderungsprotokoll: wer hat wann was umgestellt. Siehe app/protokoll.py.
    from app import protokoll
    protokoll.init_schema(cur)
    # Clients, codes and token hashes of the MCP door's OAuth. See app/oauth.py.
    from app import oauth
    oauth.init_schema(cur)
    # Live remote control sessions and the one wait slot. See app/remote_control.py.
    from app import remote_control
    remote_control.init_schema(cur)
    # Die Bitte um eine Bewertung — gezählt als Tagessumme. Siehe app/bewertung.py.
    from app import bewertung
    bewertung.init_schema(cur)
    # Konten für den FTP-/SFTP-Eingang. Siehe app/eingang.py.
    from app import eingang
    eingang.init_schema(cur)
    # One code per client for all of their galleries. See app/clients.py.
    from app import clients
    clients.init_schema(cur)
    # Old ids of galleries and pictures moved from another service. See app/legacy.py.
    from app import legacy
    legacy.init_schema(cur)
    # Clients' named selections per gallery. See app/picks.py.
    from app import picks
    picks.init_schema(cur)
    # Comments between clients and the photographer. See app/comments.py.
    from app import comments
    comments.init_schema(cur)
    # What the folder import already brought in. See app/import_folder.py.
    from app import import_folder
    import_folder.init_schema(cur)
    # Hashes of the public status links of booths. See app/status_page.py.
    from app import status_page
    status_page.init_schema(cur)
    # Customers' layout invitations (token hashes). See app/layout_freigabe.py.
    from app import layout_freigabe
    layout_freigabe.init_schema(cur)
    con.commit()
    con.close()
    _eingerichtet.add(schluessel)


# ── Freigabe (Moderation) ─────────────────────────────────────
# Die Fotobox lädt hoch, ohne von einer Freigabe zu wissen. Deshalb hängt die
# Regel an der Datenbank selbst: ein Trigger hält neue Aufnahmen zurück, wenn
# das Event ``moderation`` gesetzt hat. So greift es für jeden Upload-Weg —
# Box, Gast-Upload, Admin — ohne dass der Upload-Endpunkt es wissen muss.
# ``hidden=1`` versteckt die Aufnahme, ``review_state='pending'`` unterscheidet
# „wartet auf Freigabe" von „Original einer Web-Fassung" (ebenfalls hidden=1).

_MODERATION_TRIGGER = """
CREATE TRIGGER IF NOT EXISTS media_moderation_hold
AFTER INSERT ON media
WHEN NEW.hidden = 0 AND EXISTS (
    SELECT 1 FROM events e WHERE e.id = NEW.event_id AND ({condition})
)
BEGIN
    UPDATE media SET hidden = 1, review_state = 'pending' WHERE id = NEW.id;
END;
"""

_MODERATION_JSON1 = ("e.gallery_json IS NOT NULL AND json_valid(e.gallery_json) "
                     "AND json_extract(e.gallery_json, '$.moderation') IN (1, '1', 'true', 'on')")
# Rückfall für SQLite-Builds ohne JSON1: auf den gespeicherten JSON-Text schauen.
_MODERATION_LIKE = ("e.gallery_json LIKE '%\"moderation\": true%' "
                    "OR e.gallery_json LIKE '%\"moderation\":true%'")


# A folder of a moderated gallery is moderated too (page 4, section 13): the
# same hold, looked up on the parent. A second trigger rather than a changed
# first one, so an existing database keeps its trigger untouched.
_MODERATION_PARENT_TRIGGER = """
CREATE TRIGGER IF NOT EXISTS media_moderation_hold_parent
AFTER INSERT ON media
WHEN NEW.hidden = 0 AND EXISTS (
    SELECT 1 FROM events c JOIN events e ON e.id = c.parent_id WHERE c.id = NEW.event_id AND ({condition})
)
BEGIN
    UPDATE media SET hidden = 1, review_state = 'pending' WHERE id = NEW.id;
END;
"""


def _install_moderation_trigger(cur, parent: bool = False) -> None:
    template = _MODERATION_PARENT_TRIGGER if parent else _MODERATION_TRIGGER
    for condition in (_MODERATION_JSON1, _MODERATION_LIKE):
        try:
            cur.execute(template.format(condition=condition))
            return
        except Exception:
            continue


def pending_review_count(event_id: Optional[int] = None) -> int:
    """Wie viele Aufnahmen auf Freigabe warten (ein Event oder alle)."""
    con = db.get_db()
    try:
        if event_id is None:
            row = con.execute("SELECT COUNT(*) FROM media WHERE review_state='pending'").fetchone()
        else:
            row = con.execute("SELECT COUNT(*) FROM media WHERE review_state='pending' AND event_id=?",
                              (int(event_id),)).fetchone()
        return int(row[0] or 0)
    except Exception:
        return 0
    finally:
        con.close()


def pending_counts() -> Dict[int, int]:
    """Wartende Aufnahmen je Event in einer Abfrage — für Listen und Übersichten."""
    con = db.get_db()
    try:
        rows = con.execute("SELECT event_id, COUNT(*) AS n FROM media "
                           "WHERE review_state='pending' GROUP BY event_id").fetchall()
    except Exception:
        rows = []
    finally:
        con.close()
    return {int(r["event_id"]): int(r["n"]) for r in rows}


def pending_review(event_id: int, limit: int = 300) -> List[Dict[str, Any]]:
    """Wartende Aufnahmen eines Events, älteste zuerst — so wie sie kamen."""
    con = db.get_db()
    try:
        rows = con.execute(
            "SELECT * FROM media WHERE event_id=? AND review_state='pending' ORDER BY id LIMIT ?",
            (int(event_id), int(limit))).fetchall()
    except Exception:
        rows = []
    con.close()
    return [dict(r) for r in rows]


def approve_media(event_id: int, media_ids: Optional[List[int]] = None) -> int:
    """Freigeben: sichtbar schalten. Ohne ``media_ids`` alles Wartende."""
    con = db.get_db()
    if media_ids is None:
        cur = con.execute("UPDATE media SET hidden=0, review_state='approved' "
                          "WHERE event_id=? AND review_state='pending'", (int(event_id),))
    else:
        ids = [int(i) for i in media_ids]
        if not ids:
            con.close()
            return 0
        marks = ",".join("?" * len(ids))
        cur = con.execute(f"UPDATE media SET hidden=0, review_state='approved' "
                          f"WHERE event_id=? AND review_state='pending' AND id IN ({marks})",
                          [int(event_id), *ids])
    con.commit()
    n = cur.rowcount or 0
    con.close()
    return n


def moderation_enabled(event: Optional[Dict[str, Any]]) -> bool:
    from app import gallery_settings as gsm
    return bool(gsm.for_event(event)["moderation"])


# ── Events ────────────────────────────────────────────────────

def ensure_share_key(event: Dict[str, Any]) -> str:
    key = event.get("share_key")
    if key:
        return key
    key = secrets.token_urlsafe(24)
    con = db.get_db()
    con.execute("UPDATE events SET share_key=? WHERE id=?", (key, event["id"]))
    con.commit()
    con.close()
    event["share_key"] = key
    return key


def signature(share_key: str, uid: str, length: int = 8) -> str:
    mac = hmac.new(share_key.encode(), uid.encode(), hashlib.sha256).digest()
    return base64.b32encode(mac).decode().lower().rstrip("=")[:length]


def parse_share_code(code: str):
    if "-" not in code:
        return None, None
    uid, sig = code.rsplit("-", 1)
    if not (6 <= len(uid) <= 24 and uid.isalnum() and sig.isalnum()):
        return None, None
    return uid, sig


def event_by_share_key(key: str) -> Optional[Dict[str, Any]]:
    """Das Event zu einem Signaturschlüssel.

    Gebraucht, wenn eine Box ohne Netz ein Event angelegt hat: Der Schlüssel
    ist dann die Identität des Events — er steckt schon in jedem QR-Code, der
    an dem Abend gedruckt wurde. Der Slug ist austauschbar, der Schlüssel nicht.
    """
    if not str(key or "").strip():
        return None
    con = db.get_db()
    row = con.execute("SELECT * FROM events WHERE share_key=? LIMIT 1", (str(key).strip(),)).fetchone()
    con.close()
    return dict(row) if row else None


def find_event_for_share(uid: str, sig: str) -> Optional[Dict[str, Any]]:
    """Welches Event hat diese Kennung signiert? Erst über vorhandene Medien, sonst alle Events prüfen."""
    con = db.get_db()
    row = con.execute("SELECT e.* FROM media m JOIN events e ON e.id=m.event_id WHERE m.session_uid=? LIMIT 1",
                      (uid,)).fetchone()
    candidates = [dict(row)] if row else [dict(r) for r in con.execute(
        "SELECT * FROM events WHERE share_key IS NOT NULL ORDER BY id DESC LIMIT 500").fetchall()]
    con.close()
    for ev in candidates:
        if ev.get("share_key") and hmac.compare_digest(signature(ev["share_key"], uid), sig):
            return ev
    return None


def set_event_qr(event_id: int, qr_target: str, external_url: str, status_message: str,
                 utm: Optional[Dict[str, Any]] = None) -> None:
    """Store the QR target of an event and, optionally, its campaign parameters."""
    if qr_target not in ("gallery", "external", "status"):
        qr_target = "gallery"
    con = db.get_db()
    con.execute("UPDATE events SET qr_target=?, external_url=?, status_message=? WHERE id=?",
                (qr_target, (external_url or "").strip()[:500] or None, (status_message or "").strip()[:2000] or None, event_id))
    if utm is not None:
        clean = clean_utm(utm)
        con.execute("UPDATE events SET utm_source=?, utm_medium=?, utm_campaign=?, utm_content=? WHERE id=?",
                    (clean["utm_source"] or None, clean["utm_medium"] or None,
                     clean["utm_campaign"] or None, clean["utm_content"] or None, event_id))
    con.commit()
    con.close()


# ── QR target and campaign parameters ─────────────────────────
# Every printed QR code of an event points at ``/p/<uid>-<sig>``; where that
# leads is decided here, at any time, even for cards already handed out. The
# campaign parameters ride along so the operator can see in Umami which QR code
# actually brings visitors.

UTM_KEYS = ("utm_source", "utm_medium", "utm_campaign", "utm_content")
UTM_DEFAULTS = {"utm_source": "photobox", "utm_medium": "qr"}
UTM_MAX_LEN = 80


def clean_utm(values: Optional[Dict[str, Any]]) -> Dict[str, str]:
    """Trim campaign parameters to something safe for a URL."""
    values = values or {}
    out = {}
    for key in UTM_KEYS:
        raw = str(values.get(key) or "").strip()[:UTM_MAX_LEN]
        # Spaces and control characters have no place in a campaign name.
        out[key] = re.sub(r"[^\w.\-]+", "-", raw, flags=re.UNICODE).strip("-")
    return out


def event_utm(event: Optional[Dict[str, Any]]) -> Dict[str, str]:
    """Effective campaign parameters: event value, else a sensible default.

    ``utm_campaign`` defaults to the event slug, so a fresh event is already
    measurable without anyone filling in a form.
    """
    event = event or {}
    stored = clean_utm({k: event.get(k) for k in UTM_KEYS})
    defaults = dict(UTM_DEFAULTS)
    defaults["utm_campaign"] = str(event.get("slug") or "")
    out = {}
    for key in UTM_KEYS:
        value = stored.get(key) or defaults.get(key, "")
        if value:
            out[key] = value
    return out


def with_params(url: str, params: Dict[str, str]) -> str:
    """Append parameters to a URL without breaking what is already there.

    Existing query parameters survive, a parameter the target already carries
    wins over ours, and there is never a second ``?``. The fragment stays at
    the end where it belongs.
    """
    if not url:
        return url
    from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
    parts = urlsplit(url)
    existing = parse_qsl(parts.query, keep_blank_values=True)
    present = {k for k, _ in existing}
    merged = existing + [(k, v) for k, v in params.items() if v and k not in present]
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(merged), parts.fragment))


def session_path(event: Dict[str, Any], uid: str) -> str:
    """Path of the session page for this uid, signed with the event key."""
    return f"/p/{uid}-{signature(ensure_share_key(event), uid)}"


def qr_url_for(event: Optional[Dict[str, Any]], uid: str, base_url: str = "") -> str:
    """The URL a scanned QR code of this event should end up at.

    * ``gallery`` and ``status`` stay on our own session page ``/p/<uid>-<sig>``
      (the status page is the same route with another face).
    * ``external`` goes to ``external_url``, with ``{uid}`` replaced.

    In every case the campaign parameters are appended.
    """
    event = event or {}
    base = (base_url or "").rstrip("/")
    target = event.get("qr_target") or "gallery"
    external = (event.get("external_url") or "").strip()
    # Checked again on the way out: an address stored before the form refused
    # anything but http(s) must not end up in a QR code or a redirect.
    from app import redirects
    if target == "external" and external and redirects.clean_url(external.replace("{uid}", uid or "uid")):
        url = external.replace("{uid}", uid or "")
    else:
        url = base + session_path(event, uid)
    return with_params(url, event_utm(event))


def utm_query(event: Optional[Dict[str, Any]]) -> str:
    """Campaign parameters as a query string, for links inside our own pages."""
    from urllib.parse import urlencode
    return urlencode(event_utm(event))


# ── Medien ────────────────────────────────────────────────────

def media_by_uid(uid: str, variant: str) -> Optional[Dict[str, Any]]:
    con = db.get_db()
    row = con.execute("SELECT * FROM media WHERE uid=? AND variant=?", (uid, variant)).fetchone()
    con.close()
    return dict(row) if row else None


def session_media(session_uid: str) -> List[Dict[str, Any]]:
    con = db.get_db()
    rows = con.execute("SELECT * FROM media WHERE session_uid=? AND hidden=0 ORDER BY id", (session_uid,)).fetchall()
    con.close()
    return [dict(r) for r in rows]


_RAW_UID = re.compile(r"^(?P<run>[A-Za-z0-9]+)\.(?P<shot>\d+)$")


def unframed_partners(rows: List[Dict[str, Any]]):
    """Pair each framed picture with the raw capture of the same run and shot.

    The booth names its uploads after the run: ``<uid>`` is the result (a
    photo, or the collage sheet), ``<uid>.f<i>`` the framed single picture
    ``i`` of a collage, ``<uid>.<i>`` the unframed capture ``i`` (kind
    ``original``, only with ``upload_originals``). So ``<uid>.<i>`` belongs to
    ``<uid>.f<i>``, and ``<uid>.1`` to ``<uid>`` when that is a photo (never
    to a collage sheet or a GIF). Nothing new is stored: the ids say it.

    Returns ``(partner_of, absorbed)``: framed media id -> raw row, and the
    ids of raw rows reachable that way (not listed on their own). A raw
    capture whose framed picture carries no frame (the booth sends
    ``"frame": false`` in its meta) is absorbed without a partner: both are
    the same picture, one download is enough. Raw captures without a framed
    counterpart (GIF stills, a collage uploaded as a sheet only) stay loose.
    """
    framed = {str(r["uid"]): r for r in rows
              if r.get("uid") and (r.get("kind") or "photo") != "original"}
    partner_of: Dict[int, Dict[str, Any]] = {}
    absorbed = set()
    for raw in rows:
        if (raw.get("kind") or "") != "original" or not raw.get("uid"):
            continue
        m = _RAW_UID.match(str(raw["uid"]))
        if not m:
            continue
        partner = framed.get(f"{m['run']}.f{m['shot']}")
        if partner is None and m["shot"] == "1":
            candidate = framed.get(m["run"])
            if candidate and (candidate.get("kind") or "photo") == "photo":
                partner = candidate
        if partner is None or partner.get("event_id") != raw.get("event_id"):
            continue
        absorbed.add(raw["id"])
        try:
            frame = json.loads(partner.get("meta_json") or "{}").get("frame", True)
        except (ValueError, AttributeError):
            frame = True
        if frame is not False:
            partner_of[partner["id"]] = raw
    return partner_of, absorbed


def device_event_ids(device_id: int) -> List[int]:
    con = db.get_db()
    ids = [r[0] for r in con.execute("SELECT event_id FROM device_event_history WHERE device_id=?", (device_id,)).fetchall()]
    con.close()
    return ids


def geraet_kennt_event(device_id: int, event_id: int) -> bool:
    """Gehört dieses Event zu dieser Box?

    Zugeordnet ist die Box entweder gerade (``devices.event_id``) oder war es
    einmal (``device_event_history``) — dann darf sie noch nachliefern, was
    vom Abend übrig ist. Alles andere ist ein fremdes Event.
    """
    con = db.get_db()
    try:
        zeile = con.execute(
            "SELECT 1 FROM devices WHERE id=? AND event_id=? "
            "UNION SELECT 1 FROM device_event_history WHERE device_id=? AND event_id=? LIMIT 1",
            (int(device_id), int(event_id), int(device_id), int(event_id))).fetchone()
    finally:
        con.close()
    return bool(zeile)


def delete_session(session_uid: str, device_id: int) -> List[Dict[str, Any]]:
    """Nur Aufnahmen, die diese Box selbst hochgeladen hat (bzw. ältere ohne Gerät aus ihren Events)."""
    allowed = set(device_event_ids(device_id))
    con = db.get_db()
    rows = [dict(r) for r in con.execute("SELECT * FROM media WHERE session_uid=?", (session_uid,)).fetchall()]
    rows = [r for r in rows if r.get("device_id") == device_id or (r.get("device_id") is None and r["event_id"] in allowed)]
    for r in rows:
        con.execute("DELETE FROM media WHERE id=?", (r["id"],))
    con.commit()
    con.close()
    return rows


def session_known_elsewhere(session_uid: str) -> bool:
    """Are there still rows of this run (or with this uid) in the gallery?"""
    con = db.get_db()
    row = con.execute("SELECT 1 FROM media WHERE session_uid=? OR uid=? LIMIT 1",
                      (session_uid, session_uid)).fetchone()
    con.close()
    return bool(row)


# ── Deleted identifiers ───────────────────────────────────────
# Sub-items of a run carry "<uid>.<suffix>" (".1", ".f1", ".gif", ".boom").
# A tombstone on the run therefore covers everything that starts with it.

def _base_uid(uid: str) -> str:
    return str(uid or "").split(".", 1)[0]


def remember_deleted(uid: str, *, event_id: Optional[int], reason: str, scope: str = "session",
                     session_uid: Optional[str] = None) -> None:
    """Note that ``uid`` was deleted. Never raises: a missing note must not stop a deletion."""
    uid = str(uid or "").strip()
    if not uid:
        return
    try:
        init_schema()
        con = db.get_db()
        con.execute("INSERT OR IGNORE INTO deleted_uids(uid, session_uid, event_id, scope, reason) "
                    "VALUES (?,?,?,?,?)",
                    (uid[:64], str(session_uid or _base_uid(uid))[:64],
                     int(event_id) if event_id is not None else None, str(scope)[:20], str(reason)[:40]))
        con.commit()
        con.close()
    except Exception:                                  # noqa: BLE001 — see docstring
        import logging
        logging.getLogger(__name__).exception("Tombstone for %s not written", uid)


def remember_deleted_rows(rows: List[Dict[str, Any]], *, reason: str, scope: str = "media") -> None:
    """Tombstones for media rows that are being removed.

    ``scope="media"`` notes each row's own uid (a single picture went away);
    ``scope="session"`` notes the run each row belongs to (everything went away).
    Rows without a uid came from somewhere other than a booth — nothing will
    ever re-upload them under the same name, so there is nothing to note.
    """
    seen = set()
    for row in rows or []:
        row = dict(row)
        key = row.get("session_uid") if scope == "session" else row.get("uid")
        if not key or key in seen:
            continue
        seen.add(key)
        remember_deleted(key, event_id=row.get("event_id"), reason=reason, scope=scope,
                         session_uid=row.get("session_uid"))


def is_deleted(*uids: str) -> bool:
    """Was any of these identifiers — or the run they belong to — deleted?"""
    candidates = set()
    for uid in uids:
        uid = str(uid or "").strip()
        if uid:
            candidates.update({uid, _base_uid(uid)})
    if not candidates:
        return False
    init_schema()
    con = db.get_db()
    marks = ",".join("?" * len(candidates))
    row = con.execute(f"SELECT 1 FROM deleted_uids WHERE uid IN ({marks}) LIMIT 1",
                      tuple(candidates)).fetchone()
    con.close()
    return bool(row)


#: How many deleted runs one sync answer carries at most. An old booth that
#: never confirms keeps getting the same bounded list; nothing else changes.
DELETIONS_PER_SYNC = 200


def deletions_for_device(device_id: int, limit: int = DELETIONS_PER_SYNC) -> List[str]:
    """Deleted runs of this device's events (current or former), not yet confirmed.

    Only whole runs: a tombstone on a sub-item ("<uid>.gif") means one file of
    the run went away in the gallery, not the run at the booth.
    """
    init_schema()
    con = db.get_db()
    rows = con.execute(
        """SELECT d.uid FROM deleted_uids d
           WHERE instr(d.uid, '.') = 0
             AND d.event_id IN (SELECT event_id FROM device_event_history WHERE device_id=?
                                UNION SELECT event_id FROM devices WHERE id=? AND event_id IS NOT NULL)
             AND NOT EXISTS (SELECT 1 FROM device_deletion_acks a WHERE a.device_id=? AND a.uid=d.uid)
           ORDER BY d.deleted_at, d.uid LIMIT ?""",
        (int(device_id), int(device_id), int(device_id), int(limit))).fetchall()
    con.close()
    return [r[0] for r in rows]


def confirm_deletions(device_id: int, uids: Any) -> int:
    """The booth has removed these runs locally; stop listing them for it."""
    if not isinstance(uids, list):
        return 0
    clean = []
    for uid in uids[:DELETIONS_PER_SYNC * 2]:
        uid = str(uid or "").strip()[:64]
        if uid and uid.replace(".", "").isalnum():
            clean.append(uid)
    if not clean:
        return 0
    init_schema()
    con = db.get_db()
    con.executemany("INSERT OR IGNORE INTO device_deletion_acks(device_id, uid) VALUES (?, ?)",
                    [(int(device_id), u) for u in clean])
    con.commit()
    con.close()
    return len(clean)


def forget_deleted_for_event(event_id: int) -> None:
    """The event row is gone; its tombstones (and their confirmations) go with it."""
    init_schema()
    con = db.get_db()
    con.execute("DELETE FROM device_deletion_acks WHERE uid IN (SELECT uid FROM deleted_uids WHERE event_id=?)",
                (int(event_id),))
    con.execute("DELETE FROM deleted_uids WHERE event_id=?", (int(event_id),))
    con.commit()
    con.close()


def mark_remote(media_id: int, remote_path: str) -> None:
    con = db.get_db()
    con.execute("UPDATE media SET storage='remote', remote_path=? WHERE id=?", (remote_path, media_id))
    con.commit()
    con.close()


# ── Geräte & Profile ──────────────────────────────────────────

def deep_merge(base: Dict[str, Any], patch: Dict[str, Any]) -> Dict[str, Any]:
    out = copy.deepcopy(base)
    for k, v in (patch or {}).items():
        if isinstance(out.get(k), dict) and isinstance(v, dict) and k != "texts":
            out[k] = deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def device_profile(device: Dict[str, Any]) -> Dict[str, Any]:
    try:
        stored = json.loads(device.get("profile_json") or "{}")
    except ValueError:
        stored = {}
    # Profile aus der Zeit vor dem Abschnitt „Aussehen“ kannten nur „accent“ —
    # die Hauptfarbe soll beim Umstieg nicht auf den Standard zurückfallen.
    if "theme" not in stored and stored.get("accent"):
        stored = dict(stored, theme={"accent": stored["accent"]})
    return deep_merge(DEFAULT_PROFILE, stored)


#: Profile parts a booth's starting profile never sets in the gallery: the
#: event is the gallery's decision, the version its counter, and secrets do
#: not travel from the booth (they arrive empty anyway).
SEED_SKIP = ("event", "version")
SEED_SECRETS = (("mail", "password"), ("access", "pin"), ("webhooks", "secret"), ("http_trigger", "token"),
                ("wifi_share", "password"))


def profile_is_untouched(device: Dict[str, Any]) -> bool:
    """True while nobody has set anything in this booth's profile in the gallery.

    The stored profile, merged with the defaults, equals the defaults apart
    from the event and the version.
    """
    current = device_profile(device)
    base = deep_merge(DEFAULT_PROFILE, {})
    strip = lambda p: {k: v for k, v in p.items() if k not in SEED_SKIP}  # noqa: E731
    return strip(current) == strip(base)


def seed_profile_from_booth(device: Dict[str, Any], seed: Any) -> str:
    """Take a newly paired booth's own profile as the starting point.

    "taken": the gallery's profile was untouched, the booth's values are now
    the gallery's (checked by the same rules as every writer, bad values
    dropped). "kept": the gallery already holds a profile someone set; it
    wins as before. "refused": not a profile.
    """
    if not isinstance(seed, dict) or not seed:
        return "refused"
    if not profile_is_untouched(device):
        return "kept"
    patch = {k: copy.deepcopy(v) for k, v in seed.items() if k in DEFAULT_PROFILE and k not in SEED_SKIP}
    for section, key in SEED_SECRETS:
        if isinstance(patch.get(section), dict):
            patch[section].pop(key, None)
    merged = deep_merge(device_profile(device), patch)
    save_device_profile(device["id"], merged, device.get("event_id"), strict=False)
    return "taken"


class ProfileError(ValueError):
    """A profile change with a value the booth must not get (R2-U1)."""


def validate_profile(profile: Dict[str, Any], baseline: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """The profile with only valid values; ``ProfileError`` for a new bad one.

    Every writer (admin form, import, MCP tools, the booth API) ends in
    :func:`save_device_profile`, which calls this. ``baseline`` is what is
    stored now: a bad value that is already there (a profile saved before
    the rules existed) is dropped quietly instead of blocking every later
    save of an unrelated field; only a value this change brings is refused.
    """
    from app import profile_rules
    clean, problems = profile_rules.check(profile, DEFAULT_PROFILE, strict=True)
    if problems and baseline is not None:
        _, known = profile_rules.check(baseline, DEFAULT_PROFILE, strict=True)
        problems = [p for p in problems if p not in known]
    if problems:
        raise ProfileError(profile_rules.describe(problems))
    return clean


_PROFILE_WARNED: set = set()


def safe_profile(profile: Dict[str, Any]) -> Dict[str, Any]:
    """``profile`` with every value outside the rules replaced by its default.

    For what leaves the gallery (``profile_for_box``): a stored profile or an
    event's settings from before the rules may still hold something the
    kiosk must not get. Keys outside DEFAULT_PROFILE stay as they are.
    """
    from app import profile_rules
    part = {k: v for k, v in profile.items() if k in DEFAULT_PROFILE}
    clean, problems = profile_rules.check(part, DEFAULT_PROFILE, strict=False)
    paths = ", ".join(sorted({p[0] for p in problems}))[:300]
    if problems and paths not in _PROFILE_WARNED:
        _PROFILE_WARNED.add(paths)                  # once per set of fields, not on every heartbeat
        logging.getLogger(__name__).warning("Box-Profil: %d ungültige Werte nicht ausgeliefert (%s)",
                                            len(problems), paths)
    out = dict(profile)
    for key, default in DEFAULT_PROFILE.items():
        if key not in clean:
            out[key] = copy.deepcopy(default)
        elif isinstance(default, dict) and default:
            out[key] = deep_merge(default, clean[key])
        else:
            out[key] = clean[key]
    return out


def save_device_profile(device_id: int, profile: Dict[str, Any], event_id: Optional[int],
                        strict: bool = True) -> None:
    """Store a booth profile. Raises ``ProfileError`` for a value outside the rules.

    ``strict=False`` is for profiles a booth itself hands over (pairing,
    switching events): bad values are dropped instead of refusing.
    """
    device = db.get_device_by_id(device_id)
    if strict:
        clean = validate_profile(profile, device_profile(device) if device else None)
    else:
        from app import profile_rules
        clean, _ = profile_rules.check(profile, DEFAULT_PROFILE, strict=False)
    con = db.get_db()
    con.execute("UPDATE devices SET profile_json=?, profile_version=profile_version+1, event_id=? WHERE id=?",
                (json.dumps(clean, ensure_ascii=False), event_id, device_id))
    if event_id:
        con.execute("INSERT OR IGNORE INTO device_event_history(device_id, event_id) VALUES (?, ?)", (device_id, event_id))
    con.commit()
    con.close()


def bump_profile(device_id: int) -> None:
    con = db.get_db()
    con.execute("UPDATE devices SET profile_version=profile_version+1 WHERE id=?", (device_id,))
    con.commit()
    con.close()


# ── Collagen-Vorlagen ─────────────────────────────────────────
#
# Dieselben Vorlagen, die in ``boothcore/media/compose.py`` liegen — hier nur
# als Kurzinfo, damit die Verwaltung sie zur Auswahl stellen kann, ohne die
# Box zu fragen. Laufen die beiden Listen auseinander, bietet der Admin
# Vorlagen an, die es nicht gibt; ein Test hält sie deshalb zusammen.
BUILTIN_COLLAGES = [
    {"id": "solo", "name": "Einzelbild · 1 Foto", "shots": 1, "orientation": "portrait", "paper": "10x15"},
    {"id": "solo-quer", "name": "Einzelbild quer · 1 Foto", "shots": 1, "orientation": "landscape", "paper": "10x15-quer"},
    {"id": "quadrat1", "name": "Quadrat · 1 Foto", "shots": 1, "orientation": "portrait", "paper": "quadrat"},
    {"id": "vollbild", "name": "Vollbild · 1 Foto, ohne Rand", "shots": 1, "orientation": "portrait", "paper": "10x15"},
    {"id": "vollbild-quer", "name": "Vollbild quer · 1 Foto, ohne Rand", "shots": 1, "orientation": "landscape", "paper": "10x15-quer"},
    {"id": "duo", "name": "Duo · 2 Fotos", "shots": 2, "orientation": "portrait", "paper": "10x15"},
    {"id": "duo-quer", "name": "Duo quer · 2 Fotos", "shots": 2, "orientation": "landscape", "paper": "10x15-quer"},
    {"id": "duo-quer-gestapelt", "name": "Duo quer gestapelt · 2 Fotos", "shots": 2, "orientation": "landscape", "paper": "10x15-quer"},
    {"id": "trio", "name": "Dreier · 3 Fotos", "shots": 3, "orientation": "portrait", "paper": "10x15"},
    {"id": "strips3", "name": "Fotostreifen · 3 Fotos", "shots": 3, "orientation": "portrait", "paper": "streifen"},
    {"id": "trio-quer", "name": "Panorama · 3 Fotos", "shots": 3, "orientation": "landscape", "paper": "10x15-quer"},
    {"id": "grid4", "name": "Raster · 4 Fotos", "shots": 4, "orientation": "portrait", "paper": "10x15"},
    {"id": "grid4-quer", "name": "Raster quer · 4 Fotos", "shots": 4, "orientation": "landscape", "paper": "10x15-quer"},
    {"id": "quadrat4", "name": "Quadrat · 4 Fotos", "shots": 4, "orientation": "portrait", "paper": "quadrat"},
    {"id": "streifen4", "name": "Fotostreifen · 4 Fotos", "shots": 4, "orientation": "portrait", "paper": "streifen"},
    {"id": "fuenf", "name": "Fünfer · 1 groß, 4 klein", "shots": 5, "orientation": "portrait", "paper": "10x15"},
    {"id": "fuenf-quer", "name": "Fünfer quer · 1 groß, 4 klein", "shots": 5, "orientation": "landscape", "paper": "10x15-quer"},
    {"id": "sechs", "name": "Sechser · 2 × 3", "shots": 6, "orientation": "portrait", "paper": "10x15"},
    {"id": "sechs-quer", "name": "Sechser quer · 3 × 2", "shots": 6, "orientation": "landscape", "paper": "10x15-quer"},
    {"id": "panorama-gross", "name": "Panorama 13×18 · 3 Fotos", "shots": 3, "orientation": "landscape", "paper": "13x18-quer"},
    {"id": "grid4-gross", "name": "Raster 13×18 quer · 4 Fotos", "shots": 4, "orientation": "landscape", "paper": "13x18-quer"},
    # Schlicht / Plain: photos only, white or black paper (compose._plain_family).
    # Offered only where ticked; an empty selection means the list above.
    {"id": "schlicht-1-weiss", "name": "Schlicht · 1 Foto · weiß", "shots": 1, "orientation": "portrait", "paper": "10x15", "plain": True},
    {"id": "schlicht-1-schwarz", "name": "Schlicht · 1 Foto · schwarz", "shots": 1, "orientation": "portrait", "paper": "10x15", "plain": True},
    {"id": "schlicht-1-quer-weiss", "name": "Schlicht · 1 Foto quer · weiß", "shots": 1, "orientation": "landscape", "paper": "10x15-quer", "plain": True},
    {"id": "schlicht-1-quer-schwarz", "name": "Schlicht · 1 Foto quer · schwarz", "shots": 1, "orientation": "landscape", "paper": "10x15-quer", "plain": True},
    {"id": "schlicht-2-weiss", "name": "Schlicht · 2 Fotos · weiß", "shots": 2, "orientation": "portrait", "paper": "10x15", "plain": True},
    {"id": "schlicht-2-schwarz", "name": "Schlicht · 2 Fotos · schwarz", "shots": 2, "orientation": "portrait", "paper": "10x15", "plain": True},
    {"id": "schlicht-2-quer-weiss", "name": "Schlicht · 2 Fotos quer · weiß", "shots": 2, "orientation": "landscape", "paper": "10x15-quer", "plain": True},
    {"id": "schlicht-2-quer-schwarz", "name": "Schlicht · 2 Fotos quer · schwarz", "shots": 2, "orientation": "landscape", "paper": "10x15-quer", "plain": True},
    {"id": "schlicht-3-weiss", "name": "Schlicht · 3 Fotos · weiß", "shots": 3, "orientation": "portrait", "paper": "10x15", "plain": True},
    {"id": "schlicht-3-schwarz", "name": "Schlicht · 3 Fotos · schwarz", "shots": 3, "orientation": "portrait", "paper": "10x15", "plain": True},
    {"id": "schlicht-3-quer-weiss", "name": "Schlicht · 3 Fotos quer · weiß", "shots": 3, "orientation": "landscape", "paper": "10x15-quer", "plain": True},
    {"id": "schlicht-3-quer-schwarz", "name": "Schlicht · 3 Fotos quer · schwarz", "shots": 3, "orientation": "landscape", "paper": "10x15-quer", "plain": True},
    {"id": "schlicht-4-weiss", "name": "Schlicht · 4 Fotos (Raster) · weiß", "shots": 4, "orientation": "portrait", "paper": "10x15", "plain": True},
    {"id": "schlicht-4-schwarz", "name": "Schlicht · 4 Fotos (Raster) · schwarz", "shots": 4, "orientation": "portrait", "paper": "10x15", "plain": True},
    {"id": "schlicht-4-quer-weiss", "name": "Schlicht · 4 Fotos quer (Raster) · weiß", "shots": 4, "orientation": "landscape", "paper": "10x15-quer", "plain": True},
    {"id": "schlicht-4-quer-schwarz", "name": "Schlicht · 4 Fotos quer (Raster) · schwarz", "shots": 4, "orientation": "landscape", "paper": "10x15-quer", "plain": True},
    {"id": "schlicht-streifen3-weiss", "name": "Schlicht · Streifen · 3 Fotos · weiß", "shots": 3, "orientation": "portrait", "paper": "streifen", "plain": True},
    {"id": "schlicht-streifen3-schwarz", "name": "Schlicht · Streifen · 3 Fotos · schwarz", "shots": 3, "orientation": "portrait", "paper": "streifen", "plain": True},
    {"id": "schlicht-streifen4-weiss", "name": "Schlicht · Streifen · 4 Fotos · weiß", "shots": 4, "orientation": "portrait", "paper": "streifen", "plain": True},
    {"id": "schlicht-streifen4-schwarz", "name": "Schlicht · Streifen · 4 Fotos · schwarz", "shots": 4, "orientation": "portrait", "paper": "streifen", "plain": True},
]

BUILTIN_COLLAGE_IDS = [c["id"] for c in BUILTIN_COLLAGES]

#: Was ein Event an der Box überschreiben darf. Bewusst kurz: Alles andere
#: gehört der Box, nicht dem Event — sonst erklärt niemand mehr, warum eine
#: Box an zwei Abenden Verschiedenes tut.
# ── Wie die Vorlagen aussehen ─────────────────────────────
# Spiegel von ``boothcore.media.compose``: dieselben Anlässe, damit die
# Verwaltung nichts anbietet, was die Box nicht kennt. Ein Test hält beide
# Seiten zusammen.

COLLAGE_STYLE_KEYS = ('preset', 'background', 'ink', 'muted', 'accent', 'headline', 'subline', 'font', 'radius', 'frame')

COLLAGE_STYLE_PRESETS = {
    "clean": {"background": "#0f0d09", "ink": "#f5f3f0", "muted": "#898580", "accent": "#e7ad59", "headline": "{event_name}", "subline": "{date}", "font": "system", "radius": 14, "frame": 0},
    "hell": {"background": "#faf8f5", "ink": "#1a1714", "muted": "#6f6a63", "accent": "#b07d2b", "headline": "{event_name}", "subline": "{date}", "font": "system", "radius": 14, "frame": 0},
    "geburtstag": {"background": "#151033", "ink": "#fdf7ff", "muted": "#a79ecb", "accent": "#ffbf3c", "headline": "Happy Birthday", "subline": "{event_name} · {date}", "font": "display", "radius": 22, "frame": 0},
    "hochzeit": {"background": "#f7f3ec", "ink": "#2a2620", "muted": "#8a8073", "accent": "#b39258", "headline": "{event_name}", "subline": "{date}", "font": "serif", "radius": 0, "frame": 10},
    "feier": {"background": "#110d1a", "ink": "#fbf6ff", "muted": "#9b8fb3", "accent": "#ff5f9e", "headline": "{event_name}", "subline": "{date}", "font": "display", "radius": 24, "frame": 0},
    "firma": {"background": "#ffffff", "ink": "#101418", "muted": "#5d6772", "accent": "#1d5fd0", "headline": "{event_name}", "subline": "{date}", "font": "system", "radius": 6, "frame": 0},
}


def clean_collage_style(roh) -> Dict[str, Any]:
    """Aus Formularwerten einen gültigen Stil machen. Leer heißt: wie der Anlass."""
    if hasattr(roh, "get"):
        hole = roh.get
    else:
        hole = dict(roh or {}).get
    preset = str(hole("preset") or "clean")
    aus = {"preset": preset if preset in COLLAGE_STYLE_PRESETS else "clean"}
    for key in COLLAGE_STYLE_KEYS:
        if key == "preset":
            continue
        wert = str(hole(key) or "").strip()
        if key in ("radius", "frame"):
            aus[key] = str(max(0, min(80, int(wert)))) if wert.lstrip("-").isdigit() else ""
        elif key in ("background", "ink", "muted", "accent"):
            from app import branding
            aus[key] = branding.normalize_hex(wert) or ""
        elif key == "font":
            aus[key] = wert if wert in ("system", "display", "serif") else ""
        else:
            aus[key] = wert[:120]
    return aus


#: The booth's consent note for an event without automatic deletion. Sent
#: in place of a note that would name a number of days (profile_for_box).
NO_DELETION_NOTE = {
    "de": "Galerie: wird nicht automatisch gelöscht, Betreiber oder Gastgeber löschen sie. "
          "Löschen jederzeit über euren QR-Code.",
    "en": "Gallery: not deleted automatically; the operator or the host deletes it. "
          "Delete any time with your QR code.",
}


EVENT_PROFILE_KEYS = ("collage_exact", "collage_builtins", "collage_templates", "collage_style", "collage_upload",
                      "looks", "looks_max",
                      "looks_mode", "looks_auto", "modes", "formats", "default_format")


def event_profile_overrides(event) -> Dict[str, Any]:
    """Die Einstellungen, die dieses Event selbst setzt."""
    from app import branding
    design = branding.parse_design((event or {}).get("design_json"))
    eigenes = design.get("booth")
    if not isinstance(eigenes, dict):
        return {}
    aus = {k: v for k, v in eigenes.items() if k in EVENT_PROFILE_KEYS and v not in (None, "", [], False)}
    if aus.get("collage_exact"):
        # "Exactly these" (app/layout_wahl.py): an empty list is a decision
        # here — no own templates, or no built-ins — not "as the box has it".
        aus["collage_templates"] = list(eigenes.get("collage_templates") or [])
        aus["collage_builtins"] = list(eigenes.get("collage_builtins") or [])
    return aus


def _file_naming(event: Dict[str, Any]) -> str:
    from app import dateinamen, gallery_settings
    return dateinamen.regel_fuer(event, gallery_settings.for_event(event))


def profile_for_box(device: Dict[str, Any], base_url: str) -> Dict[str, Any]:
    prof = device_profile(device)
    prof["version"] = int(device.get("profile_version") or 1)
    event = db.get_event_by_id(device["event_id"]) if device.get("event_id") else None
    layouts_fixed = False
    if event:
        # Was das Event für sich festlegt, gewinnt über die Einstellung der Box:
        # Eine Box fährt an drei Wochenenden zu drei Kunden, und der Kunde
        # bestimmt, welche Collagen und Filter seine Gäste sehen.
        overrides = event_profile_overrides(event)
        prof.update(overrides)
        # The booth's templates page shows its layout switches as "set by the
        # event" (event.layouts_fixed) instead of letting a change there vanish.
        layouts_fixed = any(k in overrides for k in ("collage_exact", "collage_templates", "collage_builtins"))
        # The event's frames for boomerang and video win the same way.
        from app import frame_store
        frames_fixed = frame_store.apply_event_choice(prof, event)
        # The consent note promises "deleted after {tage} days". The gallery
        # deletes after the event's retention, so that is the number the booth
        # says; consent.days is only what a booth without an event promises.
        try:
            keep_days = int(event.get("retention_days") or 0)
        except (TypeError, ValueError):
            keep_days = 0
        from app import retention
        if retention.deadline(event) is None:
            # No automatic deletion (or no period at all): the booth must not
            # promise a number of days. A note that would say "{tage}" is
            # replaced by one that says who deletes instead.
            consent = dict(prof.get("consent") or {})
            consent["days"] = 0
            notes = dict(consent.get("note") or {})
            for lang, text in list(notes.items()) + [(k, None) for k in NO_DELETION_NOTE if k not in notes]:
                if text is None or "{tage}" in str(text) or "{days}" in str(text):
                    notes[lang] = NO_DELETION_NOTE.get(lang, NO_DELETION_NOTE["de"])
            consent["note"] = notes
            prof["consent"] = consent
        elif keep_days > 0:
            prof["consent"] = {**(prof.get("consent") or {}), "days": keep_days}
        # ``moderation`` lets the booth say "still being reviewed" instead of
        # promising a gallery that will not show the shot yet. ``qr_query`` is
        # appended by the booth to the session URL it builds itself.
        # Hat der Kunde eine eigene Domain, gehören seine QR-Codes und Links
        # dorthin und nicht auf die Marke des Dienstleisters. Ist keine
        # bestätigt, bleibt es bei der Hauptadresse.
        from app import domains
        prof["event"] = {"slug": event["slug"], "name": event["name"], "share_key": ensure_share_key(event),
                         "base_url": domains.fuer_event(event) or base_url,
                         # The booth's self-check warns when its start QR leads
                         # to a gallery guests cannot open yet.
                         "published": bool(event.get("published")),
                         "moderation": moderation_enabled(event),
                         "utm": event_utm(event), "qr_query": utm_query(event),
                         # How the booth names files on a stick, in a folder or
                         # on FTP/SFTP (app/dateinamen.py): it must know offline.
                         "file_naming": _file_naming(event), "layouts_fixed": layouts_fixed,
                         "frames_fixed": frames_fixed}
        # Stiller Betrieb: Die Box lädt weiter hoch, verrät den Gästen aber
        # nichts davon. Das entscheidet die Galerie, nicht die Box — sonst
        # müsste der Betreiber am Event zweimal dasselbe einstellen.
        from app import silence
        if silence.is_silent(event):
            prof["event"]["silent"] = True
            prof["qr"] = {**(prof.get("qr") or {}), "mode": "off"}
            prof["qr_url_template"] = ""
            prof["qr_on_screen"] = False
            prof["print"] = {**(prof.get("print") or {}), "qr_on_print": False}
            prof["mail"] = {**(prof.get("mail") or {}), "enabled": False}
    else:
        prof["event"] = {"slug": "", "name": "", "share_key": "", "base_url": base_url,
                         "moderation": False, "utm": {}, "qr_query": "", "layouts_fixed": False,
                         "frames_fixed": False}
    # Whatever was stored or set by the event: the booth gets only values
    # inside the profile rules (R2-U1).
    return safe_profile(prof)



# ── Device logs ───────────────────────────────────────────────
# The booth and the sync client push what went wrong; the server keeps it for a
# while so the operator can look into a box from a distance. Retention is part
# of the write path: nobody remembers to run a cleanup job.

LOG_LEVELS = ("error", "warning", "info")
LOG_RETENTION_DAYS = 30
LOG_MAX_PER_DEVICE = 2000
LOG_MAX_PER_CALL = 200

_LOG_FIELD_LIMITS = {"code": 64, "message": 2000, "context": 4000}


def _log_level(value: Any) -> str:
    """Anything unknown becomes 'info' — a strange level must not drop the entry."""
    level = str(value or "").strip().lower()
    return level if level in LOG_LEVELS else "info"


def _log_time(value: Any) -> str:
    """Normalise the booth's timestamp; unusable values fall back to 'now'."""
    from app import schedule
    stamp = schedule.parse_dt(str(value or "")) if value else None
    if stamp is None:
        from datetime import datetime
        stamp = datetime.now()
    return stamp.isoformat(sep=" ", timespec="seconds")


def _log_context(value: Any) -> Optional[str]:
    """Keep the context as JSON text, whatever shape it arrives in."""
    if value in (None, "", {}, []):
        return None
    if isinstance(value, str):
        text = value
    else:
        try:
            text = json.dumps(value, ensure_ascii=False)
        except (TypeError, ValueError):
            text = str(value)
    return text[:_LOG_FIELD_LIMITS["context"]]


def add_device_logs(device_id: int, entries: List[Dict[str, Any]]) -> int:
    """Store log entries of one device and return how many were written.

    Each entry may carry ``level``, ``code``, ``message``, ``context`` and
    ``occurred_at``. At most ``LOG_MAX_PER_CALL`` entries per call; everything
    is length-capped, so a chatty box cannot fill the disk. After writing,
    entries older than ``LOG_RETENTION_DAYS`` are dropped globally and each
    device is trimmed to its newest ``LOG_MAX_PER_DEVICE`` entries.
    """
    rows = []
    for entry in (entries or [])[:LOG_MAX_PER_CALL]:
        if not isinstance(entry, dict):
            continue
        # No guest address and no secret is kept, whatever the booth sent
        # (final review A4): these lines are shown in the admin and to an
        # assistant with ``read``.
        message = redact.text(str(entry.get("message") or ""))[:_LOG_FIELD_LIMITS["message"]]
        code = redact.text(str(entry.get("code") or ""))[:_LOG_FIELD_LIMITS["code"]] or None
        if not message and not code:
            continue                      # nothing to show, nothing to store
        rows.append((int(device_id), _log_level(entry.get("level")), code, message,
                     redact.text(_log_context(entry.get("context"))), _log_time(entry.get("occurred_at"))))
    if not rows:
        return 0

    con = db.get_db()
    con.executemany("""INSERT INTO device_logs(device_id, level, code, message, context_json, occurred_at)
                       VALUES (?,?,?,?,?,?)""", rows)
    con.execute("DELETE FROM device_logs WHERE occurred_at < datetime('now', ?)",
                (f"-{LOG_RETENTION_DAYS} days",))
    con.execute("""DELETE FROM device_logs WHERE device_id=? AND id NOT IN (
                       SELECT id FROM device_logs WHERE device_id=? ORDER BY id DESC LIMIT ?)""",
                (int(device_id), int(device_id), LOG_MAX_PER_DEVICE))
    con.commit()
    con.close()
    return len(rows)


def device_logs(device_id: int, level: Optional[str] = None, limit: int = 200,
                since_hours: Optional[int] = None, query: str = "") -> List[Dict[str, Any]]:
    """Log entries of one device, newest first.

    ``level`` filters one level, ``since_hours`` limits the period (``None`` =
    everything that is still kept) and ``query`` searches message and code.
    """
    sql = ["SELECT * FROM device_logs WHERE device_id=?"]
    args: List[Any] = [int(device_id)]
    if level in LOG_LEVELS:
        sql.append("AND level=?")
        args.append(level)
    if since_hours:
        sql.append("AND occurred_at >= datetime('now', ?)")
        args.append(f"-{int(since_hours)} hours")
    needle = (query or "").strip()
    if needle:
        sql.append("AND (message LIKE ? OR IFNULL(code,'') LIKE ?)")
        args += [f"%{needle}%", f"%{needle}%"]
    sql.append("ORDER BY occurred_at DESC, id DESC LIMIT ?")
    args.append(max(1, min(int(limit or 200), 2000)))

    con = db.get_db()
    try:
        rows = con.execute(" ".join(sql), args).fetchall()
    except Exception:
        rows = []
    finally:
        con.close()
    # Rows stored before the redaction existed are cleaned on the way out.
    out = []
    for r in rows:
        row = dict(r)
        for key in ("message", "code", "context_json"):
            if row.get(key):
                row[key] = redact.text(row[key])
        out.append(row)
    return out


def device_log_counts(device_id: int, since_hours: int = 24) -> Dict[str, int]:
    """How many errors, warnings and infos one device reported in the period."""
    counts = {level: 0 for level in LOG_LEVELS}
    con = db.get_db()
    try:
        rows = con.execute("""SELECT l.level AS level, COUNT(*) AS n
                              FROM device_logs l LEFT JOIN devices d ON d.id = l.device_id
                              WHERE l.device_id=? AND l.occurred_at >= datetime('now', ?)
                                AND (l.level='info' OR d.logs_seen_at IS NULL OR l.occurred_at > d.logs_seen_at)
                              GROUP BY l.level""",
                           (int(device_id), f"-{int(since_hours)} hours")).fetchall()
    except Exception:
        rows = []
    finally:
        con.close()
    for r in rows:
        counts[_log_level(r["level"])] = counts.get(_log_level(r["level"]), 0) + int(r["n"])
    counts["total"] = sum(counts[level] for level in LOG_LEVELS)
    return counts


def log_counts_by_device(since_hours: int = 24) -> Dict[int, Dict[str, int]]:
    """Counts per device in one query — for lists that show every box."""
    out: Dict[int, Dict[str, int]] = {}
    con = db.get_db()
    try:
        rows = con.execute("""SELECT l.device_id AS device_id, l.level AS level, COUNT(*) AS n
                              FROM device_logs l LEFT JOIN devices d ON d.id = l.device_id
                              WHERE l.occurred_at >= datetime('now', ?)
                                AND (l.level='info' OR d.logs_seen_at IS NULL OR l.occurred_at > d.logs_seen_at)
                              GROUP BY l.device_id, l.level""",
                           (f"-{int(since_hours)} hours",)).fetchall()
    except Exception:
        rows = []
    finally:
        con.close()
    for r in rows:
        entry = out.setdefault(int(r["device_id"]), {level: 0 for level in LOG_LEVELS} | {"total": 0})
        entry[_log_level(r["level"])] += int(r["n"])
        entry["total"] += int(r["n"])
    return out


#: Identical messages from one box closer together than this are one incident.
INCIDENT_GAP_MINUTES = 10


def _reason_key(message: str) -> str:
    """The cause of a log line: digits folded, so "3 Versuche" and "4 Versuche" are one."""
    import re
    return re.sub(r"\d+", "#", " ".join(str(message or "").split()).lower())[:160]


def _parse_log_time(value: str):
    from datetime import datetime
    try:
        return datetime.fromisoformat(str(value or "").replace("T", " ")[:19])
    except ValueError:
        return None


def error_groups(device_id: int, since_hours: int = 24, level: str = "error",
                 only_new: bool = True, limit: int = 5000) -> List[Dict[str, Any]]:
    """Errors of one box grouped by cause — for the display only, no line is dropped.

    Each group: ``reason`` (first wording seen), ``count`` (log lines),
    ``incidents`` (runs of the same cause, a new one after
    ``INCIDENT_GAP_MINUTES`` of quiet), ``first_at``, ``last_at``. Biggest
    group first. ``only_new``: only lines after the operator's
    acknowledgement.
    """
    sql = ["SELECT l.message AS message, l.code AS code, l.occurred_at AS at",
           "FROM device_logs l LEFT JOIN devices d ON d.id = l.device_id",
           "WHERE l.device_id=? AND l.level=? AND l.occurred_at >= datetime('now', ?)"]
    args: List[Any] = [int(device_id), level, f"-{int(since_hours)} hours"]
    if only_new:
        sql.append("AND (d.logs_seen_at IS NULL OR l.occurred_at > d.logs_seen_at)")
    sql.append("ORDER BY l.occurred_at ASC, l.id ASC LIMIT ?")
    args.append(int(limit))
    con = db.get_db()
    try:
        rows = con.execute(" ".join(sql), args).fetchall()
    except Exception:
        rows = []
    finally:
        con.close()
    groups: Dict[str, Dict[str, Any]] = {}
    for r in rows:
        text = redact.text(r["message"] or r["code"] or "")
        key = _reason_key(text)
        g = groups.get(key)
        stamp = _parse_log_time(r["at"])
        if g is None:
            groups[key] = {"reason": text[:120], "count": 1, "incidents": 1,
                           "first_at": r["at"] or "", "last_at": r["at"] or "", "_t": stamp}
            continue
        g["count"] += 1
        last = g["_t"]
        if stamp is None or last is None or (stamp - last).total_seconds() > INCIDENT_GAP_MINUTES * 60:
            g["incidents"] += 1
        g["_t"] = stamp or last
        g["last_at"] = r["at"] or g["last_at"]
    out = sorted(groups.values(), key=lambda g: (-g["count"], g["last_at"]))
    for g in out:
        g.pop("_t", None)
    return out


def summarize_groups(groups: List[Dict[str, Any]], top: int = 3) -> str:
    """"34× Kamera nicht bereit · 3× Drucker …" — the biggest causes on one line."""
    parts = [f"{g['count']}× {g['reason'][:50]}" for g in groups[:top]]
    rest = sum(g["count"] for g in groups[top:])
    if rest:
        parts.append(f"{rest}× weitere")
    return " · ".join(parts)


def acknowledge_logs(device_id: int) -> str:
    """Mark everything this box has reported so far as seen; returns the time kept.

    The time is the newer of "now" and the newest line the box has stored,
    so a booth whose clock runs ahead cannot leave lines it showed unseen.
    """
    from datetime import datetime
    now = datetime.now().isoformat(sep=" ", timespec="seconds")
    con = db.get_db()
    try:
        newest = con.execute("SELECT MAX(occurred_at) AS t FROM device_logs WHERE device_id=?",
                             (int(device_id),)).fetchone()
        stamp = max(now, (newest["t"] or "")[:19].replace("T", " ")) if newest else now
        con.execute("UPDATE devices SET logs_seen_at=? WHERE id=?", (stamp, int(device_id)))
        con.commit()
    finally:
        con.close()
    return stamp


def logs_acknowledged_at(device_id: int) -> str:
    con = db.get_db()
    try:
        row = con.execute("SELECT logs_seen_at FROM devices WHERE id=?", (int(device_id),)).fetchone()
        return (row["logs_seen_at"] if row else "") or ""
    except Exception:
        return ""
    finally:
        con.close()


def recent_error_devices(since_hours: int = 24, limit: int = 20) -> List[Dict[str, Any]]:
    """Devices with unacknowledged errors in the period, worst first — the dashboard card.

    Each row: ``device_id``, ``name``, ``errors`` (log lines), ``incidents``,
    ``warnings``, ``last_at``, ``last_message``, ``reasons`` (grouped, see
    ``error_groups``), ``reasons_text``. Errors before the operator's
    "Als gelesen markieren" are not counted.
    """
    con = db.get_db()
    try:
        rows = con.execute("""
            SELECT l.device_id AS device_id,
                   COALESCE(d.name, '?') AS name,
                   SUM(CASE WHEN l.level='error' THEN 1 ELSE 0 END) AS errors,
                   SUM(CASE WHEN l.level='warning' THEN 1 ELSE 0 END) AS warnings,
                   MAX(l.occurred_at) AS last_at
              FROM device_logs l LEFT JOIN devices d ON d.id = l.device_id
             WHERE l.occurred_at >= datetime('now', ?) AND l.level IN ('error','warning')
               AND (d.logs_seen_at IS NULL OR l.occurred_at > d.logs_seen_at)
             GROUP BY l.device_id
            HAVING errors > 0
             ORDER BY errors DESC, last_at DESC
             LIMIT ?""", (f"-{int(since_hours)} hours", max(1, int(limit)))).fetchall()
        out = []
        for r in rows:
            last = con.execute("""SELECT message, code FROM device_logs
                                  WHERE device_id=? AND level='error'
                                  ORDER BY occurred_at DESC, id DESC LIMIT 1""",
                               (int(r["device_id"]),)).fetchone()
            groups = error_groups(int(r["device_id"]), since_hours)
            out.append({"device_id": int(r["device_id"]), "name": r["name"],
                        "errors": int(r["errors"] or 0), "warnings": int(r["warnings"] or 0),
                        "incidents": sum(g["incidents"] for g in groups) or int(r["errors"] or 0),
                        "reasons": groups, "reasons_text": summarize_groups(groups),
                        "last_at": r["last_at"] or "",
                        "last_message": (last["message"] if last else "") or (last["code"] if last else "") or ""})
        return out
    except Exception:
        return []
    finally:
        con.close()


def acknowledged_error_devices(since_hours: int = 24) -> List[Dict[str, Any]]:
    """Boxes whose errors in the period were all marked as read: ``device_id``, ``name``, ``seen_at``, ``errors``.

    The dashboard says "since <time> no new errors" for them, with a link back to the lines.
    """
    con = db.get_db()
    try:
        rows = con.execute("""
            SELECT d.id AS device_id, COALESCE(d.name,'?') AS name, d.logs_seen_at AS seen_at,
                   COUNT(*) AS errors
              FROM devices d JOIN device_logs l ON l.device_id = d.id
             WHERE d.logs_seen_at IS NOT NULL AND l.level='error'
               AND l.occurred_at >= datetime('now', ?) AND l.occurred_at <= d.logs_seen_at
             GROUP BY d.id ORDER BY d.name""", (f"-{int(since_hours)} hours",)).fetchall()
        quiet = {r["device_id"] for r in con.execute("""
            SELECT l.device_id AS device_id FROM device_logs l JOIN devices d ON d.id = l.device_id
             WHERE l.level='error' AND l.occurred_at >= datetime('now', ?)
               AND l.occurred_at > d.logs_seen_at""", (f"-{int(since_hours)} hours",)).fetchall()}
        return [dict(r) for r in rows if r["device_id"] not in quiet]
    except Exception:
        return []
    finally:
        con.close()


# ── Aufträge ──────────────────────────────────────────────────

# A job result is usually one sentence. A folder listing is not, and the remote
# folder picker would be useless if it came back cut in half.
MAX_JOB_RESULT = 4000
#: A remote-control answer may carry a small picture (live image, screenshot)
#: as base64; the booth makes it at most 640 px, which stays well below this.
MAX_RESULT_BY_TYPE = {"remote_control": 400_000}


def add_job(device_id: int, job_type: str, payload: Optional[dict] = None) -> int:
    if job_type not in JOB_TYPES:
        raise ValueError("unbekannter Auftrag")
    from app import sync_secrets
    if job_type == sync_secrets.JOB and sync_secrets.has_clear_password(payload):
        # Only app/sync_secrets.seal_payload puts a target password into a job, sealed.
        raise ValueError("Ein Zielpasswort geht nur verschlossen an den PC (sync_secrets.seal_payload).")
    con = db.get_db()
    cur = con.execute("INSERT INTO device_jobs(device_id, type, payload_json) VALUES (?,?,?)",
                      (device_id, job_type, json.dumps(payload or {})))
    con.commit()
    jid = cur.lastrowid
    con.close()
    return jid


#: A delivered job without a result goes out again after this long: the answer
#: that carried it may have been lost, or the booth restarted before reporting.
#: Re-delivery is safe because the agent remembers executed job ids on disk.
JOB_REDELIVER_SECONDS = 60
#: A job nobody answered within this time expires. Otherwise it would count
#: as waiting forever and keep the booth on 5-second polling.
JOB_EXPIRE_MINUTES = 10
#: Stored as the result of an expired job; the admin shows the translation.
JOB_EXPIRED_RESULT = "no answer from the booth"


def expire_jobs(device_id: Optional[int] = None) -> int:
    con = db.get_db()
    sql = ("UPDATE device_jobs SET done_at=datetime('now'), ok=0, result=?, expired=1 "
           "WHERE done_at IS NULL AND created_at <= datetime('now', ?)")
    args: List[Any] = [JOB_EXPIRED_RESULT, f"-{JOB_EXPIRE_MINUTES} minutes"]
    if device_id is not None:
        sql += " AND device_id=?"
        args.append(int(device_id))
    n = con.execute(sql, args).rowcount
    from app import second_target
    second_target.clear_answered_payloads(con)       # an expired sealed password does not stay either
    con.commit()
    con.close()
    return int(n or 0)


def job_state(row: Dict[str, Any]) -> str:
    """issued, delivered, done or expired — the four things the admin can see."""
    if row.get("expired"):
        return "expired"
    if row.get("done_at"):
        return "done"
    if row.get("delivered_at"):
        return "delivered"
    return "issued"


#: A remote-control job not picked up within this time never goes out:
#: "start a run" must not fire when the booth comes back an hour later.
CONTROL_JOB_TTL_SECONDS = 60


def take_jobs(device_id: int) -> List[Dict[str, Any]]:
    expire_jobs(device_id)
    con = db.get_db()
    con.execute("UPDATE device_jobs SET done_at=datetime('now'), ok=0, result=?, expired=1 "
                "WHERE device_id=? AND type='remote_control' AND done_at IS NULL AND delivered_at IS NULL "
                "AND created_at <= datetime('now', ?)",
                (JOB_EXPIRED_RESULT, device_id, f"-{CONTROL_JOB_TTL_SECONDS} seconds"))
    con.commit()
    rows = [dict(r) for r in con.execute(
        "SELECT * FROM device_jobs WHERE device_id=? AND done_at IS NULL "
        "AND (delivered_at IS NULL OR delivered_at <= datetime('now', ?)) ORDER BY id LIMIT 10",
        (device_id, f"-{JOB_REDELIVER_SECONDS} seconds"))]
    if rows:
        con.executemany("UPDATE device_jobs SET delivered_at=datetime('now') WHERE id=?", [(r["id"],) for r in rows])
        con.commit()
    con.close()
    return [{"id": r["id"], "type": r["type"], "payload": json.loads(r["payload_json"] or "{}")} for r in rows]


def jobs_waiting(device_id: int) -> int:
    expire_jobs(device_id)
    con = db.get_db()
    n = con.execute("SELECT COUNT(*) c FROM device_jobs WHERE device_id=? AND done_at IS NULL",
                    (device_id,)).fetchone()["c"]
    con.close()
    return int(n)


def finish_jobs(device_id: int, results: List[Dict[str, Any]]) -> None:
    if not results:
        return
    con = db.get_db()
    for r in results:
        try:
            # A late answer replaces the expiry: the booth was slow, not dead,
            # and its real result is worth more than our guess. A job that is
            # really done keeps its first answer.
            row = con.execute("SELECT type FROM device_jobs WHERE id=? AND device_id=?",
                              (int(r.get("id")), device_id)).fetchone()
            limit = MAX_RESULT_BY_TYPE.get(row["type"] if row else "", MAX_JOB_RESULT)
            con.execute("UPDATE device_jobs SET done_at=datetime('now'), ok=?, result=?, expired=0 "
                        "WHERE id=? AND device_id=? AND (done_at IS NULL OR expired=1)",
                        (1 if r.get("ok") else 0, str(r.get("result", ""))[:limit], int(r.get("id")), device_id))
        except (TypeError, ValueError):
            continue
    # The booth has answered: the sealed password in the payload is not needed
    # any more and does not stay in the database (app/second_target.py).
    from app import second_target
    second_target.clear_answered_payloads(con)
    con.commit()
    con.close()


# No I, O, 0 or 1: a code gets read out loud and written down by hand.
PAIR_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
PAIR_MINUTES = 15


def _pair_random(length: int = 8) -> str:
    return "".join(secrets.choice(PAIR_ALPHABET) for _ in range(length))


def new_pairing(device_id: int, minutes: int = PAIR_MINUTES) -> Dict[str, Any]:
    """A fresh code for this device. Older unused codes for it are dropped.

    Short-lived on purpose: the code is a key in disguise, so it should not lie
    around in a chat window for a week.
    """
    con = db.get_db()
    con.execute("DELETE FROM device_pairings WHERE device_id=? AND used_at IS NULL", (device_id,))
    for _ in range(5):
        code = _pair_random()
        try:
            con.execute("""INSERT INTO device_pairings(code, device_id, expires_at)
                           VALUES (?,?, datetime('now', ?))""", (code, device_id, f"+{int(minutes)} minutes"))
            break
        except sqlite3.IntegrityError:
            continue
    else:
        con.close()
        raise RuntimeError("kein freier Code")
    con.commit()
    con.close()
    return {"code": code, "pretty": f"{code[:4]}-{code[4:]}", "minutes": int(minutes)}


def redeem_pairing(code: str, ip: str = "") -> Optional[Dict[str, Any]]:
    """Give out the device behind a code — once, and only while it is fresh."""
    clean = "".join(ch for ch in str(code or "").upper() if ch in PAIR_ALPHABET)
    if len(clean) != 8:
        return None
    con = db.get_db()
    row = con.execute("""SELECT * FROM device_pairings
                         WHERE code=? AND used_at IS NULL AND expires_at > datetime('now')""",
                      (clean,)).fetchone()
    if not row:
        con.execute("DELETE FROM device_pairings WHERE expires_at < datetime('now', '-1 day')")
        con.commit()
        con.close()
        return None
    cur = con.execute("UPDATE device_pairings SET used_at=datetime('now'), used_ip=? WHERE code=? AND used_at IS NULL",
                      (ip[:45], clean))
    con.commit()
    con.close()
    if (cur.rowcount or 0) != 1:            # two booths raced for the same code
        return None
    if not db.get_device_by_id(int(row["device_id"])):
        return None
    # Only the hash of a key is stored (S-6), so redeeming makes a fresh one:
    # the booth that redeems gets it, a box running with the old key stops.
    return db.regenerate_device_key(int(row["device_id"]))


def pairing_refusal(code: str) -> str:
    """Why a code opens nothing: ``used``, ``expired`` or ``unknown``.

    The person at the booth needs a different next step for each ("make a new
    one" vs. "check what you typed"). Saying a code was used or has expired
    gives a guesser nothing: such a code opens no key any more, and every
    refused attempt still counts against the brake of the pair endpoint.
    """
    clean = "".join(ch for ch in str(code or "").upper() if ch in PAIR_ALPHABET)
    if len(clean) != 8:
        return "unknown"
    con = db.get_db()
    row = con.execute("""SELECT used_at, expires_at <= datetime('now') AS expired
                         FROM device_pairings WHERE code=?""", (clean,)).fetchone()
    con.close()
    if not row:
        return "unknown"
    if row["used_at"]:
        return "used"
    return "expired" if row["expired"] else "unknown"


def gallery_display_name() -> str:
    """The name this gallery goes by (headline set in the setup or on the home page).

    The seeded welcome sentence is no name; then there is none.
    """
    name = (db.get_setting("homepage_headline") or "").strip()
    if not name or name in ("Willkommen bei unserer Fotobox!", "Willkommen bei unserer Fotobox-Galerie!"):
        return ""
    return name[:80]


MAX_SYNC_REPORT = 200_000


def save_sync_report(device_id: int, report: Optional[dict]) -> bool:
    """Store what the sync program said about itself. Rejects anything oversized."""
    if not isinstance(report, dict) or not report:
        return False
    text = json.dumps(report, ensure_ascii=False)
    if len(text) > MAX_SYNC_REPORT:
        return False
    con = db.get_db()
    con.execute("""INSERT INTO device_sync_state(device_id, updated_at, report_json)
                   VALUES (?, datetime('now'), ?)
                   ON CONFLICT(device_id) DO UPDATE SET updated_at=datetime('now'), report_json=excluded.report_json""",
                (device_id, text))
    con.commit()
    con.close()
    return True


def sync_report(device_id: int) -> Dict[str, Any]:
    con = db.get_db()
    row = con.execute("SELECT * FROM device_sync_state WHERE device_id=?", (device_id,)).fetchone()
    con.close()
    if not row:
        return {}
    try:
        report = json.loads(row["report_json"] or "{}")
    except ValueError:
        return {}
    report["updated_at"] = row["updated_at"]
    return report


def job_result(device_id: int, job_id: int) -> Dict[str, Any]:
    """One job with its answer — the remote page polls this after pressing a button."""
    expire_jobs(device_id)
    con = db.get_db()
    row = con.execute("SELECT * FROM device_jobs WHERE id=? AND device_id=?", (job_id, device_id)).fetchone()
    con.close()
    from app import second_target
    return second_target.masked_payload({**dict(row), "state": job_state(dict(row))}) if row else {}


def recent_jobs(device_id: int, limit: int = 15) -> List[Dict[str, Any]]:
    expire_jobs(device_id)
    con = db.get_db()
    rows = [dict(r) for r in con.execute("SELECT * FROM device_jobs WHERE device_id=? ORDER BY id DESC LIMIT ?",
                                         (device_id, limit))]
    con.close()
    from app import second_target
    return [second_target.masked_payload({**r, "state": job_state(r)}) for r in rows]


# ── Assets ────────────────────────────────────────────────────

def list_assets(kind: Optional[str] = None) -> List[Dict[str, Any]]:
    con = db.get_db()
    if kind:
        rows = con.execute("SELECT * FROM booth_assets WHERE kind=? ORDER BY id DESC", (kind,)).fetchall()
    else:
        rows = con.execute("SELECT * FROM booth_assets ORDER BY kind, id DESC").fetchall()
    con.close()
    return [dict(r) for r in rows]


def add_asset(kind: str, name: str, file: str, sha256: str, size: int) -> int:
    con = db.get_db()
    cur = con.execute("INSERT INTO booth_assets(kind, name, file, sha256, size_bytes) VALUES (?,?,?,?,?)",
                      (kind, name, file, sha256, size))
    con.commit()
    aid = cur.lastrowid
    con.close()
    return aid


def delete_asset(asset_id: int) -> Optional[Dict[str, Any]]:
    con = db.get_db()
    row = con.execute("SELECT * FROM booth_assets WHERE id=?", (asset_id,)).fetchone()
    if row:
        con.execute("DELETE FROM booth_assets WHERE id=?", (asset_id,))
        con.commit()
    con.close()
    return dict(row) if row else None


# Every profile field that names a file in the asset folder. Verbatim copy of
# boothcore.config.MEDIA_FIELDS (tests/test_profile_media_reach_the_booth.py
# keeps both equal and fails when the schema grows a file-like field that is
# not listed). Until 0.4 the asset list was hand-picked here, and a logo, the
# smile picture, sounds, screensaver media, a font and frame overlays were
# saved in the profile but never reached the booth.
MEDIA_FIELDS = (
    "start.media", "start.logo", "countdown.smile_media", "attract.media",
    "backdrops.*.image", "phase_media.*.media", "frame.overlay", "frame.print_overlay",
    "sounds.countdown", "sounds.shutter", "sounds.done", "sounds.print",
    "theme.font_file", "lock.image", "collage_templates", "backgrounds",
    "chroma.backgrounds", "gif.print_template", "boomerang.frame_templates", "video.frame_templates",
)


def media_files(profile: Dict[str, Any]) -> Dict[str, str]:
    """``{file name: field}`` for every file the profile points at (colours are not files)."""
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


def _template_media(dateien: List[str]) -> set:
    """Pictures and fonts the given template files place (template editor).

    A template that shows a logo is no use on a booth that never received
    the logo file.
    """
    from app import media, vorlagen_paket
    ordner = media.assets_root() / "booth"
    aus: set = set()
    from app import motion_frames
    for name in dateien:
        p = ordner / str(name)
        if p.suffix != ".json" or not p.is_file():
            continue
        try:
            inhalt = json.loads(p.read_text(encoding="utf-8"))
            # A motion frame names its background and overlay the same way.
            aus.update(motion_frames.media_of(inhalt) if motion_frames.is_motion_frame(inhalt)
                       else vorlagen_paket.medien_von(inhalt))
        except (ValueError, OSError, AttributeError):
            continue
    return aus


def assets_for_profile(profile: Dict[str, Any]) -> List[Dict[str, Any]]:
    wanted = set(media_files(profile))
    wanted |= _template_media(list(profile.get("collage_templates") or [])
                              + [str((profile.get("gif") or {}).get("print_template") or "")]
                              + [str(x) for m in ("boomerang", "video")
                                 for x in ((profile.get(m) or {}).get("frame_templates") or [])])
    # LUT-Dateien: die Box braucht jede, die sie anbieten oder fest anwenden soll.
    luts = profile.get("luts") or {}
    lut_names = {str(x).removeprefix("lut-") for x in (luts.get("show") or [])}
    if luts.get("baked"):
        lut_names.add(str(luts["baked"]).removeprefix("lut-"))
    lut_assets = [a for a in list_assets("lut")
                  if not lut_names or Path(a["file"]).stem.lower() in lut_names]
    if not lut_names and not luts.get("enabled", True):
        lut_assets = []
    return [a for a in list_assets() if a["file"] in wanted] + lut_assets
