"""The Windows setup script, checked where it can be checked: on any machine.

Inno Setup only runs on Windows, so nothing here compiles photobox.iss. What
is checked is everything a typo would break silently: files it packs exist,
the AppId never changes, both languages have every text, it only calls
commands the program has, it never reboots and never packs a Canon file.
"""
import re
import sys
from pathlib import Path

import pytest
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "booth" / "tests"))
from _pages import page  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
ISS = ROOT / "packaging" / "windows" / "installer" / "photobox.iss"
BUILD_PS1 = ROOT / "packaging" / "windows" / "build.ps1"
APP_ID = "{{31612C24-33AA-459D-9FB9-49954DBE144E}"


@pytest.fixture(scope="module")
def iss() -> str:
    raw = ISS.read_bytes()
    assert raw.startswith(b"\xef\xbb\xbf"), "photobox.iss needs a UTF-8 BOM, it carries umlauts"
    return raw.decode("utf-8-sig")


def _section(text: str, name: str) -> str:
    m = re.search(rf"^\[{name}\]\s*$(.*?)(?=^\[[A-Za-z]+\]\s*$|\Z)", text, re.S | re.M)
    return m.group(1) if m else ""


def test_the_app_id_never_changes(iss):
    # Upgrades, the uninstall entry and /DIR memory all hang on it.
    assert f"AppId={APP_ID}" in iss


def test_every_packed_repository_file_exists(iss):
    sources = re.findall(r'Source:\s*"\{#Root\}\\([^"]+)"', _section(iss, "Files"))
    assert sources
    for rel in sources:
        rel = rel.replace("\\", "/")
        if rel.endswith("/*"):
            assert (ROOT / rel[:-2]).is_dir(), rel
            assert any((ROOT / rel[:-2]).iterdir()), rel
        else:
            assert (ROOT / rel).is_file(), rel


def test_every_generated_file_is_made_by_the_generator(iss, tmp_path, monkeypatch):
    sys.path.insert(0, str(ROOT / "packaging" / "windows" / "installer"))
    import make_installer_files as mif
    monkeypatch.setattr(mif, "OUT", tmp_path)
    assert mif.main() == 0
    for rel in re.findall(r"\{#BuildDir\}\\([\w.\-]+)", iss):
        assert (tmp_path / rel).is_file(), rel
    assert re.search(r'#include BuildDir \+ "\\publisher\.isi"', iss)
    privacy = (tmp_path / "privacy-de.txt").read_text(encoding="utf-8-sig")
    assert "127.0.0.1" in privacy and "Telemetrie" in privacy
    licence = (tmp_path / "license-de.txt").read_text(encoding="utf-8-sig")
    assert "kein Rechtsrat" in licence and "GNU AFFERO GENERAL PUBLIC LICENSE" in licence
    assert "Abschnitt 7" in licence and "Canon EOS Digital SDK" in licence


def test_both_languages_have_every_message(iss):
    defined = {}
    for lang, key in re.findall(r"^(de|en)\.(\w+)=", _section(iss, "CustomMessages"), re.M):
        defined.setdefault(key, set()).add(lang)
    used = set(re.findall(r"\{cm:(\w+)", iss)) | set(re.findall(r"CustomMessage\('(\w+)'\)", iss))
    builtin = {"CreateDesktopIcon", "AdditionalIcons", "UninstallProgram", "LaunchProgram"}
    for key in used - builtin:
        assert defined.get(key) == {"de", "en"}, f"{key}: {defined.get(key)}"
    for key, langs in defined.items():
        assert langs == {"de", "en"}, key
        assert key in used, f"{key} is defined but never used"


def test_both_languages_get_their_licence_and_privacy_page(iss):
    langs = _section(iss, "Languages")
    for lang in ("de", "en"):
        line = next(l for l in langs.splitlines() if l.startswith(f'Name: "{lang}"'))
        assert f"license-{lang}.txt" in line and f"privacy-{lang}.txt" in line


def test_the_setup_only_calls_switches_the_program_has(iss):
    main = (ROOT / "booth" / "boothcore" / "__main__.py").read_text(encoding="utf-8")
    sys.path.insert(0, str(ROOT / "booth"))
    from boothcore.waechter import ONE_OFF
    calls = set(re.findall(r"'(--[a-z-]+)(?: [a-z]+)?'", iss)) | set(re.findall(r'"(--[a-z-]+)"', iss))
    assert {"--quit", "--autostart", "--dauerbetrieb", "--uninstall", "--doctor"} <= calls
    for flag in calls:
        assert f'"{flag}"' in main, f"{flag} is not an option of boothcore"
        # A one-off command must never start the watcher (and the booth) behind setup's back.
        assert flag in ONE_OFF, f"{flag} is missing from waechter.ONE_OFF"
    assert "'--autostart an'" in iss and "'--autostart aus'" in iss
    assert "'--dauerbetrieb empfohlen'" in iss and "'--dauerbetrieb aktivzeit'" in iss


