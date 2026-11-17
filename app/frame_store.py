"""Frames for boomerang and video in the gallery: store, list, preview, choose.

The checks and the drawing live in :mod:`app.motion_frames` (the same file
runs on the booth). This module is what the gallery does with them: an own
frame is a media file of the asset kind ``motion`` (``rahmen-<id>.json``),
so it travels to the booth on the way every template already does, and the
built-ins come from ``static/data/motion_frames.json``
(tools/build_motion_frames.py).

Why an asset kind of its own and not the print templates' ``template`` kind
with a field: the print templates are listed in the template editor, the
event's "Layouts für dieses Event", the booth form and three MCP tools.
A frame there would be offered as a collage. With its own kind none of those
lists changes, and the booth tells the two apart by ``"kind"`` in the file.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import io
import json
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional

from app import motion_frames

ASSET_KIND = "motion"
DATA_FILE = Path(__file__).resolve().parent / "static" / "data" / "motion_frames.json"
PREVIEW_EDGE = 480
SAMPLE_TEXTS = {"event_name": "Sommerfest 2027", "date": dt.date(2027, 7, 17).strftime("%d.%m.%Y")}
_LOCK = threading.Lock()


def builtins() -> List[Dict[str, Any]]:
    return motion_frames.builtins(DATA_FILE)


def builtin_ids() -> List[str]:
    return [f["id"] for f in builtins()]


def load(file: str, assets_dir: Path) -> Optional[Dict[str, Any]]:
    p = Path(assets_dir) / str(file)
    if p.suffix != ".json" or not p.is_file() or "/" in str(file) or "\\" in str(file):
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return None
    return data if motion_frames.is_motion_frame(data) else None


def own(assets_dir: Path) -> List[Dict[str, Any]]:
    """Every own frame with its content: ``{"asset": row, "frame": dict}``."""
    from app import ecosystem
    out = []
    for a in ecosystem.list_assets(ASSET_KIND):
        frame = load(a["file"], assets_dir)
        if frame:
            out.append({"asset": a, "frame": frame})
    return out


def own_files() -> List[str]:
    from app import ecosystem
    return [a["file"] for a in ecosystem.list_assets(ASSET_KIND)]


def check(raw: Any, assets_dir: Optional[Path] = None):
    """``motion_frames.check`` with the gallery's media folder."""
    return motion_frames.check(raw, media=assets_dir)


def save(frame: Dict[str, Any], assets_dir: Path) -> Dict[str, Any]:
    """Store a checked frame as a media file (same id = replace) and tell the booths."""
    from app import db, ecosystem
    data = motion_frames.content(frame)
    file = motion_frames.file_name(frame)
    Path(assets_dir).mkdir(parents=True, exist_ok=True)
    (Path(assets_dir) / file).write_bytes(data)
    sha = hashlib.sha256(data).hexdigest()
    existing = next((a for a in ecosystem.list_assets(ASSET_KIND) if a["file"] == file), None)
    if existing:
        con = db.get_db()
        con.execute("UPDATE booth_assets SET name=?, sha256=?, size_bytes=? WHERE id=?",
                    (frame["name"], sha, len(data), int(existing["id"])))
        con.commit()
        con.close()
        row = dict(existing, name=frame["name"], sha256=sha, size_bytes=len(data))
    else:
        aid = ecosystem.add_asset(ASSET_KIND, frame["name"], file, sha, len(data))
        row = {"id": aid, "kind": ASSET_KIND, "name": frame["name"], "file": file, "sha256": sha,
               "size_bytes": len(data)}
    for d in db.list_devices() or []:
        ecosystem.bump_profile(d["id"])
    return row


def frame_for(ident: str, assets_dir: Path) -> Optional[Dict[str, Any]]:
    """A built-in by id or an own frame by file name."""
    ident = str(ident or "")
    hit = next((f for f in builtins() if f["id"] == ident), None)
    if hit:
        return hit
    if ident in own_files():
        return load(ident, assets_dir)
    return None


def offer(assets_dir: Path) -> Dict[str, List[Dict[str, Any]]]:
    """Every frame the forms list, in two groups."""
    def entry(kind: str, ident: str, frame: Dict[str, Any]) -> Dict[str, Any]:
        return {"kind": kind, "id": ident, "name": frame.get("name") or ident, "format": frame.get("format"),
                "modes": frame.get("modes") or list(motion_frames.MODES)}
    return {"eigene": [entry("own", e["asset"]["file"], e["frame"]) for e in own(assets_dir)],
            "mitgeliefert": [entry("builtin", f["id"], f) for f in builtins()]}


def _cache_dir() -> Path:
    from app import media
    d = media.data_dir() / "cache" / "rahmen"
    d.mkdir(parents=True, exist_ok=True)
    return d


