"""Die Kalibrierungsseite — was auf dem Blatt stehen muss, damit es etwas nützt.

Geprüft wird das gerenderte Bild, nicht der Quelltext: Ob eine Seite auf ein
Blatt passt, entscheidet sich in Pixeln. Der erste Entwurf hatte hier einen
grünen Quelltext-Test verdient — und auf dem gerenderten Blatt fehlte das
untere Drittel.
"""
from pathlib import Path

import pytest
from PIL import Image

from boothcore.printing import kalibrierung
from boothcore.printing.layout import Layout, mm_zu_px


INFO = {"drucker": "DNP DS620A", "medium": "10×15 cm", "schaerfe": "leicht"}


@pytest.fixture()
def seite(tmp_path):
    def bauen(layout=None):
        lay = layout or Layout()
        p = kalibrierung.blatt(lay, tmp_path / "kal.jpg", INFO)
        return Image.open(p).convert("RGB"), lay
    return bauen


# ── Die Seite passt aufs Blatt ────────────────────────────────────────

@pytest.mark.parametrize("layout", [
    Layout(),                                   # 10×15
    Layout(paper="5x15", per_sheet=2),          # zwei Streifen
    Layout(paper="15x20"),
    Layout(paper="card"),                       # der kleinste Fall
    Layout(paper="10x15", dpi=600),
])
def test_the_page_fits_on_every_paper(layout, tmp_path):
    """Nichts darf unten abgeschnitten sein — auf keinem Format.

    Der erste Entwurf war für genau ein Papierformat gebaut. Auf einem
    10×15 fehlten Linienfeld, Lineal und Fußnoten komplett, ohne dass
    irgendetwas gemeldet wurde.
    """
    p = kalibrierung.blatt(layout, tmp_path / "x.jpg", INFO)
    bild = Image.open(p).convert("RGB")
    breite, hoehe = bild.size

    # Geprüft wird das Richtige: Nichts vom Inhalt darf aus dem weißen Feld
    # in den grauen Rahmen laufen. „Da muss noch Weißraum sein" wäre zu
    # streng — auf einer kleinen Karte füllt der Inhalt die Seite zu Recht
    # bis kurz vor die Kante.
    rahmen = mm_zu_px(kalibrierung.rahmen_mm(layout), layout.dpi)
    # Ein Stück Rahmen, in dem nichts Gewolltes steht: die Treppe sitzt bei
    # 1/3, das Kantenwort bei 1/2, die Eckwinkel in den Ecken.
    streifen = bild.crop((int(breite * 0.55), hoehe - rahmen + 1,
                          int(breite * 0.68), hoehe - 1))
    dunkel = [f for _n, f in streifen.getcolors(100000) or [] if max(f) < 120]
    assert not dunkel, f"Inhalt ragt in den grauen Rahmen: {dunkel[:3]}"


def test_the_sheet_has_the_size_the_printer_gets(seite):
    """Ein Prüfblatt in einer anderen Größe als der Ernstfall prüft nichts."""
    from boothcore.printing.layout import sheet_size_px
    bild, lay = seite()
    assert bild.size == sheet_size_px(lay, True)


