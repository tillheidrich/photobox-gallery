"""Plugin-System (pluggy).

Ein Plugin ist eine Python-Datei im Ordner ``<data>/plugins`` oder ein Paket mit
Entry-Point-Gruppe ``photobox_booth``. Es implementiert beliebig viele Hooks:

    from boothcore.plugins import hookimpl

    @hookimpl
    def booth_media_ready(session, media):
        ...

Plugins laufen im Prozess der Box. Ein Fehler in einem Plugin wird geloggt und
darf nie einen Gast-Durchgang abbrechen.
"""
from __future__ import annotations

import importlib.util
import logging
from pathlib import Path
from typing import Any

import pluggy

log = logging.getLogger("booth.plugins")

hookspec = pluggy.HookspecMarker("photobox_booth")
hookimpl = pluggy.HookimplMarker("photobox_booth")


class BoothSpec:
    @hookspec
    def booth_startup(self, app: Any, booth: Any) -> None:
        """Box ist gestartet. ``app`` ist die FastAPI-App (eigene Routen möglich)."""

    @hookspec
    def booth_register_looks(self) -> dict:
        """Zusätzliche Looks: {"name": fn(Image) -> Image}."""

    @hookspec
    def booth_register_cameras(self) -> dict:
        """Zusätzliche Kamera-Anschlüsse: ``{"meinname": lambda cfg: MeinBackend(...)}``.
        In der lokalen Konfiguration dann ``camera.backend = "meinname"``."""

    @hookspec
    def booth_register_templates(self) -> list:
        """Zusätzliche Collage-Vorlagen (JSON-Dicts)."""

    @hookspec
    def booth_session_started(self, session: Any) -> None:
        """Gast hat einen Durchgang begonnen."""

    @hookspec
    def booth_shot_taken(self, session: Any, path: Path) -> None:
        """Ein Einzelbild ist aufgenommen."""

    @hookspec
    def booth_media_ready(self, session: Any, media: dict) -> None:
        """Endergebnis (Foto, Collage, Boomerang) liegt vor."""

    @hookspec
    def booth_printed(self, session_uid: str, copies: int) -> None:
        """Ein Druck ist abgeschlossen."""

    @hookspec
    def booth_uploaded(self, item: dict) -> None:
        """Eine Datei wurde erfolgreich synchronisiert."""


def load(plugin_dir: Path) -> pluggy.PluginManager:
    pm = pluggy.PluginManager("photobox_booth")
    pm.add_hookspecs(BoothSpec)
    try:
        pm.load_setuptools_entrypoints("photobox_booth")
    except Exception as e:  # pragma: no cover
        log.warning("Entry-Point-Plugins nicht geladen: %s", e)
    plugin_dir.mkdir(parents=True, exist_ok=True)
    for f in sorted(plugin_dir.glob("*.py")):
        try:
            spec = importlib.util.spec_from_file_location(f"booth_plugin_{f.stem}", f)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)  # type: ignore[union-attr]
            pm.register(mod, name=f.stem)
            log.info("Plugin geladen: %s", f.name)
        except Exception as e:
            log.error("Plugin %s fehlerhaft, übersprungen: %s", f.name, e)
    return pm


def safe_call(pm: pluggy.PluginManager, hook: str, **kwargs):
    try:
        return getattr(pm.hook, hook)(**kwargs)
    except Exception as e:
        log.error("Plugin-Hook %s fehlgeschlagen: %s", hook, e)
        return []
