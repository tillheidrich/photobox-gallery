"""Standard-Rechtstexte (DSGVO) für die Fotobox-Galerie.

Der Betreiber kann Impressum und Datenschutzerklärung im Admin unter
Einstellungen → Rechtliches überschreiben. Sind sie leer, liefern wir hier
Vorlagen aus, die aus den Angaben im selben Formular gebaut werden.

The privacy template describes what this software actually does, one purpose
at a time, and leaves everything only the operator knows (address, hosting
provider, NAS, mail provider, supervisory authority, controller or processor)
to fields in the same admin page; unset fields show as [placeholders]. It is a
template, not legal advice. Every period it quotes is taken from the code —
retention from the event, the logs from the constants read in ``_logs`` — and
tests/test_privacy_template.py fails when one of them changes.

**A placeholder never reaches a guest.** An audit on 15.11.2026 found the raw
template ("[Name des Betreibers]", "[Straße und Hausnummer]") on a live
instance: nobody had noticed that the page was unfinished, because nothing
said so. Now :func:`page_markdown` checks the text that would be shown — the
template or the operator's own text — for anything that looks like a
placeholder (``[Name …]``, ``[OFFEN: …]``, ``[PRÜFEN: …]``). If it finds one, a
guest gets a neutral line ("noch nicht ausgefüllt") instead of the raw text,
the operator sees the full text with a warning on top, and the dashboard says
which page is unfinished (:func:`status`).
"""
from __future__ import annotations

import datetime as dt
import re
from html import escape
from typing import Any, Dict, List, Mapping, Optional
from urllib.parse import urlparse

#: Settings the operator fills in under Einstellungen → Rechtliches.
#: Additive: older instances simply have them empty.
LEGAL_FIELDS = ("legal_operator", "legal_contact_email", "legal_role", "legal_principal",
                "legal_hoster", "legal_external_storage", "legal_mail_provider", "legal_authority",
                # Impressum (15.11.2026)
                "legal_address", "legal_phone", "legal_register", "legal_tax",
                "legal_editorial", "legal_dispute",
                # Privacy: what only the operator can say (15.11.2026)
                "legal_server_logs", "legal_assistant")
ROLES = ("controller", "processor")
KINDS = ("impressum", "datenschutz")

#: What counts as an unfinished spot. Square brackets that open with a capital
#: letter and are not a Markdown link (``[Impressum](/impressum)``) — that is how
#: every placeholder of the templates is written, and how operators mark their
#: own open points (``[OFFEN: …]``, ``[PRÜFEN: …]``, ``[TODO]``). A lower-case
#: ``[x]`` or a footnote ``[1]`` is not matched. A false alarm costs the operator
#: one look at the dashboard; a missed placeholder costs a public page with
#: "[Name des Betreibers]" on it, so the rule leans towards alarming.
PLACEHOLDER_RE = re.compile(r"\[(?:[A-ZÄÖÜ][^\[\]\n]{2,160})\](?![(\[:])")


def placeholders(md: Optional[str]) -> List[str]:
    """The distinct placeholders left in a text, in order of appearance."""
    seen: List[str] = []
    for m in PLACEHOLDER_RE.finditer(md or ""):
        if m.group(0) not in seen:
            seen.append(m.group(0))
    return seen


def unfinished_notice(kind: str, lang: str = "de") -> str:
    """What a guest reads instead of a template that still has gaps.

    Neutral on purpose: no "[placeholder]", no hint at the software, no
    promise. The footer link keeps working, so the page is reachable.
    """
    if lang == "en":
        title = "Legal notice" if kind == "impressum" else "Privacy policy"
        return f"## {title}\n\nThe operator has not completed this page yet."
    title = "Impressum" if kind == "impressum" else "Datenschutzerklärung"
    return f"## {title}\n\nDer Betreiber hat diese Seite noch nicht ausgefüllt."


def admin_warning(found: List[str]) -> str:
    """The block above the text when the operator looks at an unfinished page."""
    shown = ", ".join(escape(p, quote=False) for p in found[:8])
    more = f" (und {len(found) - 8} weitere)" if len(found) > 8 else ""
    return ("> **Nur für dich sichtbar:** Diese Seite ist noch nicht fertig. Gäste sehen statt "
            "des Textes nur „Der Betreiber hat diese Seite noch nicht ausgefüllt“. Offen: "
            f"{shown}{more}. Ausfüllen unter **Einstellungen → Rechtliches**.")


def settings_from_db() -> Dict[str, str]:
    from app import db
    return {k: (db.get_setting(k) or "").strip() for k in LEGAL_FIELDS}


