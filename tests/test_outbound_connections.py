"""The privacy texts cannot drift from the code.

tools/outbound_connections.py lists every network connection the booth can
make and the setting that switches it on. The installer's privacy page and
docs/DATENSCHUTZ-BOX.md / docs/PRIVACY-BOOTH.md are generated from that list.
These tests tie the list to the code: a new file that opens a connection,
a switch that no longer exists or is on by default, or a document whose table
was edited by hand fails here.
"""
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "booth"))

import outbound_connections as oc  # noqa: E402

# Anything that opens a socket or speaks a network protocol.
NETWORK = re.compile(
    r"httpx\.(get|post|put|delete|stream|request|Client|AsyncClient)\b|urllib\.request|urlopen\("
    r"|smtplib|paramiko|ftplib|socket\.create_connection|socket\.socket\(|websocket|aiohttp|requests\.")
SCANNED = ("booth/boothcore", "sync/photobox_sync")


def _source_files():
    for base in SCANNED:
        for path in sorted((ROOT / base).rglob("*.py")):
            rel = path.relative_to(ROOT).as_posix()
            if "/tests/" in rel:
                continue
            yield rel, path.read_text(encoding="utf-8", errors="replace")


def test_every_file_that_opens_a_connection_is_listed():
    listed = {f for c in oc.CONNECTIONS for f in c.files}
    unlisted = [rel for rel, text in _source_files() if NETWORK.search(text) and rel not in listed]
    assert not unlisted, ("These files open network connections but are not in "
                          f"tools/outbound_connections.py: {unlisted}")


def test_every_listed_file_exists_and_still_talks_to_the_network():
    for c in oc.CONNECTIONS:
        for rel in c.files:
            path = ROOT / rel
            assert path.is_file(), f"{c.id}: {rel}"
            assert NETWORK.search(path.read_text(encoding="utf-8")), f"{c.id}: {rel} opens nothing any more"


def _default(path: str):
    from boothcore import config
    kind, _, key = path.partition(":")
    tree = {"local": config.DEFAULT_LOCAL, "profile": config.DEFAULT_PROFILE}[kind]
    key, _, wanted = key.partition("=")
    node = tree
    for part in key.split("."):
        assert isinstance(node, dict) and part in node, f"{path}: no such setting"
        node = node[part]
    return node, wanted


@pytest.mark.parametrize("conn", [c for c in oc.CONNECTIONS if c.switch.split(":")[0] in ("local", "profile")],
                         ids=lambda c: c.id)
def test_every_switch_exists_and_is_off_by_default(conn):
    value, wanted = _default(conn.switch)
    if wanted:
        assert value != wanted, f"{conn.id}: on by default ({value})"
    else:
        assert not value, f"{conn.id}: {conn.switch} is on by default ({value!r})"
    assert conn.off_by_default


def test_the_booth_listens_on_this_pc_only_by_default():
    main = (ROOT / "booth" / "boothcore" / "__main__.py").read_text(encoding="utf-8")
    assert 'ap.add_argument("--host", default="127.0.0.1")' in main
    from boothcore import config
    assert config.DEFAULT_LOCAL["guest_share"]["allowed"] is False
    assert config.DEFAULT_LOCAL["lan_guest_access"] is False
    assert config.DEFAULT_LOCAL["port"] == 8765


@pytest.mark.parametrize("rel, lang", sorted(oc.DOCS.items()))
def test_the_privacy_documents_carry_the_generated_table(rel, lang):
    text = (ROOT / rel).read_text(encoding="utf-8")
    assert oc.START in text and oc.END in text
    assert oc.with_table(text, lang) == text, (
        f"{rel}: table differs from tools/outbound_connections.py - run "
        "python tools/outbound_connections.py --update-docs")


def test_the_installer_page_names_every_connection_but_the_internal_one():
    for lang in ("de", "en"):
        text = oc.installer_text(lang)
        for c in oc.CONNECTIONS:
            assert (f"[{c.id};" in text) == (c.id != "local_only"), (lang, c.id)


def test_no_page_of_the_booth_loads_anything_from_the_internet():
    web = ROOT / "booth" / "boothcore" / "web"
    offenders = []
    for path in list(web.glob("*.html")) + list((web / "static").rglob("*.js")) + list((web / "static").rglob("*.css")):
        text = path.read_text(encoding="utf-8", errors="replace")
        for m in re.finditer(r'(?:src|href)\s*=\s*["\'](https?://[^"\']+)|url\(\s*["\']?(https?://[^)"\']+)'
                             r'|@import\s+["\'](https?://[^"\']+)|fetch\(\s*["\'](https?://[^"\']+)', text):
            offenders.append(f"{path.relative_to(ROOT)}: {next(g for g in m.groups() if g)}")
    assert not offenders, offenders
