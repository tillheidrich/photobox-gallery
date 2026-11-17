"""Der Weg zur Windows-.exe — geprüft am Quelltext, weil es hier kein Windows gibt.

Durchsicht vom 19.09.2026. Der Bau lief auf keinem Windows-Rechner, den ich
anfassen kann; geprüft wird deshalb, was im Bauplan, im Skript und in den
Anleitungen steht. Das ersetzt keinen echten Bau — die offenen Punkte stehen
in der Wiki-Seite „Windows-Auslieferung". Es fängt die Fehler ab, die dort
gefunden wurden und die sonst erst auf dem Fotobox-Rechner auffallen.
"""
from pathlib import Path

import pytest

WURZEL = Path(__file__).resolve().parents[2]
WIN = WURZEL / "packaging" / "windows"
BOOTH_SPEC = (WIN / "photobox-booth.spec").read_text()
BUILD = (WIN / "build.ps1").read_text(encoding="utf-8-sig")
README = (WIN / "README.md").read_text()


def _ohne_kommentare(text: str, zeichen: str = "#") -> str:
    """Nur die Zeilen, die etwas tun.

    Die Begründungen in diesen Dateien zitieren den Fehler, den sie
    beheben — eine Prüfung, die im ganzen Text sucht, findet dann den
    Kommentar und hält den Fehler für zurück.
    """
    zeilen = [z for z in text.splitlines() if not z.lstrip().startswith(zeichen)]
    return "\n".join(zeilen)


SPEC_CODE = _ohne_kommentare(BOOTH_SPEC)
BUILD_CODE = _ohne_kommentare(BUILD)


# ── Der Bauplan ───────────────────────────────────────────────────────

def test_the_brand_fonts_travel_with_the_exe():
    """Ohne sie fällt der Abzug auf Segoe UI zurück — lautlos."""
    assert (WURZEL / "booth" / "boothcore" / "media" / "fonts").is_dir()
    assert "boothcore/media/fonts" in BOOTH_SPEC


def test_every_folder_the_program_reads_at_runtime_is_packed():
    """Jeder ``Path(__file__).parent / "x"``-Ordner muss im Bauplan stehen."""
    import re
    quelle = WURZEL / "booth" / "boothcore"
    fehlt = []
    for datei in quelle.rglob("*.py"):
        text = datei.read_text(encoding="utf-8", errors="replace")
        for name in re.findall(r'Path\(__file__\)[^\n]*?/ "(\w+)"', text):
            ordner = datei.parent / name
            if not ordner.is_dir():
                continue
            ziel = ordner.relative_to(quelle.parent).as_posix()
            if ziel not in BOOTH_SPEC:
                fehlt.append(f"{ziel} (aus {datei.name})")
    assert not fehlt, "Diese Ordner werden zur Laufzeit gelesen, sind aber nicht im Bauplan: " + ", ".join(sorted(set(fehlt)))


def test_the_spec_can_see_the_packages_it_collects():
    """``collect_submodules("boothcore")`` lieferte immer eine leere Liste."""
    assert "_sys.path.insert" in SPEC_CODE
    assert SPEC_CODE.index("_sys.path.insert") < SPEC_CODE.index('collect_submodules("boothcore")')


def test_no_spec_pretends_that_onefile_is_an_argument():
    """PyInstaller kennt kein ``onefile``-Argument und verwirft es stumm."""
    for spec in WIN.glob("*.spec"):
        assert "onefile=True" not in _ohne_kommentare(spec.read_text()), spec.name


# ── Das Bauskript ─────────────────────────────────────────────────────

def test_the_requirements_are_installed_from_the_folder_they_point_at():
    """``-e ../sync`` löst pip gegen das Arbeitsverzeichnis auf, nicht gegen die Datei."""
    anforderungen = (WURZEL / "booth" / "requirements.txt").read_text()
    if "../sync" not in anforderungen:
        pytest.skip("Der relative Verweis ist weg — dann ist auch die Falle weg")
    stelle = BUILD_CODE.index("booth\\requirements.txt")
    davor = BUILD_CODE[:stelle]
    assert 'Push-Location "$Root\\booth"' in davor, \
        "pip läuft aus dem falschen Ordner und installiert gar nichts"


def test_a_failing_step_stops_the_build():
    """Sonst ist die Fehlermeldung die vom Beipackzettel, 30 Zeilen später."""
    assert "$LASTEXITCODE" in BUILD_CODE
    for schritt in ("pip install -r", "PyInstaller --noconfirm"):
        stelle = BUILD_CODE.index(schritt)
        zeile = BUILD_CODE[BUILD_CODE.rindex("\n", 0, stelle):stelle]
        assert "Invoke-Schritt" in zeile, f"Ungeprüft: {schritt}"


