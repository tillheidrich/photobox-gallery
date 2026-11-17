"""Public-facing routes: homepage, gallery, kiosk, impressum, datenschutz."""
from __future__ import annotations

import hmac
import io
import os
import secrets
import zipfile
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from werkzeug.utils import secure_filename

from app.i18n import t

from flask import (
    Blueprint, abort, flash, g, redirect, render_template,
    request, send_file, session, url_for, jsonify, current_app, make_response
)

from app import branding, db, media

bp = Blueprint("public", __name__)


# ─── Helpers ──────────────────────────────────────────────────

def _get_base_url() -> str:
    """Get configured BASE_URL from settings or environment."""
    url = db.get_setting("app_base_url") or os.environ.get("BASE_URL", "")
    return url.rstrip("/")


def _format_date(dt_str: Optional[str]) -> str:
    if not dt_str:
        return ""
    try:
        dt = datetime.fromisoformat(dt_str[:19])
        return dt.strftime("%d.%m.%Y")
    except Exception:
        return dt_str[:10] if dt_str else ""


def _format_datetime(dt_str: Optional[str]) -> str:
    """Date and time the way every other date on a guest page reads (no ISO)."""
    if not dt_str:
        return ""
    try:
        return datetime.fromisoformat(str(dt_str)[:19].replace(" ", "T")).strftime("%d.%m.%Y %H:%M")
    except ValueError:
        return str(dt_str)[:16].replace("T", " ")


def _event_cover_url(event) -> Optional[str]:
    rel = event.get("cover_path") or event.get("cover_original_path")
    if rel:
        return f"/data/{rel}"
    return None



def _make_csrf() -> str:
    """Das Formular-Zeichen dieser Sitzung — und es steht auch wirklich darin.

    Bis zum 19.09.2026 erzeugte diese Funktion bei jedem Rendern ein frisches
    Zufallszeichen, das nirgends abgelegt wurde. Die Felder in der
    Passwortseite und im Einrichtungsassistenten waren damit Dekoration: Sie
    hätten sich grundsätzlich nie prüfen lassen — und wer die Prüfung
    nachträgt, repariert zuerst diese Zeile.
    """
    import secrets as _secrets

    if not session.get("csrf_token"):
        session["csrf_token"] = _secrets.token_hex(24)
    return str(session["csrf_token"])


def _pruefe_csrf() -> None:
    """Formular-Absender prüfen. Ohne gültiges Zeichen passiert nichts."""
    import hmac

    gesendet = str(request.form.get("csrf_token") or "")
    erwartet = session.get("csrf_token")
    if not erwartet or not hmac.compare_digest(gesendet, str(erwartet)):
        abort(400)


def _is_admin() -> bool:
    """Check if current user is logged in as admin.

    False inside the owner's preview (``?ansicht=vorschau``): from the point
    where the gallery route sets ``g.guest_view`` on, the page is built with
    a guest's rights, so the owner sees what a guest sees.
    """
    return session.get("admin_logged_in") is True and not g.get("guest_view")


#: The owner's two ways into a gallery (Picdrop-style): exactly the guest's
#: page, and the same page with the tools to hide, mark and delete.
VIEW_PREVIEW, VIEW_MANAGE = "vorschau", "verwalten"


def _owner_view() -> str:
    """"vorschau", "verwalten" or "" - only for the logged-in owner."""
    want = request.args.get("ansicht", "")
    if want in (VIEW_PREVIEW, VIEW_MANAGE) and session.get("admin_logged_in") is True:
        return want
    return ""


def _preview_state(event) -> list:
    """What the preview bar says about the gallery: draft/published, password, silent."""
    from app import silence
    out = [t("preview.state_live") if event.get("published") else t("preview.state_draft")]
    if event.get("password_hash"):
        out.append(t("preview.state_password"))
    if silence.is_silent(event):
        out.append(t("preview.state_silent"))
    return out


def _hidden_media(event_id: int) -> list:
    """Hidden pictures of an event for manage mode: admin-hidden and pending ones.

    The hidden full-size originals of booth runs (``variant='original'``)
    are not pictures of their own and stay out, as in ``set_media_visibility``.
    """
    con = db.get_db()
    try:
        cols = {r[1] for r in con.execute("PRAGMA table_info(media)")}
        extra = " AND COALESCE(variant,'') != 'original'" if "variant" in cols else ""
        rows = con.execute(f"SELECT * FROM media WHERE event_id=? AND COALESCE(hidden,0)=1{extra}",
                           (int(event_id),)).fetchall()
    finally:
        con.close()
    return [dict(r) for r in rows]

#: Paths whose next segment is a secret: run pages, share links, gallery
#: codes, the double-opt-in and unsubscribe links.
_KEY_PATHS = ("/p/", "/s/", "/l/", "/g/", "/notify/")


def _key_in_path(path: str) -> bool:
    return any((path or "").startswith(p) for p in _KEY_PATHS)


def _inject_globals():
    """Inject global template variables from DB settings."""
    settings_keys = [
        "homepage_headline", "homepage_subtext", "homepage_hero_image_path",
        "homepage_show_galleries", "homepage_show_empty_events",
        "impressum_text", "datenschutz_text",
        "footer_custom_link_label", "footer_custom_link_url",
        "footer_show_powered_by", "branding_logo_path",
        "guest_upload_enabled", "zip_download_enabled",
        "zip_download_admin_only",
    ]
    # Sprache zuerst: Davon hängt ab, welche Fassung der freien Texte gilt.
    # Hat der Betreiber Englisch abgeschaltet, gibt es hier nichts zu wählen.
    sprachen = db.languages()
    if sprachen in ("de", "en"):
        g.lang = sprachen
        session["lang"] = sprachen
    else:
        g.lang = session.get("lang", "de")
    g.lang_switch = sprachen == "both"

    #: Texte, die der Betreiber selbst schreibt — sie gibt es zweisprachig.
    freie_texte = {"homepage_headline", "homepage_subtext", "impressum_text",
                   "datenschutz_text", "footer_custom_link_label"}
    for key in settings_keys:
        val = db.text_setting(key, g.lang) if key in freie_texte else (db.get_setting(key) or "")
        setattr(g, key, val)

    # Neutral by default: guests reach their gallery via QR/link, the homepage does
    # NOT list all galleries unless the admin explicitly opts in.
    g.homepage_show_galleries = (db.get_setting("homepage_show_galleries") or "0") == "1"
    g.homepage_show_empty_events = (db.get_setting("homepage_show_empty_events") or "0") == "1"
    g.footer_show_powered_by = (db.get_setting("footer_show_powered_by") or "1") == "1"
    # Die Fußzeile: Pflichtseiten plus die eigenen Links des Betreibers.
    from app import footer as footer_mod
    # Inside a gallery the privacy link carries the event, so the page can
    # name this gallery's own deletion date.
    gallery_slug = (request.view_args or {}).get("slug") if request.path.startswith("/gallery/") else None
    g.footer_links = footer_mod.links(g.lang, event_slug=gallery_slug)
    g.branding_logo = bool(db.get_setting("branding_logo_path") or "")
    # „Meine Aufnahmen": nur ein Zähler aus der Browser-Sitzung, kein Konto.
    g.my_uids_count = len(session.get("p_uids") or [])

    # Umami-Analytics (cookieless, self-hosted). Konfig: DB-Setting > Env-Variable.
    import os as _os
    g.umami_src = (db.get_setting("umami_src") or _os.environ.get("UMAMI_SRC", "")).strip()
    g.umami_website_id = (db.get_setting("umami_website_id") or _os.environ.get("UMAMI_WEBSITE_ID", "")).strip()
    # Not on pages whose address is a key: a run page (/p/…) opens and deletes
    # a guest's shots, a share link (/s/, /l/) and a confirmation link carry
    # their token in the path. The measuring script reports the full path, so
    # it would copy every key into the analytics database (15.11.2026).
    g.umami_enabled = bool(g.umami_src and g.umami_website_id) and not _key_in_path(request.path)


@bp.before_request
def before_request():
    _inject_globals()
    _apply_event_texts()


def _apply_event_texts() -> None:
    """Eigene Texte des Betreibers setzen — die des Events, sonst die globalen.

    Hängt am Slug in der Route, damit keine Seite vergessen wird: Galerie,
    Downloads, Passwortseite und Slideshow teilen sich denselben Weg.
    """
    from app import texts
    slug = (request.view_args or {}).get("slug")
    event = db.get_event_by_slug(slug) if slug else None
    texts.use(event)


# ─── Per-Device Landing Pages ─────────────────────────────────

@bp.route("/screen/<int:device_id>")
def device_landing(device_id: int):
    """Per-device landing page showing current event."""
    device = db.get_device_by_id(device_id)
    if not device or not device["active"]:
        abort(404)

    device = dict(device)
    db.touch_device_seen(device_id, request.remote_addr)

    # Get assigned event
    event = db.get_device_landing_event(device_id)

    # Get base URL for gallery link
    base_url = db.base_url() or request.host_url.rstrip("/")
    gallery_url = f"{base_url}/gallery/{event['slug']}" if event else None

    return render_template(
        "public/device_landing.html",
        device=device,
        event=event,
        gallery_url=gallery_url,
    )


# ─── Homepage ─────────────────────────────────────────────────────

@bp.get("/")
def home():
    events_data = []
    if g.homepage_show_galleries:
        all_events = db.list_events() or []
        for ev in all_events:
            # Only show published events
            if not ev.get("published") or ev.get("parent_id"):   # folders are shown inside their gallery
                continue
            if _silent(ev):          # stiller Betrieb: steht auf keiner Liste
                continue
            photos = db.list_media(ev["id"]) or []
            if not photos and not g.homepage_show_empty_events:
                continue
            events_data.append({
                "id":          ev["id"],
                "name":        ev["name"],
                "slug":        ev["slug"],
                "date":        _format_date(ev.get("date") or ev.get("starts_at")),
                "cover_url":   _event_cover_url(ev),
                "photo_count": len(photos),
                "gallery_url": url_for("public.gallery", slug=ev["slug"]),
                "featured":    bool(ev.get("featured")),
            })
        # Sort: featured first, then by date
        events_data.sort(key=lambda e: (-int(e.get("featured") or 0), e.get("date", "") or ""))
    return render_template("public/home.html", events=events_data)


# ─── Galerie per Code finden ──────────────────────────────────

