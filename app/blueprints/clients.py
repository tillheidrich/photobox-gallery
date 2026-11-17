"""Client access: the client's door (/kunde, /client) and the operator's pages.

The rules live in app/clients.py; this file only routes.
"""
from __future__ import annotations

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for

from app import clients, db
from app.i18n import t

bp = Blueprint("clients", __name__)


@bp.before_request
def _globals():
    """The layouts expect what the public and admin blueprints prepare."""
    from flask import g, session
    if request.path.startswith("/admin/"):
        from app.blueprints.admin import _inject_globals
        g.lang = session.get("lang", "de")
        _inject_globals()
    else:
        from app.blueprints.public import _inject_globals
        _inject_globals()


# ── Public ────────────────────────────────────────────────────

def _brake_key() -> str:
    return f"client:{request.remote_addr}"


def _login_page(error: str = "", status: int = 200, headers=None):
    from app.blueprints.public import _make_csrf
    return render_template("public/client_login.html", error=error, csrf_token=_make_csrf(),
                           noindex=True), status, headers or {}


@bp.route("/kunde", methods=["GET", "POST"])
@bp.route("/client", methods=["GET", "POST"])
def client_home():
    from app import sharelinks
    from app.blueprints.public import _make_csrf, _pruefe_csrf
    if request.method == "POST":
        _pruefe_csrf()
        key = _brake_key()
        wait = sharelinks.retry_after(key, clients.BRAKE_LIMIT, clients.BRAKE_WINDOW)
        if wait:
            return _login_page(sharelinks.wait_message(wait), 429, {"Retry-After": str(wait)})
        found = clients.find_by_code(request.form.get("code", ""))
        if not found:
            sharelinks.record_failure(key, clients.BRAKE_WINDOW)
            return _login_page(t("client.code_wrong"), 401)
        sharelinks.forget(key)
        clients.logout()
        clients.login(found)
        return redirect(request.path)
    client = clients.current()
    if not client:
        return _login_page()
    galleries = clients.overview(client["id"])
    layouts = _layout_tiles(galleries)
    return render_template("public/client_home.html", client=client, galleries=galleries,
                           layouts=layouts, hub=_hub_tiles(galleries, layouts),
                           csrf_token=_make_csrf(), noindex=True)


def hub_on() -> bool:
    """Account setting ``client_hub`` (no. 63), default off."""
    return (db.get_setting("client_hub") or "0") == "1"


def _hub_tiles(galleries, layouts) -> dict:
    """Per gallery slug the tiles that apply; empty when the hub is off.

    Every tile leads somewhere the client may already go: the gallery, its
    ZIP (only when the ZIP is allowed for this visitor), the layout
    invitation, the table sign (only with the guest upload on), the review
    page (only when a review is asked for) and the operator's contact address
    (only when one is set in the legal settings).
    """
    if not hub_on():
        return {}
    from app import bewertung, gallery_settings as gsm, legal
    from app.blueprints.public import _zip_allowed
    contact = ""
    try:
        contact = (legal.settings_from_db().get("legal_contact_email") or "").strip()
    except Exception:                           # noqa: BLE001 — the hub renders without it
        contact = ""
    with_layout = {l["slug"]: l for l in layouts}
    out = {}
    for g in galleries:
        if g.get("expired"):
            continue
        event = db.get_event_by_slug(g["slug"])
        if not event:
            continue
        gs = gsm.for_event(event)
        tiles = [{"key": "gallery", "href": f"/gallery/{event['slug']}", "label": t("hub.gallery"),
                  "hint": t("hub.gallery_hint", n=g.get("count", 0))}]
        if gs["selection"]:
            tiles.append({"key": "picks", "href": f"/gallery/{event['slug']}#photoGrid",
                          "label": t("hub.picks"), "hint": t("hub.picks_hint")})
        if g.get("count") and _zip_allowed(event):
            tiles.append({"key": "zip", "href": f"/gallery/{event['slug']}/download.zip",
                          "label": t("hub.zip"), "hint": t("hub.zip_hint")})
        if event["slug"] in with_layout:
            tiles.append({"key": "layout", "href": f"/kunde/drucklayout/{event['slug']}",
                          "label": t("client.layout.title"), "hint": t("hub.layout_hint")})
        if gs["guest_upload"]:
            tiles.append({"key": "sign", "href": f"/kunde/tisch-schild/{event['slug']}",
                          "label": t("hub.sign"), "hint": t("hub.sign_hint")})
        if bewertung.fuer_event(event, gs)["an"]:
            tiles.append({"key": "review", "href": f"/bewertung/{event['slug']}",
                          "label": t("hub.review"), "hint": t("hub.review_hint")})
        if contact:
            tiles.append({"key": "contact", "href": f"mailto:{contact}", "label": t("hub.contact"),
                          "hint": contact})
        out[event["slug"]] = tiles
    return out


