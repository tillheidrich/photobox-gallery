"""Was die üblichen Eventdrucker können — und was sie von sich preisgeben.

Gedruckt wird überall über den Herstellertreiber (Windows) beziehungsweise
CUPS/IPP (Linux); daran ändert diese Datei nichts. Sie beantwortet die zwei
Fragen, die der Betreiber beim Einrichten hat und die sonst niemand
beantwortet:

* **Wie viele Blatt sind auf einer Kassette/einem Band?** Fotodrucker melden
  das nicht. Steht das Modell hier, kennt die Box die Zahl und zählt herunter,
  statt den Betreiber raten zu lassen.
* **Welche Formate gibt es überhaupt?** Ein SELPHY kann genau eins — jede
  Formatauswahl daneben ist eine Falle. Ein DS620 kann fünf, und der
  2-Zoll-Schnitt gehört dazu.

``meldet`` sagt ehrlich, was die Box vom Drucker erfährt:

    "status"  — bereit/Fehler/Papier leer kommen vom Treiber (Windows, CUPS)
    "supply"  — zusätzlich Füllstände (CUPS/IPP ``marker-levels``)
    "nichts"  — der Drucker sagt gar nichts; alles, was die Box zeigt, ist
                mitgezählt

Die Zahlen stammen aus den Datenblättern der Hersteller (Medienpackungen).
Sie sind Voreinstellungen, kein Gesetz: Der Betreiber kann sie überschreiben,
und bei Rollenpapier (Epson) hängt die Zahl von der Rolle ab.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

#: Ein Blatt-Format: Name, Blätter je Packung, Leinwand in Pixeln bei 300 dpi.
#: ``cut`` heißt: Der Drucker schneidet das Blatt selbst in Streifen.
#: ``mm`` is the trim size of the whole sheet (before a cut); ``canvas`` must
#: be that size at 300 dpi (test_paper_sizes_match_their_mm). ``papier`` names
#: the same sheet in vorlagen_kern.PAPIERE when the template editor has it, so
#: a template made for that paper and this medium are one size.

_POSTKARTE = [101.6, 152.4]   # 4×6″, what every dye-sub driver calls "10×15"
_6X8 = [152.4, 203.2]         # 6×8″, what the drivers call "15×20"
_13X18 = [130.0, 180.0]       # 5×7″ class; metric sheet, 1535×2126 px


def _m(mid: str, name: str, blatt: int, canvas: List[int], cut: int = 1,
       mm: Optional[List[float]] = None, papier: Optional[str] = None) -> Dict[str, Any]:
    eintrag = {"id": mid, "name": name, "sheets": blatt, "canvas": canvas, "cut": cut, "mm": mm or []}
    if papier:
        eintrag["papier"] = papier
    return eintrag


MODELLE: List[Dict[str, Any]] = [
    {
        "id": "selphy-cp", "marke": "Canon", "name": "SELPHY CP1300 / CP1500",
        "meldet": "status", "sekunden": 47,
        "medien": [_m("postcard", "Postkarte 10×15 (KP-108IN)", 108, [1200, 1800], mm=_POSTKARTE, papier="10x15")],
        # The operator is needed at two moments, not at 108 (the retail
        # pack): the paper tray is empty, and the ink ribbon is used up.
        # Both numbers are from memory, to be confirmed at the device; they
        # are defaults the operator can change per printer (tray_total,
        # ribbon_total).
        "supplies": {"tray": 18, "ribbon": 36},
        "hinweis": "Kann genau ein Format. Die Formatauswahl am Gast gehört deshalb aus.",
    },
    {
        "id": "selphy-square", "marke": "Canon", "name": "SELPHY Square QX10/QX20",
        "meldet": "status", "sekunden": 43,
        "medien": [_m("square", "Quadrat 68×68 (XS-20L)", 20, [803, 803], mm=[68.0, 68.0])],
    },
    {
        "id": "dnp-ds620", "marke": "DNP", "name": "DS620A",
        "meldet": "status", "sekunden": 9,
        "medien": [_m("10x15", "10×15 cm", 400, [1200, 1800], mm=_POSTKARTE, papier="10x15"),
                   _m("15x20", "15×20 cm", 220, [1800, 2400], mm=_6X8, papier="15x20"),
                   _m("10x15-cut", "2× 5×15 cm (geschnitten)", 800, [1200, 1800], cut=2, mm=_POSTKARTE, papier="10x15"),
                   _m("15x10-cut", "2× 10×15 cm (15×20 geschnitten)", 440, [1800, 2400], cut=2, mm=_6X8, papier="15x20")],
        "hinweis": "Der 2-Zoll-Schnitt kommt aus dem Treiber; die Vorlage bleibt ein volles Blatt.",
    },
    {
        "id": "dnp-rx1", "marke": "DNP", "name": "DS-RX1HS",
        "meldet": "status", "sekunden": 13,
        "medien": [_m("10x15", "10×15 cm", 700, [1200, 1800], mm=_POSTKARTE, papier="10x15"),
                   _m("10x15-cut", "2× 5×15 cm (geschnitten)", 1400, [1200, 1800], cut=2, mm=_POSTKARTE, papier="10x15")],
    },
    {
        "id": "dnp-qw410", "marke": "DNP", "name": "QW410",
        "meldet": "status", "sekunden": 12,
        "medien": [_m("10x10", "10×10 cm", 330, [1200, 1200], mm=[101.6, 101.6], papier="quadrat"),
                   _m("10x15", "10×15 cm", 220, [1200, 1800], mm=_POSTKARTE, papier="10x15"),
                   _m("10x20", "10×20 cm", 165, [1200, 2400], mm=[101.6, 203.2])],
    },
    {
        "id": "citizen-cx02", "marke": "Citizen", "name": "CX-02 / CX-02S",
        "meldet": "status", "sekunden": 12,
        "medien": [_m("10x15", "10×15 cm", 600, [1200, 1800], mm=_POSTKARTE, papier="10x15"),
                   _m("15x20", "15×20 cm", 350, [1800, 2400], mm=_6X8, papier="15x20")],
    },
    {
        "id": "mitsubishi-d70", "marke": "Mitsubishi", "name": "CP-D70DW / CP-D80DW",
        "meldet": "status", "sekunden": 9,
        "medien": [_m("10x15", "10×15 cm", 400, [1200, 1800], mm=_POSTKARTE, papier="10x15"),
                   _m("15x20", "15×20 cm", 230, [1800, 2400], mm=_6X8, papier="15x20")],
    },
    {
        "id": "hiti-p525", "marke": "HiTi", "name": "P525L / P720L",
        "meldet": "status", "sekunden": 13,
        "medien": [_m("10x15", "10×15 cm", 500, [1200, 1800], mm=_POSTKARTE, papier="10x15"),
                   _m("15x20", "15×20 cm", 250, [1800, 2400], mm=_6X8, papier="15x20")],
    },
    {
        "id": "epson-d700", "marke": "Epson", "name": "SureLab D700 / D800 (Rolle)",
        "meldet": "supply", "sekunden": 8,
        "medien": [_m("10x15", "10×15 cm (Rolle)", 0, [1200, 1800], mm=_POSTKARTE, papier="10x15"),
                   _m("13x18", "13×18 cm (Rolle)", 0, [1535, 2126], mm=_13X18, papier="13x18")],
        "hinweis": "Rollenpapier: Die Zahl hängt an der Rolle — Tintenstände meldet der Drucker selbst.",
    },
    {
        "id": "epson-tinte", "marke": "Epson", "name": "EcoTank / Expression (Tinte)",
        "meldet": "supply", "sekunden": 60,
        "medien": [_m("10x15", "10×15 cm", 0, [1200, 1800], mm=_POSTKARTE, papier="10x15"),
                   _m("a4", "A4", 0, [2480, 3508], mm=[210.0, 297.0])],
        "hinweis": "Bürodrucker: langsam und randlos nur mit passendem Papier — für Events die Notlösung.",
    },
    {
        "id": "hp-tinte", "marke": "HP", "name": "HP (Tinte, Büro/Foto)",
        "meldet": "supply", "sekunden": 60,
        "medien": [_m("10x15", "10×15 cm", 0, [1200, 1800], mm=_POSTKARTE, papier="10x15"),
                   _m("a4", "A4", 0, [2480, 3508], mm=[210.0, 297.0])],
    },
    {
        "id": "hp-sprocket", "marke": "HP", "name": "Sprocket (Zink, 5×7,6)",
        "meldet": "nichts", "sekunden": 40,
        "medien": [_m("zink", "Zink 5×7,6 cm", 20, [600, 900], mm=[50.8, 76.2])],
    },
    {
        "id": "sony-updr80", "marke": "Sony", "name": "UP-DR80MD / UP-DR200",
        "meldet": "status", "sekunden": 14,
        "medien": [_m("10x15", "10×15 cm", 350, [1200, 1800], mm=_POSTKARTE, papier="10x15"),
                   _m("15x20", "15×20 cm", 175, [1800, 2400], mm=_6X8, papier="15x20")],
    },
    {
        "id": "andere", "marke": "", "name": "Anderer Drucker",
        "meldet": "nichts", "sekunden": 47,
        "medien": [_m("10x15", "10×15 cm", 0, [1200, 1800], mm=_POSTKARTE, papier="10x15"),
                   _m("frei", "anderes Format", 0, [1200, 1800], mm=_POSTKARTE)],
        "hinweis": "Blätter je Kassette selbst eintragen — die Box zählt dann herunter.",
    },
]

NACH_ID = {m["id"]: m for m in MODELLE}

#: Woran man ein Modell im Namen erkennt, den das Betriebssystem meldet.
#: Die Reihenfolge ist Absicht: Das Genaue steht vor dem Allgemeinen, sonst
#: würde „HP" schon greifen, bevor „Sprocket" drankommt. Erkannt wird nur,
#: was hier steht — geraten wird nichts.
MUSTER: List[tuple] = [
    ("selphy-square", ["selphy square", "selphy qx", "qx10", "qx20"]),
    ("selphy-cp", ["selphy cp", "cp1500", "cp1300", "cp1200", "cp910", "selphy"]),
    ("dnp-ds620", ["ds620", "ds-620"]),
    ("dnp-rx1", ["rx1hs", "ds-rx1", "dsrx1"]),
    ("dnp-qw410", ["qw410", "qw-410"]),
    ("citizen-cx02", ["cx-02", "cx02"]),
    ("mitsubishi-d70", ["cp-d70", "cpd70", "cp-d80", "cpd80"]),
    ("hiti-p525", ["p525", "p720", "hiti"]),
    ("hp-sprocket", ["sprocket"]),
    ("sony-updr80", ["up-dr", "updr", "up-cr"]),
    ("epson-d700", ["surelab", "sl-d7", "sl-d8", "d700", "d800"]),
    ("epson-tinte", ["epson", "ecotank", "expression", "xp-", "et-"]),
    ("hp-tinte", ["deskjet", "officejet", "envy", "photosmart", "laserjet", "hp "]),
]

#: Wie ein Medium sonst noch heißen kann. Der Treiber sagt „4x6" oder
#: „Postcard", die Box nennt es „10x15" — dasselbe Blatt.
ANDERE_NAMEN: Dict[str, List[str]] = {
    "10x15": ["10x15", "10×15", "4x6", "6x4", "4 x 6", "postcard", "postkarte", "100x148", "100 x 148"],
    "postcard": ["postcard", "postkarte", "10x15", "10×15", "4x6", "6x4", "100x148"],
    "13x18": ["13x18", "13×18", "5x7", "7x5"],
    "15x20": ["15x20", "15×20", "8x10", "10x8", "6x8", "8x6"],
    "10x10": ["10x10", "10×10", "4x4"],
    "10x20": ["10x20", "10×20", "4x8", "8x4"],
    "square": ["square", "quadrat", "68x68", "2.7x2.7"],
    "zink": ["zink", "2x3", "5x7.6", "sprocket"],
    "a4": ["a4", "210x297"],
    "10x15-cut": ["2x6", "2×6", "5x15", "geschnitten", "cut", "strip"],
    # A 6×8″ sheet cut once gives two 4×6″ prints (the drivers' "6x8*2");
    # the old "7,5×20" name described a cut the DS620 does not make.
    "15x10-cut": ["6x8*2", "8x6*2", "cut", "geschnitten"],
}


def erkenne(druckername: Optional[str]) -> Optional[str]:
    """Welches Modell steckt hinter diesem Druckernamen? ``None`` = unklar.

    Zurückgegeben wird nie ``andere``: Ein „ich weiß es nicht" soll sich von
    einem „ich habe nachgesehen und es ist ein DS620" unterscheiden lassen.
    """
    text = str(druckername or "").lower()
    if not text.strip():
        return None
    for mid, muster in MUSTER:
        if any(m in text for m in muster):
            return mid
    return None


def erkenne_medium(mid: Optional[str], text: Optional[str]) -> Optional[str]:
    """Welches Medium ist eingelegt — abgelesen an dem, was der Treiber nennt.

    ``text`` ist, was das System über das Papier sagt (Formatname aus CUPS
    oder Windows). Kann ein Modell ohnehin nur ein Medium, ist die Antwort
    dieses eine: Bei einem SELPHY gibt es nichts zu erkennen.
    """
    m = modell(mid)
    if len(m["medien"]) == 1:
        return m["medien"][0]["id"]
    roh = str(text or "").lower().replace(" ", "").replace("×", "x")
    if not roh:
        return None
    # Der Schnitt zuerst: „2x6" steht auf einem Blatt, das auch „10x15" ist.
    fuer = sorted(m["medien"], key=lambda x: (0 if x["cut"] > 1 else 1))
    for med in fuer:
        namen = ANDERE_NAMEN.get(med["id"], []) + [med["id"], med["name"]]
        if any(n.lower().replace(" ", "").replace("×", "x") in roh for n in namen if n):
            return med["id"]
    return None


def modell(mid: Optional[str]) -> Dict[str, Any]:
    return NACH_ID.get(str(mid or ""), NACH_ID["andere"])


def medium(mid: Optional[str], medium_id: Optional[str]) -> Dict[str, Any]:
    m = modell(mid)
    for med in m["medien"]:
        if med["id"] == str(medium_id or ""):
            return med
    return m["medien"][0]


def blatt_je_kassette(mid: Optional[str], medium_id: Optional[str]) -> int:
    """Wie viele Blätter eine volle Kassette hergibt. 0 = unbekannt (Rolle, Tinte)."""
    return int(medium(mid, medium_id).get("sheets") or 0)


def verbrauch(mid: Optional[str]) -> Dict[str, int]:
    """Tray and ribbon capacity of a model, ``{}`` when it does not define both.

    Only a model that names both gets two counters; every other model keeps
    the single count per cassette.
    """
    s = modell(mid).get("supplies") or {}
    tray, ribbon = int(s.get("tray") or 0), int(s.get("ribbon") or 0)
    return {"tray": tray, "ribbon": ribbon} if tray > 0 and ribbon > 0 else {}


# ── Druckprofile: which template papers a model takes ─────
# A template is designed for a paper of vorlagen_kern.PAPIERE (trim size and
# aspect family); the profile says which of them a printer can print, whether
# it prints borderless, what it does to the edge, and who cuts a strip.
#
# Sources (checked 15.11.2026):
#   GP  = Gutenprint, src/main/print-dyesub.c (open-source driver; page and
#         imaging sizes per model at 300 dpi), https://sourceforge.net/p/
#         gimp-print/source/ci/master/tree/src/main/print-dyesub.c
#   CAN = Canon, SELPHY CP1500 specifications,
#         https://www.canon.co.uk/printers/selphy-cp1500/specifications/
#   DNP = DNP DS620 product page of the distributor Imaging Spectrum,
#         https://imagingspectrum.com/dnp-ds620
# "anschnitt_mm" is what the driver prints beyond the trim (GP imaging size
# minus trim, per side), the bleed a design for that printer should carry.
# It stays 0 for the SELPHY: the booth has always sent 1200×1800 and let
# Canon's driver scale to its 1248×1872 (GP), and that does not change.

_PROFIL_SELPHY = {"randlos": True, "anschnitt_mm": 0.0, "schnitt": "hand",
                  "papiere": ["10x15", "10x15-quer", "streifen", "streifen-2x6"],
                  "quelle": "CAN; GP cp910 (CP1300/CP1500: Postcard 1248×1872 px)"}

DRUCKPROFILE: Dict[str, Dict[str, Any]] = {
    "selphy-cp": _PROFIL_SELPHY,
    "selphy-square": {"randlos": True, "anschnitt_mm": 0.0, "schnitt": "keiner", "papiere": [],
                      "quelle": "nicht belegt (68×68 mm hat kein Vorlagenformat)"},
    "dnp-ds620": {"randlos": True, "anschnitt_mm": 1.9, "schnitt": "drucker",
                  "papiere": ["10x15", "10x15-quer", "streifen", "streifen-2x6", "13x18", "13x18-quer",
                              "15x20", "15x20-quer", "15x23"],
                  "quelle": "GP dnpds620 (4×6 = 1240×1844 px druckbar, 6×8 = 1844×2436, 6×9 = 1844×2740, "
                            "2×6*2); DNP"},
    "dnp-rx1": {"randlos": True, "anschnitt_mm": 1.9, "schnitt": "drucker",
                "papiere": ["10x15", "10x15-quer", "streifen", "streifen-2x6", "13x18", "13x18-quer",
                            "15x20", "15x20-quer"],
                "quelle": "GP dnpsrx1 (4×6 = 1240×1844, 6×8 = 1844×2436, 2×6*2)"},
    "dnp-qw410": {"randlos": True, "anschnitt_mm": 1.5, "schnitt": "keiner",
                  "papiere": ["10x15", "10x15-quer", "quadrat", "11x20"],
                  "quelle": "GP dnp_qw410 (4×6 = 1236×1836, 4×4, 4×8 = 1236×2436, 4,5×8; kein 2×6-Schnitt)"},
    "citizen-cx02": {"randlos": True, "anschnitt_mm": 3.0, "schnitt": "drucker",
                     "papiere": ["10x15", "10x15-quer", "streifen", "15x20", "15x20-quer"],
                     "quelle": "nicht belegt (CX-02 fehlt in GP; Vorgabe 3 mm Anschnitt, 2×6 ungeprüft)"},
    "mitsubishi-d70": {"randlos": True, "anschnitt_mm": 2.7, "schnitt": "drucker",
                       "papiere": ["10x15", "10x15-quer", "streifen", "streifen-2x6", "13x18", "13x18-quer",
                                   "15x20", "15x20-quer", "15x23"],
                       "quelle": "GP mitsu_cpd70x/cpd80 (4×6 = 1228×1864, 5×7 = 1568×2128, 6×8 = 1864×2422, "
                                 "6×9 = 1864×2730, 2×6*2); CP-D90: 4×6 = 1226×1852"},
    "hiti-p525": {"randlos": True, "anschnitt_mm": 1.9, "schnitt": "drucker",
                  "papiere": ["10x15", "10x15-quer", "streifen", "streifen-2x6", "13x18", "13x18-quer",
                              "15x20", "15x20-quer", "15x23"],
                  "quelle": "GP hiti_p520l (P520L/P525L: 4×6 = 1240×1844, 5×7 = 1548×2140, "
                            "6×8 = 1844×2434, 6×9 = 1844×2740, 2×6*2)"},
}

_SCHNITT_TEXT = {"drucker": "Streifen schneidet der Drucker (2-Zoll-Schnitt im Treiber einschalten)",
                 "hand": "Streifen von Hand in der Mitte schneiden",
                 "keiner": "keine Streifen"}


def druckprofil(mid: Optional[str]) -> Dict[str, Any]:
    """The profile of a model; an unknown or missing model gets today's SELPHY behaviour."""
    roh = DRUCKPROFILE.get(str(mid or ""))
    if roh is None:
        # Models without a checked profile (Epson, HP, Sony, "andere") print
        # what they printed so far: the postcard, unchanged.
        roh = _PROFIL_SELPHY if str(mid or "") in ("", "selphy-cp") or str(mid) not in NACH_ID else dict(
            _PROFIL_SELPHY, schnitt="hand", quelle="nicht belegt (Vorgabe wie SELPHY)")
    profil = dict(roh)
    profil["papiere"] = list(roh["papiere"])
    teile = ["randlos" if profil["randlos"] else "mit Rand"]
    if profil["anschnitt_mm"]:
        teile.append(f"Überdruck ca. {str(profil['anschnitt_mm']).replace('.', ',')} mm je Seite")
    teile.append(_SCHNITT_TEXT.get(profil["schnitt"], ""))
    profil["text"] = " · ".join(t for t in teile if t)
    return profil


def kann_papier(mid: Optional[str], papier: Optional[str]) -> bool:
    """Does this printer take the paper a template is made for? Unknown paper = yes (the old way)."""
    if not papier:
        return True
    return str(papier) in druckprofil(mid)["papiere"]


def meldet_bestand(mid: Optional[str]) -> bool:
    """Sagt dieses Modell selbst, wie viel noch da ist?"""
    return modell(mid).get("meldet") == "supply"


def auswahl() -> List[Dict[str, Any]]:
    """Für die Oberfläche: Marke, Name, Medien — ohne die Kommentare."""
    return [{"id": m["id"], "marke": m["marke"], "name": m["name"], "meldet": m["meldet"],
             "sekunden": m["sekunden"], "hinweis": m.get("hinweis", ""),
             "supplies": verbrauch(m["id"]),
             "profil": druckprofil(m["id"]),
             "medien": [{"id": x["id"], "name": x["name"], "sheets": x["sheets"], "cut": x["cut"]}
                        for x in m["medien"]]}
            for m in MODELLE]
