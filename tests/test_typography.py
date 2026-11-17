"""Die Schriften müssen mitgeliefert werden, nicht nachgeladen.

Eine Box ohne Internet ist der Normalfall, und im datensparsamen Betrieb wäre
ein Aufruf bei Google ein Datenabfluss, den niemand angeordnet hat. Seit dem
Erscheinungsbild „Abzug" sind es drei Familien: Inter arbeitet, Archivo zeigt,
JetBrains Mono ist Technik und wird auf der Gästeseite gar nicht geladen.
"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
GALLERY_FONTS = ROOT / "app" / "static" / "fonts"
BOOTH_FONTS = ROOT / "booth" / "boothcore" / "web" / "static" / "fonts"
BRAND_CSS = ROOT / "app" / "static" / "css" / "brand"
BOOTH_BRAND_CSS = ROOT / "booth" / "boothcore" / "web" / "static" / "css" / "brand"
GALLERY_CSS = ROOT / "app" / "static" / "css" / "design.css"
BOOTH_CSS = ROOT / "booth" / "boothcore" / "web" / "static" / "app.css"
BOOTH_JS = ROOT / "booth" / "boothcore" / "web" / "static" / "app.js"


def test_the_files_ship_with_both_surfaces():
    for name in ("inter-variable.woff2", "archivo-variable.woff2"):
        assert (GALLERY_FONTS / name).exists(), name
        # Die Box braucht dieselben Dateien: sie lädt nichts nach.
        assert (BOOTH_FONTS / name).exists(), name
    # JetBrains Mono nur in der Verwaltung, nicht an der Box.
    assert (GALLERY_FONTS / "jetbrains-mono-variable.woff2").exists()
    assert not (BOOTH_FONTS / "jetbrains-mono-variable.woff2").exists()


def test_the_licences_travel_with_them():
    """Die OFL verlangt, dass die Lizenz mitreist. Das ist Pflicht, nicht Höflichkeit."""
    lizenzen = ROOT / "docs" / "lizenzen"
    for name in ("OFL-Inter.txt", "OFL-Archivo.txt", "OFL-JetBrains-Mono.txt"):
        text = (lizenzen / name).read_text(encoding="utf-8")
        assert "SIL OPEN FONT LICENSE" in text.upper(), name


def test_the_ttf_cuts_for_printing_exist():
    """Pillow zeichnet Sharepic und Druck — und kann kein woff2."""
    ttf = GALLERY_FONTS / "ttf"
    for name in ("Inter-Regular.ttf", "Inter-SemiBold.ttf", "Archivo-SemiBold.ttf"):
        assert (ttf / name).exists(), name


def test_no_stylesheet_fetches_a_font_from_the_internet():
    sheets = [GALLERY_CSS, BOOTH_CSS, ROOT / "app" / "static" / "css" / "public.css",
              ROOT / "app" / "static" / "css" / "admin.css"]
    sheets += sorted(BRAND_CSS.glob("*.css")) + sorted(BOOTH_BRAND_CSS.glob("*.css"))
    for css in sheets:
        text = css.read_text(encoding="utf-8")
        assert "fonts.googleapis.com" not in text, css
        assert "fonts.gstatic.com" not in text, css
        assert not re.search(r"@import\s+url\(\s*['\"]?https?://", text), css
        assert not re.search(r"url\(\s*['\"]?https?://", text), css


def test_both_surfaces_name_the_same_typefaces():
    gallery = GALLERY_CSS.read_text(encoding="utf-8")
    booth = BOOTH_CSS.read_text(encoding="utf-8")
    assert '--font: "Inter"' in gallery
    assert '--font-stack:"Inter"' in booth
    # und der Kiosk bietet beide in der Schriftauswahl des Profils an
    js = BOOTH_JS.read_text(encoding="utf-8")
    assert '"Inter"' in js and '"Archivo"' in js


def test_every_stack_ends_in_something_the_system_has():
    """Lädt die Datei nicht, muss die Box lesbar bleiben statt leer."""
    booth = BOOTH_CSS.read_text(encoding="utf-8")
    stack = re.search(r"--font-stack:([^;]+);", booth).group(1)
    assert stack.rstrip().endswith("sans-serif")
    gallery = re.search(r"--font:([^;]+);", GALLERY_CSS.read_text(encoding="utf-8")).group(1)
    assert gallery.rstrip().endswith("sans-serif")


def test_numbers_that_change_do_not_jitter():
    booth = BOOTH_CSS.read_text(encoding="utf-8")
    assert "font-variant-numeric:tabular-nums" in booth.replace(" ", "")
    for element in (".countdown", ".statusline"):
        assert element in booth
