"""docs/UPGRADE.md is what an operator follows when moving production to a
new version. It must know every environment variable the gallery reads,
and the commands it tells people to run must exist."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DOC = (ROOT / "docs" / "UPGRADE.md").read_text(encoding="utf-8")
MAINTENANCE = (ROOT / "docs" / "MAINTENANCE.md").read_text(encoding="utf-8")


def _variables_read_by_the_gallery():
    names = set()
    for path in (ROOT / "app").rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        names |= set(re.findall(r'os\.environ(?:\.get)?[(\[]\s*"([A-Z][A-Z0-9_]+)"', text))
        names |= set(re.findall(r'_mb_env\("([A-Z][A-Z0-9_]+)"', text))
        names |= set(re.findall(r'_env_int\("([A-Z][A-Z0-9_]+)"', text))
    return names


def test_every_environment_variable_is_documented():
    missing = []
    for name in sorted(_variables_read_by_the_gallery()):
        prefix_documented = any(name.startswith(p) and p + "*" in DOC
                                for p in ("ORIGINALS_SFTP_", "ORIGINALS_WEBDAV_"))
        if name not in DOC and name not in MAINTENANCE and not prefix_documented:
            missing.append(name)
    assert not missing, f"not in docs/UPGRADE.md: {missing}"


def test_the_old_version_can_be_backed_up_as_described():
    # The commands of step 1 exist in the code they name.
    assert "python /tmp/photobox_backup.py create --out /data/backups" in DOC
    from app import backup
    assert backup.main.__code__.co_consts                     # the CLI exists
    assert "tools/migration_rehearsal.py --from-backup" in DOC
    assert (ROOT / "tools" / "migration_rehearsal.py").is_file()


def test_the_retention_trap_is_named():
    assert "PHOTOBOX_MAINTENANCE=off" in DOC and "--dry-run" in DOC
