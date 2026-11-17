"""Dauerlauf für die Kamera — ohne Oberfläche, ohne Galerie, ohne Drucker.

Gedacht für den Tag, an dem eine neue Kamera oder ein frisch eingerichtetes
Canon-SDK zum ersten Mal an der Box hängt. Die Oberfläche verbirgt Fehler hinter
einer freundlichen Meldung; hier steht, was wirklich passiert ist.

    python -m boothcore.tools.cameratest                 # 10 Auslösungen
    python -m boothcore.tools.cameratest --shots 200     # der echte Dauerlauf
    python -m boothcore.tools.cameratest --live          # zusätzlich Live-Bild prüfen
    python -m boothcore.tools.cameratest --backend edsdk --keep

Rückgabe 0, wenn alle Auslösungen durchliefen, sonst 1. Die Bilder landen in
einem Ordner unter den Box-Daten und werden am Ende gelöscht (außer --keep).
"""
from __future__ import annotations

import argparse
import shutil
import statistics
import sys
import time
import traceback
from pathlib import Path
from typing import List, Optional

from ..camera.base import CameraError


def _backend(name: str, store):
    """Genau ein Backend bauen — bewusst ohne die Automatik des CameraService."""
    cam = (store.local.get("camera") or {})
    name = name or cam.get("backend") or "auto"
    if name == "auto":
        name = "ccapi" if cam.get("ccapi_url") else ("edsdk" if cam.get("edsdk_dll") else "webcam")
    if name == "ccapi":
        from ..camera.ccapi import CcapiCamera
        return name, CcapiCamera(cam.get("ccapi_url", ""), cam.get("ccapi_liveview", "medium"),
                                 cam.get("ccapi_display", "on"))
    if name == "edsdk":
        from ..camera.edsdk import EdsdkCamera
        return name, EdsdkCamera(cam.get("edsdk_dll", ""))
    if name == "webcam":
        from ..camera.webcam import WebcamCamera
        return name, WebcamCamera(int(cam.get("webcam_index", 0)))
    if name == "gphoto2":
        from ..camera.gphoto2cam import Gphoto2Camera
        return name, Gphoto2Camera()
    if name == "digicamcontrol":
        # Hieß bis zur Durchsicht am 19.09.2026 DigicamCamera — die Klasse
        # gibt es nicht. Ausgerechnet das Werkzeug, mit dem man vor dem Event
        # prüft, brach für diesen Rückfallweg mit ImportError ab.
        from ..camera.digicam import DigiCamControlCamera
        return name, DigiCamControlCamera(cam.get("digicam_url", "http://127.0.0.1:5513"),
                                          cam.get("folder", ""))
    if name == "mock":
        from ..camera.mock import MockCamera
        return name, MockCamera()
    raise SystemExit(f"Unbekanntes Backend: {name}")


def _human(seconds: float) -> str:
    return f"{seconds:.2f} s"


def run(shots: int, backend: str, live: bool, keep: bool, pause: float) -> int:
    from ..config import Store, data_dir

    store = Store(data_dir())
    name, cam = _backend(backend, store)
    out = Path(data_dir()) / "cameratest"
    out.mkdir(parents=True, exist_ok=True)

    print(f"Backend: {name}")
    print(f"Bilder:  {out}")
    print()
    try:
        started = time.monotonic()
        cam.open()
        print(f"Kamera geöffnet nach {_human(time.monotonic() - started)}")
    except CameraError as e:
        print(f"FEHLGESCHLAGEN beim Öffnen: {e}")
        if name == "edsdk":
            from ..edsdk_setup import has_sdk_platform, setup_hint, status
            ok, detail = status()
            print(f"SDK-Prüfung: {detail}")
            if not ok:
                print(("Einrichten mit:  " if has_sdk_platform() else "") + setup_hint())
        return 1
    except Exception as e:                                  # noqa: BLE001 — hier will man alles sehen
        print(f"FEHLGESCHLAGEN beim Öffnen: {e}")
        traceback.print_exc()
        return 1

    if live:
        try:
            frame = cam.preview_jpeg()
            print(f"Live-Bild: {len(frame or b'')} Bytes" if frame else "Live-Bild: kein Bild (Backend kann es nicht)")
        except Exception as e:                              # noqa: BLE001
            print(f"Live-Bild: fehlgeschlagen — {e}")

    times: List[float] = []
    failures: List[str] = []
    try:
        for i in range(1, shots + 1):
            target = out / f"shot-{i:04d}.jpg"
            t0 = time.monotonic()
            try:
                path = cam.capture(target)
                took = time.monotonic() - t0
                times.append(took)
                size = Path(path).stat().st_size if Path(path).exists() else 0
                print(f"{i:>4}/{shots}  {_human(took)}  {size // 1024} KB")
                if not size:
                    failures.append(f"{i}: Datei ist leer")
            except Exception as e:                          # noqa: BLE001
                took = time.monotonic() - t0
                failures.append(f"{i}: {e}")
                print(f"{i:>4}/{shots}  {_human(took)}  FEHLER: {e}")
            if pause:
                time.sleep(pause)
    except KeyboardInterrupt:
        print("\nAbgebrochen.")
    finally:
        try:
            cam.close()
        except Exception:                                   # noqa: BLE001
            pass
        if not keep:
            shutil.rmtree(out, ignore_errors=True)

    print()
    if times:
        print(f"Auslösungen: {len(times)} erfolgreich, {len(failures)} fehlgeschlagen")
        print(f"Zeit je Aufnahme: schnellste {_human(min(times))}, Mittel {_human(statistics.mean(times))}, "
              f"langsamste {_human(max(times))}")
        if len(times) > 2 and max(times) > 3 * statistics.median(times):
            print("Auffällig: einzelne Aufnahmen dauern um ein Vielfaches länger — meist ein Hinweis auf"
                  " einen USB-Hub, ein sparsames USB-Port-Management oder eine langsame Speicherkarte.")
    else:
        print("Keine einzige Aufnahme ist durchgelaufen.")
    for line in failures[:10]:
        print(f"  Fehler {line}")
    if len(failures) > 10:
        print(f"  … und {len(failures) - 10} weitere")
    return 0 if times and not failures else 1


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(prog="cameratest", description="Kamera ohne die restliche Box prüfen")
    ap.add_argument("--shots", type=int, default=10, help="Anzahl Auslösungen (Standard 10; für den Dauerlauf 200)")
    ap.add_argument("--backend", default="", help="mock|webcam|ccapi|edsdk|gphoto2|digicamcontrol (Standard: aus der Konfiguration)")
    ap.add_argument("--live", action="store_true", help="zusätzlich ein Live-Bild abholen")
    ap.add_argument("--keep", action="store_true", help="Testbilder behalten statt löschen")
    ap.add_argument("--pause", type=float, default=0.0, help="Pause zwischen den Auslösungen in Sekunden")
    args = ap.parse_args(argv)
    return run(args.shots, args.backend, args.live, args.keep, args.pause)


if __name__ == "__main__":
    sys.exit(main())
