"""The guest filter for unframed captures says what it shows (18.11.2026).

``?show=originals`` lists the pictures of kind ``original``: the camera's
single shots uploaded without frame (booth switch "Bilder zusätzlich ohne
Rahmen hochladen"). A capture with a framed single of its own is offered in
that picture's large view ("Ohne Rahmen") and is not counted here, so what is
left are mostly the single shots that went into a collage. "Originalfotos"
read like "the real photos" to guests; the label is now "Einzelfotos ohne
Rahmen" / "Single shots without frame". The URL parameter is unchanged.
"""
from app import db
from tests.test_link_preview import BLUE_PIC, GREEN_PIC, _event, _photo, schema  # noqa: F401


def test_the_filter_names_single_shots_without_frame_and_the_url_still_works(client):
    ev = _event()
    for _ in range(2):
        _photo(ev, BLUE_PIC)
    for _ in range(3):
        _photo(ev, GREEN_PIC, kind="original")
    html = client.get(f"/gallery/{ev['slug']}").get_data(as_text=True)
    assert 'href="?show=originals">Einzelfotos ohne Rahmen (3)</a>' in html
    assert "Originalfotos" not in html
    r = client.get(f"/gallery/{ev['slug']}?show=originals")
    assert r.status_code == 200
    assert 'aria-current="page">Einzelfotos ohne Rahmen (3)</a>' in r.get_data(as_text=True)
    client.post("/lang/en")
    assert "Single shots without frame (3)" in client.get(f"/gallery/{ev['slug']}").get_data(as_text=True)
