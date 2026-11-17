import json
import hashlib
import hmac
import os
import secrets
import sqlite3
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from werkzeug.security import check_password_hash, generate_password_hash


# -----------------------------
# Paths / DB connection
# -----------------------------

def data_dir() -> Path:
    return Path(os.environ.get("DATA_DIR", "./data")).resolve()


def db_path() -> Path:
    d = data_dir()
    d.mkdir(parents=True, exist_ok=True)
    return d / "gallery.sqlite3"


def get_db() -> sqlite3.Connection:
    con = sqlite3.connect(str(db_path()))
    con.row_factory = sqlite3.Row
    try:
        # Ensure ON DELETE CASCADE works for our relations (likes, stats, subscriptions).
        con.execute("PRAGMA foreign_keys = ON;")
    except Exception:
        pass
    return con


def _table_cols(cur: sqlite3.Cursor, table: str) -> set[str]:
    rows = cur.execute(f"PRAGMA table_info({table});").fetchall()
    return {str(r[1]) for r in rows}


# -----------------------------
# Init + migrations
# -----------------------------

def init_db() -> None:
    """Create tables + run lightweight migrations (ALTER TABLE ADD COLUMN).

    Wichtig für Updates:
    - DB liegt im gemounteten /data (Volume) -> bleibt über Container-Updates erhalten.
    - init_db() ist idempotent und migriert fehlende Spalten automatisch nach.
    """
    con = get_db()
    cur = con.cursor()

    # Base tables (initial schema)
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            slug TEXT NOT NULL UNIQUE,
            name TEXT NOT NULL,
            password_hash TEXT,
            upload_token TEXT NOT NULL UNIQUE,
            retention_days INTEGER NOT NULL DEFAULT 30,
            print_threshold INTEGER NOT NULL DEFAULT 350,
            design_json TEXT NOT NULL DEFAULT '{}',
            whatsapp_number TEXT,
            whatsapp_message TEXT,
            privacy_url TEXT,
            followup_message TEXT,
            google_review_url TEXT,
            booking_url TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            last_upload_at TEXT
        );
        """
    )

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS media (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_id INTEGER NOT NULL,
            kind TEXT NOT NULL,             -- photo|print|gif|video
            filename TEXT NOT NULL,
            path_original TEXT NOT NULL,
            path_webp TEXT,
            path_thumb TEXT,
            size_bytes INTEGER NOT NULL DEFAULT 0,
            sha256 TEXT,
            share_token TEXT NOT NULL UNIQUE,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            FOREIGN KEY(event_id) REFERENCES events(id) ON DELETE CASCADE
        );
        """
    )

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS stats (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_id INTEGER NOT NULL,
            media_id INTEGER,
            action TEXT NOT NULL,           -- download|whatsapp|link_share|view_gallery|...
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            FOREIGN KEY(event_id) REFERENCES events(id) ON DELETE CASCADE,
            FOREIGN KEY(media_id) REFERENCES media(id) ON DELETE SET NULL
        );
        """
    )

    # Global settings (logo, smtp, etc.)
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT,
            updated_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        """
    )

    # Lightweight migrations (add missing columns)
    event_cols = _table_cols(cur, "events")
    if "cover_path" not in event_cols:
        cur.execute("ALTER TABLE events ADD COLUMN cover_path TEXT;")
    if "redirect_url" not in event_cols:
        cur.execute("ALTER TABLE events ADD COLUMN redirect_url TEXT;")
    if "access_token" not in event_cols:
        cur.execute("ALTER TABLE events ADD COLUMN access_token TEXT;")

    # Release 2.1: status + scheduled publish
    if "status" not in event_cols:
        # live = öffentlich (optional Passwort), draft = nur via Token/Passwort, archived = optional Redirect
        cur.execute("ALTER TABLE events ADD COLUMN status TEXT NOT NULL DEFAULT 'live';")
    if "scheduled_live_at" not in event_cols:
        # ISO-ish timestamp string (server local time). Example: 2025-12-31T20:00
        cur.execute("ALTER TABLE events ADD COLUMN scheduled_live_at TEXT;")

    stats_cols = _table_cols(cur, "stats")
    if "meta_json" not in stats_cols:
        cur.execute("ALTER TABLE stats ADD COLUMN meta_json TEXT;")

    # Release 2.3: Additional event columns for design, review URLs, and improvements
    event_cols = _table_cols(cur, "events")
    if "design_json" not in event_cols:
        cur.execute("ALTER TABLE events ADD COLUMN design_json TEXT DEFAULT '{}';")
    if "whatsapp_number" not in event_cols:
        cur.execute("ALTER TABLE events ADD COLUMN whatsapp_number TEXT DEFAULT '';")
    if "google_review_url" not in event_cols:
        cur.execute("ALTER TABLE events ADD COLUMN google_review_url TEXT DEFAULT '';")
    if "booking_url" not in event_cols:
        cur.execute("ALTER TABLE events ADD COLUMN booking_url TEXT DEFAULT '';")
    # Eigene Texte je Event (siehe app/texts.py) — überschreiben die Übersetzung
    if "texts_json" not in event_cols:
        cur.execute("ALTER TABLE events ADD COLUMN texts_json TEXT DEFAULT '{}';")
    
    # Add published column for event visibility control
    if "published" not in event_cols:
        cur.execute("ALTER TABLE events ADD COLUMN published INTEGER DEFAULT 1;")

    # Eindeutiger, menschenfreundlicher Galerie-Code (z.B. für Code-Suche auf der Startseite)
    if "code" not in event_cols:
        cur.execute("ALTER TABLE events ADD COLUMN code TEXT;")
    for (eid,) in cur.execute("SELECT id FROM events WHERE code IS NULL OR code=''").fetchall():
        cur.execute("UPDATE events SET code=? WHERE id=?", (_unique_event_code(cur), eid))
    cur.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_events_code ON events(code);")

    # Release 2.1: likes + notify subscriptions
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS likes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_id INTEGER NOT NULL,
            media_id INTEGER NOT NULL,
            device_id TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            UNIQUE(media_id, device_id),
            FOREIGN KEY(event_id) REFERENCES events(id) ON DELETE CASCADE,
            FOREIGN KEY(media_id) REFERENCES media(id) ON DELETE CASCADE
        );
        """
    )

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS notify_subscriptions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_id INTEGER NOT NULL,
            email TEXT NOT NULL,
            token TEXT NOT NULL UNIQUE,
            confirmed_at TEXT,
            notified_at TEXT,
            unsubscribed_at TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            FOREIGN KEY(event_id) REFERENCES events(id) ON DELETE CASCADE
        );
        """
    )


    # Release 2.2: Geräte (Fotobox-PCs) + Slug-Aliases
    # Geräte-Keys werden in der DB gespeichert (im /data Volume) und sind damit update-sicher.
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS devices (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            notes TEXT,
            api_key TEXT NOT NULL UNIQUE,
            active INTEGER NOT NULL DEFAULT 1,
            default_watch_path TEXT,
            default_event_glob TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            last_seen_at TEXT,
            last_ip TEXT
        );
        """
    )

    # Verlauf der Booth-Zustände. Bewusst schlank: wir wollen erkennen können,
    # wann ein Booth wegbrach oder der Speicher zur Neige ging — kein volles
    # Monitoring-System. Alte Zeilen werden regelmäßig gekappt.
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS device_heartbeats (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            device_id INTEGER NOT NULL REFERENCES devices(id) ON DELETE CASCADE,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            health TEXT,
            disk_free_gb REAL,
            queue_pending INTEGER,
            queue_failed INTEGER,
            booth_running INTEGER,
            payload_json TEXT
        );
        """
    )
    cur.execute("CREATE INDEX IF NOT EXISTS idx_hb_device ON device_heartbeats(device_id, created_at);")

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS slug_aliases (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            alias_slug TEXT NOT NULL UNIQUE,
            event_id INTEGER NOT NULL,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            FOREIGN KEY(event_id) REFERENCES events(id) ON DELETE CASCADE
        );
        """
    )

    # Indexes (safe to re-run)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_media_event ON media(event_id);")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_stats_event ON stats(event_id);")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_stats_media ON stats(media_id);")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_stats_action ON stats(action);")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_likes_event ON likes(event_id);")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_likes_media ON likes(media_id);")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_notify_event ON notify_subscriptions(event_id);")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_notify_email ON notify_subscriptions(email);")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_devices_active ON devices(active);")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_slug_alias_event ON slug_aliases(event_id);")

    # Ensure every event has an access token (used for QR magic-link)
    rows = cur.execute("SELECT id FROM events WHERE access_token IS NULL OR access_token=''").fetchall()
    for r in rows:
        token = secrets.token_hex(16)
        cur.execute("UPDATE events SET access_token=? WHERE id=?", (token, int(r["id"])))

    # Ensure every event has a status (defensive for older DBs)
    try:
        cur.execute("UPDATE events SET status='live' WHERE status IS NULL OR TRIM(status)=''")
    except Exception:
        pass

    # Bootstrap admin credentials into settings if missing.
    # After first start, credentials live in DB (/data) and survive container updates.
    row_user = cur.execute("SELECT value FROM settings WHERE key='admin_username'").fetchone()
    row_hash = cur.execute("SELECT value FROM settings WHERE key='admin_password_hash'").fetchone()
    if not row_user:
        cur.execute(
            "INSERT OR REPLACE INTO settings (key, value) VALUES ('admin_username', ?)",
            (os.environ.get("ADMIN_USER", "admin"),),
        )
    if not row_hash:
        # Kein Standardpasswort „admin". Ist beim ersten Start keines gesetzt,
        # erzeugt die Galerie ein zufälliges, schreibt es genau einmal ins
        # Protokoll und verlangt beim ersten Anmelden eine Änderung. Dazu ein
        # Wiederherstellungscode, damit man später ohne Serverzugang wieder
        # hineinkommt.
        import logging
        import secrets as _secrets

        pw = os.environ.get("ADMIN_PASSWORD", "")
        generated = not pw
        pw = pw or _secrets.token_urlsafe(9)
        cur.execute(
            "INSERT OR REPLACE INTO settings (key, value) VALUES ('admin_password_hash', ?)",
            (generate_password_hash(pw),),
        )
        if generated:
            # The log is the only channel a fresh container has to its
            # operator, so the start password goes there once. It is only a
            # key for the first login: that login must replace it, and only
            # then is the recovery code made and shown in the browser, once.
            # Until 11.11.2026 the permanent recovery code was logged here
            # too and stayed in the container log (S16).
            cur.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('admin_password_temp', '1')")
            logging.getLogger("photobox").warning(
                "Erste Anmeldung: Benutzer %s, Startpasswort %s — gilt nur für die erste "
                "Anmeldung; danach ein eigenes Passwort setzen, der Wiederherstellungscode "
                "erscheint dann einmal im Browser.",
                os.environ.get("ADMIN_USER", "admin"), pw)

    con.commit()

    # New settings for v2
    new_keys = [
        ("homepage_headline", "Willkommen bei unserer Fotobox!"),
        ("homepage_subtext", ""),
        ("homepage_hero_image_path", ""),
        ("homepage_show_galleries", "0"),
        ("homepage_show_empty_events", "0"),
        ("notify_tg_token", ""),
        ("notify_tg_chat_id", ""),
        ("notify_discord_webhook", ""),
        ("notify_webhook_url", ""),
        ("notify_quiet_from", ""),
        ("notify_quiet_to", ""),
        ("notify_on_upload", "1"),
        ("notify_on_publish", "1"),
        ("notify_on_new_device", "0"),
        ("impressum_text", ""),
        ("datenschutz_text", ""),
        ("footer_custom_link_label", ""),
        ("footer_custom_link_url", ""),
        ("footer_show_powered_by", "1"),
        ("guest_upload_enabled", "0"),
        ("zip_download_enabled", "0"),
        ("zip_download_admin_only", "1"),
        ("selection_download_enabled", "1"),
        ("download_sizes_enabled", "1"),
        ("setup_completed", "0"),
        ("app_base_url", ""),
        ("license_key", ""),
        ("license_plan", "starter"),
    ]
    for k, v in new_keys:
        cur.execute(
            "INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)",
            (k, v),
        )

    # Add kiosk_text column to events table if it doesn't exist
    try:
        cur.execute("ALTER TABLE events ADD COLUMN kiosk_text TEXT DEFAULT ''")
    except Exception:
        pass  # column already exists

    # Add date and description columns
    try:
        cur.execute("ALTER TABLE events ADD COLUMN date TEXT")
    except Exception:
        pass
    try:
        cur.execute("ALTER TABLE events ADD COLUMN description TEXT DEFAULT ''")
    except Exception:
        pass
    try:
        cur.execute("ALTER TABLE events ADD COLUMN cover_original_path TEXT")
    except Exception:
        pass
    # Link preview (app/link_preview.py): the admin's own image, and whether
    # it may be shown for a gallery that is closed to strangers.
    ev_cols = _table_cols(cur, "events")
    if "og_image_path" not in ev_cols:
        cur.execute("ALTER TABLE events ADD COLUMN og_image_path TEXT")
    if "og_image_public" not in ev_cols:
        cur.execute("ALTER TABLE events ADD COLUMN og_image_public INTEGER NOT NULL DEFAULT 0")

    # Feature 1: Per-device landing pages (Release 2.4)
    device_cols = _table_cols(cur, "devices")
    if "landing_event_id" not in device_cols:
        cur.execute("ALTER TABLE devices ADD COLUMN landing_event_id INTEGER REFERENCES events(id) ON DELETE SET NULL")
    if "landing_headline" not in device_cols:
        cur.execute("ALTER TABLE devices ADD COLUMN landing_headline TEXT")
    if "landing_subtext" not in device_cols:
        cur.execute("ALTER TABLE devices ADD COLUMN landing_subtext TEXT")
    if "landing_bg_path" not in device_cols:
        cur.execute("ALTER TABLE devices ADD COLUMN landing_bg_path TEXT")
    # Booth-Fernüberwachung: aktueller Zustand direkt am Gerät, damit die
    # Übersicht ohne Join über den Verlauf auskommt.
    if "last_heartbeat_at" not in device_cols:
        cur.execute("ALTER TABLE devices ADD COLUMN last_heartbeat_at TEXT")
    if "status_json" not in device_cols:
        cur.execute("ALTER TABLE devices ADD COLUMN status_json TEXT")
    if "last_health" not in device_cols:
        cur.execute("ALTER TABLE devices ADD COLUMN last_health TEXT")
    # S-6 (15.11.2026): device keys are stored as SHA-256 only. Additive: a
    # new column; the old one keeps its NOT NULL UNIQUE constraint and gets a
    # placeholder that is no key. Keys of booths that are paired today are
    # hashed in place, so those booths keep working untouched.
    if "api_key_hash" not in device_cols:
        cur.execute("ALTER TABLE devices ADD COLUMN api_key_hash TEXT")
    for did, plain in cur.execute(
            "SELECT id, api_key FROM devices WHERE api_key_hash IS NULL OR api_key_hash=''").fetchall():
        if plain and not str(plain).startswith(_KEY_PLACEHOLDER):
            cur.execute("UPDATE devices SET api_key_hash=?, api_key=? WHERE id=?",
                        (hash_device_key(plain), _key_placeholder(), did))
    cur.execute("CREATE UNIQUE INDEX IF NOT EXISTS devices_api_key_hash ON devices(api_key_hash)")

    # Feature 2: Featured galleries on home page (Release 2.4)
    event_cols = _table_cols(cur, "events")  # Refresh event_cols
    if "featured" not in event_cols:
        cur.execute("ALTER TABLE events ADD COLUMN featured INTEGER NOT NULL DEFAULT 0")

    con.commit()
    con.close()


# -----------------------------
# Settings
# -----------------------------

def base_url() -> str:
    """The gallery's own public address, without a trailing slash.

    Order: what the admin set, then the BASE_URL the container was started
    with, then nothing — callers fall back to the address of the request. The
    env variable matters when a gallery is reachable under more than one
    domain: QR codes and pairing codes must name one of them, not whichever
    one the admin happened to be browsing.
    """
    import os as _os

    return ((get_setting("app_base_url") or _os.environ.get("BASE_URL", "")) or "").strip().rstrip("/")


_LOOPBACK_HOSTS = {"localhost", "127.0.0.1", "::1"}


def link_base() -> str:
    """The address for links that leave the gallery: reset mails, pairing codes.

    Fails closed (S-5): the configured base address first; without one, the
    request's own host only when it can be trusted — loopback, or a name in
    ``ALLOWED_HOSTS`` (comma-separated). Anything else returns "" and the
    caller refuses, instead of mailing a link to whatever host a request named.
    Must be called inside a request.
    """
    import os as _os

    from flask import request

    configured = base_url()
    if configured:
        return configured
    host = (request.host or "").rsplit(":", 1)[0].strip("[]").lower() if request.host else ""
    if request.host and request.host.startswith("["):
        host = request.host.split("]")[0].strip("[").lower()
    allowed = {h.strip().lower() for h in _os.environ.get("ALLOWED_HOSTS", "").split(",") if h.strip()}
    if host and (host in _LOOPBACK_HOSTS or host in allowed):
        return request.host_url.rstrip("/")
    return ""


def base_url_for(event=None) -> str:
    """Die Adresse, unter der die Links *dieses* Events laufen.

    Für die meisten Events ist das die Hauptadresse. Betreibt jemand die
    Galerie für einen Kunden mit eigener Domain, ist es dessen Domain — der
    QR an der Box, der geteilte Link und die Mail sollen dieselbe Marke
    tragen wie alles andere, was der Gast von diesem Kunden sieht.
    """
    if not event:
        return base_url()
    try:
        from app import domains
        return domains.fuer_event(event) or base_url()
    except Exception:                      # noqa: BLE001 — Links dürfen daran nie scheitern
        return base_url()


# ── Zweisprachige freie Texte ────────────────────────────────
#
# Alles, was der Betreiber selbst schreibt — Überschrift der Startseite,
# Impressum, Datenschutz — kann zweimal dastehen: einmal deutsch unter dem
# Schlüssel, einmal englisch unter ``<schlüssel>_en``. Fehlt die englische
# Fassung, gilt die deutsche. Eine leere Seite wäre schlechter als eine in
# der falschen Sprache.

SPRACHEN = ("de", "en")


def text_setting(key: str, lang: str = "de") -> str:
    """Freien Text in der gewünschten Sprache, mit Rückfall auf Deutsch."""
    if lang and lang != "de":
        wert = (get_setting(f"{key}_{lang}") or "").strip()
        if wert:
            return wert
    return get_setting(key) or ""


def languages() -> str:
    """Welche Sprachen die Gästeseiten anbieten: ``both``, ``de`` oder ``en``."""
    wert = (get_setting("site_languages") or "both").strip().lower()
    return wert if wert in ("both", "de", "en") else "both"


def get_setting(key: str) -> Optional[str]:
    con = get_db()
    row = con.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    con.close()
    return str(row["value"]) if row and row["value"] is not None else None


def set_setting(key: str, value: Optional[str]) -> None:
    con = get_db()
    con.execute(
        """
        INSERT INTO settings (key, value) VALUES (?, ?)
        ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=datetime('now');
        """,
        (key, value),
    )
    con.commit()
    con.close()


def delete_setting(key: str) -> None:
    con = get_db()
    con.execute("DELETE FROM settings WHERE key=?", (key,))
    con.commit()
    con.close()


# -----------------------------
# Events
# -----------------------------

def set_event_password(event_id: int, password: str) -> None:
    con = get_db()
    con.execute(
        "UPDATE events SET password_hash=? WHERE id=?",
        (generate_password_hash(password) if password else None, event_id),
    )
    con.commit()
    con.close()


def check_event_password(event_row: sqlite3.Row, password: str) -> bool:
    pw_hash = event_row["password_hash"]
    if not pw_hash:
        return True
    if not password:
        return False
    return check_password_hash(pw_hash, password)


# Menschenfreundliches Code-Alphabet (ohne verwechselbare 0/O/1/I/L)
_CODE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"


def generate_event_code(n: int = 6) -> str:
    return "".join(secrets.choice(_CODE_ALPHABET) for _ in range(n))


def _unique_event_code(cur, n: int = 6) -> str:
    """Erzeugt einen in der events-Tabelle noch nicht vergebenen Code."""
    for _ in range(25):
        c = generate_event_code(n)
        if not cur.execute("SELECT 1 FROM events WHERE code=?", (c,)).fetchone():
            return c
    return generate_event_code(n + 2)


def get_event_by_code(code: str) -> Optional[Dict[str, Any]]:
    code = (code or "").strip().upper().replace(" ", "").replace("-", "")
    if not code:
        return None
    con = get_db()
    try:
        row = con.execute("SELECT * FROM events WHERE code=?", (code,)).fetchone()
        return dict(row) if row else None
    finally:
        con.close()


#: One retention default for every way an event comes into being: admin
#: form, booth, MCP, database column. Until 11.11.2026 the admin form said 90
#: while the column and the booth said 30.
DEFAULT_RETENTION_DAYS = 30


def create_event(
    *,
    slug: str,
    name: str,
    upload_token: str,
    password: str = "",
    retention_days: int = DEFAULT_RETENTION_DAYS,
    print_threshold: int = 350,
    design_json: str = "{}",
    access_token: Optional[str] = None,
    status: str = "live",
    scheduled_live_at: Optional[str] = None,
) -> int:
    """Create new event and returns its id."""
    con = get_db()
    cur = con.cursor()
    code = _unique_event_code(cur)
    cur.execute(
        """
        INSERT INTO events (
          slug, name, password_hash, upload_token,
          retention_days, print_threshold, design_json,
          access_token, status, scheduled_live_at, code,
          last_upload_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
        """,
        (
            slug,
            name,
            generate_password_hash(password) if password else None,
            upload_token,
            retention_days,
            print_threshold,
            design_json or "{}",
            access_token or secrets.token_hex(16),
            (status or "live").strip().lower(),
            scheduled_live_at,
            code,
        ),
    )
    con.commit()
    event_id = cur.lastrowid
    con.close()
    # The owner's account default "no automatic deletion" (app/retention.py)
    # is written into the new event, so a later change of the default does
    # not reach galleries created before it.
    if (get_setting("retention_auto_default") or "1").strip() == "0":
        try:
            update_event(int(event_id), auto_delete=0)
        except sqlite3.OperationalError:      # column added by ecosystem.init_schema on first start
            pass
    return int(event_id)


def update_event(event_id: int, fields: Optional[Dict[str, Any]] = None, **kwargs) -> None:
    """Update event fields. Accepts a dict or keyword arguments."""
    if fields is None:
        fields = {}
    if kwargs:
        fields = {**fields, **kwargs}
    if not fields:
        return
    allowed = {
        "name",
        "slug",
        "date",
        "description",
        "published",
        "password",
        "password_hash",
        "retention_days",
        "print_threshold",
        "design_json",
        "kiosk_text",
        "whatsapp_number",
        "whatsapp_message",
        "privacy_url",
        "followup_message",
        "google_review_url",
        "booking_url",
        "cover_path",
        "cover_original_path",
        "og_image_path",
        "og_image_public",
        "redirect_url",
        "access_token",
        "status",
        "scheduled_live_at",
        "gallery_json",
        "texts_json",
        "upload_token",
        "kind",
        "auto_delete",
    }
    # Auto-hash password if provided in plain text
    if "password" in fields:
        from werkzeug.security import generate_password_hash as _gph
        pw = fields.pop("password")
        if pw:
            fields["password_hash"] = _gph(pw)
        else:
            fields["password_hash"] = None
    pairs = [(k, v) for k, v in fields.items() if k in allowed]
    if not pairs:
        return

    sets = ", ".join([f"{k}=?" for k, _ in pairs])
    values = [v for _, v in pairs] + [event_id]

    con = get_db()
    con.execute(f"UPDATE events SET {sets} WHERE id=?", values)
    con.commit()
    con.close()


def get_event_by_id(event_id: int) -> Optional[Dict[str, Any]]:
    con = get_db()
    row = con.execute("SELECT * FROM events WHERE id=?", (event_id,)).fetchone()
    con.close()
    return dict(row) if row else None


def get_event_by_slug(slug: str) -> Optional[Dict[str, Any]]:
    """Resolve event by slug.

    Release 2.2:
    - unterstützt Slug-Aliases (alte Slugs bleiben gültig).
    """
    con = get_db()
    try:
        row = con.execute("SELECT * FROM events WHERE slug=?", (slug,)).fetchone()
        if row:
            return dict(row)

        # Fallback: alias -> canonical event
        try:
            a = con.execute("SELECT event_id FROM slug_aliases WHERE alias_slug=?", (slug,)).fetchone()
        except sqlite3.OperationalError:
            return None
        if a:
            row2 = con.execute("SELECT * FROM events WHERE id=?", (int(a["event_id"]),)).fetchone()
            return dict(row2) if row2 else None
        return None
    finally:
        con.close()


def resolve_event_slug(slug: str) -> tuple[Optional[Dict[str, Any]], Optional[str], bool]:
    """Return (event_dict, canonical_slug, is_alias)."""
    con = get_db()
    try:
        row = con.execute("SELECT * FROM events WHERE slug=?", (slug,)).fetchone()
        if row:
            d = dict(row)
            return d, str(d["slug"]), False
        try:
            a = con.execute("SELECT event_id FROM slug_aliases WHERE alias_slug=?", (slug,)).fetchone()
        except sqlite3.OperationalError:
            return None, None, False
        if not a:
            return None, None, False
        row2 = con.execute("SELECT * FROM events WHERE id=?", (int(a["event_id"]),)).fetchone()
        if not row2:
            return None, None, False
        d2 = dict(row2)
        return d2, str(d2["slug"]), True
    finally:
        con.close()


def slug_taken(slug: str) -> bool:
    """True if slug is used as canonical slug OR as alias."""
    con = get_db()
    try:
        if con.execute("SELECT 1 FROM events WHERE slug=? LIMIT 1", (slug,)).fetchone():
            return True
        try:
            if con.execute("SELECT 1 FROM slug_aliases WHERE alias_slug=? LIMIT 1", (slug,)).fetchone():
                return True
        except sqlite3.OperationalError:
            return False
        return False
    finally:
        con.close()


def add_slug_alias(event_id: int, alias_slug: str) -> None:
    alias_slug = (alias_slug or "").strip()
    if not alias_slug:
        return
    con = get_db()
    try:
        # Ignore conflicts silently (safety)
        con.execute(
            "INSERT OR IGNORE INTO slug_aliases (alias_slug, event_id) VALUES (?, ?)",
            (alias_slug, int(event_id)),
        )
        con.commit()
    finally:
        con.close()


def list_slug_aliases(event_id: int) -> list:
    """Die alten Adressen dieses Events, neueste zuerst."""
    con = get_db()
    try:
        rows = con.execute("SELECT alias_slug FROM slug_aliases WHERE event_id=? ORDER BY rowid DESC",
                           (int(event_id),)).fetchall()
        return [str(r["alias_slug"]) for r in rows]
    except sqlite3.OperationalError:
        return []
    finally:
        con.close()


def delete_slug_alias(event_id: int, alias_slug: str) -> bool:
    """Eine alte Adresse abschalten — der Link führt danach ins Leere."""
    con = get_db()
    try:
        cur = con.execute("DELETE FROM slug_aliases WHERE event_id=? AND alias_slug=?",
                          (int(event_id), (alias_slug or "").strip()))
        con.commit()
        return bool(cur.rowcount)
    except sqlite3.OperationalError:
        return False
    finally:
        con.close()


def get_event_by_upload_token(token: str) -> Optional[sqlite3.Row]:
    con = get_db()
    row = con.execute("SELECT * FROM events WHERE upload_token=?", (token,)).fetchone()
    con.close()
    return row


def list_events() -> List[Dict[str, Any]]:
    """Simple event list – used by notifier and other internal callers."""
    con = get_db()
    rows = con.execute("SELECT * FROM events ORDER BY created_at DESC").fetchall()
    con.close()
    return [dict(r) for r in rows]


def list_events_with_counts() -> List[Dict[str, Any]]:
    con = get_db()
    rows = con.execute(
        """
        SELECT
          e.*,
          (SELECT COUNT(*) FROM media m WHERE m.event_id=e.id AND m.kind='photo') AS photos_count,
          (SELECT COUNT(*) FROM media m WHERE m.event_id=e.id AND m.kind='print') AS prints_count,
          (SELECT COUNT(*) FROM media m WHERE m.event_id=e.id AND m.kind='gif')   AS gifs_count,
          (SELECT COUNT(*) FROM media m WHERE m.event_id=e.id AND m.kind='video') AS videos_count,
          (SELECT COALESCE(SUM(m.size_bytes),0) FROM media m WHERE m.event_id=e.id) AS total_bytes,

          (SELECT COUNT(*) FROM stats s WHERE s.event_id=e.id AND s.action='download') AS downloads_count,
          (SELECT COUNT(*) FROM stats s WHERE s.event_id=e.id AND s.action='zip_download') AS zip_downloads_count,
          (SELECT COUNT(*) FROM stats s WHERE s.event_id=e.id AND s.action='view_gallery') AS views_count,

          (SELECT COUNT(*) FROM stats s WHERE s.event_id=e.id AND s.action='share_whatsapp_link') AS whatsapp_share_count,
          (SELECT COUNT(*) FROM stats s WHERE s.event_id=e.id AND s.action='share_file') AS file_share_count,

          (SELECT COUNT(*) FROM stats s WHERE s.event_id=e.id AND s.action='link_share') AS link_share_count,
          (SELECT COUNT(*) FROM stats s WHERE s.event_id=e.id AND s.action='whatsapp') AS whatsapp_request_count,

          (SELECT COUNT(*) FROM likes l WHERE l.event_id=e.id) AS likes_count,
          (SELECT COUNT(*) FROM notify_subscriptions n WHERE n.event_id=e.id AND n.confirmed_at IS NOT NULL AND n.unsubscribed_at IS NULL) AS subscribers_count
        FROM events e
        ORDER BY datetime(e.last_upload_at) DESC, e.id DESC
        """
    ).fetchall()
    con.close()
    return [dict(r) for r in rows]


# -----------------------------
# Media
# -----------------------------

def add_media(
    *,
    event_id: int,
    kind: str,
    filename: str,
    path_original: str,
    path_webp: str | None,
    path_thumb: str | None,
    size_bytes: int,
    sha256: str,
    share_token: str,
) -> int:
    con = get_db()
    cur = con.cursor()
    cur.execute(
        """
        INSERT INTO media (event_id, kind, filename, path_original, path_webp, path_thumb, size_bytes, sha256, share_token)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            event_id,
            kind,
            filename,
            path_original,
            path_webp,
            path_thumb,
            size_bytes,
            sha256,
            share_token,
        ),
    )
    # Update last_upload_at
    cur.execute("UPDATE events SET last_upload_at=datetime('now') WHERE id=?", (event_id,))
    con.commit()
    media_id = cur.lastrowid
    con.close()
    return int(media_id)


