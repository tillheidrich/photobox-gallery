"""Start: ``python -m boothcore`` (öffnet optional den Kiosk-Browser)."""
from __future__ import annotations

import argparse
import logging
import os
import shutil
import subprocess
import sys
import threading
import time
import webbrowser

import uvicorn

from . import lifecycle


def _keep_kiosk(url: str, booth, wait: float = 2.5, opener=None, sleep=time.sleep, clock=time.monotonic,
                rounds=None, mode=None) -> None:
    """Open the Chrome kiosk, and open it again when it has not shown the booth for a while.

    A guest who closes Chrome (Alt+F4 on the buzzer keyboard), a Chrome crash
    or a booth restart behind Chrome's "site can't be reached" page used to
    leave the screen dark until somebody came.

    Only for Chrome: started in mode "chrome" and when the own window gave
    up. ``mode`` is asked before every opening - should the own window be
    what shows the booth, Chrome is never opened over it (its page polls
    /api/state too, but its liveness is the window's heartbeat).
    """
    from . import window as fenster
    from .waechter import kiosk_should_reopen

    opener = opener or _open_kiosk
    mode = mode or (lambda: fenster.status().get("mode", ""))
    sleep(wait)
    if mode() == "chrome":
        opener(url)
    launched = clock()
    n = 0
    while rounds is None or n < rounds:
        n += 1
        sleep(15)
        if kiosk_should_reopen(mode(), getattr(booth, "kiosk_seen", None), launched, clock()):
            logging.getLogger("booth").warning("Kiosk meldet sich nicht mehr — Browser wird neu geöffnet")
            opener(url)
            launched = clock()


def wire_instance(instance, booth, delay: float = 0.5) -> None:
    """Commands a second start may send over the instance line, besides show/selftest.

    ``quit``: ``PhotoboxBooth.exe --quit`` - the migration script over ssh
    ends the booth the way the admin's "Beenden" does (camera handed back,
    exit 0, the watcher stays down), instead of killing it.
    """
    if instance is None:
        return
    from .lifecycle import CONSOLE_EVENTS

    def _ender(reason: str):
        def _end() -> str:
            # Answer first, then leave: end_session ends this process.
            threading.Thread(target=booth.end_session, args=(reason,), name="session-end", daemon=True).start()
            return "ok"
        return _end
    # ``end:<reason>``: the watcher heard Windows sign out or shut down (it is
    # the process without a window, so it hears it) and passes it on.
    for reason in list(CONSOLE_EVENTS.values()) + ["signal"]:
        instance.handlers[f"end:{reason}"] = _ender(reason)

    def _quit() -> str:
        if hasattr(booth, "quit_on_purpose"):
            booth.quit_on_purpose(delay)
        else:
            threading.Timer(delay, lambda: booth.shutdown(0)).start()
        return "ok"
    instance.handlers["quit"] = _quit


def _quit_cli(wait: float = 30.0) -> int:
    """``--quit``: ask the running booth to end, wait until its lock is free. 0 = ended."""
    from . import window as fenster
    from .config import data_dir

    line = fenster.SingleInstance(data_dir() / "window", create=False)
    answer = line.send("quit")
    if answer != "ok":
        print("Keine laufende Fotobox mit Fenster gefunden (Antwort: %r)." % (answer or "keine"))
        return 1
    end = time.monotonic() + wait
    while time.monotonic() < end:
        probe = fenster.SingleInstance(data_dir() / "window")
        if probe.acquire():
            probe.release()
            print("Fotobox beendet.")
            return 0
        time.sleep(0.5)
    print("Fotobox hat Beenden bestätigt, läuft aber nach %.0f s noch." % wait)
    return 2


def _dauerbetrieb_cli(keys: str) -> int:
    """``--dauerbetrieb KEY[,KEY]``: apply single measures (e.g. the active hours) and say how it went."""
    from .config import data_dir
    from .dauerbetrieb import Dauerbetrieb

    d = Dauerbetrieb(data_dir() / "dauerbetrieb.json")
    known = {str(e["key"]) for e in d.status()}
    wanted = [k.strip() for k in keys.split(",") if k.strip()]
    if wanted == ["aktivzeit"]:
        wanted = ["aktivzeit_automatik", "aktivzeit_beginn", "aktivzeit_ende"]
    elif wanted == ["empfohlen"]:
        # The Windows installer's "unattended operation" choice: the same set
        # the admin page recommends, each one remembered so the uninstaller
        # (``--uninstall``) can put it back.
        from .dauerbetrieb import MASSNAHMEN
        wanted = [m.key for m in MASSNAHMEN if m.empfohlen]
    worst = 0
    for key in wanted:
        if key not in known:
            print(f"-- {key}: unbekannt (bekannt: {', '.join(sorted(known))})")
            worst = 1
            continue
        r = d.anwenden(key)
        print(("OK  " if r.get("ok") else "-- ") + key + (f" · {r.get('meldung')}" if r.get("meldung") else ""))
        worst = worst or (0 if r.get("ok") else 1)
    return worst


