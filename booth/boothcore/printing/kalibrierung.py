"""Die Kalibrierungsseite — ein Blatt, das die Fragen vor dem Event beantwortet.

Der bisherige Testdruck war ein weißes Blatt mit einem Rahmen und einem Datum.
Er beantwortete genau eine Frage: „Kommt überhaupt etwas heraus?" Die Fragen,
die der Betreiber am Abend vorher wirklich hat, beantwortete er nicht:

* **Wie viel schneidet der Drucker weg?** Randlos heißt bei Thermosublimation:
  Das Blatt wird größer bedruckt als es ist, der Überstand fällt ab. Wie viel,
  sagt kein Datenblatt verlässlich. Wer das nicht weiß, setzt den Namen des
  Brautpaars zu weit außen — und auf dem Abzug fehlt der halbe Buchstabe.
* **Stimmt die Größe?** Ein Treiber, der auf „an Seite anpassen" steht,
  verkleinert stillschweigend um ein paar Prozent. Am Bildschirm sieht man das
  nicht, mit einem Lineal auf dem Abzug sofort.
* **Stimmen die Farben — und zwar die, auf die es ankommt?** Niemand bemerkt
  einen Stich im Cyan. Jeder bemerkt ihn im Gesicht. Deshalb steht hier eine
  Reihe Hauttöne, und sie ist der eigentliche Prüfstein.
* **Reißt es oben und unten ab?** Sublimationsdrucker verlieren gern die
  obersten und untersten Tonwerte: Aus 2 % und 5 % Grau wird beides Weiß.
  Dann verschwinden Falten im Brautkleid und Zeichnung im dunklen Anzug.
* **Ist das Nachschärfen zu stark?** Die Linienpaare zeigen Säume, bevor
  dreihundert Gäste sie auf ihren Gesichtern haben.

Das Blatt entsteht in der **echten** Blattgröße aus dem eingestellten Layout —
gleiches Papier, gleicher Schnitt, gleiche Auflösung. Eine Prüfseite, die
einen anderen Weg nimmt als der Ernstfall, prüft nichts.

Gebaut wird in zwei Durchgängen: Der erste misst nur, der zweite zeichnet.
Dazwischen steht ein Faktor, der alles gemeinsam auf das Blatt schrumpft oder
streckt. Ohne ihn passt die Seite auf genau ein Papierformat, und auf jedem
anderen fehlt unten die Hälfte — das war beim ersten Entwurf genau so, und es
fiel erst auf, als das gerenderte Blatt vor mir lag.
"""
from __future__ import annotations

import time
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

from PIL import Image, ImageDraw

from .layout import Layout, PAPERS, mm_zu_px, sheet_size_px

SCHWARZ = (17, 17, 17)
GRAU = (120, 120, 120)
WEISS = (255, 255, 255)
#: Der Rahmen ist grau, nicht weiß. Abgeschaut bei dslrBooth, und der Grund
#: ist zwingend: Auf einem weißen Blatt sieht man einen weißen Rand nicht.
#: Druckt der Drucker nicht randlos, frisst der unbedruckte Rand hier sichtbar
#: in die graue Fläche — das erkennt man aus zwei Metern.
RAHMEN = (158, 158, 158)
RAHMEN_MM = 8.0            #: auf einem 10×15; kleinere Blätter bekommen weniger


def rahmen_mm(layout: Layout) -> float:
    """Wie breit der graue Rahmen auf diesem Papier sein darf.

    Fest 8 mm war auf einer SELPHY-Karte (54 mm breit) fast ein Drittel der
    Blattbreite — der Rahmen erschlug die Seite, die er rahmen soll.
    """
    kurz = min(PAPERS.get(layout.sheet_paper, (100.0, 100.0)))
    return max(4.0, min(RAHMEN_MM, kurz * 0.08))

#: Hauttöne von sehr hell bis sehr dunkel. Der Prüfstein einer Fotobox: Ein
#: Stich, der in einem Farbbalken niemandem auffällt, macht in einem Gesicht
#: aus „gebräunt" ein „krank". Die Werte sind übliche sRGB-Mittelwerte für
#: Gesichtshaut und decken die Bandbreite ab, die an einem Abend vorkommt.
HAUT: List[Tuple[str, Tuple[int, int, int]]] = [
    ("I", (247, 226, 209)),
    ("II", (235, 201, 175)),
    ("III", (211, 169, 133)),
    ("IV", (172, 124, 88)),
    ("V", (120, 81, 56)),
    ("VI", (74, 49, 36)),
]

