"""API v2 für die Photobox Booth + öffentliche Durchgangs-Seite ``/p/<code>``.

    POST /api/v2/booth/sync         Heartbeat, Profil, Assets, Aufträge, Löschungen
    POST /api/v2/booth/media        Upload (idempotent über uid+variant)
    GET  /api/v2/booth/assets/<f>   Asset-Download für die Box
    GET  /p/<uid>-<sig>             QR-Ziel: Durchgang, „unterwegs", Weiterleitung oder Statusseite
"""
from __future__ import annotations

import hashlib
import json
import logging
import secrets
import time
from pathlib import Path

from flask import (Blueprint, abort, current_app, jsonify, redirect, render_template, request,
                   send_file)
from PIL import Image, ImageOps

from app import booth, db, ecosystem, media, uploads

bp = Blueprint("booth_v2", __name__)


def _claim_days(claim) -> int:
    """The period a booth asks for, else the account default (app/retention.py)."""
    from app import retention
    return retention.read_days(claim.get("retention_days")) or retention.default_days()


@bp.before_request
def _guest_page_globals():
    """The QR page is a guest page like the gallery: the guest's language,
    the language switch and the operator's name in the header. Without this
    it was always German and branded "Photobox Gallery"."""
    if request.method == "GET" and request.path.startswith("/p/") and not request.path.endswith(".json"):
        from app.blueprints.public import _inject_globals
        _inject_globals()


log = logging.getLogger(__name__)

KINDS = {"photo", "collage", "print", "gif", "boomerang", "video", "original"}
VARIANTS = {"web", "original"}
IMAGE_EXT = {".jpg", ".jpeg", ".png", ".webp", ".gif"}
VIDEO_EXT = {".mp4", ".webm", ".mov"}


def _device():
    key = (request.headers.get("X-API-Key") or "").strip()
    if not key:
        return None
    dev = db.get_device_by_api_key(key)
    if not dev or not dev.get("active"):
        return None
    return dev


def _base_url() -> str:
    return db.base_url() or request.host_url.rstrip("/")


def _pair_base():
    """Address handed to a booth while pairing; ``None`` → answer 503 (S-5, fail closed)."""
    return db.link_base() or None


_NO_BASE = ({"ok": False, "error": "Galerie-Adresse nicht eingerichtet (BASE_URL)"}, 503)


# ── Going down on purpose ─────────────────────────────────────

@bp.post("/api/v2/booth/going-down")
def going_down():
    """The box ends on purpose (quit, restart, update, Windows shutting down).

    Only its own device key; it can only write a note about itself, and
    nothing in the body becomes an address — the silence alarm mails the
    recipient the admin set (app/silence_alarm.py).
    """
    dev = _device()
    if not dev:
        return jsonify({"ok": False, "error": "invalid api key"}), 401
    body = request.get_json(silent=True) or {}
    expect = body.get("expect_back")
    from app import silence_alarm
    noted = silence_alarm.note_going_down(
        dev["id"], str(body.get("reason") or "")[:40],
        expect if isinstance(expect, bool) else None)
    return jsonify({"ok": True, **noted})


# ── Sync ──────────────────────────────────────────────────────

