"""Eigene Domains: die Galerie unter der Adresse des Kunden.

Wer eine Instanz für jemanden betreibt, will meistens nicht, dass die Gäste
dieses Kunden auf einer fremden Marke landen. Die Bilder sollen unter
``bilder.kunde.de`` liegen, nicht unter der Adresse des Dienstleisters — auf
dem QR-Code an der Box, im geteilten Link, in der Mail, auf dem Sharepic.

Wie es funktioniert:

1. Der Kunde trägt bei seinem Anbieter einen DNS-Eintrag ein. Welchen, sagt
   diese Seite ihm wörtlich — ein CNAME auf den Hostnamen dieser Instanz.
2. Die Domain wird hier eingetragen, wahlweise für ein bestimmtes Event oder
   für die ganze Instanz.
3. Die Galerie prüft, ob die Domain tatsächlich hierher zeigt. Erst danach
   gilt sie als bestätigt, und erst dann werden Links darauf gebaut.

**Was diese Datei nicht tut:** Zertifikate ausstellen. Das gehört zur
Betriebsebene (Coolify, Traefik, Caddy, nginx) und lässt sich aus einer
Flask-Anwendung heraus nicht ehrlich versprechen. Die Oberfläche sagt
deshalb klar, welcher Schritt dort noch fehlt, und prüft hinterher nach, ob
HTTPS wirklich antwortet — statt so zu tun, als sei damit alles getan.

Eine Domain ohne Bestätigung ändert nichts: Links laufen weiter über die
Hauptadresse. Das ist Absicht — ein QR-Code, der ins Leere zeigt, ist
schlimmer als einer, der auf die richtige, aber fremde Adresse zeigt.
"""
from __future__ import annotations

import re
import socket
import time
from typing import Any, Dict, List, Optional

from app import db

#: Was als Domain durchgeht. Bewusst eng: keine Pfade, keine Ports, kein
#: Schema — hier steht ein Hostname und sonst nichts.
_DOMAIN = re.compile(r"^(?=.{4,253}$)(?!-)[a-z0-9-]{1,63}(?<!-)(\.(?!-)[a-z0-9-]{1,63}(?<!-))+$")

#: Hostnamen, die niemand für sich beanspruchen darf.
GESPERRT = {"localhost", "localhost.localdomain"}


def init_schema() -> None:
    """Die Tabelle steht in ``ecosystem.init_schema`` — hier nur der Aufruf.

    Zwei Stellen, die dieselbe Tabelle anlegen, laufen irgendwann
    auseinander. Deshalb eine.
    """
    from app import ecosystem

    ecosystem.init_schema()


def normalisiere(roh: str) -> str:
    """Was der Mensch eintippt, auf einen reinen Hostnamen bringen.

    Erlaubt ist großzügig, gespeichert wird streng: ``https://Bilder.Kunde.de/``
    wird zu ``bilder.kunde.de``. Was danach kein Hostname ist, ist keiner.
    """
    wert = str(roh or "").strip().lower()
    wert = re.sub(r"^[a-z]+://", "", wert)
    wert = wert.split("/")[0].split("?")[0].split("#")[0]
    wert = wert.split(":")[0]                      # ein Port gehört nicht in DNS
    return wert.strip(".")


def gueltig(domain: str) -> bool:
    return bool(_DOMAIN.fullmatch(domain)) and domain not in GESPERRT


def eigener_host() -> str:
    """Der Hostname dieser Instanz — das Ziel, auf das der Kunde zeigen soll."""
    basis = db.base_url()
    return normalisiere(basis) if basis else ""


def anleitung(domain: str) -> Dict[str, str]:
    """Was der Kunde bei seinem Anbieter einträgt — wörtlich zum Weitergeben."""
    ziel = eigener_host()
    teile = domain.split(".")
    name = teile[0] if len(teile) > 2 else "@"
    return {
        "typ": "CNAME" if name != "@" else "ALIAS/ANAME (oder A auf die IP)",
        "name": name,
        "ziel": ziel or "— erst die Hauptadresse der Galerie eintragen —",
        "hinweis": ("Bei einer Domain ohne Unterdomain (kunde.de statt "
                    "bilder.kunde.de) kann nicht jeder Anbieter CNAME. Dann "
                    "ALIAS/ANAME nehmen oder einen A-Eintrag auf die IP "
                    "dieser Instanz."),
    }


def _adressen(host: str) -> List[str]:
    """Die IP-Adressen hinter einem Hostnamen. Leer heißt: keine Antwort."""
    if not host:
        return []
    try:
        infos = socket.getaddrinfo(host, None)
    except (socket.gaierror, UnicodeError, OSError):
        return []
    return sorted({eintrag[4][0] for eintrag in infos})


