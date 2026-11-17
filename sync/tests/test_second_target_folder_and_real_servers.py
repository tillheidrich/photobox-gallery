"""The second target's folder rule, against real servers on 127.0.0.1 (17.11.2026).

Empty folder = the login's start folder; otherwise a folder made when
missing (nested), a leading "/" from the top, placeholders filled per file
({event} {event_name} {datum} {jahr} {monat} {tag}); "Prüfen" logs in and
proves the folder writable. Servers: a paramiko SSH server whose SFTP side
is a real directory, and pyftpdlib with explicit TLS (certificate from a
test CA the client trusts). Nothing leaves this machine.
"""
from __future__ import annotations

import os
import socket
import threading
import time
from pathlib import Path

import paramiko
import pytest

from photobox_sync import targets, zielordner
from photobox_sync.targets import HostKeys, SftpTarget

PASSWORT = "test-only-not-a-secret"
ROOT = Path(__file__).resolve().parents[2]


# ── The rule itself ───────────────────────────────────────────

@pytest.mark.parametrize("raw,clean", [
    ("", ""), ("  ", ""), ("/", "/"), ("a\\b//c/./", "a/b/c"), ("/x/{jahr}/{event}", "/x/{jahr}/{event}"),
])
def test_the_folder_field_is_normalised(raw, clean):
    assert zielordner.check(raw) == clean


@pytest.mark.parametrize("raw,word", [("../x", "„..“"), ("a/{wer}", "Platzhalter"), ("a/{jahr", "Klammer"),
                                      ("a\x07b", "Steuerzeichen"), ("x" * 256, "höchstens")])
def test_the_folder_field_refuses_in_words(raw, word):
    with pytest.raises(ValueError) as e:
        zielordner.check(raw)
    assert word in str(e.value)


def test_placeholders_are_filled_per_file_and_stay_one_folder_each():
    out = zielordner.expand("fotobox/{jahr}/{monat}/{tag}/{datum}/{event}/{event_name}",
                            slug="sommer-fest", event_name="Hochzeit Müller / Öztürk", when="2026-07-20T21:14:03")
    assert out == "fotobox/2026/07/20/2026-07-20/sommer-fest/Hochzeit-Mueller-Oeztuerk"
    # A slash or ".." in an event name never becomes a folder step.
    assert zielordner.expand("{event_name}", event_name="../../etc") == "etc"
    assert zielordner.expand("{datum}", when=None) == "undatiert"


def test_the_example_says_where_a_picture_lands():
    assert zielordner.example("", True).startswith("(Startordner)/Sommerfest")
    assert zielordner.example("/{jahr}/{event}", True) == "/2026/sommerfest-2026/Sommerfest-2026_2026-07-20_21-14-03.jpg"
    assert zielordner.example("nas", False) == \
        "(Startordner)/nas/sommerfest-2026/2026-07-20/fotos/Sommerfest-2026_2026-07-20_21-14-03.jpg"


def test_gallery_and_sync_package_have_the_same_module_byte_for_byte():
    assert (ROOT / "app" / "zielordner.py").read_bytes() == (ROOT / "sync" / "photobox_sync" / "zielordner.py").read_bytes()


# ── A real SFTP server on a real directory ────────────────────

class _Ssh(paramiko.ServerInterface):
    def check_auth_password(self, username, password):
        return paramiko.AUTH_SUCCESSFUL if password == PASSWORT else paramiko.AUTH_FAILED

    def get_allowed_auths(self, username):
        return "password"

    def check_channel_request(self, kind, chanid):
        return paramiko.OPEN_SUCCEEDED if kind == "session" else paramiko.OPEN_FAILED_ADMINISTRATIVELY_PROHIBITED


class _Handle(paramiko.SFTPHandle):
    def stat(self):
        return paramiko.SFTPAttributes.from_stat(os.fstat(self.readfile.fileno()))


