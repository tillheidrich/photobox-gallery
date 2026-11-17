"""Canon EOS über das offizielle EDSDK (Windows, auch Linux-x64-Builds).

Warum nicht gphoto2 unter Windows: gibt es dort nicht nativ, und unter Linux
sind manche Canon-Modelle (z. B. EOS M50) mit gphoto2 wackelig. Das SDK deckt die aktuellen
EOS-, EOS-M- und EOS-R-Modelle ab (Liste in der SDK-Doku).

Wichtig:
* Das SDK steht unter Canon-NDA und liegt **nicht** im Repo. Jeder Betreiber
  holt es sich über das Canon Developer Programme und trägt den Pfad zur
  ``EDSDK.dll`` (plus ``EdsImage.dll`` daneben) in der lokalen Konfiguration ein.
* Alle Aufrufe laufen auf genau einem Thread (dem Kamera-Thread des
  ``CameraService``). Das SDK verlangt das.
* Ereignisse (fertiges Foto) kommen über ``EdsGetEvent`` bzw. die
  Windows-Nachrichtenschleife — beides wird hier regelmäßig bedient.

STATUS: gegen die SDK-Dokumentation geschrieben, **noch nicht an der echten
M50 getestet**. Vor dem ersten Event: ``python -m boothcore.tools.cameratest``
(200 Auslösungen am Stück).
"""
from __future__ import annotations

import ctypes
import os
import sys
import time
from ctypes import POINTER, byref, c_char, c_int32, c_uint32, c_uint64, c_void_p
from pathlib import Path
from typing import Optional

import logging

from .base import CameraBackend, CameraError, CaptureRefused

log = logging.getLogger("booth.camera.edsdk")

EDS_ERR_OK = 0x00000000
EDS_ERR_DEVICE_BUSY = 0x00000081
EDS_ERR_OBJECT_NOTREADY = 0x0000A102
EDS_ERR_TAKE_PICTURE_AF_NG = 0x00008D01

kEdsPropID_SaveTo = 0x0000000B
kEdsSaveTo_Host = 2
kEdsPropID_Evf_OutputDevice = 0x00000500
kEdsPropID_Evf_Mode = 0x00000501
kEdsEvfOutputDevice_TFT = 1
kEdsEvfOutputDevice_PC = 2

kEdsPropID_AEMode = 0x00000400
# Namen der Betriebsarten laut EDSDK-Referenz. Nur die, die an einer Fotobox
# vorkommen; alles andere meldet die Box als Zahl, damit nichts Falsches dasteht.
# 8 stand hier als "Auto" — laut EDSDK-Referenz ist 8 die AE-Speicherung und
# 9 die Vollautomatik; neuere Gehäuse (M50, R-Reihe) melden für A+ die 19.
AE_MODES = {0: "P", 1: "Tv", 2: "Av", 3: "M", 4: "Bulb", 8: "AE-Speicher",
            9: "Auto", 19: "A+"}

kEdsPropID_WhiteBalance = 0x00000106
kEdsPropID_ISOSpeed = 0x00000402
kEdsPropID_Av = 0x00000405
kEdsPropID_Tv = 0x00000406

# Werte-Tabellen laut EDSDK-Referenz (Drittelstufen). An echter Hardware prüfen.
ISO_CODES = {"auto": 0x00, "100": 0x48, "125": 0x4B, "160": 0x4D, "200": 0x50, "250": 0x53, "320": 0x55,
             "400": 0x58, "500": 0x5B, "640": 0x5D, "800": 0x60, "1000": 0x63, "1250": 0x65, "1600": 0x68,
             "2000": 0x6B, "2500": 0x6D, "3200": 0x70, "4000": 0x73, "5000": 0x75, "6400": 0x78,
             "8000": 0x7B, "10000": 0x7D, "12800": 0x80, "25600": 0x88}
AV_CODES = {"1.4": 0x10, "1.6": 0x13, "1.8": 0x15, "2": 0x18, "2.2": 0x1B, "2.5": 0x1D, "2.8": 0x20,
            "3.2": 0x23, "3.5": 0x25, "4": 0x28, "4.5": 0x2B, "5": 0x2D, "5.6": 0x30, "6.3": 0x33, "7.1": 0x35,
            "8": 0x38, "9": 0x3B, "10": 0x3D, "11": 0x40, "13": 0x43, "14": 0x45, "16": 0x48, "18": 0x4B,
            "20": 0x4D, "22": 0x50}
TV_CODES = {"30": 0x10, "15": 0x18, "8": 0x20, "4": 0x28, "2": 0x30, "1": 0x38, "0.5": 0x40, "1/4": 0x48,
            "1/8": 0x50, "1/10": 0x53, "1/13": 0x55, "1/15": 0x58, "1/20": 0x5B, "1/25": 0x5D, "1/30": 0x60,
            "1/40": 0x63, "1/50": 0x65, "1/60": 0x68, "1/80": 0x6B, "1/100": 0x6D, "1/125": 0x70,
            "1/160": 0x73, "1/200": 0x75, "1/250": 0x78, "1/320": 0x7B, "1/400": 0x7D, "1/500": 0x80,
            "1/640": 0x83, "1/800": 0x85, "1/1000": 0x88, "1/1250": 0x8B, "1/1600": 0x8D, "1/2000": 0x90,
            "1/4000": 0x98}
WB_CODES = {"auto": 0, "daylight": 1, "cloudy": 2, "tungsten": 3, "fluorescent": 4, "flash": 5, "shade": 8}

kEdsCameraCommand_ExtendShutDownTimer = 0x00000001
kEdsCameraCommand_PressShutterButton = 0x00000004
kEdsCameraCommand_ShutterButton_OFF = 0x00000000
kEdsCameraCommand_ShutterButton_Completely = 0x00000003
kEdsCameraCommand_ShutterButton_Completely_NonAF = 0x00010003

kEdsObjectEvent_All = 0x00000200
kEdsObjectEvent_DirItemRequestTransfer = 0x00000208

kEdsFileCreateDisposition_CreateAlways = 1
kEdsAccess_ReadWrite = 2

EDS_MAX_NAME = 256

# Movie on the camera (boomerang/video source "camera"). Property and command
# numbers from the public EDSDK reference; the order and the timings were
# measured on a real EOS M50 over USB (17.11.2026): Record=4 in photo mode is
# refused (0x81); after MovieSelectSwON the mode changes within ~2 s and
# Record=4 starts, Record=0 stops; the file announces itself 0.5-4 s after
# the stop as a "directory item created" event; SaveTo must be the camera.
kEdsPropID_Record = 0x00000510
kEdsRecord_Start = 4
kEdsRecord_Stop = 0
kEdsSaveTo_Camera = 1
kEdsCameraCommand_MovieSelectSwON = 0x00000107
kEdsCameraCommand_MovieSelectSwOFF = 0x00000108
kEdsObjectEvent_DirItemCreated = 0x00000204
MOVIE_EXTENSIONS = (".MP4", ".MOV")
# Bounded waits of the movie path: the guest never waits longer than this for
# a camera that does not play along before the booth films from the live view.
MOVIE_MODE_WAIT = 3.0       # mode dial value changes after MovieSelectSwON
MOVIE_START_WAIT = 3.0      # Record=4 is retried this long
MOVIE_FILE_WAIT = 8.0       # the new file shows up after the stop
MOVIE_RESTORE_WAIT = 3.0    # back in photo mode afterwards
MOVIE_DOWNLOAD_PART = 64 * 1024 * 1024   # bytes per EdsDownload call of a movie


