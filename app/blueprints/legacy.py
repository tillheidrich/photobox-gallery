"""Old addresses of a moved gallery service: ``/<account>/<id>``. Rules in app/legacy.py."""
from __future__ import annotations

from flask import Blueprint, abort, flash, redirect, request, url_for

from app import db, legacy
from app.i18n import t

bp = Blueprint("legacy", __name__)


@bp.get("/<account:konto>/<foreign_id:kennung>")
def old_address(konto: str, kennung: str):
    event = legacy.event_by_id(kennung)
    if not event:
        abort(404)
    target = url_for("public.gallery", slug=event["slug"])
    file_id = (request.args.get("file") or "").strip()
    if file_id and legacy.ID_RE.match(file_id):
        m = legacy.media_by_id(int(event["id"]), file_id)
        if m:
            target += f"#photo-{m['id']}"
    # 302, not 301: a browser keeps a 301 forever, and an id that was set
    # wrong in the import must be fixable.
    return redirect(target, 302)


def _guard():
    from app.blueprints.admin import _check_csrf, _require_admin
    r = _require_admin()
    if r:
        return r
    _check_csrf()
    return None


@bp.post("/admin/events/<int:event_id>/legacy-id")
def admin_event_id(event_id: int):
    r = _guard()
    if r:
        return r
    if not db.get_event_by_id(event_id):
        abort(404)
    try:
        legacy.set_event_id(event_id, request.form.get("legacy_id", ""))
        flash(t("client.admin.saved"), "success")
    except ValueError:
        flash(t("legacy.admin.id_refused"), "error")
    return redirect(url_for("admin.edit_event", event_id=event_id) + "#ordner")


@bp.post("/admin/clients/account")
def admin_account():
    r = _guard()
    if r:
        return r
    try:
        legacy.set_account(request.form.get("legacy_account", ""))
        flash(t("client.admin.saved"), "success")
    except ValueError:
        flash(t("legacy.admin.account_refused"), "error")
    return redirect(url_for("clients.admin_clients") + "#umzug")
