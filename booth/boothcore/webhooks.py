"""Ausgehende Webhooks: die Box sagt einer fremden Adresse Bescheid.

Wofür: eine Anzeige im Nebenraum, ein Zähler auf der Eventseite, ein
Zapier/n8n-Ablauf, der jedes Bild weiterschickt. Die Box ruft, niemand ruft die
Box an — so bleibt sie hinter jedem Hotel-WLAN erreichbar.

Jede Zustellung ist ein POST mit JSON. Ist ein ``secret`` gesetzt, steht im
Header ``X-Photobox-Signature`` der HMAC-SHA256 über dem Rumpf, damit die
Gegenstelle prüfen kann, dass der Aufruf wirklich von dieser Box kommt.

Zustellung läuft im Hintergrund und darf nie einen Durchgang aufhalten: Fehler
werden zwei Mal wiederholt und dann protokolliert, mehr nicht.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import queue
import threading
import time
from typing import Any, Callable, Dict, List

import httpx

log = logging.getLogger("booth.webhooks")

EVENTS = ("session_started", "shot_taken", "media_ready", "printed", "mail_queued", "error")
MAX_QUEUE = 200
ATTEMPTS = 3
TIMEOUT = 8.0


def sign(secret: str, body: bytes) -> str:
    return hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


class Webhooks:
    """Schickt Ereignisse an die im Profil hinterlegte Adresse."""

    def __init__(self, config: Callable[[], Dict[str, Any]], start_worker: bool = True,
                 client: httpx.Client | None = None):
        self.config = config
        self._queue: "queue.Queue[Dict[str, Any]]" = queue.Queue(maxsize=MAX_QUEUE)
        self._client = client
        self._stop = threading.Event()
        self.sent = 0
        self.failed = 0
        self.last_error: str | None = None
        self.delivered: List[Dict[str, Any]] = []      # nur für Tests/Diagnose, gedeckelt
        if start_worker:
            self.start()

    # ── Konfiguration ─────────────────────────────────────
    def _cfg(self) -> Dict[str, Any]:
        try:
            return dict(self.config() or {})
        except Exception:
            return {}

    def wants(self, event: str) -> bool:
        cfg = self._cfg()
        if not cfg.get("enabled") or not str(cfg.get("url") or "").strip():
            return False
        chosen = cfg.get("events") or list(EVENTS)
        return event in chosen

    # ── Einreihen ─────────────────────────────────────────
    def emit(self, event: str, payload: Dict[str, Any]) -> bool:
        """Ereignis melden. Gibt zurück, ob es in die Warteschlange ging."""
        if event not in EVENTS or not self.wants(event):
            return False
        item = {"event": event, "at": time.time(), "data": payload}
        try:
            self._queue.put_nowait(item)
        except queue.Full:
            self.failed += 1
            self.last_error = "Warteschlange voll"
            return False
        return True

    # ── Zustellung ────────────────────────────────────────
    def deliver(self, item: Dict[str, Any]) -> bool:
        cfg = self._cfg()
        url = str(cfg.get("url") or "").strip()
        if not url:
            return False
        body = json.dumps({"event": item["event"], "at": item["at"], "booth": cfg.get("booth_name", ""),
                           "data": item["data"]}, ensure_ascii=False).encode()
        headers = {"Content-Type": "application/json", "User-Agent": "photobox-booth"}
        secret = str(cfg.get("secret") or "")
        if secret:
            headers["X-Photobox-Signature"] = sign(secret, body)
        client = self._client or httpx.Client(timeout=TIMEOUT)
        try:
            for attempt in range(1, ATTEMPTS + 1):
                try:
                    r = client.post(url, content=body, headers=headers)
                    if r.status_code < 400:
                        self.sent += 1
                        self.delivered = (self.delivered + [item])[-50:]
                        return True
                    if r.status_code < 500:                 # falsche Adresse: nicht wiederholen
                        self.last_error = f"{r.status_code} {r.text[:120]}"
                        break
                    self.last_error = f"{r.status_code}"
                except httpx.HTTPError as e:
                    self.last_error = str(e)
                if attempt < ATTEMPTS:
                    self._stop.wait(2 * attempt)
        finally:
            if self._client is None:
                client.close()
        self.failed += 1
        log.info("Webhook %s nicht zugestellt: %s", item["event"], self.last_error)
        return False

    def status(self) -> Dict[str, Any]:
        cfg = self._cfg()
        return {"enabled": bool(cfg.get("enabled") and cfg.get("url")), "sent": self.sent,
                "failed": self.failed, "pending": self._queue.qsize(), "last_error": self.last_error}

    # ── Hintergrund ───────────────────────────────────────
    def start(self) -> None:
        threading.Thread(target=self._loop, name="webhooks", daemon=True).start()

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                item = self._queue.get(timeout=1.0)
            except queue.Empty:
                continue
            try:
                self.deliver(item)
            except Exception as e:                       # nie den Thread verlieren
                log.warning("Webhook-Fehler: %s", e)

    def drain(self) -> int:
        """Alles jetzt zustellen (Tests, „jetzt senden" in der Verwaltung)."""
        count = 0
        while True:
            try:
                item = self._queue.get_nowait()
            except queue.Empty:
                return count
            count += 1 if self.deliver(item) else 0

    def stop(self) -> None:
        self._stop.set()
