"""Die Druckwarteschlange, die einen Neustart überlebt.

Der Grund für diese Datei ist ein Abend, den man nur einmal erlebt: Das
Farbband ist leer, zehn Karten stehen aus, der Betreiber wechselt die
Kassette — und in der Zwischenzeit startet die Box neu (Windows-Update,
Stromstecker, Absturz). Lag die Warteschlange nur im Arbeitsspeicher, sind
die zehn Drucke weg, und zehn Gäste warten auf etwas, das nie kommt.

Deshalb liegt jeder Auftrag in SQLite, bevor er an den Drucker geht:

    queued    wartet auf einen freien Drucker
    printing  ist gerade dran (nach einem Neustart wieder ``queued``,
              weil niemand weiß, ob das Blatt durchlief)
    held      mehrfach gescheitert — wartet auf den Betreiber (Papier,
              Farbband, Kabel). Wird NICHT verworfen.
    done      gedruckt
    lost      vom Betreiber verworfen

„held" statt „weg" ist die eigentliche Entscheidung: Ein Druckfehler ist
fast immer behebbar, und was behebbar ist, darf nicht verschwinden.
"""
from __future__ import annotations

import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

SCHEMA = """
CREATE TABLE IF NOT EXISTS spool (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    path TEXT NOT NULL,
    copies INTEGER NOT NULL DEFAULT 1,
    session_uid TEXT NOT NULL DEFAULT '',
    orientation TEXT NOT NULL DEFAULT '',
    state TEXT NOT NULL DEFAULT 'queued',
    attempts INTEGER NOT NULL DEFAULT 0,
    last_error TEXT,
    station INTEGER,
    created REAL NOT NULL,
    changed REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS spool_state ON spool(state, id);
"""

#: Columns added later (additive: a spool file from an older booth keeps
#: its rows). ``deferred``: accepted while the printer had no paper
#: (profile ``print.queue_while_empty``). ``quota_returned``: the guest's
#: allowance was given back after the job was held; a later print books it
#: again exactly once (``take_quota_back``).
EXTRA_COLUMNS = {"deferred": "INTEGER NOT NULL DEFAULT 0",
                 "quota_returned": "INTEGER NOT NULL DEFAULT 0"}

OFFEN = ("queued", "printing", "held")


