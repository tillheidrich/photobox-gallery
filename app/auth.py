"""Wer die Verwaltung bedienen darf — nur das Nachschlagen.

**Die Anmeldung liegt nicht hier.** Sie steht in
``app/blueprints/admin.py`` (``login``), zusammen mit Kennwortprüfung,
Zwei-Faktor und Sperre nach zu vielen Versuchen. Diese Datei hatte bis zum
19.09.2026 eine zweite, unbenutzte Fassung davon — samt einem Dekorator, der
auf einen Endpunkt zeigte, den es nicht gibt (``url_for("admin_login")``, in
Wahrheit ``admin.login``). Wer „die Anmeldung" härten wollte, härtete leicht
die falsche Datei. Der tote Teil ist weg.

Übrig bleibt, was tatsächlich gebraucht wird: der Name des Kontos.
"""
import os

from app import db


def admin_user() -> str:
    """Der Anmeldename — aus der Datenbank, damit er ein Update übersteht."""
    name = (db.get_setting("admin_username") or "").strip()
    return name or os.environ.get("ADMIN_USER", "admin")


def is_logged_in() -> bool:
    from flask import session

    return bool(session.get("admin_logged_in"))


__all__ = ["admin_user", "is_logged_in"]