@bp.post("/api/v2/booth/sync")
def sync():
    ecosystem.init_schema()
    dev = _device()
    if not dev:
        return jsonify({"ok": False, "error": "invalid api key"}), 401
    body = request.get_json(silent=True) or {}

    status = booth.sanitize_payload(body.get("status") or {})
    verdict = booth.evaluate_fresh(status)
    previous = dev.get("last_health") or ""
    db.record_heartbeat(dev["id"], status, verdict["health"], request.remote_addr)
    # Die Box meldet Gesamtzähler; daraus wird der Zuwachs dieses Tages.
    try:
        from app import stats as stats_mod
        stats_mod.note_heartbeat(dev["id"], status)
    except Exception:                                 # noqa: BLE001 — Zahlen kosten keinen Heartbeat
        current_app.logger.exception("Tageszahlen nicht fortgeschrieben")
    if verdict["health"] == "red" and previous != "red":
        try:
            from app import notifier
            notifier.notify_booth_problem(dev["name"], verdict["reasons"])
        except Exception:
            pass

    # Ohne Netz angelegtes Event nachtragen, bevor das Profil zurückgeht —
    # sonst bekäme die Box noch einmal das alte Event geschickt.
    uebernommen = None
    if body.get("event_claim"):
        uebernommen = event_von_der_box(dev, body["event_claim"] or {})
        if uebernommen:
            dev = db.get_device_by_id(dev["id"]) or dev

    ecosystem.finish_jobs(dev["id"], body.get("job_results") or [])
    # Das Sync-Programm schickt mehr als die Ampelwerte: Warteschlange, Aufträge,
    # Ziele, Protokoll. Das ist die Grundlage der Fernwartungsseite.
    ecosystem.save_sync_report(dev["id"], body.get("sync"))
    # Warnungen und Fehler der Box landen im Admin-Interface — so ist eine Box
    # aus der Ferne zu beurteilen, ohne davorzustehen.
    ecosystem.add_device_logs(dev["id"], body.get("logs") or [])

    deleted_ack = []
    for uid in (body.get("deleted") or [])[:200]:
        uid = str(uid)
        if not uid.replace(".", "").isalnum():
            continue
        removed = ecosystem.delete_session(uid, dev["id"])
        for row in removed:
            if row.get("storage") == "remote" and row.get("remote_path"):
                from app import storage
                storage.queue_remote_delete(row["remote_path"])
            for rel in (row.get("path_original"), row.get("path_webp"), row.get("path_thumb"), row.get("path_poster")):
                if rel:
                    media.delete_rel_path(rel)
            media.clear_download_cache(row["id"])
        # The rest of the run may still be in the booth's queue (or another
        # target's). Note it as deleted — but only if this device owned it, or
        # nobody did yet: a foreign key must not be able to block someone
        # else's run.
        if removed or not ecosystem.session_known_elsewhere(uid):
            ecosystem.remember_deleted(uid, event_id=(removed[0]["event_id"] if removed else dev.get("event_id")),
                                       reason="booth", scope="session")
        # Beleg: Die Box hat gelöscht, die Galerie zieht nach — beides gehört ins Protokoll.
        from app import retention
        event = db.get_event_by_id(dev["event_id"]) if dev.get("event_id") else None
        retention.record(scope="session", reason="booth", actor="device", session_uid=uid,
                         event_slug=(event or {}).get("slug", ""),
                         note=f"Box {dev.get('name') or dev['id']}")
        deleted_ack.append(uid)

    # The booth deleted these itself: nothing to tell it back.
    ecosystem.confirm_deletions(dev["id"], deleted_ack)
    # Runs the booth has removed after we listed them as deleted in the gallery.
    # An old booth never sends this; it simply keeps getting the same list.
    ecosystem.confirm_deletions(dev["id"], body.get("deleted_seen"))

    # Vorlagen, die an der Box gebaut wurden: prüfen, ablegen, quittieren.
    # Erst mit der Quittung vergisst die Box ihren Zettel.
    vorlagen_ack = _vorlagen_von_der_box(dev, body.get("templates"))

    # A booth just paired offers its own profile as the starting point; it is
    # taken only while the gallery's profile for it is untouched.
    saat = None
    if "profile_seed" in body:
        try:
            saat = ecosystem.seed_profile_from_booth(db.get_device_by_id(dev["id"]) or dev, body.get("profile_seed"))
        except Exception:                             # noqa: BLE001 — a seed never costs the heartbeat
            current_app.logger.exception("Startprofil der Box nicht übernommen")
            saat = "refused"

    dev = db.get_device_by_id(dev["id"])
    profile = ecosystem.profile_for_box(dev, _base_url())
    resp = {
        "ok": True, "health": verdict["health"], "reasons": verdict["reasons"],
        "server_time": time.time(), "poll_seconds": 30,
        "jobs": ecosystem.take_jobs(dev["id"]), "deleted_ack": deleted_ack,
        # Deleted in the gallery (guest, admin, retention): the booth removes
        # its copy and confirms with "deleted_seen" in its next call.
        "deleted_sessions": ecosystem.deletions_for_device(dev["id"]),
        "assets": [{"file": a["file"], "sha256": a["sha256"], "kind": a["kind"],
                    "url": f"/api/v2/booth/assets/{a['file']}"} for a in ecosystem.assets_for_profile(profile)],
    }
    if vorlagen_ack:
        resp["templates_ack"] = vorlagen_ack
    if saat:
        resp["profile_seed"] = saat
    # Gibt es eine neuere Fassung der Box-Software? Angeboten, nicht erzwungen:
    # Mitten im Abend startet sich kein Rechner selbst neu.
    from app import software as software_mod
    # "photobox-booth 0.9.1" → "0.9.1"; eine Box ohne Angabe gilt als uralt.
    laufend = (str((body.get("status") or {}).get("booth_version") or "").split() or ["0"])[-1]
    angebot = software_mod.angebot("booth", laufend, _base_url())
    if angebot:
        resp["update"] = angebot
    if int(body.get("profile_version") or -1) != profile["version"]:
        resp["profile"] = profile
    # Wartet noch ein Auftrag, fragt die Box gleich wieder nach: Wer im Admin auf
    # einen Knopf drückt, soll nicht eine halbe Minute auf die Antwort warten.
    if ecosystem.jobs_waiting(dev["id"]):
        resp["poll_seconds"] = 5
    # A remote-control session is open: poll the small job door this often
    # (0 = none). An older booth ignores the key and keeps its 5 seconds.
    from app import remote_control
    resp["fast_seconds"] = remote_control.fast_seconds(dev["id"])
    if uebernommen:
        # Quittung: erst damit vergisst die Box ihren lokalen Zettel.
        resp["event_claimed"] = {"slug": uebernommen["slug"], "name": uebernommen["name"],
                                 "share_key": uebernommen.get("share_key") or ""}
    return jsonify(resp)


@bp.post("/api/v2/booth/jobs")
def jobs_fast():
    """The small door for a booth under live remote control (app/remote_control.py).

    Results in, jobs out, and how soon to ask again — nothing else, so a poll
    every second or two costs a few queries and holds a worker for
    milliseconds. Same key check as ``sync``; a booth only ever sees its own
    jobs. ``fast_seconds`` 0 tells the booth to stop and use the normal sync.
    """
    ecosystem.init_schema()
    dev = _device()
    if not dev:
        return jsonify({"ok": False, "error": "invalid api key"}), 401
    body = request.get_json(silent=True) or {}
    results = body.get("job_results") or []
    ecosystem.finish_jobs(dev["id"], results if isinstance(results, list) else [])
    from app import remote_control
    remote_control.note_poll(dev["id"])
    return jsonify({"ok": True, "server_time": time.time(), "jobs": ecosystem.take_jobs(dev["id"]),
                    "fast_seconds": remote_control.fast_seconds(dev["id"])})


# ── Gästefotos per Mail ───────────────────────────────────────