#: Volltöne. Darunter kommt dieselbe Reihe halb aufgehellt — ein Drucker, der
#: die Volltöne trifft und in der Mitte absäuft, ist häufiger als einer, der
#: die Volltöne verfehlt.
FARBEN: List[Tuple[str, Tuple[int, int, int]]] = [
    ("R", (230, 30, 40)), ("G", (0, 160, 75)), ("B", (0, 90, 180)),
    ("C", (0, 170, 220)), ("M", (225, 0, 130)), ("Y", (255, 215, 0)),
]

#: Der Graukeil in Prozent Schwärzung. Die Enden liegen absichtlich dicht
#: beieinander: Dort reißt es ab, nicht in der Mitte.
GRAUSTUFEN: List[int] = [0, 2, 5, 10, 25, 40, 60, 75, 90, 95, 98, 100]


#: Alle Texte des Blattes. Eine neue Sprache ist ein neuer Block — dasselbe
#: Muster wie in der Kiosk-Oberfläche (``web/static/i18n.js``). Die Seite wird
#: gedruckt und auf einem Tisch beurteilt; sie muss in der Sprache dastehen,
#: die der Betreiber spricht, nicht in der des Entwicklers.
TEXTE: Dict[str, Dict[str, str]] = {
    "de": {
        "titel": "KALIBRIERUNG",
        "drucker": "Drucker: {drucker}",
        "medium": "Medium: {medium}",
        "papier": "Papier: {groesse} · {dpi} dpi · Rand {rand:.0f} mm",
        "aufteilung": "Aufteilung: {art} · Nachschärfen: {schaerfe}",
        "art.eins": "ein Bild je Blatt",
        "art.zwei": "zwei Streifen, Schnitt in der Mitte",
        "gedruckt": "Gedruckt am %d.%m.%Y um %H:%M",
        "grau.titel": "Graukeil — reißt es oben oder unten ab?",
        "grau.text": "Zahlen = Prozent Schwärzung. 2 und 5 müssen sich von 0 unterscheiden, "
                     "95 und 98 von 100 — sonst gehen Falten im Kleid und Zeichnung im Anzug verloren.",
        "haut.titel": "Hauttöne — hier fällt ein Farbstich auf, sonst nirgends",
        "farben.titel": "Farben — voll und halb",
        "linien.titel": "Linien — helle Säume heißen: zu stark nachgeschärft",
        "lineal.titel": "Lineal",
        "lineal.text": "Zentimeter. Weicht das Lineal ab, skaliert der Treiber.",
        "rand.text": "Rand: An jeder Kante steht eine Treppe aus fünf Marken (1 bis 5 mm). "
                     "Bei welcher Stufe fängt sie an? So weit muss alles Wichtige nach innen.",
        "schnitt.text": "Schnitt: Die gestrichelte Linie muss genau dort liegen, wo der Drucker trennt.",
        "mm": "5 mm",
        "kante.oben": "OBEN", "kante.unten": "UNTEN",
        "kante.links": "LINKS", "kante.rechts": "RECHTS",
        "unbekannt": "unbekannt",
        "aus": "aus",
    },
    "en": {
        "titel": "CALIBRATION",
        "drucker": "Printer: {drucker}",
        "medium": "Media: {medium}",
        "papier": "Paper: {groesse} · {dpi} dpi · margin {rand:.0f} mm",
        "aufteilung": "Layout: {art} · sharpening: {schaerfe}",
        "art.eins": "one picture per sheet",
        "art.zwei": "two strips, cut down the middle",
        "gedruckt": "Printed %d/%m/%Y at %H:%M",
        "grau.titel": "Grey wedge — does it clip at either end?",
        "grau.text": "Numbers are percent ink. 2 and 5 must differ from 0, and 95 and 98 from 100 — "
                     "otherwise the folds in a white dress and the detail in a dark suit are gone.",
        "haut.titel": "Skin tones — a colour cast shows here and nowhere else",
        "farben.titel": "Colours — full and half",
        "linien.titel": "Lines — bright fringes mean sharpening is too strong",
        "lineal.titel": "Ruler",
        "lineal.text": "Centimetres. If a real ruler disagrees, the driver is scaling.",
        "rand.text": "Edges: every edge carries a staircase of five marks (1 to 5 mm). "
                     "Which step does it start at? That is how far in everything important must sit.",
        "schnitt.text": "Cut: the dashed line must sit exactly where the printer separates the sheet.",
        "mm": "5 mm",
        "kante.oben": "TOP", "kante.unten": "BOTTOM",
        "kante.links": "LEFT", "kante.rechts": "RIGHT",
        "unbekannt": "unknown",
        "aus": "off",
    },
}