def _txt(value: Any) -> str:
    """Operator or event text goes into Markdown, which passes HTML through."""
    return escape(str(value or "").strip(), quote=False)


def _de_date(t: dt.datetime) -> str:
    return t.strftime("%d.%m.%Y")


# ─── Impressum ────────────────────────────────────────────────

def default_impressum(host: str = "", operator: str = "", *,
                      cfg: Optional[Mapping[str, str]] = None) -> str:
    """Anbieterkennzeichnung nach § 5 DDG, gebaut aus den Angaben im Admin.

    Deliberately left out: the "Haftung für Inhalte … mit größter Sorgfalt"
    boilerplate (a review on 11.11.2026 called it filler — it promises
    nothing and excludes nothing), and the § 18 Abs. 2 MStV line unless the
    operator names someone: a gallery of event photos is not
    journalistic-editorial content, and a line that says it is would be
    wrong in both directions. The link to the EU ODR platform is gone too:
    the platform was switched off on 20.07.2025 (Regulation (EU) 2024/3228).
    """
    cfg = dict(cfg or {})
    op = _txt(cfg.get("legal_operator") or operator) or "[Name des Betreibers]"
    address = _txt(cfg.get("legal_address"))
    mail = _txt(cfg.get("legal_contact_email")) or "[E-Mail-Adresse]"
    phone = _txt(cfg.get("legal_phone"))
    register = _txt(cfg.get("legal_register"))
    tax = _txt(cfg.get("legal_tax"))
    editorial = _txt(cfg.get("legal_editorial"))
    dispute = _txt(cfg.get("legal_dispute"))

    # "Musterstraße 1, 12345 Musterstadt" in one field becomes two lines on the page.
    address_lines = ("\n".join(p.strip() for p in address.split(",") if p.strip())
                     or "[Straße und Hausnummer]\n[PLZ und Ort]")
    lines = ["## Impressum", "", "Angaben gemäß § 5 Digitale-Dienste-Gesetz (DDG)", "",
             f"**{op}**", address_lines, "", "**Kontakt**"]
    if phone:
        lines.append(f"Telefon: {phone}")
    lines.append(f"E-Mail: {mail}")
    if register:
        lines += ["", f"**Registereintrag:** {register}"]
    if tax:
        lines += ["", f"**Umsatzsteuer:** {tax}"]
    if editorial:
        lines += ["", f"**Verantwortlich für journalistisch-redaktionelle Inhalte** nach § 18 Abs. 2 "
                      f"MStV: {editorial}"]
    if dispute:
        lines += ["", f"**Verbraucherstreitbeilegung:** {dispute}"]
    lines += ["", "**Fotos in den Galerien**",
              "Die Aufnahmen in den Galerien entstehen an der Fotobox oder werden von Gästen und "
              "Fotografen hochgeladen. Sie dürfen nur für private Zwecke genutzt werden; die Rechte "
              "der abgebildeten Personen und der Urheber bleiben unberührt. Wer sich auf einem Bild "
              f"findet und es entfernen lassen möchte, schreibt an {mail} — wie das geht und welche "
              "Rechte du hast, steht in der [Datenschutzerklärung](/datenschutz)."]
    return "\n".join(lines) + "\n"


# ─── Datenschutzerklärung ─────────────────────────────────────

def _logs() -> Dict[str, Any]:
    """Periods of every log the gallery keeps, read from where they are enforced."""
    from app import access, ecosystem, protokoll, retention, sharelinks
    return {
        "deletion_days": retention.LOG_RETENTION_DAYS,          # deletion_log
        "admin_days": protokoll.AUFBEWAHRUNG_TAGE,              # admin_vorgaenge, IP shortened
        "share_view_days": 90,                                  # sharelinks.track: DELETE … '-90 days'
        "access_days": access.KEEP_DAYS,                        # access_requests
        "attempt_hours": sharelinks._KEEP_SECONDS // 3600,      # auth_attempts
        "device_log_days": ecosystem.LOG_RETENTION_DAYS,        # device_logs
    }


