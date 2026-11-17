"""Every paper a printer model offers has the pixel size of its real sheet.

The 15×20 entries used to carry 1535×2126 px — that is 13×18 cm at 300 dpi.
A design made from that canvas lands on a 6×8″ sheet stretched or with white
edges. The canvas has to follow the sheet's millimetres at the model's dpi,
and it has to be the same sheet the template editor offers (PAPIERE).
"""
from __future__ import annotations

import pytest

from boothcore.media.vorlagen_kern import PAPIERE
from boothcore.printing import models

#: One pixel of rounding either way, plus one for the 13×18 sheet whose
#: millimetres are not a whole number of pixels.
TOLERANZ_PX = 2


def _alle_medien():
    for m in models.MODELLE:
        for med in m["medien"]:
            yield m["id"], med


@pytest.mark.parametrize("mid,med", list(_alle_medien()), ids=lambda x: x if isinstance(x, str) else x["id"])
def test_every_paper_canvas_matches_its_millimetres(mid, med):
    assert med.get("mm"), f"{mid}/{med['id']} names no sheet size in mm"
    dpi = med.get("dpi") or 300
    soll = [round(mm / 25.4 * dpi) for mm in med["mm"]]
    for ist, erwartet in zip(med["canvas"], soll):
        assert abs(ist - erwartet) <= TOLERANZ_PX, (mid, med["id"], med["canvas"], soll)


@pytest.mark.parametrize("mid,med", [(m, x) for m, x in _alle_medien() if x.get("papier")],
                         ids=lambda x: x if isinstance(x, str) else x["id"])
def test_a_paper_with_a_template_sheet_is_that_sheet(mid, med):
    papier = PAPIERE[med["papier"]]
    assert med["canvas"] == papier["canvas"], (mid, med["id"])
    assert [round(x, 1) for x in med["mm"]] == [round(x, 1) for x in papier["mm"]], (mid, med["id"])


def test_fifteen_by_twenty_is_six_by_eight_inches_everywhere():
    """1800×2400 is the trim; the drivers' printable sizes are that plus their bleed."""
    for mid in ("dnp-ds620", "citizen-cx02", "mitsubishi-d70", "hiti-p525", "sony-updr80"):
        assert models.medium(mid, "15x20")["canvas"] == [1800, 2400], mid
    # Gutenprint's printable 6×8 sizes (DS620 1844×2436, CP-D70 1864×2422,
    # HiTi 1844×2434) are the trim plus a few millimetres of overprint.
    for druckbar in ((1844, 2436), (1864, 2422), (1844, 2434)):
        assert 0 < druckbar[0] - 1800 < 80 and 0 < druckbar[1] - 2400 < 80


def test_the_cut_six_by_eight_sheet_is_two_postcards():
    med = models.medium("dnp-ds620", "15x10-cut")
    assert med["canvas"] == [1800, 2400] and med["cut"] == 2
    assert models.erkenne_medium("dnp-ds620", "6x8*2") == "15x10-cut"
    assert models.erkenne_medium("dnp-ds620", "6x8") == "15x20"


def test_the_selphy_postcard_is_unchanged():
    """The SELPHY path is what runs at real events: not one value moves."""
    selphy = models.modell("selphy-cp")
    assert [(x["id"], x["sheets"], x["canvas"], x["cut"]) for x in selphy["medien"]] == [
        ("postcard", 108, [1200, 1800], 1)]
    assert models.medium("selphy-cp", None)["canvas"] == [1200, 1800]
    assert models.erkenne_medium("selphy-cp", "irgendwas") == "postcard"
    profil = models.druckprofil("selphy-cp")
    assert profil["randlos"] is True and profil["anschnitt_mm"] == 0.0 and profil["schnitt"] == "hand"
    assert profil["papiere"] == ["10x15", "10x15-quer", "streifen", "streifen-2x6"]
    assert models.verbrauch("selphy-cp") == {"tray": 18, "ribbon": 36}
