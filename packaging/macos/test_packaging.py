"""Was sich am Mac-Bauplan ohne Mac prüfen lässt.

Die App selbst kann hier niemand bauen. Prüfbar ist trotzdem das, was am
häufigsten schiefgeht: ein vergessener Info.plist-Schlüssel, eine fehlende
Berechtigung — und ein Kennwort, das versehentlich in einer Datei landet.

    python -m pytest packaging/macos/test_packaging.py -q
"""
import plistlib
import re
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent


def test_entitlements_are_valid_and_complete():
    data = plistlib.loads((HERE / "entitlements.plist").read_bytes())
    # Ohne Kamera-Berechtigung beendet macOS die App beim ersten Zugriff.
    assert data["com.apple.security.device.camera"] is True
    # Ohne diese Ausnahme lädt das von Canon signierte SDK unter Hardened Runtime nicht.
    assert data["com.apple.security.cs.disable-library-validation"] is True


def test_the_bundle_explains_itself_to_macos():
    spec = (HERE / "photobox-booth.spec").read_text(encoding="utf-8")
    # Ohne diesen Satz gibt es keinen Dialog, sondern einen stillen Abbruch.
    assert "NSCameraUsageDescription" in spec
    assert 'bundle_identifier="de.heidrich-digital.photobox-booth"' in spec
    assert "LSMinimumSystemVersion" in spec
    assert "BUNDLE(" in spec, "Ein .app-Bündel, keine Einzeldatei — sonst fehlt das Info.plist"


def test_the_build_script_carries_no_secrets():
    text = (HERE / "build.sh").read_text(encoding="utf-8")
    # Ein Kennwort im Repo wäre in einem öffentlichen Projekt sofort öffentlich.
    assert not re.search(r"--password\s+[^<\s]", text), "Kennwort im Skript"
    assert not re.search(r"--apple-id\s+[^<\s]", text), "Apple-ID im Skript"
    assert "store-credentials" in text, "Der Weg über den Schlüsselbund muss erklärt sein"
    assert "keychain-profile" in text


def test_the_build_script_stops_before_half_signing():
    text = (HERE / "build.sh").read_text(encoding="utf-8")
    assert "set -euo pipefail" in text
    assert "codesign --verify --strict" in text
    assert "stapler staple" in text
    # Apple hat --deep abgekündigt; es übergeht genau die Dateien, an denen die
    # Notarisierung später scheitert. Geprüft wird der Aufruf, nicht das Wort:
    # im Kommentar darf stehen, warum es nicht benutzt wird.
    assert not re.search(r"codesign[^\n]*--deep", text)


def test_the_sdk_stays_out_of_the_bundle():
    spec = (HERE / "photobox-booth.spec").read_text(encoding="utf-8")
    assert "EDSDK" in spec and "nicht enthalten" in spec
    for name in ("EDSDK.framework", "EDSDK.dll", "libEDSDK"):
        assert f'"{name}"' not in spec and f"'{name}'" not in spec


def test_version_comes_from_the_one_file():
    text = (HERE / "build.sh").read_text(encoding="utf-8")
    assert "$ROOT/VERSION" in text
    assert (ROOT / "VERSION").read_text(encoding="utf-8").strip()


def test_the_bundle_carries_the_licences():
    """Fonts, ffmpeg and the Python packages travel in the .app — their licences too."""
    code = "\n".join(z for z in (HERE / "photobox-booth.spec").read_text(encoding="utf-8").splitlines()
                     if not z.lstrip().startswith("#"))
    for needed in ('"LICENSE"', '"LICENSE-EXCEPTION"', '"THIRD_PARTY.md"', '"lizenzen"', "copy_metadata("):
        assert needed in code, needed