@bp.post("/api/v2/booth/mail")
def booth_mail():
    """Die Box lässt die Galerie eine Mail verschicken.

    Warum nicht die Box selbst: Dann bräuchte jede Box Zugangsdaten eines
    Postfachs, und am Veranstaltungsort ist der SMTP-Port oft gesperrt. So
    liegen die Zugangsdaten an einer Stelle, der Absender ist immer derselbe
    (wichtig für SPF und DKIM), und die Box braucht nur das, was sie ohnehin
    hat: ihren Geräteschlüssel und die Galerie.

    Das Bild kommt aus der Galerie selbst — es wurde ohnehin hochgeladen.
    Fehlt es noch, antwortet die Galerie mit 409, und die Box versucht es
    später erneut; ihre Warteschlange kann das seit jeher.
    """
    ecosystem.init_schema()
    dev = _device()
    if not dev:
        return jsonify({"ok": False, "error": "invalid api key"}), 401
    from app import emailer

    body = request.get_json(silent=True) or {}
    # Exactly one recipient (S-3): a string that is one plausible address.
    # A list, a comma or a space is how one call becomes a mailing.
    raw_to = body.get("to")
    to = raw_to.strip() if isinstance(raw_to, str) else ""
    if not emailer.valid_address(to) or any(c in to for c in ",; \t<>"):
        return jsonify({"ok": False, "error": "keine gültige Adresse"}), 400

    # Every mail belongs to a run of this box: without one, the call would be
    # a free relay with the gallery's sender address.
    session_uid = str(body.get("session_uid") or "").strip()
    if not session_uid or len(session_uid) > 80 or not session_uid.replace(".", "").isalnum():
        return jsonify({"ok": False, "error": "Durchgang fehlt"}), 400
    want_files = str(body.get("attach") or "web").lower() != "link"
    alle = ecosystem.session_media(session_uid)
    # Only runs of this booth's own events: the key of one booth is no key to
    # every run in the gallery. Hidden rows count too (originals, review).
    con = db.get_db()
    try:
        fremd = [int(r[0]) for r in con.execute(
            "SELECT DISTINCT event_id FROM media WHERE session_uid=? OR uid=?", (session_uid, session_uid))]
    finally:
        con.close()
    if any(not ecosystem.geraet_kennt_event(dev["id"], eid) for eid in fremd) or ecosystem.is_deleted(session_uid):
        return jsonify({"ok": False, "error": "Aufnahme nicht gefunden"}), 404
    if not emailer.smtp_configured():
        return jsonify({"ok": False, "error": "kein Postausgang eingerichtet"}), 503
    from app import sharelinks
    event_key = fremd[0] if fremd else (dev.get("event_id") or 0)
    if (sharelinks.too_often(f"boxmail:{dev['id']}", limit=MAILS_PER_HOUR, window=3600)
            or sharelinks.too_often(f"boxmail-event:{event_key}", limit=MAILS_PER_EVENT, window=86400)
            or sharelinks.too_often(f"boxmail-run:{dev['id']}:{session_uid}", limit=MAILS_PER_RUN, window=86400)):
        return jsonify({"ok": False, "error": "zu viele Mails in kurzer Zeit"}), 429
    items = [m for m in alle if m.get("variant") == "web"]
    if want_files and session_uid and not items:
        return jsonify({"ok": False, "error": "Aufnahme noch nicht in der Galerie"}), 409

    event = db.get_event_by_id(fremd[0] if fremd else dev["event_id"]) if (fremd or dev.get("event_id")) else None
    attachments = []
    if want_files:
        # The event's file-name rule names the attachments the way a download
        # or a ZIP names the picture (app/dateinamen.py, same second "_2"…);
        # "original" keeps the stored file's name as before. The standard
        # library writes a non-ASCII name as RFC 2231 into the MIME header.
        from app import dateinamen, gallery_settings as gsm
        gs = gsm.for_event(event) if event else {}
        namen = (dateinamen.event_datetime_namen(event)
                 if event and dateinamen.regel_fuer(event, gs) == "event_datetime" else None)
        for m in items[:4]:                       # vier Bilder reichen, sonst platzt die Mail
            rel = m.get("path_webp") or m.get("path_original") or ""
            f = media.data_dir() / rel if rel else None
            if f and f.is_file() and f.stat().st_size < 8_000_000:
                name = (dateinamen.auslieferungsname(event, m, f.name, gs=gs, namen=namen)
                        if namen is not None else None) or f.name
                attachments.append((name, f.read_bytes(), "image/jpeg" if f.suffix.lower() in (".jpg", ".jpeg") else "image/png"))

    link = ""
    if session_uid and event:
        link = f"{db.base_url_for(event) or _base_url()}{ecosystem.session_path(event, session_uid)}"
    # Subject and text come from the gallery (the box's profile, section
    # "mail", set in the admin), never from the request (S-3): otherwise a
    # device key sends any text from the gallery's address. The box sends
    # the same values it got from there, so nothing changes for a guest.
    vorlage = (ecosystem.device_profile(dev) or {}).get("mail") or {}
    # One line: a line break in a subject is how a header is smuggled in.
    # The admin form promises {event} and {link} in subject and text; the booth
    # replaces them on its own path, and until 15.11.2026 this path sent them
    # to guests as literal "{link}" (the link was appended a second time).
    event_name = str((event or {}).get("name") or "")
    subject = " ".join(str(vorlage.get("subject") or "").replace("{event}", event_name).split()) or "Deine Fotos"
    text = str(vorlage.get("body") or "").replace("{link}", link).replace("{event}", event_name).strip()
    if not text:
        text = "Hallo,\n\nhier sind deine Fotos.\n"
    if link and link not in text:
        text += f"\n\nAlle Aufnahmen dieses Durchgangs: {link}\n"
    try:
        emailer.send_text_mail(to_email=to, subject=subject[:200], body=text,
                               attachments=attachments)
    except Exception as e:
        # Vorübergehend oder dauerhaft — das entscheidet die Box anhand des Codes.
        return jsonify({"ok": False, "error": str(e)[:200]}), 502
    # No part of the address: the device log is kept 30 days and read by
    # whoever looks after the booth (S16).
    ecosystem.add_device_logs(dev["id"], [{"level": "info", "code": "mail",
                                           "message": f"Foto-Mail verschickt ({len(attachments)} Anhänge)"}])
    return jsonify({"ok": True, "attached": len(attachments), "link": link})


# ── Events von der Box aus ────────────────────────────────────
#
# Der Fall, für den das hier existiert: Der Betreiber steht mit der Box am
# Veranstaltungsort, und das Event ist im Web-Backend noch nicht angelegt.
# Ohne diese drei Aufrufe müsste er sich erst irgendwo einloggen — mit einem
# Telefon, das im Festzelt kein Netz hat. Also kann die Box es selbst:
# vorhandene Events sehen, eines übernehmen oder ein neues anlegen.
#
# Berechtigung: der Geräteschlüssel. Er gehört ohnehin zu dieser Installation,
# und was er kann, kann der Betreiber auch im Backend. Ein fremder Schlüssel
# kommt hier gar nicht erst hinein.

EVENT_NAME_MAX = 80
#: Mails one booth may have the gallery send per hour. A busy evening needs a
#: few dozen; a stolen device key must not turn the mailbox into a relay.
#: Lowered from 120 on 15.11.2026 (S-3), together with the two caps below.
MAILS_PER_HOUR = 60
#: Mails per run in 24 h: a guest sends to themselves and maybe a friend.
MAILS_PER_RUN = 3
#: Mails per event in 24 h, over all its boxes: a large wedding stays below.
MAILS_PER_EVENT = 400
EVENTS_MAX = 50


def _event_kurz(event: dict, dev: dict) -> dict:
    """Was die Box über ein Event wissen muss — nicht mehr."""
    return {"id": event["id"], "slug": event["slug"], "name": event["name"],
            "created_at": str(event.get("created_at") or "")[:10],
            "photos": int(event.get("photos_count") or 0),
            "current": bool(dev.get("event_id") == event["id"])}


