"""Layout approval by the customer: the public page and the owner's buttons.

Public: ``/layout/<token>`` and, for a client logged in with their code,
``/kunde/drucklayout/<slug>`` — the same page and the same calls, so the
client area can link to it without ever knowing the token. Every token that
opens nothing (unknown, expired, locked, switch off) gets the same 404 page;
only those count against the brake, a valid link never does.

Admin: ``/admin/events/<id>/layout…``, behind the admin login and the form
token, written to the change log. The rules live in app/layout_freigabe.py.
"""
from __future__ import annotations

import hmac

from flask import (Blueprint, Response, abort, flash, g, jsonify, redirect, render_template, request,
                   session, url_for)

from app import db, layout_freigabe as lf, sharelinks

bp = Blueprint("layout", __name__)


@bp.before_request
def _globals():
    if request.path.startswith("/admin/"):
        from app.blueprints.admin import _inject_globals
        g.lang = session.get("lang", "de")
        _inject_globals()
    else:
        from app.blueprints.public import _inject_globals
        _inject_globals()
        # The address carries the token: analytics would record it.
        g.umami_enabled = False


@bp.after_request
def _private(response):
    if request.path.startswith("/admin/"):
        return response
    h = response.headers
    h["Cache-Control"] = "no-store"
    h["X-Robots-Tag"] = "noindex, nofollow, noarchive"
    h["Referrer-Policy"] = "no-referrer"
    return response


@bp.errorhandler(404)
def _gone(_error):
    if request.path.startswith("/admin/"):
        return _error
    # One page for unknown, expired and locked links alike: a stranger cannot
    # tell which of the three a token was.
    return render_template("public/layout_gone.html", noindex=True), 404


# ── Public: which invitation is this? ─────────────────────────

def _miss():
    key = lf.rate_key(request.remote_addr)
    if sharelinks.retry_after(key, lf.RATE_LIMIT, lf.RATE_WINDOW):
        abort(429)
    sharelinks.record_failure(key, lf.RATE_WINDOW)
    abort(404)


def _resolve(token: str = "", slug: str = ""):
    """(invitation, event, base address) for this request, or the 404/429 answer."""
    if slug:
        from app import clients
        client = clients.current()
        event = db.get_event_by_slug(slug) if client else None
        if not event or not clients.holds(event):
            abort(404)
        inv = lf.for_event(event["id"])
        if not lf.live(inv):
            abort(404)
        return inv, event, f"/kunde/drucklayout/{event['slug']}"
    inv = lf.by_token(token)
    event = lf.live(inv)
    if not event:
        _miss()
    return inv, event, f"/layout/{token}"


def _csrf() -> None:
    """The session's form token, from the form or the header (the page posts JSON)."""
    sent = str(request.form.get("csrf_token") or request.headers.get("X-CSRF-Token") or "")
    expected = session.get("csrf_token")
    if not expected or not hmac.compare_digest(sent, str(expected)):
        abort(400)


def _json():
    if not request.is_json:
        abort(415)
    return request.get_json(silent=True) or {}


def _jpeg(data: bytes) -> Response:
    return Response(data, mimetype="image/jpeg")


def _render(inv, work):
    if sharelinks.too_often(f"layoutvorschau:{inv['id']}", lf.PREVIEW_LIMIT, lf.PREVIEW_WINDOW):
        return jsonify({"ok": False, "error": "Zu viele Vorschauen, bitte kurz warten."}), 429
    try:
        ergebnis = lf.bounded(inv["id"], work)
        if isinstance(ergebnis, tuple):
            daten, zu_breit = ergebnis
            antwort = _jpeg(daten)
            antwort.headers["X-Layout-Too-Wide"] = "1" if zu_breit else "0"
            return antwort
        return _jpeg(ergebnis)
    except lf.Busy:
        return jsonify({"ok": False, "error": "Die Vorschau wird gerade berechnet."}), 429
    except ValueError as e:
        return jsonify({"ok": False, "error": str(e)}), 400


# Each public route exists twice: by token and, for a logged-in client, by
# the gallery's address. ``token``/``slug`` arrive as keyword arguments.

def _both(rule: str, **options):
    def wrap(view):
        bp.add_url_rule("/layout/<token>" + rule, view_func=view, **options)
        bp.add_url_rule("/kunde/drucklayout/<slug>" + rule, view_func=view,
                        endpoint=view.__name__ + "_kunde", **options)
        return view
    return wrap


@_both("", methods=["GET"])
def page(token: str = "", slug: str = ""):
    from app.blueprints.public import _make_csrf
    inv, event, base = _resolve(token, slug)
    return render_template("public/layout_customer.html", v=lf.view(inv, event), base=base,
                           csrf_token=_make_csrf(), noindex=True)


