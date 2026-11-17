"""The stick backup, as the operator meets it at the booth.

"Man kann den unten an der Box anschließen, das muss relativ selbsterklärend
sein" (14.11.2026). And an hour later: it has to run all day by itself, and
the controls belong into the admin settings only — "sonst können normale
Besucher da Blödsinn machen". So: nothing about the stick on the guests'
screen, one switch and two buttons in the admin, no paths to type.
"""
import tempfile
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from _pages import page  # noqa: E402

WEB = Path(__file__).resolve().parent.parent / "boothcore" / "web"


def _run(root, event, uid):
    d = root / "sessions" / event / "2026-11-18" / uid
    d.mkdir(parents=True)
    for name in ("result.jpg", "print.jpg", "shot1.jpg", "thumb.jpg"):
        (d / name).write_bytes(b"x" * 2000)


@pytest.fixture()
def booth(monkeypatch, tmp_path):
    d = Path(tempfile.mkdtemp(prefix="booth-usb-"))
    monkeypatch.setenv("BOOTH_DATA", str(d))
    monkeypatch.setenv("BOOTH_CAMERA", "mock")
    monkeypatch.setenv("BOOTH_USB_ROOTS", str(tmp_path / "vol"))
    from boothcore import app as app_mod
    monkeypatch.setattr(app_mod, "_WRONG_PIN_PAUSE", 0.0)
    b = app_mod.Booth(base=d, start_threads=False)
    b.store.save_local({"admin_pin": "468213"})
    b.profile["event"]["slug"] = "mara-jonas"
    _run(d, "mara-jonas", "aaa111")
    _run(d, "mara-jonas", "bbb222")
    _run(d, "sommerfest", "ccc333")
    b._vol = tmp_path / "vol"
    yield b
    b.mail.stop()


@pytest.fixture()
def client(booth):
    from boothcore.app import create_app
    return TestClient(create_app(booth))


@pytest.fixture()
def admin(client):
    token = client.post("/api/admin/login", json={"pin": "468213"}).json()["token"]
    client.headers.update({"X-Admin-Token": token})
    return client


def _plug(booth, name="STICK", trust=True):
    (booth._vol / name).mkdir(parents=True)
    booth._usb_seen = (0.0, [])                       # look again now, not in three seconds
    if trust:                                         # confirmed at the box with the PIN (S-7)
        booth.usb_trust(str(booth._vol / name))
    return booth._vol / name


def _finish(admin):
    for _ in range(200):
        job = admin.get("/api/admin/usb").json()["job"]
        if job and job["state"] in ("done", "error"):
            return job
        time.sleep(0.02)
    raise AssertionError("backup never finished")


def test_the_guests_screen_learns_nothing_about_the_stick(booth, client):
    _plug(booth)
    assert "usb" not in client.get("/api/state").json()


def test_the_backup_needs_the_pin(booth, client):
    _plug(booth)
    assert client.get("/api/admin/usb").status_code == 401
    assert client.post("/api/admin/usb/backup", json={"scope": "all"}).status_code == 401


def test_the_operator_sees_what_would_be_saved(booth, admin):
    _plug(booth)
    d = admin.get("/api/admin/usb").json()
    assert d["drives"][0]["label"] == "STICK"
    assert d["current"] == {"slug": "mara-jonas", "runs": 2}
    assert d["all_runs"] == 3


def test_this_event_goes_onto_the_stick(booth, admin):
    stick = _plug(booth)
    r = admin.post("/api/admin/usb/backup", json={"scope": "event"})
    assert r.status_code == 200, r.text
    job = _finish(admin)
    assert job["state"] == "done" and job["runs"] == 2
    assert (stick / "Fotobox-Sicherung" / "mara-jonas" / "2026-11-18" / "aaa111" / "result.jpg").is_file()
    assert not (stick / "Fotobox-Sicherung" / "sommerfest").exists()


def test_all_events_go_onto_the_stick(booth, admin):
    stick = _plug(booth)
    admin.post("/api/admin/usb/backup", json={"scope": "all"})
    assert _finish(admin)["runs"] == 3
    assert (stick / "Fotobox-Sicherung" / "sommerfest").is_dir()


def test_a_deleted_and_a_print_only_run_stay_off_the_stick(booth, admin):
    stick = _plug(booth)
    f = Path("x.jpg")
    booth.library.upsert(uid="aaa111", event_slug="mara-jonas", mode="photo", result=f, thumb=None,
                         print_file=None, share_url="", look="")
    booth.library.upsert(uid="bbb222", event_slug="mara-jonas", mode="photo", result=f, thumb=None,
                         print_file=None, share_url="", look="", consent={"option": "print"})
    booth.library.delete("aaa111", slip=False)
    admin.post("/api/admin/usb/backup", json={"scope": "all"})
    job = _finish(admin)
    assert job["runs"] == 1
    assert not list((stick / "Fotobox-Sicherung").rglob("aaa111"))
    assert not list((stick / "Fotobox-Sicherung").rglob("bbb222"))


