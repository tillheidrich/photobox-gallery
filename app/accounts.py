"""Zugang zur Galerie ohne Umweg über den Server.

Bisher stand das erste Passwort in einer Umgebungsvariablen des Containers.
Das ist als Startschuss in Ordnung und als Dauerzustand falsch: Wer sein
Passwort vergisst, braucht sonst eine Terminalsitzung auf dem Server, um
wieder in seine eigene Galerie zu kommen.

Deshalb zwei Dinge:

* **Kein schwaches Standardpasswort mehr.** Ist beim ersten Start keines
  gesetzt, erzeugt die Galerie ein zufälliges, schreibt es einmal ins
  Protokoll und verlangt beim ersten Anmelden, dass es geändert wird.
* **Ein Wiederherstellungscode**, den man einmal zu sehen bekommt und in den
  Passwortspeicher legt. Damit setzt man das Passwort im Browser neu — ohne
  Server, ohne Terminal. Since 11.11.2026 it is made when the start password
  is first replaced and shown once in the browser, never written to the log.

Der Code ist ein zweites Passwort. Er wird deshalb nur als Hash gespeichert,
ist einmal verwendbar und nach jeder Nutzung verbraucht.
"""
from __future__ import annotations

import hmac
import secrets
import time
from typing import Optional, Tuple

from werkzeug.security import check_password_hash, generate_password_hash

from app import db

# Keine Zeichen, die man beim Abschreiben verwechselt: kein I, O, 0, 1.
ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
GROUPS, GROUP_LEN = 4, 4          # 16 Zeichen → 80 Bit

HASH_KEY = "admin_recovery_hash"
MADE_KEY = "admin_recovery_made_at"
TEMP_KEY = "admin_password_temp"


def _passwort_ok(passwort: str) -> Tuple[bool, str]:
    """Auch der Weg zurück setzt kein schwaches Passwort.

    Wer sein Passwort vergessen hat, tippt unter Druck — genau dann entsteht
    „Sommer2024". Die Prüfung ist dieselbe wie in den Einstellungen.
    """
    from app import passwords
    return passwords.pruefe(passwort, passwords.bezug_der_installation())


def make_recovery_code() -> str:
    raw = "".join(secrets.choice(ALPHABET) for _ in range(GROUPS * GROUP_LEN))
    return "-".join(raw[i:i + GROUP_LEN] for i in range(0, len(raw), GROUP_LEN))


def normalize(code: str) -> str:
    return "".join(ch for ch in str(code or "").upper() if ch in ALPHABET)


def store_recovery_code(code: str) -> None:
    db.set_setting(HASH_KEY, generate_password_hash(normalize(code)))
    db.set_setting(MADE_KEY, time.strftime("%Y-%m-%d %H:%M:%S"))


def new_recovery_code() -> str:
    """Neuen Code erzeugen und hinterlegen. Der alte gilt ab sofort nicht mehr."""
    code = make_recovery_code()
    store_recovery_code(code)
    return code


def has_recovery_code() -> bool:
    return bool((db.get_setting(HASH_KEY) or "").strip())


def recovery_made_at() -> str:
    return db.get_setting(MADE_KEY) or ""


def check_recovery(code: str) -> bool:
    stored = (db.get_setting(HASH_KEY) or "").strip()
    clean = normalize(code)
    if not stored or len(clean) != GROUPS * GROUP_LEN:
        return False
    return check_password_hash(stored, clean)


def password_is_temporary() -> bool:
    return (db.get_setting(TEMP_KEY) or "") == "1"


#: Changes with every new password. A session carries the value it was
#: logged in under; one that carries another is logged out (review S10: a
#: stolen session used to survive the password change meant to end it).
EPOCH_KEY = "admin_session_epoch"


def session_epoch() -> str:
    return db.get_setting(EPOCH_KEY) or ""


SESSION_DAYS = 31


def _sessions_table(con) -> None:
    # S-9 (15.11.2026), additive: one row per admin login, only the hash of its id.
    con.execute("""CREATE TABLE IF NOT EXISTS admin_sessions (
        sid_hash TEXT PRIMARY KEY, created_at REAL NOT NULL, revoked_at REAL)""")


def _sid_hash(sid: str) -> str:
    import hashlib
    return hashlib.sha256(str(sid or "").encode("utf-8")).hexdigest()


def open_admin_session() -> str:
    """A new server-side session id for a login; the cookie carries it, the table its hash."""
    sid = secrets.token_urlsafe(24)
    con = db.get_db()
    try:
        _sessions_table(con)
        now = time.time()
        con.execute("DELETE FROM admin_sessions WHERE created_at < ?", (now - SESSION_DAYS * 86400,))
        con.execute("INSERT INTO admin_sessions(sid_hash, created_at) VALUES (?, ?)", (_sid_hash(sid), now))
        con.commit()
    finally:
        con.close()
    return sid


def admin_session_alive(sid: str) -> bool:
    if not sid:
        return False
    con = db.get_db()
    try:
        _sessions_table(con)
        row = con.execute("SELECT revoked_at, created_at FROM admin_sessions WHERE sid_hash=?",
                          (_sid_hash(sid),)).fetchone()
    finally:
        con.close()
    return bool(row) and row[0] is None and row[1] >= time.time() - SESSION_DAYS * 86400


