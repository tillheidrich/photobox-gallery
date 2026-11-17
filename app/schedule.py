"""Zeitplan einer Galerie: Start („ab wann") und Ablauf („bis wann").

Zwei Felder, die es im Schema längst gibt, hier zu einem Verhalten verbunden:

* ``events.scheduled_live_at`` — davor sieht der Gast eine Vorfreude-Seite mit
  Countdown. Keine Fotos, keine Dateien, kein Umweg über ``/data``.
* ``gallery_settings['expires_at']`` — danach die Statusseite (schon vorhanden).
  Neu ist der sichtbare Countdown in der letzten Woche.

Reine Funktionen, damit sich beides ohne Flask testen lässt.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, Optional

# Ab wie vielen Tagen Restlaufzeit der Hinweis in der Galerie erscheint.
COUNTDOWN_DAYS = 7


def parse_dt(raw: Optional[str]) -> Optional[datetime]:
    """ISO-Zeitstempel aus Formular oder DB lesen; Unsinn ergibt ``None``."""
    raw = (raw or "").strip().replace("Z", "")
    if not raw:
        return None
    for text in (raw, raw[:16], raw[:10]):
        try:
            return datetime.fromisoformat(text)
        except ValueError:
            continue
    return None


def live_at(event: Optional[Dict[str, Any]]) -> Optional[datetime]:
    return parse_dt((event or {}).get("scheduled_live_at"))


def expires_at(cfg: Optional[Dict[str, Any]]) -> Optional[datetime]:
    return parse_dt((cfg or {}).get("expires_at"))


def coming_soon(event: Optional[Dict[str, Any]], now: Optional[datetime] = None) -> Optional[datetime]:
    """Startzeitpunkt, solange er in der Zukunft liegt — sonst ``None``."""
    start = live_at(event)
    if start and (now or datetime.now()) < start:
        return start
    return None


def due_for_publish(event: Optional[Dict[str, Any]], now: Optional[datetime] = None) -> bool:
    """Geplanter Start ist erreicht, die Galerie steht aber noch auf Entwurf."""
    start = live_at(event)
    if not start:
        return False
    return (now or datetime.now()) >= start and not (event or {}).get("published")


def seconds_left(cfg: Optional[Dict[str, Any]], now: Optional[datetime] = None) -> Optional[int]:
    """Restzeit bis zum Ablauf in Sekunden (0 = vorbei, ``None`` = ohne Ablauf)."""
    until = expires_at(cfg)
    if not until:
        return None
    return max(0, int((until - (now or datetime.now())).total_seconds()))


def days_left(cfg: Optional[Dict[str, Any]], now: Optional[datetime] = None) -> Optional[int]:
    """Verbleibende Tage, aufgerundet — nur innerhalb der letzten Woche.

    Aufgerundet, weil „noch 1 Tag" freundlicher (und ehrlicher) ist als „noch
    0 Tage", solange die Galerie noch online ist.
    """
    secs = seconds_left(cfg, now)
    if secs is None:
        return None
    days = -(-secs // 86400)          # ceil
    return days if days <= COUNTDOWN_DAYS else None


def state(event: Optional[Dict[str, Any]], cfg: Optional[Dict[str, Any]] = None,
          now: Optional[datetime] = None) -> str:
    """Zustand für die Admin-Liste: scheduled | expired | live | draft."""
    now = now or datetime.now()
    if coming_soon(event, now):
        return "scheduled"
    if seconds_left(cfg, now) == 0:
        return "expired"
    return "live" if (event or {}).get("published") else "draft"
