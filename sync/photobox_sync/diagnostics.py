"""Warnings and errors of the sync program, on their way to the gallery.

The point of this file: when something goes wrong on an event PC nobody can
reach, the operator should see it in the admin interface instead of having to
drive there and open a log file. Every WARNING and ERROR is collected here and
travels with the next heartbeat.

Repeated messages are folded into one entry with a count — a target that is
offline produces one line per retry, and without folding a single unplugged NAS
would push everything else out of the buffer.

``boothcore.diagnostics`` does the same job for the booth; it stays separate
because that one also feeds the booth's own diagnostics screen.
"""
from __future__ import annotations

import logging
import threading
import time
import traceback
from collections import deque
from typing import Any, Deque, Dict, List, Optional

from . import redact

CAPACITY = 300
MAX_MESSAGE = 500
DEDUPE_SECONDS = 60


class LogBuffer(logging.Handler):
    """Collects records at ``level`` and above, ready for the sync payload."""

    def __init__(self, level: int = logging.WARNING, capacity: int = CAPACITY):
        super().__init__(level)
        self.capacity = capacity
        self._items: Deque[Dict[str, Any]] = deque(maxlen=capacity)
        self._recent: Dict[str, Dict[str, Any]] = {}
        self._lock = threading.Lock()
        self.dropped = 0

    def emit(self, record: logging.LogRecord) -> None:   # pragma: no cover - via logging
        try:
            message = record.getMessage()
        except Exception:
            message = str(record.msg)
        detail = ""
        if record.exc_info:
            detail = "".join(traceback.format_exception(*record.exc_info))[-MAX_MESSAGE:]
        self.add(record.levelname.lower(), message, source=record.name, detail=detail, when=record.created)

    def add(self, level: str, message: str, *, source: str = "sync", detail: str = "",
            when: Optional[float] = None) -> None:
        # No guest address and no secret leaves this program (R2-U3).
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
                self._recent.clear()

    def take(self, limit: int = 50) -> List[Dict[str, Any]]:
        """Up to ``limit`` entries, removed from the buffer — the agent re-queues them if the sync fails."""
        out: List[Dict[str, Any]] = []
        with self._lock:
            while self._items and len(out) < limit:
                out.append(self._as_payload(self._items.popleft()))
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
        """The shape the gallery stores in ``device_logs``: level, code, message, context."""
        message = item["message"]
        if item["count"] > 1:
            message = f"{message} ({item['count']}×)"
        context = {"source": item["source"]}
        if item["detail"]:
            context["detail"] = item["detail"]
        return {"level": item["level"], "code": item["source"], "message": message,
                "context": context, "occurred_at": item["at"]}