def list_media(event_id: int, *, limit: Optional[int] = None) -> List[Dict[str, Any]]:
    con = get_db()
    try:
        hidden_cols = _table_cols(con.cursor(), "media")
    except Exception:
        hidden_cols = set()
    where_hidden = " AND COALESCE(hidden,0)=0" if "hidden" in hidden_cols else ""
    sql = f"SELECT * FROM media WHERE event_id=?{where_hidden} ORDER BY datetime(created_at) DESC, id DESC"
    if limit:
        sql += " LIMIT ?"
        rows = con.execute(sql, (event_id, int(limit))).fetchall()
    else:
        rows = con.execute(sql, (event_id,)).fetchall()
    con.close()
    return [dict(r) for r in rows]


def get_media(media_id: int) -> Optional[Dict[str, Any]]:
    con = get_db()
    row = con.execute("SELECT * FROM media WHERE id=?", (media_id,)).fetchone()
    con.close()
    return dict(row) if row else None


def get_media_by_share_token(token: str) -> Optional[Dict[str, Any]]:
    con = get_db()
    row = con.execute("SELECT * FROM media WHERE share_token=?", (token,)).fetchone()
    con.close()
    return dict(row) if row else None


def delete_media(media_id: int) -> Optional[Dict[str, Any]]:
    """Delete media row from DB and returns the deleted row (so caller can delete files)."""
    con = get_db()
    cur = con.cursor()
    row = cur.execute("SELECT * FROM media WHERE id=?", (media_id,)).fetchone()
    if not row:
        con.close()
        return None
    result = dict(row)
    cur.execute("DELETE FROM media WHERE id=?", (media_id,))
    con.commit()
    con.close()
    return result


