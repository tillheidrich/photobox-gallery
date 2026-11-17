#!/usr/bin/env python3
"""A whole event day in fast motion, with the faults a day without the operator brings.

    python tools/soak_faults.py --runs 600 --out /tmp/soak-tag

Starts a real gallery and a booth from ``tools/soak_booth.py`` (mock camera
that can fail, a fake SELPHY with an 18-sheet tray that runs empty, no real
printer anywhere) and walks guests through the booth's own HTTP interface:
photo, collage and GIF in turn, a print every second run. On a schedule
it injects what a venue does to a booth:

* a shot that fails, a camera that is unplugged for a while and comes back
* the paper tray running empty every 18 sheets; a helper refills it after a
  few runs but presses no button (the host does not know the PIN)
* the printer switched off for a while
* the gallery unreachable for a long stretch (venue Wi-Fi gone)
* the disk reported nearly full
* the booth process killed mid-run and started again (power cut)
* two starts at the same moment, runs abandoned half way, deleted runs
* live-view clients (live.jpg, live.mjpg) connecting and dropping all along

Every run is a CSV line; every tenth carries the process numbers (memory,
threads, open files, data folder, log). At the end a summary: runs, faults,
growth, pictures lost, prints that wait for a human. Exit code 1 when a
picture was lost or something is stuck that a layperson could not clear.

Simulation only: the camera and the printer are fakes. What a real EOS M50
or SELPHY does is for the device test plan, not for this script.
"""
from __future__ import annotations

import argparse
import csv
import importlib.util
import json
import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Dict, List, Optional

import httpx

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("rehearsal", ROOT / "tools" / "rehearsal.py")
rehearsal = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(rehearsal)

MODES = ("photo", "collage", "gif")


