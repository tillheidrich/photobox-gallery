import json
import os
import re
import smtplib
import ssl
from email.message import EmailMessage
from typing import Any, Dict, Optional

from app import db


SMTP_SETTINGS_KEY = "smtp_config_json"

# Absichtlich streng: Lieber einem Gast sagen „schau die Adresse noch mal an"
# als eine Mail verschicken, die nur zurückkommen kann. Keine Adressen in
# Anführungszeichen, keine nackten IP-Domains, kein Punkt am Ende. Dieselbe
# Regel wie in der Box (``boothcore/delivery/mail.py``) — die Box prüft beim
# Eintippen, die Galerie prüft noch einmal beim Verschicken.
ADDRESS_RE = re.compile(
    r"^[A-Za-z0-9!#$%&'*+/=?^_`{|}~-]+"
    r"(?:\.[A-Za-z0-9!#$%&'*+/=?^_`{|}~-]+)*"
    r"@"
    r"(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)+"
    r"[A-Za-z]{2,24}$"
)
MAX_ADDRESS_LENGTH = 254


def valid_address(address: str) -> bool:
    """True, wenn die Adresse plausibel genug ist, um sie abzuschicken."""
    clean = str(address or "").strip()
    if not clean or len(clean) > MAX_ADDRESS_LENGTH or ".." in clean:
        return False
    return bool(ADDRESS_RE.match(clean))



def _default_cfg() -> Dict[str, Any]:
    return {
        "host": "",
        "port": 587,
        "username": "",
        "password": "",
        "from_email": "",
        "use_tls": True,   # STARTTLS
        "use_ssl": False,  # SMTPS (usually port 465)
    }


def get_smtp_config() -> Dict[str, Any]:
    raw = (db.get_setting(SMTP_SETTINGS_KEY) or "").strip()
    cfg = _default_cfg()
    if raw:
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                cfg.update(parsed)
        except Exception:
            pass
    # normalize types
    try:
        cfg["port"] = int(cfg.get("port") or 587)
    except Exception:
        cfg["port"] = 587
    cfg["use_tls"] = bool(cfg.get("use_tls"))
    cfg["use_ssl"] = bool(cfg.get("use_ssl"))
    return cfg


def set_smtp_config(cfg: Dict[str, Any]) -> None:
    base = _default_cfg()
    base.update(cfg or {})
    db.set_setting(SMTP_SETTINGS_KEY, json.dumps(base, ensure_ascii=False))


def smtp_configured() -> bool:
    cfg = get_smtp_config()
    return bool(str(cfg.get("host") or "").strip()) and bool(str(cfg.get("from_email") or "").strip())


def mask_secret(s: str) -> str:
    s = s or ""
    if len(s) <= 4:
        return "****" if s else ""
    return s[:2] + "*" * (len(s) - 4) + s[-2:]


def send_text_mail(*, to_email: str, subject: str, body: str,
                   attachments: Optional[list] = None, reply_to: str = "") -> None:
    """Send a mail. Attachments are (filename, bytes, "image/jpeg") tuples.

    Raises on SMTP errors; callers should catch.
    """
    cfg = get_smtp_config()
    host = str(cfg.get("host") or "").strip()
    port = int(cfg.get("port") or 587)
    username = str(cfg.get("username") or "").strip()
    password = str(cfg.get("password") or "")
    from_email = str(cfg.get("from_email") or "").strip()
    use_tls = bool(cfg.get("use_tls"))
    use_ssl = bool(cfg.get("use_ssl"))

    if not host or not from_email:
        raise RuntimeError("SMTP not configured")

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = from_email
    msg["To"] = to_email
    if reply_to:
        msg["Reply-To"] = reply_to
    msg.set_content(body)
    for name, blob, mime in (attachments or []):
        main, _, sub = str(mime or "application/octet-stream").partition("/")
        msg.add_attachment(blob, maintype=main, subtype=sub or "octet-stream", filename=name)

    context = ssl.create_default_context()

    if use_ssl or port == 465:
        server: Optional[smtplib.SMTP] = smtplib.SMTP_SSL(host, port, context=context, timeout=15)
    else:
        server = smtplib.SMTP(host, port, timeout=15)

    try:
        server.ehlo()
        if (not use_ssl) and use_tls:
            server.starttls(context=context)
            server.ehlo()
        if username:
            server.login(username, password)
        server.send_message(msg)
    finally:
        try:
            server.quit()
        except Exception:
            pass


def send_confirmation_email(to_email: str, event_name: str, confirm_url: str) -> None:
    """Double-Opt-In Bestätigungsmail für Foto-Benachrichtigungen.

    Raises RuntimeError if SMTP is not configured.
    Raises smtplib errors on send failure.
    """
    subject = f"Bitte bestätige deine Anmeldung – {event_name}"
    body = (
        f"Hallo,\n\n"
        f"du hast dich für Foto-Benachrichtigungen der Galerie »{event_name}« angemeldet.\n\n"
        f"Bitte bestätige deine E-Mail-Adresse mit einem Klick auf den folgenden Link:\n\n"
        f"  {confirm_url}\n\n"
        f"Der Link ist 7 Tage gültig. Wenn du dich nicht angemeldet hast, "
        f"kannst du diese Mail einfach ignorieren.\n\n"
        f"Viele Grüße\nDein Fotobox-Team\n"
    )
    send_text_mail(to_email=to_email, subject=subject, body=body)


def send_new_photos_email(
    to_email: str,
    event_name: str,
    gallery_url: str,
    photo_count: int,
    unsubscribe_url: str,
) -> None:
    """Benachrichtigungsmail bei neuen Fotos in einer Galerie."""
    noun = "Foto" if photo_count == 1 else "Fotos"
    subject = f"{photo_count} neue {noun} in »{event_name}«"
    body = (
        f"Hallo,\n\n"
        f"es gibt {photo_count} neue {noun} in der Galerie "
        f"»{event_name}«.\n\n"
        f"Jetzt ansehen: {gallery_url}\n\n"
        f"---\n"
        f"Du erhältst diese Mail, weil du Benachrichtigungen für diese Galerie aktiviert hast.\n"
        f"Abmelden: {unsubscribe_url}\n"
    )
    send_text_mail(to_email=to_email, subject=subject, body=body)