def event_von_der_box(dev: dict, claim: dict) -> Optional[dict]:
    """Ein Event, das die Box angelegt hat, in die Galerie übernehmen.

    Die Box kann das auch ohne Netz: Sie erzeugt Name, Slug und vor allem den
    Signaturschlüssel selbst und druckt sofort QR-Codes. Kommt später eine
    Verbindung zustande, holt dieser Weg das Event nach.

    Der Schlüssel ist dabei die Identität, nicht der Slug: Er steckt in jedem
    QR-Code des Abends. Ist der Slug schon vergeben, bekommt das Event einen
    anderen — die gedruckten Karten funktionieren trotzdem, weil sie über die
    Signatur aufgelöst werden. Zweimal dieselbe Meldung legt nichts doppelt an.
    """
    from app.blueprints.admin import _slugify

    name = str(claim.get("name") or "").strip()[:EVENT_NAME_MAX]
    key = str(claim.get("share_key") or "").strip()[:120]
    if len(name) < 2 or len(key) < 16:
        return None
    vorhanden = ecosystem.event_by_share_key(key)
    if vorhanden:
        # The same claim again (the acknowledgement got lost) is fine; a key
        # that belongs to another box's event is not a way into that event.
        if not ecosystem.geraet_kennt_event(dev["id"], vorhanden["id"]):
            log.warning("Box %s claimed an event it does not own; refused", dev.get("id"))
            return None
        _auf_event_stellen(dev, vorhanden["id"])
        return vorhanden
    slug = _slug_frei(_slugify(str(claim.get("slug") or "") or name))
    event_id = db.create_event(slug=slug, name=name, upload_token=secrets.token_urlsafe(12),
                               retention_days=_claim_days(claim))
    # Wer die Bilder sehen darf, ist eine Entscheidung und keine Voreinstellung.
    # Bis zum 20.09.2026 stand hier published=1: Eine Box, die ihr Event selbst
    # anlegt, machte damit die Galerie fuer jeden lesbar, der die Adresse
    # kennt — ohne dass irgendwer das gewaehlt haette. Der Gast kommt ueber
    # seinen QR-Code auch an eine unveroeffentlichte Galerie an seine eigenen
    # Aufnahmen; veroeffentlicht heisst nur „alle sehen alles".
    gemeinsam = str(claim.get("galerie") or "privat").lower() == "gemeinsam"
    con = db.get_db()
    con.execute("UPDATE events SET published=?, share_key=? WHERE id=?",
                (1 if gemeinsam else 0, key, event_id))
    con.commit()
    con.close()
    _auf_event_stellen(dev, event_id)
    ecosystem.add_device_logs(dev["id"], [{"level": "info", "code": "event",
                                           "message": f"Event „{name}“ von der Box übernommen"}])
    return db.get_event_by_id(event_id)


@bp.get("/api/v2/booth/events")
def booth_events():
    """Welche Events gibt es — und auf welchem steht diese Box gerade?"""
    ecosystem.init_schema()
    dev = _device()
    if not dev:
        return jsonify({"ok": False, "error": "invalid api key"}), 401
    try:
        alle = db.list_events_with_counts() or []
    except Exception:
        alle = db.list_events() or []
    # Only the box's own events (S-1, 15.11.2026). Until then every event of
    # the gallery was listed and selectable, and selecting one hands its
    # signing key out with the next profile: one leaked device key opened
    # every gallery. "Own" is what the admin assigned, what the box created
    # or claimed itself — device_event_history.
    eigene = set(ecosystem.device_event_ids(dev["id"]))
    if dev.get("event_id"):
        eigene.add(int(dev["event_id"]))
    alle = [e for e in alle if int(e["id"]) in eigene]
    return jsonify({"ok": True, "current": dev.get("event_id"),
                    "events": [_event_kurz(e, dev) for e in alle[:EVENTS_MAX]]})


def _slug_frei(basis: str) -> str:
    """Ein Slug, den es noch nicht gibt. Am Abend zählt, dass es weitergeht."""
    basis = basis or secrets.token_hex(3)
    slug, n = basis, 2
    while db.get_event_by_slug(slug):
        slug = f"{basis}-{n}"
        n += 1
        if n > 99:
            return f"{basis}-{secrets.token_hex(2)}"
    return slug


@bp.post("/api/v2/booth/events")
def booth_create_event():
    """Neues Event anlegen und diese Box gleich daraufstellen."""
    from app.blueprints.admin import _slugify

    ecosystem.init_schema()
    dev = _device()
    if not dev:
        return jsonify({"ok": False, "error": "invalid api key"}), 401
    body = request.get_json(silent=True) or {}
    name = str(body.get("name") or "").strip()[:EVENT_NAME_MAX]
    if len(name) < 2:
        return jsonify({"ok": False, "error": "Das Event braucht einen Namen"}), 400
    # Bringt die Box einen eigenen Signaturschlüssel mit, gilt ihrer: Dann hat
    # sie schon ohne Netz QR-Codes gedruckt, und die müssen weiter aufgehen.
    claim = {**body, "name": name, "share_key": str(body.get("share_key") or "").strip()
             or secrets.token_urlsafe(24)}
    event = event_von_der_box(dev, claim)
    if not event:
        return jsonify({"ok": False, "error": "Das Event braucht einen Namen"}), 400
    return jsonify({"ok": True, "event": _event_kurz({**event, "photos_count": 0},
                                                     {**dev, "event_id": event["id"]})})


@bp.post("/api/v2/booth/events/select")
def booth_select_event():
    """Diese Box auf ein vorhandenes Event stellen."""
    ecosystem.init_schema()
    dev = _device()
    if not dev:
        return jsonify({"ok": False, "error": "invalid api key"}), 401
    body = request.get_json(silent=True) or {}
    event = None
    if body.get("event_id"):
        try:
            event = db.get_event_by_id(int(body["event_id"]))
        except (TypeError, ValueError):
            event = None
    elif body.get("slug"):
        event = db.get_event_by_slug(str(body["slug"]).strip())
    # A foreign event answers exactly like a missing one: the box learns
    # nothing about events that are not its own.
    if not event or not ecosystem.geraet_kennt_event(dev["id"], event["id"]):
        return jsonify({"ok": False, "error": "Event nicht gefunden"}), 404
    _auf_event_stellen(dev, event["id"])
    return jsonify({"ok": True, "event": _event_kurz(event, {**dev, "event_id": event["id"]})})


