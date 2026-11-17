"""Eigene Texte für die Galerie — global und je Event.

Die Box kann ihre Texte längst überschreiben; die Galerie konnte es nicht. Damit
ist der Satz „alle Texte im Backend änderbar" jetzt für beide Seiten wahr.

Drei Ebenen, die erste gewinnt:

1. Event  — ``events.texts_json``: für dieses eine Event, z. B. „Astrids 60."
2. Global — Einstellung ``gallery_texts_json``: gilt für alle Events
3. Übersetzung — die mitgelieferten Texte aus ``app/i18n``

Gespeichert wird ``{"de": {"gallery.download_all": "…"}, "en": {…}}``. Leere
Werte werden verworfen, damit ein leeres Formularfeld nicht die Übersetzung
durch eine leere Zeile ersetzt.
"""
from __future__ import annotations

import json
from typing import Any, Dict, Optional

# Die Texte, die Betreiber erfahrungsgemäß ändern wollen. Alles andere geht über
# das Freitextfeld — die Liste hier ist die bequeme Abkürzung, keine Grenze.
KEYS = [
    ("gallery.download_all", "Galerie: „Alle herunterladen“"),
    ("gallery.empty", "Galerie: noch keine Fotos"),
    ("gallery.deletion_info", "Galerie: Hinweis auf die Löschfrist"),
    ("gallery.footer.rating_title", "Fußzeile: Überschrift Bewertung"),
    ("gallery.footer.rating_text", "Fußzeile: Text Bewertung"),
    ("gallery.footer.booking_title", "Fußzeile: Überschrift Buchung"),
    ("gallery.footer.booking_text", "Fußzeile: Text Buchung"),
    ("gallery.expired_title", "Abgelaufen: Überschrift"),
    ("gallery.expired_text", "Abgelaufen: Text"),
    ("session.pending_title", "Foto unterwegs: Überschrift"),
    ("session.pending_sub", "Foto unterwegs: Unterzeile"),
    ("session.pending_text", "Foto unterwegs: Text"),
    ("session.review_text", "Foto wartet auf Freigabe: Text"),
    ("session.gone_text", "Durchgang gelöscht: Text"),
    ("session.check_again", "Foto unterwegs: Knopf „Noch einmal nachsehen“"),
    ("session.ready_title", "Durchgang: Überschrift"),
    ("session.to_gallery", "Durchgang: Link zur Galerie"),
    ("password.title", "Passwortseite: Überschrift"),
]

MAX_VALUE = 600
MAX_KEYS = 200
LANG_PATTERN = ("de", "en")


def parse(raw: Any) -> Dict[str, Dict[str, str]]:
    """``texts_json`` einlesen. Kaputtes JSON ergibt keine Texte, keinen Absturz."""
    if isinstance(raw, dict):
        data = raw
    else:
        try:
            data = json.loads(raw or "{}")
        except (TypeError, ValueError):
            return {}
    if not isinstance(data, dict):
        return {}
    out: Dict[str, Dict[str, str]] = {}
    for lang, values in list(data.items())[:8]:
        if not isinstance(values, dict) or str(lang) not in LANG_PATTERN:
            continue
        clean = {}
        for key, value in list(values.items())[:MAX_KEYS]:
            text = str(value).strip()
            if text:
                clean[str(key)[:120]] = text[:MAX_VALUE]
        if clean:
            out[str(lang)] = clean
    return out


def dump(texts: Dict[str, Dict[str, str]]) -> str:
    return json.dumps(parse(texts), ensure_ascii=False, indent=2)


def merge(global_texts: Dict[str, Dict[str, str]],
          event_texts: Dict[str, Dict[str, str]]) -> Dict[str, Dict[str, str]]:
    """Event schlägt global — Sprache für Sprache, nicht als Ganzes."""
    out = {lang: dict(values) for lang, values in global_texts.items()}
    for lang, values in event_texts.items():
        out.setdefault(lang, {}).update(values)
    return out


def use(event: Optional[Dict[str, Any]] = None) -> Dict[str, Dict[str, str]]:
    """Überschreibungen für diese Anfrage setzen. Von den Event-Seiten aufgerufen."""
    from flask import g

    from app import db

    globals_ = parse(db.get_setting("gallery_texts_json"))
    merged = merge(globals_, parse((event or {}).get("texts_json")))
    g.text_overrides = merged
    return merged


def lookup(key: str, lang: str) -> Optional[str]:
    """Was ``t()`` fragt, bevor es in die Übersetzung schaut."""
    try:
        from flask import g
        overrides = getattr(g, "text_overrides", None)
    except RuntimeError:
        return None
    if not overrides:
        return None
    return (overrides.get(lang) or {}).get(key)