def _retention_text(event: Optional[Mapping[str, Any]]) -> str:
    from app import db
    if not event:
        return (f"Aufnahmen werden nach der Frist gelöscht, die für das jeweilige Event eingestellt "
                f"ist (Voreinstellung: {db.DEFAULT_RETENTION_DAYS} Tage ab dem Datum des Events). Ist "
                f"für die Galerie ein späteres Ablaufdatum gesetzt, gilt dieses. Gelöscht wird beim "
                f"täglichen Wartungslauf nach Ablauf der Frist, ein Eintrag im Löschprotokoll belegt es. "
                f"Die konkrete Frist steht in der Datenschutzerklärung, die du aus der jeweiligen "
                f"Galerie heraus aufrufst.")
    from app import retention
    name = _txt(event.get("name"))
    days = int(event.get("retention_days") or 0)
    deadline = retention.deadline(dict(event))
    parts = [f"Für die Galerie „{name}“ gilt:"]
    if not retention.auto_delete(dict(event)) or (days <= 0 and not deadline):
        # Switched off at the gallery or by the account default, or a client
        # gallery (app/retention.py): no date, and who deletes instead.
        parts.append("Eine automatische Löschung gibt es für diese Galerie nicht. Die Aufnahmen bleiben "
                     "gespeichert, bis der Betreiber oder der Gastgeber sie löscht oder du die Löschung "
                     "verlangst.")
        return " ".join(parts)
    if days > 0 and deadline:
        parts.append(f"Aufnahmen werden ab dem {_de_date(deadline)} gelöscht (eingestellte Frist: "
                     f"{days} Tage ab dem Datum des Events; ein späteres Ablaufdatum der Galerie "
                     f"geht vor).")
    elif deadline:
        parts.append(f"Aufnahmen werden ab dem {_de_date(deadline)} gelöscht.")
    else:
        parts.append("Für dieses Event ist keine Löschfrist eingestellt; der Betreiber löscht von Hand.")
    parts.append("Gelöscht wird beim täglichen Wartungslauf, ein Eintrag im Löschprotokoll belegt es.")
    return " ".join(parts)


def services_from_db() -> Dict[str, bool]:
    """Which optional outside services this instance has switched on.

    Read from the same settings the features read, so the privacy page
    cannot forget a messenger the operator set up last week.
    """
    from app import db

    def on(key: str) -> bool:
        return bool((db.get_setting(key) or "").strip())

    return {
        "telegram": on("notify_tg_token") and on("notify_tg_chat_id"),
        "discord": on("notify_discord_webhook"),
        "webhook": on("notify_webhook_url"),
    }


def _host_of(url: str) -> str:
    try:
        return urlparse(url).hostname or ""
    except ValueError:
        return ""


def assistant_paragraph(assistant: str, lang: str = "de") -> str:
    """The list item on the admin's AI assistant, German or English, same content.

    One function for both languages so the two cannot drift apart: what an
    extra right opens is said in the same order in each (tests compare them).
    """
    if lang == "en":
        return f"""**AI assistant for the administration:** The operator can have this gallery's
  administration run by an AI assistant through an interface (MCP): {assistant}. Without extra
  rights the assistant sees only administrative data such as the names of events, clients and
  photo booths, settings and counts, but no photos; this includes open access requests for
  password-protected galleries (the guest's name and note, the e-mail address only shortened);
  it can decide them only with “guest photos”. The operator grants extra rights per access,
  explicitly: with “guest photos” the assistant sees and deletes pictures and sees whether a
  picture came from a guest, the operator or a photo booth, can create and read share links that
  lead to the pictures, decides access requests (granting mails the guest the link that opens
  without the password), publishes galleries and sets their password, can retrieve the
  e-mail addresses of guests who signed up for notifications and change the photo booth's
  clean-up, which deletes pictures on its disk; with
  “remote control” it operates a photo booth live, including a small live image of the camera
  (whoever is standing in front of it) and a screenshot, restarts the program or the photo booth's
  PC, shuts it down, locks its screen, takes a test photo or switches the language on its screen,
  and only if that is allowed at the photo booth itself; with “security” it sees and changes where
  QR codes, mails, operator warnings and notifications to other services (webhooks) lead and
  which event a photo booth gives its pictures to, changes the legal notice and
  the privacy notice and sees open pairing requests with the photo booth's network address; with
  “updates” it installs booth software. The interface never hands out credentials. The gallery
  itself does not analyse photos with AI and recognises no faces; if the assistant sees pictures
  with “guest photos”, its provider processes them. Legal basis:
  Art. 6(1)(f) GDPR, interest: being able to manage the gallery while on the road."""
    return f"""**KI-Assistent für die Verwaltung:** Der Betreiber kann die Verwaltung dieser Galerie über
  eine Schnittstelle (MCP) von einem KI-Assistenten bedienen lassen: {assistant}. Ohne
  Zusatzrechte sieht der Assistent nur Verwaltungsdaten wie Namen von Events, Kunden und
  Fotoboxen, Einstellungen und Zählerstände, aber keine Fotos; dazu gehören offene
  Zugriffsanfragen auf passwortgeschützte Galerien (Name und Notiz des Gastes, seine
  E-Mail-Adresse nur gekürzt); entscheiden kann er sie nur mit „Gästefotos“. Zusatzrechte
  vergibt der Betreiber je Zugang ausdrücklich: mit „Gästefotos“ sieht und löscht der Assistent
  Bilder und sieht, ob ein Bild von einem Gast, vom Betreiber oder von einer Fotobox kam, kann
  Freigabe-Links erzeugen und lesen, die zu den Bildern führen, entscheidet Zugriffsanfragen (die
  Freigabe schickt dem Gast per Mail den Link, der ohne Passwort öffnet), veröffentlicht Galerien
  und setzt ihr Passwort, kann die E-Mail-Adressen der Gäste abrufen, die sich für
  Benachrichtigungen eingetragen haben, und das Aufräumen der Fotobox einstellen, das Bilder auf
  ihrer Festplatte löscht; mit „Fernsteuern“ bedient er
  eine Fotobox live, einschließlich eines kleinen Livebilds der Kamera (wer gerade davor steht)
  und eines Bildschirmfotos, startet das Programm oder den PC der Fotobox neu, fährt ihn herunter,
  sperrt den Bildschirm, macht ein Testfoto oder stellt die Sprache am Bildschirm um, und nur,
  wenn das an der Fotobox selbst erlaubt ist; mit „Sicherheit“ sieht und ändert er, wohin
  QR-Codes, Mails, Betreiber-Warnungen und Meldungen an andere Dienste (Webhooks) führen und
  welchem Event eine Fotobox ihre Bilder gibt,
  ändert Impressum und Datenschutzerklärung und sieht
  offene Kopplungsanfragen mit der Netzadresse der Fotobox; mit „Updates“ spielt er Box-Software
  ein. Zugangsdaten gibt die Schnittstelle nie heraus. Die Galerie selbst wertet Fotos nicht mit
  KI aus und erkennt keine Gesichter; sieht der Assistent mit „Gästefotos“ Bilder, verarbeitet
  sie sein Anbieter. Rechtsgrundlage: Art. 6 Abs. 1 lit. f
  DSGVO, Interesse: die Galerie auch von unterwegs verwalten zu können."""


