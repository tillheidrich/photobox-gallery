#!/usr/bin/env python3
"""Generalprobe: eine echte Box gegen eine echte Galerie, ohne Hardware.

Was hier läuft, ist kein Unit-Test mit Attrappen, sondern der Weg, den ein Gast
am Abend nimmt — nur mit der Mock-Kamera und beides auf diesem Rechner:

    Galerie starten → Event und Box anlegen → Box starten → Durchgang auslösen
    → QR-Seite prüfen, bevor das Bild oben ist → warten, bis der Upload durch ist
    → Bild in der Galerie, Download-Größen, Moderation, Geräte-Protokoll, Aufträge

Gedacht für den Tag vor dem Event und für jeden, der etwas an der Kette
geändert hat. Ein Fehler hier ist ein Fehler, den sonst ein Gast findet.

    python tools/rehearsal.py [--keep]     # --keep lässt die Ordner stehen

Rückgabe 0, wenn alles durchläuft, sonst 1.
"""
from __future__ import annotations

import argparse
import atexit
import json
import os
import secrets
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).resolve().parent.parent
BOOTH_DIR = ROOT / "booth"
SYNC_DIR = ROOT / "sync"

START_TIMEOUT = 40.0          # so lange darf ein Dienst zum Hochfahren brauchen
UPLOAD_TIMEOUT = 60.0         # so lange warten wir auf den Upload in die Galerie
FACTORY_PIN = "2580"          # what a fresh booth ships with; the profile here sets none
BOOTH_PIN = "468213"          # what the rehearsal sets when the booth asks for its own PIN
OFFLINE_SESSIONS = 5          # so viele Durchgänge macht die Box ohne jede Verbindung
SHOTS = 2                     # Collage aus zwei Bildern — deckt mehr ab als ein Einzelfoto


def _corner_hint() -> str:
    """Where the booth menu sits, from the booth's own default (it said "oben links")."""
    sys.path.insert(0, str(BOOTH_DIR))
    from boothcore.config import DEFAULT_PROFILE, admin_corner_hint
    return admin_corner_hint(DEFAULT_PROFILE)


