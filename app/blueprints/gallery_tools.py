"""Admin tools of package D (gallery features from the vendor comparison).

Guest upload PIN and table sign (no. 58). Every route here is admin only
and every write checks the form token (CSRF), like the rest of the admin.
"""
from __future__ import annotations

import hmac

from flask import Blueprint, abort, flash, redirect, render_template, request, session, url_for

from app import branding, db, gallery_settings as gsm

bp = Blueprint("gallery_tools", __name__)


def _admin():
    from app.blueprints.admin import _require_admin
    return _require_admin()


def _check_csrf() -> None:
    sent = str(request.form.get("csrf_token") or "")
    if not session.get("csrf_token") or not hmac.compare_digest(sent, str(session["csrf_token"])):
        abort(400)


def _event_or_404(event_id: int) -> dict:
    from app import ecosystem
    ecosystem.init_schema()
    event = db.get_event_by_id(int(event_id))
    if not event:
        abort(404)
    return event


# ── Guest upload PIN ─────────────────────────────────────────────────

@bp.post("/admin/events/<int:event_id>/upload-pin")
def upload_pin(event_id: int):
    r = _admin()
    if r:
        return r
    _check_csrf()
    # The change log (app/protokoll.py) records this POST by itself and masks
    # the column (its name contains "pin" and "hash").
    from app import guest_upload
    _event_or_404(event_id)
    back = url_for("admin.edit_event", event_id=event_id) + "#gast-upload"
    if request.form.get("remove"):
        guest_upload.clear_pin(event_id)
        flash("Upload-PIN entfernt. Gäste laden wieder ohne PIN hoch.", "success")
        return redirect(back)
    pin = (request.form.get("pin") or "").strip()
    if not guest_upload.valid_pin(pin):
        flash("Die PIN muss aus 4 bis 8 Ziffern bestehen. Nichts geändert.", "error")
        return redirect(back)
    guest_upload.set_pin(event_id, pin)
    # Not repeated in the message: a flash travels in the session cookie.
    flash("Upload-PIN gesetzt. Sie wird nur als Prüfwert gespeichert — bitte notieren.", "success")
    return redirect(back)


# ── Table sign ───────────────────────────────────────────────────────

@bp.route("/admin/events/<int:event_id>/table-sign", methods=["GET", "POST"])
def table_sign(event_id: int):
    """A printable card for the guest tables: name, QR, three steps, PIN.

    Print CSS, not a PDF: the browser's print dialog makes the PDF, and the
    page needs no new dependency. The PIN is never stored readable, so the
    operator types it here (POST, so it stays out of logs and history); it
    is printed only when it matches the stored hash - a sign with a wrong
    PIN on forty tables is worse than none.
    """
    r = _admin()
    if r:
        return r
    from app import guest_upload
    from app.blueprints.admin import _csrf_token
    event = _event_or_404(event_id)
    size = "a5" if (request.values.get("size") == "a5") else "a6"
    typed = ""
    if request.method == "POST":
        _check_csrf()
        typed = (request.form.get("pin") or "").strip()
    pin_ok = bool(typed) and guest_upload.check_pin(event, typed)
    gs = gsm.for_event(event)
    return render_template(
        "admin/table_sign.html",
        event=event, size=size, brand=branding.event_branding(event),
        pin=typed if pin_ok else "", pin_wrong=bool(typed) and not pin_ok,
        has_pin=guest_upload.has_pin(event),
        upload_on=bool(gs["guest_upload"] or (db.get_setting("guest_upload_enabled") or "0") == "1"),
        gallery_path=f"/gallery/{event['slug']}", csrf_token=_csrf_token(),
    )


# ── Trash (no. 61) ───────────────────────────────────────────────────

@bp.app_context_processor
def _trash_flag():
    """``trash_on()`` for admin pages that explain a delete; a callable, so
    pages that do not ask pay nothing."""
    def trash_on() -> bool:
        from app import trash
        try:
            return trash.enabled()
        except Exception:                   # noqa: BLE001 — a page must render
            return False
    def media_sources(rows):
        from app import media_source
        try:
            return media_source.summarize([dict(r) for r in rows or []])
        except Exception:                   # noqa: BLE001
            return {"chips": [], "of": {}, "labels": {}}
    return {"trash_on": trash_on, "media_sources": media_sources,
            "design_presets": gsm.DESIGN_PRESETS, "design_preset_labels": gsm.DESIGN_PRESET_LABELS,
            "gallery_trust_line": trust_line}


