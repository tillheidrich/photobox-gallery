"""E-mail delivery for guests — queued locally, sent whenever there is a network.

The booth frequently has no internet for hours. A guest who types an address must
never wait for an SMTP handshake and must never see an error because the venue
Wi-Fi is down. Therefore:

* :meth:`MailSender.enqueue` only validates the address and writes one row to a
  local SQLite queue. It returns immediately.
* A worker thread picks up due rows and retries with a growing backoff
  (10 s, 30 s, 2 min, 10 min, 30 min, then hourly) until the mail is out or
  twelve attempts have been spent — then the row is marked ``failed`` and can be
  revived with :meth:`MailSender.retry_failed`.
* The configuration is read fresh on *every* attempt, so a new SMTP host coming
  down from the remote profile applies without restarting the booth.

Attachments: the files handed to :meth:`enqueue` are attached as long as their
combined size stays below :data:`MAX_ATTACHMENT_BYTES` (20 MB). Above that limit
nothing is attached at all — the mail then carries only the ``body``, which is
expected to contain the share link, because most mail servers reject big
messages outright and a bounced mail helps nobody. The same happens when the
configuration sets ``attach`` to ``"link"``.
"""
from __future__ import annotations

import json
import logging
import mimetypes
import re
import smtplib
import sqlite3
import ssl
import threading
import time
from email.message import EmailMessage
from email.utils import formataddr, formatdate, make_msgid
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from .. import redact