def _auf_event_stellen(dev: dict, event_id: int) -> None:
    """Gerät dem Event zuordnen — Profil bleibt, nur die Zuordnung wechselt.

    Die Profilversion steigt dabei, damit die Box beim nächsten Sync merkt,
    dass sie ein neues Event (und damit einen neuen Signaturschlüssel) hat.
    """
    profil = ecosystem.device_profile(dev) or {}
    ecosystem.save_device_profile(dev["id"], profil, event_id, strict=False)


# ── Kopplung ──────────────────────────────────────────────────

# Ein Kopplungscode hat 40 Bit und lebt eine Viertelstunde. Trotzdem soll
# niemand in Ruhe durchprobieren können: nach zehn Fehlversuchen ist für diese
# IP zehn Minuten Schluss. Bewusst im Speicher — ein Neustart der Galerie
# verliert höchstens die Sperre, nicht die Sicherheit (die Codes verfallen ja).
_PAIR_FAILS: dict = {}
PAIR_MAX_FAILS = 10
PAIR_BLOCK_SECONDS = 600


def _pair_blocked(ip: str) -> bool:
    fails, until = _PAIR_FAILS.get(ip, (0, 0.0))
    if until and time.time() > until:
        _PAIR_FAILS.pop(ip, None)
        return False
    return fails >= PAIR_MAX_FAILS


def _pair_failed(ip: str) -> None:
    fails, _ = _PAIR_FAILS.get(ip, (0, 0.0))
    _PAIR_FAILS[ip] = (fails + 1, time.time() + PAIR_BLOCK_SECONDS)


@bp.post("/api/v2/pair")
def pair():
    """Kopplungscode gegen Geräte-Schlüssel tauschen.

    Der einzige Weg, wie ein Event-PC ohne Abtippen an seinen Schlüssel kommt.
    Ohne Anmeldung erreichbar — deshalb einmalig, kurzlebig und gebremst.
    """
    ecosystem.init_schema()
    ip = request.remote_addr or "?"
    if _pair_blocked(ip):
        return jsonify({"ok": False, "error": "zu viele Versuche — später erneut"}), 429
    base = _pair_base()
    if not base:
        return jsonify(_NO_BASE[0]), _NO_BASE[1]
    body = request.get_json(silent=True) or {}
    code = str(body.get("code") or "")
    device = ecosystem.redeem_pairing(code, ip)
    if not device:
        _pair_failed(ip)
        time.sleep(0.5)
        # "reason" tells the booth which sentence to show (used / expired /
        # unknown); the status stays 404 for every refusal, as older clients expect.
        return jsonify({"ok": False, "error": "Code ungültig oder abgelaufen",
                        "reason": ecosystem.pairing_refusal(code)}), 404
    _PAIR_FAILS.pop(ip, None)
    return jsonify({"ok": True, "url": base, "api_key": device["api_key"],
                    "device_name": device["name"], "gallery_name": ecosystem.gallery_display_name()})


# ── Pairing from the phone (app/phone_pairing.py) ─────────────
# Opening a request costs nothing secret, but each one is a row: at most
# PAIR_OPEN_MAX per address in PAIR_BLOCK_SECONDS. Collecting shares the
# brake of /api/v2/pair: a wrong verifier or code counts as a failure.
_PAIR_OPENS: dict = {}
PAIR_OPEN_MAX = 10


def _pair_open_allowed(ip: str) -> bool:
    now = time.time()
    recent = [t for t in _PAIR_OPENS.get(ip, []) if now - t < PAIR_BLOCK_SECONDS]
    if len(recent) >= PAIR_OPEN_MAX:
        _PAIR_OPENS[ip] = recent
        return False
    _PAIR_OPENS[ip] = recent + [now]
    return True


@bp.post("/api/v2/pair/request")
def pair_request():
    """A booth asks to be paired; the admin confirms on the phone (QR code)."""
    from app import phone_pairing
    ecosystem.init_schema()
    ip = request.remote_addr or "?"
    if _pair_blocked(ip) or not _pair_open_allowed(ip):
        return jsonify({"ok": False, "error": "zu viele Versuche — später erneut"}), 429
    base = _pair_base()
    if not base:
        return jsonify(_NO_BASE[0]), _NO_BASE[1]
    body = request.get_json(silent=True) or {}
    try:
        made = phone_pairing.open_request(body.get("verifier_hash"), body.get("booth_name"),
                                          body.get("version"), body.get("install_id"), ip,
                                          request.headers.get("User-Agent", ""))
    except ValueError:
        return jsonify({"ok": False, "error": "bad request"}), 400
    return jsonify({"ok": True, **made,
                    "url": f"{base}/admin/devices/pair?c={made['code']}"})


@bp.post("/api/v2/pair/status")
def pair_status():
    """The booth's poll. With the right verifier, a confirmed request yields the key once."""
    from app import phone_pairing
    ecosystem.init_schema()
    ip = request.remote_addr or "?"
    if _pair_blocked(ip):
        return jsonify({"ok": False, "error": "zu viele Versuche — später erneut"}), 429
    base = _pair_base()
    if not base:
        return jsonify(_NO_BASE[0]), _NO_BASE[1]
    body = request.get_json(silent=True) or {}
    state, device = phone_pairing.collect(str(body.get("code") or ""), str(body.get("verifier") or ""))
    if state == "wrong":
        _pair_failed(ip)
        time.sleep(0.5)
        return jsonify({"ok": False, "state": "unknown"}), 404
    if state != "paired":
        return jsonify({"ok": True, "state": state})
    return jsonify({"ok": True, "state": "paired", "url": base, "api_key": device["api_key"],
                    "device_name": device["name"], "gallery_name": ecosystem.gallery_display_name()})


@bp.get("/api/v2/booth/assets/<name>")
def asset(name: str):
    if not _device():
        abort(401)
    if "/" in name or ".." in name:
        abort(404)
    path = media.assets_root() / "booth" / name
    if not path.exists():
        abort(404)
    return send_file(path, conditional=True)


@bp.get("/api/v2/booth/software/<name>")
def software_download(name: str):
    """Die Box holt sich ihre neue Fassung — nur mit gültigem Schlüssel."""
    from app import software
    if not _device():
        abort(401)
    if "/" in name or ".." in name:
        abort(404)
    pfad = software.ordner() / name
    if not pfad.exists():
        abort(404)
    return send_file(pfad, conditional=True, as_attachment=True, download_name=name)


