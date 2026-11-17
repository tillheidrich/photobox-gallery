# Die Box-Datei signieren lassen

Ohne Signatur zeigt Windows beim ersten Start von `PhotoboxBooth.exe` „unbekannter
Herausgeber“. Auf dem eigenen Box-PC stört das nicht. Wer die Datei zum Download
anbietet oder an Kundschaft weitergibt, will sie signieren. Diese Seite hat zwei
Anträge zum Einfügen (SignPath Foundation, kostenlos; Certum, kostenpflichtig),
sagt, was Windows der Kundschaft trotz Signatur zeigt, und beschreibt, wie die
Prüfsumme veröffentlicht wird.

**Stand der Angaben: 15.11.2026**, gelesen auf den Seiten der Anbieter (Quellen am
Ende). Preise und Bedingungen ändern sich; was als „laut Anbieter, bitte prüfen“
gekennzeichnet ist, vor dem Kauf oder Antrag auf der Seite des Anbieters nachlesen.
Nichts davon ist Rechtsberatung. Texte in eckigen Klammern `[…]` setzt du selbst ein.

## Was eine Signatur kann und was nicht

* Sie zeigt, **wer** die Datei herausgibt, und sie fällt auf, wenn jemand die Datei
  nach dem Signieren verändert.
* Sie macht die Datei **nicht** sofort unauffällig: Microsoft schreibt, dass auch
  eine signierte, neue Datei eine SmartScreen-Warnung zeigen kann, bis Datei oder
  Zertifikat genug gute Erfahrungen gesammelt haben (Näheres weiter unten).
* Sie ersetzt keine Prüfung des Inhalts. Der Quelltext steht offen, das ist der
  eigentliche Beweis.

## Weg A: SignPath Foundation (kostenlos für Open Source)

Die SignPath Foundation stellt Open-Source-Projekten ein Zertifikat zur Verfügung;
der private Schlüssel liegt bei SignPath in einem Hardware-Sicherheitsmodul. Es muss
kein Ausweis des Entwicklers vorgelegt werden; die Stiftung bürgt mit ihrem Namen
dafür, dass die Datei aus dem offenen Quelltext gebaut wurde (signpath.org).

### Was SignPath laut seinen Bedingungen verlangt

Wörtlich oder sinngemäß von <https://signpath.org/terms> (gelesen am 15.11.2026;
die Seite nennt ihre Bedingungen selbst einen Entwurf):

