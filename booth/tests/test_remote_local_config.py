"""Job ``set_local_config``: the gallery may change camera, printer and storage
choices at the booth — and nothing that names a server, a key, a PIN, an
address or a path. The booth checks the list itself; it does not trust the
gallery's check.
"""
import ast
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture()
def booth(monkeypatch):
    d = Path(tempfile.mkdtemp(prefix="booth-remote-local-"))
    monkeypatch.setenv("BOOTH_DATA", str(d))
    monkeypatch.setenv("BOOTH_CAMERA", "mock")
    from boothcore.app import Booth
    b = Booth(base=d, start_threads=False)
    b.store.save_local({"admin_pin": "468213",
                        "server": {"url": "https://gallery.example", "api_key": "box-key"},
                        "storage": {"usb": {"enabled": False, "path": "E:/", "label": "STICK"}}})
    yield b
    b.mail.stop()


def _gallery_allowlist():
    """The gallery's list, read as text: the suites do not share an import path."""
    tree = ast.parse((ROOT / "app" / "ecosystem.py").read_text(encoding="utf-8"))
    for node in tree.body:
        target = node.target if isinstance(node, ast.AnnAssign) else (node.targets[0] if isinstance(node, ast.Assign) else None)
        if getattr(target, "id", "") == "REMOTE_LOCAL_KEYS":
            return ast.literal_eval(node.value)
    raise AssertionError("REMOTE_LOCAL_KEYS not found in app/ecosystem.py")


def test_booth_and_gallery_agree_on_the_list():
    from boothcore.config import REMOTE_LOCAL_KEYS
    assert REMOTE_LOCAL_KEYS == _gallery_allowlist()


def test_every_key_on_the_list_exists_in_the_local_settings():
    from boothcore.config import DEFAULT_LOCAL, REMOTE_LOCAL_KEYS
    for dotted in REMOTE_LOCAL_KEYS:
        node = DEFAULT_LOCAL
        if dotted.startswith("printers."):
            # A printer entry by number; every entry has the keys of "printer".
            assert dotted.split(".")[2] in DEFAULT_LOCAL["printer"], dotted
            continue
        for part in dotted.split("."):
            assert isinstance(node, dict) and part in node, dotted
            node = node[part]


def test_allowed_settings_arrive_and_neighbours_stay(booth):
    result = booth.agent.job_handlers["set_local_config"](
        {"camera.rotate": 90, "printer.backend": "none", "storage.usb.eject_hint": False})
    assert result.startswith("übernommen")
    local = booth.store.local
    assert local["camera"]["rotate"] == 90 and local["printer"]["backend"] == "none"
    assert local["storage"]["usb"]["eject_hint"] is False
    # The stick's path and label were not part of the job and are still there.
    assert local["storage"]["usb"]["path"] == "E:/" and local["storage"]["usb"]["label"] == "STICK"
    # And it is on disk, not only in memory.
    from boothcore.config import Store
    assert Store(booth.store.base).local["camera"]["rotate"] == 90


@pytest.mark.parametrize("payload", [{"server.url": "https://evil.example"}, {"server": {"url": "https://evil"}},
                                     {"admin_pin": "000000"}, {"camera.edsdk_dll": "C:/evil.dll"},
                                     {"storage.folder": "/etc"}, {"storage.usb.path": "/"},
                                     {"storage.usb.enabled": True}, {"storage.usb.originals": True},
                                     {"printer.uri": "ipp://evil"}, {"targets": []},
                                     {"camera.rotate": 45}, {"camera.rotate": True}, {"kiosk": "ja"}])
def test_anything_else_is_refused_by_the_booth_itself(booth, payload):
    before = dict(booth.store.local["server"]), booth.store.local["admin_pin"], booth.store.local["camera"]["rotate"]
    result = booth.agent.job_handlers["set_local_config"](payload)
    assert result.startswith("nichts übernommen")
    after = dict(booth.store.local["server"]), booth.store.local["admin_pin"], booth.store.local["camera"]["rotate"]
    assert before == after
    assert booth.store.local["storage"].get("folder", "") != "/etc"
    assert booth.store.local["camera"]["edsdk_dll"] == ""


def test_a_mixed_job_takes_the_good_and_names_the_rest(booth):
    result = booth.agent.job_handlers["set_local_config"]({"camera.mirror_preview": False,
                                                           "server.url": "https://evil.example"})
    assert "übernommen: camera.mirror_preview" in result and "abgelehnt" in result and "server.url" in result
    assert booth.store.local["server"]["url"] == "https://gallery.example"
