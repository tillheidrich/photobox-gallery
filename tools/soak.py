#!/usr/bin/env python3
"""Run the booth like a long evening and write down how it holds up.

    python tools/soak.py --url http://127.0.0.1:8770 --hours 9 --every 60

One run after the other through the booth's own interface: photo, collage,
GIF in turn, each with one print. Every line of the log is one run: how long
it took, whether it worked, how much memory the booth holds, how many files
wait for upload. At the end a summary and exit code 0 — or 1 when a run
failed, memory kept growing or the booth stopped answering.

No new dependency: standard library only, so it runs on the booth PC as it is.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path


def call(base: str, path: str, body=None, timeout: float = 90.0):
    data = json.dumps(body if body is not None else {}).encode() if body is not None or path.endswith(("/shot", "/finish")) else None
    req = urllib.request.Request(base + path, data=data, headers={"Content-Type": "application/json"},
                                 method="POST" if data is not None else "GET")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read() or b"{}")


def rss_mb(pid: int) -> float:
    """Memory the process holds, in MB. 0 when it cannot be read."""
    if not pid:
        return 0.0
    try:
        if os.name == "nt":
            out = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
                                 capture_output=True, text=True, timeout=10).stdout
            kb = out.strip().split('","')[-1].strip('"').replace(".", "").replace(",", "").replace("\xa0", "")
            return int("".join(c for c in kb if c.isdigit())) / 1024
        out = subprocess.run(["ps", "-o", "rss=", "-p", str(pid)], capture_output=True, text=True, timeout=10).stdout
        return int(out.strip()) / 1024
    except Exception:                                   # noqa: BLE001
        return 0.0


def one_run(base: str, mode: str, do_print: bool) -> dict:
    begun = time.monotonic()
    s = call(base, "/api/session", {"mode": mode})
    uid = s["uid"]
    need = int(s.get("shots_needed") or 1)
    for _ in range(need):
        call(base, f"/api/session/{uid}/shot", {})
    done = call(base, f"/api/session/{uid}/finish", {})
    printed = ""
    if do_print and (done.get("print") or {}).get("enabled"):
        try:
            call(base, f"/api/print/{uid}", {"copies": 1})
            printed = "1"
        except urllib.error.HTTPError as e:
            printed = f"HTTP {e.code}"
    return {"uid": uid, "shots": need, "seconds": round(time.monotonic() - begun, 2), "printed": printed}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--url", default="http://127.0.0.1:8770")
    ap.add_argument("--hours", type=float, default=9.0)
    ap.add_argument("--every", type=float, default=60.0, help="seconds from one run to the next")
    ap.add_argument("--modes", default="photo,collage,gif")
    ap.add_argument("--pid", type=int, default=0, help="process id of the booth, for the memory column")
    ap.add_argument("--no-print", action="store_true")
    ap.add_argument("--log", default="soak.csv")
    args = ap.parse_args()

    base = args.url.rstrip("/")
    modes = [m for m in args.modes.split(",") if m]
    end = time.monotonic() + args.hours * 3600
    log = Path(args.log)
    fields = ["time", "n", "mode", "ok", "seconds", "shots", "printed", "rss_mb", "camera", "reconnects",
              "printer", "error"]
    n = failed = unreachable = 0
    memory: list = []
    with log.open("w", newline="", encoding="utf-8") as fh:
        out = csv.DictWriter(fh, fieldnames=fields)
        out.writeheader()
        while time.monotonic() < end:
            tick = time.monotonic()
            mode = modes[n % len(modes)]
            n += 1
            row = {"time": time.strftime("%H:%M:%S"), "n": n, "mode": mode, "ok": 1, "error": ""}
            try:
                row.update(one_run(base, mode, not args.no_print))
                row.pop("uid", None)
            except Exception as exc:                    # noqa: BLE001 — the soak outlives a failed run
                failed += 1
                row.update(ok=0, error=f"{type(exc).__name__}: {exc}"[:200])
            try:
                st = call(base, "/api/state", timeout=15)
                cam, pr = st.get("camera") or {}, st.get("printer") or {}
                row.update(camera=cam.get("state"), reconnects=cam.get("reconnects"), printer=pr.get("state"))
                unreachable = 0
            except Exception as exc:                    # noqa: BLE001
                unreachable += 1
                row.update(camera="?", error=(row["error"] + f" | state: {exc}")[:200])
            row["rss_mb"] = round(rss_mb(args.pid), 1)
            if row["rss_mb"]:
                memory.append(row["rss_mb"])
            out.writerow(row)
            fh.flush()
            if n % 10 == 0 or not row["ok"]:
                print(f"{row['time']}  Lauf {n}  {mode}  {'ok' if row['ok'] else 'FEHLER'}  "
                      f"{row.get('seconds', '-')} s  {row['rss_mb']} MB  {row['error']}", flush=True)
            if unreachable >= 5:
                print("Die Box antwortet nicht mehr — Abbruch.", flush=True)
                break
            time.sleep(max(0.0, args.every - (time.monotonic() - tick)))

    # Memory: compare the first and the last fifth, after the warm-up.
    growth = 0.0
    if len(memory) >= 20:
        fifth = max(2, len(memory) // 5)
        growth = sum(memory[-fifth:]) / fifth - sum(memory[fifth:2 * fifth]) / fifth
    print(f"\n{n} Läufe, {failed} fehlgeschlagen, Speicher {memory[0] if memory else 0:.0f} → "
          f"{memory[-1] if memory else 0:.0f} MB (Zuwachs nach dem Einlaufen {growth:+.0f} MB). Protokoll: {log}",
          flush=True)
    return 1 if failed or unreachable >= 5 or growth > 300 else 0


if __name__ == "__main__":
    sys.exit(main())