| Bedingung | Wie sie hier aussieht | Zu prüfen |
|---|---|---|
| Keine Schadsoftware oder unerwünschte Programme | Die Box ist ein Fotoprogramm; sie lädt nur dorthin hoch, wohin der Betreiber sie koppelt. | nein |
| **OSI-anerkannte Lizenz, ohne kommerzielles Dual-Licensing für alle Bestandteile** | AGPL-3.0-oder-später mit einer zusätzlichen Erlaubnis nach Abschnitt 7 für das Canon-SDK (`LICENSE-EXCEPTION`). Die AGPL ist OSI-anerkannt. | **Ja.** Ob die zusätzliche Erlaubnis und der Satz in der README, dass der Herausgeber Lizenzbedingungen ändern könnte, als „kommerzielles Dual-Licensing“ gelten, entscheidet SignPath. Im Antrag offen schreiben (Text unten). |
| Kein proprietärer, nicht quelloffener Bestandteil | Das Canon-SDK steckt nie in der Datei; der Betreiber holt es selbst (`--install-edsdk`). Systembibliotheken dürfen dabei sein. | Im Antrag ausdrücklich erklären. Windows-Laufzeiten (WebView2) sind Systembestandteile. |
| Aktiv gepflegt | Entwicklung läuft. | nein |
| **Schon in der Form veröffentlicht, die signiert werden soll** | Es braucht eine Veröffentlichung (Release) der `.exe` im öffentlichen Repository. | **Ja.** Erst veröffentlichen, dann beantragen. |
| Funktion auf der Download-Seite beschrieben | Eine Seite mit Beschreibung der Box nötig. | **Ja.** |
| Nur eigene Projekte, nur Dateien aus eigenem Quelltext | Ja. | nein |
| **Bau aus dem Quelltext auf nachprüfbare Weise** | Der Bau läuft bisher von Hand auf einem Windows-PC (`Fotobox bauen.bat`). SignPath braucht einen nachvollziehbaren Bau, in der Regel in einer Bau-Pipeline (CI), die SignPath als Herkunft der Datei prüft. | **Ja, die größte Aufgabe.** Eine Windows-Pipeline im öffentlichen Repository fehlt noch. |
| **Jede Veröffentlichung wird von Hand freigegeben** | Ein Mensch gibt jede Fassung frei. | Rollen festlegen (unten). |
| Rollen: Autoren, Prüfer, Freigeber | Bei einer einzelnen Person: dieselbe Person in allen Rollen ist nach dem, was die Seite sagt, nur mit einem „vom ganzen Team als vertrauenswürdig geltenden“ Mitglied möglich. | **Ja.** Eine Einzelperson sollte vorher fragen, wie SignPath das für Ein-Personen-Projekte handhabt. |
| Zwei-Faktor-Anmeldung für alle Beteiligten, bei SignPath und beim Quelltext | Nötig. | Einrichten. |
| Datenschutz: Programme, die Daten an unbestimmte Systeme senden, brauchen eine Datenschutzerklärung, zeigen sie bei der Installation und lassen sich abschalten | Die Box sendet Daten nur an das Ziel, das der Betreiber koppelt. | Erklärung schreiben (Text unten). |
| **Metadaten der Datei gesetzt**, Produktname und Version einheitlich | Der Bau trägt „Photobox“, die Version, Herausgeber und Lizenz in die Datei ein (`photobox-booth.spec`). | Prüfen, dass der Wortlaut zur Seite passt. |
| **Eine „Code signing policy“ auf der Startseite des Projekts** und auf den Download-Seiten | Fehlt noch. | Text unten. |

Der Antrag selbst läuft über <https://signpath.org/apply>. Welche Felder das
Formular abfragt, konnte beim Lesen nicht festgestellt werden (die Seite war
ohne die Formularfelder abrufbar); die folgenden Texte decken ab, was SignPath
nach seinen Bedingungen wissen will, und lassen sich auf die tatsächlichen Felder
verteilen.

### Text zum Einfügen: Beschreibung des Projekts (Englisch)

SignPath arbeitet auf Englisch. Der Text ist so geschrieben, dass er in ein
Freitextfeld passt.

```
Project: Photobox (Photobox Gallery) – booth software for Windows

Photobox is free software for photo booths at events: a kiosk program that
takes pictures with a camera, composes strips and collages, prints them on
a photo printer and uploads them to the operator's own web gallery. It works
without an internet connection (offline first) and has no cloud service of
its own, no accounts and no tracking.

Repository: [URL OF THE PUBLIC REPOSITORY]
Home page and download page: [URL OF THE PROJECT PAGE]
Licence: GNU Affero General Public License v3.0 or later (OSI approved), with
one additional permission under section 7 of the AGPL for the Canon EOS
Digital SDK (file LICENSE-EXCEPTION in the repository). There is no
commercial dual licence and no closed-source edition; the whole program is
under the AGPL. [CHECK AND CONFIRM THIS SENTENCE BEFORE SENDING.]

What we ask you to sign: PhotoboxBooth.exe (the booth, with a watcher process
that restarts it) and, if applicable, PhotoboxSync.exe. Both are built from
this repository with PyInstaller.

Proprietary components: none are part of the signed files. The booth can drive
Canon cameras through the Canon EDSDK, which is proprietary. It is never
shipped, never part of the repository and never inside the signed files; each
operator obtains it from Canon and installs it on their own machine. The
AGPL side of using it together with the booth is settled by the section 7
permission mentioned above.

Network behaviour: the program sends pictures only to the gallery (or the FTP or
SFTP target) that the operator has configured, and sends nothing anywhere else.
It has no telemetry. See the privacy section of the code signing policy.

Maintenance: [LAST RELEASE DATE AND LINK]. Active since [YEAR]; the commit
history is public.

Team: [NUMBER OF PEOPLE]. [ONE SENTENCE ON HOW A SINGLE-PERSON PROJECT MEETS THE
ROLE REQUIREMENT: WHO IS AUTHOR, REVIEWER AND APPROVER.]
```