class _Disk(paramiko.SFTPServerInterface):
    """The server's "/" is ``root``; the login starts in ``root/home``."""
    root: Path = Path(".")

    def _real(self, path):
        p = path if path.startswith("/") else "/home/" + path
        real = (self.root / p.lstrip("/")).resolve()
        if self.root.resolve() not in real.parents and real != self.root.resolve():
            raise PermissionError(path)
        return real

    def canonicalize(self, path):
        return "/home" if path in (".", "") else (path if path.startswith("/") else "/home/" + path)

    def stat(self, path):
        try:
            return paramiko.SFTPAttributes.from_stat(os.stat(self._real(path)))
        except OSError as e:
            return paramiko.SFTPServer.convert_errno(e.errno)

    lstat = stat

    def list_folder(self, path):
        real = self._real(path)
        return [paramiko.SFTPAttributes.from_stat(os.stat(real / n), n) for n in os.listdir(real)]

    def mkdir(self, path, attr):
        try:
            os.mkdir(self._real(path))
        except OSError as e:
            return paramiko.SFTPServer.convert_errno(e.errno)
        return paramiko.SFTP_OK

    def open(self, path, flags, attr):
        real = self._real(path)
        try:
            fd = os.open(real, flags | getattr(os, "O_BINARY", 0), 0o644)
            f = os.fdopen(fd, "wb" if flags & os.O_WRONLY else "rb")
        except OSError as e:
            return paramiko.SFTPServer.convert_errno(e.errno)
        h = _Handle(flags)
        h.readfile = h.writefile = f
        return h

    def remove(self, path):
        try:
            os.remove(self._real(path))
        except OSError as e:
            return paramiko.SFTPServer.convert_errno(e.errno)
        return paramiko.SFTP_OK

    def rename(self, old, new):
        os.rename(self._real(old), self._real(new))
        return paramiko.SFTP_OK


class DiskSsh:
    def __init__(self, root: Path):
        (root / "home").mkdir(parents=True, exist_ok=True)
        _Disk.root = root
        self.key = paramiko.ECDSAKey.generate()
        self.sock = socket.socket()
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(8)
        self.port = self.sock.getsockname()[1]
        self.transports = []
        threading.Thread(target=self._loop, daemon=True).start()

    def _loop(self):
        while True:
            try:
                conn, _ = self.sock.accept()
            except OSError:
                return
            t = paramiko.Transport(conn)
            t.add_server_key(self.key)
            t.set_subsystem_handler("sftp", paramiko.SFTPServer, _Disk)
            try:
                t.start_server(server=_Ssh())
            except (paramiko.SSHException, EOFError, OSError):
                continue
            self.transports.append(t)

    def close(self):
        self.sock.close()
        for t in self.transports:
            t.close()


def _files(tmp_path):
    jpg = tmp_path / "src" / "r1.jpg"
    mp4 = tmp_path / "src" / "r1.mp4"
    jpg.parent.mkdir()
    jpg.write_bytes(b"\xff\xd8bild\xff\xd9")
    mp4.write_bytes(b"\x00\x00\x00\x18ftypmp42" + b"x" * 5000)
    meta = {"taken_at": "2026-07-20T21:14:03", "event_name": "Sommer Fest"}
    return [{"uid": "r1", "variant": "web", "kind": "photo", "event_slug": "sommer-fest", "path": str(jpg), "meta": meta},
            {"uid": "r1.mp4", "variant": "web", "kind": "video", "event_slug": "sommer-fest", "path": str(mp4),
             "meta": meta}]


@pytest.fixture()
def disk_ssh(tmp_path):
    s = DiskSsh(tmp_path / "server")
    yield s
    s.close()


def _sftp(srv, tmp_path, **over):
    cfg = {"type": "sftp", "name": "picdrop", "host": "127.0.0.1", "port": srv.port, "user": "box",
           "password": PASSWORT, "flat": True, "path": "", "timeout": 5, **over}
    return SftpTarget(cfg, HostKeys(tmp_path / "k.json"))


def test_sftp_nested_folder_with_placeholders_photo_and_mp4(disk_ssh, tmp_path):
    t = _sftp(disk_ssh, tmp_path, path="fotobox/{jahr}/{event}")
    for item in _files(tmp_path):
        t.upload(item)
    folder = tmp_path / "server" / "home" / "fotobox" / "2026" / "sommer-fest"
    names = sorted(p.name for p in folder.iterdir())
    assert names == ["r1.jpg", "r1.mp4"], names                 # no ".part" left behind
    assert (folder / "r1.mp4").stat().st_size == (tmp_path / "src" / "r1.mp4").stat().st_size


def test_sftp_empty_folder_is_the_start_folder_and_a_slash_is_the_top(disk_ssh, tmp_path):
    item = _files(tmp_path)[0]
    _sftp(disk_ssh, tmp_path).upload(item)
    assert (tmp_path / "server" / "home" / "r1.jpg").is_file()
    _sftp(disk_ssh, tmp_path, path="/oben/{datum}").upload(item)
    assert (tmp_path / "server" / "oben" / "2026-07-20" / "r1.jpg").is_file()


