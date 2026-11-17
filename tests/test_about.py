"""Die Über-Seite: Fassung und Projektangaben, ohne Systemdetails für Gäste."""


def test_about_page_shows_version_and_links(client):
    client.get("/healthz")
    r = client.get("/ueber")
    assert r.status_code == 200
    body = r.data.decode()
    from app import about
    assert about.version() in body
    assert "photobox-gallery.com" in body and "AGPL-3.0" in body


def test_about_hides_system_details_from_guests(client):
    client.get("/healthz")
    body = client.get("/ueber").data.decode()
    assert "Python" not in body
    with client.session_transaction() as s:
        s["admin_logged_in"] = True
    body = client.get("/about").data.decode()
    assert "Python" in body


def test_version_is_one_number_for_the_whole_ecosystem():
    from pathlib import Path
    from app import about
    root = Path(__file__).resolve().parent.parent
    assert (root / "VERSION").read_text().strip() == about.version()
    booth = (root / "booth" / "boothcore" / "__init__.py").read_text()
    assert f'__version__ = "{about.version()}"' in booth, "Box trägt eine andere Fassung als die Galerie"


def test_footer_points_at_the_about_page(client):
    client.get("/healthz")
    body = client.get("/").data.decode()
    assert 'href="/ueber"' in body


# ── The source offer (AGPL section 13) ───────────────────────────────────
# Whoever runs a modified gallery must offer its users the source. A fork
# does that by setting one variable, not by editing app/about.py.

def test_without_the_variable_the_source_link_stays_where_it_was(monkeypatch):
    from app import about
    monkeypatch.delenv("PHOTOBOX_SOURCE_URL", raising=False)
    assert about.repo_url() == "https://github.com/tillheidrich/photobox-gallery"


def test_a_fork_points_the_source_link_at_its_own_repository(client, monkeypatch):
    from app import about
    monkeypatch.setenv("PHOTOBOX_SOURCE_URL", "https://git.example.org/fork/photobox")
    assert about.repo_url() == "https://git.example.org/fork/photobox"
    client.get("/healthz")
    body = client.get("/ueber").data.decode()
    assert 'href="https://git.example.org/fork/photobox"' in body


def test_a_source_address_that_is_not_a_web_address_is_ignored(monkeypatch):
    from app import about
    monkeypatch.setenv("PHOTOBOX_SOURCE_URL", "javascript:alert(1)")
    assert about.repo_url() == about.DEFAULT_REPO_URL


def test_the_about_page_offers_the_source_of_the_running_version(client):
    client.get("/healthz")
    body = client.get("/ueber").data.decode()
    assert 'class="about-source-offer"' in body
    offer = body.split('class="about-source-offer"', 1)[1].split("</p>", 1)[0]
    assert "AGPL" in offer


def test_the_commit_comes_from_the_build_file_without_git(tmp_path, monkeypatch):
    """A container without .git and without SOURCE_COMMIT still names its build."""
    from app import about
    for key in about.COMMIT_ENV:
        monkeypatch.delenv(key, raising=False)
    written = tmp_path / "BUILD_COMMIT"
    written.write_text("0123456789abcdef0123456789abcdef01234567\n", encoding="ascii")
    monkeypatch.setattr(about, "BUILD_COMMIT_FILE", written)

    def no_git(*args, **kwargs):
        raise FileNotFoundError("git")
    monkeypatch.setattr(about.subprocess, "run", no_git)
    about.commit.cache_clear()
    try:
        assert about.commit() == "0123456"
        monkeypatch.setattr(about, "BUILD_COMMIT_FILE", tmp_path / "missing")
        about.commit.cache_clear()
        assert about.commit() == "", "no file, no git: no commit, and no error"
    finally:
        about.commit.cache_clear()
