"""Admin routes: dashboard, events, settings."""
from __future__ import annotations

import hashlib
import io
import json
import os
import secrets
import time
from datetime import datetime
from typing import Optional

from flask import (
    Blueprint, Response, abort, current_app, flash, g, redirect, render_template,
    request, send_file, session, url_for, jsonify
)
from werkzeug.security import generate_password_hash, check_password_hash

from app import branding, db, media
from app.i18n import t

bp = Blueprint("admin", __name__)

@bp.before_request
def _set_lang():
    from flask import g, session
    g.lang = session.get("lang", "de")



# ─── Auth helpers ─────────────────────────────────────────────

def _is_admin() -> bool:
    return session.get("admin_logged_in") is True


@bp.app_context_processor
def _login_page_name():
    """The gallery's own name for the login pages ("" when none is set).

    A login page that says "Admin" and suggests the user name "admin" tells a
    stranger half of what to type. A callable, so pages that do not show it pay
    nothing; app-wide, because the admin layout is rendered by other
    blueprints too (booth profiles, sharing, clients).
    """
    def gallery_name() -> str:
        from app import ecosystem
        try:
            return ecosystem.gallery_display_name()
        except Exception:                      # noqa: BLE001 — a login page must render
            return ""
    return {"login_name": gallery_name}


def _require_admin():
    if not _is_admin():
        return redirect(url_for("admin.login"))
    return None


def _hash_password(pw: str) -> str:
    return generate_password_hash(pw)


def _check_password(pw: str, stored: str) -> bool:
    """Support both werkzeug pbkdf2 hashes and legacy sha256 hex hashes."""
    if stored.startswith("pbkdf2:") or stored.startswith("scrypt:"):
        return check_password_hash(stored, pw)
    # Legacy SHA256 fallback
    return hashlib.sha256(pw.encode()).hexdigest() == stored


def _check_csrf() -> None:
    """Formular-Absender prüfen — ohne gültiges Zeichen wird nichts geändert."""
    import hmac
    gesendet = str(request.form.get("csrf_token") or "")
    if not session.get("csrf_token") or not hmac.compare_digest(gesendet, str(session["csrf_token"])):
        abort(400)


def _csrf_token() -> str:
    if "csrf_token" not in session:
        session["csrf_token"] = secrets.token_hex(24)
    return session["csrf_token"]


def _default_text(key: str, lang: str) -> str:
    """Der mitgelieferte Text — steht als Platzhalter im Formular."""
    from app.i18n import _lookup
    return _lookup(key, lang) or ""


def _inject_globals():
    g.branding_logo = bool(db.get_setting("branding_logo_path") or "")


@bp.before_request
def before_request():
    _inject_globals()
    return _csrf_wache()


def _csrf_wache():
    """Jede schreibende Anfrage an die Verwaltung braucht das Formular-Zeichen.

    ``admin_booth`` hat diese Wache seit jeher, ``sharing`` prüft in jeder
    Route einzeln — ``admin.py`` tat bis zum 19.09.2026 beides nicht. Die
    Vorlagen rendern das Feld überall, ausgewertet wurde es an zwei von
    fünfunddreißig Stellen, darunter nicht beim Kennwortwechsel und nicht beim
    Löschen eines Events.

    Dass das ``SameSite=Lax``-Cookie heutige Browser bei einem fremden POST
    ohnehin zurückhält, macht die Prüfung nicht überflüssig: Sie ist die
    Schranke, die nicht davon abhängt, was der Browser tut.
    """
    import hmac

    if request.method not in ("POST", "PUT", "PATCH", "DELETE"):
        return None
    gesendet = request.form.get("csrf_token") or request.headers.get("X-CSRF-Token") or ""
    erwartet = session.get("csrf_token")
    if not erwartet or not hmac.compare_digest(str(gesendet), str(erwartet)):
        abort(400)
    return None


def _format_date(dt_str: Optional[str]) -> str:
    if not dt_str:
        return ""
    try:
        dt = datetime.fromisoformat(dt_str[:19])
        return dt.strftime("%d.%m.%Y")
    except Exception:
        return dt_str[:10] if dt_str else ""


def _human_bytes(num: int) -> str:
    step = 1024.0
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if num < step:
            return f"{num:.0f} {unit}" if unit == "B" else f"{num:.1f} {unit}"
        num /= step
    return f"{num:.1f} PB"


def _event_cover_url(event) -> Optional[str]:
    rel = event.get("cover_path") or event.get("cover_original_path")
    return f"/data/{rel}" if rel else None


def _sharepic_event_bild(request, slug: str, alt_pfad: str) -> str:
    """Eigenes Hintergrundbild eines Events: speichern, behalten oder löschen."""
    if request.form.get("sp_remove_bg"):
        if alt_pfad:
            media.delete_rel_path(alt_pfad)
        return ""
    datei = request.files.get("sp_bg_image")
    if not datei or not datei.filename:
        return alt_pfad
    from PIL import Image, ImageOps
    ziel = media.branding_dir() / f"sharepic-bg-{slug}.jpg"
    with Image.open(datei.stream) as im:
        im = ImageOps.exif_transpose(im).convert("RGB")
        im = ImageOps.fit(im, (1080, 1920), Image.Resampling.LANCZOS)
        im.save(ziel, "JPEG", quality=88, optimize=True)
    return ziel.relative_to(media.data_dir()).as_posix()


def _slugify(text: str) -> str:
    import re
    text = text.lower()
    text = re.sub(r'[äöüß]', lambda m: {'ä':'ae','ö':'oe','ü':'ue','ß':'ss'}[m.group()], text)
    text = re.sub(r'[^a-z0-9]+', '-', text)
    return text.strip('-')[:60]


# ─── Login / Logout ───────────────────────────────────────────

# Brakes on the doors into the admin. Only failures count (recovery counts
# every attempt), so an operator who logs in correctly all evening is never
# locked out; a correct answer wipes the slate. The counter lives in the
# database so every gunicorn worker sees the same number.
LOGIN_LIMIT, LOGIN_WINDOW = 10, 15 * 60
#: Failures for the one admin account from all addresses together (S-16).
#: Higher than the per-address limit so a typo-prone admin is not the one
#: braked; low enough that spreading guesses over many addresses stops working.
ACCOUNT_LIMIT = 30
ACCOUNT_KEY = "login-account"
CODE_LIMIT, CODE_WINDOW = 5, 15 * 60
RECOVER_LIMIT, RECOVER_WINDOW = 5, 60 * 60


def _login_key() -> str:
    # ProxyFix in app/__init__.py makes remote_addr the client, not the proxy.
    return f"login:{request.remote_addr}"


def _after_login():
    """Where a fresh login goes: back to an OAuth consent that sent us here, else the dashboard.

    Only that one path is accepted. A "next" parameter that takes any address
    is how login pages become open redirects.
    """
    target = str(session.pop("after_login", "") or "")
    if target.startswith("/oauth/authorize?") and "\\" not in target and "//" not in target.split("?", 1)[0]:
        return redirect(target)
    # The second exact path: confirming a booth's pairing request from the phone.
    import re
    if re.fullmatch(r"/admin/devices/pair\?c=[A-Z0-9]{8}", target):
        return redirect(target)
    return redirect(url_for("admin.dashboard"))


@bp.route("/login", methods=["GET", "POST"])
def login():
    if _is_admin():
        return redirect(url_for("admin.dashboard"))
    error = None
    if request.method == "POST":
        from app import sharelinks

        wait = (sharelinks.retry_after(_login_key(), LOGIN_LIMIT, LOGIN_WINDOW)
                or sharelinks.retry_after(ACCOUNT_KEY, ACCOUNT_LIMIT, LOGIN_WINDOW))
        if wait:
            return render_template("admin/login.html", error=sharelinks.wait_message(wait),
                                   csrf_token=_csrf_token(), setup_available=False,
                                   second_factor=False), 429, {"Retry-After": str(wait)}
        username = request.form.get("username", "")
        password = request.form.get("password", "")
        stored_user = db.get_setting("admin_username") or "admin"
        stored_hash = db.get_setting("admin_password_hash") or ""
        if username == stored_user and _check_password(password, stored_hash):
            from app import accounts, totp as totp_mod

            sharelinks.forget(_login_key())
            sharelinks.forget(ACCOUNT_KEY)
            if (db.get_setting("admin_totp_enabled") or "") == "1":
                # Zweiter Faktor: Das Passwort allein reicht dann nicht mehr.
                # Bis der Code stimmt, gilt niemand als angemeldet.
                session["pending_admin"] = True
                session["admin_epoch"] = accounts.session_epoch()
                session.permanent = True
                return render_template("admin/login.html", error=None, csrf_token=_csrf_token(),
                                       setup_available=False, second_factor=True)
            session["admin_logged_in"] = True
            session["admin_epoch"] = accounts.session_epoch()
            session["admin_sid"] = accounts.open_admin_session()      # S-9: revocable on logout
            session.permanent = True
            if accounts.password_is_temporary():
                # Das Startpasswort stand einmal im Protokoll des Servers.
                # Es darf nicht das bleiben, mit dem man arbeitet.
                flash("Bitte jetzt ein eigenes Passwort setzen — das Startpasswort stand im Protokoll.", "warning")
                return redirect(url_for("admin.settings"))
            return _after_login()
        sharelinks.record_failure(_login_key(), LOGIN_WINDOW)
        sharelinks.record_failure(ACCOUNT_KEY, LOGIN_WINDOW)
        error = "Ungültige Zugangsdaten"
    # The wizard is admin-only (see public.setup); the login page must not
    # send strangers to a page that answers 404.
    return render_template("admin/login.html", error=error, csrf_token=_csrf_token(),
                           setup_available=False, second_factor=False)


@bp.post("/login/code")
def login_code():
    """Zweiter Schritt: der Code aus der Authenticator-App."""
    from app import totp as totp_mod

    from app import sharelinks

    if not session.get("pending_admin"):
        return redirect(url_for("admin.login"))
    # One key for the whole installation, not per address: only someone who
    # already has the password gets here, and six digits must not be guessable
    # by spreading the attempts over many addresses.
    key = "totp"
    wait = sharelinks.retry_after(key, CODE_LIMIT, CODE_WINDOW)
    if wait:
        return render_template("admin/login.html", error=sharelinks.wait_message(wait),
                               csrf_token=_csrf_token(), setup_available=False,
                               second_factor=True), 429, {"Retry-After": str(wait)}
    secret = db.get_setting("admin_totp_secret") or ""
    last = int(db.get_setting("admin_totp_last_step") or -1)
    ok, step = totp_mod.verify(secret, request.form.get("code", ""), last_step=last)
    if not ok:
        sharelinks.record_failure(key, CODE_WINDOW)
        time.sleep(0.5)
        return render_template("admin/login.html", error="Der Code stimmt nicht.",
                               csrf_token=_csrf_token(), setup_available=False, second_factor=True)
    sharelinks.forget(key)
    # Benutzter Schritt wird vermerkt: Ein abgelesener Code funktioniert kein
    # zweites Mal, auch nicht innerhalb derselben halben Minute.
    db.set_setting("admin_totp_last_step", str(step))
    session.pop("pending_admin", None)
    session["admin_logged_in"] = True
    from app import accounts as _accounts
    session["admin_epoch"] = _accounts.session_epoch()
    session["admin_sid"] = _accounts.open_admin_session()          # S-9: revocable on logout
    session.permanent = True
    from app import accounts
    if accounts.password_is_temporary():
        flash("Bitte jetzt ein eigenes Passwort setzen — das Startpasswort stand im Protokoll.", "warning")
        return redirect(url_for("admin.settings"))
    return _after_login()


# ── Zwei-Faktor einrichten ───────────────────────────────────
@bp.post("/settings/totp/start")
def totp_start():
    """Geheimnis erzeugen und anzeigen — scharf wird es erst mit einem Code."""
    redir = _require_admin()
    if redir:
        return redir
    from app import totp as totp_mod

    session["totp_pending"] = totp_mod.new_secret()
    return redirect(url_for("admin.settings") + "#zwei-faktor")


@bp.post("/settings/totp/confirm")
def totp_confirm():
    redir = _require_admin()
    if redir:
        return redir
    from app import totp as totp_mod

    secret = session.get("totp_pending") or ""
    ok, step = totp_mod.verify(secret, request.form.get("code", ""))
    if not ok:
        flash("Der Code stimmt nicht — bitte den aktuellen aus der App eingeben.", "error")
        return redirect(url_for("admin.settings") + "#zwei-faktor")
    db.set_setting("admin_totp_secret", secret)
    db.set_setting("admin_totp_enabled", "1")
    db.set_setting("admin_totp_last_step", str(step))
    session.pop("totp_pending", None)
    flash("Zwei-Faktor ist an. Ohne Code aus der App kommt jetzt niemand mehr in die Verwaltung — "
          "der Wiederherstellungscode bleibt dein Weg zurück, falls das Telefon weg ist.", "success")
    return redirect(url_for("admin.settings") + "#zwei-faktor")


@bp.post("/settings/totp/off")
def totp_off():
    redir = _require_admin()
    if redir:
        return redir
    from app import totp as totp_mod

    secret = db.get_setting("admin_totp_secret") or ""
    ok, _ = totp_mod.verify(secret, request.form.get("code", ""), last_step=-1)
    if not ok:
        flash("Zum Ausschalten braucht es einen gültigen Code — sonst könnte es jeder, der kurz an deinem "
              "offenen Rechner sitzt.", "error")
        return redirect(url_for("admin.settings") + "#zwei-faktor")
    db.set_setting("admin_totp_enabled", "")
    db.set_setting("admin_totp_secret", "")
    flash("Zwei-Faktor ausgeschaltet.", "success")
    return redirect(url_for("admin.settings") + "#zwei-faktor")


@bp.get("/settings/totp/qr.png")
def totp_qr():
    """Der QR-Code für die Authenticator-App — nur solange die Einrichtung läuft."""
    redir = _require_admin()
    if redir:
        return redir
    from app import totp as totp_mod

    secret = session.get("totp_pending") or ""
    if not secret:
        abort(404)
    import qrcode

    account = (db.get_setting("admin_username") or "admin") + "@" + (
        (db.base_url() or request.host_url).replace("https://", "").replace("http://", "").strip("/"))
    img = qrcode.make(totp_mod.provisioning_uri(secret, account))
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return Response(buf.getvalue(), mimetype="image/png", headers={"Cache-Control": "no-store"})