# -----------------------------
# Stats / Analytics
# -----------------------------

def add_stat(event_id: int, action: str, media_id: int | None = None, meta: Optional[Dict[str, Any]] = None) -> None:
    meta_json = None
    if meta:
        try:
            meta_json = json.dumps(meta, ensure_ascii=False)
        except Exception:
            meta_json = None

    con = get_db()
    con.execute(
        "INSERT INTO stats (event_id, media_id, action, meta_json) VALUES (?, ?, ?, ?)",
        (event_id, media_id, action, meta_json),
    )
    con.commit()
    con.close()


def event_stats(event_id: int) -> Dict[str, int]:
    con = get_db()
    rows = con.execute(
        "SELECT action, COUNT(*) as c FROM stats WHERE event_id=? GROUP BY action",
        (event_id,),
    ).fetchall()
    con.close()
    return {str(r["action"]): int(r["c"]) for r in rows}


def action_src_breakdown(event_id: int, action: str) -> Dict[str, int]:
    """Counts stats for one action grouped by meta.src (best-effort; 'direct' fallback)."""
    con = get_db()
    rows = con.execute(
        "SELECT meta_json FROM stats WHERE event_id=? AND action=?",
        (event_id, action),
    ).fetchall()
    con.close()

    counts: Dict[str, int] = {}
    for r in rows:
        src = "direct"
        try:
            meta = json.loads(r["meta_json"] or "{}")
            if isinstance(meta, dict):
                src = str(meta.get("src") or "direct")
        except Exception:
            src = "direct"
        counts[src] = counts.get(src, 0) + 1
    return counts


