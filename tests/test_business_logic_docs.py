"""The business rules read out of the code live next to the code.

An agent that changes the booth/gallery boundary needs the rule it is about to
touch ("a deleted id is never accepted again"), not only the function that
implements it. The wiki holds the long form; these files are the copy that
travels with every clone and every branch. This test keeps the set complete and
keeps AGENTS.md pointing at it, so the folder cannot quietly rot into a stub.
"""
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
BL = ROOT / "business_logic"

PAGES = [
    "current/workflows/gast-durchgang-an-der-box.md",
    "current/workflows/abgleich-box-galerie.md",
    "current/endpoints/qr-seite-des-durchgangs.md",
    "current/models/galerie-sichtbarkeit-und-zugang.md",
    "current/billing/druckkontingente-und-lizenz.md",
]
# Every gap of the audit of 2026-11-11. The register has to name each of them,
# open or closed: a gap that drops out of the list is a gap nobody decides on.
GAPS = ["A1", "A2", "A3", "A4", "A5", "B1", "B2", "B3", "B4",
        "C1", "C2", "C3", "C4", "D3", "L1", "L2", "L3", "L4"]


@pytest.mark.parametrize("name", ["index.md", "glossary.md", "DISCOVERY.md"] + PAGES)
def test_the_business_logic_file_is_in_the_repository(name):
    path = BL / name
    assert path.is_file(), f"business_logic/{name} is missing"
    assert len(path.read_text(encoding="utf-8")) > 400, f"business_logic/{name} is a stub"


def test_the_index_links_every_page():
    index = (BL / "index.md").read_text(encoding="utf-8")
    for name in PAGES + ["glossary.md", "DISCOVERY.md"]:
        assert name in index, f"index.md does not link {name}"


@pytest.mark.parametrize("gap", GAPS)
def test_the_gap_register_names_every_gap_of_the_audit(gap):
    index = (BL / "index.md").read_text(encoding="utf-8")
    assert f"| {gap} " in index, f"gap {gap} has no row in the register"


def test_every_page_says_which_commit_it_was_read_from():
    for name in PAGES:
        head = (BL / name).read_text(encoding="utf-8")[:600]
        assert "1fd8505" in head, f"{name} does not name the commit it describes"


def test_agents_md_points_at_the_business_logic():
    agents = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
    assert "business_logic/index.md" in agents
