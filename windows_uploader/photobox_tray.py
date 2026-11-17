"""
photobox_tray.py – Windows System-Tray-App für Photobox Gallery Sync.

Abhängigkeiten:
    pip install pystray Pillow

Starten:
    pythonw photobox_tray.py

Bauen (als .exe):
    pyinstaller --windowed --onedir --name PhotoboxSync photobox_tray.py
"""
from __future__ import annotations

import json
import logging
import os
import queue
import sys
import threading
import time
import tkinter as tk
import tkinter.filedialog as fd
import tkinter.messagebox as mb
import tkinter.ttk as ttk
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

# ---------------------------------------------------------------------------
# Logging-Bridge: Python logging → tkinter Queue
# ---------------------------------------------------------------------------

class QueueLogHandler(logging.Handler):
    def __init__(self, log_queue: queue.Queue) -> None:
        super().__init__()
        self.log_queue = log_queue

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self.log_queue.put_nowait(self.format(record))
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Gemeinsamer App-Zustand (thread-sicher)
# ---------------------------------------------------------------------------

@dataclass
class AppState:
    running:    bool = False
    status:     str  = "Gestoppt"
    last_sync:  str  = "–"
    sync_count: int  = 0
    error_count:int  = 0
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def update(self, **kwargs) -> None:
        with self._lock:
            for k, v in kwargs.items():
                setattr(self, k, v)

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "running":     self.running,
                "status":      self.status,
                "last_sync":   self.last_sync,
                "sync_count":  self.sync_count,
                "error_count": self.error_count,
            }


_state     = AppState()
_log_queue: queue.Queue = queue.Queue(maxsize=500)
_stop_event            = threading.Event()

# ---------------------------------------------------------------------------
# Konfiguration lesen / schreiben
# ---------------------------------------------------------------------------

_CONFIG_PATH = Path(os.environ.get("APPDATA", Path.home())) / "PhotoboxSync" / "config.json"

_DEFAULTS: dict = {
    "server_url":   "",
    "api_key":      "",
    "watch_folder": "",
    "sync_interval": 30,
}


def load_config() -> dict:
    try:
        if _CONFIG_PATH.exists():
            with open(_CONFIG_PATH, encoding="utf-8") as f:
                data = json.load(f)
            return {**_DEFAULTS, **data}
    except Exception as exc:
        _log.warning("Konfiguration konnte nicht geladen werden: %s", exc)
    return dict(_DEFAULTS)


def save_config(cfg: dict) -> None:
    _CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(_CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2, ensure_ascii=False)


def validate_config(cfg: dict) -> list[str]:
    errors = []
    if not cfg.get("server_url", "").startswith(("http://", "https://")):
        errors.append("Server-URL fehlt oder ungültig (muss mit http:// oder https:// beginnen).")
    if not cfg.get("api_key", "").strip():
        errors.append("API-Key fehlt.")
    if not cfg.get("watch_folder", "").strip():
        errors.append("Sync-Ordner fehlt.")
    elif not Path(cfg["watch_folder"]).is_dir():
        errors.append(f"Sync-Ordner existiert nicht: {cfg['watch_folder']}")
    return errors


# ---------------------------------------------------------------------------
# Sync-Worker (Daemon-Thread)
# ---------------------------------------------------------------------------

def _sync_worker() -> None:
    """Synchronisiert Dateien mit dem Photobox-Server."""
    _log.info("Sync-Worker gestartet.")
    while not _stop_event.is_set():
        cfg = load_config()
        errs = validate_config(cfg)
        if errs:
            _state.update(status="Konfigurationsfehler", running=False)
            for e in errs:
                _log.warning("Konfig: %s", e)
            _stop_event.wait(30)
            continue

        _state.update(status="Synchronisiere…", running=True)
        try:
            _do_sync(cfg)
        except Exception as exc:
            _log.error("Sync-Fehler: %s", exc)
            _state.update(
                status=f"Fehler: {exc}",
                error_count=_state.error_count + 1,
            )

        interval = max(10, int(cfg.get("sync_interval", 30)))
        _stop_event.wait(interval)

    _state.update(status="Gestoppt", running=False)
    _log.info("Sync-Worker beendet.")


