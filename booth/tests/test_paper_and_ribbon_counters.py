"""A SELPHY needs the operator at two different moments: when the paper tray
is empty and when the ink ribbon is used up. One count per "cassette" (108,
the size of the retail pack) warned at neither.

The two capacities (tray 18 sheets, ribbon 36 prints) are model defaults
from memory, to be confirmed at the device; the operator can change both.
Everything here is counted, not reported by the printer — and printing is
never blocked by a count alone.
"""
import json
import re
import tempfile
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from boothcore.printing import models
from boothcore.printing.backends import FolderPrinter
from boothcore.printing.service import PrintJob, PrintService, printer_configs
from _pages import page  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
SELPHY = {"backend": "folder", "model": "selphy-cp", "medium": "postcard"}


def _warten(dienst, n=None, timeout=5.0):
    ende = time.time() + timeout
    while time.time() < ende:
        if not dienst.busy and not dienst.pending:
            return
        time.sleep(0.02)


def _dienst(tmp: Path, **eintrag):
    img = tmp / "blatt.jpg"
    Image.new("RGB", (1200, 1800), (120, 90, 60)).save(img)
    cfgs = printer_configs({"printers": [{"backend": "folder", **eintrag}]})
    return PrintService(FolderPrinter(tmp / "aus"), tmp / "zaehler.txt", options=cfgs), img


@pytest.fixture()
def tmp():
    return Path(tempfile.mkdtemp(prefix="fach-band-"))


# ── Model defaults and configuration ──────────────────────

def test_the_selphy_knows_tray_and_ribbon_as_defaults():
    assert models.verbrauch("selphy-cp") == {"tray": 18, "ribbon": 36}
    cfg = printer_configs({"printers": [dict(SELPHY)]})[0]
    assert cfg["tray_total"] == 18 and cfg["ribbon_total"] == 36


def test_the_operator_can_change_both_capacities():
    cfg = printer_configs({"printers": [{**SELPHY, "tray_total": "17", "ribbon_total": 54}]})[0]
    assert cfg["tray_total"] == 17 and cfg["ribbon_total"] == 54


def test_other_models_define_no_tray_and_ribbon():
    assert models.verbrauch("dnp-ds620") == {}
    cfg = printer_configs({"printers": [{"backend": "folder", "model": "dnp-ds620", "medium": "10x15"}]})[0]
    assert not cfg.get("tray_total") and not cfg.get("ribbon_total")
    assert cfg["sheets_total"] == 400


def test_both_capacities_live_in_the_local_defaults():
    from boothcore.config import DEFAULT_LOCAL
    assert DEFAULT_LOCAL["printer"]["tray_total"] == 0 and DEFAULT_LOCAL["printer"]["ribbon_total"] == 0


# ── Counting ──────────────────────────────────────────────

def test_each_sheet_counts_on_tray_and_ribbon_copies_included(tmp):
    dienst, img = _dienst(tmp, **{k: v for k, v in SELPHY.items() if k != "backend"})
    dienst.submit(PrintJob(img, 3, "uid"))
    _warten(dienst)
    s = dienst.sheets()[0]
    assert s["quelle"] == "gezählt"
    assert s["fach"] == {"rest": 15, "gesamt": 18}
    assert s["farbband"] == {"rest": 33, "gesamt": 36}


def test_paper_refilled_resets_only_the_tray(tmp):
    dienst, img = _dienst(tmp, model="selphy-cp", medium="postcard")
    dienst.submit(PrintJob(img, 4, "uid"))
    _warten(dienst)
    dienst.paper_changed()
    s = dienst.sheets()[0]
    assert s["fach"]["rest"] == 18 and s["farbband"]["rest"] == 32


def test_ribbon_changed_resets_only_the_ribbon(tmp):
    dienst, img = _dienst(tmp, model="selphy-cp", medium="postcard")
    dienst.submit(PrintJob(img, 4, "uid"))
    _warten(dienst)
    assert dienst.ribbon_changed() == 1
    s = dienst.sheets()[0]
    assert s["fach"]["rest"] == 14 and s["farbband"]["rest"] == 36