def top_media_actions(event_id: int, action: str, limit: int = 10) -> List[Tuple[sqlite3.Row, int]]:
    """Returns [(media_row, count), ...] for most frequent media actions (download/view_photo/etc.)."""
    con = get_db()
    rows = con.execute(
        """
        SELECT media_id, COUNT(*) as c
        FROM stats
        WHERE event_id=? AND action=? AND media_id IS NOT NULL
        GROUP BY media_id
        ORDER BY c DESC
        LIMIT ?
        """,
        (event_id, action, int(limit)),
    ).fetchall()

    out: List[Tuple[sqlite3.Row, int]] = []
    for r in rows:
        m = con.execute("SELECT * FROM media WHERE id=?", (int(r["media_id"]),)).fetchone()
        if m:
            out.append((m, int(r["c"])))
    con.close()
    return out


def delete_event(event_id: int) -> None:
    con = get_db()
    con.execute("DELETE FROM media WHERE event_id=?", (event_id,))
    con.execute("DELETE FROM stats WHERE event_id=?", (event_id,))
    # Defensive deletes (in case foreign key pragma is disabled)
    try:
        con.execute("DELETE FROM likes WHERE event_id=?", (event_id,))
        con.execute("DELETE FROM notify_subscriptions WHERE event_id=?", (event_id,))
    except Exception:
        pass
    con.execute("DELETE FROM events WHERE id=?", (event_id,))
    con.commit()
    con.close()