def _do_sync(cfg: dict) -> None:
    """Führt einen Sync-Durchlauf durch."""
    import urllib.request
    import urllib.error

    folder  = Path(cfg["watch_folder"])
    server  = cfg["server_url"].rstrip("/")
    api_key = cfg["api_key"]

    extensions = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".mp4", ".mov"}
    files = [f for f in folder.iterdir() if f.suffix.lower() in extensions and f.is_file()]

    if not files:
        _state.update(status="Warte auf Dateien…", last_sync=_now())
        return

    uploaded = 0
    for fpath in files:
        if _stop_event.is_set():
            break
        try:
            with open(fpath, "rb") as fh:
                data = fh.read()

            req = urllib.request.Request(
                f"{server}/api/upload",
                data=data,
                headers={
                    "X-API-Key":      api_key,
                    "X-Filename":     fpath.name,
                    "Content-Type":   "application/octet-stream",
                },
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=30) as resp:
                if resp.status < 300:
                    uploaded += 1
                    _log.info("✓ %s hochgeladen.", fpath.name)
                    try:
                        fpath.unlink()
                    except FileNotFoundError:
                        pass

        except urllib.error.HTTPError as exc:
            _log.warning("HTTP %d beim Upload von %s: %s", exc.code, fpath.name, exc.reason)
        except Exception as exc:
            _log.warning("Fehler beim Upload von %s: %s", fpath.name, exc)
            _state.update(error_count=_state.error_count + 1)

    _state.update(
        status=f"Bereit (letzte Sync: {_now()})",
        last_sync=_now(),
        sync_count=_state.sync_count + uploaded,
        running=True,
    )


def _now() -> str:
    from datetime import datetime
    return datetime.now().strftime("%H:%M:%S")


# ---------------------------------------------------------------------------
# Logger einrichten
# ---------------------------------------------------------------------------

_log = logging.getLogger("photobox_tray")
_log.setLevel(logging.DEBUG)

_fmt = logging.Formatter("%(asctime)s  %(levelname)-7s  %(message)s", "%H:%M:%S")

_queue_handler = QueueLogHandler(_log_queue)
_queue_handler.setFormatter(_fmt)
_log.addHandler(_queue_handler)

_stream_handler = logging.StreamHandler(sys.stdout)
_stream_handler.setFormatter(_fmt)
_log.addHandler(_stream_handler)


# ---------------------------------------------------------------------------
# Tray-Icon erzeugen
# ---------------------------------------------------------------------------

def _create_icon_image(color: str = "#1976d2"):
    """Erstellt ein einfaches quadratisches Icon als PIL Image."""
    try:
        from PIL import Image, ImageDraw
        img  = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)
        draw.rounded_rectangle([4, 4, 60, 60], radius=12, fill=color)
        draw.text((18, 18), "📸", fill="white")
        return img
    except Exception:
        # Fallback: einfarbiges Bild ohne Pillow-Features
        try:
            from PIL import Image
            r = int(color[1:3], 16) if color.startswith("#") and len(color) == 7 else 25
            g = int(color[3:5], 16) if color.startswith("#") and len(color) == 7 else 118
            b = int(color[5:7], 16) if color.startswith("#") and len(color) == 7 else 210
            return Image.new("RGB", (64, 64), (r, g, b))
        except Exception:
            return None


# ---------------------------------------------------------------------------
# Config-Dialog
# ---------------------------------------------------------------------------

