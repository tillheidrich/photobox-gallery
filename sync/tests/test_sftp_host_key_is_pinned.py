"""SFTP trusts a server's key once, then only that key; FTPS checks certificate and name (17.11.2026).

Before, ``SftpTarget`` used paramiko's ``Transport.connect`` without a host
key, so any machine between the booth and the NAS could pose as the server
and read the password (business_logic A-3c). Now the first successful login
stores the key's SHA-256 fingerprint (``HostKeys``, a file in the data
folder); a different key later ends the connection before the user name,
the password or a file leaves the booth, says so in plain German, records
the key it saw and waits for the operator to accept exactly that one.

Both tests run real servers on 127.0.0.1: a paramiko SSH/SFTP server whose
key can be swapped, and a minimal FTPS server whose certificate is signed by
a test CA the client trusts — once for the right name, once for a wrong one.
"""
from __future__ import annotations

import datetime as dt
import ipaddress
import socket
import ssl
import threading
from pathlib import Path

import paramiko
import pytest

from photobox_sync import targets
from photobox_sync.targets import HostKeyChanged, HostKeys, SftpTarget

PASSWORT = "test-only-not-a-secret"


class _Ssh(paramiko.ServerInterface):
    def __init__(self, log):
        self.log = log

    def check_auth_password(self, username, password):
        self.log.append((username, password))
        return paramiko.AUTH_SUCCESSFUL if password == PASSWORT else paramiko.AUTH_FAILED

    def get_allowed_auths(self, username):
        return "password"

    def check_channel_request(self, kind, chanid):
        return paramiko.OPEN_SUCCEEDED if kind == "session" else paramiko.OPEN_FAILED_ADMINISTRATIVELY_PROHIBITED


class _Files(paramiko.SFTPServerInterface):
    received: list = []

    def list_folder(self, path):
        return []

    def stat(self, path):
        a = paramiko.SFTPAttributes()
        a.st_mode = 0o40755
        return a

    lstat = stat

    def canonicalize(self, path):
        return "/"

    def mkdir(self, path, attr):
        return paramiko.SFTP_OK

    def open(self, path, flags, attr):
        if path.endswith(targets.CHECK_FILE):
            # "Prüfen" writes and removes a small file to prove the folder is writable.
            return _Probe(flags)
        _Files.received.append(path)
        return paramiko.SFTP_PERMISSION_DENIED

    def remove(self, path):
        return paramiko.SFTP_OK


class _Probe(paramiko.SFTPHandle):
    def write(self, offset, data):
        return paramiko.SFTP_OK


class SshServer:
    """A local SSH server with an SFTP subsystem; ``key`` is the host key it shows."""

    def __init__(self):
        self.key = paramiko.ECDSAKey.generate()
        self.logins: list = []
        self.sock = socket.socket()
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(8)
        self.port = self.sock.getsockname()[1]
        self.transports: list = []
        threading.Thread(target=self._loop, daemon=True).start()

    def _loop(self):
        while True:
            try:
                conn, _ = self.sock.accept()
            except OSError:
                return
            t = paramiko.Transport(conn)
            t.add_server_key(self.key)
            t.set_subsystem_handler("sftp", paramiko.SFTPServer, _Files)
            try:
                t.start_server(server=_Ssh(self.logins))
            except (paramiko.SSHException, EOFError, OSError):
                continue
            self.transports.append(t)

    def close(self):
        self.sock.close()
        for t in self.transports:
            t.close()


@pytest.fixture()
def ssh():
    s = SshServer()
    yield s
    s.close()


def _target(ssh, keys):
    return SftpTarget({"type": "sftp", "name": "nas", "host": "127.0.0.1", "port": ssh.port,
                       "user": "box", "password": PASSWORT, "flat": True, "timeout": 5}, keys)


