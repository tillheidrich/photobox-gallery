"""Jede Seite der Box wird einmal wirklich geladen — läuft ihr JavaScript?

Der Anlass, 20.09.2026: Die Verwaltungsseite war seit dem Vortag vollständig
tot. In einem deutschen Satz stand ``„Blatt je Kassette"`` — die öffnende
Anführung typografisch, die schließende ein gewöhnliches Anführungszeichen.
Damit endete die Zeichenkette mitten im Satz, und der Rest der Datei war
Unsinn. Kein Tastenfeld, keine Anmeldung, keine einzige Einstellung.

716 Python-Tests haben das nicht gemerkt, weil keiner davon eine Seite lädt.
Ein Syntaxfehler in einer Textdatei, die nur ausgeliefert wird, fällt
serverseitig nie auf. Dieser Test lädt jede Seite in einem echten Browser und
besteht nur, wenn dabei kein Fehler auftritt — unabhängig davon, wodurch er
entstanden ist.

Deutsche Anführungen sind hier keine Marotte, sondern die Fehlerquelle: „…“
öffnet unten und schließt OBEN. Wer die schließende mit der Tastatur tippt,
bekommt ein ``"`` — und genau das ist passiert.
"""
from __future__ import annotations

import http.server
import socket
import socketserver
import sys
import threading
from pathlib import Path

import pytest

WEB = Path(__file__).resolve().parents[1] / "boothcore" / "web"
SEITEN = sorted(p.name for p in WEB.glob("*.html"))

playwright = pytest.importorskip("playwright.sync_api", reason="Playwright nicht installiert")


@pytest.fixture(scope="module")
def basis():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = int(s.getsockname()[1])

    class Still(http.server.SimpleHTTPRequestHandler):
        def __init__(self, *a, **kw):
            super().__init__(*a, directory=str(WEB), **kw)

        def log_message(self, *a):
            pass

    server = socketserver.TCPServer(("127.0.0.1", port), Still)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.shutdown()
        server.server_close()


@pytest.fixture(scope="module")
def browser():
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        try:
            br = p.chromium.launch()
        except Exception as e:                       # pragma: no cover
            pytest.skip(f"Chromium nicht startbar: {e}")
        yield br
        br.close()


def test_es_gibt_ueberhaupt_seiten():
    assert SEITEN, "Keine Seite gefunden — der Test würde nichts prüfen"


@pytest.mark.parametrize("seite", SEITEN)
def test_die_seite_laeuft_ohne_javascript_fehler(browser, basis, seite):
    pg = browser.new_page()
    fehler = []
    pg.on("pageerror", lambda e: fehler.append(str(e)))
    try:
        pg.goto(f"{basis}/{seite}")
        pg.wait_for_timeout(500)
    finally:
        pg.close()
    # Netzwerkfehler sind hier erwartbar (keine API hinter dem Dateiserver);
    # ein Syntax- oder Verweisfehler ist es nicht.
    echte = [f for f in fehler if "Failed to fetch" not in f and "NetworkError" not in f]
    assert echte == [], f"{seite}: {echte}"


@pytest.mark.parametrize("seite", SEITEN)
def test_die_seite_baut_ihre_bedienelemente_auf(browser, basis, seite):
    """Ohne Fehler, aber auch ohne Inhalt wäre nichts gewonnen."""
    pg = browser.new_page()
    try:
        pg.goto(f"{basis}/{seite}")
        pg.wait_for_timeout(500)
        knoepfe = pg.eval_on_selector_all("button", "els => els.length")
    finally:
        pg.close()
    assert knoepfe > 0, f"{seite} hat keinen einzigen Knopf — läuft ihr Skript?"
