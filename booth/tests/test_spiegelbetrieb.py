"""Spiegelbox: die ganze Oberfläche gekippt — und trotzdem sichtbar und lesbar.

Gemessen im Browser, nicht im Quelltext. Der Fehler, um den es hier geht, war
im CSS nicht zu sehen und beim Lesen nicht zu erraten: ``#app`` hatte keine
eigenen Maße, und weil alle Bildschirme ``position: fixed`` sind, war der
Kasten null hoch. Ohne Spiegelung fällt das nie auf — fixierte Kinder rechnen
dann gegen das Sichtfenster. Sobald aber ein ``transform`` auf ``#app``
liegt, wird ``#app`` selbst zum Bezugsrahmen, auch für fixierte Kinder. Am
20.09.2026 gemessen: 1080x0 statt 1080x1920. Die Spiegelbox zeigte nichts.

Deshalb steht dieser Test im Browser. Ein Test, der nur CSS-Text prüft, hätte
die Zeile ``transform: scaleX(-1)`` gefunden und für richtig gehalten.
"""
from __future__ import annotations

import http.server
import socket
import socketserver
import threading
from pathlib import Path

import pytest

WEB = Path(__file__).resolve().parents[1] / "boothcore" / "web"

playwright = pytest.importorskip("playwright.sync_api", reason="Playwright nicht installiert")


def _freier_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


@pytest.fixture(scope="module")
def adresse():
    """Die Oberfläche über HTTP ausliefern.

    Über ``file://`` scheitert das lautlos: Die Stylesheets hängen an
    absoluten Pfaden, laden nicht, und jede Messung misst dann eine Seite
    ganz ohne Gestaltung. Genau das ist mir beim ersten Anlauf passiert.
    """
    port = _freier_port()

    class Still(http.server.SimpleHTTPRequestHandler):
        def __init__(self, *a, **kw):
            super().__init__(*a, directory=str(WEB), **kw)

        def log_message(self, *a):
            pass

    server = socketserver.TCPServer(("127.0.0.1", port), Still)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        yield f"http://127.0.0.1:{port}/index.html"
    finally:
        server.shutdown()
        server.server_close()


@pytest.fixture(scope="module")
def seite(adresse):
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        try:
            browser = p.chromium.launch()
        except Exception as e:                       # pragma: no cover - kein Chromium
            pytest.skip(f"Chromium nicht startbar: {e}")
        pg = browser.new_page(viewport={"width": 1080, "height": 1920})
        pg.goto(adresse)
        pg.wait_for_timeout(400)
        yield pg
        browser.close()


def _stellen(pg, *, breite, hoehe, spiegel_h=False, spiegel_v=False, schirm="start"):
    pg.set_viewport_size({"width": breite, "height": hoehe})
    pg.evaluate(
        """({h, v, s}) => {
            const app = document.getElementById('app');
            app.dataset.mirrorH = h ? '1' : '';
            app.dataset.mirrorV = v ? '1' : '';
            app.dataset.screen = s;
            document.querySelectorAll('.screen').forEach(e => e.setAttribute('aria-hidden', 'true'));
            document.getElementById('s-' + s).setAttribute('aria-hidden', 'false');
        }""",
        {"h": spiegel_h, "v": spiegel_v, "s": schirm},
    )
    pg.wait_for_timeout(120)
    return pg.evaluate(
        """(s) => {
            const el = document.getElementById('s-' + s);
            const r = el.getBoundingClientRect();
            const sichtbar = [...el.querySelectorAll('*')].filter(e => {
                const b = e.getBoundingClientRect();
                return b.width > 2 && b.height > 2;
            }).length;
            return {w: Math.round(r.width), h: Math.round(r.height), sichtbar};
        }""",
        schirm,
    )


LAGEN = [(1080, 1920, "hoch"), (1920, 1080, "quer")]


@pytest.mark.parametrize("breite,hoehe,lage", LAGEN)
@pytest.mark.parametrize("schirm", ["start", "setup"])
def test_der_bildschirm_fuellt_das_bild_auch_gespiegelt(seite, breite, hoehe, lage, schirm):
    ohne = _stellen(seite, breite=breite, hoehe=hoehe, schirm=schirm)
    mit = _stellen(seite, breite=breite, hoehe=hoehe, spiegel_h=True, schirm=schirm)
    assert ohne["h"] > hoehe * 0.9, f"Schon ohne Spiegelung zu klein ({lage})"
    assert mit["h"] > hoehe * 0.9, \
        f"Gespiegelt fällt der Bildschirm zusammen ({lage}: {mit['w']}x{mit['h']})"
    assert mit["w"] > breite * 0.9


def test_auch_senkrecht_gespiegelt_bleibt_die_flaeche_stehen(seite):
    mit = _stellen(seite, breite=1080, hoehe=1920, spiegel_h=True, spiegel_v=True)
    assert mit["h"] > 1920 * 0.9 and mit["w"] > 1080 * 0.9


def test_gespiegelt_verschwindet_nichts(seite):
    """Gleich viele sichtbare Elemente — sonst ist etwas aus dem Bild gerutscht."""
    ohne = _stellen(seite, breite=1080, hoehe=1920)
    mit = _stellen(seite, breite=1080, hoehe=1920, spiegel_h=True)
    assert mit["sichtbar"] == ohne["sichtbar"]


def test_die_schrift_wird_zurueckgedreht(seite):
    """Ein Spiegelbild ist schön, spiegelverkehrte Schrift ist unlesbar."""
    _stellen(seite, breite=1080, hoehe=1920, spiegel_h=True)
    verkehrt = seite.evaluate(
        """() => {
            const zaehlt = ['H1','H2','H3','P','SPAN','B','SMALL','OUTPUT'];
            const schlecht = [];
            for (const el of document.querySelectorAll('#s-start *')) {
                if (!zaehlt.includes(el.tagName)) continue;
                const r = el.getBoundingClientRect();
                if (r.width < 3 || r.height < 3) continue;
                if (!(el.textContent || '').trim()) continue;
                // Produkt der x-Skalierungen über alle Vorfahren: -1 heißt
                // gespiegelt, +1 heißt lesbar.
                let f = 1, n = el;
                while (n && n !== document.documentElement) {
                    const m = new DOMMatrixReadOnly(getComputedStyle(n).transform);
                    if (m.a < 0) f = -f;
                    n = n.parentElement;
                }
                if (f < 0) schlecht.push(el.tagName + '.' + (el.className || ''));
            }
            return schlecht;
        }"""
    )
    assert verkehrt == [], f"Spiegelverkehrte Schrift: {verkehrt}"


def test_der_kasten_hat_eigene_masse():
    """Die Ursache, festgehalten: ohne das ist #app null hoch."""
    css = (WEB / "static" / "app.css").read_text(encoding="utf-8")
    stelle = css.index('#app { position: fixed; inset: 0; }')
    assert stelle < css.index('#app[data-mirror-h="1"]'), \
        "Der Kasten muss Maße haben, bevor er gekippt wird"
