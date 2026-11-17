"""MCP coverage batch (17.11.2026): run and supervise an event remotely.

The owner's rule is "everything must be controllable via MCP". This batch
adds the dashboard, acknowledging booth messages, a backup on demand, the
booth's queues, all gallery settings, the remaining event fields, and makes
profile answers compact. Every new tool is refused without its right, the
ones that act are two-step where the admin asks twice, no answer carries a
secret, and an unknown key is reported instead of vanishing.
"""
import json
import secrets
from types import SimpleNamespace

import pytest


@pytest.fixture()
def door(client):
    client.environ_base["REMOTE_ADDR"] = f"10.{secrets.randbelow(250)}.{secrets.randbelow(250)}.{secrets.randbelow(250) + 1}"
    client.get("/healthz")
    from app import ecosystem
    ecosystem.init_schema()

    def token(scope, name="Abdeckung"):
        from app import accounts, db, oauth
        # One registered client for the whole file, a grant family per token:
        # the gallery caps registered clients (oauth.MAX_CLIENTS) and the
        # OAuth tests register their own after this file.
        cid = "pbc_coverage"
        con = db.get_db()
        con.execute("INSERT OR IGNORE INTO oauth_clients (client_id, name, redirect_uris, consented_at) "
                    "VALUES (?,?,?,datetime('now'))", (cid, name, json.dumps(["https://claude.ai/api/mcp/auth_callback"])))
        answer = oauth._issue(con, cid, secrets.token_hex(6), scope, "http://localhost/mcp", accounts.session_epoch())
        con.commit()
        con.close()
        return answer["access_token"]

    tokens = {}

    def call(scope, tool_name, **arguments):
        # One token per set of rights: a confirmation is bound to the access that asked.
        if scope not in tokens:
            tokens[scope] = token(scope)
        return client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                                         "params": {"name": tool_name, "arguments": arguments}},
                           headers={"Authorization": f"Bearer {tokens[scope]}"})

    def result(scope, tool_name, **arguments):
        r = call(scope, tool_name, **arguments)
        assert r.status_code == 200, (tool_name, r.status_code, r.get_data(as_text=True)[:300])
        return r.get_json()["result"]

    def data(scope, tool_name, **arguments):
        res = result(scope, tool_name, **arguments)
        assert not res.get("isError"), res
        return json.loads(res["content"][0]["text"])

    def text(scope, tool_name, **arguments):
        return result(scope, tool_name, **arguments)["content"][0]["text"]

    return SimpleNamespace(call=call, result=result, data=data, text=text)


def _booth(name="Abdeckungs-Box", status=None):
    from app import db
    dev = db.create_device(name=f"{name} {secrets.token_hex(2)}")
    if status is not None:
        con = db.get_db()
        con.execute("UPDATE devices SET status_json=?, last_heartbeat_at=datetime('now') WHERE id=?",
                    (json.dumps(status), dev["id"]))
        con.commit()
        con.close()
    return dev


def _event(**fields):
    from app import db
    k = secrets.token_hex(3)
    eid = db.create_event(name=f"Abdeckung {k}", slug=f"ab-{k}", upload_token=f"tok-{k}-{secrets.token_hex(8)}")
    if fields:
        db.update_event(eid, fields)
    return eid


def _log_error(device_id, message="Drucker meldet Papierstau"):
    from app import db
    con = db.get_db()
    con.execute("INSERT INTO device_logs (device_id, level, code, message, occurred_at) "
                "VALUES (?, 'error', 'printer', ?, datetime('now'))", (device_id, message))
    con.commit()
    con.close()


def _latest_log():
    from app import protokoll
    return protokoll.liste(limit=1)[0]


# ── Rights: every new tool is refused without its right ──────────────

@pytest.mark.parametrize("tool,refused,allowed", [
    ("get_dashboard", "control", "read"),
    ("acknowledge_booth_messages", "read", "read manage"),
    ("start_backup_now", "read", "read manage"),
    ("get_booth_queues", "control", "read"),
    ("discard_failed_uploads", "read manage", "read control"),
    ("get_silence_alarm", "control", "read"),
])
def test_each_new_tool_needs_its_right(door, tool, refused, allowed):
    from app import mcp
    dev = _booth()
    args = {} if tool in ("get_dashboard", "start_backup_now") else {"booth_id": dev["id"]}
    if tool == "discard_failed_uploads":
        args["all"] = True
    r = door.call(refused, tool, **args)
    assert r.status_code == 403
    assert door.call(allowed, tool, **args).status_code == 200
    spec = mcp.TOOLS[tool]
    assert "+".join((spec["scope"],) + tuple(spec.get("also", ()))) == allowed.split()[-1]


