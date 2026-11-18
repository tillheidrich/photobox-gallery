"""FTPS data connections with servers that end them without TLS close_notify (18.11.2026).

Seen on a Windows booth against a commercial FTPS host (a Go server): login,
AUTH TLS, TLS 1.3, PROT P all fine, then the check failed with
"[SSL: UNEXPECTED_EOF_WHILE_READING] EOF occurred in violation of protocol".
ftplib ends every data transfer with ``conn.unwrap()``: it sends close_notify
and waits for the server's. That server closes the data connection without
answering, and OpenSSL 3 reports that as SSLEOFError although every byte had
gone through and the server answers 226 on the control connection.

Now the target's FTPS client
* tolerates exactly that at the end of a data connection (the transfer itself
  is over; the control reply and the size check still decide),
* reuses the control connection's TLS session for every data connection, as
  servers like vsftpd with ``require_ssl_reuse`` demand.

Local pyftpdlib servers only (127.0.0.1, a CA made in the test). A server
variant closes data connections bare, one refuses data connections that do
not resume the control session, one cuts an upload in the middle.
"""
from __future__ import annotations

import ftplib
import ssl
import threading
from pathlib import Path

import pytest

pytest.importorskip("OpenSSL")

from pyftpdlib.authorizers import DummyAuthorizer          # noqa: E402
from pyftpdlib.handlers import TLS_DTPHandler, TLS_FTPHandler  # noqa: E402
from pyftpdlib.servers import FTPServer                     # noqa: E402

from photobox_sync import targets, trust                    # noqa: E402

from test_second_target_folder_and_real_servers import _files  # noqa: E402
from test_sftp_host_key_is_pinned import PASSWORT, _certs, _trust  # noqa: E402


def _quiet(*_a, **_k):
    pass


class BareCloseDTP(TLS_DTPHandler):
    """Ends every data connection without close_notify, like the server on the booth."""

    def close(self):
        self._ssl_established = False          # skips SSLConnectionMixin's TLS shutdown
        super().close()


class ReuseRequiredDTP(TLS_DTPHandler):
    """Refuses a data connection that does not resume the control connection's session."""

    seen: list = []

    def handle_ssl_established(self):
        from OpenSSL.SSL import _lib
        reused = bool(_lib.SSL_session_reused(self.socket._ssl))
        type(self).seen.append(reused)
        if not reused:
            self.cmd_channel.respond("522 SSL connection failed: session reuse required")
            self.close()
            return
        super().handle_ssl_established()


class CutDTP(BareCloseDTP):
    """Drops an upload after 64 KiB, as a server that runs out of space or patience."""

    # handle_read_event, not handle_read: DTPHandler binds the event to its own
    # handle_read, so an override of handle_read is never called.
    def handle_read_event(self):
        super().handle_read_event()
        if self.receive and not self._closed and self.tot_bytes_received > 64 * 1024:
            self._resp = ("426 Connection closed; transfer aborted.", _quiet)
            self.close()


def _server(root: Path, cert: Path, dtp=TLS_DTPHandler, implicit: bool = False):
    root.mkdir(parents=True, exist_ok=True)
    auth = DummyAuthorizer()
    auth.add_user("box", PASSWORT, str(root), perm="elradfmwMT")
    attrs = {"certfile": str(cert), "keyfile": None, "ssl_context": None, "tls_control_required": True,
             "tls_data_required": True, "authorizer": auth, "passive_ports": None, "dtp_handler": dtp}
    if implicit:
        def handle(self):
            # Implicit FTPS: TLS from the first byte, the banner already inside it.
            self.secure_connection(self.ssl_context)
            TLS_FTPHandler.handle(self)
        attrs["handle"] = handle
    handler = type("H", (TLS_FTPHandler,), attrs)
    # Without a session id context OpenSSL resumes nothing across connections.
    handler.get_ssl_context().set_session_id(b"photobox-test")
    srv = FTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=srv.serve_forever, kwargs={"timeout": 0.05}, daemon=True).start()
    return srv


@pytest.fixture()
def certs(tmp_path, monkeypatch):
    ca, right, _wrong = _certs(tmp_path)
    _trust(monkeypatch, ca)
    return right


def _cfg(srv, **over):
    return {"type": "ftps", "name": "zweites-ziel", "host": "127.0.0.1", "port": srv.address[1], "user": "box",
            "password": PASSWORT, "flat": True, "path": "", "timeout": 5, **over}


def _limit_tls(monkeypatch, version: str):
    real = trust.verify_context

    def limited():
        ctx = real()
        ctx.minimum_version = ctx.maximum_version = getattr(ssl.TLSVersion, version)
        return ctx
    monkeypatch.setattr(trust, "verify_context", limited)


# ── (a) the server closes data connections without close_notify ──────────

def test_upload_listing_and_check_work_when_the_server_closes_data_bare(certs, tmp_path):
    srv = _server(tmp_path / "root", certs, BareCloseDTP)
    try:
        t = targets.FtpsTarget(_cfg(srv, path="live"))
        jpg, mp4 = _files(tmp_path)
        assert t.upload(jpg)["bytes"] == Path(jpg["path"]).stat().st_size
        t.upload(mp4)
        landed = tmp_path / "root" / "live"
        assert (landed / "r1.jpg").read_bytes() == Path(jpg["path"]).read_bytes()
        assert (landed / "r1.mp4").read_bytes() == Path(mp4["path"]).read_bytes()
        assert sorted(p.name for p in landed.iterdir()) == ["r1.jpg", "r1.mp4"]
        assert t.listdir("live")["files"] == ["r1.jpg", "r1.mp4"]
        assert "beschreibbar" in t.check()
        assert sorted(p.name for p in landed.iterdir()) == ["r1.jpg", "r1.mp4"], "the probe file is gone again"
    finally:
        srv.close_all()