def trust_line() -> str:
    """The operator's short trust line for the gallery footer (no. 66), or ""."""
    try:
        return (db.get_setting("gallery_trust_line") or "").strip()[:160]
    except Exception:                       # noqa: BLE001
        return ""


def _human(n: int) -> str:
    from app import media
    return media.human_bytes(int(n or 0))


@bp.get("/admin/trash")
def trash_page():
    r = _admin()
    if r:
        return r
    from app import trash
    from app.blueprints.admin import _csrf_token
    event_id = request.args.get("event", type=int)
    items = trash.entries(event_id=event_id)
    for it in items:
        it["size_h"] = _human(it.get("size_bytes"))
        it["trashed_h"] = (it.get("trashed_at") or "")[:16].replace("T", " ")
        it["purge_h"] = (it.get("purge_after") or "")[:10]
    use = trash.usage()
    return render_template("admin/trash.html", items=items, days=trash.days(), guests=trash.for_guests(),
                           guests_raw=(db.get_setting("trash_guest_deletes") or "0") == "1",
                           usage_h=_human(use["bytes"]), usage=use, event_filter=event_id,
                           max_days=trash.MAX_DAYS, csrf_token=_csrf_token())


@bp.get("/admin/trash/<int:entry_id>/thumb")
def trash_thumb(entry_id: int):
    """Thumbnails live outside the public folders; only the admin sees them."""
    r = _admin()
    if r:
        return r
    from flask import send_file
    from app import media, trash
    entry = next((e for e in trash.entries(limit=5000) if e["id"] == entry_id), None)
    if not entry or not entry.get("thumb_rel"):
        abort(404)
    full = (media.data_dir() / entry["thumb_rel"]).resolve()
    if not full.is_relative_to((media.data_dir() / "trash").resolve()) or not full.is_file():
        abort(404)
    from app import uploads
    mimetype, _ = uploads.serve_type(full)
    return send_file(full, mimetype=mimetype, max_age=0)


@bp.post("/admin/trash/settings")
def trash_settings():
    r = _admin()
    if r:
        return r
    _check_csrf()
    from app import trash
    if trash.save_settings(request.form.get("trash_days"), bool(request.form.get("trash_guest_deletes"))):
        flash("Papierkorb gespeichert." if trash.enabled() else
              "Papierkorb aus: gelöschte Bilder sind wieder sofort weg.", "success")
    else:
        flash(f"Bitte 0 bis {trash.MAX_DAYS} Tage. Nichts geändert.", "error")
    return redirect(url_for("gallery_tools.trash_page"))


def _back():
    ev = request.form.get("event", type=int)
    return redirect(url_for("gallery_tools.trash_page", **({"event": ev} if ev else {})))


@bp.post("/admin/trash/<int:entry_id>/restore")
def trash_restore(entry_id: int):
    r = _admin()
    if r:
        return r
    _check_csrf()
    from app import trash
    if trash.restore(entry_id):
        flash("Bild wiederhergestellt.", "success")
    else:
        flash("Wiederherstellen ging nicht: Das Bild oder sein Event gibt es nicht mehr.", "error")
    return _back()


@bp.post("/admin/trash/<int:entry_id>/purge")
def trash_purge(entry_id: int):
    r = _admin()
    if r:
        return r
    _check_csrf()
    from app import trash
    trash.purge(entry_id, actor="admin")
    flash("Endgültig gelöscht. Die Löschung steht im Protokoll.", "success")
    return _back()


@bp.post("/admin/trash/restore-all")
def trash_restore_all():
    r = _admin()
    if r:
        return r
    _check_csrf()
    from app import trash
    ev = request.form.get("event", type=int)
    done = sum(1 for e in trash.entries(event_id=ev, limit=5000) if trash.restore(e["id"]))
    flash(f"{done} Bilder wiederhergestellt.", "success")
    return _back()


