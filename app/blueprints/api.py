"""API routes: upload endpoint for Fotobox devices, guest upload, likes."""
from __future__ import annotations

import hashlib
import hmac
import io
import logging
import secrets
from pathlib import Path

from flask import Blueprint, abort, g, jsonify, request, session

from app import booth, db, media, redact
from app.uploads import UploadRejected, max_guest_bytes

bp = Blueprint("api", __name__)
_log = logging.getLogger(__name__)


# ─── Device Upload (Fotobox → Server) ────────────────────────

@bp.post("/upload")
def upload():
    """Main upload endpoint for Fotobox devices.

    Authentication: X-API-Key header with device API key - the header only.
    Form fields: event_id (int) or event_slug (str), file (image).

    Still used by the old Windows uploader (windows_uploader/). Until
    11.11.2026 any valid key could upload into any event and the key was
    also read from the form and the query string (review S4). Now the event
    must belong to this device, as on /api/v2/booth/media.
    """
    # Authenticate device. Not from the address: a key in a URL ends up in
    # proxy logs, browser history and Referer headers.
    api_key = (request.headers.get("X-API-Key") or "").strip()
    if not api_key:
        return jsonify({"ok": False, "error": "Missing API key"}), 401

    device = db.get_device_by_api_key(api_key)
    if not device:
        return jsonify({"ok": False, "error": "Invalid API key"}), 401
    if not device.get("active"):
        return jsonify({"ok": False, "error": "Device is disabled"}), 403

    # Find event
    event = None
    event_id = request.form.get("event_id") or request.args.get("event_id")
    event_slug = request.form.get("event_slug") or request.args.get("event_slug")
    if event_id and str(event_id).isdigit():
        event = db.get_event_by_id(int(event_id))
    elif event_slug:
        event = db.get_event_by_slug(event_slug)
    if not event:
        return jsonify({"ok": False, "error": "Event not found"}), 404
    # Assigned means: through the booth profile or the sync (devices.event_id,
    # device_event_history), through /api/events/ensure, or - for devices of
    # the old uploader - "Zugewiesenes Event" in the device form
    # (landing_event_id), the only assignment that form offers.
    from app import ecosystem
    if not (ecosystem.geraet_kennt_event(int(device["id"]), int(event["id"]))
            or int(device.get("landing_event_id") or 0) == int(event["id"])):
        return jsonify({"ok": False, "error": "event not assigned to this device"}), 403

    # Get file
    f = request.files.get("file") or request.files.get("photo")
    if not f or not f.filename:
        return jsonify({"ok": False, "error": "No file provided"}), 400

    # Save and process
    try:
        result = media.save_upload(event["id"], f, source=f"box:{int(device['id'])}")
    except UploadRejected as e:
        return jsonify({"ok": False, "error": str(e)}), e.status
    except Exception as e:
        _log.error("Upload error: %s", e, exc_info=True)
        return jsonify({"ok": False, "error": "upload failed"}), 500
    try:
        # Notify if enabled
        if (db.get_setting("notify_on_upload") or "1") == "1":
            from app import notifier
            gallery_url = f"{request.host_url}gallery/{event['slug']}"
            notifier.batch_upload_notification(event["id"], event["name"], gallery_url)
    except Exception as e:                  # noqa: BLE001 — a failed notification is not a failed upload
        _log.warning("Upload notification failed: %s", e)
    return jsonify({"ok": True, "id": result.get("id"), "path": result.get("path_thumb")})


# ─── Guest Upload ─────────────────────────────────────────────

