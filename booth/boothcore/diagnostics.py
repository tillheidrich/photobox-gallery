"""Diagnostics: keep the interesting log lines where they can be sent away.

Every warning and error the booth produces goes into a ring buffer. The sync
agent drains it on its next contact with the gallery, where the operator — and
whoever develops this — sees it in the admin interface without walking to the
booth. Nothing leaves the machine when no gallery is configured; the rotating
file in ``<data>/logs`` stays the source of truth either way.
"""
from __future__ import annotations

import logging
import threading
import time
import traceback
from collections import deque
from typing import Any, Deque, Dict, List, Optional

from . import redact

# Messages kept in memory. Older ones are dropped, newest win.
CAPACITY = 500

# Same message again and again (a camera that stays unplugged) is counted, not repeated.
DEDUPE_SECONDS = 120

MAX_MESSAGE = 2000


class LogBuffer(logging.Handler):
    """Collects records at ``level`` and above, ready for the sync payload."""

    def __init__(self, level: int = logging.WARNING, capacity: int = CAPACITY):
        super().__init__(level)
        self.capacity = capacity
        self._items: Deque[Dict[str, Any]] = deque(maxlen=capacity)
        self._recent: Dict[str, Dict[str, Any]] = {}
        self._lock = threading.Lock()
        self.dropped = 0

    # ── collecting ────────────────────────────────────────
    def emit(self, record: logging.LogRecord) -> None:   # pragma: no cover - via logging
        try:
            message = record.getMessage()
        except Exception:
            message = str(record.msg)
        detail = ""
        if record.exc_info:
            detail = "".join(traceback.format_exception(*record.exc_info))[-MAX_MESSAGE:]
        self.add(record.levelname.lower(), message, source=record.name, detail=detail,
                 when=record.created)

    def add(self, level: str, message: str, *, source: str = "booth", detail: str = "",
            when: Optional[float] = None) -> None:
        # The buffer leaves the booth (sync, remote ``logs``): no guest address
        # and no secret, whatever a call site wrote (final review A4).
        message = redact.text(message)[:MAX_MESSAGE]
        detail = redact.text(detail) if detail else ""
        stamp = when or time.time()
        key = f"{level}|{source}|{message}"
        with self._lock:
            recent = self._recent.get(key)
            if recent and stamp - recent["at"] < DEDUPE_SECONDS:
                recent["count"] += 1
                recent["last_at"] = stamp
                return
            if len(self._items) == self._items.maxlen:
                self.dropped += 1
            item = {"at": stamp, "last_at": stamp, "level": level, "source": source,
                    "message": message, "detail": detail, "count": 1}
            self._items.append(item)
            self._recent[key] = item
            if len(self._recent) > self.capacity * 2:
                self._recent = {k: v for k, v in self._recent.items()
                                if stamp - v["last_at"] < DEDUPE_SECONDS}

    # ── draining ──────────────────────────────────────────
    def take(self, limit: int = 50) -> List[Dict[str, Any]]:
        """Return up to ``limit`` entries and remove them from the buffer."""
        out: List[Dict[str, Any]] = []
        with self._lock:
            while self._items and len(out) < limit:
                item = self._items.popleft()
                out.append(self._as_payload(item))
            if not self._items:
                self._recent.clear()
        return out

    def peek(self, limit: int = 50) -> List[Dict[str, Any]]:
        with self._lock:
            items = list(self._items)[-limit:]
        return [self._as_payload(i) for i in items]

    def counts(self) -> Dict[str, int]:
        with self._lock:
            out = {"pending": len(self._items), "dropped": self.dropped}
            for item in self._items:
                out[item["level"]] = out.get(item["level"], 0) + item["count"]
        return out

    @staticmethod
    def _as_payload(item: Dict[str, Any]) -> Dict[str, Any]:
        """Shape the gallery stores (``device_logs``): level, code, message, context."""
        import datetime as dt

        context: Dict[str, Any] = {}
        if item["count"] > 1:
            context["count"] = item["count"]
        if item["detail"]:
            context["detail"] = item["detail"]
        if item["last_at"] != item["at"]:
            context["last_at"] = dt.datetime.fromtimestamp(item["last_at"]).isoformat(timespec="seconds")
        return {
            "occurred_at": dt.datetime.fromtimestamp(item["at"]).isoformat(timespec="seconds"),
            "level": item["level"], "code": item["source"], "message": item["message"],
            "context": context or None,
        }