# ── Upload ────────────────────────────────────────────────────

def _image_derivatives(src: Path, slug: str, stem: str):
    d = media.event_storage_dir(slug)
    webp, thumb = d / "webp" / f"{stem}.webp", d / "thumb" / f"{stem}.webp"
    full, th = media.process_image_to_webp_and_thumb(original_path=src, webp_path=webp, thumb_path=thumb,
                                                     max_full_px=2048, thumb_px=720)
    w = h = None
    try:
        with Image.open(src) as im:
            w, h = ImageOps.exif_transpose(im).size
    except Exception:
        pass
    return full, th, w, h


@bp.post("/api/v2/booth/media")
def upload():
    ecosystem.init_schema()
    dev = _device()
    if not dev:
        return jsonify({"ok": False, "error": "invalid api key"}), 401
    f = request.files.get("file")
    uid = (request.form.get("uid") or "").strip()
    session_uid = (request.form.get("session_uid") or uid).strip()
    kind = request.form.get("kind", "photo")
    variant = request.form.get("variant", "web")
    if not f or not uid or kind not in KINDS or variant not in VARIANTS or not uid.replace(".", "").isalnum():
        return jsonify({"ok": False, "error": "bad request"}), 400
    # Deleted stays deleted. The booth sends originals long after the web
    # version; a run the guest, the operator or retention removed in between
    # must not come back. Answered as success on purpose: anything else makes
    # the booth retry forever. Nothing is stored, no row is created.
    if ecosystem.is_deleted(uid, session_uid):
        db.touch_device_seen(dev["id"], request.remote_addr)
        return jsonify({"ok": True, "gone": True})

    slug = (request.form.get("event_slug") or "").strip()
    event = db.get_event_by_slug(slug) if slug else None
    if not event and dev.get("event_id"):
        event = db.get_event_by_id(dev["event_id"])
    if not event:
        return jsonify({"ok": False, "error": "no event assigned"}), 422
    if event["id"] not in ecosystem.device_event_ids(dev["id"]):
        return jsonify({"ok": False, "error": "event not assigned to this device"}), 403

    ext = Path(f.filename or "").suffix.lower() or ".jpg"
    if ext not in IMAGE_EXT | VIDEO_EXT:
        return jsonify({"ok": False, "error": "file type"}), 400

    existing = ecosystem.media_by_uid(uid, variant)
    if existing and (int(existing["event_id"]) != int(event["id"])
                     or (existing.get("device_id") not in (None, dev["id"]))):
        return jsonify({"ok": False, "error": "uid belongs to another event or device"}), 409
    d = media.event_storage_dir(event["slug"])
    stem = f"{uid}_{variant}"
    tmp = d / "orig" / f"{stem}.upload.part"
    f.save(tmp)
    # Judge by content, store under the extension the content has: a page
    # named "x.jpg" must not land in orig/ and come back from /data.
    try:
        art, ext = uploads.sniff(tmp)
    except uploads.UploadRejected as exc:
        tmp.unlink(missing_ok=True)
        return jsonify({"ok": False, "error": str(exc)}), exc.status
    target = d / "orig" / f"{stem}{ext}"
    sha = hashlib.sha256(tmp.read_bytes()).hexdigest()
    if existing and existing.get("sha256") == sha:
        tmp.unlink(missing_ok=True)
        db.touch_device_seen(dev["id"], request.remote_addr)
        return jsonify({"ok": True, "id": existing["id"], "duplicate": True})
    tmp.replace(target)

    is_video = art == "video"
    poster_rel = None
    if is_video:
        poster = request.files.get("poster")
        full = th = None
        w = h = None
        if poster:
            ppath = d / "orig" / f"{stem}_poster.jpg"
            poster.save(ppath)
            try:
                if uploads.sniff(ppath)[0] != "image":
                    raise uploads.UploadRejected("poster is not a picture")
                poster_rel = ppath.relative_to(media.data_dir()).as_posix()
                full, th, w, h = _image_derivatives(ppath, event["slug"], stem)
            except uploads.UploadRejected:
                ppath.unlink(missing_ok=True)   # the clip stays; it just has no poster
    else:
        full, th, w, h = _image_derivatives(target, event["slug"], stem)
        if ext == ".gif":
            full = target          # Animation erhalten; Vorschaubild ist das erste Bild

    rel = lambda p: p.relative_to(media.data_dir()).as_posix() if p else None  # noqa: E731
    meta = request.form.get("meta") or "{}"
    try:
        taken_at = json.loads(meta).get("taken_at")
    except ValueError:
        meta, taken_at = "{}", None
    # Originale einer Web-Fassung erscheinen nicht doppelt in der Galerie
    hidden = 1 if (variant == "original" and kind != "original") else 0

    con = db.get_db()
    if existing:
        keep = {rel(target), rel(full), rel(th), poster_rel}
        for old in (existing.get("path_original"), existing.get("path_webp"), existing.get("path_thumb"), existing.get("path_poster")):
            if old and old not in keep:
                media.delete_rel_path(old)
        media.clear_download_cache(existing["id"])
        con.execute("""UPDATE media SET kind=?, filename=?, path_original=?, path_webp=?, path_thumb=?, path_poster=?,
                       size_bytes=?, sha256=?, width=?, height=?, meta_json=?, taken_at=?, storage='local',
                       remote_path=NULL WHERE id=?""",
                    (kind, target.name, rel(target), rel(full), rel(th), poster_rel, target.stat().st_size, sha,
                     w, h, meta, taken_at, existing["id"]))
        media_id = existing["id"]
    else:
        cur = con.execute(
            """INSERT INTO media(event_id, kind, filename, path_original, path_webp, path_thumb, path_poster, size_bytes,
                                 sha256, share_token, uid, session_uid, variant, width, height, meta_json, taken_at, hidden, device_id)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (event["id"], kind, target.name, rel(target), rel(full), rel(th), poster_rel, target.stat().st_size, sha,
             secrets.token_urlsafe(16), uid, session_uid, variant, w, h, meta, taken_at, hidden, dev["id"]))
        media_id = cur.lastrowid
    con.execute("UPDATE events SET last_upload_at=datetime('now') WHERE id=?", (event["id"],))
    con.commit()
    con.close()
    db.touch_device_seen(dev["id"], request.remote_addr)
    if variant == "web" and not existing and (db.get_setting("notify_on_upload") or "1") == "1":
        try:
            from app import notifier
            notifier.batch_upload_notification(event["id"], event["name"],
                                               f"{db.base_url_for(event) or _base_url()}/gallery/{event['slug']}")
        except Exception:
            pass
    return jsonify({"ok": True, "id": media_id})


def _vorlagen_von_der_box(dev, roh) -> list:
    """Vorlagen übernehmen, die an einer Box entstanden sind.

    Der Editor läuft an beiden Enden; wer am Abend an der Box eine Vorlage
    baut, soll sie am Montag im Web-Admin wiederfinden — und auf der zweiten
    Box. Geprüft wird mit demselben Code wie im Web-Admin, damit eine Box
    nichts hereinreichen kann, was die Verwaltung nicht anzeigt.
    """
    if not isinstance(roh, list) or not roh:
        return []
    from app import vorlagen as vorlagen_mod
    from app.blueprints.admin_booth import _assets_dir
    quittiert = []
    for eintrag in roh[:5]:
        if not isinstance(eintrag, dict):
            continue
        daten = eintrag.get("template")
        datei = str(eintrag.get("file") or "")
        if not isinstance(daten, dict):
            continue
        try:
            vorlage, _hinweise = vorlagen_mod.annehmen(daten)
        except vorlagen_mod.VorlageAbgelehnt as exc:
            # Refused for good (the same gate as an upload): acknowledged, so
            # the booth stops offering it, and said in the log with the reasons.
            current_app.logger.warning("Vorlage %s von Box %s abgelehnt: %s", datei[:80], dev.get("id"), exc)
            if datei:
                quittiert.append(datei)
            continue
        try:
            gespeichert = vorlagen_mod.speichern(vorlage, _assets_dir())
        except Exception as exc:                      # noqa: BLE001 — eine kaputte Vorlage kostet nicht den Sync
            current_app.logger.warning("Vorlage von Box %s nicht übernommen: %s", dev.get("id"), exc)
            continue
        # Die Box kennt sie unter ihrem eigenen Dateinamen; quittiert wird der.
        quittiert.append(datei or gespeichert["file"])
    return quittiert


# ── Öffentliche Durchgangs-Seite ──────────────────────────────

def _gallery_href(event) -> str:
    """Der Weg von der Durchgangs-Seite in die Galerie.

    Gibt das Event sein Passwort im Link mit, reist der Schlüssel hier mit:
    Wer an der Box stand, tippt nichts.
    """
    from app import gallery_settings as gsm
    query = "&".join(x for x in ("src=qr", ecosystem.utm_query(event)) if x)
    if event.get("password_hash") and gsm.for_event(event)["link_password"]:
        from app.blueprints.public import _access_token
        query += f"&access_token={_access_token(event)}"
    return f"/gallery/{event['slug']}?{query}"


def _session_closed(event) -> str:
    """Why the QR routes of this event are closed to a guest: "silent", "expired" or "".

    The page, the delete and the status route ask the same question — until
    11.11.2026 only the page did, so a silent or expired event still answered
    status.json and still took deletions.
    """
    from app import gallery_settings as gsm, silence
    from app.blueprints.public import _is_admin
    if _is_admin():
        return ""
    if silence.is_silent(event):
        return "silent"
    if gsm.is_expired(gsm.for_event(event)):
        return "expired"
    return ""


def _run_items(uid: str) -> list:
    return [m for m in ecosystem.session_media(uid) if m.get("variant") != "original" or m.get("kind") == "original"]


def _visible_items(uid: str, event=None) -> list:
    """The run's pictures this visitor may see: with ``event``, without the red
    ones while that gallery hides red from guests (as in the gallery and its
    file routes, ``public._guest_may_see``)."""
    items = _run_items(uid)
    if event is None:
        return items
    from app import gallery_settings as gsm
    from app.blueprints.public import _red_hidden
    gs = gsm.for_event(event)
    return [m for m in items if not _red_hidden(event, gs, m)]


def _session_state(uid: str, event, items=None) -> str:
    """pending, ready, review or gone.

    ``gone`` covers both a deletion and a rejection on purpose: a guest is
    never told that the organiser rejected their picture.
    """
    if items is None:
        items = _visible_items(uid, event)
    if items:
        return "ready"
    if ecosystem.is_deleted(uid) or _run_items(uid):
        return "gone"           # deleted, or every picture is red-hidden: like a rejection
    con = db.get_db()
    held = con.execute("SELECT 1 FROM media WHERE session_uid=? AND event_id=? AND review_state='pending' LIMIT 1",
                       (uid, event["id"])).fetchone()
    con.close()
    return "review" if held else "pending"


@bp.get("/p/<code>")
def session_page(code: str):
    ecosystem.init_schema()
    uid, sig = ecosystem.parse_share_code(code)
    event = ecosystem.find_event_for_share(uid, sig) if uid else None
    if not event:
        abort(404)
    from app import silence
    from app.blueprints.public import _is_admin as _admin_now
    if silence.is_silent(event) and not _admin_now():
        abort(404)                    # stiller Betrieb: auch alte Codes führen ins Leere
    from app import texts
    texts.use(event)                  # eigene Texte des Events gelten auch hier
    target = event.get("qr_target") or "gallery"
    from app import redirects
    if target == "external" and redirects.clean_url((event.get("external_url") or "").replace("{uid}", uid)):
        return redirect(ecosystem.qr_url_for(event, uid, db.base_url_for(event) or _base_url()), 302)
    if target == "status":
        return render_template("public/session.html", event=event, state="status", items=[], uid=uid,
                               message=event.get("status_message") or "", noindex=True)
    from app import gallery_settings as gsm
    from app.blueprints.public import _is_admin, remember_session_uid
    from app.i18n import t
    if gsm.is_expired(gsm.for_event(event)) and not _is_admin():
        return render_template("public/session.html", event=event, state="status", items=[], uid=uid,
                               message=t("gallery.expired_text"), status_title=t("gallery.expired_title"), noindex=True), 410
    remember_session_uid(uid)
    items = _visible_items(uid, event)
    gallery_ok = bool(event.get("published"))
    gallery_href = _gallery_href(event)
    if not items:
        state = _session_state(uid, event, items)
        if state != "gone":
            try:
                db.add_stat(event["id"], "view_gallery", meta={"src": "qr-pending"})
            except Exception:
                pass
        return render_template("public/session.html", event=event, state=state, items=[], uid=uid,
                               gallery_ok=gallery_ok, gallery_href=gallery_href, noindex=True)
    view = []
    # A picture with an unframed capture of the same shot offers both as two
    # named downloads; the capture is then not shown a second time below.
    partner_of, absorbed = ecosystem.unframed_partners(items)
    frame_choice = gsm.for_event(event)["run_frame_choice"]
    from app.blueprints.public import _tile_srcset, guest_namer
    name_of = guest_namer(event)
    for m in items:
        if m["id"] in absorbed:
            continue
        is_video = (m.get("kind") in ("boomerang", "video"))
        view.append({
            "id": m["id"], "kind": m["kind"], "is_video": is_video,
            "src": f"/data/{m['path_original']}" if is_video else f"/data/{m.get('path_webp') or m['path_original']}",
            "poster": f"/data/{m['path_webp']}" if m.get("path_webp") else "",
            "width": m.get("width") or 4, "height": m.get("height") or 5,
            # The preview sizes that exist (thumbnail, web version), so a
            # phone does not load the full web version of every picture.
            "srcset": "" if is_video else _tile_srcset(m, m.get("path_thumb") or "", m.get("path_webp") or ""),
            "share_token": m["share_token"], "filename": name_of(m),
            "unframed_id": (partner_of.get(m["id"]) or {}).get("id") if frame_choice else None,
        })
    view.sort(key=lambda v: (v["kind"] == "original", v["id"]))
    try:
        db.add_stat(event["id"], "view_gallery", meta={"src": "qr-session"})
    except Exception:
        pass
    gs = gsm.for_event(event)
    return render_template("public/session.html", event=event, state="ready", items=view, uid=uid,
                           gallery_ok=gallery_ok, gallery_href=gallery_href, code=code, noindex=True,
                           guest_delete=gs.get("guest_delete", True), gs=gs)


def _hidden_rows_of_session(session_uid: str, event_id: int) -> list:
    """Hidden rows of a run in this event: originals and items held for review."""
    con = db.get_db()
    rows = [dict(r) for r in con.execute(
        "SELECT * FROM media WHERE session_uid=? AND event_id=? AND hidden=1", (session_uid, event_id))]
    con.close()
    return rows


@bp.post("/p/<code>/delete")
def session_delete(code: str):
    """„Diese Aufnahmen löschen" — durch den Gast selbst, ohne Konto und ohne Nachfrage.

    Der QR-Code ist der Nachweis: Wer ihn hat, war an der Box und darf über
    seinen Durchgang bestimmen. Die Signatur wird geprüft, damit niemand fremde
    Kennungen durchprobieren kann. Gelöscht wird sofort und mit Beleg.

    Der Betreiber kann das abschalten — auf manchen Veranstaltungen gehören die
    Bilder dem Veranstalter, nicht dem Gast.
    """
    from app import gallery_settings as gsm, media, retention
    ecosystem.init_schema()
    uid, sig = ecosystem.parse_share_code(code)
    event = ecosystem.find_event_for_share(uid, sig) if uid else None
    if not event:
        abort(404)
    closed = _session_closed(event)
    if closed:
        abort(404 if closed == "silent" else 410)
    if not gsm.for_event(event).get("guest_delete", True):
        abort(403)
    rows = ecosystem.session_media(uid)
    from app import trash
    if trash.for_guests():
        # The operator chose a grace period for guest deletions too
        # (app/trash.py): the run leaves the gallery now and is purged, and
        # logged, when its days are over.
        for row in list(rows) + list(_hidden_rows_of_session(uid, event["id"])):
            trash.move(dict(row), actor="guest", reason="guest",
                       note="Durchgang über den QR-Code gelöscht")
        ecosystem.remember_deleted(uid, event_id=event["id"], reason="guest", scope="session")
        from app.i18n import t
        return render_template("public/session.html", event=event, state="status", items=[], uid=uid,
                               message=t("session.deleted_text"), status_title=t("session.deleted_title"),
                               noindex=True)
    files = size = 0
    for row in rows:
        row = dict(row)
        for rel in (row.get("path_original"), row.get("path_webp"), row.get("path_thumb"),
                    row.get("path_poster")):
            if not rel:
                continue
            try:
                full = (media.data_dir() / rel).resolve()
                if full.exists() and full.is_file():
                    size += full.stat().st_size
            except Exception:
                pass
            media.delete_rel_path(rel)
            files += 1
        if row.get("storage") == "remote" and row.get("remote_path"):
            from app import storage
            storage.queue_remote_delete(row["remote_path"])
        media.clear_download_cache(int(row["id"]))
        db.delete_media(int(row["id"]))
    # session_media() only sees visible rows; the hidden original of the web
    # version (and anything held for review) belongs to the run just as much.
    for row in _hidden_rows_of_session(uid, event["id"]):
        f, s = retention._remove_files(row)
        files += f
        size += s
        if row.get("storage") == "remote" and row.get("remote_path"):
            from app import storage
            storage.queue_remote_delete(row["remote_path"])
        db.delete_media(int(row["id"]))
    ecosystem.remember_deleted(uid, event_id=event["id"], reason="guest", scope="session")
    retention.record(scope="session", reason="guest", actor="guest", session_uid=uid,
                     event_slug=event["slug"], files=files, size=size,
                     note=f"{len(rows)} Aufnahmen über den QR-Code gelöscht")
    from app.i18n import t
    return render_template("public/session.html", event=event, state="status", items=[], uid=uid,
                           message=t("session.deleted_text"), status_title=t("session.deleted_title"),
                           noindex=True)


@bp.get("/p/<code>/status.json")
def session_status(code: str):
    """What the waiting page polls. ``ready`` stays for pages still open from before."""
    ecosystem.init_schema()
    uid, sig = ecosystem.parse_share_code(code)
    event = ecosystem.find_event_for_share(uid, sig) if uid else None
    if not event:
        return jsonify({"ok": False}), 404
    closed = _session_closed(event)
    if closed == "silent":
        return jsonify({"ok": False}), 404
    if closed == "expired":
        return jsonify({"ok": False, "state": "expired"}), 410
    state = _session_state(uid, event)
    return jsonify({"ok": True, "state": state, "ready": state == "ready"})