def test_the_version_floor_for_pyinstaller_survives_powershell():
    """``>`` ist in PowerShell eine Umleitung, kein Teil des Arguments."""
    assert '"pyinstaller>=6.6"' in BUILD_CODE
    assert "install pyinstaller>=6.6" not in BUILD_CODE


def test_the_python_check_asks_for_the_version_not_just_the_launcher():
    assert "py -3.12 -c" in BUILD_CODE


def test_powershell_files_say_which_encoding_they_are_in():
    """Windows PowerShell 5.1 liest eine .ps1 ohne BOM in der ANSI-Codepage."""
    for pfad in (WIN / "build.ps1", WURZEL / "booth" / "scripts" / "install-windows.ps1",
                 WURZEL / "sync" / "scripts" / "install-windows.ps1"):
        roh = pfad.read_bytes()
        if not any(b > 127 for b in roh):
            continue                       # reines ASCII braucht keine BOM
        assert roh.startswith(b"\xef\xbb\xbf"), f"{pfad.name}: Umlaute werden verstümmelt"


# ── Was die Anleitungen versprechen ───────────────────────────────────

def test_the_instructions_point_at_the_tool_that_picks_the_right_dll():
    """Von Hand kopieren überspringt die Architekturprüfung."""
    for text, wo in ((README, "README"), (BUILD, "LIESMICH")):
        assert "--install-edsdk" in text, f"{wo} schickt den Betreiber am Werkzeug vorbei"


def test_the_instructions_are_honest_about_administrator_rights():
    """``powercfg`` und ``netsh`` brauchen Admin — „normaler Benutzer genügt" war falsch."""
    assert "als normaler\n   Benutzer genügt" not in README
    assert "Administrator" in README and "powercfg" in README


def test_there_is_a_way_to_set_up_without_typing_commands():
    """Ein Rechtsklick auf eine .exe bietet kein Feld für Argumente."""
    bat = WIN / "Einrichten.bat"
    assert bat.is_file(), "Ohne diese Datei muss doch jemand eine Eingabeaufforderung öffnen"
    text = bat.read_text(encoding="utf-8-sig")
    for schalter in ("--install", "--install-edsdk", "--doctor", "--uninstall"):
        assert schalter in text, f"{schalter} fehlt im Menü"


# ── Was die .exe zur Laufzeit tut ─────────────────────────────────────

def test_restarting_brings_the_booth_back_when_it_is_an_exe():
    """Aus dem Quellcode startet ``start-booth.bat`` neu — die .exe hat keine Schleife.

    Der Aufgabenplaner trägt sie ohne Wiederholung ein. „Neustart" in der
    Verwaltung schaltete die Box damit endgültig aus, bis jemand physisch
    doppelklickt. Mitten im Event.
    """
    quelle = (WURZEL / "booth" / "boothcore" / "app.py").read_text()
    stelle = quelle.index("def _restart_later")
    block = quelle[stelle:quelle.index("\n    def ", stelle + 10)]
    assert 'getattr(sys, "frozen", False)' in block
    # Exit code 3 as before, now through the orderly exit (lifecycle.py): camera first.
    assert "subprocess.Popen" in block and "self.shutdown(3)" in block


def test_installing_an_update_actually_ends_the_program():
    """Sonst laufen zwei: die alte .exe hält den Port, die neue stirbt beim Binden."""
    quelle = (WURZEL / "booth" / "boothcore" / "updater.py").read_text()
    stelle = quelle.index("def einspielen")
    block = quelle[stelle:quelle.index("\n    def ", stelle + 10)]
    assert "lifecycle.leave(0)" in block, "Das Log versprach das Beenden, der Code tat es nicht"


def test_the_task_scheduler_gets_a_quoted_path():
    """``schtasks /TR`` zerlegt einen Pfad ohne Anführungszeichen am ersten Leerzeichen."""
    import sys as _s
    from boothcore import winsetup as bw
    from photobox_sync import winsetup as sw
    for modul, wie in ((bw, "Box"), (sw, "Sync")):
        alt = getattr(_s, "frozen", None)
        _s.frozen = True
        try:
            pfad = modul.program_path()
        finally:
            if alt is None:
                del _s.frozen
            else:
                _s.frozen = alt
        assert pfad.startswith('"') and pfad.endswith('"'), f"{wie}: {pfad}"


