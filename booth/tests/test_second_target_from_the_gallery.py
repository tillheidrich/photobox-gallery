"""The second upload target set from the gallery, as the booth sees it (17.11.2026).

The operator wants to set "Zweites Ziel im Netz" (FTP/FTPS/SFTP) from
the gallery's page of the booth. The booth's side: no switch at the booth
any more (owner's decision, 17.11.2026 evening); one narrow job that changes
only the card's fields and checks them again; a password that arrives
sealed to a key whose private half never leaves this booth; a report and a
check result without the password. And the owner's rule for the target
itself: every file the booth keeps of the event goes there, and what was
there before can be sent once, for this event only.
"""
from __future__ import annotations

import base64
import json
import tempfile
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from _pages import page  # noqa: E402

WEB = Path(__file__).resolve().parents[1] / "boothcore" / "web"
ROOT = Path(__file__).resolve().parents[2]
DUMMY = "Dummy-Passwort-7f3a"          # an obvious test value, searched for everywhere below


@pytest.fixture()
def booth(monkeypatch):
    d = Path(tempfile.mkdtemp(prefix="booth-ziel2-"))
    monkeypatch.setenv("BOOTH_DATA", str(d))
    monkeypatch.setenv("BOOTH_CAMERA", "mock")
    from boothcore.app import Booth
    b = Booth(base=d, start_threads=False)
    b.store.save_local({"admin_pin": "468213", "server": {"url": "https://galerie.example.org", "api_key": "k" * 8}})
    yield b
    b.camera.stop()
    b.mail.stop()


def _seal(b, secret=DUMMY):
    from nacl.public import PublicKey, SealedBox
    rep = b.second_target.report()
    box = SealedBox(PublicKey(base64.b64decode(rep["public_key"]))).encrypt(secret.encode())
    return {"password_sealed": base64.b64encode(box).decode(), "key_id": rep["key_id"]}


def _target(**over):
    t = {"type": "ftps", "host": "ftp.example.org", "port": 0, "user": "demo", "path": "",
         "flat": True, "enabled": True, "originals": True, "print_sheets": False}
    t.update(over)
    return t


def _job(b, jtype, payload):
    """Through the agent, as the gallery's job arrives: (ok, result)."""
    b._test_job_id = getattr(b, "_test_job_id", 0) + 1         # a repeated id is answered from memory
    b.agent._run_job({"id": b._test_job_id, "type": jtype, "payload": payload})
    ack = b.agent._acks[-1]
    return ack["ok"], ack["result"]


# ── No switch at the booth ─────────────────────────────────

def test_a_paired_booth_takes_the_setting_from_the_gallery_without_a_switch(booth):
    assert "remote_targets" not in booth.store.local
    rep = booth.second_target.report()
    assert rep["public_key"] and rep["key_id"] and rep["allowed"] is True     # old galleries look for "allowed"
    ok, text = _job(booth, "set_second_target", {"v": 1, "target": _target(), **_seal(booth)})
    assert ok, text
    entry = next(t for t in booth.store.local["targets"] if t["name"] == "picdrop")
    assert entry["host"] == "ftp.example.org" and entry["password"] == DUMMY
    assert not entry.get("awaiting_password")
    for jt in ("check_second_target", "backfill_second_target"):
        ok, text = _job(booth, jt, {})
        assert "nicht erlaubt" not in text


def test_an_old_switch_left_in_local_json_changes_nothing(booth):
    booth.store.save_local({"remote_targets": {"allowed": False}})
    ok, text = _job(booth, "set_second_target", {"v": 1, "target": _target(), **_seal(booth)})
    assert ok, text
    assert "public_key" in booth.second_target.report()


def test_the_job_still_carries_nothing_but_the_target(booth):
    ok, text = _job(booth, "set_second_target", {"v": 1, "target": _target(), "remote_targets": {"allowed": True}})
    assert not ok and "remote_targets" in text
    from boothcore.config import REMOTE_LOCAL_KEYS
    assert not any(k.startswith(("targets", "second_target")) for k in REMOTE_LOCAL_KEYS)


