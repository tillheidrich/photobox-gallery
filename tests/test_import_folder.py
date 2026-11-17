"""Import for the move: a folder tree on disk becomes client galleries.

``python -m app.import_folder ROOT [--client NAME] [--ids map.csv] [--dry-run]``
Top level = gallery, one level down = folder, deeper levels flattened with
the path in the name. Pictures go through the admin upload path (same type
check, web versions). Kind ``client``, date from the oldest EXIF date, a
second run creates nothing twice, a report says what happened. Nothing is
fetched from anywhere.
"""
import csv
import os
import subprocess
import sys
from pathlib import Path

import pytest
from PIL import Image

from app import clients, db, folders, import_folder, retention

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(autouse=True)
def schema(client):
    client.get("/healthz")
    yield


def _jpeg(path: Path, when="2019:06:01 14:00:00", color=(120, 80, 40)):
    path.parent.mkdir(parents=True, exist_ok=True)
    exif = Image.Exif()
    exif.get_ifd(0x8769)[0x9003] = when
    Image.new("RGB", (64, 48), color).save(path, "JPEG", exif=exif.tobytes())


def _tree(tmp_path, tag):
    root = tmp_path / f"picdrop-{tag}"
    _jpeg(root / f"Hochzeit {tag}" / "IMG_0001.jpg", "2019:06:01 14:00:00")
    _jpeg(root / f"Hochzeit {tag}" / "Trauung" / "IMG_0002.jpg", "2019:06:01 12:00:00", (10, 20, 30))
    _jpeg(root / f"Hochzeit {tag}" / "Trauung" / "Kirche" / "IMG_0003.jpg", "2019:06:01 11:00:00", (40, 50, 60))
    (root / f"Hochzeit {tag}" / "notizen.txt").write_text("kein Bild")
    (root / f"Hochzeit {tag}" / "kaputt.jpg").write_bytes(b"not a jpeg at all")
    _jpeg(root / f"Firma {tag}" / "A.jpg", "2021:03:03 09:00:00", (90, 90, 90))
    return root


def _event_named(name):
    return next(e for e in db.list_events() if e["name"] == name)


def test_a_tree_becomes_client_galleries_with_folders(tmp_path):
    root = _tree(tmp_path, "a1")
    report = import_folder.run(root, client="Studio Kunde a1")
    wedding = _event_named("Hochzeit a1")
    assert retention.kind_of(wedding) == "client" and wedding["date"][:10] == "2019-06-01"
    names = sorted(c["name"] for c in folders.children_of(wedding["id"]))
    assert names == ["Trauung", "Trauung / Kirche"]
    assert "Trauung / Kirche" in " ".join(report["flattened"])
    assert db.count_media(wedding["id"]) == 1
    rejected = {Path(r["path"]).name: r["reason"] for r in report["rejected"]}
    assert "kaputt.jpg" in rejected and "notizen.txt" in rejected
    cid = next(c["id"] for c in clients.list_all() if c["name"] == "Studio Kunde a1")
    assert set(clients.event_ids(cid)) == {wedding["id"], _event_named("Firma a1")["id"]}
    assert report["created_galleries"] == 2 and report["created_folders"] == 2 and report["files"] == 4


def test_a_second_run_creates_nothing_twice(tmp_path):
    root = _tree(tmp_path, "b2")
    import_folder.run(root)
    before = len(db.list_events())
    report = import_folder.run(root)
    assert len(db.list_events()) == before
    assert report["files"] == 0 and report["skipped"] == 4
    assert db.count_media(_event_named("Hochzeit b2")["id"]) == 1


def test_dry_run_writes_nothing(tmp_path):
    root = _tree(tmp_path, "c3")
    before = len(db.list_events())
    report = import_folder.run(root, dry_run=True)
    assert len(db.list_events()) == before
    assert report["dry_run"] and report["created_galleries"] == 2 and report["files"] == 4


def test_the_map_sets_old_ids_and_titles(tmp_path):
    root = _tree(tmp_path, "d4")
    m = tmp_path / "map.csv"
    with m.open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["ordnerpfad", "kennung", "titel"])
        w.writerow(["Hochzeit d4", "QW12ER34TZ", "Hochzeit Anna & Ben"])
        w.writerow(["Hochzeit d4/Trauung", "AS56DF78GH", ""])
    import_folder.run(root, ids_csv=m)
    wedding = _event_named("Hochzeit Anna & Ben")
    assert wedding["foreign_id"] == "QW12ER34TZ"
    trauung = next(c for c in folders.children_of(wedding["id"]) if c["name"] == "Trauung")
    assert trauung["foreign_id"] == "AS56DF78GH"


def test_the_command_line(tmp_path):
    root = _tree(tmp_path, "e5")
    proc = subprocess.run([sys.executable, "-m", "app.import_folder", str(root), "--dry-run"], cwd=ROOT,
                          capture_output=True, text=True, env={**os.environ})
    assert proc.returncode == 0 and "angelegt" in proc.stdout and "abgelehnt" in proc.stdout


def test_a_second_run_keeps_the_date(tmp_path):
    root = _tree(tmp_path, "f6")
    import_folder.run(root)
    import_folder.run(root)
    assert _event_named("Hochzeit f6")["date"][:10] == "2019-06-01"
