"""Photobox Gallery – i18n helper.

Usage in Jinja2 templates:
    {{ t('gallery.download_all') }}
    {{ t('msg.device.created', name=device.name) }}

Usage in Python:
    from app.i18n import t
    flash(t('msg.event.saved'))
"""
from __future__ import annotations

from typing import Any


def t(key: str, **kwargs: Any) -> str:
    """Return the translated string for *key* in the current request language.

    Falls back to German, then to the key itself if nothing is found.
    """
    # Import here to avoid circular imports at module load time
    try:
        from flask import g
        lang = getattr(g, "lang", "de") or "de"
    except RuntimeError:
        # Outside of request context (e.g. tests)
        lang = "de"

    text = _override(key, lang)
    if text is None:
        text = _lookup(key, lang)
    if text is None and lang != "de":
        text = _lookup(key, "de")
    if text is None:
        text = key

    if kwargs:
        try:
            text = text.format(**kwargs)
        except (KeyError, ValueError):
            pass

    return text


def _override(key: str, lang: str) -> str | None:
    """Eigener Text des Betreibers — Event zuerst, dann global (siehe app/texts.py)."""
    try:
        from app import texts
        return texts.lookup(key, lang)
    except Exception:
        return None


def _lookup(key: str, lang: str) -> str | None:
    """Look up *key* in the STRINGS dict for *lang*. Returns None if not found."""
    try:
        if lang == "de":
            from app.i18n.de import STRINGS
        elif lang == "en":
            from app.i18n.en import STRINGS
        else:
            return None
        return STRINGS.get(key)
    except ImportError:
        return None