@_both("/kachel/<int:n>.jpg", methods=["GET"])
def tile(n: int, token: str = "", slug: str = ""):
    inv, event, _base = _resolve(token, slug)
    if not 0 <= n < len(inv["templates"]):
        abort(404)
    return _render(inv, lambda: lf.tile(inv, n, event))


@_both("/vorschau", methods=["POST"])
def preview(token: str = "", slug: str = ""):
    inv, event, _base = _resolve(token, slug)
    _csrf()
    body = _json()
    return _render(inv, lambda: lf.preview_with_note(inv, body, event))


@_both("/ergebnis.jpg", methods=["GET"])
def final_picture(token: str = "", slug: str = ""):
    inv, event, _base = _resolve(token, slug)
    if inv["status"] != "freigegeben":
        abort(404)
    return _render(inv, lambda: lf.result(inv, event))


def _step(token, slug, action):
    inv, event, _base = _resolve(token, slug)
    _csrf()
    body = _json()
    try:
        if action == "submit":
            lf.submit(inv, body, event)
        else:
            lf.save_draft(inv, body)
    except lf.StateError:
        return jsonify({"ok": False, "status": inv["status"],
                        "error": "Dein Entwurf ist abgeschickt und gerade gesperrt."}), 409
    except ValueError as e:
        return jsonify({"ok": False, "error": str(e)}), 400
    return jsonify({"ok": True, "status": lf.for_event(event["id"])["status"]})


@_both("/entwurf", methods=["POST"])
def draft(token: str = "", slug: str = ""):
    return _step(token, slug, "draft")


@_both("/abschicken", methods=["POST"])
def send(token: str = "", slug: str = ""):
    return _step(token, slug, "submit")


@_both("/logo", methods=["POST"])
def logo(token: str = "", slug: str = ""):
    inv, _event, _base = _resolve(token, slug)
    _csrf()
    if not inv["logo"]:
        return jsonify({"ok": False, "error": "Für dieses Layout ist kein Logo vorgesehen."}), 403
    if inv["status"] not in lf.EDITABLE:
        return jsonify({"ok": False, "error": "Dein Entwurf ist gerade gesperrt."}), 409
    datei = request.files.get("logo")
    if not datei:
        return jsonify({"ok": False, "error": "Keine Datei."}), 400
    if (datei.filename or "").lower().endswith((".svg", ".svgz")):
        return jsonify({"ok": False, "error": "SVG geht nicht. Bitte ein PNG, JPEG oder WebP hochladen."}), 400
    daten = datei.stream.read(lf.LOGO_MAX_BYTES + 1)
    if len(daten) > lf.LOGO_MAX_BYTES:
        return jsonify({"ok": False, "error": "Die Datei ist größer als 5 MB."}), 413
    try:
        lf.save_logo(inv, daten)
    except ValueError as e:
        return jsonify({"ok": False, "error": str(e)}), 400
    return jsonify({"ok": True})


@_both("/logo/entfernen", methods=["POST"])
def logo_remove(token: str = "", slug: str = ""):
    inv, _event, _base = _resolve(token, slug)
    _csrf()
    if inv["status"] not in lf.EDITABLE:
        return jsonify({"ok": False, "error": "Dein Entwurf ist gerade gesperrt."}), 409
    lf.remove_logo(inv)
    return jsonify({"ok": True})


# ── Admin ─────────────────────────────────────────────────────

def _admin_event(event_id: int, need_switch: bool = True):
    from app.blueprints.admin import _check_csrf, _require_admin
    r = _require_admin()
    if r:
        return None, r
    _check_csrf()
    event = db.get_event_by_id(event_id)
    if not event or (need_switch and not lf.switched_on(event)):
        abort(404)
    return event, None


def _back(event_id: int):
    return redirect(url_for("admin.edit_event", event_id=event_id) + "#layout-freigabe")


@bp.post("/admin/events/<int:event_id>/layout")
def admin_create(event_id: int):
    event, r = _admin_event(event_id)
    if r:
        return r
    f = request.form
    keys, labels, maxes, defaults = (f.getlist("field_key"), f.getlist("field_label"),
                                     f.getlist("field_max"), f.getlist("field_default"))
    fields = [{"key": k, "label": labels[i] if i < len(labels) else k,
               "max": maxes[i] if i < len(maxes) else 40, "default": defaults[i] if i < len(defaults) else ""}
              for i, k in enumerate(keys) if str(k).strip()]
    offer = {"templates": f.getlist("templates"), "fields": fields, "palettes": f.getlist("palettes"),
             "fonts": f.getlist("fonts"), "logo": bool(f.get("logo"))}
    from app import auth
    try:
        token = lf.create(event_id, offer, days=f.get("days") or lf.DEFAULT_DAYS, by=auth.admin_user())
    except ValueError as e:
        flash(str(e), "error")
        return _back(event_id)
    g._protokoll_was = "Layout-Link erzeugt"
    base = db.base_url() or request.host_url.rstrip("/")
    link = f"{base}/layout/{token}"
    inv = lf.for_event(event_id)
    from app.blueprints.admin import _csrf_token
    page = render_template("admin/events/layout_link.html", event=event, link=link, inv=inv,
                           until=lf._german_date(inv["expires_at"]),
                           csrf_token=_csrf_token())
    # The link is shown in this answer and nowhere else, never through
    # flash: the session cookie is signed, not encrypted.
    return page, 200, {"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"}