class Rehearsal:
    def __init__(self, keep: bool = False, offen: bool = False):
        self.keep = keep
        # --offen: nach der Probe weiterlaufen lassen, damit jemand selbst
        # durchklicken kann. Das ist der schnellste Weg, die ganze Kette auf
        # einem fremden Rechner (Mac, Linux) anzusehen.
        self.offen = offen
        self.steps: List[Dict[str, Any]] = []
        self.procs: List[subprocess.Popen] = []
        # Each rehearsal is up to 1 GB. A run that was stopped from outside
        # (timeout, closed terminal, SIGKILL) used to leave its folder, and so
        # did every --offen run; the Mac that runs them filled up. The folder
        # carries our pid, the next rehearsal removes folders whose process is
        # gone, and gallery and booth get their temp directory inside it.
        guard = _tmp_guard()
        guard.sweep_stale(Path(os.path.realpath(tempfile.gettempdir())), prefix="photobox-probe-")
        self.tmp = guard.make_owned_dir("photobox-probe-")
        self.gallery_data = self.tmp / "gallery"
        self.booth_data = self.tmp / "booth"
        self.second_folder = self.tmp / "zweite-ablage"
        for d in (self.gallery_data, self.booth_data, self.second_folder, self.tmp / "tmp"):
            d.mkdir(parents=True, exist_ok=True)
        if not keep:
            atexit.register(shutil.rmtree, self.tmp, True)
        self.gallery_port = _free_port()
        self.booth_port = _free_port()
        self.gallery_url = f"http://127.0.0.1:{self.gallery_port}"
        self.booth_url = f"http://127.0.0.1:{self.booth_port}"
        self.event: Dict[str, Any] = {}
        self.api_key = ""
        # A throwaway gallery still needs a way in. Left to itself it would
        # generate a start password and log it to output we swallow, so pick
        # one here and name it when --offen hands the gallery over.
        self.admin_user = "admin"
        self.admin_password = secrets.token_urlsafe(12)

    # ── Ablauf ────────────────────────────────────────────
    def run(self) -> int:
        # Gallery, booth and every helper process inherit this: their temp
        # files land in the work folder and leave with it.
        for k in _tmp_guard().ENV_VARS:
            os.environ[k] = str(self.tmp / "tmp")
        try:
            self.step("Galerie vorbereiten", self.prepare_gallery)
            self.step("Galerie starten", self.start_gallery)
            self.step("Box starten", self.start_booth)
            self.step("Box meldet sich bei der Galerie", self.wait_for_heartbeat)
            self.step("Profil kommt bei der Box an", self.check_profile)
            uid = self.step("Durchgang auslösen", self.run_session)
            self.step("QR-Seite sagt „unterwegs“", lambda: self.check_pending(uid))
            self.step("Upload kommt an", lambda: self.wait_for_media(uid))
            self.step("QR-Seite zeigt das Bild", lambda: self.check_session_page(uid))
            self.step("Download-Größen stimmen", lambda: self.check_downloads(uid))
            self.step("Zweite Ablage hat eine Kopie", self.check_second_folder)
            self.step("GIF und sein Film kommen in Galerie und Ordner an", self.gif_and_film)
            self.step("Collage: Blatt und jedes Einzelbild in der Galerie", lambda: self.collage_singles(uid))
            self.step("Werks-PIN wird gegen eine eigene getauscht", self.check_factory_pin_change)
            self.step("Fehlerprotokoll erreicht die Galerie", self.check_device_logs)
            self.step("Auftrag aus der Ferne (Selbstdiagnose)", self.check_remote_job)
            self.step("Aufnahme löschen wirkt bis in die Galerie", lambda: self.check_delete(uid))
            self.step("Löschen in der Galerie erreicht die Box", self.check_gallery_delete)
            uids = self.step("Ein Abend ohne Netz: Box arbeitet weiter", self.offline_evening)
            self.step("Strom weg, Strom da — immer noch ohne Netz", lambda: self.offline_restart(uids))
            self.step("Netz ist wieder da: alles kommt nach", lambda: self.back_online(uids))
        except StepFailed:
            pass
        finally:
            if self.offen:
                # Zum Anschauen: Beides bleibt stehen, bis jemand Strg+C drückt.
                # Wer die Kette nur prüfen will, lässt --offen weg.
                ergebnis = self.report()
                print("\n── Zum Durchklicken ──")
                print(f"  Box:              {self.booth_url}/")
                print(f"  Verwaltung Box:   {self.booth_url}/  ({_corner_hint()} drücken, PIN {BOOTH_PIN})")
                print(f"  Galerie:          {self.gallery_url}/")
                print(f"  Galerie-Admin:    {self.admin_hint()}")
                print(f"  Beispielbild:     {self.share_url or '— keins'}")
                print("\nStrg+C beendet beides.")
                try:
                    while True:
                        time.sleep(1)
                except KeyboardInterrupt:
                    print("\nWird beendet …")
                self.shutdown()
                return ergebnis
            self.shutdown()
        return self.report()

    def admin_hint(self) -> str:
        return f"{self.gallery_url}/admin  (Benutzer {self.admin_user}, Passwort {self.admin_password})"

    def step(self, name: str, fn):
        started = time.monotonic()
        try:
            value = fn()
        except Exception as e:
            self.steps.append({"name": name, "ok": False, "detail": str(e)[:300],
                               "seconds": round(time.monotonic() - started, 1)})
            raise StepFailed(name) from e
        detail = value if isinstance(value, str) else ""
        self.steps.append({"name": name, "ok": True, "detail": detail,
                           "seconds": round(time.monotonic() - started, 1)})
        return value

    # ── Galerie ───────────────────────────────────────────
    def prepare_gallery(self) -> str:
        """Event und Box anlegen — im selben Prozess, bevor der Server läuft."""
        env = dict(os.environ, DATA_DIR=str(self.gallery_data), SECRET_KEY="probe",
                   ADMIN_USER=self.admin_user, ADMIN_PASSWORD=self.admin_password)
        code = (
            "import json, os, secrets;"
            "from app import create_app, db, ecosystem;"
            "app=create_app();"
            "app.test_client().get('/healthz');"          # legt das Schema an, wie im Betrieb
            "ctx=app.test_request_context(); ctx.push();"
            "ecosystem.init_schema();"
            "eid=db.create_event(name='Generalprobe', slug='generalprobe', upload_token=secrets.token_hex(8));"
            "con=db.get_db(); con.execute('UPDATE events SET published=1 WHERE id=?', (eid,)); con.commit(); con.close();"
            "dev=db.create_device(name='Probe-Box');"
            "ecosystem.save_device_profile(dev['id'], {'modes':['photo','collage','gif'],'print':{'enabled':False},"
            "'gif':{'shots':2}}, eid);"
            "ev=db.get_event_by_id(eid);"
            "print(json.dumps({'event_id':eid,'slug':ev['slug'],'share_key':ecosystem.ensure_share_key(ev),"
            "'device_id':dev['id'],'api_key':dev['api_key']}))"
        )
        out = subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=env,
                             capture_output=True, text=True, timeout=120)
        if out.returncode != 0:
            raise RuntimeError(f"Galerie-Vorbereitung fehlgeschlagen: {out.stderr[-400:]}")
        self.event = json.loads(out.stdout.strip().splitlines()[-1])
        self.api_key = self.event["api_key"]
        return f"Event {self.event['slug']}, Box {self.event['device_id']}"

    def start_gallery(self) -> str:
        env = dict(os.environ, DATA_DIR=str(self.gallery_data), SECRET_KEY="probe",
                   FLASK_APP="run.py", BASE_URL=self.gallery_url)
        self.gallery_proc = subprocess.Popen(
            [sys.executable, "-m", "flask", "run", "--host", "127.0.0.1", "--port", str(self.gallery_port)],
            cwd=ROOT, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.procs.append(self.gallery_proc)
        _wait_for(f"{self.gallery_url}/healthz", START_TIMEOUT)
        return self.gallery_url

    # ── Box ───────────────────────────────────────────────
    def start_booth(self) -> str:
        local = {
            "booth_name": "Probe-Box",
            "camera": {"backend": "mock"},
            "printer": {"backend": "none"},
            "server": {"url": self.gallery_url, "api_key": self.api_key, "verify_tls": False},
            "storage": {"folder": str(self.second_folder), "layout": "event/date", "originals": True},
            "admin_pin": FACTORY_PIN,
            "port": self.booth_port,
            "kiosk": False,
        }
        (self.booth_data / "local.json").write_text(json.dumps(local, indent=2), encoding="utf-8")
        return self._spawn_booth()

    def _spawn_booth(self) -> str:
        """Start the booth process on the data folder as it is — also after a power cut."""
        env = dict(os.environ, BOOTH_DATA=str(self.booth_data), BOOTH_CAMERA="mock",
                   PYTHONPATH=os.pathsep.join([str(BOOTH_DIR), str(SYNC_DIR)]))
        self.booth_proc = subprocess.Popen(
            [sys.executable, "-m", "boothcore", "--no-kiosk", "--port", str(self.booth_port)],
            cwd=BOOTH_DIR, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.procs.append(self.booth_proc)
        _wait_for(f"{self.booth_url}/api/about", START_TIMEOUT)
        return self.booth_url

    def wait_for_heartbeat(self) -> str:
        deadline = time.monotonic() + 40
        while time.monotonic() < deadline:
            state = _json(f"{self.booth_url}/api/state")
            if state.get("sync", {}).get("online"):
                return "online"
            time.sleep(1.0)
        raise RuntimeError("Die Box hat die Galerie nicht erreicht")

    def check_profile(self) -> str:
        state = _json(f"{self.booth_url}/api/state")
        event = state["profile"]["event"]
        if event.get("slug") != self.event["slug"]:
            raise RuntimeError(f"Box kennt das Event nicht: {event}")
        return f"Event {event['name']}, Profil v{state['profile']['version']}"

    # ── Der Durchgang ─────────────────────────────────────
    def run_session(self) -> str:
        session = _json(f"{self.booth_url}/api/session", method="POST",
                        body={"mode": "collage", "format": "4:5"})
        uid = session["uid"]
        needed = max(1, int(session.get("shots_needed") or SHOTS))
        for _ in range(needed):
            _json(f"{self.booth_url}/api/session/{uid}/shot", method="POST", body={}, timeout=40)
        item = _json(f"{self.booth_url}/api/session/{uid}/finish", method="POST",
                     body={"look": "lut-golden-warm"}, timeout=60)
        if not item.get("share_url"):
            raise RuntimeError("Kein QR-Link am Ergebnis")
        self.share_url = item["share_url"]
        return uid

    def check_pending(self, uid: str) -> str:
        """Der QR-Code muss funktionieren, bevor das Bild oben ist — das ist der Kern."""
        body = _text(self.share_url)
        if uid in body or "unterwegs" in body.lower() or "gleich" in body.lower():
            return "Seite antwortet vor dem Upload"
        raise RuntimeError("QR-Seite antwortet nicht sinnvoll, solange das Bild fehlt")

    def wait_for_media(self, uid: str) -> str:
        deadline = time.monotonic() + UPLOAD_TIMEOUT
        while time.monotonic() < deadline:
            counts = _json(f"{self.booth_url}/api/state")["sync"]
            if counts.get("done"):
                return f"{counts['done']} Dateien übertragen"
            time.sleep(1.0)
        raise RuntimeError("Upload kam nicht durch")

    def check_session_page(self, uid: str) -> str:
        body = _text(self.share_url)
        if "img" not in body.lower():
            raise RuntimeError("Auf der QR-Seite ist kein Bild")
        return "Bild sichtbar"

    def check_downloads(self, uid: str) -> str:
        """Jede angebotene Größe muss auch wirklich in dieser Größe herauskommen."""
        import io

        from PIL import Image

        media = self._media_of(uid)
        if not media:
            raise RuntimeError("Kein Medium in der Galerie gefunden")
        media_id = media[0]["id"]
        expected = {"small": 640, "web": 1024, "large": 2048}
        got = {}
        for size, limit in expected.items():
            data = _bytes(f"{self.gallery_url}/gallery/{self.event['slug']}/photo/{media_id}/download?size={size}")
            with Image.open(io.BytesIO(data)) as im:
                got[size] = max(im.size)
            if got[size] > limit:
                raise RuntimeError(f"{size} liefert {got[size]} px statt höchstens {limit}")
        original = _bytes(f"{self.gallery_url}/gallery/{self.event['slug']}/photo/{media_id}/download?size=original")
        with Image.open(io.BytesIO(original)) as im:
            got["original"] = max(im.size)
        if got["original"] < got["large"]:
            raise RuntimeError("Das Original ist kleiner als die große Fassung")
        return ", ".join(f"{k} {v} px" for k, v in got.items())

    def _media_of(self, uid: str) -> List[Dict[str, Any]]:
        env = dict(os.environ, DATA_DIR=str(self.gallery_data), SECRET_KEY="probe")
        code = ("import json;from app import create_app, ecosystem;app=create_app();"
                "app.test_client().get('/healthz');"
                "ctx=app.test_request_context();ctx.push();"
                f"rows=ecosystem.session_media('{uid}');"
                "print(json.dumps([{'id':r['id'],'variant':r['variant'],'kind':r['kind']} for r in rows]))")
        out = subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=env,
                             capture_output=True, text=True, timeout=60)
        if out.returncode != 0:
            raise RuntimeError(out.stderr[-300:])
        return json.loads(out.stdout.strip().splitlines()[-1])

    def gif_and_film(self) -> str:
        """A GIF run: the GIF and its MP4 twin both have to arrive."""
        session = _json(f"{self.booth_url}/api/session", method="POST", body={"mode": "gif", "format": "4:5"})
        uid = session["uid"]
        for _ in range(max(1, int(session.get("shots_needed") or 2))):
            _json(f"{self.booth_url}/api/session/{uid}/shot", method="POST", body={}, timeout=40)
        _json(f"{self.booth_url}/api/session/{uid}/finish", method="POST", body={}, timeout=90)
        deadline = time.monotonic() + UPLOAD_TIMEOUT * 2
        kinds: set = set()
        while time.monotonic() < deadline:
            kinds = {m["kind"] for m in self._media_of(uid)}
            if {"gif", "video"} <= kinds:
                break
            time.sleep(2.0)
        if not {"gif", "video"} <= kinds:
            raise RuntimeError(f"In der Galerie liegt zu diesem GIF nur: {sorted(kinds) or 'nichts'}")
        if not list(self.second_folder.rglob("*.mp4")) or not list(self.second_folder.rglob("*.gif")):
            raise RuntimeError("GIF oder Film fehlt in der zweiten Ablage")
        return "GIF und Film in der Galerie und im Ordner"

    def collage_singles(self, uid: str) -> str:
        """By default a collage sends its sheet and every single picture."""
        deadline = time.monotonic() + UPLOAD_TIMEOUT
        kinds: list = []
        while time.monotonic() < deadline:
            kinds = [m["kind"] for m in self._media_of(uid) if m["variant"] == "web"]
            if "collage" in kinds and kinds.count("photo") >= 1:
                return f"Blatt und {kinds.count('photo')} Einzelbilder"
            time.sleep(2.0)
        raise RuntimeError(f"Zur Collage liegt in der Galerie nur: {sorted(kinds) or 'nichts'}")

    def check_second_folder(self) -> str:
        copies = list(self.second_folder.rglob("*.jpg"))
        if not copies:
            raise RuntimeError("Keine Kopie in der zweiten Ablage")
        return f"{len(copies)} Dateien"

    def admin_token(self) -> str:
        return _json(f"{self.booth_url}/api/admin/login", method="POST", body={"pin": BOOTH_PIN})["token"]

    def check_factory_pin_change(self) -> str:
        """A fresh booth lets the factory PIN in only to choose a new one."""
        login = _json(f"{self.booth_url}/api/admin/login", method="POST", body={"pin": FACTORY_PIN})
        if not login.get("must_change_pin"):
            raise RuntimeError("Anmeldung mit der Werks-PIN verlangt keine neue PIN")
        head = {"X-Admin-Token": login["token"]}
        import httpx
        if httpx.get(f"{self.booth_url}/api/admin/config", headers=head, timeout=10).status_code != 403:
            raise RuntimeError("Mit der Werks-PIN ist die Verwaltung offen")
        _json(f"{self.booth_url}/api/admin/pin", method="POST", body={"new_pin": BOOTH_PIN}, headers=head)
        _json(f"{self.booth_url}/api/admin/config", headers=head)
        if httpx.post(f"{self.booth_url}/api/admin/login", json={"pin": FACTORY_PIN}, timeout=10).status_code != 401:
            raise RuntimeError("Die Werks-PIN gilt nach dem Wechsel noch")
        return f"neue PIN {BOOTH_PIN} gilt, 2580 nicht mehr"

    def check_device_logs(self) -> str:
        """Eine Warnung auf der Box muss im Admin-Interface ankommen."""
        token = self.admin_token()
        # Ohne Drucker schlägt der Testdruck fehl — genau darum geht es: Die
        # Meldung muss den Weg in die Galerie finden, nicht nur ins Protokoll.
        _json(f"{self.booth_url}/api/admin/action/test_print", method="POST", body={},
              headers={"X-Admin-Token": token}, allow_error=True)
        _json(f"{self.booth_url}/api/admin/action/sync_now", method="POST", body={},
              headers={"X-Admin-Token": token}, timeout=40)
        time.sleep(1.5)
        env = dict(os.environ, DATA_DIR=str(self.gallery_data), SECRET_KEY="probe")
        code = ("import json;from app import create_app, ecosystem;app=create_app();"
                "app.test_client().get('/healthz');"
                "ctx=app.test_request_context();ctx.push();"
                f"print(json.dumps(len(ecosystem.device_logs({self.event['device_id']}, limit=50))))")
        out = subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=env,
                             capture_output=True, text=True, timeout=60)
        count = int(out.stdout.strip().splitlines()[-1]) if out.returncode == 0 else 0
        if count < 1:
            raise RuntimeError("Keine Meldung in der Galerie angekommen")
        return f"{count} Meldungen"

    def check_remote_job(self) -> str:
        """Die Galerie schickt „Box prüfen“, die Box antwortet mit dem Befund."""
        env = dict(os.environ, DATA_DIR=str(self.gallery_data), SECRET_KEY="probe")
        code = ("from app import create_app, ecosystem;app=create_app();"
                "app.test_client().get('/healthz');"
                "ctx=app.test_request_context();ctx.push();"
                f"print(ecosystem.add_job({self.event['device_id']}, 'doctor'))")
        subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=env, capture_output=True,
                       text=True, timeout=60, check=True)
        token = self.admin_token()
        _json(f"{self.booth_url}/api/admin/action/sync_now", method="POST", body={},
              headers={"X-Admin-Token": token}, timeout=40)
        time.sleep(2.0)
        _json(f"{self.booth_url}/api/admin/action/sync_now", method="POST", body={},
              headers={"X-Admin-Token": token}, timeout=40)
        code = ("import json;from app import create_app, ecosystem;app=create_app();"
                "app.test_client().get('/healthz');"
                "ctx=app.test_request_context();ctx.push();"
                f"jobs=ecosystem.recent_jobs({self.event['device_id']}, 10);"
                "print(json.dumps([{'type':j['type'],'ok':j['ok'],"
                "'result':(j['result'] or '')[:160]} for j in jobs]))")
        out = subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=env,
                             capture_output=True, text=True, timeout=60)
        jobs = json.loads(out.stdout.strip().splitlines()[-1]) if out.returncode == 0 else []
        done = [j for j in jobs if j["type"] == "doctor" and j["ok"]]
        if not done:
            raise RuntimeError(f"Auftrag nicht erledigt: {jobs}")
        return done[0]["result"] or "ohne Befund"

    def check_delete(self, uid: str) -> str:
        _json(f"{self.booth_url}/api/session/{uid}", method="DELETE")
        token = self.admin_token()
        _json(f"{self.booth_url}/api/admin/action/sync_now", method="POST", body={},
              headers={"X-Admin-Token": token}, timeout=40)
        time.sleep(1.5)
        env = dict(os.environ, DATA_DIR=str(self.gallery_data), SECRET_KEY="probe")
        code = ("import json;from app import create_app, ecosystem;app=create_app();"
                "app.test_client().get('/healthz');"
                "ctx=app.test_request_context();ctx.push();"
                f"print(json.dumps(len(ecosystem.session_media('{uid}'))))")
        out = subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=env,
                             capture_output=True, text=True, timeout=60)
        left = int(out.stdout.strip().splitlines()[-1]) if out.returncode == 0 else -1
        if left != 0:
            raise RuntimeError(f"In der Galerie liegen noch {left} Dateien")
        return "auch in der Galerie weg"

    def check_gallery_delete(self) -> str:
        """The other direction: the guest deletes on the QR page, the booth follows.

        A fresh run (the first one is already gone), wait until it is in the
        gallery, delete it the way a guest does, then two syncs: the first
        brings the list to the booth, the second carries the confirmation.
        """
        uid = self.run_session()
        deadline = time.monotonic() + UPLOAD_TIMEOUT
        while not self._media_of(uid):
            if time.monotonic() > deadline:
                raise RuntimeError("Der zweite Durchgang kam nicht in der Galerie an")
            time.sleep(1.0)
        if uid not in json.dumps(_json(f"{self.booth_url}/api/gallery")):
            raise RuntimeError("Der Durchgang fehlt schon vorher in der Galerie an der Box")
        import httpx
        from urllib.parse import urlsplit
        page = urlsplit(self.share_url).path
        httpx.post(f"{self.gallery_url}{page}/delete", timeout=20).raise_for_status()
        token = self.admin_token()
        for _ in range(2):
            _json(f"{self.booth_url}/api/admin/action/sync_now", method="POST", body={},
                  headers={"X-Admin-Token": token}, timeout=40)
            time.sleep(1.0)
        if uid in json.dumps(_json(f"{self.booth_url}/api/gallery")):
            raise RuntimeError("Die Box zeigt den gelöschten Durchgang noch")
        env = dict(os.environ, DATA_DIR=str(self.gallery_data), SECRET_KEY="probe")
        code = ("from app import create_app, ecosystem;app=create_app();"
                "app.test_client().get('/healthz');"
                "ctx=app.test_request_context();ctx.push();"
                f"print('{uid}' in ecosystem.deletions_for_device({self.event['device_id']}))")
        out = subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=env,
                             capture_output=True, text=True, timeout=60)
        if out.returncode != 0 or out.stdout.strip().splitlines()[-1] != "False":
            raise RuntimeError("Die Galerie hat keine Bestätigung der Box bekommen")
        return "von der Box entfernt und bestätigt"

    # ── Aufräumen und Bericht ─────────────────────────────
    # ── Ein ganzer Abend ohne Netz ────────────────────────
    def _stop(self, proc: subprocess.Popen) -> None:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=10)
        if proc in self.procs:
            self.procs.remove(proc)

    def _pending(self) -> int:
        return int(_json(f"{self.booth_url}/api/state")["sync"].get("pending") or 0)

    def offline_evening(self) -> List[str]:
        """The venue has no reception at all: the gallery is gone before the first guest."""
        self._stop(self.gallery_proc)
        uids = []
        for _ in range(OFFLINE_SESSIONS):
            session = _json(f"{self.booth_url}/api/session", method="POST",
                            body={"mode": "collage", "format": "4:5"}, timeout=40)
            uid = session["uid"]
            for _ in range(max(1, int(session.get("shots_needed") or SHOTS))):
                _json(f"{self.booth_url}/api/session/{uid}/shot", method="POST", body={}, timeout=40)
            item = _json(f"{self.booth_url}/api/session/{uid}/finish", method="POST",
                         body={"look": "original"}, timeout=60)
            if not item.get("share_url"):
                raise RuntimeError("Ohne Netz gab es keinen QR-Link am Ergebnis")
            uids.append(uid)
        # The count covers every target. Copies to the second folder still
        # finish without a network, so wait until only the gallery is left.
        self.offline_pending, ruhig = self._pending(), 0
        deadline = time.monotonic() + UPLOAD_TIMEOUT
        while ruhig < 3 and time.monotonic() < deadline:
            time.sleep(1.0)
            jetzt = self._pending()
            ruhig = ruhig + 1 if jetzt == self.offline_pending else 0
            self.offline_pending = jetzt
        if self.offline_pending < OFFLINE_SESSIONS:
            raise RuntimeError(f"Nur {self.offline_pending} Dateien warten auf den Upload")
        return uids

    def offline_restart(self, uids: List[str]) -> str:
        """Somebody pulls the plug at the end of the night and the booth goes home."""
        self.booth_proc.kill()                        # no clean shutdown — a pulled plug
        self.booth_proc.wait(timeout=10)
        self.procs.remove(self.booth_proc)
        self._spawn_booth()
        gallery = _json(f"{self.booth_url}/api/gallery")
        known = json.dumps(gallery)
        missing = [u for u in uids if u not in known]
        if missing:
            raise RuntimeError(f"Nach dem Neustart fehlen Aufnahmen an der Box: {missing}")
        pending = self._pending()
        if pending < self.offline_pending:
            raise RuntimeError(f"Warteschlange geschrumpft: {self.offline_pending} → {pending}")
        return f"{len(uids)} Aufnahmen da, {pending} Dateien warten weiter"

    def back_online(self, uids: List[str]) -> str:
        self.start_gallery()
        deadline = time.monotonic() + UPLOAD_TIMEOUT * 2
        while time.monotonic() < deadline:
            if self._pending() == 0:
                break
            time.sleep(1.0)
        else:
            raise RuntimeError(f"Nach {int(UPLOAD_TIMEOUT * 2)} s warten noch {self._pending()} Dateien")
        missing = [u for u in uids if not self._media_of(u)]
        if missing:
            raise RuntimeError(f"In der Galerie fehlen Aufnahmen: {missing}")
        return f"{len(uids)} Durchgänge vollständig in der Galerie"

    def shutdown(self) -> None:
        for p in self.procs:
            p.terminate()
        for p in self.procs:
            try:
                p.wait(timeout=10)
            except subprocess.TimeoutExpired:
                p.kill()
        if not self.keep:
            shutil.rmtree(self.tmp, ignore_errors=True)
        else:
            # Kept on purpose (--keep): without the pid mark the next
            # rehearsal leaves it alone.
            (self.tmp / _tmp_guard().OWNER).unlink(missing_ok=True)

    def report(self) -> int:
        print()
        for s in self.steps:
            mark = "OK  " if s["ok"] else "X   "
            detail = f" — {s['detail']}" if s["detail"] else ""
            print(f"{mark}{s['name']}{detail}  [{s['seconds']} s]")
        failed = [s for s in self.steps if not s["ok"]]
        print()
        if failed:
            print(f"Generalprobe gescheitert bei: {failed[0]['name']}")
            if self.keep:
                print(f"Ordner bleibt stehen: {self.tmp}")
            return 1
        print(f"Generalprobe bestanden — {len(self.steps)} Schritte.")
        return 0