def test_tools_with_booth_text_say_it_is_data():
    from app import mcp
    for name in ("get_dashboard", "get_booth_queues"):
        assert mcp.TOOLS[name]["description"].startswith(mcp.UNTRUSTED_NOTE), name


# ── Dashboard and "Als gelesen markieren" ────────────────────────────

def test_the_dashboard_shows_booths_messages_storage_backup_and_waiting_pictures(door):
    from app import db, ecosystem
    dev = _booth(status={"camera_state": "ready", "printer_status": "ok"})
    _log_error(dev["id"])
    eid = _event()
    con = db.get_db()
    con.execute("INSERT INTO media (event_id, kind, filename, path_original, share_token, review_state) "
                "VALUES (?, 'photo', 'x.jpg', 'x.jpg', ?, 'pending')", (eid, secrets.token_hex(8)))
    con.commit()
    con.close()
    ecosystem.save_device_profile(dev["id"], {"mail": {"password": "Mail-Geheim-998877"},
                                              "access": {"pin": "735194"}}, None)
    out = door.data("read", "get_dashboard")
    booth = next(b for b in out["booths"] if b["id"] == dev["id"])
    assert booth["messages"]["unacknowledged_errors"] == 1
    assert "Papierstau" in booth["messages"]["last_message"]
    assert booth["messages"]["all_acknowledged"] is False
    assert {"health", "last_contact", "stale", "age_text"} <= set(booth)
    assert out["unacknowledged"]["errors"] >= 1
    assert {"used", "total", "free"} <= set(out["storage"])
    assert {"maintenance", "backup", "backup_now"} <= set(out)
    assert any(p["event_id"] == eid and p["pending"] == 1 for p in out["pending_review"])
    raw = door.text("read", "get_dashboard")
    assert "Mail-Geheim-998877" not in raw and "735194" not in raw and "/" + "tok-" not in raw


def test_messages_can_be_acknowledged_and_that_is_in_the_change_log(door):
    dev = _booth(status={})
    _log_error(dev["id"])
    out = door.data("read manage", "acknowledge_booth_messages", booth_id=dev["id"])
    assert out["booth_id"] == dev["id"] and out["acknowledged_at"]
    entry = _latest_log()
    assert "acknowledge_booth_messages" in entry["was"]
    booth = next(b for b in door.data("read", "get_dashboard")["booths"] if b["id"] == dev["id"])
    assert booth["messages"]["unacknowledged_errors"] == 0
    assert booth["messages"]["all_acknowledged"] is True


def test_a_backup_now_is_two_step_and_logged(door, monkeypatch):
    from app import maintenance
    started = []
    monkeypatch.setattr(maintenance, "start_backup_now", lambda app, **kw: started.append(1) or "started")
    first = door.data("read manage", "start_backup_now")
    assert first["confirm_required"] and not started
    second = door.data("read manage", "start_backup_now", confirm_token=first["confirm_token"])
    assert started == [1] and second["outcome"] == "started"
    assert "start_backup_now" in _latest_log()["was"]


# ── Queues ───────────────────────────────────────────────────────────

QUEUE_STATUS = {"queue_pending": 3, "queue_failed": 2,
                "print_jobs": [{"id": 7, "state": "waiting", "run": "r1"}],
                "failed_uploads": [{"id": 11, "target": "gallery", "kind": "photo", "reason": "Server antwortet nicht",
                                    "attempts": 4}, {"id": 12, "target": "nas", "kind": "photo",
                                                     "reason": "Ziel voll", "attempts": 1}],
                "failed_reasons": [{"reason": "Server antwortet nicht", "count": 1}],
                "upload_targets": {"gallery": {"sent": 10, "pending": 3, "failed": 1}}}


def test_the_queues_show_prints_failed_uploads_and_per_target_counts(door):
    dev = _booth(status=QUEUE_STATUS)
    out = door.data("read", "get_booth_queues", booth_id=dev["id"])
    assert [p["id"] for p in out["prints"]] == [7]
    assert [f["id"] for f in out["failed_uploads"]] == [11, 12]
    assert out["failed_uploads"][0]["reason"] == "Server antwortet nicht"
    assert out["upload_targets"]["gallery"]["failed"] == 1
    assert out["queue_pending"] == 3 and out["queue_failed"] == 2


