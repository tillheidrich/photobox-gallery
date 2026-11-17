"""Beispiel-Plugin: meldet jede fertige Aufnahme an eine eigene Adresse.

Aktivieren: Datei in den Plugin-Ordner der Box kopieren
(Windows: %LOCALAPPDATA%\\PhotoboxBooth\\plugins) und WEBHOOK_URL anpassen.
Plugins laufen lokal; ein Fehler hier stoppt nie einen Gast-Durchgang.
"""
import threading

import httpx

from boothcore.plugins import hookimpl

WEBHOOK_URL = "https://example.org/fotobox-hook"


def _post(payload):
    try:
        httpx.post(WEBHOOK_URL, json=payload, timeout=5)
    except Exception:
        pass            # offline: einfach auslassen


@hookimpl
def booth_media_ready(session, media):
    threading.Thread(target=_post, args=({"event": "media_ready", "uid": session.uid, "mode": media["kind"],
                                          "share_url": media["share_url"]},), daemon=True).start()


@hookimpl
def booth_register_looks():
    from PIL import ImageOps

    return {"sepia": lambda img: ImageOps.colorize(ImageOps.grayscale(img), "#2b1d0e", "#f3e3c3")}
