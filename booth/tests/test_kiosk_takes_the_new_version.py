"""After an update the kiosk shows the new version, not yesterday's CSS.

/static was served without Cache-Control; Chrome then guesses a lifetime from
Last-Modified and a kiosk kept old CSS/JS after an update. The page itself is
never cached, the files beside it are revalidated with their ETag on every
load (on this PC that is a 304 of a few bytes), and an asset that the gallery
replaced under the same name shows at once.
"""
import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client(monkeypatch):
    d = Path(tempfile.mkdtemp(prefix="booth-cache-"))
    monkeypatch.setenv("BOOTH_DATA", str(d))
    monkeypatch.setenv("BOOTH_CAMERA", "mock")
    from boothcore.app import Booth, create_app
    b = Booth(base=d, start_threads=False)
    b.assets_dir.mkdir(parents=True, exist_ok=True)
    (b.assets_dir / "logo.png").write_bytes(b"\x89PNG old")
    yield TestClient(create_app(b)), b
    b.mail.stop()


@pytest.mark.parametrize("page", ["/", "/admin", "/admin/vorlagen"])
def test_the_pages_are_never_taken_from_the_cache(client, page):
    c, _ = client
    assert "no-store" in c.get(page).headers.get("cache-control", "")


@pytest.mark.parametrize("path", ["/static/app.js", "/static/app.css", "/static/i18n.js"])
def test_static_files_are_revalidated_every_time(client, path):
    c, _ = client
    r = c.get(path)
    assert r.status_code == 200
    assert "no-cache" in r.headers.get("cache-control", "")
    etag = r.headers.get("etag")
    assert etag
    again = c.get(path, headers={"If-None-Match": etag})
    assert again.status_code == 304


def test_an_asset_replaced_under_the_same_name_is_not_held(client):
    c, b = client
    first = c.get("/assets/logo.png")
    assert "no-cache" in first.headers.get("cache-control", "")
    (b.assets_dir / "logo.png").write_bytes(b"\x89PNG new and longer")
    second = c.get("/assets/logo.png", headers={"If-None-Match": first.headers["etag"]})
    assert second.status_code == 200 and second.content.endswith(b"new and longer")