def _resolve_code_or_home(code: str):
    ev = db.get_event_by_code(code)
    if ev and ev.get("published") and not _silent(ev):
        return redirect(url_for("public.gallery", slug=ev["slug"]))
    return redirect(url_for("public.home", code_error=(code or "").strip()[:12]))


@bp.get("/g/<code>")
def gallery_by_code(code: str):
    """Kurzer, teilbarer Code-Link (z.B. auf Aushängen): /g/K7F2QP."""
    return _resolve_code_or_home(code)


@bp.get("/finden")
def find_by_code():
    """Code-Suche von der Startseite (funktioniert auch ohne JavaScript)."""
    return _resolve_code_or_home(request.args.get("code", ""))


# ─── Gallery ──────────────────────────────────────────────────

def _gs(event):
    from app import gallery_settings as gsm
    return gsm.for_event(event)


def _expired_for_guest(event) -> bool:
    from app import gallery_settings as gsm
    return gsm.is_expired(_gs(event)) and not _is_admin()


def _not_yet_live(event) -> Optional[datetime]:
    """Startzeitpunkt, wenn die Galerie für Gäste noch zu ist — sonst ``None``.

    Nebenwirkung mit Absicht: Ist der geplante Zeitpunkt erreicht, geht die
    Galerie hier live. Dafür braucht niemand einen Cronjob — der erste Gast,
    der scannt, löst es aus.
    """
    from app import schedule
    if not event:
        return None
    if schedule.due_for_publish(event):
        db.update_event(int(event["id"]), published=1, status="live")
        event["published"] = 1
        event["status"] = "live"
        return None
    start = schedule.coming_soon(event)
    return start if (start and not _is_admin()) else None


def _coming_soon_page(event, start: datetime):
    """Vorfreude-Seite mit Countdown — ohne ein einziges Foto."""
    from app import schedule
    return render_template(
        "public/coming_soon.html",
        event=event,
        start=start,
        start_iso=start.isoformat(timespec="seconds"),
        start_text=start.strftime("%d.%m.%Y, %H:%M"),
        brand=branding.event_branding(event),
        noindex=True,
    ), 200


def _expiry_notice(event) -> dict:
    """Countdown für Gäste in der letzten Woche: „noch 3 Tage verfügbar"."""
    from app import schedule
    gs = _gs(event)
    days = schedule.days_left(gs)
    if days is None:
        return {}
    until = schedule.expires_at(gs)
    key = "gallery.expires_today" if days <= 1 else "gallery.expires_in_days"
    return {
        "days": days,
        "text": t(key, n=days),
        "until": until.strftime("%d.%m.%Y, %H:%M") if until else "",
    }


def _zip_allowed(event) -> bool:
    enabled = (db.get_setting("zip_download_enabled") or "0") == "1"
    admin_only = (db.get_setting("zip_download_admin_only") or "1") == "1"
    if _is_admin():
        return True
    return (enabled and not admin_only) or bool(_gs(event)["download_zip"])


def remember_session_uid(uid: str) -> None:
    """Gültiger QR-Code eines Durchgangs: dessen Dateien darf dieser Browser sehen."""
    uids = [u for u in (session.get("p_uids") or []) if u != uid]
    session["p_uids"] = ([uid] + uids)[:20]


def _zip_response(entries, download_name: str):
    """ZIP auf Platte statt im RAM bauen (Videos, viele Originale)."""
    import tempfile
    tmp = tempfile.TemporaryFile()
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_STORED) as zf:   # JPEG/MP4 sind schon komprimiert
        for full, arcname in entries:
            zf.write(full, arcname)
    tmp.seek(0)
    return send_file(tmp, mimetype="application/zip", download_name=download_name, as_attachment=True)


# ─── Stiller Betrieb & Zugangslink ────────────────────────────

def _event_by_slug(slug: str):
    """Das Event zu einer Adresse — auch zu einer alten.

    Die Galerie selbst leitet auf die aktuelle Adresse um (301); alles, was an
    ihr hängt (QR-Bild, Sharepic, Druckseite, Downloads, Kiosk), bedient den
    alten Namen einfach weiter. Ein gedruckter QR-Code soll nicht deshalb
    aufhören zu funktionieren, weil der Kunde seine Galerie umbenannt hat.
    """
    event, _canonical, _alias = db.resolve_event_slug(slug)
    from app import folders
    return folders.apply_parent(event)


def _silent(event) -> bool:
    """Ist diese Galerie für Gäste stumm geschaltet? Der Betreiber sieht sie weiter."""
    from app import silence
    return silence.is_silent(event) and not _is_admin()


def _wartet_auf_freigabe(row) -> bool:
    """Diese Aufnahme ist noch nicht freigegeben.

    Der Trigger in ``ecosystem`` hält neue Aufnahmen mit ``hidden=1`` und
    ``review_state='pending'`` zurück, und ``db.list_media`` blendet sie aus.
    ``db.get_media`` tut das nicht — wer eine Kennung durchzählt, käme sonst
    an genau die Bilder, die der Betreiber noch nicht sehen lassen wollte.
    Gefunden bei der Durchsicht am 19.09.2026.

    Geprüft wird ``review_state``, nicht ``hidden``: Originale einer
    Web-Fassung tragen ebenfalls ``hidden=1`` und sollen weiter ladbar sein.
    """
    return str((row or {}).get("review_state") or "") == "pending"


def _forward_guest(event):
    """The 302 to the address the event forwards to (app/redirects.py), or None.

    Only for guests: the operator still opens his own gallery. Silent mode
    wins - a silent gallery does not exist to the outside, not even as a
    forward - so the caller's 404 for it still comes. The target is read from
    the event row only, never from the request.
    """
    from app import redirects, silence
    if not event or _is_admin() or silence.is_silent(event):
        return None
    url = redirects.target(event)
    return redirect(url, 302) if url else None


def _deny_if_silent(event) -> None:
    """Im stillen Betrieb gibt es die Galerie nach außen schlicht nicht."""
    if _silent(event):
        abort(404)


def _access_token(event) -> str:
    """Der Schlüssel, der ein Passwort im Link ersetzt — notfalls neu vergeben."""
    token = (event.get("access_token") or "").strip()
    if not token:
        token = secrets.token_hex(16)
        db.update_event(int(event["id"]), access_token=token)
        event["access_token"] = token
    return token


def gallery_link(event, base: str = "", *, mit_schluessel: bool = True) -> str:
    """Adresse der Galerie für QR-Code, Sharepic und Druckseite.

    Ist die Galerie passwortgeschützt und das Event gibt den Schlüssel mit
    (``link_password``), hängt er hier an. Wer den Code gescannt hat, ist drin;
    wer nur den Namen der Galerie kennt, steht weiter vor dem Passwortfeld.

    Dieses Versprechen hielt bis zum 19.09.2026 nicht: ``/gallery/<slug>/qr.png``
    war ohne jede Anmeldung abrufbar. Wer den Namen der Galerie kannte, holte
    sich das Bild, las den Code aus und hatte den Schlüssel, der das Passwort
    ersetzt. Deshalb ``mit_schluessel``: Für einen anonymen Aufruf wird die
    nackte Adresse gebaut. Der QR-Code funktioniert weiter, er fragt dann eben
    nach dem Passwort — genau das, was der Betreiber wollte.
    """
    base = (base or _get_base_url()).rstrip("/")
    url = f"{base}/gallery/{event['slug']}" if base else url_for("public.gallery", slug=event["slug"], _external=True)
    if mit_schluessel and event.get("password_hash") and _gs(event)["link_password"]:
        url = f"{url}?access_token={_access_token(event)}"
    return url


def _darf_schluessel_sehen(event) -> bool:
    """Wer schon drin ist, darf den Schlüssel im Bild haben — sonst niemand."""
    return _is_admin() or _check_password_access(event)


def _password_page(event, *, error: Optional[str] = None, sent: bool = False, **extra):
    """Die Passwortseite — mit dem Knopf „Zugriff anfragen", wenn das Event ihn anbietet."""
    from app import access
    return render_template("public/password.html", event=event, error=error,
                           csrf_token=_make_csrf(), access_request=access.enabled(event),
                           access_sent=sent, **extra)


def _check_password_access(event) -> bool:
    """Ist diese Galerie für den Abrufenden offen?

    Die Spalte heißt ``password_hash``, nicht ``password``. Die Galerie-Route
    setzt ``session[gallery_access_<id>]``, sobald das Passwort stimmte oder
    ein gültiger Schlüssel im Link stand; hier wird nur noch nachgesehen.

    Der angemeldete Betreiber ist ausgenommen. Alle anderen Schranken in dieser
    Datei nehmen ihn aus — Veröffentlichung, Ablauf, Wasserzeichen, Zeitplan —,
    nur das Gästepasswort tat es nicht. Das war keine Entscheidung, sondern
    eine Lücke: Der Betreiber kam an das ZIP seines eigenen Events nicht heran,
    obwohl in der Verwaltung jedes einzelne Bild offen liegt. Die Abnahme
    (``tools/acceptance.py``, Punkt „ZIP als Admin erlaubt") stand deshalb seit
    Langem auf Rot. Wer die Gästeansicht sehen will, nimmt ein privates Fenster.
    """
    if _is_admin():
        return True
    from app import clients, folders
    if event.get("parent_id"):
        # A folder: whoever may open the parent may open it (client code,
        # password, link key of the parent) - unless the client holds the
        # folder itself. Its own password comes on top, never instead.
        parent = folders.parent_of(event)
        if parent and not clients.has_event(event["id"]) and not _password_open(parent):
            return False
    return _password_open(event)


def _password_open(event) -> bool:
    """The password gate of exactly this event, without looking at a parent."""
    if not event.get("password_hash") or _is_admin():
        return True
    if session.get(f"gallery_access_{event['id']}"):
        return True
    # The client access code is the key to the galleries assigned to that
    # client: it stands in for their password and for nothing else. A folder
    # with a password of its own wants that password (or the folder itself
    # assigned), not only the parent's key.
    from app import clients
    return clients.has_event(event["id"])


def _folder_tiles(event) -> list:
    """The folders of a gallery as tiles - only those this visitor may open or knock on."""
    from app import folders
    tiles = []
    for child in folders.children_of(int(event["id"])):
        if not (child.get("published") or _is_admin()) or _silent(child):
            continue
        locked = not _check_password_access(child)
        rows = db.list_media(int(child["id"])) or []
        cover = child.get("cover_path") or child.get("cover_original_path")
        if not cover:
            first = next((m for m in reversed(rows) if m.get("path_thumb") or m.get("path_webp")), None)
            cover = (first.get("path_thumb") or first.get("path_webp")) if first else None
        # A folder with a password of its own shows no picture before it is opened.
        tiles.append({"name": child["name"], "slug": child["slug"], "count": len(rows),
                      "date": _format_date(child.get("date")),
                      "cover_url": f"/data/{cover}" if cover and not locked else None,
                      "locked": locked})
    return tiles


