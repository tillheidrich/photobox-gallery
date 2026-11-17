"""Die beiden Server hinter dem Eingang: FTP(S) und SFTP.

Getrennt von ``app/eingang.py``, weil dort die Regeln stehen (wer darf was,
was wird angenommen, wie kommt es in die Galerie) und hier nur die Technik,
die sie ausspricht. Wer die Regeln ändert, muss diese Datei nicht anfassen.

**Warum in der Anwendung und nicht als eigener Dienst.** Ein zweiter Container
wäre sauberer getrennt, aber er bräuchte dieselbe Datenbank, dieselben Ordner
und dieselbe Einstellungsseite — und der Betreiber müsste ihn zusätzlich
aufsetzen. Der Eingang läuft deshalb im selben Prozess, in einem eigenen
Thread, und bindet den Port genau einmal: Laufen mehrere Arbeiter (gunicorn),
bekommt der erste den Port, die übrigen merken das und lassen es. Das steht
auch im Protokoll, damit niemand rätselt.
"""
from __future__ import annotations

import errno
import logging
import os
import socket
import threading
import time
from pathlib import Path
from typing import Optional

from app import db, eingang

log = logging.getLogger("photobox.eingang")

_gestartet: set = set()
_lock = threading.Lock()


# ── FTP / FTPS ────────────────────────────────────────────────

def _ftp_bauen():
    """Den FTP-Server zusammensetzen. ``None``, wenn etwas Nötiges fehlt."""
    try:
        from pyftpdlib.authorizers import AuthenticationFailed, DummyAuthorizer
        from pyftpdlib.handlers import FTPHandler
        from pyftpdlib.servers import FTPServer
    except ImportError:
        log.error("Eingang FTP: pyftpdlib fehlt — Paket nachinstallieren oder FTP ausschalten")
        return None

    class Anmeldung(DummyAuthorizer):
        """Kein Benutzerverzeichnis im Speicher — jede Anmeldung fragt die Datenbank."""

        def validate_authentication(self, benutzer, passwort, handler):
            konto = eingang.pruefe(benutzer, passwort, getattr(handler, "remote_ip", ""))
            if not konto:
                # Gleiche Antwort für „Konto gibt es nicht" und „Kennwort falsch":
                # Wer probiert, soll nicht erfahren, welche Events existieren.
                time.sleep(0.5)
                raise AuthenticationFailed("Anmeldung fehlgeschlagen")
            handler.pb_event_id = konto["event_id"]
            handler.pb_ordner = konto["ordner"]

        def get_home_dir(self, benutzer):
            event = db.get_event_by_slug(str(benutzer).lower())
            return str(eingang.ordner_fuer(event["slug"])) if event else str(eingang.wurzel())

        def get_perms(self, benutzer):
            # Hochladen, umbenennen, eigene Zwischendateien aufräumen, Ordner
            # anlegen. Kein Zugriff nach außerhalb — dafür sorgt das Heimatverzeichnis.
            return "elradfmw"

        def get_msg_login(self, benutzer):
            return "Eingang der Photobox-Galerie."

        def get_msg_quit(self, benutzer):
            return "Bis später."

        def has_user(self, benutzer):
            return db.get_event_by_slug(str(benutzer).lower()) is not None

        def has_perm(self, benutzer, perm, pfad=None):
            return perm in self.get_perms(benutzer)

        def impersonate_user(self, benutzer, passwort):
            return None

        def terminate_impersonation(self, benutzer):
            return None

    class Handler(FTPHandler):
        def on_file_received(self, datei):
            """Datei fertig — sofort in die Galerie holen."""
            try:
                eingang.uebernimm(int(getattr(self, "pb_event_id", 0)), Path(datei))
            except Exception as e:              # noqa: BLE001 — ein Fehler beendet keine Sitzung
                log.error("Eingang FTP: %s nicht übernommen: %s", Path(datei).name, e)

        def on_incomplete_file_received(self, datei):
            log.info("Eingang FTP: abgebrochener Upload verworfen: %s", Path(datei).name)
            Path(datei).unlink(missing_ok=True)

    tls = eingang.einstellung("eingang_ftp_tls") == "1"
    klartext_erlaubt = eingang.einstellung("eingang_ftp_klartext") == "1"
    if tls:
        try:
            from pyftpdlib.handlers import TLS_FTPHandler
        except ImportError:
            log.error("Eingang FTP: Verschlüsselung verlangt, aber pyOpenSSL fehlt")
            return None
        zertifikat = _zertifikat()
        if not zertifikat:
            return None

        class SicherHandler(TLS_FTPHandler, Handler):
            certfile = str(zertifikat)
            # Ohne diese beiden ist FTPS eine Empfehlung, keine Bedingung.
            tls_control_required = not klartext_erlaubt
            tls_data_required = not klartext_erlaubt

        handler = SicherHandler
    else:
        if not klartext_erlaubt:
            log.error("Eingang FTP: weder Verschlüsselung noch Klartext erlaubt — nichts zu tun")
            return None
        handler = Handler

    handler.authorizer = Anmeldung()
    handler.banner = "Photobox-Galerie"
    handler.timeout = 300
    bereich = str(eingang.einstellung("eingang_ftp_passiv") or "")
    if "-" in bereich:
        try:
            von, bis = (int(x) for x in bereich.split("-", 1))
            handler.passive_ports = range(von, bis + 1)
        except ValueError:
            log.warning("Eingang FTP: %r ist kein Portbereich, Standard wird genutzt", bereich)

    port = int(eingang.einstellung("eingang_ftp_port") or 2121)
    return FTPServer(("0.0.0.0", port), handler), port