# ── Set, keep, clear ───────────────────────────────────────

def test_a_sealed_password_is_opened_here_and_never_reported(booth):
    ok, text = _job(booth, "set_second_target", {"v": 1, "target": _target(path="/galerie/"), **_seal(booth)})
    assert ok, text
    assert DUMMY not in text
    entry = next(t for t in booth.store.local["targets"] if t["name"] == "picdrop")
    assert entry["password"] == DUMMY and entry["host"] == "ftp.example.org" and entry["path"] == "/galerie"
    rep = booth.second_target.report()
    assert rep["password_set"] is True and rep["configured"] and rep["host"] == "ftp.example.org"
    assert DUMMY not in json.dumps(rep)


def test_without_a_new_password_the_stored_one_stays_and_clear_removes_it(booth):
    assert _job(booth, "set_second_target", {"v": 1, "target": _target(), **_seal(booth)})[0]
    ok, text = _job(booth, "set_second_target", {"v": 1, "target": _target(path="neu/{jahr}", enabled=False)})
    assert ok and "unverändert" in text
    entry = next(t for t in booth.store.local["targets"] if t["name"] == "picdrop")
    assert entry["password"] == DUMMY and entry["path"] == "neu/{jahr}" and entry["enabled"] is False
    ok, _ = _job(booth, "set_second_target", {"v": 1, "action": "clear"})
    assert ok and not [t for t in booth.store.local["targets"] if t.get("name") == "picdrop"]
    assert booth.second_target.report()["configured"] is False


def test_the_older_card_name_is_the_same_target_and_other_targets_stay(booth):
    booth.store.save_local({"targets": [{"name": "zweites-ziel", "type": "sftp", "host": "nas.example.org",
                                          "user": "a", "password": "alt", "key_file": "C:/k"},
                                         {"name": "nas2", "type": "sftp", "host": "b.example.org", "user": "b"}]})
    assert _job(booth, "set_second_target", {"v": 1, "target": _target(type="sftp", host="nas.example.org",
                                                                         user="a")})[0]
    names = [t["name"] for t in booth.store.local["targets"]]
    assert names == ["zweites-ziel", "nas2"]
    entry = booth.store.local["targets"][0]
    assert entry["password"] == "alt" and entry["key_file"] == "C:/k"     # booth-only key file kept for SFTP


@pytest.mark.parametrize("over,word", [
    ({"host": "ftp://ftp.example.org"}, "Server"), ({"host": "ftp.example.org/pfad"}, "Server"),
    ({"host": "ftp.example.org:21"}, "Server"), ({"host": ""}, "Server"), ({"host": "a b.example.org"}, "Server"),
    ({"port": 70000}, "Port"), ({"port": -1}, "Port"), ({"port": True}, "Port"), ({"port": "21"}, "Port"),
    ({"user": "x\ny"}, "Steuerzeichen"), ({"path": "../etc"}, "„..“"), ({"path": "a/\x00"}, "Steuerzeichen"),
    ({"type": "http"}, "Art"), ({"user": "u" * 129}, "höchstens"), ({"flat": "ja"}, "flat"),
    ({"key_file": "C:/x"}, "key_file"), ({"name": "gallery"}, "name"),
])
def test_every_field_is_checked_again_at_the_booth(booth, over, word):
    ok, text = _job(booth, "set_second_target", {"v": 1, "target": _target(**over)})
    assert not ok and word in text, text


def test_a_password_sealed_for_another_key_is_refused_in_words(booth):
    sealed = _seal(booth)
    ok, text = _job(booth, "set_second_target", {"v": 1, "target": _target(), **sealed, "key_id": "000000000000"})
    assert not ok and "neu eingeben" in text
    ok, text = _job(booth, "set_second_target", {"v": 1, "target": _target(),
                                                 "password_sealed": base64.b64encode(b"x" * 60).decode()})
    assert not ok and "neu eingeben" in text