def _opener(event, gs) -> Optional[dict]:
    """The opener above the gallery: only when switched on and a cover exists."""
    cover = _event_cover_url(event)
    if not gs["opener"] or not cover:
        return None
    return {"cover_url": cover, "date": _format_date(event.get("date")), "subtitle": gs["opener_subtitle"],
            "font": gs["title_font"]}


def _client_holds(event) -> bool:
    from app import clients
    return bool(clients.current() and clients.holds(event))


def _comment_marks(event, gs) -> set:
    """Pictures with a comment: the client sees marks on their own threads, the operator on all."""
    if not gs["comments"]:
        return set()
    from app import clients, comments
    if _is_admin():
        return comments.media_with_comments(int(event["id"]))
    client = clients.current()
    if client and clients.holds(event):
        return comments.media_with_comments(int(event["id"]), int(client["id"]))
    return set()


def _client_picks(event, gs) -> Optional[dict]:
    """The logged-in client's selections in this gallery (app/picks.py), or None.

    None for everybody without client access: then the page is what it was.
    """
    from app import clients, picks
    client = clients.current()
    if not client or not gs["selection"] or not clients.holds(event):
        return None
    sels = picks.for_client(client["id"], event["id"])
    return {"api": f"/kunde/api/{event['slug']}/selections", "max": picks.cap(event),
            "selections": sels, "default_name": t("picks.default_name")}


def _upload_state(event, gs) -> dict:
    """What the guest upload card needs: open or closed, PIN or not."""
    from app import guest_upload as gu
    until = gu.closes_at(event, gs)
    return {
        "open": gu.is_open(event, gs),
        "pin": gu.has_pin(event) and not session.get(f"upload_pin_{event['id']}"),
        # The last open day, as the guest reads it ("bis 21.11.2026").
        "until": (until - timedelta(days=1)).strftime("%d.%m.%Y") if until else "",
        "moderated": bool(gs["moderation"]),
    }


def _live_state(event, gs) -> Optional[dict]:
    """The "Live" badge (no. 59): None when the switch is off."""
    if not gs["live_badge"]:
        return None
    from app import live
    return {"on": live.is_live(event), "since": live.latest(event["id"])["last_id"],
            "feed": f"/gallery/{event['slug']}/live.json"}


def _tile_srcset(row, path_thumb: str, path_webp: str) -> str:
    """Both sizes that already exist for a picture, for ``srcset``.

    The tile showed the thumbnail at any width - in the list layout a 480 px
    file drawn 796 px wide. No new size is made here: the thumbnail is at
    least 600 px on its long edge (720 for booth uploads) and the web version
    at least 2048 (2400 for uploads); the smaller numbers are declared, so the
    browser never takes a file for sharper than it is.
    """
    if not path_thumb or not path_webp or path_thumb == path_webp:
        return ""
    w, h = int(row.get("width") or 0), int(row.get("height") or 0)
    if not w or not h:
        return ""
    tw = media.scaled_size(w, h, 600)[0]
    fw = media.scaled_size(w, h, 2048)[0]
    if fw <= tw:
        return ""
    return f"/data/{path_thumb} {tw}w, /data/{path_webp} {fw}w"


@bp.route("/gallery/<slug>", methods=["GET", "POST"])
def gallery(slug: str):
    event, canonical_slug, is_alias = db.resolve_event_slug(slug)
    if not event:
        abort(404)
    # A gallery that moved (e.g. to Picdrop) sends its guests there before
    # any other gate: the old name, the password form, an expired or an
    # unpublished gallery all forward. Only silent mode still says 404.
    forward = _forward_guest(event)
    if forward:
        return forward
    if is_alias:
        return redirect(url_for("public.gallery", slug=canonical_slug), 301)

    # Zeitplan zuerst: erreichte Startzeit schaltet live, offene zeigt Countdown.
    start = _not_yet_live(event)
    if start:
        return _coming_soon_page(event, start)

    # A folder lives behind its parent: unpublished or closed there, closed here.
    from app import folders
    parent = folders.parent_of(event)
    folders.apply_parent(event)
    view = _owner_view()
    if not event.get("published") and not view:
        abort(404)
    _deny_if_silent(event)

    # Access token check (direct/magic link) — MUST run before the password gate,
    # otherwise a magic link to a password-protected gallery is swallowed by the form.
    # Constant-time comparison: the key replaces the password (S18).
    access_token = request.args.get("access_token", "")
    stored_token = event.get("access_token") or ""
    if access_token and stored_token and hmac.compare_digest(access_token.encode(), stored_token.encode()):
        session[f"gallery_access_{event['id']}"] = True

    # A folder whose parent is still closed to this visitor: the door is the
    # parent's, so that is where the password field is.
    from app import clients
    if parent and not _is_admin() and not clients.has_event(event["id"]) and not _password_open(parent):
        return redirect(url_for("public.gallery", slug=parent["slug"]))

    # Password check
    if event.get("password_hash") and not _is_admin():
        sess_key = f"gallery_access_{event['id']}"
        if not _check_password_access(event):
            if request.method == "POST":
                _pruefe_csrf()
                from app import sharelinks as _sl
                # Ten failures per event and address in 15 minutes. Only
                # failures count: a table of guests typing the right password
                # one after another is never slowed down.
                brake = f"galpw:{event['id']}:{request.remote_addr}"
                wait = _sl.retry_after(brake, 10, 15 * 60)
                if wait:
                    return _password_page(event, error=_sl.wait_message(wait)), 429, {"Retry-After": str(wait)}
                entered = request.form.get("password", "")
                if db.check_event_password(event, entered):
                    _sl.forget(brake)
                    session[sess_key] = True
                    return redirect(url_for("public.gallery", slug=slug))
                _sl.record_failure(brake, 15 * 60)
                return _password_page(event, error=t("password.wrong"))
            return _password_page(event)

    from app import gallery_settings as gsm
    gs = gsm.for_event(event)
    if gsm.is_expired(gs) and not _is_admin():
        return render_template("public/session.html", event=event, state="status", items=[], uid="",
                               message=t("gallery.expired_text"), status_title=t("gallery.expired_title"), noindex=True), 410
    from app import clients
    if view == VIEW_PREVIEW:
        # Every gate above let the owner through; from here on the page is
        # built with a guest's rights (hidden, red-hidden, watermark, ZIP).
        g.guest_view = True
    else:
        clients.mark_visit(event["id"])
    manage = view == VIEW_MANAGE
    # Modus: Präsentation oder Abstimmung (Farbmarkierungen). Wechsel nur, wenn erlaubt (optional mit PIN).
    mode_key = f"gallery_mode_{event['id']}"
    wanted = request.args.get("mode")
    # ``allow_mode_switch`` regelt, was *Gäste* dürfen. Der angemeldete
    # Betreiber kommt immer in die Abstimmung — sonst hat er keinen Weg, seine
    # eigenen Aufnahmen zu markieren, und die Farbmarkierung ist in einer
    # Standardgalerie unerreichbar. Genau das ist Till am 19.09.2026
    # aufgefallen: „Das Markieren klappt nicht."
    darf_wechseln = gs["allow_mode_switch"] or _is_admin()
    if wanted in ("presentation", "proofing") and darf_wechseln:
        from app import sharelinks as _sl
        import hmac as _hmac
        sent_pin = str(request.form.get("mode_pin", request.args.get("pin", "")))
        pin_ok = bool(gs["mode_pin"]) and _hmac.compare_digest(sent_pin, str(gs["mode_pin"]))
        if sent_pin and not pin_ok and _sl.too_often(f"modepin:{event['id']}:{request.remote_addr}"):
            abort(429)
        if wanted == "proofing" and gs["mode_pin"] and not pin_ok and not _is_admin():
            if request.method == "POST":
                flash(t("share.passphrase_wrong"), "error")
            return render_template("public/password.html", event=event, error=None, csrf_token=_make_csrf(),
                                   pin_mode=True, title=t("gallery.pin_title"))
        session[mode_key] = wanted
        return redirect(url_for("public.gallery", slug=slug))
    proofing = ((session.get(mode_key) or gs["mode"]) == "proofing") if darf_wechseln \
        else gs["mode"] == "proofing"
    # Markieren darf, wer im Abstimmungsmodus ist — und der Betreiber immer.
    # Die Schnittstelle erlaubt ihm das seit jeher („Admin immer"), nur die
    # Oberfläche bot es nie an.
    darf_markieren = bool(gs["color_tags"]) and (proofing or _is_admin())

    # Embed mode (iframe)
    is_embed = request.args.get("embed") == "1"

    # Track gallery view - not the owner's own preview or manage view.
    src = request.args.get("src", "direct")
    try:
        if not view:
            db.add_stat(event["id"], "view_gallery", meta={"src": src})
    except Exception:
        pass

    # Medien laden. Originalfotos einzelner Durchgänge (Kind "original") stehen
    # hinter einem eigenen Filter, damit Collagen nicht doppelt erscheinen.
    all_media = db.list_media(event["id"]) or []
    hidden_ids: set = set()
    if manage:
        # Manage mode shows the hidden pictures too, dimmed and marked.
        extra = _hidden_media(int(event["id"]))
        hidden_ids = {int(m["id"]) for m in extra}
        all_media = sorted(all_media + extra, key=lambda m: (str(m.get("created_at") or ""), int(m["id"])),
                           reverse=True)
    # An unframed capture that belongs to a framed picture of the same shot is
    # offered in the large view of that picture ("Ohne Rahmen"), not as a
    # tile of its own.
    from app import ecosystem as _eco
    partner_of, absorbed = _eco.unframed_partners(all_media)
    all_media = [m for m in all_media if m["id"] not in absorbed]
    show_originals = request.args.get("show") == "originals"
    originals_count = sum(1 for m in all_media if (m.get("kind") or "photo") == "original")

    like_counts = db.like_counts_for_event(event["id"]) or {}
    my_device = session.get("device_id", "")
    liked_ids = db.liked_media_ids_for_device(event["id"], my_device) if my_device else set()

    kind_counts: dict = {}
    photos = []
    name_of = guest_namer(event)
    for m in all_media:
        kind = m.get("kind") or "photo"
        if (kind == "original") != show_originals and originals_count and len(all_media) > originals_count:
            continue
        if not proofing and not manage and gs["hide_red_in_presentation"] and m.get("color_tag") == "red":
            continue
        kind_counts[kind] = kind_counts.get(kind, 0) + 1
        is_video = kind in ("video", "boomerang", "gif") and str(m.get("path_original", "")).lower().endswith((".mp4", ".webm", ".mov"))
        path_webp = m.get("path_webp") or ("" if is_video else m.get("path_original")) or ""
        path_thumb = m.get("path_thumb") or path_webp
        wm = gs["watermark"] and not is_video and not _is_admin()
        srcset = "" if (wm or is_video) else _tile_srcset(m, path_thumb, path_webp)
        photos.append({
            "id":        m["id"],
            "kind":      kind,
            "is_video":  is_video,
            "wm":        wm,
            "url":       f"/data/{m['path_original']}" if is_video else (f"/data/{path_webp}" if path_webp else ""),
            "thumb_url": f"/data/{path_thumb}" if path_thumb else "",
            "filename":  m.get("filename") or f"foto_{m['id']}.jpg",
            "display_name": name_of(m),
            "width":     m.get("width") or 1920,
            "height":    m.get("height") or 1280,
            "share_token": m.get("share_token") or "",
            "like_count": int(like_counts.get(m["id"], 0)),
            "liked_by_me": m["id"] in liked_ids,
            "color_tag": m.get("color_tag") or "",
            "unframed_id": (partner_of.get(m["id"]) or {}).get("id"),
            "srcset":    srcset,
            **({"url": f"/gallery/{event['slug']}/wm/{m['id']}/full.jpg", "thumb_url": f"/gallery/{event['slug']}/wm/{m['id']}/thumb.jpg"} if wm else {}),
            "size_h": media.human_bytes(int(m.get("size_bytes") or 0)),
            "taken": _format_datetime(m.get("taken_at") or m.get("created_at")),
            "hidden": int(m["id"]) in hidden_ids,
            "pending": int(m["id"]) in hidden_ids and str(m.get("review_state") or "") == "pending",
        })

    has_multiple_kinds = len(kind_counts) > 1

    # Source chips (no. 60), only when the event switches them on for guests
    # and there is more than one source to choose from.
    source_chips = []
    if gs["show_source_filter"] and photos:
        from app import media_source
        shown = {p["id"] for p in photos}
        summary = media_source.summarize([m for m in all_media if m["id"] in shown], for_guests=True)
        if len(summary["chips"]) > 1:
            source_chips = summary["chips"]
            for p in photos:
                p["source"] = summary["of"].get(p["id"], "")

    # Settings
    zip_enabled = (db.get_setting("zip_download_enabled") or "0") == "1"
    zip_admin_only = (db.get_setting("zip_download_admin_only") or "1") == "1"
    guest_upload_enabled = (db.get_setting("guest_upload_enabled") or "0") == "1"
    # „Meine Auswahl": Gäste dürfen ihre eigene Foto-Auswahl als ZIP laden,
    # unabhängig vom (oft admin-only) „Alles herunterladen". Standard: an.
    selection_enabled = (db.get_setting("selection_download_enabled") or "1") == "1"
    folder_tiles = [] if parent else _folder_tiles(event)
    owner = _owner_bar(event, view, gs) if view else None
    if manage:
        from app import media_source
        src_of = media_source.summarize(all_media)["of"]
        for p in photos:
            p["guest_upload"] = src_of.get(p["id"]) == "guest"

    return render_template(
        "public/gallery.html",
        event=event,
        photos=photos,
        kind_counts=kind_counts,
        has_multiple_kinds=has_multiple_kinds,
        source_chips=source_chips,
        zip_enabled=_zip_allowed(event) and not (gs["watermark"] and not _is_admin()),
        selection_enabled=selection_enabled and gs["selection"],
        gs=gs, proofing=proofing, darf_markieren=darf_markieren,
        darf_wechseln=darf_wechseln, is_admin=_is_admin(), tags=gsm.TAGS, tag_colors={k: c for k, _, c in gsm.TAGS},
        tag_labels={"red": t("tag.red"), "orange": t("tag.orange"), "green": t("tag.green"), "final": t("tag.final")},
        download_sizes_enabled=_download_sizes_enabled(),
        dl_options=_labelled_download_options(gs, all_media),
        brand=branding.event_branding(event),
        expiry=_expiry_notice(event),
        show_originals=show_originals,
        originals_count=originals_count if len(all_media) > originals_count else 0,
        guest_upload_enabled=guest_upload_enabled or gs["guest_upload"],
        upload=_upload_state(event, gs),
        live=_live_state(event, gs),
        is_embed=is_embed,
        parent=parent,
        folder_tiles=folder_tiles,
        # A gallery made only of folders has no pictures of its own; its
        # header counts what is in the folders instead of saying "0 photos".
        photo_total=len(photos) + sum(f["count"] for f in folder_tiles if not f["locked"]),
        event_date=_format_date(event.get("date")),
        client_picks=_client_picks(event, gs),
        opener=_opener(event, gs),
        client_comments=bool(gs["comments"] and _client_holds(event)),
        comment_marks=_comment_marks(event, gs),
        owner=owner, manage=manage, view_qs=f"?ansicht={view}" if view else "",
        noindex=True,)