@bp.get("/logout")
def logout():
    # S-9: end it on the server too — a copy of the cookie must not outlive the logout.
    from app import accounts
    accounts.end_admin_session(session.get("admin_sid") or "")
    for key in ("admin_logged_in", "admin_sid", "pending_admin"):
        session.pop(key, None)
    return redirect(url_for("admin.login"))


@bp.get("/")
def index():
    return redirect(url_for("admin.dashboard"))


# ─── Dashboard ────────────────────────────────────────────────

@bp.get("/dashboard")
def dashboard():
    redir = _require_admin()
    if redir:
        return redir
    from app import ecosystem, gallery_settings, schedule, storage_stats
    ecosystem.init_schema()
    all_events = db.list_events() or []
    # Bytes kommen als eine Summenabfrage aus der DB, nicht aus dem Dateibaum.
    overview = storage_stats.overview()
    pending = ecosystem.pending_counts()
    device_errors = ecosystem.recent_error_devices(since_hours=24)
    total_photos = 0
    events_data = []
    names = {int(e["id"]): e["name"] for e in all_events}
    from app import retention as retention_mod
    for ev in all_events:
        photos = db.list_media(ev["id"]) or []
        photo_count = len(photos)
        total_photos += photo_count
        events_data.append({
            "id":          ev["id"],
            "name":        ev["name"],
            "slug":        ev["slug"],
            "date":        _format_date(ev.get("date") or ev.get("starts_at")),
            "cover_url":   _event_cover_url(ev),
            "photo_count": photo_count,
            "published":   ev.get("published") or ev.get("is_published"),
            "state":       schedule.state(ev, gallery_settings.for_event(ev)),
            "pending":     pending.get(int(ev["id"]), 0),
            "parent_name": names.get(int(ev.get("parent_id") or 0), ""),
            "fate":        retention_mod.info(ev),
        })
    events_data.sort(key=lambda e: e.get("date") or "", reverse=True)
    active_devices = len(db.list_devices() or [])
    stats = {
        "total_events":  len(all_events),
        "total_photos":  total_photos,
        "storage":       _human_bytes(overview["totals"]["total_bytes"]),
        "active_devices": active_devices,
    }
    from app import backup, maintenance, silence_alarm
    try:
        silence_lines = silence_alarm.dashboard_lines()
    except Exception:                                 # noqa: BLE001 — a line costs no dashboard
        current_app.logger.exception("Funkstille-Zeilen nicht lesbar")
        silence_lines = []
    from app import legal
    try:
        legal_status = legal.status()
    except Exception:                                 # noqa: BLE001 — a warning costs no dashboard
        current_app.logger.exception("Rechtstexte nicht prüfbar")
        legal_status = {}
    return render_template("admin/dashboard.html",
                           stats=stats, events=events_data,
                           acked_devices=ecosystem.acknowledged_error_devices(since_hours=24),
                           backup_now=maintenance.backup_now_status(),
                           legal_status=legal_status,
                           storage=overview,
                           device_errors=device_errors,
                           silence_lines=silence_lines,
                           maintenance=maintenance.status(),
                           backup_status=backup.status(),
                           csrf_token=_csrf_token())


@bp.post("/devices/<int:device_id>/logs/ack")
def device_logs_ack(device_id: int):
    """"Als gelesen markieren": errors up to now stop counting; the lines stay."""
    redir = _require_admin()
    if redir:
        return redir
    from app import ecosystem
    ecosystem.init_schema()
    if not db.get_device_by_id(device_id):
        abort(404)
    ecosystem.acknowledge_logs(device_id)
    flash(t("admin.logs.acked_flash"), "success")
    back = request.form.get("back", "")
    if back == "logs":
        return redirect(url_for("admin.device_logs", device_id=device_id))
    if back == "booths":
        return redirect(url_for("admin.booths"))
    return redirect(url_for("admin.dashboard") + "#device-errors")


@bp.post("/backup/now")
def backup_now():
    """"Sicherung jetzt anlegen": the nightly backup function, once, in the background."""
    redir = _require_admin()
    if redir:
        return redir
    from app import maintenance
    outcome = maintenance.start_backup_now(current_app._get_current_object())
    flash(t(f"admin.backup.now_{outcome}"), "warning" if outcome == "off" else "success")
    return redirect(url_for("admin.dashboard") + "#backup-status")


# ─── Events ───────────────────────────────────────────────────

@bp.get("/events")
def events():
    redir = _require_admin()
    if redir:
        return redir
    from app import ecosystem, gallery_settings, redirects, schedule, storage_stats
    ecosystem.init_schema()
    all_events = db.list_events() or []
    usage = storage_stats.usage_by_event()
    pending = ecosystem.pending_counts()
    events_data = []
    for ev in all_events:
        photos = db.list_media(ev["id"]) or []
        gs = gallery_settings.for_event(ev)
        events_data.append({
            "id":          ev["id"],
            "name":        ev["name"],
            "slug":        ev["slug"],
            "date":        _format_date(ev.get("date") or ev.get("starts_at")),
            "cover_url":   _event_cover_url(ev),
            "photo_count": len(photos),
            "published":   ev.get("published") or ev.get("is_published"),
            "no_password": not ev.get("password_hash"),
            "kind":        ev.get("kind") or "booth",
            "featured":    bool(ev.get("featured")),
            "state":       schedule.state(ev, gs),
            "live_at":     (ev.get("scheduled_live_at") or "")[:16].replace("T", " "),
            "expires_at":  (gs["expires_at"] or "")[:16].replace("T", " "),
            "pending":     pending.get(int(ev["id"]), 0),
            "redirect_host": redirects.host(ev.get("redirect_url")),
            "storage_h":   _human_bytes(int((usage.get(int(ev["id"])) or {}).get("total_bytes", 0))),
        })
    events_data.sort(key=lambda e: e.get("date") or "", reverse=True)
    return render_template("admin/events/list.html",
                           events=events_data,
                           csrf_token=_csrf_token())


def _frame_store():
    from app import frame_store
    return frame_store


def _motion_offer() -> dict:
    from app.blueprints.admin_booth import _assets_dir
    return _frame_store().offer(_assets_dir())


def _layout_offer() -> dict:
    from app import layout_wahl
    from app.blueprints.admin_booth import _assets_dir
    return layout_wahl.offer(_assets_dir())


@bp.get("/events/<int:event_id>/layouts/<kind>/<path:ident>.png")
def event_layout_picture(event_id: int, kind: str, ident: str):
    """A layout as the booth would print it, with sample photos (app/layout_wahl.py).

    Rendered here with the shared renderer and cached; the event's own style
    if it has one, else the house style "clean".
    """
    redir = _require_admin()
    if redir:
        return redir
    event = db.get_event_by_id(event_id)
    if not event or kind not in ("t", "b"):
        abort(404)
    from app import ecosystem, layout_wahl
    from app.blueprints.admin_booth import _assets_dir
    stil = ecosystem.event_profile_overrides(event).get("collage_style") or {"preset": "clean"}
    png = layout_wahl.picture(kind, ident, _assets_dir(), stil)
    if png is None:
        abort(404)
    return Response(png, mimetype="image/png", headers={"Cache-Control": "private, max-age=3600"})


def _retention_default_days() -> int:
    from app import retention
    return retention.default_days()


@bp.route("/events/new", methods=["GET", "POST"])
def new_event():
    redir = _require_admin()
    if redir:
        return redir
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        if not name:
            flash("Name ist Pflichtfeld", "error")
            return redirect(url_for("admin.new_event"))
        slug = request.form.get("slug", "").strip() or _slugify(name)
        date = request.form.get("date") or None
        description = request.form.get("description", "").strip()
        published = bool(request.form.get("published"))
        password = request.form.get("password", "").strip()
        from app import retention as _ret
        retention_days = _ret.read_days(request.form.get("retention_days")) or _ret.default_days()
        kiosk_text = request.form.get("kiosk_text", "").strip()
        try:
            event_id = db.create_event(
                name=name,
                slug=slug,
                password=password,
                retention_days=retention_days,
                upload_token=secrets.token_urlsafe(32),
                status="live" if published else "draft",
            )
            # Set extra fields after creation
            extra: dict = {}
            if date:
                extra["date"] = date
            if description:
                extra["description"] = description
            if kiosk_text:
                extra["kiosk_text"] = kiosk_text
            # The column defaults to 1: "draft" has to be written, or a new
            # event with the switch off was live anyway (found 17.11.2026).
            extra["published"] = 1 if published else 0
            # Photo booth event (deleted after its period) or a photographer's
            # client gallery (stays until deleted by hand) - app/retention.py.
            extra["kind"] = "client" if request.form.get("gallery_kind") == "client" else "booth"
            # Account default "Schlichte Galerie für neue Events" (not for client galleries).
            from app import gallery_settings as _gsm
            start = _gsm.new_event_defaults(extra["kind"])
            if start:
                extra["gallery_json"] = json.dumps(_gsm.clean(start))
            db.update_event(event_id, extra)
            flash(f"Event '{name}' erstellt!", "success")
            return redirect(url_for("admin.edit_event", event_id=event_id))
        except Exception as e:
            flash(f"Fehler: {e}", "error")
    from app import ecosystem as ecosystem_mod
    return render_template("admin/events/edit.html",
                           event=None, photos=[],
                           sharepic_event={},          # neues Event: überall „wie global"
                           booth_event={}, builtin_collages=ecosystem_mod.BUILTIN_COLLAGES,
                           default_retention_days=_retention_default_days(),
                           csrf_token=_csrf_token())