def test_discarding_failed_uploads_is_two_step_and_sends_the_job(door):
    from app import ecosystem
    dev = _booth(status=QUEUE_STATUS)
    first = door.data("read control", "discard_failed_uploads", booth_id=dev["id"], ids=[11])
    assert first["confirm_required"] and [f["id"] for f in first["what_would_happen"]["failed_uploads"]] == [11]
    assert not [j for j in ecosystem.recent_jobs(dev["id"], 5) if j["type"] == "discard_failed_uploads"]
    door.data("read control", "discard_failed_uploads", booth_id=dev["id"], ids=[11],
              confirm_token=first["confirm_token"])
    job = next(j for j in ecosystem.recent_jobs(dev["id"], 5) if j["type"] == "discard_failed_uploads")
    assert json.loads(job["payload_json"]) == {"ids": [11]}
    assert "discard_failed_uploads" in _latest_log()["was"]
    every = door.data("read control", "discard_failed_uploads", booth_id=dev["id"], all=True)
    door.data("read control", "discard_failed_uploads", booth_id=dev["id"], all=True,
              confirm_token=every["confirm_token"])
    job = next(j for j in ecosystem.recent_jobs(dev["id"], 5) if j["type"] == "discard_failed_uploads")
    assert json.loads(job["payload_json"]) == {"confirm": "all"}
    assert door.result("read control", "discard_failed_uploads", booth_id=dev["id"]).get("isError")


# ── Profile answers: compact, with ignored keys ──────────────────────

def test_a_one_leaf_change_answers_compactly(door):
    dev = _booth()
    text = door.text("read manage", "update_booth_profile", booth_id=dev["id"], changes={"print": {"enabled": False}})
    out = json.loads(text)
    assert len(text) < 1500
    assert out["changed"] == ["print"] and out["values"] == {"print.enabled": False}
    assert out["ignored_keys"] == [] and isinstance(out["profile_version"], int)
    assert "profile" not in out


def test_a_typo_is_reported_and_nothing_applied_says_so(door):
    from app import db, ecosystem
    dev = _booth()
    before = db.get_device_by_id(dev["id"])["profile_version"]
    res = door.result("read manage", "update_booth_profile", booth_id=dev["id"],
                      changes={"boomerang": {"sorce": "camera"}})
    assert res.get("isError") and "boomerang.sorce" in res["content"][0]["text"]
    assert "Nichts übernommen" in res["content"][0]["text"]
    assert db.get_device_by_id(dev["id"])["profile_version"] == before
    out = door.data("read manage", "update_booth_profile", booth_id=dev["id"],
                    changes={"boomerang": {"sorce": "camera"}, "print": {"enabled": False}})
    assert out["ignored_keys"] == ["boomerang.sorce"] and out["values"] == {"print.enabled": False}
    assert ecosystem.device_profile(db.get_device_by_id(dev["id"]))["print"]["enabled"] is False


def test_return_full_gives_the_whole_profile_as_before(door):
    dev = _booth()
    out = door.data("read manage", "update_booth_profile", booth_id=dev["id"],
                    changes={"print": {"enabled": False}}, **{"return": "full"})
    assert out["profile"]["print"]["enabled"] is False and out["changed"] == ["print"]


def test_the_secure_profile_tool_is_compact_and_never_shows_the_pin(door):
    dev = _booth()
    text = door.text("read secure", "update_booth_profile_secure", booth_id=dev["id"],
                     changes={"access": {"pin": "735194"}, "mail": {"pasword": "x"}})
    out = json.loads(text)
    assert "735194" not in text and out["values"] == {"access.pin": "••••"}
    assert out["ignored_keys"] == ["mail.pasword"] and len(text) < 1500


def test_a_profile_can_be_read_by_section(door):
    dev = _booth()
    out = door.data("read", "get_booth_profile", booth_id=dev["id"], sections=["print", "nicht-da"])
    assert set(out) == {"print", "unknown_sections"} and out["unknown_sections"] == ["nicht-da"]


def test_booth_texts_come_without_the_catalogue_unless_asked(door):
    dev = _booth()
    plain = door.data("read", "get_booth_texts", booth_id=dev["id"])
    assert "catalogue" not in plain
    full = door.data("read", "get_booth_texts", booth_id=dev["id"], include_catalogue=True, keys=["start.title"])
    assert [e["key"] for e in full["catalogue"]] == ["start.title"]