def _retention_text_en(event: Optional[Mapping[str, Any]]) -> str:
    """The English counterpart of ``_retention_text`` for the summary block."""
    from app import db, retention
    if not event:
        return (f"Pictures are deleted after the period set for each event (default: "
                f"{db.DEFAULT_RETENTION_DAYS} days from the event's date), or at a later expiry date of "
                f"the gallery. Open this page from a gallery to see its own period.")
    name = _txt(event.get("name"))
    deadline = retention.deadline(dict(event))
    if not retention.auto_delete(dict(event)) or not deadline:
        return (f"For the gallery “{name}” there is no automatic deletion. The pictures stay stored until "
                f"the operator or the host deletes them, or until you ask for their deletion.")
    return (f"For the gallery “{name}”, pictures are deleted from {deadline.strftime('%Y-%m-%d')} on, "
            f"in the daily maintenance run; an entry in the deletion log records it.")


def _english_summary(event: Optional[Mapping[str, Any]], assistant: str) -> str:
    """What an English visitor reads above the German notice.

    The notice itself exists in German only (its other periods and rights
    are fixed text); the parts that depend on this gallery's settings are
    given here in English as well, with the same content.
    """
    items = [f"- **Pictures:** {_retention_text_en(event)}"]
    if assistant:
        items.append("- " + assistant_paragraph(assistant, "en"))
    return ("## Summary in English\n\nThe privacy notice below is written in German. What depends on this "
            "gallery's settings:\n\n" + "\n".join(items) + "\n\n")


