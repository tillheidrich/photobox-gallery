"""Import for the move: a folder tree on disk becomes client galleries.

    python -m app.import_folder ROOT [--client NAME] [--ids map.csv] [--dry-run]

* Top level of ROOT = one gallery each, one level down = a folder
  (app/folders.py). Deeper directories become folders of the gallery too,
  named with their path ("Trauung / Kirche"), and the report lists them.
* Every file goes through ``media.save_upload``, the admin upload: same
  content check (``uploads.sniff``), web versions and thumbnails. What is not
  a picture or video is rejected with its reason and stays where it is.
* Galleries are of kind ``client`` (no deletion by date, app/retention.py).
  The date is the oldest EXIF capture date, else the directory's date.
* Idempotent: every imported file is remembered by its path, size and
  SHA-256 (table ``import_sources``); a second run skips what is there.
  Galleries and folders are found again by their path.
* ``--client`` creates (or finds) a client of that name and assigns every
  imported gallery. ``--ids`` (CSV: ordnerpfad, kennung, [titel]) sets old
  ids for the address /<account>/<id> and optionally a new title.
* Nothing is fetched from anywhere; no credentials of any service.
"""
from __future__ import annotations

import csv
import datetime as dt
import hashlib
import io
import sys
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from app import db

MEDIA_EXT = {".jpg", ".jpeg", ".png", ".webp", ".heic", ".heif", ".tif", ".tiff", ".gif",
             ".mp4", ".mov", ".webm", ".m4v"}


def init_schema(cur) -> None:
    cur.execute("""
        CREATE TABLE IF NOT EXISTS import_sources (
            path TEXT PRIMARY KEY,
            kind TEXT NOT NULL,                  -- gallery | folder | file
            event_id INTEGER REFERENCES events(id) ON DELETE CASCADE,
            media_id INTEGER REFERENCES media(id) ON DELETE CASCADE,
            size INTEGER, sha256 TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );""")


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _known(key: str) -> Optional[Dict[str, Any]]:
    con = db.get_db()
    try:
        row = con.execute("SELECT * FROM import_sources WHERE path=?", (key,)).fetchone()
    finally:
        con.close()
    if not row:
        return None
    row = dict(row)
    # A gallery deleted by hand since the last run is created again.
    if row["kind"] in ("gallery", "folder") and not db.get_event_by_id(int(row["event_id"] or 0)):
        return None
    if row["kind"] == "file" and not db.get_media(int(row["media_id"] or 0)):
        return None
    return row


def _remember(key: str, kind: str, event_id: int, media_id: Optional[int] = None,
              size: Optional[int] = None, sha: Optional[str] = None) -> None:
    con = db.get_db()
    try:
        con.execute("INSERT OR REPLACE INTO import_sources(path, kind, event_id, media_id, size, sha256)"
                    " VALUES (?,?,?,?,?,?)", (key, kind, event_id, media_id, size, sha))
        con.commit()
    finally:
        con.close()


def _taken(path: Path) -> Optional[dt.datetime]:
    try:
        from PIL import Image
        with Image.open(path) as img:
            raw = img.getexif().get_ifd(0x8769).get(0x9003) or img.getexif().get(0x0132)
        return dt.datetime.strptime(str(raw).strip()[:19], "%Y:%m:%d %H:%M:%S") if raw else None
    except Exception:                   # noqa: BLE001 — no date is not an error
        return None


def _new_gallery(name: str, parent_id: Optional[int]) -> int:
    from app import folders
    if parent_id:
        return folders.create_child(parent_id, name)
    import secrets
    base = folders._slugify(name) or "galerie"
    slug, n = base, 2
    while db.slug_taken(slug):
        slug, n = f"{base}-{n}", n + 1
    eid = db.create_event(name=name[:200], slug=slug, upload_token=secrets.token_urlsafe(32), status="live")
    db.update_event(eid, published=1, kind="client")
    return eid


def _read_map(path: Optional[Path]) -> Dict[str, Dict[str, str]]:
    if not path:
        return {}
    text = Path(path).read_text(encoding="utf-8-sig")
    dialect = csv.Sniffer().sniff(text.splitlines()[0] if text else ",", delimiters=",;")
    out = {}
    for row in csv.DictReader(text.splitlines(), dialect=dialect):
        row = {(k or "").strip().lower(): (v or "").strip() for k, v in row.items()}
        key = row.get("ordnerpfad", "").strip("/").replace("\\", "/")
        if key:
            out[key] = row
    return out