def test_first_login_pins_the_key_and_a_different_key_is_refused_before_the_password(ssh, tmp_path):
    keys = HostKeys(tmp_path / "sftp_host_keys.json")
    antwort = _target(ssh, keys).check()
    first = targets.host_key_fingerprint(ssh.key)
    assert first in antwort and len(ssh.logins) == 1
    # Kept on disk: a restart does not forget it, and only the fingerprint is there.
    gespeichert = (tmp_path / "sftp_host_keys.json").read_text()
    assert first in gespeichert and "BEGIN" not in gespeichert and PASSWORT not in gespeichert
    assert HostKeys(tmp_path / "sftp_host_keys.json").get("127.0.0.1", ssh.port)["sha256"] == first

    ssh.key = paramiko.ECDSAKey.generate()            # the "server" was swapped
    for tun in (lambda t: t.check(), lambda t: t.upload({"variant": "web", "kind": "photo", "uid": "a",
                                                          "path": str(tmp_path / "x.jpg"), "meta": {}})):
        with pytest.raises(HostKeyChanged) as e:
            tun(_target(ssh, keys))
        assert "anderen Schlüssel als beim Einrichten" in str(e.value)
        assert PASSWORT not in str(e.value)
    assert len(ssh.logins) == 1, "no user name, no password went to the other key"
    assert _Files.received == []

    bericht = keys.report("127.0.0.1", ssh.port)
    neu = targets.host_key_fingerprint(ssh.key)
    assert bericht["host_key"]["sha256"] == first and bericht["host_key_seen"]["sha256"] == neu
    assert not keys.accept("127.0.0.1", ssh.port, first), "only the key the server showed"
    assert keys.accept("127.0.0.1", ssh.port, neu)
    assert neu in _target(ssh, keys).check() and len(ssh.logins) == 2


def test_another_host_or_port_is_a_first_use_again(ssh, tmp_path):
    keys = HostKeys(tmp_path / "k.json")
    keys.pin("127.0.0.1", ssh.port + 1, "SHA256:" + "A" * 43)     # the old port's key
    assert _target(ssh, keys).check()
    assert keys.get("127.0.0.1", ssh.port)["sha256"] == targets.host_key_fingerprint(ssh.key)


def test_a_refused_login_pins_nothing(ssh, tmp_path):
    keys = HostKeys(tmp_path / "k.json")
    t = SftpTarget({"type": "sftp", "host": "127.0.0.1", "port": ssh.port, "user": "box",
                    "password": "falsch", "timeout": 5}, keys)
    with pytest.raises(targets.PermanentError):
        t.check()
    assert keys.get("127.0.0.1", ssh.port) == {}


def test_the_agent_and_build_extra_targets_share_one_store(tmp_path):
    from photobox_sync.agent import Agent
    from photobox_sync.outbox import Outbox
    keys = HostKeys(tmp_path / "k.json")
    a = Agent(outbox=Outbox(tmp_path / "o.sqlite"), host_keys=keys,
              local_config=lambda: {"targets": [{"type": "sftp", "name": "nas", "host": "h", "user": "u"}]})
    (sftp,) = [t for t in a.targets() if t.name == "nas"]
    assert sftp.host_keys is keys


# ── FTPS: certificate and host name ───────────────────────────

