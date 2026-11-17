# Photobox Booth auf einem Mac bauen

Ergebnis: ein signiertes, notarisiertes `Photobox-Booth-<version>.dmg`, das auf
einem fremden Mac ohne Warnung startet. Das ist der Unterschied zwischen einem
Programm, das man jemandem in die Hand geben kann, und einer Datei, bei der der
Betreiber erst einen Rechtsklick-Trick lernen muss.

## Einmal vorbereiten

1. Apple-Entwicklermitgliedschaft (vorhanden).
2. **Developer ID Application**-Zertifikat erzeugen — Xcode → Settings →
   Accounts → Manage Certificates → „+". Prüfen mit:

   ```
   security find-identity -v -p codesigning
   ```

3. App-spezifisches Kennwort auf appleid.apple.com anlegen und einmal im
   Schlüsselbund hinterlegen:

   ```
   xcrun notarytool store-credentials photobox \
     --apple-id <apple-id> --team-id <TEAMID> --password <app-kennwort>
   ```

   Danach kennt nur der Schlüsselbund dieses Kennwort. Es steht in keiner Datei
   dieses Projekts, und es gehört auch in keine.

## Bauen

```
cd packaging/macos
./build.sh
```

Das Skript prüft erst die Voraussetzungen und bricht mit einem klaren Satz ab,
wenn etwas fehlt — es entsteht keine halb signierte App. Zwischenstände ohne
Apple-Runde: `SKIP_NOTARIZE=1 ./build.sh` (zeigt beim Kunden eine Warnung, taugt
nur zum Ausprobieren).

## Warum ein .app-Bündel statt einer Einzeldatei

Unter Windows ist eine einzelne `.exe` das Bequemste. Auf einem Mac geht das
nicht: Nur ein Bündel hat ein `Info.plist`, und ohne `Info.plist` gibt es keinen
`NSCameraUsageDescription`. Ohne den beendet macOS die Anwendung beim ersten
Kamerazugriff wortlos — kein Dialog, keine Meldung, die Box steht einfach.

## Warum `disable-library-validation`

Das Canon EDSDK ist von Canon signiert, nicht von uns. Unter Hardened Runtime —
den die Notarisierung verlangt — lädt macOS fremd signierte Bibliotheken sonst
nicht. Diese eine Ausnahme in `entitlements.plist` ist der Grund, warum eine
notarisierte Fotobox überhaupt mit einer Canon-Kamera sprechen kann.

## Das SDK kommt nicht mit

Wie unter Windows liegt das Canon-SDK nicht im DMG. Nach der Installation einmal
im Terminal:

```
"/Applications/Photobox Booth.app/Contents/MacOS/photobox-booth" --install-edsdk <entpackter SDK-Ordner>
```

Das SDK wird nie mitgeliefert, auch nicht in einem DMG, das an Dritte geht:
Jeder Betreiber holt es sich selbst bei Canon und hält Canons Bedingungen ein.
Dass die Box mit dem SDK zusammen laufen darf, regelt die Zusatzerlaubnis in
`LICENSE-EXCEPTION` im Projektstamm.

## Beim Kunden prüfen

```
spctl --assess --type open --context context:primary-signature -v Photobox-Booth-<version>.dmg
```

`accepted` heißt: Der Mac eines Fremden öffnet es ohne Rückfrage.
