#!/bin/bash
# Baut "Photobox Booth.app", signiert, notarisiert und packt sie in ein DMG.
#
#   cd packaging/macos && ./build.sh
#
# Läuft nur auf einem Mac und nur mit Apple-Entwicklermitgliedschaft. Was das
# Skript NICHT tut: Kennwörter speichern, Zertifikate anlegen oder das Canon-SDK
# einpacken. Es prüft die Voraussetzungen, sagt im Klartext, was fehlt, und
# bricht ab, bevor eine halb fertige App entsteht.
#
# Einmalige Vorbereitung (nicht Teil dieses Skripts):
#   1. In Xcode oder auf developer.apple.com ein "Developer ID Application"-
#      Zertifikat erzeugen und in den Schlüsselbund legen.
#   2. Ein app-spezifisches Kennwort auf appleid.apple.com anlegen.
#   3. Es einmal im Schlüsselbund hinterlegen:
#        xcrun notarytool store-credentials photobox \
#          --apple-id <deine-apple-id> --team-id <TEAMID> --password <app-kennwort>
#      Danach kennt nur der Schlüsselbund das Kennwort — nirgends sonst.
#
# Steuerung über Umgebungsvariablen (alle optional):
#   SIGN_IDENTITY   Name des Zertifikats (Standard: das einzige Developer-ID-Zertifikat)
#   NOTARY_PROFILE  Name des Schlüsselbund-Profils aus Schritt 3 (Standard: photobox)
#   SKIP_NOTARIZE=1 nur bauen und signieren, ohne Apple zu fragen (für Zwischenstände)
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"
export PHOTOBOX_ROOT="$ROOT"
PHOTOBOX_VERSION="$(tr -d '[:space:]' < "$ROOT/VERSION")"
export PHOTOBOX_VERSION
NOTARY_PROFILE="${NOTARY_PROFILE:-photobox}"
APP="$HERE/dist/Photobox Booth.app"
DMG="$HERE/dist/Photobox-Booth-$PHOTOBOX_VERSION.dmg"

say()  { printf "\n\033[1;36m%s\033[0m\n" "$*"; }
fail() { printf "\n\033[1;31mAbbruch: %s\033[0m\n" "$*" >&2; exit 1; }

# ── Voraussetzungen ──────────────────────────────────────
[ "$(uname)" = "Darwin" ] || fail "Das läuft nur auf einem Mac."
command -v xcrun >/dev/null || fail "Xcode-Kommandozeilenwerkzeuge fehlen: xcode-select --install"
PYTHON="${PYTHON:-python3}"
command -v "$PYTHON" >/dev/null || fail "python3 nicht gefunden."

if [ -z "${SIGN_IDENTITY:-}" ]; then
  SIGN_IDENTITY="$(security find-identity -v -p codesigning 2>/dev/null \
    | grep "Developer ID Application" | head -1 | sed -E 's/.*"(.*)"/\1/')"
fi
[ -n "$SIGN_IDENTITY" ] || fail "Kein „Developer ID Application\"-Zertifikat im Schlüsselbund.
  Prüfen mit:  security find-identity -v -p codesigning
  Anlegen in Xcode unter Settings → Accounts → Manage Certificates."

if [ "${SKIP_NOTARIZE:-0}" != "1" ]; then
  xcrun notarytool history --keychain-profile "$NOTARY_PROFILE" >/dev/null 2>&1 || fail \
"Das Schlüsselbund-Profil „$NOTARY_PROFILE\" gibt es nicht (oder es ist abgelaufen).
  Einmalig anlegen:
    xcrun notarytool store-credentials $NOTARY_PROFILE \\
      --apple-id <deine-apple-id> --team-id <TEAMID> --password <app-spezifisches-Kennwort>
  Oder diesen Schritt überspringen:  SKIP_NOTARIZE=1 ./build.sh"
fi

say "Photobox $PHOTOBOX_VERSION · Zertifikat: $SIGN_IDENTITY"

# ── Bauen ────────────────────────────────────────────────
say "Umgebung vorbereiten"
VENV="$HERE/.venv"
[ -d "$VENV" ] || "$PYTHON" -m venv "$VENV"
# shellcheck disable=SC1091
source "$VENV/bin/activate"
pip install --quiet --upgrade pip wheel
# From booth/: its requirements say `-e ../sync`, which pip resolves against the
# working directory (the Windows build learned that the hard way). The
# constraints are the exact versions the test suites passed with.
(cd "$ROOT/booth" && pip install --quiet -r requirements.txt -c "$ROOT/constraints.txt")
pip install --quiet pyinstaller

say "App bauen"
rm -rf "$HERE/build" "$HERE/dist"
pyinstaller --noconfirm --clean --distpath "$HERE/dist" --workpath "$HERE/build" \
  "$HERE/photobox-booth.spec"
[ -d "$APP" ] || fail "PyInstaller hat kein Bündel erzeugt — Ausgabe oben lesen."

# ── Signieren ────────────────────────────────────────────
# Zuerst jede mitgebrachte Bibliothek, dann das Bündel. „--deep" wäre kürzer,
# ist aber von Apple abgekündigt und übergeht genau die Dateien, an denen die
# Notarisierung später scheitert.
say "Signieren"
find "$APP" \( -name "*.dylib" -o -name "*.so" -o -name "Python3" \) -type f -print0 \
  | while IFS= read -r -d '' lib; do
      codesign --force --timestamp --options runtime --sign "$SIGN_IDENTITY" "$lib"
    done
codesign --force --timestamp --options runtime \
  --entitlements "$HERE/entitlements.plist" --sign "$SIGN_IDENTITY" "$APP"
codesign --verify --strict --verbose=2 "$APP" || fail "Signatur hält der Prüfung nicht stand."

# ── Verpacken ────────────────────────────────────────────
say "DMG bauen"
rm -f "$DMG"
STAGING="$(mktemp -d)"
cp -R "$APP" "$STAGING/"
ln -s /Applications "$STAGING/Applications"
hdiutil create -volname "Photobox Booth" -srcfolder "$STAGING" -ov -format UDZO "$DMG" >/dev/null
rm -rf "$STAGING"
codesign --force --timestamp --sign "$SIGN_IDENTITY" "$DMG"

# ── Notarisieren ─────────────────────────────────────────
if [ "${SKIP_NOTARIZE:-0}" = "1" ]; then
  say "Notarisierung übersprungen — diese Fassung zeigt beim Kunden eine Warnung."
else
  say "Bei Apple einreichen (dauert meist ein paar Minuten)"
  xcrun notarytool submit "$DMG" --keychain-profile "$NOTARY_PROFILE" --wait \
    || fail "Notarisierung abgelehnt. Grund holen mit:
    xcrun notarytool log <submission-id> --keychain-profile $NOTARY_PROFILE"
  xcrun stapler staple "$DMG"
  xcrun stapler validate "$DMG" || fail "Stapeln fehlgeschlagen."
  say "Notarisiert und gestapelt."
fi

say "Fertig: $DMG"
echo "Prüfen wie ein Kunde:  spctl --assess --type open --context context:primary-signature -v \"$DMG\""
echo "Canon-SDK gehört NICHT hier hinein — nach der Installation einmal:"
echo "  \"/Applications/Photobox Booth.app/Contents/MacOS/photobox-booth\" --install-edsdk <SDK-Ordner>"
