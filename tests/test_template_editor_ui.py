"""The template editor behaves like a design tool, not like a form.

Till's yardstick is the editor of dslrBooth/LumaBooth and Breeze: undo,
layers, rotation, alignment. The decisions behind those (what counts as one
undo step, how a layer moves, where an element snaps) are pure functions in
the editor script, between the ``<editor-kern>`` markers; these tests run
that very code with Node. Without Node on the machine they are skipped, the
wiring tests further down still run.
"""
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

WURZEL = Path(__file__).resolve().parents[1]
JS = (WURZEL / "app" / "static" / "js" / "vorlagen-editor.js").read_text(encoding="utf-8")
NODE = shutil.which("node")


def _kern() -> str:
    start = JS.index("// <editor-kern>")
    ende = JS.index("// </editor-kern>")
    return JS[start:ende]


def run(body: str):
    """Run ``body`` after the editor core; whatever it assigns to ``out`` comes back."""
    if not NODE:
        pytest.skip("node is not installed")
    script = ("var out = null;\n" + _kern() + "\n;(function(){\n" + body +
              "\n})();\nprocess.stdout.write(JSON.stringify(out));")
    res = subprocess.run([NODE, "-e", script], capture_output=True, text=True, timeout=20)
    assert res.returncode == 0, res.stderr
    return json.loads(res.stdout)


# ── E1: undo and redo ─────────────────────────────────────

def test_at_least_fifty_steps_can_be_undone():
    out = run("""
      var h = new Verlauf(); h.merke('0');
      for (var i = 1; i <= 60; i++) h.merke(String(i));
      var n = 0; while (h.zurueck() !== null) n++;
      out = n;
    """)
    assert out >= 50


def test_a_drag_is_one_step_not_a_hundred():
    out = run("""
      var h = new Verlauf(); h.merke('start');
      h.beginne();
      for (var i = 0; i < 100; i++) h.merke('zwischen' + i);
      h.schliesse('ende');
      out = [h.zurueck(), h.zurueck()];
    """)
    assert out == ["start", None]


def test_a_drag_that_moved_nothing_leaves_no_step():
    out = run("""
      var h = new Verlauf(); h.merke('start');
      h.beginne(); h.schliesse('start');
      out = h.kannZurueck();
    """)
    assert out is False


def test_redo_brings_back_what_undo_took_and_a_new_change_ends_redo():
    out = run("""
      var h = new Verlauf(); h.merke('a'); h.merke('b'); h.merke('c');
      var r = [h.zurueck(), h.zurueck(), h.vor()];
      h.merke('x');
      r.push(h.kannVor(), h.zurueck());
      out = r;
    """)
    assert out == ["b", "a", "b", False, "b"]


# ── wiring ────────────────────────────────────────────────

def test_the_editor_page_runs_without_inline_handlers():
    """New editor code lives in files; the CSP can drop 'unsafe-inline' one day."""
    seite = (WURZEL / "app" / "templates" / "admin" / "template_editor.html").read_text(encoding="utf-8")
    assert "onclick=" not in seite and "onsubmit=" not in seite
    assert "vorlagen-editor-seite.js" in seite
    assert not re.search(r"<script>(?!\s*</script>)", seite), "no inline script block"


def test_undo_keys_and_the_leave_warning_are_wired():
    assert "beforeunload" in JS
    assert re.search(r"metaKey|ctrlKey", JS) and '"z"' in JS


def test_the_editor_page_shows_undo_and_redo(client):
    with client.session_transaction() as s:
        s["admin_logged_in"] = True
        s["csrf_token"] = "test-token"
    seite = client.get("/admin/vorlagen/editor").get_data(as_text=True)
    assert 'id="pbEditorDaten"' in seite
    daten = re.search(r'<script type="application/json" id="pbEditorDaten">(.*?)</script>', seite, re.S)
    assert daten and json.loads(daten.group(1))["speichernUrl"].endswith("/vorlagen/speichern")


# ── E4: align, distribute, snap ───────────────────────────

BLATT = {"x": 0, "y": 0, "w": 1200, "h": 1800}


