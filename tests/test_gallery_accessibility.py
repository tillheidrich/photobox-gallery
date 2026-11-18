"""Die Galerie auf Kiosk-Niveau: mit Tastatur, mit Screenreader, mit Daumen.

Am Kiosk gilt das schon. Für die Galerie ist es genauso wichtig — sie ist
die Seite, die Gäste tagelang nach dem Fest noch öffnen, auf allen Geräten
und mit allen Hilfsmitteln. Geprüft wird hier, was sich prüfen lässt:
Struktur, Beschriftungen, Tastaturwege, und dass Bewegung abschaltbar ist.
Der Rest bleibt Handarbeit mit VoiceOver und NVDA.
"""
import json
import re
import secrets
from pathlib import Path

import pytest


def _event(client, name="Sommerfest"):
    client.get("/healthz")
    from app import db, media
    from PIL import Image
    slug = "a11y-" + secrets.token_hex(3)
    eid = db.create_event(name=name, slug=slug, upload_token=secrets.token_hex(8))
    con = db.get_db(); con.execute("UPDATE events SET published=1 WHERE id=?", (eid,)); con.commit(); con.close()
    # Ein Foto, damit das Raster nicht leer ist.
    rel = f"storage/{slug}/eins.jpg"
    voll = media.data_dir() / rel
    voll.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (80, 60), (120, 90, 60)).save(voll, "JPEG")
    mid = db.add_media(event_id=eid, kind="photo", filename="eins.jpg", path_original=rel,
                       path_webp=rel, path_thumb=rel, size_bytes=voll.stat().st_size,
                       sha256=secrets.token_hex(8), share_token=secrets.token_hex(6))
    con = db.get_db(); con.execute("UPDATE media SET width=80, height=60 WHERE id=?", (mid,)); con.commit(); con.close()
    return db.get_event_by_id(eid)


@pytest.fixture()
def seite(client):
    ev = _event(client)
    html = client.get(f"/gallery/{ev['slug']}").get_data(as_text=True)
    return html


def test_der_erste_tabstopp_springt_zum_inhalt(seite):
    assert 'class="skip-link" href="#inhalt"' in seite
    assert 'id="inhalt"' in seite


def test_die_seite_hat_benannte_bereiche(seite):
    assert re.search(r'<nav class="public-nav" aria-label=', seite) or 'aria-label="Hauptnavigation"' in seite
    assert "<main" in seite and "<footer" in seite


def test_im_raster_steckt_kein_knopf_in_einem_link(seite):
    """Knöpfe in einem Link sind für Tastatur und Screenreader kaputt."""
    raster = seite[seite.index('id="photoGrid"'):]
    raster = raster[:raster.index("</div>\n</div>")] if "</div>\n</div>" in raster else raster
    for treffer in re.finditer(r"<a\b[^>]*>(.*?)</a>", raster, re.S):
        assert "<button" not in treffer.group(1)


def test_jede_kachel_ist_mit_der_tastatur_zu_oeffnen(seite):
    assert 'class="photo-open"' in seite
    assert "Foto öffnen" in seite or "Open photo" in seite


def test_jedes_bild_hat_ein_alt_attribut(seite):
    for treffer in re.finditer(r"<img\b[^>]*>", seite):
        assert "alt=" in treffer.group(0), treffer.group(0)


def test_die_lightbox_ist_benannt_und_meldet_sich(seite):
    lb = seite[seite.index('id="lightbox"'):]
    assert 'aria-modal="true"' in lb[:400]
    assert "aria-label=" in lb[:400]
    assert 'id="lbCounter"' in lb and 'aria-live="polite"' in lb[:800]


def test_die_lightbox_haelt_den_fokus_fest_und_gibt_ihn_zurueck(seite):
    assert "hinterMir" in seite and "inert" in seite
    assert "zurueck.focus" in seite
    assert "e.key === 'Tab'" in seite


def test_die_filterknoepfe_sagen_was_gedrueckt_ist(seite):
    assert "aria-pressed" in seite


def test_was_sichtbar_passiert_wird_auch_gesagt(seite):
    assert "pbSagen" in seite and 'role\', \'status\'' in seite.replace('"', "'")


def test_bewegung_ist_abschaltbar_und_ziele_sind_fingergross():
    css = Path("app/static/css/public.css").read_text()
    assert "prefers-reduced-motion" in css
    block = css[css.index("@media (pointer: coarse)"):]
    assert "44px" in block[:600]