### Text zum Einfügen: Build aus dem öffentlichen Repository (Englisch)

```
Build: the Windows files are built from the public repository by
[THE BUILD WORKFLOW, e.g. .github/workflows/release-windows.yml] on a
GitHub-hosted Windows runner. The build installs the dependencies pinned in
constraints.txt, runs the test suites, builds with PyInstaller using
packaging/windows/photobox-booth.spec and uploads the unsigned file as a build
artifact. The signing request refers to that artifact, so only files built from
the repository on that runner can be signed. File metadata (product name
"Photobox", file version, original file name, licence) is set by the spec file
and checked by the build. The build fails if the metadata is missing.
Every release needs manual approval before it is signed.
```

Dieser Text beschreibt eine Pipeline, die **noch nicht existiert**. Er darf
erst abgeschickt werden, wenn sie läuft; sonst steht dort etwas Falsches. Die
Pipeline gehört ins Repository unter `.github/workflows/`; sie wird in einem
eigenen Schritt angelegt.

### Text zum Einfügen: „Code signing policy“ für Startseite und Download-Seite (Englisch)

SignPath verlangt, dass diese Abschnitte auf der Startseite des Projekts stehen
(zum Beispiel in der README und auf der Webseite) und auf den Download- und
Release-Seiten, mit der Überschrift „Code signing policy“. Der erste Satz muss
genau so lauten, wie SignPath ihn vorgibt:

```
## Code signing policy

Free code signing provided by [SignPath.io](https://about.signpath.io),
certificate by [SignPath Foundation](https://signpath.org)

**What is signed.** Only files built from the source code in this repository by
the project's own build workflow. Nothing from other projects is signed.

**Team roles.**
- Authors and reviewers: [LIST OF PEOPLE OR LINK TO THE TEAM]
- Approvers: [LIST OF PEOPLE OR LINK TO THE OWNERS]
Every release is approved by hand by an approver before it is signed. All
members use multi-factor authentication for the repository and for SignPath.

**Privacy policy.** [PROPOSED TEXT BELOW]
```

Datenschutz-Absatz für die Box, in dem die Pflichten von SignPath zusammenkommen:

```
This program will not transfer any information to other networked systems unless
specifically requested by the user or the person installing or operating it.
[CHECK THE EXACT SENTENCE ON https://signpath.org/terms BEFORE USING IT.]

When the operator pairs the booth with a gallery, pictures taken at the booth
(and the data needed to deliver them: event, time, print counters, error
messages) are uploaded to that gallery, and only to it. Operators can also add
FTP or SFTP targets and an e-mail or webhook address; nothing is sent to them
unless the operator sets them up. The booth has a mode that sends nothing at
all. There is no telemetry, no advertising and no update check that sends
personal data. Guests' pictures are never sent to the developers.
Third-party components used by the program and their terms are listed in
THIRD_PARTY.md.
```

Den Satz zur Telemetrie vor dem Abschicken am Programm prüfen: Er muss stimmen, nicht
nur gut klingen.

### Rollen, wenn das Projekt eine Person ist