def _zertifikat() -> Optional[Path]:
    """Das Zertifikat für FTPS — eingetragen oder selbst erzeugt.

    Ein selbst erzeugtes Zertifikat verschlüsselt genauso gut, weist aber
    nichts nach: Der Client kann nicht prüfen, mit wem er spricht. Für einen
    Box-Rechner im selben Netz ist das in Ordnung, für einen Upload über das
    Internet gehört ein echtes hierher. Die Einstellungsseite sagt das auch.
    """
    eigener = str(eingang.einstellung("eingang_ftp_cert") or "").strip()
    if eigener and Path(eigener).is_file():
        return Path(eigener)
    from app import media

    ziel = media.data_dir() / "eingang" / "ftps.pem"
    if ziel.is_file():
        return ziel
    try:
        from datetime import datetime, timedelta, timezone

        from cryptography import x509
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import rsa
        from cryptography.x509.oid import NameOID
    except ImportError:
        log.error("Eingang FTP: kein Zertifikat eingetragen und keines erzeugbar")
        return None
    schluessel = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "photobox-eingang")])
    jetzt = datetime.now(timezone.utc)
    zert = (x509.CertificateBuilder()
            .subject_name(name).issuer_name(name)
            .public_key(schluessel.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(jetzt - timedelta(days=1))
            .not_valid_after(jetzt + timedelta(days=3650))
            .sign(schluessel, hashes.SHA256()))
    ziel.parent.mkdir(parents=True, exist_ok=True)
    ziel.write_bytes(
        schluessel.private_bytes(serialization.Encoding.PEM,
                                 serialization.PrivateFormat.TraditionalOpenSSL,
                                 serialization.NoEncryption())
        + zert.public_bytes(serialization.Encoding.PEM))
    os.chmod(ziel, 0o600)
    log.info("Eingang FTP: eigenes Zertifikat erzeugt (%s)", ziel)
    return ziel


# ── SFTP ──────────────────────────────────────────────────────

def _sftp_starten(port: int) -> bool:
    try:
        import paramiko
    except ImportError:
        log.error("Eingang SFTP: paramiko fehlt")
        return False
    from app import media

    schluesseldatei = media.data_dir() / "eingang" / "sftp_host_key"
    if not schluesseldatei.is_file():
        schluesseldatei.parent.mkdir(parents=True, exist_ok=True)
        paramiko.RSAKey.generate(2048).write_private_key_file(str(schluesseldatei))
        os.chmod(schluesseldatei, 0o600)
        log.info("Eingang SFTP: Serverschlüssel erzeugt (%s)", schluesseldatei)
    hostkey = paramiko.RSAKey(filename=str(schluesseldatei))

    class Wache(paramiko.ServerInterface):
        def __init__(self):
            self.konto = None

        def check_auth_password(self, benutzer, passwort):
            self.konto = eingang.pruefe(benutzer, passwort)
            if self.konto:
                return paramiko.AUTH_SUCCESSFUL
            time.sleep(0.5)
            return paramiko.AUTH_FAILED

        def get_allowed_auths(self, benutzer):
            return "password"

        def check_channel_request(self, art, kanal):
            return (paramiko.OPEN_SUCCEEDED if art == "session"
                    else paramiko.OPEN_FAILED_ADMINISTRATIVELY_PROHIBITED)

    class Dateien(paramiko.SFTPServerInterface):
        """Ein Dateisystem, das genau einen Ordner groß ist."""

        def __init__(self, server, *args, **kwargs):
            super().__init__(server, *args, **kwargs)
            konto = getattr(server, "konto", None) or {}
            self.wurzel = Path(konto.get("ordner") or eingang.wurzel())
            self.event_id = int(konto.get("event_id") or 0)

        def _echt(self, pfad: str) -> Optional[Path]:
            ziel = (self.wurzel / str(pfad or "").lstrip("/")).resolve()
            wurzel = self.wurzel.resolve()
            # Ein Pfad mit „..“ ist kein Versehen, das man geradebiegt.
            return ziel if ziel == wurzel or wurzel in ziel.parents else None

        def list_folder(self, pfad):
            ziel = self._echt(pfad)
            if not ziel or not ziel.is_dir():
                return paramiko.SFTP_NO_SUCH_FILE
            aus = []
            for kind in ziel.iterdir():
                eintrag = paramiko.SFTPAttributes.from_stat(kind.stat())
                eintrag.filename = kind.name
                aus.append(eintrag)
            return aus

        def stat(self, pfad):
            ziel = self._echt(pfad)
            if not ziel or not ziel.exists():
                return paramiko.SFTP_NO_SUCH_FILE
            return paramiko.SFTPAttributes.from_stat(ziel.stat())

        lstat = stat

        def open(self, pfad, flags, attr):
            ziel = self._echt(pfad)
            if not ziel:
                return paramiko.SFTP_PERMISSION_DENIED
            ziel.parent.mkdir(parents=True, exist_ok=True)
            modus = "rb" if not (flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT)) else "wb"
            try:
                datei = ziel.open(modus)
            except OSError:
                return paramiko.SFTP_FAILURE
            griff = paramiko.SFTPHandle(flags)
            griff.readfile = datei
            griff.writefile = datei if modus == "wb" else None
            griff._pb_pfad = ziel
            griff._pb_server = self
            return griff

        def remove(self, pfad):
            ziel = self._echt(pfad)
            if not ziel or not ziel.is_file():
                return paramiko.SFTP_NO_SUCH_FILE
            ziel.unlink()
            return paramiko.SFTP_OK

        def rename(self, alt, neu):
            a, b = self._echt(alt), self._echt(neu)
            if not a or not b or not a.exists():
                return paramiko.SFTP_NO_SUCH_FILE
            a.rename(b)
            return paramiko.SFTP_OK

        def mkdir(self, pfad, attr):
            ziel = self._echt(pfad)
            if not ziel:
                return paramiko.SFTP_PERMISSION_DENIED
            ziel.mkdir(parents=True, exist_ok=True)
            return paramiko.SFTP_OK

        def rmdir(self, pfad):
            ziel = self._echt(pfad)
            if not ziel or not ziel.is_dir():
                return paramiko.SFTP_NO_SUCH_FILE
            try:
                ziel.rmdir()
            except OSError:
                return paramiko.SFTP_FAILURE
            return paramiko.SFTP_OK

        def chattr(self, pfad, attr):
            return paramiko.SFTP_OK          # Rechte interessieren hier niemanden

    lauscher = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    lauscher.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        lauscher.bind(("0.0.0.0", port))
    except OSError as e:
        if e.errno in (errno.EADDRINUSE, errno.EACCES):
            log.info("Eingang SFTP: Port %s ist vergeben — ein anderer Arbeiter bedient ihn", port)
        else:
            log.error("Eingang SFTP: Port %s nicht belegbar: %s", port, e)
        lauscher.close()
        return False
    lauscher.listen(16)
    log.info("Eingang SFTP: hört auf Port %s", port)

    def bedienen(verbindung, adresse):
        transport = paramiko.Transport(verbindung)
        transport.add_server_key(hostkey)
        wache = Wache()
        transport.set_subsystem_handler("sftp", paramiko.SFTPServer, Dateien)
        try:
            transport.start_server(server=wache)
            kanal = transport.accept(30)
            if kanal is None:
                return
            # Die Sitzung läuft, solange der Client verbunden ist. Danach wird
            # eingelesen: Ein Ende meldet SFTP nicht, also sehen wir selbst nach.
            while transport.is_active():
                time.sleep(1)
        except Exception as e:                  # noqa: BLE001
            log.debug("Eingang SFTP: Sitzung beendet (%s)", e)
        finally:
            try:
                transport.close()
            except Exception:                   # noqa: BLE001
                pass
            try:
                eingang.einlesen()
            except Exception as e:              # noqa: BLE001
                log.error("Eingang SFTP: Einlesen fehlgeschlagen: %s", e)

    def annehmen():
        while True:
            try:
                verbindung, adresse = lauscher.accept()
            except OSError:
                return
            threading.Thread(target=bedienen, args=(verbindung, adresse),
                             name="sftp-sitzung", daemon=True).start()

    threading.Thread(target=annehmen, name="sftp-annahme", daemon=True).start()
    return True


