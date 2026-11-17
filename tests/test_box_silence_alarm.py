"""The gallery warns the owner when a box stops answering — only when switched on.

Owner's rule: "nur, wenn man als Admin aktiviert hat, dass die nicht
reagierende Box eine Warnung auslöst. Kann ja auch sein, dass die Gäste sie
bewusst heruntergefahren haben." One mail when silent, one when back, none in
between; a box that said goodbye on purpose is not reported.
"""
import datetime as dt
import secrets
import threading

import pytest


@pytest.fixture()
def mails(client, monkeypatch):
    from app import db, emailer
    client.get("/healthz")
    sent = []
    monkeypatch.setattr(emailer, "smtp_configured", lambda: True)
    monkeypatch.setattr(emailer, "send_text_mail", lambda **kw: sent.append(kw))
    db.set_setting("admin_email", "betrieb@example.org")
    db.set_setting("app_base_url", "https://galerie.example.org")
    return sent


def _box(event_today=True, enabled=True, **opts):
    from app import db, ecosystem, silence_alarm
    ecosystem.init_schema()
    dev = dict(db.create_device(name=f"Box {secrets.token_hex(3)}"))
    eid = db.create_event(name=f"Fest {secrets.token_hex(2)}", slug="still-" + secrets.token_hex(4),
                          upload_token=secrets.token_hex(6))
    day = dt.date.today() if event_today else dt.date.today() - dt.timedelta(days=9)
    db.update_event(eid, date=day.isoformat())
    ecosystem.save_device_profile(dev["id"], ecosystem.device_profile(db.get_device_by_id(dev["id"])) or {}, eid)
    assert silence_alarm.save_settings(dev["id"], enabled=enabled, **opts) == []
    return dev


def _heard(dev, minutes_ago):
    from app import db
    con = db.get_db()
    con.execute("UPDATE devices SET last_heartbeat_at=datetime('now', ?) WHERE id=?",
                (f"-{int(minutes_ago * 60)} seconds", dev["id"]))
    con.commit()
    con.close()


def _sync(client, dev, status=None):
    return client.post("/api/v2/booth/sync", headers={"X-API-Key": dev["api_key"]},
                       json={"status": status or {"camera_state": "ready"}})


def _mine(sent, dev):
    from app import db
    name = db.get_device_by_id(dev["id"])["name"]
    return [m for m in sent if name in m["subject"]]


def test_a_silent_box_sends_one_mail_and_then_nothing(client, mails):
    from app import silence_alarm
    client.get("/healthz")
    dev = _box()
    _sync(client, dev, {"camera_state": "ready", "printer_status": "ready", "queue_pending": 3})
    _heard(dev, 20)
    silence_alarm.check()
    silence_alarm.check()
    got = _mine(mails, dev)
    assert len(got) == 1
    m = got[0]
    assert m["to_email"] == "betrieb@example.org"
    assert "antwortet nicht" in m["subject"]
    assert "Box oder Internetverbindung" in m["body"]
    assert "Uploads: 3 warten" in m["body"]
    assert f"https://galerie.example.org/admin/devices/{dev['id']}/booth" in m["body"]


def test_a_box_that_comes_back_sends_one_back_mail(client, mails):
    from app import silence_alarm
    client.get("/healthz")
    dev = _box()
    _sync(client, dev)
    _heard(dev, 42)
    silence_alarm.check()
    assert _sync(client, dev).status_code == 200
    silence_alarm.check()
    silence_alarm.check()
    got = _mine(mails, dev)
    assert [("antwortet nicht" in m["subject"], "wieder da" in m["subject"]) for m in got] == \
        [(True, False), (False, True)]
    assert "wieder da nach 42 min" in got[1]["body"]


def test_a_deliberate_quit_sends_nothing(client, mails):
    from app import silence_alarm
    client.get("/healthz")
    dev = _box()
    _sync(client, dev)
    r = client.post("/api/v2/booth/going-down", headers={"X-API-Key": dev["api_key"]},
                    json={"reason": "quit", "expect_back": False})
    assert r.status_code == 200 and r.get_json()["ok"]
    _heard(dev, 120)
    silence_alarm.check()
    assert _mine(mails, dev) == []
    assert "bewusst heruntergefahren um" in silence_alarm.view(dev["id"])["goodbye"]


def test_only_the_box_itself_may_say_goodbye(client, mails):
    client.get("/healthz")
    dev = _box()
    assert client.post("/api/v2/booth/going-down", json={"reason": "quit"}).status_code == 401
    assert client.post("/api/v2/booth/going-down", headers={"X-API-Key": "falsch"},
                       json={"reason": "quit"}).status_code == 401


def _goodbye(dev, reason, expect_back, minutes_ago):
    from app import db, silence_alarm
    silence_alarm.note_going_down(dev["id"], reason, expect_back)
    con = db.get_db()
    con.execute("UPDATE device_silence_alarm SET down_at=datetime('now', ?) WHERE device_id=?",
                (f"-{int(minutes_ago * 60)} seconds", dev["id"]))
    con.commit()
    con.close()


def test_an_announced_restart_that_comes_back_in_time_sends_nothing(client, mails):
    from app import silence_alarm
    client.get("/healthz")
    dev = _box()
    _heard(dev, 6)
    _goodbye(dev, "restart", True, 6)
    _sync(client, dev)                      # back after six minutes
    silence_alarm.check()
    assert _mine(mails, dev) == []
    assert silence_alarm.view(dev["id"])["goodbye"] == ""


