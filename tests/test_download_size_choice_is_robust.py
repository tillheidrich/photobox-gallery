"""A strange remembered download size never stops the gallery's script (review 17.11.2026).

``dlShowChoice`` put the value from localStorage unescaped into a CSS
selector; a value with a quote or bracket made ``querySelector`` throw at
page load and every later line of the script (lightbox, downloads) was dead.
"""
from pathlib import Path

SRC = (Path(__file__).resolve().parents[1] / "app" / "templates" / "public" / "gallery.html").read_text("utf-8")


def _body(name: str) -> str:
    start = SRC.index(f"function {name}(")
    return SRC[start:SRC.index("\n}\n", start)]


def test_the_remembered_size_is_escaped_before_it_goes_into_a_selector():
    body = _body("dlShowChoice")
    assert "CSS.escape(size)" in body
    assert "'\"] .dl-opt-main'" in body or '"] .dl-opt-main' in body
    assert "+ size +" not in body, "the raw value must not reach the selector"


def test_a_selector_that_does_not_parse_only_hides_the_button():
    body = _body("dlShowChoice")
    assert "try {" in body and "catch (e)" in body
    assert body.index("try {") < body.index("querySelector('#dlSheet")