def default_datenschutz(host: str = "", operator: str = "", contact_email: str = "",
                        analytics: bool = False, *, cfg: Optional[Mapping[str, str]] = None,
                        event: Optional[Mapping[str, Any]] = None, analytics_host: str = "",
                        services: Optional[Mapping[str, bool]] = None, lang: str = "de") -> str:
    cfg = dict(cfg or {})
    services = dict(services or {})
    op = _txt(cfg.get("legal_operator") or operator) or "[Name des Betreibers]"
    mail = _txt(cfg.get("legal_contact_email") or contact_email) or "[E-Mail-Adresse für Datenschutzanfragen]"
    address = _txt(cfg.get("legal_address")) or "[Anschrift des Betreibers]"
    role = cfg.get("legal_role") if cfg.get("legal_role") in ROLES else "controller"
    principal = _txt(cfg.get("legal_principal")) or "[Name und Anschrift des Auftraggebers]"
    hoster = _txt(cfg.get("legal_hoster")) or "[Hosting-Anbieter mit Anschrift]"
    storage = _txt(cfg.get("legal_external_storage"))
    mail_provider = _txt(cfg.get("legal_mail_provider"))
    authority = _txt(cfg.get("legal_authority")) or "[zuständige Aufsichtsbehörde mit Anschrift]"
    server_logs = (_txt(cfg.get("legal_server_logs"))
                   or "[Server-Protokolle: ob der Webserver bzw. Reverse-Proxy Zugriffe mit IP-Adresse "
                      "protokolliert und wie lange]")
    assistant = _txt(cfg.get("legal_assistant"))
    logs = _logs()

    if role == "processor":
        controller = (f"Verantwortlich für die Verarbeitung ist **{principal}** (Auftraggeber, "
                      f"z. B. Gastgeber oder Veranstalter). {op} betreibt diese Galerie und die "
                      f"Fotobox **im Auftrag** des Verantwortlichen als Auftragsverarbeiter "
                      f"(Art. 28 DSGVO). Anfragen zu deinen Rechten kannst du an den "
                      f"Verantwortlichen richten oder an {mail}; wir leiten sie weiter.")
    else:
        controller = (f"Verantwortlich für die Datenverarbeitung in dieser Galerie ist {op}, "
                      f"{address}, E-Mail: {mail}. Weitere Angaben stehen im "
                      f"[Impressum](/impressum). Einen Datenschutzbeauftragten müssen wir nicht "
                      f"benennen (Art. 37 DSGVO, § 38 BDSG).")

    analytics_block = ""
    if analytics:
        where = f" auf {_txt(analytics_host)}" if analytics_host else ""
        analytics_block = f"""
- **Reichweitenmessung (Umami{where}):** Gezählt werden Seitenaufrufe und einzelne Klicks
  (z. B. „Gefällt mir“, Download, Teilen), ohne Cookies und ohne Speicherung auf deinem
  Gerät. Aus IP-Adresse und Browserkennung wird eine pseudonyme Sitzungskennung gebildet;
  die IP-Adresse selbst wird nicht gespeichert. Gespeichert werden aufgerufene Seite,
  Herkunftsseite, Land, Browser, Betriebssystem, Gerätetyp und Sprache. Seiten, deren
  Adresse einen Zugangsschlüssel enthält (Freigabe-Links, die Seite deines Durchgangs,
  Bestätigungslinks), werden nicht gemessen. Rechtsgrundlage: Art. 6 Abs. 1 lit. f DSGVO,
  Interesse: zu sehen, ob und wie die Galerie genutzt wird."""

    assistant_block = ("\n- " + assistant_paragraph(assistant, "de")) if assistant else ""

    recipients = [f"- **Hosting:** {hoster} betreibt den Server, auf dem die Galerie läuft, "
                  f"als Auftragsverarbeiter (Art. 28 DSGVO)."]
    if storage:
        recipients.append(f"- **Externer Speicher:** Originaldateien werden nach einer Frist "
                          f"ausgelagert auf: {storage}.")
    recipients += [
        "- **Gastgeber bzw. Auftraggeber:** sieht alle Aufnahmen seines Events und kann sie "
        "herunterladen.",
        "- **Andere Gäste:** Wer Zugang zur Galerie hat (Adresse, QR-Code oder Passwort), sieht "
        "die freigegebenen Aufnahmen und kann sie je nach Einstellung herunterladen und "
        "weitergeben. Ist die Galerie nicht veröffentlicht, sieht jeder Gast nur seinen "
        "eigenen Durchgang.",
    ]
    if mail_provider:
        recipients.append(f"- **E-Mail-Versand:** Benachrichtigungen und Foto-Mails werden über "
                          f"{mail_provider} verschickt.")
    messengers = [n for k, n in (("telegram", "Telegram"), ("discord", "Discord")) if services.get(k)]
    if messengers:
        recipients.append(
            f"- **Hinweise an den Betreiber:** Über {' und '.join(messengers)} erfährt der Betreiber, "
            f"dass neue Aufnahmen hochgeladen oder eine Galerie veröffentlicht wurde. Übermittelt "
            f"werden nur der Name des Events, der Link zur Galerie und gegebenenfalls der Name eines "
            f"Kunden — keine Fotos und keine Angaben über Gäste. Die Anbieter sitzen außerhalb der "
            f"EU; der Name des Events kann Namen enthalten (z. B. eines Brautpaars).")
    recipients.append("- **Bewertung:** Bittet die Galerie um eine Bewertung, führt der Link erst nach "
                      "deinem Klick zur Seite des Bewertungsportals; gezählt wird nur die Zahl der "
                      "Klicks je Tag.")
    recipients.append("- Die Galerie selbst lädt keine Skripte, Schriften oder Karten von fremden "
                      "Servern" + (" (außer der oben genannten Reichweitenmessung)" if analytics else "")
                      + ". Daten werden nicht verkauft und nicht für Werbung weitergegeben. Eine "
                      "Übermittlung in Länder außerhalb der EU findet nur statt, wo oben ausdrücklich "
                      "genannt.")
    recipients_md = "\n".join(recipients)

    summary = _english_summary(event, assistant) if lang == "en" else ""
    return summary + f"""## Datenschutzerklärung

### Verantwortlicher

{controller}

### Was wir verarbeiten, wozu und auf welcher Grundlage

- **Aufnahmen der Fotobox in der Galerie:** Fotos und Videos, die an der Fotobox entstehen,
  werden hochgeladen, angezeigt und zum Download bereitgestellt; Gäste können, wenn der
  Gastgeber es erlaubt, eigene Fotos hinzufügen (Standortdaten werden dabei entfernt).
  Rechtsgrundlage: Art. 6 Abs. 1 lit. f DSGVO. Berechtigtes Interesse: das Interesse des
  Gastgebers und der Gäste, Erinnerungen an die Veranstaltung miteinander zu teilen. Wer an
  die Fotobox tritt und auslöst, entscheidet selbst, ob er fotografiert wird. Ist an der
  Fotobox die Auswahl „In die Galerie“ oder „Nur drucken“ eingeschaltet, entscheidest du das
  vor jeder Aufnahme; „Nur drucken“ bleibt auf der Fotobox und wird nach dem Druck gelöscht.
- **Kinder:** Auf Feiern sind oft Kinder dabei. Für Aufnahmen von Kindern entscheiden die
  Eltern; sie können jederzeit verlangen, dass ein Bild aus der Galerie genommen wird.
- **Vertrag mit dem Auftraggeber:** Kontakt- und Vertragsdaten des Gastgebers bzw.
  Veranstalters und die Bereitstellung der Galerie für ihn. Rechtsgrundlage: Art. 6 Abs. 1
  lit. b DSGVO.
- **Kundenzugang (optional):** Hat der Betreiber dir einen Zugangscode gegeben, speichern wir
  deinen Namen, welche Galerien du sehen darfst, wann du zuletzt angemeldet warst, deine
  Auswahl-Listen und Kommentare. Der Code selbst wird nur als Prüfwert gespeichert.
  Rechtsgrundlage: Art. 6 Abs. 1 lit. b DSGVO.
- **Technische Zugriffsdaten:** IP-Adresse, Zeitpunkt, aufgerufene Adresse und Browser
  werden verarbeitet, um die Galerie auszuliefern und vor Missbrauch zu schützen (z. B.
  Bremse gegen zu viele Fehlversuche). Rechtsgrundlage: Art. 6 Abs. 1 lit. f DSGVO,
  Interesse: sicherer Betrieb.
- **Nutzungszahlen der Galerie:** Aufrufe, Downloads und Teilen werden je Event gezählt —
  ohne IP-Adresse, ohne Kennung, ohne Bezug zu dir. Rechtsgrundlage: Art. 6 Abs. 1 lit. f
  DSGVO, Interesse: dem Gastgeber sagen zu können, wie die Galerie genutzt wurde.
- **„Gefällt mir“ und Auswahl:** Dein Browser bekommt eine zufällige Kennung; Likes werden
  mit dieser Kennung gespeichert. Sie ist **pseudonym**, nicht anonym: Wer Kennung und
  Browser zusammenbringt, kann die Likes zuordnen. Rechtsgrundlage: Art. 6 Abs. 1 lit. f
  DSGVO, Interesse: die Funktion, die du gerade benutzt.
- **„Meine Aufnahmen“:** Welche Durchgänge du an der Fotobox gemacht hast, merkt sich nur
  dein Browser (im Sitzungs-Cookie, höchstens die letzten 20); auf dem Server entsteht
  dafür kein Konto.
- **E-Mail-Benachrichtigung über neue Fotos (optional):** nur nach deiner Bestätigung
  (Double-Opt-In) und nur zu diesem Zweck. Rechtsgrundlage: deine Einwilligung, Art. 6
  Abs. 1 lit. a DSGVO; Widerruf jederzeit über den Link in jeder Mail.
- **Fotos per E-Mail an der Fotobox (optional):** Die Adresse wird verwendet, um dir deine
  Aufnahmen zu schicken. Die Galerie speichert sie dafür nicht; die Fotobox hält sie in
  ihrer Versand-Warteschlange, bis die Mail draußen ist, und löscht sie einen Tag danach
  (ließ sich die Mail nicht zustellen, nach 14 Tagen). Rechtsgrundlage: Art. 6 Abs. 1
  lit. b DSGVO (dein Auftrag).
- **Zugriffsanfrage für eine geschützte Galerie (optional):** Name, E-Mail und Nachricht,
  damit der Betreiber den Zugang gewähren kann. Rechtsgrundlage: Art. 6 Abs. 1 lit. f
  DSGVO, Interesse: deine Anfrage zu beantworten.
- **Freigabe-Links:** Aufrufe und Downloads eines Freigabe-Links werden gezählt; die
  IP-Adresse nur als Prüfwert (Hash mit geheimem Schlüssel), dazu Browser und
  Betriebssystem. Rechtsgrundlage: Art. 6 Abs. 1 lit. f DSGVO, Interesse: zu sehen, ob ein
  Link genutzt wird, und ihn sonst zu widerrufen.{analytics_block}{assistant_block}

### Cookies und lokaler Speicher

| Name | Wo | Wozu | Wie lange |
|---|---|---|---|
| `session` | Cookie | Sprache, Schutz von Formularen, Freischaltung nach Passworteingabe, pseudonyme Kennung für „Gefällt mir“, „Meine Aufnahmen“, Anmeldung mit Kundenzugang | bis du den Browser schließt |
| `pbshare_…` | Cookie | Freischaltung eines Freigabe-Links mit Passwort | 6 Stunden |
| `pb-theme` | lokaler Speicher | Hell- oder Dunkelansicht | bis du ihn löschst |
| `pb-dl-size` | lokaler Speicher | bevorzugte Download-Größe | bis du ihn löschst |
| `pb-bewertung-…` | lokaler Speicher | dass du die Bitte um eine Bewertung schon gesehen hast | bis du ihn löschst |

Diese Cookies und Einträge im lokalen Speicher deines Browsers sind für die Funktionen
nötig, die du aufrufst (§ 25 Abs. 2 Nr. 2 TDDDG); deshalb gibt es kein Cookie-Banner.
Tracking-Cookies werden nicht gesetzt.

### Empfänger und Speicherorte

{recipients_md}

### Speicherdauer

- **Aufnahmen:** {_retention_text(event)}
- **E-Mail-Benachrichtigungen:** Eine nicht bestätigte Anmeldung wird nach 7 Tagen gelöscht.
  Nach der Abmeldung bekommst du keine Mail mehr; die Adresse wird gelöscht, wenn das Event
  gelöscht wird.
- **Zugriffsanfragen:** {logs['access_days']} Tage.
- **Fehlversuchszähler** (IP-Adresse, z. B. bei falschem Passwort): {logs['attempt_hours']} Stunden.
- **Aufrufe von Freigabe-Links:** {logs['share_view_days']} Tage.
- **Löschprotokoll:** drei Jahre ({logs['deletion_days']} Tage). Es enthält keine Bilder und
  keine Kontaktdaten, nur Event-Kennung, Anzahl, Umfang und Zeitpunkt der Löschung — als
  Nachweis, dass gelöscht wurde.
- **Protokoll der Verwaltung:** {logs['admin_days']} Tage, mit gekürzter IP-Adresse (das
  letzte Feld fehlt). Es betrifft Änderungen durch den Betreiber, nicht Gäste.
- **Geräteprotokoll der Fotobox:** {logs['device_log_days']} Tage; es nennt bei einer
  Foto-Mail nur, dass sie verschickt wurde, nicht an wen.
- **Server-Protokolle:** {server_logs}
- **Datensicherungen:** Sicherungen enthalten dieselben Daten und werden nach dem
  eingestellten Turnus überschrieben; gelöschte Aufnahmen verschwinden daraus mit der
  nächsten Runde.

### Deine Rechte

Du hast das Recht auf Auskunft (Art. 15 DSGVO), Berichtigung (Art. 16), Löschung (Art. 17),
Einschränkung der Verarbeitung (Art. 18) und Datenübertragbarkeit (Art. 20). Eine
Einwilligung kannst du jederzeit mit Wirkung für die Zukunft widerrufen (Art. 7 Abs. 3).
Wende dich dazu an {mail}.

> **Widerspruch (Art. 21 DSGVO):** Soweit wir Daten auf Grundlage von Art. 6 Abs. 1 lit. f
> DSGVO verarbeiten — vor allem deine Aufnahmen in der Galerie —, kannst du jederzeit aus
> Gründen, die sich aus deiner besonderen Situation ergeben, widersprechen. Dann nehmen wir
> die Aufnahmen heraus, es sei denn, es gibt zwingende Gründe, die überwiegen. Hat der
> Gastgeber es eingeschaltet, kannst du deine Aufnahmen auch selbst löschen: auf der Seite,
> die der QR-Code deines Durchgangs öffnet. Sonst genügt eine Nachricht an {mail}.

Du kannst dich außerdem bei einer Aufsichtsbehörde beschweren. Zuständig ist:
{authority}.

### Freiwilligkeit und automatisierte Entscheidungen

Die Fotobox zu benutzen, Fotos hochzuladen oder eine E-Mail-Adresse anzugeben ist
freiwillig; die Galerie lässt sich ohne Angaben ansehen. Es gibt keine automatisierte
Entscheidung im Einzelfall und kein Profiling (Art. 22 DSGVO), keine Gesichtserkennung.
"""


