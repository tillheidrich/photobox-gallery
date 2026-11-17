"""„Zugriff zur Galerie anfragen" — der Weg hinein, wenn das Passwort fehlt.

Ein Gast steht vor der Passwortseite: Die Braut hat den Link weitergeschickt,
das Passwort aber nicht. Statt beim Betreiber anzurufen, hinterlässt der Gast
seine E-Mail-Adresse. Der Betreiber bekommt eine Nachricht, sieht die Anfrage
im Admin und gibt sie mit einem Klick frei — dann geht eine Mail mit einem
Link zurück, der die Galerie ohne Passwort öffnet (derselbe Zugangsschlüssel,
den auch der QR-Code mitträgt).

Alles daran ist abschaltbar: Der Knopf erscheint nur, wenn das Event ihn
einschaltet, die Galerie wirklich ein Passwort hat und ein Postausgang
eingerichtet ist. Ohne diese drei gibt es ihn nicht — ein Knopf, der ins Leere
führt, ist schlimmer als keiner.

Gespeichert wird so wenig wie möglich: Adresse, optional Name und eine Zeile
Begründung, die Sprache des Gastes (damit die Antwort in seiner Sprache
ankommt) und der Zustand. Entschiedene Anfragen räumt ``cleanup`` nach 90
Tagen weg.
"""
from __future__ import annotations

import secrets
from typing import Any, Dict, List, Optional

from app import db, emailer

OPEN, GRANTED, DENIED = "open", "granted", "denied"
KEEP_DAYS = 90

_ready = False


def init_schema() -> None:
    global _ready
    if _ready:
        return
    con = db.get_db()
    con.execute("""
        CREATE TABLE IF NOT EXISTS access_requests (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            event_id    INTEGER NOT NULL,
            email       TEXT NOT NULL,
            name        TEXT NOT NULL DEFAULT '',
            note        TEXT NOT NULL DEFAULT '',
            lang        TEXT NOT NULL DEFAULT 'de',
            state       TEXT NOT NULL DEFAULT 'open',
            token       TEXT NOT NULL,
            created_at  TEXT NOT NULL DEFAULT (datetime('now')),
            decided_at  TEXT
        );""")
    con.execute("CREATE INDEX IF NOT EXISTS idx_access_event ON access_requests(event_id, state);")
    con.commit()
    con.close()
    _ready = True


# ── Ist der Knopf überhaupt zu sehen? ─────────────────────────

def enabled(event: Optional[Dict[str, Any]]) -> bool:
    """Drei Bedingungen, alle nötig: eingeschaltet, Passwort da, Post geht raus."""
    if not event:
        return False
    from app import gallery_settings as gsm, silence
    if silence.is_silent(event):
        return False
    if not event.get("password_hash"):
        return False
    if not gsm.for_event(event)["access_request"]:
        return False
    return emailer.smtp_configured() and bool((db.get_setting("admin_email") or "").strip())


# ── Anfragen ──────────────────────────────────────────────────

def create(event: Dict[str, Any], *, email: str, name: str = "", note: str = "",
           lang: str = "de") -> Optional[Dict[str, Any]]:
    """Anfrage anlegen — oder die offene Anfrage derselben Adresse zurückgeben."""
    init_schema()
    email = (email or "").strip().lower()[:200]
    if not emailer.valid_address(email):
        return None
    con = db.get_db()
    try:
        row = con.execute("""SELECT * FROM access_requests
                             WHERE event_id=? AND email=? AND state=? ORDER BY id DESC LIMIT 1""",
                          (int(event["id"]), email, OPEN)).fetchone()
        if row:
            return dict(row)
        cur = con.execute("""INSERT INTO access_requests(event_id, email, name, note, lang, state, token)
                             VALUES (?,?,?,?,?,?,?)""",
                          (int(event["id"]), email, (name or "").strip()[:120],
                           (note or "").strip()[:500], "en" if lang == "en" else "de",
                           OPEN, secrets.token_hex(16)))
        con.commit()
        row = con.execute("SELECT * FROM access_requests WHERE id=?", (cur.lastrowid,)).fetchone()
        return dict(row) if row else None
    finally:
        con.close()


def get(request_id: int, event_id: int) -> Optional[Dict[str, Any]]:
    init_schema()
    con = db.get_db()
    try:
        row = con.execute("SELECT * FROM access_requests WHERE id=? AND event_id=?",
                          (int(request_id), int(event_id))).fetchone()
        return dict(row) if row else None
    finally:
        con.close()


def list_open(event_id: int) -> List[Dict[str, Any]]:
    init_schema()
    con = db.get_db()
    try:
        rows = con.execute("""SELECT * FROM access_requests WHERE event_id=? AND state=?
                              ORDER BY id DESC LIMIT 100""", (int(event_id), OPEN)).fetchall()
        return [dict(r) for r in rows]
    finally:
        con.close()


