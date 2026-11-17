"""Ein nachgebautes Canon-EDSDK — für Tests ohne Kamera.

Der ctypes-Code des Treibers war bis zur Durchsicht am 19.09.2026 komplett
ungetestet: keine einzige Testdatei legte je ein ``EdsdkCamera`` an. Das ist
schlecht ausgerechnet dort, wo die Fehler nicht als Ausnahme ankommen,
sondern als vertauschtes Foto oder als hängende Kamera.

Nachgebaut wird, was der Treiber wirklich anfasst: Referenzen mit Zählung,
der Ereignis-Rückruf, der Abruf einer Datei, das Live-Bild — und die
unangenehmen Fälle: Kamera beschäftigt, kein Fokus, Bild kommt zu spät.

Das beweist nicht, dass es an einer M50 läuft. Es beweist, dass der Ablauf
stimmt und dass die Fehler, die wir kennen, nicht zurückkommen.
"""
from __future__ import annotations

import ctypes

OK = 0x00000000
BUSY = 0x00000081
AF_NG = 0x00008D01
NOTREADY = 0x0000A102

JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 256 + b"\xff\xd9"


class FakeEdsdk:
    """Verhält sich wie die geladene DLL: Attribute sind aufrufbare Funktionen."""

    def __init__(self):
        self.initialisiert = 0
        self.beendet = 0
        self.sitzung_offen = 0
        self.sitzung_zu = 0
        self.freigegeben: list[int] = []      # jede EdsRelease-Referenz
        self.abgesagt: list[int] = []         # jedes EdsDownloadCancel
        self.handler = None
        self.kameras = 1
        self.ereignis_abfragen = 0            # wie oft EdsGetEvent gerufen wurde
        self.kameras_spaet = 0                # so viele Abfragen lang ist die Liste noch leer
        self.busy_mal = 0                     # so oft BUSY auf das Auslösen
        self.af_ng_mal = 0                    # so oft „kein Fokus"
        self.nonaf_versuche = 0
        self.ausloesungen = 0
        self.liefert = True                   # ob nach dem Auslösen ein Bild kommt
        # A mirrorless body (seen on the EOS M50, 14.11.2026): the shutter
        # button has to stay down until the camera has fired. Press and let
        # go in the same breath, and it drops the shot without a word.
        self.spiegellos = False
        self.halte_abfragen = 3               # so many event polls the button must stay down
        self._gedrueckt = None                # event polls since the full press, None = up
        self.befehle = []                     # every shutter parameter, in order
        self.zustand_handler = None
        self.aufnahmefehler = 0               # the camera refuses with this code (state event 0x305)
        self.halb_meldet_af_ng = False        # half press says ok, the refusal "no focus" comes as an event
        self.nonaf_af_ng_spaet = 0            # so often: the press without AF says ok, then event "no focus"
        self.af_ng_spaet = 0                  # so often: full press says ok, then event "no focus"
        # Real EOS M50, 17.11.2026, dim static scene: the press WITHOUT
        # autofocus itself came back 0x8D01 as its return code.
        self.nonaf_af_ng_mal = 0              # so often: the press without AF returns AF_NG
        self.nonaf_fehler = OK                # the press without AF returns this (a link error)
        # The press without AF says ok, "no focus" comes as an event — and the
        # picture of that very press still arrives, bild_nach polls later
        # (None: together with the next press).
        self.nonaf_af_ng_mit_bild = 0
        self.bild_nach = 3
        self._geplant: list = []              # [polls left, ref] pictures still on their way
        self._beim_naechsten: list = []       # refs delivered with the next press without AF
        self.downloads = 0                    # every EdsDownload of a picture
        self._zustand = []
        self.geschrieben: dict[str, bytes] = {}
        self.evf_zeiger_null = False          # EdsGetPointer schlägt fehl
        self.evf_fehler = OK
        self.handler_fehler = OK
        self.gesetzt: list = []               # every EdsSetPropertyData: (prop, size, value)
        self.eigenschaft_fehler: dict = {}    # prop -> error code the camera answers
        self.eigenschaft_wirft: set = set()   # props whose setting raises inside the DLL call
        self.eigenschaften: dict = {}         # prop -> value EdsGetPropertyData answers (else 3)
        self.lese_fehler: dict = {}           # prop -> error code on reading
        self.gelesen: list = []               # every EdsGetPropertyData prop, in order
        self._ref = 1000
        self._streams: dict[int, dict] = {}
        self._nachzuliefern: list[int] = []   # Objekte, die _events ausliefert

    # ── Referenzen ─────────────────────────────────────────
    def _neu(self) -> int:
        self._ref += 1
        return self._ref

    def EdsRelease(self, ref):
        self.freigegeben.append(_wert(ref))
        return OK

    # ── Lebenszyklus ───────────────────────────────────────
    def EdsInitializeSDK(self):
        self.initialisiert += 1
        return OK

    def EdsTerminateSDK(self):
        self.beendet += 1
        return OK

    def EdsGetCameraList(self, out):
        out._obj.value = self._neu()
        return OK

    def EdsGetChildCount(self, _liste, out):
        if self.kameras_spaet > 0:
            self.kameras_spaet -= 1
            out._obj.value = 0
            return OK
        out._obj.value = self.kameras
        return OK

    def EdsGetChildAtIndex(self, _liste, _i, out):
        out._obj.value = self._neu()
        return OK

    def EdsSetObjectEventHandler(self, _cam, _ereignis, handler, _ctx):
        if self.handler_fehler:
            return self.handler_fehler
        self.handler = handler
        return OK

    def EdsOpenSession(self, _cam):
        self.sitzung_offen += 1
        return OK

    def EdsCloseSession(self, _cam):
        self.sitzung_zu += 1
        return OK

    def EdsSetPropertyData(self, _cam, prop, _param, size, data):
        # Every property the driver sets, with what it wrote: a structure as
        # a dict of its fields, a number as the number.
        wert = getattr(data, "_obj", data)
        if hasattr(wert, "_fields_"):
            wert = {name: getattr(wert, name) for name, *_ in wert._fields_}
        else:
            wert = getattr(wert, "value", wert)
        self.gesetzt.append((prop, size, wert))
        if prop in self.eigenschaft_wirft:
            raise OSError(f"access violation setting 0x{prop:08X}")
        return self.eigenschaft_fehler.get(prop, OK)

    def EdsGetPropertyData(self, _cam, prop, _p, _size, out):
        self.gelesen.append(prop)
        if prop in self.lese_fehler:
            return self.lese_fehler[prop]
        out._obj.value = self.eigenschaften.get(prop, 3)   # 3 = M on the dial
        return OK

    def EdsSetCapacity(self, *_a):
        # Real EOS M50, 17.11.2026: after a movie the camera answered every
        # shot with 0x8D07 ("card") until the host announced its capacity
        # again. ``heilt_mit_kapazitaet``: such a refusal ends at this call.
        self.kapazitaeten = getattr(self, "kapazitaeten", 0) + 1
        if getattr(self, "heilt_mit_kapazitaet", False):
            self.aufnahmefehler = 0
        return OK

    # ── Live-Bild ──────────────────────────────────────────
    def EdsCreateMemoryStream(self, _size, out):
        ref = self._neu(); out._obj.value = ref
        self._streams[ref] = {"daten": JPEG}
        return OK

    def EdsCreateEvfImageRef(self, _stream, out):
        out._obj.value = self._neu()
        return OK

    def EdsDownloadEvfImage(self, _cam, _evf):
        return self.evf_fehler

    def EdsGetPointer(self, _stream, out):
        if self.evf_zeiger_null:
            return NOTREADY                    # Zeiger bleibt NULL
        buf = ctypes.create_string_buffer(JPEG)
        self._puffer = buf                     # am Leben halten
        out._obj.value = ctypes.cast(buf, ctypes.c_void_p).value
        return OK

    def EdsGetLength(self, _stream, out):
        out._obj.value = 0 if self.evf_zeiger_null else len(JPEG)
        return OK

    # ── Auslösen ───────────────────────────────────────────
    def EdsSetCameraStateEventHandler(self, _cam, _ereignis, handler, _ctx):
        self.zustand_handler = handler
        return OK

    def _ausloesen(self):
        self.ausloesungen += 1
        if self.aufnahmefehler:
            self._zustand.append((0x00000305, self.aufnahmefehler))
        elif self.liefert:
            self._nachzuliefern.append(self._neu())

    def EdsSendCommand(self, _cam, befehl, param):
        if befehl != 0x00000004:               # PressShutterButton
            self.andere_befehle = getattr(self, "andere_befehle", []) + [befehl]
            return OK
        self.befehle.append(param)
        if param == 0x00000000:                # loslassen
            self._gedrueckt = None             # let go too early: the shot is dropped
            return OK
        if param in (0x00000001, 0x00010001):  # halb: scharfstellen
            if self.busy_mal > 0:
                self.busy_mal -= 1
                return BUSY
            if param == 0x00000001 and self.af_ng_mal > 0:
                self.af_ng_mal -= 1
                return AF_NG
            if self.halb_meldet_af_ng:
                self._zustand.append((0x00000305, 0x8D01))
            return OK
        if param == 0x00010003:                # ohne Autofokus
            self.nonaf_versuche += 1
            # A picture of an earlier refused press that turns up just as the
            # next press goes in: both reach the driver in the same poll.
            self._nachzuliefern.extend(self._beim_naechsten)
            self._beim_naechsten = []
            if self.nonaf_fehler:
                return self.nonaf_fehler
            if self.nonaf_af_ng_mal > 0:
                self.nonaf_af_ng_mal -= 1
                return AF_NG
            if self.nonaf_af_ng_mit_bild > 0:
                self.nonaf_af_ng_mit_bild -= 1
                self._zustand.append((0x00000305, 0x8D01))
                self.ausloesungen += 1
                if self.bild_nach is None:
                    self._beim_naechsten.append(self._neu())
                else:
                    self._geplant.append([self.bild_nach, self._neu()])
                return OK
            if self.nonaf_af_ng_spaet > 0:
                self.nonaf_af_ng_spaet -= 1
                self._zustand.append((0x00000305, 0x8D01))
                return OK
            if self.spiegellos:
                self._gedrueckt = 0
            else:
                self._ausloesen()
            return OK
        if self.busy_mal > 0:
            self.busy_mal -= 1
            return BUSY
        if self.af_ng_mal > 0:
            self.af_ng_mal -= 1
            return AF_NG
        if self.af_ng_spaet > 0:
            self.af_ng_spaet -= 1
            self._zustand.append((0x00000305, 0x8D01))
            return OK
        if self.spiegellos:
            self._gedrueckt = 0
        else:
            self._ausloesen()
        return OK

    def EdsGetEvent(self):
        self.ereignis_abfragen += 1
        if self._gedrueckt is not None:
            self._gedrueckt += 1
            if self.spiegellos and self._gedrueckt == self.halte_abfragen:
                self._ausloesen()
                self._gedrueckt = -10**9      # fired once; holding longer adds nothing
        for eintrag in list(self._geplant):
            eintrag[0] -= 1
            if eintrag[0] <= 0:
                self._geplant.remove(eintrag)
                self._nachzuliefern.append(eintrag[1])
        while self._zustand and self.zustand_handler:
            ev, data = self._zustand.pop(0)
            self.zustand_handler(ev, data, None)
        while self._nachzuliefern and self.handler:
            ref = self._nachzuliefern.pop(0)
            self.handler(0x00000208, ref, None)
        return OK

    def spaet_liefern(self, ref: int | None = None) -> int:
        """Ein Bild einreihen, das zu keiner laufenden Aufnahme mehr gehört."""
        ref = ref or self._neu()
        self._nachzuliefern.append(ref)
        return ref

    # ── Abruf ──────────────────────────────────────────────
    def EdsGetDirectoryItemInfo(self, _item, out):
        out._obj.size = len(JPEG)
        out._obj.szFileName = b"IMG_0001.JPG"
        return OK

    def EdsCreateFileStream(self, pfad, _disp, _zugriff, out):
        ref = self._neu(); out._obj.value = ref
        self._streams[ref] = {"pfad": pfad.decode("utf-8", "replace")}
        return OK

    def EdsDownload(self, _item, _size, stream):
        eintrag = self._streams.get(_wert(stream), {})
        if "pfad" in eintrag:
            self.downloads += 1
            self.geschrieben[eintrag["pfad"]] = JPEG
            open(eintrag["pfad"], "wb").write(JPEG)
        return OK

    def EdsDownloadComplete(self, _item):
        return OK

    def EdsDownloadCancel(self, item):
        self.abgesagt.append(_wert(item))
        return OK


