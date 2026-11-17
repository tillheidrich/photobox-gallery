"""Freigabe-Links (/l/<slug>), Galerie-Einstellungen je Event, Farbmarkierung im Admin, Wasserzeichen."""
from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path

from flask import (Blueprint, abort, current_app, flash, jsonify, make_response, redirect, render_template, request,
                   send_file, session, url_for)
from PIL import Image, ImageDraw, ImageOps
from werkzeug.utils import secure_filename

from app import db, gallery_settings as gsm, media, sharelinks
from app.i18n import t

bp = Blueprint("sharing", __name__)


def _admin():
    from app.blueprints.admin import _require_admin
    return _require_admin()


def _csrf():
    from app.blueprints.admin import _csrf_token
    return _csrf_token()


def _check_csrf():
    import hmac
    if not session.get("csrf_token") or not hmac.compare_digest(str(request.form.get("csrf_token") or ""), str(session.get("csrf_token"))):
        abort(400)


# ── Öffentlich ────────────────────────────────────────────────

def _cookie_name(slug: str) -> str:
    return f"pbshare_{slug}"


@bp.before_request
def _guest_page_globals():
    """A shared link is read by guests in either language, like the gallery."""
    if request.path.startswith("/l/") and "/file/" not in request.path and not request.path.endswith(".zip"):
        from app.blueprints.public import _inject_globals
        _inject_globals()


def _resolve(slug: str):
    link = sharelinks.get(slug)
    st = sharelinks.status(link)
    if st == "ok" and link.get("password_hash") and not sharelinks.proof_valid(link, request.cookies.get(_cookie_name(slug))):
        st = "passphrase"
    return link, st


@bp.route("/l/<slug>", methods=["GET", "POST"])
def share_page(slug: str):
    link = sharelinks.get(slug)
    st = sharelinks.status(link)
    if link:
        # The event moved elsewhere (app/redirects.py): its share links follow,
        # whatever state they are in - the pictures are not here any more.
        from app.blueprints.public import _forward_guest
        forward = _forward_guest(db.get_event_by_id(link["event_id"]) or {})
        if forward:
            return forward
    wrong = blocked = False
    set_cookie = None
    if st == "ok" and link.get("password_hash"):
        if request.method == "POST":
            if sharelinks.too_often(f"pass:{slug}:{request.remote_addr}"):
                blocked = True
            elif sharelinks.check_password(link, request.form.get("pw", "")):
                set_cookie = sharelinks.proof(link)
            else:
                wrong = True
        if not set_cookie and not sharelinks.proof_valid(link, request.cookies.get(_cookie_name(slug))):
            st = "passphrase"
    event = db.get_event_by_id(link["event_id"]) if link else None
    items = []
    if st == "ok":
        from app.blueprints.public import guest_namer
        namers: dict = {}
        for m in sharelinks.media_for(link):
            if m.get("kind") == "original" and not link.get("media_ids"):
                continue
            # Each picture is named by the rule of the event it belongs to.
            eid = int(m.get("event_id") or (event or {}).get("id") or 0)
            if eid not in namers:
                ev = event if event and int(event["id"]) == eid else db.get_event_by_id(eid)
                namers[eid] = guest_namer(ev) if ev else (lambda row: row.get("filename") or "")
            is_video = str(m.get("path_original", "")).lower().endswith((".mp4", ".webm", ".mov"))
            items.append({"id": m["id"], "is_video": is_video, "w": m.get("width") or 4, "h": m.get("height") or 5,
                          "name": namers[eid](m), "folder": m.get("folder") or ""})
        sharelinks.track(link, "view", request.remote_addr, request.headers.get("User-Agent"))
    code = {"ok": 200, "not-found": 404, "passphrase": 401}.get(st, 410)
    html = render_template("public/share_link.html", link=link, event=event, st=st, items=items, wrong=wrong,
                           blocked=blocked, noindex=True)
    resp = make_response(html, code)
    resp.headers["X-Robots-Tag"] = "noindex, nofollow, noarchive, noimageindex"
    if set_cookie:
        resp.set_cookie(_cookie_name(slug), set_cookie, max_age=21600, httponly=True, samesite="Lax",
                        secure=request.is_secure)
    return resp


