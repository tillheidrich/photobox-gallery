"""The booth says where its source is, and which build it is.

The booth is rented out with the program on it. Whoever has it in hand gets
the right to the source of exactly that build (AGPL-3.0), so the admin card
names an address and, when the build wrote it down, the commit.
"""
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from boothcore import source_offer
from boothcore.app import create_app
from _pages import page as _page_with_scripts  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture()
def booth(monkeypatch, tmp_path):
    monkeypatch.setenv("BOOTH_DATA", str(tmp_path))
    monkeypatch.setenv("BOOTH_CAMERA", "mock")
    from boothcore.app import Booth
    b = Booth(base=tmp_path, start_threads=False)
    yield b
    b.mail.stop()


def test_the_about_endpoint_names_the_source_and_the_build(booth, monkeypatch):
    monkeypatch.delenv(source_offer.SOURCE_URL_ENV, raising=False)
    monkeypatch.setenv(source_offer.COMMIT_ENV, "a9968eea65de1702")
    data = TestClient(create_app(booth)).get("/api/about").json()
    assert data["repo_url"] == "https://github.com/tillheidrich/photobox-gallery"
    assert data["commit"] == "a9968ee"
    assert data["license"] == "AGPL-3.0-or-later"


def test_a_fork_points_the_booth_at_its_own_source(booth, monkeypatch):
    monkeypatch.setenv(source_offer.SOURCE_URL_ENV, "https://git.example.org/fork/photobox")
    data = TestClient(create_app(booth)).get("/api/about").json()
    assert data["repo_url"] == "https://git.example.org/fork/photobox"


def test_an_address_that_is_not_a_web_address_is_ignored(monkeypatch):
    monkeypatch.setenv(source_offer.SOURCE_URL_ENV, "javascript:alert(1)")
    assert source_offer.source_url() == source_offer.DEFAULT_SOURCE_URL


def test_the_commit_comes_from_the_file_the_build_wrote(tmp_path, monkeypatch):
    monkeypatch.delenv(source_offer.COMMIT_ENV, raising=False)
    written = tmp_path / "BUILD_COMMIT"
    written.write_text("0123456789abcdef0123456789abcdef01234567\n", encoding="ascii")
    assert source_offer.commit(written) == "0123456"
    assert source_offer.commit(written, short=False).endswith("01234567")


def test_without_a_build_file_there_is_no_commit_and_no_error(tmp_path, monkeypatch):
    monkeypatch.delenv(source_offer.COMMIT_ENV, raising=False)
    assert source_offer.commit(tmp_path / "missing") == ""
    damaged = tmp_path / "BUILD_COMMIT"
    damaged.write_text("<script>", encoding="ascii")
    assert source_offer.commit(damaged) == ""


def test_the_admin_card_tells_the_renter_about_the_source():
    page = _page_with_scripts(ROOT / "booth" / "boothcore" / "web" / "admin.html")
    assert 'id="sourceOffer"' in page
    hint = page.split('id="sourceOffer"', 1)[1].split("</p>", 1)[0]
    assert "AGPL" in hint and "Quelltext" in hint and "Canon" in hint
    assert "a.commit" in page, "the card shows the commit next to the version"


def test_both_build_plans_write_the_commit_into_the_package():
    for spec in ("packaging/windows/photobox-booth.spec", "packaging/macos/photobox-booth.spec"):
        code = "\n".join(line for line in (ROOT / spec).read_text(encoding="utf-8").splitlines()
                         if not line.lstrip().startswith("#"))
        assert "BUILD_COMMIT" in code and '"boothcore"' in code, spec