def _owner_bar(event, view: str, gs) -> dict:
    """What the owner's slim bar on top of the gallery needs."""
    from app.blueprints.admin import _csrf_token
    edit = url_for("admin.edit_event", event_id=int(event["id"]))
    slideshow = (url_for("public.slideshow", slug=event["slug"])
                 if event.get("published") and not event.get("parent_id") else "")
    return {
        "view": view,
        "state": _preview_state(event),
        "share_url": gallery_link(event),
        "close_url": edit,
        "slideshow_url": slideshow,
        "preview_url": url_for("public.gallery", slug=event["slug"], ansicht=VIEW_PREVIEW),
        "manage_url": url_for("public.gallery", slug=event["slug"], ansicht=VIEW_MANAGE),
        "api": url_for("admin.manage_media", event_id=int(event["id"])),
        "csrf": _csrf_token(),
        "event_id": int(event["id"]),
    }

# ─── „Zugriff anfragen" ───────────────────────────────────────
# Der Gast hat den Link, aber nicht das Passwort. Statt beim Veranstalter
# anzurufen, hinterlässt er seine Adresse; der Betreiber entscheidet im Admin.
# Sieht der Gast den Knopf nicht, gibt es die Route trotzdem nicht: ``enabled``
# prüft Schalter, Passwort und Postausgang.

@bp.post("/gallery/<slug>/zugang")
def gallery_access_request(slug: str):
    from app import access, sharelinks
    event, canonical_slug, _alias = db.resolve_event_slug(slug)
    if not event or not event.get("published"):
        abort(404)
    _deny_if_silent(event)
    if not access.enabled(event):
        abort(404)
    if sharelinks.too_often(f"zugang:{event['id']}:{request.remote_addr}", limit=5, window=3600):
        return _password_page(event, error=t("access.too_often")), 429

    req = access.create(event, email=request.form.get("email", ""),
                        name=request.form.get("name", ""),
                        note=request.form.get("note", ""),
                        lang=getattr(g, "lang", "de"))
    if not req:
        return _password_page(event, error=t("access.invalid")), 400
    base = _get_base_url() or request.host_url.rstrip("/")
    access.notify_admin(event, req, f"{base}/admin/events/{event['id']}#zugang")
    return _password_page(event, sent=True)


# ─── Individual Share Links ───────────────────────────────────

@bp.get("/s/<share_token>")
def share_redirect(share_token: str):
    """Redirect share token to gallery with photo highlighted."""
    media = db.get_media_by_share_token(share_token)
    if not media:
        abort(404)
    event = db.get_event_by_id(media["event_id"])
    if not event:
        abort(404)
    # Redirect to gallery with photo anchor
    gallery_url = url_for("public.gallery", slug=event["slug"])
    return redirect(f"{gallery_url}#photo-{media['id']}", 301)


# ─── „Meine Aufnahmen" — ohne Biometrie ───────────────────────
# Pic-Time & Co. suchen Gesichter. Wir nicht: Die Liste entsteht aus den
# QR-Codes, die dieser Browser gescannt hat (``session['p_uids']``). Kein
# Gesichtsmodell, keine Anmeldung, nichts davon verlässt das Gerät.

@bp.get("/meine")
def my_shots():
    """Alle Durchgänge, die dieser Browser gescannt hat — je Durchgang gruppiert."""
    from app import ecosystem, gallery_settings as gsm
    ecosystem.init_schema()

    groups = []
    for uid in (session.get("p_uids") or []):
        rows = [m for m in ecosystem.session_media(uid)
                if m.get("variant") != "original" or m.get("kind") == "original"]
        if not rows:
            continue
        event = db.get_event_by_id(rows[0]["event_id"])
        if not event:
            continue
        gs = gsm.for_event(event)
        if (gsm.is_expired(gs) or _not_yet_live(event)) and not _is_admin():
            continue        # abgelaufen oder noch nicht freigeschaltet: nichts zeigen

        sizes = [s for s in media.allowed_download_sizes(gs) if s != "original"]
        name_of = guest_namer(event)
        shots = []
        for m in sorted(rows, key=lambda r: ((r.get("kind") == "original"), r["id"])):
            is_video = m.get("kind") in ("boomerang", "video")
            shots.append({
                "id": m["id"],
                "is_video": is_video,
                "src": f"/data/{m['path_original']}" if is_video else f"/data/{m.get('path_webp') or m['path_original']}",
                "poster": f"/data/{m['path_webp']}" if m.get("path_webp") else "",
                "width": m.get("width") or 4,
                "height": m.get("height") or 5,
                "filename": name_of(m),          # what the guest sees and the buttons hand over
                "share_token": m.get("share_token") or "",
            })
        groups.append({
            "uid": uid,
            "code": f"{uid}-{ecosystem.signature(ecosystem.ensure_share_key(event), uid)}",
            "event": event,
            "shots": shots,
            "taken": max((m.get("taken_at") or m.get("created_at") or "") for m in rows),
            "download_size": sizes[-1] if sizes else "",
            "allow_original": bool(gs["download_original"]),
            "allow_download": bool(gs["download_single"]) and not gs["watermark"],
            # Sharing follows the event's switches, as in its gallery.
            "share": {"native": bool(gs["share_native"]), "link": bool(gs["share_link_copy"])},
        })

    groups.sort(key=lambda gr: gr["taken"], reverse=True)
    return render_template("public/my_shots.html", groups=groups, noindex=True)


