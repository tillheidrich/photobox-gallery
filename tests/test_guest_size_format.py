"""Guard: sizes a guest reads are formatted by one rule (18.11.2026).

The size sheet said "ca. 61.42 KB" (binary units, two decimals, English
point). ``media.guest_bytes`` and the page's ``pbBytes`` give "61 KB",
"1,4 MB" in German and "1.4 MB" in English; no guest template or guest
script formats a size another way.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from app import media

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("n,de,en", [
    (0, "0 KB", "0 KB"), (300, "1 KB", "1 KB"), (61_420, "61 KB", "61 KB"), (999_400, "999 KB", "999 KB"),
    (1_000_000, "1 MB", "1 MB"), (1_420_000, "1,4 MB", "1.4 MB"), (12_600_000, "13 MB", "13 MB"),
    (2_340_000_000, "2,3 GB", "2.3 GB"),
])
def test_guest_bytes(n, de, en):
    assert media.guest_bytes(n, "de") == de
    assert media.guest_bytes(n, "en") == en


def test_the_size_sheet_uses_the_german_comma(client):
    import json
    import secrets

    from app import db, gallery_settings as gsm
    from tests.test_download_sizes import _event_with_photo
    client.get("/healthz")
    slug, eid, _ = _event_with_photo(client.application, w=6000, h=4000)
    html = client.get(f"/gallery/{slug}").get_data(as_text=True)
    ests = re.findall(r'class="dl-opt-est"[^>]*>([^<]*)<', html)
    assert ests and any("KB" in e or "MB" in e for e in ests), ests
    for e in ests:
        assert not re.search(r"\d\.\d", e), e                 # no decimal point in German
        assert not re.search(r"\d[.,]\d\d\b", e), e           # never two decimals


def test_no_guest_page_formats_a_size_on_its_own():
    """Python: guest routes use guest_bytes, never human_bytes. Templates and
    guest scripts: no toFixed/Intl.NumberFormat next to KB/MB except pbBytes."""
    public = (ROOT / "app" / "blueprints" / "public.py").read_text(encoding="utf-8")
    assert "human_bytes" not in public
    assert '"size_h": guest_bytes' in (ROOT / "app" / "media.py").read_text(encoding="utf-8")
    for f in list((ROOT / "app" / "templates" / "public").glob("*.html")) + list((ROOT / "app" / "static" / "js").glob("*.js")):
        if "admin" in f.name or "vorlagen" in f.name:
            continue
        src = f.read_text(encoding="utf-8")
        src = re.sub(r"function pbBytes\(n\) \{.*?\n\}", "", src, flags=re.S)
        for line in src.splitlines():
            if re.search(r"\b[KMG]B\b", line) and re.search(r"toFixed|NumberFormat|toLocaleString|/ ?1e[369]|/ ?1024", line):
                pytest.fail(f"{f.name}: {line.strip()}")