# -----------------------------
# Counts / helpers
# -----------------------------

def count_media(event_id: int) -> int:
    con = get_db()
    row = con.execute("SELECT COUNT(*) as c FROM media WHERE event_id=?", (event_id,)).fetchone()
    con.close()
    return int(row["c"]) if row else 0


# -----------------------------
# Likes
# -----------------------------

def like_counts_for_event(event_id: int) -> Dict[int, int]:
    """Returns {media_id: like_count} for the given event."""
    con = get_db()
    rows = con.execute(
        """
        SELECT media_id, COUNT(*) as c
        FROM likes
        WHERE event_id=?
        GROUP BY media_id
        """,
        (event_id,),
    ).fetchall()
    con.close()
    return {int(r["media_id"]): int(r["c"]) for r in rows}


def like_count(media_id: int) -> int:
    con = get_db()
    row = con.execute("SELECT COUNT(*) as c FROM likes WHERE media_id=?", (media_id,)).fetchone()
    con.close()
    return int(row["c"]) if row else 0


def liked_media_ids_for_device(event_id: int, device_id: str) -> set:
    """media_ids, die dieses Gerät in diesem Event bereits geliked hat."""
    device_id = (device_id or "").strip()
    if not device_id:
        return set()
    con = get_db()
    rows = con.execute(
        "SELECT media_id FROM likes WHERE event_id=? AND device_id=?",
        (event_id, device_id),
    ).fetchall()
    con.close()
    return {int(r["media_id"]) for r in rows}


