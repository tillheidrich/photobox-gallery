"""A brake on the booth PIN.

The PIN may be four digits and the factory value is printed in every manual.
A 0.8 s pause per wrong guess still allows all ten thousand in a little over
two hours. So: five wrong guesses, then a pause of a minute; every further
round of five doubles the pause, up to a quarter of an hour. The right PIN
ends it. With the pauses, trying all ten thousand four-digit PINs takes weeks.

The count survives a restart (``path``): four digits are only acceptable when
pulling the plug or crashing the program does not give a guesser a fresh set
of attempts. The file holds counts and a wall-clock time, never a PIN.
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from pathlib import Path
from typing import Callable, Optional

log = logging.getLogger(__name__)


class PinBrake:
    def __init__(self, limit: int = 5, first_pause: float = 60.0, max_pause: float = 900.0,
                 clock: Callable[[], float] = time.monotonic, path: Optional[Path] = None,
                 wall: Callable[[], float] = time.time):
        self.limit = limit
        self.first_pause = first_pause
        self.max_pause = max_pause
        self.clock = clock
        self.wall = wall
        self.path = Path(path) if path else None
        self._lock = threading.Lock()
        self._failures = 0          # in the current round
        self._rounds = 0            # pauses served since the last correct PIN
        self._until = 0.0
        self._load()

    def _load(self) -> None:
        if not self.path:
            return
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            self._failures = max(0, min(self.limit, int(raw.get("failures", 0))))
            self._rounds = max(0, min(64, int(raw.get("rounds", 0))))
            # A pause is stored as wall time (the monotonic clock restarts with
            # the PC). A clock set back cannot stretch it past the longest pause.
            left = min(float(raw.get("until_wall", 0)) - self.wall(), self.max_pause)
            if left > 0:
                self._until = self.clock() + left
        except FileNotFoundError:
            return
        except (OSError, ValueError, TypeError, AttributeError) as e:
            log.warning("PIN-Bremse: gespeicherter Stand nicht lesbar (%s), es gilt ein neuer", e)

    def _save(self) -> None:
        if not self.path:
            return
        left = self._until - self.clock()
        data = {"failures": self._failures, "rounds": self._rounds,
                "until_wall": self.wall() + left if left > 0 else 0}
        try:
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(data), encoding="utf-8")
            os.replace(tmp, self.path)
        except OSError as e:                         # the brake in memory still works
            log.warning("PIN-Bremse: Stand nicht gespeichert (%s)", e)

    def wait_seconds(self) -> int:
        """Seconds until the next attempt is allowed; 0 if it is allowed now."""
        with self._lock:
            left = self._until - self.clock()
        return int(left + 0.999) if left > 0 else 0

    def failed(self) -> None:
        with self._lock:
            self._failures += 1
            if self._failures >= self.limit:
                pause = min(self.first_pause * (2 ** self._rounds), self.max_pause)
                self._until = self.clock() + pause
                self._rounds += 1
                self._failures = 0
            self._save()

    def succeeded(self) -> None:
        with self._lock:
            changed = self._failures or self._rounds or self._until
            self._failures = 0
            self._rounds = 0
            self._until = 0.0
            if changed:
                self._save()
