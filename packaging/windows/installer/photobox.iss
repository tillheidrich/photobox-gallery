; Photobox - Windows installer (Inno Setup 6.4 or newer, also Inno Setup 7;
; built and checked with 6.7.3).
;
; Built by packaging\windows\build.ps1 -Installer, which first builds
; PhotoboxBooth.exe and PhotoboxSync.exe (both are packed), writes THIRD-PARTY-NOTICES.txt from the build environment
; (tools\third_party_notices.py) and the generated pages and images
; (installer\make_installer_files.py), then runs ISCC with the defines below.
; Documentation: docs\INSTALLER.md (German).
;
; What it does, in order:
;   welcome - licence (project licence + German note) - third-party notices -
;   "what this program does" (privacy, generated from tools\outbound_connections.py)
;   - folder - tasks - ready - [stop a running booth, keep a rollback copy of
;   the old .exe, copy files, logon task, optional Windows settings, optional
;   WebView2 Runtime from Microsoft] - finish ("Photobox jetzt starten").
;
; Never touched: the data folder %LOCALAPPDATA%\PhotoboxBooth (profile.json,
; local.json, sessions, logs, the EDSDK folder the operator filled). The
; uninstaller asks before deleting it and keeps it by default.
;
; The free edition never contains a file of the Canon EDSDK; only the edition
; with camera SDK (website only, built outside CI) adds it. See below.

; 6.4 renamed FileCopy to CopyFile; older compilers stop here with a clear
; sentence instead of "Unknown identifier" deep in [Code].
#if Ver < EncodeVer(6, 4, 0)
  #error Inno Setup 6.4 or newer is needed (https://jrsoftware.org/isdl.php).
#endif

#define AppName "Photobox"
#define AppExeName "PhotoboxBooth.exe"
; The sync program alone (for PCs with dslrBooth or LumaBooth) always travels
; with the setup. In its own subfolder: an old installation in C:\Fotobox may
; have a PhotoboxSync.exe of its own next to the booth, perhaps running; the
; setup never overwrites or removes that one. Not started and not put into
; autostart by the setup -- the booth carries its own sync; whoever needs the
; standalone program sets it up with "PhotoboxSync.exe --install".
#define SyncExeName "PhotoboxSync.exe"
#define SyncDir "sync"
#define TaskName "Photobox Booth"
#define LegacyDir "C:\Fotobox"
#define Root "..\..\.."

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif
; Four numbers for the version resource; build.ps1 passes it.
#ifndef AppVersionNumeric
  #define AppVersionNumeric "0.0.0.0"
#endif
#ifndef DistDir
  #define DistDir "..\dist"
#endif
#ifndef BuildDir
  #define BuildDir "build"
#endif
#ifndef OutputDir
  #define OutputDir "..\dist"
#endif