def test_the_ip_address_and_path_are_normalised(booth):
    from boothcore.second_target import clean_fields
    out = clean_fields(_target(host="192.0.2.10", path="\\a//b/./c/"))
    assert out["host"] == "192.0.2.10" and out["path"] == "/a/b/c"
    assert clean_fields(_target(path="a//b/"))["path"] == "a/b"
    assert clean_fields(_target(host="FTP.Example.org."))["host"] == "ftp.example.org"
    assert clean_fields(_target(host="2001:db8::1"))["host"] == "2001:db8::1"


# ── The private key stays here ─────────────────────────────

def test_the_private_key_is_in_no_page_no_heartbeat_and_not_in_local_json(booth):
    assert _job(booth, "set_second_target", {"v": 1, "target": _target(), **_seal(booth)})[0]
    private = booth.second_target.keys.path.read_text(encoding="ascii").strip()
    assert private and private not in booth.store.local_path.read_text(encoding="utf-8")
    from boothcore.app import create_app
    c = TestClient(create_app(booth))
    c.headers.update({"X-Admin-Token": c.post("/api/admin/login", json={"pin": "468213"}).json()["token"]})
    config = c.get("/api/admin/config").text
    status = json.dumps(booth._status_payload())
    queues = c.get("/api/admin/queues").text
    for text in (config, status, queues):
        assert private not in text and DUMMY not in text
    assert booth.second_target.keys.public_b64() in status        # the public half travels, by design


def test_the_dummy_password_is_nowhere_after_set_and_check(booth):
    """Job memory, booth log, log buffer for the gallery, queue, heartbeat: grep for it."""
    import logging
    assert _job(booth, "set_second_target", {"v": 1, "target": _target(host="127.0.0.1", port=1), **_seal(booth)})[0]
    ok, text = _job(booth, "check_second_target", {})
    assert not ok
    for h in logging.getLogger().handlers:
        h.flush()
    places = [json.dumps(booth._status_payload()), json.dumps(booth.logbuf.take(500)), text]
    for f in [booth.data / "logs" / "booth.log", *booth.data.rglob("jobs.json"), *booth.data.rglob("*.db")]:
        if f.is_file():
            places.append(f.read_bytes().decode("utf-8", "replace"))
    for p in places:
        assert DUMMY not in p


# ── Check ──────────────────────────────────────────────────

def test_the_check_answers_in_plain_words_and_is_kept_for_the_report(booth):
    assert _job(booth, "set_second_target", {"v": 1, "target": _target(host="127.0.0.1", port=1), **_seal(booth)})[0]
    ok, text = _job(booth, "check_second_target", {})
    assert not ok and ("Server" in text or "Verbindung" in text), text
    check = booth.second_target.report()["check"]
    assert check["ok"] is False and check["message"] and check["at"] > 0


def test_the_audit_line_reaches_the_gallery_log_without_the_secret(booth):
    assert _job(booth, "set_second_target", {"v": 1, "target": _target(), **_seal(booth)})[0]
    lines = [e["message"] for e in booth.logbuf.take(50)]
    assert any("Zweites Ziel aus der Galerie geändert" in m and "Passwort neu" in m for m in lines)
    assert not any(DUMMY in m for m in lines)


# ── Everything flows to the second target ─────────────────

def _run(b, mode="photo", tpl=None):
    if not getattr(b, "_test_camera_on", False):
        b.camera.start()
        b._test_camera_on = True
    for _ in range(50):
        if b.camera.state == "ready":
            break
        time.sleep(0.1)
    s = b.start_session(mode, "4:5", tpl, None)
    for _ in range(max(1, s.shots_needed)):
        b.take_shot(s)
    b.finalize(s, "original")
    if mode == "gif":
        b.wait_for_films(s)
    return s


def _rows(b, target):
    return {(r["uid"], r["kind"], r["variant"]) for r in b.outbox.due(500) if r["target"] == target}


