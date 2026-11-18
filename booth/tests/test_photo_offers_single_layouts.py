"""Photo mode offers the designed layouts with one photo (owner, 18.11.2026).

"Foto heißt ein Bild, aber es muss die gestalteten Layouts als Einzelbild
anbieten." When the operator chose layouts (an event's exact list, own
templates or ticked built-ins), photo mode offers those with exactly one
photo slot and collage mode those with two or more (all of them if there is
none with two). The chosen layout makes the result and the print sheet; the
plain photo is kept and uploaded as well. The template's shape wins over the
format choice. No one-photo layout chosen: photo mode is exactly as before.
"""
import tempfile
import time
from pathlib import Path

import pytest
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture()
def booth(monkeypatch):
    d = Path(tempfile.mkdtemp(prefix="booth-photo-layouts-"))
    monkeypatch.setenv("BOOTH_DATA", str(d))
    monkeypatch.setenv("BOOTH_CAMERA", "mock")
    from boothcore.app import Booth
    b = Booth(base=d, start_threads=False)
    b.store.profile["modes"] = ["photo", "collage"]
    b.camera.start()
    for _ in range(50):
        if b.camera.state == "ready":
            break
        time.sleep(0.1)
    yield b
    b.camera.stop()
    b.mail.stop()


def _exact(b, ids):
    b.store.profile["collage_exact"] = True
    b.store.profile["collage_builtins"] = list(ids)


def _run(b, mode, template=None, fmt="4:5"):
    s = b.start_session(mode, fmt, template, None)
    for _ in range(s.shots_needed):
        b.take_shot(s)
    b.finalize(s, "original")
    return s


def test_photo_offers_the_one_photo_layouts_and_collage_the_others(booth):
    _exact(booth, ["schlicht-1-quer-weiss", "vollbild-quer", "grid4"])
    assert [t["id"] for t in booth.photo_templates()] == ["schlicht-1-quer-weiss", "vollbild-quer"]
    assert [t["id"] for t in booth.collage_choice()] == ["grid4"]


def test_without_a_multi_photo_layout_collage_keeps_all(booth):
    _exact(booth, ["schlicht-1-quer-weiss"])
    assert [t["id"] for t in booth.collage_choice()] == ["schlicht-1-quer-weiss"]


def test_the_chosen_layout_makes_result_and_print_and_the_plain_photo_is_kept(booth):
    _exact(booth, ["schlicht-1-quer-weiss", "vollbild-quer", "grid4"])
    s = _run(booth, "photo", "schlicht-1-quer-weiss", fmt="9:16")
    assert s.template and s.template["id"] == "schlicht-1-quer-weiss"
    assert s.fmt == "3:2"                                   # the layout's shape wins
    with Image.open(s.result) as r:
        assert r.size[0] > r.size[1]                        # the landscape sheet
    with Image.open(s.print_file) as p:
        assert p.size[0] > p.size[1]
    assert s.singles and Path(s.singles[0][0]).is_file()    # the photo without the layout
    with Image.open(s.singles[0][0]) as single:
        assert abs(single.size[0] / single.size[1] - 1.5) < 0.02


def test_an_unknown_or_multi_photo_id_takes_the_first_one_photo_layout(booth):
    _exact(booth, ["vollbild-quer", "grid4"])
    s = booth.start_session("photo", "4:5", "grid4", None)
    assert s.template["id"] == "vollbild-quer" and s.shots_needed == 1


def test_a_collage_without_a_choice_takes_the_first_multi_photo_layout(booth):
    _exact(booth, ["vollbild-quer", "grid4"])
    assert booth.start_session("collage", "4:5", None, None).template["id"] == "grid4"


def test_without_chosen_layouts_photo_is_as_before(booth):
    assert booth.photo_templates() == []                     # the default catalogue is no choice
    s = _run(booth, "photo", "vollbild-quer")
    assert s.template is None and s.fmt == "4:5" and not s.singles