def _certs(tmp_path: Path):
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID

    jetzt = dt.datetime.now(dt.timezone.utc)
    ca_key = ec.generate_private_key(ec.SECP256R1())
    ca_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Test CA")])
    ca = (x509.CertificateBuilder().subject_name(ca_name).issuer_name(ca_name).public_key(ca_key.public_key())
          .serial_number(1).not_valid_before(jetzt - dt.timedelta(days=1)).not_valid_after(jetzt + dt.timedelta(days=2))
          .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
          .add_extension(x509.SubjectKeyIdentifier.from_public_key(ca_key.public_key()), critical=False)
          .add_extension(x509.KeyUsage(digital_signature=True, key_cert_sign=True, crl_sign=True, content_commitment=False,
                                       key_encipherment=False, data_encipherment=False, key_agreement=False,
                                       encipher_only=False, decipher_only=False), critical=True)
          .sign(ca_key, hashes.SHA256()))
    (tmp_path / "ca.pem").write_bytes(ca.public_bytes(serialization.Encoding.PEM))

    def leaf(name, sans):
        k = ec.generate_private_key(ec.SECP256R1())
        c = (x509.CertificateBuilder().subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, sans[0].value
                                                                                  if isinstance(sans[0], x509.DNSName) else "ip")]))
             .issuer_name(ca_name).public_key(k.public_key()).serial_number(x509.random_serial_number())
             .not_valid_before(jetzt - dt.timedelta(days=1)).not_valid_after(jetzt + dt.timedelta(days=2))
             .add_extension(x509.SubjectAlternativeName(sans), critical=False)
             .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()), critical=False)
             .add_extension(x509.ExtendedKeyUsage([x509.oid.ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
             .sign(ca_key, hashes.SHA256()))
        (tmp_path / f"{name}.pem").write_bytes(c.public_bytes(serialization.Encoding.PEM) + k.private_bytes(
            serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
        return tmp_path / f"{name}.pem"

    richtig = leaf("richtig", [x509.DNSName("localhost"), x509.IPAddress(ipaddress.ip_address("127.0.0.1"))])
    falsch = leaf("falsch", [x509.DNSName("wrong.invalid")])
    return tmp_path / "ca.pem", richtig, falsch


class FtpsServer:
    """Just enough FTP over explicit TLS for a login: 220, AUTH TLS, USER/PASS, PBSZ/PROT."""

    def __init__(self, cert: Path):
        self.ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        self.ctx.load_cert_chain(str(cert))
        self.passwords: list = []
        self.handshake_failed = threading.Event()
        self.sock = socket.socket()
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(4)
        self.port = self.sock.getsockname()[1]
        threading.Thread(target=self._one, daemon=True).start()

    def _one(self):
        conn, _ = self.sock.accept()
        conn.sendall(b"220 test\r\n")
        f = conn.makefile("rb")
        if not f.readline().upper().startswith(b"AUTH"):
            return
        conn.sendall(b"234 go\r\n")
        try:
            tls = self.ctx.wrap_socket(conn, server_side=True)
        except (ssl.SSLError, OSError):
            self.handshake_failed.set()
            return
        f = tls.makefile("rb")
        antworten = {b"USER": b"331 pass\r\n", b"PASS": b"230 ok\r\n", b"PBSZ": b"200 ok\r\n",
                     b"PROT": b"200 ok\r\n", b"QUIT": b"221 bye\r\n"}
        while True:
            line = f.readline()
            if not line:
                return
            cmd = line.split(b" ", 1)[0].strip().upper()
            if cmd == b"PASS":
                self.passwords.append(line[5:].strip().decode())
            tls.sendall(antworten.get(cmd, b"200 ok\r\n"))
            if cmd == b"QUIT":
                return

    def close(self):
        self.sock.close()


def _trust(monkeypatch, ca: Path):
    echt = ssl.create_default_context
    # The "system store" knows only the test CA; certifi is added on top and does not know it.
    monkeypatch.setattr(ssl, "create_default_context", lambda *a, **k: echt(cafile=str(ca)))


def test_ftps_refuses_a_certificate_for_another_name_before_the_password(tmp_path, monkeypatch):
    ca, _richtig, falsch = _certs(tmp_path)
    _trust(monkeypatch, ca)
    srv = FtpsServer(falsch)
    t = targets.FtpsTarget({"type": "ftps", "host": "127.0.0.1", "port": srv.port, "user": "box",
                            "password": PASSWORT, "timeout": 5})
    with pytest.raises(targets.TemporaryError) as e:
        t.check()
    # The name, not the chain, is what fails: the same CA signs the right certificate below.
    assert "mismatch" in str(e.value).lower() and "certificate" in str(e.value).lower(), str(e.value)
    assert srv.handshake_failed.wait(5)
    assert srv.passwords == []
    srv.close()


def test_ftps_logs_in_when_certificate_and_name_fit(tmp_path, monkeypatch):
    ca, richtig, _falsch = _certs(tmp_path)
    _trust(monkeypatch, ca)
    srv = FtpsServer(richtig)
    t = targets.FtpsTarget({"type": "ftps", "host": "127.0.0.1", "port": srv.port, "user": "box",
                            "password": PASSWORT, "timeout": 5})
    ftp = t._connect()
    ftp.quit()
    assert srv.passwords == [PASSWORT], "the password went only through the checked TLS channel"
    srv.close()
