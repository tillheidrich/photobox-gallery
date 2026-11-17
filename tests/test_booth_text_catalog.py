"""Der Textkatalog im Backend muss zur Oberfläche der Box passen.

Sonst steht im Formular ein Feld, das nichts bewirkt — oder ein Text der Box
lässt sich nicht ändern, weil ihn niemand findet.
"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
I18N = ROOT / "booth" / "boothcore" / "web" / "static" / "i18n.js"


def _keys(block: str) -> set:
    return set(re.findall(r'"([a-z0-9._:]+)":', block))


def _entschluesselt(wert: str) -> str:
    """Ein JS-Textliteral als Text — Anführungszeichen und Zeilenumbrüche.

    In der Datei steht \\n; gemeint ist ein Zeilenumbruch. Verglichen wird
    der Text, nicht seine Schreibweise in JavaScript.
    """
    return wert.replace('\\"', '"').replace("\\n", "\n")


def _blocks():
    js = I18N.read_text(encoding="utf-8")
    return js[js.index("de: {"):js.index("en: {")], js[js.index("en: {"):]


def test_catalog_covers_every_key_of_the_kiosk():
    from app import booth_texts
    de, _ = _blocks()
    assert _keys(de) == set(booth_texts.KEYS), (
        "Neuer Text in der Box? Dann gehört er in app/booth_texts.py — sonst kann ihn niemand ändern.")


def test_both_languages_carry_the_same_keys():
    de, en = _blocks()
    assert _keys(de) == _keys(en)


def test_defaults_match_the_shipped_german_texts():
    from app import booth_texts
    de, _ = _blocks()
    shipped = dict(re.findall(r'"([a-z0-9._:]+)":\s*"((?:[^"\\]|\\.)*)"', de))
    for key, default in booth_texts.DEFAULTS.items():
        assert _entschluesselt(shipped[key]) == default, f"{key}: Vorlage im Formular ist veraltet"


def test_every_key_has_a_human_label():
    from app import booth_texts
    for group, entries in booth_texts.GROUPS:
        assert group and entries
        for key, label, _default in entries:
            assert label and label != key, f"{key} braucht eine Beschriftung"


def test_form_saves_every_catalogued_text(client):
    """Rundlauf: jeder Schlüssel aus dem Katalog kommt über das Formular im Profil an."""
    client.get("/healthz")                      # Schema anlegen
    from app import booth_texts, db, ecosystem
    ecosystem.init_schema()
    with client.session_transaction() as s:
        s["admin_logged_in"] = True
    dev_id = db.create_device(name="Textbox")["id"]

    page = client.get(f"/admin/devices/{dev_id}/booth")
    assert page.status_code == 200
    body = page.get_data(as_text=True)
    for key in booth_texts.KEYS:
        assert f'name="text_de_{key}"' in body, f"{key} fehlt im Formular"
        assert f'name="text_en_{key}"' in body, f"{key} fehlt in der zweiten Sprache"

    with client.session_transaction() as s:
        token = s.get("csrf_token") or ""
    r = client.post(f"/admin/devices/{dev_id}/booth", data={
        "csrf_token": token, "mode_photo": "on",
        "text_de_start.title": "Bitte lächeln",
        "text_de_result.print": "Drucken!",
        "text_en_result.print": "Print it",
        "text_de_start.subtitle": "   ",          # nur Leerzeichen zählt als „nicht gesetzt“
        "texts_json": '{"fr": {"start.title": "Souriez"}}',
    }, follow_redirects=True)
    assert r.status_code == 200

    texts = (ecosystem.device_profile(db.get_device_by_id(dev_id)) or {}).get("texts") or {}
    assert texts["de"]["start.title"] == "Bitte lächeln"
    assert texts["de"]["result.print"] == "Drucken!"
    assert texts["en"]["result.print"] == "Print it"
    assert texts["fr"]["start.title"] == "Souriez"
    # Leere Felder werden nicht gespeichert — sonst stünde der Standard als Kopie im Profil.
    assert "start.subtitle" not in texts["de"]


def test_form_shows_the_shipped_text_as_placeholder(client):
    """Der Betreiber muss sehen, was er überschreibt — auch in der zweiten Sprache."""
    client.get("/healthz")
    from app import db, ecosystem
    ecosystem.init_schema()
    with client.session_transaction() as s:
        s["admin_logged_in"] = True
    dev_id = db.create_device(name="Textbox 2")["id"]
    body = client.get(f"/admin/devices/{dev_id}/booth").get_data(as_text=True)
    flat = " ".join(body.split())
    assert 'name="text_de_start.title" value="" placeholder="Tippen &amp; lächeln"' in flat
    assert 'name="text_en_start.title" value="" placeholder="Tap &amp; smile"' in flat


def test_shipped_english_matches_the_kiosk():
    from app import booth_texts
    _de, en = _blocks()
    shipped = dict(re.findall(r'"([a-z0-9._:]+)":\s*"((?:[^"\\]|\\.)*)"', en))
    assert set(shipped) == set(booth_texts.SHIPPED_EN)
    for key, text in booth_texts.SHIPPED_EN.items():
        assert _entschluesselt(shipped[key]) == text, f"{key}: englische Vorlage ist veraltet"