@bp.route("/events/<int:event_id>/edit", methods=["GET", "POST"])
def edit_event(event_id: int):
    redir = _require_admin()
    if redir:
        return redir
    event = db.get_event_by_id(event_id)
    if not event:
        abort(404)
    if request.method == "POST":
        action = request.form.get("action", "save")
        if action == "delete_cover":
            db.update_event(event_id, cover_path=None, cover_original_path=None)
            flash("Cover entfernt", "success")
            return redirect(url_for("admin.edit_event", event_id=event_id))
        name = request.form.get("name", "").strip()
        slug = request.form.get("slug", "").strip() or event.get("slug")
        date = request.form.get("date") or None
        description = request.form.get("description", "").strip()
        published = bool(request.form.get("published"))
        # The field is never filled in (only a hash is stored): empty keeps
        # the stored password. Before 17.11.2026 every save of this form
        # removed it. Removing is the checkbox, a typed password wins over it.
        password = request.form.get("password", "").strip()
        remove_password = bool(request.form.get("remove_password"))
        # 0, a negative number or text keeps the period that was there: "no
        # automatic deletion" is the switch below, never a period of 0 days.
        from app import retention as _ret_days
        retention_days = (_ret_days.read_days(request.form.get("retention_days"))
                          or _ret_days.read_days(event.get("retention_days")) or _ret_days.default_days())
        kiosk_text = request.form.get("kiosk_text", "").strip()
        # Die Adresse der Galerie gehört dem Betreiber: Sie darf jederzeit
        # heißen, wie der Kunde sie nennt. Was der Betreiber tippt, wird
        # zurechtgeschliffen (Großbuchstaben, Umlaute, Leerzeichen) — sonst
        # entsteht eine Adresse, die kein QR-Code sauber trägt.
        old_slug = event.get("slug")
        if slug and slug != old_slug:
            slug = _slugify(slug) or old_slug
        slug_changed = bool(slug) and slug != old_slug
        eigener_alias = slug_changed and slug in db.list_slug_aliases(event_id)
        if slug_changed and db.slug_taken(slug) and not eigener_alias:
            flash("Diese Adresse ist schon vergeben — die alte bleibt stehen.", "error")
            slug, slug_changed = old_slug, False
        # Kind and deletion switch (app/retention.py). The switch is stored
        # only where it differs from what the kind would do anyway.
        from app import retention as _ret
        kind = "client" if request.form.get("gallery_kind") == "client" else "booth"
        if not event.get("parent_id"):
            db.update_event(event_id, kind=kind)
        want = bool(request.form.get("auto_delete"))
        default = not _ret.protected({**event, "kind": kind, "auto_delete": None})
        db.update_event(event_id, auto_delete=None if want == default else (1 if want else 0))
        db.update_event(event_id,
                        name=name, slug=slug, date=date,
                        description=description, retention_days=retention_days,
                        kiosk_text=kiosk_text)
        if password:
            db.update_event(event_id, password=password)
        elif remove_password:
            db.update_event(event_id, password="")
        if slug_changed:
            # Weiterleiten ist der Standard — gedruckte QR-Codes und Links in
            # WhatsApp-Gruppen laufen sonst ins Leere. Wer das nicht will
            # (die alte Adresse soll wirklich weg sein), nimmt den Haken raus.
            if eigener_alias:
                # Zurück auf einen früheren Namen: Der Alias wird wieder die Adresse.
                db.delete_slug_alias(event_id, slug)
            if request.form.get("drop_old_slug"):
                flash(f"Neue Adresse: /gallery/{slug} — /gallery/{old_slug} führt ab jetzt ins Leere.", "warning")
            else:
                db.add_slug_alias(event_id, old_slug)
                flash(f"Neue Adresse: /gallery/{slug} — /gallery/{old_slug} leitet weiter.", "success")

        # ── Zeitplan: Start und Ablauf an einer Stelle ───────────────
        # „Ab wann" steht in der events-Spalte, „bis wann" in den
        # Galerie-Einstellungen. Das Formular kennt nur ein Feldpaar.
        from app import gallery_settings as gsm, schedule
        start_raw = (request.form.get("scheduled_live_at") or "").strip()
        start = schedule.parse_dt(start_raw)
        if start_raw and not start:
            flash(t("msg.schedule.start_invalid"), "warning")
        else:
            db.update_event(event_id, scheduled_live_at=start.isoformat(timespec="minutes") if start else None)

        gs = gsm.for_event(event)
        expires_raw = (request.form.get("expires_at") or "").strip()
        if expires_raw and not schedule.parse_dt(expires_raw):
            flash(t("msg.schedule.expiry_invalid"), "warning")
        else:
            gs["expires_at"] = expires_raw
            db.update_event(event_id, gallery_json=gsm.store(event, gs))
        # Handle cover upload
        cover_file = request.files.get("cover")
        if cover_file and cover_file.filename:
            try:
                cover_rel = media.save_cover(event_id, cover_file)
                db.update_event(event_id, cover_path=cover_rel)
            except Exception as e:
                flash(f"Cover-Fehler: {e}", "warning")

        # Link preview (app/link_preview.py): own image, and whether a closed
        # gallery may show it. The checkbox lives in this form, so unticked
        # here means off.
        if request.form.get("og_form"):
            og_fields: dict = {"og_image_public": 1 if request.form.get("og_image_public") else 0}
            og_file = request.files.get("og_image")
            if request.form.get("remove_og_image"):
                if event.get("og_image_path"):
                    media.delete_rel_path(event["og_image_path"])
                og_fields["og_image_path"] = None
            elif og_file and og_file.filename:
                try:
                    og_fields["og_image_path"] = media.save_og_image(slug, og_file)
                except Exception:           # noqa: BLE001 - Pillow names the reason badly
                    flash(t("msg.og.refused"), "warning")
            db.update_event(event_id, og_fields)

        # ── Eigene Texte für dieses Event ───────────────────────────
        # Leeres Feld heißt „Übersetzung benutzen", nicht „leerer Text" —
        # sonst stünde nach einem versehentlich geleerten Feld nichts da.
        from app import texts as texts_mod
        collected: dict = {}
        for lang in ("de", "en"):
            for key, _label in texts_mod.KEYS:
                value = (request.form.get(f"text_{lang}_{key}") or "").strip()
                if value:
                    collected.setdefault(lang, {})[key] = value
        extra_raw = (request.form.get("texts_extra_json") or "").strip()
        if extra_raw:
            extra = texts_mod.parse(extra_raw)
            if not extra:
                flash("Zusätzliche Texte: ungültiges JSON, ignoriert.", "warning")
            for lang, values in extra.items():
                collected.setdefault(lang, {}).update(values)
        db.update_event(event_id, texts_json=texts_mod.dump(collected))

        # ── Branding pro Event (Farben, Schrift, Logo, Banner) ───────
        design = branding.parse_design(event.get("design_json"))

        for field, key in (("brand_accent", "accent"), ("brand_bg", "bg"), ("brand_text", "text")):
            raw = request.form.get(field, "")
            value = branding.normalize_hex(raw)
            if raw.strip() and not value:
                flash(t("msg.brand.color_invalid"), "warning")
            if value:
                design[key] = value
            else:
                design.pop(key, None)

        font = branding.font_id(request.form.get("brand_font"))
        if font:
            design["font"] = font
        else:
            design.pop("font", None)

        if request.form.get("remove_brand_logo"):
            old_logo = design.pop("logo_path", "")
            if old_logo:
                media.delete_rel_path(old_logo)
        else:
            logo_file = request.files.get("brand_logo")
            if logo_file and logo_file.filename:
                try:
                    design["logo_path"] = media.save_event_logo(slug, logo_file)
                except Exception as e:
                    flash(f"Logo-Fehler: {e}", "warning")

        if request.form.get("remove_brand_banner"):
            old_banner = design.pop("banner_path", "")
            if old_banner:
                media.delete_rel_path(old_banner)
        else:
            banner_file = request.files.get("brand_banner")
            if banner_file and banner_file.filename:
                try:
                    design["banner_path"] = media.save_event_banner(slug, banner_file)
                except Exception as e:
                    flash(f"Banner-Fehler: {e}", "warning")

        # Sharepic je Event. Leeres Feld heißt „wie global" — deshalb wird hier
        # nur abgelegt, was der Betreiber wirklich gesetzt hat.
        from app import sharepic as sharepic_mod
        vorher = sharepic_mod.event_design(event)
        eigenes = {}
        for feld in sharepic_mod.FELDER:
            if feld == "bg_path":
                continue
            roh = (request.form.get("sp_" + feld) or "").strip()
            if roh:
                eigenes[feld] = roh
        bild = _sharepic_event_bild(request, slug, vorher.get("bg_path", ""))
        if bild:
            eigenes["bg_path"] = bild
        if eigenes:
            design["sharepic"] = sharepic_mod._sauber(eigenes)
        else:
            design.pop("sharepic", None)

        # Was die Box bei diesem Event anbietet. Leer heißt: nichts überschreiben.
        from app import ecosystem as ecosystem_mod
        boxsachen = {}
        # The form has no field for own templates; the one set there is the
        # layout a customer had approved (app/layout_freigabe.py). Saving the
        # event page must not take it off the booth.
        bisher = design.get("booth") if isinstance(design.get("booth"), dict) else {}
        if request.form.get("ev_layouts_form"):
            # "Layouts für dieses Event" (app/layout_wahl.py): ticked = exactly
            # these at the booth, nothing ticked = the box decides.
            from app import layout_wahl
            boxsachen.update(layout_wahl.from_form(request.form))
        else:
            if bisher.get("collage_templates"):
                boxsachen["collage_templates"] = bisher["collage_templates"]
            if bisher.get("collage_exact"):
                boxsachen["collage_exact"] = True
                boxsachen.setdefault("collage_templates", [])
            gewaehlt = [x for x in request.form.getlist("ev_collage_builtins")
                        if x in ecosystem_mod.BUILTIN_COLLAGE_IDS]
            if gewaehlt:
                boxsachen["collage_builtins"] = gewaehlt
            elif bisher.get("collage_exact"):
                boxsachen["collage_builtins"] = list(bisher.get("collage_builtins") or [])
        # Frames for boomerang and video at this event (app/frame_store.py):
        # ticked = exactly these at the booth, nothing ticked = the box decides.
        from app import frame_store
        if request.form.get("ev_frames_form"):
            rahmen_wahl = frame_store.event_choice_from_form(request.form)
        else:
            rahmen_wahl = bisher.get(frame_store.EVENT_KEY) if isinstance(bisher.get(frame_store.EVENT_KEY), dict) else {}
        if rahmen_wahl:
            boxsachen[frame_store.EVENT_KEY] = rahmen_wahl
        # Der Stil der Vorlagen je Event — „kein eigener" bleibt leer und lässt
        # der Box ihren Hausstil.
        ev_preset = (request.form.get("ev_cs_preset") or "").strip()
        if ev_preset in ecosystem_mod.COLLAGE_STYLE_PRESETS:
            boxsachen["collage_style"] = ecosystem_mod.clean_collage_style(
                {k: request.form.get("ev_cs_" + k, "") for k in ecosystem_mod.COLLAGE_STYLE_KEYS})
        ev_collage_upload = (request.form.get("ev_collage_upload") or "").strip()
        if ev_collage_upload in ("collage", "photos", "both"):
            boxsachen["collage_upload"] = ev_collage_upload
        modus = (request.form.get("ev_looks_mode") or "").strip()
        if modus in ("ask", "auto", "off"):
            boxsachen["looks_mode"] = modus
        grenze = (request.form.get("ev_looks_max") or "").strip()
        if grenze.isdigit() and int(grenze) > 0:
            boxsachen["looks_max"] = min(24, int(grenze))
        if boxsachen:
            design["booth"] = boxsachen
        else:
            design.pop("booth", None)

        vorher_box = (ecosystem_mod.event_profile_overrides(event), frame_store.event_choice(event))
        db.update_event(event_id, design_json=branding.dump_design(design))
        # The gallery sends a box its profile only when the profile version
        # changes. What the event sets for the boxes (layouts, looks, styles)
        # has to reach the boxes on this event, so their version moves on.
        nachher = db.get_event_by_id(event_id)
        if (ecosystem_mod.event_profile_overrides(nachher), frame_store.event_choice(nachher)) != vorher_box:
            for d in db.list_devices() or []:
                if d.get("event_id") == event_id:
                    ecosystem_mod.bump_profile(d["id"])

        # Kontrast prüfen, aber nicht blockieren: gespeichert ist gespeichert.
        for warn in branding.contrast_warnings(branding.event_branding(db.get_event_by_id(event_id))):
            flash(t(f"msg.brand.contrast_{warn['kind']}", ratio=warn["ratio"], need=warn["need"]), "warning")
        # Live or draft goes through the one function every other way uses
        # (list button, page button, MCP): status column and notification too.
        from app import publishing
        publishing.set_published(event_id, published, fallback_base=request.host_url)
        flash("Gespeichert", "success")
        return redirect(url_for("admin.edit_event", event_id=event_id))

    event = db.get_event_by_id(event_id)  # re-fetch
    event["cover_url"] = _event_cover_url(event)
    photos = db.list_media(event_id) or []
    # Mehrere Boxen auf einem Event sind der Normalfall bei größeren
    # Veranstaltungen. Dann will jemand wissen, was von welcher Box kam —
    # spätestens, wenn eine davon schief stand oder ausgefallen ist.
    boxen = {int(d["id"]): d["name"] for d in (db.list_devices() or [])}
    je_box = {}
    for foto in photos:
        je_box[foto.get("device_id")] = je_box.get(foto.get("device_id"), 0) + 1
    box_filter = (request.args.get("box") or "").strip()
    if box_filter.isdigit():
        photos = [f for f in photos if str(f.get("device_id") or "") == box_filter]
    event_boxen = [{"id": bid, "name": boxen.get(bid) or f"Box {bid}", "anzahl": anzahl}
                   for bid, anzahl in sorted(je_box.items(), key=lambda x: -x[1]) if bid]
    ohne_box = je_box.get(None, 0) + je_box.get(0, 0)
    from app import access, ecosystem, emailer, gallery_settings, schedule, sharelinks, silence
    ecosystem.init_schema()
    gs = gallery_settings.for_event(event)
    brand = branding.event_branding(event)
    from app import texts as texts_mod
    event_texts = texts_mod.parse(event.get("texts_json"))
    known = {k for k, _ in texts_mod.KEYS}
    texts_extra = {lang: {k: v for k, v in values.items() if k not in known}
                   for lang, values in event_texts.items()}
    texts_extra = {lang: values for lang, values in texts_extra.items() if values}
    from app import bewertung as bewertung_mod
    from app import dateinamen as dateinamen_mod
    from app import sharepic as sharepic_mod
    return render_template("admin/events/edit.html",
                           event=event, photos=photos,
                           event_boxen=event_boxen, ohne_box=ohne_box, box_filter=box_filter,
                           brand=brand,
                           sharepic_event=sharepic_mod.event_design(event),
                           booth_event=ecosystem.event_profile_overrides(event),
                           builtin_collages=ecosystem.BUILTIN_COLLAGES,
                           layout_offer=_layout_offer(),
                           motion_offer=_motion_offer(), ev_frames=_frame_store().event_choice(event),
                           text_keys=texts_mod.KEYS, event_texts=event_texts,
                           texts_extra=json.dumps(texts_extra, ensure_ascii=False, indent=2) if texts_extra else "",
                           default_text=lambda key, lang: _default_text(key, lang),
                           font_choices=[(fid, t(f"brand.font_{fid}")) for fid in branding.FONT_IDS],
                           contrast_warnings=branding.contrast_warnings(brand),
                           gs=gs,
                           schedule_state=schedule.state(event, gs),
                           pending_count=ecosystem.pending_review_count(event_id),
                           qr_example=ecosystem.qr_url_for(
                               event, "abcd2345xy",
                               db.base_url() or request.host_url.rstrip("/")),
                           tags=gallery_settings.TAGS,
                           preset_clean=list(gallery_settings.PRESET_CLEAN),
                           preset_clean_labels="QR-Code drucken, Benachrichtigung, Sharepic, "
                                               "Teilen, Link kopieren, Likes, mit/ohne Rahmen, Meine Aufnahmen",
                           silent_global=silence.global_on(),
                           slug_aliases=db.list_slug_aliases(event_id),
                           mail_ready=emailer.smtp_configured(),
                           access_requests=access.list_open(event_id),
                           muster_platzhalter=dateinamen_mod.PLATZHALTER,
                           muster_beispiel=dateinamen_mod.BEISPIEL,
                           naming_default=dateinamen_mod.regel_fuer(None, {}),
                           naming_example=dateinamen_mod.event_datetime_name(
                               event.get("name") or "", datetime(2026, 7, 20, 21, 14, 3), "jpg"),
                           muster_vorgabe=db.get_setting("download_pattern") or "",
                           muster_vorgabe_vorschau=dateinamen_mod.vorschau(
                               db.get_setting("download_pattern") or ""),
                           muster_vorschau=dateinamen_mod.vorschau(gs.get("download_pattern") or ""),
                           muster_werte=dateinamen_mod.werte(
                               event={"slug": "sommerfest", "name": "Sommerfest 2026"},
                               medium={"created_at": "2026-07-20T21:14:03", "kind": "photo",
                                       "filename": "abcd2345xy_1.jpg", "id": 4711},
                               nummer=7, groesse="web"),
                           review_klicks=bewertung_mod.zahlen(event_id),
                           review_default_url=db.get_setting("review_url") or "",
                           review_default_text=(db.get_setting("review_text")
                                                or bewertung_mod.STANDARDTEXT),
                           share_links=sharelinks.list_for_event(event_id),
                           base_url=db.base_url() or request.host_url.rstrip("/"),
                           csrf_token=_csrf_token())


# ── Löschprotokoll ────────────────────────────────────────────
# Der Nachweis, dass gelöscht wurde: Zeitpunkt, Umfang, Grund, Veranlasser.
# Keine Bilder, keine Namen — Kennungen, Zahlen, Zeitpunkte.

@bp.get("/deletions")
def deletions():
    redir = _require_admin()
    if redir:
        return redir
    from app import retention
    retention.init_schema()
    reason = request.args.get("reason", "")
    slug = request.args.get("event", "")
    rows = retention.entries(limit=500, reason=reason, event_slug=slug)
    return render_template("admin/deletions.html", rows=rows,
                           summary=retention.summary(30), reasons=retention.REASONS,
                           reason=reason, event_slug=slug,
                           due=retention.due(), csrf_token=_csrf_token(),
                           active_nav="deletions")


@bp.get("/deletions.csv")
def deletions_csv():
    redir = _require_admin()
    if redir:
        return redir
    from app import retention
    rows = retention.entries(limit=2000, reason=request.args.get("reason", ""),
                             event_slug=request.args.get("event", ""))
    stamp = __import__("datetime").datetime.now().strftime("%Y-%m-%d")
    return Response(retention.as_csv(rows), mimetype="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="loeschprotokoll-{stamp}.csv"',
                             "Cache-Control": "no-store"})


