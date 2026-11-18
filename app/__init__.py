"""Photobox Gallery v2 – Flask App Factory."""
from __future__ import annotations

import logging
import os
from flask import Flask


#: What docker-compose.yml puts in when nothing is set — never used as a key.
SECRET_KEY_PLACEHOLDER = "change-me-in-production"


def _secret_key() -> str:
    """SECRET_KEY from the environment; if missing (or the placeholder), a
    random key generated once and kept in DATA_DIR/.secret_key.

    The old production code signed with the placeholder itself. Here the
    fallback is safe but must not be silent: the key then lives in the
    volume and has to survive with it (every backup contains it), so the
    log says so on every start. No refusal to start — an update must not
    lock out a running instance.

    Several Gunicorn workers start at the same moment on a fresh volume.
    The file is created exclusively (O_EXCL): exactly one worker writes it,
    the others read that worker's key. Writing it unconditionally, as until
    12.11.2026, could give each worker its own key, and a session or share
    link signed by one would be refused by the other.
    """
    import secrets as _sec
    import time as _time
    from pathlib import Path as _P

    log = logging.getLogger("photobox")
    env = os.environ.get("SECRET_KEY", "")
    if env and env != SECRET_KEY_PLACEHOLDER:
        if len(env) < 16:
            log.warning("SECRET_KEY is only %d characters long; sessions and share links are signed "
                        "with it. Use at least 32 random characters.", len(env))
        return env
    d = _P(os.environ.get("DATA_DIR", "./data")).resolve()
    d.mkdir(parents=True, exist_ok=True)
    f = d / ".secret_key"
    if not f.exists():
        candidate = _sec.token_urlsafe(48)
        try:
            fd = os.open(f, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            pass                                    # another worker was first: use its key
        else:
            with os.fdopen(fd, "w") as fh:
                fh.write(candidate)
                fh.flush()
                os.fsync(fh.fileno())
    log.warning("SECRET_KEY is %s: sessions and share links are signed with the generated key in %s. "
                "Keep that file (it is part of every backup) or set SECRET_KEY to a long random value.",
                "the placeholder" if env else "not set", f)
    # The winner may still be writing: wait briefly for a non-empty file.
    for _ in range(100):
        key = f.read_text().strip()
        if key:
            return key
        _time.sleep(0.02)
    raise RuntimeError(f"{f} stays empty; delete it and start again")


def create_app() -> Flask:
    app = Flask(__name__, template_folder="templates", static_folder="static")
    app.secret_key = _secret_key()
    app.config.update(SESSION_COOKIE_SAMESITE="Lax", SESSION_COOKIE_HTTPONLY=True,
                      SESSION_COOKIE_SECURE=os.environ.get("BASE_URL", "").startswith("https://"))
    # Without a cap Werkzeug spools any body to disk. The value and its
    # reasoning live in app/uploads.py (PHOTOBOX_MAX_UPLOAD_MB).
    from app import uploads as _uploads
    app.config["MAX_CONTENT_LENGTH"] = _uploads.max_request_bytes()
    try:
        from werkzeug.middleware.proxy_fix import ProxyFix
        # Opt-in since 15.11.2026 (S-5). Behind Coolify/Traefik set
        # TRUST_PROXY=1 to see the real client address. On a container that
        # is reachable directly, trusting X-Forwarded-For lets anybody choose
        # the address the login brakes count — so off is the safe default.
        if os.environ.get("TRUST_PROXY", "0").strip() == "1":
            app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)
    except ImportError:
        pass

    # Register blueprints
    from app.blueprints.public import bp as public_bp
    from app.blueprints.admin import bp as admin_bp
    from app.blueprints.api import bp as api_bp

    app.register_blueprint(public_bp)
    # Link-preview image without the public blueprint's session handling.
    from app.blueprints.link_preview import bp as link_preview_bp
    app.register_blueprint(link_preview_bp)

    def _link_meta(event, brand_only: bool = False):
        """Open Graph data for a guest page (app/link_preview.py); None on any error."""
        from flask import request as _rq
        from app import db as _db, link_preview as _lp
        try:
            base = _db.base_url_for(event) or _rq.url_root.rstrip("/")
            return _lp.meta(event, base, force_brand=brand_only)
        except Exception:               # noqa: BLE001 - a preview never breaks the page
            app.logger.exception("link preview failed")
            return None
    app.jinja_env.globals["link_meta"] = _link_meta
    app.register_blueprint(admin_bp, url_prefix="/admin")
    app.register_blueprint(api_bp, url_prefix="/api")
    from app.mcp import bp as mcp_bp
    app.register_blueprint(mcp_bp)          # Assistenten-Zugang (MCP) zur Verwaltung, eigener Schlüssel
    from app.oauth import bp as oauth_bp
    app.register_blueprint(oauth_bp)        # OAuth for the same door (Claude custom connector)
    from app.blueprints.booth_v2 import bp as booth_v2_bp
    from app.blueprints.admin_booth import bp as admin_booth_bp
    app.register_blueprint(booth_v2_bp)
    from app.blueprints.sharing import bp as sharing_bp
    app.register_blueprint(sharing_bp)
    from app.blueprints.gallery_tools import bp as gallery_tools_bp
    app.register_blueprint(gallery_tools_bp)  # package D: upload PIN, table sign, trash, sorting out
    from app.blueprints.status_page import bp as status_page_bp
    app.register_blueprint(status_page_bp)  # read-only status link of a booth, for rentals
    app.register_blueprint(admin_booth_bp, url_prefix="/admin")
    from app.blueprints.clients import bp as clients_bp, panel as _client_panel
    app.register_blueprint(clients_bp)      # client access: one code, all galleries of a client
    app.jinja_env.globals["client_panel"] = _client_panel
    from app.blueprints.clients import comment_panel as _comment_panel
    app.jinja_env.globals["comment_panel"] = _comment_panel
    from app.blueprints.folders import bp as folders_bp, panel as _folder_panel
    app.register_blueprint(folders_bp)      # sub-galleries, one level deep
    app.jinja_env.globals["folder_panel"] = _folder_panel
    from app.blueprints.layout import bp as layout_bp, panel as _layout_panel
    app.register_blueprint(layout_bp)       # customers design their print layout, the owner approves
    app.jinja_env.globals["layout_panel"] = _layout_panel
    # Old addresses after a move (/<account>/<id>). Registered last; its
    # converter matches only the configured account name (app/legacy.py).
    from app import legacy as _legacy
    app.url_map.converters["account"] = _legacy.AccountConverter
    app.url_map.converters["foreign_id"] = _legacy.ForeignIdConverter
    from app.blueprints.legacy import bp as legacy_bp
    app.register_blueprint(legacy_bp)
    app.jinja_env.globals["legacy_account"] = _legacy.account
    from app import retention as _retention
    app.jinja_env.globals["retention_info"] = _retention.info
    from app import redirects as _redirects
    # "leitet weiter auf <host>" in the event list and on the event page.
    app.jinja_env.globals["redirect_host"] = _redirects.host

    def _current_client():
        from app import clients
        try:
            return clients.current()
        except Exception:               # noqa: BLE001 — a broken client row must not break a page
            return None
    app.jinja_env.globals["current_client"] = _current_client

    @app.context_processor
    def _version_context():
        """Fassung und Commit für die Verwaltung — eine Zeile, die Rückfragen erspart."""
        from app import about
        return {"app_version": about.version(), "app_commit": about.commit()}

    # Schema einmal pro Prozess prüfen/migrieren — nicht bei jeder Anfrage.
    state = {"ready": False}

    @app.before_request
    def _init_db():
        if state["ready"]:
            return
        from app import db, ecosystem
        db.init_db()
        ecosystem.init_schema()
        state["ready"] = True
        # Der Eingang für fremde Aufnahme-Programme, falls eingeschaltet.
        # Er bindet seinen Port einmal; laufen mehrere Arbeiter, bekommt ihn
        # der erste. Siehe app/eingang_dienste.py.
        try:
            from app import eingang_dienste
            eingang_dienste.starte(app)
        except Exception:               # noqa: BLE001 — ein Eingang kippt keine Galerie
            app.logger.exception("Eingang konnte nicht starten")

    # The daily maintenance run (retention, NAS offload, remote deletions)
    # without an external cron job: production never had one (gap L3).
    # Checks at most every ten minutes per worker; one worker wins the day.
    @app.before_request
    def _tageslauf():
        try:
            from app import maintenance
            maintenance.on_request(app)
        except Exception:               # noqa: BLE001 — maintenance never breaks a request
            app.logger.exception("Wartungslauf konnte nicht starten")
        # The silence alarm's check thread, once per worker (app/silence_alarm.py).
        try:
            from app import silence_alarm
            silence_alarm.on_request(app)
        except Exception:               # noqa: BLE001
            app.logger.exception("Funkstille-Prüfung konnte nicht starten")

    # An admin session belongs to the password it was opened with. After a
    # change (settings, recovery, reset link, setup) every other session is
    # logged out here, before any route looks at ``admin_logged_in`` (S10).
    @app.before_request
    def _admin_session_still_valid():
        from flask import session
        if not session.get("admin_logged_in") and not session.get("pending_admin"):
            return
        from app import accounts
        # S-9: a login since 15.11.2026 carries a server-side id; a cookie
        # whose id was revoked (logout) is no login, whichever copy it is.
        # Cookies from before carry none and still end with the epoch.
        sid = session.get("admin_sid")
        if ((session.get("admin_epoch") or "") != accounts.session_epoch()
                or (sid and not accounts.admin_session_alive(sid))):
            for key in ("admin_logged_in", "admin_epoch", "pending_admin", "admin_sid"):
                session.pop(key, None)

    # Änderungsprotokoll: ein Haken vor und einer nach jeder ändernden Anfrage
    # an die Verwaltung. Welche Route das war, muss hier niemand wissen — neue
    # Formulare sind damit vom ersten Tag an erfasst. Siehe app/protokoll.py.
    @app.before_request
    def _protokoll_vorher():
        from app import protokoll
        protokoll.vorher_merken()

    @app.after_request
    def _protokoll_nachher(response):
        from app import protokoll
        return protokoll.nachher_festhalten(response)

    from app import security_headers
    security_headers.install(app)

    # gzip for text answers; runs after every other hook (see app/compression.py).
    from app import compression
    compression.install(app)

    # Wer die Galerie unter der Domain seines Kunden betreibt, landet beim
    # Aufruf der nackten Adresse sonst auf der Startseite des Dienstleisters.
    # Gehört die Domain zu einem Event, führt „/" direkt dorthin.
    @app.before_request
    def _eigene_domain():
        from flask import redirect, request
        if request.path != "/" or request.method != "GET":
            return None
        try:
            from app import db, domains
            event_id = domains.event_zu_host(request.host)
            if not event_id:
                return None
            event = db.get_event_by_id(event_id)
        except Exception:                 # noqa: BLE001 — eine kaputte Zuordnung darf nichts sperren
            return None
        return redirect(f"/gallery/{event['slug']}", 302) if event else None

    # Register i18n helper as Jinja2 global
    from app.i18n import t
    app.jinja_env.globals["t"] = t

    # Branding: als Jinja-Global, damit jede Gastseite es selbst holen kann —
    # auch die, deren Route nichts davon weiß (z. B. die Durchgangs-Seite).
    from app import branding as _branding
    app.jinja_env.globals["brand_for"] = _branding.event_branding
    app.jinja_env.globals["brand_css_vars"] = _branding.css_vars
    app.jinja_env.globals["global_logo"] = _branding.global_logo
    # The size a guest download takes without a choice (run page, "Meine Aufnahmen").
    from app import media as _media
    app.jinja_env.globals["best_download_size"] = _media.best_download_size
    # ... and the size a share button fetches (capped at 2048 px for iOS).
    app.jinja_env.globals["share_download_size"] = _media.share_download_size

    # Die Bitte um eine Bewertung: als Jinja-Global, damit jede Gastseite sie
    # selbst holen kann — ohne dass jede Route davon wissen muss.
    def _bewertung_fuer(event):
        from app import bewertung, gallery_settings
        try:
            gs = gallery_settings.for_event(event)
        except Exception:               # noqa: BLE001 — eine kaputte Einstellung blendet die Bitte aus
            gs = {}
        return bewertung.fuer_event(event, gs)
    app.jinja_env.globals["bewertung_fuer"] = _bewertung_fuer

    # Campaign parameters for links that stay inside our own pages
    # (session page -> gallery), so the click is measurable as well.
    def _utm_query(event) -> str:
        from app import ecosystem
        return ecosystem.utm_query(event)
    app.jinja_env.globals["utm_query"] = _utm_query

    # Wartende Freigaben im Admin-Menü — eine Zählabfrage, kein Vorladen.
    def _pending_review_total() -> int:
        from app import ecosystem
        return ecosystem.pending_review_count()
    app.jinja_env.globals["pending_review_total"] = _pending_review_total

    @app.errorhandler(404)
    def _not_found(error):
        # A mistyped link is the first thing some guests see of a gallery, and
        # it used to be the web server's bare "Not Found". People get a page in
        # the site's own layout; programs (the API, the booth, static files)
        # keep the plain answer they already handle.
        from flask import render_template, request
        if request.method != "GET" or request.path.startswith(("/api/", "/static/")):
            return error
        try:
            # No blueprint ran for an unknown address, so nothing set the
            # visitor's language: the page was German for everyone.
            from flask import g
            if not getattr(g, "lang", None):
                from app.blueprints.public import _inject_globals
                _inject_globals()
            return render_template("public/not_found.html"), 404
        except Exception:        # never let the error page be the error
            logging.getLogger(__name__).exception("404 page failed to render")
            return error

    return app
