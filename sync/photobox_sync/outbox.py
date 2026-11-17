"""Outbox: jede Datei landet zuerst lokal in einer Warteschlange.

Hochgeladen wird, sobald Netz da ist — auch wenn die Box am Tag nur zweimal
kurz einen Hotspot sieht. Deshalb:

* Priorität: Web-Fassungen und Poster zuerst (klein, Gäste warten darauf),
  Originale danach.
* Backoff pro Eintrag, aber sobald irgendein Upload wieder klappt, wird der
  Backoff aller Einträge zurückgesetzt — das kurze Netzfenster wird genutzt.
* Idempotent: Der Server erkennt ``uid`` + ``variant`` und meldet Doppeltes als
  erledigt. Ein abgebrochener Upload wird einfach wiederholt.
"""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

SCHEMA = """
CREATE TABLE IF NOT EXISTS outbox (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    target TEXT NOT NULL,               -- gallery | <name eines Zusatzziels>
    uid TEXT NOT NULL,                  -- Medien-Kennung (= Share-Kennung des Durchgangs + Suffix)
    session_uid TEXT NOT NULL,
    event_slug TEXT NOT NULL DEFAULT '',
    kind TEXT NOT NULL,                 -- photo | collage | boomerang | original
    variant TEXT NOT NULL,              -- web | original
    path TEXT NOT NULL,
    poster TEXT,
    meta TEXT NOT NULL DEFAULT '{}',
    bytes INTEGER NOT NULL DEFAULT 0,   -- Größe beim Einreihen: Grundlage für Restmenge und Restzeit
    priority INTEGER NOT NULL DEFAULT 5, -- kleiner = früher
    status TEXT NOT NULL DEFAULT 'pending',   -- pending | done | failed
    attempts INTEGER NOT NULL DEFAULT 0,
    next_try REAL NOT NULL DEFAULT 0,
    last_error TEXT,
    day TEXT NOT NULL DEFAULT '',            -- local date of the attempts counted in day_attempts
    day_attempts INTEGER NOT NULL DEFAULT 0, -- permanent failures on that day; caps the automatic retry
    created REAL NOT NULL,
    done REAL,
    UNIQUE(target, uid, variant)
);
CREATE INDEX IF NOT EXISTS outbox_pending ON outbox(status, next_try, priority);
"""

MAX_BACKOFF = 600

#: After this many permanent failures a file goes to "failed" (one round).
MAX_ATTEMPTS = 8
#: Permanent failures per file and day after which the automatic retry
#: leaves it alone until the next day: three rounds, then rest. Only the
#: automatic retry looks at it; the remote job retry_failed does not.
MAX_ATTEMPTS_PER_DAY = 24