@bp.post("/deletions/sweep")
def deletions_sweep():
    """Aufbewahrungsfristen jetzt durchsetzen — sonst tut das die geplante Aufgabe."""
    redir = _require_admin()
    if redir:
        return redir
    from app import maintenance
    report = maintenance.run()
    result = report.get("retention") or {}
    if result.get("events"):
        flash(f"{len(result['events'])} Galerie(n) geleert, {result['files']} Dateien entfernt. "
              "Die Löschungen stehen im Protokoll.", "success")
    else:
        flash("Nichts fällig — keine Galerie hat ihre Aufbewahrungsdauer überschritten.", "success")
    if report.get("remote_deletions"):
        flash(f"{report['remote_deletions']} Datei(en) auch auf dem Auslagerungsziel gelöscht.", "success")
    for key in ("offload_error", "remote_deletions_error", "retention_error"):
        if report.get(key):
            flash(f"{key.replace('_error', '')}: {report[key]}", "warning")
    return redirect(url_for("admin.deletions"))


@bp.post("/events/<int:event_id>/delete")
def delete_event(event_id: int):
    redir = _require_admin()
    if redir:
        return redir
    from app import retention
    event = db.get_event_by_id(event_id)
    # Erst die Aufnahmen samt Dateien und Beleg, dann das Event selbst.
    retention.purge_event(event_id, reason="event", actor="admin", delete_event_row=True)
    flash(f"Event gelöscht — die Löschung steht im Protokoll ({(event or {}).get('slug', '')})", "success")
    return redirect(url_for("admin.events"))


def _optional_text(raw) -> Optional[str]:
    """An optional text field of a form: empty stays empty (None).

    The box page used to print a missing value as "None" and send that word
    back; it is read as empty so a box saved that way heals on its next save.
    """
    value = str(raw or "").strip()
    return None if value in ("", "None") else value


def guest_photo_count(event_id: int) -> int:
    """Pictures guests see in this event; their hidden originals go with them."""
    con = db.get_db()
    row = con.execute("SELECT COUNT(*) AS c FROM media WHERE event_id=? AND hidden=0", (event_id,)).fetchone()
    con.close()
    return int(row["c"]) if row else 0


@bp.post("/events/<int:event_id>/purge-media")
def purge_event_media(event_id: int):
    """„Fotos löschen, Event behalten" — for a gallery that moved elsewhere.

    Every picture goes (files, rows, download sizes, watermarks, the hidden
    originals and their copy on the NAS); the event with its slug, old
    slugs, signing key, QR target and forward stays, so every printed code
    keeps working. The form sends the number it showed: if pictures arrived
    in between, nothing is deleted and the page asks again.
    """
    redir = _require_admin()
    if redir:
        return redir
    from app import retention
    event = db.get_event_by_id(event_id)
    if not event:
        abort(404)
    count = guest_photo_count(event_id)
    back = url_for("admin.event_storage", event_id=event_id)
    if str(request.form.get("confirm_count", "")).strip() != str(count):
        flash("Nichts gelöscht: Die Zahl der Fotos hat sich geändert. Bitte noch einmal bestätigen.", "warning")
        return redirect(back)
    result = retention.purge_event(event_id, reason="admin", actor="admin")
    flash(f"{count} Fotos gelöscht ({result.get('files', 0)} Dateien). Das Event bleibt — "
          "Adresse, QR-Codes und Weiterleitung gelten weiter. Die Löschung steht im Protokoll.", "success")
    return redirect(back)


def _purge_media(media_id: int, reason: str = "admin", actor: str = "admin", note: str = "") -> Optional[dict]:
    """Aufnahme samt Dateien, Original-Variante und Cache entfernen.

    ``actor`` is who the deletion log names (an MCP assistant passes its name).

    With a trash (account setting ``trash_days`` > 0, app/trash.py) the
    picture and its original variant go there instead; the booth tombstone
    is written all the same, the deletion log only on the real purge.
    """
    from app import trash
    if trash.enabled():
        row = db.get_media(media_id)
        if not row:
            return None
        if row.get("uid"):
            from app import ecosystem
            ecosystem.remember_deleted(row["uid"], event_id=row.get("event_id"), reason=reason,
                                       scope="media", session_uid=row.get("session_uid"))
            sib = ecosystem.media_by_uid(row["uid"], "original")
            if sib and int(sib["id"]) != int(media_id):
                trash.move(sib, actor=actor, reason=reason, note=note)
        trash.move(row, actor=actor, reason=reason, note=note)
        return row
    media.clear_download_cache(media_id)   # skalierte Fassungen mitentfernen
    row = db.delete_media(media_id)
    if row:
        # The booth may still be sending this item's original (or, for the
        # main result of a run, its raw shots): note it so it stays deleted.
        if row.get("uid"):
            from app import ecosystem
            ecosystem.remember_deleted(row["uid"], event_id=row.get("event_id"), reason=reason,
                                       scope="media", session_uid=row.get("session_uid"))
        rels = [row.get("path_original"), row.get("path_webp"), row.get("path_thumb"), row.get("path_poster")]
        if row.get("uid"):                 # Box-Upload: Original-Variante gehört dazu
            from app import ecosystem
            sib = ecosystem.media_by_uid(row["uid"], "original")
            if sib:
                db.delete_media(sib["id"])
                rels += [sib.get("path_original"), sib.get("path_webp"), sib.get("path_thumb")]
                if sib.get("storage") == "remote" and sib.get("remote_path"):
                    from app import storage
                    storage.queue_remote_delete(sib["remote_path"])
        if row.get("storage") == "remote" and row.get("remote_path"):
            from app import storage
            storage.queue_remote_delete(row["remote_path"])
        size = 0
        for rel in rels:
            if not rel:
                continue
            try:
                full = (media.data_dir() / rel).resolve()
                if full.exists() and full.is_file():
                    size += full.stat().st_size
            except Exception:
                pass
            media.delete_rel_path(rel)
        from app import retention
        event = db.get_event_by_id(int(row["event_id"])) if row.get("event_id") else None
        retention.record(scope="media", reason="admin", actor=actor,
                         event_slug=(event or {}).get("slug", ""), session_uid=row.get("session_uid") or "",
                         files=len([r for r in rels if r]), size=size, note=note)
    return row


#: What manage mode on the gallery page may do (``public.gallery?ansicht=verwalten``).
MANAGE_ACTIONS = ("hide", "show", "tag", "delete", "restore", "cover")


@bp.post("/events/<int:event_id>/manage")
def manage_media(event_id: int):
    """One endpoint for the owner's tools on the gallery page.

    JSON ``{action, ids, tag?}``; the CSRF token comes as ``X-CSRF-Token``
    (``_csrf_wache``). Only ids of this event are touched - a foreign id in
    the list is dropped, not refused, like on the sort-out page. Each action
    is the one the rest of the admin uses: hide = into the review queue
    (``set_media_visibility``), show = approve, delete = ``_purge_media``
    (trash when it is on, with the trash entries back for "Rückgängig").
    """
    if not session.get("admin_logged_in"):
        return jsonify({"ok": False, "error": "login"}), 401
    event = db.get_event_by_id(event_id)
    if not event:
        abort(404)
    body = request.get_json(silent=True) or {}
    action = str(body.get("action") or "")
    if action not in MANAGE_ACTIONS:
        return jsonify({"ok": False, "error": "action"}), 400
    raw = body.get("ids") or []
    wanted = {int(i) for i in raw if str(i).isdigit()}
    if action == "restore":
        from app import trash
        trash.init_schema()
    con = db.get_db()
    try:
        if action == "restore":
            rows = con.execute("SELECT id FROM media_trash WHERE event_id=?", (event_id,)).fetchall() \
                if wanted else []
            entries = sorted(wanted & {int(r[0]) for r in rows})
        else:
            own = {int(r[0]) for r in con.execute("SELECT id FROM media WHERE event_id=?", (event_id,))}
            ids = sorted(wanted & own)[:500]
    finally:
        con.close()
    if action == "restore":
        from app import trash
        done = [e for e in entries if trash.restore(e)]
        return jsonify({"ok": True, "restored": len(done)})
    if not ids:
        return jsonify({"ok": False, "error": "ids"}), 400
    marks = ",".join("?" * len(ids))
    result: dict = {"ok": True, "ids": ids}
    if action in ("hide", "show"):
        con = db.get_db()
        try:
            if action == "show":
                con.execute(f"UPDATE media SET hidden=0, review_state='approved' WHERE id IN ({marks})", ids)
            else:
                con.execute(f"UPDATE media SET hidden=1, review_state='pending' WHERE id IN ({marks})", ids)
            con.commit()
        finally:
            con.close()
    elif action == "tag":
        from app import gallery_settings as gsm
        tag = str(body.get("tag") or "").strip()
        if tag and tag not in gsm.TAG_IDS:
            return jsonify({"ok": False, "error": "tag"}), 400
        con = db.get_db()
        try:
            con.execute(f"UPDATE media SET color_tag=? WHERE id IN ({marks})", [tag or None] + ids)
            con.commit()
        finally:
            con.close()
        result["tag"] = tag
    elif action == "cover":
        row = db.get_media(ids[0])
        rel = (row or {}).get("path_webp") or (row or {}).get("path_thumb")
        if not rel:
            return jsonify({"ok": False, "error": "ids"}), 400
        db.update_event(event_id, cover_path=rel)
    elif action == "delete":
        from app import trash
        for i in ids:
            _purge_media(i, reason="admin", actor="admin", note="Galerie verwalten")
        result["trash"] = []
        if trash.enabled():
            con = db.get_db()
            try:
                result["trash"] = [int(r[0]) for r in con.execute(
                    f"SELECT MAX(id) FROM media_trash WHERE media_id IN ({marks}) GROUP BY media_id", ids)]
            finally:
                con.close()
    return jsonify(result)


@bp.post("/media/<int:media_id>/delete")
def delete_media(media_id: int):
    redir = _require_admin()
    if redir:
        return redir
    _purge_media(media_id)
    return jsonify({"ok": True})


# ─── Freigabe von Aufnahmen (Moderation) ──────────────────────
# Ist ``moderation`` für ein Event an, kommen neue Aufnahmen zunächst in diese
# Warteschlange. Große Vorschaubilder, zwei Knöpfe: freigeben oder löschen.

@bp.get("/events/<int:event_id>/review")
def review_event(event_id: int):
    redir = _require_admin()
    if redir:
        return redir
    from app import ecosystem, gallery_settings
    ecosystem.init_schema()
    event = db.get_event_by_id(event_id)
    if not event:
        abort(404)
    items = []
    for m in ecosystem.pending_review(event_id):
        rel = m.get("path_webp") or m.get("path_thumb") or m.get("path_original")
        is_video = str(m.get("path_original", "")).lower().endswith((".mp4", ".webm", ".mov"))
        items.append({
            "id": m["id"],
            "url": f"/data/{rel}" if rel else "",
            "video_url": f"/data/{m['path_original']}" if is_video else "",
            "is_video": is_video,
            "kind": m.get("kind") or "photo",
            "filename": m.get("filename") or "",
            "size_h": _human_bytes(int(m.get("size_bytes") or 0)),
            "taken": (m.get("taken_at") or m.get("created_at") or "")[:16].replace("T", " "),
        })
    return render_template("admin/events/review.html",
                           event=event, items=items,
                           gs=gallery_settings.for_event(event),
                           csrf_token=_csrf_token())


@bp.post("/events/<int:event_id>/review/approve")
def review_approve(event_id: int):
    redir = _require_admin()
    if redir:
        return redir
    from app import ecosystem
    ids = [int(i) for i in request.form.getlist("media_id") if str(i).isdigit()]
    n = ecosystem.approve_media(event_id, ids or None)
    flash(t("msg.review.approved", n=n), "success")
    return redirect(url_for("admin.review_event", event_id=event_id))


@bp.post("/events/<int:event_id>/review/<int:media_id>/reject")
def review_reject(event_id: int, media_id: int):
    redir = _require_admin()
    if redir:
        return redir
    row = db.get_media(media_id)
    if not row or int(row["event_id"]) != int(event_id):
        abort(404)
    _purge_media(media_id, reason="review")  # Ablehnen heißt löschen — nichts bleibt liegen
    flash(t("msg.review.rejected"), "success")
    return redirect(url_for("admin.review_event", event_id=event_id))


@bp.post("/events/<int:event_id>/toggle-featured")
def toggle_event_featured(event_id: int):
    """Toggle featured status for an event."""
    redir = _require_admin()
    if redir:
        return redir
    event = db.get_event_by_id(event_id)
    if not event:
        abort(404)
    db.set_event_featured(event_id, not bool(event.get("featured")))
    return redirect(request.referrer or url_for("admin.events"))


@bp.post("/events/<int:event_id>/publish")
def publish_event(event_id: int):
    """"Live schalten" / "Zurück auf Entwurf" from the list or the event page.

    The same function as the form's switch and the MCP tool
    (``app/publishing.py``). The flash says what happened and, after going
    live, gives the gallery's address and whatever still holds it back.
    """
    redir = _require_admin()
    if redir:
        return redir
    from markupsafe import Markup, escape

    from app import publishing
    if not db.get_event_by_id(event_id):
        abort(404)
    on = request.form.get("live") == "1"
    res = publishing.set_published(event_id, on, fallback_base=request.host_url)
    ev = res["event"]
    back = (url_for("admin.edit_event", event_id=event_id) if request.form.get("back") == "edit"
            else url_for("admin.events"))
    if on:
        head = t("publish.done_live" if res["changed"] else "publish.already_live", name=ev.get("name") or "")
        msg = Markup("<strong>{}</strong> {} <a href=\"{}\" target=\"_blank\" rel=\"noopener\">{}</a>").format(
            head, t("publish.link_label"), res["url"], res["url"])
        lines = [escape(publishing.note_text(n)) for n in res["notes"]]
        if lines:
            msg += Markup("<br>") + Markup("<br>").join(lines)
        if any(n["code"] == "no_password" for n in res["notes"]):
            msg += Markup(" <a href=\"{}#password\">{}</a>").format(
                url_for("admin.edit_event", event_id=event_id), t("publish.set_password"))
        flash(msg, "warning" if any(n["code"] != "no_password" for n in res["notes"]) else "success")
    else:
        flash(t("publish.done_draft" if res["changed"] else "publish.already_draft", name=ev.get("name") or ""),
              "success")
    return redirect(back)


# ─── Geräte-Logs ──────────────────────────────────────────────
# Was eine Box meldet, landet über /api/v2/booth/sync in device_logs. Hier
# schaut der Betreiber aus der Ferne hinein: gefiltert nach Schwere, Zeitraum
# und Suchbegriff, mit dem Kontext als aufklappbares JSON.