def toggle_like(event_id: int, media_id: int, device_id: str) -> Tuple[bool, int]:
    """Toggle like for (media_id, device_id). Returns (liked_now, like_count)."""
    device_id = (device_id or "").strip()
    if not device_id:
        return (False, like_count(media_id))

    con = get_db()
    cur = con.cursor()

    existing = cur.execute(
        "SELECT id FROM likes WHERE media_id=? AND device_id=?",
        (media_id, device_id),
    ).fetchone()

    liked_now = False
    if existing:
        cur.execute("DELETE FROM likes WHERE id=?", (int(existing["id"]),))
        liked_now = False
    else:
        try:
            cur.execute(
                "INSERT INTO likes (event_id, media_id, device_id) VALUES (?, ?, ?)",
                (event_id, media_id, device_id),
            )
            liked_now = True
        except sqlite3.IntegrityError:
            liked_now = False

    con.commit()

    row = cur.execute("SELECT COUNT(*) as c FROM likes WHERE media_id=?", (media_id,)).fetchone()
    c = int(row["c"]) if row else 0

    con.close()
    return liked_now, c


def top_liked_media(event_id: int, limit: int = 10) -> List[Tuple[sqlite3.Row, int]]:
    """Returns [(media_row, like_count), ...]"""
    con = get_db()
    rows = con.execute(
        """
        SELECT media_id, COUNT(*) as c
        FROM likes
        WHERE event_id=?
        GROUP BY media_id
        ORDER BY c DESC
        LIMIT ?
        """,
        (event_id, int(limit)),
    ).fetchall()

    out: List[Tuple[sqlite3.Row, int]] = []
    for r in rows:
        m = con.execute("SELECT * FROM media WHERE id=?", (int(r["media_id"]),)).fetchone()
        if m:
            out.append((m, int(r["c"])))
    con.close()
    return out


# -----------------------------
# Visitor notifications
# -----------------------------

def get_subscription_by_token(token: str) -> Optional[sqlite3.Row]:
    con = get_db()
    row = con.execute("SELECT * FROM notify_subscriptions WHERE token=?", (token,)).fetchone()
    con.close()
    return row


def upsert_subscription(event_id: int, email: str, token: str) -> int:
    """Create or refresh a subscription for (event_id,email). Returns subscription id."""
    email = (email or "").strip().lower()
    token = (token or "").strip()
    con = get_db()
    cur = con.cursor()

    existing = cur.execute(
        """
        SELECT id FROM notify_subscriptions
        WHERE event_id=? AND lower(email)=lower(?) AND (unsubscribed_at IS NULL)
        """,
        (event_id, email),
    ).fetchone()

    if existing:
        cur.execute(
            "UPDATE notify_subscriptions SET token=?, confirmed_at=NULL, notified_at=NULL, created_at=datetime('now') WHERE id=?",
            (token, int(existing["id"])),
        )
        sub_id = int(existing["id"])
    else:
        cur.execute(
            "INSERT INTO notify_subscriptions (event_id, email, token) VALUES (?, ?, ?)",
            (event_id, email, token),
        )
        sub_id = int(cur.lastrowid)

    con.commit()
    con.close()
    return sub_id


#: How long a double-opt-in link is good for. The confirmation mail says
#: "7 Tage" and the privacy page promises that an unconfirmed address is gone
#: after that; until 15.11.2026 neither was enforced and unconfirmed addresses
#: stayed until the event was deleted.
SUBSCRIPTION_CONFIRM_DAYS = 7


def purge_unconfirmed_subscriptions(days: int = SUBSCRIPTION_CONFIRM_DAYS) -> int:
    """Delete sign-ups nobody confirmed within ``days``. Returns how many."""
    con = get_db()
    try:
        cur = con.execute(
            "DELETE FROM notify_subscriptions WHERE confirmed_at IS NULL "
            "AND created_at < datetime('now', ?)", (f"-{int(days)} days",))
        con.commit()
        return int(cur.rowcount or 0)
    finally:
        con.close()


def confirm_subscription(token: str) -> Optional[sqlite3.Row]:
    con = get_db()
    cur = con.cursor()
    row = cur.execute(
        "SELECT * FROM notify_subscriptions WHERE token=? AND (confirmed_at IS NOT NULL "
        "OR created_at >= datetime('now', ?))",
        (token, f"-{SUBSCRIPTION_CONFIRM_DAYS} days")).fetchone()
    if not row:
        con.close()
        return None
    cur.execute(
        "UPDATE notify_subscriptions SET confirmed_at=datetime('now') WHERE token=?",
        (token,),
    )
    con.commit()
    con.close()
    return row


def unsubscribe(token: str) -> Optional[sqlite3.Row]:
    con = get_db()
    cur = con.cursor()
    row = cur.execute("SELECT * FROM notify_subscriptions WHERE token=?", (token,)).fetchone()
    if not row:
        con.close()
        return None
    cur.execute(
        "UPDATE notify_subscriptions SET unsubscribed_at=datetime('now') WHERE token=?",
        (token,),
    )
    con.commit()
    con.close()
    return row


def pending_confirmed_subscriptions(event_id: int) -> List[sqlite3.Row]:
    con = get_db()
    rows = con.execute(
        """
        SELECT * FROM notify_subscriptions
        WHERE event_id=?
          AND confirmed_at IS NOT NULL
          AND notified_at IS NULL
          AND (unsubscribed_at IS NULL)
        ORDER BY id ASC
        """,
        (event_id,),
    ).fetchall()
    con.close()
    return rows


def mark_notified(tokens: List[str]) -> None:
    if not tokens:
        return
    con = get_db()
    placeholders = ",".join(["?"] * len(tokens))
    con.execute(
        f"UPDATE notify_subscriptions SET notified_at=datetime('now') WHERE token IN ({placeholders})",
        tokens,
    )
    con.commit()
    con.close()