def test_one_off_commands_do_not_wait_for_a_key(iss):
    # --uninstall and --doctor wait for Enter unless PHOTOBOX_NO_PAUSE is set.
    assert iss.count("SetEnvironmentVariable('PHOTOBOX_NO_PAUSE', '1')") == 2


def test_it_never_reboots_on_its_own(iss):
    assert "RestartIfNeededByRun=no" in iss and "AlwaysRestart=no" in iss
    assert "restartreplace" not in iss.lower()
    assert "NeedsRestart := False" in iss
    assert "CloseApplications=no" in iss


def test_no_canon_file_ever(iss):
    files = _section(iss, "Files")
    assert "EDSDK" not in files.upper() and "EDSIMAGE" not in files.upper()
    assert '#if Edition != "community"' in iss and "#error" in iss


def test_the_data_folder_is_never_removed_without_asking(iss):
    uninstall_delete = _section(iss, "UninstallDelete")
    assert "{#AppExeName}.vorher" in uninstall_delete
    assert "{localappdata}" not in uninstall_delete and "filesandordirs" not in uninstall_delete.lower()
    assert "Type: dirifempty" not in uninstall_delete
    # Asked with "No" as the default answer, which is also the silent answer.
    assert "MB_YESNO or MB_DEFBUTTON2, IDNO" in iss
    assert "{param:DELETEDATA|0}" in iss


def test_hardening_is_off_by_default_and_admin_only(iss):
    tasks = {m.group(1): m.group(0) for m in re.finditer(r'^Name: "(\w+)".*$', _section(iss, "Tasks"), re.M)}
    assert set(tasks) == {"autostart", "desktopicon", "aktivzeit", "dauerbetrieb", "lanfreigabe", "webview2"}
    for name in ("dauerbetrieb", "lanfreigabe"):
        assert "unchecked" in tasks[name] and "IsAdminInstallMode" in tasks[name]
    assert "unchecked" not in tasks["autostart"]
    assert "WebView2Missing" in tasks["webview2"]


def test_the_only_firewall_rule_is_the_optional_guest_page(iss):
    assert iss.count("advfirewall firewall add rule") == 1
    assert "profile=private" in iss and "LanPort = '8766'" in iss
    assert "advfirewall firewall delete rule" in iss


def test_webview2_is_downloaded_from_microsoft_never_packed(iss):
    assert "go.microsoft.com/fwlink/p/?LinkId=2124703" in iss
    assert "MicrosoftEdgeWebview2Setup" not in _section(iss, "Files")
    assert "{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}" in iss


def test_per_machine_by_default_per_user_allowed(iss):
    assert "PrivilegesRequired=admin" in iss
    assert "PrivilegesRequiredOverridesAllowed=dialog commandline" in iss
    assert "{autopf}" in iss and "C:\\Fotobox" in iss


def test_the_documented_task_names_are_the_real_ones(iss):
    doc = (ROOT / "docs" / "INSTALLER.md").read_text(encoding="utf-8")
    for name in re.findall(r'^Name: "(\w+)"', _section(iss, "Tasks"), re.M):
        assert f"`{name}`" in doc, name


def test_the_build_script_offers_installer_signing_and_checksums():
    text = BUILD_PS1.read_text(encoding="utf-8-sig")
    for needed in ("[switch]$Installer", "[switch]$Sign", "JRSoftware.InnoSetup", "SHA256SUMS.txt",
                   "third_party_notices.py", "make_installer_files.py", "PHOTOBOX_LEGAL_DIR",
                   "PHOTOBOX_SIGN_COMMAND", "SignToolName"):
        assert needed in text, needed


def test_the_booth_spec_packs_the_notices_for_the_about_card():
    spec = (ROOT / "packaging" / "windows" / "photobox-booth.spec").read_text(encoding="utf-8")
    assert "PHOTOBOX_LEGAL_DIR" in spec and "boothcore/web/static/legal" in spec
    admin = page(ROOT / "booth" / "boothcore" / "web" / "admin.html")
    assert "/static/legal/THIRD-PARTY-NOTICES.txt" in admin and "/static/legal/publisher.json" in admin


# ── Final review A12 (15.11.2026) ─────────────────────────────────────

def _function(iss: str, name: str) -> str:
    m = re.search(rf"^(?:function|procedure) {name}\b.*?^end;", _section(iss, "Code"), re.S | re.M)
    assert m, name
    return m.group(0)