@bp.post("/meine/leeren")
def my_shots_clear():
    """Liste dieses Browsers vergessen — die Fotos selbst bleiben unberührt."""
    session.pop("p_uids", None)
    return redirect(url_for("public.my_shots"))


# ─── Weiter zur Bewertung ─────────────────────────────────────
# Der Gast klickt hier, wir zählen den Klick als Tagessumme und schicken ihn
# weiter. Kein Zählpixel eines Dritten, keine Zeile je Person — siehe
# app/bewertung.py.

@bp.get("/bewertung/<slug>")
def bewertung_weiter(slug: str):
    from app import bewertung, gallery_settings as gsm

    event, canonical_slug, _ = db.resolve_event_slug(slug)
    if not event:
        abort(404)
    cfg = bewertung.fuer_event(event, gsm.for_event(event))
    if not cfg["an"]:
        abort(404)
    bewertung.zaehle(event.get("id"))
    return redirect(cfg["url"], 302)


# ─── Gallery QR Image
# ─── Gallery QR Image ─────────────────────────────────────────

@bp.get("/gallery/<slug>/qr.png")
def gallery_qr_image(slug: str):
    from app import qrstyle, sharepic
    event = _event_by_slug(slug)
    if not event:
        abort(404)
    _deny_if_silent(event)
    if not event.get("published") and not _is_admin():
        abort(404)
    gallery_url = gallery_link(event, mit_schluessel=_darf_schluessel_sehen(event))
    cfg = sharepic.settings()
    size = max(200, min(int(request.args.get("size", 800) or 800), 2000))
    img = qrstyle.render(gallery_url, size, cfg["qr_style"], sharepic.logo_path(cfg))
    buf = io.BytesIO()
    img.save(buf, "PNG")
    buf.seek(0)
    return send_file(buf, mimetype="image/png", max_age=600)


# ─── Gallery Sharepic ─────────────────────────────────────────

@bp.get("/gallery/<slug>/sharepic.png")
def gallery_sharepic(slug: str):
    from app import sharepic
    event = _event_by_slug(slug)
    if not event:
        abort(404)
    _deny_if_silent(event)
    if not event.get("published") and not _is_admin():
        abort(404)
    gallery_url = gallery_link(event, mit_schluessel=_darf_schluessel_sehen(event))
    png = sharepic.render(event, gallery_url)
    name = f"sharepic-{slug}.png"
    return send_file(io.BytesIO(png), mimetype="image/png", max_age=300,
                     as_attachment=request.args.get("download") == "1", download_name=name)


@bp.get("/gallery/<slug>/share")
def gallery_share_page(slug: str):
    """Seite zum Sharepic — Ziel des „Galerie aufs Handy"-QR-Codes an der Box."""
    event = _event_by_slug(slug)
    if not event:
        abort(404)
    forward = _forward_guest(event)
    if forward:
        return forward
    if not event.get("published"):
        abort(404)
    _deny_if_silent(event)
    return render_template("public/share.html", event=event, noindex=True)


# ─── Print QR Page ────────────────────────────────────────────

@bp.route("/gallery/<slug>/print")
def print_qr(slug: str):
    """A4 printable page with QR code."""
    event = _event_by_slug(slug)
    if not event:
        abort(404)
    _deny_if_silent(event)
    if not event.get("published") and not _is_admin():
        abort(404)
    # Switched off for the guests ("QR-Code drucken"): the page is closed too,
    # not only the button. The operator still prints it from the admin.
    if not _gs(event)["print_qr"] and not _is_admin():
        abort(404)
    gallery_url = gallery_link(event, mit_schluessel=_darf_schluessel_sehen(event))
    show_powered_by = db.get_setting("footer_show_powered_by") != "0"
    logo_path = db.get_setting("branding_logo_path") or ""
    return render_template(
        "public/print_qr.html",
        event=event,
        gallery_url=gallery_url,
        footer_show_powered_by=show_powered_by,
        branding_logo=bool(logo_path),
    )


# ─── Gallery ZIP Download ─────────────────────────────────────

def _zip_not_allowed(event):
    """A page instead of a bare 403 when the ZIP is off for guests (final review U4)."""
    resp = make_response(render_template("public/zip_not_allowed.html", event=event,
                                         brand=branding.event_branding(event), noindex=True), 403)
    resp.headers["Cache-Control"] = "no-store"
    return resp


@bp.get("/gallery/<slug>/download.zip")
def gallery_download_zip(slug: str):
    event = _event_by_slug(slug)
    if not event:
        abort(404)
    _deny_if_silent(event)
    if not event.get("published") and not _is_admin():
        abort(404)
    if not _check_password_access(event):
        abort(403)
    if _expired_for_guest(event):
        abort(410)
    if _not_yet_live(event):
        abort(403)
    if not _zip_allowed(event) or (_gs(event)["watermark"] and not _is_admin()):
        return _zip_not_allowed(event)

    all_media = db.list_media(event["id"]) or []
    
    # Handle selective download via ids parameter
    ids_param = request.args.get('ids')
    if ids_param:
        id_list = [int(id_str) for id_str in ids_param.split(',') if id_str.strip().isdigit()]
        if id_list:
            all_media = [m for m in all_media if m.get('id') in id_list]

    from app import dateinamen
    nummern = dateinamen.nummern(int(event["id"]))
    namen = dateinamen.event_datetime_namen(event)
    entries = []
    benutzt: set = set()
    for i, m in enumerate(all_media, 1):
        rel = m.get("path_original") or m.get("path_webp")
        if not rel:
            continue
        full = media.data_dir() / rel
        if not full.exists():
            continue
        name = _abzugname(event, m, f"{i:04d}{full.suffix or '.jpg'}", "original",
                          nummer=nummern.get(int(m["id"]), i), namen=namen)
        # Zwei gleiche Namen in einem ZIP sind ein verlorenes Foto.
        if name in benutzt:
            stamm, _, endung = name.rpartition(".")
            name = f"{stamm}-{i}.{endung}" if stamm else f"{name}-{i}"
        benutzt.add(name)
        entries.append((full, name))
    if request.args.get("folders") == "1" and not event.get("parent_id"):
        entries += _folder_zip_entries(event)
    return _zip_response(entries, f"{slug}-fotos.zip")


def _folder_zip_entries(event) -> list:
    """The folders of a gallery for its ZIP, one directory each.

    A folder comes along only if this visitor could download its own ZIP:
    same gates, same switches. A folder that says no stays out, silently -
    the rest of the archive is still what was asked for.
    """
    from app import dateinamen, folders
    out = []
    used_dirs: set = set()
    for child in folders.children_of(int(event["id"])):
        folders.apply_parent(child)
        if not (child.get("published") or _is_admin()) or _silent(child):
            continue
        if not _zip_allowed(child) or not _check_password_access(child) or _expired_for_guest(child):
            continue
        if _not_yet_live(child) or (_gs(child)["watermark"] and not _is_admin()):
            continue
        folder = secure_filename(child["name"]) or child["slug"]
        while folder in used_dirs:
            folder = f"{folder}-{child['id']}"
        used_dirs.add(folder)
        nummern = dateinamen.nummern(int(child["id"]))
        namen = dateinamen.event_datetime_namen(child)
        names: set = set()
        for i, m in enumerate(db.list_media(int(child["id"])) or [], 1):
            rel = m.get("path_original") or m.get("path_webp")
            if not rel:
                continue
            full = media.data_dir() / rel
            if not full.exists():
                continue
            name = _abzugname(child, m, f"{i:04d}{full.suffix or '.jpg'}", "original",
                              nummer=nummern.get(int(m["id"]), i), namen=namen)
            if name in names:
                stamm, _, endung = name.rpartition(".")
                name = f"{stamm}-{i}.{endung}" if stamm else f"{name}-{i}"
            names.add(name)
            out.append((full, f"{folder}/{name}"))
    return out


# ─── Einzelfoto-Download in wählbarer Größe ───────────────────

def _download_sizes_enabled() -> bool:
    return (db.get_setting("download_sizes_enabled") or "1") == "1"


def _labelled_download_options(gs: dict, rows: list) -> list:
    """Größen fürs Auswahl-Blatt, mit übersetzten Texten und Größenschätzung."""
    if not _download_sizes_enabled():
        return []
    out = []
    for opt in media.download_options(gs, rows):
        out.append({
            **opt,
            "label": t(f"dl.size_{opt['id']}"),
            "hint": t(f"dl.size_{opt['id']}_hint"),
            "estimate": t("dl.size_about", size=opt["size_h"]) if opt["size_h"] else "",
        })
    return out


def _requested_size(raw: Optional[str], gs: Optional[dict] = None) -> str:
    """Gewünschte Größe — bei abgeschalteter Auswahl immer das Original.

    Ist die gewünschte Größe für dieses Event abgeschaltet, liefern wir die
    größte erlaubte statt einer Fehlerseite. Das Original bleibt streng: dafür
    entscheidet ``download_original`` in der Route.
    """
    if not _download_sizes_enabled():
        return "original"
    size = media.normalize_download_size(raw)
    if gs is None:
        return size
    allowed = media.allowed_download_sizes(gs)
    if size in allowed:
        return size
    scaled = [s for s in allowed if s != "original"]
    return scaled[-1] if scaled else "original"


