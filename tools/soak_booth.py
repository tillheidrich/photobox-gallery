#!/usr/bin/env python3
"""A booth with a camera and a printer that can be told to fail — for tools/soak_faults.py.

Not for events. It starts the ordinary booth (``boothcore.__main__.main``)
after swapping two things:

* the camera is the mock camera, but it reads ``faults.json`` before every
  shot and every live frame: ``camera_fail_next`` (that many shots fail),
  ``camera_gone`` (open and live view fail, as with a pulled USB cable);
* the printer is a fake SELPHY: 18 sheets in the tray, then "paper out"
  until the driver writes a higher ``tray_refills`` (somebody put paper in —
  nobody pressed a button). ``printer_off`` makes it unreachable. It never
  talks to a real printer, it only counts sheets in ``printed.txt``.

``disk_free_gb`` (when set) is what ``shutil.disk_usage`` reports for the
booth's data folder.

    BOOTH_DATA=… SOAK_FAULTS=…/faults.json python tools/soak_booth.py --no-kiosk --port 8791
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT / "booth"), str(ROOT / "sync")]

FAULTS = Path(os.environ["SOAK_FAULTS"])
TRAY = 18
_lock = threading.Lock()


def faults() -> dict:
    try:
        return json.loads(FAULTS.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _take_camera_failure() -> bool:
    """One failing shot less; True when this shot is to fail."""
    with _lock:
        f = faults()
        n = int(f.get("camera_fail_next") or 0)
        if n <= 0:
            return False
        f["camera_fail_next"] = n - 1
        FAULTS.write_text(json.dumps(f), encoding="utf-8")
        return True


def patch() -> None:
    from boothcore.camera import service as camsvc
    from boothcore.camera.mock import MockCamera
    from boothcore.printing import backends
    from boothcore.printing import service as prsvc
    import boothcore.app as app_mod
    import boothcore.printing as pr_pkg

    class FaultCamera(MockCamera):
        name = "mock"

        def open(self) -> None:
            if faults().get("camera_gone"):
                raise RuntimeError("Kamera nicht gefunden (Soak: Kabel gezogen)")
            super().open()

        def preview_jpeg(self, max_width: int = 1280):
            if faults().get("camera_gone"):
                raise RuntimeError("Kamera getrennt (Soak)")
            return super().preview_jpeg(max_width)

        def capture(self, target):
            if faults().get("camera_gone") or _take_camera_failure():
                raise RuntimeError("Auslösen fehlgeschlagen (Soak)")
            return super().capture(target)

    real_create_backend = camsvc.create_backend

    def create_backend(cfg):
        if (cfg.get("backend") or "") in ("mock", "auto"):
            return FaultCamera()
        return real_create_backend(cfg)

    camsvc.create_backend = create_backend
    import boothcore.camera as cam_pkg
    cam_pkg.create_backend = create_backend
    app_mod.create_backend = create_backend

    data = Path(os.environ["BOOTH_DATA"])
    counter = data / "soak-printed.txt"

    class FakeSelphy(backends.PrinterBackend):
        name = "soak-selphy"

        def _used(self) -> int:
            try:
                return int(counter.read_text() or 0)
            except (OSError, ValueError):
                return 0

        def _paper_out(self) -> bool:
            f = faults()
            return self._used() >= TRAY * (1 + int(f.get("tray_refills") or 0))

        def status(self) -> dict:
            f = faults()
            if f.get("printer_off"):
                return {"state": "offline", "name": "Soak SELPHY", "reasons": ["offline-report"],
                        "hinweis": "Drucker nicht erreichbar"}
            if self._paper_out() and f.get("printer_reports_paper", True):
                return {"state": "error", "name": "Soak SELPHY", "reasons": ["media-empty"],
                        "hinweis": "Papier leer"}
            return {"state": "ready", "name": "Soak SELPHY"}

        def print_file(self, path, copies: int = 1) -> None:
            for _ in range(max(1, copies)):
                if faults().get("printer_off"):
                    raise RuntimeError("Drucker: nicht erreichbar (Soak)")
                if self._paper_out():
                    raise RuntimeError("Drucker: Papier leer (Soak)")
                time.sleep(float(faults().get("print_seconds") or 0.2))
                with _lock:
                    counter.write_text(str(self._used() + 1))
                    # Which file came out, one line per sheet: the driver
                    # matches sheets to accepted print requests per run.
                    with open(data / "soak-printed.log", "a", encoding="utf-8") as f:
                        f.write(f"{path}\n")

    real_create_printer = prsvc.create_printer

    def create_printer(cfg, folder):
        kind = (cfg.get("backend") or "auto").lower()
        if kind == "none":
            return None
        if kind in ("soak", "auto"):              # "auto" would pick a real printer here
            return FakeSelphy()
        # Anything else would be a real printer on this machine: refused.
        raise SystemExit(f"soak_booth: printer backend {kind!r} refused, only 'soak' or 'none'")

    for module in (prsvc, pr_pkg, app_mod):
        module.create_printer = create_printer
    del real_create_printer

    real_du = shutil.disk_usage

    def disk_usage(path):
        du = real_du(path)
        gb = faults().get("disk_free_gb")
        if gb is None:
            return du
        free = int(float(gb) * 1e9)
        return type(du)(du.total, du.total - free, free)

    shutil.disk_usage = disk_usage


if __name__ == "__main__":
    patch()
    from boothcore.__main__ import main
    main()