log = logging.getLogger(__name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS mail_queue (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    address TEXT NOT NULL,
    files TEXT NOT NULL DEFAULT '[]',       -- JSON list of absolute paths
    subject TEXT NOT NULL DEFAULT '',
    body TEXT NOT NULL DEFAULT '',
    session_uid TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'pending', -- pending | sent | failed
    attempts INTEGER NOT NULL DEFAULT 0,
    next_try REAL NOT NULL DEFAULT 0,
    last_error TEXT,
    created_at REAL NOT NULL,
    sent_at REAL
);
CREATE INDEX IF NOT EXISTS mail_queue_due ON mail_queue(status, next_try);
"""

#: Delay before attempt n+1 after n failures. Everything beyond the list is hourly.
BACKOFF_SECONDS: List[int] = [10, 30, 120, 600, 1800]
HOURLY_BACKOFF = 3600

#: After this many failed attempts a row is given up on and marked ``failed``.
MAX_ATTEMPTS = 12

#: A guest's address is only needed until the mail is out. After this the
#: row keeps its status (the admin page counts sent mails) but forgets the
#: address and the text. Given-up rows go entirely after FAILED_KEEP_SECONDS:
#: two weeks is enough to notice the error and press "retry".
SENT_KEEP_SECONDS = 24 * 3600
FAILED_KEEP_SECONDS = 14 * 24 * 3600

#: Combined attachment budget. Above it the mail goes out with the link only.
MAX_ATTACHMENT_BYTES = 20 * 1024 * 1024

#: SMTP conversations are cut off after this many seconds — a hanging hotspot
#: must never block the worker thread for minutes.
SMTP_TIMEOUT = 20

#: Configuration flag that turns every send into a recorded no-op.
DRY_RUN_KEY = "dry_run"

# Conservative on purpose: we would rather tell a guest "please check the
# address" than queue something that can only bounce. No quoted local parts, no
# bare IP domains, no trailing dots.
ADDRESS_RE = re.compile(
    r"^[A-Za-z0-9!#$%&'*+/=?^_`{|}~-]+"
    r"(?:\.[A-Za-z0-9!#$%&'*+/=?^_`{|}~-]+)*"
    r"@"
    r"(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)+"
    r"[A-Za-z]{2,24}$"
)

MAX_ADDRESS_LENGTH = 254

_EXTRA_TYPES = {
    ".jpg": ("image", "jpeg"),
    ".jpeg": ("image", "jpeg"),
    ".png": ("image", "png"),
    ".gif": ("image", "gif"),
    ".mp4": ("video", "mp4"),
    ".webm": ("video", "webm"),
    ".webp": ("image", "webp"),
}


def normalise_address(address: str) -> str:
    """Trim and lower-case the domain part; the local part is left untouched."""
    cleaned = (address or "").strip().strip("<>").strip()
    if "@" in cleaned:
        local, _, domain = cleaned.rpartition("@")
        cleaned = f"{local}@{domain.lower()}"
    return cleaned


def valid_address(address: str) -> bool:
    """True when the address looks plausible enough to be worth queueing."""
    if not address or len(address) > MAX_ADDRESS_LENGTH:
        return False
    if ".." in address:
        return False
    return bool(ADDRESS_RE.match(address))


def backoff_for(attempts: int) -> int:
    """Seconds to wait after ``attempts`` failures (``attempts`` counts from 1)."""
    if attempts <= 0:
        return 0
    index = attempts - 1
    if index < len(BACKOFF_SECONDS):
        return BACKOFF_SECONDS[index]
    return HOURLY_BACKOFF


def guess_mime(path: Path) -> tuple[str, str]:
    suffix = path.suffix.lower()
    if suffix in _EXTRA_TYPES:
        return _EXTRA_TYPES[suffix]
    guessed, _ = mimetypes.guess_type(path.name)
    if guessed and "/" in guessed:
        main, _, sub = guessed.partition("/")
        return main, sub
    return "application", "octet-stream"


def attachment_names(files: List[Path], *, file_naming: str, event_name: str,
                     when) -> Optional[List[str]]:
    """The names the attachments carry under the event's file-name rule.

    ``original`` (or no rule): None, the files keep their own names as they
    always did. ``event_datetime``: the same name a USB stick or FTP server
    gets (``photobox_sync.targets.event_datetime_name``, the booth's copy of
    the gallery's rule), and since every file of one mail comes from one run
    and so from one second, the second and later attachments get ``_2``,
    ``_3`` in the order they are attached.
    """
    if str(file_naming or "") != "event_datetime" or when is None:
        return None
    from photobox_sync.targets import event_datetime_name
    return [event_datetime_name(event_name, when, Path(f).suffix.lstrip("."), i)
            for i, f in enumerate(files, 1)]


class MailSender:
    """Sends one photo to one guest address over SMTP, with an offline queue.

    The booth is often offline. :meth:`enqueue` never blocks and never fails the
    guest; a worker thread retries with backoff until the mail is out.

    ``config`` is a callable returning a fresh dictionary on every call::

        {"enabled": True, "host": "smtp.example.de", "port": 587,
         "user": "booth@example.de", "password": "…",
         "security": "starttls",            # starttls | ssl | none
         "from_addr": "booth@example.de", "from_name": "Fotobox",
         "subject": "Your photo", "body": "Have fun!",
         "attach": "web",                   # web | original | link
         "bcc": "", "dry_run": False}

    ``attach`` is a hint for the caller which file variant to hand to
    :meth:`enqueue`; the only value interpreted here is ``"link"``, which sends
    the mail without any attachment.
    """

    def __init__(self, db_path: Path, config: Callable[[], dict], start_worker: bool = True,
                 clock: Optional[Callable[[], float]] = None):
        self.db_path = Path(db_path)
        self._config = config
        self._clock = clock
        self._lock = threading.Lock()
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._last_error: Optional[str] = None
        #: Weg über die Galerie; gesetzt von der Box, wenn sie eine kennt.
        self.relay: Optional[Callable[[Dict[str, Any]], None]] = None
        #: Records of mails "sent" while ``dry_run`` was on — for tests and for
        #: the admin's "send test e-mail" button.
        self.sent_messages: List[Dict[str, Any]] = []
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._con() as con:
            con.executescript(SCHEMA)
            # Additive: the attachment names (event file-name rule) came on
            # 15.11.2026; a queue from before keeps its rows, they have none.
            cols = {r[1] for r in con.execute("PRAGMA table_info(mail_queue)")}
            if "names" not in cols:
                con.execute("ALTER TABLE mail_queue ADD COLUMN names TEXT NOT NULL DEFAULT '[]'")
        if start_worker:
            self.start()

    # -- plumbing ---------------------------------------------------------

    def _now(self) -> float:
        # Not cached: a test may either inject ``clock`` or patch ``time.time``.
        return self._clock() if self._clock is not None else time.time()

    def _con(self) -> sqlite3.Connection:
        con = sqlite3.connect(self.db_path, timeout=10, check_same_thread=False)
        con.row_factory = sqlite3.Row
        return con

    def _cfg(self) -> Dict[str, Any]:
        try:
            cfg = self._config() or {}
        except Exception as exc:                      # a broken profile must not kill the worker
            log.warning("mail configuration unreadable: %s", exc)
            return {}
        return dict(cfg)

    # -- queue ------------------------------------------------------------

    def enqueue(self, *, address: str, files: List[Path], subject: str, body: str,
                session_uid: str = "", names: Optional[List[str]] = None) -> int:
        """Queue one mail and return its row id.

        Raises :class:`ValueError` for an address that cannot possibly work, so
        the UI can ask the guest to check the spelling right away. Everything
        else — no network, wrong password, server down — is handled by retries.

        ``names``: the name each file carries as an attachment, in the order of
        ``files`` (see :func:`attachment_names`); None keeps the files' own.
        """
        cleaned = normalise_address(address)
        if not valid_address(cleaned):
            raise ValueError(f"not a usable e-mail address: {address!r}")
        paths = [str(Path(f)) for f in (files or [])]
        given = [str(n) for n in (names or [])]
        if given and len(given) != len(paths):
            raise ValueError("one attachment name per file")
        now = self._now()
        with self._lock, self._con() as con:
            cur = con.execute(
                """INSERT INTO mail_queue(address, files, subject, body, session_uid, status,
                                          attempts, next_try, created_at, names)
                   VALUES (?,?,?,?,?,'pending',0,?,?,?)""",
                (cleaned, json.dumps(paths), subject or "", body or "", session_uid or "", now, now,
                 json.dumps(given, ensure_ascii=False)),
            )
            item_id = int(cur.lastrowid)
        self._wake.set()
        return item_id

    def get(self, item_id: int) -> Optional[Dict[str, Any]]:
        with self._con() as con:
            row = con.execute("SELECT * FROM mail_queue WHERE id=?", (item_id,)).fetchone()
        return dict(row) if row else None

    def due(self, limit: int = 10) -> List[Dict[str, Any]]:
        with self._con() as con:
            rows = con.execute(
                "SELECT * FROM mail_queue WHERE status='pending' AND next_try<=? ORDER BY id LIMIT ?",
                (self._now(), limit),
            ).fetchall()
        return [dict(r) for r in rows]

    def status(self) -> Dict[str, Any]:
        """Counts for the admin page, plus the most recent error."""
        counts = {"pending": 0, "sent": 0, "failed": 0}
        with self._con() as con:
            for row in con.execute("SELECT status, COUNT(*) AS n FROM mail_queue GROUP BY status"):
                if row["status"] in counts:
                    counts[row["status"]] = int(row["n"])
            last_error = self._last_error
            if last_error is None:
                row = con.execute(
                    """SELECT last_error FROM mail_queue
                       WHERE last_error IS NOT NULL AND last_error <> ''
                       ORDER BY id DESC LIMIT 1"""
                ).fetchone()
                last_error = row["last_error"] if row else None
        return {**counts, "last_error": last_error}

    def forget_old(self) -> int:
        """Drop guests' addresses the queue no longer needs. Returns rows touched."""
        now = self._now()
        with self._lock, self._con() as con:
            cur = con.execute(
                "UPDATE mail_queue SET address='', body='', files='[]', names='[]' "
                "WHERE status='sent' AND address<>'' AND COALESCE(sent_at, created_at) < ?",
                (now - SENT_KEEP_SECONDS,))
            n = int(cur.rowcount or 0)
            cur = con.execute("DELETE FROM mail_queue WHERE status='failed' AND created_at < ?",
                              (now - FAILED_KEEP_SECONDS,))
            n += int(cur.rowcount or 0)
        return n

    def retry_failed(self) -> int:
        """Put every given-up mail back into the queue. Returns how many."""
        with self._lock, self._con() as con:
            cur = con.execute(
                "UPDATE mail_queue SET status='pending', attempts=0, next_try=?, last_error=NULL "
                "WHERE status='failed'",
                (self._now(),),
            )
            changed = int(cur.rowcount or 0)
        if changed:
            self._last_error = None
            self._wake.set()
        return changed

    # -- sending ----------------------------------------------------------

    def send_now(self, item_id: int) -> bool:
        """Try one row immediately, ignoring its backoff. True when it went out.

        Used by the tests and by the manual "send now" button in the admin UI.
        """
        item = self.get(item_id)
        if item is None or item["status"] == "sent":
            return item is not None and item["status"] == "sent"
        return self._attempt(item)

    def set_relay(self, relay: Optional[Callable[[Dict[str, Any]], None]]) -> None:
        """Den Weg über die Galerie eintragen. Ohne ihn bleibt es bei SMTP."""
        self.relay = relay

    def _attempt(self, item: Dict[str, Any]) -> bool:
        cfg = self._cfg()
        dry_run = bool(cfg.get(DRY_RUN_KEY))
        if not dry_run and not cfg.get("enabled", False):
            self._defer(int(item["id"]), "e-mail delivery is switched off")
            return False
        ueber_galerie = str(cfg.get("transport") or "gallery").lower() == "gallery"
        try:
            if not dry_run and ueber_galerie and self.relay is not None:
                # Die Galerie verschickt. Sie hat das Bild ohnehin, deshalb gehen
                # nur Adresse, Text und die Kennung des Durchgangs raus — kein
                # Postfach auf der Box, kein offener SMTP-Port am Veranstaltungsort.
                #
                # Genau deshalb darf hier keine Nachricht gebaut werden: ``_build``
                # verlangt eine Absenderadresse, und die gibt es auf diesem Weg
                # nicht. Bis zur Durchsicht am 19.09.2026 wurde sie trotzdem
                # gebaut — jede Gästemail scheiterte still an
                # „no sender address configured", obwohl der Weg über die Galerie
                # der eingestellte Standard ist und ausdrücklich ohne Postfach
                # auskommen soll.
                # Ohne Relay bleibt es beim alten Weg über SMTP (siehe unten) —
                # eine Box ohne Galerie soll weiter ihr eigenes Postfach nutzen.
                self.relay({
                    "to": item["address"],
                    "subject": self._betreff(item, cfg),
                    "body": item["body"] or cfg.get("body", ""),
                    "session_uid": item["session_uid"],
                    "attach": str(cfg.get("attach") or "web"),
                })
            else:
                message, attached, skipped = self._build(item, cfg)
                if dry_run:
                    self.sent_messages.append({
                        "id": int(item["id"]),
                        "to": item["address"],
                        "subject": message["Subject"],
                        "from": message["From"],
                        "body": item["body"] or cfg.get("body", ""),
                        "attachments": attached,
                        "attachments_skipped": skipped,
                        "session_uid": item["session_uid"],
                    })
                else:
                    self._send_smtp(cfg, message)
        except Exception as exc:                       # noqa: BLE001 — any failure is a retry
            self._fail(item, f"{type(exc).__name__}: {exc}")
            return False
        self._mark_sent(int(item["id"]))
        return True

    @staticmethod
    def _betreff(item: Dict[str, Any], cfg: Dict[str, Any]) -> str:
        """Der Betreff — ohne dass dafür eine ganze Nachricht entstehen muss."""
        return item["subject"] or cfg.get("subject") or "Your photo"

    def _build(self, item: Dict[str, Any], cfg: Dict[str, Any]) -> tuple[EmailMessage, List[str], bool]:
        """Compose the message. Returns it with the attached names and a skip flag."""
        from_addr = cfg.get("from_addr") or cfg.get("user") or ""
        if not from_addr:
            raise ValueError("no sender address configured")
        message = EmailMessage()
        message["From"] = formataddr((cfg.get("from_name") or "", from_addr))
        message["To"] = item["address"]
        message["Subject"] = self._betreff(item, cfg)
        message["Date"] = formatdate(localtime=True)
        # With no domain make_msgid() calls socket.getfqdn() — a reverse name
        # lookup that blocks for minutes on a booth without internet.
        message["Message-ID"] = make_msgid(domain=from_addr.rpartition("@")[2].strip() or "photobox.invalid")
        bcc = (cfg.get("bcc") or "").strip()
        if bcc:
            # smtplib.send_message adds Bcc to the envelope and strips the header.
            message["Bcc"] = bcc
        message.set_content(item["body"] or cfg.get("body") or "")

        wanted = [Path(p) for p in json.loads(item["files"] or "[]")]
        try:
            names = json.loads(item.get("names") or "[]")
        except ValueError:
            names = []
        named = dict(zip(map(str, wanted), names)) if len(names) == len(wanted) else {}
        existing = [p for p in wanted if p.is_file()]
        total = sum(p.stat().st_size for p in existing)
        link_only = str(cfg.get("attach") or "web").lower() == "link"
        if link_only or total > MAX_ATTACHMENT_BYTES:
            # Too big for most mail servers — the body carries the share link.
            return message, [], bool(existing)

        attached: List[str] = []
        for path in existing:
            main, sub = guess_mime(path)
            # email.policy.default writes a name that is not plain ASCII as
            # RFC 2231 ``filename*=utf-8''…``; the event rule's names are ASCII.
            name = named.get(str(path)) or path.name
            message.add_attachment(path.read_bytes(), maintype=main, subtype=sub, filename=name)
            attached.append(name)
        return message, attached, False

    def _send_smtp(self, cfg: Dict[str, Any], message: EmailMessage) -> None:
        host = (cfg.get("host") or "").strip()
        if not host:
            raise ValueError("no SMTP host configured")
        port = int(cfg.get("port") or 587)
        security = str(cfg.get("security") or "starttls").lower()
        user = cfg.get("user") or ""
        password = cfg.get("password") or ""
        log.info("sending mail via %s:%s (%s)", host, port, security)   # never the password
        # Name ourselves: left alone smtplib asks socket.getfqdn(), the same
        # blocking reverse lookup as in _build().
        sender = cfg.get("from_addr") or cfg.get("user") or ""
        helo = sender.rpartition("@")[2].strip() or "localhost"
        if security == "ssl":
            client = smtplib.SMTP_SSL(host, port, local_hostname=helo, timeout=SMTP_TIMEOUT,
                                      context=ssl.create_default_context())
        else:
            client = smtplib.SMTP(host, port, local_hostname=helo, timeout=SMTP_TIMEOUT)
        try:
            client.ehlo()
            if security == "starttls":
                client.starttls(context=ssl.create_default_context())
                client.ehlo()
            if user:
                client.login(user, password)
            client.send_message(message)
        finally:
            try:
                client.quit()
            except Exception:                          # noqa: BLE001 — closing is best effort
                pass

    # -- bookkeeping ------------------------------------------------------

    def _mark_sent(self, item_id: int) -> None:
        now = self._now()
        with self._lock, self._con() as con:
            con.execute("UPDATE mail_queue SET status='sent', sent_at=?, last_error=NULL WHERE id=?",
                        (now, item_id))
        self._last_error = None

    def _fail(self, item: Dict[str, Any], error: str) -> None:
        attempts = int(item["attempts"]) + 1
        now = self._now()
        if attempts >= MAX_ATTEMPTS:
            status, next_try = "failed", now
        else:
            status, next_try = "pending", now + backoff_for(attempts)
        with self._lock, self._con() as con:
            con.execute(
                "UPDATE mail_queue SET status=?, attempts=?, next_try=?, last_error=? WHERE id=?",
                (status, attempts, next_try, error[:500], int(item["id"])),
            )
        self._last_error = error[:500]
        # Never the full address: this line goes into the log buffer and from
        # there to the gallery (final review A4); the SMTP answer may quote it.
        log.warning("mail to %s failed (attempt %s/%s): %s",
                    redact.address(item["address"]), attempts, MAX_ATTEMPTS, redact.text(error))

    def _defer(self, item_id: int, reason: str) -> None:
        """Push a row back without spending an attempt (configuration, not failure)."""
        with self._lock, self._con() as con:
            con.execute("UPDATE mail_queue SET next_try=?, last_error=? WHERE id=?",
                        (self._now() + BACKOFF_SECONDS[1], reason, item_id))
        self._last_error = reason

    # -- worker -----------------------------------------------------------

    def start(self) -> None:
        """Start the retry thread. Idempotent."""
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="mail-sender", daemon=True)
        self._thread.start()

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                wait = self._run_due()
            except Exception as exc:                   # noqa: BLE001 — the thread must survive
                log.warning("mail worker cycle failed: %s", exc)
                wait = 30.0
            self._wake.wait(wait)
            self._wake.clear()

    def _run_due(self) -> float:
        """Work off everything that is due. Returns how long to sleep."""
        try:
            self.forget_old()
        except sqlite3.Error as exc:                   # the queue must keep sending
            log.warning("mail queue cleanup failed: %s", exc)
        for item in self.due():
            if self._stop.is_set():
                break
            self._attempt(item)
        with self._con() as con:
            row = con.execute(
                "SELECT MIN(next_try) AS soonest FROM mail_queue WHERE status='pending'"
            ).fetchone()
        soonest = row["soonest"] if row else None
        if soonest is None:
            return 60.0
        return max(1.0, min(60.0, float(soonest) - self._now()))

    def stop(self) -> None:
        """Stop the retry thread. Queued mails stay queued for the next start."""
        self._stop.set()
        self._wake.set()
        thread, self._thread = self._thread, None
        if thread is not None and thread.is_alive():
            thread.join(timeout=5)