@bp.get("/gallery/<slug>/photo/<int:media_id>/download")
def download_photo(slug: str, media_id: int):
    from app import ecosystem, storage

    event = _event_by_slug(slug)
    if not event:
        abort(404)
    _deny_if_silent(event)
    row = db.get_media(media_id)
    if not row or int(row["event_id"]) != int(event["id"]):
        abort(404)          # nie Fotos aus fremden Events ausliefern

    # Zugang: veröffentlichte Galerie mit Passwort-Session — oder ein gültiger
    # QR-Code dieses Durchgangs (der Link auf der Durchgangs-Seite).
    code_ok = False
    if _wartet_auf_freigabe(row) and not _is_admin():
        abort(404)
    code = request.args.get("code", "")
    if code and row.get("session_uid"):
        uid, sig = ecosystem.parse_share_code(code)
        ev = ecosystem.find_event_for_share(uid, sig) if uid else None
        code_ok = bool(ev and int(ev["id"]) == int(event["id"]) and uid == row["session_uid"])
    if not code_ok and not _is_admin():
        if not event.get("published") or not _check_password_access(event):
            abort(403 if event.get("published") else 404)
    if _expired_for_guest(event):
        abort(410)
    if not code_ok and _not_yet_live(event):
        abort(403)          # vor dem Start nur der eigene Durchgang per QR-Code

    gs = _gs(event)
    if not code_ok and not _is_admin():
        if not gs["download_single"] or gs["watermark"] or (request.args.get("size") == "original" and not gs["download_original"]):
            abort(403)
    if code_ok and not _is_admin():
        # The run page /p/<code> follows the same switches since 15.11.2026:
        # no single downloads, no original, no "ohne Rahmen" when they are off.
        # The watermark stays the gallery's business: the guest owns their run.
        if not gs["download_single"] or (request.args.get("size") == "original" and not gs["download_original"]):
            abort(403)
        if not gs["run_frame_choice"] and (row.get("kind") or "") == "original":
            _partners, absorbed = ecosystem.unframed_partners(ecosystem.session_media(row["session_uid"]))
            if row["id"] in absorbed:
                abort(403)
    size = _requested_size(request.args.get("size"), gs)
    base = secure_filename(row["filename"] or f"foto_{media_id}") or f"foto_{media_id}"
    stem = base.rsplit(".", 1)[0]

    # Box-Uploads: das Original liegt als eigene Variante neben der Web-Fassung
    source_row = row
    if size == "original" and row.get("uid") and row.get("variant") == "web":
        source_row = ecosystem.media_by_uid(row["uid"], "original") or row

    try:
        db.add_stat(event["id"], "download", media_id=media_id, meta={"size": size})
    except Exception:
        pass

    if source_row.get("storage") == "remote" and source_row.get("remote_path"):
        if size != "original" and row.get("path_webp"):
            source_rel = row["path_webp"]        # skaliert aus der Web-Fassung, NAS nicht nötig
        else:
            s = storage.store()
            if not s:
                abort(503)
            try:
                gen = s.stream(source_row["remote_path"])
                first = next(gen)
            except (storage.RemoteUnavailable, StopIteration):
                return render_template("public/notify_result.html", ok=False,
                                       message=t("session.status_text")), 503
            def body():
                yield first
                yield from gen
            from app import dateinamen
            name = _abzugname(event, source_row, secure_filename(source_row["filename"]) or base, size)
            return current_app.response_class(body(), mimetype="application/octet-stream",
                                              headers={"Content-Disposition": dateinamen.content_disposition(name)})
    else:
        source_rel = source_row["path_original"] or source_row["path_webp"]
    if not source_rel:
        abort(404)

    variant = media.build_download_variant(media_id, source_rel, size)
    if variant is not None:
        path, name = variant, f"{stem}.jpg"
    else:
        path = (media.data_dir() / source_rel).resolve()
        if not str(path).startswith(str(media.data_dir())) or not path.exists():
            abort(404)
        name = base if "." in base else f"{stem}{path.suffix or '.jpg'}"
    name = _abzugname(event, source_row, name, size)
    return send_file(path, as_attachment=True, download_name=name)


@bp.get("/gallery/<slug>/photo/<int:media_id>/exif.json")
def photo_exif(slug: str, media_id: int):
    """Camera, focal length, exposure, aperture, ISO - never a position.

    Off unless the gallery switches ``exif`` on; same gates as the gallery
    (published, silent, password/code/parent, expiry, schedule, approval).
    """
    from app import ecosystem
    event = _event_by_slug(slug)
    if not event:
        abort(404)
    row = db.get_media(media_id)
    if not row or int(row["event_id"]) != int(event["id"]) or not _gs(event)["exif"]:
        abort(404)
    _deny_if_silent(event)
    if not _is_admin():
        if _wartet_auf_freigabe(row) or int(row.get("hidden") or 0) or not event.get("published"):
            abort(404)
        if not _check_password_access(event) or _expired_for_guest(event) or _not_yet_live(event):
            abort(403)
    source = row
    if row.get("uid") and row.get("variant") == "web":
        source = ecosystem.media_by_uid(row["uid"], "original") or row
    rel = source.get("path_original") if source.get("storage", "local") in (None, "", "local") else None
    return jsonify(_exif_line((media.data_dir() / rel) if rel else None))


def _exif_line(path) -> dict:
    """The five values a photographer reads, as text. Everything else is dropped."""
    out: dict = {}
    if not path or not path.is_file():
        return out
    try:
        from PIL import Image
        with Image.open(path) as img:
            exif = img.getexif()
            sub = exif.get_ifd(0x8769)
    except Exception:                   # noqa: BLE001 — no EXIF is an answer too
        return out

    def num(v):
        try:
            return float(v)
        except (TypeError, ValueError, ZeroDivisionError):
            return None
    model = str(exif.get(0x0110) or "").strip().strip("\x00")
    if model:
        out["camera"] = model[:60]
    focal = num(sub.get(0x920A))
    if focal:
        out["focal"] = f"{focal:.0f} mm"
    exposure = num(sub.get(0x829A))
    if exposure:
        out["exposure"] = f"1/{round(1 / exposure)} s" if exposure < 1 else f"{exposure:g} s"
    aperture = num(sub.get(0x829D))
    if aperture:
        out["aperture"] = f"f/{aperture:g}"
    iso = sub.get(0x8827)
    if isinstance(iso, (tuple, list)):
        iso = iso[0] if iso else None
    if num(iso):
        out["iso"] = f"ISO {int(num(iso))}"
    return out


def _abzugname(event, row, rueckfall: str, groesse: str = "", nummer: Optional[int] = None,
               namen: Optional[dict] = None) -> str:
    """Der Name, unter dem diese Datei beim Gast ankommt. Siehe app/dateinamen.py.

    ``namen`` is ``dateinamen.event_datetime_namen(event)`` when the caller
    names many files of one event (a ZIP): one query instead of one per file.
    """
    from app import dateinamen

    fest = dateinamen.auslieferungsname(event, row, rueckfall, gs=_gs(event), namen=namen)
    if fest:
        return fest
    muster = dateinamen.muster_fuer(event, _gs(event))
    if not muster:
        # "Original" chosen on purpose keeps the box's name. Nothing chosen at
        # all gets a readable one instead of the storage id.
        if dateinamen.regel_gewaehlt(event, _gs(event)) == "original":
            return rueckfall
        muster = dateinamen.STANDARDMUSTER
    endung = (rueckfall.rsplit(".", 1)[-1] if "." in rueckfall else "jpg")
    if nummer is None:
        nummer = dateinamen.laufende_nummer(int(event["id"]), row)
    return dateinamen.baue(muster, event=event, medium=row, endung=endung,
                           nummer=nummer, groesse=groesse, rueckfall=rueckfall)


def guest_namer(event):
    """``name(row)`` for every picture of one event: the name its download would get.

    What a guest sees (file-info line, aria-labels) and what the share and
    download buttons hand over is this name, never the storage id. The numbers
    and the rule's names are read once here, so a page of five hundred pictures
    costs two queries, not five hundred.
    """
    from app import dateinamen

    # Everything that is the same for every picture is resolved here, once:
    # until 17.11.2026 each name re-read the event's settings three times and
    # the account settings two or three times (a SQLite connection each),
    # 2.5 s more for a page of 2000 pictures. Same order as _abzugname.
    gs = _gs(event)
    regel = dateinamen.regel_fuer(event, gs)
    nummern = dateinamen.nummern(int(event["id"]))
    namen = dateinamen.event_datetime_namen(event) if regel == "event_datetime" else None
    muster = "" if namen is not None else dateinamen.muster_fuer(event, gs)
    original = namen is None and not muster and dateinamen.regel_gewaehlt(event, gs) == "original"
    muster = muster or dateinamen.STANDARDMUSTER

    def name(row, groesse: str = "") -> str:
        base = secure_filename(row.get("filename") or "") or f"foto_{row.get('id')}"
        if "." not in base:
            base += ".jpg"
        if namen is not None:
            return dateinamen.event_datetime_auslieferung(event, row, base, namen)
        if original:
            return base
        endung = base.rsplit(".", 1)[-1] if "." in base else "jpg"
        return dateinamen.baue(muster, event=event, medium=row, endung=endung,
                               nummer=dateinamen.nummer_fuer(nummern, row), groesse=groesse, rueckfall=base)
    return name


# ─── „Meine Auswahl" (gastfreundlich) ─────────────────────────
# Gäste wählen einzelne Fotos aus und laden sie als ein ZIP – bewusst
# UNABHÄNGIG vom (oft admin-only) „Alles herunterladen". Eigenes Setting,
# Pflicht-`ids` (nie „alles"), gedeckelt gegen Missbrauch.

SELECTION_MAX = 100


@bp.get("/gallery/<slug>/selection.zip")
def gallery_download_selection(slug: str):
    if (db.get_setting("selection_download_enabled") or "1") != "1":
        abort(403)
    event = _event_by_slug(slug)
    if not event or not event.get("published"):
        abort(404)
    _deny_if_silent(event)
    if not _check_password_access(event):
        abort(403)
    if _expired_for_guest(event):
        abort(410)
    if _not_yet_live(event):
        abort(403)
    if not _gs(event)["selection"] or (_gs(event)["watermark"] and not _is_admin()):
        abort(403)

    ids_param = request.args.get("ids", "")
    id_list = [int(s) for s in ids_param.split(",") if s.strip().isdigit()]
    # Reihenfolge/Duplikate bereinigen, Deckel anwenden
    seen: set = set()
    id_list = [i for i in id_list if not (i in seen or seen.add(i))][:SELECTION_MAX]
    if not id_list:
        abort(400)

    id_set = set(id_list)
    all_media = db.list_media(event["id"]) or []
    selected = [m for m in all_media if m.get("id") in id_set]
    if not selected:
        abort(404)

    size = _requested_size(request.args.get("size"), _gs(event))

    used_names: set = set()
    entries = []
    from app import dateinamen
    nummern = dateinamen.nummern(int(event["id"]))
    namen = dateinamen.event_datetime_namen(event)
    if True:
        for i, m in enumerate(selected, 1):
            rel = m.get("path_original") or m.get("path_webp")
            if not rel:
                continue
            full = media.data_dir() / rel
            if not full.exists():
                continue

            # Skalierte Fassung, wenn gewünscht — sonst das Original
            variant = media.build_download_variant(m["id"], rel, size)
            if variant is not None:
                full, ext = variant, ".jpg"
            else:
                ext = full.suffix or ".jpg"

            base = secure_filename(m.get("filename") or "") or f"foto_{m.get('id')}"
            stem = base.rsplit(".", 1)[0] if "." in base else base
            arcname = _abzugname(event, m, f"{i:03d}_{stem}{ext}", size,
                                 nummer=nummern.get(int(m["id"]), i), namen=namen)
            while arcname in used_names:
                arcname = f"{i:03d}_{secrets.token_hex(2)}_{stem}{ext}"
            used_names.add(arcname)
            entries.append((full, arcname))

    try:
        db.add_stat(event["id"], "download_selection",
                    meta={"count": len(selected), "size": size})
    except Exception:
        pass

    return _zip_response(entries, f"{slug}-auswahl.zip")


