#!/usr/bin/env python3
"""Every network connection the booth can make, with the setting that switches it.

This list is the single source for three texts that must say the same thing:
the privacy page of the Windows installer, docs/DATENSCHUTZ-BOX.md and
docs/PRIVACY-BOOTH.md. tests/test_outbound_connections.py keeps it honest:
every file in booth/ or sync/ that opens a network connection must be named
here, every switch named here must exist in booth/boothcore/config.py with
"off" as its default, and both documents must contain the table generated
from it. Adding a connection without adding it here fails the suite.

    python tools/outbound_connections.py --markdown de     # table for the docs
    python tools/outbound_connections.py --installer de    # text for the installer page
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
from typing import Dict, Optional, Sequence, Tuple


@dataclass(frozen=True)
class Connection:
    id: str
    de: str                    # what, in German
    en: str
    target: str                # where it goes (as the operator sets it)
    switch: str                # "local:server.url", "profile:mail.enabled", "action:..." or "none"
    files: Tuple[str, ...]     # the source files that open it
    #: True: off unless the operator turns it on. False: on by default (named why).
    off_by_default: bool = True
    direction: str = "out"     # out | in (a listener other devices connect to)


CONNECTIONS: Tuple[Connection, ...] = (
    Connection(
        "gallery", "Galerie: Bilder hochladen, Profil und Aufträge abholen, Lebenszeichen, "
        "Mails über die Galerie, Update-Angebot, Abmeldung beim gewollten Beenden, stündlicher "
        "Neuversuch fehlgeschlagener Uploads",
        "Gallery: upload photos, fetch profile and jobs, heartbeat, mail via the gallery, update offer, "
        "sign-off when shut down on purpose, hourly retry of failed uploads",
        "die Galerie-Adresse, die der Betreiber einträgt oder per Kopplung bekommt",
        "local:server.url",
        ("sync/photobox_sync/targets.py", "booth/boothcore/app.py", "booth/boothcore/doctor.py")),
    Connection(
        "pairing", "Kopplung mit einer Galerie über einen Kopplungscode",
        "Pairing with a gallery using a pairing code",
        "die Adresse im Code; ein Kurzcode wird zu https://<code>.photobox-gallery.com",
        "action:Kopplungscode in der Verwaltung eingeben",
        ("sync/photobox_sync/pairing.py", "booth/boothcore/app.py")),
    # No socket of its own: the answers travel over the gallery connection above.
    # Listed because it changes what that connection carries, and has its own switch.
    Connection(
        "remote_control", "Fernsteuerung durch die Galerie (Aufträge aus fester Liste; Antworten "
        "an die Galerie, auf Anfrage auch Livebild der Kamera und Bildschirmfoto, höchstens 640 px)",
        "Remote control by the gallery (orders from a fixed list; answers go to the gallery, on "
        "request also the camera's live image and a screenshot, 640 px at most)",
        "die gekoppelte Galerie (über die Galerie-Verbindung)",
        "local:remote_control.allowed",
        ()),
    Connection(
        "update", "Neue Programmfassung herunterladen (nur wenn die gekoppelte Galerie sie anbietet "
        "und der Betreiber oder die Galerie den Abruf auslöst; Prüfung per SHA-256)",
        "Download a new program version (only when the paired gallery offers one and the operator "
        "or the gallery starts the download; checked by SHA-256)",
        "die Download-Adresse aus dem Angebot der Galerie",
        "local:server.url",
        ("booth/boothcore/updater.py",)),
    Connection(
        "mail_smtp", "E-Mails an Gäste und Meldungen an den Betreiber direkt per SMTP",
        "E-mails to guests and alerts to the operator directly over SMTP",
        "der eingetragene SMTP-Server",
        "profile:mail.enabled",
        ("booth/boothcore/delivery/mail.py",)),
    Connection(
        "webhooks", "Webhooks: eigene Adresse bekommt Bescheid, wenn ein Bild fertig ist",
        "Webhooks: your own address is told when a photo is ready",
        "die eingetragene Webhook-Adresse",
        "profile:webhooks.enabled",
        ("booth/boothcore/webhooks.py",)),
    Connection(
        "targets", "Zusätzliche Ablageziele per SFTP, FTPS oder FTP",
        "Additional storage targets over SFTP, FTPS or FTP",
        "die eingetragenen Server",
        "local:targets",
        # second_target.py runs the same check when the gallery asks for it (17.11.2026)
        ("sync/photobox_sync/targets.py", "booth/boothcore/second_target.py")),
    Connection(
        "camera_wifi", "Canon-Kamera über WLAN (CCAPI)", "Canon camera over Wi-Fi (CCAPI)",
        "die Adresse der Kamera im lokalen Netz",
        "local:camera.ccapi_url",
        ("booth/boothcore/camera/ccapi.py", "booth/boothcore/einrichtung.py", "booth/boothcore/doctor.py")),
    Connection(
        "digicamcontrol", "Kamera über digiCamControl (läuft auf demselben Rechner)",
        "Camera via digiCamControl (runs on the same PC)",
        "http://127.0.0.1:5513 (voreingestellt), nur mit Kamera-Backend „digicamcontrol“",
        "local:camera.backend=digicamcontrol",
        ("booth/boothcore/camera/digicam.py", "booth/boothcore/camera/service.py",
         "booth/boothcore/einrichtung.py")),
    Connection(
        "printer_ipp", "Drucker im Netz über IPP", "Network printer over IPP",
        "die eingetragene Drucker-Adresse (ipp://...)",
        "local:printers",
        ("booth/boothcore/printing/backends.py",)),
    Connection(
        "guest_wifi", "Gastseite im WLAN der Box (Herunterladen ohne Internet): eine zweite "
        "Gegenstelle auf allen Netzadressen, Port 8766 - eingehend, Telefone der Gäste verbinden sich",
        "Guest page on the booth's Wi-Fi (download without internet): a second listener on all "
        "addresses, port 8766 - incoming, guests' phones connect",
        "eingehend aus dem lokalen Netz", "local:guest_share.allowed",
        ("booth/boothcore/guest_share.py",), direction="in"),
    Connection(
        "local_only", "Programmteile untereinander (Wächter, Fenster, Box) - nur 127.0.0.1",
        "Parts of the program talking to each other (watcher, window, booth) - 127.0.0.1 only",
        "127.0.0.1 (verlässt den Rechner nicht)", "none",
        ("booth/boothcore/waechter.py", "booth/boothcore/window.py", "sync/photobox_sync/__main__.py"),
        off_by_default=False),
)

#: The "where to" column in English.
TARGET_EN: Dict[str, str] = {
    "gallery": "the gallery address the operator enters or gets by pairing",
    "pairing": "the address inside the code; a short code becomes https://<code>.photobox-gallery.com",
    "remote_control": "the paired gallery (over the gallery connection)",
    "update": "the download address in the gallery's offer",
    "mail_smtp": "the SMTP server entered",
    "webhooks": "the webhook address entered",
    "targets": "the servers entered",
    "camera_wifi": "the camera's address on the local network",
    "digicamcontrol": "http://127.0.0.1:5513 (default), only with camera backend \"digicamcontrol\"",
    "printer_ipp": "the printer address entered (ipp://...)",
    "guest_wifi": "incoming from the local network",
    "local_only": "127.0.0.1 (never leaves the PC)",
}

#: What is never done, said on the installer page and in the docs.
NEVER_DE = ("keine Telemetrie, keine Nutzungsstatistik an den Herausgeber, keine Werbung, "
            "keine Gesichtserkennung, keine KI, keine Schriften oder Skripte aus dem Internet, "
            "keine eigene Update-Prüfung ohne gekoppelte Galerie")
NEVER_EN = ("no telemetry, no usage statistics sent to the publisher, no advertising, no face "
            "recognition, no AI, no fonts or scripts loaded from the internet, no update check of "
            "its own without a paired gallery")


def by_id() -> Dict[str, Connection]:
    return {c.id: c for c in CONNECTIONS}


def switch_label(c: Connection, lang: str) -> str:
    if c.switch == "none":
        return "-"
    kind, _, key = c.switch.partition(":")
    if kind == "action":
        return key if lang == "de" else "entering a pairing code in the admin"
    where = {"local": "local.json", "profile": "Profil"}.get(kind, kind) if lang == "de" else \
        {"local": "local.json", "profile": "profile"}.get(kind, kind)
    return f"{where}: {key}"


def markdown(lang: str = "de") -> str:
    head = ("| Kennung | Was | Wohin | Eingeschaltet durch | Ab Werk |\n|---|---|---|---|---|"
            if lang == "de" else
            "| Id | What | Where to | Switched on by | Default |\n|---|---|---|---|---|")
    rows = []
    for c in CONNECTIONS:
        what = c.de if lang == "de" else c.en
        default = ("aus" if lang == "de" else "off") if c.off_by_default else ("an" if lang == "de" else "on")
        target = c.target if lang == "de" else TARGET_EN[c.id]
        rows.append(f"| `{c.id}` | {what} | {target} | {switch_label(c, lang)} | {default} |")
    return head + "\n" + "\n".join(rows)


def installer_text(lang: str = "de") -> str:
    lines = []
    for c in CONNECTIONS:
        if c.id == "local_only":
            continue
        what = c.de if lang == "de" else c.en
        lines.append(f"      * {what} [{c.id}; {switch_label(c, lang)}]")
    return "\n".join(lines)


START, END = "<!-- outbound:start -->", "<!-- outbound:end -->"
#: The documents that carry the table, and their language.
DOCS = {"docs/DATENSCHUTZ-BOX.md": "de", "docs/PRIVACY-BOOTH.md": "en"}


def with_table(text: str, lang: str) -> str:
    """The document with the generated table between its two markers."""
    before, _, rest = text.partition(START)
    _, _, after = rest.partition(END)
    return f"{before}{START}\n{markdown(lang)}\n{END}{after}"


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--markdown", choices=("de", "en"))
    ap.add_argument("--installer", choices=("de", "en"))
    ap.add_argument("--update-docs", action="store_true", help="rewrite the table in the privacy docs")
    args = ap.parse_args(argv)
    if args.update_docs:
        from pathlib import Path
        root = Path(__file__).resolve().parent.parent
        for rel, lang in DOCS.items():
            path = root / rel
            path.write_text(with_table(path.read_text(encoding="utf-8"), lang), encoding="utf-8")
            print(f"updated {rel}")
    if args.markdown:
        print(markdown(args.markdown))
    if args.installer:
        print(installer_text(args.installer))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
