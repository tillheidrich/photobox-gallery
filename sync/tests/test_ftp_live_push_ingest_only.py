"""FTP(S) "live push" accounts that only take pictures (18.11.2026).

A gallery service offers an FTPS login per gallery: whatever lands there is
put into a gallery chosen on the service's side, so the folder field stays
empty. The login worked, every real upload failed with "550 Permission
denied" or "EOF occurred in violation of protocol" during the transfer: the
target uploaded ``<name>.part`` and renamed it, and such an ingest-only
account takes only picture and film names and refuses rename, delete and
folders. Now:

* option ``direct``: STOR straight to the final name, no rename, no DELE;
* without the option, a refused ``.part`` upload or rename is tried once more
  in a fresh connection in direct mode; if that works, the target remembers
  (in the host-key file, per type/host/port/user) and goes direct from then on;
* ``check()`` falls back to a tiny real JPEG and says "direkter Upload
  möglich (Live-Push)";
* flat with an empty folder sends no CWD/MKD at all.

The server here is pyftpdlib on 127.0.0.1 with an "ingest-only" mode: 550 for
STOR of a name that is no picture or film, for RNFR/RNTO, DELE, MKD and
optionally SIZE; a variant drops the data connection of a refused STOR
without a TLS goodbye instead of answering 550 at once.
"""
from __future__ import annotations

import io
import logging
import os
import threading
from pathlib import Path

import pytest

pytest.importorskip("OpenSSL")

from pyftpdlib.authorizers import DummyAuthorizer          # noqa: E402
from pyftpdlib.handlers import FTPHandler, TLS_DTPHandler, TLS_FTPHandler  # noqa: E402
from pyftpdlib.servers import FTPServer                     # noqa: E402

from photobox_sync import targets                           # noqa: E402

from test_sftp_host_key_is_pinned import PASSWORT, _certs, _trust  # noqa: E402

MEDIA = (".jpg", ".jpeg", ".png", ".gif", ".mp4", ".mov", ".webp")


def _quiet(*_a, **_k):
    pass


class IngestOnly:
    """Mixin for a pyftpdlib handler: an account that only takes pictures and films.

    Class attributes set per server: ``eof`` drops the data connection of a
    refused STOR (no TLS close_notify) instead of answering 550 straight away;
    ``refuse_size`` answers 550 to SIZE; ``refuse_all`` refuses every STOR
    (also picture names); ``accept_any`` takes every name but still refuses
    rename and delete. ``seen`` records every command with its argument.
    """

    eof = False
    refuse_size = False
    refuse_all = False
    quota = False
    accept_any = False
    seen: list = []

    def pre_process_command(self, line, cmd, arg):
        type(self).seen.append((cmd, arg or ""))
        return super().pre_process_command(line, cmd, arg)

    def _refused(self, file) -> bool:
        if self.refuse_all:
            return True
        if self.accept_any:
            return False
        return not str(file).lower().endswith(MEDIA)

    def ftp_STOR(self, file, mode="w"):
        if self.quota:
            self.respond("552 Quota exceeded.")
            return None
        if self._refused(file):
            if self.eof:
                self._eof_path = file
                return super().ftp_STOR(file, mode)
            self.respond("550 Permission denied.")
            return None
        self._eof_path = None
        return super().ftp_STOR(file, mode)

    def ftp_RNFR(self, path):
        self.respond("550 Permission denied.")

    def ftp_RNTO(self, path):
        self.respond("550 Permission denied.")

    def ftp_DELE(self, path):
        self.respond("550 Permission denied.")

    def ftp_MKD(self, path):
        self.respond("550 Permission denied.")

    def ftp_SIZE(self, path):
        if self.refuse_size:
            self.respond("550 Permission denied.")
            return None
        return super().ftp_SIZE(path)


class EofDTP(TLS_DTPHandler):
    """Drops a refused upload's data connection bare, as the real server did."""

    def handle_read_event(self):
        path = getattr(self.cmd_channel, "_eof_path", None)
        if path and self.receive and not self._closed and getattr(self, "_ssl_established", False):
            self._resp = ("550 Permission denied.", _quiet)
            self.cmd_channel._eof_path = None
            self._ssl_established = False          # no TLS close_notify, like the server on the booth
            self.close()
            try:
                os.remove(path)
            except OSError:
                pass
            return
        super().handle_read_event()


