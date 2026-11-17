"""Wie sicher ist dieses Passwort — und warum nicht.

Die Regeln folgen dem, was heute empfohlen wird (NIST SP 800-63B), nicht dem,
was Formulare früher verlangt haben: **Länge zählt, Zeichensalat nicht.**
„Sommer2024!" erfüllt jede klassische Regel und steht trotzdem in jeder
Passwortliste; „mein fahrrad steht im keller" erfüllt keine und ist besser.

Deshalb wird hier geprüft:

* Länge — der einzige Wert, der wirklich Arbeit kostet.
* Bekannte Muster: Zahlenreihen, Tastaturwege, ein Wort mit Jahreszahl.
* Bezug zur Installation: Benutzername, Galeriename, Domain.
* Wiederholungen: „aaaaaaaaaa" ist lang und trotzdem nichts wert.

Abgelehnt wird nur, was wirklich schwach ist. Alles andere bekommt eine
Einschätzung und darf gespeichert werden — es ist das Passwort des Betreibers,
nicht unseres.
"""
from __future__ import annotations

import re
import unicodedata
from typing import Iterable, List, Tuple

#: Ab hier lassen wir ein Admin-Passwort überhaupt zu.
MINDESTLAENGE = 10
#: Ab hier nennen wir es gut.
GUTE_LAENGE = 16

# Klein und wirksam: die Muster, die in Leaks immer oben stehen. Eine
# Millionenliste bringt hier wenig — wer sie umgeht, hat schon ein besseres
# Passwort als das, was diese Prüfung verhindern soll.
BEKANNT = {
    "passwort", "password", "pass", "geheim", "secret", "admin", "administrator",
    "fotobox", "photobox", "photobooth", "galerie", "gallery", "willkommen",
    "welcome", "hallo", "hello", "sommer", "winter", "fruehling", "herbst",
    "januar", "februar", "maerz", "april", "juni", "juli", "august",
    "september", "oktober", "november", "dezember", "qwertz", "qwerty",
    "asdfgh", "letmein", "iloveyou", "monkey", "dragon", "master", "test",
    "start", "hochzeit", "wedding", "fotos", "bilder",
}
REIHEN = ("0123456789", "9876543210", "abcdefghijklmnopqrstuvwxyz",
          "qwertzuiopasdfghjklyxcvbnm", "qwertyuiopasdfghjklzxcvbnm")


def _normal(text: str) -> str:
    """Kleinschreibung, ohne Akzente. Zahlen bleiben Zahlen."""
    roh = unicodedata.normalize("NFKD", str(text or "")).encode("ascii", "ignore").decode()
    return roh.lower()


def _entleet(text: str) -> str:
    """„P4ssw0rt" zu „passwort" — sonst hilft jede Wortliste nichts."""
    roh = _normal(text)
    for von, nach in (("0", "o"), ("1", "l"), ("3", "e"), ("4", "a"), ("5", "s"),
                      ("7", "t"), ("@", "a"), ("$", "s"), ("!", "i")):
        roh = roh.replace(von, nach)
    return roh


def _hat_reihe(text: str, laenge: int = 5) -> bool:
    klein = _normal(text)
    return any(klein[i:i + laenge] in reihe
               for reihe in REIHEN
               for i in range(len(klein) - laenge + 1))


def _wiederholt(text: str) -> bool:
    """„aaaaaa" oder „abcabcabc" — lang, aber ohne Substanz."""
    if re.search(r"(.)\1{3,}", text):
        return True
    for n in (2, 3, 4):
        if len(text) >= n * 3 and text == (text[:n] * (len(text) // n))[:len(text)]:
            return True
    return False


def _bekanntes_wort(text: str) -> bool:
    """Ein bekanntes Wort, auch mit Jahreszahl, Ausrufezeichen oder Zahlendreher."""
    kandidaten = set()
    for fassung in (_normal(text), _entleet(text)):
        kandidaten.add(fassung)
        kandidaten.add(re.sub(r"[\d\W_]+$", "", fassung))
        kandidaten.add(re.sub(r"^[\d\W_]+", "", fassung))
    return any(k in BEKANNT for k in kandidaten if k)


def staerke(passwort: str, bezug: Iterable[str] = ()) -> dict:
    """Einschätzung: 0 = untauglich … 4 = gut. Mit Begründung."""
    pw = str(passwort or "")
    gruende: List[str] = []
    punkte = 0

    if len(pw) >= MINDESTLAENGE:
        punkte += 1
    if len(pw) >= 13:
        punkte += 1
    if len(pw) >= GUTE_LAENGE:
        punkte += 1
    arten = sum(bool(re.search(muster, pw)) for muster in
                (r"[a-z]", r"[A-Z]", r"\d", r"[^\w\s]", r"\s"))
    if arten >= 3:
        punkte += 1

    if len(pw) < MINDESTLAENGE:
        gruende.append(f"mindestens {MINDESTLAENGE} Zeichen")
        punkte = 0
    if _bekanntes_wort(pw):
        gruende.append("ein bekanntes Wort — das steht in jeder Liste")
        punkte = 0
    if _hat_reihe(pw):
        gruende.append("eine Zahlen- oder Tastaturreihe")
        punkte = min(punkte, 1)
    if _wiederholt(pw):
        gruende.append("nur Wiederholungen")
        punkte = min(punkte, 1)
    for wort in bezug:
        wort = _normal(wort)
        if len(wort) >= 4 and wort in _normal(pw):
            gruende.append("etwas, das ohnehin auf der Seite steht")
            punkte = min(punkte, 1)
            break

    stufen = ["untauglich", "schwach", "geht so", "gut", "sehr gut"]
    return {"punkte": max(0, min(4, punkte)), "stufe": stufen[max(0, min(4, punkte))],
            "gruende": gruende, "ok": punkte >= 2 and not gruende}


def pruefe(passwort: str, bezug: Iterable[str] = ()) -> Tuple[bool, str]:
    """(geht klar, Meldung). Die Meldung sagt, was zu ändern ist — nicht „ungültig"."""
    ergebnis = staerke(passwort, bezug)
    if ergebnis["ok"]:
        return True, ""
    if not ergebnis["gruende"]:
        return False, (f"Das Passwort ist zu einfach. Am einfachsten ist es lang: "
                       f"vier Wörter, die sonst niemand zusammen sagt.")
    return False, ("Dieses Passwort geht so nicht — es enthält "
                   + " und ".join(ergebnis["gruende"])
                   + ". Am einfachsten ist es lang: vier Wörter, die sonst niemand zusammen sagt.")


def bezug_der_installation() -> List[str]:
    """Wörter, die in diesem Haus ohnehin jeder kennt."""
    from app import db
    return [w for w in (db.get_setting("admin_username") or "admin",
                        db.get_setting("homepage_headline") or "",
                        (db.get_setting("app_base_url") or "").split("//")[-1].split("/")[0],
                        "photobox", "gallery") if w]