def _autostart_cli(on: bool, setter=None) -> int:
    """``--autostart an|aus``: the logon task alone, for the Windows installer. 0 = done."""
    if setter is None:
        from .winsetup import set_autostart as setter
    from .winsetup import report
    results = setter(on)
    print(report(results))
    return 0 if all(ok for _, ok, _ in results) else 1


def _open_kiosk(url: str) -> None:
    candidates = [
        os.path.expandvars(r"%ProgramFiles%\Google\Chrome\Application\chrome.exe"),
        os.path.expandvars(r"%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe"),
        os.path.expandvars(r"%ProgramFiles%\Microsoft\Edge\Application\msedge.exe"),
        # macOS: die ausführbare Datei liegt im Paket, nicht im Suchpfad
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
        "/Applications/Chromium.app/Contents/MacOS/Chromium",
        shutil.which("chromium") or "", shutil.which("chromium-browser") or "", shutil.which("google-chrome") or "",
    ]
    for exe in candidates:
        if exe and os.path.exists(exe):
            subprocess.Popen([exe, "--kiosk", url, "--no-first-run", "--disable-pinch", "--overscroll-history-navigation=0",
                              "--disable-features=TranslateUI", "--autoplay-policy=no-user-gesture-required",
                              # after a power cut the browser must not ask whether to restore pages
                              "--disable-session-crashed-bubble", "--hide-crash-restore-bubble", "--noerrdialogs",
                              f"--user-data-dir={os.path.join(os.path.expanduser('~'), '.photobox-kiosk')}"])
            return
    webbrowser.open(url)


def _doctor(args) -> int:
    """Prüflauf für den Rechner am Event. Rückgabe 1, wenn so nichts läuft."""
    from .waechter import port_from, port_taken

    port = args.port or port_from([])
    if port_taken(port):
        # The running booth holds the camera. A second process opening it
        # (EDSDK: one session per camera) could cost the running booth its
        # camera in the middle of the event.
        print("Die Fotobox läuft gerade. Die Selbstdiagnose dann bitte in der Verwaltung starten:\n"
              "Ecke des Startbildschirms gedrückt halten, PIN, dann „Prüfen“.")
        if os.name == "nt":
            lifecycle.pause()
        return 2
    from .app import Booth
    from .doctor import Doctor, FAIL, report, summary

    booth = Booth(start_threads=False)
    booth.listen_host = args.host
    booth.camera.start()
    for _ in range(80):                       # der Kamera kurz Zeit geben, sich zu melden
        if booth.camera.state in ("ready", "error"):
            break
        time.sleep(0.1)
    checks = Doctor(booth).run()
    print(report(checks))
    booth.camera.stop()
    booth.mail.stop()
    if os.name == "nt":
        lifecycle.pause()
    return 1 if summary(checks)["worst"] == FAIL else 0


def _install_edsdk(folder: str) -> int:
    """Canon-SDK aus dem entpackten Download übernehmen. Rückgabe 1 bei Fehler."""
    from pathlib import Path

    from .config import Store, data_dir
    from .edsdk_setup import configure, install

    ok, lines = install(Path(folder))
    for line in lines:
        print(line)
    if not ok:
        if os.name == "nt":
            lifecycle.pause()
        return 1
    dll = configure(Store(data_dir()))
    print(f"\nEingetragen: Kamera-Backend „edsdk“, Datei {dll}")
    print("Nächster Schritt: Kamera anschließen, einschalten, dann")
    print("    python -m boothcore.tools.cameratest --shots 5")
    if os.name == "nt":
        lifecycle.pause()
    return 0


