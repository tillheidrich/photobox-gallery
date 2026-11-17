"""Die Medienablage in der Verwaltung — im Browser gemessen.

Warum im Browser: Die Zuweisung ist reines Frontend. Ein Test, der nur das
HTML nach Zeichenketten durchsucht, hätte hier zwei Fehler durchgelassen, die
beide teuer sind — ein `Content-Type`, der den Upload zerlegt, und ein
Auswahlfeld, das eine eingestellte, aber fehlende Datei stillschweigend
vergisst. Beides sieht man erst, wenn die Seite wirklich läuft.
"""
from __future__ import annotations

import json
import socket
import sys
import threading
from pathlib import Path

import pytest
from _pages import page  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

playwright = pytest.importorskip("playwright.sync_api", reason="Playwright nicht installiert")


def _freier_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


@pytest.fixture(scope="module")
def box(tmp_path_factory):
    """Eine echte Box hinter einem echten Server — kein Nachbau."""
    import uvicorn
    from boothcore.app import Booth, create_app

    d = tmp_path_factory.mktemp("box")
    import os
    os.environ["BOOTH_DATA"] = str(d)
    os.environ["BOOTH_CAMERA"] = "mock"
    b = Booth(base=d, start_threads=False)
    b.store.save_local({"admin_pin": "468213"})  # the operator has replaced the factory PIN (see test_factory_pin.py)
    # Zwei Töne und ein Bild einlagern, plus eine Einstellung auf eine Datei,
    # die es NICHT gibt — der interessante Fall.
    b.assets_dir.mkdir(parents=True, exist_ok=True)
    (b.assets_dir / "klick.mp3").write_bytes(b"x" * 10)
    (b.assets_dir / "gong.wav").write_bytes(b"x" * 10)
    (b.assets_dir / "logo.png").write_bytes(b"x" * 10)
    b.store.profile.setdefault("sounds", {})["shutter"] = "klick.mp3"
    b.store.profile["sounds"]["done"] = "verschollen.mp3"      # Datei fehlt
    b.store.profile.setdefault("start", {})["media"] = ["logo.png"]

    port = _freier_port()
    server = uvicorn.Server(uvicorn.Config(create_app(b), host="127.0.0.1", port=port,
                                           log_level="error"))
    t = threading.Thread(target=server.run, daemon=True)
    t.start()
    import time
    for _ in range(100):
        if server.started:
            break
        time.sleep(0.05)
    yield b, f"http://127.0.0.1:{port}"
    server.should_exit = True
    t.join(timeout=5)


@pytest.fixture(scope="module")
def seite(box):
    from playwright.sync_api import sync_playwright
    b, basis = box
    with sync_playwright() as p:
        try:
            browser = p.chromium.launch()
        except Exception as e:                       # pragma: no cover
            pytest.skip(f"Chromium nicht startbar: {e}")
        pg = browser.new_page(viewport={"width": 1280, "height": 1600})
        fehler = []
        pg.on("pageerror", lambda e: fehler.append(str(e)))
        pg.goto(f"{basis}/admin")
        pin = str(b.store.local.get("admin_pin") or b.profile.get("admin_pin") or "1234")
        pg.evaluate("""async (pin) => {
            const r = await fetch("/api/admin/login", {method: "POST",
                headers: {"Content-Type": "application/json"}, body: JSON.stringify({pin})});
            window.__token = (await r.json()).token;
        }""", pin)
        # Anmelden wie ein Mensch: über das Tastenfeld. Die Tasten tragen
        # ihre Beschriftung als Textinhalt — danach wird gesucht, nicht nach
        # Position, damit ein umsortiertes Feld den Test nicht still bricht.
        pg.wait_for_selector("#pad button", timeout=5000)
        for zeichen in pin:
            pg.locator("#pad button", has_text=zeichen).filter(
                has_not=pg.locator("nichts")).first.click()
        pg.locator("#pad button", has_text="OK").first.click()
        pg.wait_for_selector("#main:not([hidden])", timeout=5000)
        pg.wait_for_timeout(600)
        pg.__dict__["fehler"] = fehler
        yield pg, fehler
        browser.close()


