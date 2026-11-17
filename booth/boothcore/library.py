"""Lokale Bibliothek aller Ergebnisse — für die Galerie an der Box.

Gäste stöbern an der Box durch die Aufnahmen des Events, öffnen den QR-Code
eines Bildes oder drucken nach (im Rahmen der Limits). Gelöschte Aufnahmen
verschwinden sofort lokal und werden beim nächsten Sync auch auf dem Server
entfernt.
"""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

SCHEMA = """
CREATE TABLE IF NOT EXISTS items (
    uid TEXT PRIMARY KEY,
    event_slug TEXT NOT NULL DEFAULT '',
    mode TEXT NOT NULL,
    result TEXT NOT NULL,
    thumb TEXT,
    print_file TEXT,
    anim TEXT,
    share_url TEXT,
    look TEXT,
    prints INTEGER NOT NULL DEFAULT 0,
    created REAL NOT NULL,
    deleted REAL
);
CREATE INDEX IF NOT EXISTS items_event ON items(event_slug, deleted, created);
CREATE TABLE IF NOT EXISTS deletions (uid TEXT PRIMARY KEY, created REAL NOT NULL, acked REAL);
"""


class Library:
    def __init__(self, path: Path):
        self.path = path
        self._lock = threading.Lock()
        with self._con() as con:
            con.executescript(SCHEMA)
            # Nachträglich dazugekommen: die bewegte Fassung einer Collage.
            # Eine bestehende Bibliothek darf davon nichts merken.
            cols = {r[1] for r in con.execute("PRAGMA table_info(items)")}
            if "anim" not in cols:
                con.execute("ALTER TABLE items ADD COLUMN anim TEXT")
            # Dauerbetrieb: Wann war dieser Durchgang nachweislich in der
            # Galerie, und wann wurden seine Dateien hier wieder entfernt?
            # Beides getrennt von ``deleted`` — das heißt „der Gast will es
            # weg" und reist als Löschauftrag zur Galerie. Aufräumen heißt
            # nur „die Kopie auf dieser Platte wird nicht mehr gebraucht".
            if "geliefert" not in cols:
                con.execute("ALTER TABLE items ADD COLUMN geliefert REAL")
            if "aufgeraeumt" not in cols:
                con.execute("ALTER TABLE items ADD COLUMN aufgeraeumt REAL")
            # Consent per run: the option the guest picked and the record of
            # what was shown (JSON, nothing about the person). A "print" run is
            # never listed in the kiosk gallery; the row outlives the wipe, so
            # the record stays after the files are gone.
            if "consent_option" not in cols:
                con.execute("ALTER TABLE items ADD COLUMN consent_option TEXT")
            if "consent" not in cols:
                con.execute("ALTER TABLE items ADD COLUMN consent TEXT")

    def _con(self):
        con = sqlite3.connect(self.path, timeout=10)
        con.row_factory = sqlite3.Row
        return con

    def upsert(self, *, uid: str, event_slug: str, mode: str, result: Path, thumb: Optional[Path],
               print_file: Optional[Path], share_url: str, look: str,
               anim: Optional[Path] = None, consent: Optional[Dict[str, Any]] = None) -> None:
        option = str((consent or {}).get("option") or "") or None
        record = json.dumps(consent, sort_keys=True) if consent else None
        with self._lock, self._con() as con:
            con.execute(
                """INSERT INTO items(uid, event_slug, mode, result, thumb, print_file, anim,
                                    share_url, look, created, consent_option, consent)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(uid) DO UPDATE SET result=excluded.result, thumb=excluded.thumb,
                     print_file=excluded.print_file, anim=excluded.anim, look=excluded.look,
                     share_url=excluded.share_url,
                     consent_option=COALESCE(excluded.consent_option, consent_option),
                     consent=COALESCE(excluded.consent, consent)""",
                (uid, event_slug, mode, str(result), str(thumb) if thumb else None,
                 str(print_file) if print_file else None, str(anim) if anim else None,
                 share_url, look, time.time(), option, record),
            )

    def consent(self, uid: str) -> Dict[str, Any]:
        """The consent record of a run, also after it was deleted or wiped."""
        with self._con() as con:
            r = con.execute("SELECT consent FROM items WHERE uid=?", (uid,)).fetchone()
        try:
            return json.loads(r[0]) if r and r[0] else {}
        except ValueError:
            return {}

    def set_gallery(self, uid: str, share_url: str, consent: Dict[str, Any]) -> None:
        """A "Nur drucken" run that the guest moved to the gallery after all."""
        with self._lock, self._con() as con:
            con.execute("UPDATE items SET share_url=?, consent_option=?, consent=? WHERE uid=?",
                        (share_url, str(consent.get("option") or "gallery"),
                         json.dumps(consent, sort_keys=True), uid))

    def is_print_only(self, uid: str) -> bool:
        with self._con() as con:
            r = con.execute("SELECT consent_option FROM items WHERE uid=?", (uid,)).fetchone()
        return bool(r and r[0] == "print")

    def withheld(self, uid: str) -> bool:
        """Must this run stay on the booth? Deleted by a guest, or "print only"."""
        with self._con() as con:
            r = con.execute("SELECT deleted, consent_option FROM items WHERE uid=?", (uid,)).fetchone()
        return bool(r and (r[0] is not None or r[1] == "print"))

    def get(self, uid: str) -> Optional[Dict[str, Any]]:
        with self._con() as con:
            r = con.execute("SELECT * FROM items WHERE uid=? AND deleted IS NULL", (uid,)).fetchone()
        return dict(r) if r else None

    def list(self, event_slug: str, offset: int = 0, limit: int = 60) -> List[Dict[str, Any]]:
        with self._con() as con:
            rows = con.execute(
                "SELECT * FROM items WHERE event_slug=? AND deleted IS NULL AND aufgeraeumt IS NULL "
                "AND COALESCE(consent_option, '') != 'print' "
                "ORDER BY created DESC LIMIT ? OFFSET ?",
                (event_slug, limit, offset)).fetchall()
        return [dict(r) for r in rows]

    def count(self, event_slug: str) -> int:
        with self._con() as con:
            return con.execute("SELECT COUNT(*) FROM items WHERE event_slug=? AND deleted IS NULL "
                               "AND aufgeraeumt IS NULL AND COALESCE(consent_option, '') != 'print'",
                               (event_slug,)).fetchone()[0]

    def add_prints(self, uid: str, copies: int) -> None:
        with self._lock, self._con() as con:
            con.execute("UPDATE items SET prints=prints+? WHERE uid=?", (copies, uid))

    def prints(self, uid: str) -> int:
        with self._con() as con:
            r = con.execute("SELECT prints FROM items WHERE uid=?", (uid,)).fetchone()
        return int(r[0]) if r else 0

    def event_prints(self, event_slug: str) -> int:
        with self._con() as con:
            return int(con.execute("SELECT COALESCE(SUM(prints),0) FROM items WHERE event_slug=?",
                                   (event_slug,)).fetchone()[0])

    def delete(self, uid: str, slip: bool = True) -> Optional[Dict[str, Any]]:
        """Hide a run from the kiosk gallery. The row stays: print counters
        are summed over it.

        ``slip=False`` when the deletion came from the gallery — a slip would
        only send it back to where it came from.
        """
        item = self.get(uid)
        with self._lock, self._con() as con:
            con.execute("UPDATE items SET deleted=? WHERE uid=?", (time.time(), uid))
            if slip:
                con.execute("INSERT OR IGNORE INTO deletions(uid, created) VALUES (?,?)", (uid, time.time()))
        return item

    def on_the_booth(self, event_slugs: List[str]) -> List[Dict[str, Any]]:
        """Runs of these events whose files are still on this booth (oldest first)."""
        if not event_slugs:
            return []
        marks = ",".join("?" * len(event_slugs))
        with self._con() as con:
            rows = con.execute(
                f"SELECT * FROM items WHERE event_slug IN ({marks}) AND deleted IS NULL "
                "AND aufgeraeumt IS NULL ORDER BY created", list(event_slugs)).fetchall()
        return [dict(r) for r in rows]

    def others_on_the_booth(self, event_slugs: List[str]) -> int:
        """How many runs of any other event are still on this booth."""
        marks = ",".join("?" * len(event_slugs)) or "''"
        with self._con() as con:
            return int(con.execute(
                f"SELECT COUNT(*) FROM items WHERE event_slug NOT IN ({marks}) AND deleted IS NULL "
                "AND aufgeraeumt IS NULL", list(event_slugs)).fetchone()[0])

    def forget_prints(self, uids: List[str]) -> None:
        """Test runs removed: their prints no longer count against the event's allowance."""
        with self._lock, self._con() as con:
            con.executemany("UPDATE items SET prints=0 WHERE uid=?", [(u,) for u in uids])

    def pending_deletions(self) -> List[str]:
        with self._con() as con:
            return [r[0] for r in con.execute("SELECT uid FROM deletions WHERE acked IS NULL LIMIT 200")]

    def ack_deletions(self, uids: List[str]) -> None:
        with self._lock, self._con() as con:
            con.executemany("UPDATE deletions SET acked=? WHERE uid=?", [(time.time(), u) for u in uids])

    # ── Dauerbetrieb: was darf die Platte wieder verlassen ────────────

    def mark_geliefert(self, uid: str) -> None:
        """Dieser Durchgang ist nachweislich oben.

        Wird festgehalten, sobald es beobachtet wurde — nicht erst, wenn
        aufgeräumt wird. Die Warteschlange räumt erledigte Einträge nach
        dreißig Tagen selbst weg; ohne diese Notiz wüsste danach niemand
        mehr, ob die Dateien jemals angekommen sind, und das Aufräumen
        müsste im Zweifel alles behalten.
        """
        with self._lock, self._con() as con:
            con.execute("UPDATE items SET geliefert=COALESCE(geliefert, ?) WHERE uid=?",
                        (time.time(), uid))

    def aufraeum_kandidaten(self, aelter_als: float, limit: int = 500) -> List[Dict[str, Any]]:
        """Durchgänge, die alt genug sind und deren Dateien noch hier liegen."""
        with self._con() as con:
            rows = con.execute(
                """SELECT * FROM items WHERE created < ? AND deleted IS NULL AND aufgeraeumt IS NULL
                   ORDER BY created LIMIT ?""", (aelter_als, limit)).fetchall()
        return [dict(r) for r in rows]

    def mark_aufgeraeumt(self, uid: str) -> None:
        with self._lock, self._con() as con:
            con.execute("UPDATE items SET aufgeraeumt=? WHERE uid=?", (time.time(), uid))

    def zahlen(self) -> Dict[str, int]:
        with self._con() as con:
            r = con.execute(
                """SELECT COUNT(*) gesamt,
                          SUM(CASE WHEN aufgeraeumt IS NOT NULL THEN 1 ELSE 0 END) aufgeraeumt,
                          SUM(CASE WHEN geliefert IS NOT NULL THEN 1 ELSE 0 END) geliefert
                     FROM items WHERE deleted IS NULL""").fetchone()
        return {"gesamt": int(r[0] or 0), "aufgeraeumt": int(r[1] or 0), "geliefert": int(r[2] or 0)}
