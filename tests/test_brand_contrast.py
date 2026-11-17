"""Die Vorlagen der Box müssen lesbar sein — gerechnet, nicht geschätzt.

Eine Vorlage ist ein Versprechen: Wer sie wählt, soll nicht prüfen müssen, ob
die Schrift auf dem Knopf noch zu lesen ist. Deshalb steht hier die Rechnung.

Maßstab ist WCAG 2.2 AA: 4,5:1 für Text, 3:1 für Bedienelemente. Am Kiosk
kommt dazu, dass der Gast zwei Meter entfernt steht und Partylicht auf dem
Panel liegt — die Werte liegen deshalb deutlich darüber.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "booth"))

from app.blueprints.admin_booth import THEME_PRESETS      # noqa: E402
from boothcore import config                              # noqa: E402


def _leuchtdichte(hexfarbe: str) -> float:
    h = hexfarbe.lstrip("#")
    kanaele = [int(h[i:i + 2], 16) / 255 for i in (0, 2, 4)]
    linear = [k / 12.92 if k <= 0.04045 else ((k + 0.055) / 1.055) ** 2.4 for k in kanaele]
    return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]


def kontrast(a: str, b: str) -> float:
    la, lb = _leuchtdichte(a), _leuchtdichte(b)
    hell, dunkel = max(la, lb), min(la, lb)
    return (hell + 0.05) / (dunkel + 0.05)


ALLE = [("Standard", config.DEFAULT_PROFILE["theme"])] + list(THEME_PRESETS)


@pytest.mark.parametrize("name,theme", ALLE)
def test_text_is_readable_on_every_surface(name, theme):
    assert kontrast(theme["text"], theme["bg"]) >= 4.5, name
    assert kontrast(theme["text"], theme["panel"]) >= 4.5, name


@pytest.mark.parametrize("name,theme", ALLE)
def test_the_quiet_line_is_still_a_line_not_a_hint(name, theme):
    """Unterzeilen sind Text, kein Dekor — auch sie müssen 4,5:1 schaffen."""
    assert kontrast(theme["muted"], theme["bg"]) >= 4.5, name


@pytest.mark.parametrize("name,theme", ALLE)
def test_the_button_stands_out_and_its_label_reads(name, theme):
    # Fläche gegen Grund: mindestens 3:1 (AA für Bedienelemente)
    assert kontrast(theme["accent"], theme["bg"]) >= 3.0, name
    # Schrift auf der Fläche: mindestens 4,5:1
    assert kontrast(theme["on_accent"], theme["accent"]) >= 4.5, name


def test_the_default_is_the_stage_of_the_brand():
    """Warm-dunkel mit Bernstein — nicht das alte Rot."""
    theme = config.DEFAULT_PROFILE["theme"]
    assert theme["accent"] == "#e7ad59"
    assert theme["bg"].lower() != "#0c0c0e"


def test_there_are_five_templates_and_they_differ():
    namen = [n for n, _ in THEME_PRESETS]
    assert len(namen) == 5 and len(set(namen)) == 5
    akzente = {t["accent"] for _, t in THEME_PRESETS}
    assert len(akzente) >= 4          # eine Vorlage darf den Hausakzent teilen
