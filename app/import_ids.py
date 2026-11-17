"""Set the old gallery ids from a CSV in one go: ``python -m app.import_ids ids.csv``.

Columns: ``kennung``, and ``slug`` or ``titel``; optionally ``datei`` and
``datei_kennung`` for single pictures. Uses the gallery's ``DATA_DIR`` like the
server does. Fetches nothing from anywhere; see app/legacy.py.
"""
from __future__ import annotations

import sys


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if len(argv) != 1:
        print("Aufruf: python -m app.import_ids datei.csv", file=sys.stderr)
        return 2
    from app import db, legacy
    db.init_db()
    report = legacy.import_csv(argv[0])
    for line in report["lines"]:
        print(line)
    print(f"gesetzt: {report['set']} · nicht gefunden: {report['missing']} · doppelt: {report['double']}"
          f" · Bilder: {report['files']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
