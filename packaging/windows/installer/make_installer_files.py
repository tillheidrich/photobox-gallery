#!/usr/bin/env python3
"""Everything photobox.iss reads that is generated, written into installer/build/.

    python packaging/windows/installer/make_installer_files.py

* wizard images (BMP, 100 % and 200 %) from the program icon - no new design,
  the existing mark on the brand's paper and ink colours;
* licence pages (German and English): a short note, LICENSE-EXCEPTION and
  the unmodified LICENSE;
* the "what this program does" page, built from tools/outbound_connections.py
  so that it cannot say something the code does not do;
* publisher.isi: publisher name and URLs for the installer, from
  packaging/windows/publisher.json (git-ignored) over publisher.example.json.

THIRD-PARTY-NOTICES.txt is written by tools/third_party_notices.py (build.ps1
runs it first, in the build environment, because it reads the packages there).
Runs anywhere Pillow is installed; nothing here needs Windows.
"""
from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
OUT = HERE / "build"
sys.path.insert(0, str(ROOT / "tools"))

import outbound_connections as oc          # noqa: E402
import third_party_notices as tpn          # noqa: E402

INK = (29, 26, 22)          # #1d1a16, the mark's dark
AMBER = (216, 149, 52)      # #d89534, the mark's amber
PAPER = (254, 253, 252)     # #fefdfc

# Not legal advice and not part of the licence: says where the licence's own
# disclaimer ends under German law, without promising anything beyond it.
NOTE_DE = """\
Photobox - Lizenz
=================

Photobox ist freie Software. Sie dürfen das Programm nach den Bedingungen der
GNU Affero General Public License, Version 3 oder (nach Ihrer Wahl) jeder
späteren Version, nutzen, weitergeben und verändern. Verbindlich ist der
englische Lizenztext unten; dazu kommt die Zusatzerlaubnis nach Abschnitt 7
(Canon EDSDK) aus LICENSE-EXCEPTION.

Copyright (C) {year} {publisher} und die Mitwirkenden des Projekts.
Quelltext: {project_url}

Hinweis zur Haftung (kein Rechtsrat, nicht Teil der Lizenz): Die Ausschlüsse
von Gewährleistung und Haftung in den Abschnitten 15 und 16 der Lizenz gelten
nur, soweit das anwendbare Recht sie zulässt. Nach deutschem Recht kann
insbesondere die Haftung für Vorsatz und grobe Fahrlässigkeit nicht
ausgeschlossen werden; zwingende gesetzliche Haftung bleibt unberührt.

Mit „Ich akzeptiere“ bestätigen Sie, diese Bedingungen gelesen zu haben. Für
die bloße Nutzung des Programms verlangt die AGPL keine Zustimmung; sie
regelt Weitergabe und Veränderung.
"""
NOTE_EN = """\
Photobox - licence
==================

Photobox is free software. You may use, share and modify it under the terms
of the GNU Affero General Public License, version 3 or (at your option) any
later version, below, together with the additional permission under section 7
(Canon EDSDK) in LICENSE-EXCEPTION.

Copyright (C) {year} {publisher} and the project's contributors.
Source code: {project_url}

Note on liability (not legal advice, not part of the licence): the disclaimers
in sections 15 and 16 apply only to the extent permitted by applicable law.
Under German law, for example, liability for intent and gross negligence
cannot be excluded; mandatory statutory liability is not affected.

"I accept" confirms that you have read these terms. The AGPL does not require
acceptance for merely running the program; it governs sharing and modifying.
"""

