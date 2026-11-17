"""Target passwords for Photobox Sync, sealed before anything is written (17.11.2026).

The sync page of a PC running Photobox Sync sends its targets with a
``set_sync_config`` job. Until 17.11.2026 an FTP/SFTP password typed there
went into ``device_jobs.payload_json`` in clear and stayed there. Now, as
for the booth's second target (app/second_target.py):

* the program publishes an X25519 public key in its report (``sealing``;
  sync/photobox_sync/sealing.py), its private key stays on that PC;
* ``seal_payload`` runs in the request handler and replaces every typed
  password by ``password_sealed`` + ``key_id`` before the job is stored;
* a program too old to publish a key gets no password at all: the request
  is refused with "Fassung zu alt — Passwort am PC eintragen", never sent
  in clear; ``ecosystem.add_job`` refuses a clear one from any other path;
* the payload is emptied when the program answers, when the job expires and
  when a newer settings job replaces it (``second_target.SEALED_JOBS``);
  pages and MCP see ``••••`` until then;
* ``blank_clear_passwords`` empties the password fields of rows written
  before this (runs with the schema at every start; idempotent).
"""
from __future__ import annotations

import base64
import json
import logging
import re
from typing import Any, Dict, Optional, Tuple

JOB = "set_sync_config"
MASK = "••••"
TOO_OLD = "Fassung zu alt — Passwort am PC eintragen"
_B64 = re.compile(r"^[A-Za-z0-9+/]{40,64}={0,2}$")
_CONTROL = re.compile(r"[\x00-\x1f\x7f-\x9f]")
#: Fields of a target entry that hold a secret; never stored in clear.
SECRET_FIELDS = ("password",)

log = logging.getLogger(__name__)


class TooOld(PermissionError):
    pass


def public_key(report: Optional[Dict[str, Any]]) -> Optional[Tuple[str, str]]:
    """(public key, key id) the program reported, or None for a version without one."""
    s = (report or {}).get("sealing") if isinstance(report, dict) else None
    if not isinstance(s, dict):
        return None
    pk = str(s.get("public_key") or "")
    if not _B64.match(pk):
        return None
    try:
        if len(base64.b64decode(pk)) != 32:
            return None
    except ValueError:
        return None
    return pk, re.sub(r"[^0-9a-f]", "", str(s.get("key_id") or ""))[:16]


def _clean_password(value: Any) -> str:
    if not isinstance(value, str):
        raise ValueError("Passwort: muss Text sein")
    if _CONTROL.search(value):
        raise ValueError("Passwort: Steuerzeichen sind nicht erlaubt")
    if len(value) > 256:
        raise ValueError("Passwort: höchstens 256 Zeichen")
    return value


def seal_payload(payload: Dict[str, Any], report: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """The job's payload with every typed target password sealed to the program's key.

    An empty field or the dots mean "keep the stored one" and are dropped.
    Raises ``TooOld`` when a password was typed and the program has no key.
    """
    from app.second_target import seal
    out = dict(payload or {})
    targets = out.get("targets")
    if not isinstance(targets, list):
        return out
    key = public_key(report)
    sealed = []
    for t in targets:
        if not isinstance(t, dict):
            continue
        t = dict(t)
        t.pop("password_sealed", None)            # only this handler seals
        t.pop("key_id", None)
        pw = t.pop("password", None)
        if pw not in (None, "", MASK):
            if key is None:
                raise TooOld(TOO_OLD)
            t["password_sealed"] = seal(_clean_password(pw), key[0])
            t["key_id"] = key[1]
        sealed.append(t)
    out["targets"] = sealed
    return out


def has_clear_password(payload: Any) -> bool:
    targets = payload.get("targets") if isinstance(payload, dict) else None
    return any(isinstance(t, dict) and any(t.get(f) not in (None, "", MASK) for f in SECRET_FIELDS)
               for t in (targets if isinstance(targets, list) else []))


def mask(payload: Dict[str, Any]) -> Dict[str, Any]:
    """For a page or an assistant: sealed or clear passwords inside the targets as dots."""
    out = dict(payload)
    if isinstance(out.get("targets"), list):
        out["targets"] = [{k: (MASK if k in SECRET_FIELDS + ("password_sealed",) and v else v) for k, v in t.items()}
                          if isinstance(t, dict) else t for t in out["targets"]]
    return out


def supersede_open(con, device_id: int) -> int:
    """An open settings job with targets is replaced by the newer one (and emptied)."""
    rows = con.execute("SELECT id, payload_json FROM device_jobs WHERE device_id=? AND type=? AND done_at IS NULL",
                       (int(device_id), JOB)).fetchall()
    ids = []
    for r in rows:
        try:
            p = json.loads(r["payload_json"] or "{}")
        except ValueError:
            p = {}
        if isinstance(p, dict) and "targets" in p:
            ids.append(int(r["id"]))
    for jid in ids:
        con.execute("UPDATE device_jobs SET done_at=datetime('now'), ok=0, result=?, expired=1, payload_json='{}' "
                    "WHERE id=?", ("ersetzt durch eine neuere Änderung", jid))
    return len(ids)


def blank_clear_passwords(con) -> int:
    """One-time clean-up: blank password fields left in clear in old ``set_sync_config`` rows.

    Additive (only the value of the field changes, to ""), safe to run any
    number of times (a second run finds nothing), logged with the row count
    and ids, never the secret. Returns how many rows it changed.
    """
    try:
        rows = con.execute("SELECT id, payload_json FROM device_jobs WHERE type=? AND payload_json LIKE ?",
                           (JOB, '%"password"%')).fetchall()
    except Exception:                                  # noqa: BLE001 — no table yet: nothing to clean
        return 0
    changed = []
    if rows:
        # Overwrite the old bytes instead of leaving them in a free page of the file.
        con.execute("PRAGMA secure_delete=ON")
    for r in rows:
        try:
            p = json.loads(r["payload_json"] or "{}")
        except ValueError:
            continue
        if not has_clear_password(p):
            continue
        for t in p.get("targets") or []:
            if isinstance(t, dict):
                for f in SECRET_FIELDS:
                    if t.get(f) not in (None, "", MASK):
                        t[f] = ""
        con.execute("UPDATE device_jobs SET payload_json=? WHERE id=?", (json.dumps(p), int(r["id"])))
        changed.append(int(r["id"]))
    if changed:
        log.warning("Klartext-Passwörter aus %d alten Sync-Aufträgen entfernt (Auftrag %s)", len(changed),
                    ", ".join(map(str, changed[:20])) + (" …" if len(changed) > 20 else ""))
    return len(changed)