; Two editions, both free of charge (docs/INSTALLER.md):
;   free edition         no define: no Canon file. The only one built in CI
;                        and the only one that is code-signed.
;   edition with camera  CameraSdkDir defined by build.ps1 -WithCameraSdk on
;   SDK                  the build PC: a folder OUTSIDE the repository with
;                        EDSDK\*.dll (Canon's runtime libraries, unmodified)
;                        and CAMERA-SDK-LICENCE.txt. Adds them to {app}\EDSDK
;                        and a second licence page with the end-user terms
;                        that must be accepted (silently only with
;                        /ACCEPTSDKTERMS=1). The booth's load order
;                        (camera/edsdk.py): configured edsdk_dll, then the data
;                        folder (--install-edsdk), then {app}\EDSDK, then a
;                        bundle inside the program. Website only, as a setup
;                        only; never on a CI runner.
; The former "Edition" switch is gone; a build that still passes it stops.
#ifdef Edition
  #error The Edition define is gone. Free edition: no define. Edition with camera SDK: build.ps1 -WithCameraSdk.
#endif
#ifdef CameraSdkDir
  #if GetEnv("GITHUB_ACTIONS") != "" || GetEnv("CI") != ""
    #error The edition with camera SDK is never built on a CI runner.
  #endif
  #define SetupSuffix "-mit-Kamera-SDK"
  #ifndef NoticesFile
    #error The edition with camera SDK needs NoticesFile (notices with the SDK section).
  #endif
#else
  #define SetupSuffix ""
#endif
#ifndef NoticesFile
  #define NoticesFile DistDir + "\THIRD-PARTY-NOTICES.txt"
#endif

#include BuildDir + "\publisher.isi"

[Setup]
; Stable for every version of this product: upgrades and the uninstall entry
; depend on it. Never change it.
AppId={{31612C24-33AA-459D-9FB9-49954DBE144E}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher={#PublisherName}
AppPublisherURL={#PublisherURL}
AppSupportURL={#SupportURL}
AppUpdatesURL={#PublisherURL}/releases
AppContact={#PublisherContact}
AppCopyright=Copyright (C) {#PublisherName}. AGPL-3.0-or-later
; The setup's version resource matches the programs' (version_resource.py):
; one product name and one product version string per build, which a code
; signing service checks (docs/CODE-SIGNING.md). The four-number fields stay
; numeric; the text fields carry VERSION as it is, like the programs do.
VersionInfoVersion={#AppVersionNumeric}
VersionInfoTextVersion={#AppVersion}
VersionInfoCompany={#PublisherName}
VersionInfoDescription={#AppName} Setup
VersionInfoProductName={#AppName}
VersionInfoProductVersion={#AppVersionNumeric}
VersionInfoProductTextVersion={#AppVersion}
VersionInfoCopyright=Copyright (C) {#PublisherName}. AGPL-3.0-or-later
; Per machine by default (C:\Program Files\Photobox); the privileges dialog
; or /CURRENTUSER installs for this user only, without administrator rights.
PrivilegesRequired=admin
PrivilegesRequiredOverridesAllowed=dialog commandline
DefaultDirName={code:DefaultDir}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
UsePreviousAppDir=yes
UsePreviousTasks=yes
DisableWelcomePage=no
DisableDirPage=auto
DisableReadyPage=no
ShowLanguageDialog=auto
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
; Windows 10 1809: the oldest Windows the WebView2 Runtime supports.
MinVersion=10.0.17763
WizardStyle=modern
WizardImageFile={#BuildDir}\wizard-large-100.bmp,{#BuildDir}\wizard-large-200.bmp
WizardSmallImageFile={#BuildDir}\wizard-small-100.bmp,{#BuildDir}\wizard-small-200.bmp
SetupIconFile=..\photobox.ico
UninstallDisplayIcon={app}\{#AppExeName}
UninstallDisplayName={#AppName}
OutputDir={#OutputDir}
OutputBaseFilename=Photobox-Setup-{#AppVersion}{#SetupSuffix}
Compression=lzma2/max
SolidCompression=yes
SetupLogging=yes
SetupMutex=PhotoboxSetup,Global\PhotoboxSetup
; The running booth is stopped by the script (PrepareToInstall), the way the
; admin's "Beenden" does it. The Restart Manager would ask the guest-facing
; window to close or, worse, ask for a reboot.
CloseApplications=no
RestartApplications=no
; Never reboots by itself, also not in /VERYSILENT.
RestartIfNeededByRun=no
AlwaysRestart=no
#ifdef SignToolName
; build.ps1 -Sign defines a sign tool "photobox" (/Sphotobox=...): setup and
; uninstaller are signed with the same certificate as the .exe.
SignTool={#SignToolName}
SignedUninstaller=yes
#endif

[Languages]
Name: "de"; MessagesFile: "compiler:Languages\German.isl"; LicenseFile: "{#BuildDir}\license-de.txt"; InfoBeforeFile: "{#BuildDir}\privacy-de.txt"
Name: "en"; MessagesFile: "compiler:Default.isl"; LicenseFile: "{#BuildDir}\license-en.txt"; InfoBeforeFile: "{#BuildDir}\privacy-en.txt"

[Tasks]
Name: "autostart"; Description: "{cm:TaskAutostart}"; GroupDescription: "{cm:GroupStart}"
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"
Name: "aktivzeit"; Description: "{cm:TaskActiveHours}"; GroupDescription: "{cm:GroupUnattended}"; Check: IsAdminInstallMode
Name: "dauerbetrieb"; Description: "{cm:TaskHardening}"; GroupDescription: "{cm:GroupUnattended}"; Flags: unchecked; Check: IsAdminInstallMode
Name: "lanfreigabe"; Description: "{cm:TaskLanFirewall}"; GroupDescription: "{cm:GroupNetwork}"; Flags: unchecked; Check: IsAdminInstallMode
Name: "webview2"; Description: "{cm:TaskWebView2}"; GroupDescription: "{cm:GroupWebView2}"; Check: WebView2Missing

[Files]
Source: "{#DistDir}\{#AppExeName}"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#DistDir}\{#SyncExeName}"; DestDir: "{app}\{#SyncDir}"; Flags: ignoreversion
Source: "{#NoticesFile}"; DestDir: "{app}"; DestName: "THIRD-PARTY-NOTICES.txt"; Flags: ignoreversion
; The same file once more, for the notices page during setup.
Source: "{#NoticesFile}"; DestName: "THIRD-PARTY-NOTICES.txt"; Flags: dontcopy
#ifdef CameraSdkDir
; Edition with camera SDK: Canon's runtime libraries, unmodified, in the
; EDSDK folder next to the program (searched after edsdk_dll and the data
; folder), and the end-user terms beside the program (with the uninstaller
; data, the marker by which the booth knows the bundle).
Source: "{#CameraSdkDir}\EDSDK\*.dll"; DestDir: "{app}\EDSDK"; Flags: ignoreversion
Source: "{#CameraSdkDir}\CAMERA-SDK-LICENCE.txt"; DestDir: "{app}"; Flags: ignoreversion
; Once more for the licence page during setup.
Source: "{#CameraSdkDir}\CAMERA-SDK-LICENCE.txt"; Flags: dontcopy
#endif
Source: "{#Root}\LICENSE"; DestDir: "{app}"; DestName: "LICENSE.txt"; Flags: ignoreversion
Source: "{#Root}\LICENSE-EXCEPTION"; DestDir: "{app}"; DestName: "LICENSE-EXCEPTION.txt"; Flags: ignoreversion
Source: "{#Root}\THIRD_PARTY.md"; DestDir: "{app}\docs"; Flags: ignoreversion
; Excludes: a tree copied from a Mac carries AppleDouble "._*" files and
; .DS_Store next to the real ones; they must not end up in Program Files.
Source: "{#Root}\docs\lizenzen\*"; Excludes: "._*,.DS_Store"; DestDir: "{app}\docs\lizenzen"; Flags: ignoreversion
Source: "{#Root}\docs\WINDOWS-INSTALLATION.md"; DestDir: "{app}\docs"; Flags: ignoreversion
Source: "{#Root}\docs\INSTALLER.md"; DestDir: "{app}\docs"; Flags: ignoreversion
Source: "{#Root}\docs\WENN-ETWAS-HAKT.md"; DestDir: "{app}\docs"; Flags: ignoreversion
Source: "{#Root}\docs\DATENSCHUTZ-BOX.md"; DestDir: "{app}\docs"; Flags: ignoreversion
Source: "{#Root}\docs\PRIVACY-BOOTH.md"; DestDir: "{app}\docs"; Flags: ignoreversion

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExeName}"; WorkingDir: "{app}"
; The admin page in the default browser: a URL as Filename makes Inno write an
; internet shortcut (.url); IconFilename gives it the booth's icon instead of
; the browser's. 8765 is the default port; a booth with another "port" in
; local.json is reached through the corner of the start screen as always.
Name: "{group}\{cm:IconAdmin}"; Filename: "http://127.0.0.1:8765/admin"; IconFilename: "{app}\{#AppExeName}"
Name: "{group}\{cm:IconSelftest}"; Filename: "{app}\{#AppExeName}"; Parameters: "--doctor"; WorkingDir: "{app}"
Name: "{group}\{cm:IconSync}"; Filename: "{app}\{#SyncDir}\{#SyncExeName}"; WorkingDir: "{app}\{#SyncDir}"
Name: "{group}\{cm:IconManual}"; Filename: "{win}\notepad.exe"; Parameters: """{app}\docs\WINDOWS-INSTALLATION.md"""
Name: "{group}\{cm:IconTrouble}"; Filename: "{win}\notepad.exe"; Parameters: """{app}\docs\WENN-ETWAS-HAKT.md"""
Name: "{group}\{cm:IconPrivacy}"; Filename: "{win}\notepad.exe"; Parameters: """{app}\docs\{cm:PrivacyDoc}"""
Name: "{group}\{cm:UninstallProgram,{#AppName}}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExeName}"; WorkingDir: "{app}"; Tasks: desktopicon

[Run]
; Through the task when there is one: the booth then runs exactly as after a
; logon (and in the logged-on session, also when setup ran over ssh).
Filename: "{sys}\schtasks.exe"; Parameters: "/Run /TN ""{#TaskName}"""; Description: "{cm:LaunchNow}"; Flags: postinstall nowait runhidden skipifsilent; Check: AutostartChosen
Filename: "{app}\{#AppExeName}"; Description: "{cm:LaunchNow}"; Flags: postinstall nowait skipifsilent runasoriginaluser; Check: not AutostartChosen

[UninstallDelete]
; Only what this installer put there or made by itself. Never {app} as a
; whole: with /DIR=C:\Fotobox it may hold photos (storage folder "Bilder"),
; PhotoboxSync.exe or an EDSDK folder from before.
Type: files; Name: "{app}\{#AppExeName}.vorher"

[CustomMessages]
de.GroupStart=Start:
en.GroupStart=Start:
de.TaskAutostart=Photobox bei der Windows-Anmeldung starten (Aufgabe „Photobox Booth“)
en.TaskAutostart=Start Photobox at Windows logon (task "Photobox Booth")
de.GroupUnattended=Windows für den unbeaufsichtigten Betrieb (umkehrbar, die Deinstallation stellt die vorigen Werte her):
en.GroupUnattended=Windows for unattended operation (reversible, uninstalling restores the previous values):
de.TaskActiveHours=Windows-Updates nicht zwischen 10 und 4 Uhr neu starten lassen (Nutzungszeit)
en.TaskActiveHours=No Windows update restarts between 10:00 and 04:00 (active hours)
de.TaskHardening=Dauerbetrieb: kein Standby, Bildschirm/Platte/USB bleiben an, keine Benachrichtigungen, kein Schnellstart
en.TaskHardening=Unattended: no sleep, screen/disk/USB stay on, no notifications, no fast startup
de.GroupNetwork=Netzwerk:
en.GroupNetwork=Network:
de.TaskLanFirewall=Firewall für die WLAN-Gastseite öffnen (Port 8766, nur private Netze) - nur nötig für „Herunterladen ohne Internet“
en.TaskLanFirewall=Open the firewall for the Wi-Fi guest page (port 8766, private networks only) - only for "download without internet"
de.GroupWebView2=Programmfenster:
en.GroupWebView2=Program window:
de.TaskWebView2=Microsoft Edge WebView2-Laufzeit von Microsoft herunterladen und installieren (fehlt auf diesem Rechner; es gelten Microsofts Lizenzbedingungen)
en.TaskWebView2=Download and install the Microsoft Edge WebView2 Runtime from Microsoft (missing on this PC; Microsoft's licence terms apply)
de.IconAdmin=Photobox Verwaltung
en.IconAdmin=Photobox admin
de.IconSelftest=Photobox Selbsttest
en.IconSelftest=Photobox self-test
de.IconSync=Photobox Sync
en.IconSync=Photobox Sync
de.IconManual=Handbuch
en.IconManual=Manual
de.IconTrouble=Wenn etwas hakt
en.IconTrouble=When something goes wrong
de.IconPrivacy=Datenschutz an der Box
en.IconPrivacy=Privacy at the booth
de.PrivacyDoc=DATENSCHUTZ-BOX.md
en.PrivacyDoc=PRIVACY-BOOTH.md
de.LaunchNow=Photobox jetzt starten
en.LaunchNow=Start Photobox now
de.NoticesTitle=Komponenten Dritter
en.NoticesTitle=Third-party components
de.NoticesSubtitle=Photobox enthält Software anderer Autoren. Hier stehen alle mit ihren Lizenzen.
en.NoticesSubtitle=Photobox contains software by other authors. All of them are listed here with their licences.
de.NoticesIntro=Die Datei liegt nach der Installation auch als THIRD-PARTY-NOTICES.txt im Programmordner.
en.NoticesIntro=After installation the file is also in the program folder as THIRD-PARTY-NOTICES.txt.
de.NoticesMissing=THIRD-PARTY-NOTICES.txt konnte nicht gelesen werden. Sie liegt nach der Installation im Programmordner.
en.NoticesMissing=THIRD-PARTY-NOTICES.txt could not be read. It is in the program folder after installation.
de.BoothWontStop=Die laufende Photobox ließ sich nicht beenden. Bitte die Box über Verwaltung → Beenden schließen und das Setup erneut starten.
en.BoothWontStop=The running Photobox could not be stopped. Please close it via admin → quit and run setup again.
de.ReadyUpgrade=Aktualisierung: Eine laufende Photobox wird geordnet beendet; die bisherige Programmdatei bleibt als PhotoboxBooth.exe.vorher liegen.
en.ReadyUpgrade=Upgrade: a running Photobox is stopped in order; the previous program file is kept as PhotoboxBooth.exe.vorher.
de.ReadyData=Datenordner (bleibt unverändert): %1
en.ReadyData=Data folder (left unchanged): %1
de.ReadyLegacy=Bisherige Installation in %1 gefunden: Sie wird an Ort und Stelle aktualisiert; die Aufgabe „Photobox Booth“ zeigt danach auf diesen Ordner.
en.ReadyLegacy=Previous installation found in %1: it is updated in place; the task "Photobox Booth" then points to this folder.
de.ReadyHardening=Dauerbetrieb setzt: kein Standby, Bildschirm bleibt an, Festplatte bleibt an, USB-Energiesparen aus, Bildschirmschoner aus, App-Benachrichtigungen aus, Ruhezustand aus, Schnellstart aus, kein Update-Neustart bei Anmeldung, Nutzungszeit 10-4 Uhr. Jeder vorige Wert wird gemerkt; die Deinstallation (oder Verwaltung → Dauerbetrieb) setzt ihn zurück.
en.ReadyHardening=Unattended operation sets: no sleep, screen stays on, disk stays on, USB power saving off, screen saver off, app notifications off, hibernation off, fast startup off, no update restart while logged on, active hours 10-04. Each previous value is remembered; uninstalling (or admin → unattended operation) restores it.
de.ReadySkipped=Ohne Administratorrechte übersprungen: Nutzungszeit, Dauerbetrieb, Firewall-Freigabe. Nachholen: Setup als Administrator erneut ausführen.
en.ReadySkipped=Skipped without administrator rights: active hours, unattended operation, firewall rule. To add them later, run setup again as administrator.
de.ReadyLegacyElevated=Ordner %1 dürfen auch normale Benutzer beschreiben: Schritte, die das Programm mit Administratorrechten starten (Nutzungszeit, Dauerbetrieb, Aufgabe als Administrator), werden übersprungen. Für sie die Box nach „Programme“ umziehen (Setup mit /DIR= ohne Altordner).
en.ReadyLegacyElevated=Normal users can write to the folder %1: steps that start the program with administrator rights (active hours, unattended operation, task as administrator) are skipped. For them, move the booth to "Program Files" (setup with /DIR= away from the old folder).
de.ElevationSkipped=übersprungen: Programm liegt in einem Ordner, den normale Benutzer beschreiben können
en.ElevationSkipped=skipped: the program lies in a folder that normal users can write to
de.WebView2Unsigned=Die heruntergeladene WebView2-Installationsdatei trägt keine gültige Signatur von Microsoft und wurde nicht ausgeführt. Bitte die WebView2-Laufzeit selbst von Microsoft installieren.
en.WebView2Unsigned=The downloaded WebView2 installer has no valid Microsoft signature and was not run. Please install the WebView2 Runtime from Microsoft yourself.
de.ReadyFirewall=Firewall: keine Regel nötig, die Box ist nur auf diesem Rechner erreichbar (127.0.0.1).
en.ReadyFirewall=Firewall: no rule needed, the booth is reachable from this PC only (127.0.0.1).
de.ReadyWebView2Ok=WebView2-Laufzeit: vorhanden.
en.ReadyWebView2Ok=WebView2 Runtime: present.
de.ReadyWebView2Missing=WebView2-Laufzeit: fehlt. Ohne sie zeigt sich die Box in Chrome oder Edge statt im eigenen Fenster.
en.ReadyWebView2Missing=WebView2 Runtime: missing. Without it the booth shows itself in Chrome or Edge instead of its own window.
de.StepsFailed=Einige Schritte haben nicht geklappt (Einzelheiten im Protokoll %1):%n%n%2
en.StepsFailed=Some steps did not work (details in the log %1):%n%n%2
de.AskDeleteData=Den Datenordner mit allen Aufnahmen der Gäste, Einstellungen und Protokollen ebenfalls löschen?%n%n%1%n%nEmpfohlen: Nein. Weitere Ablageordner und USB-Sticks werden nie angetastet.
en.AskDeleteData=Also delete the data folder with all guests' photos, settings and logs?%n%n%1%n%nRecommended: No. Additional storage folders and USB sticks are never touched.
de.DataKept=Der Datenordner bleibt erhalten: %1
en.DataKept=The data folder is kept: %1
de.SdkTermsTitle=Kamera-SDK: Bedingungen
en.SdkTermsTitle=Camera SDK: terms
de.SdkTermsSubtitle=Diese Fassung enthält die Laufzeitbibliotheken des Canon EOS Digital SDK. Für sie gelten eigene Bedingungen.
en.SdkTermsSubtitle=This edition contains the runtime libraries of the Canon EOS Digital SDK. Terms of their own apply to them.
de.SdkTermsIntro=Bitte lesen. Die Datei liegt nach der Installation als CAMERA-SDK-LICENCE.txt im Programmordner.
en.SdkTermsIntro=Please read. After installation the file is in the program folder as CAMERA-SDK-LICENCE.txt.
de.SdkAccept=Ich akzeptiere diese Bedingungen
en.SdkAccept=I accept these terms
de.SdkDecline=Ich akzeptiere sie nicht (dann bitte die freie Fassung ohne Kamera-SDK verwenden)
en.SdkDecline=I do not accept them (then please use the free edition without the camera SDK)
de.SdkTermsSilent=Diese Fassung enthält das Canon EOS Digital SDK mit eigenen Bedingungen (CAMERA-SDK-LICENCE.txt). Eine stille Installation nimmt sie nicht stillschweigend an: wer sie gelesen hat und annimmt, ruft das Setup mit /ACCEPTSDKTERMS=1 auf. Abgebrochen, nichts wurde installiert.
en.SdkTermsSilent=This edition contains the Canon EOS Digital SDK with terms of its own (CAMERA-SDK-LICENCE.txt). A silent installation does not accept them tacitly: if you have read and accept them, run the setup with /ACCEPTSDKTERMS=1. Stopped, nothing was installed.

[Code]
const
  WV2Client = '{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}';
  { Microsoft's link to the Evergreen Bootstrapper (developer.microsoft.com/microsoft-edge/webview2).
    Downloaded only when the task is chosen; not bundled with this setup. }
  WV2BootstrapperUrl = 'https://go.microsoft.com/fwlink/p/?LinkId=2124703';
  LanRule = 'Photobox Gastseite (WLAN)';
  LanPort = '8766';

var
  NoticesPage: TOutputMsgMemoWizardPage;
#ifdef CameraSdkDir
  { Edition with camera SDK: Canon's end-user terms, to be accepted like the
    licence page (Next stays disabled until "I accept" is chosen). }
  SdkTermsPage: TOutputMsgMemoWizardPage;
  SdkAccept, SdkDecline: TNewRadioButton;
#endif
  BoothWasRunning: Boolean;
  FailedSteps: String;

{ The program's one-off commands (--install, --uninstall, --doctor) wait for
  the Enter key unless PHOTOBOX_NO_PAUSE is set; child processes inherit it. }
function SetEnvironmentVariable(lpName: String; lpValue: String): BOOL;
  external 'SetEnvironmentVariableW@kernel32.dll stdcall';

function DataDir(): String;
begin
  Result := ExpandConstant('{localappdata}\PhotoboxBooth');
end;

function InstallerLog(): String;
begin
  Result := DataDir() + '\logs\installer.log';
end;

procedure Note(const Text: String);
begin
  Log(Text);
  ForceDirectories(DataDir() + '\logs');
  SaveStringToFile(InstallerLog(), GetDateTimeString('yyyy-mm-dd hh:nn:ss', '-', ':') + '  ' + Text + #13#10, True);
end;

{ ---------- WebView2 --------------------------------------------------- }

function WV2Version(RootKey: Integer; const Key: String): String;
var
  Value: String;
begin
  Result := '';
  if RegQueryStringValue(RootKey, Key, 'pv', Value) and (Value <> '0.0.0.0') then
    Result := Value;
end;

{ The registry check Microsoft documents for the Evergreen Runtime:
  HKLM (32-bit view = WOW6432Node) per machine, HKCU per user. }
function WebView2Installed(): Boolean;
begin
  Result := (WV2Version(HKLM32, 'SOFTWARE\Microsoft\EdgeUpdate\Clients\' + WV2Client) <> '') or
            (WV2Version(HKLM64, 'SOFTWARE\Microsoft\EdgeUpdate\Clients\' + WV2Client) <> '') or
            (WV2Version(HKCU, 'Software\Microsoft\EdgeUpdate\Clients\' + WV2Client) <> '');
end;

function WebView2Missing(): Boolean;
begin
  Result := not WebView2Installed();
end;

{ ---------- folders ---------------------------------------------------- }

{ A booth set up before the installer existed lives in C:\Fotobox, and its
  task, ssh scripts and Einrichten.bat point there. Updating it in place is
  the safe path: nothing that refers to C:\Fotobox breaks. /DIR= overrides. }
function DefaultDir(Param: String): String;
begin
  if IsAdminInstallMode() and FileExists('{#LegacyDir}\{#AppExeName}') then
    Result := '{#LegacyDir}'
  else
    Result := ExpandConstant('{autopf}\{#AppName}');
end;

function AutostartChosen(): Boolean;
begin
  Result := WizardIsTaskSelected('autostart');
end;

{ ---------- elevation (final review A12) -------------------------------- }

{ True when Exe lies under Program Files. Only there normal users cannot
  replace the file, so only from there may setup start it with administrator
  rights. The old C:\Fotobox of hand-made booths is writable for every user:
  a program swapped in there would run as administrator. }
function SafeForElevation(const Exe: String): Boolean;
var
  Dir: String;
begin
  Dir := Uppercase(AddBackslash(ExtractFileDir(Exe)));
  Result := (Pos(Uppercase(AddBackslash(ExpandConstant('{commonpf}'))), Dir) = 1);
  if (not Result) and IsWin64() then
    Result := (Pos(Uppercase(AddBackslash(ExpandConstant('{commonpf32}'))), Dir) = 1) or
              (Pos(Uppercase(AddBackslash(ExpandConstant('{commonpf64}'))), Dir) = 1);
end;

{ ---------- the running booth ----------------------------------------- }

{ Number of PhotoboxBooth.exe processes (watcher and booth), -1 = unknown. }
function BoothProcessCount(): Integer;
var
  Locator, Service, Items: Variant;
begin
  try
    Locator := CreateOleObject('WbemScripting.SWbemLocator');
    Service := Locator.ConnectServer('.', 'root\CIMV2');
    Items := Service.ExecQuery('SELECT ProcessId FROM Win32_Process WHERE Name = ''{#AppExeName}''');
    Result := Items.Count;
  except
    Result := -1;
  end;
end;

function ExistingExe(): String;
begin
  Result := ExpandConstant('{app}\{#AppExeName}');
  if not FileExists(Result) then
    Result := '{#LegacyDir}\{#AppExeName}';
  if not FileExists(Result) then
    Result := '';
end;

{ Ends the booth the way the admin's "Beenden" does: --quit (camera handed
  back, exit 0, the watcher stays down). Only if it does not end within 30 s:
  the task is ended and the processes are killed. Returns '' when no booth
  runs any more. AsUser: run --quit as the logged-on user (setup only). }
function StopBooth(AsUser: Boolean): String;
var
  I, Code: Integer;
  Exe: String;
begin
  Result := '';
  if BoothProcessCount() = 0 then
    exit;
  BoothWasRunning := True;
  Exe := ExistingExe();
  { Only an elevated uninstaller would start it with administrator rights;
    a per-user one runs as the user anyway. }
  if (Exe <> '') and (not AsUser) and IsAdminInstallMode() and (not SafeForElevation(Exe)) then begin
    Note('--quit ' + CustomMessage('ElevationSkipped') + ': ' + Exe);
    Exe := '';
  end;
  if Exe <> '' then begin
    Note('Stopping the booth: ' + Exe + ' --quit');
    if AsUser then
      ExecAsOriginalUser(Exe, '--quit', '', SW_HIDE, ewWaitUntilTerminated, Code)
    else
      Exec(Exe, '--quit', '', SW_HIDE, ewWaitUntilTerminated, Code);
    for I := 1 to 30 do begin
      if BoothProcessCount() = 0 then begin
        Note('Booth ended in order.');
        exit;
      end;
      Sleep(1000);
    end;
  end;
  Note('Booth did not end by itself: ending the task and the processes.');
  Exec(ExpandConstant('{sys}\schtasks.exe'), '/End /TN "{#TaskName}"', '', SW_HIDE, ewWaitUntilTerminated, Code);
  Exec(ExpandConstant('{sys}\taskkill.exe'), '/F /T /IM {#AppExeName}', '', SW_HIDE, ewWaitUntilTerminated, Code);
  for I := 1 to 10 do begin
    if BoothProcessCount() <= 0 then
      exit;
    Sleep(1000);
  end;
  Result := CustomMessage('BoothWontStop');
  Note(Result);
end;

{ ---------- signatures (final review A12) ------------------------------- }

{ The Evergreen bootstrapper is downloaded at install time; it runs only when
  Windows says its Authenticode signature is valid AND the signer is
  Microsoft Corporation. PowerShell's Get-AuthenticodeSignature does the
  check, the exit code carries the answer. }
function SignedByMicrosoft(const FileName: String): Boolean;
var
  Code: Integer;
  Cmd: String;
begin
  Cmd := '-NoProfile -NonInteractive -ExecutionPolicy Bypass -Command "' +
    '$s = Get-AuthenticodeSignature -LiteralPath ''' + FileName + '''; ' +
    'if ($s.Status -eq ''Valid'' -and $s.SignerCertificate.Subject -match ''(^|, )O=Microsoft Corporation(,|$)'') ' +
    '{ exit 0 } else { exit 3 }"';
  Result := Exec(ExpandConstant('{sys}\WindowsPowerShell\v1.0\powershell.exe'), Cmd, '', SW_HIDE,
    ewWaitUntilTerminated, Code) and (Code = 0);
  Note('WebView2 bootstrapper signature (Microsoft, valid): ' + IntToStr(Ord(Result)) + ', exit code ' + IntToStr(Code));
end;

{ ---------- steps after copying ---------------------------------------- }

{ Runs the program with one of its one-off commands. IsLast = False: a failure
  is only logged, the caller has another way to try. }
function TryStep(const Title, Params: String; AsUser, IsLast: Boolean): Boolean;
var
  Code: Integer;
  Exe: String;
begin
  Exe := ExpandConstant('{app}\{#AppExeName}');
  Code := -1;
  if (not AsUser) and (not SafeForElevation(Exe)) then begin
    Note('--  ' + Title + ' (' + Params + '): ' + CustomMessage('ElevationSkipped'));
    if IsLast then
      FailedSteps := FailedSteps + '- ' + Title + ' (' + CustomMessage('ElevationSkipped') + ')' + #13#10;
    Result := False;
    exit;
  end;
  if AsUser then
    Result := ExecAsOriginalUser(Exe, Params, '', SW_HIDE, ewWaitUntilTerminated, Code)
  else
    Result := Exec(Exe, Params, '', SW_HIDE, ewWaitUntilTerminated, Code);
  Result := Result and (Code = 0);
  if Result then
    Note('OK  ' + Title + ' (' + Params + ')')
  else begin
    Note('--  ' + Title + ' (' + Params + '): exit code ' + IntToStr(Code));
    if IsLast then
      FailedSteps := FailedSteps + '- ' + Title + #13#10;
  end;
end;

procedure RunStep(const Title, Params: String; AsUser: Boolean);
begin
  TryStep(Title, Params, AsUser, True);
end;

{ The logon task belongs to the user who operates the booth, so it is made
  as that user. A task left by an older "--install" run as administrator may
  refuse that user; then it is replaced from the elevated setup, as
  "--install" did it. }
procedure SetAutostart(const Params: String);
begin
  if not TryStep('Autostart', Params, True, not IsAdminInstallMode()) then
    if IsAdminInstallMode() then
      TryStep('Autostart (als Administrator)', Params, False, True);
end;

procedure RunSystem(const Title, Exe, Params: String);
var
  Code: Integer;
begin
  if Exec(Exe, Params, '', SW_HIDE, ewWaitUntilTerminated, Code) and (Code = 0) then
    Note('OK  ' + Title)
  else begin
    Note('--  ' + Title + ': exit code ' + IntToStr(Code));
    FailedSteps := FailedSteps + '- ' + Title + #13#10;
  end;
end;

procedure InstallWebView2();
var
  Code: Integer;
  Setup: String;
begin
  if WebView2Installed() then
    exit;
  try
    DownloadTemporaryFile(WV2BootstrapperUrl, 'MicrosoftEdgeWebview2Setup.exe', '', nil);
    Setup := ExpandConstant('{tmp}\MicrosoftEdgeWebview2Setup.exe');
    if not SignedByMicrosoft(Setup) then begin
      Note('--  ' + CustomMessage('WebView2Unsigned'));
      FailedSteps := FailedSteps + '- WebView2: ' + CustomMessage('WebView2Unsigned') + #13#10;
      DeleteFile(Setup);
      exit;
    end;
    if Exec(Setup, '/silent /install', '', SW_HIDE, ewWaitUntilTerminated, Code) and (Code = 0) then
      Note('OK  WebView2 Runtime installed by Microsoft''s bootstrapper')
    else begin
      Note('--  WebView2 bootstrapper: exit code ' + IntToStr(Code));
      FailedSteps := FailedSteps + '- WebView2' + #13#10;
    end;
  except
    Note('--  WebView2 download failed: ' + GetExceptionMessage());
    FailedSteps := FailedSteps + '- WebView2 (Download)' + #13#10;
  end;
end;

{ ---------- wizard ------------------------------------------------------ }

function InitializeSetup(): Boolean;
begin
  SetEnvironmentVariable('PHOTOBOX_NO_PAUSE', '1');
  Result := True;
#ifdef CameraSdkDir
  { /SILENT and /VERYSILENT skip the terms page; Canon's terms are never
    accepted on the operator's behalf. Said in the setup log (/LOG), in our
    own log and in a message box (which /SUPPRESSMSGBOXES hides). }
  if WizardSilent() and (ExpandConstant('{param:ACCEPTSDKTERMS|0}') <> '1') then begin
    Note('Stopped: silent installation without /ACCEPTSDKTERMS=1, camera SDK terms not accepted.');
    SuppressibleMsgBox(CustomMessage('SdkTermsSilent'), mbCriticalError, MB_OK, IDOK);
    Result := False;
  end;
#endif
end;

#ifdef CameraSdkDir
procedure SdkChoiceClick(Sender: TObject);
begin
  WizardForm.NextButton.Enabled := SdkAccept.Checked;
end;

#endif
procedure InitializeWizard();
begin
  { Third-party notices, scrollable, right after the project licence. The
    file is UTF-8 with BOM; LoadFromFile reads it as such. Half a megabyte:
    loaded in one go, not line by line. }
  NoticesPage := CreateOutputMsgMemoPage(wpLicense, CustomMessage('NoticesTitle'),
    CustomMessage('NoticesSubtitle'), CustomMessage('NoticesIntro'), '');
  try
    ExtractTemporaryFile('THIRD-PARTY-NOTICES.txt');
    NoticesPage.RichEditViewer.Lines.LoadFromFile(ExpandConstant('{tmp}\THIRD-PARTY-NOTICES.txt'));
  except
    NoticesPage.RichEditViewer.Lines.Text := CustomMessage('NoticesMissing');
  end;
  if NoticesPage.RichEditViewer.Lines.Count = 0 then
    NoticesPage.RichEditViewer.Lines.Text := CustomMessage('NoticesMissing');
#ifdef CameraSdkDir
  SdkTermsPage := CreateOutputMsgMemoPage(NoticesPage.ID, CustomMessage('SdkTermsTitle'),
    CustomMessage('SdkTermsSubtitle'), CustomMessage('SdkTermsIntro'), '');
  ExtractTemporaryFile('CAMERA-SDK-LICENCE.txt');
  SdkTermsPage.RichEditViewer.Lines.LoadFromFile(ExpandConstant('{tmp}\CAMERA-SDK-LICENCE.txt'));
  { Room for the two choices below the text, as on the licence page. }
  SdkTermsPage.RichEditViewer.Height := SdkTermsPage.SurfaceHeight - ScaleY(52);
  SdkAccept := TNewRadioButton.Create(SdkTermsPage);
  SdkAccept.Parent := SdkTermsPage.Surface;
  SdkAccept.Caption := CustomMessage('SdkAccept');
  SdkAccept.Top := SdkTermsPage.RichEditViewer.Top + SdkTermsPage.RichEditViewer.Height + ScaleY(8);
  SdkAccept.Width := SdkTermsPage.SurfaceWidth;
  SdkAccept.Height := ScaleY(17);
  SdkAccept.OnClick := @SdkChoiceClick;
  SdkDecline := TNewRadioButton.Create(SdkTermsPage);
  SdkDecline.Parent := SdkTermsPage.Surface;
  SdkDecline.Caption := CustomMessage('SdkDecline');
  SdkDecline.Top := SdkAccept.Top + SdkAccept.Height + ScaleY(4);
  SdkDecline.Width := SdkTermsPage.SurfaceWidth;
  SdkDecline.Height := ScaleY(17);
  SdkDecline.Checked := True;
  SdkDecline.OnClick := @SdkChoiceClick;
#endif
end;

#ifdef CameraSdkDir
procedure CurPageChanged(CurPageID: Integer);
begin
  if CurPageID = SdkTermsPage.ID then
    WizardForm.NextButton.Enabled := SdkAccept.Checked;
end;

{ Also when Enter is pressed: without "I accept" the setup does not go on. }
function NextButtonClick(CurPageID: Integer): Boolean;
begin
  Result := True;
  if (CurPageID = SdkTermsPage.ID) and not SdkAccept.Checked then
    Result := False;
end;
#endif

function UpdateReadyMemo(Space, NewLine, MemoUserInfoInfo, MemoDirInfo, MemoTypeInfo,
  MemoComponentsInfo, MemoGroupInfo, MemoTasksInfo: String): String;
var
  S: String;
begin
  S := MemoDirInfo + NewLine + NewLine;
  if MemoTasksInfo <> '' then
    S := S + MemoTasksInfo + NewLine + NewLine;
  if FileExists(ExpandConstant('{app}\{#AppExeName}')) then begin
    if CompareText(ExpandConstant('{app}'), '{#LegacyDir}') = 0 then
      S := S + FmtMessage(CustomMessage('ReadyLegacy'), ['{#LegacyDir}']) + NewLine;
    S := S + CustomMessage('ReadyUpgrade') + NewLine;
  end;
  S := S + FmtMessage(CustomMessage('ReadyData'), [DataDir()]) + NewLine;
  if WizardIsTaskSelected('dauerbetrieb') then
    S := S + CustomMessage('ReadyHardening') + NewLine;
  if not IsAdminInstallMode() then
    S := S + CustomMessage('ReadySkipped') + NewLine
  else if not SafeForElevation(ExpandConstant('{app}\{#AppExeName}')) then
    S := S + FmtMessage(CustomMessage('ReadyLegacyElevated'), [ExpandConstant('{app}')]) + NewLine;
  if not WizardIsTaskSelected('lanfreigabe') then
    S := S + CustomMessage('ReadyFirewall') + NewLine;
  if WebView2Installed() then
    S := S + CustomMessage('ReadyWebView2Ok') + NewLine
  else
    S := S + CustomMessage('ReadyWebView2Missing') + NewLine;
  Result := S;
end;

{ Runs after "Install" was pressed (also in /VERYSILENT), before any file is
  touched. A non-empty result aborts setup (exit code 7) without changes. }
function PrepareToInstall(var NeedsRestart: Boolean): String;
begin
  NeedsRestart := False;
  Note('Setup {#AppVersion} into ' + ExpandConstant('{app}') +
       ' (admin mode: ' + IntToStr(Ord(IsAdminInstallMode())) + ')');
  Result := StopBooth(True);
end;

procedure CurStepChanged(CurStep: TSetupStep);
var
  Exe: String;
begin
  if CurStep = ssInstall then begin
    { Rollback copy: the previous .exe stays next to the new one (same name
      the update scripts use). Restoring = renaming it back. }
    Exe := ExpandConstant('{app}\{#AppExeName}');
    if FileExists(Exe) then begin
      if CopyFile(Exe, Exe + '.vorher', False) then
        Note('Previous program kept as ' + Exe + '.vorher')
      else
        Note('--  could not keep a copy of the previous program');
    end;
  end;
  if CurStep = ssPostInstall then begin
    FailedSteps := '';
    { The logon task runs as the logged-on user: created as that user. }
    if WizardIsTaskSelected('autostart') then
      SetAutostart('--autostart an')
    else
      SetAutostart('--autostart aus');
    if IsAdminInstallMode() then begin
      if WizardIsTaskSelected('dauerbetrieb') then
        RunStep('Dauerbetrieb', '--dauerbetrieb empfohlen', False)
      else if WizardIsTaskSelected('aktivzeit') then
        RunStep('Nutzungszeit', '--dauerbetrieb aktivzeit', False);
      if WizardIsTaskSelected('lanfreigabe') then
        RunSystem('Firewall ' + LanRule, ExpandConstant('{sys}\netsh.exe'),
          'advfirewall firewall add rule name="' + LanRule + '" dir=in action=allow protocol=TCP localport=' +
          LanPort + ' profile=private remoteip=localsubnet program="' + ExpandConstant('{app}\{#AppExeName}') + '"');
    end;
    if WizardIsTaskSelected('webview2') then
      InstallWebView2();
    { A silent upgrade of a booth that was running brings it back the way a
      logon does - through the task, into the logged-on session. /START=1
      starts it also when it was not running. }
    if WizardSilent() and AutostartChosen() and
       (BoothWasRunning or (ExpandConstant('{param:START|0}') = '1')) then
      RunSystem('Booth gestartet (Aufgabe)', ExpandConstant('{sys}\schtasks.exe'), '/Run /TN "{#TaskName}"');
    if FailedSteps <> '' then
      SuppressibleMsgBox(FmtMessage(CustomMessage('StepsFailed'), [InstallerLog(), FailedSteps]),
        mbInformation, MB_OK, IDOK);
    Note('Setup finished.');
  end;
end;

{ ---------- uninstall --------------------------------------------------- }

function InitializeUninstall(): Boolean;
begin
  SetEnvironmentVariable('PHOTOBOX_NO_PAUSE', '1');
  Result := True;
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  Code: Integer;
  Exe: String;
begin
  if CurUninstallStep = usUninstall then begin
    StopBooth(False);
    { The program's own reversal: removes the task and the old firewall rule
      of --install, and puts back every Windows setting it changed - only
      those, each to the value it found (boothcore/dauerbetrieb.py). }
    Exe := ExpandConstant('{app}\{#AppExeName}');
    if FileExists(Exe) and ((not IsAdminInstallMode()) or SafeForElevation(Exe)) then begin
      Exec(Exe, '--uninstall', '', SW_HIDE, ewWaitUntilTerminated, Code);
      Note('Uninstall: PhotoboxBooth.exe --uninstall, exit code ' + IntToStr(Code));
    end else if FileExists(Exe) then
      Note('Uninstall: PhotoboxBooth.exe --uninstall ' + CustomMessage('ElevationSkipped') +
           ' - Windows settings of an old --install stay; run it by hand as administrator after checking the folder');
    Exec(ExpandConstant('{sys}\netsh.exe'), 'advfirewall firewall delete rule name="' + LanRule + '"',
      '', SW_HIDE, ewWaitUntilTerminated, Code);
  end;
  if CurUninstallStep = usPostUninstall then begin
    if not DirExists(DataDir()) then
      exit;
    { Default: keep. Silent uninstall keeps it unless /DELETEDATA=1 is given. }
    if (ExpandConstant('{param:DELETEDATA|0}') = '1') or
       (SuppressibleMsgBox(FmtMessage(CustomMessage('AskDeleteData'), [DataDir()]),
          mbConfirmation, MB_YESNO or MB_DEFBUTTON2, IDNO) = IDYES) then begin
      if DelTree(DataDir(), True, True, True) then
        Log('Data folder deleted: ' + DataDir())
      else
        Log('Data folder could not be deleted completely: ' + DataDir());
    end else
      Note(FmtMessage(CustomMessage('DataKept'), [DataDir()]));
  end;
end;