@bp.get("/kunde/tisch-schild/<slug>")
@bp.get("/client/table-sign/<slug>")
def client_table_sign(slug: str):
    """The table sign for the client's own event (no. 63): no PIN field,
    the PIN stays with the operator. Only with the hub and the guest upload on."""
    from flask import abort
    from app import branding, gallery_settings as gsm
    if not hub_on():
        abort(404)
    client = clients.current()
    event = db.get_event_by_slug(slug)
    if not client or not event or not clients.holds(event):
        abort(404)
    if not gsm.for_event(event)["guest_upload"]:
        abort(404)
    size = "a5" if request.args.get("size") == "a5" else "a6"
    return render_template("admin/table_sign.html", event=event, size=size,
                           brand=branding.event_branding(event), pin="", pin_wrong=False, has_pin=False,
                           upload_on=True, gallery_path=f"/gallery/{event['slug']}", csrf_token="",
                           client_view=True)


def _layout_tiles(galleries) -> list:
    """Galleries of this client with a live layout invitation (app/layout_freigabe.py).

    The tile links to /kunde/drucklayout/<slug>: the client's session opens the
    page, so the token never has to appear here or in any log.
    """
    from app import layout_freigabe
    aus = []
    for e in galleries:
        event = db.get_event_by_slug(e.get("slug") or "")
        inv = layout_freigabe.for_event(event["id"]) if event else None
        if inv and layout_freigabe.live(inv):
            aus.append({"slug": event["slug"], "name": event["name"], "status": inv["status"]})
    return aus


@bp.post("/kunde/abmelden")
@bp.post("/client/logout")
def client_logout():
    from app.blueprints.public import _pruefe_csrf
    _pruefe_csrf()
    clients.logout()
    return redirect("/client" if request.path.startswith("/client") else "/kunde")


# ── Selections that stay (app/picks.py) ───────────────────────

def _pick_gate(slug: str, switch: str = "selection"):
    """The gallery, if the logged-in client may pick (or comment) in it - else an error answer."""
    from flask import jsonify
    from app import gallery_settings as gsm
    from app.blueprints.public import (_check_password_access, _event_by_slug, _expired_for_guest,
                                       _not_yet_live, _silent)
    event = _event_by_slug(slug)
    if not event:
        return None, (jsonify({"ok": False}), 404)
    client = clients.current()
    if not client or not clients.holds(event):
        return None, (jsonify({"ok": False, "error": "client access required"}), 403)
    if not event.get("published") or _silent(event):
        return None, (jsonify({"ok": False}), 404)
    if not _check_password_access(event) or _expired_for_guest(event) or _not_yet_live(event):
        return None, (jsonify({"ok": False}), 403)
    if not gsm.for_event(event)[switch]:
        return None, (jsonify({"ok": False, "error": f"{switch} off"}), 403)
    return (event, client), None


def _json_body():
    # JSON forces a preflight on a foreign page, which is what keeps these
    # writes free of CSRF (same rule as /api/photo/<id>/tag).
    from flask import jsonify
    if not request.is_json:
        return None, (jsonify({"ok": False, "error": "json required"}), 415)
    return request.get_json(silent=True) or {}, None


@bp.route("/kunde/api/<slug>/selections", methods=["GET", "POST"])
def client_selections(slug: str):
    from flask import jsonify
    from app import picks
    if request.method == "POST":
        body, err = _json_body()
        if err:
            return err
    ok, err = _pick_gate(slug)
    if err:
        return err
    event, client = ok
    if request.method == "POST":
        try:
            sid = picks.create(client["id"], event["id"], str(body.get("name") or ""))
        except ValueError as e:
            return jsonify({"ok": False, "error": str(e)}), 400
        return jsonify({"ok": True, "id": sid, "name": picks.get(sid)["name"]})
    return jsonify({"ok": True, "max": picks.cap(event),
                    "selections": picks.for_client(client["id"], event["id"])})