def test_the_second_target_gets_sheet_singles_and_originals_whatever_the_gallery_shows(booth):
    booth.store.profile["collage_upload"] = "photos"        # the gallery shows the singles only
    booth.store.profile["upload_originals"] = False          # and no camera originals
    booth.store.save_local({"targets": [{"name": "picdrop", **_target()}]})
    s = _run(booth, "collage", "grid4")
    gallery, second = _rows(booth, "gallery"), _rows(booth, "picdrop")
    assert (s.uid, "collage", "web") not in gallery and (s.uid, "collage", "web") in second
    assert (s.uid, "collage", "original") in second
    assert sum(1 for u, k, v in second if k == "photo") == 2 * s.shots_needed      # each single, web + full
    assert sum(1 for u, k, v in second if k == "original") == s.shots_needed       # camera originals
    assert not any(k == "original" for u, k, v in gallery)
    assert not any(v == "print" for u, k, v in second)                              # print sheets: off by default


def test_gif_films_boomerang_and_print_sheets_reach_it(booth):
    booth.store.profile.setdefault("gif", {}).setdefault("boomerang", {})["enabled"] = True
    booth.store.save_local({"targets": [{"name": "picdrop", **_target(print_sheets=True)}]})
    s = _run(booth, "gif")
    second = _rows(booth, "picdrop")
    assert (s.uid, "gif", "web") in second and (f"{s.uid}.mp4", "video", "web") in second
    if s.boom:
        assert (f"{s.uid}.boom", "boomerang", "web") in second
    s2 = _run(booth, "photo")
    if s2.print_file and Path(s2.print_file) != Path(s2.result):
        assert (f"{s2.uid}.print", "print", "print") in _rows(booth, "picdrop")
        assert not any(v == "print" for u, k, v in _rows(booth, "gallery"))


def test_videos_and_boomerangs_reach_it(booth):
    booth.store.save_local({"targets": [{"name": "picdrop", **_target()}]})
    from boothcore.session import Session
    folder = booth.sessions_dir / "v1"
    folder.mkdir(parents=True)
    clip = folder / "clip.mp4"
    clip.write_bytes(b"mp4")
    s = Session(uid="v1", mode="boomerang", fmt="9:16", folder=folder, event_slug="fest", event_name="Fest",
                result=clip)
    booth._enqueue(s)
    assert ("v1", "boomerang", "web") in _rows(booth, "picdrop")


# ── Backfill ───────────────────────────────────────────────

def test_backfill_sends_this_events_earlier_runs_once_and_never_by_itself(booth):
    booth.store.profile["event"] = {**(booth.store.profile.get("event") or {}), "slug": "sommerfest"}
    booth._event_slug = "sommerfest"
    s = _run(booth, "photo")
    assert s.event_slug == "sommerfest"
    booth.store.save_local({"targets": [{"name": "picdrop", **_target()}]})
    assert not _rows(booth, "picdrop")                       # switching on queues nothing by itself
    n = booth.second_target.backfill()
    assert n >= 2 and (s.uid, "photo", "web") in _rows(booth, "picdrop")
    assert booth.second_target.backfill() == 0               # a second tap adds nothing
    # A run of another event stays where it is.
    booth.outbox.add(target="gallery", uid="fremd", session_uid="fremd", event_slug="anderes", kind="photo",
                     variant="web", path=s.result)
    booth.second_target.backfill()
    assert not any(u == "fremd" for u, k, v in _rows(booth, "picdrop"))


def test_backfill_needs_an_enabled_target_and_works_from_the_gallery(booth):
    with pytest.raises(ValueError):
        booth.second_target.backfill()
    booth.store.save_local({"targets": [{"name": "picdrop", **_target(enabled=False)}]})
    with pytest.raises(ValueError):
        booth.second_target.backfill()
    ok, text = _job(booth, "backfill_second_target", {})
    assert not ok and "nicht eingeschaltet" in text            # the reason, not a refusal of the gallery