def _server(root: Path, cert: Path | None, **mode):
    root.mkdir(parents=True, exist_ok=True)
    auth = DummyAuthorizer()
    auth.add_user("box", PASSWORT, str(root), perm="elradfmwMT")
    attrs = {"authorizer": auth, "passive_ports": None, "seen": [], **mode}
    if cert:
        attrs.update({"certfile": str(cert), "keyfile": None, "ssl_context": None, "tls_control_required": True,
                      "tls_data_required": True, "dtp_handler": EofDTP})
        handler = type("H", (IngestOnly, TLS_FTPHandler), attrs)
        handler.get_ssl_context().set_session_id(b"photobox-test")
    else:
        handler = type("H", (IngestOnly, FTPHandler), attrs)
    srv = FTPServer(("127.0.0.1", 0), handler)
    srv.seen = handler.seen
    threading.Thread(target=srv.serve_forever, kwargs={"timeout": 0.05}, daemon=True).start()
    return srv


@pytest.fixture()
def cert(tmp_path, monkeypatch):
    ca, right, _wrong = _certs(tmp_path)
    _trust(monkeypatch, ca)
    return right


def _cfg(srv, kind="ftps", **over):
    return {"type": kind, "name": "zweites-ziel", "host": "127.0.0.1", "port": srv.address[1], "user": "box",
            "password": PASSWORT, "flat": True, "path": "", "timeout": 5, **over}


def _item(tmp_path, name="r1.jpg", size=2000, uid="r1"):
    src = tmp_path / "src" / name
    src.parent.mkdir(exist_ok=True)
    src.write_bytes(b"\xff\xd8" + b"x" * max(0, size - 4) + b"\xff\xd9")
    return {"uid": uid, "variant": "web", "kind": "photo", "event_slug": "hochzeit", "path": str(src),
            "meta": {"taken_at": "2026-11-18T15:04:05", "event_name": "Hochzeit"}}


def _stors(srv):
    return [a for c, a in srv.seen if c == "STOR"]


def _cmds(srv):
    return {c for c, _a in srv.seen}


# ── (1) without the option: one fallback, then remembered ─────────────

@pytest.mark.parametrize("eof", [False, True], ids=["550", "eof"])
def test_a_refused_part_upload_goes_direct_once_and_is_remembered(cert, tmp_path, eof):
    srv = _server(tmp_path / "root", cert, eof=eof)
    keys = targets.HostKeys(tmp_path / "keys.json")
    try:
        # Large enough that a dropped data connection shows up on the client while it sends.
        first = _item(tmp_path, size=3 * 1024 * 1024)
        answer = targets.FtpsTarget(_cfg(srv), keys).upload(first)
        assert answer["bytes"] == Path(first["path"]).stat().st_size
        landed = tmp_path / "root"
        assert sorted(p.name for p in landed.iterdir()) == ["r1.jpg"]
        assert (landed / "r1.jpg").read_bytes() == Path(first["path"]).read_bytes()
        assert _stors(srv) == ["r1.jpg.part", "r1.jpg"], "one try with the in-between name, one direct"
        # Remembered across a restart: the next file goes direct at once.
        srv.seen.clear()
        again = targets.HostKeys(tmp_path / "keys.json")
        targets.FtpsTarget(_cfg(srv), again).upload(_item(tmp_path, "r2.jpg", uid="r2"))
        assert _stors(srv) == ["r2.jpg"]
        assert not {"RNFR", "RNTO", "DELE", "MKD", "CWD"} & _cmds(srv)
    finally:
        srv.close_all()


def _break_part_uploads(monkeypatch, error):
    """The in-between upload dies on the client with ``error`` and no reply from the server."""
    real = targets.ExplicitFTP_TLS.storbinary

    def storbinary(self, cmd, fp, *a, **k):
        if cmd.endswith(".part"):
            raise error
        return real(self, cmd, fp, *a, **k)
    monkeypatch.setattr(targets.ExplicitFTP_TLS, "storbinary", storbinary)