def confirmed_subscriber_count(event_id: int) -> int:
    con = get_db()
    row = con.execute(
        """
        SELECT COUNT(*) as c
        FROM notify_subscriptions
        WHERE event_id=? AND confirmed_at IS NOT NULL AND (unsubscribed_at IS NULL)
        """,
        (event_id,),
    ).fetchone()
    con.close()
    return int(row["c"]) if row else 0


# -----------------------------
# Devices (Release 2.2)
# -----------------------------

def list_devices() -> List[Dict[str, Any]]:
    con = get_db()
    rows = con.execute("SELECT * FROM devices ORDER BY id DESC").fetchall()
    con.close()
    return [dict(r) for r in rows]


def get_device_by_id(device_id: int) -> Optional[Dict[str, Any]]:
    con = get_db()
    row = con.execute("SELECT * FROM devices WHERE id=?", (int(device_id),)).fetchone()
    con.close()
    return dict(row) if row else None


_KEY_PLACEHOLDER = "hashed:"


def hash_device_key(api_key: str) -> str:
    """SHA-256 of a device key. A key has 192 random bits, so no slow hash is needed."""
    return hashlib.sha256(str(api_key or "").strip().encode("utf-8")).hexdigest()


def _key_placeholder() -> str:
    # The column api_key is NOT NULL UNIQUE (schema of release 2.2); this
    # fills it with something unique that is never accepted as a key.
    return _KEY_PLACEHOLDER + secrets.token_hex(8)


def get_device_by_api_key(api_key: str) -> Optional[Dict[str, Any]]:
    api_key = (api_key or "").strip()
    if not api_key or api_key.startswith(_KEY_PLACEHOLDER):
        return None
    wanted = hash_device_key(api_key)
    con = get_db()
    row = con.execute(
        "SELECT * FROM devices WHERE api_key_hash=? AND active=1",
        (wanted,),
    ).fetchone()
    con.close()
    # The indexed lookup finds the row; the comparison that decides is
    # constant-time, so the timing says nothing about how much matched.
    if not row or not hmac.compare_digest(str(row["api_key_hash"] or ""), wanted):
        return None
    return dict(row)


def _issue_device_key(con: sqlite3.Connection, device_id: int) -> str:
    """Store the hash of a fresh key for this device and return the key — the only time it exists."""
    new_key = secrets.token_hex(24)  # 48 hex chars
    con.execute("UPDATE devices SET api_key_hash=?, api_key=? WHERE id=?",
                (hash_device_key(new_key), _key_placeholder(), int(device_id)))
    return new_key


def create_device(
    *,
    name: str,
    notes: str | None = None,
    default_watch_path: str | None = None,
    default_event_glob: str | None = None,
) -> Dict[str, Any]:
    """Create a device. The returned dict carries the plain key — the only copy there is."""
    name = (name or "").strip()
    if not name:
        raise ValueError("device name required")

    con = get_db()
    cur = con.cursor()
    cur.execute(
        """
        INSERT INTO devices (name, notes, api_key, active, default_watch_path, default_event_glob)
        VALUES (?, ?, ?, 1, ?, ?)
        """,
        (
            name,
            (notes or "").strip() or None,
            _key_placeholder(),
            (default_watch_path or "").strip() or None,
            (default_event_glob or "").strip() or None,
        ),
    )
    api_key = _issue_device_key(con, int(cur.lastrowid))
    con.commit()
    row = con.execute("SELECT * FROM devices WHERE id=?", (int(cur.lastrowid),)).fetchone()
    con.close()
    assert row is not None
    return {**dict(row), "api_key": api_key}


def update_device(device_id: int, fields: Dict[str, Any]) -> None:
    allowed = {"name", "notes", "default_watch_path", "default_event_glob",
               "landing_event_id", "landing_headline", "landing_subtext", "landing_bg_path"}
    pairs = [(k, v) for k, v in fields.items() if k in allowed]
    if not pairs:
        return
    sets = ", ".join([f"{k}=?" for k, _ in pairs])
    values = [v for _, v in pairs] + [int(device_id)]
    con = get_db()
    con.execute(f"UPDATE devices SET {sets} WHERE id=?", values)
    con.commit()
    con.close()


def set_device_active(device_id: int, active: bool) -> None:
    con = get_db()
    con.execute("UPDATE devices SET active=? WHERE id=?", (1 if active else 0, int(device_id)))
    con.commit()
    con.close()


def regenerate_device_key(device_id: int) -> Dict[str, Any]:
    """Replace the key; the old one is dead at once. The dict carries the new plain key, once."""
    con = get_db()
    new_key = _issue_device_key(con, int(device_id))
    con.commit()
    row = con.execute("SELECT * FROM devices WHERE id=?", (int(device_id),)).fetchone()
    con.close()
    assert row is not None
    return {**dict(row), "api_key": new_key}


def regenerate_all_device_keys() -> int:
    """Every booth is cut off until it is paired again (after a password change, a lost laptop)."""
    con = get_db()
    ids = [int(r[0]) for r in con.execute("SELECT id FROM devices").fetchall()]
    for did in ids:
        _issue_device_key(con, did)
    con.commit()
    con.close()
    return len(ids)


def touch_device_seen(device_id: int, ip: str | None = None) -> None:
    con = get_db()
    con.execute(
        "UPDATE devices SET last_seen_at=datetime('now'), last_ip=? WHERE id=?",
        ((ip or "").strip() or None, int(device_id)),
    )
    con.commit()
    con.close()


# -----------------------------
# Booth-Heartbeat
# -----------------------------

HEARTBEAT_KEEP = 500          # Verlauf je Gerät, damit die DB nicht wächst


