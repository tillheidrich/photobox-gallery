"""Minimal smoke tests: app boots, DB initialises, core routes respond."""


def test_app_factory_imports(app):
    assert app is not None


def test_db_creates_all_tables(client):
    # A request triggers before_request -> db.init_db()
    client.get("/healthz")
    from app import db
    import sqlite3
    conn = sqlite3.connect(str(db.db_path()))
    try:
        names = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()}
    finally:
        conn.close()
    expected = {"events", "media", "devices", "settings",
                "stats", "likes", "notify_subscriptions", "slug_aliases"}
    missing = expected - names
    assert not missing, f"missing tables: {missing}"


def test_healthz(client):
    r = client.get("/healthz")
    assert r.status_code == 200


def test_public_routes_respond(client):
    for path in ("/", "/admin/login"):
        r = client.get(path)
        assert r.status_code in (200, 302), f"{path} -> {r.status_code}"


def test_api_status(client):
    r = client.get("/api/status")
    assert r.status_code in (200, 401, 404)
