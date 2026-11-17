"""Taking a gallery live and back to draft: one function for every way in.

The admin has three ways to do it (the switch in the event form, the button
in the event list, the button next to the badge on the event page) and the
MCP door a fourth (``configure_event`` with ``published``). They all end
here, so they all do the same thing:

* ``published`` and the ``status`` column move together (``live``/``draft``);
  before 17.11.2026 only the form's create path and the scheduled start kept
  ``status`` in step, the edit form and the MCP tool left it behind;
* going live from draft sends the "gallery published" notification once,
  whichever way it was switched;
* nothing else changes: the start date (``scheduled_live_at``), the expiry
  (``gallery_settings.expires_at``), the password, silent mode and the
  parent of a folder keep deciding what a guest really sees. ``notes()``
  says which of them will still hold the gallery back, so the operator is
  told instead of surprised. None of them blocks the switch.

Who may switch is the caller's business: the admin routes check the admin
session and the form token, the MCP tool checks the right ``photos``.
"""
from __future__ import annotations

from typing import Any, Dict, List, Mapping, Optional

from app import db


def notes(event: Mapping[str, Any]) -> List[Dict[str, Any]]:
    """What still decides who sees this gallery once it is live, as codes.

    ``no_password``  anyone with the link sees the pictures
    ``scheduled``    a start date in the future: a countdown page until then (``at``)
    ``expired``      the expiry date has passed: guests see the closed page (``at``)
    ``silent``       silent mode: no public gallery at all, live or not
    ``parent_draft`` a folder whose gallery is still a draft stays closed
    """
    from app import gallery_settings as gsm, schedule, silence

    event = dict(event)
    out: List[Dict[str, Any]] = []
    parent = db.get_event_by_id(int(event["parent_id"])) if event.get("parent_id") else None
    if not (event.get("password_hash") or (parent or {}).get("password_hash")):
        out.append({"code": "no_password"})
    start = schedule.coming_soon(event)
    if start:
        out.append({"code": "scheduled", "at": start.strftime("%d.%m.%Y %H:%M")})
    cfg = gsm.for_event(event)
    if schedule.seconds_left(cfg) == 0:
        until = schedule.expires_at(cfg)
        out.append({"code": "expired", "at": until.strftime("%d.%m.%Y %H:%M") if until else ""})
    if silence.is_silent(event):
        out.append({"code": "silent"})
    if parent is not None and not parent.get("published"):
        out.append({"code": "parent_draft", "parent": parent.get("name") or ""})
    return out


def gallery_url(event: Mapping[str, Any], fallback_base: str = "") -> str:
    """The address guests open: the event's own domain if it has one."""
    base = (db.base_url_for(event) or fallback_base or "").rstrip("/")
    return f"{base}/gallery/{event.get('slug')}"


def set_published(event_id: int, on: bool, *, fallback_base: str = "") -> Dict[str, Any]:
    """Switch the gallery live (``on``) or back to draft. Returns what happened.

    ``{"event": <row after>, "changed": bool, "published": bool,
       "url": <gallery address>, "notes": notes(...)}``
    """
    event = db.get_event_by_id(int(event_id))
    if not event:
        raise LookupError(event_id)
    was = bool(event.get("published"))
    on = bool(on)
    db.update_event(int(event_id), published=1 if on else 0, status="live" if on else "draft")
    after = db.get_event_by_id(int(event_id)) or event
    url = gallery_url(after, fallback_base)
    if on and not was:
        from app import notifier
        notifier.notify_event_published(after.get("name") or "", url)
    return {"event": after, "changed": was != on, "published": on, "url": url,
            "notes": notes(after) if on else []}


def note_text(note: Mapping[str, Any], t=None) -> str:
    """One note in the admin's language."""
    if t is None:
        from app.i18n import t
    return t("publish.note." + note["code"], at=note.get("at", ""), parent=note.get("parent", ""))


def confirm_needed(event: Optional[Mapping[str, Any]]) -> bool:
    """Going live without a password gets a confirmation step, never a block."""
    return bool(event) and any(n["code"] == "no_password" for n in notes(event))
