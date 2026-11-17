"""The two switches the Windows installer calls, and only those.

The installer (packaging/windows/installer/photobox.iss) offers the logon task
and the unattended-operation settings as separate choices. It drives them
through ``--autostart an|aus`` and ``--dauerbetrieb empfohlen``; uninstalling
goes through the existing ``--uninstall``. If one of these names changes, the
installer silently stops doing what its page promises - hence these tests.
"""
from boothcore import __main__ as cli
from boothcore import waechter, winsetup
from boothcore.dauerbetrieb import MASSNAHMEN


def test_the_installer_commands_never_start_a_watcher():
    # A frozen .exe started with these must run the command and end, not
    # start the booth behind the installer's back.
    for argv in (["--autostart", "an"], ["--autostart", "aus"], ["--dauerbetrieb", "empfohlen"],
                 ["--uninstall"], ["--quit"], ["--doctor"]):
        assert waechter.should_watch(argv, frozen=True, environ={}) is False, argv


def test_autostart_reports_success_only_when_every_step_worked(capsys):
    assert cli._autostart_cli(True, setter=lambda on: [("Autostart bei Anmeldung", True, "")]) == 0
    assert cli._autostart_cli(False, setter=lambda on: [("Autostart entfernt", False, "Zugriff verweigert")]) == 1
    assert "Zugriff verweigert" in capsys.readouterr().out


def test_autostart_passes_the_choice_on():
    seen = []
    cli._autostart_cli(False, setter=lambda on: seen.append(on) or [("x", True, "")])
    cli._autostart_cli(True, setter=lambda on: seen.append(on) or [("x", True, "")])
    assert seen == [False, True]


def test_autostart_outside_windows_changes_nothing():
    if winsetup.is_windows():          # pragma: no cover - the suite runs on macOS/Linux
        return
    assert winsetup.set_autostart(True) == [("Windows", False, "Dieser Schritt läuft nur unter Windows.")]


def test_recommended_means_exactly_the_recommended_measures(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("BOOTH_DATA", str(tmp_path))
    cli._dauerbetrieb_cli("empfohlen")
    out = capsys.readouterr().out
    named = [line.split()[1] for line in out.splitlines() if line.strip()]
    assert named == [m.key for m in MASSNAHMEN if m.empfohlen]
    # The edge-swipe lock takes the gesture from the operator too: never by default.
    assert "randgesten" not in named
