"""Die Fußzeile — was dort steht, bestimmt der Betreiber.

Impressum und Datenschutzerklärung stehen immer da (in Deutschland Pflicht),
die FAQ nur, wenn es welche gibt — ein Link auf eine leere Seite ist ein
Fehler, kein Angebot. Ihre Texte pflegt der Betreiber unter Einstellungen →
Rechtliches und → FAQ.

Daneben darf er eigene Links setzen, so viele wie er braucht: zum Studio, zur
Preisliste, zur Buchung. Zweisprachig, weil die Galerie es auch ist — bleibt
die englische Beschriftung leer, gilt die deutsche. Früher gab es genau einen
solchen Link (``footer_custom_link_*``); der wandert hier ohne Zutun in die
Liste, damit niemand nach einem Update eine leere Fußzeile hat.
"""
from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from app import db

KEY = "footer_links_json"
MAX = 8


def _sauber(eintrag: Dict[str, Any]) -> Dict[str, Any]:
    url = str(eintrag.get("url") or "").strip()[:500]
    # Nur Ziele, die ein Browser gefahrlos öffnet: „javascript:" und Konsorten
    # haben in einer Fußzeile nichts verloren.
    if url and not (url.startswith(("http://", "https://", "/", "mailto:")) or url.startswith("#")):
        url = "https://" + url
    return {
        "label": str(eintrag.get("label") or "").strip()[:60],
        "label_en": str(eintrag.get("label_en") or "").strip()[:60],
        "url": url,
        "new_tab": bool(eintrag.get("new_tab")),
    }


def parse(raw: Any) -> List[Dict[str, Any]]:
    try:
        daten = json.loads(raw or "[]")
    except (TypeError, ValueError):
        return []
    if not isinstance(daten, list):
        return []
    aus = [_sauber(e) for e in daten if isinstance(e, dict)]
    return [e for e in aus if e["label"] and e["url"]][:MAX]


def dump(items: List[Dict[str, Any]]) -> str:
    return json.dumps(items, ensure_ascii=False)


def custom() -> List[Dict[str, Any]]:
    """Die eigenen Links des Betreibers — inklusive des alten Einzel-Links."""
    items = parse(db.get_setting(KEY))
    if items:
        return items
    label = (db.get_setting("footer_custom_link_label") or "").strip()
    url = (db.get_setting("footer_custom_link_url") or "").strip()
    if label and url:
        return [_sauber({"label": label,
                         "label_en": (db.get_setting("footer_custom_link_label_en") or "").strip(),
                         "url": url, "new_tab": True})]
    return []


def save(items: List[Dict[str, Any]]) -> None:
    db.set_setting(KEY, dump([_sauber(e) for e in items if _sauber(e)["label"] and _sauber(e)["url"]][:MAX]))


def from_form(form) -> List[Dict[str, Any]]:
    """Aus den Zeilen des Formulars eine Liste machen. Leere Zeilen fallen weg."""
    labels = form.getlist("footer_label")
    labels_en = form.getlist("footer_label_en")
    urls = form.getlist("footer_url")
    neue_tabs = form.getlist("footer_new_tab")     # Werte sind die Zeilennummern
    aus = []
    for i, (label, url) in enumerate(zip(labels, urls)):
        if not str(label).strip() or not str(url).strip():
            continue
        aus.append({"label": label, "url": url,
                    "label_en": labels_en[i] if i < len(labels_en) else "",
                    "new_tab": str(i) in neue_tabs})
    return aus[:MAX]


def links(lang: str = "de", event_slug: Optional[str] = None) -> List[Dict[str, Any]]:
    """Was die Fußzeile zeigt: erst die Pflichtseiten, dann die eigenen Links."""
    from urllib.parse import quote

    from app.i18n import t
    aus = []
    if faq_vorhanden():
        aus.append({"label": t("layout.faq") if t("layout.faq") != "layout.faq" else "FAQ",
                    "url": "/faq", "new_tab": False})
    aus.append({"label": t("layout.impressum"), "url": "/impressum", "new_tab": False})
    privacy = f"/datenschutz?event={quote(event_slug)}" if event_slug else "/datenschutz"
    aus.append({"label": t("layout.datenschutz"), "url": privacy, "new_tab": False})
    for eintrag in custom():
        beschriftung = (eintrag["label_en"] if lang == "en" and eintrag["label_en"] else eintrag["label"])
        aus.append({"label": beschriftung, "url": eintrag["url"], "new_tab": eintrag["new_tab"]})
    return aus


def faq_vorhanden() -> bool:
    """Gibt es überhaupt FAQ? Sonst zeigt die Fußzeile den Punkt nicht."""
    try:
        if db.get_faq_items():
            return True
    except Exception:
        pass
    return bool((db.get_setting("faq_intro_text") or "").strip())