def test_both_counts_survive_a_restart(tmp):
    dienst, img = _dienst(tmp, model="selphy-cp", medium="postcard")
    dienst.submit(PrintJob(img, 5, "uid"))
    _warten(dienst)
    dienst.paper_changed()
    dienst.submit(PrintJob(img, 2, "uid"))
    _warten(dienst)
    zweiter = PrintService(FolderPrinter(tmp / "aus"), tmp / "zaehler.txt",
                           options=printer_configs({"printers": [dict(SELPHY)]}))
    s = zweiter.sheets()[0]
    assert s["fach"]["rest"] == 16 and s["farbband"]["rest"] == 29


def test_an_empty_count_never_blocks_printing(tmp):
    dienst, img = _dienst(tmp, model="selphy-cp", medium="postcard")
    st = dienst.stations[0]
    st.fach_gedruckt = st.band_gedruckt = 40
    dienst.submit(PrintJob(img, 1, "uid"))
    _warten(dienst)
    assert len(list((tmp / "aus").iterdir())) == 1, "the counter is a guess; the printer decides"
    # Since 15.11.2026 a sheet through an "empty" count proves a refill: a new
    # tray starts (test_prints_resume_without_the_pin.py).
    assert dienst.sheets()[0]["fach"]["rest"] == 17


def test_other_models_keep_the_single_counter(tmp):
    dienst, img = _dienst(tmp, model="dnp-ds620", medium="10x15")
    dienst.submit(PrintJob(img, 2, "uid"))
    _warten(dienst)
    s = dienst.sheets()[0]
    assert s["quelle"] == "gezählt" and s["rest"] == 398 and s["gesamt"] == 400
    assert "fach" not in s and "farbband" not in s


def test_an_old_sheet_file_still_loads(tmp):
    (tmp / "zaehler.sheets.json").write_text(json.dumps({"0": 7}), encoding="utf-8")
    dienst, _img = _dienst(tmp, model="selphy-cp", medium="postcard")
    assert dienst.stations[0].printed == 7
    assert dienst.sheets()[0]["fach"]["rest"] == 18


# ── The booth: notices, reprint, admin ────────────────────

@pytest.fixture()
def booth(monkeypatch):
    d = Path(tempfile.mkdtemp(prefix="booth-fach-band-"))
    (d / "local.json").write_text(json.dumps({"printers": [dict(SELPHY)]}), encoding="utf-8")
    monkeypatch.setenv("BOOTH_DATA", str(d))
    monkeypatch.setenv("BOOTH_CAMERA", "mock")
    from boothcore import app as app_mod
    monkeypatch.setattr(app_mod, "_WRONG_PIN_PAUSE", 0.0)
    b = app_mod.Booth(base=d, start_threads=False)
    assert [s.backend.name for s in b.printer.stations] == ["folder"], "never a real printer in this test"
    b.store.profile["print"].update({"enabled": True, "sheets_per_cassette": 0})
    yield b
    b.mail.stop()


def _codes(b):
    b._status_payload()
    return {m["key"]: (m["code"], m["stufe"]) for m in b.meldungen.offen()}


def _setze(b, fach=0, band=0):
    st = b.printer.stations[0]
    st.fach_gedruckt, st.band_gedruckt = fach, band


def test_a_full_selphy_raises_nothing(booth):
    _setze(booth, 0, 0)
    codes = _codes(booth)
    assert not {"blatt", "fach", "farbband"} & codes.keys()


def test_the_tray_warns_shortly_before_and_when_empty(booth):
    _setze(booth, 14, 14)
    assert "fach" not in _codes(booth)
    _setze(booth, 15, 15)
    assert _codes(booth)["fach"] == ("op.tray_low", "warnung")
    _setze(booth, 18, 18)
    assert _codes(booth)["fach"] == ("op.tray_out", "kritisch")
    booth.paper_changed()
    assert "fach" not in _codes(booth)


def test_the_ribbon_warns_on_its_own(booth):
    _setze(booth, 0, 31)
    codes = _codes(booth)
    assert codes["farbband"] == ("op.ribbon_low", "warnung") and "fach" not in codes
    _setze(booth, 0, 36)
    assert _codes(booth)["farbband"] == ("op.ribbon_out", "kritisch")
    booth.printer.ribbon_changed()
    assert "farbband" not in _codes(booth)