def test_sftp_layout_with_folders_per_event_and_day(disk_ssh, tmp_path):
    _sftp(disk_ssh, tmp_path, path="nas", flat=False).upload(_files(tmp_path)[1])
    assert (tmp_path / "server" / "home" / "nas" / "sommer-fest" / "2026-07-20" / "fotos" / "r1.mp4").is_file()


def test_sftp_check_proves_the_folder_writable_and_leaves_nothing(disk_ssh, tmp_path):
    answer = _sftp(disk_ssh, tmp_path, path="pruef/{event}").check()
    assert "beschreibbar" in answer and "pruef" in answer
    folder = tmp_path / "server" / "home" / "pruef"
    assert folder.is_dir() and list(folder.iterdir()) == []


def test_sftp_check_says_a_read_only_folder_is_not_writable(disk_ssh, tmp_path):
    ro = tmp_path / "server" / "home" / "nur-lesen"
    ro.mkdir()
    ro.chmod(0o555)
    try:
        with pytest.raises(targets.PermanentError) as e:
            _sftp(disk_ssh, tmp_path, path="nur-lesen").check()
        assert "nicht beschreibbar" in str(e.value)
    finally:
        ro.chmod(0o755)


# ── A real FTPS server (explicit TLS) and plain FTP ───────────

def _ftp_server(root: Path, tls_cert: Path | None):
    from pyftpdlib.authorizers import DummyAuthorizer
    from pyftpdlib.handlers import FTPHandler, TLS_FTPHandler
    from pyftpdlib.servers import FTPServer
    root.mkdir(parents=True, exist_ok=True)
    auth = DummyAuthorizer()
    auth.add_user("box", PASSWORT, str(root), perm="elradfmwMT")
    if tls_cert:
        handler = type("H", (TLS_FTPHandler,), {})
        handler.certfile = str(tls_cert)
        handler.tls_control_required = True
        handler.tls_data_required = True
    else:
        handler = type("H", (FTPHandler,), {})
    handler.authorizer = auth
    handler.passive_ports = None
    srv = FTPServer(("127.0.0.1", 0), handler)
    th = threading.Thread(target=srv.serve_forever, kwargs={"timeout": 0.05}, daemon=True)
    th.start()
    return srv


@pytest.fixture()
def ftps(tmp_path, monkeypatch):
    pytest.importorskip("OpenSSL")
    from test_sftp_host_key_is_pinned import _certs, _trust
    ca, right, _wrong = _certs(tmp_path)
    _trust(monkeypatch, ca)
    srv = _ftp_server(tmp_path / "ftproot", right)
    yield srv
    srv.close_all()


def _ftp_cfg(srv, kind="ftps", **over):
    return {"type": kind, "name": "picdrop", "host": "127.0.0.1", "port": srv.address[1], "user": "box",
            "password": PASSWORT, "flat": True, "path": "", "timeout": 5, **over}


def test_ftps_nested_folder_with_placeholders_photo_and_mp4(ftps, tmp_path):
    t = targets.FtpsTarget(_ftp_cfg(ftps, path="galerie/{datum}/{event_name}"))
    for item in _files(tmp_path):
        t.upload(item)
    folder = tmp_path / "ftproot" / "galerie" / "2026-07-20" / "Sommer-Fest"
    assert sorted(p.name for p in folder.iterdir()) == ["r1.jpg", "r1.mp4"]


def test_ftps_check_proves_the_folder_writable(ftps, tmp_path):
    answer = targets.FtpsTarget(_ftp_cfg(ftps, path="/live/{event}")).check()
    assert "beschreibbar" in answer and "/live" in answer
    assert list((tmp_path / "ftproot" / "live").iterdir()) == []


def test_plain_ftp_empty_folder_is_the_start_folder(tmp_path):
    srv = _ftp_server(tmp_path / "plain", None)
    try:
        targets.FtpTarget(_ftp_cfg(srv, "ftp")).upload(_files(tmp_path)[1])
        assert (tmp_path / "plain" / "r1.mp4").is_file()
    finally:
        srv.close_all()


def test_a_wrong_ftps_password_is_said_as_such(ftps):
    with pytest.raises(targets.PermanentError) as e:
        targets.FtpsTarget(_ftp_cfg(ftps, password="falsch")).check()
    assert "Anmeldung abgelehnt" in str(e.value)