def t(sprache: str, schluessel: str, **werte) -> str:
    """Ein Text in der Sprache des Betreibers. Unbekannte Sprache → Deutsch."""
    block = TEXTE.get(str(sprache or "de").lower()[:2]) or TEXTE["de"]
    roh = block.get(schluessel) or TEXTE["de"].get(schluessel, schluessel)
    return roh.format(**werte) if werte else roh


def _grau(prozent: int) -> Tuple[int, int, int]:
    v = int(round(255 * (1 - prozent / 100)))
    return (v, v, v)


def _schrift(px: int, fett: bool = True):
    from ..media import compose
    return compose.load_font(max(7, int(px)), bold=fett)


def _breite_von(text: str, px: int, fett: bool) -> int:
    f = _schrift(px, fett)
    try:
        return int(f.getbbox(text)[2])
    except Exception:                            # noqa: BLE001 — Ersatzschrift
        return int(len(text) * px * 0.55)


def _umbrechen(text: str, px: int, fett: bool, breite: int) -> List[str]:
    """Text auf die verfügbare Breite umbrechen.

    Ohne das lief der Erklärsatz unter dem Graukeil über den Blattrand hinaus
    und quer durch die nächste Überschrift.
    """
    worte = text.split()
    zeilen: List[str] = []
    aktuell = ""
    for wort in worte:
        versuch = f"{aktuell} {wort}".strip()
        if aktuell and _breite_von(versuch, px, fett) > breite:
            zeilen.append(aktuell)
            aktuell = wort
        else:
            aktuell = versuch
    if aktuell:
        zeilen.append(aktuell)
    return zeilen or [""]


