"""A gallery template edited at the booth is not overwritten by the gallery's older copy.

The sequence before the fix: the operator edits a template the gallery sent
(``vorlagen.html`` → "Bearbeiten" → save). The booth writes the file and
notes it for upload (``template_push``); the upload travels with the next
heartbeat. Every heartbeat answer lists the profile's assets with the
gallery's checksum, and the agent downloads every file whose checksum
differs. So the edit was replaced by the gallery's old version whenever the
answer did not already know the new one:

* a heartbeat that was under way while the operator saved (the payload was
  built before the edit, the answer lists the old checksum);
* a gallery that refused the template (another version's checks) or is too
  old to take templates from a booth - it never acknowledges, the note stays,
  and the next push carried the gallery's own content back: the edit was gone.

Now the agent leaves a file alone while the booth still has it to upload;
once the gallery has acknowledged it, the gallery lists the booth's version.
"""
from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

GALERIE = {"id": "abendvorlage", "name": "Abendvorlage", "paper": "10x15",
           "slots": [{"x": 60, "y": 60, "w": 1080, "h": 700, "shot": 0},
                     {"x": 60, "y": 800, "w": 1080, "h": 700, "shot": 1}]}


class Gallery:
    """Hands out its own version of a file, like GalleryTarget.download."""

    def __init__(self, content: bytes):
        self.content = content
        self.downloads = []

    def download(self, url, target: Path):
        self.downloads.append(url)
        Path(target).write_bytes(self.content)


def _sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


@pytest.fixture()
def booth(monkeypatch, tmp_path):
    monkeypatch.setenv("BOOTH_DATA", str(tmp_path))
    monkeypatch.setenv("BOOTH_CAMERA", "mock")
    from boothcore.app import Booth
    b = Booth(base=tmp_path, start_threads=False)
    b.profile["access"]["pin"] = "135790"
    yield b
    b.mail.stop()


@pytest.fixture()
def client(booth):
    from boothcore.app import create_app
    c = TestClient(create_app(booth))
    c.headers.update({"X-Admin-Token": c.post("/api/admin/login", json={"pin": "135790"}).json()["token"]})
    return c


def _gallery_file(booth) -> tuple:
    """The template as the gallery sent it with the last profile."""
    import json
    datei = "abendvorlage.json"
    roh = (json.dumps(GALERIE, indent=1) + "\n").encode()
    booth.assets_dir.mkdir(parents=True, exist_ok=True)
    (booth.assets_dir / datei).write_bytes(roh)
    booth.store.save_local({"server": {"url": "https://galerie.example.org", "api_key": "k"},
                            "gallery_templates": [datei]})
    return datei, roh


def test_an_edit_waiting_for_upload_is_not_replaced_by_the_gallerys_copy(client, booth):
    datei, roh = _gallery_file(booth)
    edited = dict(GALERIE, name="Abendvorlage (Box)")
    assert client.post("/api/admin/templates", json=edited).json()["asset"]["file"] == datei
    mine = (booth.assets_dir / datei).read_bytes()
    assert mine != roh and datei in booth.store.local["template_push"]

    galerie = Gallery(roh)                      # a heartbeat answer that still lists the old checksum
    booth.agent._ensure_asset(galerie, {"file": datei, "sha256": _sha(roh), "url": "/a/" + datei})
    assert galerie.downloads == []
    assert (booth.assets_dir / datei).read_bytes() == mine
    assert [t["file"] for t in booth.templates_to_push()] == [datei]
    assert booth.templates_to_push()[0]["template"]["name"] == "Abendvorlage (Box)", "the edit goes up, not the old copy"


def test_once_the_gallery_acknowledged_it_its_files_are_followed_again(client, booth):
    datei, roh = _gallery_file(booth)
    client.post("/api/admin/templates", json=dict(GALERIE, name="Abendvorlage (Box)"))
    mine = (booth.assets_dir / datei).read_bytes()
    booth.templates_acked([datei])
    galerie = Gallery(mine)
    booth.agent._ensure_asset(galerie, {"file": datei, "sha256": _sha(mine), "url": "/a/" + datei})
    assert galerie.downloads == [], "same checksum: nothing to fetch"
    # Edited later in the web admin: the box follows the gallery again.
    neu = mine + b" "
    galerie = Gallery(neu)
    booth.agent._ensure_asset(galerie, {"file": datei, "sha256": _sha(neu), "url": "/a/" + datei})
    assert galerie.downloads and (booth.assets_dir / datei).read_bytes() == neu


def test_other_files_still_come_from_the_gallery(booth):
    booth.store.save_local({"template_push": ["andere.json"]})
    galerie = Gallery(b"logo")
    booth.agent._ensure_asset(galerie, {"file": "logo.png", "sha256": _sha(b"logo"), "url": "/a/logo.png"})
    assert (booth.assets_dir / "logo.png").read_bytes() == b"logo"
