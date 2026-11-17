"""Was die Box zu sagen hat — an den Betreiber, nicht an den Gast.

Eine Fotobox steht allein im Saal. Wenn das Farbband leer ist, die Kamera
den Anschluss verloren hat oder die Platte vollläuft, merkt das niemand —
bis jemand vorbeikommt. Genau dafür gibt es diese Datei.

Drei Stufen, absichtlich nur drei:

* ``info``     — es läuft, hier ist der Stand. Ereignisse, keine Probleme.
* ``warnung``  — könnte gleich kritisch werden. Noch fünfzig Blatt, die
                 Galerie antwortet seit zehn Minuten nicht, die Platte wird
                 eng. Der Abend läuft weiter, aber jemand sollte hinsehen.
* ``kritisch`` — es geht nicht mehr. Kamera weg, kein Drucker verfügbar,
                 Platte voll. Ab hier hilft nur Hingehen.

Zwei Wege hinaus, getrennt schaltbar:

* **Mail** an den Betreiber, über dieselbe Warteschlange wie die Gastmails —
  also auch dann, wenn die Box gerade kein Netz hat. Eine Meldung, die erst
  am nächsten Morgen ankommt, ist immer noch besser als keine.
* **Anzeige an der Box**, aber nur auf dem Startbildschirm. Nie während der
  Aufnahme, nie auf dem Ergebnis: Eine Warnung über dem Gesicht des Gastes
  ist auf jedem Handyfoto von der Box zu sehen, und „Druckerfehler 0x8007"
  ist keine Nachricht an einen Hochzeitsgast.

Wiederholungen werden gedämpft. Die Kamera meldet ihren Verlust alle fünf
Sekunden; der Betreiber braucht das einmal. Dieselbe Meldung geht deshalb
erst nach ``wiederholen_minuten`` erneut hinaus — die Anzeige bleibt
solange stehen, die Mail nicht.
"""
from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional

log = logging.getLogger("boothcore.meldungen")

INFO, WARNUNG, KRITISCH = "info", "warnung", "kritisch"
STUFEN = (INFO, WARNUNG, KRITISCH)
#: Je höher, desto dringender — damit „ab dieser Stufe" eine Rechnung ist.
RANG = {INFO: 0, WARNUNG: 1, KRITISCH: 2}

#: Wie eine Stufe in einer Mail-Betreffzeile heißt. Wer morgens aufs Handy
#: schaut, soll ohne Öffnen wissen, ob er aufstehen muss.
BETREFF = {INFO: "Hinweis", WARNUNG: "Bitte ansehen", KRITISCH: "Die Box steht"}


@dataclass
class Meldung:
    key: str                      # stabil je Ursache — daran hängt die Dämpfung
    stufe: str
    titel: str
    text: str = ""
    at: float = field(default_factory=time.time)
    quelle: str = ""              # kamera | drucker | platte | galerie | box | betreiber
    #: Text key for the kiosk ("op.paper_out"), so the status line follows
    #: the interface's language; ``werte`` fills its placeholders.
    code: str = ""
    werte: Dict[str, str] = field(default_factory=dict)

    def as_dict(self) -> Dict[str, object]:
        return {"key": self.key, "stufe": self.stufe, "titel": self.titel, "text": self.text,
                "at": self.at, "quelle": self.quelle, "code": self.code, "werte": dict(self.werte),
                "zeit": time.strftime("%d.%m.%Y %H:%M", time.localtime(self.at))}


