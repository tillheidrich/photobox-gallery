"""Admin routes for folders (sub-galleries). The rules live in app/folders.py."""
from __future__ import annotations

from flask import Blueprint, abort, flash, redirect, request, url_for

from app import db, folders
from app.i18n import t

bp = Blueprint("folders", __name__)


def _guard():
    from app.blueprints.admin import _check_csrf, _require_admin
    r = _require_admin()
    if r:
        return r
    _check_csrf()
    return None


@bp.post("/admin/events/<int:event_id>/folders")
def admin_create_folder(event_id: int):
    r = _guard()
    if r:
        return r
    if not db.get_event_by_id(event_id):
        abort(404)
    name = (request.form.get("name") or "").strip()
    try:
        child = folders.create_child(event_id, name)
    except ValueError:
        flash(t("folder.admin.refused") if name else t("client.admin.name_required"), "error")
        return redirect(url_for("admin.edit_event", event_id=event_id) + "#ordner")
    flash(t("folder.admin.created", name=name), "success")
    return redirect(url_for("admin.edit_event", event_id=child))


@bp.post("/admin/events/<int:event_id>/parent")
def admin_move_folder(event_id: int):
    r = _guard()
    if r:
        return r
    if not db.get_event_by_id(event_id):
        abort(404)
    raw = (request.form.get("parent_id") or "").strip()
    try:
        folders.set_parent(event_id, int(raw) if raw.isdigit() else None)
        flash(t("folder.admin.moved"), "success")
    except ValueError:
        flash(t("folder.admin.refused"), "error")
    return redirect(url_for("admin.edit_event", event_id=event_id) + "#ordner")


@bp.post("/admin/events/<int:event_id>/cover")
def admin_set_cover(event_id: int):
    """The cover is one of the gallery's own pictures, nothing else."""
    r = _guard()
    if r:
        return r
    raw = (request.form.get("media_id") or "").strip()
    row = db.get_media(int(raw)) if raw.isdigit() else None
    if not db.get_event_by_id(event_id) or not row or int(row["event_id"]) != int(event_id):
        abort(404)
    rel = row.get("path_webp") or row.get("path_thumb")
    if not rel:
        abort(404)
    db.update_event(event_id, cover_path=rel)
    flash(t("cover.set"), "success")
    return redirect(url_for("admin.edit_event", event_id=event_id) + "#fotos")


def panel(event: dict) -> dict:
    """What the event page shows about folders (a Jinja global)."""
    parent = folders.parent_of(event)
    children = folders.children_of(int(event["id"]))
    candidates = []
    if not children:
        candidates = [e for e in (db.list_events() or [])
                      if not e.get("parent_id") and int(e["id"]) != int(event["id"])]
        candidates.sort(key=lambda e: e["name"].lower())
    return {"parent": parent, "children": children, "candidates": candidates}