# The camera's date and time (EDSDK reference: kEdsPropID_DateTime, data type
# kEdsDataType_Time, an EdsTime of seven EdsUInt32). Newer bodies also know a
# UTC time and a time zone (0x01000016, 0x01000017); their encoding is not
# documented well enough to write them blind, so only the local clock is set.
kEdsPropID_DateTime = 0x00000006
# Battery charge in percent; 0xFFFFFFFF while the camera runs on its mains
# adapter (DR-E… coupler). EDSDK reference, kEdsPropID_BatteryLevel.
kEdsPropID_BatteryLevel = 0x00000008
kEdsBatteryLevel_AC = 0xFFFFFFFF


class EdsTime(ctypes.Structure):
    _fields_ = [("year", c_uint32), ("month", c_uint32), ("day", c_uint32), ("hour", c_uint32),
                ("minute", c_uint32), ("second", c_uint32), ("milliseconds", c_uint32)]


class EdsCapacity(ctypes.Structure):
    _fields_ = [("numberOfFreeClusters", c_int32), ("bytesPerSector", c_int32), ("reset", c_int32)]


class EdsDirectoryItemInfo(ctypes.Structure):
    _fields_ = [
        ("size", c_uint64),
        ("isFolder", c_int32),
        ("groupID", c_uint32),
        ("option", c_uint32),
        ("szFileName", c_char * EDS_MAX_NAME),
        ("format", c_uint32),
        ("dateTime", c_uint32),
    ]


if os.name == "nt":
    _FUNCTYPE = ctypes.WINFUNCTYPE
else:
    _FUNCTYPE = ctypes.CFUNCTYPE

EdsObjectEventHandler = _FUNCTYPE(c_uint32, c_uint32, c_void_p, c_void_p)
EdsStateEventHandler = _FUNCTYPE(c_uint32, c_uint32, c_uint32, c_void_p)

kEdsStateEvent_All = 0x00000300
kEdsStateEvent_CaptureError = 0x00000305
kEdsStateEvent_AfResult = 0x00000309
kEdsPropID_DriveMode = 0x00000401
kEdsCameraCommand_ShutterButton_Halfway = 0x00000001

# How long a capture may take from the full press to the picture.
CAPTURE_TIMEOUT = 15.0
# Focusing gets this long after the half press before the booth fires anyway.
AF_WAIT = 0.8
# The button stays down this long at most. A mirrorless body drops the shot
# when the button comes up before it has fired.
HOLD_MAX = 3.0
# "No focus" is answered with this many more presses without autofocus, each
# after the button came up and this pause (seconds) went by.
FOCUS_RETRIES = 3
FOCUS_RETRY_PAUSE = 0.3
# A whole capture, every retry included, ends within this many seconds. The
# caller (CameraService.capture) gives up after 40 s; a press after that would
# fire a picture nobody waits for any more.
CAPTURE_DEADLINE = 30.0
# Refusals that went away only by running the photo setup again (SaveTo host,
# capacity, single shot, live view). On the EOS M50 "card NG" (0x8D07) is what
# a missing host-capacity announcement looks like after a movie, sleep or a
# turn of the dial. Answered within the same shot by the setup and one more
# press; if the camera still refuses, it is opened afresh (CameraError).
SETUP_REFUSALS = (0x8D06, 0x8D07, 0x8D08, 0x8D0C, 0x8D0E)
# Of those, the ones that say "movie mode": movie mode off comes first.
MOVIE_MODE_REFUSALS = (0x8D0C, 0x8D0E)


def is_shot_refusal(code: int) -> bool:
    """EDS_ERR_TAKE_PICTURE_* (0x8D00-0x8DFF): the camera refused this shot —
    no focus, card full, dial on movie. Not a broken link."""
    return 0x8D00 <= (code or 0) <= 0x8DFF


# What the camera says when it refuses to fire, in words for the person
# standing at the booth. Codes from EDSDKErrors.h (EDS_ERR_TAKE_PICTURE_*).
CAPTURE_REFUSALS = {
    0x8D01: "Die Kamera findet keinen Fokus.",
    0x8D03: "Die Kamera hat die Spiegelverriegelung an. Bitte im Kameramenü ausschalten.",
    0x8D04: "Die Kamera reinigt gerade den Sensor. Bitte kurz warten.",
    0x8D05: "Die Kamera steht auf leise Aufnahme und löst so nicht aus. Bitte im Kameramenü ausschalten.",
    0x8D06: "Die Kamera löst ohne Speicherkarte nicht aus. Karte einlegen oder im Kameramenü „Auslösen ohne Karte“ erlauben.",
    0x8D07: "Die Speicherkarte der Kamera ist voll oder defekt.",
    0x8D08: "Die Speicherkarte der Kamera ist schreibgeschützt (Schieber an der Karte).",
    0x8D0A: "Der Blitz lädt noch.",
    0x8D0B: "An der Kamera sitzt kein Objektiv, oder es ist nicht eingerastet.",
    0x8D0C: "Die Kamera steht auf Film. Bitte das Moduswahlrad auf M oder Av stellen.",
    0x8D0D: "Die Kamera lässt in diesem Modus kein Auslösen zu. Bitte das Moduswahlrad auf M oder Av stellen.",
    0x8D0E: "Die Kamera steht auf Film. Bitte das Moduswahlrad auf M oder Av stellen.",
    0x8D0F: "Das Objektiv ist eingefahren. Bitte am Objektiv den Schalter schieben und es herausdrehen.",
}


def _pump_windows_messages() -> None:
    if os.name != "nt":
        return
    import ctypes.wintypes as wt

    user32 = ctypes.windll.user32
    msg = wt.MSG()
    PM_REMOVE = 1
    while user32.PeekMessageW(byref(msg), None, 0, 0, PM_REMOVE):
        user32.TranslateMessage(byref(msg))
        user32.DispatchMessageW(byref(msg))


_CF = None