def test_a_tls_eof_without_any_reply_still_goes_direct(cert, tmp_path, monkeypatch):
    """The booth's error word for word: no 550, just the TLS end of file during the transfer."""
    import ssl
    _break_part_uploads(monkeypatch, ssl.SSLEOFError(8, "EOF occurred in violation of protocol (_ssl.c:2427)"))
    srv = _server(tmp_path / "root", cert)
    try:
        targets.FtpsTarget(_cfg(srv, timeout=1), targets.HostKeys(None)).upload(_item(tmp_path))
        assert _stors(srv) == ["r1.jpg"]
        assert (tmp_path / "root" / "r1.jpg").is_file()
    finally:
        srv.close_all()


def test_a_cut_line_without_any_reply_is_temporary_and_not_retried(cert, tmp_path, monkeypatch):
    _break_part_uploads(monkeypatch, BrokenPipeError(32, "Broken pipe"))
    srv = _server(tmp_path / "root", cert)
    keys = targets.HostKeys(None)
    try:
        with pytest.raises(targets.TemporaryError):
            targets.FtpsTarget(_cfg(srv, timeout=1), keys).upload(_item(tmp_path))
        assert _stors(srv) == [], "no second attempt for what looks like the network"
        assert not keys.ftp_direct(targets.endpoint_key(_cfg(srv, timeout=1)))
    finally:
        srv.close_all()


def test_the_remembered_direct_mode_belongs_to_that_login_only(cert, tmp_path):
    srv = _server(tmp_path / "root", cert)
    keys = targets.HostKeys(tmp_path / "keys.json")
    try:
        targets.FtpsTarget(_cfg(srv), keys).upload(_item(tmp_path))
        assert keys.ftp_direct(targets.endpoint_key(_cfg(srv)))
        assert not keys.ftp_direct(targets.endpoint_key(_cfg(srv, user="jemand-anders")))
        assert keys.report("127.0.0.1", srv.address[1]) == {}, "nothing of it shows as an SFTP host key"
    finally:
        srv.close_all()


def test_a_file_refused_under_both_names_is_tried_twice_not_more(cert, tmp_path):
    srv = _server(tmp_path / "root", cert, refuse_all=True)
    try:
        with pytest.raises(targets.TemporaryError) as e:
            targets.FtpsTarget(_cfg(srv), targets.HostKeys(None)).upload(_item(tmp_path))
        assert _stors(srv) == ["r1.jpg.part", "r1.jpg"]
        assert "nimmt" in str(e.value) and "nicht an" in str(e.value)
    finally:
        srv.close_all()


def test_a_name_the_account_never_takes_is_a_permanent_error_in_plain_words(cert, tmp_path):
    srv = _server(tmp_path / "root", cert)
    try:
        with pytest.raises(targets.PermanentError) as e:
            targets.FtpsTarget(_cfg(srv, direct=True), targets.HostKeys(None)).upload(
                _item(tmp_path, "r1.heic"))
        text = str(e.value)
        assert "Das Konto auf dem Server nimmt" in text and "r1.heic" in text
        assert _stors(srv) == ["r1.heic"]
    finally:
        srv.close_all()


def test_a_refused_rename_leaves_the_part_file_logged_once_and_goes_direct(cert, tmp_path, caplog):
    srv = _server(tmp_path / "root", cert, accept_any=True)
    keys = targets.HostKeys(None)
    try:
        with caplog.at_level(logging.WARNING, logger="photobox_sync.targets"):
            targets.FtpsTarget(_cfg(srv), keys).upload(_item(tmp_path))
        names = sorted(p.name for p in (tmp_path / "root").iterdir())
        assert names == ["r1.jpg", "r1.jpg.part"], names
        assert _stors(srv) == ["r1.jpg.part", "r1.jpg"]
        leftovers = [r for r in caplog.records if ".part" in r.getMessage()]
        assert len(leftovers) == 1, [r.getMessage() for r in caplog.records]
    finally:
        srv.close_all()


