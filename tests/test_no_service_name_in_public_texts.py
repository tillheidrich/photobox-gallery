"""The second upload target is described, not branded.

User-visible texts, help, docs and the README say "Online-Galerie per
FTP/FTPS/SFTP" / "an FTP-fed proofing gallery". tools/check_hygiene.py stops
the name of a particular service from coming back; the only form it allows is
the quoted identifier existing booths store their target under.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import check_hygiene as hygiene  # noqa: E402

NAME = "Pic" + "drop"            # in two parts, like the guard itself


def _check(tmp_path, monkeypatch, name, text):
    monkeypatch.setattr(hygiene, "ROOT", tmp_path)
    path = tmp_path / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return hygiene.check_service_name([name])


def test_the_name_in_a_text_is_found(tmp_path, monkeypatch):
    assert _check(tmp_path, monkeypatch, "app/i18n/de.py", f"x = 'FTP für {NAME}'\n")
    assert _check(tmp_path, monkeypatch, "README.md", f"upload to {NAME.lower()}.com\n")


def test_the_quoted_legacy_identifier_is_allowed(tmp_path, monkeypatch):
    assert not _check(tmp_path, monkeypatch, "booth/boothcore/second_target.py",
                      f'NAMES = ("{NAME.lower()}", "zweites-ziel")\n')


def test_changelog_history_stays_but_new_entries_are_checked(tmp_path, monkeypatch):
    old = f"## 0.9.1 — 2026-11-17\n\n* second target ({NAME})\n"
    assert not _check(tmp_path, monkeypatch, "CHANGELOG.md", "## 0.9.2\n\n* fine\n\n" + old)
    assert _check(tmp_path, monkeypatch, "CHANGELOG.md", f"## 0.9.2\n\n* {NAME}\n\n" + old)


def test_the_repository_is_clean():
    assert hygiene.check_service_name(hygiene.tracked_files()) == []