@bp.post("/guest-upload")
def guest_upload():
    """Ein Gast lädt eigene Fotos in die Galerie.

    Drei Dinge waren hier bis zur Durchsicht am 19.09.2026 falsch, und
    zusammen hieß das: Die Funktion hat nie funktioniert, hat aber Erfolg
    gemeldet.

    * Das Formular schickt ``event_slug``, gelesen wurde nur ``event_id``.
    * Der Schalter je Event (``guest_upload`` in den Galerie-Einstellungen)
      wurde von der Galerie beachtet und hier ignoriert — wer den Upload nur
      für ein Event freigab, bekam ein Feld, das 403 antwortete.
    * Die Passwortprüfung las ``event["password"]``. Diese Spalte gibt es
      nicht; sie heißt ``password_hash``. Die Bedingung war immer falsch, die
      Prüfung also wirkungslos — in eine passwortgeschützte Galerie durfte
      jeder hochladen, der die Kennung kannte.
    """
    from app import gallery_settings as gsm, sharelinks
    from app.blueprints.public import (_check_password_access, _expired_for_guest,
                                       _not_yet_live, _silent)

    slug = (request.form.get("event_slug") or "").strip()
    event_id = (request.form.get("event_id") or "").strip()
    if slug:
        event = db.get_event_by_slug(slug)
    elif event_id.isdigit():
        event = db.get_event_by_id(int(event_id))
    else:
        return jsonify({"ok": False, "error": "Missing event"}), 400

    from app import folders
    folders.apply_parent(event)
    if not event or not event.get("published") or _silent(event):
        return jsonify({"ok": False, "error": "Event not found"}), 404

    gs = gsm.for_event(event)
    global_an = (db.get_setting("guest_upload_enabled") or "0") == "1"
    if not (global_an or gs["guest_upload"]):
        return jsonify({"ok": False, "error": "Guest upload disabled"}), 403

    if not _check_password_access(event):
        return jsonify({"ok": False, "error": "Not authorized"}), 403
    if _expired_for_guest(event) or _not_yet_live(event):
        return jsonify({"ok": False, "error": "Not authorized"}), 403

    # Grace period and PIN (no. 58). Both are off unless the event sets them.
    from app import guest_upload as gu
    if not gu.is_open(event, gs):
        return jsonify({"ok": False, "error": "closed"}), 403
    if gu.has_pin(event) and not session.get(f"upload_pin_{event['id']}"):
        brake = f"uploadpin:{event['id']}:{request.remote_addr}"
        wait = sharelinks.retry_after(brake, gu.PIN_TRIES, gu.PIN_WINDOW)
        if wait:
            return jsonify({"ok": False, "error": "pin_wait", "retry_after": wait}), 429, \
                {"Retry-After": str(wait)}
        if not gu.check_pin(event, request.form.get("upload_pin", "")):
            if request.form.get("upload_pin"):
                sharelinks.record_failure(brake, gu.PIN_WINDOW)
            return jsonify({"ok": False, "error": "pin"}), 403
        sharelinks.forget(brake)
        # One right PIN per browser session is enough; the next files of the
        # same batch do not pay for the hash again.
        session[f"upload_pin_{event['id']}"] = True

    # Ein offener Upload ohne Grenze ist eine Einladung. Zwanzig Bilder je
    # Viertelstunde und Adresse reichen für einen Gast und für niemanden sonst.
    if sharelinks.too_often(f"gastupload:{event['id']}:{request.remote_addr}",
                            limit=20, window=900):
        return jsonify({"ok": False, "error": "Too many uploads"}), 429

    # Checked before the body is read: a guest's phone picture is a few MB,
    # and the request cap (MAX_CONTENT_LENGTH) is sized for booth videos.
    limit = max_guest_bytes()
    if request.content_length and request.content_length > limit + 64 * 1024:
        return jsonify({"ok": False, "error": "File too large"}), 413

    f = request.files.get("file")
    if not f or not f.filename:
        return jsonify({"ok": False, "error": "No file"}), 400

    try:
        # A phone writes where the picture was taken into it; a wedding
        # guest's home address does not belong in a shared gallery.
        result = media.save_upload(event["id"], f, max_bytes=limit, allow_video=False,
                                   strip_gps=True, source="guest")
        # Moderation is enforced by the database trigger; the answer only
        # tells the guest why the picture does not show up yet.
        row = db.get_media(int(result.get("id") or 0)) or {}
        return jsonify({"ok": True, "id": result.get("id"),
                        "pending": (row.get("review_state") == "pending")})
    except UploadRejected as e:
        return jsonify({"ok": False, "error": str(e)}), e.status
    except Exception as e:                  # noqa: BLE001
        _log.error("Guest upload error: %s", e, exc_info=True)
        return jsonify({"ok": False, "error": "upload failed"}), 500


# ─── Admin Upload ─────────────────────────────────────────────

@bp.post("/admin-upload")
def admin_upload():
    """Admin photo upload (from event edit page)."""
    if not session.get("admin_logged_in"):
        return jsonify({"ok": False, "error": "Not authenticated"}), 401
    # S-10: this route is outside the admin blueprint and its CSRF guard, so
    # it checks the same form token itself (field or X-CSRF-Token header).
    import hmac
    sent = str(request.form.get("csrf_token") or request.headers.get("X-CSRF-Token") or "")
    if not session.get("csrf_token") or not hmac.compare_digest(sent, str(session["csrf_token"])):
        return jsonify({"ok": False, "error": "csrf"}), 400

    event_id = request.form.get("event_id")
    if not event_id:
        return jsonify({"ok": False, "error": "Missing event_id"}), 400

    event = db.get_event_by_id(int(event_id))
    if not event:
        return jsonify({"ok": False, "error": "Event not found"}), 404

    f = request.files.get("file")
    if not f or not f.filename:
        return jsonify({"ok": False, "error": "No file"}), 400

    try:
        result = media.save_upload(event["id"], f, source="admin")
        return jsonify({
            "ok": True,
            "id": result.get("id"),
            "thumb_url": result.get("path_thumb") or result.get("path_webp") or "",
        })
    except UploadRejected as e:
        return jsonify({"ok": False, "error": str(e)}), e.status
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


