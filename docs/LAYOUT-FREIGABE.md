# Layout-Freigabe durch den Kunden

Dein Kunde (zum Beispiel ein Brautpaar) gestaltet das Drucklayout seiner
Veranstaltung selbst: Er bekommt von dir einen persönlichen Link, sucht sich
eines deiner Layouts aus, trägt seine Texte ein und schickt es ab. Du schaust
drüber und gibst frei. Danach druckt die Box bei diesem Event genau dieses
Layout. Nichts herunterladen, nichts von Hand auf die Box kopieren.

## Vorbereiten: Layouts mit Kundenfeldern

Unter **Druckvorlagen** legst du eigene Vorlagen an wie bisher. Damit der Kunde
etwas eintragen kann, schreibst du in einen Text der Vorlage einen Platzhalter:

| Platzhalter | Was der Kunde einträgt |
|---|---|
| `{feld_namen}` | Namen, z. B. „Mia & Tom“ |
| `{feld_datum}` | Datum, so wie er es gedruckt haben will |
| `{feld_spruch}` | eine Zeile Text |

Andere Namen gehen auch (`{feld_ort}`, nur Kleinbuchstaben, Ziffern und `_`).
`{event_name}` und `{date}` füllt die Box wie immer selbst.

Soll der Kunde ein Logo oder Monogramm hochladen können, gib einer Ebene der
Vorlage den Namen **Logo** (ein Rechteck reicht). Lädt der Kunde ein Logo hoch,
steht es genau in diesem Kasten. Lädt er keins hoch, fällt ein Rechteck weg; ein
Bild, das du dort schon hingelegt hast, bleibt.

Farben, die in der Vorlage als `@hintergrund`, `@schrift`, `@gedaempft` und
`@akzent` stehen, folgen der Farbwahl des Kunden.

## Einschalten und Link verschicken

1. Event öffnen, unter **Galerie-Einstellungen** „Layout-Freigabe durch Kunden“
   anhaken und speichern. Ohne diesen Haken gibt es die Karte nicht, und jeder
   Link des Events zeigt nur „Dieser Link gilt nicht (mehr)“.
2. In der Karte **Layout vom Kunden gestalten lassen** wählst du:
   welche Layouts zur Wahl stehen, welche Felder der Kunde ausfüllt (Beschriftung,
   höchstens wie viele Zeichen, Vorgabe), welche Farbsätze und Schriften er
   nehmen darf, ob er ein Logo hochladen darf und wie lange der Link gilt
   (Vorgabe 30 Tage).
3. **Link erzeugen**. Der Link erscheint genau einmal, mit einem fertigen Text
   für Mail oder Messenger. Kopieren und selbst verschicken. Die Galerie schickt
   keine Mail.

Ein neuer Link für dasselbe Event ersetzt den alten sofort.

## Was der Kunde sieht

Eine Seite mit vier Schritten, gebaut fürs Handy:

1. **Layout wählen**: deine angebotenen Layouts als Vorschaubilder.
2. **Texte eintragen**: die Felder, die du freigegeben hast.
3. **Farben und Schrift**: die erlaubten Farbsätze und Schriften, auf Wunsch
   ein Logo (PNG, JPEG oder WebP, höchstens 5 MB).
4. **Vorschau und abschicken**: das Blatt, so wie die Box es druckt, mit
   Beispielbildern. Jede Änderung zeigt sich nach einem Augenblick; der Entwurf
   wird von selbst gespeichert. Ist ein Text breiter als das Blatt, sagt die
   Seite es.

Nach dem Abschicken ist der Entwurf gesperrt, bis du ihn zurückgibst. Der Kunde
sieht je nach Stand: „wartet auf Freigabe“, „zurück an dich“ mit deinem Hinweis,
oder „freigegeben“ mit dem fertigen Blatt.

Hat der Kunde einen Kundencode für `/kunde`, findet er dort zusätzlich die Kachel
**Drucklayout**, ohne den Link zu brauchen.

## Freigeben

In der Karte siehst du, was der Kunde gewählt und eingetragen hat, und rechts
das Blatt aus demselben Renderer wie beim Testdruck.

- **Freigeben** legt aus der Wahl des Kunden eine fertige Vorlage an
  (`kunde-…` unter Druckvorlagen), stellt sie bei diesem Event als einzige eigene
  Vorlage ein und setzt den Farbstil des Events auf die Farbwahl. Die Boxen des
  Events holen beides beim nächsten Abgleich. Probier es danach mit einem
  Testdruck aus dem Vorlagen-Editor.
- **Zurückgeben mit Hinweis** schickt den Entwurf mit deinem Satz an den Kunden
  zurück; er kann wieder ändern und neu abschicken.
- **Link sperren** beendet den Link sofort.
- **Zuweisung aufheben** nimmt das freigegebene Layout wieder vom Event; die
  Vorlage selbst bleibt.

Alles davon steht im Änderungsprotokoll, der Link nie.

## Gut zu wissen

- Der Link ist das Geheimnis: Wer ihn hat, kann den Entwurf ändern, solange er
  nicht abgeschickt ist. Er gilt nur für dieses eine Event und läuft ab.
- Logos bleiben bis zur Freigabe außerhalb der Box-Medien und werden ohne
  Metadaten neu gespeichert.
- Was der Kunde tippt, wird genau so gedruckt, auch geschweifte Klammern.