LOG_RANGES = {"24h": 24, "7d": 24 * 7, "all": None}


def _log_filters():
    """Read the filter arguments of the log view (level, period, search)."""
    from app import ecosystem
    level = request.args.get("level", "")
    level = level if level in ecosystem.LOG_LEVELS else ""
    period = request.args.get("range", "24h")
    if period not in LOG_RANGES:
        period = "24h"
    return level, period, (request.args.get("q", "") or "").strip()[:80]


@bp.get("/devices/<int:device_id>/logs")
def device_logs(device_id: int):
    redir = _require_admin()
    if redir:
        return redir
    from app import ecosystem
    ecosystem.init_schema()
    device = db.get_device_by_id(device_id)
    if not device:
        abort(404)
    level, period, query = _log_filters()
    entries = ecosystem.device_logs(device_id, level=level or None, limit=500,
                                    since_hours=LOG_RANGES[period], query=query)
    rows = []
    for e in entries:
        context = e.get("context_json") or ""
        try:
            context = json.dumps(json.loads(context), indent=2, ensure_ascii=False) if context else ""
        except ValueError:
            pass                      # not JSON? then show it as it came in
        rows.append({**e, "context": context,
                     "when": (e.get("occurred_at") or "")[:19].replace("T", " ")})
    return render_template("admin/device_logs.html",
                           device=device, entries=rows,
                           counts=ecosystem.device_log_counts(device_id, since_hours=24),
                           groups=ecosystem.error_groups(device_id, 24),
                           acked_at=ecosystem.logs_acknowledged_at(device_id),
                           level=level, period=period, query=query,
                           ranges=[("24h", "24 Stunden"), ("7d", "7 Tage"), ("all", "alles")],
                           levels=ecosystem.LOG_LEVELS,
                           retention_days=ecosystem.LOG_RETENTION_DAYS,
                           csrf_token=_csrf_token())


@bp.get("/devices/<int:device_id>/logs.txt")
def device_logs_txt(device_id: int):
    """Same view as plain text — easy to paste into a mail to the manufacturer."""
    redir = _require_admin()
    if redir:
        return redir
    from app import ecosystem
    ecosystem.init_schema()
    device = db.get_device_by_id(device_id)
    if not device:
        abort(404)
    level, period, query = _log_filters()
    lines = [f"# {device['name']} — Logs ({period}{', ' + level if level else ''}"
             f"{', Suche: ' + query if query else ''})"]
    for e in ecosystem.device_logs(device_id, level=level or None, limit=2000,
                                   since_hours=LOG_RANGES[period], query=query):
        line = f"{(e.get('occurred_at') or '')[:19]}\t{e.get('level', ''):7}\t{e.get('code') or '-'}\t{e.get('message') or ''}"
        if e.get("context_json"):
            line += f"\t{e['context_json']}"
        lines.append(line)
    body = "\n".join(lines) + "\n"
    return current_app.response_class(body, mimetype="text/plain; charset=utf-8")


# ─── Speicher ─────────────────────────────────────────────────

@bp.get("/events/<int:event_id>/storage")
def event_storage(event_id: int):
    """Wie viel Platz dieses Event belegt — Web, Originale, NAS."""
    redir = _require_admin()
    if redir:
        return redir
    from app import storage_stats
    event = db.get_event_by_id(event_id)
    if not event:
        abort(404)
    overview = storage_stats.overview()
    usage = storage_stats.event_usage(event_id)
    from app import redirects
    return render_template("admin/events/storage.html",
                           event=event, usage=usage,
                           photo_count=guest_photo_count(event_id),
                           redirect_host=redirects.host(event.get("redirect_url")),
                           disk=overview["disk"],
                           totals=overview["totals"],
                           warning=overview["warning"],
                           warn_percent=overview["warn_percent"],
                           human=media.human_bytes,
                           csrf_token=_csrf_token())


# ─── Settings ─────────────────────────────────────────────────

def _load_settings(*keys):
    return {k: (db.get_setting(k) or "") for k in keys}


@bp.route("/settings", methods=["GET", "POST"])
def settings():
    redir = _require_admin()
    if redir:
        return redir
    settings_data = _load_settings(
        "admin_username", "branding_logo_path", "branding_logo_dark_path",
        "branding_accent", "branding_bg", "branding_text", "branding_font",
        "branding_banner_path",
        "umami_src", "umami_website_id",
    )
    brand = branding.event_branding(None)
    from app import accounts

    return render_template("admin/settings/index.html",
                           settings=settings_data,
                           fresh_recovery=session.pop("fresh_recovery", None),
                           totp_enabled=(db.get_setting("admin_totp_enabled") or "") == "1",
                           totp_pending=session.get("totp_pending"),
                           recovery_made_at=accounts.recovery_made_at(),
                           brand=brand,
                           font_choices=[(fid, t(f"brand.font_{fid}")) for fid in branding.FONT_IDS],
                           contrast_warnings=branding.contrast_warnings(brand),
                           tab="account",
                           csrf_token=_csrf_token())


@bp.route("/settings/tracking", methods=["GET", "POST"])
def settings_tracking():
    redir = _require_admin()
    if redir:
        return redir
    if request.method == "POST":
        db.set_setting("umami_src", request.form.get("umami_src", "").strip())
        db.set_setting("umami_website_id", request.form.get("umami_website_id", "").strip())
        flash("Tracking-Einstellungen gespeichert", "success")
        return redirect(url_for("admin.settings_tracking"))
    settings_data = _load_settings("umami_src", "umami_website_id")
    return render_template("admin/settings/tracking.html",
                           settings=settings_data,
                           tab="tracking",
                           csrf_token=_csrf_token())


@bp.route("/settings/eingang", methods=["GET", "POST"])
def settings_eingang():
    """FTP- und SFTP-Eingang: Dienste an/aus und Konten je Event."""
    redir = _require_admin()
    if redir:
        return redir
    from app import eingang

    neues_konto = None
    if request.method == "POST":
        tat = request.form.get("tat") or ""
        if tat == "dienste":
            for schalter in ("eingang_ftp", "eingang_ftp_tls", "eingang_ftp_klartext", "eingang_sftp"):
                db.set_setting(schalter, "1" if request.form.get(schalter) else "0")
            for feld in ("eingang_ftp_port", "eingang_sftp_port"):
                wert = (request.form.get(feld) or "").strip()
                db.set_setting(feld, wert if wert.isdigit() and 1 <= int(wert) <= 65535
                               else eingang.STANDARD.get(feld, ""))
            bereich = (request.form.get("eingang_ftp_passiv") or "").strip()
            db.set_setting("eingang_ftp_passiv", bereich if _portbereich(bereich)
                           else eingang.STANDARD["eingang_ftp_passiv"])
            db.set_setting("eingang_ftp_cert", (request.form.get("eingang_ftp_cert") or "").strip()[:300])
            flash("Gespeichert. Nach dem Einschalten eines Dienstes einmal neu starten.", "success")
            return redirect(url_for("admin.settings_eingang"))
        if tat == "konto":
            try:
                event_id = int(request.form.get("event_id") or 0)
                passwort = eingang.neues_passwort(event_id)
            except (ValueError, TypeError) as e:
                flash(str(e) or "Das Event gibt es nicht.", "error")
                return redirect(url_for("admin.settings_eingang"))
            event = db.get_event_by_id(event_id)
            # Bewusst nicht weiterleiten: Das Kennwort steht genau einmal da.
            neues_konto = {"event": event["name"], "benutzer": event["slug"], "passwort": passwort}
        elif tat == "weg":
            eingang.konto_loeschen(int(request.form.get("event_id") or 0))
            flash("Zugang entfernt.", "success")
            return redirect(url_for("admin.settings_eingang"))

    host = (db.base_url() or request.host_url).split("//")[-1].strip("/").split(":")[0]
    return render_template(
        "admin/settings/eingang.html",
        events=db.list_events() or [], konten=eingang.konten(), neues_konto=neues_konto,
        host=host,
        ftp_an=eingang.an("ftp"), sftp_an=eingang.an("sftp"),
        ftp_port=eingang.einstellung("eingang_ftp_port"),
        sftp_port=eingang.einstellung("eingang_sftp_port"),
        ftp_passiv=eingang.einstellung("eingang_ftp_passiv"),
        ftp_tls=eingang.einstellung("eingang_ftp_tls") == "1",
        ftp_klartext=eingang.einstellung("eingang_ftp_klartext") == "1",
        ftp_cert=eingang.einstellung("eingang_ftp_cert"),
        endungen=", ".join(sorted(e.lstrip(".").upper() for e in eingang.ENDUNGEN)),
        max_mb=eingang.MAX_BYTES // (1024 * 1024),
        tab="eingang", csrf_token=_csrf_token())


def _portbereich(wert: str) -> bool:
    teile = str(wert or "").split("-")
    if len(teile) != 2 or not all(t.strip().isdigit() for t in teile):
        return False
    von, bis = int(teile[0]), int(teile[1])
    return 1024 <= von <= bis <= 65535


@bp.route("/settings/bewertungen", methods=["GET", "POST"])
def settings_bewertungen():
    """Die Vorgabe für alle Events — einzelne dürfen davon abweichen."""
    redir = _require_admin()
    if redir:
        return redir
    from app import bewertung

    url_ungueltig = False
    if request.method == "POST":
        _check_csrf()
        roh = (request.form.get("review_url") or "").strip()
        url = bewertung.gueltige_url(roh)
        url_ungueltig = bool(roh) and not url
        db.set_setting("review_url", url)
        db.set_setting("review_text", (request.form.get("review_text") or "").strip()[:bewertung.MAX_TEXT])
        db.set_setting("review_enabled", "1" if request.form.get("review_enabled") else "0")
        if url_ungueltig:
            flash("Das war keine Web-Adresse — das Feld ist leer geblieben.", "error")
        else:
            flash("Gespeichert.", "success")
            return redirect(url_for("admin.settings_bewertungen"))

    con = db.get_db()
    try:
        top = [dict(z) for z in con.execute(
            "SELECT b.event_id, e.name, SUM(b.klicks) AS gesamt, "
            "SUM(CASE WHEN b.tag >= date('now', '-30 days') THEN b.klicks ELSE 0 END) AS monat "
            "FROM bewertung_tage b JOIN events e ON e.id = b.event_id "
            "GROUP BY b.event_id ORDER BY gesamt DESC LIMIT 20")]
    except Exception:                       # noqa: BLE001 — ohne Zahlen eben ohne Tabelle
        top = []
    finally:
        con.close()

    return render_template("admin/settings/bewertungen.html",
                           enabled=(db.get_setting("review_enabled") or "") == "1",
                           url=db.get_setting("review_url") or "",
                           text=db.get_setting("review_text") or "",
                           standardtext=bewertung.STANDARDTEXT,
                           url_ungueltig=url_ungueltig, top=top,
                           tab="bewertungen", csrf_token=_csrf_token())


@bp.route("/settings/mcp", methods=["GET", "POST"])
def settings_mcp():
    """Assistenten-Zugang (MCP): Schlüssel erzeugen oder wegnehmen."""
    redir = _require_admin()
    if redir:
        return redir
    from app import mcp

    if request.method == "POST":
        action = request.form.get("action")
        if action == "revoke":
            mcp.revoke_key()
            flash("Zugang entzogen. Bestehende Verbindungen antworten ab sofort mit „Kein Schlüssel“.", "success")
        elif action == "key_extras":
            # Extra rights of the static key: only with the password typed
            # again; written to the change log like an OAuth grant.
            from app import oauth
            wanted = oauth.extras_from_form(request.form)
            before = mcp.key_scopes() - set(mcp.KEY_SCOPES)
            if wanted - before:
                ok, message = oauth.reauthenticate(str(request.form.get("password") or ""))
                if not ok:
                    flash(message, "error")
                    return redirect(url_for("admin.settings_mcp"))
            mcp.set_key_extras(wanted)
            if wanted != before:
                oauth.note_grant("Schlüssel (Skripte)", wanted, "Zusatzrechte geändert")
            flash(t("mcp.key.extras_saved"), "success")
        elif action in ("static_off", "static_on"):
            # Switching off keeps the key: a script that is paused for an
            # event does not need a new key afterwards.
            db.set_setting(mcp.STATIC_SWITCH, "1" if action == "static_on" and mcp.current_key() else "0")
            flash(t("mcp.key.switched_on" if mcp.static_key_enabled() else "mcp.key.switched_off"), "success")
        else:
            # S-12: only a hash is stored; this answer is the one time the key
            # is shown — rendered here, not put into a flash (the cookie).
            fresh = mcp.new_key()
            flash("Neuer Schlüssel erzeugt. Der alte gilt nicht mehr.", "success")
            resp = current_app.make_response(render_template(
                "admin/settings/mcp.html", key=mcp.current_key(), fresh_key=fresh,
                key_enabled=mcp.static_key_enabled(), tools=sorted(mcp.TOOLS.items()),
                key_extras=mcp.key_scopes() - set(mcp.KEY_SCOPES),
                tab="mcp", csrf_token=_csrf_token()))
            resp.headers["Cache-Control"] = "no-store"
            return resp
        return redirect(url_for("admin.settings_mcp"))

    return render_template("admin/settings/mcp.html",
                           key=mcp.current_key(), fresh_key=None, key_enabled=mcp.static_key_enabled(),
                           tools=sorted(mcp.TOOLS.items()), key_extras=mcp.key_scopes() - set(mcp.KEY_SCOPES),
                           tab="mcp",
                           csrf_token=_csrf_token())


@bp.route("/settings/assistants", methods=["GET", "POST"])
def settings_assistants():
    """Connected assistants (OAuth clients): who, what they may do, last seen, withdraw."""
    redir = _require_admin()
    if redir:
        return redir
    from app import oauth

    if request.method == "POST":
        action = request.form.get("action")
        if action == "revoke_client":
            oauth.revoke_client(str(request.form.get("client_id") or ""))
            flash(t("oauth.admin.revoked_one"), "success")
        elif action == "withdraw_extras":
            client_id = str(request.form.get("client_id") or "")
            oauth.withdraw_extras(client_id)
            client = oauth.get_client(client_id) or {}
            oauth.note_grant(f"Assistent „{client.get('name') or client_id}“", (), "Zusatzrechte entzogen")
            flash(t("oauth.admin.extras_withdrawn"), "success")
        elif action == "revoke_all":
            oauth.revoke_all()
            flash(t("oauth.admin.revoked_all"), "success")
        elif action in ("enable", "disable"):
            db.set_setting(oauth.ENABLED_KEY, "1" if action == "enable" else "0")
            flash(t("oauth.admin.switched_on" if action == "enable" else "oauth.admin.switched_off"), "success")
        return redirect(url_for("admin.settings_assistants"))

    return render_template("admin/settings/assistants.html",
                           clients=oauth.list_clients(), enabled=oauth.enabled(),
                           mcp_url=oauth.resource_uri(), tab="assistants",
                           csrf_token=_csrf_token())


