"""The start-screen QR works without a gallery when the operator set an own link.

A booth that is not paired and has the QR mode "custom" (Picdrop, Nextcloud,
an own page) used to show no start-screen QR at all, because ``event_url``
only knew the gallery. Now it shows the operator's link. The start screen has
no photo yet, so placeholders that name one photo cannot be filled: the link
is cut back to the part before the first of them (see share.start_link).
"""
from __future__ import annotations

from boothcore import share


def _profil(**qr):
    return {"event": {"slug": "fest", "name": "Sommerfest"}, "qr": dict(qr)}


def test_unpaired_custom_mode_shows_the_own_link():
    assert share.event_url(_profil(mode="custom", url="https://example.org/fotos")) == "https://example.org/fotos"


def test_unpaired_custom_mode_keeps_the_event_placeholder_and_the_utm():
    p = _profil(mode="custom", url="https://example.org/{event}/", utm="utm_source=box")
    assert share.event_url(p) == "https://example.org/fest/?utm_source=box"


def test_a_photo_placeholder_in_the_path_cuts_back_to_the_folder():
    p = _profil(mode="custom", url="https://picdrop.example/abc/{uid}")
    assert share.event_url(p) == "https://picdrop.example/abc/"
    p = _profil(mode="custom", url="https://picdrop.example/abc/foto-{code}.jpg?x=1")
    assert share.event_url(p) == "https://picdrop.example/abc/"


def test_a_photo_placeholder_in_the_query_drops_the_query():
    p = _profil(mode="custom", url="https://cloud.example/s/tok/download?path=%2F&files={filename}")
    assert share.event_url(p) == "https://cloud.example/s/tok/download"


def test_custom_mode_without_a_web_link_shows_nothing():
    for url in ("", "ftp://example.org/x", "javascript:alert(1)", "example.org/fotos", "https:///nohost",
                "https://{uid}.example.org/"):
        assert share.event_url(_profil(mode="custom", url=url)) == "", url


def test_the_old_template_field_counts_as_the_own_link():
    p = {"event": {"slug": "fest"}, "qr": {"mode": "custom"}, "qr_url_template": "https://alt.example/g/{uid}"}
    assert share.event_url(p) == "https://alt.example/g/"


def test_paired_booth_keeps_the_gallery_link_in_every_mode():
    event = {"base_url": "https://g.example", "slug": "fest", "share_key": "k"}
    for mode in ("auto", "gallery", "custom"):
        p = {"event": event, "qr": {"mode": mode, "url": "https://example.org/{uid}"}}
        assert share.event_url(p) == "https://g.example/gallery/fest", mode


def test_unpaired_auto_and_gallery_modes_still_show_nothing():
    for mode in ("auto", "gallery"):
        assert share.event_url(_profil(mode=mode, url="https://example.org/fotos")) == "", mode


def test_off_stays_off():
    assert share.event_url(_profil(mode="off", url="https://example.org/fotos")) == ""