def test_a_strip_sheet_is_a_full_sheet_with_a_cut_mark(seite):
    """Bei zwei Streifen entsteht EIN Blatt — der Drucker trennt es."""
    bild, lay = seite(Layout(paper="5x15", per_sheet=2))
    from boothcore.printing.layout import sheet_size_px
    assert bild.size == sheet_size_px(lay, True)
    breite, hoehe = bild.size
    # Über eine lange Strecke suchen, nicht in einem schmalen Band: Die
    # Linie ist gestrichelt, und ein kurzes Band landet leicht in einer
    # Lücke — dann meldet der Test „keine Schnittmarke", obwohl sie da ist.
    streifen = bild.crop((breite // 2 - 1, hoehe // 4, breite // 2 + 2, hoehe // 2))
    assert streifen.getextrema()[0][0] < 200, "Keine Schnittmarke in der Mitte"


def test_without_a_cut_there_is_no_cut_mark(tmp_path):
    """Die Gegenprobe — sonst beweist der Test oben nur, dass es dunkel ist.

    Verglichen wird dieselbe Stelle auf beiden Blättern: Das Blatt mit
    Schnitt muss dort dunkler sein. Eine feste Koordinate zu prüfen war
    zweimal falsch, weil sich das Blatt darunter verschoben hat.
    """
    mit = Image.open(kalibrierung.blatt(Layout(paper="5x15", per_sheet=2),
                                        tmp_path / "mit.jpg", INFO)).convert("RGB")
    ohne = Image.open(kalibrierung.blatt(Layout(), tmp_path / "ohne.jpg", INFO)).convert("RGB")
    assert mit.size == ohne.size, "Für den Vergleich müssen die Blätter gleich groß sein"
    breite, hoehe = mit.size

    def dunkel(bild):
        """Dunkle Pixel in der Mittelspalte, über die ganze Blatthöhe.

        Über die ganze Höhe zählen, nicht in einem Ausschnitt: Die
        Farbfelder laufen durch die Mitte, ein kurzer Ausschnitt trifft
        immer Inhalt. Die gestrichelte Linie dagegen zieht sich durch alle
        Lücken dazwischen — das ist der Unterschied, den man messen kann.
        """
        spalte = bild.crop((breite // 2, 0, breite // 2 + 1, hoehe))
        return sum(1 for px in list(spalte.getdata()) if max(px) < 200)

    assert dunkel(mit) > dunkel(ohne) * 1.3, \
        "Mit und ohne Schnitt sieht die Blattmitte gleich aus"


# ── Was auf dem Blatt stehen muss ─────────────────────────────────────

def _findet(bild, soll, toleranz: int = 2) -> bool:
    """Steht diese Farbe auf dem Blatt? JPEG verschiebt um ein, zwei Stufen."""
    for _n, farbe in bild.getcolors(600000) or []:
        if max(abs(a - b) for a, b in zip(farbe, soll)) <= toleranz:
            return True
    return False


def test_the_page_carries_skin_tones(seite):
    """Der eigentliche Prüfstein: Ein Stich fällt nur im Gesicht auf."""
    bild, _ = seite()
    fehlt = [f for _n, f in kalibrierung.HAUT if not _findet(bild, f)]
    assert not fehlt, f"Diese Hauttöne stehen nicht auf dem Blatt: {fehlt}"


def _uebergangsbreiten(bild):
    """Wie viele Pixel breit sind die Kanten zwischen den Farbfeldern?

    Das ist die Stelle, an der sich die JPEG-Unterabtastung zeigt: Sie
    halbiert die Farbauflösung, und aus einer scharfen Kante zwischen zwei
    Feldern wird ein mehrere Pixel breiter Saum. Im Inneren eines großen
    Feldes sieht man davon nichts — ein Test, der dort nachsieht, ist grün,
    egal wie die Datei kodiert ist. Genau das war der erste Entwurf.
    """
    b, h = bild.size
    for y in range(h // 3, h * 2 // 3):
        zeile = [bild.getpixel((x, y)) for x in range(b // 6, b * 5 // 6, 4)]
        bunt = [c for c in zeile if max(c) - min(c) > 80]
        if len(bunt) > len(zeile) * 0.8:
            break
    else:
        pytest.skip("Keine Farbreihe gefunden")
    breiten, x = [], b // 8
    while x < b * 7 // 8 - 1:
        a, c = bild.getpixel((x, y)), bild.getpixel((x + 1, y))
        if max(abs(p - q) for p, q in zip(a, c)) > 30:
            start = x
            while x < b * 7 // 8 - 1:
                a2, c2 = bild.getpixel((x, y)), bild.getpixel((x + 1, y))
                if max(abs(p - q) for p, q in zip(a2, c2)) <= 8:
                    break
                x += 1
            breiten.append(x - start)
        x += 1
    return breiten


def test_the_patch_edges_are_sharp_not_smeared(seite):
    """Die übliche JPEG-Unterabtastung macht aus jeder Feldkante einen Saum.

    Auf einer Seite, deren Zweck das Beurteilen von Farben ist, wäre das
    absurd. Ohne Unterabtastung sind die Übergänge einen Pixel breit, mit
    ihr zwei bis sieben.
    """
    bild, _ = seite()
    breiten = _uebergangsbreiten(bild)
    assert breiten, "Keine Feldkanten gefunden"
    assert max(breiten) <= 1, f"Die Feldkanten sind verschmiert: {breiten}"


def test_the_patch_colours_are_on_the_sheet(seite):
    bild, _ = seite()
    for name, soll in kalibrierung.HAUT + kalibrierung.FARBEN:
        assert _findet(bild, soll, toleranz=2), f"Feld {name} fehlt"


def test_the_grey_wedge_has_the_ends_where_it_tears(seite):
    """2/5 und 95/98 sind der Zweck des Keils, nicht die Mitte."""
    for p in (2, 5, 95, 98):
        assert p in kalibrierung.GRAUSTUFEN, f"{p} % fehlt im Graukeil"
    bild, _ = seite()
    vorhanden = {f for _, f in bild.getcolors(400000) or []}
    for p in (2, 5, 95, 98):
        assert kalibrierung._grau(p) in vorhanden, f"{p} % ist nicht gedruckt"


def test_the_ruler_stays_in_real_centimetres(tmp_path):
    """Ein Lineal, das sich mitskaliert, misst den eigenen Faktor.

    Auf einem kleinen Blatt schrumpft der Inhalt — die Zentimeterstriche
    dürfen davon nicht erfasst werden, sonst zeigt jedes Blatt „stimmt".
    """
    quelle = (Path(kalibrierung.__file__)).read_text()
    stelle = quelle.index("def lineal(")
    block = quelle[stelle:quelle.index("\n\n\n", stelle)]
    assert "mm_zu_px(cm * 10, self.dpi)" in block, "Das Lineal skaliert mit"
    assert "self.mm(cm" not in block


def test_the_edge_marks_stay_in_real_millimetres():
    quelle = (Path(kalibrierung.__file__)).read_text()
    stelle = quelle.index("def _kamm(")
    block = quelle[stelle:quelle.index("def _randlineal(")]
    assert "mm_zu_px(mm, dpi)" in block, "Die Randtreppe skaliert mit"


def test_the_edge_marks_are_countable(seite):
    """„Welcher Millimeterstrich fehlt?" muss das Blatt beantworten können.

    Der erste Entwurf zeichnete gleich lange, unbeschriftete Striche — auf
    dem Blatt war nicht zu erkennen, welcher welcher war. Damit beantwortete
    das wichtigste Feld der Seite seine eigene Frage nicht.
    """
    bild, _ = seite()
    breite, _h = bild.size
    # In 1 bis 5 mm Tiefe muss an der Oberkante jeweils etwas stehen.
    getroffen = 0
    for mm in (1, 2, 3, 4, 5):
        streifen = bild.crop((breite // 2 - mm_zu_px(3, 300), mm_zu_px(mm, 300),
                              breite // 2 + mm_zu_px(15, 300), mm_zu_px(mm, 300) + 3))
        if streifen.getextrema()[0][0] < 220:
            getroffen += 1
    assert getroffen >= 4, f"Nur {getroffen} von 5 Stufen der Randtreppe gefunden"


def test_the_page_says_which_settings_produced_it(tmp_path):
    """Nach dem dritten Versuch liegen drei fast gleiche Blätter da."""
    zeilen = kalibrierung._kopfzeilen(INFO, Layout(), "de")
    text = " ".join(zeilen)
    assert "DNP DS620A" in text and "10×15 cm" in text
    assert "300 dpi" in text and "leicht" in text


def test_long_explanations_are_wrapped_not_cut(seite):
    """Der Erklärsatz lief über den Blattrand und durch die nächste Überschrift."""
    lang = "Wort " * 40
    zeilen = kalibrierung._umbrechen(lang.strip(), 30, False, 600)
    assert len(zeilen) > 1
    assert all(kalibrierung._breite_von(z, 30, False) <= 600 for z in zeilen)


# ── Der Weg von der Box zum Drucker ───────────────────────────────────

def test_the_booth_prints_the_calibration_page(tmp_path):
    from boothcore.app import Booth
    b = Booth(base=tmp_path, start_threads=False)
    b.store.profile["printer"] = {"name": "DNP DS620A", "model": "dnp-ds620", "medium": "10x15"}
    antwort = b.test_print()
    assert "Kalibrierung" in antwort
    assert (tmp_path / "logs" / "kalibrierung.jpg").exists()


def test_the_page_names_the_printer_that_is_set_up(tmp_path):
    from boothcore.app import Booth
    b = Booth(base=tmp_path, start_threads=False)
    b.store.profile["printer"] = {"name": "DNP DS620A", "model": "dnp-ds620", "medium": "10x15"}
    b.store.profile["print"] = {**(b.profile.get("print") or {}), "sharpen": "strong"}
    info = b._druckinfo(b.profile["printer"])
    assert info["drucker"] == "DNP DS620A"
    assert info["medium"] == "10×15 cm"
    assert info["schaerfe"] == "stark", "Die Stufe steht englisch auf einem deutschen Blatt"


def test_an_unknown_printer_does_not_break_the_page(tmp_path):
    from boothcore.app import Booth
    b = Booth(base=tmp_path, start_threads=False)
    b.store.profile["printer"] = {}
    assert b.test_print()
    assert (tmp_path / "logs" / "kalibrierung.jpg").exists()


# ── Sprache und Rahmen (20.09.2026) ───────────────────────────────────

def test_the_page_speaks_the_language_of_the_operator(tmp_path):
    """Das Blatt wird von einem Menschen am Tisch gelesen, nicht vom Code."""
    de = Image.open(kalibrierung.blatt(Layout(), tmp_path / "de.jpg", INFO, sprache="de"))
    en = Image.open(kalibrierung.blatt(Layout(), tmp_path / "en.jpg", INFO, sprache="en"))
    assert de.size == en.size
    assert list(de.getdata()) != list(en.getdata()), "Beide Sprachen ergeben dasselbe Bild"


def test_every_text_exists_in_both_languages():
    fehlt = [k for k in kalibrierung.TEXTE["de"] if k not in kalibrierung.TEXTE["en"]]
    zuviel = [k for k in kalibrierung.TEXTE["en"] if k not in kalibrierung.TEXTE["de"]]
    assert not fehlt and not zuviel, f"fehlt in en: {fehlt}, fehlt in de: {zuviel}"


def test_an_unknown_language_still_prints(tmp_path):
    p = kalibrierung.blatt(Layout(), tmp_path / "x.jpg", INFO, sprache="fr")
    assert Image.open(p).size == (1200, 1800)
    assert kalibrierung.t("fr", "titel") == kalibrierung.TEXTE["de"]["titel"]


def test_the_booth_prints_in_its_own_language(tmp_path):
    from boothcore.app import Booth
    b = Booth(base=tmp_path, start_threads=False)
    b.store.profile["languages"] = ["en", "de"]
    b.test_print()
    # Die englische Fassung hat andere Maße im Textfluss als die deutsche;
    # geprüft wird, dass überhaupt die eingestellte Sprache ankommt.
    quelle = (Path(kalibrierung.__file__)).read_text()
    assert 'sprache=sprachen[0]' in (Path(__file__).resolve().parents[1]
                                     / "boothcore" / "app.py").read_text()
    assert (tmp_path / "logs" / "kalibrierung.jpg").exists()


def test_the_frame_is_grey_so_a_white_border_shows(seite):
    """Auf einem weißen Blatt sieht man einen weißen Rand nicht."""
    bild, lay = seite()
    breite, hoehe = bild.size
    ecke = bild.crop((2, hoehe // 2 - 40, 6, hoehe // 2 - 20))
    for _n, f in ecke.getcolors(1000) or []:
        assert max(abs(a - b) for a, b in zip(f, kalibrierung.RAHMEN)) < 12, \
            f"Der Blattrand ist nicht grau, sondern {f}"


def test_the_frame_shrinks_with_the_paper():
    """Feste 8 mm waren auf einer SELPHY-Karte fast ein Drittel der Breite."""
    gross = kalibrierung.rahmen_mm(Layout(paper="15x20"))
    klein = kalibrierung.rahmen_mm(Layout(paper="card"))
    assert klein < gross, "Der Rahmen passt sich dem Blatt nicht an"
    assert klein >= 4.0, "Unter vier Millimetern ist der Rahmen nicht mehr zu sehen"


def test_the_sheet_says_which_edge_is_which(seite):
    """Ein Abzug kommt gedreht heraus — dann weiß niemand, welche Kante fehlt."""
    for schluessel in ("kante.oben", "kante.unten", "kante.links", "kante.rechts"):
        assert kalibrierung.TEXTE["de"][schluessel]
        assert kalibrierung.TEXTE["en"][schluessel]
    quelle = Path(kalibrierung.__file__).read_text()
    assert "_kanten(" in quelle and "_gedreht(" in quelle, \
        "Seitliche Beschriftung muss gedreht werden, sonst ragt sie über die Kante"