@bp.get("/l/<slug>/file/<int:media_id>")
def share_file(slug: str, media_id: int):
    link, st = _resolve(slug)
    if st != "ok":
        abort(401 if st == "passphrase" else 404 if st == "not-found" else 410)
    if not sharelinks.has_media(link, media_id):
        abort(404)
    row = db.get_media(media_id)
    download = request.args.get("download") == "1"
    thumb = request.args.get("thumb") == "1"
    if download and not link["allow_download"]:
        abort(403)
    if download:
        sharelinks.track(link, "download", request.remote_addr, request.headers.get("User-Agent"))
        from app import ecosystem
        src = row
        if row.get("uid") and row.get("variant") == "web":
            src = ecosystem.media_by_uid(row["uid"], "original") or row
        rel = src["path_original"] if src.get("storage", "local") == "local" else row["path_webp"]
        name = secure_filename(src["filename"] or f"foto_{media_id}.jpg")
        # The event's file-name rule (app/dateinamen.py), as in the gallery.
        from app import dateinamen
        event = db.get_event_by_id(link["event_id"]) or {}
        if event:
            rueckfall = name if "." in name else f"{name}{Path(rel or '').suffix or '.jpg'}"
            from app.blueprints.public import _abzugname
            name = _abzugname(event, src, rueckfall)
    else:
        is_video = str(row.get("path_original", "")).lower().endswith((".mp4", ".webm", ".mov"))
        rel = (row.get("path_thumb") if thumb else None) or (row["path_original"] if is_video and not thumb else row.get("path_webp")) or row["path_original"]
        name = None
    path = (media.data_dir() / rel).resolve()
    if not str(path).startswith(str(media.data_dir())) or not path.exists():
        abort(404)
    resp = send_file(path, as_attachment=download, download_name=name, max_age=300)
    resp.headers["X-Robots-Tag"] = "noindex, nofollow, noarchive, noimageindex"
    resp.headers["Cache-Control"] = "private, max-age=300"
    return resp


@bp.get("/l/<slug>/all.zip")
def share_zip(slug: str):
    link, st = _resolve(slug)
    if st != "ok" or not link["allow_download"]:
        abort(403)
    sharelinks.track(link, "download", request.remote_addr, request.headers.get("User-Agent"))
    from app import dateinamen
    event = db.get_event_by_id(link["event_id"])
    namen: dict = {}
    nummern: dict = {}
    used: set = set()
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_STORED) as z:
        for m in sharelinks.media_for(link)[:500]:
            p = media.data_dir() / (m["path_original"] if m.get("storage", "local") == "local" else (m.get("path_webp") or ""))
            if p.is_file():
                name = secure_filename(m["filename"] or p.name)
                # Each picture is named by the rule of the event it belongs to
                # (a folder is an event of its own).
                quelle = event if int(m.get("event_id") or event["id"]) == int(event["id"]) else (
                    db.get_event_by_id(int(m["event_id"])) or event)
                if int(quelle["id"]) not in namen:
                    namen[int(quelle["id"])] = dateinamen.event_datetime_namen(quelle)
                    nummern[int(quelle["id"])] = dateinamen.nummern(int(quelle["id"]))
                rueckfall = name if "." in name else f"{name}{p.suffix}"
                from app.blueprints.public import _abzugname
                name = _abzugname(quelle, m, rueckfall, nummer=nummern[int(quelle["id"])].get(int(m["id"]), 1),
                                  namen=namen[int(quelle["id"])])
                if m.get("folder"):
                    name = f"{secure_filename(m['folder']) or 'ordner'}/{name}"
                while name in used:
                    stem, dot, ext = name.rpartition(".")
                    name = f"{stem}-{len(used)}.{ext}" if dot else f"{name}-{len(used)}"
                used.add(name)
                z.write(p, name)
    buf.seek(0)
    return send_file(buf, mimetype="application/zip", as_attachment=True,
                     download_name=f"{secure_filename(link.get('title') or event['slug'])}.zip")


# ── Wasserzeichen ─────────────────────────────────────────────