@bp.post("/admin/events/<int:event_id>/layout/freigeben")
def admin_approve(event_id: int):
    event, r = _admin_event(event_id)
    if r:
        return r
    from app import auth
    try:
        lf.approve(event_id, auth.admin_user() or "admin")
    except lf.StateError:
        flash("Freigeben geht erst, wenn der Kunde abgeschickt hat.", "warning")
        return _back(event_id)
    except ValueError as e:
        flash(str(e), "error")
        return _back(event_id)
    g._protokoll_was = "Layout freigegeben"
    flash("Freigegeben. Die Box bekommt das Layout beim nächsten Abgleich.", "success")
    return _back(event_id)


@bp.post("/admin/events/<int:event_id>/layout/zurueck")
def admin_send_back(event_id: int):
    event, r = _admin_event(event_id)
    if r:
        return r
    try:
        lf.send_back(event_id, request.form.get("note", ""))
    except lf.StateError:
        flash("Zurückgeben geht nur, solange ein Entwurf zur Freigabe wartet.", "warning")
        return _back(event_id)
    g._protokoll_was = "Layout zurückgegeben"
    flash("Zurückgegeben. Der Kunde sieht deinen Hinweis, wenn er den Link öffnet.", "success")
    return _back(event_id)


@bp.post("/admin/events/<int:event_id>/layout/sperren")
def admin_lock(event_id: int):
    event, r = _admin_event(event_id, need_switch=False)
    if r:
        return r
    if lf.revoke(event_id):
        g._protokoll_was = "Layout-Link gesperrt"
        flash("Link gesperrt. Er zeigt ab jetzt nur noch „nicht gültig“.", "success")
    return _back(event_id)


@bp.post("/admin/events/<int:event_id>/layout/zuweisung-aufheben")
def admin_unassign(event_id: int):
    event, r = _admin_event(event_id, need_switch=False)
    if r:
        return r
    if lf.unassign(event_id):
        g._protokoll_was = "Kundenlayout von der Box genommen"
        flash("Die Box bietet bei diesem Event wieder die üblichen Vorlagen an.", "success")
    return _back(event_id)


@bp.get("/admin/events/<int:event_id>/layout/vorschau.jpg")
def admin_preview(event_id: int):
    from app.blueprints.admin import _require_admin
    r = _require_admin()
    if r:
        return r
    event = db.get_event_by_id(event_id)
    inv = lf.for_event(event_id) if event else None
    if not inv or not (inv.get("values") or {}).get("template"):
        abort(404)
    try:
        if inv["status"] == "freigegeben" and inv.get("result_file"):
            daten = lf.bounded(inv["id"], lambda: lf.result(inv, event))
        else:
            daten = lf.bounded(inv["id"], lambda: lf.preview(inv, inv["values"], event))
    except lf.Busy:
        abort(429)
    except ValueError:
        abort(404)
    return Response(daten, mimetype="image/jpeg", headers={"Cache-Control": "no-store"})


def panel(event) -> dict:
    """What the event page's card shows (a Jinja global)."""
    inv = lf.for_event(event["id"])
    from app import ecosystem
    zugewiesen = (ecosystem.event_profile_overrides(event).get("collage_templates") or [])
    angebot = lf.own_templates()
    from app import vorlagen
    namen = {e["asset"]["file"]: str(e["template"].get("name") or e["asset"]["file"])
             for e in vorlagen.eigene(lf._assets_dir())}
    return {
        "inv": inv,
        "live": bool(inv and lf.live(inv)),
        "expired": bool(inv and lf.expired(inv)),
        "templates": angebot,
        "template_names": namen,
        "fields": lf.DEFAULT_FIELDS,
        "palettes": [(p, lf.PALETTE_LABELS.get(p, p)) for p in lf.PALETTE_LABELS],
        "fonts": list(lf.font_choices().items()),
        "default_days": lf.DEFAULT_DAYS,
        "assigned": [namen.get(f, f) for f in zugewiesen],
        "has_logo": bool(inv and lf.logo_path(inv)),
        "status_label": STATUS_LABELS,
    }


STATUS_LABELS = {"offen": "Link erzeugt, noch nichts gespeichert",
                 "entwurf": "Kunde arbeitet daran",
                 "eingereicht": "Wartet auf deine Freigabe",
                 "freigegeben": "Freigegeben",
                 "zurueck": "Zurückgegeben, Kunde ist dran"}
