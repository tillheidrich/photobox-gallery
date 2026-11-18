"""Ein Gast-Durchgang: Modus wählen -> Aufnahme(n) -> Ergebnis -> teilen/drucken.

Die Oberfläche steuert den Ablauf, die Box führt aus und hält den Zustand.
Jeder Durchgang bekommt beim Start seine Kennung — damit steht der QR-Link
fest, bevor überhaupt ein Foto existiert.
"""
from __future__ import annotations

import datetime as dt
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import share

VIDEO_MODES = ("boomerang", "video")


@dataclass
class Session:
    uid: str
    mode: str                     # photo | collage | gif | boomerang | video
    fmt: str                      # Framing, z. B. 4:5
    folder: Path
    event_slug: str
    event_name: str
    template: Optional[dict] = None
    background: Optional[str] = None
    chroma_background: str = ""    # Greenscreen-Hintergrund, vom Gast gewählt
    shots: List[Path] = field(default_factory=list)
    shots_wanted: int = 1         # gif: from the profile, collage: from the template
    clip: Optional[Path] = None
    #: Boomerang-Fassung eines GIF-Durchgangs mit eigenen Maßen (fürs Handy).
    boom: Optional[Path] = None
    #: Der Gruß, den der Gast selbst eingetippt hat (leer = keiner).
    guest_text: str = ""
    #: What the guest drew on the finished picture: checked strokes from
    #: ``drawing.clean``. Painted on at every render, never into a file that
    #: is drawn on again — so a second drawing replaces the first.
    drawing: List[Dict[str, Any]] = field(default_factory=list)
    poster: Optional[Path] = None
    look: str = "original"
    result: Optional[Path] = None
    result_web: Optional[Path] = None
    thumb: Optional[Path] = None
    print_file: Optional[Path] = None
    #: Bewegte Fassung einer Collage — dasselbe Ergebnis, nur als GIF.
    anim: Optional[Path] = None
    #: Die Einzelbilder einer Collage, fertig bearbeitet — für die Galerie,
    #: wenn der Betreiber sie dort einzeln haben will. Je Eintrag (groß, Web).
    singles: List[tuple] = field(default_factory=list)
    finalized: bool = False
    #: Einmal wahr, nie wieder falsch. ``finalized`` heißt „das Ergebnis ist
    #: aktuell" und wird von jeder neuen Aufnahme zurückgesetzt — auch von
    #: „Nochmal". Wer damit „ist dieser Durchgang neu?" beantwortet, zählt
    #: jede Wiederholung als weitere Sitzung, legt einen zweiten Löschtimer
    #: an und druckt automatisch ein zweites Blatt. Dafür gibt es dieses Feld.
    abgerechnet: bool = False
    deleted: bool = False
    #: The guest chose "Nur drucken": this run follows the data-minimal mode
    #: (no upload, no link, wiped after the print) while the booth does not.
    print_only: bool = False
    #: What the guest was shown and picked, without anything about the person:
    #: {"option", "lang", "text_hash", "text_version", "at"}. Empty = no choice step.
    consent: Dict[str, Any] = field(default_factory=dict)
    share_url: str = ""
    created: float = field(default_factory=time.time)
    lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
    #: The films of a GIF run are made after the result (Booth._gif_films_start):
    #: which finalize they belong to, and the thread that makes them.
    film_gen: int = 0
    films_thread: Optional[threading.Thread] = field(default=None, repr=False)
    #: A boomerang or film: the recording answers when the camera stops, the
    #: film is made meanwhile (Booth.record_clip) and finish waits for it.
    clip_job: Any = field(default=None, repr=False)
    #: The rate the clip plays at (the live view's real rate) and how long
    #: the camera really recorded, in seconds.
    clip_fps: int = 0
    clip_seconds: float = 0.0
    #: Key frames of a boomerang for its print sheet (``boomerang.print``),
    #: as the film shows them; empty when the sheet is off.
    key_frames: List[Any] = field(default_factory=list, repr=False)
    #: The motion frame ("Rahmen") a boomerang or film plays in, checked
    #: (media/motion_frames.py), and its id; None = without a frame.
    motion_frame: Optional[Dict[str, Any]] = field(default=None, repr=False)
    motion_frame_id: str = ""
    #: The boomerang length the guest chose (boomerang.seconds_choices),
    #: checked against the offer; None = no choice, the profile's seconds
    #: and loops apply as before.
    clip_choice_seconds: Optional[float] = None

    @property
    def is_video(self) -> bool:
        return self.mode in VIDEO_MODES

    @property
    def shots_needed(self) -> int:
        if self.mode == "gif":
            return max(2, int(self.shots_wanted or 4))
        if self.mode == "collage" and self.template:
            slots = self.template.get("slots", [])
            return int(self.template.get("shots") or len({s.get("shot", i) for i, s in enumerate(slots)}) or 1)
        return 0 if self.is_video else 1

    @property
    def taken_at(self) -> str:
        return dt.datetime.fromtimestamp(self.created).isoformat(timespec="seconds")

    def public(self) -> Dict:
        return {
            "uid": self.uid, "mode": self.mode, "format": self.fmt, "shots": len(self.shots),
            "shots_needed": self.shots_needed, "look": self.look, "finalized": self.finalized,
            "share_url": self.share_url, "template": (self.template or {}).get("id"),
            "chroma_background": self.chroma_background, "print_only": self.print_only,
            **({"motion_frame": self.motion_frame_id} if self.motion_frame_id else {}),
            **({"clip_seconds": self.clip_choice_seconds} if self.clip_choice_seconds else {}),
        }


def new_session(base: Path, mode: str, fmt: str, profile: dict, template: Optional[dict],
                background: Optional[str], chroma_background: str = "") -> Session:
    uid = share.new_uid()
    event = profile.get("event") or {}
    day = time.strftime("%Y-%m-%d")
    folder = base / (event.get("slug") or "ohne-event") / day / uid
    folder.mkdir(parents=True, exist_ok=True)
    s = Session(uid=uid, mode=mode, fmt=fmt, folder=folder, event_slug=event.get("slug", ""),
                event_name=event.get("name", ""), template=template, background=background,
                chroma_background=chroma_background,
                shots_wanted=int((profile.get("gif") or {}).get("shots", 4)))
    s.share_url = share.qr_target(profile, uid, mode)
    return s