def test_the_webview2_bootstrapper_runs_only_with_a_valid_microsoft_signature(iss):
    body = _function(iss, "InstallWebView2")
    check = body.index("SignedByMicrosoft(")
    run = body.index("'/silent /install'")
    assert check < run, "the signature is checked before the bootstrapper runs"
    verify = _function(iss, "SignedByMicrosoft")
    assert "Get-AuthenticodeSignature" in verify and "'Valid'" in verify
    assert "O=Microsoft Corporation" in verify
    assert "WebView2Unsigned" in body
    for lang in ("de", "en"):
        assert re.search(rf"^{lang}\.WebView2Unsigned=", iss, re.M), lang


def test_the_guest_page_firewall_rule_is_limited_to_the_local_subnet(iss):
    rule = re.search(r"advfirewall firewall add rule[^;]*", _section(iss, "Code")).group(0)
    assert "remoteip=localsubnet" in rule and "profile=private" in rule


def test_the_program_is_run_elevated_only_from_a_folder_users_cannot_write(iss):
    code = _section(iss, "Code")
    guard = _function(iss, "SafeForElevation")
    assert "{commonpf" in guard
    # Every place that starts PhotoboxBooth.exe with administrator rights asks first.
    for fn in ("TryStep", "StopBooth", "CurUninstallStepChanged"):
        assert "SafeForElevation(" in _function(iss, fn), fn
    assert "ElevationSkipped" in code
    for lang in ("de", "en"):
        assert re.search(rf"^{lang}\.ReadyLegacyElevated=", iss, re.M), lang
        assert re.search(rf"^{lang}\.ElevationSkipped=", iss, re.M), lang
    assert "ReadyLegacyElevated" in _function(iss, "UpdateReadyMemo")


# ── First compile on Windows (Inno Setup 6.7.3, 15.11.2026) ───────────

def _code_without_strings_and_comments(iss: str) -> str:
    # One pass, like the compiler reads it: '{app}' is a string, and an
    # apostrophe inside a { comment } ("Microsoft's") starts no string.
    token = re.compile(r"'(?:[^']|'')*'|\{.*?\}|\(\*.*?\*\)|//[^\n]*", re.S)
    return token.sub(lambda m: "''" if m.group(0).startswith("'") else " ", _section(iss, "Code"))


def test_every_code_function_is_declared_before_it_is_used(iss):
    # Pascal Script has no look-ahead: a call above the definition fails the
    # compile with "Unknown identifier" (the first Windows build did).
    code = _code_without_strings_and_comments(iss)
    defined = [(m.group(1), m.start()) for m in re.finditer(r"^\s*(?:function|procedure)\s+(\w+)", code, re.M)]
    assert len(defined) > 20
    for name, at in defined:
        first = re.search(rf"\b{name}\b", code).start()
        assert first >= at, f"{name} is used before it is declared"


def test_it_needs_inno_setup_6_4_and_uses_its_current_function_names(iss):
    assert "#if Ver < EncodeVer(6, 4, 0)" in iss
    code = _code_without_strings_and_comments(iss)
    assert not re.search(r"\bFileCopy\s*\(", code), "renamed to CopyFile in 6.4 (ISCC hint)"
    assert "CopyFile(Exe, Exe + '.vorher', False)" in iss


def test_wildcard_sources_leave_mac_metadata_behind(iss):
    for line in _section(iss, "Files").splitlines():
        if re.match(r'Source:\s*"[^"]*\*"', line):
            assert re.search(r'Excludes:\s*"[^"]*\._\*', line) and ".DS_Store" in line, line


def test_the_admin_shortcut_is_an_internet_shortcut_with_the_booth_icon(iss):
    line = next(l for l in _section(iss, "Icons").splitlines() if "{cm:IconAdmin}" in l)
    assert 'Filename: "http://127.0.0.1:8765/admin"' in line
    assert 'IconFilename: "{app}\\{#AppExeName}"' in line


def test_a_per_user_uninstall_still_runs_the_programs_own_reversal(iss):
    # Nothing is elevated in a per-user install, so the Program Files guard
    # must not skip --quit and --uninstall there (the task would be left behind).
    assert "(not IsAdminInstallMode()) or SafeForElevation(Exe)" in _function(iss, "CurUninstallStepChanged")
    assert "IsAdminInstallMode() and (not SafeForElevation(Exe))" in _function(iss, "StopBooth")


def test_the_build_script_finds_winget_and_inno_setup_outside_an_interactive_logon():
    text = BUILD_PS1.read_text(encoding="utf-8-sig")
    assert "Microsoft\\WindowsApps\\winget.exe" in text
    assert not re.search(r"^\s*winget install", text, re.M), "bare winget is not on PATH over ssh"
    assert text.count('Invoke-Winget "') == 2
    assert "\\Users\\*\\AppData\\Local\\Programs\\Inno Setup $v\\ISCC.exe" in text