def record_heartbeat(device_id: int, status: Dict[str, Any], health: str,
                     ip: str | None = None) -> None:
    """Aktuellen Zustand am Gerät ablegen und im Verlauf festhalten."""
    payload = json.dumps(status, ensure_ascii=False)
    con = get_db()
    cur = con.cursor()
    cur.execute(
        """UPDATE devices
              SET last_heartbeat_at=datetime('now'), last_seen_at=datetime('now'),
                  status_json=?, last_health=?, last_ip=COALESCE(?, last_ip)
            WHERE id=?""",
        (payload, health, (ip or "").strip() or None, int(device_id)),
    )
    cur.execute(
        """INSERT INTO device_heartbeats
             (device_id, health, disk_free_gb, queue_pending, queue_failed,
              booth_running, payload_json)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (int(device_id), health,
         status.get("disk_free_gb"), status.get("queue_pending"),
         status.get("queue_failed"),
         1 if status.get("booth_running") else 0,
         payload),
    )
    # Verlauf kappen
    cur.execute(
        """DELETE FROM device_heartbeats
            WHERE device_id=? AND id NOT IN (
              SELECT id FROM device_heartbeats WHERE device_id=?
              ORDER BY id DESC LIMIT ?)""",
        (int(device_id), int(device_id), HEARTBEAT_KEEP),
    )
    con.commit()
    con.close()
    # The silence alarm (app/silence_alarm.py) learns here that the box is back.
    try:
        from app import silence_alarm
        silence_alarm.note_heartbeat(int(device_id))
    except Exception:                       # noqa: BLE001 — a heartbeat is never lost to the alarm
        import logging
        logging.getLogger(__name__).exception("silence alarm: heartbeat not noted")


def get_device_status(device_id: int) -> Dict[str, Any]:
    """Zuletzt gemeldeter Zustand eines Geräts (leeres Dict, wenn keiner)."""
    con = get_db()
    row = con.execute("SELECT status_json FROM devices WHERE id=?", (int(device_id),)).fetchone()
    con.close()
    if not row or not row["status_json"]:
        return {}
    try:
        data = json.loads(row["status_json"])
        return data if isinstance(data, dict) else {}
    except (ValueError, TypeError):
        return {}


def list_heartbeats(device_id: int, limit: int = 60) -> List[Dict[str, Any]]:
    con = get_db()
    rows = con.execute(
        """SELECT created_at, health, disk_free_gb, queue_pending, queue_failed, booth_running
             FROM device_heartbeats WHERE device_id=?
            ORDER BY id DESC LIMIT ?""",
        (int(device_id), int(limit)),
    ).fetchall()
    con.close()
    return [dict(r) for r in rows]



# -----------------------------
# First-run / Setup helpers
# -----------------------------

def is_first_run() -> bool:
    """Returns True if the setup wizard has not been completed yet."""
    return get_setting("setup_completed") is None


def mark_setup_completed() -> None:
    set_setting("setup_completed", "1")


# -----------------------------
# Media thumb helper
# -----------------------------

def update_media_thumb(media_id: int, thumb_path: str) -> None:
    """Update the path_thumb column for a media row."""
    con = get_db()
    con.execute(
        "UPDATE media SET path_thumb=? WHERE id=?",
        (thumb_path, int(media_id)),
    )
    con.commit()
    con.close()


# ===== Analytics & Stats Functions (Release 2.3) =====

def log_stat(event_id: int, action: str, media_id: int = None, meta: dict = None) -> None:
    """Log an action/event for analytics (view, download, share, etc.)."""
    import json
    con = get_db()
    con.execute(
        "INSERT INTO stats (event_id, action, media_id, meta_json) VALUES (?,?,?,?)",
        (event_id, action, media_id, json.dumps(meta or {}))
    )
    con.commit()
    con.close()


def get_event_stats(event_id: int) -> dict:
    """Get summary stats for an event: {action: count, ...}"""
    con = get_db()
    rows = con.execute(
        "SELECT action, COUNT(*) as cnt FROM stats WHERE event_id=? GROUP BY action",
        (event_id,)
    ).fetchall()
    con.close()
    return {r["action"]: r["cnt"] for r in rows}


def get_analytics_overview() -> list:
    """Get analytics overview for all events."""
    con = get_db()
    # Media and stats are counted separately: joined in one go, every stats
    # row was multiplied by the number of photos in the event.
    result = con.execute("""
        SELECT e.id, e.name, e.slug, e.status,
               COALESCE(m.media_count, 0) as media_count,
               COALESCE(s.views, 0) as views,
               COALESCE(s.downloads, 0) as downloads,
               COALESCE(s.zip_downloads, 0) as zip_downloads,
               COALESCE(s.whatsapp_shares, 0) as whatsapp_shares
        FROM events e
        LEFT JOIN (SELECT event_id, COUNT(*) as media_count FROM media GROUP BY event_id) m
               ON m.event_id = e.id
        LEFT JOIN (SELECT event_id,
                          SUM(CASE WHEN action='view_gallery' THEN 1 ELSE 0 END) as views,
                          SUM(CASE WHEN action='download' THEN 1 ELSE 0 END) as downloads,
                          SUM(CASE WHEN action='zip_download' THEN 1 ELSE 0 END) as zip_downloads,
                          SUM(CASE WHEN action='share_whatsapp' THEN 1 ELSE 0 END) as whatsapp_shares
                   FROM stats GROUP BY event_id) s
               ON s.event_id = e.id
        ORDER BY e.created_at DESC
    """).fetchall()
    con.close()
    return result


# ===== Email Subscription Functions (Release 2.3) =====

def add_subscription(event_id: int, email: str, token: str) -> Optional[int]:
    """Add a new email subscription for an event. Returns subscription ID."""
    con = get_db()
    try:
        con.execute(
            "INSERT INTO notify_subscriptions (event_id, email, token) VALUES (?,?,?)",
            (event_id, email, token)
        )
        con.commit()
    except sqlite3.IntegrityError:
        con.close()
        return None
    
    row = con.execute("SELECT id FROM notify_subscriptions WHERE token=?", (token,)).fetchone()
    con.close()
    return row["id"] if row else None


def confirm_subscription_by_token(token: str) -> bool:
    """Confirm an email subscription by token."""
    con = get_db()
    con.execute(
        "UPDATE notify_subscriptions SET confirmed_at=datetime('now') WHERE token=? AND confirmed_at IS NULL "
        "AND created_at >= datetime('now', ?)",
        (token, f"-{SUBSCRIPTION_CONFIRM_DAYS} days")
    )
    con.commit()
    row = con.execute("SELECT id FROM notify_subscriptions WHERE token=? AND confirmed_at IS NOT NULL", (token,)).fetchone()
    con.close()
    return row is not None


def get_confirmed_subscribers(event_id: int) -> list:
    """Get all confirmed subscribers for an event."""
    con = get_db()
    rows = con.execute(
        "SELECT * FROM notify_subscriptions WHERE event_id=? AND confirmed_at IS NOT NULL AND unsubscribed_at IS NULL ORDER BY id ASC",
        (event_id,)
    ).fetchall()
    con.close()
    return rows or []


def delete_device(device_id: int) -> None:
    """Delete a device and all associated data."""
    con = get_db()
    con.execute("DELETE FROM devices WHERE id=?", (int(device_id),))
    con.commit()
    con.close()


# ─────────────────────────────────────────────────────────────────────────
# FAQ (Frequently Asked Questions)
# ─────────────────────────────────────────────────────────────────────────

def get_faq_items() -> list:
    """Return parsed FAQ items list, or empty list."""
    import json
    raw = get_setting("faq_items_json") or "[]"
    try:
        items = json.loads(raw)
        if isinstance(items, list):
            return items
    except Exception:
        pass
    return []


def save_faq_items(items: list) -> None:
    """Save FAQ items list as JSON setting."""
    import json
    set_setting("faq_items_json", json.dumps(items, ensure_ascii=False))


# ─────────────────────────────────────────────────────────────────────────
# Feature 1: Per-Device Landing Pages (Release 2.4)
# ─────────────────────────────────────────────────────────────────────────

def get_device_landing_event(device_id: int) -> Optional[Dict[str, Any]]:
    """Get the event currently assigned to a device's landing page."""
    con = get_db()
    try:
        row = con.execute(
            """SELECT e.* FROM events e
               JOIN devices d ON d.landing_event_id = e.id
               WHERE d.id = ? AND e.published = 1""",
            (int(device_id),)
        ).fetchone()
        return dict(row) if row else None
    finally:
        con.close()


# ─────────────────────────────────────────────────────────────────────────
# Feature 2: Featured Galleries (Release 2.4)
# ─────────────────────────────────────────────────────────────────────────

def set_event_featured(event_id: int, featured: bool) -> None:
    """Set featured status for an event."""
    con = get_db()
    try:
        con.execute("UPDATE events SET featured=? WHERE id=?", (1 if featured else 0, int(event_id)))
        con.commit()
    finally:
        con.close()
