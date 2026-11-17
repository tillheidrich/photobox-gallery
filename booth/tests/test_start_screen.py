"""Der Startbildschirm — alles, was an einer Box im Einsatz draufsteht.

Vorbild ist der Zettel, der an fast jeder Fotobox klebt: Anleitung, ein Blick
auf die letzte Aufnahme, ein QR zur Galerie, das Logo des Betreibers. Bisher
stand das auf Papier daneben. Jetzt steht es auf dem Bildschirm — und nichts
davon liegt über den Knöpfen.
"""
from pathlib import Path

WEB = Path(__file__).resolve().parents[1] / "boothcore" / "web"
JS = (WEB / "static" / "app.js").read_text()
CSS = (WEB / "static" / "app.css").read_text()
HTML = (WEB / "index.html").read_text()


def test_the_profile_carries_all_four():
    from boothcore.config import DEFAULT_PROFILE
    st = DEFAULT_PROFILE["start"]
    assert st["steps"] == "aus", "Nichts davon drängt sich von allein auf"
    assert st["show_last"] is False and st["show_qr"] is False
    assert st["logo"] == "" and 4 <= st["logo_size"] <= 50


def test_gallery_and_booth_ship_the_same_start_block():
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from app import ecosystem
    from boothcore.config import DEFAULT_PROFILE
    assert DEFAULT_PROFILE["start"] == ecosystem.DEFAULT_PROFILE["start"]


def test_the_instructions_can_be_off_always_or_on_demand():
    assert '["immer", "knopf"].includes(st.steps)' in JS
    assert 'id="stepsBtn"' in HTML, "Ohne Knopf gibt es kein Einblenden"
    assert 'case "steps":' in JS


def test_the_instructions_start_closed_for_every_guest():
    assert "stepsOffen = false;" in JS, "Sonst erbt der nächste Gast den Zustand des vorigen"


def test_the_text_is_the_operators_and_translatable():
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from app import booth_texts
    assert "start.steps" in booth_texts.DEFAULTS
    assert "start.steps" in booth_texts.SHIPPED_EN
    # Eine Zeile je Schritt: Die Nummerierung macht die Box, nicht der Text.
    assert "\n" in booth_texts.DEFAULTS["start.steps"]
    assert "1." not in booth_texts.DEFAULTS["start.steps"]


def test_nothing_lies_on_top_of_the_buttons():
    """Genau das sieht man an fremden Boxen: Die Anleitung deckt „Foto" zu."""
    assert '#s-start[data-steps="1"] .modes{left:36%}' in CSS
    assert '#s-start[data-logo="1"] .modes{right:20%}' in CSS
    assert '#s-start[data-last="1"] .start-copy{top:23vh}' in CSS


def test_the_labels_shrink_with_the_tile_not_the_window():
    """Sonst zerreißt „Boomerang", sobald die Reihe schmaler wird."""
    assert ".mode{container-type:inline-size}" in CSS
    assert "font-size:clamp(22px,11cqw,38px)" in CSS


def test_everything_can_be_placed_freely():
    for key in ("steps", "last", "qr", "logo", "status"):
        assert f'place($("#' in JS and f'"{key}"' in JS, f"{key} fehlt im freien Layout"
    assert 'place($("#statusline"), "status");' in JS


def test_booth_and_admin_agree_on_where_things_start():
    """Sonst springt beim ersten Anfassen im Editor alles an eine andere Stelle."""
    admin = (Path(__file__).resolve().parents[2] / "app" / "templates" / "admin"
             / "booth_profile.html").read_text()
    for stueck in ("steps: {x: 7, y: 58, w: 30}", "last: {x: 2, y: 3, w: 10}",
                   "qr: {x: 82, y: 19, w: 14}", "logo: {x: 78, y: 86, w: 18}",
                   "status: {x: 72, y: 2, w: 26}"):
        knapp = stueck.replace(" ", "")
        assert knapp in admin.replace(" ", ""), f"{stueck} fehlt im Editor"
        assert knapp in JS.replace(" ", ""), f"{stueck} fehlt in der Box"


def test_the_last_shot_stays_out_of_the_quiet_mode():
    """Im datensparsamen Betrieb soll nach dem Druck nichts mehr zu sehen sein."""
    # Entschieden wird das in der Box, nicht in der Oberfläche — geprüft wird
    # deshalb app.py. (Hier stand eine Zeile, die nie fehlschlagen konnte.)
    app = (Path(__file__).resolve().parents[1] / "boothcore" / "app.py").read_text()
    stelle = app.index("def _letztes_thumb(")
    assert "if b.ephemeral:" in app[stelle:stelle + 700]
    assert '"gallery_url": "" if b.ephemeral' in app


def test_the_start_qr_points_at_the_evening_not_at_one_picture():
    from boothcore import share
    url = share.event_url({"event": {"base_url": "https://g.example", "slug": "fest"}})
    assert url == "https://g.example/gallery/fest"
    assert share.event_url({"event": {"slug": "fest"}}) == "", "Ohne Adresse kein QR ins Leere"
    assert share.event_url({"event": {"base_url": "https://g.example", "slug": "f"},
                            "qr": {"mode": "off"}}) == ""
