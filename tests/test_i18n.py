"""Guard against language leaks: DE and EN must define the same keys, and every
t()-key used in the public templates must exist in both languages."""
import re, pathlib
from app.i18n.de import STRINGS as DE
from app.i18n.en import STRINGS as EN


def test_de_en_keys_match():
    only_de = set(DE) - set(EN)
    only_en = set(EN) - set(DE)
    assert not only_de and not only_en, f"DE-only={sorted(only_de)} EN-only={sorted(only_en)}"


def test_public_template_keys_exist():
    root = pathlib.Path("app/templates")
    used = set()
    for p in root.rglob("*.html"):
        for m in re.finditer(r"(?<![A-Za-z0-9_])t\(\s*'([^']+)'", p.read_text(encoding="utf-8")):
            used.add(m.group(1))
    missing = sorted(k for k in used if k not in DE or k not in EN)
    assert not missing, f"keys used in templates but missing from a language: {missing}"
