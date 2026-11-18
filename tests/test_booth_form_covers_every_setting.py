"""Every setting the booth knows has a field in the gallery form.

The operator sets a box up from the gallery, often from a phone at the venue.
A setting that exists only at the box is a setting that cannot be reached from
there, and a profile key without a field is replaced at the next sync (the booth
takes the gallery's profile whole). This file fails the moment someone adds a
key to the profile, or to the keys the gallery may change on the box, without
adding the field. Keys that are deliberately not in the form are listed below
with the reason: the list is short on purpose, and every entry is a decision.
"""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "booth"))

#: Blocks that are one value as far as the form is concerned.
_ONE_VALUE = {"positions", "texts", "keys_actions", "note"}


def _leaves(block, prefix=""):
    out = {}
    for key, value in block.items():
        path = f"{prefix}{key}"
        if isinstance(value, dict) and value and key not in _ONE_VALUE:
            out.update(_leaves(value, path + "."))
        else:
            out[path] = value
    return out


def _fields():
    """Profile key -> one form field that carries it (as rendered)."""
    f = {
        "modes": "mode_photo", "formats": "format_4:5", "default_format": "default_format",
        "languages": "languages", "retake": "retake", "review_seconds": "review_seconds",
        "looks": "look_original", "looks_mode": "looks_mode", "looks_max": "looks_max",
        "looks_auto": "looks_auto", "upload_originals": "upload_originals",
        "qr_url_template": "qr_url_template", "qr_on_screen": "qr_on_screen",
        "result_seconds": "result_seconds", "idle_seconds": "idle_seconds",
        "gallery_on_booth": "gallery_on_booth", "allow_delete": "allow_delete",
        "disclaimer.enabled": "disclaimer_enabled", "accent": "theme_accent",
        "backgrounds": "backgrounds", "collage_templates": "collage_templates",
        "collage_builtins": "collage_builtins", "collage_upload": "collage_upload",
        "collage_plain_qr": "collage_plain_qr", "collage_exact": "collage_exact",
        "collage_fit": "collage_fit", "texts": "texts_json",
        "default_format": "default_format",
    }
    f.update({f"countdown.{k}": n for k, n in {
        "first": "cd_first", "next": "cd_next", "style": "cd_style", "review_seconds": "cd_review",
        "sound": "cd_sound", "live_view": "cd_live_view", "crop_guide": "cd_crop_guide",
        "smile_media": "cd_smile_media", "smile_ms": "cd_smile_ms", "smile_text": "cd_smile_text",
        "poses": "cd_poses", "cancel": "cd_cancel", "display": "cd_display", "color": "cd_color",
        "size": "cd_size"}.items()})
    f.update({"guest_text.enabled": "gt_enabled", "guest_text.max_len": "gt_max_len",
              "guest_text.required": "gt_required", "guest_text.modes": "gt_mode_photo",
              "draw.enabled": "draw_enabled", "draw.colors": "draw_colors", "draw.modes": "draw_mode_photo"})
    for clip in ("boomerang", "video"):
        for k in ("format", "layout", "seconds", "fps", "scale", "source"):
            f[f"{clip}.{k}"] = f"{clip}_{k}"
    f.update({"boomerang.loops": "boomerang_loops", "boomerang.guest_choice": "boomerang_guest_choice",
              "boomerang.print": "boomerang_print",
              # The lengths the guest may choose (18.11.2026).
              "boomerang.seconds_choices": "boomerang_seconds_choices",
              "boomerang.seconds_guest_choice": "boomerang_seconds_guest_choice",
              "boomerang.min_total_seconds": "boomerang_min_total",
              "boomerang.max_total_seconds": "boomerang_max_total"})
    f["gif.guest_choice"] = "gif_guest_choice"     # the GIF set up like the boomerang (18.11.2026)
    for clip in ("boomerang", "video", "gif"):   # frames ("Rahmen", 17.11.2026; GIF 18.11.2026)
        for k in ("frames", "frame_templates", "frame_builtins", "frame_none", "frame_default",
                  "frame_guest_choice"):
            f[f"{clip}.{k}"] = f"{clip}_{k}"
    f.update({f"gif.{k}": n for k, n in {
        "shots": "gif_shots", "interval": "gif_interval", "frame_ms": "gif_frame_ms",
        "video_seconds": "gif_video_seconds", "pingpong": "gif_pingpong", "also_mp4": "gif_mp4",
        "format": "gif_format", "scale": "gif_scale", "from_collage": "gif_from_collage",
        "from_collage_min": "gif_from_collage_min", "print_template": "gif_print_template"}.items()})
    f.update({f"gif.boomerang.{k}": n for k, n in {
        "enabled": "gifboom_enabled", "format": "gifboom_format", "layout": "gifboom_layout",
        "fps": "gifboom_fps", "loops": "gifboom_loops", "guest_choice": "gifboom_choice"}.items()})
    f.update({f"guest_choice.{k}": n for k, n in {
        "format": "choice_format", "look": "choice_look", "collage_template": "choice_template",
        "background": "choice_background"}.items()})
    f.update({f"luts.{k}": n for k, n in {
        "enabled": "luts_enabled", "strength": "lut_strength", "show": "lut_show", "baked": "lut_baked",
        "baked_strength": "lut_baked_strength"}.items()})
    f.update({f"chroma.{k}": f"chroma_{k}" for k in
              ("enabled", "guest_choice", "color", "tolerance", "softness", "spill", "backgrounds")})
    f.update({f"mail.{k}": f"mail_{k}" for k in
              ("transport", "enabled", "host", "port", "security", "user", "password", "from_addr",
               "from_name", "subject", "body", "attach", "dry_run")})
    f.update({f"privacy.{k}": f"privacy_{k}" for k in ("mode", "delete_after", "delete_seconds", "notice")})
    f.update({"mirror.horizontal": "mirror_horizontal", "mirror.vertical": "mirror_vertical",
              "qr.mode": "qr_mode", "qr.url": "qr_url", "qr.utm": "qr_utm"})
    f.update({f"wifi_share.{k}": n for k, n in {
        "enabled": "wifi_enabled", "mode": "wifi_mode", "ssid": "wifi_ssid", "password": "wifi_password",
        "security": "wifi_security", "hidden": "wifi_hidden", "link_hours": "wifi_link_hours"}.items()})
    f.update({"frame.enabled": "frame_enabled", "frame.overlay": "frame_overlay",
              "frame.print_overlay": "frame_print_overlay", "frame.on_photo": "frame_on_photo",
              "frame.on_print": "frame_on_print", "frame.texts": "frame_text0_value",
              "print_qr.size": "print_qr_size", "print_qr.position": "print_qr_position",
              "print_qr.margin": "print_qr_margin", "sounds.enabled": "sounds_enabled",
              "sounds.volume": "sounds_volume"})
    f.update({f"sounds.{k}": f"sound_{k}" for k in ("countdown", "shutter", "done", "print")})
    f.update({f"attract.{k}": n for k, n in {
        "enabled": "attract_enabled", "after_seconds": "attract_after", "source": "attract_source",
        "media": "attract_media", "seconds_per_item": "attract_seconds", "show_hint": "attract_show_hint",
        "hint_style": "attract_hint_style", "color": "attract_color", "text_color": "attract_text_color",
        "dim": "attract_dim", "fit": "attract_fit", "mute": "attract_mute",
        "hint_size": "attract_hint_size"}.items()})
    f.update({"webhooks.enabled": "webhooks_enabled", "webhooks.url": "webhook_url",
              "webhooks.secret": "webhook_secret", "webhooks.events": "webhook_event_media_ready",
              "http_trigger.enabled": "http_trigger_enabled", "http_trigger.token": "http_trigger_token",
              "http_trigger.mode": "http_trigger_mode"})
    f.update({f"camera.{k}": f"cam_{k}" for k in
              ("iso", "aperture", "shutter", "wb", "preview_iso", "preview_aperture", "preview_shutter",
               "preview_iso_max", "preview_shutter_min")})
    f.update({f"start.{k}": n for k, n in {
        "media": "start_media", "media_seconds": "start_media_seconds", "positions": "start_positions",
        "layout": "start_layout", "button_size": "button_size", "show_gallery_button": "show_gallery_button",
        "show_mode_hints": "show_mode_hints", "steps": "start_steps", "show_last": "start_last",
        "show_qr": "start_qr", "logo": "start_logo", "logo_size": "start_logo_size",
        "bg_color": "start_bg_color", "text_tone": "start_text_tone", "dim": "start_dim", "show_title": "start_show_title",
        "show_status": "start_show_status", "fit": "start_fit", "gallery_style": "gallery_style",
        "corner_size": "corner_size"}.items()})
    for screen in ("setup", "capture", "result"):
        for k in ("image", "color", "dim"):
            f[f"backdrops.{screen}.{k}"] = f"bd_{screen}_{k}"
    for phase in ("processing", "between", "after", "error"):
        for k in ("media", "fit", "text", "loop"):
            f[f"phase_media.{phase}.{k}"] = f"pm_{phase}_{k}"
    f["phase_media.after.seconds"] = "pm_after_seconds"
    f.update({f"collage_style.{k}": f"cs_{k}" for k in
              ("preset", "background", "ink", "muted", "accent", "headline", "subline", "font", "radius", "frame")})
    f.update({f"print.{k}": n for k, n in {
        "reprint_from_gallery": "reprint_from_gallery", "enabled": "print_enabled", "auto": "print_auto",
        "copies_max": "copies_max", "limit_session": "limit_session", "limit_event": "limit_event",
        "qr_on_print": "qr_on_print", "seconds_per_print": "seconds_per_print",
        "sheets_per_cassette": "sheets_per_cassette", "sharpen": "print_sharpen",
        "number_keys": "print_number_keys", "queue_while_empty": "print_queue_while_empty",
        "queue_max": "print_queue_max", "queue_max_hours": "print_queue_max_hours",
        "fit_to_paper": "print_fit_to_paper"}.items()})
    f.update({f"print_layout.{k}": n for k, n in {
        "paper": "paper", "per_sheet": "per_sheet", "margin_mm": "print_margin", "dpi": "print_dpi",
        "fit": "print_fit", "rotate_to_fit": "rotate_to_fit", "background": "print_bg"}.items()})
    f.update({f"flash.{k}": n for k, n in {
        "enabled": "flash_enabled", "recycle_seconds": "flash_recycle", "preview_gain": "flash_gain",
        "preview_gain_auto": "flash_gain_auto", "expected_mode": "flash_mode"}.items()})
    f.update({f"screen_flash.{k}": n for k, n in {
        "enabled": "sf_enabled", "brightness": "sf_brightness", "lead_ms": "sf_lead",
        "hold_ms": "sf_hold", "color": "sf_color"}.items()})
    f.update({"consent.enabled": "consent_enabled", "consent.print_only": "consent_print_only",
              "consent.days": "consent_days", "consent.note": "consent_note_de"})
    f.update({"access.pin": "access_pin", "access.lock_on_booth": "lock_on_booth",
              "access.hotspot_seconds": "hotspot_seconds", "access.lock_icon": "lock_icon"})
    f.update({f"controls.{k}": n for k, n in {
        "touch": "ctl_touch", "keys_start": "keys_start", "keys_back": "keys_back", "keys_print": "keys_print",
        "mouse_start": "mouse_start", "mouse_swallow": "mouse_swallow", "hide_cursor": "hide_cursor",
        "repeat_ms": "repeat_ms", "start_mode": "start_mode", "mouse_right": "mouse_right",
        "mouse_middle": "mouse_middle", "keys_actions": "keys_actions",
        "arrow_navigation": "arrow_navigation", "onscreen_keyboard": "onscreen_keyboard"}.items()})
    f.update({"layout.safe_top": "safe_top", "layout.safe_right": "safe_right",
              "layout.safe_bottom": "safe_bottom", "layout.safe_left": "safe_left",
              "layout.scale": "ui_scale", "layout.corner": "corner", "layout.guides": "guides",
              "layout.gallery_size": "gallery_size", "layout.back_size": "back_size"})
    f.update({"lock.active": "lock_active", "lock.image": "lock_image",
              "lock.schedule.enabled": "lock_schedule", "lock.schedule.from": "lock_from",
              "lock.schedule.until": "lock_until", "lock.schedule.days": "lock_days"})
    f.update({f"theme.{k}": f"theme_{k}" for k in
              ("mode", "accent", "on_accent", "bg", "panel", "text", "muted", "radius", "size", "weight",
               "uppercase", "secondary", "font", "font_file")})
    f.update({"aufraeumen.enabled": "aufraeumen_enabled", "aufraeumen.nach_tagen": "aufraeumen_nach_tagen",
              "aufraeumen.nur_bestaetigte": "aufraeumen_nur_bestaetigte",
              "aufraeumen.behalten_mindestens": "aufraeumen_behalten_mindestens",
              "meldungen.mail.enabled": "meldungen_mail_enabled", "meldungen.mail.to": "meldungen_mail_to",
              "meldungen.mail.ab_stufe": "meldungen_mail_ab_stufe",
              "meldungen.box.enabled": "meldungen_box_enabled", "meldungen.box.ab_stufe": "meldungen_box_ab_stufe",
              "meldungen.wiederholen_minuten": "meldungen_wiederholen_minuten"})
    return f