def test_die_seite_laeuft_ohne_javascript_fehler(seite):
    """PROFILE wird erst nach dem Medienteil deklariert — hält das?"""
    pg, fehler = seite
    assert fehler == [], f"JavaScript-Fehler beim Laden: {fehler}"


def test_die_ablage_zeigt_was_da_ist(seite):
    pg, _ = seite
    text = pg.inner_text("#medienListe")
    for name in ("klick.mp3", "gong.wav", "logo.png"):
        assert name in text


def test_die_verwendung_steht_dabei(seite):
    pg, _ = seite
    zeile = pg.inner_text("#medienListe")
    assert "Ton beim Auslösen" in zeile
    assert "wird nirgends verwendet" in zeile, "Unbenutztes muss auch als solches dastehen"


def test_was_in_gebrauch_ist_hat_keinen_loeschknopf(seite):
    pg, _ = seite
    gesperrt = pg.eval_on_selector_all(
        "#medienListe button[disabled]", "els => els.map(e => e.textContent)")
    assert "In Gebrauch" in gesperrt


def test_die_toene_stehen_zur_auswahl(seite):
    pg, _ = seite
    werte = pg.eval_on_selector_all("#snd_shutter option", "els => els.map(e => e.value)")
    assert "klick.mp3" in werte and "gong.wav" in werte
    assert "logo.png" not in werte, "Ein Bild ist kein Ton"
    assert pg.input_value("#snd_shutter") == "klick.mp3"


def test_eine_fehlende_datei_verschwindet_nicht_stillschweigend(seite):
    """Der teure Fall: sonst ist die Einstellung beim nächsten Speichern weg."""
    pg, _ = seite
    assert pg.input_value("#snd_done") == "verschollen.mp3"
    beschriftung = pg.eval_on_selector_all(
        "#snd_done option", "els => els.filter(e => e.value === 'verschollen.mp3').map(e => e.textContent)")
    assert beschriftung and "fehlt" in beschriftung[0]


def test_startmedien_kennen_bilder_und_videos_aber_keine_toene(seite):
    pg, _ = seite
    werte = pg.eval_on_selector_all("#startTiles .start-tile", "els => els.map(e => e.dataset.name)")
    assert "logo.png" in werte
    assert "klick.mp3" not in werte
    assert "builtin:abend.svg" in werte, "the backgrounds that ship with the booth need no upload"


def test_speichern_schickt_was_dasteht(seite, box):
    b, _ = box
    pg, _ = seite
    pg.select_option("#snd_shutter", "gong.wav")
    pg.fill("#snd_volume", "0.5")
    pg.click("#medienSpeichern")
    pg.wait_for_timeout(700)
    assert b.profile["sounds"]["shutter"] == "gong.wav"
    assert abs(float(b.profile["sounds"]["volume"]) - 0.5) < 0.01
    # Und die fehlende Datei steht noch da, statt beim Speichern zu verdampfen.
    assert b.profile["sounds"]["done"] == "verschollen.mp3"


def test_der_upload_kommt_wirklich_durch(seite, box):
    """Der Content-Type-Fehler wäre hier aufgefallen, sonst nirgends."""
    b, _ = box
    pg, _ = seite
    pg.evaluate("""async () => {
        const fd = new FormData();
        fd.append("datei", new File([new Uint8Array(32)], "Neuer Ton.MP3"));
        const r = await fetch("/api/admin/assets", {method: "POST",
            headers: {"X-Admin-Token": window.__token}, body: fd});
        window.__upload = {status: r.status, body: await r.json()};
    }""")
    pg.wait_for_timeout(400)
    ergebnis = pg.evaluate("window.__upload")
    assert ergebnis["status"] == 200, ergebnis
    assert ergebnis["body"]["name"] == "neuer-ton.mp3"
    assert (b.assets_dir / "neuer-ton.mp3").exists()


# ── Die Testseite, im Browser ─────────────────────────────────────────
# Sie ist die Seite, auf die sich jemand vor dem Event verlässt. Wenn sie
# still kaputtgeht, merkt er es genau dann nicht, wenn es darauf ankommt.