def _set_state(request_id: int, state: str) -> None:
    con = db.get_db()
    con.execute("UPDATE access_requests SET state=?, decided_at=datetime('now') WHERE id=?",
                (state, int(request_id)))
    con.commit()
    con.close()


def cleanup(days: int = KEEP_DAYS) -> int:
    """Entschiedene Anfragen nach einer Weile wegräumen. Offene bleiben."""
    init_schema()
    con = db.get_db()
    try:
        cur = con.execute("DELETE FROM access_requests WHERE state!=? AND decided_at < datetime('now', ?)",
                          (OPEN, f"-{int(days)} days"))
        con.commit()
        return cur.rowcount or 0
    finally:
        con.close()


# ── Texte ─────────────────────────────────────────────────────
# Mails sind keine Seiten; ihre Texte stehen hier, zweisprachig, statt über
# ein Dutzend i18n-Schlüssel verstreut.

TEXTE = {
    "de": {
        "admin_subject": "Zugriff auf »{event}« angefragt",
        "admin_body": ("{who} möchte die Galerie »{event}« sehen.\n\n"
                       "Adresse: {email}\n{note}"
                       "Freigeben oder ablehnen: {admin_url}\n"),
        "grant_subject": "Dein Zugang zu »{event}«",
        "grant_body": ("Hallo{name},\n\n"
                       "hier ist dein Zugang zur Galerie »{event}«:\n\n"
                       "  {url}\n\n"
                       "Der Link öffnet die Galerie ohne Passwort. Bitte gib ihn nicht weiter.\n"),
        "deny_subject": "Zu »{event}«",
        "deny_body": ("Hallo{name},\n\n"
                      "die Galerie »{event}« bleibt geschlossen — deine Anfrage wurde nicht freigegeben.\n"),
    },
    "en": {
        "admin_subject": "Access requested for “{event}”",
        "admin_body": ("{who} would like to see the gallery “{event}”.\n\n"
                       "Address: {email}\n{note}"
                       "Approve or decline: {admin_url}\n"),
        "grant_subject": "Your access to “{event}”",
        "grant_body": ("Hello{name},\n\n"
                       "here is your access to the gallery “{event}”:\n\n"
                       "  {url}\n\n"
                       "The link opens the gallery without a password. Please keep it to yourself.\n"),
        "deny_subject": "About “{event}”",
        "deny_body": ("Hello{name},\n\n"
                      "the gallery “{event}” stays closed — your request was not approved.\n"),
    },
}


def _text(key: str, lang: str) -> str:
    return TEXTE.get("en" if lang == "en" else "de", TEXTE["de"])[key]


# ── Post ──────────────────────────────────────────────────────

def notify_admin(event: Dict[str, Any], req: Dict[str, Any], admin_url: str) -> bool:
    """Den Betreiber unterrichten. Fehlschlag kostet die Anfrage nicht."""
    to = (db.get_setting("admin_email") or "").strip()
    if not to:
        return False
    lang = "de"
    who = req.get("name") or req["email"]
    note = f"Nachricht: {req['note']}\n\n" if req.get("note") else "\n"
    try:
        emailer.send_text_mail(
            to_email=to,
            subject=_text("admin_subject", lang).format(event=event["name"]),
            body=_text("admin_body", lang).format(who=who, event=event["name"], email=req["email"],
                                                  note=note, admin_url=admin_url),
            reply_to=req["email"])
        return True
    except Exception:
        return False


def grant(event: Dict[str, Any], req: Dict[str, Any], gallery_url: str) -> bool:
    """Freigeben und den Link schicken. Erst wenn die Mail draußen ist, gilt sie als erledigt."""
    lang = req.get("lang") or "de"
    name = f" {req['name']}" if req.get("name") else ""
    try:
        emailer.send_text_mail(
            to_email=req["email"],
            subject=_text("grant_subject", lang).format(event=event["name"]),
            body=_text("grant_body", lang).format(name=name, event=event["name"], url=gallery_url))
    except Exception:
        return False
    _set_state(int(req["id"]), GRANTED)
    return True


def deny(event: Dict[str, Any], req: Dict[str, Any]) -> bool:
    """Ablehnen — und das auch sagen. Wer fragt, verdient eine Antwort."""
    lang = req.get("lang") or "de"
    name = f" {req['name']}" if req.get("name") else ""
    _set_state(int(req["id"]), DENIED)
    try:
        emailer.send_text_mail(
            to_email=req["email"],
            subject=_text("deny_subject", lang).format(event=event["name"]),
            body=_text("deny_body", lang).format(name=name, event=event["name"]))
        return True
    except Exception:
        return False