@bp.post("/settings/account")
def settings_account():
    redir = _require_admin()
    if redir:
        return redir
    from app import accounts

    from app import sharelinks

    username = request.form.get("admin_username", "").strip()
    new_password = request.form.get("new_password", "").strip()
    confirm = request.form.get("confirm_password", "").strip()
    current = request.form.get("current_password", "")
    stored_user = db.get_setting("admin_username") or "admin"
    rename = bool(username) and username != stored_user
    if not rename and not new_password:
        flash("Nichts geändert.", "warning")
        return redirect(url_for("admin.settings"))
    # Whoever holds a session (an unlocked laptop, a stolen cookie) must not
    # be able to take the account over: user name and password change only
    # with the current password. Wrong answers count on the login brake, so
    # this form is no side door for guessing.
    wait = sharelinks.retry_after(_login_key(), LOGIN_LIMIT, LOGIN_WINDOW)
    if wait:
        flash(sharelinks.wait_message(wait) + " Nichts geändert.", "error")
        return redirect(url_for("admin.settings"))
    if not _check_password(current, db.get_setting("admin_password_hash") or ""):
        sharelinks.record_failure(_login_key(), LOGIN_WINDOW)
        time.sleep(0.5)
        flash("Das aktuelle Passwort stimmt nicht — nichts geändert.", "error")
        return redirect(url_for("admin.settings"))
    sharelinks.forget(_login_key())
    if new_password:
        # The form asks twice; an empty second field used to skip the check,
        # and one typo locked the operator out.
        if confirm != new_password:
            flash("Die beiden Passwörter sind nicht gleich — nichts geändert.", "error")
            return redirect(url_for("admin.settings"))
        from app import passwords
        ok, meldung = passwords.pruefe(new_password, passwords.bezug_der_installation())
        if not ok:
            flash(meldung + " Nichts geändert.", "error")
            return redirect(url_for("admin.settings"))
    # Everything checked; only now is anything written.
    if rename:
        db.set_setting("admin_username", username)
        # Other sessions end with the old name, as with a new password.
        accounts.rotate_session_epoch()
        session["admin_epoch"] = accounts.session_epoch()
    if new_password:
        first_change = accounts.password_is_temporary()
        accounts.set_password(new_password)
        # Every other session ends with the old password; this one goes on.
        session["admin_epoch"] = accounts.session_epoch()
        # The first start no longer writes a recovery code into the server
        # log (S16). It is made here, when the operator has just proved
        # access, and shown once on the next page.
        if first_change and not accounts.has_recovery_code():
            session["fresh_recovery"] = accounts.new_recovery_code()
        # Revocation after a password change (S-6): device keys are not tied
        # to the password, so a leaked key survives it unless renewed.
        if request.form.get("rotate_device_keys") == "1":
            n = db.regenerate_all_device_keys()
            flash(f"{n} Geräteschlüssel erneuert — jede Box muss neu gekoppelt werden.", "success")
        else:
            flash("Hinweis: Geräteschlüssel bleiben gültig. Wenn einer in falsche Hände geraten sein könnte, "
                  "unter „Boxen“ „Alle Geräteschlüssel erneuern“ wählen.", "info")
    flash("Zugangsdaten gespeichert", "success")
    return redirect(url_for("admin.settings"))


@bp.post("/settings/recovery")
def settings_recovery():
    """Neuen Wiederherstellungscode erzeugen — einmal sichtbar, dann nur als Hash."""
    redir = _require_admin()
    if redir:
        return redir
    from app import accounts

    code = accounts.new_recovery_code()
    session["fresh_recovery"] = code          # nur für die nächste Seitenanzeige
    flash("Neuer Wiederherstellungscode erzeugt. Jetzt notieren — er wird nicht wieder angezeigt.", "success")
    return redirect(url_for("admin.settings"))


@bp.route("/recover", methods=["GET", "POST"])
def recover():
    """Passwort neu setzen, ohne Serverzugang — mit dem Wiederherstellungscode."""
    from app import accounts, sharelinks

    # A recovery code is a second password: 80 bits, but guessing must still
    # not be possible at leisure. Five attempts per address and hour, every
    # attempt counts (asking for a mail link too), a success wipes the count.
    key = f"recover:{request.remote_addr}"
    wait = sharelinks.retry_after(key, RECOVER_LIMIT, RECOVER_WINDOW)
    blocked = bool(wait)
    if request.method == "POST" and not blocked:
        sharelinks.record_failure(key, RECOVER_WINDOW)
    new_code = None
    error = None
    notice = None
    token = (request.args.get("token") or request.form.get("token") or "").strip()

    if request.method == "POST" and not blocked:
        if request.form.get("send_link"):
            # Link anfordern: Die Antwort ist immer dieselbe, egal ob eine
            # Adresse hinterlegt ist — sonst verrät die Seite, ob es sie gibt.
            # Never a link to whatever host the request named (S-5): this
            # form is reachable without login, and the mail goes to the admin.
            base = db.link_base()
            if not base:
                current_app.logger.warning("Reset link refused: no BASE_URL and an untrusted Host header")
                error = ("Für diese Galerie ist keine Adresse eingerichtet (BASE_URL). "
                         "Ohne sie wird kein Link verschickt.")
            else:
                sent, notice = accounts.start_mail_reset(base)
                if not sent:
                    error, notice = notice, None
        elif token:
            ok, message = accounts.redeem_reset_token(token, request.form.get("new_password", ""))
            if ok:
                sharelinks.forget(key)
                session.pop("admin_logged_in", None)
                return render_template("admin/recover.html", done=True, new_code=None,
                                       message=message + " Du kannst dich jetzt anmelden.",
                                       csrf_token=_csrf_token())
            error = message
        else:
            ok, new_code, message = accounts.redeem_recovery(
                request.form.get("username", ""), request.form.get("code", ""),
                request.form.get("new_password", ""))
            if ok:
                sharelinks.forget(key)
                session.pop("admin_logged_in", None)
                return render_template("admin/recover.html", done=True, new_code=new_code,
                                       message=message, csrf_token=_csrf_token())
            time.sleep(0.5)
            error = message

    page = render_template("admin/recover.html", done=False, error=error, blocked=blocked,
                           blocked_message=sharelinks.wait_message(wait, "auth.locked_attempts") if wait else "",
                           notice=notice, token=token if accounts.check_reset_token(token) else "",
                           token_bad=bool(token) and not accounts.check_reset_token(token),
                           mail_possible=accounts.mail_reset_possible(),
                           available=accounts.has_recovery_code(), csrf_token=_csrf_token())
    if blocked and request.method == "POST":
        return page, 429, {"Retry-After": str(wait)}
    return page


@bp.post("/settings/branding")
def settings_branding():
    """Branding der Installation — der Rückfall für jedes Event."""
    redir = _require_admin()
    if redir:
        return redir
    for field, key in (("branding_accent", "branding_accent"),
                       ("branding_bg", "branding_bg"),
                       ("branding_text", "branding_text")):
        raw = request.form.get(field, "")
        value = branding.normalize_hex(raw)
        if raw.strip() and not value:
            flash(t("msg.brand.color_invalid"), "warning")
        db.set_setting(key, value)
    db.set_setting("branding_font", branding.font_id(request.form.get("branding_font")))

    if request.form.get("remove_banner"):
        old_banner = db.get_setting("branding_banner_path") or ""
        if old_banner:
            media.delete_rel_path(old_banner)
        db.set_setting("branding_banner_path", "")
    else:
        banner_file = request.files.get("banner")
        if banner_file and banner_file.filename:
            try:
                db.set_setting("branding_banner_path", media.save_brand_banner(banner_file))
            except Exception as e:
                flash(f"Banner-Fehler: {e}", "error")

    for warn in branding.contrast_warnings(branding.event_branding(None)):
        flash(t(f"msg.brand.contrast_{warn['kind']}", ratio=warn["ratio"], need=warn["need"]), "warning")
    flash("Branding gespeichert", "success")
    return redirect(url_for("admin.settings"))


LOGO_SETTINGS = {"light": "branding_logo_path", "dark": "branding_logo_dark_path"}


@bp.post("/settings/logo")
def settings_logo():
    """Upload (or, with ``remove_logo``, remove) the logo; ``variant`` "dark" is the one for dark surfaces."""
    redir = _require_admin()
    if redir:
        return redir
    variant = "dark" if request.form.get("variant") == "dark" else "light"
    if request.form.get("remove_logo"):
        return _remove_logo(variant)
    logo_file = request.files.get("logo")
    if not (logo_file and logo_file.filename):
        flash("Bitte zuerst eine Datei wählen.", "warning")
        return redirect(url_for("admin.settings") + "#logo")
    try:
        rel = media.save_logo(logo_file, variant)
    except media.LogoRefused as e:
        flash(str(e), "error")
        return redirect(url_for("admin.settings") + "#logo")
    except Exception:                          # noqa: BLE001 — no stack text in a flash
        flash("Das Logo konnte nicht gespeichert werden. Bitte PNG, JPEG oder WebP versuchen.", "error")
        return redirect(url_for("admin.settings") + "#logo")
    db.set_setting(LOGO_SETTINGS[variant], rel)
    flash("Logo für dunklen Hintergrund gespeichert" if variant == "dark" else "Logo gespeichert", "success")
    return redirect(url_for("admin.settings") + "#logo")


def _remove_logo(variant: str):
    rel = db.get_setting(LOGO_SETTINGS[variant]) or ""
    if rel:
        media.delete_rel_path(rel)
    db.set_setting(LOGO_SETTINGS[variant], "")
    flash("Logo für dunklen Hintergrund entfernt" if variant == "dark" else "Logo entfernt", "success")
    return redirect(url_for("admin.settings") + "#logo")


@bp.post("/settings/logo/delete")
def settings_logo_delete():
    redir = _require_admin()
    if redir:
        return redir
    return _remove_logo("dark" if request.form.get("variant") == "dark" else "light")


@bp.route("/settings/homepage", methods=["GET", "POST"])
def settings_homepage():
    redir = _require_admin()
    if redir:
        return redir
    if request.method == "POST":
        action = request.form.get("action")
        if action == "delete_hero_image":
            db.set_setting("homepage_hero_image_path", "")
            flash("Hintergrundbild entfernt", "success")
            return redirect(url_for("admin.settings_homepage"))
        # Das Formular zeigt jetzt alle Download-Optionen explizit an, daher werden
        # zip_download_admin_only und selection_download_enabled hier mitgespeichert.
        keys = [
            "homepage_headline", "homepage_subtext",
            # Englische Fassungen der freien Texte. Leer heißt: deutsche Fassung.
            "homepage_headline_en", "homepage_subtext_en", "site_languages",
            "homepage_show_galleries", "homepage_show_empty_events",
            "footer_custom_link_label", "footer_custom_link_url",
            "footer_show_powered_by", "guest_upload_enabled",
            "zip_download_enabled", "zip_download_admin_only",
            "selection_download_enabled", "download_sizes_enabled",
            "silent_mode",
            "storage_warn_percent",
            "download_pattern",
            "file_naming",
        ]
        checkbox_keys = {
            "homepage_show_galleries", "homepage_show_empty_events",
            "footer_show_powered_by", "guest_upload_enabled",
            "zip_download_enabled", "zip_download_admin_only",
            "selection_download_enabled", "download_sizes_enabled",
            "silent_mode",
        }
        from app import dateinamen as _dn
        for key in keys:
            val = request.form.get(key, "")
            # For checkboxes that aren't checked, set to "0"
            if key in checkbox_keys:
                val = "1" if val else "0"
            if key == "file_naming":
                val = val if val in _dn.RULES else "original"
            if key == "download_pattern":
                val = val.strip()
                if not _dn.gueltig(val):
                    flash("Das Namensmuster geht so nicht — Schrägstriche und „..“ sind nicht erlaubt.",
                          "error")
                    val = db.get_setting("download_pattern") or ""
            db.set_setting(key, val)
        # Deletion defaults for new events (app/retention.py). A period that
        # is not 1..3650 days keeps the one stored: "no automatic deletion"
        # is its own choice, never a period of 0.
        from app import retention as _ret
        if "retention_days_default" in request.form:
            tage = _ret.read_days(request.form.get("retention_days_default"))
            if tage:
                db.set_setting("retention_days_default", str(tage))
            else:
                flash("Die Aufbewahrung braucht eine Zahl von 1 bis 3650 Tagen — die bisherige bleibt.",
                      "warning")
        if "retention_auto_default" in request.form:
            db.set_setting("retention_auto_default",
                           "0" if request.form.get("retention_auto_default") == "0" else "1")
        # Fußzeile: beliebig viele eigene Links, zweisprachig.
        from app import footer as footer_mod
        footer_mod.save(footer_mod.from_form(request.form))
        # Eigene Texte für alle Galerien. Je Event lässt sich das nochmal
        # überschreiben — hier steht der Hausstandard.
        from app import texts as texts_mod
        collected: dict = {}
        for lang in ("de", "en"):
            for key, _label in texts_mod.KEYS:
                value = (request.form.get(f"gtext_{lang}_{key}") or "").strip()
                if value:
                    collected.setdefault(lang, {})[key] = value
        extra_raw = (request.form.get("gtexts_extra_json") or "").strip()
        if extra_raw:
            extra = texts_mod.parse(extra_raw)
            if not extra:
                flash("Zusätzliche Texte: ungültiges JSON, ignoriert.", "warning")
            for lang, values in extra.items():
                collected.setdefault(lang, {}).update(values)
        db.set_setting("gallery_texts_json", texts_mod.dump(collected))

        # Hintergrundbild entfernen (Checkbox) hat Vorrang vor einem Upload
        if request.form.get("remove_hero_image"):
            db.set_setting("homepage_hero_image_path", "")
        else:
            hero_file = request.files.get("hero_image")
            if hero_file and hero_file.filename:
                try:
                    rel = media.save_hero_image(hero_file)
                    db.set_setting("homepage_hero_image_path", rel)
                except Exception as e:
                    flash(f"Bild-Fehler: {e}", "warning")
        flash("Homepage-Einstellungen gespeichert", "success")
        return redirect(url_for("admin.settings_homepage"))
    settings_data = _load_settings(
        "homepage_headline", "homepage_subtext", "homepage_hero_image_path",
        "homepage_headline_en", "homepage_subtext_en", "site_languages",
        "homepage_show_galleries", "homepage_show_empty_events",
        "footer_custom_link_label", "footer_custom_link_url",
        "footer_show_powered_by", "guest_upload_enabled",
        "zip_download_enabled", "zip_download_admin_only",
        "selection_download_enabled", "download_sizes_enabled",
        "silent_mode",
        "storage_warn_percent",
        "download_pattern",
        "file_naming",
    )
    from app import texts as texts_mod
    global_texts = texts_mod.parse(db.get_setting("gallery_texts_json"))
    known = {k for k, _ in texts_mod.KEYS}
    extra = {lang: {k: v for k, v in values.items() if k not in known}
             for lang, values in global_texts.items()}
    extra = {lang: values for lang, values in extra.items() if values}
    from app import footer as footer_mod
    from app import dateinamen as dn_mod
    from app import retention as ret_mod
    return render_template("admin/settings/homepage.html",
                           settings=settings_data,
                           retention_days_default=ret_mod.default_days(),
                           retention_auto_default=ret_mod.account_auto_default(),
                           muster_platzhalter=dn_mod.PLATZHALTER,
                           muster_beispiel=dn_mod.BEISPIEL,
                           muster_vorschau=dn_mod.vorschau(settings_data.get("download_pattern") or ""),
                           footer_links=footer_mod.custom() or [{"label": "", "label_en": "", "url": "", "new_tab": True}],
                           tab="homepage",
                           text_keys=texts_mod.KEYS, global_texts=global_texts,
                           gtexts_extra=json.dumps(extra, ensure_ascii=False, indent=2) if extra else "",
                           default_text=lambda key, lang: _default_text(key, lang),
                           csrf_token=_csrf_token())