# ─── Kiosk ────────────────────────────────────────────────────

@bp.get("/kiosk/<slug>")
def kiosk(slug: str):
    """Das QR-Plakat auf dem Tablet am Tresen.

    Zeigt Vorschaubilder und den Code zur Galerie — also dasselbe, wofür die
    Slideshow Veröffentlichung und Passwort verlangt. Bis zum 19.09.2026 tat
    das hier niemand: Wer den Namen kannte, sah die Bilder eines noch nicht
    veröffentlichten oder passwortgeschützten Events.
    """
    event = _event_by_slug(slug)
    if not event:
        abort(404)
    _deny_if_silent(event)
    if not event.get("published") and not _is_admin():
        abort(404)
    if not _check_password_access(event):
        return _password_page(event)        # ein 403 wäre eine Sackgasse
    if _expired_for_guest(event):
        abort(410)
    start = _not_yet_live(event)
    if start:
        return _coming_soon_page(event, start)
    gallery_url = gallery_link(event, mit_schluessel=_darf_schluessel_sehen(event))
    # Get last 20 photos for slideshow background
    all_media = db.list_media(event["id"]) or []
    slideshow_photos = []
    for m in reversed(all_media[-20:]):
        thumb = m.get("path_thumb") or m.get("path_webp")
        if thumb:
            slideshow_photos.append({"url_thumb": f"/data/{thumb}"})
    return render_template(
        "public/kiosk.html",
        event=event,
        gallery_url=gallery_url,
        slideshow_photos=slideshow_photos,
        brand=branding.event_branding(event),
        noindex=True,)


# ─── Slideshow (Beamer / Screen am Event) ─────────────────────
# Eigene Ansicht neben /kiosk: der Kiosk ist das QR-Plakat, die Slideshow
# zeigt die Fotos groß. Lädt neue Fotos per JSON nach – kein Seiten-Reload,
# damit der Übergang auf dem Beamer nicht blitzt.

SLIDESHOW_MAX = 200


def _slideshow_media(event_id: int) -> list:
    """Fotos für die Slideshow (neueste zuerst), nur anzeigbare Bilder."""
    out = []
    event = db.get_event_by_id(event_id)
    name_of = guest_namer(event) if event else (lambda row: row.get("filename") or f"foto_{row['id']}.jpg")
    for m in (db.list_media(event_id, limit=SLIDESHOW_MAX) or []):
        if (m.get("kind") or "photo") == "video":
            continue  # Videos spielen wir in der Slideshow (noch) nicht ab
        path = m.get("path_webp") or m.get("path_original")
        if not path:
            continue
        out.append({
            "id": int(m["id"]),
            "url": f"/data/{path}",
            "thumb": f"/data/{m.get('path_thumb') or path}",
            "name": name_of(m),
            "token": m.get("share_token") or "",
            "created_at": m.get("created_at") or "",
        })
    return out


def _slideshow_event_or_abort(slug: str):
    event = _event_by_slug(slug)
    if not event or not event.get("published"):
        abort(404)
    _deny_if_silent(event)
    if not _check_password_access(event):
        abort(403)
    if _expired_for_guest(event):
        abort(410)
    if _not_yet_live(event):
        abort(403)
    return event


@bp.get("/slideshow/<slug>")
def slideshow(slug: str):
    event = _slideshow_event_or_abort(slug)
    photos = _slideshow_media(event["id"])
    return render_template(
        "public/slideshow.html",
        event=event,
        photos=photos,
        show_order=_gs(event)["show_order"],
        gallery_url=gallery_link(event),
        brand=branding.event_branding(event),
        noindex=True,
    )


@bp.get("/gallery/<slug>/slideshow.json")
def slideshow_feed(slug: str):
    """Aktueller Fotostand für die laufende Slideshow (Polling)."""
    event = _slideshow_event_or_abort(slug)
    return jsonify({"ok": True, "photos": _slideshow_media(event["id"])})


@bp.get("/gallery/<slug>/live.json")
def gallery_live(slug: str):
    """What the "Live" badge polls (no. 59): live or not, and how many new.

    The same gates as the gallery page; switched off it does not exist. Only
    numbers, never a picture or an address.
    """
    from app import folders, live
    event = _event_by_slug(slug)
    if not event:
        abort(404)
    folders.apply_parent(event)
    if not _gs(event)["live_badge"] or _silent(event):
        abort(404)
    if not event.get("published") and not _is_admin():
        abort(404)
    if _not_yet_live(event) or not _check_password_access(event):
        abort(403)
    if _expired_for_guest(event):
        abort(410)
    since = request.args.get("since", type=int) or 0
    resp = jsonify({"live": live.is_live(event), "new": live.new_since(event["id"], since)})
    resp.headers["Cache-Control"] = "no-store"
    return resp


# ─── Kiosk für Gäste (iPad, Handy, zweiter PC) ────────────────
# dslrBooth hat dafür eine eigene iPad-App. Wir brauchen keine: Ein Browser
# auf irgendeinem Gerät, dieselbe Galerie, ein Vollbild zum Anfassen. Die
# Bilder laufen als Slideshow (der Reihe nach oder zufällig), ein Tipp hält
# an, und wer sein Foto mitnehmen will, bekommt es per Teilen-Blatt
# (auf dem iPad heißt das AirDrop), als Download oder über einen QR-Code
# aufs eigene Handy.

def _kiosk_event_or_abort(slug: str):
    event = _event_by_slug(slug)
    if not event or not event.get("published"):
        abort(404)
    _deny_if_silent(event)
    gs = _gs(event)
    if not gs["kiosk"] and not _is_admin():
        abort(404)                      # der Betreiber schaltet ihn frei, sonst gibt es ihn nicht
    if not _check_password_access(event):
        abort(403)
    if _expired_for_guest(event):
        abort(410)
    if _not_yet_live(event):
        abort(403)
    return event, gs


@bp.get("/schau/<slug>")
def kiosk_guests(slug: str):
    event, gs = _kiosk_event_or_abort(slug)
    photos = _slideshow_media(event["id"])
    return render_template(
        "public/kiosk_guests.html",
        event=event,
        photos=photos,
        gs=gs,
        show_order=gs["show_order"],
        teilen=bool(gs["kiosk_share"]),
        laden=bool(gs["kiosk_share"] and gs["download_single"] and not gs["watermark"]),
        gallery_url=gallery_link(event),
        brand=branding.event_branding(event),
        noindex=True,
    )


@bp.get("/gallery/<slug>/photo/<int:media_id>/qr.png")
def photo_qr(slug: str, media_id: int):
    """QR-Code zu einem einzelnen Foto — der sicherste Weg aufs eigene Handy.

    Kein Netzwerk zwischen zwei Geräten, keine App: Der Gast scannt vom
    Kiosk-Bildschirm und landet auf der Seite seines Fotos.
    """
    from app import qrstyle, sharepic
    event = _event_by_slug(slug)
    if not event or not event.get("published"):
        abort(404)
    _deny_if_silent(event)
    if not _check_password_access(event):
        abort(403)
    row = db.get_media(media_id)
    if not row or int(row["event_id"]) != int(event["id"]):
        abort(404)
    if _wartet_auf_freigabe(row) and not _is_admin():
        abort(404)
    token = row.get("share_token") or ""
    base = _get_base_url() or request.host_url.rstrip("/")
    ziel = f"{base}/s/{token}" if token else gallery_link(event)
    cfg = sharepic.settings()
    size = max(200, min(int(request.args.get("size", 520) or 520), 1200))
    img = qrstyle.render(ziel, size, cfg["qr_style"], sharepic.logo_path(cfg))
    buf = io.BytesIO()
    img.save(buf, "PNG")
    buf.seek(0)
    return send_file(buf, mimetype="image/png", max_age=600)


# ─── Impressum / Datenschutz ──────────────────────────────────

def _markdown_to_html(text: str) -> str:
    """Operator Markdown for guests: escaped and filtered (app/safe_markdown.py, final review A5)."""
    from app import safe_markdown
    return safe_markdown.render(text)


@bp.get("/impressum")
def impressum():
    from app import legal
    md = legal.page_markdown("impressum", g.lang, host=request.host)
    # A page with "[Name des Betreibers]" on it never reaches a guest (app/legal.py).
    md = legal.for_viewer("impressum", md, lang=g.lang, is_admin=_is_admin())
    return render_template("public/impressum.html", content=_markdown_to_html(md))


@bp.route("/ueber")
@bp.route("/about")
def about_page():
    """Über die Software: welche Fassung läuft, wo das Projekt zu Hause ist.

    Bewusst öffentlich — wer ein Foto von einer fremden Box bekommt, soll sehen
    können, was ihn da ausgeliefert hat. Systemdetails sieht nur die Verwaltung.
    """
    from app import about
    return render_template("public/about.html", info=about.info(),
                           env=about.info(True) if _is_admin() else None,
                           is_admin=_is_admin(),
                           operator=(db.get_setting("legal_operator") or ""))


@bp.get("/datenschutz")
def datenschutz():
    from app import legal
    # Opened from a gallery, the page names that event's own deletion date.
    # Only for events whose gallery a guest can open (published, not silent):
    # the page must not confirm that an unpublished event exists.
    event = None
    slug = (request.args.get("event") or "").strip()
    if slug:
        candidate = db.get_event_by_slug(slug)
        if candidate and (candidate.get("published") or _is_admin()) and not _silent(candidate):
            event = candidate
    md = legal.page_markdown(
        "datenschutz", g.lang, host=request.host, event=event,
        analytics_src=(g.umami_src if getattr(g, "umami_enabled", False) else ""),
    )
    md = legal.for_viewer("datenschutz", md, lang=g.lang, is_admin=_is_admin())
    return render_template("public/datenschutz.html", content=_markdown_to_html(md))