def _pump_mac_runloop(seconds: float = 0.002) -> None:
    """Let the macOS run loop turn once.

    The macOS build of the EDSDK reports cameras and delivers object events
    through the CoreFoundation run loop of the calling thread. Without a turn
    the camera list stays empty and no picture ever arrives. Measured on a
    real EOS M50 on 2026-11-14.
    """
    global _CF
    if sys.platform != "darwin":
        return
    try:
        if _CF is None:
            cf = ctypes.CDLL("/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation")
            cf.CFRunLoopRunInMode.argtypes = [c_void_p, ctypes.c_double, ctypes.c_bool]
            cf.CFRunLoopRunInMode.restype = c_int32
            _CF = (cf, c_void_p.in_dll(cf, "kCFRunLoopDefaultMode"))
        cf, mode = _CF
        cf.CFRunLoopRunInMode(mode, seconds, False)
    except Exception:                                 # noqa: BLE001 — pumping must never break the camera
        pass


def _terminate_quietly(sdk) -> None:
    try:
        sdk.EdsTerminateSDK()
    except Exception:                                 # noqa: BLE001 — the process is ending anyway
        pass


class EdsdkCamera(CameraBackend):
    name = "edsdk"
    # How long open() waits for the camera to appear in the SDK's list. Right
    # after EdsInitializeSDK the list can still be empty although the camera
    # is connected and switched on.
    FIND_TIMEOUT = 3.0
    # Seconds between two event polls while only the live view is running.
    EVENT_INTERVAL = 0.3
    settable = ("iso", "aperture", "shutter", "wb")
    TABLES = {"iso": (kEdsPropID_ISOSpeed, ISO_CODES), "aperture": (kEdsPropID_Av, AV_CODES),
              "shutter": (kEdsPropID_Tv, TV_CODES), "wb": (kEdsPropID_WhiteBalance, WB_CODES)}

    def shooting_mode(self) -> str:
        """Was am Moduswahlrad steht: M, Av, Tv, P … oder "" wenn nicht lesbar."""
        if not self._sdk or not self._cam:
            return ""
        try:
            value = c_uint32(0)
            err = self._sdk.EdsGetPropertyData(self._cam, kEdsPropID_AEMode, 0, 4, byref(value))
            if err != EDS_ERR_OK:
                return ""
            return AE_MODES.get(value.value, str(value.value))
        except Exception:                       # noqa: BLE001 — eine Anzeige darf nichts umwerfen
            return ""

    def battery_level(self):
        """Percent (0–100), "ac" on the mains adapter, None if not readable.

        EDSDK reference: kEdsPropID_BatteryLevel, an EdsUInt32; 0xFFFFFFFF
        means AC power. Read like the dial (``shooting_mode``) — on the
        camera thread, between two live-view frames, never during a shot.
        """
        if not self._sdk or not self._cam:
            return None
        try:
            value = c_uint32(0)
            if self._sdk.EdsGetPropertyData(self._cam, kEdsPropID_BatteryLevel, 0, 4, byref(value)) != EDS_ERR_OK:
                return None
        except Exception:                       # noqa: BLE001 — unread is "not known"
            return None
        if value.value == kEdsBatteryLevel_AC:
            return "ac"
        return value.value if 0 <= value.value <= 100 else None

    def apply_settings(self, settings: dict) -> dict:
        applied, failed = {}, {}
        for key, value in (settings or {}).items():
            if value in (None, ""):
                continue
            if key not in self.TABLES:
                failed[key] = "unbekannt"
                continue
            prop, table = self.TABLES[key]
            code = table.get(str(value).replace("f/", "").strip())
            if code is None:
                failed[key] = f"Wert {value} nicht in der Tabelle"
                continue
            data = c_uint32(code)
            err = self._sdk.EdsSetPropertyData(self._cam, prop, 0, 4, byref(data)) if self._sdk else -1
            if err == EDS_ERR_OK:
                applied[key] = value
            else:
                failed[key] = f"Kamera lehnt ab (0x{err & 0xFFFFFFFF:08X}) — Moduswahlrad auf M/Av/Tv?"
        return {"applied": applied, "failed": failed}

    def read_settings(self, keys) -> dict:
        """The camera's current values for ``keys`` (iso, aperture, shutter, wb), as the TABLES name them."""
        out = {}
        if not self._sdk or not self._cam:
            return out
        for key in keys:
            if key not in self.TABLES:
                continue
            prop, table = self.TABLES[key]
            try:
                value = c_uint32(0)
                if self._sdk.EdsGetPropertyData(self._cam, prop, 0, 4, byref(value)) != EDS_ERR_OK:
                    continue
            except Exception:                   # noqa: BLE001 — unread is "not known", never a failure
                continue
            name = next((n for n, code in table.items() if code == value.value), None)
            if name is not None:
                out[key] = name
        return out

    def __init__(self, dll_path: str = "", release: str = "hold"):
        self.dll_path = dll_path
        # "hold": focus, press, keep the button down until the picture is
        # there. "classic": press and let go at once, as before 14.11.2026 —
        # kept as a way back, set with camera.edsdk_release in local.json.
        self.release = "classic" if release == "classic" else "hold"
        self._sdk = None
        self._cam = c_void_p()
        self._handler = None
        self._state_handler = None
        self._refusal = 0
        self._af_done = False
        self._attempt = 1
        #: How often "no focus" was answered with a press without autofocus.
        self.focus_fallbacks = 0
        #: How often a card/mode refusal was answered with the photo setup.
        self.setup_recoveries = 0
        self._pending_target: Optional[Path] = None
        self._download_done = False
        self._download_error: Optional[str] = None
        self._evf_on = False
        self._events_at = 0.0
        self._seen: list = []
        # Zählt die Bilder, die zur laufenden Aufnahme angenommen wurden.
        # Erwartet wird genau eines; alles weitere wird abgesagt.
        self._angenommen = 0
        # Movie files the camera announced while record_movie listened.
        self._movie_watch = False
        self._movie_names: list = []
        self.restore_problem = ""

    # ── Hilfen ─────────────────────────────────────────────
    def _check(self, err: int, what: str) -> None:
        if err == EDS_ERR_DEVICE_BUSY:
            # Seen on the EOS M50: once stuck, the camera answers "busy" to
            # everything, across program starts, until it is switched off
            # and on. The operator needs that sentence, not a hex code.
            raise CameraError("Die Kamera ist beschäftigt und nimmt keine Befehle an. "
                              f"Bitte Kamera aus- und wieder einschalten. ({what}, 0x{err:08X})")
        if err != EDS_ERR_OK:
            raise CameraError(f"EDSDK {what} fehlgeschlagen (0x{err:08X})")

    def _load(self):
        candidates = [self.dll_path] if self.dll_path else []
        try:                                  # dorthin legt --install-edsdk die Dateien
            from ..config import data_dir
            base = Path(data_dir()) / "EDSDK"
            candidates += [str(base / "EDSDK.dll"), str(base / "libEDSDK.so"),
                           str(base / "EDSDK.framework" / "EDSDK")]
        except Exception:                     # noqa: BLE001 — ohne Datenordner geht es auch
            pass
        here = Path(sys.argv[0]).resolve().parent
        candidates += [str(here / "EDSDK" / "EDSDK.dll"),
                       str(here / "EDSDK" / "EDSDK.framework" / "EDSDK"),
                       "/Library/Frameworks/EDSDK.framework/EDSDK",
                       str(Path.home() / "Library" / "Frameworks" / "EDSDK.framework" / "EDSDK"),
                       "EDSDK.dll", "libEDSDK.so"]
        last = None
        for c in candidates:
            if not c:
                continue
            try:
                if os.name == "nt":
                    d = os.path.dirname(c)
                    if d and hasattr(os, "add_dll_directory"):
                        os.add_dll_directory(d)  # EdsImage.dll liegt daneben
                    return ctypes.WinDLL(c)
                return ctypes.CDLL(c)
            except OSError as e:
                last = e
        if sys.platform not in ("win32", "darwin"):
            raise CameraError(
                "Für Linux gibt es kein Canon-SDK — dort läuft die Box mit gphoto2 "
                "(Backend in der Verwaltung umstellen)")
        raise CameraError(
            "Canon EDSDK nicht gefunden. Über das Canon Developer Programme beziehen und mit "
            f"--install-edsdk einrichten ({last})"
        )

    def _events(self) -> None:
        get_event = getattr(self._sdk, "EdsGetEvent", None)
        if get_event is not None:
            get_event()
        _pump_windows_messages()
        _pump_mac_runloop()

    # ── Lebenszyklus ───────────────────────────────────────
    # The SDK is started once per process and ended when the process ends.
    # Terminate and initialise again, and it lists no camera any more — seen
    # on a real EOS M50 after the first capture timeout: every reconnect said
    # "no camera found" until the program was restarted.
    _live: list = []          # [(key, sdk)] — the one started SDK of this process

    def _started_sdk(self):
        sdk = self._load()
        key = getattr(sdk, "_name", None) or id(sdk)
        for known, live in EdsdkCamera._live:
            if known == key:
                return live
        sdk.EdsDownload.argtypes = [c_void_p, c_uint64, c_void_p]
        self._check(sdk.EdsInitializeSDK(), "Initialisierung")
        EdsdkCamera._live.append((key, sdk))
        import atexit
        atexit.register(_terminate_quietly, sdk)
        return sdk

    def open(self) -> None:
        sdk = self._started_sdk()
        self._sdk = sdk
        cam_list = c_void_p()
        deadline = time.monotonic() + self.FIND_TIMEOUT
        try:
            while True:
                self._check(sdk.EdsGetCameraList(byref(cam_list)), "Kameraliste")
                count = c_uint32()
                self._check(sdk.EdsGetChildCount(cam_list, byref(count)), "Kameras zählen")
                if count.value:
                    break
                if time.monotonic() >= deadline:
                    raise CameraError("Keine Canon-Kamera per USB gefunden (eingeschaltet? Kabel?)")
                # The list is a snapshot. Release it, let the SDK work, ask again.
                sdk.EdsRelease(cam_list)
                cam_list = c_void_p()
                self._events()
                time.sleep(0.1)
            self._check(sdk.EdsGetChildAtIndex(cam_list, 0, byref(self._cam)), "Kamera wählen")
        finally:
            # Auch auf dem Fehlerweg freigeben — sonst blieb pro
            # Verbindungsversuch eine Kameraliste liegen, und versucht wird
            # es alle ein bis acht Sekunden.
            if cam_list:
                sdk.EdsRelease(cam_list)

        self._handler = EdsObjectEventHandler(self._on_object_event)
        # Ungeprüft startete die Box scheinbar sauber und lief bei jeder
        # Auslösung in den Timeout, ohne den wahren Grund zu nennen.
        self._check(sdk.EdsSetObjectEventHandler(self._cam, kEdsObjectEvent_All,
                                                 self._handler, None), "Ereignisse anmelden")
        # The camera names its reason when it refuses to fire, but only to
        # someone who listens. Not every body sends these, so a failure to
        # register is no reason to refuse the camera.
        self._state_handler = EdsStateEventHandler(self._on_state_event)
        setter = getattr(sdk, "EdsSetCameraStateEventHandler", None)
        if setter is not None:
            err = setter(self._cam, kEdsStateEvent_All, self._state_handler, None)
            if err != EDS_ERR_OK:
                log.info("Kamera meldet keine Zustandsereignisse (0x%08X)", err & 0xFFFFFFFF)
        self._check(sdk.EdsOpenSession(self._cam), "Sitzung öffnen")

        self._photo_setup()

    def _photo_setup(self) -> None:
        """Everything a photo needs from the session: run at connect and after every movie.

        The order matters: with SaveTo on the host the camera only releases
        once the host has announced its capacity *after* that change. Seen on
        the EOS M50 (17.11.2026): after a movie the restore set SaveTo back
        without the capacity, and the next shot was refused with 0x8D07
        ("card"), until a reconnect ran this sequence again.
        """
        sdk = self._sdk
        save_to = c_uint32(kEdsSaveTo_Host)
        self._check(sdk.EdsSetPropertyData(self._cam, kEdsPropID_SaveTo, 0, 4, byref(save_to)), "SaveTo=Host")
        cap = EdsCapacity(0x7FFFFFFF, 0x1000, 1)
        self._check(sdk.EdsSetCapacity(self._cam, cap), "Kapazität setzen")
        # Single shots. The button is held until the picture arrives, and in
        # a continuous drive mode that would be a burst. A dial position that
        # does not allow the change is not a reason to refuse the camera.
        single = c_uint32(0)
        err = sdk.EdsSetPropertyData(self._cam, kEdsPropID_DriveMode, 0, 4, byref(single))
        if err != EDS_ERR_OK:
            log.info("Einzelbild ließ sich nicht einstellen (0x%08X)", err & 0xFFFFFFFF)
        self._set_clock()
        self._start_evf()

    # Reconnects come every one to eight seconds when a cable is loose: say
    # once per program run that the clock could not be set, not every time.
    _clock_warned = False

    def _set_clock(self) -> None:
        """Give the camera the PC's local time.

        On a mains adapter (dummy battery) the EOS M50 forgets its clock and
        dates every picture 2017 (seen 15.11.2026). The booth's own files
        carry the PC's time anyway; this keeps the camera's original honest.
        Never a reason to refuse the camera: any failure is logged once.
        """
        try:
            now = time.localtime()
            value = EdsTime(now.tm_year, now.tm_mon, now.tm_mday, now.tm_hour,
                            now.tm_min, now.tm_sec, 0)
            err = self._sdk.EdsSetPropertyData(self._cam, kEdsPropID_DateTime, 0,
                                               ctypes.sizeof(EdsTime), byref(value))
            problem = "" if err == EDS_ERR_OK else f"0x{err & 0xFFFFFFFF:08X}"
        except Exception as e:                       # noqa: BLE001 — a clock never costs the camera
            problem = str(e) or type(e).__name__
        if problem and not EdsdkCamera._clock_warned:
            EdsdkCamera._clock_warned = True
            log.info("Uhrzeit der Kamera ließ sich nicht stellen (%s)", problem)

    def close(self) -> None:
        """Hand the camera back. The SDK itself stays up (see _started_sdk)."""
        if not self._sdk:
            return
        try:
            self._stop_evf()
            self._sdk.EdsCloseSession(self._cam)
            self._sdk.EdsRelease(self._cam)
        finally:
            self._cam = c_void_p()
            self._sdk = None

    def _start_evf(self) -> None:
        sdk = self._sdk
        mode = c_uint32(1)
        sdk.EdsSetPropertyData(self._cam, kEdsPropID_Evf_Mode, 0, 4, byref(mode))
        dev = c_uint32(kEdsEvfOutputDevice_PC)
        self._check(sdk.EdsSetPropertyData(self._cam, kEdsPropID_Evf_OutputDevice, 0, 4, byref(dev)), "Live-View starten")
        self._evf_on = True
        time.sleep(0.3)

    def _stop_evf(self) -> None:
        if not self._evf_on:
            return
        dev = c_uint32(kEdsEvfOutputDevice_TFT)
        self._sdk.EdsSetPropertyData(self._cam, kEdsPropID_Evf_OutputDevice, 0, 4, byref(dev))
        self._evf_on = False

    # ── Live-View ──────────────────────────────────────────
    def preview_jpeg(self, max_width: int = 1280) -> Optional[bytes]:
        sdk = self._sdk
        # Not before every frame: EdsGetEvent took 65 ms per call on the real
        # M50 and halved the live view. A few times a second is enough to
        # cancel a picture somebody took at the camera body.
        jetzt = time.monotonic()
        if jetzt - self._events_at >= self.EVENT_INTERVAL:
            self._events_at = jetzt
            self._events()
        stream = c_void_p()
        evf = c_void_p()
        self._check(sdk.EdsCreateMemoryStream(c_uint64(0), byref(stream)), "Speicherstream")
        try:
            self._check(sdk.EdsCreateEvfImageRef(stream, byref(evf)), "EVF-Referenz")
            err = sdk.EdsDownloadEvfImage(self._cam, evf)
            if err in (EDS_ERR_OBJECT_NOTREADY, EDS_ERR_DEVICE_BUSY):
                return None
            self._check(err, "Live-Bild laden")
            ptr = c_void_p()
            length = c_uint64()
            # Ungeprüft war das ein Absturz ohne Traceback: Schlägt
            # EdsGetPointer fehl, bleibt ptr NULL, und string_at liest von
            # Adresse 0 — 20-mal in der Sekunde als Lotterie.
            self._check(sdk.EdsGetPointer(stream, byref(ptr)), "Live-Bild lesen")
            self._check(sdk.EdsGetLength(stream, byref(length)), "Live-Bild messen")
            if not ptr or not length.value:
                return None
            return ctypes.string_at(ptr, length.value)
        finally:
            if evf:
                sdk.EdsRelease(evf)
            sdk.EdsRelease(stream)

    # ── Auslösen ───────────────────────────────────────────
    def _absagen(self, item) -> None:
        """Eine Transferanfrage, die wir nicht abholen, muss abgesagt werden.

        Sonst wartet die Kamera weiter auf den Abruf und quittiert die
        nächsten Kommandos mit DEVICE_BUSY — bei der M50 hilft dann nur
        noch Akku heraus. Vorher fiel dieser Fall einfach durch.
        """
        absage = getattr(self._sdk, "EdsDownloadCancel", None)
        if absage is None:
            return
        try:
            absage(item)
        except Exception:                            # noqa: BLE001
            pass

    def _on_state_event(self, event: int, data: int, _ctx: int) -> int:
        self._seen.append(event)
        if event == kEdsStateEvent_CaptureError:
            self._refusal = data or 0xFFFF
        elif event == kEdsStateEvent_AfResult:
            self._af_done = True
        return EDS_ERR_OK

    def _on_object_event(self, event: int, ref: int, _ctx: int) -> int:
        sdk = self._sdk
        item = c_void_p(ref)
        self._seen.append(event)
        try:
            if event == kEdsObjectEvent_DirItemCreated and self._movie_watch:
                # Only the name is kept: the reference is released below and
                # record_movie finds the file on the card by its name.
                info = EdsDirectoryItemInfo()
                if sdk.EdsGetDirectoryItemInfo(item, byref(info)) == EDS_ERR_OK:
                    name = info.szFileName.decode(errors="replace")
                    if name.upper().endswith(MOVIE_EXTENSIONS):
                        self._movie_names.append(name)
                return EDS_ERR_OK
            if event != kEdsObjectEvent_DirItemRequestTransfer:
                return EDS_ERR_OK
            # Gehört das Bild zur laufenden Aufnahme? Alles andere — eine
            # Auslösung am Gehäuse, ein verspätetes Bild nach einem Timeout —
            # wird abgesagt statt in das nächstbeste Ziel geschrieben.
            ziel = self._pending_target
            if ziel is None or self._angenommen:
                # Kein Ziel: Das Bild gehört zu keiner laufenden Aufnahme —
                # jemand hat am Gehäuse ausgelöst, oder es ist ein Nachzügler
                # aus einer abgelaufenen Aufnahme. Schon eines angenommen:
                # Eine Aufnahme liefert genau ein Bild, der Rest ist Nachlauf.
                self._absagen(item)
                return EDS_ERR_OK
            info = EdsDirectoryItemInfo()
            self._check(sdk.EdsGetDirectoryItemInfo(item, byref(info)), "Dateiinfo")
            stream = c_void_p()
            path = str(ziel).encode("mbcs" if os.name == "nt" else "utf-8")
            self._check(sdk.EdsCreateFileStream(path, kEdsFileCreateDisposition_CreateAlways,
                                                kEdsAccess_ReadWrite, byref(stream)), "Dateistream")
            try:
                self._check(sdk.EdsDownload(item, info.size, stream), "Download")
                self._check(sdk.EdsDownloadComplete(item), "Download abschließen")
            finally:
                sdk.EdsRelease(stream)
            self._angenommen += 1
            self._download_done = True
        except Exception as e:                       # noqa: BLE001
            # Nicht nur CameraError: Ein UnicodeEncodeError beim Pfad oder ein
            # AttributeError entkäme sonst in den ctypes-Rückruf, wo ihn
            # niemand sieht — die Box liefe in den 15-Sekunden-Timeout und
            # nennte den falschen Grund.
            self._download_error = str(e)
            self._download_done = True
            self._absagen(item)
        finally:
            if ref:
                sdk.EdsRelease(item)
        return EDS_ERR_OK

    def capture(self, target: Path) -> Path:
        sdk = self._sdk
        # Erst aufräumen, dann auslösen. Im Ereignisstapel des SDK kann noch
        # ein Bild liegen, das zu dieser Aufnahme nicht gehört: ein Nachzügler
        # aus einer Aufnahme, die in den Timeout lief, oder eine Auslösung am
        # Gehäuse. Ohne Ziel sagt der Rückruf es ab. Vorher wurde es in das
        # Ziel DIESER Aufnahme geschrieben — ab dem ersten Timeout bekam jede
        # Gruppe die Fotos der Gruppe davor.
        self._pending_target = None
        self._angenommen = 0
        self._events()
        self._pending_target = target
        self._download_done = False
        self._download_error = None
        self._seen = []
        self._refusal = 0
        self._af_done = False
        press = lambda how: sdk.EdsSendCommand(             # noqa: E731
            self._cam, kEdsCameraCommand_PressShutterButton, how)

        deadline = time.monotonic() + CAPTURE_DEADLINE

        def wait(seconds: float, until) -> None:
            end = min(time.monotonic() + seconds, deadline)
            while not until() and time.monotonic() < end:
                self._events()
                time.sleep(0.02)

        got_it = lambda: self._download_done or bool(self._refusal)   # noqa: E731
        err = EDS_ERR_OK
        self._attempt = 1
        hold = self.release != "classic"
        if not hold:
            err = self._press_classic(press)
            if err == EDS_ERR_TAKE_PICTURE_AF_NG:
                # The press without AF said "no focus" as well: the retries
                # below take over, as for the same answer as an event.
                self._refusal = err
            else:
                self._check_press(err)
                wait(CAPTURE_TIMEOUT, got_it)
        else:
            try:
                err = self._half_press(press, wait)
            except BaseException:
                # Whatever happened: the button comes up. Left down, the
                # camera stays busy for every command that follows.
                press(kEdsCameraCommand_ShutterButton_OFF)
                raise
            # 2. Fire without a second autofocus run, and keep the button
            #    down. Let go too early and a mirrorless body drops the shot
            #    without a word — seen on the EOS M50: command "ok", live view
            #    running, no picture, and the camera busy from then on.
            #    "No focus" reported during the half press is no reason not
            #    to fire: the press below does not focus.
            err = self._fire_without_af(press, wait, got_it, hold=True)
        # "No focus" (0x8D01) is about the scene — dim, static, no contrast —
        # never about the link. Seen on the EOS M50 (17.11.2026) as an event
        # after an "ok" AND as the return code of the press without AF itself;
        # the camera is fine and the next press usually fires. So: button up,
        # a short pause in which a late picture of the refused press may
        # still come, then the press without AF again — a few times, never
        # after a picture arrived. Only one picture is ever taken per shot
        # (_angenommen), a straggler of an earlier attempt is cancelled.
        while (self._refusal == EDS_ERR_TAKE_PICTURE_AF_NG and not self._download_done
               and self._attempt <= FOCUS_RETRIES and time.monotonic() < deadline):
            wait(FOCUS_RETRY_PAUSE, lambda: self._download_done)
            if self._download_done or time.monotonic() >= deadline:
                break
            self._attempt += 1
            self._focus_fallback()
            err = self._fire_without_af(press, wait, got_it, hold=hold)
        if (self._refusal in SETUP_REFUSALS and not self._download_done
                and time.monotonic() < deadline):
            code = self._refusal
            self.setup_recoveries += 1
            log.warning("Kamera verweigert die Aufnahme mit 0x%04X – Fotobetrieb neu eingerichtet, "
                        "noch einmal ausgelöst", code)
            try:
                self._recover_photo_setup(code)
            except BaseException:
                self._pending_target = None
                raise
            err = self._fire_without_af(press, wait, got_it, hold=hold)
            if self._refusal in SETUP_REFUSALS and not self._download_done:
                # Still refused: not a refusal of this one shot. The service
                # opens the camera afresh (a CameraError, not CaptureRefused);
                # the code stays in the text, so the screen still says "card".
                self._pending_target = None
                text = CAPTURE_REFUSALS.get(self._refusal) or "Die Kamera verweigert die Aufnahme."
                log.warning("Kamera verweigert auch nach dem Einrichten: 0x%04X – neu verbinden", self._refusal)
                raise CameraError(f"{text} (0x{self._refusal:08X})")
        return self._finish_capture(target, err)

    def _recover_photo_setup(self, code: int) -> None:
        """The answer to a card/mode refusal: movie mode off if that is what it said, then the photo setup."""
        if code in MOVIE_MODE_REFUSALS:
            err = self._sdk.EdsSendCommand(self._cam, kEdsCameraCommand_MovieSelectSwOFF, 0) & 0xFFFFFFFF
            if err != EDS_ERR_OK:
                log.info("Filmmodus aus: 0x%08X", err)
            end = time.monotonic() + 0.5
            while time.monotonic() < end:
                self._events()
                time.sleep(0.02)
        self._photo_setup()

    def _focus_fallback(self) -> None:
        """Count and log one "no focus" that is answered with a press without AF."""
        self.focus_fallbacks += 1
        log.info("kein Fokus – ohne Autofokus ausgelöst, Versuch %d", self._attempt)

    def _check_press(self, err: int) -> None:
        """A press's return code: a refusal of the shot, a link error, or fine."""
        if is_shot_refusal(err):
            self._refusal = err
            return
        self._check(err, "Auslösen")

    def _fire_without_af(self, press, wait, got_it, hold: bool) -> int:
        """One full press without autofocus, until the picture or a refusal.

        The button always comes up before this returns. A refusal — as the
        press's return code or as the camera's event — is left in
        ``_refusal``; a link error raises.
        """
        self._refusal = 0
        err = EDS_ERR_OK
        try:
            err = press(kEdsCameraCommand_ShutterButton_Completely_NonAF)
            self._check_press(err)
            if hold and not self._refusal:
                wait(HOLD_MAX, got_it)
        finally:
            press(kEdsCameraCommand_ShutterButton_OFF)
        if not self._refusal:
            wait(CAPTURE_TIMEOUT - (HOLD_MAX if hold else 0.0), got_it)
        return err

    def _half_press(self, press, wait) -> int:
        """1. Focus. A mirrorless body focuses for a moment after the press; a
        DSLR answers "no focus" right away. Both are fine: the picture is
        taken either way, a little soft beats none."""
        VERSUCHE = 3
        begun = time.monotonic()
        err = EDS_ERR_OK
        for attempt in range(VERSUCHE):
            begun = time.monotonic()
            err = press(kEdsCameraCommand_ShutterButton_Halfway)
            if err == EDS_ERR_DEVICE_BUSY and attempt < VERSUCHE - 1:
                press(kEdsCameraCommand_ShutterButton_OFF)
                time.sleep(0.5)
                continue
            break
        if err not in (EDS_ERR_OK, EDS_ERR_TAKE_PICTURE_AF_NG):
            self._check(err, "Auslösen")
        if err == EDS_ERR_OK and time.monotonic() - begun < 0.25:
            # The call came back at once, so the camera is still at it.
            wait(AF_WAIT, lambda: self._af_done or bool(self._refusal))
        return err

    def _press_classic(self, press) -> int:
        """Full press with autofocus, released at once; without focus, once more without AF."""
        err = EDS_ERR_OK
        for attempt in range(3):
            err = press(kEdsCameraCommand_ShutterButton_Completely)
            press(kEdsCameraCommand_ShutterButton_OFF)
            if err == EDS_ERR_TAKE_PICTURE_AF_NG:
                self._attempt = 2
                self._focus_fallback()
                err = press(kEdsCameraCommand_ShutterButton_Completely_NonAF)
                press(kEdsCameraCommand_ShutterButton_OFF)
            if err == EDS_ERR_DEVICE_BUSY and attempt < 2:
                time.sleep(0.5)
                continue
            break
        return err

    def _finish_capture(self, target: Path, err: int) -> Path:
        self._pending_target = None
        fertig, fehler = self._download_done, self._download_error
        if not fertig:
            if self._refusal:
                text = CAPTURE_REFUSALS.get(self._refusal)
                log.warning("Kamera verweigert die Aufnahme: 0x%04X", self._refusal)
                # The code travels with the sentence: the screen picks its words by it.
                # A refusal of the shot leaves the link alone (no reconnect);
                # anything else the camera reports here is treated as before.
                message = f"{text or 'Die Kamera verweigert die Aufnahme.'} (0x{self._refusal:08X})"
                if is_shot_refusal(self._refusal):
                    raise CaptureRefused(message, code=self._refusal)
                raise CameraError(message)
            # What the camera did say goes into the log: without it a timeout
            # in the field cannot be told from a camera that never fired.
            log.warning("Auslösen 0x%08X, danach Ereignisse %s — kein Bild in %d s",
                        err & 0xFFFFFFFF, [hex(e) for e in self._seen] or "keine", CAPTURE_TIMEOUT)
            raise CameraError("Kamera hat kein Bild geliefert (Timeout)")
        if fehler:
            raise CameraError(fehler)
        return target

    # ── Movie on the camera ────────────────────────────────
    can_record_movie = True

    def _prop(self, prop: int):
        value = c_uint32(0)
        err = self._sdk.EdsGetPropertyData(self._cam, prop, 0, 4, byref(value))
        return value.value if err == EDS_ERR_OK else None

    def _set(self, prop: int, value: int) -> int:
        data = c_uint32(value)
        return self._sdk.EdsSetPropertyData(self._cam, prop, 0, 4, byref(data)) & 0xFFFFFFFF

    def _children(self, ref, items: bool = True) -> list:
        """``[(ref, name, is_folder, size)]`` below a camera, volume or folder. The caller releases.

        ``items=False`` for the camera's children: those are volumes, which
        carry no directory item info.
        """
        sdk = self._sdk
        count = c_uint32(0)
        if sdk.EdsGetChildCount(ref, byref(count)) != EDS_ERR_OK:
            return []
        out = []
        for i in range(count.value):
            child = c_void_p()
            if sdk.EdsGetChildAtIndex(ref, i, byref(child)) != EDS_ERR_OK:
                continue
            if not items:
                out.append((child, "", True, 0))
                continue
            info = EdsDirectoryItemInfo()
            if sdk.EdsGetDirectoryItemInfo(child, byref(info)) != EDS_ERR_OK:
                sdk.EdsRelease(child)
                continue
            out.append((child, info.szFileName.decode(errors="replace"), bool(info.isFolder), int(info.size)))
        return out

    def _movies_on_card(self) -> list:
        """Every movie file under DCIM/<folder>/ on every card: ``[(ref, name, size)]``. The caller releases."""
        sdk = self._sdk
        found, release = [], []
        try:
            for vol, _n, _f, _s in self._children(self._cam, items=False):
                release.append(vol)
                for dcim, name, folder, _s in self._children(vol):
                    release.append(dcim)
                    if not folder or name.upper() != "DCIM":
                        continue
                    for sub, _n2, folder2, _s2 in self._children(dcim):
                        release.append(sub)
                        if not folder2:
                            continue
                        for item, fname, isdir, size in self._children(sub):
                            if not isdir and fname.upper().endswith(MOVIE_EXTENSIONS):
                                found.append((item, fname, size))
                            else:
                                release.append(item)
        finally:
            for ref in release:
                sdk.EdsRelease(ref)
        return found

    def _pick_movie(self, before: set):
        """The file of this recording: the name the camera announced, else the newest name not there before."""
        movies = self._movies_on_card()
        wanted = [n for n in self._movie_names if n not in before]
        pick = None
        if wanted:
            pick = next((m for m in movies if m[1] == wanted[-1]), None)
        if pick is None:
            new = sorted((m for m in movies if m[1] not in before), key=lambda m: m[1])
            pick = new[-1] if new else None
        for m in movies:
            if m is not pick:
                self._sdk.EdsRelease(m[0])
        return pick

    def record_movie(self, seconds: float, folder: Path, *, tick=None, stopped=None) -> Path:
        """Film ``seconds`` on the camera's card, download the file, delete it from the card.

        Runs on the camera thread like every other call. The camera goes
        back to photo state in ``finally`` whatever happened; when that fails
        ``restore_problem`` says so and the service opens the camera afresh.
        """
        sdk = self._sdk
        if not sdk or not self._cam:
            raise CameraError("Kamera nicht verbunden")
        tick = tick or (lambda _recording: None)
        # ``tick(None)``: a sign of life only, no live frame (the service's
        # watchdog), before and after every step that can take long.
        beat = lambda: tick(None)                     # noqa: E731
        folder = Path(folder)

        def wait(limit: float, until, recording: bool) -> bool:
            end = time.monotonic() + limit
            while True:
                self._events()
                if until():
                    return True
                if time.monotonic() >= end:
                    return False
                tick(recording)
                time.sleep(0.02)

        self.restore_problem = ""
        self._movie_names = []
        ae_before = self._prop(kEdsPropID_AEMode)
        save_to_changed = switched = recording = False
        item = None
        before: set = set()
        # Where the time goes, for booth.log (the device test had 6 s from
        # the request to the answer for a 2 s clip).
        marks = {}
        t_begin = time.monotonic()
        mark = lambda key: marks.__setitem__(key, round(time.monotonic() - t_begin, 2))   # noqa: E731
        attempts = 0
        try:
            beat()
            for ref, name, _size in self._movies_on_card():
                before.add(name)
                sdk.EdsRelease(ref)
            beat()
            mark("card")
            self._movie_watch = True
            err = self._set(kEdsPropID_SaveTo, kEdsSaveTo_Camera)
            save_to_changed = True
            self._check(err, "Film: SaveTo=Kamera")
            err = sdk.EdsSendCommand(self._cam, kEdsCameraCommand_MovieSelectSwON, 0) & 0xFFFFFFFF
            switched = True
            self._check(err, "Film: Filmmodus")
            # The mode changes within about 2 s; wait for it, not blindly.
            if ae_before is not None:
                wait(MOVIE_MODE_WAIT, lambda: self._prop(kEdsPropID_AEMode) not in (None, ae_before), False)
            mark("mode")
            end = time.monotonic() + MOVIE_START_WAIT
            while True:
                attempts += 1
                err = self._set(kEdsPropID_Record, kEdsRecord_Start)
                if err == EDS_ERR_OK:
                    break
                if time.monotonic() >= end:
                    raise CameraError(f"Kamera startet keinen Film (0x{err:08X}) — SD-Karte in der Kamera? "
                                      "Filmen über USB möglich?")
                wait(0.1, lambda: False, False)          # poll, the camera is ready any moment
            mark("start")
            recording = True
            t0 = time.monotonic()
            wait(float(seconds), lambda: False, True)
            err = self._set(kEdsPropID_Record, kEdsRecord_Stop)
            if err != EDS_ERR_OK:
                wait(0.3, lambda: False, False)
                err = self._set(kEdsPropID_Record, kEdsRecord_Stop)
            elapsed = time.monotonic() - t0
            self._check(err, "Film: Stopp")
            recording = False
            mark("stop")
            if stopped:
                stopped(elapsed)
            # The camera announces the file; until then (or if it never does)
            # the card is looked through once the wait is over.
            wait(MOVIE_FILE_WAIT, lambda: any(n not in before for n in self._movie_names), False)
            item = self._pick_movie(before)
            beat()
            mark("file")
            if item is None:
                raise CameraError("Kamera hat nach dem Film keine Datei geliefert")
            ref, name, size = item
            folder.mkdir(parents=True, exist_ok=True)
            target = folder / Path(name).name
            stream = c_void_p()
            path = str(target).encode("mbcs" if os.name == "nt" else "utf-8")
            self._check(sdk.EdsCreateFileStream(path, kEdsFileCreateDisposition_CreateAlways,
                                                kEdsAccess_ReadWrite, byref(stream)), "Film: Dateistream")
            try:
                beat()
                # In parts with a sign of life in between: a long 4K clip
                # downloads for longer than the watchdog's 60 s. A clip up to
                # one part (every boomerang, short videos) is one call, as
                # tested on the EOS M50.
                done = 0
                while True:
                    part = size - done if size - done <= MOVIE_DOWNLOAD_PART else MOVIE_DOWNLOAD_PART
                    self._check(sdk.EdsDownload(ref, c_uint64(part), stream), "Film: Download")
                    done += part
                    beat()
                    if done >= size:
                        break
                self._check(sdk.EdsDownloadComplete(ref), "Film: Download abschließen")
            finally:
                sdk.EdsRelease(stream)
            beat()
            if not target.is_file() or (size and target.stat().st_size != size):
                raise CameraError(f"Film unvollständig geladen ({target.stat().st_size if target.is_file() else 0}"
                                  f" von {size} Bytes)")
            # Downloaded whole: now, and only now, the file goes from the
            # card, so a long evening does not fill it. Nothing else is ever
            # deleted. A failed delete costs card space, not the clip.
            delete = getattr(sdk, "EdsDeleteDirectoryItem", None)
            derr = delete(ref) & 0xFFFFFFFF if delete is not None else -1
            if derr != EDS_ERR_OK:
                log.warning("Film %s nicht von der Karte gelöscht (%s)", name,
                            "Funktion fehlt" if derr == -1 else f"0x{derr:08X}")
            mark("download")
            log.info("Film der Kamera: %s, %.1f MB, %.2f s aufgenommen", name, size / 1e6, elapsed)
            return target
        finally:
            self._movie_watch = False
            if item is not None:
                sdk.EdsRelease(item[0])
            beat()
            self._restore_photo(ae_before, recording, switched, save_to_changed)
            beat()
            mark("restored")
            # Seconds since the request at each step: card listed, mode
            # changed, recording started (after n tries), stopped, file
            # found, downloaded, back in photo mode.
            log.info("Film: Zeiten ab Anfrage %s, Startversuche %d", marks, attempts)

    def _restore_photo(self, ae_before, recording: bool, switched: bool, save_to_changed: bool) -> None:
        """Back to photo state, each step tried on its own.

        Stop, movie mode off, wait until the camera reports its photo mode
        again, then the very sequence the connect runs (``_photo_setup``:
        SaveTo host, capacity, single shot, clock, live view) and a read-back
        of SaveTo. Anything that does not come back is a ``restore_problem``:
        the service then opens the camera afresh instead of carrying on.
        """
        problems = []
        t0 = time.monotonic()
        for needed, what, call in (
                (recording, "Stopp", lambda: self._set(kEdsPropID_Record, kEdsRecord_Stop)),
                (switched, "Filmmodus aus",
                 lambda: self._sdk.EdsSendCommand(self._cam, kEdsCameraCommand_MovieSelectSwOFF, 0) & 0xFFFFFFFF)):
            if not needed:
                continue
            err = -1
            for _ in range(3):
                try:
                    err = call()
                except Exception as e:                # noqa: BLE001 — the next step still runs
                    log.warning("Film: %s: %s", what, e)
                    err = -1
                if err == EDS_ERR_OK:
                    break
                time.sleep(0.2)
                self._events()
            if err != EDS_ERR_OK:
                problems.append(f"{what} 0x{err & 0xFFFFFFFF:08X}")
        if switched and ae_before is not None:
            end = time.monotonic() + MOVIE_RESTORE_WAIT
            while self._prop(kEdsPropID_AEMode) != ae_before and time.monotonic() < end:
                self._events()
                time.sleep(0.05)
            now = self._prop(kEdsPropID_AEMode)
            if now != ae_before:
                problems.append(f"Betriebsart {now} statt {ae_before}")
        mode_back = time.monotonic() - t0
        setup_err = ""
        for _ in range(3):
            try:
                self._photo_setup()
                setup_err = ""
                break
            except Exception as e:                    # noqa: BLE001 — tried again, then reported
                setup_err = str(e) or type(e).__name__
                time.sleep(0.2)
                self._events()
        if setup_err:
            problems.append(f"Fotobetrieb einrichten: {setup_err}")
        elif self._prop(kEdsPropID_SaveTo) not in (None, kEdsSaveTo_Host):
            problems.append(f"SaveTo liest {self._prop(kEdsPropID_SaveTo)} statt Host")
        log.info("Film: zurück im Fotobetrieb nach %.2f s (Betriebsart nach %.2f s)",
                 time.monotonic() - t0, mode_back)
        if problems:
            self.restore_problem = "; ".join(problems)
            log.error("Kamera nach dem Film nicht zurück im Fotobetrieb: %s", self.restore_problem)

    def keepalive(self) -> None:
        if self._sdk:
            self._sdk.EdsSendCommand(self._cam, kEdsCameraCommand_ExtendShutDownTimer, 0)
