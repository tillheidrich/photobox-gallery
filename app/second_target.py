"""A booth's second upload target, set from the gallery (17.11.2026).

The booth's card "Zweites Ziel im Netz" (Picdrop over FTPS, FTP, an SFTP
NAS) can now be filled in on the gallery's sync page of the booth and by an
assistant with the right ``secure``. The design and why each part is as it
is: docs/SECOND-TARGET-FROM-THE-GALLERY.md. In short:

* No switch at the booth any more (owner's decision, 17.11.2026 evening):
  every paired booth that reports its key takes the setting. What protects
  the password instead is decided at the booth: when type, host, port or
  user change and no new password comes with the change, the booth deletes
  the stored one and the old server's pinned SFTP key, and the target waits
  for a password typed for the new server (rule B2, SECURITY.md).
* The password is sealed in the request handler, before anything is
  stored, to the public key the booth published in its heartbeat
  (libsodium sealed box). The database holds only that ciphertext, inside
  the job, and the job's payload is emptied as soon as the booth answers or
  the job expires. The form never shows a password: empty keeps the stored
  one, "Zugang entfernen" deletes the target at the booth.
* One job type changes only the target's fields; the booth checks them
  again with the same rules (``booth/boothcore/second_target.py``; a test in
  each suite compares the lists).
* Through MCP the fields and the check, never the password and never a
  new SFTP server key: an assistant should not carry FTP passwords through
  a chat, where they would sit in a transcript the gallery does not control.
* Every change is one line in the change log (app/protokoll.py): who, when,
  which fields, never the password; the card shows the last ones.
"""
from __future__ import annotations

import base64
import ipaddress
import json
import re
from typing import Any, Dict, Optional

#: Same lists as booth/boothcore/second_target.py (compared by a test in each suite).
TYPES = ("ftps", "ftp", "sftp")
FIELDS = ("type", "host", "port", "user", "path", "flat", "enabled", "originals", "print_sheets")
LIMITS = {"host": 253, "user": 128, "password": 256, "path": 255}
NAMES = ("picdrop", "zweites-ziel")

#: The job whose payload carries the sealed password and is emptied after the answer.
#: Jobs whose payload may carry a sealed password: emptied once answered or
#: expired. ``set_sync_config`` since 17.11.2026 (app/sync_secrets.py).
SEALED_JOBS = frozenset({"set_second_target", "set_sync_config"})
JOBS = ("set_second_target", "check_second_target", "backfill_second_target", "accept_second_target_host_key")

_LABEL = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?$")
_CONTROL = re.compile(r"[\x00-\x1f\x7f-\x9f]")
_B64 = re.compile(r"^[A-Za-z0-9+/]{40,64}={0,2}$")
#: An SSH host key fingerprint as OpenSSH shows it; never the key itself.
FINGERPRINT = re.compile(r"^SHA256:[A-Za-z0-9+/]{43}$")


def _text(value: Any, field: str, limit: int) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        raise ValueError(f"{field}: muss Text sein")
    if _CONTROL.search(value):
        raise ValueError(f"{field}: Steuerzeichen sind nicht erlaubt")
    value = value.strip()
    if len(value) > limit:
        raise ValueError(f"{field}: höchstens {limit} Zeichen")
    return value


def clean_host(value: Any) -> str:
    host = _text(value, "Server", LIMITS["host"]).rstrip(".")
    if not host:
        raise ValueError("Server: bitte eintragen")
    try:
        return str(ipaddress.ip_address(host.strip("[]")))
    except ValueError:
        pass
    labels = host.split(".")
    if not all(_LABEL.match(part) for part in labels) or labels[-1].isdigit():
        raise ValueError("Server: nur der Name oder die IP-Adresse, ohne ftp://, Pfad oder Port")
    return host.lower()


def clean_path(value: Any) -> str:
    """The folder (app/zielordner.py: placeholders, no "..", slashes unified)."""
    from app import zielordner
    return zielordner.check(_text(value, "Ordner", LIMITS["path"]))


