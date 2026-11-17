"""Photobox Sync opens target passwords the gallery sealed to it; its private key stays here (17.11.2026)."""
from __future__ import annotations

import base64
import json
import os

import pytest

from photobox_sync.sealing import SealKeys

GEHEIM = "Dummy-Zielpasswort-91c2"


def _seal(public_b64: str, text: str) -> str:
    from nacl.public import PublicKey, SealedBox
    return base64.b64encode(SealedBox(PublicKey(base64.b64decode(public_b64))).encrypt(text.encode())).decode()


def test_the_key_is_made_once_kept_private_and_only_its_public_half_is_reported(tmp_path):
    keys = SealKeys(tmp_path)
    rep = keys.report()
    privat = (tmp_path / "keys" / "sync_targets.key").read_text()
    assert privat not in json.dumps(rep) and len(base64.b64decode(rep["public_key"])) == 32
    if os.name != "nt":
        assert oct((tmp_path / "keys" / "sync_targets.key").stat().st_mode & 0o777) == "0o600"
    assert SealKeys(tmp_path).report() == rep, "the same key after a restart"


def test_a_sealed_password_opens_here_and_nowhere_else(tmp_path):
    keys = SealKeys(tmp_path / "a")
    box = _seal(keys.public_b64(), GEHEIM)
    assert keys.open(box, keys.key_id()) == GEHEIM
    with pytest.raises(ValueError, match="anderen Schlüssel"):
        keys.open(box, "000000000000")
    with pytest.raises(ValueError, match="neu eingeben"):
        SealKeys(tmp_path / "b").open(box)
    with pytest.raises(ValueError):
        keys.open(_seal(keys.public_b64(), "a\x00b"))


def test_the_program_opens_the_job_and_never_answers_the_password(tmp_path):
    from photobox_sync.standalone import SyncApp
    app = SyncApp(base=tmp_path / "pc", start_threads=False)
    rep = app.report()["sealing"]
    antwort = app.agent.job_handlers["set_sync_config"]({"targets": [
        {"type": "ftps", "name": "picdrop", "host": "ftp.example.org", "user": "u",
         "password_sealed": _seal(rep["public_key"], GEHEIM), "key_id": rep["key_id"]}]})
    assert GEHEIM not in antwort
    t = app.config["targets"][0]
    assert t["password"] == GEHEIM and "password_sealed" not in t and "key_id" not in t
    assert GEHEIM not in json.dumps(app.report())
