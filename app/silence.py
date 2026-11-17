"""Stiller Betrieb — die Galerie läuft, die Gäste merken nichts davon.

Manche Veranstaltungen wollen die Bilder sichern, aber nicht verteilen: eine
Firmenfeier mit Betriebsrat, eine Hochzeit, bei der erst das Brautpaar sehen
soll, ein Presseabend. Dann lädt der Sync weiter hoch, der Betreiber sieht
alles im Admin — nach außen gibt es nichts:

* kein QR-Code an der Box, weder auf dem Schirm noch auf dem Abzug,
* kein Sharepic, keine Druckseite, kein Link auf der Durchgangs-Seite,
* keine öffentliche Galerie, auch nicht mit Passwort,
* keine Gästemail, denn die trüge den Link nach draußen.

Zwei Schalter, beide genügen für sich: global für die ganze Installation
(``silent_mode``) und je Event in den Galerie-Einstellungen (``silent``).
Der strengere gewinnt — wer global still fährt, kann ihn nicht je Event
wieder aufmachen. Das ist Absicht: Der globale Schalter ist der Notaus.
"""
from __future__ import annotations

from typing import Any, Mapping, Optional

from app import db

KEY = "silent_mode"


def global_on() -> bool:
    """Fährt die ganze Installation still?"""
    return (db.get_setting(KEY) or "0") == "1"


def set_global(on: bool) -> None:
    db.set_setting(KEY, "1" if on else "0")


def is_silent(event: Optional[Mapping[str, Any]] = None) -> bool:
    """Ist dieses Event nach außen stumm? Ohne Event zählt nur der globale Schalter."""
    if global_on():
        return True
    if not event:
        return False
    from app import gallery_settings as gsm
    try:
        event = dict(event)
    except (TypeError, ValueError):
        return False
    # for_event: a folder of a silent gallery is silent too (app/folders.py)
    return bool(gsm.for_event(event)["silent"])
