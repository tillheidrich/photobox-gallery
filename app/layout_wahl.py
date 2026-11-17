"""Which layouts the booth offers at one event, and their pictures in the admin.

Owner, 15.11.2026: "Man muss je Event aktivieren können welche Layouts es
gibt". The event page lists every layout there is — own templates (those
adopted from the library are own templates too, ``vorlagen.eigene``), the
plain family and the other built-ins — each with a picture and a box.

How it reaches the booth: ticked layouts become the event's booth overrides
``collage_exact`` + ``collage_templates`` + ``collage_builtins``
(``ecosystem.event_profile_overrides``); the booth then offers exactly those
(``Booth.templates``). Nothing ticked: no override, the box decides as before.
Template files travel with the profile (``assets_for_profile``), so the
choice works offline once the box has synced.

The pictures are made here, by the gallery, with the renderer the booth
prints with (``vorlagen_render``, one file in both programs) and the shared
sample photos; never fetched from a booth. They are cached on disk under a
key of everything that changes them, so the page costs one render per
layout and style, ever.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import io
import json
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional

#: Bump when the way pictures are made changes, so old files are not served.
REVISION = 1
EDGE = 480
_LOCK = threading.Lock()
_CATALOGUE: Dict[str, Any] = {}


def cache_dir() -> Path:
    from app import media
    d = media.data_dir() / "cache" / "layout-wahl"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _catalogue() -> Dict[str, Dict[str, Any]]:
    """The booth's built-ins as the gallery has them (tools/build_collage_catalog.py)."""
    if not _CATALOGUE:
        pfad = Path(__file__).resolve().parent / "static" / "data" / "collage_templates.json"
        data = json.loads(pfad.read_text(encoding="utf-8"))
        for t in data.get("templates") or []:
            _CATALOGUE[t["id"]] = t
    return _CATALOGUE


def builtin_template(ident: str) -> Optional[Dict[str, Any]]:
    """A built-in as the booth draws it: the catalogue omits what every
    ordinary built-in shares (style background, rounded corners)."""
    t = _catalogue().get(str(ident))
    if not t:
        return None
    t = json.loads(json.dumps(t))
    if not t.get("plain"):
        t.setdefault("background", "@hintergrund")
        for s in t.get("slots") or []:
            s.setdefault("radius", "@rund")
    if not t.get("qr"):
        t.pop("qr", None)
    return t


def offer(assets_dir: Path) -> Dict[str, List[Dict[str, Any]]]:
    """Every layout the event page lists, in three groups."""
    from app import ecosystem, vorlagen
    eigene = []
    for e in vorlagen.eigene(assets_dir):
        t = e["template"]
        breite, hoehe = (t.get("canvas") or [1200, 1800])[:2]
        eigene.append({"kind": "t", "id": e["asset"]["file"], "name": e["asset"].get("name") or t.get("name"),
                       "shots": int(t.get("shots") or len(t.get("slots") or []) or 1),
                       "orientation": "landscape" if breite > hoehe else "portrait",
                       "paper": t.get("paper") or ""})
    plain, builtin = [], []
    for c in ecosystem.BUILTIN_COLLAGES:
        eintrag = {"kind": "b", "id": c["id"], "name": c["name"], "shots": c["shots"],
                   "orientation": c["orientation"], "paper": c["paper"]}
        (plain if c.get("plain") else builtin).append(eintrag)
    return {"eigene": eigene, "schlicht": plain, "mitgeliefert": builtin}


def template_for(kind: str, ident: str, assets_dir: Path) -> Optional[Dict[str, Any]]:
    from app import ecosystem, vorlagen
    if kind == "b":
        return builtin_template(ident) if ident in ecosystem.BUILTIN_COLLAGE_IDS else None
    if kind == "t" and any(a["file"] == ident for a in ecosystem.list_assets("template")):
        return vorlagen.laden(ident, assets_dir)
    return None


def _key(template: Dict[str, Any], style: Dict[str, Any], assets_dir: Path) -> str:
    from app import vorlagen_paket
    medien = []
    try:
        for name in sorted(vorlagen_paket.medien_von(template)):
            p = assets_dir / name
            medien.append([name, p.stat().st_size, int(p.stat().st_mtime)] if p.is_file() else [name])
    except Exception:                    # noqa: BLE001 — a key without media is still a key
        pass
    roh = json.dumps([REVISION, EDGE, template, style, medien], sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(roh.encode("utf-8")).hexdigest()[:32]


def picture(kind: str, ident: str, assets_dir: Path, style: Optional[Dict[str, Any]] = None) -> Optional[bytes]:
    """The layout as PNG at most ``EDGE`` px, from the cache or rendered once."""
    from app import vorlagen, vorlagen_render
    template = template_for(kind, ident, assets_dir)
    if template is None:
        return None
    style = dict(style or {"preset": "clean"})
    if style.get("preset") not in vorlagen_render.STYLE_PRESETS:
        style["preset"] = "clean"
    pfad = cache_dir() / f"{_key(template, style, assets_dir)}.png"
    if pfad.is_file():
        return pfad.read_bytes()
    with _LOCK:                          # one render at a time: a page asks for forty at once
        if pfad.is_file():
            return pfad.read_bytes()
        bild = vorlagen_render.render_template(
            template, vorlagen.beispielbilder(int(template.get("shots") or 1)), assets_dir=assets_dir,
            qr_url="https://example.invalid/vorschau",
            texts={"event_name": "Mia & Tom", "date": dt.date(2026, 7, 20).strftime("%d.%m.%Y")},
            style_cfg=style)
        bild.thumbnail((EDGE, EDGE))
        puffer = io.BytesIO()
        bild.convert("RGB").save(puffer, "PNG", optimize=True)
        tmp = pfad.with_suffix(".part")
        tmp.write_bytes(puffer.getvalue())
        tmp.replace(pfad)
        return puffer.getvalue()


def from_form(form) -> Dict[str, Any]:
    """The event's booth overrides for layouts, from the event form.

    Only names that exist are kept. Nothing ticked: none of the three keys,
    so the box's own settings apply.
    """
    from app import ecosystem
    vorhanden = {a["file"] for a in ecosystem.list_assets("template")}
    eigene = [x for x in form.getlist("ev_collage_templates") if x in vorhanden]
    gebaut = [x for x in form.getlist("ev_collage_builtins") if x in ecosystem.BUILTIN_COLLAGE_IDS]
    if not eigene and not gebaut:
        return {}
    return {"collage_exact": True, "collage_templates": eigene, "collage_builtins": gebaut}


__all__ = ["EDGE", "REVISION", "builtin_template", "cache_dir", "from_form", "offer", "picture", "template_for"]