def test_die_testseite_zeigt_einen_stand(seite):
    pg, _ = seite
    text = pg.inner_text("#einrichtungStand")
    assert "Kamera" in text and "Drucker" in text and "System" in text


def test_die_testseite_nennt_den_naechsten_handgriff(seite):
    """Ein Zustand ohne Handlungsanweisung hilft niemandem."""
    pg, _ = seite
    offen = pg.inner_text("#einrichtungOffen")
    assert "Nächster Handgriff" in offen and len(offen) > 40


def test_die_ampel_ist_nicht_gruen_wenn_nichts_nachgewiesen_ist(seite):
    """Ohne Kamera und ohne Drucker darf hier kein Grün stehen."""
    pg, _ = seite
    farben = pg.eval_on_selector_all(
        "#einrichtungStand span", "els => els.map(e => e.style.color)")
    assert farben, "Keine Ampel gefunden"
    gruen = [f for f in farben if "46, 158, 91" in f or "2e9e5b" in f.lower()]
    assert not gruen, f"Grün, obwohl nichts nachgewiesen ist: {farben}"


def test_nur_der_passende_einrichtungsweg_wird_gezeigt(seite):
    """Unter Linux gehört der Canon-SDK-Block nicht auf den Schirm."""
    pg, _ = seite
    assert pg.is_hidden("#edsdkBlock"), "Der Windows-Weg steht auf einem Linux-System"
    assert pg.is_visible("#linuxBlock")


def test_die_kameraeinstellungen_stehen_da(seite):
    pg, _ = seite
    text = pg.inner_text("#kameraEinstellungen")
    assert "Sony" in text and "PC-Fernsteuerung" in text
    assert "Nikon" in text and "MTP/PTP" in text


def test_freigeben_ist_gesperrt_wenn_nichts_blockiert(seite):
    """Ein Knopf, der nichts zu tun hat, darf nicht klickbar aussehen."""
    pg, _ = seite
    assert pg.is_disabled("#linuxFrei")


def test_das_sdk_archiv_geht_wirklich_hoch(seite, box):
    """Der Upload-Weg für die .exe/.dll — samt ehrlicher Absage bei Unsinn."""
    pg, _ = seite
    pg.evaluate("""async () => {
        const fd = new FormData();
        fd.append("datei", new File([new TextEncoder().encode("kein zip")], "EDSDK.zip"));
        const r = await fetch("/api/admin/einrichtung/edsdk", {method: "POST",
            headers: {"X-Admin-Token": window.__token}, body: fd});
        window.__sdk = {status: r.status, body: await r.json()};
    }""")
    pg.wait_for_timeout(500)
    ergebnis = pg.evaluate("window.__sdk")
    assert ergebnis["status"] == 200
    assert ergebnis["body"]["ok"] is False
    assert "Canon" in ergebnis["body"]["hinweis"], "Die Absage muss sagen, was gebraucht wird"


def test_die_reihenfolge_beim_anmelden_ist_festgenagelt():
    """Ein Wettrennen lässt sich im Browser nicht verlässlich nachstellen.

    ``load()`` setzt ``PROFILE``, ``medienLaden()`` füllt daraus die
    Auswahlfelder. Liefen beide nebeneinander, gewann mal das eine, mal das
    andere — die Töne standen dann auf „eingebauter Ton", obwohl im Profil
    etwas anderes stand. Gefunden hat das ein Browsertest, aber nur, weil
    eine dritte Ladefunktion die Zeiten verschoben hat. Verlassen kann man
    sich darauf nicht: Beim Gegentest lief es zufällig wieder richtig
    durch.

    Deshalb hier ein Test, der nicht misst, sondern festhält — die einzige
    Art, ein Wettrennen deterministisch zu verhindern.
    """
    quelle = page(Path(__file__).resolve().parents[1] / "boothcore" / "web" / "admin.html")
    stelle = quelle.index('$("pin").hidden = true;')
    block = quelle[stelle:stelle + 700]
    assert "await load();" in block, \
        "load() muss abgewartet werden, bevor die Felder daraus gefüllt werden"
    assert block.index("await load();") < block.index("medienLaden();")
    assert block.index("medienLaden();") < block.index("einrichtungLaden();")
