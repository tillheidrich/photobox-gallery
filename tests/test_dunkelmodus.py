"""Der Dunkelmodus muss derselbe sein, egal wie man hineinkommt.

Es gibt zwei Wege: den Umschalter in der Oberfläche
(``:root[data-theme="dark"]``) und die Einstellung des Betriebssystems
(``@media (prefers-color-scheme: dark)``). Beide setzen dieselben Tokens —
und am 20.09.2026 waren sie auseinandergelaufen:

* ``--pb-text-muted`` war 0.660 über den Umschalter, 0.585 über das System
* ``--pb-text-2`` 0.790 gegen 0.750
* ``--pb-on-accent`` zeigte auf zwei verschiedene Paletten
* ``--pb-accent-solid`` und ``--pb-icon-muted`` fehlten im System-Block ganz

Die Folge war nicht nur kosmetisch: Über die Systemeinstellung fielen vier
Textstellen auf 4,13:1 und damit unter die geforderten 4,5:1 für kleine
Schrift. Wer den Umschalter benutzte, sah das nie.

Gefunden wurde das erst beim Messen im Browser — und auch das erst im
zweiten Anlauf: Die erste Messung zerlegte ``oklch(…)``-Farben mit einer
Formel für ``rgb(…)`` und erfand dabei Kontrastprobleme, die es nicht gab.
Deshalb rechnet die Prüfung unten den Browser selbst rechnen lassen.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

MARKE = Path(__file__).resolve().parents[1] / "app" / "static" / "css" / "brand" / "photobox-brand.css"


def _tokens(text: str) -> dict:
    return dict(re.findall(r"(--[\w-]+)\s*:\s*([^;]+);", text))


def _bloecke():
    s = MARKE.read_text(encoding="utf-8")
    umschalter = re.search(r':root\[data-theme="dark"\]\s*\{(.*?)\n\}', s, re.S)
    system = re.search(
        r'@media \(prefers-color-scheme: dark\)\s*\{\s*:root:not\(\[data-theme="light"\]\)\s*\{(.*?)\n  \}',
        s, re.S)
    assert umschalter and system, "Die beiden Dunkelmodus-Blöcke wurden nicht gefunden"
    return _tokens(umschalter.group(1)), _tokens(system.group(1))


def test_beide_dunkelmodus_wege_setzen_dieselben_tokens():
    umschalter, system = _bloecke()
    fehlend = sorted(set(umschalter) - set(system))
    zuviel = sorted(set(system) - set(umschalter))
    assert not fehlend, f"Im System-Block fehlen: {fehlend}"
    assert not zuviel, f"Im System-Block stehen zusätzlich: {zuviel}"


def test_beide_wege_setzen_dieselben_werte():
    umschalter, system = _bloecke()
    abweichend = {k: (umschalter[k], system[k])
                  for k in umschalter if k in system and umschalter[k] != system[k]}
    assert not abweichend, (
        "Dieselbe Oberfläche sähe anders aus, je nachdem wie der Dunkelmodus "
        f"erreicht wird: {abweichend}")


def test_es_gibt_ueberhaupt_etwas_zu_pruefen():
    umschalter, _ = _bloecke()
    assert len(umschalter) > 20, "Zu wenige Tokens — die Blockerkennung stimmt nicht mehr"