@pytest.mark.parametrize("wie,erwartet", [
    ("links", {"a": [-100, 0]}), ("rechts", {"a": [900, 0]}), ("hmitte", {"a": [400, 0]}),
    ("oben", {"a": [0, -200]}), ("unten", {"a": [0, 1400]}), ("vmitte", {"a": [0, 600]}),
])
def test_one_element_aligns_to_the_sheet(wie, erwartet):
    out = run("out = ausrichten([{id: 'a', x: 100, y: 200, w: 200, h: 200}], '%s', %s);"
              % (wie, json.dumps(BLATT)))
    assert out == erwartet


def test_several_elements_align_to_each_other():
    out = run("""
      var b = [{id: 'a', x: 100, y: 0, w: 200, h: 100}, {id: 'b', x: 400, y: 300, w: 100, h: 100}];
      out = [ausrichten(b, 'links', huelle(b)), ausrichten(b, 'rechts', huelle(b)), ausrichten(b, 'hmitte', huelle(b))];
    """)
    assert out[0] == {"a": [0, 0], "b": [-300, 0]}
    assert out[1] == {"a": [200, 0], "b": [0, 0]}
    assert out[2] == {"a": [100, 0], "b": [-150, 0]}


def test_a_turned_element_aligns_by_what_is_seen():
    """A 400×100 box turned by 90° is 100 wide on the sheet; its visible left edge goes to 0."""
    out = run("out = ausrichten([{id: 'a', x: 300, y: 300, w: 400, h: 100, r: 90}], 'links', %s);"
              % json.dumps(BLATT))
    assert out == {"a": [-450, 0]}


def test_three_elements_are_spread_evenly():
    out = run("""
      out = verteilen([{id: 'a', x: 0, y: 0, w: 100, h: 50}, {id: 'b', x: 150, y: 0, w: 100, h: 50},
                       {id: 'c', x: 900, y: 0, w: 100, h: 50}], 'x');
    """)
    assert out == {"a": [0, 0], "b": [300, 0], "c": [0, 0]}


def test_dragging_snaps_to_the_middle_of_the_sheet_and_says_where():
    out = run("""
      var linien = fanglinien(%s, []);
      out = einrasten({x: 497, y: 1000, w: 200, h: 100}, linien, 8);
    """ % json.dumps(BLATT))
    assert out["dx"] == 3 and out["dy"] == 0
    assert out["linienX"] == [600]


def test_dragging_snaps_to_a_neighbours_edge():
    out = run("""
      var linien = fanglinien(%s, [{x: 100, y: 100, w: 300, h: 300}]);
      out = einrasten({x: 405, y: 700, w: 210, h: 100}, linien, 8);
    """ % json.dumps(BLATT))
    assert out["dx"] == -5 and out["linienX"] == [400]


def test_far_from_every_line_nothing_snaps():
    out = run("""
      out = einrasten({x: 333, y: 777, w: 100, h: 100}, fanglinien(%s, []), 8);
    """ % json.dumps(BLATT))
    assert out == {"dx": 0, "dy": 0, "linienX": [], "linienY": []}


# ── E6: print margin and cut ──────────────────────────────

def test_the_safe_area_is_three_percent_unless_set():
    out = run("out = [sichererBereich({canvas: [1200, 1800]}), sichererBereich({canvas: [1200, 1800], safe_area: 5})];")
    assert out[0] == {"x": 36, "y": 54, "w": 1128, "h": 1692}
    assert out[1] == {"x": 60, "y": 90, "w": 1080, "h": 1620}


@pytest.mark.parametrize("box,draussen", [
    ({"x": 100, "y": 100, "w": 300, "h": 60}, False),
    ({"x": 20, "y": 100, "w": 300, "h": 60}, True),
    ({"x": 900, "y": 1700, "w": 280, "h": 60}, True),
    ({"x": 30, "y": 400, "w": 300, "h": 60, "r": 90}, False),
])
def test_a_text_outside_the_safe_area_is_flagged(box, draussen):
    out = run("out = ausserhalbSicher(%s, sichererBereich({canvas: [1200, 1800]}));" % json.dumps(box))
    assert out is draussen


def test_a_text_across_the_strip_cut_is_flagged():
    out = run("""
      var v = {paper: 'streifen', canvas: [1200, 1800]};
      out = [schnitte(v), schnitte({paper: '10x15', canvas: [1200, 1800]}),
             kreuztSchnitt({x: 500, y: 100, w: 200, h: 50}, schnitte(v)),
             kreuztSchnitt({x: 100, y: 100, w: 200, h: 50}, schnitte(v))];
    """)
    assert out == [[600], [], True, False]