def test_the_desktop_is_asked_for_not_guessed():
    """Mit OneDrive-Ordnerschutz gibt es %USERPROFILE%\\Desktop nicht mehr."""
    for pfad in (WURZEL / "booth" / "boothcore" / "winsetup.py",
                 WURZEL / "sync" / "photobox_sync" / "winsetup.py"):
        text = pfad.read_text()
        assert "GetFolderPath('Desktop')" in text, pfad.name
        assert "OneDrive" in text, pfad.name


def test_the_sync_says_something_even_without_a_console():
    """``console=False`` heißt: ``sys.stdout`` ist None und ``print`` schweigt."""
    quelle = (WURZEL / "sync" / "photobox_sync" / "__main__.py").read_text()
    assert "def _sagen(" in quelle
    stelle = quelle.index("if args.install or args.uninstall:")
    block = quelle[stelle:stelle + 400]
    assert "_sagen(" in block and "print(winsetup.report" not in block


# ── Einrichten und wieder ausrichten (20.09.2026) ──────────────────────
# Gefunden beim Abgleich mit PSPhotoBox: install() hat fünf powercfg-Aufrufe
# abgesetzt, ohne den Istwert zu lesen, und uninstall() hat sie nie
# zurückgenommen. Wer die Box auf seinem Arbeitsrechner ausprobiert hatte,
# hatte danach dauerhaft kein Standby mehr — und keinen Hinweis darauf, warum.

def _fake_windows_winsetup(monkeypatch, tmp_path):
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from boothcore import dauerbetrieb as db, winsetup
    from tests.fake_windows import FakeWindows

    w = FakeWindows()
    w.setze_power(db.SUB_SLEEP, db.STANDBYIDLE, 1800)
    w.setze_power(db.SUB_VIDEO, db.VIDEOIDLE, 600)
    w.setze_power(db.SUB_DISK, db.DISKIDLE, 1200)
    w.setze_power(db.SUB_USB, db.USBSELECTIVE, 1)
    w.setze_registry(r"HKCU\Control Panel\Desktop", "ScreenSaveActive", "1", "REG_SZ")
    w.setze_registry(r"HKCU\SOFTWARE\Microsoft\Windows\CurrentVersion\PushNotifications",
                     "ToastEnabled", "1")
    w.setze_registry(r"HKLM\SYSTEM\CurrentControlSet\Control\Power", "HibernateEnabled", "1")
    w.setze_registry(r"HKLM\SYSTEM\CurrentControlSet\Control\Session Manager\Power",
                     "HiberbootEnabled", "1")

    monkeypatch.setattr(winsetup, "is_windows", lambda: True)
    monkeypatch.setattr(winsetup, "_run", lambda args: (True, ""))
    monkeypatch.setattr(winsetup, "_autostart", lambda t: (True, ""))
    monkeypatch.setattr(winsetup, "_shortcut", lambda t, name="Fotobox": (True, ""))
    monkeypatch.setattr(winsetup, "_firewall", lambda p: (True, ""))
    monkeypatch.setattr(winsetup, "_dauerbetrieb",
                        lambda: db.Dauerbetrieb(tmp_path / "dauerbetrieb.json",
                                                run=w.run, windows=True))
    return winsetup, w, db


def test_einrichten_schaltet_das_energiesparen_ab(monkeypatch, tmp_path):
    winsetup, w, db = _fake_windows_winsetup(monkeypatch, tmp_path)
    winsetup.install(port=8765)
    assert w.power[(db.SUB_SLEEP, db.STANDBYIDLE)][0] == 0
    assert w.power[(db.SUB_USB, db.USBSELECTIVE)][0] == 0


def test_rueckgaengig_stellt_die_energieeinstellungen_wieder_her(monkeypatch, tmp_path):
    """Der eigentliche Fehler: „Einrichtung rückgängig machen“ hat das nie getan."""
    winsetup, w, db = _fake_windows_winsetup(monkeypatch, tmp_path)
    vorher = dict(w.power)
    winsetup.install(port=8765)
    assert w.power != vorher, "Der Test misst nichts, wenn install() nichts ändert"
    winsetup.uninstall()
    assert w.power == vorher, "Der Rechner bleibt mit abgeschaltetem Standby zurück"


def test_rueckgaengig_fasst_nichts_an_was_die_box_nicht_verstellt_hat(monkeypatch, tmp_path):
    winsetup, w, db = _fake_windows_winsetup(monkeypatch, tmp_path)
    schluessel = (r"hklm\software\policies\microsoft\windows\edgeui", "allowedgeswipe")
    w.registry[schluessel] = ("REG_DWORD", "1")   # vom Betreiber selbst gesetzt
    winsetup.install(port=8765)
    winsetup.uninstall()
    assert w.registry[schluessel] == ("REG_DWORD", "1")
