"""A tray icon for the sync program — Windows only, no extra dependency.

The program runs for a whole event without anybody looking at it. It still has
to be findable: an icon next to the clock, a right click, "Status öffnen".
Written against the Win32 API through ctypes on purpose — pystray would pull in
Pillow and roughly three megabytes into the .exe for one 16×16 picture.

Everything fails soft. If the icon cannot be created — a locked-down machine, a
session without a desktop — the program keeps running without it and only says
so in the log. A missing icon is a cosmetic problem; a program that refuses to
start over one is not.
"""
from __future__ import annotations

import logging
import os
import threading
from typing import Callable, Optional

log = logging.getLogger("photobox.sync")

WM_DESTROY = 0x0002
WM_COMMAND = 0x0111
WM_USER = 0x0400
WM_TRAY = WM_USER + 20
WM_LBUTTONDBLCLK = 0x0203
WM_RBUTTONUP = 0x0205
NIM_ADD, NIM_MODIFY, NIM_DELETE = 0, 1, 2
NIF_MESSAGE, NIF_ICON, NIF_TIP = 0x01, 0x02, 0x04
IDI_APPLICATION = 32512
MF_STRING, MF_SEPARATOR = 0x0000, 0x0800
TPM_RIGHTBUTTON, TPM_RETURNCMD = 0x0002, 0x0100

ID_OPEN, ID_SYNC, ID_QUIT = 1001, 1002, 1003


def supported() -> bool:
    return os.name == "nt"


