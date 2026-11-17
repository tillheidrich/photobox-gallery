"""The operator's own start picture, shown as it was designed.

Till's start screen in dslrBooth is one full-screen picture with the headline,
the instructions, a QR code and his signature burned in. On our box the event
name, "Tippen & lächeln" and the status line sat on top of it, and the picture
drifted and was cropped at the edges. Now the title and the status line can be
switched off, and the picture is either filled (cropped, standing still),
shown whole, or drifts as before.

The operator notice must stay findable without the status line: a single
coloured dot, with the text for screen readers.
"""
from pathlib import Path

from boothcore import config

WEB = Path(__file__).resolve().parents[1] / "boothcore" / "web"
JS = (WEB / "static" / "app.js").read_text(encoding="utf-8")
CSS = (WEB / "static" / "app.css").read_text(encoding="utf-8")


def test_a_box_that_sets_nothing_looks_as_before():
    s = config.DEFAULT_PROFILE["start"]
    assert s["show_title"] is True
    assert s["show_status"] is True
    assert s["fit"] == "drift", "the slow drift is what every box shows today"


def test_the_title_can_go():
    assert 'st.dataset.title = p.start.show_title === false ? "off" : "";' in JS
    assert '#s-start[data-title="off"] .start-copy{display:none}' in CSS


def test_the_picture_can_fill_or_show_whole_and_then_stands_still():
    assert '#s-start[data-fit="cover"] .start-bg,#s-start[data-fit="contain"] .start-bg{inset:0;transform:none;animation:none}' in CSS
    assert '#s-start[data-fit="contain"] .start-bg{background-size:contain}' in CSS
    assert '#s-start[data-fit="contain"] .start-bg video{object-fit:contain}' in CSS


def test_without_the_status_line_the_notice_stays_a_dot():
    status = JS.split("function renderStatus()")[1].split("\n  }\n")[0]
    assert "show_status === false" in status
    assert "betriebsmeldung--punkt" in status, "the operator notice survives as a dot"
    assert "nur-vorlesen" in status, "and its text stays readable for a screen reader"
    assert ".betriebsmeldung--punkt i{" in CSS