def test_diagnostics_can_leave_out_the_status_and_send_fewer_lines(door):
    dev = _booth(status={"camera_state": "ready"})
    for i in range(30):
        _log_error(dev["id"], f"Zeile {i}")
    out = door.data("read", "booth_diagnostics", booth_id=dev["id"])
    assert len(out["logs"]) == 15 and out["status"]["camera_state"] == "ready"
    slim = door.data("read", "booth_diagnostics", booth_id=dev["id"], include_status=False, limit=5)
    assert "status" not in slim and len(slim["logs"]) == 5


# ── Events ───────────────────────────────────────────────────────────

def test_list_events_names_state_date_kind_and_dates(door):
    eid = _event(date="2026-12-24", kind="client")
    row = next(e for e in door.data("read", "list_events") if e["id"] == eid)
    assert {"published", "status", "date", "kind", "parent_id", "scheduled_live_at", "expires_at",
            "media", "retention_days"} <= set(row)
    assert row["date"] == "2026-12-24" and row["kind"] == "client"


def test_create_event_does_not_put_the_upload_token_into_the_chat(door):
    from app import db
    text = door.text("read manage", "create_event", name="Ohne Token")
    out = json.loads(text)
    stored = db.get_event_by_id(out["id"])["upload_token"]
    assert stored and stored not in text and "im Admin sichtbar" in out["upload_token"]


MANAGE_SAMPLE = {"layout": "grid", "thumb_size": "l", "theme": "dark", "tint": "#aabbcc", "show_date": True,
                 "selection_max": "25", "download_640": False, "watermark_text": "Fest", "review_mode": "aus",
                 "layout_approval": True, "share_whatsapp": False, "opener_title": "Willkommen"}


def test_all_presentation_and_download_settings_are_settable_with_manage(door):
    from app import db, gallery_settings as gsm
    eid = _event()
    out = door.data("read manage", "set_event_settings", event_id=eid, changes=MANAGE_SAMPLE)
    assert sorted(out["changed"]) == sorted(MANAGE_SAMPLE) and out["ignored_keys"] == []
    stored = gsm.for_event(db.get_event_by_id(eid))
    assert {k: stored[k] for k in MANAGE_SAMPLE} == MANAGE_SAMPLE
    got = door.data("read", "get_event_settings", event_id=eid)["settings"]
    assert set(gsm.DEFAULTS) - {"mode_pin"} <= set(got) and "mode_pin" not in got


@pytest.mark.parametrize("key,value", [("layout", "kacheln"), ("tint", "red"), ("selection_max", "viele"),
                                       ("thumb_size", "xl"), ("download_pattern", "../{event}"),
                                       ("review_mode", "vielleicht"), ("show_date", "ja bitte"),
                                       ("upload_grace_days", "900")])
def test_a_value_the_admin_form_would_drop_is_refused(door, key, value):
    from app import db, gallery_settings as gsm
    from werkzeug.datastructures import MultiDict
    eid = _event()
    before = gsm.for_event(db.get_event_by_id(eid))
    # Parity: the admin form's parser does not store this value either.
    assert gsm.from_form(MultiDict({key: value}), base=before)[key] != value
    res = door.result("read manage photos", "set_event_settings", event_id=eid, changes={key: value})
    assert res.get("isError") and key in res["content"][0]["text"]
    assert gsm.for_event(db.get_event_by_id(eid)) == before


def test_an_unknown_setting_is_reported_not_dropped(door):
    eid = _event()
    out = door.data("read manage", "set_event_settings", event_id=eid, changes={"layot": "grid", "likes": False})
    assert out["ignored_keys"] == ["layot"] and out["changed"] == ["likes"]
    res = door.result("read manage", "set_event_settings", event_id=eid, changes={"layot": "grid"})
    assert res.get("isError") and "layot" in res["content"][0]["text"]


@pytest.mark.parametrize("changes,needed", [({"embed": True}, "photos"), ({"allow_mode_switch": True}, "photos"),
                                            ({"upload_grace_days": "3"}, "photos"),
                                            ({"embed_origins": "https://example.org"}, "secure"),
                                            ({"review_url": "https://example.org/bewerten"}, "secure"),
                                            ({"mode_pin": "4711"}, "secure")])