@bp.route("/faq")
def faq():
    from app import safe_markdown
    items = db.get_faq_items()
    intro_raw = db.get_setting("faq_intro_text") or ""
    # Operator text for guests: escaped and filtered like the legal pages (A5).
    intro_html = safe_markdown.render(intro_raw, ("nl2br",))
    rendered = [{**item, "a_html": safe_markdown.render(item.get("a", ""), ("nl2br",))} for item in items]
    return render_template("public/faq.html", items=rendered, intro_html=intro_html)


# ─── Misc ─────────────────────────────────────────────────────

@bp.post("/lang/<code>")
def set_language(code: str):
    """Sprache umschalten. Steht der Betreiber auf einer Sprache, bleibt es dabei."""
    if code in ("de", "en") and db.languages() == "both":
        session["lang"] = code
    return redirect(request.referrer or "/")


@bp.get("/healthz")
def healthz():
    return jsonify({"ok": True}), 200


@bp.get("/branding/logo")
def branding_logo():
    """The installation's logo, open without login (login page, mails, galleries).

    Always a PNG made by the gallery itself (media.save_brand_logo), sent as
    image/png with nosniff. ``?variant=dark`` is the one for dark surfaces
    (falls back to the normal one). With ``?v=`` (the file's time, see
    branding.global_logo) it may be cached for a day; without, for 5 minutes.
    """
    key = "branding_logo_dark_path" if request.args.get("variant") == "dark" else "branding_logo_path"
    rel = (db.get_setting(key) or db.get_setting("branding_logo_path") or "").strip()
    if not rel:
        abort(404)
    full = (media.data_dir() / rel).resolve()
    if not str(full).startswith(str(media.data_dir().resolve())) or not full.exists():
        abort(404)
    resp = send_file(full, mimetype="image/png", conditional=True,
                     max_age=86400 if request.args.get("v") else 300)
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["Content-Security-Policy"] = "default-src 'none'; sandbox"
    return resp


def _datei_wartet(rel: str) -> bool:
    """Gehört dieser Pfad zu einer Aufnahme, die noch auf Freigabe wartet?"""
    try:
        con = db.get_db()
        try:
            zeile = con.execute(
                "SELECT 1 FROM media WHERE review_state='pending' AND "
                "(path_original=? OR path_webp=? OR path_thumb=?) LIMIT 1",
                (rel, rel, rel)).fetchone()
        finally:
            con.close()
    except Exception:                   # noqa: BLE001 — im Zweifel ausliefern wie bisher
        return False
    return bool(zeile)


def _pruefe_datei_gegen_aufnahme(event, rel: str) -> None:
    """Apply the recording's own switches to one of its files (guests only).

    * A hidden recording (the booth's full-size original of a web version,
      or one the operator hid) is not served at all; its download goes
      through the download route, which has its own checks.
    * A file that is only the original - the picture shown is the WebP next
      to it - follows the download switches: "single download" and
      "original download" must both be on. A video plays from its original
      file, and a picture without a web version is shown from it; those
      stay what the gallery displays.
    """
    con = db.get_db()
    try:
        row = con.execute(
            "SELECT kind, hidden, path_original, path_webp, path_thumb, path_poster FROM media "
            "WHERE event_id=? AND ? IN (path_original, path_webp, path_thumb, path_poster) "
            "ORDER BY hidden ASC LIMIT 1",
            (int(event["id"]), rel)).fetchone()
    finally:
        con.close()
    if not row:
        return
    if int(row["hidden"] or 0):
        abort(404)
    is_video = str(row["path_original"] or "").lower().endswith((".mp4", ".webm", ".mov"))
    shown = {row["path_webp"], row["path_thumb"], row["path_poster"]}
    if rel == row["path_original"] and rel not in shown and row["path_webp"] and not is_video:
        gs = _gs(event)
        if not (gs["download_single"] and gs["download_original"]):
            abort(403)


@bp.get("/data/<path:path>")
def serve_data_file(path: str):
    # Nur öffentliche Unterordner. Datenbank, Cache, Box-Assets und alles andere bleiben unerreichbar.
    root = media.data_dir()
    full = (root / path).resolve()
    rel = full.relative_to(root).as_posix() if full.is_relative_to(root) else ""
    public_prefixes = ("storage/", "assets/branding/", "assets/events/", "faq/")
    if not rel or not rel.startswith(public_prefixes) or full.suffix.lower() in (".sqlite", ".sqlite3", ".db", ".json", ".part"):
        abort(404)
    if not full.is_file():
        abort(404)
    if rel.startswith("storage/") and not _is_admin():
        slug = rel.split("/")[1]
        from app import folders
        event = folders.apply_parent(db.get_event_by_slug(slug))
        if not event:
            abort(404)
        # Silent mode: the gallery does not exist for guests, with password
        # or QR code alike (business logic page 4, gate 4; the QR page
        # answers 404 too). Until 11.11.2026 /data did not ask (S8).
        if _silent(event):
            abort(404)
        _pruefe_datei_gegen_aufnahme(event, rel)
        name = full.name
        via_qr = any(name.startswith(f"{u}_") for u in (session.get("p_uids") or []))
        if not via_qr:
            if not event.get("published"):
                abort(404)
            if not _check_password_access(event):
                abort(403)
            if _gs(event)["watermark"] and "/thumb/" not in rel:
                abort(403)
            # Wartet diese Datei noch auf Freigabe? Nur nachschlagen, wenn das
            # Event überhaupt moderiert — sonst wäre es eine Abfrage je Bild
            # für einen Fall, den es nicht gibt. Der Gast mit dem QR-Code
            # seines eigenen Durchgangs ist oben schon durch (``via_qr``).
            if _gs(event)["moderation"] and _datei_wartet(rel):
                abort(404)
            # Vor dem geplanten Start gibt es keine Dateien. Ausnahme: der Gast,
            # der eben den QR-Code seines Durchgangs gescannt hat (``via_qr``) —
            # seine eigenen Aufnahmen darf die Box sofort ausliefern.
            if _not_yet_live(event):
                abort(403)
        if _expired_for_guest(event):
            abort(410)
    # Dateinamen in storage/ ändern sich bei neuem Inhalt → lange cachebar.
    max_age = 7 * 86400 if path.startswith("storage/") else 3600
    # The type comes from a fixed list, never from the platform's mimetypes
    # table: HTML, SVG and everything unknown are downloads (findings S2/S7).
    from app import uploads
    mimetype, as_download = uploads.serve_type(full)
    resp = send_file(full, conditional=True, max_age=max_age, mimetype=mimetype,
                     as_attachment=as_download, download_name=full.name)
    resp.headers["X-Content-Type-Options"] = "nosniff"
    # A picture behind a password, of an unpublished event or reached via
    # the QR code must not sit in a shared cache (proxy, CDN) for the next
    # visitor. The browser itself may keep it.
    if rel.startswith("storage/"):
        owner = db.get_event_by_slug(rel.split("/")[1])
        if not owner or not owner.get("published") or owner.get("password_hash"):
            resp.cache_control.public = False
            resp.cache_control.private = True
    return resp


# ─── Email Subscription Confirmation ───────────────────────

@bp.route("/notify/confirm/<token>")
def notify_confirm(token: str):
    """Double Opt-In Bestätigung."""
    success = db.confirm_subscription(token) is not None
    msg = "Deine E-Mail-Adresse wurde bestätigt! Du erhältst ab jetzt Benachrichtigungen bei neuen Fotos." if success else "Link ungültig oder bereits bestätigt."
    return render_template("public/notify_result.html", success=success, message=msg)


@bp.route("/notify/unsubscribe/<token>")
def notify_unsubscribe(token: str):
    """Von E-Mail-Benachrichtigungen abmelden."""
    db.unsubscribe(token)
    return render_template("public/notify_result.html", success=True, message="Du wurdest erfolgreich abgemeldet.")


# ─── Setup Wizard ─────────────────────────────────────────────

@bp.route("/setup", methods=["GET", "POST"])
def setup():
    """Der Assistent für den ersten Start — und nur für den.

    Er setzt Benutzername und Kennwort der Verwaltung, ohne nach einem
    Kennwort zu fragen. Das muss so sein, solange es noch keines gibt. Sobald
    die Einrichtung abgeschlossen ist, darf es diesen Weg nicht mehr geben:
    Sonst übernimmt jeder, der die Adresse kennt, mit einem einzigen Formular
    die ganze Instanz.

    Gefunden bei der Durchsicht am 19.09.2026 — der Schalter
    ``setup_completed`` wurde zwar gesetzt und für die Anzeige gelesen, aber
    nie geprüft.
    """
    import secrets as _secrets

    # Admin-only, always. init_db creates an admin password on the very first
    # start (ADMIN_PASSWORD or a random one written to the log), so nobody
    # needs this page without a login. Keying it on ``setup_completed`` left
    # it open for ever on every instance whose owner never ran the wizard.
    if not _is_admin():
        abort(404)

    if request.method == "POST":
        _pruefe_csrf()
        admin_username = request.form.get("admin_username", "").strip() or "admin"
        admin_password = request.form.get("admin_password", "").strip()
        base_url = request.form.get("base_url", "").strip().rstrip("/")
        language = request.form.get("language", "de")
        gallery_name = request.form.get("gallery_name", "").strip()
        
        if not admin_password:
            flash("Bitte gib ein Passwort ein.", "error")
            return redirect(url_for("public.setup"))
        from app import passwords
        ok, meldung = passwords.pruefe(admin_password, [admin_username, gallery_name, "photobox"])
        if not ok:
            flash(meldung, "error")
            return redirect(url_for("public.setup"))
        
        from werkzeug.security import generate_password_hash as _gph
        db.set_setting("admin_username", admin_username)
        from app import accounts
        accounts.set_password(admin_password)    # also ends every older session (S10)
        db.set_setting("app_base_url", base_url)
        db.set_setting("app_language", language)
        if gallery_name:
            db.set_setting("homepage_headline", gallery_name)
        db.set_setting("setup_completed", "1")
        
        flash("Einrichtung abgeschlossen! Du kannst dich jetzt anmelden.", "success")
        return redirect(url_for("admin.login"))
    
    completed = db.get_setting("setup_completed") == "1"
    form = {
        "admin_username": db.get_setting("admin_username") or "admin",
        "base_url": db.get_setting("app_base_url") or "",
        "language": db.get_setting("app_language") or "de",
        "gallery_name": db.get_setting("homepage_headline") or "",
    }
    return render_template("setup.html", form=form, completed=completed, csrf_token=_make_csrf())