@bp.post("/admin/trash/empty")
def trash_empty():
    r = _admin()
    if r:
        return r
    _check_csrf()
    from app import trash
    ev = request.form.get("event", type=int)
    items = trash.entries(event_id=ev, limit=5000)
    # The form sends the number it showed; something new in between and
    # nothing is deleted - the same rule as "Fotos löschen, Event behalten".
    if str(request.form.get("confirm_count", "")) != str(len(items)):
        flash("Nichts gelöscht: Der Papierkorb hat sich inzwischen geändert. Bitte noch einmal.", "warning")
        return _back()
    for e in items:
        trash.purge(e["id"], actor="admin")
    flash(f"{len(items)} Bilder endgültig gelöscht. Die Löschungen stehen im Protokoll.", "success")
    return _back()


# ── Sorting out (no. 62) ─────────────────────────────────────────────

@bp.route("/admin/events/<int:event_id>/aussortieren", methods=["GET", "POST"])
def sortout_page(event_id: int):
    """Near-duplicates and blurry photos of one event, as a proposal.

    POST acts only on ids of this event (a foreign id in the form is
    ignored) and only on what the operator ticked.
    """
    r = _admin()
    if r:
        return r
    from app import sortout
    from app.blueprints.admin import _csrf_token
    event = _event_or_404(event_id)
    strict = request.values.get("streng", "normal")
    if strict not in sortout.BLUR_FACTOR:
        strict = "normal"
    if request.method == "POST":
        _check_csrf()
        wanted = {int(i) for i in request.form.getlist("media_ids") if str(i).isdigit()}
        con = db.get_db()
        try:
            own = {int(x[0]) for x in con.execute("SELECT id FROM media WHERE event_id=?", (event_id,))}
        finally:
            con.close()
        ids = sorted(wanted & own)
        action = request.form.get("action")
        if not ids:
            flash("Nichts ausgewählt.", "warning")
        elif action == "hide":
            con = db.get_db()
            try:
                marks = ",".join("?" * len(ids))
                # Hidden like "ausblenden" everywhere else: into the review
                # queue, from where one click shows it again.
                con.execute(f"UPDATE media SET hidden=1, review_state='pending' WHERE id IN ({marks})", ids)
                con.commit()
            finally:
                con.close()
            flash(f"{len(ids)} Bilder ausgeblendet. Unter „Prüfen“ lassen sie sich wieder freigeben.", "success")
        elif action == "delete":
            from app import trash
            from app.blueprints.admin import _purge_media
            for i in ids:
                _purge_media(i, reason="admin", actor="admin", note="Aussortieren")
            flash(f"{len(ids)} Bilder " + ("in den Papierkorb gelegt." if trash.enabled() else "gelöscht.")
                  , "success")
        return redirect(url_for("gallery_tools.sortout_page", event_id=event_id, streng=strict))
    result = sortout.analyse(event_id, strictness=strict)
    from app import trash
    return render_template("admin/sortout.html", event=event, r=result, strict=strict,
                           trash_days=trash.days(), csrf_token=_csrf_token())


# ── Account defaults for galleries (Einstellungen → Galerie-Vorgaben) ──

#: Account switches on this page: key -> (allowed values, default).
ACCOUNT_KEYS = {
    "client_hub": (("0", "1"), "0"),
    # Free text (``None`` = any text up to 160 characters)
    "gallery_trust_line": (None, ""),
    "new_event_gallery_default": (("normal", "schlicht"), "normal"),
}


@bp.route("/admin/settings/galerie", methods=["GET", "POST"])
def account_gallery_settings():
    r = _admin()
    if r:
        return r
    from app import trash
    from app.blueprints.admin import _csrf_token
    if request.method == "POST":
        _check_csrf()
        for key, (allowed, default) in ACCOUNT_KEYS.items():
            if allowed is None:
                text = " ".join(str(request.form.get(key) or "").split())[:160]
                db.set_setting(key, text)
            elif allowed == ("0", "1"):
                db.set_setting(key, "1" if request.form.get(key) else "0")
            else:
                # A checkbox with a hidden fallback sends two values; the last one counts.
                sent = request.form.getlist(key)
                value = sent[-1] if sent else default
                db.set_setting(key, value if value in allowed else default)
        flash("Galerie-Vorgaben gespeichert.", "success")
        return redirect(url_for("gallery_tools.account_gallery_settings"))
    values = {k: (db.get_setting(k) or d) for k, (_a, d) in ACCOUNT_KEYS.items()}
    return render_template("admin/settings/galerie.html", v=values, trash_days=trash.days(),
                           csrf_token=_csrf_token(), active_tab="galerie")