def clean_fields(raw: Any) -> Dict[str, Any]:
    """Exactly the rules of the booth: unknown fields refuse the whole target."""
    if not isinstance(raw, dict):
        raise ValueError("Ziel fehlt")
    extra = sorted(set(map(str, raw)) - set(FIELDS))
    if extra:
        raise ValueError("Nicht änderbar über diesen Weg: " + ", ".join(extra))
    kind = str(raw.get("type") or "")
    if kind not in TYPES:
        raise ValueError("Art: ftps, ftp oder sftp")
    port = raw.get("port") or 0
    if isinstance(port, bool) or not isinstance(port, int) or not 0 <= port <= 65535:
        raise ValueError("Port: 1 bis 65535 (leer = üblich)")
    out: Dict[str, Any] = {"type": kind, "host": clean_host(raw.get("host")),
                           "user": _text(raw.get("user"), "Benutzer", LIMITS["user"]),
                           "path": clean_path(raw.get("path"))}
    if port:
        out["port"] = port
    for flag, default in (("flat", True), ("enabled", True), ("originals", True), ("print_sheets", False)):
        value = raw.get(flag, default)
        if not isinstance(value, bool):
            raise ValueError(f"{flag}: ja oder nein")
        out[flag] = value
    return out


def clean_password(value: Any) -> str:
    if not isinstance(value, str):
        raise ValueError("Passwort: muss Text sein")
    if _CONTROL.search(value):
        raise ValueError("Passwort: Steuerzeichen sind nicht erlaubt")
    if len(value) > LIMITS["password"]:
        raise ValueError(f"Passwort: höchstens {LIMITS['password']} Zeichen")
    return value


def seal(password: str, public_key_b64: str) -> str:
    """The password, readable only with the booth's private key. Nothing of it stays here."""
    from nacl.public import PublicKey, SealedBox
    box = SealedBox(PublicKey(base64.b64decode(public_key_b64, validate=True)))
    return base64.b64encode(box.encrypt(password.encode("utf-8"))).decode("ascii")


# ── What the booth reports ───────────────────────────────────

def clean_report(raw: Any) -> Dict[str, Any]:
    """The heartbeat's ``second_target``: known keys, capped, and never a password."""
    if not isinstance(raw, dict):
        return {}
    out: Dict[str, Any] = {"v": int(raw.get("v") or 1) if str(raw.get("v") or "1").isdigit() else 1,
                           "allowed": raw.get("allowed") is True, "configured": raw.get("configured") is True}
    pk = str(raw.get("public_key") or "")
    # A booth from before 17.11.2026 (evening) publishes its key only while
    # its old switch is on; a newer one always. The key is all that counts.
    if _B64.match(pk):
        try:
            if len(base64.b64decode(pk)) == 32:
                out["public_key"] = pk
                out["key_id"] = re.sub(r"[^0-9a-f]", "", str(raw.get("key_id") or ""))[:16]
        except ValueError:
            pass
    if out["configured"]:
        # The operator's own data, shown back in the form: not redacted (a
        # Picdrop user is often an e-mail address, and a masked one would be
        # saved back masked), only stripped of control characters.
        for key in ("type", "host", "user", "path"):
            out[key] = _CONTROL.sub("", str(raw.get(key) or ""))[:LIMITS.get(key, 20)]
        if out["type"] not in TYPES:
            out["type"] = ""
        try:
            port = int(raw.get("port") or 0)
        except (TypeError, ValueError):
            port = 0
        out["port"] = port if 0 <= port <= 65535 else 0
        for flag in ("enabled", "flat", "originals", "print_sheets", "password_set", "key_file",
                     "awaiting_password"):
            out[flag] = raw.get(flag) is True
        # The SFTP server's key: the pinned fingerprint and a different one it
        # showed since (A-3c). Fingerprints only; anything else is dropped.
        for key in ("host_key", "host_key_seen"):
            hk = raw.get(key)
            if isinstance(hk, dict) and FINGERPRINT.match(str(hk.get("sha256") or "")):
                try:
                    at = int(hk.get("at") or 0)
                except (TypeError, ValueError):
                    at = 0
                out[key] = {"sha256": str(hk["sha256"]), "type": re.sub(r"[^a-z0-9@.-]", "", str(hk.get("type") or ""))[:40],
                            "at": at}
    check = raw.get("check")
    if isinstance(check, dict):
        try:
            at = float(check.get("at") or 0)
        except (TypeError, ValueError):
            at = 0.0
        out["check"] = {"ok": check.get("ok") is True, "message": redact_free(str(check.get("message") or ""))[:300],
                        "at": at}
    return out


def redact_free(text: str) -> str:
    from app import redact
    return _CONTROL.sub(" ", redact.text(text))