def run(root: Any, *, client: str = "", ids_csv: Any = None, dry_run: bool = False,
        progress: Optional[Callable[[str], None]] = None) -> Dict[str, Any]:
    """Import ROOT. Returns the report; writes nothing with ``dry_run``."""
    from app import clients, ecosystem, legacy, media, uploads
    from werkzeug.datastructures import FileStorage
    ecosystem.init_schema()
    say = progress or (lambda _msg: None)
    root = Path(root)
    mapping = _read_map(Path(ids_csv) if ids_csv else None)
    report: Dict[str, Any] = {"dry_run": dry_run, "created_galleries": 0, "created_folders": 0, "files": 0,
                              "skipped": 0, "rejected": [], "flattened": [], "galleries": [], "ids": 0}
    top_ids: List[int] = []
    for gdir in sorted(p for p in root.iterdir() if p.is_dir() and not p.name.startswith(".")):
        say(f"Galerie {gdir.name}")
        targets: Dict[str, Path] = {gdir.name: gdir}           # relpath -> directory
        for sub in sorted(p for p in gdir.rglob("*") if p.is_dir() and not p.name.startswith(".")):
            rel = sub.relative_to(root).as_posix()
            targets[rel] = sub
            if len(sub.relative_to(gdir).parts) > 1:
                report["flattened"].append(f"{rel} → Ordner „{' / '.join(sub.relative_to(gdir).parts)}“")
        gallery_id: Optional[int] = None
        oldest: Optional[dt.datetime] = None
        fresh_gallery = False
        for rel, directory in targets.items():
            is_top = directory == gdir
            key = "dir:" + rel
            known = _known(key)
            event_id = int(known["event_id"]) if known else None
            if not event_id:
                if is_top:
                    report["created_galleries"] += 1
                    fresh_gallery = True
                else:
                    report["created_folders"] += 1
                if not dry_run:
                    name = gdir.name if is_top else " / ".join(directory.relative_to(gdir).parts)
                    event_id = _new_gallery(name, None if is_top else gallery_id)
                    _remember(key, "gallery" if is_top else "folder", event_id)
            if is_top:
                gallery_id = event_id
                if event_id:
                    top_ids.append(event_id)
                report["galleries"].append(rel)
            files = sorted(p for p in directory.iterdir() if p.is_file() and not p.name.startswith("."))
            for f in files:
                frel = f.relative_to(root).as_posix()
                if f.suffix.lower() not in MEDIA_EXT:
                    report["rejected"].append({"path": frel, "reason": "kein Bild und kein Video"})
                    continue
                size, sha = f.stat().st_size, _sha256(f)
                seen = _known("file:" + frel)
                if seen and seen.get("size") == size and seen.get("sha256") == sha:
                    report["skipped"] += 1
                    continue
                try:
                    uploads.sniff(f)
                except uploads.UploadRejected as e:
                    report["rejected"].append({"path": frel, "reason": str(e) or "Typprüfung"})
                    continue
                when = _taken(f)
                if when and (oldest is None or when < oldest):
                    oldest = when
                report["files"] += 1
                if dry_run:
                    continue
                try:
                    with f.open("rb") as fh:
                        result = media.save_upload(event_id, FileStorage(stream=io.BytesIO(fh.read()),
                                                                         filename=f.name),
                                                   source="import")
                except uploads.UploadRejected as e:
                    report["files"] -= 1
                    report["rejected"].append({"path": frel, "reason": str(e)})
                    continue
                # The original name is what the photographer's catalogue knows.
                con = db.get_db()
                try:
                    con.execute("UPDATE media SET filename=? WHERE id=?", (f.name, int(result["id"])))
                    con.commit()
                finally:
                    con.close()
                _remember("file:" + frel, "file", event_id, int(result["id"]), size, sha)
            if not dry_run and rel in mapping and event_id:
                row = mapping[rel]
                if row.get("kennung"):
                    try:
                        legacy.set_event_id(event_id, row["kennung"])
                        report["ids"] += 1
                    except ValueError as e:
                        report["rejected"].append({"path": rel, "reason": f"Kennung: {e}"})
                if row.get("titel"):
                    db.update_event(event_id, name=row["titel"][:200])
        current = (db.get_event_by_id(gallery_id) or {}).get("date") if gallery_id else None
        if not dry_run and gallery_id and (fresh_gallery or (oldest and (not current or
                                                                         oldest.strftime("%Y-%m-%d") < current[:10]))):
            # A second run that skipped everything keeps the date it found.
            date = oldest or dt.datetime.fromtimestamp(gdir.stat().st_mtime)
            db.update_event(gallery_id, date=date.strftime("%Y-%m-%d"))
            from app import folders
            for child in folders.children_of(gallery_id):
                db.update_event(int(child["id"]), date=date.strftime("%Y-%m-%d"))
    if client and not dry_run and top_ids:
        found = next((c for c in clients.list_all() if c["name"] == client.strip()), None)
        cid = found["id"] if found else clients.create(client)
        clients.set_events(cid, set(clients.event_ids(cid)) | set(top_ids))
    return report


def main(argv=None) -> int:
    import argparse
    p = argparse.ArgumentParser(prog="python -m app.import_folder",
                                description="Ordnerstruktur als Kundengalerien übernehmen.")
    p.add_argument("root")
    p.add_argument("--client", default="", help="Kunde, dem alle Galerien zugeordnet werden")
    p.add_argument("--ids", default=None, help="CSV: ordnerpfad, kennung, [titel]")
    p.add_argument("--dry-run", action="store_true", help="nur zeigen, nichts anlegen")
    args = p.parse_args(argv)
    db.init_db()
    report = run(args.root, client=args.client, ids_csv=args.ids, dry_run=args.dry_run,
                 progress=lambda m: print(m, file=sys.stderr, flush=True))
    for line in report["flattened"]:
        print("flachgezogen:", line)
    for r in report["rejected"]:
        print(f"abgelehnt: {r['path']} — {r['reason']}")
    print(("Probelauf · " if report["dry_run"] else "")
          + f"angelegt: {report['created_galleries']} Galerien, {report['created_folders']} Ordner, "
          f"{report['files']} Dateien · übersprungen: {report['skipped']} · abgelehnt: {len(report['rejected'])}"
          f" · Kennungen: {report['ids']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