class Spool:
    """Der Zettelkasten vor dem Drucker. Thread-sicher, klein, ohne Zauber."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        with self._con() as con:
            con.executescript(SCHEMA)
            spalten = {r["name"] for r in con.execute("PRAGMA table_info(spool)")}
            for name, art in EXTRA_COLUMNS.items():
                if name not in spalten:
                    con.execute(f"ALTER TABLE spool ADD COLUMN {name} {art}")
            # Was beim Ausschalten „printing" war, ist jetzt unbekannt. Lieber
            # einmal zu viel drucken als einen Gast ohne Bild nach Hause
            # schicken — der Betreiber sieht den Auftrag und kann ihn verwerfen.
            con.execute("UPDATE spool SET state='queued', changed=? WHERE state='printing'", (time.time(),))

    def _con(self) -> sqlite3.Connection:
        con = sqlite3.connect(self.path, timeout=10, check_same_thread=False)
        con.row_factory = sqlite3.Row
        return con

    # ── Schreiben ─────────────────────────────────────────
    def add(self, *, path: str, copies: int, session_uid: str = "", orientation: str = "",
            deferred: bool = False) -> int:
        now = time.time()
        with self._lock, self._con() as con:
            cur = con.execute(
                """INSERT INTO spool(path, copies, session_uid, orientation, state, created, changed, deferred)
                   VALUES (?,?,?,?, 'queued', ?, ?, ?)""",
                (str(path), int(copies), session_uid or "", orientation or "", now, now, 1 if deferred else 0))
            return int(cur.lastrowid)

    # ── The guest's allowance ─────────────────────────────
    def mark_quota_returned(self, spool_id: int) -> bool:
        """True exactly once per job: the allowance is to be given back now.

        A job without a spool row (tests) has no marker: always True, as
        before the marker existed.
        """
        if not spool_id:
            return True
        with self._lock, self._con() as con:
            cur = con.execute("UPDATE spool SET quota_returned=1 WHERE id=? AND quota_returned=0", (int(spool_id),))
            return cur.rowcount == 1

    def take_quota_back(self, spool_id: int) -> bool:
        """True exactly once after ``mark_quota_returned``: the held job printed
        after all, so the sheet is booked again."""
        if not spool_id:
            return False
        with self._lock, self._con() as con:
            cur = con.execute("UPDATE spool SET quota_returned=0 WHERE id=? AND quota_returned=1", (int(spool_id),))
            return cur.rowcount == 1

    def waiting(self) -> int:
        """Jobs that are not out yet (queued, printing, held)."""
        with self._con() as con:
            return int(con.execute("SELECT COUNT(*) FROM spool WHERE state IN ('queued','printing','held')")
                       .fetchone()[0])

    def deferred_open(self) -> int:
        with self._con() as con:
            return int(con.execute("SELECT COUNT(*) FROM spool WHERE deferred=1 AND state IN "
                                   "('queued','printing','held')").fetchone()[0])

    def drop_waiting(self, *, older_than: float = 0.0, deferred_only: bool = False) -> List[Dict[str, Any]]:
        """Throw away what is not out yet — all, or the deferred ones older
        than ``older_than`` (a timestamp). Returns the rows thrown away.

        "printing" is included: a printer thread marks a job "printing"
        before it finds the printer still without paper and puts the job
        back. A discard in that moment used to miss the job, which then came
        out after the refill. Now it is "lost", and ``requeue``/``hold``
        leave a lost job alone. A sheet really on the printer may still come
        out; ``done`` then records it and the allowance is booked again.
        """
        sql = "SELECT * FROM spool WHERE state IN ('queued','printing','held')"
        args: list = []
        if deferred_only:
            sql += " AND deferred=1"
        if older_than:
            sql += " AND created < ?"
            args.append(float(older_than))
        with self._lock, self._con() as con:
            rows = [dict(r) for r in con.execute(sql, args).fetchall()]
            for r in rows:
                con.execute("UPDATE spool SET state='lost', changed=? WHERE id=? "
                            "AND state IN ('queued','printing','held')",
                            (time.time(), int(r["id"])))
        return rows

    def drop_one(self, spool_id: int) -> Optional[Dict[str, Any]]:
        """The operator throws away one job that is not out yet.

        Same states and the same conditional UPDATE as ``drop_waiting``, so a
        job that printed meanwhile stays "done". Returns the row as it was,
        or None when there was nothing open to throw away.
        """
        with self._lock, self._con() as con:
            row = con.execute("SELECT * FROM spool WHERE id=? AND state IN ('queued','printing','held')",
                              (int(spool_id),)).fetchone()
            if row is None:
                return None
            cur = con.execute("UPDATE spool SET state='lost', changed=? WHERE id=? "
                              "AND state IN ('queued','printing','held')", (time.time(), int(spool_id)))
            return dict(row) if cur.rowcount == 1 else None

    def _set(self, spool_id: int, **felder: Any) -> None:
        if not spool_id:
            return
        felder["changed"] = time.time()
        sql = ", ".join(f"{k}=?" for k in felder)
        with self._lock, self._con() as con:
            con.execute(f"UPDATE spool SET {sql} WHERE id=?", (*felder.values(), int(spool_id)))

    #: States a job may be in while a printer thread works on it. Every
    #: change a printer thread makes is conditional on these, so a job
    #: thrown away meanwhile ("lost") is never brought back.
    _IN_HAND = "('queued','printing')"

    def _move(self, spool_id: int, sql_set: str, args: tuple) -> bool:
        if not spool_id:
            return True                       # no spool row (tests): nothing to guard
        with self._lock, self._con() as con:
            cur = con.execute(f"UPDATE spool SET {sql_set}, changed=? WHERE id=? AND state IN {self._IN_HAND}",
                              (*args, time.time(), int(spool_id)))
            return cur.rowcount == 1

    def started(self, spool_id: int, station: int) -> bool:
        """A printer takes the job. False: it was thrown away (or is gone) — skip it."""
        return self._move(spool_id, "state='printing', station=?", (int(station),))

    def done(self, spool_id: int) -> None:
        self._set(spool_id, state="done", last_error=None)

    def requeue(self, spool_id: int, fehler: str = "") -> bool:
        """Wieder in die Reihe — ein Drucker war belegt oder hat gemuckt.

        False when the job was thrown away meanwhile: it stays "lost"."""
        return self._move(spool_id, "state='queued', attempts=attempts+1, last_error=?",
                          (str(fehler)[:300] or None,))

    def hold(self, spool_id: int, fehler: str = "") -> bool:
        """Wartet auf den Betreiber: Papier, Farbband, Kabel.

        False when the job was thrown away meanwhile: it stays "lost"."""
        return self._move(spool_id, "state='held', attempts=attempts+1, last_error=?",
                          (str(fehler)[:300] or None,))

    def release(self) -> int:
        """Alles Wartende zurück in die Reihe — „Kassette gewechselt, weiter"."""
        with self._lock, self._con() as con:
            cur = con.execute("UPDATE spool SET state='queued', attempts=0, changed=? WHERE state='held'",
                              (time.time(),))
            return int(cur.rowcount or 0)

    def drop(self, spool_id: int) -> None:
        self._set(spool_id, state="lost")

    def zustand(self, spool_id: int) -> str:
        """Der Zustand eines Auftrags — leer, wenn es ihn nicht mehr gibt.

        Gebraucht kurz vor dem Druck: Zwischen dem Einreihen und dem Moment,
        in dem der Drucker frei wird, können Minuten liegen, und in dieser
        Zeit kann der Gast seinen Durchgang gelöscht haben.
        """
        with self._con() as con:
            zeile = con.execute("SELECT state FROM spool WHERE id=?", (int(spool_id),)).fetchone()
        return str(zeile["state"]) if zeile else ""

    def drop_session(self, session_uid: str) -> List[Dict[str, Any]]:
        """Gelöschter Durchgang: Was noch nicht gedruckt ist, geht auch nicht mehr raus.

        Same states as ``drop_waiting``, "printing" included: a printer thread
        that took the job and then finds the printer not ready used to put it
        back, and it came out after the run was deleted. Now it is "lost" and
        ``requeue``/``hold`` leave it alone. Returns the rows thrown away.
        """
        with self._lock, self._con() as con:
            rows = [dict(r) for r in con.execute(
                "SELECT * FROM spool WHERE session_uid=? AND state IN ('queued','printing','held')",
                (str(session_uid),)).fetchall()]
            for r in rows:
                con.execute("UPDATE spool SET state='lost', changed=? WHERE id=? "
                            "AND state IN ('queued','printing','held')", (time.time(), int(r["id"])))
        return rows

    # ── Lesen ─────────────────────────────────────────────
    def open_jobs(self) -> List[Dict[str, Any]]:
        """Was nach einem Start wieder eingereiht werden muss."""
        with self._con() as con:
            rows = con.execute("SELECT * FROM spool WHERE state IN ('queued','held') ORDER BY id").fetchall()
        return [dict(r) for r in rows]

    def counts(self) -> Dict[str, int]:
        zaehler = {"queued": 0, "printing": 0, "held": 0, "done": 0, "lost": 0}
        with self._con() as con:
            for row in con.execute("SELECT state, COUNT(*) AS n FROM spool GROUP BY state"):
                zaehler[row["state"]] = int(row["n"])
        return zaehler

    def recent(self, limit: int = 30) -> List[Dict[str, Any]]:
        with self._con() as con:
            rows = con.execute("SELECT * FROM spool ORDER BY id DESC LIMIT ?", (int(limit),)).fetchall()
        return [dict(r) for r in rows]

    def open_list(self, limit: int = 50) -> List[Dict[str, Any]]:
        """What is not out yet, oldest first: the operator's view of the queue."""
        with self._con() as con:
            rows = con.execute("SELECT * FROM spool WHERE state IN ('queued','printing','held') ORDER BY id LIMIT ?",
                               (max(1, int(limit)),)).fetchall()
        return [dict(r) for r in rows]

    def held(self) -> List[Dict[str, Any]]:
        with self._con() as con:
            rows = con.execute("SELECT * FROM spool WHERE state='held' ORDER BY id").fetchall()
        return [dict(r) for r in rows]

    def cleanup(self, tage: int = 14) -> int:
        """Alte erledigte Zettel wegwerfen. Offene nie."""
        grenze = time.time() - tage * 86400
        with self._lock, self._con() as con:
            cur = con.execute("DELETE FROM spool WHERE state IN ('done','lost') AND changed < ?", (grenze,))
            return int(cur.rowcount or 0)
