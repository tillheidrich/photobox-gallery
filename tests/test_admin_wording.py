"""Plain words in the admin: no emojis, one name for the photo booth.

Review of 15.11.2026: buttons and headings carried emojis (computer, arrows,
window, inbox, paper clip, waving hand, flags), and the same thing was called
"Gerät", "Fotobox-PC" and "Booth" on neighbouring pages. Now: no pictographic
emoji in the gallery's templates or the booth's pages, and "Box"/"Boxen" in
the gallery admin's navigation, headings, buttons and messages. ("Gerät"
stays where it means a guest's phone, "Booth" where it is part of a product
name such as dslrBooth or the booth software's own status line.)
"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Pictographs and flags; typographic marks like ✓ ✕ ↳ ★ ♥ ⌫ are text.
EMOJI = re.compile("[\U0001F000-\U0001FAFF\U0001F1E6-\U0001F1FF⬆️]")


def test_no_emojis_in_the_pages():
    found = []
    for folder in (ROOT / "app" / "templates", ROOT / "booth" / "boothcore" / "web"):
        for f in folder.rglob("*"):
            if f.suffix not in (".html", ".js"):
                continue
            for n, line in enumerate(f.read_text(encoding="utf-8").splitlines(), 1):
                if EMOJI.search(line):
                    found.append(f"{f.relative_to(ROOT)}:{n}")
    assert not found, found


def test_the_admin_calls_it_a_box(client):
    client.get("/healthz")
    with client.session_transaction() as s:
        s["admin_logged_in"] = True
    from app import accounts
    with client.session_transaction() as s:
        s["admin_epoch"] = accounts.session_epoch()
    page = client.get("/admin/devices").get_data(as_text=True)
    assert "Boxen" in page and "Box-Status" in page
    nav = page.split('class="sidebar', 1)[-1]
    assert not re.search(r">\s*Geräte\s*<", nav) and "Booth-Status" not in nav