def test_settings_that_open_doors_need_their_right(door, changes, needed):
    eid = _event()
    assert door.call("read manage", "set_event_settings", event_id=eid, changes=changes).status_code == 403
    assert door.data(f"read manage {needed}", "set_event_settings", event_id=eid, changes=changes)["changed"]


def test_the_mode_pin_is_set_but_never_returned(door):
    eid = _event()
    text = door.text("read manage secure", "set_event_settings", event_id=eid, changes={"mode_pin": "480213"})
    assert "480213" not in text
    got = door.text("read", "get_event_settings", event_id=eid)
    assert "480213" not in got and json.loads(got)["settings"]["mode_pin_set"] is True
    summary = door.text("read manage", "configure_event", event_id=eid, name="Neu")
    assert "480213" not in summary


def test_configure_event_sets_date_description_kiosk_text(door):
    from app import db
    eid = _event()
    door.data("read manage", "configure_event", event_id=eid, date="2026-12-31",
              description="Silvester im Saal", kiosk_text="Scannt den Code!")
    ev = db.get_event_by_id(eid)
    assert (ev["date"], ev["description"], ev["kiosk_text"]) == ("2026-12-31", "Silvester im Saal", "Scannt den Code!")
    for bad in ({"date": "31.12."}, {"kiosk_text": "a\nb"}, {"description": "x" * 3000}):
        assert door.result("read manage", "configure_event", event_id=eid, **bad).get("isError"), bad


def test_a_new_slug_keeps_the_old_address_as_a_redirect(door):
    from app import db
    eid = _event()
    old = db.get_event_by_id(eid)["slug"]
    out = door.data("read manage", "configure_event", event_id=eid, slug="Neue Adresse " + secrets.token_hex(2))
    assert out["slug"] != old and out["slug"].startswith("neue-adresse")
    assert old in db.list_slug_aliases(eid)
    back = door.data("read manage", "configure_event", event_id=eid, slug=old)
    assert back["slug"] == old and old not in db.list_slug_aliases(eid)
    other = _event()
    res = door.result("read manage", "configure_event", event_id=other, slug=old)
    assert res.get("isError") and "vergeben" in res["content"][0]["text"]


def test_setup_event_takes_every_gallery_setting_too(door):
    out = door.data("read manage", "setup_event", name="Mit Layout", settings={"layout": "list", "gap": "s"})
    from app import db, gallery_settings as gsm
    gs = gsm.for_event(db.get_event_by_id(out["id"]))
    assert gs["layout"] == "list" and gs["gap"] == "s"
    # An expiry on a new, unpublished gallery stays manage, as before this batch.
    later = door.data("read manage", "setup_event", name="Mit Ablauf", expires_at="2030-01-01T00:00")
    assert later["expires_at"] == "2030-01-01T00:00"
    assert door.call("read manage", "setup_event", name="Offen", settings={"embed": True}).status_code == 403


def test_the_silence_alarm_can_be_read_without_secure_and_hides_the_recipient(door):
    from app import silence_alarm
    dev = _booth()
    assert not silence_alarm.save_settings(dev["id"], enabled=True, minutes=10, recipient="alarm@example.org")
    text = door.text("read", "get_silence_alarm", booth_id=dev["id"])
    out = json.loads(text)
    assert out["enabled"] and out["minutes"] == 10 and "alarm@example.org" not in text
    assert "alarm@example.org" in door.text("read secure", "set_silence_alarm", booth_id=dev["id"])


@pytest.mark.parametrize("tool_name,args", [
    ("setup_event", {"name": "Abgelaufen", "settings": {"expires_at": "2020-01-01"}}),
    ("setup_event", {"name": "Abgelaufen", "expires_at": "2020-01-01"}),
    ("set_event_settings", {"changes": {"expires_at": "2020-01-01"}}),
    ("set_event_settings", {"changes": {"expires_at": "irgendwann"}}),
    ("configure_event", {"settings": {"expires_at": "2020-01-01"}}),
])
def test_an_expiry_in_the_past_or_unreadable_is_refused_on_every_path(door, tool_name, args):
    from app import db, gallery_settings as gsm
    if tool_name != "setup_event":
        eid = _event()
        args = {"event_id": eid, **args}
    res = door.result("read manage photos", tool_name, **args)
    assert res.get("isError") and "expires_at" in res["content"][0]["text"]
    if tool_name != "setup_event":
        assert gsm.for_event(db.get_event_by_id(eid))["expires_at"] == ""