SignPath verlangt Autoren, Prüfer und Freigeber und dass jede Freigabe „ein Mitglied
des Teams“ erteilt, dem das ganze Team vertraut. Bei einer einzelnen Person ist
dieselbe Person in allen drei Rollen. Ob SignPath das für ein Ein-Personen-Projekt
zulässt, steht nicht auf der Seite der Bedingungen. **Vor dem Antrag nachfragen**
(Kontakt über signpath.org), statt es zu erraten. Eine zweite Person als
Freigeber löst das Problem und ist ohnehin die bessere Sicherung.

## Weg B: Certum „Open Source Code Signing“

Certum (Polen, EU) verkauft ein Zertifikat „für Open-Source-Entwickler“. Es wird auf
**eine Person** ausgestellt; der Herausgeber, den Windows zeigt, ist dann der Name
dieser Person mit dem Zusatz „Open Source Developer“. Das ist ein Unterschied zu
SignPath, wo die Stiftung als Herausgeber steht.

Zahlen laut Anbieter, bitte vor dem Kauf prüfen (Preisseiten gelesen am 15.11.2026):

| | Als Karte | In der Cloud (SimplySign) |
|---|---|---|
| Preis laut Anbieter | 69,00 EUR, Chipkarte mit Kartenleser | 49,00 EUR |
| Gültigkeit | auf der Seite des Satzes nicht genannt; in der Angebotsübersicht von Certum steht für „Open Source Code Signing“ 1 Jahr | wie links |
| Ausstellungsdauer laut Angebotsübersicht | 1 bis 5 Tage | wie links |
| Schlüssel | auf der Karte, mindestens RSA 3072 Bit | in der Cloud, mindestens RSA 3072 Bit |
| Grenze | nicht genannt | höchstens 5.000 Signaturen im Monat |
| Rückgabe | die Karte ist laut Seite von der Rückgabe ausgeschlossen | nicht genannt |

Was du brauchst:

1. **Identitätsprüfung der Person**: Ausweis (automatische Prüfung über einen Link
   per Kamera, oder Hochladen eines Scans). Für „Open Source“ zusätzlich die **Anschrift** des
   Zertifikatsinhabers (Anleitung zur Aktivierung bei Certum). Welche Nachweise für das
   Open-Source-Projekt selbst verlangt werden, konnte nicht festgestellt werden: Die
   Seite „Required documents“ von Certum ließ sich nicht laden. **Bitte dort nachlesen.**
2. **Windows-Programm für den Schlüssel**: bei der Karte der Kartenleser und
   „proCertum CardManager“ samt „Certum SignService“; bei der Cloud-Fassung die App
   „SimplySign“ (Handy und Desktop) für die Freigabe jedes Anmeldens.
3. **Signieren**: Ist das Zertifikat im Windows-Speicher eingerichtet, signiert
   `Fotobox bauen.bat` von selbst, sobald `PHOTOBOX_SIGN_THUMBPRINT` den
   Fingerabdruck trägt. Der Bau braucht `signtool.exe` (Windows SDK) und bricht ab,
   wenn die Signatur nicht stimmt ([`WINDOWS-INSTALLATION.md`](WINDOWS-INSTALLATION.md),
   Abschnitt 11). Die Cloud-Fassung verlangt für jede Anmeldung eine Bestätigung auf dem Handy.
   Sie eignet sich daher für das Signieren von Hand auf dem Bau-PC, nicht für eine
   vollautomatische Pipeline.
4. **Zeitstempel**: Certum nennt einen Zeitstempel, damit eine signierte Datei auch
   nach Ablauf des Zertifikats ohne Warnung installierbar bleibt. Beim Signieren den
   Zeitstempel-Server angeben (`signtool sign … /tr <ZEITSTEMPEL-ADRESSE> /td SHA256`).

Weil das Zertifikat auf deinen Namen läuft, steht dein Name in jeder signierten
Datei, die Kunden herunterladen. Überlege vorher, ob du das willst.

### Text für den Antrag bei Certum

Certum fragt, anders als SignPath, eher nach der Person als nach dem Projekt. Wo ein
Freitext die Open-Source-Nutzung erklären soll:

