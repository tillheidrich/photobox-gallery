#!/usr/bin/env python3
"""Set up a demo gallery and a demo booth for product screenshots.

    python tools/marketing/stage.py up --work /private/tmp/mk/run \
        [--booth-root <checkout>] [--gallery-port 9810] [--booth-port 9811]

Called by tools/marketing_shots.mjs; usable on its own to click through the
demo by hand. It

1. seeds a throwaway gallery (made-up events "Sommerfest am See",
   "Hochzeit Mara & Jonas", "Herbstmarkt am Lindenhof"; a booth; layouts
   from the template library ticked for the event),
2. starts that gallery and a real booth whose camera is the drawn scene in
   scene_camera.py (no person in any picture, nothing downloaded),
3. lets the booth take a set of runs through its own HTTP API (photos,
   collages in library and plain layouts, a GIF, a boomerang), waits until
   they are in the gallery,
4. writes <work>/state.json (URLs, ids, session cookies) and keeps both
   servers running until it is stopped (SIGTERM or Ctrl+C).

Never point --work at a real data folder: it is deleted first. Printing goes
to a hot folder inside --work, never to a printer.
"""
from __future__ import annotations

import argparse
import json
import os
import secrets
import shutil
import signal
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
PY = sys.executable
# A throwaway key per stage, shared with the processes it starts through the
# environment; the admin is signed in by cookie, its password is never used.
os.environ.setdefault("MK_STAGE_KEY", secrets.token_hex(24))
SECRET = os.environ["MK_STAGE_KEY"]
ADMIN_USER = "demo"
EVENT_DATE = "2027-07-17"
LIBRARY = ["gartenfest-hoch-4.json", "gartenfest-hoch-1.json", "gartenfest-quer-3.json",
           "salbei-hoch-4.json"]
BUILTINS = ["schlicht-1-weiss", "schlicht-2-schwarz", "streifen4", "grid4"]


def _json(url, body=None, method=None, timeout=60, headers=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method or ("POST" if data is not None else "GET"),
                                 headers={"Content-Type": "application/json", **(headers or {})})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read() or b"{}")


def _wait(url, seconds=60):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        try:
            urllib.request.urlopen(url, timeout=3).read()
            return
        except Exception:
            time.sleep(0.5)
    raise SystemExit(f"no answer from {url}")


# ── gallery seed, runs inside the gallery checkout with DATA_DIR set ──────────
def prep() -> None:
    import io
    sys.path.insert(0, str(ROOT))
    sys.path.insert(0, str(HERE))
    os.chdir(ROOT)
    from werkzeug.datastructures import FileStorage
    from app import create_app
    from scene_camera import draw_scene

    app = create_app()
    app.test_client().get("/healthz")
    out = {}
    with app.app_context():
        from app import accounts, db, ecosystem, media, vorlagen_bibliothek
        from app import gallery_settings as gsm
        from app.blueprints.admin_booth import _assets_dir
        db.init_db(); ecosystem.init_schema()
        db.set_setting("admin_username", ADMIN_USER)
        db.set_setting("setup_completed", "1")
        db.set_setting("homepage_headline", "Fotobox Seeblick")
        accounts.set_password(secrets.token_urlsafe(18))
        # Made-up operator details, so the dashboard does not warn about
        # unfinished legal texts in every screenshot.
        for key, value in {"legal_operator": "Fotobox Seeblick (Demo)", "legal_contact_email": "kontakt@example.org",
                           "legal_address": "Musterweg 1, 12345 Musterstadt", "legal_role": "controller",
                           "legal_hoster": "ein Rechenzentrum in Deutschland", "legal_phone": "0000 000000",
                           "legal_server_logs": "7 Tage", "legal_authority": "die zuständige Landesbehörde",
                           "legal_mail_provider": "kein Mailversand", "legal_external_storage": "keine",
                           "legal_principal": "-", "legal_register": "-", "legal_tax": "-",
                           "legal_editorial": "Fotobox Seeblick (Demo)", "legal_dispute": "nein",
                           "legal_assistant": "kein Assistent"}.items():
            db.set_setting(key, value)

        def ev(name, slug, date, gallery=None, **f):
            eid = db.create_event(name=name, slug=slug, upload_token="tok-" + slug[:20])
            f.update(published=1, date=date, gallery_json=json.dumps(gsm.clean(gallery or {})))
            db.update_event(eid, f)
            return eid

        e1 = ev("Sommerfest am See", "sommerfest-am-see", EVENT_DATE,
                {"guest_upload": True, "show_source_filter": True, "live_badge": True})
        e2 = ev("Hochzeit Mara & Jonas", "mara-und-jonas", "2027-08-21")
        e3 = ev("Herbstmarkt am Lindenhof", "herbstmarkt-lindenhof", "2026-10-03")
        db.set_event_featured(e1, True)
        for eid, n, base in ((e2, 9, 40), (e3, 6, 60)):
            for i in range(n):
                b = io.BytesIO()
                draw_scene(1800, 1200, base + i).save(b, "JPEG", quality=86)
                media.save_upload(eid, FileStorage(io.BytesIO(b.getvalue()), filename=f"DSC_{base + i:04d}.jpg"),
                                  source="admin")
        # Layouts for the event: four from the library, the rest built in.
        own = []
        for name in LIBRARY:
            try:
                own.append(vorlagen_bibliothek.uebernehmen(name, _assets_dir())[0])
            except Exception as exc:  # a missing one is a note, not a stop
                print("library", name, exc, file=sys.stderr)
        from app import branding
        row = db.get_event_by_id(e1)
        design = branding.parse_design(row.get("design_json")) if row else {}
        design["booth"] = {"collage_exact": True, "collage_templates": own, "collage_builtins": BUILTINS}
        db.update_event(e1, {"design_json": json.dumps(design)})
        dev = db.create_device(name="Box 1 Seeblick")
        ecosystem.save_device_profile(dev["id"], {
            "modes": ["photo", "collage", "gif", "boomerang"],
            "print": {"enabled": True, "auto": False, "copies_max": 2, "limit_session": 2},
            "countdown": {"first": 9, "next": 3},
            "gif": {"shots": 4},
        }, e1)
        ser = app.session_interface.get_signing_serializer(app)
        out = {"events": {"main": e1, "wedding": e2, "market": e3}, "device_id": dev["id"],
               "api_key": dev["api_key"], "templates": own,
               "admin_cookie": ser.dumps({"admin_logged_in": True, "admin_epoch": accounts.session_epoch(),
                                          "csrf_token": "demo"})}
    print("STAGE " + json.dumps(out))


