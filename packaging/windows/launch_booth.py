"""Einstiegspunkt für die gebaute .exe.

``boothcore/__main__.py`` benutzt relative Importe — als Skript gestartet kennt
PyInstaller das Paket nicht. Dieser Starter ruft dieselbe Funktion über den
Paketnamen auf.

The .exe started by the scheduled task is the watcher (``boothcore.waechter``):
it runs the booth as a child process (this same .exe with PHOTOBOX_WATCHED=1)
and starts it again after a crash; a second start brings the running window
forward and ends. One-off commands (``--install``, ``--doctor``,
``--window-selftest``, ``--quit`` …) run directly. Process model and exit
codes: docs/EIGENES-FENSTER.md.
"""
import sys

from boothcore import waechter

if __name__ == "__main__":
    if waechter.should_watch(sys.argv[1:], getattr(sys, "frozen", False)):
        # The watcher imports only what it needs: no web server, no window
        # library. A console process that loaded user32/gdi32 no longer hears
        # Windows sign out or shut down - and the watcher is the one that must.
        raise SystemExit(waechter.main(sys.argv[1:]))
    from boothcore.__main__ import main
    main()
