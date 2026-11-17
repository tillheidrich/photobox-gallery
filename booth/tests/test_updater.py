"""Die Box holt sich ihre neue Fassung — geprüft, und nie von selbst.

Der wichtigste Satz über diesen Teil: Wenn etwas schiefgeht, läuft die alte
Fassung weiter. Eine Prüfsumme, die nicht passt, ist kein „na gut" — die Datei
fliegt weg.
"""
from __future__ import annotations

import hashlib
import json
import tempfile
from pathlib import Path

import pytest

from boothcore.updater import Updater


@pytest.fixture()
def updater(tmp_path):
    return Updater(tmp_path, "0.2.0")


def _angebot(daten: bytes, url: str = "https://galerie.test/api/v2/booth/software/booth-0.3.0.exe"):
    return {"version": "0.3.0", "url": url, "sha256": hashlib.sha256(daten).hexdigest(),
            "size": len(daten), "notes": "Neue Vorlagen", "file": "booth-0.3.0.exe"}


class _Antwort:
    def __init__(self, daten): self.daten = daten
    def raise_for_status(self): pass
    def iter_bytes(self, n): yield self.daten
    def __enter__(self): return self
    def __exit__(self, *a): return False


def _httpx(monkeypatch, daten):
    import httpx
    monkeypatch.setattr(httpx, "stream", lambda *a, **k: _Antwort(daten))


def test_nothing_is_offered_by_default(updater):
    assert updater.stand()["state"] == "nichts"


def test_an_offer_is_only_noted_not_fetched(updater):
    updater.merke(_angebot(b"neu"))
    stand = updater.stand()
    assert stand["state"] == "angeboten" and stand["version"] == "0.3.0"
    assert not stand["ready"], "heruntergeladen wird erst auf Zuruf"


def test_fetching_checks_the_checksum(updater, monkeypatch):
    daten = b"eine neue fassung"
    updater.merke(_angebot(daten))
    _httpx(monkeypatch, daten)
    assert updater.hole()["ok"] is True
    assert updater.stand()["state"] == "bereit"
    assert (updater.dir / "booth-0.3.0.exe").read_bytes() == daten


def test_a_wrong_checksum_is_thrown_away(updater, monkeypatch):
    updater.merke(_angebot(b"das erwartete"))
    _httpx(monkeypatch, b"etwas ganz anderes")
    antwort = updater.hole()
    assert antwort["ok"] is False and "Prüfsumme" in antwort["error"]
    assert not (updater.dir / "booth-0.3.0.exe").exists()
    assert updater.stand()["state"] == "angeboten", "nichts Fertiges — die alte Fassung bleibt"


def test_a_failed_download_costs_nothing(updater, monkeypatch):
    import httpx
    updater.merke(_angebot(b"x"))

    def kaputt(*a, **k):
        raise httpx.ConnectError("kein Netz")
    monkeypatch.setattr(httpx, "stream", kaputt)
    antwort = updater.hole()
    assert antwort["ok"] is False
    assert updater.stand()["state"] == "angeboten"


def test_installing_needs_something_ready(updater):
    assert updater.einspielen()["ok"] is False


def test_from_source_it_says_so_instead_of_swapping(updater, monkeypatch):
    daten = b"neu"
    updater.merke(_angebot(daten))
    _httpx(monkeypatch, daten)
    updater.hole()
    antwort = updater.einspielen()
    assert antwort["ok"] is False and "Quellcode" in antwort["error"]


def test_the_swap_script_keeps_the_old_file(updater, tmp_path, monkeypatch):
    """Das Skript legt die alte Datei beiseite und holt sie zurück, wenn der Tausch scheitert."""
    daten = b"neu"
    updater.merke(_angebot(daten))
    _httpx(monkeypatch, daten)
    updater.hole()
    programm = tmp_path / "PhotoboxBooth.exe"
    programm.write_bytes(b"alt")
    monkeypatch.setattr(updater, "ziel_programm", lambda: programm)
    monkeypatch.setattr("subprocess.Popen", lambda *a, **k: None)
    # einspielen() ends the program 1.5 s later (os._exit(0)). Unpatched, that
    # timer ended the whole test run with exit code 0 and no summary: the
    # tests after this one and every failure went unreported.
    armed = []
    monkeypatch.setattr("boothcore.updater.threading.Timer",
                        lambda delay, fn: type("T", (), {"start": lambda self: armed.append(delay)})())
    antwort = updater.einspielen()
    assert antwort["ok"] is True and armed == [1.5]
    text = Path(antwort["script"]).read_text(encoding="utf-8")
    assert ".alt" in text and str(programm) in text


def test_an_offer_already_ready_is_not_reset(updater, monkeypatch):
    daten = b"neu"
    updater.merke(_angebot(daten))
    _httpx(monkeypatch, daten)
    updater.hole()
    updater.merke(_angebot(daten))               # derselbe Stand noch einmal
    assert updater.stand()["state"] == "bereit"


# ── Security review G3, finding S-4 (15.11.2026) ─────────────
# The update path runs code from the gallery on the booth. Until signed
# updates exist, at least: no hash means no update, never a downgrade, the
# device key only goes to the paired gallery, and no redirect to elsewhere.

def test_an_offer_without_a_checksum_is_refused(updater, monkeypatch):
    for sha in ("", None, "abc", "z" * 64):
        updater.verwerfen()
        offer = dict(_angebot(b"neu"), sha256=sha)
        updater.merke(offer)
        assert updater.stand()["state"] == "nichts", sha
    _httpx(monkeypatch, b"neu")
    assert updater.hole()["ok"] is False


def test_an_older_or_equal_version_is_never_offered(updater):
    for version in ("0.2.0", "0.1.9", "0.2", "abc"):
        updater.verwerfen()
        updater.merke(dict(_angebot(b"alt"), version=version))
        assert updater.stand()["state"] == "nichts", version


def _record(monkeypatch, daten=b"neu"):
    import httpx
    seen = {}

    def stream(method, url, **kw):
        seen.update(url=url, headers=kw.get("headers") or {}, follow=kw.get("follow_redirects"))
        return _Antwort(daten)
    monkeypatch.setattr(httpx, "stream", stream)
    return seen


def test_the_key_goes_only_to_the_paired_gallery(updater, monkeypatch):
    seen = _record(monkeypatch)
    updater.merke(_angebot(b"neu", url="https://evil.example/booth-0.3.0.exe"))
    updater.hole("geheimer-schluessel", gallery_url="https://galerie.test")
    assert "X-API-Key" not in seen.get("headers", {}), "the key never goes to a foreign host"


def test_the_key_goes_to_the_paired_gallery(updater, monkeypatch):
    seen = _record(monkeypatch)
    updater.merke(_angebot(b"neu"))
    assert updater.hole("geheimer-schluessel", gallery_url="https://galerie.test/")["ok"] is True
    assert seen["headers"].get("X-API-Key") == "geheimer-schluessel"


def test_redirects_are_not_followed(updater, monkeypatch):
    seen = _record(monkeypatch)
    updater.merke(_angebot(b"neu"))
    updater.hole("k", gallery_url="https://galerie.test")
    assert seen["follow"] is False


def test_the_security_page_no_longer_claims_what_is_not_true():
    text = (Path(__file__).resolve().parents[2] / "SECURITY.md").read_text(encoding="utf-8")
    assert "can never make a booth run a shell command" not in text
    assert "signed" in text.lower()
