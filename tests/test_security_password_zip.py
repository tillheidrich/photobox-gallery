"""Regression test for Bug #2/#4: password-protected ZIP must be gated."""
import secrets


def _make_pw_event(app):
    with app.app_context():
        from app import db
        db.init_db()
        db.set_setting("zip_download_enabled", "1")
        db.set_setting("zip_download_admin_only", "0")  # only the password gates it
        slug = "secret-" + secrets.token_hex(3)
        db.create_event(slug=slug, name="Secret", upload_token=secrets.token_hex(8),
                        password="geheim123")
        return slug


def test_zip_blocked_without_password_session(app, client):
    slug = _make_pw_event(app)
    r = client.get(f"/gallery/{slug}/download.zip")
    assert r.status_code == 403, f"expected 403, got {r.status_code}"


def test_gallery_shows_password_form(app, client):
    slug = _make_pw_event(app)
    r = client.get(f"/gallery/{slug}")
    assert r.status_code == 200
    body = r.get_data(as_text=True).lower()
    assert "passw" in body  # password form rendered, gallery not leaked