#: Profile keys with no field of their own, and why. Do not add a key here to
#: make the test pass: a setting the operator cannot reach is the defect.
NOT_A_FIELD = {
    "boomerang.width": "fixed by the form (auto); the clip is sized by max_width and scale",
    "boomerang.max_width": "fixed by the form (1080)",
    "video.width": "fixed by the form (auto)",
    "video.max_width": "fixed by the form (1080)",
    "gif.loops": "fixed by the form (0 = endless)",
    "gif.max_width": "fixed by the form (1080)",
    "gif.boomerang.max_width": "fixed by the form (1080)",
    "gif.boomerang.scale": "fixed by the form (native)",
    "chroma.fit": "fixed by the form (cover)",
    "mail.bcc": "kept as stored: set by import or by the booth owner, not meant for daily use",
    "qr.label": "kept as stored: written by the QR page of the event, not by this form",
    "privacy.keep_originals": "nothing reads it any more; the form no longer writes it",
}

#: Settings that exist only at the box and are not on REMOTE_LOCAL_KEYS. Each
#: one names a server, a key, a PIN, an address, a path or a window the
#: operator at the box decides about; the gallery may not change them.
LOCAL_ONLY = {
    "aufraeumen_ohne_bestaetigung": "deleting unsent runs needs a yes on the box itself (second review R2-N1)",
    "event_claim": "a note the box writes itself while it has no contact",
    "template_push": "a queue the box fills itself",
    "gallery_templates": "a note the box writes itself from the gallery's last profile",
    "install_id": "made at pairing",
    "targets": "extra upload targets: addresses and keys, set at the box",
    "standalone": "operating without a gallery: decided at the box",
    "profile_hold": "keeps a profile change away from the box: only the box may say so",
    "admin_pin": "the PIN of the box itself; the gallery sets access.pin in the profile",
    "pin_overridden": "a fingerprint the box writes itself when its PIN is changed at the box",
    "profile_seed_pending": "a flag the box sets itself at pairing",
    "port": "where the box listens: set at the box",
    "lan_guest_access": "opens the box to the network: remote-control permission, set at the box",
    "window.mode": "own window or browser: set at the box",
    "window.screen": "which screen: set at the box",
    "window.on_top": "window behaviour: set at the box",
    "window.debug": "window behaviour: set at the box",
    "window.hide_console": "window behaviour: set at the box",
    "guest_share.allowed": "guest download over the LAN: set at the box",
    "guest_share.address": "an address: set at the box",
    "guest_share.port": "a port: set at the box",
    "remote_control.allowed": "consent to live remote control: only at the box, with the PIN; "
                              "the gallery must not switch it on for itself",
    "remote_control.indicator": "the kiosk mark during remote control: set at the box, so the "
                                "gallery cannot hide that it is driving the box",
    "second_target_check": "the result of the last target check, written by the box itself; the "
                           "gallery triggers it with check_second_target and shows it",
    "server.url": "where the box sends pictures: never remotely",
    "server.api_key": "a key: never remotely",
    "server.verify_tls": "a security switch: never remotely",
    "server.gallery_name": "written by pairing",
    "server.device_name": "written by pairing",
    "storage.folder": "a path: set at the box",
    "storage.usb.enabled": "USB backup: where pictures are copied is decided at the box (S-7)",
    "storage.usb.path": "USB backup: a path, set at the box",
    "storage.usb.label": "USB backup: written when a stick is trusted at the box",
    "storage.usb.originals": "USB backup: decided at the box (S-7)",
    "storage.usb.trusted": "USB backup: sticks confirmed at the box with the PIN",
    "camera.video_source": "names a device: set at the box",
    "camera.edsdk_dll": "a path to a vendor DLL: set at the box",
    "camera.ccapi_url": "an address: set at the box",
    "camera.digicam_url": "an address: set at the box",
    "camera.folder": "a path: set at the box",
    "camera.preview_webcam": "names a device: set at the box",
    "camera.trigger_url": "an address: set at the box",
    "printer.uri": "an address: set at the box",
    "printer.folder": "a path: set at the box",
    "printer.media": "paper name for network printers: set at the box",
    "printer.brightness": "the first printer is printers.1.* on the remote list",
    "printer.contrast": "the first printer is printers.1.* on the remote list",
    "printer.saturation": "the first printer is printers.1.* on the remote list",
    "printer2.backend": "obsolete, replaced by printers",
    "printer2.name": "obsolete, replaced by printers",
    "printer2.paper": "obsolete, replaced by printers",
    "printers": "the list of printers: names and addresses, set at the box",
}


