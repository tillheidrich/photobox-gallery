"""QR-Links, die schon funktionieren, bevor das Foto hochgeladen ist.

Die Box erzeugt pro Durchgang eine zufällige Kennung und signiert sie mit dem
Event-Schlüssel, den sie beim letzten Sync bekommen hat. Der QR-Code zeigt
sofort auf ``<galerie>/p/<kennung>-<signatur>``. Der Server prüft die Signatur:
echt, aber noch nicht da -> „Dein Foto ist unterwegs"; hochgeladen -> Foto.

Wer Kennungen durchprobiert, bekommt ohne gültige Signatur nur „nicht gefunden".
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import secrets

_ALPHABET = "abcdefghjkmnpqrstuvwxyz23456789"   # ohne 0/o/1/l/i: abtippbar


def new_uid(length: int = 10) -> str:
    return "".join(secrets.choice(_ALPHABET) for _ in range(length))


def signature(share_key: str, uid: str, length: int = 8) -> str:
    mac = hmac.new(share_key.encode(), uid.encode(), hashlib.sha256).digest()
    return base64.b32encode(mac).decode().lower().rstrip("=")[:length]


def share_code(share_key: str, uid: str) -> str:
    return f"{uid}-{signature(share_key, uid)}"


def share_url(base_url: str, share_key: str, uid: str, template: str = "") -> str:
    """Link für den QR-Code. ``template`` erlaubt fremde Ziele (z. B. Picdrop-Galerie)."""
    if template:
        return template.replace("{uid}", uid)
    if not base_url or not share_key:
        return ""
    return f"{base_url.rstrip('/')}/p/{share_code(share_key, uid)}"


#: Wie die Datei heißt, die ein Gast sehen will — je Betriebsart.
#:
#: Das ist eine Kopplung an zwei andere Stellen: an die Dateinamen, die
#: ``app.finalize`` vergibt, und an ``targets.file_name()``, die dem Ziel-
#: namen die Kennung voranstellt. Beides wird von einem Test festgehalten
#: (``test_qr_dateiname``): Wer ``result_web.jpg`` umbenennt, bekommt dort
#: einen roten Test und findet diese Tabelle.
WEB_DATEI = {
    "photo": "result_web.jpg",
    "collage": "result_web.jpg",
    "gif": "result.gif",
    "boomerang": "boomerang.mp4",
    "video": "video.mp4",
}


def ziel_dateiname(uid: str, mode: str) -> str:
    """Der Name, unter dem diese Aufnahme beim Ziel ankommt.

    Gebraucht für fremde Ablagen, deren Download-Adresse den Dateinamen
    enthält statt einer Kennung — Nextcloud zum Beispiel kennt für eine
    einzelne Datei in einem geteilten Ordner nur
    ``…/s/<token>/download?path=%2F&files=<name>``.

    Der Name steht schon fest, bevor das Foto existiert: Er setzt sich aus
    der Kennung des Durchgangs und einem festen Dateinamen je Betriebsart
    zusammen. Deshalb stimmt der QR-Code ab der ersten Sekunde — genau wie
    beim eigenen Galerie-Link.
    """
    return f"{uid}_{WEB_DATEI.get(str(mode), 'result_web.jpg')}"


def _fill(text: str, uid: str, code: str, event: dict, dateiname: str = "") -> str:
    # Der Dateiname wandert in eine Adresse und muss dorthin kodiert — ein
    # Leerzeichen oder ein Umlaut zerlegt sonst die ganze URL.
    from urllib.parse import quote
    return (text.replace("{uid}", uid).replace("{code}", code)
                .replace("{filename}", quote(dateiname, safe=""))
                .replace("{event}", str(event.get("slug", "")))
                .replace("{event_name}", str(event.get("name", ""))))


def with_query(url: str, query: str) -> str:
    """Append ``a=1&b=2`` to a URL, keeping an existing query and fragment intact."""
    query = (query or "").lstrip("?&").strip()
    if not url or not query:
        return url
    head, sep, fragment = url.partition("#")
    joiner = "&" if "?" in head else "?"
    return f"{head}{joiner}{query}{sep}{fragment}"


#: Placeholders that name one photo. Before the first trigger there is none.
_FOTO_PLATZHALTER = ("{uid}", "{code}", "{filename}")


def start_link(vorlage: str, event: dict) -> str:
    """The operator's own QR link, made fit for the start screen, or ``""``.

    The start screen has no photo, so ``{uid}``, ``{code}`` and ``{filename}``
    cannot be filled. Rather than show a link with a hole in it, the link is
    cut back: a photo placeholder in the query drops the query (the page the
    query was for remains), one in the path cuts back to the folder before it
    (``…/album/{uid}`` -> ``…/album/``). ``{event}`` and ``{event_name}`` are
    filled. Only a complete http(s) link with a host is returned.
    """
    from urllib.parse import urlsplit
    url = str(vorlage or "").strip()
    if not url:
        return ""
    stellen = [url.find(p) for p in _FOTO_PLATZHALTER if p in url]
    if stellen:
        erste = min(stellen)
        frage = url.find("?")
        if 0 <= frage < erste:
            url = url[:frage]
        else:
            url = url[:url.rfind("/", 0, erste) + 1]
    url = _fill(url, "", "", event)
    try:
        teile = urlsplit(url)
    except ValueError:
        return ""
    # A placeholder in the host name cuts back to "https://", which has no
    # host and ends here as well.
    if teile.scheme.lower() not in ("http", "https") or not teile.hostname:
        return ""
    return url


def event_url(profile: dict) -> str:
    """Der Link zur Galerie des Events — für den QR-Code auf dem Startbildschirm.

    Nicht zu verwechseln mit dem QR nach einer Aufnahme: Der zeigt auf genau
    dieses eine Bild. Dieser hier zeigt auf den Abend, und zwar bevor jemand
    ausgelöst hat — für alle, die nur vorbeigehen und später nachsehen wollen.
    """
    event = profile.get("event") or {}
    qr = profile.get("qr") or {}
    if str(qr.get("mode") or "auto").lower() == "off":
        return ""
    basis = str(event.get("base_url") or "").rstrip("/")
    slug = str(event.get("slug") or "").strip()
    if not basis or not slug:
        # No gallery to point at. A booth without cloud whose operator chose
        # an own QR destination still gets a start-screen QR: that link.
        if str(qr.get("mode") or "auto").lower() == "custom":
            eigen = start_link(str(qr.get("url") or "").strip()
                               or str(profile.get("qr_url_template") or "").strip(), event)
            return with_query(eigen, _fill(str(qr.get("utm") or ""), "", "", event)) if eigen else ""
        return ""
    code = str(event.get("share_key") or "")
    url = f"{basis}/gallery/{slug}"
    # Ist die Galerie mit einem Passwort geschützt, hängt der Schlüssel als
    # Zugangs-Kennung mit dran — sonst steht der Gast vor einer Abfrage, die
    # er nicht beantworten kann.
    return with_query(url, _fill(str(qr.get("utm") or ""), "", code, event))


def qr_target(profile: dict, uid: str, mode: str = "photo") -> str:
    """The URL behind the QR code — works with or without the gallery.

    ``profile["qr"]``: ``mode`` is ``auto`` (gallery when configured, otherwise the
    free URL), ``gallery``, ``custom`` or ``off``; ``url`` is the operator's own
    destination with ``{uid}``/``{code}``/``{event}`` placeholders; ``utm`` is a
    query string appended to whichever URL wins, so campaign tracking survives.
    """
    event = profile.get("event") or {}
    qr = profile.get("qr") or {}
    mode = str(qr.get("mode") or "auto").lower()
    if mode == "off":
        return ""
    key = str(event.get("share_key") or "")
    code = share_code(key, uid) if key else uid
    datei = ziel_dateiname(uid, mode)
    gallery = share_url(str(event.get("base_url") or ""), key, uid)
    legacy = str(profile.get("qr_url_template") or "").strip()
    custom = str(qr.get("url") or "").strip() or legacy
    if mode == "gallery":
        url = gallery
    elif mode == "custom" or (mode == "auto" and legacy and not qr.get("url")):
        # ein gesetztes qr_url_template hat wie bisher Vorrang (z. B. Picdrop)
        url = _fill(custom, uid, code, event, datei)
    else:                                   # auto: eigene Galerie zuerst
        url = gallery or _fill(custom, uid, code, event, datei)
    return with_query(url, _fill(str(qr.get("utm") or ""), uid, code, event, datei))
