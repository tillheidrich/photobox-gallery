#!/usr/bin/env bash
# Startet die Box auf einem Mac — und startet sie nach einem Absturz oder
# einem Neustart aus der Ferne wieder. Die Datei lässt sich im Finder
# doppelklicken; das ist der Grund für die Endung .command.
cd "$(dirname "$0")/.."

# caffeinate: Ein Mac, der mitten im Event einschläft, ist eine Box, die aus
# ist. Das gilt auch für den Bildschirm.
VORNE=""
command -v caffeinate >/dev/null 2>&1 && VORNE="caffeinate -dimsu"

while true; do
  $VORNE ".venv/bin/python" -m boothcore "$@" && break
  code=$?
  # 4 = another booth already runs and was brought to the front.
  [ "$code" -eq 4 ] && break
  echo "Box beendet mit Code ${code} — Neustart in 3 Sekunden …"
  sleep 3
done