def test_without_a_stick_the_answer_is_a_sentence(booth, admin):
    r = admin.post("/api/admin/usb/backup", json={"scope": "all"})
    assert r.status_code == 409
    assert "Stick" in r.json()["detail"]


def test_only_a_plugged_in_stick_can_be_the_target(booth, admin, tmp_path):
    _plug(booth)
    r = admin.post("/api/admin/usb/backup", json={"scope": "all", "drive": str(tmp_path)})
    assert r.status_code == 409, "an arbitrary folder is not a stick"


# ── All day, by itself ────────────────────────────────────────────────

def _wait_job(booth):
    for _ in range(200):
        if booth.usb_job and booth.usb_job.status()["state"] in ("done", "error"):
            return booth.usb_job.status()
        time.sleep(0.02)
    raise AssertionError("backup never finished")


def _switch_on(booth, **more):
    usb = dict((booth.store.local.get("storage") or {}).get("usb") or {})
    booth.store.save_local({"storage": {"usb": {**usb, "enabled": True, **more}}})


def test_switched_on_every_new_run_reaches_the_stick_by_itself(booth):
    stick = _plug(booth)
    _switch_on(booth)
    assert booth.usb_auto_tick() is True
    assert _wait_job(booth)["state"] == "done"
    assert (stick / "Fotobox-Sicherung" / "mara-jonas" / "2026-11-18" / "aaa111" / "result.jpg").is_file()
    # nothing new: no second run right away
    assert booth.usb_auto_tick() is False
    # a new run comes in
    _run(booth.data, "mara-jonas", "neu999")
    booth.usb_mark_dirty()
    assert booth.usb_auto_tick() is True
    _wait_job(booth)
    assert (stick / "Fotobox-Sicherung" / "mara-jonas" / "2026-11-18" / "neu999" / "result.jpg").is_file()


def test_switched_off_nothing_is_copied(booth):
    stick = _plug(booth)
    assert booth.usb_auto_tick() is False
    assert not (stick / "Fotobox-Sicherung").exists()


def test_a_stick_plugged_in_later_is_filled_from_the_start(booth):
    _switch_on(booth)
    assert booth.usb_auto_tick() is False, "no stick, nothing to do — and no error either"
    stick = _plug(booth)
    assert booth.usb_auto_tick() is True
    _wait_job(booth)
    assert (stick / "Fotobox-Sicherung" / "mara-jonas").is_dir()


def test_a_swapped_stick_gets_everything_again(booth):
    _plug(booth, "EINS")
    _switch_on(booth)
    booth.usb_auto_tick(); _wait_job(booth)
    import shutil
    shutil.rmtree(booth._vol / "EINS")
    zwei = _plug(booth, "ZWEI")
    assert booth.usb_auto_tick() is True
    _wait_job(booth)
    assert (zwei / "Fotobox-Sicherung" / "mara-jonas" / "2026-11-18" / "bbb222").is_dir()


def test_without_single_shots_only_the_finished_pictures_go(booth):
    stick = _plug(booth)
    _switch_on(booth, originals=False)
    booth.usb_auto_tick(); _wait_job(booth)
    names = {p.name for p in (stick / "Fotobox-Sicherung").rglob("*.jpg")}
    assert "result.jpg" in names and "shot1.jpg" not in names


def test_before_the_first_picture_nothing_is_an_error(booth, admin):
    """Seen in the first soak run: switched on in the morning, the booth
    logged a warning every minute and the admin showed "Fehler" — because
    nobody had taken a picture yet."""
    import shutil
    shutil.rmtree(booth.sessions_dir)
    booth.sessions_dir.mkdir()
    _plug(booth)
    _switch_on(booth)
    assert booth.usb_auto_tick() is False
    assert booth.usb_job is None
    assert admin.get("/api/admin/usb").json()["auto"]["error"] == ""
    r = admin.post("/api/admin/usb/backup", json={"scope": "all"})
    assert r.status_code == 409 and "noch keine Aufnahmen" in r.json()["detail"]


def test_a_typed_folder_keeps_the_older_file_by_file_copy_and_nothing_doubles(booth, tmp_path):
    """With a folder typed in, the sync agent already copies every file there
    as it is made (target "usb"). The backup by run must not write the same
    pictures a second time next to it."""
    _plug(booth)
    ziel = tmp_path / "eigener-ordner"; ziel.mkdir()
    _switch_on(booth, path=str(ziel))
    assert booth.usb_auto_tick() is False
    assert not (ziel / "Fotobox-Sicherung").exists()