@bp.route("/settings/notifications", methods=["GET", "POST"])
def settings_notifications():
    redir = _require_admin()
    if redir:
        return redir
    if request.method == "POST":
        keys = [
            "notify_tg_token", "notify_tg_chat_id",
            "notify_discord_webhook", "notify_webhook_url",
            "notify_quiet_from", "notify_quiet_to",
            "notify_on_upload", "notify_on_publish", "notify_on_new_device",
        ]
        for key in keys:
            val = request.form.get(key, "")
            if key in ("notify_on_upload", "notify_on_publish", "notify_on_new_device"):
                val = "1" if val else "0"
            db.set_setting(key, val)
        flash("Benachrichtigungen gespeichert", "success")
        return redirect(url_for("admin.settings_notifications"))
    settings_data = _load_settings(
        "notify_tg_token", "notify_tg_chat_id",
        "notify_discord_webhook", "notify_webhook_url",
        "notify_quiet_from", "notify_quiet_to",
        "notify_on_upload", "notify_on_publish", "notify_on_new_device",
    )
    return render_template("admin/settings/notifications.html",
                           settings=settings_data,
                           tab="notifications",
                           csrf_token=_csrf_token())


@bp.route("/settings/mail", methods=["GET", "POST"])
def settings_mail():
    """SMTP der Galerie — bisher gab es dafür keine Oberfläche.

    Die Galerie verschickt selbst: Bestätigungsmails für Foto-Benachrichtigungen
    (Double-Opt-in), die Benachrichtigung über neue Fotos und, wenn nötig, den
    Link zum Zurücksetzen des Passworts. Ohne diese Seite war das alles nur im
    Code vorhanden.
    """
    redir = _require_admin()
    if redir:
        return redir
    from app import emailer

    if request.method == "POST":
        f = request.form
        cfg = emailer.get_smtp_config()
        password = f.get("password", "")
        # Leer lassen heißt „unverändert": Das Kennwort wird nie zurückgeschickt,
        # also darf ein Speichern ohne Eingabe es nicht löschen.
        cfg.update({
            "host": f.get("host", "").strip(),
            "port": int(f.get("port") or 587),
            "username": f.get("username", "").strip(),
            "from_email": f.get("from_email", "").strip(),
            "use_ssl": f.get("security") == "ssl",
            "use_tls": f.get("security") == "starttls",
        })
        if password:
            cfg["password"] = password
        emailer.set_smtp_config(cfg)
        db.set_setting("admin_email", f.get("admin_email", "").strip())
        flash("E-Mail-Einstellungen gespeichert", "success")
        return redirect(url_for("admin.settings_mail"))

    cfg = emailer.get_smtp_config()
    return render_template("admin/settings/mail.html", tab="mail", cfg=cfg,
                           has_password=bool(cfg.get("password")),
                           admin_email=db.get_setting("admin_email") or "",
                           configured=emailer.smtp_configured(),
                           csrf_token=_csrf_token())


@bp.post("/settings/mail/test")
def settings_mail_test():
    """Eine Testmail an die hinterlegte Adresse — die einzige ehrliche Prüfung."""
    redir = _require_admin()
    if redir:
        return redir
    from app import emailer

    to = (request.form.get("to") or db.get_setting("admin_email") or "").strip()
    if not to:
        flash("Keine Empfängeradresse eingetragen.", "error")
        return redirect(url_for("admin.settings_mail"))
    try:
        emailer.send_text_mail(
            to_email=to, subject="Testmail aus der Photobox Gallery",
            body="Wenn diese Nachricht ankommt, kann die Galerie E-Mails verschicken.\n\n"
                 "Damit funktionieren: Bestätigungsmails für Foto-Benachrichtigungen, "
                 "die Meldung über neue Fotos und das Zurücksetzen des Passworts.\n")
        flash(f"Testmail an {to} verschickt.", "success")
    except Exception as e:
        flash(f"Testmail fehlgeschlagen: {e}", "error")
    return redirect(url_for("admin.settings_mail"))


@bp.post("/settings/notifications/test/<channel>")
def settings_notifications_test(channel: str):
    redir = _require_admin()
    if redir:
        return jsonify({"ok": False, "error": "Not authenticated"}), 401
    from app import notifier
    try:
        if channel == "telegram":
            ok = notifier.send_telegram("Test-Nachricht von Photobox Gallery – alles funktioniert!")
        elif channel == "discord":
            ok = notifier.send_discord("Test-Nachricht von Photobox Gallery – alles funktioniert!")
        elif channel == "webhook":
            ok = notifier.send_webhook({"event": "test", "message": "Test from Photobox Gallery"})
        else:
            return jsonify({"ok": False, "error": "Unknown channel"}), 400
        return jsonify({"ok": ok, "error": None if ok else "Überprüfe deine Konfiguration"})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})


@bp.route("/settings/legal", methods=["GET", "POST"])
def settings_legal():
    redir = _require_admin()
    if redir:
        return redir
    if request.method == "POST":
        from app import safe_markdown
        zu_lang = [k for k in ("impressum_text", "datenschutz_text", "impressum_text_en", "datenschutz_text_en")
                   if safe_markdown.too_long(request.form.get(k, ""))]
        if zu_lang:
            # Refused whole, not cut: a cut-off privacy text is worse than none (R2-U5).
            flash(f"Nicht gespeichert: höchstens {safe_markdown.MAX_CHARS} Zeichen je Text "
                  f"({', '.join(zu_lang)}).", "error")
            return redirect(url_for("admin.settings_legal"))
        db.set_setting("impressum_text", request.form.get("impressum_text", ""))
        db.set_setting("datenschutz_text", request.form.get("datenschutz_text", ""))
        db.set_setting("impressum_text_en", request.form.get("impressum_text_en", ""))
        db.set_setting("datenschutz_text_en", request.form.get("datenschutz_text_en", ""))
        # The facts the privacy template cannot know (app/legal.py).
        from app import legal
        for key in legal.LEGAL_FIELDS:
            if key in request.form:
                value = request.form.get(key, "").strip()[:500]
                if key == "legal_role" and value not in legal.ROLES:
                    value = "controller"
                db.set_setting(key, value)
        flash("Texte gespeichert", "success")
        return redirect(url_for("admin.settings_legal"))
    from app import legal
    settings_data = _load_settings("impressum_text", "datenschutz_text",
                                   "impressum_text_en", "datenschutz_text_en", *legal.LEGAL_FIELDS)
    return render_template("admin/settings/legal.html",
                           settings=settings_data,
                           legal_status=legal.status(),
                           tab="legal",
                           csrf_token=_csrf_token())


@bp.route("/settings/faq", methods=["GET", "POST"])
def settings_faq():
    redir = _require_admin()
    if redir:
        return redir
    import json
    import uuid
    import os
    from werkzeug.utils import secure_filename

    msg = None
    if request.method == "POST":
        action = request.form.get("action", "save")

        from app import safe_markdown
        if action in ("save_intro", "add_item") and (safe_markdown.too_long(request.form.get("intro_text", ""))
                                                     or safe_markdown.too_long(request.form.get("a", ""))):
            msg = f"Nicht gespeichert: höchstens {safe_markdown.MAX_CHARS} Zeichen."
            action = "too_long"
        if action == "save_intro":
            db.set_setting("faq_intro_text", request.form.get("intro_text", ""))
            msg = "Einleitung gespeichert."

        elif action == "add_item":
            items = db.get_faq_items()
            new_item = {
                "id": uuid.uuid4().hex[:8],
                "q": request.form.get("q", "").strip(),
                "a": request.form.get("a", "").strip(),
                "file_url": None,
                "file_name": None,
            }
            # Handle file upload
            f = request.files.get("attachment")
            if f and f.filename:
                fname = secure_filename(f.filename)
                ext = os.path.splitext(fname)[1].lower()
                if ext in {".pdf", ".jpg", ".jpeg", ".png", ".gif"}:
                    save_name = f"faq_{new_item['id']}{ext}"
                    upload_dir = os.path.join(str(media.data_dir()), "faq")
                    os.makedirs(upload_dir, exist_ok=True)
                    f.save(os.path.join(upload_dir, save_name))
                    new_item["file_url"] = f"/data/faq/{save_name}"
                    new_item["file_name"] = fname
            if new_item["q"]:
                items.append(new_item)
                db.save_faq_items(items)
            msg = "FAQ-Eintrag hinzugefügt."

        elif action == "delete_item":
            item_id = request.form.get("item_id", "")
            items = db.get_faq_items()
            items = [i for i in items if i.get("id") != item_id]
            db.save_faq_items(items)
            msg = "Eintrag gelöscht."

        elif action == "move_up":
            item_id = request.form.get("item_id", "")
            items = db.get_faq_items()
            idx = next((i for i, x in enumerate(items) if x.get("id") == item_id), None)
            if idx and idx > 0:
                items[idx - 1], items[idx] = items[idx], items[idx - 1]
                db.save_faq_items(items)

        elif action == "move_down":
            item_id = request.form.get("item_id", "")
            items = db.get_faq_items()
            idx = next((i for i, x in enumerate(items) if x.get("id") == item_id), None)
            if idx is not None and idx < len(items) - 1:
                items[idx], items[idx + 1] = items[idx + 1], items[idx]
                db.save_faq_items(items)

    items = db.get_faq_items()
    intro_text = db.get_setting("faq_intro_text") or ""
    return render_template("admin/settings/faq.html", items=items, intro_text=intro_text, msg=msg, csrf_token=_csrf_token())


# ── Analytics ────────────────────────────────────────────────────────────

@bp.route("/analytics")
def analytics():
    redir = _require_admin()
    if redir:
        return redir
    events = db.get_analytics_overview()
    totals = {"views": 0, "downloads": 0, "zip_downloads": 0, "whatsapp": 0}
    for e in events:
        totals["views"] += e["views"] or 0
        totals["downloads"] += e["downloads"] or 0
        totals["zip_downloads"] += e["zip_downloads"] or 0
        totals["whatsapp"] += e["whatsapp_shares"] or 0

    from app import stats as stats_mod
    return render_template(
        "admin/analytics.html",
        events=events,
        boxen=stats_mod.overview()["devices"],
        totals=totals,
    )


@bp.route("/analytics/<int:event_id>")
def analytics_event(event_id: int):
    redir = _require_admin()
    if redir:
        return redir
    event = db.get_event_by_id(event_id)
    if not event:
        abort(404)
    stats_raw = db.get_event_stats(event_id)

    # Echte Netto-Likes aus der likes-Tabelle (Toggle-fest), nicht die
    # 'like'-Klick-Zählung aus stats.
    like_counts = db.like_counts_for_event(event_id) or {}
    total_likes = sum(like_counts.values())

    # Top-Fotos nach Downloads – mit echtem Like-Zähler angereichert
    conn = db.get_db()
    rows = conn.execute("""
        SELECT m.*, COUNT(s.id) as dl_count
        FROM media m
        LEFT JOIN stats s ON s.media_id = m.id AND s.action = 'download'
        WHERE m.event_id = ?
        GROUP BY m.id
        ORDER BY dl_count DESC
        LIMIT 12
    """, (event_id,)).fetchall()
    top_photos = []
    for r in rows:
        d = dict(r)
        d["like_count"] = int(like_counts.get(r["id"], 0))
        top_photos.append(d)

    from app import stats as stats_mod
    return render_template(
        "admin/analytics_event.html",
        event=event,
        stats=stats_raw,
        top_photos=top_photos,
        total_likes=total_likes,
        daten=stats_mod.event(event_id),
        csrf_token=_csrf_token(),
    )


# ── Zahlen: je Event, je Box, zum Mitnehmen ──────────────────────────
# Wer einen Abend abrechnet oder einem Kunden zeigt, was lief, braucht Zahlen
# in der Hand — nicht auf einer Seite, die er abschreibt. Deshalb Export, und
# deshalb Zurücksetzen: Nach dem Event gehören die Zahlen dem Kunden, nicht
# der nächsten Auswertung.

@bp.get("/analytics/box/<int:device_id>")
def analytics_device(device_id: int):
    redir = _require_admin()
    if redir:
        return redir
    from app import stats as stats_mod
    geraet = db.get_device_by_id(device_id)
    if not geraet:
        abort(404)
    return render_template("admin/analytics_device.html", device=geraet,
                           daten=stats_mod.device(device_id), csrf_token=_csrf_token())


