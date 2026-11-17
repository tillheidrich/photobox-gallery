"""An unknown paper stock is not an empty one, and one cause is one message.

Found in the browser check on 12.11.2026: a booth whose printer has no
cassette size (a folder printer, or a real one nobody entered) reported
"Das Papier ist rechnerisch alle" as critical on every heartbeat. The
gallery's dashboard counted about 99 errors after an hour — every heartbeat
logged the same open message again at ERROR — and the kiosk's status line
showed it in German in the English interface.
"""
import logging
import re
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture()
def booth(monkeypatch):
    d = Path(tempfile.mkdtemp(prefix="booth-paper-"))
    monkeypatch.setenv("BOOTH_DATA", str(d))
    monkeypatch.setenv("BOOTH_CAMERA", "mock")
    from boothcore.app import Booth
    b = Booth(base=d, start_threads=False)
    b.store.profile["print"].update({"enabled": True, "sheets_per_cassette": 0})
    yield b
    b.mail.stop()


def test_no_known_stock_raises_no_paper_message(booth):
    assert all(s.get("quelle") == "unbekannt" for s in booth.sheets())
    st = booth._status_payload()
    keys = {m["key"] for m in booth.meldungen.offen()}
    assert "blatt" not in keys
    assert not any("Papier" in n["titel"] for n in st["notices"])


def test_a_counted_stock_that_runs_out_still_raises_it(booth):
    st = booth.printer.stations[0]
    st.sheets_total, st.printed = 100, 100
    booth._status_payload()
    assert any(m["key"] == "blatt" and m["stufe"] == "kritisch" for m in booth.meldungen.offen())


def test_the_self_check_says_calmly_that_it_cannot_count(booth):
    from boothcore.doctor import Doctor, SKIP
    check = Doctor(booth).check_media()
    assert check.state == SKIP
    assert "nicht eingetragen" in check.message and "mitzählen" in check.message


def test_an_open_message_is_logged_once_not_at_every_heartbeat(booth, caplog):
    from boothcore import meldungen as mm
    caplog.set_level(logging.INFO, logger="boothcore.meldungen")
    for _ in range(5):
        booth.meldungen.melden("drucker", mm.KRITISCH, "Kein Drucker ist bereit", "Papierfach offen")
    fehler = [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert len(fehler) == 1
    assert len(booth.meldungen.verlauf()) == 1 and len(booth.meldungen.offen()) == 1
    # After the all-clear the same cause is news again.
    booth.meldungen.entwarnung("drucker")
    booth.meldungen.melden("drucker", mm.KRITISCH, "Kein Drucker ist bereit", "Papierfach offen")
    assert len([r for r in caplog.records if r.levelno >= logging.ERROR]) == 2


def test_the_kiosk_gets_a_text_key_for_its_language(booth):
    booth.camera.state = "error"
    booth._status_payload()
    m = booth.meldungen.fuer_die_box()
    assert m["code"] == "op.camera_down" and "state" in m["werte"]


def _i18n_keys(teil):
    js = (ROOT / "booth" / "boothcore" / "web" / "static" / "i18n.js").read_text(encoding="utf-8")
    block = js[js.index("de: {"):js.index("en: {")] if teil == "de" else js[js.index("en: {"):]
    return set(re.findall(r'"([a-z0-9._]+)":', block))


def test_every_operator_message_exists_in_both_languages():
    from boothcore.app import OPERATOR_CODES
    for code in OPERATOR_CODES:
        for teil in ("de", "en"):
            assert {f"{code}.title", f"{code}.text"} <= _i18n_keys(teil), (code, teil)


def test_the_status_line_uses_the_key():
    js = (ROOT / "booth" / "boothcore" / "web" / "static" / "app.js").read_text(encoding="utf-8")
    assert "meldungText(S.meldung)" in js
