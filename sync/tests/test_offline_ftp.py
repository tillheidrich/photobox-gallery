"""Der Fall, für den dieses Programm da ist: kein Internet, dann kurz ein Hotspot.

Getestet wird gegen einen echten FTP-Server im selben Prozess — nicht gegen eine
Attrappe. Der Server geht mittendrin aus und wieder an, so wie ein Hotspot, den
jemand am Eventabend für zwei Minuten aufmacht.
"""
import threading
import time
from pathlib import Path

import pytest

from photobox_sync.outbox import Outbox
from photobox_sync.targets import PermanentError, TemporaryError, build_extra_targets


class Server:
    """Ein FTP-Server auf einem freien Port, an- und abschaltbar."""

    def __init__(self, root: Path, user="box", password="geheim"):
        from pyftpdlib.authorizers import DummyAuthorizer
        from pyftpdlib.handlers import FTPHandler
        from pyftpdlib.servers import FTPServer

        root.mkdir(parents=True, exist_ok=True)
        self.root = root
        auth = DummyAuthorizer()
        auth.add_user(user, password, str(root), perm="elradfmwMT")
        handler = FTPHandler
        handler.authorizer = auth
        handler.banner = "photobox test"
        handler.auth_failed_timeout = 0      # sonst wartet der Server 5 s, bevor er Nein sagt
        self.server = FTPServer(("127.0.0.1", 0), handler)
        self.port = self.server.socket.getsockname()[1]
        self._thread = None

    def start(self):
        self._thread = threading.Thread(target=self.server.serve_forever, kwargs={"timeout": 0.1}, daemon=True)
        self._thread.start()
        time.sleep(0.2)

    def stop(self):
        self.server.close_all()
        if self._thread:
            self._thread.join(timeout=3)

    def files(self):
        return sorted(p.name for p in self.root.rglob("*") if p.is_file())


@pytest.fixture()
def shot(tmp_path):
    def make(name="IMG_0001.jpg", content=b"\xff\xd8bild\xff\xd9"):
        p = tmp_path / name
        p.write_bytes(content)
        return p
    return make


def test_a_plain_ftp_server_receives_the_file(tmp_path, shot):
    """Viele NAS und Webspaces können kein TLS. Vorher landete „ftp" auf dem
    TLS-Weg und scheiterte mit einer Meldung über Zertifikate."""
    srv = Server(tmp_path / "remote")
    srv.start()
    try:
        target = build_extra_targets([{"type": "ftp", "name": "nas", "host": "127.0.0.1",
                                       "port": srv.port, "user": "box", "password": "geheim",
                                       "flat": True}])[0]
        target.upload({"path": str(shot()), "variant": "web", "uid": "abc", "kind": "photo",
                       "meta": {"original_name": "IMG_0001.jpg"}})
        # Die Kennung des Durchgangs steht vorn, damit zwei „IMG_0001.jpg" aus
        # verschiedenen Durchgängen einander nicht überschreiben.
        assert srv.files() == ["abc_IMG_0001.jpg"]
    finally:
        srv.stop()


def test_nothing_is_lost_while_the_network_is_down(tmp_path, shot):
    """Der eigentliche Zweck: Aufnahmen warten, bis wieder Netz da ist."""
    outbox = Outbox(tmp_path / "outbox.sqlite")
    remote = tmp_path / "remote"
    srv = Server(remote)
    cfg = {"type": "ftp", "name": "nas", "host": "127.0.0.1", "port": srv.port,
           "user": "box", "password": "geheim", "flat": True, "timeout": 2}

    # Drei Aufnahmen, während kein Server läuft.
    for i in range(3):
        outbox.add(target="nas", uid=f"u{i}", session_uid=f"u{i}", event_slug="fest", kind="photo",
                   variant="web", path=shot(f"IMG_{i}.jpg"), poster=None,
                   meta={"original_name": f"IMG_{i}.jpg"}, priority=2)

    target = build_extra_targets([cfg])[0]
    failures = 0
    for item in outbox.due(limit=10):
        try:
            target.upload(item)
        except (TemporaryError, OSError):
            failures += 1
            outbox.mark_retry(item["id"], "kein Netz")
    assert failures == 3, "ohne Server muss jeder Versuch vorübergehend scheitern"
    assert outbox.counts()["pending"] == 3, "nichts darf verloren gehen"

    # Der Hotspot geht an.
    srv.start()
    try:
        # Sobald wieder Netz da ist, zählt jede Sekunde: der Backoff aller
        # Einträge wird zurückgesetzt, damit das kurze Fenster reicht.
        outbox.reset_backoff()
        sent = 0
        for item in outbox.due(limit=10):
            target.upload(item)
            outbox.mark_done(item["id"])
            sent += 1
        assert sent == 3
        assert srv.files() == ["u0_IMG_0.jpg", "u1_IMG_1.jpg", "u2_IMG_2.jpg"]
        assert outbox.counts()["pending"] == 0
    finally:
        srv.stop()