def _unwrap(md: str) -> str:
    """Join the template's soft-wrapped lines.

    The page renders Markdown with ``nl2br`` (operators' own texts rely on
    it), so every wrapped source line would become a visible line break.
    List items, quotes and paragraphs are joined; blank lines, headings,
    table rows and new items start a new line as before.
    """
    out: list = []
    for line in md.split("\n"):
        stripped = line.strip()
        prev = out[-1] if out else ""
        starts_block = (not stripped or stripped.startswith(("#", "- ", "|")) or not prev.strip()
                        or prev.lstrip().startswith(("#", "|")))
        if stripped.startswith(">") and prev.lstrip().startswith(">"):
            out[-1] = prev + " " + stripped.lstrip(">").strip()
        elif not starts_block and not stripped.startswith(">"):
            out[-1] = prev + " " + stripped
        else:
            out.append(line)
    return "\n".join(out)


def get_legal_markdown(kind: str, *, custom: Optional[str], host: str = "",
                       operator: str = "", contact_email: str = "",
                       analytics: bool = False, cfg: Optional[Mapping[str, str]] = None,
                       event: Optional[Mapping[str, Any]] = None, analytics_host: str = "",
                       services: Optional[Mapping[str, bool]] = None, lang: str = "de") -> str:
    """Liefert den eingestellten Text oder – wenn leer – die Standard-Vorlage."""
    custom = (custom or "").strip()
    if custom:
        return custom
    if kind == "impressum":
        return default_impressum(host, operator, cfg=cfg)
    return _unwrap(default_datenschutz(host, operator, contact_email, analytics=analytics,
                                       cfg=cfg, event=event, analytics_host=analytics_host,
                                       services=services, lang=lang))