# ── (2) the option ``direct`` ──────────────────────────────────────────

@pytest.mark.parametrize("refuse_size", [False, True], ids=["size", "no-size"])
def test_direct_sends_the_final_name_only_and_nothing_else(cert, tmp_path, refuse_size):
    srv = _server(tmp_path / "root", cert, refuse_size=refuse_size)
    try:
        item = _item(tmp_path)
        answer = targets.FtpsTarget(_cfg(srv, direct=True), targets.HostKeys(None)).upload(item)
        assert answer["bytes"] == Path(item["path"]).stat().st_size
        assert (tmp_path / "root" / "r1.jpg").read_bytes() == Path(item["path"]).read_bytes()
        assert _stors(srv) == ["r1.jpg"]
        assert not {"RNFR", "RNTO", "DELE", "MKD", "CWD"} & _cmds(srv), sorted(_cmds(srv))
    finally:
        srv.close_all()


def test_plain_ftp_takes_the_same_fallback(tmp_path):
    srv = _server(tmp_path / "plain", None)
    try:
        targets.FtpTarget(_cfg(srv, "ftp"), targets.HostKeys(None)).upload(_item(tmp_path))
        assert sorted(p.name for p in (tmp_path / "plain").iterdir()) == ["r1.jpg"]
        assert _stors(srv) == ["r1.jpg.part", "r1.jpg"]
    finally:
        srv.close_all()


def test_flat_with_an_empty_folder_sends_no_folder_commands_on_a_normal_server(cert, tmp_path):
    srv = _server(tmp_path / "root", cert, accept_any=True)
    srv.handler.ftp_RNFR = TLS_FTPHandler.ftp_RNFR        # a normal server: rename works
    srv.handler.ftp_RNTO = TLS_FTPHandler.ftp_RNTO
    try:
        targets.FtpsTarget(_cfg(srv), targets.HostKeys(None)).upload(_item(tmp_path))
        assert sorted(p.name for p in (tmp_path / "root").iterdir()) == ["r1.jpg"]
        assert not {"MKD", "CWD"} & _cmds(srv)
        assert _stors(srv) == ["r1.jpg.part"], "a normal server keeps the in-between name"
    finally:
        srv.close_all()


# ── (3) check() ─────────────────────────────────────────────────────

@pytest.mark.parametrize("eof", [False, True], ids=["550", "eof"])
def test_check_proves_a_live_push_account_with_a_tiny_jpeg(cert, tmp_path, eof):
    srv = _server(tmp_path / "root", cert, eof=eof)
    keys = targets.HostKeys(None)
    try:
        answer = targets.FtpsTarget(_cfg(srv), keys).check()
        assert "angemeldet, direkter Upload möglich (Live-Push)" in answer
        assert "Testbild" in answer and "von Hand" in answer          # DELE refused: it stays
        probe = tmp_path / "root" / targets.LIVE_PUSH_CHECK_FILE
        assert probe.read_bytes() == targets.TINY_JPEG
        assert not {"MKD", "CWD"} & _cmds(srv)
        assert keys.ftp_direct(targets.endpoint_key(_cfg(srv)))
    finally:
        srv.close_all()


def test_the_test_picture_is_a_real_jpeg():
    pil = pytest.importorskip("PIL.Image")
    with pil.open(io.BytesIO(targets.TINY_JPEG)) as im:
        im.load()
        assert im.format == "JPEG" and im.size == (1, 1)


def test_check_on_a_normal_server_says_what_it_said_before(cert, tmp_path):
    srv = _server(tmp_path / "root", cert, accept_any=True)
    srv.handler.ftp_DELE = TLS_FTPHandler.ftp_DELE
    try:
        answer = targets.FtpsTarget(_cfg(srv), targets.HostKeys(None)).check()
        assert answer.startswith("angemeldet, Ordner") and "beschreibbar" in answer
        assert "Live-Push" not in answer
        assert list((tmp_path / "root").iterdir()) == []
    finally:
        srv.close_all()