def main() -> None:
    # Started without a console (a restart, a scheduled task), Python has no
    # stdout at all and the first print or log line would be the last.
    for name in ("stdout", "stderr"):
        if getattr(sys, name) is None:
            setattr(sys, name, open(os.devnull, "w"))
    lifecycle.calm_console()
    ap = argparse.ArgumentParser(prog="photobox-booth")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=None)
    ap.add_argument("--no-kiosk", action="store_true", help="Nur der Server, kein Fenster (wie --window none)")
    ap.add_argument("--window", choices=("own", "chrome", "none"),
                    help="Oberfläche: own = eigenes Programmfenster (Standard), chrome = Chrome im "
                         "Kiosk-Modus, none = nur Server. Sonst gilt window.mode in local.json")
    ap.add_argument("--window-debug", action="store_true",
                    help="Eigenes Fenster mit Entwicklerwerkzeugen, Kontextmenü und Schließen-Knopf")
    ap.add_argument("--window-selftest", action="store_true",
                    help="Die laufende Box prüft ihr Fenster und schreibt fenster-selbsttest.json "
                         "(plus Bildschirmfoto) in ihren Datenordner")
    ap.add_argument("--camera", help="Kamera-Backend erzwingen: mock|webcam|edsdk|gphoto2")
    ap.add_argument("--install", action="store_true",
                    help="Rechner einrichten: Autostart, kein Ruhezustand, Starter "
                         "(Windows: Aufgabenplanung · Linux: systemd · macOS: LaunchAgent)")
    ap.add_argument("--uninstall", action="store_true", help="Autostart und alles Eingerichtete wieder entfernen")
    ap.add_argument("--doctor", action="store_true",
                    help="Diese Box prüfen: Kamera, Drucker, Platz, Galerie, Ablage — und was zu tun ist")
    ap.add_argument("--install-edsdk", metavar="ORDNER",
                    help="Canon-SDK einrichten: entpackten SDK-Ordner angeben, die Box holt sich die"
                         " passenden Dateien heraus (das SDK selbst kommt von Canon)")
    ap.add_argument("--quit", action="store_true",
                    help="Die laufende Box geordnet beenden (wie Verwaltung → Beenden; Kamera wird freigegeben)")
    ap.add_argument("--dauerbetrieb", metavar="MASSNAHMEN",
                    help="Einzelne Windows-Einstellungen der Box setzen, z. B. „aktivzeit“ "
                         "(Update-Nutzungszeit 10–4 Uhr; braucht Administratorrechte) oder "
                         "„empfohlen“ (alles für den unbeaufsichtigten Betrieb)")
    ap.add_argument("--autostart", choices=("an", "aus"),
                    help="Nur die Aufgabe „Photobox Booth“ (Start bei Anmeldung) einrichten oder "
                         "entfernen - ohne Energie-, Firewall- oder Verknüpfungsschritte")
    args = ap.parse_args()
    if args.quit:
        raise SystemExit(_quit_cli())
    if args.autostart:
        raise SystemExit(_autostart_cli(args.autostart == "an"))
    if args.dauerbetrieb:
        raise SystemExit(_dauerbetrieb_cli(args.dauerbetrieb))
    if args.install_edsdk:
        raise SystemExit(_install_edsdk(args.install_edsdk))
    if args.install or args.uninstall:
        from .hostsetup import install, uninstall
        from .winsetup import report
        from .config import Store, admin_corner_hint, data_dir
        store = Store(data_dir())
        port = args.port or int(store.local.get("port", 8765))
        print(report(uninstall() if args.uninstall else install(port)))
        if args.install:
            print("\nFertig. Die Fotobox startet ab jetzt bei jeder Anmeldung von selbst.")
            print(f"Verwaltung: {admin_corner_hint(store.profile)} auf den Startbildschirm drücken (PIN ändern!).")
        if os.name == "nt":
            lifecycle.pause()
        return
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    if args.camera:
        os.environ["BOOTH_CAMERA"] = args.camera
    from . import window as fenster
    from .config import Store, data_dir
    # The edition with camera SDK: the portable .exe has no setup page, so
    # its first start shows Canon's end-user terms once (camera_sdk_bundle).
    from .camera_sdk_bundle import acknowledge_once
    try:
        acknowledge_once(data_dir())
    except Exception:  # noqa: BLE001 - never a reason not to start
        logging.getLogger("booth").warning("camera SDK terms could not be shown", exc_info=True)
    if args.window_selftest:
        raise SystemExit(fenster.selftest_cli(data_dir(), Store(data_dir()).local))
    from .app import Booth, create_app

    if args.doctor:
        raise SystemExit(_doctor(args))

    # Which screen: own window, Chrome or none (window.py). Decided before
    # the camera starts, because on a Mac both want the main thread.
    local = Store(data_dir()).local
    window_opts = fenster.settings(local)
    if args.window_debug:
        window_opts["debug"] = True
    camera_backend = os.environ.get("BOOTH_CAMERA") or str((local.get("camera") or {}).get("backend", "auto"))
    plan = fenster.plan(local, cli=args.window, no_kiosk=args.no_kiosk, camera_backend=camera_backend,
                        failures=fenster.FailureMemory(data_dir() / "window").count())
    instance = None
    if plan.mode != "none":
        instance, outcome = fenster.claim_instance_outcome(data_dir(), plan.mode)
        if instance is None:
            # "shown": another booth runs and was brought to the front - exit 4,
            # a watcher behind this process ends too. "silent": a booth holds the
            # lock and does not answer - exit 1, the watcher tries again later.
            raise SystemExit(fenster.EXIT_FOR_CLAIM[outcome])

    # macOS: the Canon SDK reports cameras only to the thread that owns the
    # run loop, the main thread. So there the camera loop gets the main thread
    # and the web server a worker thread. Everywhere else it stays as it was.
    # With the own window the window has the main thread (plan() made sure
    # the camera does not need it then).
    camera_on_main = sys.platform == "darwin" and plan.mode != "own"
    if camera_on_main:
        from .camera.service import CameraService
        CameraService.RUN_IN_CALLER = True

    booth = Booth()
    booth.listen_host = args.host
    if args.camera:
        booth.store.local["camera"]["backend"] = args.camera
    port = args.port or int(booth.store.local.get("port", 8765))
    # Download without internet: the guest page gets its own listener on its
    # own port as soon as the profile switches it on; this one stays on --host.
    from .guest_share import for_booth
    threading.Thread(target=for_booth(booth).watch, name="guest-share-watch", daemon=True).start()
    kiosk_url = f"http://127.0.0.1:{port}/"
    if plan.mode == "chrome":
        # Chrome is opened, and opened again when it stops asking (_keep_kiosk).
        threading.Thread(target=_keep_kiosk, args=(kiosk_url, booth), name="kiosk-keeper", daemon=True).start()
    wire_instance(instance, booth)
    from . import waechter
    if waechter.watched():
        # The console: hidden here when the watcher decided so (it must not load
        # user32 itself). The watcher: when it is gone, leave in order.
        fenster.hide_console_for(plan.mode)
        pid = waechter.supervisor_status().get("watcher_pid")
        if pid:
            threading.Thread(target=waechter.watch_parent, args=(waechter.parent_alive(pid), booth.end_session),
                             name="watcher-watch", daemon=True).start()
    app = create_app(booth)
    if plan.mode == "own":
        lifecycle.watch_signals(lambda: booth.shutdown(0))
        server = fenster.run_server(lambda: uvicorn.run(app, host=args.host, port=port, log_level="warning"),
                                    booth.shutdown)
        result = fenster.run_own(kiosk_url, data_dir(), window_opts, instance,
                                 # The own window gave up: Chrome, kept open like in mode "chrome".
                                 open_chrome=lambda url: _keep_kiosk(url, booth, wait=0),
                                 # The web view's own process died: a fresh booth process
                                 # (under the watcher: exit 3, restarted at once).
                                 on_stuck=booth._restart_later,
                                 # Neither reload nor restart in the middle of a guest's run.
                                 busy=booth.session_busy,
                                 # Windows closes the window at sign-out/shutdown: leave in order.
                                 on_session_end=booth.end_session)
        if result == "session_end":
            booth.end_session("windows_shutdown")
            return
        if result == "fallback":
            server.join()                        # Chrome shows the booth; the server's end is the end
            return
        booth.shutdown(0)                        # the window was closed (only possible with --window-debug)
        return
    if camera_on_main:
        # A signal ends the program in order: the camera is handed back
        # first. Through a watcher thread, because this thread is the camera
        # loop and may sit inside the SDK when the signal comes.
        lifecycle.watch_signals(lambda: booth.shutdown(0))

        def _web() -> None:
            try:
                uvicorn.run(app, host=args.host, port=port, log_level="warning")
            finally:
                # Port taken, or the server gave up: no screen, no booth.
                booth.shutdown(1)

        threading.Thread(target=_web, name="web", daemon=True).start()
        booth.camera.run_forever()
        return
    uvicorn.run(app, host=args.host, port=port, log_level="warning")
    # uvicorn handles SIGTERM/SIGINT itself and returns here: leave in order.
    booth.shutdown(0)


if __name__ == "__main__":
    main()