def guest_cookie(uids) -> str:
    sys.path.insert(0, str(ROOT))
    os.chdir(ROOT)
    from app import create_app
    app = create_app()
    return app.session_interface.get_signing_serializer(app).dumps({"p_uids": list(uids)})


def _in_gallery(args, *argv: str) -> str:
    """Run this file once more, with the gallery's data folder and key."""
    env = dict(os.environ, DATA_DIR=str(args.work / "gallery"), SECRET_KEY=SECRET,
               TMPDIR=str(args.work / "tmp"))
    r = subprocess.run([PY, str(Path(__file__).resolve()), *argv], cwd=ROOT, env=env,
                       capture_output=True, text=True, timeout=300)
    if r.returncode:
        raise SystemExit(r.stderr[-2000:])
    return r.stdout


# ── the whole stage ─────────────────────────────────────────────────────────
def up(args) -> None:
    work: Path = args.work
    if not str(work).startswith(("/tmp/", "/private/tmp/")):
        raise SystemExit("refusing: --work must be under /tmp or /private/tmp")
    shutil.rmtree(work, ignore_errors=True)
    for sub in ("gallery", "booth/plugins", "hotfolder", "tmp"):
        (work / sub).mkdir(parents=True, exist_ok=True)
    base_env = dict(os.environ, TMPDIR=str(work / "tmp"))
    out = _in_gallery(args, "prep")
    seed = json.loads([ln for ln in out.splitlines() if ln.startswith("STAGE ")][-1][6:])

    gurl = f"http://127.0.0.1:{args.gallery_port}"
    burl = f"http://127.0.0.1:{args.booth_port}"
    procs = []

    def stop(*_):
        for p in procs:
            if p.poll() is None:
                p.terminate()
        for p in procs:
            try:
                p.wait(10)
            except Exception:
                p.kill()
        sys.exit(0)

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    log = open(work / "gallery.log", "w")
    procs.append(subprocess.Popen([PY, "-m", "flask", "run", "--host", "127.0.0.1", "--port", str(args.gallery_port)],
                                  cwd=ROOT, stdout=log, stderr=subprocess.STDOUT,
                                  env=dict(base_env, DATA_DIR=str(work / "gallery"), SECRET_KEY=SECRET,
                                           FLASK_APP="run.py", BASE_URL=gurl)))
    _wait(gurl + "/healthz")

    booth_root = Path(args.booth_root).resolve()
    shutil.copy(HERE / "scene_camera.py", work / "booth/plugins/scene_camera.py")
    local = {"booth_name": "Box 1 Seeblick", "camera": {"backend": "szene"},
             "printer": {"backend": "hotfolder", "folder": str(work / "hotfolder")},
             "server": {"url": gurl, "api_key": seed["api_key"], "verify_tls": False},
             "admin_pin": "468213", "port": args.booth_port, "kiosk": False}
    (work / "booth/local.json").write_text(json.dumps(local, indent=2), encoding="utf-8")
    blog = open(work / "booth.log", "w")
    benv = dict(base_env, BOOTH_DATA=str(work / "booth"),
                PYTHONPATH=os.pathsep.join([str(booth_root / "booth"), str(booth_root / "sync")]))
    benv.pop("BOOTH_CAMERA", None)
    procs.append(subprocess.Popen([PY, "-m", "boothcore", "--no-kiosk", "--port", str(args.booth_port)],
                                  cwd=booth_root / "booth", env=benv, stdout=blog, stderr=subprocess.STDOUT))
    _wait(burl + "/api/about")
    end = time.monotonic() + 60
    while time.monotonic() < end:
        st = _json(burl + "/api/state")
        if st.get("sync", {}).get("online") and (st.get("profile", {}).get("event") or {}).get("slug"):
            break
        time.sleep(1)
    else:
        raise SystemExit("booth did not reach the gallery")
    # The template files come after the profile, in their own download. Runs
    # started before they are there fall back to a built-in layout.
    wanted = {Path(t).stem for t in seed["templates"]}
    end = time.monotonic() + 120
    while True:
        state = _json(burl + "/api/state")
        tmpl_ids = [t.get("id") for t in (state.get("templates") or []) if isinstance(t, dict)]
        if wanted <= set(tmpl_ids) or time.monotonic() > end:
            break
        time.sleep(2)
    print("booth templates:", tmpl_ids, flush=True)

    def run(mode, template=None, fmt=None, look=None):
        body = {"mode": mode}
        if template:
            body["template"] = template
        if fmt:
            body["format"] = fmt
        s = _json(burl + "/api/session", body)
        uid = s["uid"]
        if mode == "boomerang":
            _json(f"{burl}/api/session/{uid}/clip", {}, timeout=120)
        else:
            for _ in range(max(1, int(s.get("shots_needed") or 1))):
                _json(f"{burl}/api/session/{uid}/shot", {}, timeout=60)
        item = _json(f"{burl}/api/session/{uid}/finish", {"look": look} if look else {}, timeout=180)
        return uid, item

    runs = []
    plan = [("collage", next((t for t in tmpl_ids if "gartenfest-hoch-4" in str(t)), None), None),
            ("photo", None, None), ("collage", "schlicht-2-schwarz", None), ("photo", None, None),
            ("collage", next((t for t in tmpl_ids if "salbei" in str(t)), None), None),
            ("photo", None, None), ("collage", "schlicht-1-weiss", None), ("gif", None, None),
            ("collage", next((t for t in tmpl_ids if "gartenfest-quer-3" in str(t)), None), None),
            ("photo", None, None), ("boomerang", None, None), ("collage", "streifen4", None),
            ("photo", None, None), ("collage", "grid4", None)]
    for mode, template, fmt in plan:
        try:
            uid, item = run(mode, template, fmt)
            runs.append({"uid": uid, "mode": mode, "template": template, "share_url": item.get("share_url"),
                         "files": {k: v for k, v in item.items() if isinstance(v, str)}})
            print("run", mode, template, uid, flush=True)
        except Exception as exc:
            print("run failed", mode, template, exc, flush=True)
    end = time.monotonic() + 240
    while time.monotonic() < end:
        sync = _json(burl + "/api/state").get("sync", {})
        if not sync.get("pending") and not sync.get("uploading") and sync.get("done"):
            break
        time.sleep(2)
    time.sleep(3)
    uids = [r["uid"] for r in runs]
    cookie = _in_gallery(args, "cookie", *uids[:4]).strip().splitlines()[-1]
    state = {"gallery": gurl, "booth": burl, "work": str(work), "booth_root": str(booth_root),
             "admin_cookie": seed["admin_cookie"], "guest_cookie": cookie, "events": seed["events"],
             "device_id": seed["device_id"], "templates": seed["templates"], "runs": runs,
             "slug": "sommerfest-am-see", "booth_pin": local["admin_pin"]}
    (work / "state.json").write_text(json.dumps(state, indent=1), encoding="utf-8")
    print("READY", work / "state.json", flush=True)
    while True:
        time.sleep(1)
        if any(p.poll() is not None for p in procs):
            print("a server stopped", flush=True)
            stop()


def main() -> None:
    if len(sys.argv) > 1 and sys.argv[1] == "prep":
        prep()
        return
    if len(sys.argv) > 1 and sys.argv[1] == "cookie":
        print(guest_cookie(sys.argv[2:]))
        return
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["up"])
    ap.add_argument("--work", type=Path, required=True)
    ap.add_argument("--booth-root", default=str(ROOT))
    ap.add_argument("--gallery-port", type=int, default=9810)
    ap.add_argument("--booth-port", type=int, default=9811)
    up(ap.parse_args())


if __name__ == "__main__":
    main()
