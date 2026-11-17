"""Every file the booth profile points at travels to the booth.

Until 0.4 the asset list was built from a hand-picked set of fields: a logo,
the "smile" picture, sounds, screensaver media, a font and frame overlays were
chosen in the form, saved in the profile — and never reached the booth, which
then silently showed nothing. The list now comes from one table of media
fields, and the schema scan below fails the day a new media field is added
without being entered there.
"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "booth"))

from boothcore import config  # noqa: E402

from app import ecosystem  # noqa: E402

# Key names that, in this schema, mean "a file". A field with such a name that
# is *not* a file goes into NOT_MEDIA with the reason.
MEDIA_NAME = re.compile(r"(^|_)(media|image|images|logo|overlay|file|files|backgrounds?|templates|"
                        r"picture|video|sound|audio|music|watermark|icon)$")
NOT_MEDIA = {
    "collage_style.background": "a colour of the collage sheet",
    "print_layout.background": "a colour around a contained print",
}


def _leaves(d, prefix=""):
    for key, value in d.items():
        path = f"{prefix}.{key}" if prefix else key
        if isinstance(value, dict) and value:
            yield from _leaves(value, path)
        else:
            yield path, value


def _covered(path: str) -> bool:
    for field in ecosystem.MEDIA_FIELDS:
        pattern = "^" + re.escape(field).replace(r"\*", "[^.]+") + "$"
        if re.match(pattern, path):
            return True
    return False


def _looks_like_media(path: str, value) -> bool:
    if isinstance(value, bool) or not (isinstance(value, (str, list)) or value is None):
        return False
    parts = path.split(".")
    if parts[0] == "sounds":
        return True
    return bool(MEDIA_NAME.search(parts[-1]))


def test_every_media_field_of_the_schema_is_in_the_table():
    missing = []
    for name, profile in (("gallery", ecosystem.DEFAULT_PROFILE), ("booth", config.DEFAULT_PROFILE)):
        for path, value in _leaves(profile):
            if path in NOT_MEDIA or path.startswith(("event.", "texts.")):
                continue
            if _looks_like_media(path, value) and not _covered(path):
                missing.append(f"{name}: {path}")
    assert not missing, ("profile fields that look like files but are not in MEDIA_FIELDS "
                         f"(add them, or to NOT_MEDIA with a reason): {missing}")


def test_gallery_and_booth_share_one_table():
    assert tuple(ecosystem.MEDIA_FIELDS) == tuple(config.MEDIA_FIELDS)


def test_every_file_the_profile_names_is_sent(monkeypatch):
    names = ["logo.png", "laecheln.gif", "piep.mp3", "klick.wav", "fertig.mp3", "druck.mp3",
             "schoner.mp4", "schrift.woff2", "rahmen.png", "druckrahmen.png", "pause.jpg",
             "start.jpg", "nacht.jpg", "wand.jpg", "szene.jpg", "vorlage.json", "fremd.png"]
    rows = [{"file": n, "kind": "start", "sha256": "0" * 64} for n in names]
    monkeypatch.setattr(ecosystem, "list_assets",
                        lambda kind=None: [r for r in rows if not kind or r["kind"] == kind])
    profile = ecosystem.deep_merge(ecosystem.DEFAULT_PROFILE, {
        "start": {"logo": "logo.png", "media": ["start.jpg"]},
        "countdown": {"smile_media": "laecheln.gif"},
        "sounds": {"countdown": "piep.mp3", "shutter": "klick.wav", "done": "fertig.mp3", "print": "druck.mp3"},
        "attract": {"media": ["schoner.mp4"]},
        "theme": {"font_file": "schrift.woff2"},
        "frame": {"overlay": "rahmen.png", "print_overlay": "druckrahmen.png"},
        "lock": {"image": "pause.jpg"},
        "backdrops": {"setup": {"image": "nacht.jpg"}},
        "backgrounds": ["wand.jpg"],
        "chroma": {"backgrounds": ["szene.jpg", "#00ff00"]},
        "collage_templates": ["vorlage.json"],
    })
    sent = {a["file"] for a in ecosystem.assets_for_profile(profile)}
    assert sent == set(names) - {"fremd.png"}


def test_media_files_lists_names_not_colours():
    profile = {"chroma": {"backgrounds": ["#112233", "wiese.jpg"]}, "lock": {"image": None},
               "backdrops": {"setup": {"image": "a.jpg"}, "result": {"image": ""}}}
    assert ecosystem.media_files(profile) == {"wiese.jpg": "chroma.backgrounds", "a.jpg": "backdrops.setup.image"}
    assert ecosystem.media_files(profile) == config.media_files(profile)