def pruefe(domain: str) -> Dict[str, Any]:
    """Zeigt die Domain hierher?

    Verglichen werden die aufgelösten Adressen mit denen der eigenen
    Hauptadresse. Das ist nicht kryptografisch, sondern die Frage, die
    tatsächlich zählt: Landet ein Gast, der diese Adresse eingibt, bei uns?
    """
    ziel = eigener_host()
    unsere = _adressen(ziel)
    ihre = _adressen(domain)
    if not ziel:
        return {"ok": False, "grund": "Die Galerie kennt ihre eigene Hauptadresse nicht — "
                                      "erst unter Einstellungen eintragen."}
    if not ihre:
        return {"ok": False, "grund": f"{domain} löst noch nicht auf. DNS braucht nach dem "
                                      f"Eintragen oft ein paar Minuten, manchmal Stunden."}
    if not unsere:
        return {"ok": False, "grund": f"Die eigene Adresse {ziel} löst nicht auf — Prüfung nicht möglich."}
    treffer = sorted(set(ihre) & set(unsere))
    if not treffer:
        return {"ok": False, "adressen": ihre, "ziel": unsere,
                "grund": f"{domain} zeigt auf {', '.join(ihre)}, diese Instanz liegt auf "
                         f"{', '.join(unsere)}."}
    return {"ok": True, "adressen": treffer,
            "grund": f"{domain} zeigt hierher ({', '.join(treffer)})."}


def liste(event_id: Optional[int] = None) -> List[Dict[str, Any]]:
    con = db.get_db()
    if event_id is None:
        rows = con.execute("SELECT * FROM event_domains ORDER BY domain").fetchall()
    else:
        rows = con.execute("SELECT * FROM event_domains WHERE event_id=? ORDER BY domain",
                           (int(event_id),)).fetchall()
    con.close()
    return [dict(r) for r in rows]


def anlegen(domain: str, event_id: Optional[int] = None, note: str = "") -> Dict[str, Any]:
    """Eine Domain eintragen. Bestätigt wird sie erst durch die Prüfung."""
    domain = normalisiere(domain)
    if not gueltig(domain):
        raise ValueError("Das ist kein Hostname, den man in DNS eintragen kann.")
    if domain == eigener_host():
        raise ValueError("Das ist bereits die Hauptadresse dieser Galerie.")
    con = db.get_db()
    try:
        con.execute("INSERT INTO event_domains (domain, event_id, note, created_at) VALUES (?,?,?,?)",
                    (domain, int(event_id) if event_id else None, str(note or "")[:200],
                     time.strftime("%Y-%m-%dT%H:%M:%S")))
        con.commit()
    except Exception as e:                        # noqa: BLE001 — UNIQUE-Verstoß als Klartext
        con.close()
        raise ValueError(f"{domain} ist schon eingetragen.") from e
    row = con.execute("SELECT * FROM event_domains WHERE domain=?", (domain,)).fetchone()
    con.close()
    return dict(row)


def bestaetigen(domain_id: int) -> Dict[str, Any]:
    """Prüfen und das Ergebnis festhalten."""
    con = db.get_db()
    row = con.execute("SELECT * FROM event_domains WHERE id=?", (int(domain_id),)).fetchone()
    con.close()
    if not row:
        raise ValueError("Diese Domain gibt es nicht.")
    ergebnis = pruefe(row["domain"])
    con = db.get_db()
    con.execute("UPDATE event_domains SET verified_at=?, last_check=?, last_result=? WHERE id=?",
                (time.strftime("%Y-%m-%dT%H:%M:%S") if ergebnis["ok"] else row["verified_at"],
                 time.strftime("%Y-%m-%dT%H:%M:%S"), ergebnis["grund"][:300], int(domain_id)))
    con.commit()
    con.close()
    return ergebnis


def loeschen(domain_id: int) -> None:
    con = db.get_db()
    con.execute("DELETE FROM event_domains WHERE id=?", (int(domain_id),))
    con.commit()
    con.close()


def fuer_event(event: Optional[Dict[str, Any]]) -> str:
    """Unter welcher Adresse die Links dieses Events laufen sollen.

    Reihenfolge: eine bestätigte Domain des Events, sonst eine bestätigte
    Domain für die ganze Instanz, sonst die Hauptadresse. Unbestätigtes zählt
    nicht — ein QR-Code auf eine Adresse, die noch nicht zeigt, wäre
    schlimmer als einer auf die eigene.
    """
    haupt = db.base_url()
    if not event:
        return haupt
    con = db.get_db()
    row = con.execute(
        "SELECT domain FROM event_domains WHERE verified_at IS NOT NULL AND event_id=? "
        "ORDER BY id LIMIT 1", (int(event.get("id") or 0),)).fetchone()
    if not row:
        row = con.execute(
            "SELECT domain FROM event_domains WHERE verified_at IS NOT NULL AND event_id IS NULL "
            "ORDER BY id LIMIT 1").fetchone()
    con.close()
    if not row:
        return haupt
    schema = "https://" if not haupt or haupt.startswith("https://") else "http://"
    return f"{schema}{row['domain']}"


def event_zu_host(host: str) -> Optional[int]:
    """Welches Event gehört zu dieser Adresse? ``None`` = keins oder alle.

    Damit weiß die Galerie, wohin ein Gast gehört, der nur die Domain des
    Kunden kennt und sonst nichts.
    """
    domain = normalisiere(host)
    if not domain:
        return None
    con = db.get_db()
    row = con.execute("SELECT event_id FROM event_domains WHERE domain=? AND verified_at IS NOT NULL",
                      (domain,)).fetchone()
    con.close()
    return int(row["event_id"]) if row and row["event_id"] else None
