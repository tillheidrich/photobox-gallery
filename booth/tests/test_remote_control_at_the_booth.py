"""Job ``remote_control``: the gallery asks, this booth decides.

Off by default; only the fixed list of actions; the event must match; the
kiosk's own methods and guards (lock, one run at a time, allowances). With
the mock camera and the folder printer: a whole run from start to delete.
"""
import ast
import base64
import io
import json
import tempfile
from pathlib import Path

import pytest
from _pages import page  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture()
def booth(monkeypatch):
    d = Path(tempfile.mkdtemp(prefix="booth-remote-control-"))
    monkeypatch.setenv("BOOTH_DATA", str(d))
    monkeypatch.setenv("BOOTH_CAMERA", "mock")
    from boothcore.app import Booth
    b = Booth(base=d, start_threads=False)
    b.camera.start()
    import time
    for _ in range(100):
        if b.camera.state == "ready" and b.camera.latest_frame(-1, 0.1)[0]:
            break
        time.sleep(0.1)
    b.store.save_local({"admin_pin": "468213"})
    yield b
    b.camera.stop()
    b.mail.stop()


def _allow(b, on=True):
    b.store.save_local({"remote_control": {"allowed": on, "indicator": True}})


def _job(b, action, **args):
    return json.loads(b.agent.job_handlers["remote_control"]({"action": action, "args": args}))


def _gallery_actions():
    tree = ast.parse((ROOT / "app" / "remote_control.py").read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.Assign) and getattr(node.targets[0], "id", "") == "ACTIONS":
            return set(ast.literal_eval(node.value))
    raise AssertionError("ACTIONS not found in app/remote_control.py")


def test_booth_and_gallery_know_the_same_actions():
    from boothcore.fernsteuerung import ACTIONS
    assert ACTIONS == _gallery_actions()


def test_off_by_default_and_refused(booth):
    from boothcore.config import DEFAULT_LOCAL
    assert DEFAULT_LOCAL["remote_control"]["allowed"] is False
    with pytest.raises(Exception, match="nicht erlaubt"):
        _job(booth, "status")
    # Through the agent the refusal is the job's answer, and nothing ran.
    booth.agent._run_job({"id": 901, "type": "remote_control", "payload": {"action": "lock"}})
    ack = [a for a in booth.agent._acks if a["id"] == 901][0]
    assert ack["ok"] is False and "nicht erlaubt" in ack["result"]
    assert booth.remote_locked is False


def test_the_gallery_cannot_switch_it_on(booth):
    from boothcore.config import REMOTE_LOCAL_KEYS
    assert not [k for k in REMOTE_LOCAL_KEYS if k.startswith("remote_control")]
    try:
        booth.agent.job_handlers["set_local_config"]({"remote_control.allowed": True})
    except Exception:                                   # refused either way: by message or by error
        pass
    assert not (booth.store.local.get("remote_control") or {}).get("allowed")


def test_unknown_actions_and_foreign_events_are_refused(booth):
    _allow(booth)
    with pytest.raises(Exception, match="Unbekannte Aktion"):
        _job(booth, "rm -rf")
    booth.profile["event"]["slug"] = "unsere-feier"
    with pytest.raises(Exception, match="anderen Event"):
        booth.agent.job_handlers["remote_control"]({"action": "status", "event": "fremde-feier"})
    assert _job(booth, "status")["event"] == "unsere-feier"


def test_a_whole_run_from_start_to_print_to_delete(booth):
    _allow(booth)
    assert booth.camera.state == "ready"
    started = _job(booth, "start_run", mode="photo")
    uid = started["uid"]
    # One run at a time: a second start is refused while this one is open.
    with pytest.raises(Exception, match="läuft schon"):
        _job(booth, "start_run", mode="photo")
    shot = _job(booth, "shot", uid=uid)
    assert shot["shot"] == 1
    done = _job(booth, "finish", uid=uid)
    assert done["uid"] == uid
    booth.profile["print"]["enabled"] = True
    printed = _job(booth, "print", uid=uid, copies=1)
    assert printed
    assert uid in [r["uid"] for r in _job(booth, "list_runs")["runs"]]
    # Not while its print is on its way (final review A10: delete_run waits
    # like restarting the PC does).
    import time
    for _ in range(150):
        if not booth.printer.pending:
            break
        time.sleep(0.1)
    assert _job(booth, "delete_run", uid=uid) == {"deleted": uid}
    assert booth.library.get(uid) is None


def test_run_in_one_go(booth):
    _allow(booth)
    out = _job(booth, "run", mode="collage")
    assert out["uid"] and booth.library.get(out["uid"])


def test_a_locked_booth_starts_nothing(booth):
    _allow(booth)
    _job(booth, "lock")
    assert booth.locked
    with pytest.raises(Exception, match="gesperrt"):
        _job(booth, "start_run", mode="photo")
    with pytest.raises(Exception, match="gesperrt"):
        _job(booth, "trigger", mode="photo")
    _job(booth, "unlock")
    assert _job(booth, "trigger", mode="photo")["triggered"] == "photo"
    assert booth.take_trigger()["mode"] == "photo"


def test_live_image_is_a_small_jpeg(booth):
    from PIL import Image
    _allow(booth)
    out = _job(booth, "live_image")
    im = Image.open(io.BytesIO(base64.b64decode(out["image_base64"])))
    assert im.format == "JPEG" and max(im.size) <= 640


def test_screenshot_uses_the_window_module(booth, monkeypatch):
    from PIL import Image
    from boothcore import window as window_mod
    _allow(booth)

    def fake(target, grab=None):
        Image.new("RGB", (1920, 1080), (10, 20, 30)).save(target, "PNG")
        return {"file": str(target), "width": 1920, "height": 1080}
    monkeypatch.setattr(window_mod, "screenshot", fake)
    out = _job(booth, "screenshot")
    assert max(Image.open(io.BytesIO(base64.b64decode(out["image_base64"]))).size) <= 640


def test_quit_goes_through_the_operators_quit(booth, monkeypatch):
    _allow(booth)
    called = []
    monkeypatch.setattr(booth, "quit_on_purpose", lambda delay=1.0: called.append(delay) or "Beenden")
    assert _job(booth, "quit") == "Beenden" and called


def test_the_kiosk_shows_the_mark_only_while_driven_and_when_wanted(booth):
    assert booth.fernsteuerung.kiosk_state()["active"] is False
    _allow(booth)
    _job(booth, "status")
    assert booth.fernsteuerung.kiosk_state() == {"active": True, "indicator": True}
    booth.store.save_local({"remote_control": {"allowed": True, "indicator": False}})
    assert booth.fernsteuerung.kiosk_state()["indicator"] is False


def test_the_admin_page_has_the_switch():
    html = page(ROOT / "booth" / "boothcore" / "web" / "admin.html")
    assert "Fernsteuerung durch die Galerie erlauben" in html and 'id="rc_allowed"' in html
    assert "remote_control: { allowed:" in html
