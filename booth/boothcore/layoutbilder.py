"""Small pictures of the layouts a guest can choose: what the sheet will look like.

The set-up screen offered the print layouts as text chips ("Raster · 4
Fotos"), and a guest could not tell one from the other (owner, 15.11.2026:
"Man sieht nicht die ganzen Vorschaubilder vom Layout"). Each offered layout
now has a picture, rendered by the booth with the renderer it prints with:
sample photos in the slots, the event's own texts, the operator's style,
frame and overlay, and the QR code only where a print would carry one.

Rendered in the background (at start and after a profile change, never
while a run is going), kept on disk under a key made of everything that
changes the picture — so a cached picture is never out of date, and an
unchanged layout is not rendered again after a restart.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Dict, List, Optional, Tuple

from PIL import Image

from .media import compose

log = logging.getLogger("booth.layoutbilder")

#: Bump when the way pictures are made changes, so old files are not served.
REVISION = 1
#: Height of a picture in pixels: a card at 4K is about 300 CSS px high at
#: a pixel ratio of 2, so one size serves 1280×800 up to 4K.
HOEHE = 600
#: Kinds of layout: a collage template, a photo format's print card, the GIF sheet,
#: a collage template as a boomerang's sheet in one format (id "<template>@<format>"),
#: a frame for boomerang and video ("m", id = built-in id or own file name).
ARTEN = ("t", "f", "g", "b", "m")
#: Ids become file names (Windows: no ":"), so "4:5" is "4-5" on disk and in the URL.
_NAME = re.compile(r"[^A-Za-z0-9._-]+")


def _sauber(ident: str) -> str:
    return _NAME.sub("-", str(ident))[:80] or "x"


class Layoutbilder:
    def __init__(self, booth, ordner: Optional[Path] = None):
        self.b = booth
        self.ordner = Path(ordner or booth.data / "cache" / "layout-thumbs")
        self.ordner.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._wache = threading.Event()
        self._faden: Optional[threading.Thread] = None
        #: Milliseconds the last render of each layout took, for the admin and the report.
        self.dauer: Dict[str, int] = {}

    # ── what is offered ──────────────────────────────────
    def angebot(self) -> List[Tuple[str, str, dict]]:
        """(kind, id, template) of every layout this booth offers right now."""
        p = self.b.profile
        aus: List[Tuple[str, str, dict]] = [("t", str(t.get("id")), t) for t in self.b.templates()]
        mit_qr = self._qr_url() is not None
        for fmt in p.get("formats") or []:
            if fmt in compose.ASPECTS:
                aus.append(("f", fmt, compose.single_print_template(fmt, mit_qr, p.get("print_qr"))))
        aus.append(("g", "gif", self._gif_vorlage()))
        # boomerang.print: the same templates, filled with the boomerang's
        # framing in each format the guest can get — a 9:16 boomerang on a
        # landscape sheet looks different from a photo's collage there.
        if self.b.boomerang_prints() and "boomerang" in (p.get("modes") or []):
            for t in self.b.templates():
                for fmt in self.boomerang_formate():
                    aus.append(("b", f"{t.get('id')}@{fmt}", t))
        # Frames for boomerang and video: only while switched on (nothing extra rendered otherwise).
        gesehen = set()
        for modus in ("boomerang", "video"):
            if modus not in (p.get("modes") or []):
                continue
            for ident, rahmen in self.b.motion_frame_offer(modus)["frames"]:
                if ident not in gesehen:
                    gesehen.add(ident)
                    aus.append(("m", ident, rahmen))
        return aus

    def boomerang_formate(self) -> List[str]:
        """The formats a boomerang can have here: the guest's choice, or the operator's one."""
        p = self.b.profile
        eigenes = str((p.get("boomerang") or {}).get("format") or "9:16")
        wahl = [f for f in (p.get("formats") or []) if f in compose.ASPECTS] if self.b.boomerang_format_choice() else []
        return wahl if len(wahl) > 1 else [eigenes]

    def _gif_vorlage(self) -> dict:
        eigene = self.b._gif_druckvorlage()
        if eigene is not None:
            return eigene
        gif = self.b.profile.get("gif") or {}
        return compose.gif_print_template(self.b.profile.get("default_format", "4:5"),
                                          int(gif.get("shots", 4) or 4))[0]

    def vorlage(self, art: str, ident: str) -> Optional[dict]:
        return next((t for a, i, t in self.angebot() if a == art and i == ident), None)

    # ── what the picture depends on ─────────────────────
    def _qr_url(self) -> Optional[str]:
        """The sample link when a print of this booth carries a code, else None."""
        p = self.b.profile
        if not (p.get("print") or {}).get("qr_on_print"):
            return None
        try:
            from . import share
            ziel = share.qr_target(p, "vorschau", "photo")
        except Exception:                              # noqa: BLE001 — no link, no code
            ziel = ""
        return compose.BEISPIEL_QR if ziel and not self.b.ephemeral else None

    def _texte(self) -> dict:
        s = self._lauf()
        return self.b._texts(s)

    def _lauf(self):
        """What _texts and apply_frame need of a run: the event, today, no guest text."""
        return SimpleNamespace(event_name=self.b.profile["event"].get("name", ""), created=time.time(),
                               guest_text="", drawing=[])

    def _dateien(self, tpl: dict) -> List[str]:
        namen = [tpl.get("overlay"), tpl.get("background")]
        namen += [i.get("file") for i in tpl.get("images") or [] if isinstance(i, dict)]
        namen += [t.get(k) for t in tpl.get("texts") or [] if isinstance(t, dict) for k in ("font", "amp_font")]
        rahmen = self.b.profile.get("frame") or {}
        if rahmen.get("enabled") and rahmen.get("on_print", True):
            namen += [rahmen.get("print_overlay"), rahmen.get("overlay")]
        aus = []
        for n in namen:
            if not n or str(n).startswith("#") or str(n).startswith("@"):
                continue
            p = self.b.assets_dir / str(n)
            try:
                st = p.stat()
                aus.append(f"{n}:{st.st_size}:{int(st.st_mtime)}")
            except OSError:
                aus.append(f"{n}:-")
        return aus

    def schluessel(self, art: str, ident: str, tpl: dict) -> str:
        p = self.b.profile
        heute = time.strftime("%Y-%m-%d")
        inhalt = json.dumps({"r": REVISION, "a": art, "i": ident, "t": tpl, "s": p.get("collage_style"),
                             "fit": p.get("collage_fit"), "frame": p.get("frame"), "qr": self._qr_url(),
                             "bl": (p.get("boomerang") or {}).get("layout") if art == "b" else None,
                             "x": self._texte(), "d": heute, "f": self._dateien(tpl)},
                            sort_keys=True, default=str)
        return hashlib.sha256(inhalt.encode("utf-8")).hexdigest()[:16]

    def url(self, art: str, ident: str, tpl: dict) -> str:
        from urllib.parse import quote
        return f"/api/layout-thumb/{art}/{quote(_sauber(ident))}.jpg?v={self.schluessel(art, ident, tpl)}"

    def urls(self) -> Dict[str, Dict[str, str]]:
        """{kind: {id: url}} for everything offered — one pass, for /api/state."""
        aus: Dict[str, Dict[str, str]] = {a: {} for a in ARTEN}
        for art, ident, tpl in self.angebot():
            aus[art][ident] = self.url(art, ident, tpl)
        return aus

    def _pfad(self, art: str, ident: str, schluessel: str) -> Path:
        return self.ordner / f"{art}-{_sauber(ident)}-{schluessel}.jpg"

    # ── making the picture ───────────────────────────────
    def lauf_aktiv(self) -> bool:
        """A guest's run is going: nothing is rendered that could slow it down."""
        jetzt = time.time()
        return any(not s.finalized and jetzt - s.created < 180 for s in list(self.b.sessions.values()))

    def datei(self, art: str, ident: str, warten: bool = False) -> Optional[Path]:
        """The picture of one layout: from the cache, or rendered now unless a run is going."""
        tpl = next((t for a, i, t in self.angebot() if a == art and _sauber(i) == _sauber(ident)), None)
        if tpl is None:
            return None
        return self.datei_fuer(art, ident, tpl, warten)

    def datei_fuer(self, art: str, ident: str, tpl: dict, warten: bool = False) -> Optional[Path]:
        """The picture of a layout given by its template, offered or not (the templates page's tiles)."""
        schluessel = self.schluessel(art, ident, tpl)
        ziel = self._pfad(art, ident, schluessel)
        if ziel.is_file():
            return ziel
        if self.lauf_aktiv() and not warten:
            return None
        with self._lock:
            if ziel.is_file():
                return ziel
            return self._rendern(art, ident, tpl, ziel)

    def _rendern(self, art: str, ident: str, tpl: dict, ziel: Path) -> Optional[Path]:
        anfang = time.perf_counter()
        p = self.b.profile
        try:
            if art == "m":
                # A frame with the sample picture in its window, the event's texts in it.
                from .media import motion_frames
                bild = motion_frames.preview(tpl, self.b.assets_dir, self._texte(), max_px=int(HOEHE * 1.6),
                                             style_cfg=p.get("collage_style"))
                tmp = ziel.with_suffix(".tmp")
                bild.convert("RGB").save(tmp, "JPEG", quality=84, optimize=True, progressive=True)
                tmp.replace(ziel)
                self.dauer[f"{art}/{ident}"] = int((time.perf_counter() - anfang) * 1000)
                return ziel
            if art == "f":
                bilder = [compose.crop_to_aspect(compose.beispielbilder(1)[0], ident)]
            elif art == "b":
                from .media import boomerang
                fmt = ident.rsplit("@", 1)[-1]
                n = int(compose.template_info(tpl)["shots"] or 1)
                layout = str((p.get("boomerang") or {}).get("layout") or "crop")
                bilder = [boomerang.print_frame(b, fmt, layout) for b in compose.beispielbilder(n)]
            else:
                slots = tpl.get("slots") or []
                n = int(tpl.get("shots") or len({s.get("shot", i) for i, s in enumerate(slots)}) or 1)
                bilder = compose.beispielbilder(n)
            bild = compose.render_template(tpl, bilder, assets_dir=self.b.assets_dir, qr_url=self._qr_url(),
                                           texts=self._texte(), style_cfg=p.get("collage_style"),
                                           einpassen=p.get("collage_fit", "auto"))
            bild = self.b.apply_frame(bild, self._lauf(), "print")
            bild = bild.convert("RGB")
            bild.thumbnail((int(HOEHE * 1.6), HOEHE), Image.Resampling.LANCZOS)
            tmp = ziel.with_suffix(".tmp")
            bild.save(tmp, "JPEG", quality=84, optimize=True, progressive=True)
            tmp.replace(ziel)
        except Exception:                              # noqa: BLE001 — the kiosk then draws the outline
            log.exception("Vorschaubild für %s/%s nicht erzeugt", art, ident)
            return None
        self.dauer[f"{art}/{ident}"] = int((time.perf_counter() - anfang) * 1000)
        for alt in self.ordner.glob(f"{art}-{_sauber(ident)}-*.jpg"):
            if alt != ziel:
                alt.unlink(missing_ok=True)
        return ziel

    # ── in the background ────────────────────────────────
    def vorbereiten(self) -> None:
        """Render every offered picture that is missing — in a thread, between runs."""
        self._wache.set()
        if self._faden and self._faden.is_alive():
            return
        self._faden = threading.Thread(target=self._arbeiten, name="layoutbilder", daemon=True)
        self._faden.start()

    def _arbeiten(self) -> None:
        while self._wache.is_set():
            self._wache.clear()
            for art, ident, tpl in self.angebot():
                while self.lauf_aktiv():
                    time.sleep(1.0)
                try:
                    self.datei(art, ident)
                except Exception:                      # noqa: BLE001
                    log.exception("Vorschaubild %s/%s", art, ident)

    def vorwaermen(self, eintraege: List[Tuple[str, str, dict]]) -> None:
        """Render the pictures of these layouts (the templates page's tiles, hidden ones too)
        in the background, so the page finds them on disk.

        Opening the page asked for ~45 pictures, two at a time, each rendered
        on demand; it took seconds every first time (17.11.2026). One thread,
        the newest list wins, it waits while a guest's run is going.
        """
        self._waerm_liste = list(eintraege)
        faden = getattr(self, "_waerm_faden", None)
        if faden and faden.is_alive():
            return
        self._waerm_faden = threading.Thread(target=self._waermen, name="layoutbilder-alle", daemon=True)
        self._waerm_faden.start()

    def _waermen(self) -> None:
        while getattr(self, "_waerm_liste", None):
            art, ident, tpl = self._waerm_liste.pop(0)
            while self.lauf_aktiv():
                time.sleep(1.0)
            try:
                self.datei_fuer(art, ident, tpl)
            except Exception:                          # noqa: BLE001
                log.exception("Vorschaubild %s/%s", art, ident)

    def groesse(self) -> int:
        """Bytes the cache takes on disk."""
        return sum(p.stat().st_size for p in self.ordner.glob("*.jpg"))
