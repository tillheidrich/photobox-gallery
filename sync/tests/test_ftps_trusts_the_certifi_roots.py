"""FTPS trusts the system's roots plus the certifi bundle, never less (18.11.2026).

Seen on a Windows booth (frozen .exe, Python 3.12): the second target, FTPS
to a commercial host whose chain ends in a root Windows had never fetched,
failed with "unable to get local issuer certificate", while the gallery
upload on the same booth worked. ``ssl.create_default_context()`` on Windows
reads only the roots already in the Windows store; Windows fetches newer
roots lazily and only for its own TLS stack. httpx (the gallery upload) uses
the certifi bundle. Now every FTPS connection gets the same roots as the
gallery upload, on top of the system's, with name check and CERT_REQUIRED.

The handshake tests run a local FTPS server whose certificate is signed by a
CA made here. "Windows without that root" is an empty system store; "certifi
has it" is certifi.where() pointing at that CA. No connection leaves 127.0.0.1.
"""
from __future__ import annotations

import ssl
from pathlib import Path

import certifi
import pytest

from photobox_sync import targets, trust

from test_sftp_host_key_is_pinned import PASSWORT, FtpsServer, _certs

SYNC = Path(__file__).resolve().parents[1]
BOOTH = SYNC.parent / "booth"


def _empty_system_store(monkeypatch):
    """A system store that knows no root at all, like a Windows that never fetched it."""
    monkeypatch.setattr(trust, "_system_context", lambda: ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT))


def _subjects(ctx: ssl.SSLContext) -> set:
    return {dict(x[0] for x in c["subject"]).get("commonName") for c in ctx.get_ca_certs()}


def test_the_context_checks_certificate_and_host_name():
    ctx = trust.verify_context()
    assert ctx.verify_mode == ssl.CERT_REQUIRED
    assert ctx.check_hostname is True


def test_the_certifi_roots_are_trusted_even_when_the_system_store_is_empty(monkeypatch):
    _empty_system_store(monkeypatch)
    ctx = trust.verify_context()
    bundle = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    bundle.load_verify_locations(cafile=certifi.where())
    assert ctx.cert_store_stats()["x509_ca"] >= bundle.cert_store_stats()["x509_ca"] > 50
    # The root of the chain that failed on the booth.
    assert "Sectigo Public Server Authentication Root E46" in _subjects(ctx)
    assert ctx.verify_mode == ssl.CERT_REQUIRED and ctx.check_hostname is True


def test_the_system_roots_stay_trusted(tmp_path, monkeypatch):
    ca, _richtig, _falsch = _certs(tmp_path)
    monkeypatch.setattr(trust, "_system_context", lambda: ssl.create_default_context(cafile=str(ca)))
    assert "Test CA" in _subjects(trust.verify_context())


def test_without_certifi_the_system_store_is_used_alone(tmp_path, monkeypatch):
    ca, _richtig, _falsch = _certs(tmp_path)
    monkeypatch.setattr(trust, "_system_context", lambda: ssl.create_default_context(cafile=str(ca)))
    monkeypatch.setattr(trust, "_certifi_bundle", lambda: None)
    ctx = trust.verify_context()
    assert _subjects(ctx) == {"Test CA"}
    assert ctx.verify_mode == ssl.CERT_REQUIRED and ctx.check_hostname is True


def test_ftps_logs_in_when_only_certifi_knows_the_root(tmp_path, monkeypatch):
    ca, richtig, _falsch = _certs(tmp_path)
    _empty_system_store(monkeypatch)
    monkeypatch.setattr(certifi, "where", lambda: str(ca))
    srv = FtpsServer(richtig)
    t = targets.FtpsTarget({"type": "ftps", "host": "127.0.0.1", "port": srv.port, "user": "box",
                            "password": PASSWORT, "timeout": 5})
    ftp = t._connect()
    ftp.quit()
    assert srv.passwords == [PASSWORT]
    srv.close()


def test_ftps_still_refuses_a_root_nobody_trusts_before_the_password(tmp_path, monkeypatch):
    _ca, richtig, _falsch = _certs(tmp_path)
    _empty_system_store(monkeypatch)                 # certifi stays the real bundle: no "Test CA" in it
    srv = FtpsServer(richtig)
    t = targets.FtpsTarget({"type": "ftps", "host": "127.0.0.1", "port": srv.port, "user": "box",
                            "password": PASSWORT, "timeout": 5})
    with pytest.raises(targets.TemporaryError) as e:
        t.check()
    assert "certificate verify failed" in str(e.value).lower(), str(e.value)
    assert srv.handshake_failed.wait(5)
    assert srv.passwords == []
    srv.close()


@pytest.mark.parametrize("port", [21, 2121, 990])
def test_every_ftps_connection_takes_its_context_from_the_helper(port, monkeypatch):
    class Asked(Exception):
        pass

    def ask():
        raise Asked()

    monkeypatch.setattr(trust, "verify_context", ask)
    t = targets.FtpsTarget({"type": "ftps", "host": "ftp.example.org", "port": port, "user": "box",
                            "password": PASSWORT, "timeout": 1})
    with pytest.raises(Asked):
        t._connect()


def _code_files():
    for root in (SYNC / "photobox_sync", BOOTH / "boothcore"):
        for path in sorted(root.rglob("*.py")):
            yield path


def test_no_outgoing_tls_builds_its_own_context_or_switches_the_check_off():
    files = list(_code_files())
    assert any(p.name == "targets.py" for p in files) and any(p.name == "mail.py" for p in files)
    for path in files:
        text = path.read_text(encoding="utf-8")
        if path != SYNC / "photobox_sync" / "trust.py":
            assert "create_default_context(" not in text, f"{path}: use photobox_sync.trust.verify_context()"
            assert "SSLContext(" not in text, f"{path}: use photobox_sync.trust.verify_context()"
        for word in ("_create_unverified_context", "CERT_NONE", "check_hostname = False", "check_hostname=False"):
            assert word not in text, f"{path}: {word}"


@pytest.mark.parametrize("inhalt", [
    b"-----BEGIN CERTIFICATE-----\nnot base64 at all\n-----END CERTIFICATE-----\n",
    b"no certificate in here\n",
    b"",
])
def test_a_corrupt_certifi_bundle_falls_back_to_the_system_store(tmp_path, monkeypatch, inhalt):
    ca, _richtig, _falsch = _certs(tmp_path)
    kaputt = tmp_path / "cacert.pem"
    kaputt.write_bytes(inhalt)
    monkeypatch.setattr(trust, "_system_context", lambda: ssl.create_default_context(cafile=str(ca)))
    monkeypatch.setattr(certifi, "where", lambda: str(kaputt))
    ctx = trust.verify_context()
    assert "Test CA" in _subjects(ctx)
    assert ctx.verify_mode == ssl.CERT_REQUIRED and ctx.check_hostname is True