class Outbox:
    def __init__(self, path: Path):
        self.path = path
        self._lock = threading.Lock()
        self.wake = threading.Event()
        #: Clock for the daily attempt count (tests move it).
        self.clock = time.time
        with self._con() as con:
            con.executescript(SCHEMA)
            cols = {r[1] for r in con.execute("PRAGMA table_info(outbox)")}
            if "bytes" not in cols:                    # ältere Warteschlange: Spalte ergänzen, nichts wegwerfen
                con.execute("ALTER TABLE outbox ADD COLUMN bytes INTEGER NOT NULL DEFAULT 0")
            if "day" not in cols:                      # additive, like bytes: old queues keep every row
                con.execute("ALTER TABLE outbox ADD COLUMN day TEXT NOT NULL DEFAULT ''")
            if "day_attempts" not in cols:
                con.execute("ALTER TABLE outbox ADD COLUMN day_attempts INTEGER NOT NULL DEFAULT 0")

    def _today(self) -> str:
        return time.strftime("%Y-%m-%d", time.localtime(self.clock()))

    def _con(self) -> sqlite3.Connection:
        con = sqlite3.connect(self.path, timeout=10)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA journal_mode=WAL")
        return con

    def add(self, *, target: str, uid: str, session_uid: str, event_slug: str, kind: str, variant: str,
            path: Path, poster: Optional[Path] = None, meta: Optional[Dict[str, Any]] = None,
            priority: int = 5) -> None:
        try:
            size = Path(path).stat().st_size
        except OSError:
            size = 0
        with self._lock, self._con() as con:
            con.execute(
                """INSERT INTO outbox(target, uid, session_uid, event_slug, kind, variant, path, poster, meta,
                                      priority, created, bytes)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(target, uid, variant) DO UPDATE SET
                     path=excluded.path, poster=excluded.poster, meta=excluded.meta, bytes=excluded.bytes,
                     status='pending', attempts=0, next_try=0, last_error=NULL""",
                (target, uid, session_uid, event_slug, kind, variant, str(path), str(poster) if poster else None,
                 json.dumps(meta or {}), priority, time.time(), size),
            )
        self.wake.set()

    def due(self, limit: int = 20) -> List[Dict[str, Any]]:
        with self._con() as con:
            rows = con.execute(
                "SELECT * FROM outbox WHERE status='pending' AND next_try<=? ORDER BY priority, id LIMIT ?",
                (time.time(), limit),
            ).fetchall()
        return [dict(r) for r in rows]

    def mark_done(self, item_id: int) -> None:
        with self._lock, self._con() as con:
            con.execute("UPDATE outbox SET status='done', done=?, last_error=NULL WHERE id=?", (time.time(), item_id))

    def mark_retry(self, item_id: int, error: str, permanent: bool = False,
                   max_attempts: int = MAX_ATTEMPTS) -> None:
        with self._lock, self._con() as con:
            row = con.execute("SELECT attempts, day, day_attempts FROM outbox WHERE id=?", (item_id,)).fetchone()
            attempts = (row["attempts"] if row else 0) + 1
            if permanent and row:
                # Counted per day for the automatic retry; a network that is
                # away (temporary) is not the file's fault and is not counted.
                today = self._today()
                am_tag = (row["day_attempts"] if row["day"] == today else 0) + 1
                con.execute("UPDATE outbox SET day=?, day_attempts=? WHERE id=?", (today, am_tag, item_id))
            if permanent and attempts >= max_attempts:
                con.execute("UPDATE outbox SET status='failed', attempts=?, last_error=? WHERE id=?",
                            (attempts, error[:500], item_id))
                return
            delay = min(MAX_BACKOFF, 5 * (2 ** min(attempts, 7)))
            con.execute("UPDATE outbox SET attempts=?, next_try=?, last_error=? WHERE id=?",
                        (attempts, time.time() + delay, error[:500], item_id))

    def status_of(self, item_id: int) -> str:
        """Der Zustand eines Eintrags — leer, wenn es ihn nicht mehr gibt."""
        with self._lock, self._con() as con:
            zeile = con.execute("SELECT status FROM outbox WHERE id=?", (int(item_id),)).fetchone()
        return str(zeile["status"]) if zeile else ""

    def reset_backoff(self) -> None:
        """Netz ist (wieder) da: alle wartenden Einträge sofort fällig machen."""
        with self._lock, self._con() as con:
            con.execute("UPDATE outbox SET next_try=0 WHERE status='pending' AND next_try>?", (time.time(),))
        self.wake.set()

    def retry_failed(self) -> int:
        with self._lock, self._con() as con:
            cur = con.execute("UPDATE outbox SET status='pending', attempts=0, next_try=0 WHERE status='failed'")
        self.wake.set()
        return cur.rowcount

    def failed_list(self, limit: int = 50) -> List[Dict[str, Any]]:
        """Uploads that gave up, oldest first, with the reason they gave up.

        ``event_slug`` is the event the file was taken for. Empty means the
        booth had no event then; a retry sends it without one, and the
        gallery files it under the event the booth has at that moment.
        """
        with self._con() as con:
            rows = con.execute("SELECT id, target, uid, session_uid, event_slug, kind, variant, path, attempts, "
                               "last_error, created FROM outbox WHERE status='failed' ORDER BY id LIMIT ?",
                               (max(1, int(limit)),)).fetchall()
        return [{"id": r["id"], "target": r["target"], "uid": r["uid"], "run": r["session_uid"],
                 "event_slug": r["event_slug"] or "", "kind": r["kind"], "variant": r["variant"],
                 "file": Path(r["path"]).name, "attempts": r["attempts"], "error": r["last_error"] or "",
                 "created": r["created"]} for r in rows]

    @staticmethod
    def _id_filter(ids: Optional[List[int]]) -> tuple:
        if ids is None:
            return "", ()
        clean = [int(i) for i in ids][:500]
        return f" AND id IN ({','.join('?' * len(clean)) or 'NULL'})", tuple(clean)

    def retry_ids(self, ids: Optional[List[int]] = None) -> int:
        """Failed uploads back into the normal queue — these ``ids``, or all with None.

        The row keeps its own event: nothing is rewritten here (see
        ``failed_list`` for a row without one).
        """
        where, args = self._id_filter(ids)
        with self._lock, self._con() as con:
            cur = con.execute("UPDATE outbox SET status='pending', attempts=0, next_try=0 WHERE status='failed'"
                              + where, args)
        if cur.rowcount:
            self.wake.set()
        return cur.rowcount

    def discard_failed(self, ids: Optional[List[int]] = None) -> int:
        """The operator gives up on failed uploads — these ``ids``, or all with None.

        The row stays, marked ``discarded``: the file on the booth is not
        touched, the history still shows it, and neither the automatic retry
        nor ``retry_failed`` picks it up again. It no longer counts as failed.
        """
        where, args = self._id_filter(ids)
        with self._lock, self._con() as con:
            cur = con.execute("UPDATE outbox SET status='discarded' WHERE status='failed'" + where, args)
        return cur.rowcount

    def retry_failed_capped(self, target: str, per_day: int = MAX_ATTEMPTS_PER_DAY,
                            max_attempts: int = MAX_ATTEMPTS) -> int:
        """Failed files of one target back into the queue, within today's cap.

        A file gets at most ``per_day`` permanent failures a day across all
        rounds: its new round is shortened to what is left of today, and a
        file with nothing left stays on "failed" until the date changes.
        """
        today = self._today()
        with self._lock, self._con() as con:
            rows = con.execute("SELECT id, day, day_attempts FROM outbox WHERE status='failed' AND target=?",
                               (target,)).fetchall()
            n = 0
            for r in rows:
                rest = per_day - (r["day_attempts"] if r["day"] == today else 0)
                if rest <= 0:
                    continue
                con.execute("UPDATE outbox SET status='pending', attempts=?, next_try=0 WHERE id=?",
                            (max(0, max_attempts - min(max_attempts, rest)), r["id"]))
                n += 1
        if n:
            self.wake.set()
        return n

    def cancel_session(self, session_uid: str) -> int:
        """Gelöschter Durchgang: noch nicht hochgeladene Dateien nicht mehr senden."""
        with self._lock, self._con() as con:
            cur = con.execute("DELETE FROM outbox WHERE session_uid=? AND status!='done'", (session_uid,))
        return cur.rowcount

    def offen_fuer_session(self, session_uid: str) -> Dict[str, int]:
        """Wie viele Einträge dieses Durchgangs warten noch, wie viele sind fertig.

        Gebraucht vom Aufräumen: Gelöscht wird nur, was nachweislich oben
        ist. ``offen`` zählt alles, was nicht ``done`` ist — auch
        ``failed``, denn ein fehlgeschlagener Upload ist kein angekommener.
        Sind beide Zahlen null, weiß die Warteschlange nichts über diesen
        Durchgang; dann entscheidet der Aufrufer, und zwar im Zweifel für
        Behalten.
        """
        with self._con() as con:
            rows = con.execute(
                "SELECT status, COUNT(*) c FROM outbox WHERE session_uid=? GROUP BY status",
                (session_uid,)).fetchall()
        nach_status = {r["status"]: int(r["c"]) for r in rows}
        erledigt = nach_status.pop("done", 0)
        # "discarded" (given up by the operator) stays in "offen" on purpose:
        # the file never reached the gallery, so the clean-up must not delete
        # the booth's copy of it.
        return {"offen": sum(nach_status.values()), "erledigt": erledigt}

    def history(self, limit: int = 50, target: Optional[str] = None) -> List[Dict[str, Any]]:
        """Was ist wann wohin gegangen. Die Frage nach dem Event: ist alles oben?"""
        sql = "SELECT path, target, kind, variant, bytes, done, status, last_error FROM outbox WHERE status!='pending'"
        args: List[Any] = []
        if target:
            sql += " AND target=?"
            args.append(target)
        sql += " ORDER BY COALESCE(done, created) DESC LIMIT ?"
        args.append(limit)
        with self._con() as con:
            rows = con.execute(sql, args).fetchall()
        return [{"file": Path(r["path"]).name, "target": r["target"], "kind": r["kind"], "variant": r["variant"],
                 "bytes": r["bytes"], "at": r["done"], "ok": r["status"] == "done", "error": r["last_error"]}
                for r in rows]

    def pending_by_target(self) -> Dict[str, Dict[str, int]]:
        """Offene Menge je Ziel — daraus wird die Restzeit."""
        with self._con() as con:
            rows = con.execute("""SELECT target, COUNT(*) n, COALESCE(SUM(bytes),0) b
                                  FROM outbox WHERE status='pending' GROUP BY target""").fetchall()
        return {r["target"]: {"files": r["n"], "bytes": r["b"]} for r in rows}

    def counts_by_target(self, limit: int = 8) -> Dict[str, Dict[str, int]]:
        """Sent, waiting and failed per target ("gesendet / offen / fehlgeschlagen").

        What the queue still remembers: sent entries age out after 30 days
        (``prune``). Discarded uploads count nowhere — the operator gave up on them.
        """
        out: Dict[str, Dict[str, int]] = {}
        with self._con() as con:
            rows = con.execute("SELECT target, status, COUNT(*) c FROM outbox "
                               "WHERE status IN ('done','pending','failed') GROUP BY target, status").fetchall()
        for r in rows:
            entry = out.setdefault(str(r["target"]), {"done": 0, "pending": 0, "failed": 0})
            entry[r["status"]] = int(r["c"])
        return dict(sorted(out.items())[:limit])

    def copy_event_to(self, target: str, event_slug: str, keep_session: Any = None) -> int:
        """Queue for ``target`` every file of ``event_slug`` another target was given.

        The button "Bisherige Aufnahmen dieses Events nachsenden": what was
        made before the target existed (or while it was switched off) was
        never queued for it. The rows of the other targets name exactly the
        files the booth made for each run, with their meta (time, naming
        rule). A file already queued or sent for ``target`` stays as it is;
        a file that is no longer on disk is left out. ``keep_session`` says
        whether a run may still leave the booth (deleted, "Nur drucken").
        Only this one event, never another.
        """
        if not event_slug:
            return 0
        with self._con() as con:
            rows = [dict(r) for r in con.execute(
                "SELECT * FROM outbox WHERE event_slug=? AND target!=? AND status!='discarded' "
                "ORDER BY id", (event_slug, target)).fetchall()]
        seen, added = set(), 0
        for r in rows:
            key = (r["uid"], r["variant"])
            if key in seen:
                continue
            seen.add(key)
            if keep_session is not None and not keep_session(r["session_uid"]):
                continue
            if not Path(r["path"]).is_file():
                continue
            added += self.add_new(target=target, uid=r["uid"], session_uid=r["session_uid"],
                                  event_slug=event_slug, kind=r["kind"], variant=r["variant"], path=Path(r["path"]),
                                  poster=Path(r["poster"]) if r.get("poster") else None,
                                  meta=json.loads(r.get("meta") or "{}"), priority=int(r["priority"] or 5) + 2)
        return added

    def add_new(self, *, target: str, uid: str, session_uid: str, event_slug: str, kind: str, variant: str,
                path: Path, poster: Optional[Path] = None, meta: Optional[Dict[str, Any]] = None,
                priority: int = 5) -> int:
        """Like ``add``, but a file this target already has (queued, sent, failed) is not touched. 1 = added."""
        try:
            size = Path(path).stat().st_size
        except OSError:
            size = 0
        with self._lock, self._con() as con:
            cur = con.execute(
                """INSERT INTO outbox(target, uid, session_uid, event_slug, kind, variant, path, poster, meta,
                                      priority, created, bytes)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(target, uid, variant) DO NOTHING""",
                (target, uid, session_uid, event_slug, kind, variant, str(path), str(poster) if poster else None,
                 json.dumps(meta or {}), priority, time.time(), size))
        if cur.rowcount:
            self.wake.set()
        return int(cur.rowcount or 0)

    def prune(self, days: int = 30, keep: int = 2000) -> int:
        """Erledigtes altert aus. Der Verlauf bleibt lesbar, die Datei klein."""
        cutoff = time.time() - days * 86400
        with self._lock, self._con() as con:
            cur = con.execute("""DELETE FROM outbox WHERE status='done' AND done < ?
                                 AND id NOT IN (SELECT id FROM outbox WHERE status='done' ORDER BY done DESC LIMIT ?)""",
                              (cutoff, keep))
        return cur.rowcount

    def counts(self) -> Dict[str, int]:
        with self._con() as con:
            rows = con.execute("SELECT status, COUNT(*) c FROM outbox GROUP BY status").fetchall()
        out = {"pending": 0, "done": 0, "failed": 0}
        out.update({r["status"]: r["c"] for r in rows})
        return out