def _admin(client):
    client.get("/healthz")
    from app import ecosystem
    ecosystem.init_schema()
    with client.session_transaction() as s:
        s["admin_logged_in"] = True


def test_every_profile_key_has_a_field_or_a_stated_reason():
    from app import ecosystem
    known = set(_fields()) | set(NOT_A_FIELD)
    missing = sorted(set(_leaves(ecosystem.DEFAULT_PROFILE)) - known)
    assert not missing, (
        "profile keys with no field in the gallery form (add the field and the parser, or list the key in "
        f"NOT_A_FIELD with a reason): {missing}")
    stale = sorted(known - set(_leaves(ecosystem.DEFAULT_PROFILE)))
    assert not stale, f"fields or reasons for keys that no longer exist: {stale}"


def test_every_mapped_field_is_in_the_rendered_form(client):
    _admin(client)
    from app import db
    from app import ecosystem
    dev_id = db.create_device(name="Formularbox")["id"]
    # The pickers of pictures and templates render one box per uploaded file:
    # with nothing uploaded there is no field to find.
    for n, kind in enumerate(ecosystem.ASSET_KINDS):
        ecosystem.add_asset(kind, f"Probe {kind}", f"probe-{n}.png", f"{n:064x}", 10)
    page = client.get(f"/admin/devices/{dev_id}/booth").get_data(as_text=True)
    absent = sorted({name for name in _fields().values() if f'name="{name}"' not in page})
    assert not absent, f"fields the form is supposed to carry but does not render: {absent}"