def test_der_fokus_ist_ueberall_sichtbar():
    css = Path("app/static/css/public.css").read_text()
    ring = css[css.index("a:focus-visible"):]
    assert "outline" in ring[:400]


def test_auch_die_passwortseite_und_die_startseite_tragen_den_sprunglink(client):
    from app import db
    ev = _event(client)
    db.set_event_password(ev["id"], "geheim1234")
    for pfad in ("/", f"/gallery/{ev['slug']}"):
        assert "skip-link" in client.get(pfad).get_data(as_text=True), pfad


# ── Funde der Durchsicht vom 19.09.2026 ───────────────────────────────

def test_das_download_blatt_haelt_den_fokus_genauso_fest_wie_die_lightbox(seite):
    """``aria-modal="true"`` ohne Fokusklammer ist eine Behauptung, kein Zustand."""
    blatt = seite[seite.index("function dlAskSize"):]
    blatt = blatt[:blatt.index("window.dlAskSize") if "window.dlAskSize" in blatt else 3000]
    assert "setAttribute('inert'" in blatt, "Der Hintergrund bleibt bedienbar"
    assert "zuerst.focus()" in blatt, "Der Fokus wandert nicht ins Blatt"
    assert "vorher.focus()" in blatt, "Der Fokus kommt nicht zurück"
    assert "e.key !== 'Tab'" in blatt, "Keine Tab-Klammer"
    # Der Test oben war gruen, waehrend es im Browser nicht funktionierte:
    # Das Blatt steht im <main>, und `inert` auf dem <main> meldet das Blatt
    # mit ab. Die Lightbox zieht deshalb ans Ende des Dokuments -- das Blatt
    # muss es auch, sonst landet der Fokus nirgends.
    assert "document.body.appendChild(sheet)" in blatt, \
        "Das Blatt liegt im abgemeldeten Bereich und faengt den Fokus nicht"


def test_aria_pressed_steht_nur_auf_knoepfen(seite):
    """Ein Link ist kein Schalter — ``aria-pressed`` ist dort unzulässig."""
    assert "document.querySelectorAll('button.filter-btn')" in seite
    assert "document.querySelectorAll('.filter-btn').forEach(btn =>" not in seite


def test_der_aktive_filterlink_traegt_aria_current(client):
    ev = _event(client)
    html = client.get(f"/gallery/{ev['slug']}?show=originals").get_data(as_text=True)
    if 'href="?show=originals"' not in html:
        pytest.skip("Diese Galerie zeigt keine Originalspalte")
    assert 'aria-current="page"' in html


def test_die_kachelknoepfe_haben_einen_namen_nicht_nur_einen_tooltip(seite):
    """``title`` liest kein Screenreader zuverlässig vor und sieht keine Tastatur."""
    # Since 17.11.2026 the tile's buttons live in the large view (proofing-gallery
    # round); the rule is the same for them there.
    raster = seite[seite.index('id="lightbox"'):]
    raster = raster[:raster.index('id="lbStage"')]
    for stueck in re.findall(r"<button[^>]*>", raster):
        if "title=" in stueck:
            assert "aria-label=" in stueck, stueck[:160]


def test_die_ueberschriften_springen_keine_ebene(seite):
    ebenen = [int(m) for m in re.findall(r"<h([1-6])[ >]", seite)]
    assert ebenen and ebenen[0] == 1, "Die Seite beginnt nicht mit <h1>"
    for vorher, jetzt in zip(ebenen, ebenen[1:]):
        assert jetzt <= vorher + 1, f"Sprung von h{vorher} auf h{jetzt}"


def test_das_e_mail_feld_hat_ein_label_keinen_platzhalter_als_ersatz(seite):
    if 'id="subscribeEmail"' not in seite:
        pytest.skip("Diese Galerie fragt keine Mailadresse ab")
    assert 'for="subscribeEmail"' in seite
    kopf = seite[:seite.index('id="subscribeEmail"')]
    assert 'class="sr-only"' in kopf[-400:], "Das Label nutzt eine Klasse, die es nicht gibt"


def test_die_klasse_fuers_versteckte_label_gibt_es_wirklich():
    css = Path("app/static/css/design.css").read_text()
    assert ".sr-only" in css