# ── Overlay export (no. 65) ──────────────────────────────────────────

def overlay_png(template: dict, assets_dir, texts: dict, style_cfg: dict = None) -> "Image.Image":
    """The template's frame with transparent photo fields, at paper size.

    Rendered twice by the booth's own renderer, once with black and once
    with white stand-in photos: wherever the two differ, a photo shows
    through. The difference is the transparency, so a half-transparent
    overlay picture above a photo field stays half-transparent, rotated or
    rounded fields come out exactly, and text above a field stays opaque.
    The QR code is left out: the other program prints its own.
    """
    from PIL import Image, ImageChops
    from app import vorlagen_render
    canvas = template.get("canvas") or vorlagen_render.PRINT_CANVAS
    shots = (template.get("shots") or 1)
    black = [Image.new("RGB", tuple(canvas), (0, 0, 0))] * max(1, int(shots))
    white = [Image.new("RGB", tuple(canvas), (255, 255, 255))] * max(1, int(shots))
    a = vorlagen_render.render_template(template, black, assets_dir=assets_dir, texts=texts,
                                        style_cfg=style_cfg, einpassen="fill").convert("RGB")
    b = vorlagen_render.render_template(template, white, assets_dir=assets_dir, texts=texts,
                                        style_cfg=style_cfg, einpassen="fill").convert("RGB")
    diff = ImageChops.difference(b, a).convert("L")
    alpha = diff.point(lambda v: 255 - v)
    # Colour where the frame covers a photo only partly: un-premultiply the
    # black render (a = colour * alpha).
    out = a.copy()
    if diff.getbbox():
        px_a, px_al = a.load(), alpha.load()
        bx = diff.getbbox()
        for y in range(bx[1], bx[3]):
            for x in range(bx[0], bx[2]):
                al = px_al[x, y]
                if 0 < al < 255:
                    r, g, bb = px_a[x, y]
                    k = 255.0 / al
                    px_a[x, y] = (min(255, int(r * k)), min(255, int(g * k)), min(255, int(bb * k)))
        out = a
    out.putalpha(alpha)
    return out


@bp.get("/admin/vorlagen/overlay.png")
def template_overlay():
    r = _admin()
    if r:
        return r
    import io
    from flask import send_file
    from app import ecosystem, vorlagen as vorlagen_mod
    from app.blueprints.admin_booth import _assets_dir
    datei = (request.args.get("datei") or "").strip()
    # Only a template the gallery stores, never a path from the address.
    if datei not in {a["file"] for a in ecosystem.list_assets("template")}:
        abort(404)
    template = vorlagen_mod.laden(datei, _assets_dir())
    if not template:
        abort(404)
    texts = {}
    ev_id = request.args.get("event_id", type=int)
    event = db.get_event_by_id(ev_id) if ev_id else None
    if event:
        texts = {"event_name": event["name"], "date": _de_date(event.get("date"))}
    from app import vorlagen_render
    # The same style as the editor's preview: the house style ("clean")
    # unless one of the occasions is named.
    stil = request.args.get("stil") or "clean"
    img = overlay_png(template, _assets_dir(), texts,
                      {"preset": stil if stil in vorlagen_render.STYLE_PRESETS else "clean"})
    buf = io.BytesIO()
    img.save(buf, "PNG", optimize=True, dpi=(300, 300))
    buf.seek(0)
    name = datei.rsplit(".", 1)[0] + "-overlay.png"
    return send_file(buf, mimetype="image/png", as_attachment=True, download_name=name, max_age=0)


def _de_date(raw) -> str:
    raw = str(raw or "")[:10]
    return f"{raw[8:10]}.{raw[5:7]}.{raw[0:4]}" if len(raw) == 10 else ""