class Meldungen:
    """Sammelt, dämpft, verschickt und hält vor, was angezeigt werden soll."""

    def __init__(self, config: Callable[[], dict], *,
                 mail_enqueue: Optional[Callable[..., int]] = None,
                 clock: Optional[Callable[[], float]] = None,
                 groesse: int = 100) -> None:
        self._config = config
        self._mail = mail_enqueue
        self._clock = clock or time.time
        self._lock = threading.Lock()
        self._verlauf: List[Meldung] = []
        self._groesse = int(groesse)
        #: key -> wann zuletzt hinausgegangen. Nicht: wann zuletzt gemeldet.
        self._zuletzt_raus: Dict[str, float] = {}
        #: Was gerade ansteht. Eine Ursache, ein Eintrag — verschwindet, wenn
        #: der Auslöser meldet, dass es wieder geht (``entwarnung``).
        self._offen: Dict[str, Meldung] = {}

    # ── Einstellungen ─────────────────────────────────────────────────

    def _cfg(self) -> dict:
        roh = dict(self._config() or {})
        mail = dict(roh.get("mail") or {})
        box = dict(roh.get("box") or {})
        return {
            "mail": {"enabled": bool(mail.get("enabled")), "to": str(mail.get("to") or "").strip(),
                     "ab_stufe": mail.get("ab_stufe") if mail.get("ab_stufe") in STUFEN else WARNUNG},
            "box": {"enabled": bool(box.get("enabled", True)),
                    "ab_stufe": box.get("ab_stufe") if box.get("ab_stufe") in STUFEN else KRITISCH},
            "wiederholen_minuten": max(0, int(roh.get("wiederholen_minuten") or 60)),
        }

    # ── hereinkommen ──────────────────────────────────────────────────

    def melden(self, key: str, stufe: str, titel: str, text: str = "",
               quelle: str = "box", code: str = "",
               werte: Optional[Dict[str, str]] = None) -> Dict[str, object]:
        """Eine Meldung annehmen. Gibt zurück, was damit geschehen ist.

        The heartbeat raises an open cause again every few seconds. That is
        still one message: it is written to the history and logged once,
        until the all-clear (``entwarnung``) or a change of level or title.
        Until 12.11.2026 every heartbeat logged it again at ERROR, and the
        gallery's dashboard counted ~100 errors an hour for one cause.
        """
        stufe = stufe if stufe in STUFEN else INFO
        m = Meldung(key=str(key or "")[:60] or "ohne", stufe=stufe, titel=str(titel)[:200],
                    text=str(text or "")[:2000], at=self._clock(), quelle=quelle,
                    code=str(code or "")[:40], werte={str(k)[:20]: str(v)[:200] for k, v in (werte or {}).items()})
        cfg = self._cfg()
        with self._lock:
            alt = self._offen.get(m.key)
            if (stufe != INFO and alt is not None and alt.stufe == stufe and alt.titel == m.titel
                    and not self._darf_raus(m.key, cfg["wiederholen_minuten"])):
                # Same cause, still open, no reminder due: refresh the details,
                # keep its time, say nothing. A due reminder goes the normal way.
                m.at = alt.at
                self._offen[m.key] = m
                return {"key": m.key, "stufe": stufe, "gemailt": False, "wiederholung": True}
            self._verlauf.append(m)
            del self._verlauf[:-self._groesse]
            if stufe != INFO:
                self._offen[m.key] = m
            frisch = self._darf_raus(m.key, cfg["wiederholen_minuten"])
            if frisch:
                self._zuletzt_raus[m.key] = m.at
        gemailt = False
        if frisch and RANG[stufe] >= RANG[cfg["mail"]["ab_stufe"]]:
            gemailt = self._mailen(m, cfg)
        log.log(logging.ERROR if stufe == KRITISCH else
                logging.WARNING if stufe == WARNUNG else logging.INFO,
                "Meldung [%s] %s: %s", stufe, m.titel, m.text)
        return {"key": m.key, "stufe": stufe, "gemailt": gemailt, "wiederholung": not frisch}

    def entwarnung(self, key: str) -> None:
        """Die Ursache ist weg — der Eintrag verschwindet aus dem Anstehenden.

        Wichtig, damit die Box nach dem Papierwechsel nicht weiter „kein
        Papier" anzeigt. Der Verlauf bleibt; eine Warnung, die einmal da war,
        gehört ins Protokoll.
        """
        with self._lock:
            self._offen.pop(str(key), None)
            self._zuletzt_raus.pop(str(key), None)

    def _darf_raus(self, key: str, minuten: int) -> bool:
        letzte = self._zuletzt_raus.get(key)
        if letzte is None:
            return True
        return (self._clock() - letzte) >= minuten * 60

    # ── hinausgehen ───────────────────────────────────────────────────

    def _mailen(self, m: Meldung, cfg: dict) -> bool:
        ziel = cfg["mail"]["to"]
        if not (cfg["mail"]["enabled"] and ziel and self._mail):
            return False
        betreff = f"[Fotobox · {BETREFF[m.stufe]}] {m.titel}"
        zeit = time.strftime("%d.%m.%Y um %H:%M", time.localtime(m.at))
        text = (f"{m.titel}\n\n{m.text}\n\n"
                f"Stufe: {m.stufe}\nQuelle: {m.quelle}\nZeitpunkt: {zeit}\n")
        try:
            # Über dieselbe Warteschlange wie die Gastmails: Die Box ist oft
            # offline, und eine Meldung, die morgen ankommt, ist besser als
            # eine, die nie geschrieben wurde.
            self._mail(address=ziel, files=[], subject=betreff, body=text)
            return True
        except Exception as e:                              # pragma: no cover - SMTP-Fehler
            log.warning("Meldung konnte nicht eingereiht werden: %s", e)
            return False

    # ── herausgeben ───────────────────────────────────────────────────

    def fuer_die_box(self) -> Optional[Dict[str, object]]:
        """Was auf dem Startbildschirm stehen soll — oder nichts.

        Immer nur die dringendste, und immer nur eine: Ein Gast, der vor einer
        Liste von Fehlermeldungen steht, geht weiter.
        """
        cfg = self._cfg()
        if not cfg["box"]["enabled"]:
            return None
        schwelle = RANG[cfg["box"]["ab_stufe"]]
        with self._lock:
            passend = [m for m in self._offen.values() if RANG[m.stufe] >= schwelle]
        if not passend:
            return None
        dringend = max(passend, key=lambda m: (RANG[m.stufe], m.at))
        return dringend.as_dict()

    def offen(self) -> List[Dict[str, object]]:
        with self._lock:
            return [m.as_dict() for m in sorted(self._offen.values(),
                                                key=lambda m: (-RANG[m.stufe], -m.at))]

    def verlauf(self, limit: int = 50) -> List[Dict[str, object]]:
        with self._lock:
            return [m.as_dict() for m in reversed(self._verlauf[-max(1, int(limit)):])]

    def schlimmste(self) -> str:
        with self._lock:
            if not self._offen:
                return "ok"
            return max(self._offen.values(), key=lambda m: RANG[m.stufe]).stufe

    # ── Probe ─────────────────────────────────────────────────────────

    def probe(self) -> Dict[str, object]:
        """Eine Meldung je Stufe — damit der Betreiber den Weg einmal sieht.

        Ohne das merkt man einen falschen SMTP-Server erst an dem Abend, an
        dem die Meldung wirklich gebraucht wird. Die Dämpfung wird dafür
        übergangen: Eine Probe, die wegen einer Wiederholungssperre nicht
        losgeht, prüft nichts.
        """
        with self._lock:
            for stufe in STUFEN:
                self._zuletzt_raus.pop(f"probe-{stufe}", None)
        ergebnisse = []
        for stufe in STUFEN:
            ergebnisse.append(self.melden(
                f"probe-{stufe}", stufe, f"Probemeldung ({BETREFF[stufe]})",
                "Dies ist eine Probe. Wenn diese Nachricht ankommt, funktioniert der Weg — "
                "an dem Abend, an dem es darauf ankommt, wird sie genauso zugestellt.",
                quelle="betreiber"))
        # Proben stehen nicht als offene Punkte herum.
        for stufe in STUFEN:
            self.entwarnung(f"probe-{stufe}")
        cfg = self._cfg()
        gemailt = [e for e in ergebnisse if e["gemailt"]]
        if not cfg["mail"]["enabled"]:
            hinweis = "Mail-Meldungen sind aus — es wurde nichts verschickt."
        elif not cfg["mail"]["to"]:
            hinweis = "Es ist keine Empfängeradresse hinterlegt."
        elif not gemailt:
            hinweis = (f"Keine Probe erreichte die Schwelle „ab {cfg['mail']['ab_stufe']}" + "“.")
        else:
            hinweis = (f"{len(gemailt)} von {len(STUFEN)} Proben eingereiht an {cfg['mail']['to']}. "
                       "Die Warteschlange stellt zu, sobald Netz da ist.")
        return {"ergebnisse": ergebnisse, "hinweis": hinweis}


# ── Übersetzung aus der Selbstdiagnose ────────────────────────────────

#: Die Selbstdiagnose kennt vier Zustände, die Meldungen drei. „skip" heißt
#: „nicht eingerichtet" und ist keine Nachricht wert.
AUS_DOCTOR = {"fail": KRITISCH, "warn": WARNUNG, "ok": None, "skip": None}


def aus_diagnose(meldungen: "Meldungen", checks) -> int:
    """Die Selbstdiagnose in Meldungen übersetzen — und Behobenes entwarnen."""
    anzahl = 0
    for c in checks:
        d = c.as_dict() if hasattr(c, "as_dict") else dict(c)
        key = f"doctor-{d.get('name', '')}"
        stufe = AUS_DOCTOR.get(str(d.get("state")), None)
        if stufe is None:
            meldungen.entwarnung(key)
            continue
        meldungen.melden(key, stufe, str(d.get("name", "")),
                         " ".join(x for x in (d.get("message"), d.get("hint")) if x),
                         quelle="box")
        anzahl += 1
    return anzahl