def _wert(x):
    return getattr(x, "value", x)


class _Download:
    """``EdsDownload.argtypes = [...]`` muss am Fake ebenfalls gehen."""
    argtypes = None


def bereitstellen(monkeypatch, sdk: FakeEdsdk):
    """Den Treiber die nachgebaute DLL laden lassen statt einer echten."""
    from boothcore.camera import edsdk as modul

    sdk.EdsDownload = _MitAttributen(sdk.EdsDownload)
    monkeypatch.setattr(modul.EdsdkCamera, "_load", lambda self: sdk)
    # Der Rückruf-Typ muss eine Python-Funktion durchreichen statt sie in
    # einen C-Trampolin zu packen — sonst ruft der Fake ins Leere.
    monkeypatch.setattr(modul, "EdsObjectEventHandler", lambda fn: fn)
    monkeypatch.setattr(modul, "EdsStateEventHandler", lambda fn: fn, raising=False)
    monkeypatch.setattr(modul, "_pump_windows_messages", lambda: None)
    return sdk


class _MitAttributen:
    """Eine Funktion, an die sich ``argtypes`` schreiben lässt."""

    def __init__(self, fn):
        self._fn = fn
        self.argtypes = None

    def __call__(self, *a, **k):
        return self._fn(*a, **k)
