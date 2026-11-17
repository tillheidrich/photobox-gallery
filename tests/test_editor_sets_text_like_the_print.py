"""The template editor fits a long line exactly as the renderer prints it.

vorlagen_render.textsatz decides how a line that is too long for its field
is printed (smaller, two lines, smaller still). The editor's sheet runs its
own copy of that rule (``textSatz`` in vorlagen-editor.js) on the browser's
measurements. Both are run here on one made-up font whose widths are known,
so any difference is a difference in the rule, not in the fonts.
"""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from app import vorlagen_render as vr

WURZEL = Path(__file__).resolve().parents[1]
JS = (WURZEL / "app" / "static" / "js" / "vorlagen-editor.js").read_text(encoding="utf-8")
NODE = shutil.which("node")


class _Schrift:
    """Every character 0.55 em wide; ascent 0.9 em, descent 0.25 em."""

    def __init__(self, size):
        self.size = size

    def getlength(self, text, *args, **kwargs):
        return len(text) * self.size * 0.55

    def getmetrics(self):
        return round(self.size * 0.9), round(self.size * 0.25)


FAELLE = [
    ({"x": 1510, "y": 120, "size": 46, "value": "{event_name}"}, "Mara & Jonas Berg", "10x15-quer"),
    ({"x": 1510, "y": 120, "size": 46, "value": "{event_name}", "w": 243, "h": 900}, "Maximiliane & Jonas Oberwasser",
     "10x15-quer"),
    ({"x": 600, "y": 1300, "size": 118, "value": "{event_name}", "align": "center"},
     "Maximiliane Wilhelmina & Jonas Bartholomäus K", "10x15"),
    ({"x": 600, "y": 1300, "size": 60, "value": "{event_name}", "align": "center"}, "Mia & Tom", "10x15"),
    ({"x": 1190, "y": 300, "size": 50, "value": "Mara & Jonas"}, "", "10x15"),
    ({"x": 900, "y": 400, "size": 70, "value": "{event_name}", "align": "right"}, "Maximiliane & Jonas", "10x15"),
    ({"x": 60, "y": 1600, "size": 54, "value": "{event_name}"}, "Maximiliane Wilhelmina & Jonas Bartholomäus K",
     "10x15"),
    ({"x": 100, "y": 200, "size": 60, "value": "Zeile eins ist lang genug\nund zwei", "line_height": 1.3,
      "w": 400, "h": 400}, "", "10x15"),
]


def _vorlage(t, papier):
    canvas = [1800, 1200] if papier.endswith("quer") else [1200, 1800]
    v = {"paper": papier, "canvas": canvas, "slots": [], "texts": [t, {"x": 60, "y": 1700, "size": 30,
                                                                       "value": "{date}"}]}
    if t["x"] == 60 and t["y"] == 1600:
        v["qr"] = {"x": 950, "y": 1560, "size": 200}
    return v


def _python(monkeypatch):
    monkeypatch.setattr(vr, "load_font", lambda size, *a, **k: _Schrift(size))
    aus = []
    for t, name, papier in FAELLE:
        v = _vorlage(t, papier)
        st = vr.style()
        satz = vr.textsatz(t, vr.werte({"event_name": name, "date": "18.11.2026"}, st), st, v)
        aus.append({"size": satz["size"], "zeilen": satz["zeilen"], "x": round(satz["x"], 2),
                    "align": satz["align"], "stufe": satz["stufe"], "abstand": satz["abstand"]})
    return aus


def _js():
    start, ende = JS.index("// <editor-kern>"), JS.index("// </editor-kern>")
    faelle = [{"t": t, "wert": t["value"].replace("{event_name}", name).replace("{date}", "18.11.2026"),
               "v": _vorlage(t, papier)} for t, name, papier in FAELLE]
    script = (JS[start:ende] + "\nvar mass = {breite: function (z, s) { return z.length * s * 0.55; },"
              " metrik: function (s) { return [Math.round(s * 0.9), Math.round(s * 0.25)]; }};\n"
              "var faelle = " + json.dumps(faelle) + ";\n"
              "process.stdout.write(JSON.stringify(faelle.map(function (f) {\n"
              "  f.v.texts[0] = f.t; var s = textSatz(f.t, f.wert, f.v, mass);\n"
              "  return {size: s.size, zeilen: s.zeilen, x: Math.round(s.x * 100) / 100, align: s.align,"
              " stufe: s.stufe, abstand: s.abstand};\n})));")
    res = subprocess.run([NODE, "-e", script], capture_output=True, text=True, timeout=20)
    assert res.returncode == 0, res.stderr
    return json.loads(res.stdout)


@pytest.mark.skipif(not NODE, reason="node is not installed")
def test_editor_and_renderer_fit_every_case_the_same_way(monkeypatch):
    py, js = _python(monkeypatch), _js()
    for fall, a, b in zip(FAELLE, py, js):
        assert a == b, (fall, a, b)
    # The cases cover every step of the rule.
    assert {f["stufe"] for f in py} >= {"", "kleiner", "zweizeilig", "verschoben"}


def test_the_editor_offers_the_qr_switch_and_the_field_size():
    assert "nur_mit_qr" in JS and "fieldW" in JS and "textSatz(t, beispielText(t.value), v, mass)" in JS