def clean_counts(raw: Any) -> Dict[str, Dict[str, int]]:
    """``upload_targets``: per target sent / waiting / failed, at most 8 targets."""
    out: Dict[str, Dict[str, int]] = {}
    if not isinstance(raw, dict):
        return out
    for name, c in list(raw.items())[:8]:
        if not isinstance(c, dict):
            continue
        clean = re.sub(r"[^A-Za-z0-9_.-]", "", str(name))[:40]
        if not clean:
            continue
        entry = {}
        for k in ("done", "pending", "failed"):
            try:
                entry[k] = max(0, min(10_000_000, int(c.get(k) or 0)))
            except (TypeError, ValueError):
                entry[k] = 0
        out[clean] = entry
    return out


def state(status: Dict[str, Any]) -> Dict[str, Any]:
    """What the page and MCP show: whether the booth can take it, and what is set."""
    rep = status.get("second_target") if isinstance(status, dict) else None
    if not isinstance(rep, dict) or not rep:
        return {"supported": False, "allowed": False, "configured": False}
    out = {**rep, "supported": True}
    # Older booths (before 17.11.2026, evening) still refuse unless their own
    # switch is on; they report "allowed" False and no key, and the card says so.
    out["can_set"] = bool(rep.get("public_key"))
    out["old_booth_off"] = not out["can_set"] and rep.get("allowed") is False
    return out


# ── Jobs ────────────────────────────────────────────────────

def set_job(device_id: int, status: Dict[str, Any], fields: Optional[Dict[str, Any]] = None,
            password: Optional[str] = None, clear: bool = False) -> int:
    """Queue ``set_second_target``. The password is sealed here and only the ciphertext is stored.

    ``password`` None or "" keeps the stored one at the booth. Older open set
    jobs for the same booth are replaced (and emptied) — the newest wins.
    """
    from app import ecosystem
    st = state(status)
    if not st["supported"]:
        raise LookupError("Diese Box meldet kein zweites Ziel — ihre Fassung ist zu alt.")
    if not st.get("can_set"):
        raise LookupError("Die Box hat noch keinen Schlüssel gemeldet — ihre Fassung ist älter als vom "
                          "17.11.2026 oder sie war noch nicht online.")
    payload: Dict[str, Any] = {"v": 1}
    if clear:
        payload["action"] = "clear"
    else:
        payload["action"] = "set"
        payload["target"] = clean_fields(fields)
        if password:
            if not st.get("public_key"):
                raise PermissionError("Die Box hat noch keinen Schlüssel gemeldet — kurz warten, dann neu laden.")
            payload["password_sealed"] = seal(clean_password(password), st["public_key"])
            payload["key_id"] = st.get("key_id") or ""
    supersede_open(device_id)
    return ecosystem.add_job(int(device_id), "set_second_target", payload)


def accept_host_key_job(device_id: int, status: Dict[str, Any], fingerprint: str) -> int:
    """Queue ``accept_second_target_host_key``: only the fingerprint the booth reports the server showed.

    A separate, explicit action — saving the form never accepts a key.
    """
    from app import ecosystem
    st = state(status)
    if not st["supported"]:
        raise LookupError("Diese Box meldet kein zweites Ziel — ihre Fassung ist zu alt.")
    if not st.get("can_set"):
        raise LookupError("Diese Box nimmt das von hier nicht an (ältere Fassung mit Schalter an der Box).")
    seen = (st.get("host_key_seen") or {}).get("sha256")
    if not seen or not FINGERPRINT.match(str(fingerprint or "")) or fingerprint != seen:
        raise ValueError("Dieser Schlüssel ist nicht der, den die Box zuletzt vom Server gemeldet hat.")
    return ecosystem.add_job(int(device_id), "accept_second_target_host_key", {"v": 1, "fingerprint": seen})


def supersede_open(device_id: int) -> None:
    from app import db
    con = db.get_db()
    con.execute("UPDATE device_jobs SET done_at=datetime('now'), ok=0, result=?, expired=1, payload_json='{}' "
                "WHERE device_id=? AND type='set_second_target' AND done_at IS NULL",
                ("ersetzt durch eine neuere Änderung", int(device_id)))
    con.commit()
    con.close()


def clear_answered_payloads(con) -> None:
    """Empty the sealed payloads of answered and expired jobs. Called where jobs finish or expire."""
    marks = ",".join("?" * len(SEALED_JOBS))
    con.execute(f"UPDATE device_jobs SET payload_json='{{}}' WHERE type IN ({marks}) AND done_at IS NOT NULL "
                "AND payload_json != '{}'", tuple(sorted(SEALED_JOBS)))