class StepFailed(Exception):
    pass


# ── kleine Helfer ─────────────────────────────────────────
def _tmp_guard():
    """tools/tmp_guard.py, loaded by path (tools/ is not a package)."""
    import importlib.util
    if "photobox_tmp_guard" in sys.modules:
        return sys.modules["photobox_tmp_guard"]
    spec = importlib.util.spec_from_file_location("photobox_tmp_guard", ROOT / "tools" / "tmp_guard.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules["photobox_tmp_guard"] = module
    spec.loader.exec_module(module)
    return module


def _stop_cleanly_on_terminate() -> None:
    """SIGTERM/SIGHUP (a timeout, a closed terminal) end the run like Ctrl+C.

    Without this the finally block never ran: gallery and booth kept running
    and the work folder stayed.
    """
    import signal

    def interrupt(signum, frame):
        raise KeyboardInterrupt

    for name in ("SIGTERM", "SIGHUP"):
        if hasattr(signal, name):
            signal.signal(getattr(signal, name), interrupt)


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _wait_for(url: str, timeout: float) -> None:
    import httpx
    deadline = time.monotonic() + timeout
    last = ""
    while time.monotonic() < deadline:
        try:
            if httpx.get(url, timeout=3.0).status_code < 500:
                return
        except Exception as e:
            last = str(e)
        time.sleep(0.5)
    raise RuntimeError(f"{url} kam nicht hoch ({last})")


def _json(url: str, method: str = "GET", body: Optional[dict] = None,
          headers: Optional[dict] = None, timeout: float = 20.0, allow_error: bool = False) -> dict:
    import httpx
    r = httpx.request(method, url, json=body, headers=headers or {}, timeout=timeout)
    if not allow_error:
        r.raise_for_status()
    return r.json() if r.content else {}


def _bytes(url: str, timeout: float = 30.0) -> bytes:
    import httpx
    r = httpx.get(url, timeout=timeout, follow_redirects=True)
    r.raise_for_status()
    return r.content


def _text(url: str, timeout: float = 20.0) -> str:
    import httpx
    r = httpx.get(url, timeout=timeout, follow_redirects=True)
    r.raise_for_status()
    return r.text


def main() -> int:
    ap = argparse.ArgumentParser(description="Generalprobe: Box gegen Galerie, ohne Hardware")
    ap.add_argument("--keep", action="store_true", help="Arbeitsordner stehen lassen (zum Nachsehen)")
    ap.add_argument("--offen", action="store_true",
                    help="Galerie und Box danach weiterlaufen lassen und die Adressen nennen "
                         "— zum Anschauen und Durchklicken auf diesem Rechner")
    args = ap.parse_args()
    print("Generalprobe läuft — Galerie und Box werden gestartet, das dauert etwa eine Minute.")
    _stop_cleanly_on_terminate()
    # --offen keeps everything running until Ctrl+C, then removes the folder
    # like any other run; only --keep leaves it for a look afterwards.
    return Rehearsal(keep=args.keep, offen=args.offen).run()


if __name__ == "__main__":
    raise SystemExit(main())
