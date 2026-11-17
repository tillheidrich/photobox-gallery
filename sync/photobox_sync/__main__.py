"""Start: ``python -m photobox_sync`` (öffnet die Status-Seite im Browser).

Einrichten auf dem Event-PC: ``PhotoboxSync.exe --install`` — Autostart bei der
Anmeldung, kein Standby, Verknüpfung auf dem Desktop. Danach läuft das Programm
ohne Fenster im Hintergrund, erreichbar über das Symbol neben der Uhr.
"""
from __future__ import annotations

import argparse
import logging
import socket
import sys
import threading
import time
import webbrowser
from pathlib import Path

import uvicorn

log = logging.getLogger("photobox.sync")


def _already_running(port: int) -> bool:
    """Zweiter Start: nicht zwei Warteschlangen auf denselben Ordnern."""
    with socket.socket() as s:
        s.settimeout(0.5)
        return s.connect_ex(("127.0.0.1", port)) == 0


def _pair_from_file(app) -> None:
    """Kopplung ohne Tastatur: ``kopplung.txt`` neben die .exe legen.

    Für den Fall, dass jemand den Code nicht abtippen soll — Datei hinlegen,
    Programm starten, fertig. Danach wird der Inhalt ersetzt und die Datei
    gelöscht: Umbenennen genügte nicht, denn der Kopplungscode trägt den
    API-Schlüssel im Klartext, und der lag danach unter neuem Namen weiter
    neben dem Programm. Gefunden bei der Durchsicht am 19.09.2026.
    """
    if (app.config.get("server") or {}).get("api_key"):
        return
    from .pairing import redeem_file

    folder = Path(sys.executable).parent if getattr(sys, "frozen", False) else Path.cwd()
    redeem_file([folder], app.pair, log)


def _sagen(text: str) -> None:
    """Eine Meldung, die auch ankommt, wenn es keine Konsole gibt.

    Die .exe ist mit ``console=False`` gebaut — dann sind ``sys.stdout`` und
    ``sys.stderr`` None und ``print()`` kehrt kommentarlos zurück. Der
    Betreiber tippte ``--install`` und bekam: nichts. Kein Fenster, keine
    Zeile, kein Fehler. Ob Autostart, Energiesparen und Firewall geklappt
    haben, war nicht feststellbar — dabei ist genau das der Zweck des
    Aufrufs.
    """
    if sys.stdout is not None:
        print(text)
        return
    try:                                        # nur unter Windows vorhanden
        import ctypes
        ctypes.windll.user32.MessageBoxW(None, text, "Photobox Sync", 0x40)
        return
    except Exception:                           # noqa: BLE001
        pass
    try:                                        # letzter Halt: neben die .exe
        from pathlib import Path
        Path(sys.executable).with_name("einrichtung.txt").write_text(text, encoding="utf-8")
    except Exception:                           # noqa: BLE001
        pass


def main() -> None:
    ap = argparse.ArgumentParser(prog="photobox-sync")
    ap.add_argument("--port", type=int, default=None)
    ap.add_argument("--no-browser", action="store_true")
    ap.add_argument("--no-tray", action="store_true", help="ohne Symbol im Infobereich")
    ap.add_argument("--pair", metavar="CODE", help="Kopplungscode aus der Galerie einlösen")
    ap.add_argument("--install", action="store_true", help="Autostart, Standby aus, Verknüpfung")
    ap.add_argument("--uninstall", action="store_true", help="Autostart und Firewall-Regel entfernen")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    from . import winsetup
    from .standalone import SyncApp, create_app

    if args.install or args.uninstall:
        s = SyncApp(start_threads=False)
        port = args.port or int(s.config.get("port") or 8766)
        steps = winsetup.uninstall() if args.uninstall else winsetup.install(port)
        _sagen(winsetup.report(steps))
        return

    s = SyncApp()
    if args.pair:
        try:
            _sagen("Gekoppelt mit " + s.pair(args.pair)["url"])
        except ValueError as e:
            _sagen("Kopplung fehlgeschlagen: " + str(e))
            raise SystemExit(2)
    else:
        _pair_from_file(s)
    port = args.port or int(s.config.get("port") or 8766)
    if _already_running(port):
        print(f"Photobox Sync läuft bereits — Status: http://127.0.0.1:{port}/")
        if not args.no_browser:
            webbrowser.open(f"http://127.0.0.1:{port}/")
        return

    url = f"http://127.0.0.1:{port}/"
    config = uvicorn.Config(create_app(s), host="127.0.0.1", port=port, log_level="warning")
    server = uvicorn.Server(config)
    threading.Thread(target=server.run, name="web", daemon=True).start()

    if not args.no_browser and not s.config.get("server", {}).get("url"):
        threading.Thread(target=lambda: (time.sleep(2), webbrowser.open(url)), daemon=True).start()

    from . import tray

    def quit_now() -> None:
        server.should_exit = True
        s.agent.stop()
        s.watcher.stop()

    if not args.no_tray and tray.supported():
        icon = sys.executable if getattr(sys, "frozen", False) else ""
        ok = tray.run_tray(title="Photobox Sync", icon_path=icon,
                           on_open=lambda: webbrowser.open(url),
                           on_sync=lambda: (s.outbox.reset_backoff(), s.agent.sync_once()),
                           on_quit=quit_now)
        if ok:
            return
    try:
        while not server.should_exit:
            time.sleep(1)
    except KeyboardInterrupt:
        quit_now()


if __name__ == "__main__":
    main()