def masked_payload(row: Dict[str, Any]) -> Dict[str, Any]:
    """A job row for a page or an assistant: a sealed password shows as dots, never as ciphertext."""
    if row.get("type") not in SEALED_JOBS or not row.get("payload_json"):
        return row
    try:
        p = json.loads(row["payload_json"])
    except (TypeError, ValueError):
        return {**row, "payload_json": "{}"}
    if row.get("type") == "set_sync_config" and isinstance(p, dict):
        from app import sync_secrets
        return {**row, "payload_json": json.dumps(sync_secrets.mask(p))}
    if isinstance(p, dict) and "password_sealed" in p:
        p["password_sealed"] = "••••"
    return {**row, "payload_json": json.dumps(p)}


# ── What changed, for the change log and the card (rule B4) ─────────────

#: Field names as the change log and the card say them.
FIELD_LABELS = {"type": "Art", "host": "Server", "port": "Port", "user": "Benutzer", "path": "Ordner",
                "flat": "ein Ordner", "enabled": "ein/aus", "originals": "Originale", "print_sheets": "Druckblätter"}
ENDPOINT = ("type", "host", "port", "user")
AUDIT_PREFIX = "Zweites Ziel"


def _norm(key: str, value: Any) -> Any:
    if key == "port":
        try:
            return int(value or 0)
        except (TypeError, ValueError):
            return 0
    if key in ("flat", "enabled", "originals"):
        return value is not False
    if key == "print_sheets":
        return bool(value)
    return str(value or "").strip().lower() if key in ("type", "host") else str(value or "")


def changed_fields(current: Dict[str, Any], fields: Dict[str, Any]) -> list:
    """The fields a change sets differently from what the booth reports (all of them for a new target)."""
    if not current.get("configured"):
        return [k for k in FIELDS if k in fields]
    return [k for k in FIELDS if k in fields and _norm(k, current.get(k)) != _norm(k, fields.get(k))]


def drops_password(current: Dict[str, Any], fields: Dict[str, Any], password_given: bool) -> bool:
    """True when the booth will delete the stored password and wait (rule B2) — said before saving."""
    return not password_given and (not current.get("configured")
                                   or any(k in changed_fields(current, fields) for k in ENDPOINT))


def audit_text(device_id: int, current: Dict[str, Any], fields: Optional[Dict[str, Any]], *,
               password_given: bool = False, source: str = "Formular", action: str = "set") -> str:
    """One change-log line: device, source, which fields — never a password."""
    head = f"{AUDIT_PREFIX} · Box #{int(device_id)} · {source}"
    if action == "clear":
        return f"{head}: entfernt (Zugang gelöscht)"
    if action == "accept_host_key":
        return f"{head}: neuer SFTP-Server-Schlüssel angenommen"
    names = [FIELD_LABELS[k] for k in changed_fields(current, fields or {})] or ["nichts geändert"]
    if password_given:
        pw = "neues Passwort (verschlossen)"
    elif drops_password(current, fields or {}, False):
        pw = "Passwort verworfen, wartet auf ein neues"
    else:
        pw = "Passwort unverändert"
    return f"{head}: {', '.join(names)}; {pw}"[:240]


def last_changes(device_id: int, limit: int = 3) -> list:
    """The newest change-log lines of this booth's second target, for "zuletzt geändert"."""
    from app import db
    con = db.get_db()
    try:
        rows = con.execute("SELECT at, wer, was FROM admin_vorgaenge WHERE was LIKE ? ORDER BY id DESC LIMIT ?",
                           (f"{AUDIT_PREFIX} · Box #{int(device_id)} · %", int(limit))).fetchall()
    except Exception:                       # noqa: BLE001 — no change log yet, nothing to show
        rows = []
    finally:
        con.close()
    out = []
    for r in rows:
        was = str(r["was"])
        out.append({"at": str(r["at"]), "wer": str(r["wer"]), "was": was.split(" · ", 2)[-1]})
    return out


def path_example(path: Any, flat: bool, event: Optional[Dict[str, Any]] = None) -> Dict[str, str]:
    """The line under the folder field: where a picture lands, with the booth's current event if known."""
    from app import zielordner
    ev = event or {}
    try:
        text = zielordner.example(path, flat, slug=ev.get("slug") or "sommerfest-2026",
                                  event_name=ev.get("name") or "Sommerfest 2026")
        return {"ok": "1", "text": text}
    except ValueError as e:
        return {"ok": "", "text": str(e)}
