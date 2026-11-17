"""Where a picture came from (no. 60).

``media.source`` is written at the insert paths that know it:

* ``guest``  - the guest upload in the gallery
* ``admin``  - the upload on the event page in the admin
* ``box:<device id>`` - the old Windows uploader (``POST /api/upload``)
* ``import`` - the folder import, ``ftp`` - the FTP/SFTP intake

A booth upload over the v2 API carries ``device_id`` already, so it needs no
source of its own; a moved gallery's pictures carry ``foreign_id``. Rows from
before this column have neither and show no chip.
"""
from __future__ import annotations

from typing import Any, Dict, Iterable, List

from app import db

#: Order of the chips; boxes come first, sorted by name.
FIXED = ("guest", "admin", "import", "ftp")


def key(row: Dict[str, Any]) -> str:
    src = str(row.get("source") or "")
    if src in FIXED:
        return src
    if src.startswith("box:") and src[4:].isdigit():
        return src
    if row.get("device_id"):
        return f"box:{int(row['device_id'])}"
    if row.get("foreign_id"):
        return "import"
    return ""


def _labels() -> Dict[str, str]:
    from app.i18n import t
    return {"guest": t("src.guest"), "admin": t("src.admin"), "import": t("src.import"), "ftp": t("src.ftp")}


def _device_names(ids: Iterable[int]) -> Dict[int, str]:
    ids = sorted({int(i) for i in ids})
    if not ids:
        return {}
    con = db.get_db()
    try:
        marks = ",".join("?" * len(ids))
        return {int(r["id"]): str(r["name"]) for r in
                con.execute(f"SELECT id, name FROM devices WHERE id IN ({marks})", ids)}
    finally:
        con.close()


def label_of(k: str, names: Dict[int, str]) -> str:
    from app.i18n import t
    if k.startswith("box:"):
        name = names.get(int(k[4:]))
        return name if (name and name.lower().startswith("box")) else (f"Box {name}" if name else t("src.box"))
    return _labels().get(k, "")


#: What guests see (final review U2): not the admin's sources but three
#: groups in their words. Every booth is "Fotobox", guest uploads "Gäste",
#: what the operator added (admin upload, folder import, FTP intake) "Fotograf".
GUEST_GROUPS = ("booth", "guest", "photographer")


def guest_key(k: str) -> str:
    if k.startswith("box:"):
        return "booth"
    if k == "guest":
        return "guest"
    return "photographer" if k else ""


def _guest_label(k: str) -> str:
    from app.i18n import t
    return t({"booth": "src.for_guests.booth", "guest": "src.for_guests.guest",
              "photographer": "src.for_guests.photographer"}[k])


def summarize(rows: List[Dict[str, Any]], for_guests: bool = False) -> Dict[str, Any]:
    """Chips with counts and the key of each row, for one page of pictures.

    ``for_guests``: the three guest groups instead of the admin's sources.
    """
    if for_guests:
        of = {int(r["id"]): guest_key(key(r)) for r in rows}
        counts: Dict[str, int] = {}
        for k in of.values():
            if k:
                counts[k] = counts.get(k, 0) + 1
        chips = [{"key": k, "label": _guest_label(k), "count": counts[k]} for k in GUEST_GROUPS if k in counts]
        return {"chips": chips, "of": of, "labels": {k: _guest_label(k) for k in counts}}
    of = {int(r["id"]): key(r) for r in rows}
    counts: Dict[str, int] = {}
    for k in of.values():
        if k:
            counts[k] = counts.get(k, 0) + 1
    names = _device_names(int(k[4:]) for k in counts if k.startswith("box:"))
    boxes = sorted((k for k in counts if k.startswith("box:")), key=lambda k: label_of(k, names).lower())
    order = boxes + [k for k in FIXED if k in counts]
    chips = [{"key": k, "label": label_of(k, names), "count": counts[k]} for k in order]
    return {"chips": chips, "of": of, "labels": {k: label_of(k, names) for k in counts}}


def describe(row: Dict[str, Any]) -> Dict[str, str]:
    """For one row (MCP listings): the key and a readable label."""
    k = key(row)
    names = _device_names([int(k[4:])]) if k.startswith("box:") else {}
    return {"source": k, "source_label": label_of(k, names) if k else ""}