@bp.get("/analytics/export/<was>/<int:objekt_id>.<fmt>")
def analytics_export(was: str, objekt_id: int, fmt: str):
    redir = _require_admin()
    if redir:
        return redir
    from app import stats as stats_mod
    if was not in ("event", "box") or fmt not in ("csv", "json"):
        abort(404)
    if was == "event":
        if not db.get_event_by_id(objekt_id):
            abort(404)
        inhalt = stats_mod.event_csv(objekt_id) if fmt == "csv" else stats_mod.event_json(objekt_id)
    else:
        if not db.get_device_by_id(objekt_id):
            abort(404)
        inhalt = stats_mod.device_csv(objekt_id) if fmt == "csv" else stats_mod.device_json(objekt_id)
    name = f"photobox-{was}-{objekt_id}.{fmt}"
    typ = "text/csv; charset=utf-8" if fmt == "csv" else "application/json"
    # BOM, damit Excel die Umlaute richtig liest — sonst steht da „Durchgänge".
    daten = ("\ufeff" + inhalt) if fmt == "csv" else inhalt
    return Response(daten, mimetype=typ,
                    headers={"Content-Disposition": f'attachment; filename="{name}"'})


@bp.post("/analytics/reset/<was>/<int:objekt_id>")
def analytics_reset(was: str, objekt_id: int):
    redir = _require_admin()
    if redir:
        return redir
    _check_csrf()
    from app import stats as stats_mod
    if was == "event":
        anzahl = stats_mod.reset_event(objekt_id)
        flash(f"{anzahl} Einträge gelöscht. Die Fotos bleiben.", "success")
        return redirect(url_for("admin.analytics_event", event_id=objekt_id))
    if was == "box":
        anzahl = stats_mod.reset_device(objekt_id)
        flash(f"{anzahl} Tage gelöscht.", "success")
        return redirect(url_for("admin.analytics_device", device_id=objekt_id))
    abort(404)


# ── Devices ──────────────────────────────────────────────────────────

@bp.route("/devices")
def devices():
    redir = _require_admin()
    if redir:
        return redir
    return render_template("admin/devices.html", devices=db.list_devices(), csrf_token=_csrf_token())


def _devices_with_fresh_key(fresh=None, rotated_all=None):
    """The one page a device key is shown on: the answer to the POST that made it (S-6).

    Not a redirect with a flash — that would put the key into the session
    cookie. ``no-store`` keeps it out of the browser cache.
    """
    page = render_template("admin/devices.html", devices=db.list_devices(), csrf_token=_csrf_token(),
                           fresh_key=fresh, rotated_all=rotated_all)
    resp = current_app.make_response(page)
    resp.headers["Cache-Control"] = "no-store"
    return resp


@bp.post("/devices/rotate-all")
def rotate_all_device_keys():
    """„Alle Geräteschlüssel erneuern“ — after a password change or a lost laptop."""
    redir = _require_admin()
    if redir:
        return redir
    n = db.regenerate_all_device_keys()
    g._protokoll_was = "Alle Geräteschlüssel erneuert"
    g._protokoll_ergebnis = f"{n} Boxen"
    return _devices_with_fresh_key(rotated_all=n)


@bp.route("/devices/new", methods=["GET", "POST"])
def new_device():
    redir = _require_admin()
    if redir:
        return redir
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        if not name:
            flash("Name darf nicht leer sein.", "error")
            return redirect(url_for("admin.new_device"))
        try:
            made = db.create_device(
                name=name,
                default_watch_path=_optional_text(request.form.get("default_watch_path")),
                default_event_glob=_optional_text(request.form.get("default_event_glob")),
            )
            flash(f"Box '{name}' angelegt.", "success")
            return _devices_with_fresh_key(fresh={"name": made["name"], "api_key": made["api_key"]})
        except Exception as e:
            flash(f"Fehler: {e}", "error")
    events = db.list_events()  # for the dropdown
    return render_template("admin/device_edit.html", device=None, events=events, csrf_token=_csrf_token())


@bp.route("/devices/<int:device_id>/edit", methods=["GET", "POST"])
def edit_device(device_id: int):
    redir = _require_admin()
    if redir:
        return redir
    device = db.get_device_by_id(device_id)
    if not device:
        abort(404)
    if request.method == "POST":
        try:
            # Landing page fields
            landing_event_id = request.form.get("landing_event_id") or None
            if landing_event_id:
                landing_event_id = int(landing_event_id)
            fields = {
                "name": request.form.get("name", "").strip(),
                "default_watch_path": _optional_text(request.form.get("default_watch_path")),
                "default_event_glob": _optional_text(request.form.get("default_event_glob")),
                "landing_event_id": landing_event_id,
                "landing_headline": _optional_text(request.form.get("landing_headline")),
                "landing_subtext": _optional_text(request.form.get("landing_subtext")),
                "landing_bg_path": _optional_text(request.form.get("landing_bg_path")),
            }
            db.update_device(device_id, fields)
            # Handle active status separately
            db.set_device_active(device_id, "active" in request.form)
            flash("Box gespeichert.", "success")
            return redirect(url_for("admin.devices"))
        except Exception as e:
            flash(f"Fehler: {e}", "error")
    events = db.list_events()  # for the dropdown
    return render_template("admin/device_edit.html", device=device, events=events, csrf_token=_csrf_token())


@bp.route("/devices/pair", methods=["GET", "POST"])
def pair_request_page():
    """Confirm a booth's pairing request on the phone (QR code on the booth's admin page).

    Login first (second factor included); afterwards the operator lands here
    again. The page shows what asks — never a key — and one POST confirms
    (choosing or creating the box, optionally its event) or declines.
    """
    from flask import g

    from app import auth, ecosystem, phone_pairing

    code = phone_pairing.clean_code(request.args.get("c") or request.form.get("c") or "")
    if not _is_admin():
        if code:
            session["after_login"] = f"/admin/devices/pair?c={code}"
        return redirect(url_for("admin.login"))
    ecosystem.init_schema()
    row = phone_pairing.get_request(code) if code else None
    state = phone_pairing.state_of(row)
    if request.method == "POST" and state == "open":
        who = auth.admin_user()
        if request.form.get("action") == "decline":
            phone_pairing.decide(code, device_id=None, by=who)
            g._protokoll_was = f"Kopplungsanfrage von „{row['booth_name'] or 'Box'}“ abgelehnt"
            return render_template("admin/pair_request.html", code=code, row=row, state="declined_now",
                                   devices=[], events=[], csrf_token=_csrf_token())
        choice = request.form.get("device_id", "")
        if choice == "new":
            name = (request.form.get("new_name") or row["booth_name"] or "Fotobox").strip()[:60]
            device = db.create_device(name=name)
        else:
            device = db.get_device_by_id(int(choice)) if choice.isdigit() else None
        if not device:
            flash("Bitte eine Box wählen oder „Neue Box anlegen“.", "error")
            return redirect(url_for("admin.pair_request_page", c=code))
        event_id = request.form.get("event_id", "")
        if event_id.isdigit() and db.get_event_by_id(int(event_id)):
            current = db.get_device_by_id(device["id"])
            try:
                stored = json.loads(current.get("profile_json") or "{}")
            except ValueError:
                stored = {}
            ecosystem.save_device_profile(device["id"], stored, int(event_id), strict=False)
        if not phone_pairing.decide(code, device_id=int(device["id"]), by=who):
            return render_template("admin/pair_request.html", code=code, row=row, state="expired",
                                   devices=[], events=[], csrf_token=_csrf_token())
        g._protokoll_was = f"Box „{device['name']}“ gekoppelt von {who} per Handy"
        # A page of its own, not the box list: that one carries the keys.
        return render_template("admin/pair_request.html", code=code, row=row, state="confirmed_now",
                               device_name=device["name"], devices=[], events=[], csrf_token=_csrf_token())
    return render_template("admin/pair_request.html", code=code, row=row, state=state,
                           devices=db.list_devices() or [], events=db.list_events() or [],
                           csrf_token=_csrf_token())


@bp.get("/booths")
def booths():
    """Zustand aller Fotobox-PCs auf einen Blick (Ampel + Gründe)."""
    redir = _require_admin()
    if redir:
        return redir
    from app import booth as booth_mod, ecosystem

    ecosystem.init_schema()
    log_counts = ecosystem.log_counts_by_device(since_hours=24)
    rank = {"red": 0, "yellow": 1, "unknown": 2, "green": 3}
    tiles = []
    for d in (db.list_devices() or []):
        status = db.get_device_status(d["id"])
        # Same function as the MCP tool list_booths: page and assistant never disagree.
        light = booth_mod.light(d, status)
        tiles.append({
            "id": d["id"],
            "name": d["name"],
            "active": bool(d.get("active")),
            "health": light["health"],
            "reasons": light["reasons"],
            "light": light,
            "age_text": light["age_text"],
            "status": status,
            "disk_used_pct": booth_mod.disk_percent_used(status),
            "last_ip": d.get("last_ip") or "",
            "never_seen": d.get("last_heartbeat_at") is None,
            "log_errors": int((log_counts.get(int(d["id"])) or {}).get("error", 0)),
            "log_warnings": int((log_counts.get(int(d["id"])) or {}).get("warning", 0)),
        })
    tiles.sort(key=lambda t: (rank.get(t["health"], 4), t["name"].lower()))

    summary = {
        "total": len(tiles),
        "red": sum(1 for t in tiles if t["health"] == "red"),
        "yellow": sum(1 for t in tiles if t["health"] == "yellow"),
        "green": sum(1 for t in tiles if t["health"] == "green"),
        "unknown": sum(1 for t in tiles if t["health"] == "unknown"),
    }
    return render_template("admin/booths.html", tiles=tiles, summary=summary,
                           ack_label=t("admin.logs.ack_button"),
                           csrf_token=_csrf_token())


@bp.post("/devices/<int:device_id>/delete")
def delete_device(device_id: int):
    redir = _require_admin()
    if redir:
        return redir
    try:
        device = db.get_device_by_id(device_id)
        if device:
            db.delete_device(device_id)
            flash("Box gelöscht.", "success")
    except Exception as e:
        flash(f"Fehler: {e}", "error")
    return redirect(url_for("admin.devices"))


@bp.post("/devices/<int:device_id>/regen-key")
def regen_device_key(device_id: int):
    redir = _require_admin()
    if redir:
        return redir
    if not db.get_device_by_id(device_id):
        abort(404)
    fresh = db.regenerate_device_key(device_id)
    # Der alte Schlüssel ist sofort tot: Ohne Hinweis sucht man den Fehler
    # später am Event-PC statt hier.
    flash("Schlüssel erneuert. Der PC meldet sich erst wieder, wenn er neu gekoppelt ist — "
          "Kopplungscode erzeugen und dort einfügen.", "success")
    return _devices_with_fresh_key(fresh={"name": fresh["name"], "api_key": fresh["api_key"]})


@bp.post("/devices/<int:device_id>/config.json")
def device_config(device_id: int):
    """Config file for the old Windows uploader. It carries the key, so it makes a new one (S-6)."""
    redir = _require_admin()
    if redir:
        return redir
    import json
    device = db.get_device_by_id(device_id)
    if not device:
        abort(404)
    base_url = db.link_base() or request.host_url.rstrip("/")
    device = db.regenerate_device_key(device_id)
    g._protokoll_was = "config.json heruntergeladen (Schlüssel erneuert)"
    g._protokoll_ergebnis = f"Gerät #{device_id}"
    config = {
        "server_url": base_url,
        "api_key": device["api_key"],
        "device_id": device["id"],
        "device_name": device["name"],
        "watch_paths": [
            {"path": device["default_watch_path"] or "C:\\Fotobox\\Fotos", "kind": "photo"}
        ] if device["default_watch_path"] else [],
    }
    from flask import current_app
    return current_app.response_class(
        json.dumps(config, indent=2),
        mimetype="application/json",
        headers={"Content-Disposition": f"attachment; filename=config_{device['name']}.json",
                 "Cache-Control": "no-store"},
    )


# ── Änderungsprotokoll ────────────────────────────────────────
# Wer hat wann was umgestellt. Aufgezeichnet wird nicht hier, sondern durch die
# beiden Haken in ``app/__init__.py``; diese Seite liest nur. Siehe
# ``app/protokoll.py`` dazu, warum das über Schnappschüsse läuft.

@bp.get("/protokoll")
def protokoll_seite():
    redir = _require_admin()
    if redir:
        return redir
    from app import protokoll
    seite = max(1, int(request.args.get("seite", 1) or 1))
    pro_seite = 50
    suche = (request.args.get("suche") or "").strip()
    wer = (request.args.get("wer") or "").strip()
    vorgaenge = protokoll.liste(limit=pro_seite, offset=(seite - 1) * pro_seite,
                                suche=suche, wer=wer)
    return render_template("admin/protokoll.html", vorgaenge=vorgaenge, seite=seite,
                           weiter=len(vorgaenge) == pro_seite, suche=suche, wer=wer,
                           leute=protokoll.leute(), tage=protokoll.AUFBEWAHRUNG_TAGE,
                           csrf_token=_csrf_token(), active_nav="protokoll")


@bp.get("/protokoll.csv")
def protokoll_csv():
    """Zum Weitergeben an jemanden, der es prüfen soll — eine Zeile je Wert."""
    redir = _require_admin()
    if redir:
        return redir
    import csv
    from app import protokoll
    puffer = io.StringIO()
    schreiber = csv.writer(puffer, delimiter=";")
    schreiber.writerow(["Zeitpunkt (UTC)", "Wer", "IP (gekürzt)", "Methode", "Pfad",
                        "Vorgang", "Ergebnis", "Gegenstand", "Feld", "Vorher", "Nachher"])
    for v in protokoll.liste(limit=5000, suche=request.args.get("suche", "").strip(),
                             wer=request.args.get("wer", "").strip()):
        kopf = [v["at"], v["wer"], v["ip"], v["methode"], v["pfad"], v["was"], v["ergebnis"]]
        if not v["aenderungen"]:
            schreiber.writerow(kopf + ["", "", "", ""])
        for a in v["aenderungen"]:
            schreiber.writerow(kopf + [a["gegenstand"], a["feld"],
                                       a["vorher"] or "", a["nachher"] or ""])
    stamp = datetime.now().strftime("%Y-%m-%d")
    return Response(puffer.getvalue(), mimetype="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="aenderungsprotokoll-{stamp}.csv"',
                             "Cache-Control": "no-store"})
