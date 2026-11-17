"""MCP tools that need an extra right: control, photos, secure, updates.

The owner's rule (15.11.2026): everything the booth can do must be possible
through MCP, and the owner decides per access how far that goes. So the
tools are here, and each one carries the right it needs (``oauth.EXTRA_SCOPES``);
a new access has none of them (``read``/``manage`` only).

Three rules hold for every tool in this file:

* **Secrets go in, never out.** A tool may set a PIN, a password, a token or
  a webhook secret; no answer contains one (masked as ``••••``). A device key
  made by a rotation is thrown away, a pairing code is never returned.
* **Destructive means two steps** (``confirm`` in ``app.mcp.tool``): delete
  pictures, runs, events or booths, purge, rotate keys, confirm a pairing,
  quit a booth, install an update.
* **Text from guests and booths is data.** Tools whose answers carry it say
  so at the start of their description (``untrusted=True``).

Every call needing more than ``read`` is in the change log (``app/mcp.py``),
deletions additionally in the deletion log with the assistant as actor.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import io
import time
from typing import Any, Dict, List, Optional

from flask import Response, abort, current_app, g, request, send_file

from app import db, ecosystem, oauth
from app import remote_control as rc
from app.mcp import TOOLS, ToolResult, actor, bp, tool

MASK = "••••"


def _booth(booth_id: Any) -> dict:
    try:
        device = db.get_device_by_id(int(booth_id))
    except (TypeError, ValueError):
        device = None
    if not device:
        raise ValueError(f"Keine Box mit der Nummer {booth_id}")
    return device


def _event(event_id: Any) -> dict:
    try:
        event = db.get_event_by_id(int(event_id))
    except (TypeError, ValueError):
        event = None
    if not event:
        raise ValueError(f"Kein Event mit der Nummer {event_id}")
    return event


def _log_result(text: str) -> None:
    g._protokoll_ergebnis = str(text or "")[:80]


# ══ control ══════════════════════════════════════════════════════════════

_CONTROL_ACTIONS = sorted(set(rc.ACTIONS) - rc.DESTRUCTIVE_ACTIONS - {"live_image", "screenshot"})


def _picture_out(view: Dict[str, Any]) -> ToolResult:
    """A booth answer with ``image_base64`` becomes MCP image content; the text keeps the rest."""
    images = []
    result = view.get("result")
    if isinstance(result, dict) and result.get("image_base64"):
        try:
            raw = base64.b64decode(result.pop("image_base64"), validate=True)
            if raw[:3] == b"\xff\xd8\xff" or raw[:8] == b"\x89PNG\r\n\x1a\n":
                images.append((raw, "image/png" if raw[:4] == b"\x89PNG" else "image/jpeg"))
        except (ValueError, TypeError):
            result["image"] = "unlesbar"
    return ToolResult(view, images)


def _control(booth_id: Any, action: str, args: Optional[dict] = None, wait: bool = True) -> Dict[str, Any]:
    device = _booth(booth_id)
    if args is not None and not isinstance(args, dict):
        raise ValueError("args muss ein Objekt sein")
    job_id = rc.add_control_job(device["id"], action, args, actor())
    view = rc.wait_for(device["id"], job_id, rc.WAIT_SECONDS if wait is not False else 0)
    view["booth"] = {"id": device["id"], "name": device["name"]}
    _log_result(f"Box #{device['id']} {action}: {view.get('state')}")
    return view


@tool("remote_control_start",
      "Fernsteuerung einer Box beginnen: Die Box fragt dann alle 1–2 Sekunden nach Aufträgen statt alle "
      "30 Sekunden. Wartet bis zu 15 s, bis sie zuhört (listening). Die Box macht nur mit, wenn an ihr "
      "„Fernsteuerung durch die Galerie erlauben“ eingeschaltet ist. Endet nach 5 Minuten ohne Steuerbefehl.",
      {"type": "object", "required": ["booth_id"], "properties": {"booth_id": {"type": "integer"}}},
      scope="control")
def _remote_start(booth_id: int) -> dict:
    device = _booth(booth_id)
    until = rc.open_session(device["id"], actor())
    holder = f"start:{device['id']}:{time.time()}"
    waited = rc._acquire_wait_slot(holder, rc.WAIT_SECONDS)
    try:
        deadline = time.time() + (rc.WAIT_SECONDS if waited else 0)
        state = rc.session(device["id"])
        while not state["listening"] and time.time() < deadline:
            time.sleep(0.2)
            state = rc.session(device["id"])
    finally:
        if waited:
            rc._release_wait_slot(holder)
    _log_result(f"Box #{device['id']} Fernsteuerung offen")
    return {"booth": {"id": device["id"], "name": device["name"]}, "session_until": until,
            "listening": state["listening"],
            "hint": "Box hört zu." if state["listening"] else
                    "Die Box hat sich noch nicht gemeldet (sie fragt sonst alle 30 s). Befehle werden "
                    "angenommen und ausgeführt, sobald sie sich meldet — aber nur innerhalb von 60 s."}


@tool("remote_control_stop", "Fernsteuerung einer Box beenden; sie fragt danach wieder im normalen Takt.",
      {"type": "object", "required": ["booth_id"], "properties": {"booth_id": {"type": "integer"}}},
      scope="control")
def _remote_stop(booth_id: int) -> dict:
    device = _booth(booth_id)
    return {"booth_id": device["id"], "stopped": rc.close_session(device["id"])}


_ARGS_DESC = ("Angaben zur Aktion. start_run/run: mode (photo, collage, gif, boomerang, video), format, template, "
              "look, consent (Option, wenn die Box nach Einwilligung fragt), copies (nur run: danach drucken). "
              "shot: uid, retake, replace (Nummer). clip/finish/abort/set_text: uid (+ look bzw. text). "
              "print/reprint: uid, copies. trigger: mode. paper_changed/ribbon_changed: slot. test_print: template. "
              "logs: limit. list_runs: limit.")


@tool("booth_control",
      "Eine Box live bedienen und auf ihre Antwort warten (bis 15 s, sonst job_id für get_job). Aktionen: "
      + "; ".join(f"{k} = {rc.ACTIONS[k]}" for k in _CONTROL_ACTIONS)
      + ". Es gelten dieselben Regeln wie am Kiosk: gesperrte Box startet nichts, ein Durchgang zur Zeit, "
        "Druckkontingente. Löschen und Beenden gehen über booth_delete_run und booth_quit.",
      {"type": "object", "required": ["booth_id", "action"],
       "properties": {"booth_id": {"type": "integer"},
                      "action": {"type": "string", "enum": _CONTROL_ACTIONS},
                      "args": {"type": "object", "description": _ARGS_DESC},
                      "wait": {"type": "boolean", "description": "Auf die Antwort warten (Standard ja)"}}},
      scope="control", untrusted=True)
def _booth_control(booth_id: int, action: str, args: Optional[dict] = None, wait: bool = True) -> dict:
    if action not in _CONTROL_ACTIONS:
        raise ValueError("Diese Aktion gibt es hier nicht. Möglich: " + ", ".join(_CONTROL_ACTIONS))
    return _control(booth_id, action, args, wait)


@tool("booth_live_image",
      "Ein aktuelles Livebild der Kamera einer Box (höchstens 640 px, als Bild). Zeigt, wer gerade vor der "
      "Box steht — nur ansehen, wenn es nötig ist. Die Box lehnt ab, solange ein Gast-Durchgang läuft, ein "
      "Druck unterwegs ist oder jemand am Bildschirm der Box tippt (E-Mail-Adresse, Gruß, PIN).",
      {"type": "object", "required": ["booth_id"], "properties": {"booth_id": {"type": "integer"}}},
      scope="control")
def _booth_live_image(booth_id: int):
    return _picture_out(_control(booth_id, "live_image", {}))


@tool("booth_screenshot",
      "Ein Bildschirmfoto des Kiosk-Fensters der Box (höchstens 640 px), wenn die Box das kann. Die Box lehnt "
      "ab, solange ein Gast-Durchgang läuft, ein Druck unterwegs ist oder jemand am Bildschirm der Box tippt "
      "(E-Mail-Adresse, Gruß, PIN).",
      {"type": "object", "required": ["booth_id"], "properties": {"booth_id": {"type": "integer"}}},
      scope="control")
def _booth_screenshot(booth_id: int):
    return _picture_out(_control(booth_id, "screenshot", {}))


@tool("get_job", "Stand und Ergebnis eines Auftrags an eine Box (job_id aus booth_control oder send_booth_job).",
      {"type": "object", "required": ["booth_id", "job_id"],
       "properties": {"booth_id": {"type": "integer"}, "job_id": {"type": "integer"}}},
      scope="control", untrusted=True)
def _get_job(booth_id: int, job_id: int):
    device = _booth(booth_id)
    row = ecosystem.job_result(device["id"], int(job_id))     # only jobs of this booth
    if not row:
        raise ValueError(f"Kein Auftrag {job_id} bei dieser Box")
    return _picture_out(rc.job_view(row))


def _quit_preview(booth_id: int) -> dict:
    device = _booth(booth_id)
    return {"booth": device["name"], "action": "Box-Programm beenden",
            "effect": "Der Kiosk geht aus. Wieder an nur vor Ort oder durch den Neustart-Dienst des PCs, "
                      "falls eingerichtet. Laufende Durchgänge gehen verloren.",
            "remote_session": rc.session(device["id"])}


@tool("booth_quit", "Das Box-Programm beenden. Nie während eines Gast-Durchgangs oder Drucks (die Box lehnt ab).",
      {"type": "object", "required": ["booth_id"], "properties": {"booth_id": {"type": "integer"}}},
      scope="control", destructive=True, confirm=_quit_preview)
def _booth_quit(booth_id: int) -> dict:
    return _control(booth_id, "quit", {})


def _power_preview(booth_id: int, restart: bool) -> dict:
    device = _booth(booth_id)
    status = db.get_device_status(device["id"]) or {}
    return {"booth": device["name"], "action": "PC neu starten" if restart else "PC herunterfahren",
            "effect": ("Das Box-Programm meldet sich bei der Galerie ab, dann startet Windows nach 15 s neu; "
                       "die Box kommt mit dem Autostart zurück, wenn er eingerichtet ist." if restart else
                       "Das Box-Programm meldet sich bei der Galerie ab, dann fährt Windows nach 15 s herunter. "
                       "Wieder an nur vor Ort."),
            "refused_when": "ein Durchgang läuft, ein Druck unterwegs ist, oder die Box ist nicht Windows",
            "waiting_prints": status.get("prints_pending"), "remote_session": rc.session(device["id"])}


def _restart_pc_preview(booth_id: int) -> dict:
    return _power_preview(booth_id, True)


def _shutdown_pc_preview(booth_id: int) -> dict:
    return _power_preview(booth_id, False)


@tool("booth_restart_pc", "Den PC der Box neu starten (Windows). Nie während eines Durchgangs oder Drucks.",
      {"type": "object", "required": ["booth_id"], "properties": {"booth_id": {"type": "integer"}}},
      scope="control", destructive=True, confirm=_restart_pc_preview)
def _booth_restart_pc(booth_id: int) -> dict:
    return _control(booth_id, "restart_pc", {})


@tool("booth_shutdown_pc", "Den PC der Box herunterfahren (Windows). Nie während eines Durchgangs oder Drucks.",
      {"type": "object", "required": ["booth_id"], "properties": {"booth_id": {"type": "integer"}}},
      scope="control", destructive=True, confirm=_shutdown_pc_preview)
def _booth_shutdown_pc(booth_id: int) -> dict:
    return _control(booth_id, "shutdown_pc", {})


def _language_preview(booth_id: int, lang: str = "") -> dict:
    device = _booth(booth_id)
    lang = str(lang or "").strip().lower()
    if len(lang) > 8 or (lang and not lang.isalpha()):
        raise ValueError("lang ist ein Sprachkürzel wie de oder en, oder leer")
    return {"booth": device["name"], "lang": lang or "(erste Sprache des Profils)",
            "effect": "Jeder nächste Gast beginnt in dieser Sprache, bis die Box neu startet. "
                      "Nur Sprachen, die im Profil eingeschaltet sind; nie während eines Durchgangs."}


@tool("booth_set_language", "Die Sprache setzen, in der der Kiosk jeden Durchgang beginnt (bis zum Neustart).",
      {"type": "object", "required": ["booth_id"],
       "properties": {"booth_id": {"type": "integer"},
                      "lang": {"type": "string", "description": "z. B. de oder en; leer = erste Sprache des Profils"}}},
      scope="control", destructive=True, confirm=_language_preview)
def _booth_set_language(booth_id: int, lang: str = "") -> dict:
    _language_preview(booth_id, lang)
    return _control(booth_id, "set_language", {"lang": str(lang or "").strip().lower()})


def _print_ids(ids: Any) -> Optional[List[int]]:
    if ids is None:
        return None
    if not isinstance(ids, list) or not ids or len(ids) > 50:
        raise ValueError("ids: eine Liste mit 1 bis 50 Nummern aus print_jobs")
    try:
        return [int(i) for i in ids]
    except (TypeError, ValueError):
        raise ValueError("ids: nur Nummern aus print_jobs") from None


def _discard_preview(booth_id: int, ids: Any = None) -> dict:
    device = _booth(booth_id)
    status = db.get_device_status(device["id"]) or {}
    chosen = _print_ids(ids)
    queue = list(status.get("print_jobs") or [])
    if chosen is not None:
        queue = [q for q in queue if isinstance(q, dict) and q.get("id") in chosen]
    effect = ("Diese Drucke werden verworfen" if chosen is not None
              else "Alle Drucke, die noch nicht heraus sind, werden verworfen")
    return {"booth": device["name"], "waiting_prints": status.get("prints_pending"), "held": status.get("prints_held"),
            "print_jobs": queue,
            "effect": effect + "; jeder Gast bekommt sein Kontingent zurück. Ein Auftrag, den ein Windows-Drucker "
                               "schon hat, wird aus der Windows-Warteschlange genommen."}


@tool("booth_discard_prints",
      "Drucke an der Box verwerfen: ohne ids alle, die noch nicht heraus sind; mit ids nur diese "
      "(die Nummern stehen in booth_diagnostics unter status.print_jobs, mit Durchgang, Zeit, Kopien, Zustand und Grund).",
      {"type": "object", "required": ["booth_id"],
       "properties": {"booth_id": {"type": "integer"},
                      "ids": {"type": "array", "items": {"type": "integer"}, "maxItems": 50}}},
      scope="control", destructive=True, confirm=_discard_preview)
def _booth_discard(booth_id: int, ids: Any = None) -> dict:
    chosen = _print_ids(ids)
    return _control(booth_id, "discard_prints", {} if chosen is None else {"ids": chosen})


def _delete_run_preview(booth_id: int, uid: str) -> dict:
    device = _booth(booth_id)
    uid = str(uid or "").strip()
    if not uid or len(uid) > 64 or not uid.replace(".", "").replace("-", "").isalnum():
        raise ValueError("uid fehlt oder ist keine Kennung eines Durchgangs")
    rows = _media_of_run(uid)
    return {"booth": device["name"], "run": uid, "gallery_media": len(rows),
            "effect": "Der Durchgang wird an der Box gelöscht (Dateien, Galerie der Box). Die Box meldet "
                      "die Löschung an die Galerie, die ihn dort ebenfalls löscht; im Löschprotokoll steht es."}


@tool("booth_delete_run", "Einen Durchgang an der Box löschen (wie „Löschen“ am Kiosk).",
      {"type": "object", "required": ["booth_id", "uid"],
       "properties": {"booth_id": {"type": "integer"}, "uid": {"type": "string"}}},
      scope="control", also=("photos",), destructive=True, confirm=_delete_run_preview)
def _booth_delete_run(booth_id: int, uid: str) -> dict:
    return _control(booth_id, "delete_run", {"uid": str(uid)})


# ══ photos ═══════════════════════════════════════════════════════════════
#
# Pictures leave only through this right. A page lists at most 50, never a
# dump; a picture comes as a preview of at most 1024 px, the original only
# through a signed link that works for ten minutes. The admin sees what the
# admin sees in the gallery: hidden and waiting pictures included.

LINK_SECONDS = 600
PAGE_MAX = 50
PREVIEW_MAX = 1024


def _sign(media_id: int, variant: str, exp: int) -> str:
    key = hashlib.sha256(("mcp-media:" + str(current_app.secret_key or "")).encode()).digest()
    return hmac.new(key, f"{media_id}:{variant}:{exp}".encode(), hashlib.sha256).hexdigest()[:40]


def signed_link(media_id: int, variant: str, seconds: int = LINK_SECONDS) -> str:
    exp = int(time.time()) + int(seconds)
    base = db.base_url() or oauth.issuer()
    return f"{base}/mcp/media/{int(media_id)}/{variant}?exp={exp}&sig={_sign(int(media_id), variant, exp)}"


def _media_file(row: dict, variant: str):
    from app import media
    if variant == "original":
        rels = [row.get("path_original")]
    else:
        rels = [row.get("path_thumb"), row.get("path_poster"), row.get("path_webp")]
    base = media.data_dir().resolve()
    for rel in rels:
        if not rel:
            continue
        full = (base / rel).resolve()
        if base in full.parents and full.is_file():          # never outside the data folder
            return full
    return None


@bp.get("/mcp/media/<int:media_id>/<variant>")
def signed_media(media_id: int, variant: str):
    """A picture for an assistant: only with a valid, unexpired signature from ``signed_link``."""
    if variant not in ("thumb", "original"):
        abort(404)
    try:
        exp = int(request.args.get("exp") or 0)
    except ValueError:
        abort(404)
    sig = str(request.args.get("sig") or "")
    if exp < time.time() or not hmac.compare_digest(sig, _sign(media_id, variant, exp)):
        abort(404)                                           # same answer as a missing picture
    row = db.get_media(media_id)
    path = _media_file(row, variant) if row else None
    if not path:
        abort(404)
    resp = send_file(path, conditional=False, download_name=f"{media_id}{path.suffix}")
    resp.headers["Cache-Control"] = "no-store"
    resp.headers["Referrer-Policy"] = "no-referrer"
    resp.headers["X-Robots-Tag"] = "noindex"
    return resp


def _media_view(row: dict) -> dict:
    from app import media_source
    return {"id": row["id"], "kind": row.get("kind"), "variant": row.get("variant") or "",
            **media_source.describe(row),
            "file_name": row.get("filename"), "run": row.get("session_uid") or "",
            "created_at": row.get("taken_at") or row.get("created_at"),
            "hidden": bool(row.get("hidden")), "review": row.get("review_state") or "",
            "thumb_url": signed_link(row["id"], "thumb"), "original_url": signed_link(row["id"], "original"),
            "links_expire_in_seconds": LINK_SECONDS}


def _media_of_run(uid: str) -> List[dict]:
    ecosystem.init_schema()
    con = db.get_db()
    try:
        rows = [dict(r) for r in con.execute("SELECT * FROM media WHERE session_uid=? OR uid=? ORDER BY id",
                                             (uid, uid))]
    finally:
        con.close()
    return rows


@tool("list_media",
      "Bilder eines Events seitenweise (höchstens 50 je Seite): Kennung, Art, Dateiname, Durchgang, sichtbar, "
      "Freigabe-Stand und zwei kurz gültige Links (Vorschau, Original; 10 Minuten). Optional nur ein Durchgang.",
      {"type": "object", "required": ["event_id"],
       "properties": {"event_id": {"type": "integer"},
                      "page": {"type": "integer", "description": "ab 1"},
                      "per_page": {"type": "integer", "description": "1–50, Standard 24"},
                      "run": {"type": "string", "description": "Nur dieser Durchgang (session uid)"},
                      "include_originals": {"type": "boolean",
                                            "description": "Auch die verborgenen Original-Varianten der Box"}}},
      scope="photos", untrusted=True)
def _list_media(event_id: int, page: int = 1, per_page: int = 24, run: str = "",
                include_originals: bool = False) -> dict:
    event = _event(event_id)
    ecosystem.init_schema()
    per = max(1, min(int(per_page or 24), PAGE_MAX))
    page = max(1, int(page or 1))
    where, args = "event_id=?", [event["id"]]
    if not include_originals:
        where += " AND COALESCE(variant,'') != 'original'"
    if run:
        where += " AND session_uid=?"
        args.append(str(run))
    con = db.get_db()
    try:
        total = con.execute(f"SELECT COUNT(*) FROM media WHERE {where}", args).fetchone()[0]
        rows = [dict(r) for r in con.execute(
            f"SELECT * FROM media WHERE {where} ORDER BY id DESC LIMIT ? OFFSET ?", args + [per, (page - 1) * per])]
    finally:
        con.close()
    _log_result(f"Event #{event['id']}: {len(rows)} Bilder gezeigt")
    return {"event": {"id": event["id"], "name": event["name"]}, "page": page, "per_page": per,
            "total": int(total), "pages": (int(total) + per - 1) // per,
            "items": [_media_view(r) for r in rows]}


def _preview_jpeg(row: dict, max_px: int) -> Optional[bytes]:
    from PIL import Image, ImageOps
    from app import media
    base = media.data_dir().resolve()
    for rel in (row.get("path_webp"), row.get("path_poster"), row.get("path_original"), row.get("path_thumb")):
        if not rel:
            continue
        full = (base / rel).resolve()
        if base not in full.parents or not full.is_file():
            continue
        try:
            with Image.open(full) as im:
                im.draft("RGB", (max_px, max_px))
                im = ImageOps.exif_transpose(im).convert("RGB")
                im.thumbnail((max_px, max_px))
                buf = io.BytesIO()
                im.save(buf, "JPEG", quality=82)
                return buf.getvalue()
        except Exception:                                    # noqa: BLE001 — a video file is not a picture
            continue
    return None


@tool("get_photo",
      "Ein Bild als Vorschau (höchstens 1024 px) zum Ansehen, dazu ein 10 Minuten gültiger Link aufs Original.",
      {"type": "object", "required": ["media_id"],
       "properties": {"media_id": {"type": "integer"},
                      "max_px": {"type": "integer", "description": "Kantenlänge 64–1024, Standard 1024"}}},
      scope="photos", untrusted=True)
def _get_photo(media_id: int, max_px: int = PREVIEW_MAX):
    row = db.get_media(int(media_id))
    if not row:
        raise ValueError(f"Kein Bild mit der Nummer {media_id}")
    size = max(64, min(int(max_px or PREVIEW_MAX), PREVIEW_MAX))
    jpeg = _preview_jpeg(row, size)
    _log_result(f"Bild #{row['id']} angesehen")
    return ToolResult({**_media_view(row), "event_id": row["event_id"],
                       "preview": "als Bild angehängt" if jpeg else "keine Vorschau möglich (Video ohne Standbild?)"},
                      [(jpeg, "image/jpeg")] if jpeg else [])


@tool("set_media_visibility",
      "Bilder für Gäste aus- oder einblenden. Ausgeblendete Bilder liegen danach in der Freigabe-Warteschlange "
      "des Events, wo der Betreiber sie wieder freigeben oder löschen kann.",
      {"type": "object", "required": ["media_ids", "visible"],
       "properties": {"media_ids": {"type": "array", "items": {"type": "integer"}, "description": "höchstens 100"},
                      "visible": {"type": "boolean"}}},
      scope="photos")
def _set_visibility(media_ids: list, visible: bool) -> dict:
    ids = [int(i) for i in (media_ids or [])][:100]
    if not ids:
        raise ValueError("media_ids ist leer")
    ecosystem.init_schema()
    marks = ",".join("?" * len(ids))
    con = db.get_db()
    try:
        # The hidden full-size originals of booth runs are never shown this way.
        if visible:
            cur = con.execute(f"UPDATE media SET hidden=0, review_state='approved' WHERE id IN ({marks}) "
                              "AND COALESCE(variant,'') != 'original'", ids)
        else:
            cur = con.execute(f"UPDATE media SET hidden=1, review_state='pending' WHERE id IN ({marks}) "
                              "AND COALESCE(variant,'') != 'original'", ids)
        con.commit()
        n = int(cur.rowcount or 0)
    finally:
        con.close()
    _log_result(f"{n} Bilder {'eingeblendet' if visible else 'ausgeblendet'}")
    return {"changed": n, "visible": bool(visible)}


def _delete_media_preview(media_ids: list) -> dict:
    ids = [int(i) for i in (media_ids or [])][:100]
    rows = [r for r in (db.get_media(i) for i in ids) if r]
    if not rows:
        raise ValueError("Keins der Bilder gibt es (mehr)")
    events = sorted({r["event_id"] for r in rows})
    from app import trash
    if trash.enabled():
        return {"pictures": len(rows), "events": events, "missing": sorted(set(ids) - {r["id"] for r in rows}),
                "effect": f"Die Bilder gehen für {trash.days()} Tage in den Papierkorb (in der Galerie sofort "
                          "weg, im Admin wiederherstellbar); eine Box kann sie nicht wieder hochladen; "
                          "endgültig gelöscht und protokolliert wird nach Ablauf."}
    return {"pictures": len(rows), "events": events, "missing": sorted(set(ids) - {r["id"] for r in rows}),
            "effect": "Die Bilder werden mit allen Größen und der Original-Variante gelöscht; eine Box kann "
                      "sie nicht wieder hochladen; ein Eintrag im Löschprotokoll."}


@tool("delete_media", "Einzelne Bilder endgültig löschen (höchstens 100 je Aufruf).",
      {"type": "object", "required": ["media_ids"],
       "properties": {"media_ids": {"type": "array", "items": {"type": "integer"}}}},
      scope="photos", destructive=True, confirm=_delete_media_preview)
def _delete_media(media_ids: list) -> dict:
    from app.blueprints.admin import _purge_media
    ids = [int(i) for i in (media_ids or [])][:100]
    done = [i for i in ids if _purge_media(i, reason="admin", actor="assistant", note=actor())]
    _log_result(f"{len(done)} Bilder gelöscht")
    return {"deleted": done}


def _delete_run_gallery_preview(run: str) -> dict:
    rows = _media_of_run(str(run or ""))
    if not rows:
        raise ValueError("Zu diesem Durchgang gibt es in der Galerie keine Bilder")
    return {"run": run, "pictures": len(rows), "events": sorted({r["event_id"] for r in rows}),
            "effect": "Alle Bilder des Durchgangs werden in der Galerie gelöscht; die Box erfährt es beim "
                      "nächsten Abgleich und löscht ihre Kopie."}


@tool("delete_gallery_run", "Einen ganzen Durchgang (alle seine Bilder) in der Galerie löschen.",
      {"type": "object", "required": ["run"], "properties": {"run": {"type": "string", "description": "session uid"}}},
      scope="photos", destructive=True, confirm=_delete_run_gallery_preview)
def _delete_gallery_run(run: str) -> dict:
    from app.blueprints.admin import _purge_media
    done = [r["id"] for r in _media_of_run(str(run or "")) if _purge_media(r["id"], reason="admin", actor="assistant", note=actor())]
    _log_result(f"Durchgang gelöscht ({len(done)} Bilder)")
    return {"run": run, "deleted": done}


def _purge_preview(event_id: int) -> dict:
    from app.blueprints.admin import guest_photo_count
    event = _event(event_id)
    return {"event": event["name"], "slug": event["slug"], "guest_visible_pictures": guest_photo_count(event["id"]),
            "effect": "Alle Bilder des Events werden gelöscht (auch verborgene und wartende). Das Event bleibt mit "
                      "Adresse, QR-Codes und Weiterleitung. Eintrag im Löschprotokoll."}


@tool("purge_event_photos", "Alle Fotos eines Events löschen, das Event behalten.",
      {"type": "object", "required": ["event_id"], "properties": {"event_id": {"type": "integer"}}},
      scope="photos", destructive=True, confirm=_purge_preview)
def _purge_event_photos(event_id: int) -> dict:
    from app import retention
    event = _event(event_id)
    result = retention.purge_event(event["id"], reason="admin", actor="assistant", note=actor())
    _log_result(f"Event #{event['id']}: Fotos gelöscht")
    return {"event_id": event["id"], "files": result.get("files", 0), "kept_event": True}


def _delete_event_preview(event_id: int) -> dict:
    from app import redirects
    from app.blueprints.admin import guest_photo_count
    event = _event(event_id)
    forward = bool(redirects.clean_url(event.get("redirect_url")))
    return {"event": event["name"], "slug": event["slug"], "pictures": guest_photo_count(event["id"]),
            "effect": ("Alle Bilder werden gelöscht. Das Event hat eine Weiterleitung und bleibt deshalb als "
                       "Hülle stehen (löschen kann es dann nur der Betreiber im Admin)." if forward else
                       "Alle Bilder und das Event selbst werden gelöscht; gedruckte QR-Codes führen ins Leere.")}


@tool("delete_event", "Ein Event samt allen Bildern löschen.",
      {"type": "object", "required": ["event_id"], "properties": {"event_id": {"type": "integer"}}},
      scope="photos", destructive=True, confirm=_delete_event_preview)
def _delete_event(event_id: int) -> dict:
    from app import retention
    event = _event(event_id)
    result = retention.purge_event(event["id"], reason="event", actor="assistant", note=actor(),
                                    delete_event_row=True)
    _log_result(f"Event #{event['id']} gelöscht")
    return {"event_id": event["id"], "files": result.get("files", 0),
            "event_row_deleted": db.get_event_by_id(event["id"]) is None}


@tool("list_pending_uploads",
      "Bilder eines Events, die auf Freigabe warten (Moderation, Gast-Uploads), mit kurz gültigen Links.",
      {"type": "object", "required": ["event_id"],
       "properties": {"event_id": {"type": "integer"}, "limit": {"type": "integer", "description": "höchstens 50"}}},
      scope="photos", untrusted=True)
def _list_pending(event_id: int, limit: int = 50) -> dict:
    event = _event(event_id)
    ecosystem.init_schema()
    rows = ecosystem.pending_review(event["id"], max(1, min(int(limit or 50), PAGE_MAX)))
    return {"event_id": event["id"], "waiting": ecosystem.pending_review_count(event["id"]),
            "items": [_media_view(r) for r in rows]}


@tool("approve_uploads", "Wartende Bilder eines Events freigeben (ohne media_ids: alle wartenden).",
      {"type": "object", "required": ["event_id"],
       "properties": {"event_id": {"type": "integer"}, "media_ids": {"type": "array", "items": {"type": "integer"}}}},
      scope="photos")
def _approve_uploads(event_id: int, media_ids: Optional[list] = None) -> dict:
    event = _event(event_id)
    n = ecosystem.approve_media(event["id"], [int(i) for i in media_ids] if media_ids else None)
    _log_result(f"{n} Bilder freigegeben")
    return {"approved": n}


def _subscriptions(event_id: int) -> List[dict]:
    con = db.get_db()
    try:
        return [dict(r) for r in con.execute(
            "SELECT email, confirmed_at FROM notify_subscriptions WHERE event_id=? AND unsubscribed_at IS NULL "
            "ORDER BY id", (int(event_id),))]
    finally:
        con.close()


@tool("guest_contact_counts",
      "Wie viele Gäste sich für Benachrichtigungen zu einem Event eingetragen haben (bestätigt/offen). "
      "Nur Zahlen; Adressen gibt nur list_guest_addresses.",
      {"type": "object", "required": ["event_id"], "properties": {"event_id": {"type": "integer"}}},
      scope="photos")
def _guest_counts(event_id: int) -> dict:
    event = _event(event_id)
    rows = _subscriptions(event["id"])
    return {"event_id": event["id"], "confirmed": sum(1 for r in rows if r["confirmed_at"]),
            "unconfirmed": sum(1 for r in rows if not r["confirmed_at"])}


@tool("list_guest_addresses",
      "Die E-Mail-Adressen der bestätigten Benachrichtigungs-Abos eines Events. Personenbezogen: nur abrufen, "
      "wenn der Mensch es ausdrücklich will; der Abruf steht im Änderungsprotokoll.",
      {"type": "object", "required": ["event_id"], "properties": {"event_id": {"type": "integer"}}},
      scope="photos", untrusted=True)
def _guest_addresses(event_id: int) -> dict:
    event = _event(event_id)
    addresses = [r["email"] for r in _subscriptions(event["id"]) if r["confirmed_at"]]
    _log_result(f"{len(addresses)} Gast-Adressen abgerufen")
    return {"event_id": event["id"], "addresses": addresses}


# ══ secure ═══════════════════════════════════════════════════════════════
#
# What PROFILE_LOCKED kept from ``update_booth_profile`` (S-13 and before):
# it leads to guests or holds a secret. With ``secure`` it may change; a
# secret is written and never read back.

#: The booth's PIN rule (``boothcore.config.MIN_PIN_LENGTH`` = 4) and the
#: admin form's "4 bis 8 Ziffern" (final review A13; the owner's decision of
#: 16.11.2026 brought the minimum back to four, guarded by the PIN brake).
PIN_PATTERN = r"\d{4,8}"

_SECRET_FIELDS = (("mail", "password"), ("webhooks", "secret"), ("http_trigger", "token"), ("access", "pin"),
                  ("wifi_share", "password"))


def _check_url(value: Any, field: str, placeholder: bool = False) -> None:
    from app import redirects
    raw = str(value or "").strip()
    if raw and not redirects.clean_url(raw.replace("{uid}", "uid") if placeholder else raw):
        raise ValueError(f"{field}: nur eine vollständige http(s)-Adresse ohne Leerzeichen")


@tool("update_booth_profile_secure",
      "Die gesperrten Teile eines Box-Profils ändern: access (PIN, Sperre), mail (Versand, Absender, BCC, "
      "SMTP-Zugang), webhooks, http_trigger, qr und qr_url_template (wohin jeder gedruckte QR-Code führt), "
      "wifi_share (WLAN-Name und -Passwort, die Gäste als QR-Code sehen), meldungen.mail (wohin Warnungen "
      "an den Betreiber gehen). "
      "Passwörter, Secrets, Token und die PIN dürfen gesetzt werden; zurückgegeben werden sie nie (••••). "
      "Ein mitgeschicktes „••••“ lässt den gespeicherten Wert stehen. Andere Teile: update_booth_profile. "
      "Antwort wie dort: changed, values (Geheimnisse als ••••), ignored_keys (unbekannte Schlüssel, "
      "gemeldet statt still verworfen), profile_version; return=full gibt das ganze Profil.",
      {"type": "object", "required": ["booth_id", "changes"],
       "properties": {"booth_id": {"type": "integer"},
                      "changes": {"type": "object", "description": "Teilmenge von access, mail, webhooks, "
                                                                   "http_trigger, qr, qr_url_template, wifi_share, meldungen.mail"},
                      "return": {"type": "string", "enum": ["changed", "full"],
                                 "description": "changed (Standard) = nur die angefassten Werte; full = ganzes Profil"}}},
      scope="secure")
def _update_profile_secure(booth_id: int, changes: dict, **opts) -> dict:
    import copy
    import re

    from app.blueprints.admin_booth import _weak_pin_reason
    from app.mcp import (PROFILE_LOCKED, _leaves, _profile_for_chat, nothing_applied, profile_answer,
                         profile_ignored, profile_return)
    how = profile_return(opts)
    device = _booth(booth_id)
    if not isinstance(changes, dict) or not changes:
        raise ValueError("changes muss ein Objekt mit mindestens einem Eintrag sein")
    other = sorted(set(changes) - set(PROFILE_LOCKED) - {"meldungen"})
    if isinstance(changes.get("meldungen"), dict) and set(changes["meldungen"]) - {"mail"}:
        other.append("meldungen (außer mail)")
    if other:
        raise ValueError("Hier nur die gesperrten Teile; für " + ", ".join(other) + " update_booth_profile nehmen")
    changes = copy.deepcopy(changes)
    for section, key in _SECRET_FIELDS:
        part = changes.get(section)
        if isinstance(part, dict) and part.get(key) == MASK:
            part.pop(key)                                    # "keep what is stored"
    pin = ((changes.get("access") or {}) if isinstance(changes.get("access"), dict) else {}).get("pin")
    if pin is not None:
        pin = str(pin)
        if pin and (not re.fullmatch(PIN_PATTERN, pin) or _weak_pin_reason(pin)):
            raise ValueError("PIN nicht übernommen: " + (_weak_pin_reason(pin) if pin.isdigit() and
                                                        _weak_pin_reason(pin) else "nur 4 bis 8 Ziffern"))
        changes["access"]["pin"] = pin
    if isinstance(changes.get("webhooks"), dict):
        _check_url(changes["webhooks"].get("url"), "webhooks.url")
    warn_to = ((changes.get("meldungen") or {}).get("mail") or {}) if isinstance(changes.get("meldungen"), dict) else {}
    to = warn_to.get("to") if isinstance(warn_to, dict) else None
    if to and not re.fullmatch(r"[^@\s<>\"',;]+@[^@\s<>\"',;]+\.[^@\s<>\"',;]+", str(to)):
        raise ValueError("meldungen.mail.to: genau eine E-Mail-Adresse")
    if isinstance(changes.get("qr"), dict):
        _check_url(changes["qr"].get("url"), "qr.url", placeholder=True)
    if "qr_url_template" in changes:
        _check_url(changes.get("qr_url_template"), "qr_url_template", placeholder=True)
    current = ecosystem.device_profile(device)
    merged = ecosystem.deep_merge(current, changes)
    ignored = profile_ignored(current, merged, changes)
    if changes and len(ignored) == len(_leaves(changes)):
        raise nothing_applied(ignored)
    ecosystem.save_device_profile(device["id"], merged, device.get("event_id"))
    after = ecosystem.device_profile(db.get_device_by_id(device["id"]))
    _log_result(f"Box #{device['id']}: gesperrte Profilteile geändert")
    out = _profile_for_chat(after, secure=True)
    if isinstance(out.get("access"), dict):
        out["access"]["pin"] = MASK if (after.get("access") or {}).get("pin") else ""
    return profile_answer(device["id"], changes, out, ignored, how)


@tool("set_event_qr_target",
      "Wohin die QR-Codes und der Galerie-Link eines Events führen: qr_target gallery (Standard), external "
      "(external_url, {uid} wird ersetzt) oder status (status_message); redirect_url leitet die ganze Galerie "
      "weiter (leer = aus). Gilt sofort für alle gedruckten Codes. Nur übergebene Felder ändern sich.",
      {"type": "object", "required": ["event_id"],
       "properties": {"event_id": {"type": "integer"},
                      "qr_target": {"type": "string", "enum": ["gallery", "external", "status"]},
                      "external_url": {"type": "string"}, "status_message": {"type": "string"},
                      "redirect_url": {"type": "string"}}},
      scope="secure")
def _set_event_qr(event_id: int, qr_target: Optional[str] = None, external_url: Optional[str] = None,
                  status_message: Optional[str] = None, redirect_url: Optional[str] = None) -> dict:
    from app import redirects
    event = _event(event_id)
    if redirect_url is not None:
        _check_url(redirect_url, "redirect_url")
        db.update_event(event["id"], redirect_url=redirects.clean_url(redirect_url) or None)
    if qr_target is not None or external_url is not None or status_message is not None:
        if external_url:
            _check_url(external_url, "external_url", placeholder=True)
        target = qr_target or event.get("qr_target") or "gallery"
        url = external_url if external_url is not None else (event.get("external_url") or "")
        if target == "external" and not url:
            raise ValueError("qr_target external braucht external_url")
        ecosystem.set_event_qr(event["id"], target, url,
                               status_message if status_message is not None else (event.get("status_message") or ""))
    after = db.get_event_by_id(event["id"])
    _log_result(f"Event #{event['id']}: QR-Ziel/Weiterleitung")
    return {"event_id": event["id"], "qr_target": after.get("qr_target") or "gallery",
            "external_url": after.get("external_url") or "", "redirect_url": after.get("redirect_url") or "",
            "status_message": after.get("status_message") or ""}


def _rotate_preview(booth_id: int) -> dict:
    device = _booth(booth_id)
    return {"booth": device["name"], "effect": "Der Geräteschlüssel wird sofort ersetzt; die Box ist danach "
            "abgemeldet, bis sie neu gekoppelt wird (Kopplungscode im Admin oder Kopplung per Handy). Der neue "
            "Schlüssel wird nicht angezeigt — weder hier noch sonst wo; er wird verworfen."}


@tool("rotate_booth_key",
      "Den Schlüssel einer Box erneuern und sie damit abmelden (gestohlene oder verlorene Box aussperren).",
      {"type": "object", "required": ["booth_id"], "properties": {"booth_id": {"type": "integer"}}},
      scope="secure", destructive=True, confirm=_rotate_preview)
def _rotate_key(booth_id: int) -> dict:
    device = _booth(booth_id)
    db.regenerate_device_key(device["id"])                   # the fresh key is dropped on the floor
    _log_result(f"Box #{device['id']}: Schlüssel erneuert")
    return {"booth_id": device["id"], "signed_off": True,
            "next": "Neu koppeln: im Admin unter Boxen einen Kopplungscode erzeugen oder an der Box „Mit dem Handy koppeln“."}


def _delete_booth_preview(booth_id: int) -> dict:
    device = _booth(booth_id)
    return {"booth": device["name"], "effect": "Die Box wird aus der Galerie entfernt (Profil, Aufträge, "
            "Meldungen, Schlüssel). Ihre Bilder bleiben in den Events."}


@tool("delete_booth", "Eine Box aus der Galerie entfernen.",
      {"type": "object", "required": ["booth_id"], "properties": {"booth_id": {"type": "integer"}}},
      scope="secure", destructive=True, confirm=_delete_booth_preview)
def _delete_booth(booth_id: int) -> dict:
    device = _booth(booth_id)
    db.delete_device(device["id"])
    _log_result(f"Box #{device['id']} entfernt")
    return {"deleted": device["id"]}


@tool("list_pairing_requests",
      "Offene Kopplungsanfragen („Mit dem Handy koppeln“ an einer Box): Code, Name der Box, Adresse, Programm, Alter.",
      scope="secure", untrusted=True)
def _list_pairing_requests() -> list:
    con = db.get_db()
    try:
        rows = [dict(r) for r in con.execute(
            "SELECT * FROM pairing_requests WHERE state='open' AND expires_at > datetime('now') ORDER BY created_at DESC")]
    finally:
        con.close()
    return [{"code": r["code"], "booth_name": r.get("booth_name"), "version": r.get("version"),
             "ip": r.get("ip"), "user_agent": r.get("user_agent"), "created_at": r.get("created_at")} for r in rows]


def _pairing_preview(code: str, booth_id: int) -> dict:
    from app import phone_pairing
    device = _booth(booth_id)
    row = phone_pairing.get_request(code)
    if not row or phone_pairing.state_of(row) != "open":
        raise ValueError("Keine offene Kopplungsanfrage mit diesem Code")
    return {"request": {"code": row["code"], "booth_name": row.get("booth_name"), "ip": row.get("ip"),
                        "user_agent": row.get("user_agent"), "created_at": row.get("created_at")},
            "for_booth": device["name"],
            "effect": "Die anfragende Box bekommt einen neuen Schlüssel für diese Box; wer bisher mit ihrem "
                      "Schlüssel lief, ist abgemeldet. Nur bestätigen, wenn der Mensch gerade selbst an der "
                      "Box den Code geöffnet hat und Adresse/Programm passen."}


@tool("confirm_pairing_request", "Eine Kopplungsanfrage für eine Box bestätigen.",
      {"type": "object", "required": ["code", "booth_id"],
       "properties": {"code": {"type": "string"}, "booth_id": {"type": "integer"}}},
      scope="secure", destructive=True, confirm=_pairing_preview)
def _confirm_pairing(code: str, booth_id: int) -> dict:
    from app import phone_pairing
    device = _booth(booth_id)
    ok = phone_pairing.decide(code, device_id=device["id"], by=actor())
    _log_result(f"Kopplung bestätigt: {ok}")
    return {"confirmed": ok, "hint": "Die Box holt ihren Schlüssel innerhalb von 2 Minuten selbst ab."}


@tool("decline_pairing_request", "Eine Kopplungsanfrage ablehnen.",
      {"type": "object", "required": ["code"], "properties": {"code": {"type": "string"}}}, scope="secure")
def _decline_pairing(code: str) -> dict:
    from app import phone_pairing
    return {"declined": phone_pairing.decide(code, device_id=None, by=actor())}


NOTIFY_KEYS = {"notify_tg_token": True, "notify_tg_chat_id": False, "notify_discord_webhook": True,
               "notify_webhook_url": True, "notify_quiet_from": False, "notify_quiet_to": False,
               "notify_on_upload": False, "notify_on_publish": False, "notify_on_new_device": False}


@tool("get_notification_settings",
      "Wohin die Galerie Alarme schickt (Box rot/still, Uploads, Veröffentlichung): Telegram, Discord, Webhook, "
      "Ruhezeiten. Token und Adressen mit Zugang erscheinen nur als ••••.", scope="secure")
def _get_notify() -> dict:
    out = {}
    for key, secret in NOTIFY_KEYS.items():
        value = db.get_setting(key) or ""
        out[key] = (MASK if value else "") if secret else value
    return out


@tool("set_notification_settings",
      "Alarm-Empfänger und Ruhezeiten setzen. Schlüssel: " + ", ".join(NOTIFY_KEYS)
      + ". Token/Webhook-Adressen werden geschrieben, nie zurückgegeben; „••••“ lässt den Wert stehen.",
      {"type": "object", "required": ["changes"], "properties": {"changes": {"type": "object"}}},
      scope="secure")
def _set_notify(changes: dict) -> dict:
    if not isinstance(changes, dict) or not changes:
        raise ValueError("changes muss ein Objekt mit mindestens einem Eintrag sein")
    unknown = sorted(set(changes) - set(NOTIFY_KEYS))
    if unknown:
        raise ValueError("Unbekannt: " + ", ".join(unknown))
    for key, value in changes.items():
        if value == MASK:
            continue
        if key.startswith("notify_on_"):
            value = "1" if value in (True, 1, "1", "true", "on") else "0"
        value = str(value or "").strip()[:500]
        if key in ("notify_discord_webhook", "notify_webhook_url"):
            _check_url(value, key)
        db.set_setting(key, value)
    return {"changed": sorted(changes), "settings": _get_notify()}


@tool("set_silence_alarm",
      "Warnung, wenn eine Box nicht mehr antwortet: an/aus, Minuten, Empfänger (eine Adresse; leer = Konto-Adresse), "
      "nur während eines Events, Ruhezeit HH:MM–HH:MM. Ohne Angaben: zeigt nur die aktuelle Einstellung.",
      {"type": "object", "required": ["booth_id"],
       "properties": {"booth_id": {"type": "integer"}, "enabled": {"type": "boolean"},
                      "minutes": {"type": "integer"}, "recipient": {"type": "string"},
                      "only_event": {"type": "boolean"}, "quiet_from": {"type": "string"},
                      "quiet_to": {"type": "string"}}},
      scope="secure")
def _set_silence_alarm(booth_id: int, **changes) -> dict:
    from app import silence_alarm
    device = _booth(booth_id)
    current = silence_alarm.settings(device["id"])
    unknown = sorted(set(changes) - {"enabled", "minutes", "recipient", "only_event", "quiet_from", "quiet_to"})
    if unknown:
        raise ValueError("Unbekannt: " + ", ".join(unknown))
    if changes:
        merged = {k: changes.get(k, current.get(k)) for k in
                  ("enabled", "minutes", "recipient", "only_event", "quiet_from", "quiet_to")}
        problems = silence_alarm.save_settings(device["id"], enabled=bool(merged["enabled"]),
                                               minutes=merged["minutes"], recipient=merged["recipient"] or "",
                                               only_event=bool(merged["only_event"]),
                                               quiet_from=merged["quiet_from"] or "", quiet_to=merged["quiet_to"] or "")
        if problems:
            raise ValueError("Nicht gespeichert: " + "; ".join(problems))
        current = silence_alarm.settings(device["id"])
    return {k: current.get(k) for k in ("device_id", "enabled", "minutes", "recipient", "only_event",
                                        "quiet_from", "quiet_to", "state")}


# ══ updates ══════════════════════════════════════════════════════════════
#
# The booth's updater rules stay (S-4): only an offer with a SHA-256, never
# a downgrade, the key only to the paired gallery. Signed builds are still
# open: whoever holds this right can put code on a booth that the gallery
# stores — that is the residual risk, stated in docs/MCP.md and SECURITY.md.

def _offer_for(device: dict):
    from app import software
    status = db.get_device_status(device["id"]) or {}
    running = (str(status.get("booth_version") or "").split() or ["0"])[-1]
    return software.angebot("booth", running, db.base_url() or oauth.issuer()), running


@tool("fetch_booth_update",
      "Eine freigegebene neuere Fassung auf die Box laden (noch nicht einspielen). Prüfsumme wird an der Box geprüft.",
      {"type": "object", "required": ["booth_id"], "properties": {"booth_id": {"type": "integer"}}},
      scope="updates")
def _fetch_update(booth_id: int) -> dict:
    device = _booth(booth_id)
    offer, running = _offer_for(device)
    if not offer:
        raise ValueError(f"Für diese Box liegt keine neuere freigegebene Fassung vor (läuft: {running})")
    job_id = ecosystem.add_job(device["id"], "fetch_update")
    return {"job_id": job_id, "running": running, "offered": offer["version"], "sha256": offer["sha256"]}


def _install_preview(booth_id: int) -> dict:
    device = _booth(booth_id)
    offer, running = _offer_for(device)
    if not offer:
        raise ValueError(f"Für diese Box liegt keine neuere freigegebene Fassung vor (läuft: {running})")
    return {"booth": device["name"], "running": running, "installs": offer["version"], "sha256": offer["sha256"],
            "effect": "Die Box spielt die geladene Fassung ein und startet neu (ca. 1 Minute ohne Kiosk). "
                      "Ältere oder gleiche Fassungen lehnt sie ab; ohne vorher geladene Datei passiert nichts."}


@tool("install_booth_update", "Die geladene neuere Fassung an der Box einspielen (Box startet neu).",
      {"type": "object", "required": ["booth_id"], "properties": {"booth_id": {"type": "integer"}}},
      scope="updates", destructive=True, confirm=_install_preview)
def _install_update(booth_id: int) -> dict:
    device = _booth(booth_id)
    job_id = ecosystem.add_job(device["id"], "install_update")
    _log_result(f"Box #{device['id']}: Update einspielen")
    return {"job_id": job_id, "hint": "Ergebnis mit get_job oder booth_diagnostics nachsehen."}


def _release_preview(software_id: int, released: bool = True) -> dict:
    from app import software
    row = next((s for s in software.liste() if int(s["id"]) == int(software_id)), None)
    if not row:
        raise ValueError(f"Keine Fassung mit der Nummer {software_id}")
    return {"version": row["version"], "program": row["program"], "released": bool(released),
            "effect": ("Jede Box mit älterer Fassung bekommt sie beim nächsten Abgleich angeboten (nicht "
                       "eingespielt)." if released else "Die Fassung wird keiner Box mehr angeboten.")}


@tool("release_booth_software", "Eine in der Galerie abgelegte Box-Fassung freigeben oder zurückziehen.",
      {"type": "object", "required": ["software_id"],
       "properties": {"software_id": {"type": "integer"}, "released": {"type": "boolean"}}},
      scope="updates", destructive=True, confirm=_release_preview)
def _release_software(software_id: int, released: bool = True) -> dict:
    from app import software
    _release_preview(software_id, released)
    software.freigeben(int(software_id), bool(released))
    return {"software_id": int(software_id), "released": bool(released)}


# ══ manage + secure ══════════════════════════════════════════════════════
#
# The event of a booth decides where every picture of the evening goes and
# where its QR codes lead (final review A3): with ``manage`` alone an
# assistant could point a booth at an event it made and controls.

@tool("assign_booth_event",
      "Einer Box ein Event zuweisen (wie das Event-Feld auf der Box-Seite). Die Box übernimmt es beim nächsten "
      "Abgleich. Braucht zusätzlich das Recht secure: ab dann landen alle Bilder der Box dort.",
      {"type": "object", "required": ["booth_id", "event_id"],
       "properties": {"booth_id": {"type": "integer"}, "event_id": {"type": "integer"}}},
      scope="manage", also=("secure",))
def _assign_booth_event(booth_id: int, event_id: int) -> dict:
    device = _booth(booth_id)
    event = _event(event_id)
    ecosystem.save_device_profile(device["id"], ecosystem.device_profile(device), event["id"], strict=False)
    return {"booth_id": device["id"], "event": {"id": event["id"], "slug": event["slug"], "name": event["name"]}}


# ══ Package D: legal texts and access requests (owner's request 15.11.2026) ══

_LEGAL_TEXTS = ("impressum_text", "datenschutz_text", "impressum_text_en", "datenschutz_text_en")
_LEGAL_TEXT_MAX = 20000             # = safe_markdown.MAX_CHARS (R2-U5)


def _legal_keys() -> tuple:
    # The facts come from app/legal.py (another branch extends them); its
    # public LEGAL_FIELDS is the list, never a copy here.
    from app import legal
    return _LEGAL_TEXTS + tuple(legal.LEGAL_FIELDS)


@tool("get_legal_texts", "Impressum, Datenschutzerklärung (de/en) und die Angaben unter Einstellungen → Rechtliches "
      "lesen. Leere Texte heißen: die Galerie zeigt ihre eingebaute Fassung.", scope="read")
def _get_legal_texts() -> dict:
    return {k: (db.get_setting(k) or "") for k in _legal_keys()}


@tool("set_legal_texts",
      "Impressum, Datenschutzerklärung (de/en) und die Angaben unter Einstellungen → Rechtliches ändern. "
      "Nur übergebene Felder ändern sich; ein leerer Text heißt: die eingebaute Fassung. Wird protokolliert.",
      {"type": "object",
       "properties": {"impressum_text": {"type": "string"}, "datenschutz_text": {"type": "string"},
                      "impressum_text_en": {"type": "string"}, "datenschutz_text_en": {"type": "string"},
                      "facts": {"type": "object", "description": "Angaben wie legal_operator, legal_contact_email, "
                                "legal_role (controller|processor), legal_address, legal_phone, legal_register, "
                                "legal_tax, legal_editorial, legal_dispute, legal_server_logs, "
                                "legal_assistant … (alle Felder aus Einstellungen → Rechtliches)"}}},
      scope="secure")
def _set_legal_texts(facts: Optional[dict] = None, **texts: Any) -> dict:
    from app import legal
    changes: Dict[str, str] = {}
    for key, value in texts.items():
        if key not in _LEGAL_TEXTS:
            raise ValueError(f"Unbekanntes Feld: {key}")
        if not isinstance(value, str):
            raise ValueError(f"{key} muss Text sein")
        if len(value) > _LEGAL_TEXT_MAX:
            raise ValueError(f"{key}: höchstens {_LEGAL_TEXT_MAX} Zeichen (sind {len(value)})")
        changes[key] = value
    for key, value in (facts or {}).items():
        if key not in legal.LEGAL_FIELDS:
            raise ValueError(f"Unbekannte Angabe: {key}")
        value = str(value or "").strip()[:500]
        # Same rule as the admin form (settings_legal).
        if key == "legal_role" and value not in legal.ROLES:
            value = "controller"
        changes[key] = value
    if not changes:
        raise ValueError("Nichts zu ändern")
    for key, value in changes.items():
        db.set_setting(key, value)
    _log_result("Rechtstexte geändert: " + ", ".join(sorted(changes)))
    return {"changed": sorted(changes)}


def _mask(email: str) -> str:
    local, _, domain = str(email or "").partition("@")
    return (local[:1] + "***@" + domain) if domain else "***"


@tool("list_access_requests",
      "Offene Zugriffsanfragen von Gästen zu einer passwortgeschützten Galerie: Kennung, Name, Notiz, "
      "Zeitpunkt und die Adresse gekürzt.", {"type": "object", "required": ["event_id"],
                                             "properties": {"event_id": {"type": "integer"}}},
      scope="manage", untrusted=True)
def _list_access_requests(event_id: int) -> dict:
    from app import access
    event = _event(event_id)
    return {"event": {"id": event["id"], "name": event["name"]},
            "requests": [{"id": r["id"], "name": r.get("name") or "", "email": _mask(r.get("email")),
                          "note": r.get("note") or "", "created_at": r.get("created_at")}
                         for r in access.list_open(event["id"])]}


@tool("decide_access_request",
      "Eine Zugriffsanfrage entscheiden: grant schickt der angegebenen Adresse eine Mail mit dem Link, der "
      "ohne Passwort öffnet; deny schickt ihr eine kurze Mail mit der Absage. Beides geht an die Adresse, die "
      "der Anfragende selbst eingetippt hat. Braucht zusätzlich das Recht photos (der Link öffnet die Bilder).",
      {"type": "object", "required": ["event_id", "request_id", "decision"],
       "properties": {"event_id": {"type": "integer"}, "request_id": {"type": "integer"},
                      "decision": {"type": "string", "enum": ["grant", "deny"]}}},
      scope="manage", also=("photos",))
def _decide_access_request(event_id: int, request_id: int, decision: str) -> dict:
    from app import access
    from app.blueprints.sharing import access_grant_url
    if decision not in ("grant", "deny"):
        raise ValueError("decision ist grant oder deny")
    event = _event(event_id)
    req = access.get(int(request_id), event["id"])
    if not req or req.get("state") != access.OPEN:
        raise ValueError("Diese Anfrage gibt es nicht (mehr) offen")
    if decision == "deny":
        access.deny(event, req)
        _log_result(f"Zugriffsanfrage #{req['id']} abgelehnt")
        return {"request_id": req["id"], "decision": "deny"}
    sent = access.grant(event, req, access_grant_url(event))
    _log_result(f"Zugriffsanfrage #{req['id']} " + ("freigegeben" if sent else "nicht freigegeben (Mail ging nicht raus)"))
    return {"request_id": req["id"], "decision": "grant", "mail_sent": bool(sent),
            **({} if sent else {"note": "Die Mail ging nicht raus; die Anfrage bleibt offen."})}


# ══ The booth's second upload target (right secure) ══════════════════════
#
# Picdrop / FTP / SFTP of a booth (app/second_target.py). The fields and the
# check, never the password and never a new SFTP server key: an assistant
# should not carry FTP passwords — a chat transcript is a place the gallery
# does not control. Rule B2 holds at the booth: a changed server (type,
# host, port, user) loses the stored password and waits until the owner
# types the new one in the gallery form or at the booth. No switch at the
# booth any more (17.11.2026).

def _second_state(device: dict) -> dict:
    from app import second_target
    st = second_target.state(db.get_device_status(device["id"]) or {})
    shown = {k: st.get(k) for k in ("supported", "configured", "enabled", "type", "host", "port", "user",
                                    "path", "flat", "originals", "print_sheets", "password_set",
                                    "awaiting_password", "check")
             if k in st}
    if not st.get("supported"):
        shown["note"] = "Die Box meldet kein zweites Ziel (Fassung vor dem 17.11.2026)."
    elif not st.get("can_set"):
        shown["note"] = ("Die Box hat noch keinen Schlüssel gemeldet (ältere Fassung mit Schalter an der Box "
                         "oder noch nicht online).")
    elif st.get("awaiting_password"):
        shown["note"] = ("Wartet auf das Passwort für den neuen Server: nur der Besitzer trägt es im Formular der "
                         "Galerie (Sync-Seite der Box) oder an der Box ein. Bis dahin sendet die Box nichts dorthin.")
    shown["last_changes"] = second_target.last_changes(device["id"])
    return shown


@tool("get_second_target",
      "Das zweite Upload-Ziel einer Box (Picdrop, FTP, SFTP): Art, Server, Benutzer, Ordner, ein/aus, ob ein "
      "Passwort gespeichert ist, ob die Box auf ein Passwort wartet (awaiting_password), die letzte Prüfung "
      "und die letzten Änderungen (wer, wann, welche Felder). Das Passwort selbst nie. Dazu gesendet/offen/"
      "fehlgeschlagen je Ziel.",
      {"type": "object", "required": ["booth_id"], "properties": {"booth_id": {"type": "integer"}}},
      scope="secure", untrusted=True)
def _get_second_target(booth_id: int) -> dict:
    device = _booth(booth_id)
    status = db.get_device_status(device["id"]) or {}
    return {"booth": device["name"], "second_target": _second_state(device),
            "uploads_per_target": status.get("upload_targets") or {}}


_SECOND_FIELDS = ("type", "host", "port", "user", "path", "flat", "enabled", "originals", "print_sheets")


@tool("set_second_target",
      "Das zweite Upload-Ziel einer Box ändern. Nur die angegebenen Felder ändern sich, der Rest bleibt: "
      "path (Ordner; leer = Startordner der Anmeldung, „/“ vorn = ab oberster Ebene, Platzhalter {event} "
      "{event_name} {datum} {jahr} {monat} {tag}), flat, enabled, originals (volle Auflösung, Standard an), "
      "print_sheets (Standard aus) frei; type (ftps|ftp|sftp), host (nur Name oder IP), port (0 = üblich) und "
      "user ebenfalls — aber dann löscht die Box das gespeicherte Passwort und den gemerkten SFTP-Schlüssel und "
      "lädt nichts mehr hoch, bis der Besitzer das Passwort für den neuen Server im Formular der Galerie oder an "
      "der Box einträgt. Ein Passwort lässt sich hier absichtlich NIE setzen, ein neuer SFTP-Server-Schlüssel "
      "nie annehmen.",
      {"type": "object", "required": ["booth_id"],
       "properties": {"booth_id": {"type": "integer"},
                      "type": {"type": "string", "enum": ["ftps", "ftp", "sftp"]},
                      "host": {"type": "string"}, "port": {"type": "integer"}, "user": {"type": "string"},
                      "path": {"type": "string"}, "flat": {"type": "boolean"}, "enabled": {"type": "boolean"},
                      "originals": {"type": "boolean"}, "print_sheets": {"type": "boolean"}}},
      scope="secure")
def _set_second_target(booth_id: int, **given) -> dict:
    from app import second_target
    extra = sorted(k for k in given if k not in _SECOND_FIELDS)
    if extra:
        raise ValueError("Nicht über diesen Weg: " + ", ".join(extra) +
                         (" — ein Passwort trägt nur der Besitzer im Formular der Galerie oder an der Box ein"
                          if any("pass" in k for k in extra) else "") +
                         (" — einen Server-Schlüssel nimmt nur der Besitzer im Formular der Galerie an"
                          if any("key" in k or "finger" in k for k in extra) else ""))
    device = _booth(booth_id)
    status = db.get_device_status(device["id"]) or {}
    st = second_target.state(status)
    if not st.get("configured") and not {"type", "host"} <= set(given):
        raise ValueError("Die Box hat noch kein zweites Ziel: type und host sind nötig.")
    # Unnamed fields keep what the booth reports, so "change the folder" changes the folder only.
    current = {"type": st.get("type") or "", "host": st.get("host") or "", "port": int(st.get("port") or 0),
               "user": st.get("user") or "", "path": st.get("path") or "", "flat": st.get("flat", True),
               "enabled": st.get("enabled", True), "originals": st.get("originals", True),
               "print_sheets": bool(st.get("print_sheets"))} if st.get("configured") else \
        {"port": 0, "user": "", "path": "", "flat": True, "enabled": True, "originals": True, "print_sheets": False}
    fields = {**current, **{k: v for k, v in given.items() if k in _SECOND_FIELDS}}
    try:
        cleaned = second_target.clean_fields(fields)
        job_id = second_target.set_job(device["id"], status, fields, password=None)
    except (PermissionError, LookupError) as e:
        raise ValueError(str(e)) from e
    drops = second_target.drops_password(st, cleaned, False)
    g._protokoll_was = second_target.audit_text(device["id"], st, cleaned, source="MCP")
    _log_result(f"Gerät #{device['id']}")
    out = {"job_id": job_id, "booth": device["name"],
           "changed": [second_target.FIELD_LABELS[k] for k in second_target.changed_fields(st, cleaned)],
           "password": "gelöscht, Box wartet auf ein neues" if drops else "unverändert",
           "note": "Die Box übernimmt es bei ihrem nächsten Kontakt; Stand mit get_second_target."}
    if drops:
        out["owner_must"] = ("Der Besitzer muss das Passwort für den neuen Server im Formular der Galerie "
                             "(Sync-Seite der Box, Karte „Zweites Ziel“) oder an der Box eintragen.")
    return out


@tool("check_second_target",
      "Die Box prüft ihr zweites Upload-Ziel (Anmeldung, Ordner). Das Ergebnis in Worten steht nach dem "
      "nächsten Kontakt in get_second_target unter check.",
      {"type": "object", "required": ["booth_id"], "properties": {"booth_id": {"type": "integer"}}},
      scope="secure")
def _check_second_target(booth_id: int) -> dict:
    from app import second_target
    device = _booth(booth_id)
    st = second_target.state(db.get_device_status(device["id"]) or {})
    if not st.get("supported"):
        raise ValueError(_second_state(device).get("note") or "Nicht möglich")
    job_id = ecosystem.add_job(device["id"], "check_second_target", {})
    return {"job_id": job_id, "booth": device["name"]}


assert all(spec["scope"] in oauth.SCOPES for spec in TOOLS.values())