def test_a_wrong_password_is_permanent_not_temporary(tmp_path, shot):
    """Sonst versucht die Warteschlange es ewig weiter und verdeckt den Fehler."""
    srv = Server(tmp_path / "remote")
    srv.start()
    try:
        target = build_extra_targets([{"type": "ftp", "name": "nas", "host": "127.0.0.1",
                                       "port": srv.port, "user": "box", "password": "falsch"}])[0]
        with pytest.raises(PermanentError):
            target.upload({"path": str(shot()), "variant": "web", "uid": "abc", "kind": "photo",
                           "meta": {"original_name": "IMG_0001.jpg"}})
    finally:
        srv.stop()


def test_subfolders_are_created_when_asked(tmp_path, shot):
    srv = Server(tmp_path / "remote")
    srv.start()
    try:
        target = build_extra_targets([{"type": "ftp", "name": "nas", "host": "127.0.0.1",
                                       "port": srv.port, "user": "box", "password": "geheim",
                                       "flat": False, "path": "fotobox"}])[0]
        target.upload({"path": str(shot()), "variant": "web", "uid": "abc", "kind": "photo",
                       "event_slug": "fest", "meta": {"original_name": "IMG_0001.jpg"}})
        assert (tmp_path / "remote" / "fotobox" / "fest").is_dir()
        assert srv.files() == ["abc_IMG_0001.jpg"]
    finally:
        srv.stop()


def test_two_jobs_land_in_two_different_places(tmp_path, shot):
    """Der eigentliche Grund für mehrere Aufträge: Drucke und Originale gehören
    an verschiedene Stellen. Beide Wege laufen hier gegen einen echten Server —
    was am falschen Ort landet, fällt auf."""
    from photobox_sync.standalone import SyncApp

    srv = Server(tmp_path / "srv"); srv.start()
    try:
        app = SyncApp(base=tmp_path / "data", start_threads=False)
        ziel = {"type": "ftp", "host": "127.0.0.1", "port": srv.port, "user": "box",
                "password": "geheim", "flat": True, "timeout": 2}
        app.save_config({"server": {"url": "", "api_key": ""}, "upload_to_gallery": False,
                         "targets": [{**ziel, "name": "drucke", "flat": False, "path": "drucke"},
                                     {**ziel, "name": "archiv", "flat": False, "path": "archiv"}]})
        app.on_file(shot("print_1.jpg"), "p1", "print", {"path": "C:/x/Prints", "name": "Drucke", "targets": ["drucke"]})
        app.on_file(shot("orig_1.jpg"), "o1", "original", {"path": "C:/x/Originals", "name": "Originale", "targets": ["archiv"]})

        by_name = {t.name: t for t in app.agent.targets()}
        for item in app.outbox.due(10):
            by_name[item["target"]].upload(item)
            app.outbox.mark_done(item["id"])

        landed = sorted(str(f.relative_to(srv.root)).replace("\\", "/") for f in srv.root.rglob("*") if f.is_file())
        assert [f.split("/")[0] for f in landed] == ["archiv", "drucke"]
        assert landed[0].endswith("p1_print_1.jpg") is False and landed[0].endswith("o1_orig_1.jpg")
        assert landed[1].endswith("p1_print_1.jpg")
    finally:
        srv.stop()


def test_checking_a_target_says_plainly_what_is_wrong(tmp_path):
    """Vor Ort zählt eine Frage: nimmt der Server die Zugangsdaten an?"""
    from fastapi.testclient import TestClient

    from photobox_sync.standalone import SyncApp, create_app

    srv = Server(tmp_path / "srv"); srv.start()
    try:
        app = SyncApp(base=tmp_path / "data", start_threads=False)
        gut = {"type": "ftp", "name": "gut", "host": "127.0.0.1", "port": srv.port,
               "user": "box", "password": "geheim", "flat": True, "timeout": 2}
        app.save_config({"server": {"url": "", "api_key": ""}, "upload_to_gallery": False,
                         "targets": [gut, {**gut, "name": "tippfehler", "password": "falsch"},
                                     {**gut, "name": "aus", "port": 1}]})
        res = {t["name"]: t for t in TestClient(create_app(app)).get("/api/check_targets").json()["targets"]}
        assert res["gut"]["ok"] is True
        assert res["tippfehler"]["ok"] is False and "abgelehnt" in res["tippfehler"]["detail"]
        assert res["aus"]["ok"] is False
    finally:
        srv.stop()


def test_a_speed_limit_still_delivers_the_whole_file(tmp_path, shot):
    """Gebremst heißt langsamer, nicht unvollständig — und die Größe wird geprüft."""
    import time as clock

    from photobox_sync.outbox import Outbox

    srv = Server(tmp_path / "srv"); srv.start()
    try:
        big = shot("video.mp4", b"\x00" * 300_000)
        ob = Outbox(tmp_path / "o.sqlite")
        ob.add(target="nas", uid="v1", session_uid="s", event_slug="fest", kind="video",
               variant="web", path=big)
        item = ob.due(1)[0]
        target = build_extra_targets([{"type": "ftp", "name": "nas", "host": "127.0.0.1", "port": srv.port,
                                       "user": "box", "password": "geheim", "flat": True,
                                       "timeout": 5, "limit_kbps": 150}])[0]
        started = clock.monotonic()
        out = target.upload(item)
        took = clock.monotonic() - started

        assert out["bytes"] == 300_000
        assert (srv.root / "v1_video.mp4").stat().st_size == 300_000
        assert took > 1.0, took          # 300 KB bei 150 KB/s dauern spürbar
    finally:
        srv.stop()