@bp.post("/kunde/api/<slug>/selections/<int:selection_id>/items")
def client_selection_item(slug: str, selection_id: int):
    from flask import jsonify
    from app import db as _db, picks
    body, err = _json_body()
    if err:
        return err
    ok, err = _pick_gate(slug)
    if err:
        return err
    event, client = ok
    sel = picks.get(selection_id)
    if not sel or int(sel["client_id"]) != int(client["id"]) or int(sel["event_id"]) != int(event["id"]):
        return jsonify({"ok": False}), 404
    if body.get("delete") is True:
        picks.delete(selection_id)
        return jsonify({"ok": True, "deleted": True})
    try:
        mid = int(body.get("media_id"))
    except (TypeError, ValueError):
        return jsonify({"ok": False}), 400
    row = _db.get_media(mid)
    if (not row or int(row["event_id"]) != int(event["id"]) or int(row.get("hidden") or 0)
            or str(row.get("review_state") or "") == "pending"):
        return jsonify({"ok": False}), 404
    on = body.get("on") is not False
    limit = picks.cap(event)
    current = picks.media_ids(selection_id)
    if on and limit and mid not in current and len(current) >= limit:
        return jsonify({"ok": False, "error": "full", "max": limit, "count": len(current)}), 409
    count = picks.toggle(selection_id, mid, on)
    return jsonify({"ok": True, "count": count, "max": limit, "on": on})


# ── Comments (app/comments.py) ─────────────────────────────────

def _comment_view(c: dict) -> dict:
    return {"id": c["id"], "media_id": c.get("media_id"), "parent_id": c.get("parent_id"),
            "author": c["author"], "body": c["body"], "from_operator": bool(c["from_operator"]),
            "created_at": c["created_at"]}


@bp.route("/kunde/api/<slug>/comments", methods=["GET", "POST"])
def client_comments(slug: str):
    from flask import jsonify
    from app import comments, sharelinks
    body = {}
    if request.method == "POST":
        body, err = _json_body()
        if err:
            return err
    ok, err = _pick_gate(slug, "comments")
    if err:
        return err
    event, client = ok
    if request.method == "GET":
        return jsonify({"ok": True, "comments": [_comment_view(c) for c in
                                                 comments.for_client(event["id"], client["id"])]})
    media_id = body.get("media_id")
    if media_id is not None:
        from app import db as _db
        try:
            row = _db.get_media(int(media_id))
        except (TypeError, ValueError):
            row = None
        if (not row or int(row["event_id"]) != int(event["id"]) or int(row.get("hidden") or 0)
                or str(row.get("review_state") or "") == "pending"):
            return jsonify({"ok": False}), 404
        media_id = int(row["id"])
    try:
        text = comments.clean_body(body.get("body"))
    except ValueError:
        return jsonify({"ok": False, "error": "length", "max": comments.BODY_MAX}), 400
    if sharelinks.too_often(f"comment:{client['id']}", limit=comments.BRAKE_LIMIT, window=comments.BRAKE_WINDOW):
        return jsonify({"ok": False, "error": "too often"}), 429
    cid = comments.add(event["id"], body=text, author=client["name"], client_id=client["id"], media_id=media_id)
    try:
        from app import notifier
        base = db.base_url() or request.host_url.rstrip("/")
        notifier.notify_comment(event_name=event["name"], client_name=client["name"],
                                admin_url=f"{base}/admin/events/{event['id']}/edit#kommentare")
    except Exception:                   # noqa: BLE001
        pass
    return jsonify({"ok": True, "comment": _comment_view(comments.get(cid))})


@bp.post("/admin/events/<int:event_id>/comments/<int:comment_id>/reply")
def admin_comment_reply(event_id: int, comment_id: int):
    r = _admin_or_redirect()
    if r:
        return r
    _check_csrf()
    from app import comments
    c = comments.get(comment_id)
    if not c or int(c["event_id"]) != int(event_id):
        abort(404)
    try:
        comments.add(event_id, body=request.form.get("body", ""), author=t("comments.operator"),
                     media_id=c.get("media_id"), parent_id=c.get("parent_id") or c["id"], from_operator=True)
        flash(t("client.admin.saved"), "success")
    except ValueError:
        flash(t("comments.length", n=comments.BODY_MAX), "error")
    return redirect(url_for("admin.edit_event", event_id=event_id) + "#kommentare")


@bp.post("/admin/events/<int:event_id>/comments/<int:comment_id>/delete")
def admin_comment_delete(event_id: int, comment_id: int):
    r = _admin_or_redirect()
    if r:
        return r
    _check_csrf()
    from app import comments
    c = comments.get(comment_id)
    if not c or int(c["event_id"]) != int(event_id):
        abort(404)
    comments.delete(comment_id)
    flash(t("comments.deleted"), "success")
    return redirect(url_for("admin.edit_event", event_id=event_id) + "#kommentare")


def comment_panel(event_id: int) -> list:
    from app import comments
    return comments.for_event(event_id)


# ── Admin ─────────────────────────────────────────────────────