# ─── Photo Actions ────────────────────────────────────────────

@bp.post("/photo/<int:photo_id>/like")
def like_photo(photo_id: int):
    """Toggle like on a photo - only for someone who may see it (S18).

    Until 11.11.2026 any picture id of any event took likes: protected,
    unpublished, silent, hidden or waiting for approval. The gates are the
    gallery's; the operator is exempt as everywhere.
    """
    from app.blueprints.public import (_check_password_access, _expired_for_guest, _is_admin,
                                       _not_yet_live, _silent, _wartet_auf_freigabe)
    media_row = db.get_media(photo_id)
    if not media_row:
        return jsonify({"ok": False, "error": "not found"}), 404
    from app import folders
    event = folders.apply_parent(db.get_event_by_id(media_row["event_id"]))
    if not _is_admin():
        if (not event or not event.get("published") or _silent(event)
                or int(media_row.get("hidden") or 0) or _wartet_auf_freigabe(media_row)):
            return jsonify({"ok": False, "error": "not found"}), 404
        if not _check_password_access(event) or _expired_for_guest(event) or _not_yet_live(event):
            return jsonify({"ok": False, "error": "not allowed"}), 403
    device_id = session.get("device_id")
    if not device_id:
        device_id = secrets.token_hex(8)
        session["device_id"] = device_id
    try:
        liked, count = db.toggle_like(media_row["event_id"], photo_id, device_id)
        return jsonify({"ok": True, "liked": liked, "count": count})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@bp.post("/photo/<int:photo_id>/tag")
def tag_photo(photo_id: int):
    """Farbmarkierung setzen. Admin immer; Gäste nur im Abstimmungsmodus mit Freigabe."""
    from app import gallery_settings as gsm
    if not request.is_json:            # JSON erzwingt bei fremden Seiten einen Preflight → kein CSRF
        return jsonify({"ok": False, "error": "json required"}), 415
    body = request.get_json(silent=True) or {}
    tag = (body.get("tag") or "").strip()
    if tag and tag not in gsm.TAG_IDS:
        return jsonify({"ok": False, "error": "unknown tag"}), 400
    row = db.get_media(photo_id)
    if not row:
        return jsonify({"ok": False}), 404
    from app import folders
    event = folders.apply_parent(db.get_event_by_id(row["event_id"]))
    if not session.get("admin_logged_in"):
        gs = gsm.for_event(event)
        mode = session.get(f"gallery_mode_{event['id']}") if gs["allow_mode_switch"] else None
        if not (gs["color_tags"] and (mode or gs["mode"]) == "proofing" and event.get("published")):
            return jsonify({"ok": False, "error": "not allowed"}), 403
        from app.blueprints.public import _check_password_access
        if not _check_password_access(event):
            return jsonify({"ok": False, "error": "not allowed"}), 403
    con = db.get_db()
    con.execute("UPDATE media SET color_tag=? WHERE id=?", (tag or None, photo_id))
    con.commit()
    con.close()
    return jsonify({"ok": True, "tag": tag})


# ─── Booth-Heartbeat ──────────────────────────────────────────

@bp.post("/booth/heartbeat")
def booth_heartbeat():
    """Der Agent auf dem Fotobox-PC meldet seinen Zustand.

    Bewusst als ausgehender Aufruf des Booths gebaut: Fotobox-Rechner stehen
    hinter Venue-WLAN oder LTE, ohne feste IP und ohne Portfreigabe. Der Server
    erreicht sie nie — sie fragen bei uns nach. Die Antwort enthält später die
    Liste offener Aufträge (Templates, Fernbefehle); heute ist sie leer, damit
    der Agent schon jetzt dagegen bauen kann.
    """
    api_key = (request.headers.get("X-API-Key") or "").strip()
    # Until 0.4 the key was also taken from ?api_key=. An address ends up in
    # proxy and access logs; booth and sync program have always sent the
    # header, so whoever still uses the address is told what to change.
    if not api_key and request.args.get("api_key"):
        return jsonify({"ok": False, "error": "device key only in the X-API-Key header, "
                                              "not in the address — please update the client"}), 400
    if not api_key:
        return jsonify({"ok": False, "error": "missing api key"}), 401
    device = db.get_device_by_api_key(api_key)
    if not device:
        return jsonify({"ok": False, "error": "invalid api key"}), 401

    raw = request.get_json(silent=True) or {}
    status = booth.sanitize_payload(raw)
    verdict = booth.evaluate_fresh(status)
    health = verdict["health"]

    previous = (device.get("last_health") or "")
    try:
        db.record_heartbeat(device["id"], status, health, request.remote_addr)
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

    # Nur beim Wechsel nach Rot melden — sonst klingelt es im Minutentakt.
    if health == "red" and previous != "red":
        try:
            from app import notifier
            notifier.notify_booth_problem(device["name"], verdict["reasons"])
        except Exception:
            pass

    return jsonify({
        "ok": True,
        "health": health,
        "reasons": verdict["reasons"],
        "jobs": [],              # Platzhalter für Etappe 2 (Fernbefehle)
        "poll_seconds": 30,
    })


