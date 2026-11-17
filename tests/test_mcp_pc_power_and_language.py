"""MCP: restart or shut down a booth PC, set its kiosk language (Paket A, a).

Three new actions of the right ``control``, each a tool of its own with the
two-step confirmation (a first call only says what would happen and hands out
a token; only the second call with that token sends the job). The booth then
applies its own guards (switch at the booth, no open run, no print on its way)
and says goodbye to the gallery before Windows ends — tested in the booth
suite (booth/tests/test_remote_pc_power_and_language.py).
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_mcp_extra_rights import _booth, _poll, door  # noqa: E402,F401  (door is a fixture)

TOOLS = {"booth_restart_pc": "restart_pc", "booth_shutdown_pc": "shutdown_pc", "booth_set_language": "set_language"}


def test_the_actions_are_destructive_and_kept_out_of_booth_control():
    from app import mcp, remote_control
    for name, action in TOOLS.items():
        spec = mcp.TOOLS[name]
        assert spec["scope"] == "control" and spec.get("destructive") and spec["confirm"] is not None, name
        assert action in remote_control.DESTRUCTIVE_ACTIONS
        assert action not in mcp.TOOLS["booth_control"]["schema"]["properties"]["action"]["enum"]


@pytest.mark.parametrize("name,args", [("booth_restart_pc", {}), ("booth_shutdown_pc", {}),
                                       ("booth_set_language", {"lang": "en"})])
def test_two_steps_and_only_then_a_job(door, monkeypatch, name, args):
    from app import remote_control
    monkeypatch.setattr(remote_control, "WAIT_SECONDS", 0.0)
    dev = _booth()
    access = door.token("read control")
    first = door.data(door.tool(access, name, booth_id=dev["id"], **args))
    assert first["confirm_required"] is True and first["what_would_happen"]["booth"] == dev["name"]
    assert _poll(door, dev)["jobs"] == []                       # nothing sent yet
    door.data(door.tool(access, name, booth_id=dev["id"], confirm_token=first["confirm_token"], **args))
    jobs = _poll(door, dev)["jobs"]
    assert [j["payload"]["action"] for j in jobs] == [TOOLS[name]]


def test_without_the_control_right_nothing_is_offered(door):
    r = door.call(door.token("read manage"), "tools/call", {"name": "booth_restart_pc", "arguments": {"booth_id": 1}})
    assert r.status_code == 403


def test_a_strange_language_is_refused_before_anything_is_sent(door, monkeypatch):
    from app import remote_control
    monkeypatch.setattr(remote_control, "WAIT_SECONDS", 0.0)
    dev = _booth()
    assert door.tool(door.token("read control"), "booth_set_language", booth_id=dev["id"],
                     lang="../etc").get("isError")
    assert _poll(door, dev)["jobs"] == []