def test_the_kiosk_lists_layouts_per_mode_and_previews_the_photo_slot():
    js = (ROOT / "boothcore" / "web" / "static" / "app.js").read_text(encoding="utf-8")
    assert "function templatesFor(mode)" in js
    assert "S.photo_templates" in js
    # the photo's live preview follows the chosen layout's slot, like a collage's
    assert 'flow.photoTemplate ? "collage" : flow.mode' in js


def test_the_kiosk_state_names_the_photo_layouts(booth):
    from fastapi.testclient import TestClient
    from boothcore.app import create_app
    _exact(booth, ["schlicht-1-quer-weiss", "grid4"])
    st = TestClient(create_app(booth)).get("/api/state").json()
    assert st["photo_templates"] == ["schlicht-1-quer-weiss"]


# ── Review 18.11.2026 ─────────────────────────────────────

def test_a_fallback_to_the_catalogue_offers_no_photo_layout(booth):
    """The event names its own templates, but they are not on the booth yet:
    templates() falls back to the shipped catalogue. Photo mode must not offer
    solo/vollbild from it by accident; it stays the plain photo."""
    booth.store.profile["collage_exact"] = True
    booth.store.profile["collage_templates"] = ["noch-nicht-da.json"]
    booth.store.profile["collage_builtins"] = []
    assert any(t["id"] == "solo" for t in booth.templates())          # the fallback is there
    assert booth.photo_templates() == []
    s = booth.start_session("photo", "4:5", "solo", None)
    assert s.template is None and s.fmt == "4:5"


def test_the_picture_between_shots_follows_the_layout_slot(booth):
    from fastapi.testclient import TestClient
    from boothcore.app import create_app
    _exact(booth, ["schlicht-1-quer-weiss", "grid4"])
    s = booth.start_session("photo", "9:16", "schlicht-1-quer-weiss", None)
    booth.take_shot(s)
    import io
    r = TestClient(create_app(booth)).get(f"/api/session/{s.uid}/shot/1.jpg")
    assert r.status_code == 200
    with Image.open(io.BytesIO(r.content)) as im:
        assert im.size[0] > im.size[1]                      # not cut to a portrait format


def test_names_of_a_photo_on_a_layout_are_stable_and_distinct(booth):
    import json
    from photobox_sync.targets import file_name
    _exact(booth, ["schlicht-1-quer-weiss", "grid4"])
    booth.store.save_local({"server": {"url": "https://galerie.example", "api_key": "k" * 8}})
    booth.store.profile["upload_originals"] = True
    booth.store.profile["event"] = {**booth.store.profile["event"], "name": "Hochzeit F & P",
                                    "file_naming": "event_datetime"}
    s = _run(booth, "photo", "schlicht-1-quer-weiss")
    items = [dict(e, meta=json.loads(e["meta"]) if isinstance(e["meta"], str) else e["meta"])
             for e in booth.outbox.due(limit=200) if e["session_uid"] == s.uid]
    assert items and all(i["meta"]["n_singles"] == 1 for i in items)
    one_target = [i for i in items if i["target"] == items[0]["target"]]
    first = one_target[0]
    one_target.append(dict(first, uid=f"{s.uid}.print", variant="print", path=str(s.print_file)))
    names = {(i["uid"], i["variant"]): file_name(i, flat=True) for i in one_target}
    assert len(set(names.values())) == len(names)
    stem = names[(s.uid, "original")].rsplit(".", 1)[0]
    assert stem.startswith("Hochzeit-F-P_") and stem.count("_") == 2       # <Event>_<date>_<time>
    assert names[(f"{s.uid}.f1", "original")] == f"{stem}_2.jpg"
    assert names[(f"{s.uid}.1", "original")].startswith(f"{stem}_3.")
    assert names[(f"{s.uid}.print", "print")] == f"{stem}-druck.jpg"
    assert names[(s.uid, "web")] == f"{stem}-web.jpg"


def test_a_photo_on_a_layout_makes_no_collage_gif(booth):
    """gif.from_collage is on by default (18.11.2026); it is the collage's rule only."""
    _exact(booth, ["schlicht-1-quer-weiss", "grid4"])
    booth.store.profile["gif"].update({"from_collage": True, "from_collage_min": 2})
    s = _run(booth, "photo", "schlicht-1-quer-weiss")
    assert s.anim is None
