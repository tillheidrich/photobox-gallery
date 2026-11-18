"""Zusätzliche Ziele: wohin eine Datei tatsächlich geschrieben wird.

Der Ort ist bei fremden Diensten kein Detail. Eine Online-Galerie per FTP zum Beispiel ordnet
Bilder selbst einer Galerie zu und erwartet sie im Wurzelverzeichnis — was in
einem Unterordner landet, sieht dort niemand wieder.
"""
# ── Online-Galerie per FTP: alles in den Wurzelordner ─────────────────────────────────
class _FakeSftp:
    """Ein SFTP-Kanal, der nur mitschreibt, wohin geschrieben würde."""

    def __init__(self):
        self.angelegt = []
        self.geschrieben = None

    def stat(self, pfad):
        if pfad.endswith(".part"):
            import types
            return types.SimpleNamespace(st_size=10)
        raise OSError("gibt es nicht")

    def mkdir(self, pfad):
        self.angelegt.append(pfad)

    def putfo(self, f, remote, **kw):
        self.geschrieben = remote

    def stat_groesse(self):
        return 10

    def rename(self, a, b):
        self.geschrieben = b

    def remove(self, pfad):
        pass


def _ziel_pfad(cfg, monkeypatch, tmp_path):
    """Einen Upload trocken durchspielen und den Zielpfad zurückgeben."""
    from photobox_sync import targets as t

    datei = tmp_path / "foto.jpg"
    datei.write_bytes(b"x" * 10)
    kanal = _FakeSftp()

    import types

    class _Transport:
        """Key exchange, host key, password login — what SftpTarget asks since 17.11.2026."""

        def close(self):
            pass

        def start_client(self, timeout=None):
            pass

        def get_remote_server_key(self):
            return types.SimpleNamespace(asbytes=lambda: b"test-key", get_name=lambda: "ssh-ed25519")

        def auth_password(self, user, password):
            pass

        def is_authenticated(self):
            return True

    fake = types.SimpleNamespace(
        Transport=lambda adr: _Transport(),
        SSHException=Exception,
        AuthenticationException=type("AuthenticationException", (Exception,), {}),
        Ed25519Key=types.SimpleNamespace(from_private_key_file=lambda p: None),
        SFTPClient=types.SimpleNamespace(from_transport=lambda tr: kanal),
    )
    monkeypatch.setitem(__import__("sys").modules, "paramiko", fake)
    ziel = t.SftpTarget({**cfg, "host": "h", "user": "u", "password": "p"})
    ergebnis = ziel.upload({"uid": "abc", "variant": "web", "event_slug": "fest",
                            "meta": "{}", "path": str(datei), "kind": "photo"})
    return ergebnis["remote"]


def test_sftp_puts_everything_in_the_root_when_asked(monkeypatch, tmp_path):
    """Eine Online-Galerie per FTP ordnet selbst zu und will die Bilder im Wurzelverzeichnis.

    Bisher tat das nur FTPS; über SFTP landeten die Bilder in Unterordnern
    je Event und Tag — und damit dort, wo sie nicht nachsieht.
    """
    pfad = _ziel_pfad({"flat": True, "path": "/"}, monkeypatch, tmp_path)
    assert pfad and pfad.count("/") == 1, f"Kein Unterordner erwartet, war: {pfad}"


def test_sftp_still_sorts_into_folders_when_that_is_wanted(monkeypatch, tmp_path):
    pfad = _ziel_pfad({"flat": False, "path": "/photobox"}, monkeypatch, tmp_path)
    assert "/photobox/fest/" in pfad


# --- review of 14 Nov 2026 ---
import pytest

@pytest.mark.parametrize("status, kind", [(401, "temporary"), (403, "temporary"), (408, "temporary"),
                                          (404, "permanent"), (413, "permanent")])
def test_a_refused_key_stalls_the_queue_instead_of_spending_the_files(tmp_path, status, kind):
    """A key changed at the gallery answered 401 for every file; after eight
    tries each one was "failed" for good and nothing went up by itself once
    the key was right again. A wrong key is the target's trouble, not the file's."""
    import httpx
    from photobox_sync.targets import GalleryTarget, PermanentError, TemporaryError
    f = tmp_path / "a.jpg"
    f.write_bytes(b"x" * 10)
    t = GalleryTarget("https://gallery.invalid", "key")
    t.client = httpx.Client(transport=httpx.MockTransport(lambda req: httpx.Response(status, text="no")))
    item = {"path": str(f), "uid": "u", "session_uid": "s", "event_slug": "e", "kind": "photo", "variant": "web"}
    with pytest.raises(TemporaryError if kind == "temporary" else PermanentError):
        t.upload(item)


def test_a_target_switched_off_is_kept_but_not_used():
    from photobox_sync.targets import build_extra_targets
    cfgs = [{"type": "ftps", "name": "picdrop", "host": "h", "enabled": False}, {"type": "ftps", "name": "an", "host": "h"}]
    assert [t.name for t in build_extra_targets(cfgs)] == ["an"]