# ─── Health ───────────────────────────────────────────────────

@bp.get("/status")
def status():
    events = db.list_events() or []
    return jsonify({
        "ok": True,
        "events": len(events),
        "version": "2.0.0",
    })


# ─── Analytics Tracking ───────────────────────────────────────

@bp.route("/track", methods=["POST"])
def track():
    """Analytics-Tracking Endpoint."""
    data = request.get_json(silent=True) or {}
    event_id = data.get("event_id")
    action = data.get("action", "")
    media_id = data.get("media_id")

    if not event_id or not action:
        return jsonify({"ok": False, "error": "event_id and action required"}), 400

    # Diese Liste muss zu dem passen, was die Galerie tatsächlich schickt
    # (siehe trackAction in public/gallery.html) — sonst laufen Fotoansichten
    # und Teilen-Klicks ins Leere und fehlen in jeder Auswertung.
    ALLOWED_ACTIONS = {
        "view_gallery", "view_photo", "download", "zip_download", "download_selection",
        "like", "unlike", "share_photo", "share_whatsapp", "share_whatsapp_link",
        "share_link", "whatsapp_request", "guest_upload",
    }
    if action not in ALLOWED_ACTIONS:
        return jsonify({"ok": False, "error": "unknown action"}), 400

    # Zählen darf nur, wer die Galerie auch sehen dürfte. Ohne diese Prüfung
    # ist jede Auswertung und jeder Kundenbericht mit einer Zeile curl
    # fälschbar — für jede Event-Kennung. Gefunden am 19.09.2026.
    from app import sharelinks
    from app.blueprints.public import _check_password_access, _silent
    event = db.get_event_by_id(int(event_id)) if str(event_id).isdigit() else None
    if not event or not event.get("published") or _silent(event):
        return jsonify({"ok": False, "error": "unknown event"}), 404
    if not _check_password_access(event):
        return jsonify({"ok": False, "error": "unknown event"}), 404
    if sharelinks.too_often(f"track:{event_id}:{request.remote_addr}", limit=300, window=900):
        return jsonify({"ok": False, "error": "too many"}), 429

    try:
        db.add_stat(int(event_id), action, media_id)
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


# ─── Auto-Event API (Release 2.3) ─────────────────────────