def test_plain_ftplib_fails_against_that_server_which_is_the_booth_error(certs, tmp_path):
    """The reproduction: the standard library's FTP_TLS raises at unwrap()."""
    srv = _server(tmp_path / "root", certs, BareCloseDTP)
    try:
        ftp = ftplib.FTP_TLS(context=ssl.create_default_context(), timeout=5)
        ftp.connect("127.0.0.1", srv.address[1])
        ftp.login("box", PASSWORT)
        ftp.prot_p()
        with pytest.raises(ssl.SSLError) as e:
            ftp.storbinary("STOR x.bin", __import__("io").BytesIO(b"x" * 1000))
        assert "EOF" in str(e.value), str(e.value)
        ftp.close()
    finally:
        srv.close_all()


def test_an_end_of_data_error_that_is_not_an_eof_is_still_an_error(monkeypatch):
    def broken(self):
        raise ssl.SSLError(1, "[SSL: BAD_RECORD_MAC] bad record mac")
    monkeypatch.setattr(ssl.SSLSocket, "unwrap", broken)
    sock = targets._DataChannelSocket.__new__(targets._DataChannelSocket)
    with pytest.raises(ssl.SSLError):
        sock.unwrap()


# ── (b) the server requires TLS session reuse on the data connection ─────

@pytest.mark.parametrize("version", ["TLSv1_2", "TLSv1_3"])
def test_data_connections_resume_the_control_session(certs, tmp_path, monkeypatch, version):
    _limit_tls(monkeypatch, version)
    ReuseRequiredDTP.seen = []
    srv = _server(tmp_path / "root", certs, ReuseRequiredDTP)
    try:
        t = targets.FtpsTarget(_cfg(srv, path="live"))
        jpg, mp4 = _files(tmp_path)
        t.upload(jpg)
        t.upload(mp4)
        assert t.listdir("live")["files"] == ["r1.jpg", "r1.mp4"]
        assert "beschreibbar" in t.check()
        assert (tmp_path / "root" / "live" / "r1.mp4").read_bytes() == Path(mp4["path"]).read_bytes()
        assert ReuseRequiredDTP.seen and all(ReuseRequiredDTP.seen), ReuseRequiredDTP.seen
        ftp = t._connect()
        assert ftp.sock.version() == version.replace("_", ".")
        ftp.quit()
    finally:
        srv.close_all()


def test_without_session_reuse_that_server_refuses_the_data_connection(certs, tmp_path):
    """Proves the server variant really checks: plain ftplib does not resume."""
    ReuseRequiredDTP.seen = []
    srv = _server(tmp_path / "root", certs, ReuseRequiredDTP)
    try:
        ftp = ftplib.FTP_TLS(context=ssl.create_default_context(), timeout=5)
        ftp.connect("127.0.0.1", srv.address[1])
        ftp.login("box", PASSWORT)
        ftp.prot_p()
        with pytest.raises((ftplib.Error, OSError)):
            ftp.storbinary("STOR x.bin", __import__("io").BytesIO(b"x" * 1000))
        assert ReuseRequiredDTP.seen == [False]
        ftp.close()
    finally:
        srv.close_all()


@pytest.mark.parametrize("dtp", [ReuseRequiredDTP, BareCloseDTP])
def test_implicit_ftps_resumes_the_session_and_tolerates_a_bare_close(certs, tmp_path, dtp):
    ReuseRequiredDTP.seen = []
    srv = _server(tmp_path / "root", certs, dtp, implicit=True)
    try:
        ftp = targets.ImplicitFTP_TLS(context=trust.verify_context(), timeout=5)
        ftp.connect("127.0.0.1", srv.address[1])
        ftp.login("box", PASSWORT)
        ftp.prot_p()
        ftp.set_pasv(True)
        ftp.storbinary("STOR r.bin", __import__("io").BytesIO(b"y" * 20000))
        assert ftp.size("r.bin") == 20000
        assert ftp.nlst() == ["r.bin"]
        ftp.quit()
        assert (tmp_path / "root" / "r.bin").read_bytes() == b"y" * 20000
        if dtp is ReuseRequiredDTP:
            assert ReuseRequiredDTP.seen and all(ReuseRequiredDTP.seen)
    finally:
        srv.close_all()


# ── (c) a transfer cut in the middle still fails ────────────────────────

def test_an_upload_cut_in_the_middle_is_a_temporary_error_and_lands_nowhere(certs, tmp_path):
    srv = _server(tmp_path / "root", certs, CutDTP)
    try:
        big = tmp_path / "src" / "gross.mp4"
        big.parent.mkdir()
        big.write_bytes(b"\x00" * (4 * 1024 * 1024))
        item = {"uid": "gross", "variant": "web", "kind": "video", "event_slug": "fest", "path": str(big),
                "meta": {"taken_at": "2026-07-20T21:14:03", "event_name": "Fest"}}
        with pytest.raises(targets.TemporaryError):
            targets.FtpsTarget(_cfg(srv)).upload(item)
        assert not (tmp_path / "root" / "gross.mp4").exists()
    finally:
        srv.close_all()


# ── (d) an untrusted certificate is still refused (see also test_sftp_host_key_is_pinned) ──

def test_a_bare_closing_server_with_an_untrusted_certificate_is_still_refused(tmp_path, monkeypatch):
    _ca, right, _wrong = _certs(tmp_path)          # no _trust: the test CA is in no store
    srv = _server(tmp_path / "root", right, BareCloseDTP)
    try:
        with pytest.raises(targets.TemporaryError) as e:
            targets.FtpsTarget(_cfg(srv)).check()
        assert "certificate verify failed" in str(e.value).lower()
    finally:
        srv.close_all()