```
I develop and publish free and open-source software (Photobox, a photo booth
program under the GNU AGPL v3 or later). The source code is public at
[URL OF THE PUBLIC REPOSITORY]. I need the certificate to sign the Windows
executables built from that repository, so that users can see who published
them and that they were not altered after signing. The certificate will not be
used for closed-source or commercial third-party software.
```

## Azure Artifact Signing (früher „Trusted Signing“) im Vergleich

Microsoft nennt es für Verteilung außerhalb des Microsoft Store den empfohlenen
Dienst: ab 9,99 US-Dollar im Monat, ohne Hardware-Schlüssel, in Bau-Pipelines
einbindbar, mit Identitätsprüfung durch Microsoft (Seite zur SmartScreen-Reputation,
gelesen am 15.11.2026). Für wen er zur Verfügung steht, hängt vom Land und von der
Art des Herausgebers ab. Das bisherige Dokument [`WINDOWS-INSTALLATION.md`](WINDOWS-INSTALLATION.md)
sagt, für Einzelpersonen gelte er nur in den USA und Kanada; das ist hier **nicht neu
geprüft** und vor jeder Entscheidung bei Microsoft nachzulesen.

## Was die Kundschaft sieht, bis sich ein Ruf aufgebaut hat

Microsoft sagt zur Anzeige „SmartScreen“ (Seite „SmartScreen reputation“, gelesen
am 15.11.2026):

* SmartScreen bewertet zwei Dinge: den Ruf des Herausgebers (ist die Datei signiert,
  ist das Zertifikat bekannt?) und den Ruf der einzelnen Datei (wurde genau diese
  Datei schon ohne Auffälligkeit geladen?).
* Auch signiert kann „eine neu gebaute Datei weiter eine SmartScreen-Warnung zeigen,
  bis Datei oder Zertifikat genug Hinweise auf einen guten Ruf gesammelt haben“.
  Bei einem gültigen Zertifikat (OV oder EV) beim ersten Download nennt die Tabelle
  eine Warnung als „unbekannte App“, **mit angezeigtem, geprüftem Herausgebernamen**.
* Es gibt keine feste Schwelle: Es „kann mehrere Wochen und Hunderte sauberer
  Installationen aus einem breiten Publikum dauern“. Ein Weg, den Ruf von Hand
  einzureichen, existiert nicht.
* Eine **ungesignte** Datei muss bei jeder neuen Fassung neu anfangen; mit
  gleichbleibendem Zertifikat kann der Ruf des Zertifikats auf neue Dateien
  übergehen.
* Ein „EV“-Zertifikat umgeht SmartScreen nicht mehr; ein Aufpreis nur dafür lohnt nicht.
* Der einzige Weg ohne Warnung ist der Microsoft Store.

Für deine Kundschaft heißt das, ehrlich gesagt: **Die Warnung kommt anfangs auch bei
einer signierten Datei.** Der Unterschied: Statt „Unbekannter Herausgeber“ steht
dort ein Name, und mit jeder Fassung sinkt die Warnung.

Sage das auf der Download-Seite, mit einem Satz und dem Bild der Meldung:

> Windows zeigt bei neuen Dateien manchmal „Der Computer wurde durch Windows
> geschützt“. Klick auf „Weitere Informationen“, dann auf „Trotzdem ausführen“.
> Die Datei ist von [HERAUSGEBER] signiert, und die Prüfsumme unten lässt dich
> nachprüfen, dass du die Datei bekommen hast, die wir veröffentlicht haben.

`[Bildschirmfoto: Der SmartScreen-Dialog mit „Weitere Informationen“ aufgeklappt und dem Herausgebernamen, Dateiname docs/bilder/smartscreen-signiert.png; zum Vergleich der Dialog derselben Datei ohne Signatur, smartscreen-unsigniert.png]`

