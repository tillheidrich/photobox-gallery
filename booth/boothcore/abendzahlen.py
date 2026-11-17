"""Numbers of an evening: what happened at the booth, per event and day.

Until 12.11.2026 the booth counted runs since the program started and prints
in a file; a restart in the middle of a wedding lost the evening's numbers,
cancelled runs were not counted at all, and nobody could say when the rush
was. Now each event and day has its own counters in the booth's library
(an extra table, nothing in it is changed), and the heartbeat carries the
sums of the running day and event to the gallery.

Only numbers. No names, no file names, no run ids, no time of a single run —
the hour of a run goes into one of 24 buckets and nothing else. That is why
the counters keep running in the data-minimal mode: there is nothing in them
that could lead back to a guest.
"""
from __future__ import annotations

import sqlite3
import threading
import time
from pathlib import Path
from typing import Dict, Optional

MODI = ("photo", "collage", "gif", "boomerang", "video")

#: Every counter there is. A key not on this list is refused, so a typo in a
#: call cannot open a new column of data nobody agreed on.
SCHLUESSEL = (
    *(f"runs.{m}" for m in MODI),
    "aborted.cancel", "aborted.timeout", "retakes",
    "prints", "print_errors", "prints_backup",
    "consent.gallery", "consent.print", "consent.notice",
    "camera.errors", "camera.reconnects", "qr_big",
    "flash.dark",                     # a picture with the flash came out near black (15.11.2026)
    *(f"hour.{h:02d}" for h in range(24)),
)
ERLAUBT = frozenset(SCHLUESSEL)

SCHEMA = """
CREATE TABLE IF NOT EXISTS counters (
    event_slug TEXT NOT NULL,
    day        TEXT NOT NULL,
    key        TEXT NOT NULL,
    n          INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (event_slug, day, key)
);
"""


class Abendzahlen:
    def __init__(self, path: Path):
        self.path = Path(path)
        self._lock = threading.Lock()
        with self._con() as con:
            con.executescript(SCHEMA)

    def _con(self):
        con = sqlite3.connect(self.path, timeout=10)
        con.row_factory = sqlite3.Row
        return con

    @staticmethod
    def heute(jetzt: Optional[float] = None) -> str:
        return time.strftime("%Y-%m-%d", time.localtime(jetzt if jetzt is not None else time.time()))

    def zaehle(self, event_slug: str, key: str, n: int = 1, jetzt: Optional[float] = None) -> None:
        """Add ``n`` to one counter of today. Never raises: a counter costs no run."""
        if key not in ERLAUBT or not n:
            return
        try:
            with self._lock, self._con() as con:
                con.execute("""INSERT INTO counters(event_slug, day, key, n) VALUES (?,?,?,?)
                               ON CONFLICT(event_slug, day, key) DO UPDATE SET n = n + excluded.n""",
                            (str(event_slug or ""), self.heute(jetzt), key, int(n)))
        except sqlite3.Error:
            pass

    def durchgang(self, event_slug: str, mode: str, jetzt: Optional[float] = None) -> None:
        """A finished run: its mode and the hour it fell in (for the rush hour)."""
        jetzt = time.time() if jetzt is None else jetzt
        if mode in MODI:
            self.zaehle(event_slug, f"runs.{mode}", 1, jetzt)
        self.zaehle(event_slug, f"hour.{time.localtime(jetzt).tm_hour:02d}", 1, jetzt)

    def vergessen(self, event_slug: str, day: str) -> None:
        """Drop one event's numbers of one day — the test runs before the event."""
        with self._lock, self._con() as con:
            con.execute("DELETE FROM counters WHERE event_slug=? AND day=?", (str(event_slug or ""), day))

    def tag(self, event_slug: str, day: Optional[str] = None) -> Dict[str, int]:
        with self._con() as con:
            rows = con.execute("SELECT key, n FROM counters WHERE event_slug=? AND day=? AND n<>0",
                               (str(event_slug or ""), day or self.heute())).fetchall()
        return {r["key"]: int(r["n"]) for r in rows}

    def event(self, event_slug: str) -> Dict[str, int]:
        with self._con() as con:
            rows = con.execute("""SELECT key, SUM(n) AS n FROM counters WHERE event_slug=?
                                  GROUP BY key HAVING SUM(n)<>0""", (str(event_slug or ""),)).fetchall()
        return {r["key"]: int(r["n"]) for r in rows}

    def bericht(self, event_slug: str) -> Dict[str, object]:
        """What the heartbeat carries: today's and the event's sums, non-zero keys only."""
        return {"event_slug": str(event_slug or ""), "day": self.heute(),
                "today": self.tag(event_slug), "event_total": self.event(event_slug)}