def test_the_old_cassette_message_stays_for_other_models(monkeypatch):
    d = Path(tempfile.mkdtemp(prefix="booth-dnp-"))
    (d / "local.json").write_text(json.dumps({"printers": [
        {"backend": "folder", "model": "dnp-ds620", "medium": "10x15"}]}), encoding="utf-8")
    monkeypatch.setenv("BOOTH_DATA", str(d))
    monkeypatch.setenv("BOOTH_CAMERA", "mock")
    from boothcore.app import Booth
    b = Booth(base=d, start_threads=False)
    try:
        b.printer.stations[0].printed = 390
        codes = _codes(b)
        assert codes["blatt"] == ("op.paper_low", "warnung")
        assert not {"fach", "farbband"} & codes.keys()
    finally:
        b.mail.stop()


def test_an_operator_reprint_counts_on_both(booth, monkeypatch):
    f = booth.data / "druck.jpg"
    Image.new("RGB", (120, 180), "#777").save(f, "JPEG")
    monkeypatch.setattr(booth.library, "get", lambda u: {"uid": u, "print_file": str(f)})
    monkeypatch.setattr(booth.library, "add_prints", lambda u, n: None)
    monkeypatch.setattr(booth, "sheet_for", lambda p, u="": p)
    booth.reprint("abc123", copies=2)
    _warten(booth.printer)
    s = booth.sheets()[0]
    assert s["fach"]["rest"] == 16 and s["farbband"]["rest"] == 34


def test_admin_buttons_reset_tray_and_ribbon_separately(booth):
    from boothcore import app as app_mod
    booth.store.save_local({"admin_pin": "468213"})
    c = TestClient(app_mod.create_app(booth))
    c.headers.update({"X-Admin-Token": c.post("/api/admin/login", json={"pin": "468213"}).json()["token"]})
    _setze(booth, 10, 20)
    assert c.post("/api/admin/action/ribbon_changed").json()["ok"] is True
    s = booth.sheets()[0]
    assert s["fach"]["rest"] == 8 and s["farbband"]["rest"] == 36
    _setze(booth, 10, 20)
    assert c.post("/api/admin/action/paper_changed").json()["ok"] is True
    s = booth.sheets()[0]
    assert s["fach"]["rest"] == 18 and s["farbband"]["rest"] == 16
    html = page(ROOT / "booth" / "boothcore" / "web" / "admin.html")
    assert re.search(r'data-act="paper_changed"[^>]*>Papier nachgelegt<', html)
    assert re.search(r'data-act="ribbon_changed"[^>]*>Farbband gewechselt<', html)
    assert 'data-k="tray_total"' in html and 'data-k="ribbon_total"' in html


# ── Texts ─────────────────────────────────────────────────

NEU = ("op.tray_low", "op.tray_out", "op.ribbon_low", "op.ribbon_out")


def test_the_new_notices_exist_in_both_languages_and_both_files():
    from boothcore.app import OPERATOR_CODES
    import importlib.util
    spec = importlib.util.spec_from_file_location("booth_texts_catalog", ROOT / "app" / "booth_texts.py")
    booth_texts = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(booth_texts)          # the gallery's catalog, without starting the gallery
    js = (ROOT / "booth" / "boothcore" / "web" / "static" / "i18n.js").read_text(encoding="utf-8")
    de_js, en_js = js[js.index("de: {"):js.index("en: {")], js[js.index("en: {"):]
    de_py, en_py = booth_texts.DEFAULTS, booth_texts.SHIPPED_EN
    for code in NEU:
        assert code in OPERATOR_CODES
        for teil in ("title", "text"):
            key = f"{code}.{teil}"
            assert f'"{key}":' in de_js and f'"{key}":' in en_js, key
            assert key in de_py and key in en_py, key
    # Honest about where the number comes from, in both languages.
    for code in NEU:
        assert "nicht gemeldet" in de_py[f"{code}.text"]
        assert "not reported" in en_py[f"{code}.text"]
