"""Wie die Datei heißt, die der Gast bekommt.

Aus der Box kommt ``abcd2345xy_1.jpg``. Wer zwanzig Aufnahmen von drei
Veranstaltungen im Download-Ordner liegen hat, sieht zwanzigmal dasselbe
Muster und weiß bei keiner, wozu sie gehört. Also ein Muster je Event.

Geprüft wird vor allem, was dabei nicht passieren darf: kein Pfad im Namen,
keine zwei gleichen Namen in einem ZIP, keine Datei, die mit einem Punkt
anfängt — und die Dateien auf der Platte bleiben unberührt.
"""
import io
import json
import secrets
import zipfile

import pytest
from PIL import Image

from app import db, dateinamen, ecosystem, gallery_settings as gsm, media as media_mod


@pytest.fixture(autouse=True)
def schema(client):
    client.get("/healthz")
    with client.application.app_context():
        db.init_db()
        ecosystem.init_schema()
        db.set_setting("download_pattern", "")
        db.set_setting("zip_download_enabled", "1")
        db.set_setting("zip_download_admin_only", "0")
    yield


EVENT = {"slug": "sommerfest", "name": "Sommerfest 2026"}
MEDIUM = {"created_at": "2026-07-20T21:14:03", "kind": "photo",
          "filename": "abcd2345xy_1.jpg", "id": 4711}


# ── Das Muster selbst ─────────────────────────────────────────────────
@pytest.mark.parametrize("muster, erwartet", [
    ("{event}-{datum}-{nr}", "sommerfest-2026-07-20-0007.jpg"),
    ("{event_name} {nr2}", "Sommerfest 2026 07.jpg"),
    ("{jahr}{monat}{tag}_{zeit}", "20260720_21-14-03.jpg"),
    ("{art}-{id}", "photo-4711.jpg"),
    ("{original}", "abcd2345xy_1.jpg"),
    ("{groesse}-{nr6}", "web-000007.jpg"),
])
def test_the_pattern_becomes_the_name(muster, erwartet):
    assert dateinamen.baue(muster, event=EVENT, medium=MEDIUM, endung="jpg",
                           nummer=7, groesse="web", rueckfall="x.jpg") == erwartet


def test_an_empty_pattern_changes_nothing():
    assert dateinamen.baue("", event=EVENT, medium=MEDIUM, endung="jpg",
                           rueckfall="abcd2345xy_1.jpg") == "abcd2345xy_1.jpg"


@pytest.mark.parametrize("muster", ["../{nr}", "ordner/{nr}", "a\\b", "{nr}/..", "x" * 200])
def test_a_pattern_may_not_contain_a_path(muster):
    assert dateinamen.gueltig(muster) is False
    assert dateinamen.baue(muster, event=EVENT, medium=MEDIUM, endung="jpg",
                           rueckfall="sicher.jpg") == "sicher.jpg"


def test_umlauts_stay_readable_and_never_break_a_filesystem():
    event = {"slug": "gruesse", "name": "Müllers Fest & Co."}
    name = dateinamen.baue("{event_name}-{nr}", event=event, medium=MEDIUM,
                           endung="jpg", nummer=3, rueckfall="x.jpg")
    assert name == "Muellers Fest-Co-0003.jpg"
    assert "/" not in name and not name.startswith(".")


def test_a_pattern_that_ends_up_empty_falls_back():
    assert dateinamen.baue("...", event=EVENT, medium=MEDIUM, endung="jpg",
                           rueckfall="sicher.jpg") == "sicher.jpg"


def test_an_unknown_placeholder_is_left_standing_not_swallowed():
    """Wer sich vertippt, soll es am Ergebnis sehen."""
    name = dateinamen.baue("{gibtsnicht}-{nr}", event=EVENT, medium=MEDIUM,
                           endung="jpg", nummer=1, rueckfall="x.jpg")
    assert "gibtsnicht" in name