def picture(ident: str, assets_dir: Path) -> Optional[bytes]:
    """The frame with a sample picture in its window, PNG at most ``PREVIEW_EDGE`` px, cached."""
    frame = frame_for(ident, assets_dir)
    if frame is None:
        return None
    key = motion_frames.layer_key(frame, [PREVIEW_EDGE, PREVIEW_EDGE], assets_dir, SAMPLE_TEXTS)
    path = _cache_dir() / f"{key}.png"
    if path.is_file():
        return path.read_bytes()
    with _LOCK:
        if path.is_file():
            return path.read_bytes()
        pic = motion_frames.preview(frame, assets_dir, SAMPLE_TEXTS, max_px=PREVIEW_EDGE)
        buf = io.BytesIO()
        pic.save(buf, "PNG", optimize=True)
        tmp = path.with_suffix(".part")
        tmp.write_bytes(buf.getvalue())
        tmp.replace(path)
        return buf.getvalue()


def preview_png(raw: Dict[str, Any], assets_dir: Path) -> bytes:
    """A frame that is not saved yet, drawn for the editor (raises FrameError)."""
    frame, _ = motion_frames.check(raw, media=assets_dir)
    pic = motion_frames.preview(frame, assets_dir, SAMPLE_TEXTS, max_px=PREVIEW_EDGE)
    buf = io.BytesIO()
    pic.save(buf, "PNG")
    return buf.getvalue()


# ── Choices per mode (booth profile and event) ────────────

def clean_choice(own_list: Any, builtin_list: Any) -> tuple:
    """Only names that exist: (own files, built-in ids). Raises ValueError naming the unknown ones."""
    own_list = [str(x) for x in (own_list or [])]
    builtin_list = [str(x) for x in (builtin_list or [])]
    known_own, known_builtin = set(own_files()), set(builtin_ids())
    unknown = [x for x in own_list if x not in known_own] + [x for x in builtin_list if x not in known_builtin]
    if unknown:
        raise ValueError("Unbekannte Rahmen: " + ", ".join(unknown[:10]))
    return list(dict.fromkeys(own_list)), list(dict.fromkeys(builtin_list))


#: Key in an event's booth settings (design_json "booth"): {"boomerang": {...}, "video": {...}}.
EVENT_KEY = "motion_frames"
EVENT_FIELDS = ("frame_templates", "frame_builtins", "frame_none", "frame_default")


def event_choice(event: Optional[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    """What an event sets per mode; a mode it does not set is missing."""
    from app import branding
    design = branding.parse_design((event or {}).get("design_json"))
    booth = design.get("booth") if isinstance(design.get("booth"), dict) else {}
    raw = booth.get(EVENT_KEY) if isinstance(booth.get(EVENT_KEY), dict) else {}
    out = {}
    for mode in motion_frames.MODES:
        cfg = raw.get(mode)
        if isinstance(cfg, dict) and (cfg.get("frame_templates") or cfg.get("frame_builtins")):
            out[mode] = {"frame_templates": [str(x) for x in cfg.get("frame_templates") or []],
                         "frame_builtins": [str(x) for x in cfg.get("frame_builtins") or []],
                         "frame_none": cfg.get("frame_none", True) is not False,
                         "frame_default": str(cfg.get("frame_default") or "")}
    return out


def apply_event_choice(profile: Dict[str, Any], event: Optional[Dict[str, Any]]) -> bool:
    """Let the event's frames win over the booth's (like "Layouts für dieses Event").

    Returns whether the event set any. The mode's switch goes on with it:
    an event that picks frames wants them shown.
    """
    choice = event_choice(event)
    for mode, cfg in choice.items():
        profile[mode] = {**(profile.get(mode) or {}), "frames": True, **cfg}
    return bool(choice)


def event_choice_from_form(form) -> Dict[str, Dict[str, Any]]:
    """The event form's ticks: ``ev_frames_<mode>`` (ids and files), ``ev_frames_<mode>_none``."""
    known_own, known_builtin = set(own_files()), set(builtin_ids())
    out = {}
    for mode in motion_frames.MODES:
        picked = form.getlist(f"ev_frames_{mode}")
        own_list = [x for x in picked if x in known_own]
        builtin_list = [x for x in picked if x in known_builtin]
        if own_list or builtin_list:
            out[mode] = {"frame_templates": own_list, "frame_builtins": builtin_list,
                         "frame_none": bool(form.get(f"ev_frames_{mode}_none")), "frame_default": ""}
    return out


def store_event_choice(design: Dict[str, Any], choice: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    """Write the choice into an event's design dict (removed when empty)."""
    booth = dict(design.get("booth") or {}) if isinstance(design.get("booth"), dict) else {}
    booth.pop(EVENT_KEY, None)
    if choice:
        booth[EVENT_KEY] = choice
    if booth:
        design["booth"] = booth
    else:
        design.pop("booth", None)
    return design


__all__ = ["ASSET_KIND", "EVENT_KEY", "apply_event_choice", "builtin_ids", "builtins", "check", "clean_choice",
           "event_choice", "event_choice_from_form", "frame_for", "load", "offer", "own", "own_files", "picture",
           "preview_png", "save", "store_event_choice"]