# ── The booth admin ────────────────────────────────────────

def test_the_admin_card_has_no_switch_but_the_choices_the_path_rule_and_the_backfill(booth):
    html = page(WEB / "admin.html")
    for feld in ('id="zt_originals"', 'id="zt_prints"', 'id="zt_backfill"', 'id="qTargets"', 'id="zt_path_example"'):
        assert feld in html, feld
    assert 'id="zt_remote"' not in html
    assert "Zweites Ziel aus der Galerie einstellen lassen" not in html
    assert "Bisherige Aufnahmen dieses Events nachsenden" in html
    assert "{event}" in html and "{datum}" in html and "„..“" in html
    js = (WEB / "static" / "admin-page.js").read_text(encoding="utf-8")
    assert "remote_targets" not in js and "/api/admin/targets/backfill" in js and "ordnerBeispiel" in js


def test_the_admin_counts_per_target(booth):
    from boothcore.app import create_app
    c = TestClient(create_app(booth))
    assert c.post("/api/admin/targets/backfill").status_code in (401, 403)
    c.headers.update({"X-Admin-Token": c.post("/api/admin/login", json={"pin": "468213"}).json()["token"]})
    booth.store.save_local({"targets": [{"name": "picdrop", **_target()}]})
    _run(booth, "photo")
    q = c.get("/api/admin/queues").json()
    assert q["targets"]["picdrop"]["pending"] >= 1 and q["targets"]["gallery"]["pending"] >= 1
    r = c.post("/api/admin/targets/backfill")
    assert r.status_code in (200, 409)


def test_the_booth_and_the_gallery_check_the_same_fields_and_limits():
    import ast
    from boothcore import second_target as here
    tree = ast.parse((ROOT / "app" / "second_target.py").read_text(encoding="utf-8"))
    there = {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name):
            if node.targets[0].id in ("TYPES", "FIELDS", "LIMITS", "NAMES"):
                there[node.targets[0].id] = ast.literal_eval(node.value)
    assert there == {"TYPES": here.TYPES, "FIELDS": here.FIELDS, "LIMITS": here.LIMITS, "NAMES": here.NAMES}


# ── Direct upload for live-push accounts (rule L-S5, 18.11.2026) ─────────

def test_direct_is_taken_from_the_job_reported_and_off_when_the_job_leaves_it_out(booth):
    from boothcore.second_target import find
    ok, text = _job(booth, "set_second_target", {"v": 1, "target": _target(direct=True), **_seal(booth)})
    assert ok, text
    _, entry = find(booth.store.local)
    assert entry["direct"] is True and booth.second_target.report()["direct"] is True
    assert any("direct" in e["message"] for e in booth.logbuf.take(50))
    # A gallery sends "direct" only while it is on: a job without it switches it off.
    ok, text = _job(booth, "set_second_target", {"v": 1, "target": _target()})
    assert ok, text
    _, entry = find(booth.store.local)
    assert entry["direct"] is False and booth.second_target.report()["direct"] is False
    ok, text = _job(booth, "set_second_target", {"v": 1, "target": _target(direct="ja")})
    assert not ok and "direct" in text


def test_the_admin_card_offers_direct_upload_with_a_line_of_help():
    html = page(WEB / "admin.html")
    assert 'id="zt_direct"' in html and "Direkt hochladen, ohne Zwischennamen" in html
    js = (WEB / "static" / "admin-page.js").read_text(encoding="utf-8")
    assert '$("zt_direct").checked = !!(zweites && zweites.direct)' in js
    assert 'direct: $("zt_direct").checked' in js


def test_a_refused_final_name_is_said_as_the_account_refusing_it():
    from boothcore.app import ziel_fehler_text
    raw = "Das Konto auf dem Server nimmt die Datei a.heic nicht an: … (550 Permission denied.)"
    assert ziel_fehler_text(raw) == raw
    assert "nicht beschreibbar" in ziel_fehler_text("550 Permission denied.")
