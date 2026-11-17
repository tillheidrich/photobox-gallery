"""Die Programme tragen das Zeichen — auch in der Taskleiste.

Eine .exe ohne Symbol sieht auf dem Event-PC aus wie ein Fremdkörper, und im
Zweifel klickt jemand das Falsche an. Geprüft wird deshalb, dass die Dateien
da sind, die die Bauplandateien erwarten, und dass sie die Größen enthalten,
die Windows verlangt (16 für die Titelleiste bis 256 für große Kacheln).
"""
from pathlib import Path

import pytest
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
WINDOWS = ROOT / "packaging" / "windows"


@pytest.mark.parametrize("name", ["photobox.ico", "sync.ico"])
def test_the_icon_exists_and_covers_every_size(name):
    datei = WINDOWS / name
    assert datei.is_file(), f"{name} fehlt — die Bauplandatei zeigt darauf"
    with Image.open(datei) as im:
        groessen = {s[0] for s in im.info.get("sizes", [])}
    for noetig in (16, 32, 48, 256):
        assert noetig in groessen, f"{name}: {noetig} px fehlt"


@pytest.mark.parametrize("spec,icon", [("photobox-booth.spec", "photobox.ico"),
                                       ("photobox-sync.spec", "sync.ico")])
def test_the_build_file_points_at_it(spec, icon):
    text = (WINDOWS / spec).read_text(encoding="utf-8")
    assert icon in text, spec
