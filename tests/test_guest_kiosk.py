"""Kiosk für Gäste — der zweite Bildschirm ohne zweite App.

dslrBooth verkauft dafür eine iPad-App. Nötig ist sie nicht: Ein iPad auf dem
Tresen, ein Handy am Ständer oder ein zweiter Rechner neben der Box öffnen
dieselbe Galerie im Vollbild. Die Fotos laufen als Show, ein Tipp hält an, und
wer sein Bild mitnehmen will, bekommt es übers Teilen-Blatt, als Download oder
per QR-Code aufs eigene Handy.

Der Betreiber schaltet das frei — vorher gibt es die Seite nicht.
"""
import json
import secrets

import pytest


def _event(client, **cfg_extra):
    client.get("/healthz")
    from app import db, gallery_settings as gsm, media
    from PIL import Image
    slug = "kiosk-" + secrets.token_hex(3)
    eid = db.create_event(name="Sommerfest", slug=slug, upload_token=secrets.token_hex(8))
    cfg = dict(gsm.DEFAULTS)
    cfg.update(cfg_extra)
    con = db.get_db()
    con.execute("UPDATE events SET published=1, gallery_json=? WHERE id=?", (json.dumps(cfg), eid))
    con.commit(); con.close()
    rel = f"storage/{slug}/eins.jpg"
    voll = media.data_dir() / rel
    voll.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (400, 300), (80, 110, 140)).save(voll, "JPEG")
    mid = db.add_media(event_id=eid, kind="photo", filename="eins.jpg", path_original=rel,
                       path_webp=rel, path_thumb=rel, size_bytes=voll.stat().st_size,
                       sha256=secrets.token_hex(8), share_token=secrets.token_hex(6))
    con = db.get_db(); con.execute("UPDATE media SET width=400, height=300 WHERE id=?", (mid,)); con.commit(); con.close()
    ev = db.get_event_by_id(eid)
    ev["_media_id"] = mid
    return ev


def test_ohne_freischaltung_gibt_es_die_seite_nicht(client):
    ev = _event(client, kiosk=False)
    assert client.get(f"/schau/{ev['slug']}").status_code == 404


def test_freigeschaltet_laeuft_der_kiosk(client):
    ev = _event(client, kiosk=True)
    seite = client.get(f"/schau/{ev['slug']}")
    assert seite.status_code == 200
    text = seite.get_data(as_text=True)
    assert "gkTap" in text and "slideshow.json" in text


def test_der_betreiber_sieht_ihn_auch_ungeschaltet(client):
    ev = _event(client, kiosk=False)
    with client.session_transaction() as s:
        s["admin_logged_in"] = True
    assert client.get(f"/schau/{ev['slug']}").status_code == 200


def test_die_reihenfolge_reist_mit(client):
    zufall = _event(client, kiosk=True, show_order="random")
    text = client.get(f"/schau/{zufall['slug']}").get_data(as_text=True)
    assert '"random"' in text
    # Und der Beamer nimmt dieselbe Einstellung.
    assert '"random"' in client.get(f"/slideshow/{zufall['slug']}").get_data(as_text=True)


def test_ohne_mitnehmen_keine_knoepfe(client):
    ohne = _event(client, kiosk=True, kiosk_share=False)
    text = client.get(f"/schau/{ohne['slug']}").get_data(as_text=True)
    assert 'id="gkShare"' not in text and 'id="gkDownload"' not in text and 'id="gkQr"' not in text
    mit = _event(client, kiosk=True, kiosk_share=True)
    text = client.get(f"/schau/{mit['slug']}").get_data(as_text=True)
    assert 'id="gkShare"' in text and 'id="gkDownload"' in text


def test_der_download_folgt_der_galerie_einstellung(client):
    ev = _event(client, kiosk=True, kiosk_share=True, download_single=False)
    text = client.get(f"/schau/{ev['slug']}").get_data(as_text=True)
    assert 'id="gkDownload"' not in text     # kein Knopf, der doch nur 403 bringt
    assert 'id="gkQr"' in text               # der QR-Weg bleibt


def test_das_wasserzeichen_schliesst_den_download_aus(client):
    ev = _event(client, kiosk=True, kiosk_share=True, watermark=True, watermark_text="Muster")
    assert 'id="gkDownload"' not in client.get(f"/schau/{ev['slug']}").get_data(as_text=True)


def test_der_qr_code_zum_einzelnen_foto_zeigt_auf_dessen_seite(client):
    ev = _event(client, kiosk=True)
    antwort = client.get(f"/gallery/{ev['slug']}/photo/{ev['_media_id']}/qr.png")
    assert antwort.status_code == 200 and antwort.mimetype == "image/png"

    import io
    import numpy as np
    from PIL import Image
    cv2 = pytest.importorskip("cv2")
    bild = Image.open(io.BytesIO(antwort.data)).convert("RGB")
    text, *_ = cv2.QRCodeDetector().detectAndDecode(np.array(bild)[:, :, ::-1])
    assert text, "QR-Code nicht lesbar"
    from app import db
    token = db.get_media(ev["_media_id"])["share_token"]
    assert f"/s/{token}" in text


def test_fremde_fotos_gibt_es_nicht_ueber_den_qr(client):
    eins = _event(client, kiosk=True)
    zwei = _event(client, kiosk=True)
    assert client.get(f"/gallery/{eins['slug']}/photo/{zwei['_media_id']}/qr.png").status_code == 404


def test_im_stillen_betrieb_ist_auch_der_kiosk_zu(client):
    ev = _event(client, kiosk=True, silent=True)
    assert client.get(f"/schau/{ev['slug']}").status_code == 404
    assert client.get(f"/gallery/{ev['slug']}/photo/{ev['_media_id']}/qr.png").status_code == 404


def test_hinter_dem_passwort_bleibt_er_zu(client):
    from app import db
    ev = _event(client, kiosk=True)
    db.set_event_password(ev["id"], "geheim1234")
    frisch = client.application.test_client()
    assert frisch.get(f"/schau/{ev['slug']}").status_code == 403
    mit = client.application.test_client()
    mit.get(f"/gallery/{ev['slug']}?access_token={ev['access_token']}")
    assert mit.get(f"/schau/{ev['slug']}").status_code == 200


def test_der_kiosk_laesst_sich_mit_der_tastatur_bedienen(client):
    ev = _event(client, kiosk=True)
    text = client.get(f"/schau/{ev['slug']}").get_data(as_text=True)
    assert "<button" in text and 'id="gkTap"' in text      # kein klickbares <div>
    assert "keydown" in text and "ArrowRight" in text and "Escape" in text
    assert "inert" in text                                  # offene Flächen melden den Rest ab