def test_an_announced_restart_that_does_not_come_back_warns_once(client, mails):
    from app import silence_alarm
    client.get("/healthz")
    dev = _box()
    _heard(dev, 10)
    _goodbye(dev, "update", True, 10)
    silence_alarm.check()
    assert _mine(mails, dev) == []          # still inside the 15 minutes
    _heard(dev, 20)
    _goodbye(dev, "update", True, 20)
    silence_alarm.check()
    silence_alarm.check()
    assert len(_mine(mails, dev)) == 1


def test_switched_off_sends_nothing(client, mails):
    from app import silence_alarm
    client.get("/healthz")
    dev = _box(enabled=False)
    _sync(client, dev)
    _heard(dev, 300)
    silence_alarm.check()
    assert _mine(mails, dev) == []


def test_outside_the_event_window_sends_nothing_when_restricted(client, mails):
    from app import silence_alarm
    client.get("/healthz")
    restricted = _box(event_today=False)
    free = _box(event_today=False, only_event=False)
    for dev in (restricted, free):
        _sync(client, dev)
        _heard(dev, 30)
    silence_alarm.check()
    assert _mine(mails, restricted) == []
    assert len(_mine(mails, free)) == 1


def test_two_workers_send_one_mail(client, mails):
    from app import silence_alarm
    client.get("/healthz")
    dev = _box()
    _sync(client, dev)
    _heard(dev, 25)
    start = threading.Barrier(4)

    def worker():
        start.wait()
        silence_alarm.check()

    threads = [threading.Thread(target=worker) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(_mine(mails, dev)) == 1


def test_the_recipient_comes_from_the_admin_only(client, mails):
    from app import silence_alarm
    client.get("/healthz")
    dev = _box(recipient="vor-ort@example.org")
    assert silence_alarm.save_settings(dev["id"], enabled=True, recipient="a@x.de, b@y.de") != []
    assert silence_alarm.save_settings(dev["id"], enabled=True, minutes=2) != []
    assert silence_alarm.save_settings(dev["id"], enabled=True, minutes=241) != []
    # Whatever a box sends along with its goodbye or heartbeat is not an address.
    client.post("/api/v2/booth/going-down", headers={"X-API-Key": dev["api_key"]},
                json={"reason": "restart", "to": "fremd@example.net", "recipient": "fremd@example.net"})
    _heard(dev, 40)
    _goodbye(dev, "restart", True, 40)
    silence_alarm.check()
    assert [m["to_email"] for m in _mine(mails, dev)] == ["vor-ort@example.org"]


def test_english_account_gets_english_mail(client, mails):
    from app import db, silence_alarm
    client.get("/healthz")
    db.set_setting("app_language", "en")
    dev = _box()
    _sync(client, dev)
    _heard(dev, 20)
    silence_alarm.check()
    got = _mine(mails, dev)
    assert len(got) == 1 and "is not answering" in got[0]["subject"]
    assert "box or internet connection" in got[0]["body"]


def test_the_admin_switch_is_off_by_default_and_saves(client, formular, mails):
    from app import db, ecosystem, silence_alarm
    client.get("/healthz")
    ecosystem.init_schema()
    dev = dict(db.create_device(name=f"Box {secrets.token_hex(3)}"))
    assert silence_alarm.settings(dev["id"])["enabled"] == 0
    with client.session_transaction() as s:
        s["admin_logged_in"] = True
    page = f"/admin/devices/{dev['id']}/booth"
    html = client.get(page).get_data(as_text=True)
    assert "Warnen, wenn die Box nicht mehr antwortet" in html
    r = formular(f"/admin/devices/{dev['id']}/silence-alarm",
                 {"enabled": "1", "minutes": "30", "recipient": "", "only_event": "1"}, von=page)
    assert r.status_code in (302, 303)
    s = silence_alarm.settings(dev["id"])
    assert (s["enabled"], s["minutes"], s["only_event"]) == (1, 30, 1)


def test_dashboard_shows_the_alarm_line(client, mails):
    from app import silence_alarm
    client.get("/healthz")
    dev = _box()
    _sync(client, dev)
    _heard(dev, 20)
    silence_alarm.check()
    lines = [line for line in silence_alarm.dashboard_lines() if line["device_id"] == dev["id"]]
    assert lines and "antwortet nicht" in lines[0]["text"]


def test_the_light_says_shut_down_on_purpose_instead_of_red(client, mails):
    from app import booth, db
    client.get("/healthz")
    dev = _box(enabled=False)               # the note shows with the alarm off as well
    _sync(client, dev)
    _heard(dev, 60)
    _goodbye(dev, "quit", False, 60)
    light = booth.light(db.get_device_by_id(dev["id"]))
    assert light["health"] == "unknown"
    assert light["reasons"][0].startswith("bewusst heruntergefahren um")


def test_the_camera_battery_travels_with_the_heartbeat_and_into_the_mail(client, mails):
    from app import db, silence_alarm
    client.get("/healthz")
    dev = _box()
    _sync(client, dev, {"camera_state": "ready", "camera_battery": 18})
    assert db.get_device_status(dev["id"])["camera_battery"] == "18"
    _heard(dev, 20)
    silence_alarm.check()
    assert "Kamera-Akku: 18 %" in _mine(mails, dev)[0]["body"]
