"""
notifier.py – Benachrichtigungen via Telegram-Bot und Discord-Webhook.

Umgebungsvariablen:
    TG_TOKEN      – Telegram Bot-Token (z.B. 123456789:ABC...)
    TG_CHAT_ID    – Telegram Chat-ID (numerisch, positiv oder negativ)
    DISCORD_WEBHOOK – vollständige Discord-Webhook-URL
    QUIET_FROM    – Beginn der Ruhezeit HH:MM (Standard: leer = keine Ruhezeit)
    QUIET_TO      – Ende der Ruhezeit HH:MM
"""

from __future__ import annotations

import logging
import os
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from typing import List, Optional

from app import redact

_log = logging.getLogger(__name__)

# -----------------------------------------------------------------------
# Konfiguration (mit DB-Fallback)
# -----------------------------------------------------------------------

def _tg_token() -> str:
    from app import db as _db
    try:
        val = _db.get_setting("notify_tg_token") or ""
        if val.strip(): return val.strip()
    except Exception:
        pass
    return os.environ.get("TG_TOKEN", "").strip()

def _tg_chat_id() -> str:
    from app import db as _db
    try:
        val = _db.get_setting("notify_tg_chat_id") or ""
        if val.strip(): return val.strip()
    except Exception:
        pass
    return os.environ.get("TG_CHAT_ID", "").strip()

def _discord_url() -> str:
    from app import db as _db
    try:
        val = _db.get_setting("notify_discord_webhook") or ""
        if val.strip(): return val.strip()
    except Exception:
        pass
    return os.environ.get("DISCORD_WEBHOOK", "").strip()

def _quiet_from() -> str:
    from app import db as _db
    try:
        val = _db.get_setting("notify_quiet_from") or ""
        if val.strip(): return val.strip()
    except Exception:
        pass
    return os.environ.get("QUIET_FROM", "").strip()

def _quiet_to() -> str:
    from app import db as _db
    try:
        val = _db.get_setting("notify_quiet_to") or ""
        if val.strip(): return val.strip()
    except Exception:
        pass
    return os.environ.get("QUIET_TO", "").strip()


# -----------------------------------------------------------------------
# Ruhezeit-Prüfung
# -----------------------------------------------------------------------

def _in_quiet_hours() -> bool:
    q_from = _quiet_from()
    q_to   = _quiet_to()
    if not q_from or not q_to:
        return False
    try:
        fh, fm = map(int, q_from.split(":"))
        th, tm = map(int, q_to.split(":"))
        from_min = fh * 60 + fm
        to_min   = th * 60 + tm
        now = datetime.now()
        now_min  = now.hour * 60 + now.minute
        if from_min <= to_min:
            return from_min <= now_min < to_min
        else:  # overnight span (z.B. 23:00 → 08:00)
            return now_min >= from_min or now_min < to_min
    except (ValueError, AttributeError) as exc:
        _log.warning("Ungültige QUIET_FROM/QUIET_TO-Konfiguration: %s", exc)
        return False


# -----------------------------------------------------------------------
# HTTP-Hilfsfunktion
# -----------------------------------------------------------------------

def _post_json(url: str, payload: dict, timeout: int = 10) -> bool:
    """Sendet einen JSON-POST und gibt True bei Erfolg zurück."""
    import json
    data = json.dumps(payload).encode()
    req  = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status < 400
    except urllib.error.HTTPError as exc:
        _log.warning("HTTP-Fehler beim Senden der Benachrichtigung: %s %s", exc.code, exc.reason)
        return False
    except Exception as exc:
        _log.warning("Netzwerkfehler beim Senden der Benachrichtigung: %s", exc)
        return False


# -----------------------------------------------------------------------
# Telegram
# -----------------------------------------------------------------------

def _tg_configured() -> bool:
    """A channel that is not set up is skipped quietly; only a configured one
    that fails is worth a warning (final review T3)."""
    return bool(_tg_token() and _tg_chat_id())


def send_telegram(text: str, parse_mode: str = "HTML") -> bool:
    """Sendet eine Nachricht via Telegram-Bot. Gibt True bei Erfolg zurück."""
    token   = _tg_token()
    chat_id = _tg_chat_id()
    if not token or not chat_id:
        return False
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    return _post_json(url, {"chat_id": chat_id, "text": text, "parse_mode": parse_mode})


def send_telegram_photo(photo_url: str, caption: str = "") -> bool:
    """Sendet ein Foto via Telegram."""
    token   = _tg_token()
    chat_id = _tg_chat_id()
    if not token or not chat_id:
        return False
    url = f"https://api.telegram.org/bot{token}/sendPhoto"
    return _post_json(url, {"chat_id": chat_id, "photo": photo_url, "caption": caption})


# -----------------------------------------------------------------------
# Discord
# -----------------------------------------------------------------------

def send_discord(content: str, *, username: str = "Photobox", embeds: Optional[list] = None) -> bool:
    """Sendet eine Nachricht via Discord-Webhook."""
    webhook = _discord_url()
    if not webhook:
        return False
    payload: dict = {"content": content, "username": username}
    if embeds:
        payload["embeds"] = embeds
    return _post_json(webhook, payload)