def test_every_key_the_box_can_set_remotely_has_a_field(client):
    _admin(client)
    from app import db, ecosystem
    dev_id = db.create_device(name="Hardwarebox")["id"]
    page = client.get(f"/admin/devices/{dev_id}/booth").get_data(as_text=True)
    absent = sorted(k for k in ecosystem.REMOTE_LOCAL_KEYS
                    if f'name="hw_{k.replace(".", "_")}"' not in page)
    assert not absent, f"settings the gallery may change on the box, but the form has no field for: {absent}"


def test_every_local_setting_of_the_box_is_reachable_or_stated():
    from boothcore import config as booth_config
    from app import ecosystem
    local = set(_leaves(booth_config.DEFAULT_LOCAL))
    remote = set(ecosystem.REMOTE_LOCAL_KEYS)
    # printers.N.* are entries of the "printers" list, not leaves of DEFAULT_LOCAL.
    unknown = sorted(local - remote - set(LOCAL_ONLY))
    assert not unknown, (
        "local settings of the box that the gallery can neither change nor has a stated reason for "
        f"(put the key on REMOTE_LOCAL_KEYS in both config files, or list it in LOCAL_ONLY): {unknown}")
    stale = sorted(set(LOCAL_ONLY) - local)
    assert not stale, f"LOCAL_ONLY lists keys the box no longer has: {stale}"
    assert not set(LOCAL_ONLY) & remote, "a key is on both lists"