# ── Die laufende Nummer ───────────────────────────────────────────────
def _event_mit_fotos(client, anzahl=3, **gs_werte):
    kennung = secrets.token_hex(4)
    with client.application.app_context():
        eid = db.create_event(name="Sommerfest", slug=f"dn-{kennung}",
                              upload_token=f"tok-{kennung}")
        db.update_event(eid, {"published": 1, "gallery_json": json.dumps(gsm.clean(gs_werte))})
        wurzel = media_mod.data_dir()
        con = db.get_db()
        ids = []
        for i in range(anzahl):
            rel = f"storage/dn-{kennung}/orig/f{i}.jpg"
            p = wurzel / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            Image.new("RGB", (80, 100), (120, 110 + i * 10, 100)).save(p)
            cur = con.execute(
                "INSERT INTO media (event_id, filename, path_original, kind, share_token, "
                "created_at) VALUES (?,?,?,?,?,?)",
                (eid, f"f{i}.jpg", rel, "photo", secrets.token_hex(8),
                 f"2026-07-20T10:0{i}:00"))
            ids.append(int(cur.lastrowid))
        con.commit()
        con.close()
        return db.get_event_by_id(eid), ids


def test_the_running_number_counts_from_the_oldest(client):
    event, ids = _event_mit_fotos(client, 3)
    with client.application.app_context():
        nummern = dateinamen.nummern(event["id"])
    assert [nummern[i] for i in ids] == [1, 2, 3]


def test_one_query_and_many_single_lookups_agree(client):
    """Die schnelle Fassung und die einzelne dürfen nicht auseinanderlaufen."""
    event, ids = _event_mit_fotos(client, 4)
    with client.application.app_context():
        aus_einer_abfrage = dateinamen.nummern(event["id"])
        einzeln = {i: dateinamen.laufende_nummer(event["id"], db.get_media(i)) for i in ids}
    assert aus_einer_abfrage == einzeln


# ── Im Betrieb ────────────────────────────────────────────────────────
def test_a_single_download_carries_the_pattern(client):
    event, ids = _event_mit_fotos(client, 2, download_pattern="{event}-{nr}")
    antwort = client.get(f"/gallery/{event['slug']}/photo/{ids[1]}/download?size=original")
    assert antwort.status_code == 200
    assert f"{event['slug']}-0002.jpg" in antwort.headers["Content-Disposition"]


def test_the_event_beats_the_instance(client):
    with client.application.app_context():
        db.set_setting("download_pattern", "{event_name}-{nr}")
    event, ids = _event_mit_fotos(client, 1, download_pattern="{art}-{nr}")
    antwort = client.get(f"/gallery/{event['slug']}/photo/{ids[0]}/download?size=original")
    assert "photo-0001.jpg" in antwort.headers["Content-Disposition"]


def test_without_an_event_pattern_the_instance_counts(client):
    with client.application.app_context():
        db.set_setting("download_pattern", "{jahr}-{nr2}")
    event, ids = _event_mit_fotos(client, 1)
    antwort = client.get(f"/gallery/{event['slug']}/photo/{ids[0]}/download?size=original")
    assert "2026-01.jpg" in antwort.headers["Content-Disposition"]


def test_the_zip_uses_the_pattern_and_never_the_same_name_twice(client):
    """Zwei gleiche Namen in einem ZIP sind ein verlorenes Foto."""
    event, ids = _event_mit_fotos(client, 3, download_pattern="immergleich")
    with client.session_transaction() as s:
        s["admin_logged_in"] = True
        s["csrf_token"] = "x"
    antwort = client.get(f"/gallery/{event['slug']}/download.zip")
    assert antwort.status_code == 200
    namen = zipfile.ZipFile(io.BytesIO(antwort.data)).namelist()
    assert len(namen) == 3 and len(set(namen)) == 3, namen
    assert all(n.startswith("immergleich") for n in namen)


def test_the_files_on_disk_keep_their_names(client):
    """Umbenannt wird nur, was ausgeliefert wird."""
    event, ids = _event_mit_fotos(client, 1, download_pattern="{event}-{nr}")
    client.get(f"/gallery/{event['slug']}/photo/{ids[0]}/download?size=original")
    with client.application.app_context():
        assert db.get_media(ids[0])["filename"] == "f0.jpg"
        assert (media_mod.data_dir() / db.get_media(ids[0])["path_original"]).exists()


def test_a_broken_pattern_never_reaches_the_settings():
    assert gsm.clean({"download_pattern": "../../etc/passwd"})["download_pattern"] == ""