class ConfigDialog(tk.Toplevel):
    def __init__(self, parent: tk.Tk) -> None:
        super().__init__(parent)
        self.title("Photobox Sync – Einstellungen")
        self.resizable(False, False)
        self.grab_set()

        cfg = load_config()

        pad = {"padx": 8, "pady": 4}

        # Server-URL
        tk.Label(self, text="Server-URL:", anchor="w").grid(row=0, column=0, sticky="w", **pad)
        self._url = tk.StringVar(value=cfg.get("server_url", ""))
        tk.Entry(self, textvariable=self._url, width=40).grid(row=0, column=1, columnspan=2, sticky="ew", **pad)

        # API-Key
        tk.Label(self, text="API-Key:", anchor="w").grid(row=1, column=0, sticky="w", **pad)
        self._key = tk.StringVar(value=cfg.get("api_key", ""))
        tk.Entry(self, textvariable=self._key, width=40, show="•").grid(row=1, column=1, columnspan=2, sticky="ew", **pad)

        # Sync-Ordner
        tk.Label(self, text="Sync-Ordner:", anchor="w").grid(row=2, column=0, sticky="w", **pad)
        self._folder = tk.StringVar(value=cfg.get("watch_folder", ""))
        tk.Entry(self, textvariable=self._folder, width=32).grid(row=2, column=1, sticky="ew", **pad)
        tk.Button(self, text="…", width=3, command=self._browse_folder).grid(row=2, column=2, **pad)

        # Intervall
        tk.Label(self, text="Intervall (Sek.):", anchor="w").grid(row=3, column=0, sticky="w", **pad)
        self._interval = tk.IntVar(value=int(cfg.get("sync_interval", 30)))
        tk.Spinbox(self, from_=10, to=3600, textvariable=self._interval, width=8).grid(row=3, column=1, sticky="w", **pad)

        # Buttons
        frame = tk.Frame(self)
        frame.grid(row=4, column=0, columnspan=3, pady=8)
        tk.Button(frame, text="Speichern", command=self._save, width=12).pack(side="left", padx=4)
        tk.Button(frame, text="Abbrechen", command=self.destroy, width=12).pack(side="left", padx=4)

        self.columnconfigure(1, weight=1)

    def _browse_folder(self) -> None:
        path = fd.askdirectory(title="Sync-Ordner wählen")
        if path:
            self._folder.set(path)

    def _save(self) -> None:
        cfg = {
            "server_url":    self._url.get().strip(),
            "api_key":       self._key.get().strip(),
            "watch_folder":  self._folder.get().strip(),
            "sync_interval": self._interval.get(),
        }
        errors = validate_config(cfg)
        if errors:
            mb.showerror("Ungültige Konfiguration", "\n".join(errors), parent=self)
            return
        save_config(cfg)
        _log.info("Konfiguration gespeichert.")
        self.destroy()


# ---------------------------------------------------------------------------
# Status-Fenster (Haupt-UI)
# ---------------------------------------------------------------------------

_status_window: Optional["StatusWindow"] = None


