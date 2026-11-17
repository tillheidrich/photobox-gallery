"""The template library: built-in designs the admin takes over with one click.

The collection ships with the gallery (app/vorlagen_bibliothek/). The admin
page lists it by family and category with previews and a search; "Übernehmen"
copies one template, its pictures and fonts into the gallery's own templates.
Nothing the installation already has is overwritten: an identical template is
reported as already there, a different one under the same name stays, and the
library's copy gets a name of its own.
"""
import json

import pytest

from app import ecosystem, vorlagen_bibliothek


@pytest.fixture()
def admin_client(client):
    with client.session_transaction() as s:
        s["admin_logged_in"] = True
        s["csrf_token"] = "test-token"
    client.get("/healthz")
    ecosystem.init_schema()
    return client


def _ordner():
    from app import media
    return media.assets_root() / "booth"


def _eine_mit_bildern():
    for fam in vorlagen_bibliothek.katalog()["familien"]:
        for v in fam["vorlagen"]:
            inhalt = vorlagen_bibliothek.vorlage(v["datei"])
            if inhalt.get("images") and any(t.get("font", "").endswith(".ttf") for t in inhalt["texts"]):
                return v["datei"], inhalt
    raise AssertionError("no template with pictures and fonts in the library")


def test_the_catalogue_lists_families_with_previews_that_exist():
    kat = vorlagen_bibliothek.katalog()
    assert len(kat["familien"]) >= 10
    for fam in kat["familien"]:
        assert fam["name"] and fam["kategorie"] and fam["vorlagen"]
        for v in fam["vorlagen"]:
            assert vorlagen_bibliothek.vorschau_pfad(v["vorschau"]) is not None
            assert vorlagen_bibliothek.vorlage(v["datei"])["id"] == v["id"]


def test_every_library_template_passes_the_check_unchanged():
    from app import vorlagen_kern
    for fam in vorlagen_bibliothek.katalog()["familien"]:
        for v in fam["vorlagen"]:
            inhalt = vorlagen_bibliothek.vorlage(v["datei"])
            geprueft, hinweise = vorlagen_kern.pruefe(inhalt)
            assert not hinweise, (v["datei"], hinweise)
            assert geprueft["slots"] == inhalt["slots"]


def test_the_page_lists_and_filters(admin_client):
    body = admin_client.get("/admin/vorlagen/bibliothek").get_data(as_text=True)
    assert "Grün &amp; Gold" in body and "Übernehmen" in body
    gefiltert = admin_client.get("/admin/vorlagen/bibliothek?q=nachtblau").get_data(as_text=True)
    assert "Nachtblau" in gefiltert and "Grün &amp; Gold" not in gefiltert
    nur = admin_client.get("/admin/vorlagen/bibliothek?kategorie=Firma").get_data(as_text=True)
    assert "Business klar" in nur and "Boho Terrakotta" not in nur


def test_taking_over_copies_template_pictures_and_fonts(admin_client):
    datei, inhalt = _eine_mit_bildern()
    r = admin_client.post("/admin/vorlagen/bibliothek/uebernehmen",
                          data={"csrf_token": "test-token", "datei": datei})
    assert r.status_code == 302
    eigene = {a["file"]: a for a in ecosystem.list_assets()}
    assert datei in eigene and eigene[datei]["kind"] == "template"
    for b in inhalt["images"]:
        assert eigene[b["file"]]["kind"] == "overlay"
        assert (_ordner() / b["file"]).is_file()
    for t in inhalt["texts"]:
        if t.get("font", "").endswith(".ttf"):
            assert eigene[t["font"]]["kind"] == "font"


def test_taking_over_twice_does_not_duplicate(admin_client):
    datei, _ = _eine_mit_bildern()
    for _ in range(2):
        admin_client.post("/admin/vorlagen/bibliothek/uebernehmen",
                          data={"csrf_token": "test-token", "datei": datei})
    vorlagen = [a for a in ecosystem.list_assets("template") if a["file"].startswith(datei[:-5])]
    assert len(vorlagen) == 1


def test_an_own_template_of_the_same_name_is_never_overwritten(admin_client):
    datei, _ = _eine_mit_bildern()
    ordner = _ordner()
    ordner.mkdir(parents=True, exist_ok=True)
    eigen = {"id": datei[:-5], "name": "Meine eigene", "paper": "10x15", "canvas": [1200, 1800],
             "slots": [{"x": 60, "y": 60, "w": 1080, "h": 700}], "texts": []}
    (ordner / datei).write_text(json.dumps(eigen), encoding="utf-8")
    if not any(a["file"] == datei for a in ecosystem.list_assets()):
        ecosystem.add_asset("template", "Meine eigene", datei, "x", 1)
    admin_client.post("/admin/vorlagen/bibliothek/uebernehmen",
                      data={"csrf_token": "test-token", "datei": datei})
    assert json.loads((ordner / datei).read_text(encoding="utf-8"))["name"] == "Meine eigene"
    neu = [a for a in ecosystem.list_assets("template") if a["file"] != datei and a["file"].startswith(datei[:-5])]
    assert len(neu) == 1


def test_unknown_names_and_paths_are_refused(admin_client):
    for name in ("../../etc/passwd", "gibt-es-nicht.json", "katalog.json"):
        r = admin_client.post("/admin/vorlagen/bibliothek/uebernehmen",
                              data={"csrf_token": "test-token", "datei": name})
        assert r.status_code in (302, 400, 404)
        assert name not in {a["file"] for a in ecosystem.list_assets()}
    assert admin_client.get("/admin/vorlagen/bibliothek/vorschau/..%2F..%2Fapp.py").status_code == 404


def test_the_library_needs_the_admin(client):
    client.get("/healthz")
    assert client.get("/admin/vorlagen/bibliothek").status_code in (302, 401, 403)
    assert client.post("/admin/vorlagen/bibliothek/uebernehmen", data={"datei": "x.json"}).status_code in (
        302, 400, 401, 403)
