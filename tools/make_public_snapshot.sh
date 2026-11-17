#!/usr/bin/env bash
# Builds the public snapshot of this repository.
#
#   tools/make_public_snapshot.sh [output-dir] [message-file]
#
# Without a message file the commit is "Photobox Gallery <version>" plus a note
# that the history stays private. A release worth explaining gets its own file.
#
# The public repository is a mirror, not a fork: one commit per release, no
# development history, and without anything that belongs only to us. It is
# regenerated on every release rather than kept in sync commit by commit —
# that way nothing private can leak through a merge nobody read.
#
# What is left out:
#   docs/intern/        business material (removed from the tree 15.11.2026; kept as a net)
#   docs/PUBLISHING.md  this process — it is about the mirror, not for it
#   docs/FIRST_ISSUES.md issues to open; they belong in the tracker
#   docs/release-*.txt  commit messages for releases — build input, not content
#
# Environment: SNAPSHOT_EMAIL / SNAPSHOT_NAME (commit author, default the
# GitHub noreply address), SNAPSHOT_DENYLIST (see below), PYTHON.
# This script never pushes and sets no remote; publishing is a separate,
# manual step (docs/PUBLISHING.md).
set -euo pipefail

PYTHON="${PYTHON:-python3}"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OUT="${1:-/tmp/photobox-public}"
MESSAGE_FILE="${2:-}"
# nach dem cd unten wäre ein relativer Pfad falsch aufgelöst
[ -n "$MESSAGE_FILE" ] && MESSAGE_FILE="$(cd "$(dirname "$MESSAGE_FILE")" && pwd)/$(basename "$MESSAGE_FILE")"
VERSION="$(head -n1 "$ROOT/VERSION" | tr -d '[:space:]')"

rm -rf "$OUT"
mkdir -p "$OUT"

# Copy the tracked files minus the exclusions. Python instead of
# `cp --parents` (GNU only, failed on macOS, second review M2) and instead of
# `grep -z` (not on every BSD grep).
"$PYTHON" - "$ROOT" "$OUT" <<'PY'
import os, re, shutil, subprocess, sys
root, out = sys.argv[1:3]
EXCLUDE = [r"^docs/intern/", r"^docs/PUBLISHING\.md$", r"^docs/FIRST_ISSUES\.md$", r"^docs/release-.*\.txt$"]
names = subprocess.run(["git", "-C", root, "ls-files", "-z"], capture_output=True, check=True).stdout.decode().split("\0")
for name in names:
    if not name or any(re.search(p, name) for p in EXCLUDE):
        continue
    src, dst = os.path.join(root, name), os.path.join(out, name)
    if not os.path.lexists(src):
        continue                                   # deleted in the working tree, not yet committed
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    shutil.copy2(src, dst, follow_symlinks=False)
PY

# Words that must not leave the house (names, addresses, hosts). The list is
# NOT in the repository - it would publish what it protects. The maintainer
# keeps it outside and points SNAPSHOT_DENYLIST at it: one entry per line,
# "#" starts a comment, matched case-insensitively. Any hit stops the snapshot;
# the message names the file and the list's line number, not the word.
if [ -n "${SNAPSHOT_DENYLIST:-}" ]; then
  "$PYTHON" - "$OUT" "$SNAPSHOT_DENYLIST" <<'PY'
import os, sys
out, listfile = sys.argv[1:3]
words = []
for no, line in enumerate(open(listfile, encoding="utf-8"), 1):
    line = line.strip()
    if line and not line.startswith("#"):
        words.append((no, line.lower().encode("utf-8")))
hits = 0
for base, _, files in os.walk(out):
    if "/.git" in base or base.endswith(".git"):
        continue
    for f in files:
        path = os.path.join(base, f)
        data = open(path, "rb").read().lower()
        for no, word in words:
            if word in data:
                print(f"deny-list line {no} found in {os.path.relpath(path, out)}", file=sys.stderr)
                hits += 1
sys.exit(1 if hits else 0)
PY
fi

cd "$OUT"
git init -q -b main
git add -A

# The hygiene check runs against the snapshot, not against our working tree:
# what leaves the house is what gets checked.
"$PYTHON" tools/check_hygiene.py

if [ -n "$MESSAGE_FILE" ]; then
  git -c user.name="${SNAPSHOT_NAME:-Till Heidrich}" -c user.email="${SNAPSHOT_EMAIL:-tillheidrich@users.noreply.github.com}" \
      commit -q -F "$MESSAGE_FILE"
else
  git -c user.name="${SNAPSHOT_NAME:-Till Heidrich}" -c user.email="${SNAPSHOT_EMAIL:-tillheidrich@users.noreply.github.com}" \
      commit -q -m "Photobox Gallery $VERSION" \
      -m "Public snapshot. Development history stays in the author's private repository. Read AGENTS.md before changing anything."
fi
git branch ecosystem

git bundle create "$OUT.bundle" --all >/dev/null
echo "snapshot: $OUT"
echo "bundle:   $OUT.bundle"
echo "files:    $(git ls-files | wc -l), version $VERSION, 1 commit"
