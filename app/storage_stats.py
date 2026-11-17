"""Speicherüberblick: je Event aus der Datenbank, global von der Platte.

Zwei Zahlenquellen, zwei Kosten:

* Je Event summieren wir ``media.size_bytes`` — eine Abfrage, kein Dateibaum.
  Getrennt nach Web-Fassung und Original sowie danach, ob die Datei lokal liegt
  oder ausgelagert ist (``media.storage`` / ``media.remote_path``).
* Für die Platte fragen wir ``shutil.disk_usage(DATA_DIR)``. Das ist billig,
  aber nicht kostenlos, deshalb eine Minute gecacht.

Nichts hier läuft über den Dateibaum. Wer 80.000 Fotos hat, soll ein Dashboard
sehen und nicht warten.
"""
from __future__ import annotations

import os
import shutil
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from app import db

# Wie lange die Plattenzahlen gelten, bevor wir wieder nachsehen.
DISK_CACHE_SECONDS = 60

# Ab diesem Füllstand warnt das Dashboard (überschreibbar per Einstellung).
DEFAULT_WARN_PERCENT = 85

_disk_cache: Dict[str, Any] = {}


def data_dir() -> Path:
    return Path(os.environ.get("DATA_DIR", "./data")).resolve()


def warn_percent() -> int:
    try:
        value = int(db.get_setting("storage_warn_percent") or DEFAULT_WARN_PERCENT)
    except (TypeError, ValueError):
        return DEFAULT_WARN_PERCENT
    return min(99, max(50, value))


def _media_cols() -> set:
    con = db.get_db()
    try:
        return db._table_cols(con.cursor(), "media")
    except Exception:
        return set()
    finally:
        con.close()


def _sum_sql(cols: set) -> str:
    """Summen-Abfrage, die auch auf einer noch nicht migrierten Tabelle läuft."""
    is_original = ("(COALESCE(variant,'web')='original' OR kind='original')"
                   if "variant" in cols else "kind='original'")
    is_remote = "storage='remote'" if "storage" in cols else "0"
    return f"""
        SELECT event_id,
               COUNT(*) AS files,
               COALESCE(SUM(size_bytes), 0) AS total_bytes,
               COALESCE(SUM(CASE WHEN {is_original} THEN size_bytes ELSE 0 END), 0) AS original_bytes,
               COALESCE(SUM(CASE WHEN {is_original} THEN 0 ELSE size_bytes END), 0) AS web_bytes,
               COALESCE(SUM(CASE WHEN {is_remote} THEN size_bytes ELSE 0 END), 0) AS remote_bytes,
               COALESCE(SUM(CASE WHEN {is_remote} THEN 0 ELSE size_bytes END), 0) AS local_bytes,
               COALESCE(SUM(CASE WHEN {is_remote} THEN 1 ELSE 0 END), 0) AS remote_files
          FROM media
         GROUP BY event_id
    """


_EMPTY = {"files": 0, "total_bytes": 0, "original_bytes": 0, "web_bytes": 0,
          "remote_bytes": 0, "local_bytes": 0, "remote_files": 0}


def usage_by_event() -> Dict[int, Dict[str, int]]:
    """Speicherzahlen aller Events, nach Event-ID."""
    cols = _media_cols()
    if not cols:
        return {}
    con = db.get_db()
    try:
        rows = con.execute(_sum_sql(cols)).fetchall()
    except Exception:
        rows = []
    finally:
        con.close()
    return {int(r["event_id"]): {k: int(r[k] or 0) for k in _EMPTY} for r in rows}


def event_usage(event_id: int) -> Dict[str, int]:
    """Speicherzahlen eines Events — nie ``None``, im Zweifel Nullen."""
    return dict(usage_by_event().get(int(event_id), _EMPTY))


def sum_usage(values) -> Dict[str, int]:
    out = dict(_EMPTY)
    for row in values:
        for key in _EMPTY:
            out[key] += int(row.get(key) or 0)
    return out


def disk(path: Optional[Path] = None) -> Dict[str, Any]:
    """Plattenbelegung von DATA_DIR — eine Minute gecacht."""
    root = Path(path or data_dir())
    key = str(root)
    hit = _disk_cache.get(key)
    now = time.time()
    if hit and now - hit["at"] < DISK_CACHE_SECONDS:
        return hit["value"]
    try:
        root.mkdir(parents=True, exist_ok=True)
        total, used, free = shutil.disk_usage(str(root))
        value = {
            "ok": True, "path": key,
            "total": int(total), "used": int(used), "free": int(free),
            "percent_used": int(round(used * 100 / total)) if total else 0,
        }
    except OSError:
        value = {"ok": False, "path": key, "total": 0, "used": 0, "free": 0, "percent_used": 0}
    _disk_cache[key] = {"at": now, "value": value}
    return value


def clear_cache() -> None:
    _disk_cache.clear()


def overview() -> Dict[str, Any]:
    """Alles, was die Dashboard-Karte braucht."""
    from app import media as media_mod

    by_event = usage_by_event()
    events = []
    for ev in (db.list_events() or []):
        usage = dict(by_event.get(int(ev["id"]), _EMPTY))
        usage.update({"id": ev["id"], "name": ev["name"], "slug": ev["slug"]})
        events.append(usage)
    events.sort(key=lambda e: e["total_bytes"], reverse=True)

    totals = sum_usage(by_event.values())
    d = disk()
    limit = warn_percent()
    return {
        "events": events,
        "totals": totals,
        "disk": d,
        "warn_percent": limit,
        "warning": bool(d["ok"] and d["percent_used"] >= limit),
        "human": media_mod.human_bytes,
    }