PRIVACY_DE = """\
Was dieses Programm tut
=======================

Photobox läuft vollständig auf diesem Rechner. Für Aufnahme, Druck und
Anzeige braucht es weder Internet noch ein Konto.

Was gespeichert wird und wo
  - Aufnahmen, Warteschlange, Protokolle und Einstellungen liegen im
    Datenordner des Windows-Benutzers: %LOCALAPPDATA%\\PhotoboxBooth
    (profile.json, local.json, sessions\\, logs\\). Das Installieren und
    Deinstallieren lässt diesen Ordner unangetastet, außer Sie wählen beim
    Deinstallieren ausdrücklich „löschen“.
  - Auf Wunsch zusätzlich eine Kopie in einem zweiten Ordner und auf einem
    USB-Stick (Verwaltung → Ablage). Alte Aufnahmen räumt die Box nur auf, wenn
    „Aufräumen“ eingeschaltet ist (ab Werk aus).

Netz
  - Die Oberfläche der Box ist nur auf diesem Rechner erreichbar
    (127.0.0.1, Port 8765). Dafür ist keine Firewall-Regel nötig.
  - Von sich aus baut Photobox keine Verbindung ins Internet auf. Jede der
    folgenden Verbindungen entsteht erst, wenn der Betreiber sie einschaltet
    [Kennung; Schalter]:
{connections}
  - Nie: {never}.
  - QR-Codes werden auf diesem Rechner erzeugt. Sie enthalten die Adresse der
    Galerie oder eine eigene Adresse, die Sie eintragen.

Was nicht in unserer Hand liegt
  - Das Programmfenster zeigt die Box mit der Microsoft Edge WebView2-Laufzeit
    von Windows. Fällt es auf Chrome oder Edge zurück, läuft dieser Browser mit
    seinen eigenen Einstellungen. Windows, die WebView2-Laufzeit und Browser
    können eigene Verbindungen zu Microsoft bzw. Google aufbauen (etwa für
    Updates); darauf hat Photobox keinen Einfluss.

Wer mit der Box Gäste fotografiert, ist für deren Daten verantwortlich
(Datenschutz-Grundverordnung). Eine Anleitung mit Aushang-Vorlage liegt nach
der Installation im Programmordner unter docs\\DATENSCHUTZ-BOX.md.
"""
PRIVACY_EN = """\
What this program does
======================

Photobox runs entirely on this PC. Taking, printing and showing photos needs
neither internet nor an account.

What is stored and where
  - Photos, queue, logs and settings are kept in the Windows user's data
    folder: %LOCALAPPDATA%\\PhotoboxBooth (profile.json, local.json,
    sessions\\, logs\\). Installing and uninstalling leave this folder alone
    unless you explicitly choose "delete" when uninstalling.
  - Optionally a copy in a second folder and on a USB stick (admin → storage).
    Old photos are only cleaned up when "clean-up" is switched on (off by
    default).

Network
  - The booth's interface is reachable from this PC only (127.0.0.1, port
    8765). No firewall rule is needed for it.
  - Photobox opens no internet connection on its own. Each of the following
    connections only exists once the operator switches it on [id; switch]:
{connections}
  - Never: {never}.
  - QR codes are generated on this PC. They contain the gallery's address or
    an address of your own.

Outside our control
  - The program window shows the booth with Windows' Microsoft Edge WebView2
    Runtime. If it falls back to Chrome or Edge, that browser runs with its own
    settings. Windows, the WebView2 Runtime and browsers may open connections
    of their own to Microsoft or Google (for updates, for example); Photobox
    has no influence on that.

Whoever photographs guests with the booth is responsible for their data
(GDPR). A guide with a notice template is installed in the program folder as
docs\\PRIVACY-BOOTH.md.
"""


def write_text(path: Path, text: str) -> None:
    # UTF-8 with BOM and CRLF: Inno Setup shows the file as it is.
    path.write_text(text.replace("\r\n", "\n"), encoding="utf-8-sig", newline="\r\n")


def wizard_images(out: Path) -> list:
    from PIL import Image, ImageDraw

    icon = Image.open(ROOT / "packaging" / "windows" / "photobox.ico")
    icon.size = max(icon.info.get("sizes", {(256, 256)}))
    mark = icon.convert("RGBA")
    made = []
    for scale in (1, 2):
        # Large image: left of the welcome and finish pages, 164 x 314 at 100 %.
        w, h = 164 * scale, 314 * scale
        img = Image.new("RGB", (w, h), INK)
        draw = ImageDraw.Draw(img)
        draw.rectangle([0, h - 6 * scale, w, h], fill=AMBER)
        size = 112 * scale
        m = mark.resize((size, size), Image.LANCZOS)
        img.paste(m, ((w - size) // 2, 84 * scale), m)
        name = out / f"wizard-large-{scale * 100}.bmp"
        img.save(name, "BMP")
        made.append(name)
        # Small image: top right of the inner pages, 55 x 55 at 100 %.
        s = 55 * scale
        small = Image.new("RGB", (s, s), PAPER)
        sm = mark.resize((s - 6 * scale, s - 6 * scale), Image.LANCZOS)
        small.paste(sm, (3 * scale, 3 * scale), sm)
        name = out / f"wizard-small-{scale * 100}.bmp"
        small.save(name, "BMP")
        made.append(name)
    return made


def _isi_escape(value: str) -> str:
    return value.replace('"', "'").replace("{", "{{")


def main() -> int:
    from datetime import date

    OUT.mkdir(parents=True, exist_ok=True)
    pub = tpn.load_publisher()
    fields = {"year": date.today().year, "publisher": pub.get("name", "Till Heidrich"),
              "project_url": pub.get("project_url", "")}
    licence = (ROOT / "LICENSE").read_text(encoding="utf-8")
    exception = (ROOT / "LICENSE-EXCEPTION").read_text(encoding="utf-8")
    sep = "\n\n" + "-" * 78 + "\n\n"
    write_text(OUT / "license-de.txt", NOTE_DE.format(**fields) + sep + exception + sep + licence)
    write_text(OUT / "license-en.txt", NOTE_EN.format(**fields) + sep + exception + sep + licence)
    write_text(OUT / "privacy-de.txt", PRIVACY_DE.format(connections=oc.installer_text("de"), never=oc.NEVER_DE))
    write_text(OUT / "privacy-en.txt", PRIVACY_EN.format(connections=oc.installer_text("en"), never=oc.NEVER_EN))
    isi = [f'#define PublisherName "{_isi_escape(pub.get("name", "Till Heidrich"))}"',
           f'#define PublisherURL "{_isi_escape(pub.get("project_url", ""))}"',
           f'#define SupportURL "{_isi_escape(pub.get("support_url", pub.get("project_url", "")))}"',
           f'#define PublisherContact "{_isi_escape(pub.get("email", ""))}"']
    (OUT / "publisher.isi").write_text("\n".join(isi) + "\n", encoding="utf-8-sig")
    images = wizard_images(OUT)
    for p in sorted(OUT.iterdir()):
        print(p)
    return 0 if images else 1


if __name__ == "__main__":
    raise SystemExit(main())