def test_build_extra_targets_hands_the_key_store_to_ftp_targets(tmp_path):
    keys = targets.HostKeys(tmp_path / "k.json")
    built = targets.build_extra_targets([{"type": "ftps", "host": "x", "user": "u", "password": "p"},
                                         {"type": "ftp", "host": "y", "user": "u", "password": "p"}], keys)
    assert [t.host_keys for t in built] == [keys, keys]


# ── Review round: what is remembered, what never falls back ─────────────

def test_a_tls_eof_without_reply_is_not_remembered_until_it_happened_twice(cert, tmp_path, monkeypatch):
    """No 5xx was seen: one direct success may have been luck on a flaky line, so nothing is written."""
    import ssl
    import json as _json
    monkeypatch.setattr(targets, "_EOF_DIRECT_OK", {})
    _break_part_uploads(monkeypatch, ssl.SSLEOFError(8, "EOF occurred in violation of protocol (_ssl.c:2427)"))
    srv = _server(tmp_path / "root", cert)
    store = tmp_path / "keys.json"
    try:
        targets.FtpsTarget(_cfg(srv, timeout=1), targets.HostKeys(store)).upload(_item(tmp_path))
        assert not targets.HostKeys(store).ftp_direct(targets.endpoint_key(_cfg(srv, timeout=1)))
        assert not store.exists() or "_ftp_direct" not in _json.loads(store.read_text())
        targets.FtpsTarget(_cfg(srv, timeout=1), targets.HostKeys(store)).upload(_item(tmp_path, "r2.jpg", uid="r2"))
        assert targets.HostKeys(store).ftp_direct(targets.endpoint_key(_cfg(srv, timeout=1)))
    finally:
        srv.close_all()


def test_a_broken_direct_store_is_read_as_empty_and_overwritten(tmp_path):
    store = tmp_path / "keys.json"
    store.write_text('{"_ftp_direct": "kaputt", "h:22": {"sha256": "x"}}', encoding="utf-8")
    keys = targets.HostKeys(store)
    assert keys.ftp_direct("ftps|h|21|u") is False
    keys.remember_ftp_direct("ftps|h|21|u")
    assert targets.HostKeys(store).ftp_direct("ftps|h|21|u")


def test_a_store_that_cannot_be_saved_does_not_fail_the_upload_or_the_check(cert, tmp_path, monkeypatch, caplog):
    srv = _server(tmp_path / "root", cert)
    keys = targets.HostKeys(tmp_path / "keys.json")

    def broken():
        raise OSError(28, "No space left on device")
    monkeypatch.setattr(keys, "_save", broken)
    monkeypatch.setattr(targets, "_DIRECT_SAVE_WARNED", False)
    try:
        with caplog.at_level(logging.WARNING, logger="photobox_sync.targets"):
            assert targets.FtpsTarget(_cfg(srv), keys).upload(_item(tmp_path))["bytes"] == 2000
            assert "Live-Push" in targets.FtpsTarget(_cfg(srv), keys).check()
        assert keys.ftp_direct(targets.endpoint_key(_cfg(srv))), "still known in this process"
        assert len([r for r in caplog.records if "nicht gespeichert" in r.getMessage()]) == 1
    finally:
        srv.close_all()


@pytest.mark.parametrize("direct", [False, True], ids=["part", "direct"])
@pytest.mark.parametrize("name", ["r1.jpg", "r1.heic"])
def test_a_full_server_never_falls_back_and_is_never_permanent(cert, tmp_path, direct, name):
    srv = _server(tmp_path / "root", cert, quota=True)
    keys = targets.HostKeys(None)
    try:
        with pytest.raises(targets.TemporaryError) as e:
            targets.FtpsTarget(_cfg(srv, direct=direct), keys).upload(_item(tmp_path, name))
        assert not isinstance(e.value, targets.PermanentError) and "552" in str(e.value)
        assert len(_stors(srv)) == 1, _stors(srv)
        assert not keys.ftp_direct(targets.endpoint_key(_cfg(srv, direct=direct)))
    finally:
        srv.close_all()