# -----------------------------------------------------------------------
# Generischer Webhook
# -----------------------------------------------------------------------

def send_webhook(payload: dict) -> bool:
    """Sendet einen generischen HTTP-Webhook POST."""
    from app import db as _db
    try:
        url = (_db.get_setting("notify_webhook_url") or "").strip()
    except Exception:
        url = ""
    if not url:
        return False
    return _post_json(url, payload)


# -----------------------------------------------------------------------
# Batch-Upload-Benachrichtigung
# -----------------------------------------------------------------------

_upload_lock  = threading.Lock()
_upload_batch: dict[int, dict] = {}   # event_id → {count, event_name, gallery_url}
_upload_timer: Optional[threading.Timer] = None

_BATCH_DELAY = 30   # Sekunden, nach denen die gebündelte Nachricht gesendet wird


def _flush_batch() -> None:
    global _upload_timer
    with _upload_lock:
        batch = dict(_upload_batch)
        _upload_batch.clear()
        _upload_timer = None

    if _in_quiet_hours():
        _log.info("Batch-Benachrichtigung in Ruhezeit unterdrückt.")
        return

    for event_id, info in batch.items():
        count = info["count"]
        name  = info.get("event_name", f"Event #{event_id}")
        url   = info.get("gallery_url", "")
        text  = (
            f"📸 <b>{count} neues Foto{'s' if count != 1 else ''}</b> "
            f"in <b>{name}</b> hochgeladen!"
            + (f"\n🔗 {url}" if url else "")
        )
        if _tg_configured() and not send_telegram(text):
            _log.warning("Telegram-Benachrichtigung für Event %d fehlgeschlagen.", event_id)

        discord_text = (
            f"📸 **{count} neues Foto{'s' if count != 1 else ''}** in **{name}** hochgeladen!"
            + (f"\n{url}" if url else "")
        )
        if _discord_url() and not send_discord(discord_text):
            _log.warning("Discord-Benachrichtigung für Event %d fehlgeschlagen.", event_id)

        # Send generic webhook
        webhook_payload = {
            "event": "batch_upload",
            "event_id": event_id,
            "event_name": name,
            "count": count,
            "gallery_url": url,
        }
        if not send_webhook(webhook_payload):
            _log.debug("Webhook-Benachrichtigung für Event %d nicht konfiguriert oder fehlgeschlagen.", event_id)

        # Send email to confirmed subscribers
        try:
            from app import db, emailer
            if emailer.smtp_configured():
                base_url = db.get_setting("app_base_url") or ""
                pending = db.pending_confirmed_subscriptions(event_id)
                notified_tokens: list = []
                for sub in pending:
                    try:
                        unsub_url = f"{base_url}/notify/unsubscribe/{sub['token']}" if base_url else ""
                        emailer.send_new_photos_email(
                            to_email=sub["email"],
                            event_name=name,
                            gallery_url=url,
                            photo_count=count,
                            unsubscribe_url=unsub_url,
                        )
                        notified_tokens.append(sub["token"])
                    except Exception as mail_err:
                        _log.warning("E-Mail an %s fehlgeschlagen: %s", redact.address(sub["email"]),
                                     redact.text(mail_err))
                if notified_tokens:
                    db.mark_notified(notified_tokens)
                    _log.info("E-Mail-Benachrichtigungen gesendet an %d Abonnent(en) für Event %d.", len(notified_tokens), event_id)
        except Exception as e:
            _log.warning("Subscriber-E-Mail-Batch für Event %d fehlgeschlagen: %s", event_id, e)


def batch_upload_notification(event_id: int, event_name: str, gallery_url: str = "") -> None:
    """
    Sammelt Upload-Benachrichtigungen für _BATCH_DELAY Sekunden und sendet
    dann eine gebündelte Nachricht.
    Thread-sicher.
    """
    global _upload_timer
    with _upload_lock:
        if event_id in _upload_batch:
            _upload_batch[event_id]["count"] += 1
        else:
            _upload_batch[event_id] = {
                "count":       1,
                "event_name":  event_name,
                "gallery_url": gallery_url,
            }

        # Timer (neu) starten – immer abbrechen und neu anlegen
        if _upload_timer is not None:
            _upload_timer.cancel()
        _upload_timer = threading.Timer(_BATCH_DELAY, _flush_batch)
        _upload_timer.daemon = True
        _upload_timer.start()


# -----------------------------------------------------------------------
# Event-Veröffentlichungs-Benachrichtigung
# -----------------------------------------------------------------------