def end_admin_session(sid: str) -> None:
    """Logout: this session id is dead, whatever copy of the cookie still exists."""
    if not sid:
        return
    con = db.get_db()
    try:
        _sessions_table(con)
        con.execute("UPDATE admin_sessions SET revoked_at=? WHERE sid_hash=?", (time.time(), _sid_hash(sid)))
        con.commit()
    finally:
        con.close()


def rotate_session_epoch() -> None:
    """End every admin session but the one that stores the new value (user name changed)."""
    db.set_setting(EPOCH_KEY, secrets.token_hex(8))


def set_password(new_password: str) -> None:
    """Passwort setzen und das Startpasswort-Kennzeichen löschen."""
    db.set_setting("admin_password_hash", generate_password_hash(new_password))
    db.set_setting(TEMP_KEY, "")
    db.set_setting(EPOCH_KEY, secrets.token_hex(8))
    # S-12: the static MCP key opens the whole administration and is not tied
    # to the password otherwise; a new password ends it (OAuth grants end too).
    from app import mcp
    mcp.revoke_key()


def redeem_recovery(username: str, code: str, new_password: str) -> Tuple[bool, Optional[str], str]:
    """Code einlösen: Passwort neu setzen, neuen Code ausgeben.

    Rückgabe: (geklappt, neuer Code, Meldung). Der Benutzername muss stimmen —
    sonst wäre der Code allein der ganze Zugang.
    """
    stored_user = (db.get_setting("admin_username") or "admin").strip()
    if not hmac.compare_digest(str(username or "").strip(), stored_user):
        return False, None, "Benutzername oder Code stimmt nicht."
    if not check_recovery(code):
        return False, None, "Benutzername oder Code stimmt nicht."
    ok, meldung = _passwort_ok(new_password)
    if not ok:
        return False, None, meldung
    set_password(new_password)
    # Der Wiederherstellungscode ist der Weg für „Telefon weg": Er schaltet den
    # zweiten Faktor mit ab, sonst käme man trotz richtigem Passwort nicht rein.
    # Der Link per E-Mail tut das bewusst nicht — dort bleibt der zweite Faktor
    # stehen, sonst wäre ein übernommenes Postfach der ganze Zugang.
    db.set_setting("admin_totp_enabled", "")
    db.set_setting("admin_totp_secret", "")
    return True, new_recovery_code(), ("Passwort gesetzt, zweiter Faktor abgeschaltet. "
                                       "Der neue Wiederherstellungscode steht unten — einmalig.")


# ── Zurücksetzen per E-Mail ───────────────────────────────
#
# Der zweite Weg zurück, wenn der Wiederherstellungscode auch weg ist. Er
# hängt an zwei Bedingungen: ein eingerichteter Postausgang und eine
# hinterlegte Admin-Adresse. Der Link lebt eine halbe Stunde und gilt einmal.
TOKEN_KEY = "admin_reset_hash"
TOKEN_UNTIL = "admin_reset_until"
TOKEN_MINUTES = 30


def mail_reset_possible() -> bool:
    from app import emailer

    return bool((db.get_setting("admin_email") or "").strip()) and emailer.smtp_configured()


def start_mail_reset(base_url: str) -> Tuple[bool, str]:
    """Link erzeugen und verschicken. Gibt (geklappt, Meldung) zurück."""
    from app import emailer

    to = (db.get_setting("admin_email") or "").strip()
    if not mail_reset_possible():
        return False, "Für diese Galerie ist kein Postausgang eingerichtet."
    token = secrets.token_urlsafe(32)
    db.set_setting(TOKEN_KEY, generate_password_hash(token))
    db.set_setting(TOKEN_UNTIL, str(int(time.time()) + TOKEN_MINUTES * 60))
    link = f"{base_url.rstrip('/')}/admin/recover?token={token}"
    try:
        emailer.send_text_mail(
            to_email=to, subject="Passwort für deine Photobox Gallery zurücksetzen",
            body=("Jemand — vermutlich du — möchte das Admin-Passwort dieser Galerie neu setzen.\n\n"
                  f"Link (gültig {TOKEN_MINUTES} Minuten, einmal verwendbar):\n\n  {link}\n\n"
                  "Wenn du das nicht warst, ignoriere diese Mail. Ohne den Link ändert sich nichts.\n"))
    except Exception as e:
        db.set_setting(TOKEN_KEY, "")
        return False, f"Die Mail konnte nicht verschickt werden: {e}"
    # Die Adresse steht bewusst nicht in der Antwort: Sie ist eine Auskunft
    # über den Betreiber, und die Seite ist ohne Anmeldung erreichbar.
    return True, "Wenn eine Adresse hinterlegt ist, ist der Link unterwegs. Er gilt eine halbe Stunde."


def check_reset_token(token: str) -> bool:
    stored = (db.get_setting(TOKEN_KEY) or "").strip()
    until = db.get_setting(TOKEN_UNTIL) or "0"
    if not stored or not str(token or "").strip():
        return False
    try:
        if time.time() > int(until):
            return False
    except ValueError:
        return False
    return check_password_hash(stored, token)


def redeem_reset_token(token: str, new_password: str) -> Tuple[bool, str]:
    if not check_reset_token(token):
        return False, "Der Link ist abgelaufen oder schon benutzt worden."
    ok, meldung = _passwort_ok(new_password)
    if not ok:
        return False, meldung
    set_password(new_password)
    db.set_setting(TOKEN_KEY, "")
    db.set_setting(TOKEN_UNTIL, "0")
    return True, "Passwort gesetzt."