def watermark_path(row: dict, which: str, text: str) -> Path | None:
    rel = row.get("path_thumb") if which == "thumb" else row.get("path_webp")
    if not rel:
        return None
    src = media.data_dir() / rel
    if not src.exists():
        return None
    cache = media.data_dir() / "cache" / "watermark"
    cache.mkdir(parents=True, exist_ok=True)
    import hashlib
    key = hashlib.sha1(f"{rel}|{text}".encode()).hexdigest()[:16]
    out = cache / f"{row['id']}_{which}_{key}.jpg"
    if out.exists() and out.stat().st_mtime >= src.stat().st_mtime:
        return out
    with Image.open(src) as im:
        im = ImageOps.exif_transpose(im).convert("RGBA")
    layer = Image.new("RGBA", im.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    from app.sharepic import _font
    size = max(14, im.width // 22)
    f = _font(size, True)
    label = text or "Vorschau"
    step_x = int(d.textlength(label, font=f) + size * 3)
    for y in range(-im.height, im.height * 2, size * 5):
        for x in range(-im.width, im.width * 2, step_x):
            d.text((x + (y // (size * 5)) % 2 * step_x // 2, y), label, font=f, fill=(255, 255, 255, 70))
    layer = layer.rotate(-24, resample=Image.Resampling.BICUBIC, center=(im.width // 2, im.height // 2))
    Image.alpha_composite(im, layer).convert("RGB").save(out, "JPEG", quality=84)
    return out


@bp.get("/gallery/<slug>/wm/<int:media_id>/<which>.jpg")
def watermarked(slug: str, media_id: int, which: str):
    """Die Vorschau mit Wasserzeichen.

    Bei ``watermark: true`` ist das der Weg, über den die Galerie ihre Bilder
    überhaupt ausliefert — ``/data/…`` ist dann gesperrt. Diese Route muss
    deshalb dieselben Schranken kennen wie die Galerie selbst: stiller
    Betrieb, Ablauf, Zeitplan, Freigabe. Bis zur Durchsicht am 19.09.2026
    prüfte sie nur ``published`` und das Passwort — wer eine Adresse aus dem
    Seitenquelltext hatte, lud die Bilder danach unbegrenzt weiter.
    """
    from app import folders
    from app.blueprints.public import (_check_password_access, _expired_for_guest, _is_admin, _not_yet_live,
                                       _wartet_auf_freigabe, _deny_if_silent)

    event = folders.apply_parent(db.get_event_by_slug(slug))
    row = db.get_media(media_id)
    if not event or not row or row["event_id"] != event["id"] or which not in ("thumb", "full") or not event.get("published"):
        abort(404)
    _deny_if_silent(event)
    if _wartet_auf_freigabe(row) and not _is_admin():
        abort(404)
    # Same gate as the gallery: password, link key, client code, parent (S18).
    if not _check_password_access(event):
        abort(403)
    if _expired_for_guest(event):
        abort(410)
    if _not_yet_live(event) and not _is_admin():
        abort(403)
    gs = gsm.for_event(event)
    path = watermark_path(row, which, gs["watermark_text"] or event["name"])
    if not path:
        abort(404)
    return send_file(path, mimetype="image/jpeg", max_age=86400)


# ── Admin ─────────────────────────────────────────────────────

@bp.post("/admin/events/<int:event_id>/gallery-settings")
def admin_gallery_settings(event_id: int):
    r = _admin()
    if r:
        return r
    _check_csrf()
    if not db.get_event_by_id(event_id):
        abort(404)
    event = db.get_event_by_id(event_id)
    before = gsm.for_event(event)
    # ``expires_at`` wohnt im Event-Formular („Sichtbarkeit & Zugang"), nicht hier —
    # ``base`` hält es fest, damit es beim Speichern nicht verloren geht.
    cfg = gsm.from_form(request.form, base=before)
    con = db.get_db()
    con.execute("UPDATE events SET gallery_json=? WHERE id=?", (gsm.store(event, cfg), event_id))
    con.commit()
    con.close()
    # Freigabe abgeschaltet? Dann darf nichts in der Warteschlange hängen bleiben.
    if before["moderation"] and not cfg["moderation"]:
        from app import ecosystem
        released = ecosystem.approve_media(event_id)
        # Folders that only waited because of this gallery's switch go too.
        from app import folders
        for child in folders.children_of(event_id):
            if not gsm.for_event(child)["moderation"]:
                released += ecosystem.approve_media(int(child["id"]))
        if released:
            flash(t("msg.review.approved", n=released), "success")
    flash("Galerie-Einstellungen gespeichert.", "success")
    return redirect(url_for("admin.edit_event", event_id=event_id) + "#galerie-einstellungen")


@bp.post("/admin/events/<int:event_id>/share-links")
def admin_share_create(event_id: int):
    r = _admin()
    if r:
        return r
    _check_csrf()
    if not db.get_event_by_id(event_id):
        abort(404)
    ids = [int(x) for x in request.form.get("media_ids", "").split(",") if x.strip().isdigit()]
    try:
        link = sharelinks.create(event_id, media_ids=ids, title=request.form.get("title", ""),
                                 note=request.form.get("note", ""), days=request.form.get("days", ""),
                                 allow_download=bool(request.form.get("allow_download")),
                                 password=request.form.get("password", ""))
    except ValueError as e:
        flash(str(e), "error")
        return redirect(url_for("admin.edit_event", event_id=event_id) + "#freigaben")
    base = db.base_url_for(db.get_event_by_id(event_id)) or request.host_url.rstrip("/")
    flash(f"Freigabe-Link erstellt: {base}/l/{link['slug']}", "success")
    return redirect(url_for("admin.edit_event", event_id=event_id) + "#freigaben")


# ── Alte Adressen ─────────────────────────────────────────────

@bp.post("/admin/events/<int:event_id>/alias/delete")
def admin_alias_delete(event_id: int):
    """Eine alte Adresse abschalten. Danach führt sie ins Leere — mit Ansage."""
    r = _admin()
    if r:
        return r
    _check_csrf()
    if not db.get_event_by_id(event_id):
        abort(404)
    alias = (request.form.get("alias") or "").strip()
    if db.delete_slug_alias(event_id, alias):
        flash(f"/gallery/{alias} leitet nicht mehr weiter.", "success")
    return redirect(url_for("admin.edit_event", event_id=event_id) + "#grunddaten")


# ── Zugriffsanfragen ──────────────────────────────────────────
# Freigeben heißt: dem Gast den Link schicken, der ohne Passwort öffnet.
# Erst wenn die Mail draußen ist, gilt die Anfrage als erledigt — sonst
# verschwindet sie aus der Liste, ohne dass jemand etwas bekommen hat.

def access_grant_url(event) -> str:
    """The gallery link a granted guest gets: it opens without the password,
    even when the event does not hand its key out otherwise. Also used by the
    MCP tool ``decide_access_request``."""
    from app.blueprints.public import _access_token, gallery_link
    url = gallery_link(event)
    if "access_token" not in url:
        sep = "&" if "?" in url else "?"
        url = f"{url}{sep}access_token={_access_token(event)}"
    return url


@bp.post("/admin/events/<int:event_id>/access/<int:req_id>/<decision>")
def admin_access_decide(event_id: int, req_id: int, decision: str):
    from app import access
    r = _admin()
    if r:
        return r
    _check_csrf()
    if decision not in ("grant", "deny"):
        abort(404)
    event = db.get_event_by_id(event_id)
    req = access.get(req_id, event_id) if event else None
    if not event or not req:
        abort(404)
    if decision == "deny":
        access.deny(event, req)
        flash("Anfrage abgelehnt.", "success")
    else:
        url = access_grant_url(event)
        if access.grant(event, req, url):
            flash(f"Freigegeben — Link an {req['email']} geschickt.", "success")
        else:
            flash("Die Mail ging nicht raus. Anfrage bleibt offen — bitte den Postausgang prüfen.", "error")
    return redirect(url_for("admin.edit_event", event_id=event_id) + "#zugang")


@bp.post("/admin/events/<int:event_id>/share-links/<int:link_id>/revoke")
def admin_share_revoke(event_id: int, link_id: int):
    r = _admin()
    if r:
        return r
    _check_csrf()
    flash("Link abgeschaltet." if sharelinks.revoke(link_id, event_id) else "Link war schon abgeschaltet.", "success")
    return redirect(url_for("admin.edit_event", event_id=event_id) + "#freigaben")