class Tray:
    """Hidden window plus one icon. ``run()`` blocks until the icon goes away."""

    def __init__(self, *, title: str, on_open: Callable[[], None],
                 on_sync: Callable[[], None], on_quit: Callable[[], None],
                 icon_path: str = ""):
        self.title = title[:127]
        self.on_open, self.on_sync, self.on_quit = on_open, on_sync, on_quit
        self.icon_path = icon_path
        self._hwnd = None
        self._data = None
        self._stop = threading.Event()

    # ── Win32 ─────────────────────────────────────────────
    def _build(self):
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        shell32 = ctypes.windll.shell32
        kernel32 = ctypes.windll.kernel32

        WNDPROC = ctypes.WINFUNCTYPE(ctypes.c_longlong, wintypes.HWND, wintypes.UINT,
                                     wintypes.WPARAM, wintypes.LPARAM)

        class WNDCLASS(ctypes.Structure):
            _fields_ = [("style", wintypes.UINT), ("lpfnWndProc", WNDPROC),
                        ("cbClsExtra", ctypes.c_int), ("cbWndExtra", ctypes.c_int),
                        ("hInstance", wintypes.HINSTANCE), ("hIcon", wintypes.HICON),
                        ("hCursor", wintypes.HANDLE), ("hbrBackground", wintypes.HBRUSH),
                        ("lpszMenuName", wintypes.LPCWSTR), ("lpszClassName", wintypes.LPCWSTR)]

        class NOTIFYICONDATA(ctypes.Structure):
            _fields_ = [("cbSize", wintypes.DWORD), ("hWnd", wintypes.HWND),
                        ("uID", wintypes.UINT), ("uFlags", wintypes.UINT),
                        ("uCallbackMessage", wintypes.UINT), ("hIcon", wintypes.HICON),
                        ("szTip", wintypes.WCHAR * 128)]

        def wndproc(hwnd, msg, wparam, lparam):
            if msg == WM_TRAY:
                if lparam == WM_LBUTTONDBLCLK:
                    self._safe(self.on_open)
                elif lparam == WM_RBUTTONUP:
                    self._menu(user32, hwnd)
            elif msg == WM_COMMAND:
                cmd = wparam & 0xFFFF
                if cmd == ID_OPEN:
                    self._safe(self.on_open)
                elif cmd == ID_SYNC:
                    self._safe(self.on_sync)
                elif cmd == ID_QUIT:
                    user32.DestroyWindow(hwnd)
            elif msg == WM_DESTROY:
                user32.PostQuitMessage(0)
            return user32.DefWindowProcW(hwnd, msg, wparam, lparam)

        self._proc = WNDPROC(wndproc)          # muss am Objekt hängen bleiben, sonst räumt Python es weg
        cls = WNDCLASS()
        cls.lpfnWndProc = self._proc
        cls.hInstance = kernel32.GetModuleHandleW(None)
        cls.lpszClassName = "PhotoboxSyncTray"
        user32.RegisterClassW(ctypes.byref(cls))
        hwnd = user32.CreateWindowExW(0, cls.lpszClassName, self.title, 0, 0, 0, 0, 0,
                                      None, None, cls.hInstance, None)
        icon = None
        if self.icon_path:
            icon = shell32.ExtractIconW(cls.hInstance, self.icon_path, 0) or None
        if not icon:
            icon = user32.LoadIconW(None, ctypes.c_wchar_p(IDI_APPLICATION))
        data = NOTIFYICONDATA()
        data.cbSize = ctypes.sizeof(NOTIFYICONDATA)
        data.hWnd = hwnd
        data.uID = 1
        data.uFlags = NIF_MESSAGE | NIF_ICON | NIF_TIP
        data.uCallbackMessage = WM_TRAY
        data.hIcon = icon
        data.szTip = self.title
        shell32.Shell_NotifyIconW(NIM_ADD, ctypes.byref(data))
        self._hwnd, self._data, self._user32, self._shell32 = hwnd, data, user32, shell32
        return user32, ctypes, wintypes

    def _menu(self, user32, hwnd):
        import ctypes

        menu = user32.CreatePopupMenu()
        user32.AppendMenuW(menu, MF_STRING, ID_OPEN, "Status öffnen")
        user32.AppendMenuW(menu, MF_STRING, ID_SYNC, "Jetzt synchronisieren")
        user32.AppendMenuW(menu, MF_SEPARATOR, 0, None)
        user32.AppendMenuW(menu, MF_STRING, ID_QUIT, "Beenden")
        pt = ctypes.wintypes.POINT()
        user32.GetCursorPos(ctypes.byref(pt))
        user32.SetForegroundWindow(hwnd)       # sonst bleibt das Menü offen stehen
        cmd = user32.TrackPopupMenu(menu, TPM_RIGHTBUTTON | TPM_RETURNCMD, pt.x, pt.y, 0, hwnd, None)
        user32.DestroyMenu(menu)
        if cmd:
            user32.PostMessageW(hwnd, WM_COMMAND, cmd, 0)

    @staticmethod
    def _safe(fn: Callable[[], None]) -> None:
        try:
            fn()
        except Exception as e:
            log.warning("Tray-Aktion fehlgeschlagen: %s", e)

    # ── Betrieb ───────────────────────────────────────────
    def run(self) -> bool:
        """Message loop. Returns False when there is no tray to talk to."""
        if not supported():
            return False
        try:
            import ctypes
            from ctypes import wintypes

            user32, ctypes_mod, _ = self._build()
            msg = wintypes.MSG()
            while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
                user32.TranslateMessage(ctypes.byref(msg))
                user32.DispatchMessageW(ctypes.byref(msg))
            return True
        except Exception as e:
            log.warning("Kein Symbol im Infobereich (%s) — das Programm läuft trotzdem", e)
            return False
        finally:
            self.remove()

    def remove(self) -> None:
        try:
            if self._data is not None:
                import ctypes
                self._shell32.Shell_NotifyIconW(NIM_DELETE, ctypes.byref(self._data))
                self._data = None
        except Exception:
            pass


def run_tray(*, title: str, on_open: Callable[[], None], on_sync: Callable[[], None],
             on_quit: Callable[[], None], icon_path: str = "") -> bool:
    """Blocks while the icon exists. False means: no icon here, carry on without."""
    tray = Tray(title=title, on_open=on_open, on_sync=on_sync, on_quit=on_quit, icon_path=icon_path)
    ok = tray.run()
    if ok:
        on_quit()
    return ok


def wait_forever(stop: Optional[threading.Event] = None) -> None:
    """Fallback for machines without a tray: keep the program alive."""
    (stop or threading.Event()).wait()
