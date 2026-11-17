#!/usr/bin/env bash
# Photobox Booth auf einem Mac einrichten.
#
# Gedacht für den Test auf dem eigenen Rechner und für Boxen, in denen ein Mac
# mini steckt. Was hier passiert:
#
#   * Homebrew-Pakete für Kamera (gphoto2) und Video (ffmpeg) — ohne sie läuft
#     die Box auch, aber nur mit Webcam und ohne Boomerang/Video.
#   * Ein eigener Python-Ordner (.venv), damit nichts am System hängt.
#   * Nichts wird in den Autostart geschrieben. Das macht `--install`, wenn du
#     es willst; zum Ausprobieren wäre es im Weg.
#
# Gedruckt wird über CUPS, das auf einem Mac ohnehin läuft: Drucker in den
# Systemeinstellungen einrichten, die Box findet ihn dann selbst.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"

if [ "$(uname -s)" != "Darwin" ]; then
  echo "Das ist das Skript für macOS. Für Linux: scripts/install-ubuntu.sh" >&2
  exit 1
fi

if command -v brew >/dev/null 2>&1; then
  echo "── Pakete (gphoto2, ffmpeg) ──"
  brew install libgphoto2 gphoto2 ffmpeg || echo "Homebrew war nicht vollständig — weiter geht es trotzdem."
else
  cat <<'HINWEIS'
Homebrew ist nicht installiert. Die Box läuft trotzdem: Webcam, Druck über
CUPS und der Ordner-Modus brauchen es nicht. Für Spiegelreflexkameras am
Kabel (gphoto2) und für Boomerang/Video (ffmpeg) fehlt es.
Nachinstallieren: https://brew.sh, danach dieses Skript noch einmal.
HINWEIS
fi

echo "── Python-Umgebung ──"
cd "$ROOT"
python3 -m venv "$ROOT/.venv"
"$ROOT/.venv/bin/pip" install --upgrade pip >/dev/null
"$ROOT/.venv/bin/pip" install -r "$ROOT/requirements.txt"

# gphoto2 hat eine C-Abhängigkeit; ohne Homebrew scheitert der Bau. Das ist
# kein Grund, die Einrichtung abzubrechen — die Box läuft auch ohne.
if command -v brew >/dev/null 2>&1; then
  "$ROOT/.venv/bin/pip" install gphoto2 || echo "gphoto2-Anbindung nicht gebaut — Kamera dann über Webcam oder Ordner."
fi

cat <<FERTIG

Fertig.

  Starten:        $ROOT/scripts/start-booth.command   (oder im Finder doppelklicken)
  Ohne Kamera:    .venv/bin/python -m boothcore --camera mock
  Selbstprüfung:  .venv/bin/python -m boothcore --doctor
  Autostart an:   .venv/bin/python -m boothcore --install
  Autostart weg:  .venv/bin/python -m boothcore --uninstall

Die Oberfläche läuft danach unter http://127.0.0.1:8765/ — die Verwaltung
erreichst du, indem du drei Sekunden lang oben links auf den Startbildschirm
drückst. Die PIN steht beim ersten Start im Fenster; ändere sie.
FERTIG