class _Seite:
    """Ein Durchgang über die Seite — misst oder zeichnet.

    ``d is None`` heißt messen. Die Maße stehen dann in ``y``, und der
    Aufrufer weiß, wie viel er schrumpfen muss.
    """

    def __init__(self, d: Optional[ImageDraw.ImageDraw], x: int, y: int,
                 breite: int, dpi: int, skala: float):
        self.d = d
        self.x = x
        self.y = y
        self.breite = breite
        self.dpi = dpi
        self.skala = skala

    # ── Maße ──
    def mm(self, wert: float) -> int:
        return mm_zu_px(wert * self.skala, self.dpi)

    def luft(self, wert: float) -> None:
        self.y += self.mm(wert)

    # ── Bausteine ──
    def zeile(self, text: str, groesse: float, fett: bool = True, farbe=SCHWARZ,
              abstand: float = 1.35) -> None:
        px = self.mm(groesse)
        for stueck in _umbrechen(text, px, fett, self.breite):
            if self.d is not None:
                self.d.text((self.x, self.y), stueck, fill=farbe,
                            font=_schrift(px, fett), anchor="la")
            self.y += int(px * abstand)

    def ueberschrift(self, text: str) -> None:
        self.luft(2.0)
        self.zeile(text, 3.4)
        self.luft(1.2)

    def felder(self, eintraege: Sequence[Tuple[str, Tuple[int, int, int]]],
               hoehe_mm: float) -> None:
        hoehe = self.mm(hoehe_mm)
        n = max(1, len(eintraege))
        fw = self.breite / n
        beschriftung = self.mm(2.6)
        for i, (name, farbe) in enumerate(eintraege):
            x0 = int(round(self.x + i * fw))
            x1 = int(round(self.x + (i + 1) * fw))
            if self.d is not None:
                self.d.rectangle([x0, self.y, x1 - 1, self.y + hoehe], fill=farbe)
                self.d.rectangle([x0, self.y, x1 - 1, self.y + hoehe], outline=GRAU, width=1)
                # Die Beschriftung steht UNTER dem Feld, nie darin: In einem
                # 98-%-Feld wäre schwarze Schrift lesbar, in einem 2-%-Feld
                # nicht — und genau diese beiden muss man vergleichen können.
                self.d.text(((x0 + x1) // 2, self.y + hoehe + self.mm(0.8)), name,
                            fill=SCHWARZ, font=_schrift(beschriftung, True), anchor="ma")
        self.y += hoehe + beschriftung + self.mm(2.2)

    def linien(self, hoehe_mm: float) -> None:
        """Feine Linien — sie zeigen, was das Nachschärfen anrichtet."""
        hoehe = self.mm(hoehe_mm)
        block = self.breite // 4
        beschriftung = self.mm(2.4)
        for i, staerke in enumerate((1, 2, 3, 4)):
            x0 = self.x + i * block
            if self.d is not None:
                px = x0
                while px < x0 + block - staerke - self.mm(2):
                    self.d.rectangle([px, self.y, px + staerke - 1, self.y + hoehe], fill=SCHWARZ)
                    px += staerke * 2
                self.d.text((x0 + block // 2, self.y + hoehe + self.mm(0.8)), f"{staerke} px",
                            fill=GRAU, font=_schrift(beschriftung, False), anchor="ma")
        self.y += hoehe + beschriftung + self.mm(2.2)

    def lineal(self, hinweis: str) -> None:
        """Zum Nachmessen mit einem echten Lineal.

        Der einzige Weg, eine stillschweigende Skalierung im Treiber zu
        bemerken: Steht hier 10 und das Lineal zeigt 9,6 cm, ist „an Seite
        anpassen" eingeschaltet. Die Striche stehen in echten Zentimetern —
        sie dürfen NICHT mitskaliert werden, sonst misst man den Faktor mit.
        """
        hoch = self.mm(5)
        zahl = self.mm(2.4)
        if self.d is not None:
            self.d.line([(self.x, self.y), (self.x + self.breite, self.y)],
                        fill=SCHWARZ, width=max(2, self.dpi // 200))
        cm = 0
        while True:
            p = self.x + mm_zu_px(cm * 10, self.dpi)
            if p > self.x + self.breite:
                break
            if self.d is not None:
                self.d.line([(p, self.y), (p, self.y + hoch)], fill=SCHWARZ,
                            width=max(2, self.dpi // 250))
                self.d.text((p + self.mm(0.8), self.y + hoch // 5), f"{cm}",
                            fill=SCHWARZ, font=_schrift(zahl, True), anchor="la")
            halb = self.x + mm_zu_px(cm * 10 + 5, self.dpi)
            if halb <= self.x + self.breite and self.d is not None:
                self.d.line([(halb, self.y), (halb, self.y + hoch // 2)], fill=GRAU,
                            width=max(1, self.dpi // 300))
            cm += 1
        self.y += hoch + self.mm(1.5)
        self.zeile(hinweis, 2.4, fett=False, farbe=GRAU)


# ── Rahmenwerk um den Inhalt ──────────────────────────────────────────

def _kamm(d: ImageDraw.ImageDraw, dpi: int, anker: Tuple[int, int],
          nach_innen: Tuple[int, int], entlang: Tuple[int, int],
          beschriften: bool, rahmen: int = 0) -> None:
    """Eine Treppe aus fünf Marken in 1 bis 5 mm Abstand von der Kante.

    Die Marken liegen nebeneinander, jede eine Stufe tiefer. Schneidet der
    Drucker zwei Millimeter weg, fehlen die ersten beiden Stufen und die
    Treppe fängt sichtbar bei 3 an. Genau das ist die Zahl, die der
    Betreiber braucht.

    Erster Entwurf waren gleich lange Striche ohne Zahlen — auf dem
    gerenderten Blatt war nicht zu erkennen, welcher Strich welcher war.
    Damit beantwortete das wichtigste Feld der Seite seine eigene Frage nicht.
    """
    ax, ay = anker
    ix, iy = nach_innen
    ex, ey = entlang
    dicke = mm_zu_px(1.4, dpi)
    schrift = max(6, int(rahmen * 0.34)) if rahmen else mm_zu_px(2.2, dpi)
    # Unter etwa fünf Millimetern Rahmen ist für Ziffern kein Platz. Dann
    # bleibt die Treppe unbeschriftet — abzählbar ist sie trotzdem, so wie
    # an der linken und rechten Kante ohnehin.
    beschriften = beschriften and rahmen >= mm_zu_px(5.0, dpi)
    for i, mm in enumerate((1, 2, 3, 4, 5)):
        tiefe = mm_zu_px(mm, dpi)
        versatz = i * mm_zu_px(2.2, dpi)
        # Die Marke sitzt in ``mm`` Abstand von der Kante und ist so lang,
        # dass sie von dort bis zur Kante reicht — fällt sie weg, ist der
        # Abstand verbraucht.
        x0 = ax + ix * tiefe + ex * versatz
        y0 = ay + iy * tiefe + ey * versatz
        x1 = x0 + ex * dicke + ix * mm_zu_px(1.2, dpi)
        y1 = y0 + ey * dicke + iy * mm_zu_px(1.2, dpi)
        # Weiß auf dem grauen Rahmen, die 5 in Schwarz als Ankerstufe. Grau
        # auf Grau war unsichtbar — der Rahmen kam erst später dazu, und die
        # Marken standen noch auf der Farbe von vorher.
        d.rectangle([min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1)],
                    fill=SCHWARZ if mm == 5 else WEISS)
        if beschriften:
            # Die Ziffern gehören IN den grauen Rahmen. Auf festen 7,5 mm
            # landeten sie bei einem schmalen Rahmen (SELPHY-Karte) mitten
            # im weißen Feld und lagen über Titel und Fußnote.
            tx = x0 + ex * dicke // 2
            ty = ay + iy * int(rahmen * 0.72)
            d.text((tx, ty), str(mm), fill=WEISS, font=_schrift(schrift, False),
                   anchor="ma" if iy > 0 else "md")


def _randlineal(d: ImageDraw.ImageDraw, breite: int, hoehe: int, dpi: int,
                rahmen: int) -> None:
    """Millimetermarken an allen vier Kanten, dazu Eckwinkel.

    Das ist das wichtigste Feld auf dem Blatt. Randlos bedruckte Blätter
    verlieren an jeder Kante etwas; wie viel, verrät keine Herstellerangabe
    zuverlässig. Hier zählt man ab, bei welcher Stufe die Treppe anfängt.

    Die Marken stehen in echten Millimetern und werden NICHT mitskaliert —
    ein Lineal, das sich der Seite anpasst, ist kein Lineal.
    """
    # Auf ein Drittel versetzt, nicht mittig: In der Mitte jeder Kante steht
    # die Beschriftung (OBEN/LINKS/…), und beides übereinander war unlesbar.
    # oben und unten: mit Zahlen, weil die Schrift dort waagerecht steht
    _kamm(d, dpi, (breite // 3, 0), (0, 1), (1, 0), True, rahmen)
    _kamm(d, dpi, (breite // 3, hoehe), (0, -1), (1, 0), True, rahmen)
    # links und rechts: dieselbe Treppe, ohne Zahlen. Die Stufenform ist
    # auch ohne Beschriftung abzählbar, und gedrehte Schrift am Blattrand
    # liest ohnehin niemand.
    _kamm(d, dpi, (0, hoehe // 3), (1, 0), (0, 1), False, rahmen)
    _kamm(d, dpi, (breite, hoehe // 3), (-1, 0), (0, 1), False, rahmen)

    # Ecken: ein Winkel bei 5 mm. Fehlt einer ganz, sitzt das Blatt schief.
    e = mm_zu_px(4, dpi)
    arm = mm_zu_px(6, dpi)
    dick = max(2, dpi // 200)
    for ex, ey, sx, sy in ((e, e, 1, 1), (breite - e, e, -1, 1),
                           (e, hoehe - e, 1, -1), (breite - e, hoehe - e, -1, -1)):
        d.line([(ex, ey), (ex + sx * arm, ey)], fill=SCHWARZ, width=dick)
        d.line([(ex, ey), (ex, ey + sy * arm)], fill=SCHWARZ, width=dick)


def _gedreht(d: ImageDraw.ImageDraw, bild: Image.Image, text: str, f,
             mitte: Tuple[int, int], winkel: int, farbe) -> None:
    """Text um 90° gedreht aufs Blatt setzen.

    Waagerecht geschrieben ragte „LINKS" über die Blattkante hinaus — der
    Rahmen ist acht Millimeter breit, das Wort ist es nicht.
    """
    kasten = f.getbbox(text)
    w, h = kasten[2] - kasten[0] + 8, kasten[3] - kasten[1] + 8
    schnipsel = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    ImageDraw.Draw(schnipsel).text((4 - kasten[0], 4 - kasten[1]), text, font=f, fill=farbe)
    schnipsel = schnipsel.rotate(winkel, expand=True)
    bild.paste(schnipsel, (mitte[0] - schnipsel.width // 2,
                           mitte[1] - schnipsel.height // 2), schnipsel)


def _kanten(d: ImageDraw.ImageDraw, bild: Image.Image, breite: int, hoehe: int,
            dpi: int, rahmen: int, sprache: str) -> None:
    """OBEN/UNTEN/LINKS/RECHTS auf den grauen Rahmen.

    Abgeschaut bei dslrBooth, und der Nutzen zeigt sich erst am Tisch: Ein
    Abzug kommt gedreht aus dem Drucker, liegt falsch herum da, und dann
    weiß niemand mehr, welche Kante beschnitten wurde. Ohne Beschriftung ist
    die Randtreppe nur halb so viel wert — man sieht, DASS etwas fehlt, aber
    nicht WO.
    """
    px = max(6, int(rahmen * 0.42))
    f = _schrift(px, True)
    mitte = rahmen // 2
    d.text((breite // 2, mitte), t(sprache, "kante.oben"), fill=WEISS, font=f, anchor="mm")
    d.text((breite // 2, hoehe - mitte), t(sprache, "kante.unten"), fill=WEISS, font=f, anchor="mm")
    _gedreht(d, bild, t(sprache, "kante.links"), f, (mitte, hoehe // 2), 90, WEISS)
    _gedreht(d, bild, t(sprache, "kante.rechts"), f, (breite - mitte, hoehe // 2), 270, WEISS)


def _schnittlinie(d: ImageDraw.ImageDraw, breite: int, hoehe: int, dpi: int) -> None:
    """Wo der Drucker trennt — gestrichelt, damit es nicht wie Inhalt aussieht."""
    mitte = breite // 2
    schritt = mm_zu_px(3, dpi)
    for yy in range(0, hoehe, schritt * 2):
        d.line([(mitte, yy), (mitte, min(hoehe, yy + schritt))], fill=GRAU,
               width=max(1, dpi // 300))


def _kopfzeilen(info: Dict[str, str], layout: Layout, sprache: str) -> List[str]:
    """Was auf dem Blatt stehen muss, damit man drei Versuche auseinanderhält.

    Nach dem dritten Probedruck liegen drei fast gleiche Blätter auf dem
    Tisch. Ohne diese Zeilen weiß niemand mehr, welches mit welcher
    Einstellung entstanden ist — und die ganze Runde war umsonst.
    """
    papier = layout.sheet_paper
    mm = PAPERS.get(papier)
    groesse = f"{papier} ({mm[0]:.0f}×{mm[1]:.0f} mm)" if mm else papier
    zeilen = [t(sprache, "drucker", drucker=info.get("drucker") or t(sprache, "unbekannt"))]
    if info.get("medium"):
        zeilen.append(t(sprache, "medium", medium=info["medium"]))
    zeilen.append(t(sprache, "papier", groesse=groesse, dpi=layout.dpi, rand=layout.margin_mm))
    art = t(sprache, "art.zwei" if layout.per_sheet == 2 else "art.eins")
    zeilen.append(t(sprache, "aufteilung", art=art,
                    schaerfe=info.get("schaerfe") or t(sprache, "aus")))
    zeilen.append(time.strftime(t(sprache, "gedruckt")))
    return zeilen


def _inhalt(seite: _Seite, layout: Layout, info: Dict[str, str], sprache: str) -> int:
    """Der ganze Inhalt, einmal. Gibt die Unterkante zurück.

    Dieselbe Funktion misst und zeichnet — dadurch kann das Gemessene nicht
    von dem abweichen, was am Ende auf dem Blatt steht.
    """
    seite.zeile(t(sprache, "titel"), 6.0)
    seite.luft(1.0)
    for z in _kopfzeilen(info, layout, sprache):
        seite.zeile(z, 2.6, fett=False)
    seite.luft(1.5)

    seite.ueberschrift(t(sprache, "grau.titel"))
    seite.felder([(str(p), _grau(p)) for p in GRAUSTUFEN], 10)
    seite.zeile(t(sprache, "grau.text"), 2.3, fett=False, farbe=GRAU)

    seite.ueberschrift(t(sprache, "haut.titel"))
    seite.felder(HAUT, 13)

    seite.ueberschrift(t(sprache, "farben.titel"))
    seite.felder(FARBEN, 8)
    seite.felder([(n.lower(), tuple(int(round(c + (255 - c) * 0.5)) for c in f))
                  for n, f in FARBEN], 8)

    seite.ueberschrift(t(sprache, "linien.titel"))
    seite.linien(8)

    seite.ueberschrift(t(sprache, "lineal.titel"))
    seite.lineal(t(sprache, "lineal.text"))

    seite.luft(1.5)
    seite.zeile(t(sprache, "rand.text"), 2.3, fett=False, farbe=GRAU)
    if layout.per_sheet == 2:
        seite.zeile(t(sprache, "schnitt.text"), 2.3, fett=False, farbe=GRAU)
    return seite.y


def blatt(layout: Layout, ziel: Path, info: Optional[Dict[str, str]] = None,
          sprache: str = "de") -> Path:
    """Die Kalibrierungsseite in der echten Blattgröße bauen."""
    info = dict(info or {})
    dpi = max(72, int(layout.dpi or 300))

    # Immer hochkant. Das Blatt wird gelesen, nicht betrachtet: Eine quer
    # aufgebaute Seite müsste man drehen, um die Hauttöne zu beurteilen, und
    # ein gedrehtes Blatt legt niemand neben ein Gesicht.
    breite, hoehe = sheet_size_px(layout, True)

    rand = rahmen_mm(layout)
    rahmen = mm_zu_px(rand, dpi)
    innen = rahmen + mm_zu_px(3, dpi)
    oben = rahmen + mm_zu_px(3, dpi)
    unten = rahmen + mm_zu_px(3, dpi)
    nutz_breite = breite - 2 * innen
    nutz_hoehe = hoehe - oben - unten

    # Erster Durchgang: nur messen. Der zweite zeichnet mit dem Faktor, der
    # dabei herauskommt — dadurch passt die Seite auf jedes Papier, vom
    # 5×15-Streifen bis A4, statt auf genau eines.
    # Zweimal messen. Die Textumbrüche hängen an der Schriftgröße, und die
    # hängt am Faktor: Beim ersten Messen mit Faktor 1 brechen die Sätze in
    # mehr Zeilen um als beim Zeichnen mit dem kleineren Faktor. Wer nur
    # einmal misst, schätzt zu hoch und schrumpft zu stark — auf einer
    # SELPHY-Karte stand der Inhalt danach in der oberen Blatthälfte und
    # darunter war nichts.
    skala = 1.0
    for _ in range(3):
        gemessen = _inhalt(_Seite(None, innen, 0, nutz_breite, dpi, skala), layout, info, sprache)
        if gemessen <= 0:
            break
        neu_skala = max(0.2, min(1.6, skala * nutz_hoehe / gemessen))
        if abs(neu_skala - skala) < 0.01:
            skala = neu_skala
            break
        skala = neu_skala

    # Der Rahmen ist grau, das Feld darin weiß. Wer einen weißen Rand
    # bekommt, sieht ihn als helle Kante im Grau — auf einem durchgehend
    # weißen Blatt wäre er unsichtbar.
    bild = Image.new("RGB", (breite, hoehe), RAHMEN)
    d = ImageDraw.Draw(bild)
    d.rectangle([rahmen, rahmen, breite - rahmen - 1, hoehe - rahmen - 1], fill=WEISS)

    _randlineal(d, breite, hoehe, dpi, rahmen)
    _kanten(d, bild, breite, hoehe, dpi, rahmen, sprache)
    if layout.per_sheet == 2:
        _schnittlinie(d, breite, hoehe, dpi)
    _inhalt(_Seite(d, innen, oben, nutz_breite, dpi, skala), layout, info, sprache)

    ziel.parent.mkdir(parents=True, exist_ok=True)
    # JPEG, weil der IPP-Weg „image/jpeg" fest ansagt und Hotfolder-Drucker
    # nichts anderes erwarten. Aber ohne Unterabtastung: Die übliche
    # 4:2:0-Kodierung halbiert die Farbauflösung, und genau das verfälscht
    # flächige Farbfelder an ihren Kanten — auf einer Seite, deren Zweck
    # es ist, Farben zu beurteilen, wäre das absurd.
    bild.save(ziel, "JPEG", quality=100, subsampling=0, dpi=(dpi, dpi))
    return ziel