def test_the_admin_sees_when_it_last_worked_and_what_went_wrong(booth, admin, monkeypatch):
    from boothcore import usb_backup
    _plug(booth)
    _switch_on(booth)
    booth.usb_auto_tick(); _wait_job(booth)
    d = admin.get("/api/admin/usb").json()
    assert d["auto"]["enabled"] is True and d["auto"]["last_ok"] > 0 and d["auto"]["error"] == ""
    monkeypatch.setattr(usb_backup, "_free_bytes", lambda _p: 10)
    _run(booth.data, "mara-jonas", "voll123")
    booth.usb_mark_dirty()
    booth.usb_auto_tick(); _wait_job(booth)
    booth.usb_auto_tick()                                 # the loop reads the result
    assert "Platz" in admin.get("/api/admin/usb").json()["auto"]["error"]


def test_a_finished_run_marks_the_stick_as_behind():
    src = (WEB.parent / "app.py").read_text(encoding="utf-8")
    i = src.index("self._library_upsert(s, look)")
    assert "usb_mark_dirty()" in src[i:i + 900], "the run just saved has to reach the stick"
    # the films of a GIF run are saved later, on their own thread: they too
    j = src.index("self._enqueue(s, films_only=True)")
    assert "usb_mark_dirty()" in src[j:j + 300], "the films of a GIF run have to reach the stick"


# ── Where the controls are ────────────────────────────────────────────

def test_the_guests_screen_carries_no_stick_controls():
    html = (WEB / "index.html").read_text(encoding="utf-8")
    js = (WEB / "static" / "app.js").read_text(encoding="utf-8")
    i18n = (WEB / "static" / "i18n.js").read_text(encoding="utf-8")
    assert "usb" not in html.lower()
    assert "/api/admin/usb" not in js and "menu-usb" not in js
    assert '"usb.' not in i18n and '"menu.usb' not in i18n


def test_the_admin_page_carries_them():
    html = page(WEB / "admin.html")
    for needle in ('id="usb_enabled"', 'id="usbStand"', 'id="usbJetzt"', 'id="usbAlle"',
                   "/api/admin/usb/backup", "Laufend auf den USB-Stick sichern"):
        assert needle in html, needle


# ── seen on the booth PC, 15.11.2026 ────────────────────────────────────
def test_a_stick_that_drops_out_for_one_look_is_not_copied_again(booth, caplog):
    """"USB-Sicherung gestartet" every 15–30 s with nothing new: a stick that
    vanished from the drive list for one look came back as a "new" stick, and
    every reason to look started a full pass. Now a pass starts only when it
    would change something on the stick."""
    import logging
    stick = _plug(booth)
    _switch_on(booth)
    assert booth.usb_auto_tick() is True
    _wait_job(booth)
    booth.usb_job = None
    caplog.clear()
    with caplog.at_level(logging.INFO, logger="booth"):
        for _ in range(3):
            booth._usb_auto_stick = ""                      # the flicker
            booth.usb_mark_dirty()                          # and any other reason to look
            booth._usb_auto_at = 0.0                        # including the ten quiet minutes
            assert booth.usb_auto_tick() is False
    assert booth.usb_job is None
    assert not [r for r in caplog.records if "USB-Sicherung gestartet" in r.getMessage()]
    # something new still goes at once
    _run(booth.data, "mara-jonas", "neu777")
    booth.usb_mark_dirty()
    assert booth.usb_auto_tick() is True
    _wait_job(booth)
    assert (stick / "Fotobox-Sicherung" / "mara-jonas" / "2026-11-18" / "neu777" / "result.jpg").is_file()


def test_a_run_deleted_during_the_backup_is_skipped_not_the_whole_backup(booth, monkeypatch):
    """"USB-Sicherung abgebrochen: No such file … result.mp4": the guest
    deleted a run while the backup copied the evening."""
    import shutil
    from boothcore import usb_backup
    stick = _plug(booth)
    real = shutil.copyfile
    weg = booth.data / "sessions" / "mara-jonas" / "2026-11-18" / "aaa111"

    def copy(src, dst, *a, **k):
        if Path(src).parent == weg and weg.exists():
            shutil.rmtree(weg)                               # deleted right now
        return real(src, dst, *a, **k)
    monkeypatch.setattr(usb_backup.shutil, "copyfile", copy)
    job = usb_backup.start(booth.sessions_dir, stick, ["mara-jonas"])
    for _ in range(200):
        if job.status()["state"] != "running":
            break
        time.sleep(0.02)
    st = job.status()
    assert st["state"] == "done", st
    assert (stick / "Fotobox-Sicherung" / "mara-jonas" / "2026-11-18" / "bbb222" / "result.jpg").is_file()