# ── Starten ───────────────────────────────────────────────────

def starte(app) -> None:
    """Beide Dienste hochfahren, soweit eingeschaltet. Mehrfach aufrufbar."""
    with _lock:
        if eingang.an("ftp") and "ftp" not in _gestartet:
            gebaut = _ftp_bauen()
            if gebaut:
                server, port = gebaut
                threading.Thread(target=server.serve_forever, name="ftp-eingang",
                                 daemon=True).start()
                log.info("Eingang FTP: hört auf Port %s", port)
                _gestartet.add("ftp")
        if eingang.an("sftp") and "sftp" not in _gestartet:
            port = int(eingang.einstellung("eingang_sftp_port") or 2222)
            if _sftp_starten(port):
                _gestartet.add("sftp")
        if _gestartet and "nachlese" not in _gestartet:
            _gestartet.add("nachlese")
            threading.Thread(target=_nachlese, args=(app,), name="eingang-nachlese",
                             daemon=True).start()


def _nachlese(app) -> None:
    """Alle zehn Sekunden nachsehen, ob etwas liegengeblieben ist.

    Der FTP-Server meldet jedes fertige Dateiende selbst; SFTP tut das nicht,
    und ein Client, der mitten im Upload den Stecker zieht, meldet gar nichts.
    Diese Runde ist das Netz darunter — sie nimmt nur, was seit Sekunden
    unverändert daliegt.
    """
    while True:
        time.sleep(10)
        try:
            with app.app_context():
                eingang.einlesen()
        except Exception as e:                  # noqa: BLE001
            log.error("Eingang: Nachlese fehlgeschlagen: %s", e)


__all__ = ["starte"]