def _admin_or_redirect():
    from app.blueprints.admin import _require_admin
    return _require_admin()


def _check_csrf() -> None:
    from app.blueprints.admin import _check_csrf as check
    check()


def _event_choices():
    events = db.list_events() or []
    events.sort(key=lambda e: (e.get("date") or e.get("created_at") or ""), reverse=True)
    return events


@bp.route("/admin/clients", methods=["GET", "POST"])
def admin_clients():
    r = _admin_or_redirect()
    if r:
        return r
    from app.blueprints.admin import _csrf_token
    if request.method == "POST":
        _check_csrf()
        name = (request.form.get("name") or "").strip()
        if not name:
            flash(t("client.admin.name_required"), "error")
            return redirect(url_for("clients.admin_clients"))
        cid = clients.create(name, request.form.get("note", ""))
        flash(t("client.admin.created", name=name), "success")
        return redirect(url_for("clients.admin_client", client_id=cid))
    return render_template("admin/clients.html", clients=clients.list_all(),
                           csrf_token=_csrf_token(), active_nav="clients")


def _render_client(client, new_code: str = ""):
    from app.blueprints.admin import _csrf_token
    base = db.base_url() or request.host_url.rstrip("/")
    return render_template("admin/client_edit.html", client=client, events=_event_choices(),
                           assigned=set(clients.event_ids(client["id"])), new_code=new_code,
                           door_url=f"{base}/kunde", csrf_token=_csrf_token(), active_nav="clients")


@bp.route("/admin/clients/<int:client_id>", methods=["GET", "POST"])
def admin_client(client_id: int):
    r = _admin_or_redirect()
    if r:
        return r
    client = clients.get(client_id)
    if not client:
        abort(404)
    if request.method == "POST":
        _check_csrf()
        clients.update(client_id, name=request.form.get("name", ""), note=request.form.get("note", ""),
                       active=bool(request.form.get("active")))
        clients.set_events(client_id, request.form.getlist("event_ids"))
        flash(t("client.admin.saved"), "success")
        return redirect(url_for("clients.admin_client", client_id=client_id))
    return _render_client(client)


@bp.post("/admin/clients/<int:client_id>/code")
def admin_client_code(client_id: int):
    """A new code, shown once in this answer and nowhere else.

    Not via ``flash``: the session cookie is signed, not encrypted, and the
    code would sit readable in it until the next page.
    """
    r = _admin_or_redirect()
    if r:
        return r
    _check_csrf()
    if not clients.get(client_id):
        abort(404)
    code = clients.new_code(client_id)
    resp = _render_client(clients.get(client_id), new_code=code)
    return resp, 200, {"Cache-Control": "no-store"}


@bp.post("/admin/events/<int:event_id>/clients")
def admin_event_clients(event_id: int):
    """Assign clients from the event page."""
    r = _admin_or_redirect()
    if r:
        return r
    _check_csrf()
    if not db.get_event_by_id(event_id):
        abort(404)
    wanted = {int(x) for x in request.form.getlist("client_ids") if str(x).isdigit()}
    for c in clients.list_all():
        clients.assign(c["id"], event_id, c["id"] in wanted)
    flash(t("client.admin.saved"), "success")
    return redirect(url_for("admin.edit_event", event_id=event_id) + "#kunden")


@bp.get("/admin/events/<int:event_id>/selections/<int:selection_id>.<fmt>")
def admin_selection_export(event_id: int, selection_id: int, fmt: str):
    """A client's selection as file names for Lightroom: comma, " OR ", CSV or one per line."""
    r = _admin_or_redirect()
    if r:
        return r
    from flask import Response
    from app import picks
    sel = picks.get(selection_id)
    if not sel or int(sel["event_id"]) != int(event_id) or fmt not in picks.FORMATS:
        abort(404)
    body = picks.export(selection_id, fmt)
    from werkzeug.utils import secure_filename
    name = secure_filename(f"{sel['name']}-{selection_id}") or f"auswahl-{selection_id}"
    mimetype = "text/csv" if fmt == "csv" else "text/plain"
    disposition = "attachment" if fmt in ("csv", "txt") else "inline"
    ext = "csv" if fmt == "csv" else "txt"
    return Response(body, mimetype=f"{mimetype}; charset=utf-8",
                    headers={"Content-Disposition": f'{disposition}; filename="{name}.{ext}"',
                             "Cache-Control": "no-store"})


def panel(event_id: int) -> dict:
    """What the event page shows about clients (a Jinja global)."""
    from app import picks
    assigned = set(clients.clients_of_event(event_id))
    return {"clients": clients.list_all(), "assigned": assigned, "selections": picks.for_event(event_id)}