def notify_event_published(event_name: str, gallery_url: str = "") -> None:
    """Benachrichtigung wenn ein Event veröffentlicht wird."""
    if _in_quiet_hours():
        return
    text = (
        f"🎉 Galerie <b>{event_name}</b> ist jetzt online!"
        + (f"\n🔗 {gallery_url}" if gallery_url else "")
    )
    if _tg_configured() and not send_telegram(text):
        _log.warning("Telegram-Benachrichtigung (Event published) fehlgeschlagen.")
    discord_text = (
        f"🎉 Galerie **{event_name}** ist jetzt online!"
        + (f"\n{gallery_url}" if gallery_url else "")
    )
    if _discord_url() and not send_discord(discord_text):
        _log.warning("Discord-Benachrichtigung (Event published) fehlgeschlagen.")

    # Send generic webhook
    webhook_payload = {
        "event": "event_published",
        "event_name": event_name,
        "gallery_url": gallery_url,
    }
    if not send_webhook(webhook_payload):
        _log.debug("Webhook-Benachrichtigung (Event published) nicht konfiguriert oder fehlgeschlagen.")


def notify_comment(*, event_name: str, client_name: str, admin_url: str = "") -> None:
    """A client commented. Which gallery and who - never the text, not even in the log."""
    if _in_quiet_hours():
        return
    import html as _html
    plain = f"Neuer Kommentar von {client_name} in {event_name}" + (f"\n{admin_url}" if admin_url else "")
    try:
        send_telegram(_html.escape(plain))
        send_discord(plain)
        send_webhook({"event": "comment", "event_name": event_name, "client": client_name,
                      "admin_url": admin_url})
    except Exception:                   # noqa: BLE001 — a notice never breaks the comment
        _log.warning("Kommentar-Benachrichtigung fehlgeschlagen.")


def notify_booth_problem(device_name: str, reasons: Optional[list] = None) -> None:
    """Meldung, wenn ein Fotobox-PC auf Rot springt.

    Bewusst NICHT von den Ruhezeiten unterdrückt: Wenn nachts um eins auf einer
    laufenden Hochzeit die Box ausfällt, ist genau das die Nachricht, die der
    Betreiber sehen muss.
    """
    lines = reasons or []
    detail_tg = "\n".join(f"• {r}" for r in lines)
    text = f"🔴 Fotobox <b>{device_name}</b> meldet ein Problem" + (f"\n{detail_tg}" if detail_tg else "")
    if _tg_configured() and not send_telegram(text):
        _log.warning("Telegram-Benachrichtigung (Booth-Problem) fehlgeschlagen.")

    discord_text = f"🔴 Fotobox **{device_name}** meldet ein Problem" + (f"\n{detail_tg}" if detail_tg else "")
    if _discord_url() and not send_discord(discord_text):
        _log.warning("Discord-Benachrichtigung (Booth-Problem) fehlgeschlagen.")

    if not send_webhook({"event": "booth_problem",
                         "device": device_name,
                         "reasons": lines}):
        _log.debug("Webhook (Booth-Problem) nicht konfiguriert oder fehlgeschlagen.")


# -----------------------------------------------------------------------
# Telegram-Command-Polling (optional, für /status Befehl)
# -----------------------------------------------------------------------

_poll_thread:  Optional[threading.Thread] = None
_poll_stop     = threading.Event()
_last_update_id: int = 0


def _poll_commands() -> None:
    """Hintergrund-Thread: pollt Telegram-Updates und antwortet auf /status."""
    global _last_update_id
    backoff = 1
    while not _poll_stop.is_set():
        token = _tg_token()
        if not token:
            time.sleep(30)
            continue
        try:
            url = (
                f"https://api.telegram.org/bot{token}/getUpdates"
                f"?timeout=20&offset={_last_update_id + 1}"
            )
            req = urllib.request.Request(url, method="GET")
            with urllib.request.urlopen(req, timeout=25) as resp:
                import json
                body = json.loads(resp.read())
            updates: list = body.get("result", [])
            backoff = 1  # Reset bei Erfolg
            for upd in updates:
                _last_update_id = max(_last_update_id, upd.get("update_id", 0))
                msg = upd.get("message", {})
                text = (msg.get("text") or "").strip()
                chat = msg.get("chat", {}).get("id")
                if text.startswith("/status") and str(chat) == _tg_chat_id():
                    _handle_status_command(chat)
        except Exception as exc:
            _log.warning("Telegram-Polling-Fehler: %s", exc)
            backoff = min(backoff * 2, 120)
        _poll_stop.wait(backoff)


def _handle_status_command(chat_id: int) -> None:
    from app import db as _db
    try:
        events = _db.list_events()
        count  = len(events) if events else 0
        text   = f"✅ Photobox läuft. {count} Event{'s' if count != 1 else ''} in der Datenbank."
    except Exception as exc:
        text = f"⚠️ Fehler beim Abrufen des Status: {exc}"
    token = _tg_token()
    if token:
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        _post_json(url, {"chat_id": chat_id, "text": text})


def start_command_polling() -> None:
    """Startet den Telegram-Command-Polling-Thread (einmalig)."""
    global _poll_thread
    if _poll_thread and _poll_thread.is_alive():
        return
    if not _tg_token():
        return
    _poll_stop.clear()
    _poll_thread = threading.Thread(target=_poll_commands, daemon=True, name="tg-poll")
    _poll_thread.start()
    _log.info("Telegram-Command-Polling gestartet.")


def stop_command_polling() -> None:
    """Stoppt den Telegram-Command-Polling-Thread."""
    _poll_stop.set()
