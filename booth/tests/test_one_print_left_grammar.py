"""'Noch 1 Drucke möglich' stood on the result screen (seen 14.11.2026)."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
JS = (ROOT / "booth/boothcore/web/static/app.js").read_text(encoding="utf-8")
I18N = (ROOT / "booth/boothcore/web/static/i18n.js").read_text(encoding="utf-8")
TEXTS = (ROOT / "app/booth_texts.py").read_text(encoding="utf-8")


def test_one_print_has_its_own_sentence_in_both_languages():
    assert '"print.left_one": "Noch 1 Druck möglich"' in I18N
    assert '"print.left_one": "1 print left"' in I18N
    assert '("print.left_one", ' in TEXTS and '"print.left_one": "1 print left"' in TEXTS


def test_the_result_screen_uses_it_for_exactly_one():
    assert re.search(r't\(n === 1 \? "print\.left_one" : "print\.left", \{ n \}\)', JS)


def test_the_built_in_template_catalogue_is_part_of_the_repository():
    """The gallery reads it when a built-in layout is copied into the editor.

    It sat under the ignore rule for runtime data, so no image built from git
    had it: the page failed in production.
    """
    import subprocess
    out = subprocess.run(["git", "ls-files", "app/static/data/collage_templates.json"],
                         cwd=ROOT, capture_output=True, text=True).stdout
    assert out.strip() == "app/static/data/collage_templates.json"