class Soak(rehearsal.Rehearsal):
    def __init__(self, runs: int, out: Path, refill_after: float = 0.0, silent_printer: bool = False,
                 pace: float = 0.0, print_while_empty: bool = False):
        super().__init__(keep=True)
        self.runs = runs
        #: Profile print.queue_while_empty on: a print asked for while the
        #: tray is empty is accepted and must come out later — once.
        self.print_while_empty = print_while_empty
        #: uid -> copies the booth accepted; refusals by reason.
        self.accepted: Dict[str, int] = {}
        self.deferred_accepted = 0
        self.refused: Dict[str, int] = {}
        self.refill_after = refill_after
        self.pace = pace
        self.out = out
        out.mkdir(parents=True, exist_ok=True)
        self.faults_file = self.booth_data / "faults.json"
        self.faults: Dict = {"tray_refills": 0}
        if silent_printer:
            self.faults["printer_reports_paper"] = False
        self._write_faults()
        self.injected: Dict[str, int] = {}
        self.finished: List[str] = []
        self.deleted: List[str] = []
        self.errors: List[str] = []
        self.paper_out_since: Optional[int] = None
        self.stop_clients = threading.Event()
        self.client_stats = {"mjpg": 0, "jpg": 0, "errors": 0}
        self.http = httpx.Client(timeout=90)

    # ── faults ────────────────────────────────────────────
    def _write_faults(self) -> None:
        tmp = self.faults_file.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.faults), encoding="utf-8")
        os.replace(tmp, self.faults_file)

    def fault(self, name: str, **values) -> None:
        self.faults.update(values)
        self._write_faults()
        self.injected[name] = self.injected.get(name, 0) + 1

    # ── booth ─────────────────────────────────────────────
    def start_booth(self) -> str:
        local = {
            "booth_name": "Soak-Box",
            "camera": {"backend": "mock"},
            "printers": [{"backend": "soak", "name": "Soak SELPHY", "model": "selphy-cp"}],
            "server": {"url": self.gallery_url, "api_key": self.api_key, "verify_tls": False},
            "admin_pin": rehearsal.FACTORY_PIN,
            "port": self.booth_port,
            "kiosk": False,
        }
        (self.booth_data / "local.json").write_text(json.dumps(local, indent=2), encoding="utf-8")
        return self._spawn_booth()

    def _spawn_booth(self) -> str:
        env = dict(os.environ, BOOTH_DATA=str(self.booth_data), BOOTH_CAMERA="mock",
                   SOAK_FAULTS=str(self.faults_file),
                   PYTHONPATH=os.pathsep.join([str(rehearsal.BOOTH_DIR), str(rehearsal.SYNC_DIR)]))
        log = open(self.out / "booth-stdout.log", "ab")
        self.booth_proc = subprocess.Popen(
            [sys.executable, str(ROOT / "tools" / "soak_booth.py"), "--no-kiosk", "--port", str(self.booth_port)],
            cwd=rehearsal.BOOTH_DIR, env=env, stdout=log, stderr=subprocess.STDOUT)
        self.procs.append(self.booth_proc)
        rehearsal._wait_for(f"{self.booth_url}/api/about", rehearsal.START_TIMEOUT)
        return self.booth_url

    def prepare_gallery(self) -> str:
        text = super().prepare_gallery()
        # Printing on, two per run, so the paper runs out several times.
        env = dict(os.environ, DATA_DIR=str(self.gallery_data), SECRET_KEY="probe")
        code = ("from app import create_app, ecosystem;app=create_app();app.test_client().get('/healthz');"
                "ctx=app.test_request_context();ctx.push();"
                f"ecosystem.save_device_profile({self.event['device_id']}, {{'modes':['photo','collage','gif'],"
                "'print':{'enabled':True,'limit_session':2,'copies_max':1"
                + (",'queue_while_empty':True,'queue_max':12,'queue_max_hours':6" if self.print_while_empty else "")
                + "},'gif':{'shots':2},"
                "'countdown':0,'result_seconds':5}, " + str(self.event["event_id"]) + ")")
        out = subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=env, capture_output=True, text=True,
                             timeout=120)
        if out.returncode:
            raise RuntimeError(out.stderr[-400:])
        return text

    def kill_booth(self) -> None:
        self.booth_proc.send_signal(signal.SIGKILL)
        self.booth_proc.wait(timeout=10)
        self.procs.remove(self.booth_proc)
        self.injected["kill"] = self.injected.get("kill", 0) + 1
        self._spawn_booth()

    # ── guests ────────────────────────────────────────────
    def post(self, path: str, body: Optional[dict] = None, timeout: float = 90):
        r = self.http.post(self.booth_url + path, json=body or {}, timeout=timeout)
        r.raise_for_status()
        return r.json() if r.content else {}

    def state(self) -> dict:
        return self.http.get(self.booth_url + "/api/state", timeout=30).json()

    def live_clients(self) -> None:
        """Phones and the admin page: live view opened, a few frames, gone."""
        while not self.stop_clients.is_set():
            try:
                with httpx.stream("GET", self.booth_url + "/api/live.mjpg", timeout=10) as r:
                    got = 0
                    for chunk in r.iter_bytes():
                        got += len(chunk)
                        if got > 200_000:
                            break
                self.client_stats["mjpg"] += 1
                httpx.get(self.booth_url + "/api/live.jpg", timeout=10)
                self.client_stats["jpg"] += 1
            except Exception:                         # noqa: BLE001 — booth restarting, camera gone
                self.client_stats["errors"] += 1
            self.stop_clients.wait(1.5)

    def one_run(self, n: int, mode: str, *, abandon: bool = False, kill: bool = False) -> dict:
        s = self.post("/api/session", {"mode": mode})
        uid = s["uid"]
        need = max(1, int(s.get("shots_needed") or 1))
        for i in range(need):
            self.post(f"/api/session/{uid}/shot", {})
            if abandon:
                return {"uid": uid, "result": "abandoned"}
            if kill and i == 0:
                self.kill_booth()
                return {"uid": uid, "result": "killed"}
        item = self.post(f"/api/session/{uid}/finish", {}, timeout=120)
        if not item.get("share_url"):
            raise RuntimeError("kein QR-Link")
        self.finished.append(uid)
        printed = ""
        # With queue_while_empty every run prints, so the tray runs empty more
        # often and more guests press "Drucken" while it is empty.
        if n % 2 == 0 or self.print_while_empty:
            try:
                answer = self.post(f"/api/print/{uid}", {"copies": 1})
                self.accepted[uid] = self.accepted.get(uid, 0) + int(answer.get("queued") or 1)
                printed = "deferred" if answer.get("deferred") else "queued"
                self.deferred_accepted += 1 if answer.get("deferred") else 0
            except httpx.HTTPStatusError as e:
                try:
                    why = str(e.response.json().get("detail") or e.response.status_code)
                except ValueError:
                    why = str(e.response.status_code)
                self.refused[why] = self.refused.get(why, 0) + 1
                printed = f"refused {e.response.status_code} {e.response.text[:60]}"
        return {"uid": uid, "result": "ok", "print": printed}

    # ── numbers ───────────────────────────────────────────
    def process_numbers(self) -> dict:
        pid = self.booth_proc.pid
        out = {"rss_mb": 0.0, "threads": 0, "open_files": 0}
        try:
            out["rss_mb"] = int(subprocess.run(["ps", "-o", "rss=", "-p", str(pid)], capture_output=True,
                                               text=True, timeout=10).stdout.strip() or 0) / 1024
            out["threads"] = max(0, len(subprocess.run(["ps", "-M", "-p", str(pid)], capture_output=True,
                                                       text=True, timeout=10).stdout.splitlines()) - 1)
            out["open_files"] = max(0, len(subprocess.run(["lsof", "-n", "-P", "-p", str(pid)], capture_output=True,
                                                          text=True, timeout=30).stdout.splitlines()) - 1)
        except Exception:                             # noqa: BLE001
            pass
        size = 0
        tmp = 0
        for p in self.booth_data.rglob("*"):
            try:
                if p.is_file():
                    size += p.stat().st_size
                    if "tmp" in p.parts or p.suffix in (".tmp", ".part"):
                        tmp += p.stat().st_size
            except OSError:
                pass
        out["data_mb"] = round(size / 1e6, 1)
        out["tmp_mb"] = round(tmp / 1e6, 2)
        logs = self.booth_data / "logs"
        out["log_kb"] = round(sum(p.stat().st_size for p in logs.glob("*")) / 1e3) if logs.exists() else 0
        return out

    def queue_numbers(self) -> dict:
        try:
            st = self.state()
        except Exception as e:                        # noqa: BLE001
            return {"state_error": str(e)[:80]}
        pr = st.get("printer") or {}
        sheets = ((pr.get("stations") or [{}])[0].get("sheets") or {})
        return {"camera": (st.get("camera") or {}).get("state"), "online": (st.get("sync") or {}).get("online"),
                "upload_pending": (st.get("sync") or {}).get("pending"), "print_pending": pr.get("pending"),
                "print_held": pr.get("held"), "printer_state": pr.get("state"),
                "tray_left": (sheets.get("fach") or {}).get("rest"),
                "notice": ((st.get("meldung") or {}).get("titel") or "")[:60]}

    # ── the day ───────────────────────────────────────────
    def schedule(self, n: int) -> dict:
        """What the venue does before run ``n``. Fixed, so two soaks are comparable."""
        r = self.runs
        plan = {}
        if n % 37 == 5:
            self.fault("camera_shot_fails", camera_fail_next=1)
        if n == int(r * 0.25):
            # Unplugged for 20 s; the guest in front of it keeps pressing.
            self.fault("camera_unplugged", camera_gone=True)
            threading.Timer(20.0, self._camera_back).start()
        if n == int(r * 0.5):
            # From here the printer says nothing about its paper (as a SELPHY
            # on Windows may): failures show only as failed jobs, prints end
            # up "held" — the case that needed the PIN before 15.11.2026.
            self.fault("printer_silent_about_paper", printer_reports_paper=False)
        if n == int(r * 0.33):
            self.fault("network_down")
            self._stop(self.gallery_proc)
        if n == int(r * 0.66):
            self.start_gallery()
            self.injected["network_back"] = 1
        if n == int(r * 0.5):
            self.fault("printer_off", printer_off=True)
        if n == int(r * 0.5) + 10:
            self.faults["printer_off"] = False
            self._write_faults()
        if n == int(r * 0.75):
            self.fault("disk_nearly_full", disk_free_gb=1.5)
        if n == int(r * 0.75) + 15:
            self.faults.pop("disk_free_gb", None)
            self._write_faults()
        if n in (int(r * 0.4), int(r * 0.8)):
            plan["kill"] = True
        if n % 23 == 11:
            plan["abandon"] = True
        if n % 50 == 25:
            plan["double"] = True
        if n % 41 == 7:
            plan["delete"] = True
        return plan

    def refill_like_a_helper(self, n: int, q: dict) -> None:
        """Paper out: a helper notices after three runs and refills — no button, no PIN."""
        empty = q.get("printer_state") == "error" or "Papier" in str(q.get("notice") or "")
        used = int((self.booth_data / "soak-printed.txt").read_text() or 0) \
            if (self.booth_data / "soak-printed.txt").exists() else 0
        tray_empty = used >= 18 * (1 + int(self.faults.get("tray_refills") or 0))
        if self.refill_after:
            # Wall-clock variant: the helper needs this many seconds to notice.
            now = time.monotonic()
            if tray_empty and self.paper_out_since is None:
                self.paper_out_since = now
            if tray_empty and now - self.paper_out_since >= self.refill_after:
                self.fault("tray_refilled_without_button",
                           tray_refills=int(self.faults.get("tray_refills") or 0) + 1)
                self.paper_out_since = None
            return
        if tray_empty and self.paper_out_since is None:
            self.paper_out_since = n
        # A slower helper when prints are queued: more requests arrive while
        # the tray is empty (ten runs), which is the case under test.
        if tray_empty and n - (self.paper_out_since or n) >= (10 if self.print_while_empty else 3):
            self.fault("tray_refilled_without_button", tray_refills=int(self.faults.get("tray_refills") or 0) + 1)
            self.paper_out_since = None
        del empty

    def start_with_retry(self, fn, *args, **kw):
        """A guest presses again when the booth is busy; how long until it takes the run?"""
        waited = 0.0
        while True:
            try:
                return fn(*args, **kw), waited
            except httpx.HTTPStatusError as e:
                if e.response.status_code in (409, 423, 503) and waited < 180:
                    time.sleep(2.0)
                    waited += 2.0
                    continue
                raise

    def day(self) -> int:
        self.step("Galerie vorbereiten", self.prepare_gallery)
        self.step("Galerie starten", self.start_gallery)
        self.step("Box starten", self.start_booth)
        self.step("Box meldet sich", self.wait_for_heartbeat)
        clients = threading.Thread(target=self.live_clients, daemon=True)
        clients.start()
        started = time.monotonic()
        rows = []
        fields = ["n", "t", "mode", "result", "seconds", "waited", "print", "error", "rss_mb", "threads",
                  "open_files", "data_mb", "tmp_mb", "log_kb", "camera", "online", "upload_pending",
                  "print_pending", "print_held", "printer_state", "tray_left", "notice", "state_error"]
        f = open(self.out / "runs.csv", "w", newline="", encoding="utf-8")
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        for n in range(1, self.runs + 1):
            plan = self.schedule(n)
            mode = MODES[n % len(MODES)]
            row = {"n": n, "t": round(time.monotonic() - started, 1), "mode": mode}
            begun = time.monotonic()
            try:
                if plan.get("double"):
                    self.injected["double_start"] = self.injected.get("double_start", 0) + 1
                    other = {}
                    th = threading.Thread(target=lambda: other.update(self._try_start(mode)))
                    th.start()
                res, waited = self.start_with_retry(self.one_run, n, mode, abandon=plan.get("abandon", False),
                                                    kill=plan.get("kill", False))
                if plan.get("double"):
                    th.join(30)
                    row["error"] = f"second start: {other.get('outcome')}"
                if plan.get("abandon"):
                    self.injected["abandoned"] = self.injected.get("abandoned", 0) + 1
                if plan.get("delete") and res.get("result") == "ok":
                    self.http.delete(f"{self.booth_url}/api/session/{res['uid']}", timeout=30).raise_for_status()
                    self.deleted.append(res["uid"])
                    self.injected["deleted"] = self.injected.get("deleted", 0) + 1
                row.update(result=res["result"], waited=waited, print=res.get("print", ""))
            except Exception as e:                    # noqa: BLE001
                row.update(result="error", error=str(e)[:160])
                self.errors.append(f"{n}: {str(e)[:160]}")
            row["seconds"] = round(time.monotonic() - begun, 2)
            q = self.queue_numbers()
            row.update(q)
            self.refill_like_a_helper(n, q)
            if n % 10 == 0 or n == 1:
                row.update(self.process_numbers())
            rows.append(row)
            w.writerow(row)
            f.flush()
            if self.pace:
                time.sleep(self.pace)
            if n % 25 == 0:
                print(f"{n}/{self.runs} t={row['t']}s rss={row.get('rss_mb')} held={q.get('print_held')} "
                      f"up={q.get('upload_pending')} err={len(self.errors)}", flush=True)
        f.close()
        self.stop_clients.set()
        return self.finish(rows, time.monotonic() - started)

    def _try_start(self, mode: str) -> dict:
        try:
            r = self.http.post(self.booth_url + "/api/session", json={"mode": mode}, timeout=30)
            if r.status_code == 200:
                uid = r.json().get("uid")
                self.http.post(self.booth_url + f"/api/session/{uid}/abort", json={}, timeout=30)
                return {"outcome": "also started (then aborted)"}
            return {"outcome": f"refused {r.status_code}"}
        except Exception as e:                        # noqa: BLE001
            return {"outcome": f"error {e}"}

    def _camera_back(self) -> None:
        self.faults["camera_gone"] = False
        self._write_faults()

    def _wait_camera(self, timeout: float = 60) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.queue_numbers().get("camera") == "ready":
                return
            time.sleep(1)
        self.errors.append("camera did not come back within 60 s")

    # ── the morning after ─────────────────────────────────
    def gallery_has(self, uids: List[str]) -> Dict[str, int]:
        env = dict(os.environ, DATA_DIR=str(self.gallery_data), SECRET_KEY="probe")
        code = ("import json,sys;from app import create_app, ecosystem;app=create_app();"
                "app.test_client().get('/healthz');ctx=app.test_request_context();ctx.push();"
                "uids=json.load(sys.stdin);"
                "print(json.dumps({u: len(ecosystem.session_media(u)) for u in uids}))")
        out = subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=env, input=json.dumps(uids),
                             capture_output=True, text=True, timeout=600)
        if out.returncode:
            raise RuntimeError(out.stderr[-300:])
        return json.loads(out.stdout.strip().splitlines()[-1])

    def finish(self, rows: List[dict], seconds: float) -> int:
        # Uploads drain once the network is back (it is back since 66 %).
        deadline = time.monotonic() + 600
        while time.monotonic() < deadline and int(self.queue_numbers().get("upload_pending") or 0):
            time.sleep(3)
        # Let the printer work off what it can on its own (no button pressed):
        # the last refill happens here too, as a helper would, then up to
        # four minutes for whatever waits to come out without the PIN.
        self.fault("tray_refilled_without_button", tray_refills=int(self.faults.get("tray_refills") or 0) + 1)
        deadline = time.monotonic() + 240
        while time.monotonic() < deadline:
            q = self.queue_numbers()
            if not int(q.get("print_held") or 0) and not int(q.get("print_pending") or 0):
                break
            time.sleep(5)
        q = self.queue_numbers()
        keep = [u for u in self.finished if u not in self.deleted]
        in_gallery = self.gallery_has(keep)
        lost = [u for u, k in in_gallery.items() if not k]
        printed_fake = int((self.booth_data / "soak-printed.txt").read_text() or 0) \
            if (self.booth_data / "soak-printed.txt").exists() else 0
        sampled = [r for r in rows if r.get("rss_mb")]
        # Sheets per run, from the fake printer's own log, against what the
        # booth accepted. Deleted runs are left out: their prints are dropped
        # on purpose.
        sheets: Dict[str, int] = {}
        log_file = self.booth_data / "soak-printed.log"
        lines = log_file.read_text(encoding="utf-8").splitlines() if log_file.exists() else []
        for uid in self.accepted:
            sheets[uid] = sum(1 for line in lines if uid in line)
        considered = {u: n for u, n in self.accepted.items() if u not in self.deleted}
        prints_lost = sorted(u for u, n in considered.items() if sheets.get(u, 0) < n)
        prints_duplicated = sorted(u for u, n in considered.items() if sheets.get(u, 0) > n)

        def span(key):
            vals = [r[key] for r in sampled if r.get(key) not in (None, "")]
            return {"start": vals[0], "end": vals[-1], "max": max(vals)} if vals else {}

        per_run = [r["seconds"] for r in rows if r.get("result") == "ok"]
        tenth = max(1, len(per_run) // 10)
        summary = {
            "runs": len(rows), "ok": sum(r.get("result") == "ok" for r in rows),
            "errors": len(self.errors), "error_samples": self.errors[:15],
            "minutes": round(seconds / 60, 1), "faults": self.injected,
            "live_clients": self.client_stats,
            "rss_mb": span("rss_mb"), "threads": span("threads"), "open_files": span("open_files"),
            "data_mb": span("data_mb"), "tmp_mb": span("tmp_mb"), "log_kb": span("log_kb"),
            "seconds_per_run_first_tenth": round(sum(per_run[:tenth]) / tenth, 2) if per_run else None,
            "seconds_per_run_last_tenth": round(sum(per_run[-tenth:]) / tenth, 2) if per_run else None,
            "finished_runs": len(self.finished), "deleted": len(self.deleted),
            "pictures_lost_in_gallery": lost[:20], "lost_count": len(lost),
            "upload_pending_end": q.get("upload_pending"),
            "prints_held_end_needing_a_human": q.get("print_held"),
            "print_pending_end": q.get("print_pending"),
            "sheets_through_fake_printer": printed_fake, "tray_refills": self.faults.get("tray_refills"),
            "notice_end": q.get("notice"), "camera_end": q.get("camera"),
            "print_while_empty": self.print_while_empty,
            "print_requests_accepted": sum(considered.values()),
            "print_requests_deferred": self.deferred_accepted,
            "print_requests_refused": self.refused,
            "print_requests_lost": len(prints_lost), "print_lost_samples": prints_lost[:10],
            "print_requests_duplicated": len(prints_duplicated), "print_duplicated_samples": prints_duplicated[:10],
            "sheets_logged": len(lines),
        }
        (self.out / "summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
        print(json.dumps(summary, indent=2, ensure_ascii=False))
        bad = (lost or int(q.get("print_held") or 0) or int(q.get("upload_pending") or 0)
               or (self.print_while_empty and (prints_lost or prints_duplicated)))
        return 1 if bad else 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Event day in fast motion, with injected faults (no hardware)")
    ap.add_argument("--runs", type=int, default=600)
    ap.add_argument("--out", default="soak-tag")
    ap.add_argument("--refill-after", type=float, default=0.0,
                    help="seconds until the helper refills an empty tray (default: after three runs)")
    ap.add_argument("--silent-printer", action="store_true",
                    help="the printer never reports paper out, only failed jobs show it (all day)")
    ap.add_argument("--pace", type=float, default=0.0, help="seconds between two guests")
    ap.add_argument("--print-while-empty", action="store_true",
                    help="profile print.queue_while_empty on: prints asked for while the tray is empty are "
                         "queued; the summary matches every accepted print to a sheet (lost / duplicated)")
    args = ap.parse_args()
    soak = Soak(args.runs, Path(args.out).resolve(), refill_after=args.refill_after,
                silent_printer=args.silent_printer, pace=args.pace, print_while_empty=args.print_while_empty)
    # A terminated soak must take its gallery and booth with it.
    signal.signal(signal.SIGTERM, lambda *_: (_ for _ in ()).throw(SystemExit(1)))
    try:
        return soak.day()
    except rehearsal.StepFailed:
        soak.report()
        return 1
    finally:
        soak.stop_clients.set()
        soak.shutdown()
        print(f"Booth data kept in {soak.tmp}")


if __name__ == "__main__":
    raise SystemExit(main())
