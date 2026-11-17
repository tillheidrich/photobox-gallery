"""The gallery knows the built-in looks under the names the booth ships.

The booth renamed seven looks that carried film-stock trademarks. The gallery
lists the built-in looks in the booth form, and a profile saved before the
rename still names the old keys — the form has to show those selections
under the new names instead of silently unticking them.
"""
import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BOOTH_LUTS = ROOT / "booth" / "boothcore" / "media"


def _booth_legacy_keys() -> dict:
    tree = ast.parse((BOOTH_LUTS / "luts.py").read_text(encoding="utf-8"))
    for node in tree.body:
        target = getattr(node, "target", None) or (node.targets[0] if isinstance(node, ast.Assign) else None)
        if getattr(target, "id", "") == "LEGACY_KEYS":
            return ast.literal_eval(node.value)
    raise AssertionError("booth/boothcore/media/luts.py has no LEGACY_KEYS")


def test_the_gallery_lists_exactly_the_looks_the_booth_ships():
    from app import ecosystem
    shipped = sorted(p.stem for p in (BOOTH_LUTS / "luts_builtin").glob("*.cube"))
    assert sorted(ecosystem.BUILTIN_LUTS) == shipped


def test_gallery_and_booth_know_the_same_old_names():
    from app import ecosystem
    assert ecosystem.LEGACY_LUT_KEYS == _booth_legacy_keys()


def test_old_names_resolve_in_the_gallery_too():
    from app import ecosystem
    assert ecosystem.canonical_lut("kodak-gold") == "golden-warm"
    assert ecosystem.canonical_lut("lut-film-portra-400") == "lut-natural-portrait"
    assert ecosystem.canonical_lut("my-upload") == "my-upload"


def test_the_form_shows_an_old_selection_under_its_new_name(client):
    client.get("/healthz")
    from app import db, ecosystem
    ecosystem.init_schema()
    with client.session_transaction() as s:
        s["admin_logged_in"] = True
    dev = db.get_device_by_id(db.create_device(name="Altbox")["id"])
    profile = ecosystem.device_profile(dev)
    profile["luts"] = dict(profile["luts"], show=["kodak-gold"], baked="mono-trix-400")
    ecosystem.save_device_profile(dev["id"], profile, None)
    page = client.get(f"/admin/devices/{dev['id']}/booth").get_data(as_text=True)
    assert 'name="lut_show" value="golden-warm" checked' in page
    assert '<option value="classic-mono" selected>' in page
    assert "kodak" not in page.lower() and "tri-x" not in page.lower()


def test_every_builtin_look_has_a_line_in_third_party_md():
    text = (ROOT / "THIRD_PARTY.md").read_text(encoding="utf-8")
    for cube in (BOOTH_LUTS / "luts_builtin").glob("*.cube"):
        assert f"| `{cube.name}` |" in text, cube.name


def test_a_form_opened_before_the_rename_still_saves_its_choice(client):
    client.get("/healthz")
    from app import db, ecosystem
    ecosystem.init_schema()
    with client.session_transaction() as s:
        s["admin_logged_in"] = True
    dev_id = db.create_device(name="Offener Tab")["id"]
    client.get(f"/admin/devices/{dev_id}/booth")
    with client.session_transaction() as s:
        token = s.get("csrf_token") or ""
    client.post(f"/admin/devices/{dev_id}/booth", data={
        "csrf_token": token, "mode_photo": "on", "format_4:5": "on", "default_format": "4:5",
        "luts_enabled": "on", "lut_show": ["kodak-gold"], "lut_baked": "fuji-eterna",
    }, follow_redirects=True)
    luts = ecosystem.device_profile(db.get_device_by_id(dev_id))["luts"]
    assert luts["show"] == ["golden-warm"] and luts["baked"] == "soft-cinema"