def page_markdown(kind: str, lang: str = "de", *, host: str = "", analytics_src: str = "",
                  event: Optional[Mapping[str, Any]] = None) -> str:
    """The Markdown a page shows, before anyone decides who may see it."""
    from app import db
    cfg = settings_from_db()
    return get_legal_markdown(
        kind,
        custom=db.text_setting(f"{kind}_text", lang),
        host=host,
        operator=cfg.get("legal_operator", ""),
        contact_email=cfg.get("legal_contact_email", ""),
        analytics=bool(analytics_src),
        analytics_host=_host_of(analytics_src),
        cfg=cfg,
        event=event,
        services=services_from_db(),
        lang=lang,
    )


def for_viewer(kind: str, md: str, *, lang: str = "de", is_admin: bool = False) -> str:
    """Guests never see a placeholder; the operator sees the text and what is missing."""
    found = placeholders(md)
    if not found:
        return md
    if is_admin:
        return admin_warning(found) + "\n\n" + md
    return unfinished_notice(kind, lang)


def status() -> Dict[str, List[str]]:
    """Placeholders left per page and language, for the dashboard. Empty = finished.

    Checks German always and English only where an own English text exists:
    without one, English visitors get the German page.
    """
    from app import db
    import os
    src = (db.get_setting("umami_src") or os.environ.get("UMAMI_SRC", "")).strip()
    out: Dict[str, List[str]] = {}
    for kind in KINDS:
        langs = ["de"] + (["en"] if (db.get_setting(f"{kind}_text_en") or "").strip() else [])
        found: List[str] = []
        for lang in langs:
            for p in placeholders(page_markdown(kind, lang, analytics_src=src)):
                if p not in found:
                    found.append(p)
        if found:
            out[kind] = found
    return out