## Die Prüfsumme veröffentlichen

Die Prüfsumme sagt: Die Datei, die du geladen hast, ist Bit für Bit die, die der
Herausgeber veröffentlicht hat. Sie sagt nicht, dass die Datei harmlos ist; das
sagt nur der offene Quelltext. Sie ist keine Signatur, aber sie funktioniert
sofort, ohne Zertifikat, und schadet neben der Signatur nicht.

1. Nach dem Signieren (die Prüfsumme gehört zur **signierten** Datei) in PowerShell:

   ```powershell
   Get-FileHash -Algorithm SHA256 .\PhotoboxBooth.exe
   ```

   Oder in der Eingabeaufforderung: `certutil -hashfile PhotoboxBooth.exe SHA256`.
2. Eine Datei `SHA256SUMS.txt` pflegen, eine Zeile je Datei: `<PRÜFSUMME>  PhotoboxBooth.exe`.
3. Sie **neben** den Download legen und zusätzlich in den Text der Veröffentlichung
   (Release) schreiben, am besten so, dass man sie nicht über denselben Weg
   fälschen kann wie die Datei: Release-Seite im Repository **und** die Download-Seite.
4. Auf der Download-Seite erklären, wie man sie prüft: `Get-FileHash` ausführen und die Zeichen
   vergleichen. Ein Satz genügt.
5. Bei jeder neuen Fassung die Datei und die Prüfsumme gemeinsam ersetzen.
   Frühere Prüfsummen stehen lassen, damit man ältere Fassungen prüfen kann.

## Reihenfolge

1. Öffentliches Repository, Release-Seite und Download-Seite mit Beschreibung.
2. „Code signing policy“-Abschnitt, Datenschutz-Absatz, Rollen, Zwei-Faktor-Anmeldung.
3. Windows-Bau in einer Pipeline im öffentlichen Repository.
4. Eine Fassung ungesignt veröffentlichen, mit Prüfsumme (das ist die Form, die später signiert wird).
5. Antrag bei SignPath; **parallel entscheiden**, ob bis zur Antwort Certum die Brücke ist.
6. Nach der Signatur: Prüfsumme der signierten Datei, Download-Seite aktualisieren, SmartScreen-Hinweis lassen.

## Quellen

Alle gelesen am 15.11.2026. Die Seiten ändern sich.

* SignPath Foundation, Bedingungen für kostenlose Open-Source-Zertifikate und
  Code signing policy: <https://signpath.org/terms>
* SignPath Foundation, Überblick (Zertifikat, Hardware-Sicherheitsmodul, Bürgen
  mit eigenem Namen): <https://signpath.org/>
* SignPath, Antrag („Apply for a free SignPath.io subscription“): <https://signpath.org/apply>
  (die Felder des Formulars wurden nicht gelesen)
* Certum, Open Source Code Signing (Kartensatz, 69 EUR): <https://shop.certum.eu/open-source-code-signing.html>
* Certum, Open Source Code Signing in der Cloud (SimplySign, 49 EUR, Grenze 5.000 Signaturen im Monat):
  <https://shop.certum.eu/open-source-code-signing-on-simplysign.html>
* Certum, Angebotsübersicht (Open Source Code Signing: Gültigkeit 1 Jahr, 1 bis 5 Tage Ausstellung):
  <https://www.certum.eu/en/cert_offer_code_signing/>
* Certum, Anleitung zur Aktivierung (Identitätsprüfung, Anschrift bei „Open Source“):
  <https://files.certum.eu/documents/manual_en/CS-Open_Source_Code_Signing_Certificate_activation.pdf>
* Certum, Seite „Required documents“ (nicht ladbar, bitte selbst lesen):
  <https://www.support.certum.eu/en/required-documents/>
* Microsoft, SmartScreen-Ruf, Signatur und Artifact Signing:
  <https://learn.microsoft.com/windows/apps/package-and-deploy/smartscreen-reputation>
