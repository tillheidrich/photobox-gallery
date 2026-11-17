"""The booth's counters of an evening, kept per booth, event and day.

The booth sends the sums of its running day and event in the heartbeat
(``booth_stats``); the gallery stores them in extra tables and shows them on
the booth's admin page ("Heute"), in the event's analytics and through the
MCP tools booth_stats and event_stats — one function for all of them.
"""
import secrets

import pytest


def _setup(client):
    client.get("/healthz")
    from app import db, ecosystem
    ecosystem.init_schema()
    slug = "hochzeit-" + secrets.token_hex(3)
    eid = db.create_event(name="Hochzeit", slug=slug, upload_token=secrets.token_hex(8))
    _made = db.create_device(name="Box Zahlen")
    did = _made["id"]
    ecosystem.save_device_profile(did, {"modes": ["photo"]}, eid)
    return {**db.get_device_by_id(did), "api_key": _made["api_key"]}, eid, slug


def _beat(client, dev, slug, today, event_total=None, day="2026-11-14", **status):
    body = {"status": {"booth_running": True, "camera_state": "ready", **status,
                       "booth_stats": {"event_slug": slug, "day": day, "today": today,
                                       "event_total": event_total if event_total is not None else today}}}
    r = client.post("/api/v2/booth/sync", json=body, headers={"X-API-Key": dev["api_key"]})
    assert r.status_code == 200, r.get_data(as_text=True)


TODAY = {"runs.photo": 40, "runs.collage": 12, "aborted.cancel": 3, "aborted.timeout": 1, "retakes": 5,
         "prints": 50, "print_errors": 2, "prints_backup": 4, "consent.gallery": 45, "consent.print": 7,
         "camera.errors": 1, "camera.reconnects": 1, "qr_big": 9, "hour.21": 30, "hour.22": 22}


def test_the_heartbeat_numbers_are_stored_and_summed(client):
    from app import stats
    dev, eid, slug = _setup(client)
    _beat(client, dev, slug, {"runs.photo": 10}, day="2026-11-14")
    _beat(client, dev, slug, TODAY, day="2026-11-14")          # later in the evening: replaces
    z = stats.device(dev["id"])["today"]
    assert z["day"] == "2026-11-14" and z["event_slug"] == slug
    assert z["runs_total"] == 52 and z["runs"]["collage"] == 12
    assert z["aborted"] == {"cancel": 3, "timeout": 1, "total": 4}
    assert z["prints"] == 50 and z["print_errors"] == 2 and z["prints_backup"] == 4
    assert z["consent"] == {"gallery": 45, "print": 7, "notice": 0}
    assert z["peak_hour"] == 21 and z["peak_runs"] == 30


def test_the_event_view_sums_booths_and_days(client):
    from app import db, stats
    dev, eid, slug = _setup(client)
    zweite = db.create_device(name="Box 2")
    _beat(client, dev, slug, {"runs.photo": 5}, {"runs.photo": 25}, day="2026-11-15")
    _beat(client, zweite, slug, {"runs.photo": 7, "prints": 3}, day="2026-11-15")
    z = stats.event(eid)["booth_counters"]
    assert z["runs_total"] == 32 and z["prints"] == 3
    assert [t["day"] for t in stats.event(eid)["booth_counter_days"]] == ["2026-11-15"]


def test_only_numbers_of_known_shape_are_kept(client):
    from app import stats
    dev, eid, slug = _setup(client)
    _beat(client, dev, slug, {"runs.photo": 3, "guest": "Anna Müller", "x" * 80: 1, "prints": -4,
                              "qr_big": 2.5, "retakes": True})
    roh = stats.booth_counters_raw(dev["id"])
    assert roh == {"runs.photo": 3}


def test_a_broken_report_costs_no_heartbeat(client):
    dev, eid, slug = _setup(client)
    r = client.post("/api/v2/booth/sync", json={"status": {"booth_stats": "kaputt"}},
                    headers={"X-API-Key": dev["api_key"]})
    assert r.status_code == 200
    r = client.post("/api/v2/booth/sync", json={"status": {"booth_stats": {"day": "gestern", "today": {}}}},
                    headers={"X-API-Key": dev["api_key"]})
    assert r.status_code == 200


def test_paper_reach_comes_from_the_counted_sheets(client):
    from app import stats
    dev, eid, slug = _setup(client)
    _beat(client, dev, slug, TODAY, sheets_left=120,
          sheets=[{"slot": 1, "quelle": "gezählt", "rest": 120, "gesamt": 400}])
    assert stats.device(dev["id"])["today"]["paper_prints_left"] == 120
    _beat(client, dev, slug, TODAY, sheets=[{"slot": 1, "quelle": "unbekannt"}])
    assert stats.device(dev["id"])["today"]["paper_prints_left"] is None


def test_admin_pages_show_the_card(client):
    dev, eid, slug = _setup(client)
    _beat(client, dev, slug, TODAY)
    with client.session_transaction() as s:
        s["admin_logged_in"] = True
        s["csrf_token"] = "x"
    seite = client.get(f"/admin/devices/{dev['id']}/booth").get_data(as_text=True)
    assert 'id="booth-heute"' in seite and ">52<" in seite
    seite = client.get(f"/admin/analytics/{eid}").get_data(as_text=True)
    assert 'id="booth-zahlen"' in seite and ">52<" in seite


def test_mcp_tools_carry_the_same_numbers(client):
    from app import ecosystem, mcp as mcp_mod
    import json
    dev, eid, slug = _setup(client)
    _beat(client, dev, slug, TODAY)
    key = mcp_mod.new_key()

    def call(name, args):
        r = client.post("/mcp", headers={"X-API-Key": key}, json={
            "jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": name, "arguments": args}})
        return json.loads(r.get_json()["result"]["content"][0]["text"])

    assert call("booth_stats", {"booth_id": dev["id"]})["today"]["runs_total"] == 52
    assert call("event_stats", {"event_id": eid})["booth_counters"]["runs_total"] == 52