class StatusWindow(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("Photobox Sync")
        self.geometry("600x420")
        self.resizable(True, True)
        self.protocol("WM_DELETE_WINDOW", self._minimize)

        # Status-Karten
        info_frame = tk.Frame(self, bd=1, relief="groove")
        info_frame.pack(fill="x", padx=10, pady=8)

        self._lbl_status    = self._card(info_frame, "Status",        "–", 0)
        self._lbl_last_sync = self._card(info_frame, "Letzte Sync",   "–", 1)
        self._lbl_uploaded  = self._card(info_frame, "Hochgeladen",   "0", 2)
        self._lbl_errors    = self._card(info_frame, "Fehler",        "0", 3)

        # Button-Leiste
        btn_frame = tk.Frame(self)
        btn_frame.pack(fill="x", padx=10)
        self._btn_toggle = tk.Button(btn_frame, text="▶ Start", width=12, command=self._toggle)
        self._btn_toggle.pack(side="left", padx=4, pady=4)
        tk.Button(btn_frame, text="⚙ Einstellungen", command=self._open_settings).pack(side="left", padx=4, pady=4)
        tk.Button(btn_frame, text="✕ Beenden",        command=self._quit).pack(side="right", padx=4, pady=4)

        # Log-Bereich
        log_frame = tk.Frame(self)
        log_frame.pack(fill="both", expand=True, padx=10, pady=(0, 10))

        self._log_text = tk.Text(
            log_frame, bg="#1e1e1e", fg="#d4d4d4",
            font=("Consolas", 9), state="disabled", wrap="word",
        )
        scrollbar = ttk.Scrollbar(log_frame, command=self._log_text.yview)
        self._log_text.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side="right", fill="y")
        self._log_text.pack(side="left", fill="both", expand=True)

        # Worker-Thread
        self._worker_thread: Optional[threading.Thread] = None
        self._poll_ui()

    def _card(self, parent: tk.Frame, label: str, value: str, col: int) -> tk.Label:
        frame = tk.Frame(parent, bd=1, relief="sunken", padx=6, pady=4)
        frame.grid(row=0, column=col, sticky="ew", padx=4, pady=4)
        parent.columnconfigure(col, weight=1)
        tk.Label(frame, text=label, font=("Segoe UI", 8), fg="gray").pack(anchor="w")
        lbl = tk.Label(frame, text=value, font=("Segoe UI", 11, "bold"))
        lbl.pack(anchor="w")
        return lbl

    def _toggle(self) -> None:
        if _state.running:
            _stop_event.set()
            self._btn_toggle.config(text="▶ Start")
            _log.info("Sync wird gestoppt…")
        else:
            _stop_event.clear()
            cfg_errors = validate_config(load_config())
            if cfg_errors:
                mb.showerror("Konfigurationsfehler", "\n".join(cfg_errors))
                return
            self._start_worker()
            self._btn_toggle.config(text="■ Stop")

    def _start_worker(self) -> None:
        if self._worker_thread and self._worker_thread.is_alive():
            return
        self._worker_thread = threading.Thread(target=_sync_worker, daemon=True, name="sync-worker")
        self._worker_thread.start()

    def _open_settings(self) -> None:
        ConfigDialog(self)

    def _minimize(self) -> None:
        self.withdraw()

    def _quit(self) -> None:
        _stop_event.set()
        self.destroy()
        sys.exit(0)

    def _poll_ui(self) -> None:
        """Aktualisiert die UI alle 500 ms aus dem Queue."""
        snap = _state.snapshot()
        self._lbl_status.config(    text=snap["status"])
        self._lbl_last_sync.config( text=snap["last_sync"])
        self._lbl_uploaded.config(  text=str(snap["sync_count"]))
        self._lbl_errors.config(    text=str(snap["error_count"]),
                                    fg="red" if snap["error_count"] > 0 else "black")

        # Log-Nachrichten einlesen
        lines = []
        try:
            while True:
                lines.append(_log_queue.get_nowait())
        except queue.Empty:
            pass

        if lines:
            self._log_text.config(state="normal")
            for line in lines:
                self._log_text.insert("end", line + "\n")
            self._log_text.see("end")
            self._log_text.config(state="disabled")

        self.after(500, self._poll_ui)


# ---------------------------------------------------------------------------
# Tray-Setup
# ---------------------------------------------------------------------------

def setup_tray(root: StatusWindow) -> None:
    try:
        import pystray
    except ImportError:
        _log.warning("pystray nicht installiert – kein System-Tray-Icon.")
        return

    icon_img = _create_icon_image()
    if icon_img is None:
        _log.warning("Kein Icon-Bild verfügbar.")
        return

    def on_show(_icon, _item) -> None:
        root.deiconify()
        root.lift()

    def on_quit(_icon, _item) -> None:
        _stop_event.set()
        _icon.stop()
        root.destroy()
        sys.exit(0)

    menu = pystray.Menu(
        pystray.MenuItem("Photobox Sync anzeigen", on_show, default=True),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Beenden", on_quit),
    )

    icon = pystray.Icon("photobox_sync", icon_img, "Photobox Sync", menu)
    tray_thread = threading.Thread(target=icon.run, daemon=True, name="tray-icon")
    tray_thread.start()
    _log.info("System-Tray-Icon gestartet.")


# ---------------------------------------------------------------------------
# Entry-Point
# ---------------------------------------------------------------------------

def main() -> None:
    global _status_window
    _status_window = StatusWindow()
    setup_tray(_status_window)
    _log.info("Photobox Sync gestartet. Version 3.1.0")
    _status_window.mainloop()


if __name__ == "__main__":
    main()
