#!/usr/bin/env bash
# Photobox Booth unter Ubuntu 24.04 (Kamera über gphoto2, Druck über CUPS).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
sudo apt-get update
sudo apt-get install -y python3-venv python3-dev libgphoto2-dev gphoto2 cups printer-driver-gutenprint ffmpeg chromium-browser || true
cd "$ROOT"
python3 -m venv "$ROOT/.venv"
"$ROOT/.venv/bin/pip" install -r "$ROOT/requirements.txt" gphoto2
mkdir -p ~/.config/autostart
cat > ~/.config/autostart/photobox-booth.desktop <<DESK
[Desktop Entry]
Type=Application
Name=Photobox Booth
Exec=bash -c 'cd $ROOT && while true; do .venv/bin/python -m boothcore && break; sleep 3; done'
X-GNOME-Autostart-enabled=true
DESK
echo "Fertig. Drucker in CUPS einrichten (SELPHY CP1300: USB mit Gutenprint oder WLAN per IPP)."