@bp.route("/events/ensure", methods=["POST"])
def ensure_event():
    """Auto-Event-Erstellung für Windows-Uploader Auto-Mode.
    
    Request:
        X-Api-Key header with device API key
        JSON: {"name": "Event Name", "slug": "optional-slug"}
    
    Response:
        {"ok": True, "event_id": int, "slug": str, "upload_token": str, "created": bool}
    """
    api_key = request.headers.get("X-Api-Key", "").strip()
    if not api_key:
        return jsonify({"ok": False, "error": "Missing X-Api-Key header"}), 401

    device = db.get_device_by_api_key(api_key)
    if not device:
        return jsonify({"ok": False, "error": "Invalid API key"}), 401

    data = request.get_json(silent=True) or {}
    event_name = (data.get("name") or "").strip()
    slug = (data.get("slug") or "").strip()

    if not event_name:
        return jsonify({"ok": False, "error": "name is required"}), 400

    # Slug generieren wenn nicht angegeben
    if not slug:
        import re
        slug = re.sub(r"[^a-z0-9-]", "-", event_name.lower())
        slug = re.sub(r"-+", "-", slug).strip("-")

    # Update device last_seen
    db.touch_device_seen(device["id"], request.remote_addr)

    # Vorhandenes Event zurückgeben — aber nicht jedem seinen Schlüssel.
    #
    # Der Slug steht in jeder Galerie-Adresse. Wer irgendeinen gültigen
    # Box-Schlüssel hat, bekam hier bis zum 19.09.2026 den ``upload_token``
    # jedes beliebigen fremden Events und durfte damit in dessen Galerie
    # hochladen. Der Schlüssel geht jetzt nur an eine Box, die zu diesem Event
    # gehört — gerade zugeordnet oder irgendwann einmal (``device_event_history``,
    # damit eine Box nach dem Wechsel noch nachliefern darf).
    existing = db.get_event_by_slug(slug)
    if existing:
        from app import ecosystem
        darf = ecosystem.geraet_kennt_event(int(device["id"]), int(existing["id"]))
        antwort = {"ok": True, "event_id": existing["id"], "slug": existing["slug"],
                   "created": False}
        if darf:
            antwort["upload_token"] = existing.get("upload_token") or ""
        else:
            antwort["upload_token"] = ""
            antwort["hint"] = ("Dieses Event gehört einer anderen Box. "
                               "Im Admin der Galerie zuordnen, dann klappt der Upload.")
        return jsonify(antwort)

    # Neues Event erstellen
    import secrets
    upload_token = secrets.token_urlsafe(32)
    event_id = db.create_event(
        name=event_name,
        slug=slug,
        upload_token=upload_token,
    )
    # The box that created the event may fill it: /api/upload only takes
    # events of the device (device_event_history), like the v2 API.
    from app import ecosystem
    ecosystem.init_schema()
    con = db.get_db()
    try:
        con.execute("INSERT OR IGNORE INTO device_event_history(device_id, event_id) VALUES (?, ?)",
                    (int(device["id"]), int(event_id)))
        con.commit()
    finally:
        con.close()

    return jsonify({
        "ok": True,
        "event_id": event_id,
        "slug": slug,
        "upload_token": upload_token,
        "created": True,
    }), 201


# ─── Email Subscription API (Release 2.3) ────────────────

@bp.route("/notify/subscribe", methods=["POST"])
def notify_subscribe():
    """E-Mail-Benachrichtigung abonnieren (Double Opt-In).
    
    Request:
        JSON: {"eventSlug": "str", "email": "str"}
    
    Response:
        {"ok": True, "message": "Confirmation email sent"}
    """
    import secrets
    data = request.get_json(silent=True) or {}
    event_id = data.get("event_id")
    slug = (data.get("eventSlug") or "").strip()
    email = (data.get("email") or "").strip().lower()

    if not email:
        return jsonify({"ok": False, "error": "email required"}), 400

    import re
    if not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", email):
        return jsonify({"ok": False, "error": "Invalid email"}), 400

    # Handle both event_id and slug
    if event_id:
        event = db.get_event_by_id(event_id)
    elif slug:
        event = db.get_event_by_slug(slug)
    else:
        return jsonify({"ok": False, "error": "event_id or eventSlug required"}), 400
    
    # Eine unveröffentlichte oder stille Galerie gibt es nach außen nicht —
    # auch nicht als Antwort, die ihre Existenz bestätigt. Und ohne Grenze ist
    # das hier ein Formular, mit dem sich fremde Postfächer zuschütten lassen.
    from app import sharelinks
    from app.blueprints.public import _silent
    if not event or not event.get("published") or _silent(event):
        return jsonify({"ok": False, "error": "Event not found"}), 404
    from app import gallery_settings as gsm
    if not gsm.for_event(event)["notify_signup"]:
        # The signup is switched off for this gallery: the form is gone, so is the door.
        return jsonify({"ok": False, "error": "Signup switched off"}), 403
    if sharelinks.too_often(f"notify:{request.remote_addr}", limit=10, window=3600):
        return jsonify({"ok": False, "error": "Too many requests"}), 429

    token = secrets.token_urlsafe(32)
    sub_id = db.add_subscription(event["id"], email, token)

    if not sub_id:
        return jsonify({"ok": False, "error": "Email already subscribed"}), 409

    # Send confirmation email
    try:
        from app import emailer
        base_url = db.base_url() or request.host_url.rstrip("/")
        confirm_url = f"{base_url}/notify/confirm/{token}"
        emailer.send_confirmation_email(email, event["name"], confirm_url)
    except Exception as e:
        _log.warning("Email send failed: %s", redact.text(e))   # the SMTP answer may quote the address

    return jsonify({"ok": True, "message": "Bestätigungsmail gesendet."})


@bp.route("/notify/confirm/<token>", methods=["GET"])
def notify_confirm_redirect(token: str):
    """Shortcut: /api/notify/confirm/<token> → redirect to /notify/confirm/<token>"""
    from flask import redirect, url_for
    return redirect(url_for("public.notify_confirm", token=token))

