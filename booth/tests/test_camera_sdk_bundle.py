"""The edition with camera SDK is recognised by what it carries, nothing else.

No file of Canon's SDK is in this repository: the "libraries" here are empty
placeholder files made in tmp_path, which is all the detection looks at.
"""
import io
import sys

import pytest

from boothcore import camera_sdk_bundle as bundle


def _make_bundle(root, licence=True):
    sdk = root / bundle.SDK_FOLDER
    sdk.mkdir(parents=True)
    for name in bundle.RUNTIME_LIBRARIES:
        (sdk / name).write_bytes(b"")
    if licence:
        bundle.write_licence_file(root / bundle.LICENCE_FILE)
    return root


@pytest.fixture
def frozen(tmp_path, monkeypatch):
    """A portable .exe: the bundle lies in the folder PyInstaller unpacks to."""
    inside = tmp_path / "meipass"
    inside.mkdir()
    monkeypatch.setattr(sys, "_MEIPASS", str(inside), raising=False)
    monkeypatch.setattr(sys, "argv", [str(tmp_path / "exe" / "PhotoboxBooth.exe")])
    (tmp_path / "exe").mkdir()
    return inside


def test_the_free_edition_has_no_bundle(tmp_path):
    assert bundle.find([tmp_path]) is None
    assert bundle.component([tmp_path]) is None


def test_an_sdk_folder_filled_by_the_operator_is_not_called_bundled(tmp_path):
    _make_bundle(tmp_path, licence=False)
    assert bundle.find([tmp_path]) is None


def test_libraries_and_licence_side_by_side_are_the_bundle(tmp_path):
    _make_bundle(tmp_path)
    assert bundle.find([tmp_path]) == tmp_path
    assert bundle.sdk_dir([tmp_path]) == tmp_path / "EDSDK"


def test_the_about_card_lists_it_as_proprietary_and_not_agpl(tmp_path):
    _make_bundle(tmp_path)
    entry = bundle.component([tmp_path])
    assert entry["files"] == sorted(bundle.RUNTIME_LIBRARIES)
    assert "not under the AGPL" in entry["licence"]
    assert entry["terms_url"].endswith(bundle.LICENCE_FILE)


@pytest.mark.parametrize("lang,words", [
    ("de", ["vertrauliche Dateien von Canon", "nur zusammen mit dieser Software",
            "Reverse Engineering", "außer soweit das Gesetz", "Copyleft",
            "unverändert und nur als Objektcode", "Kameras von Canon", "NICHT unter der GNU Affero"]),
    ("en", ["proprietary and confidential files of Canon", "only be used together with this software",
            "reverse engineered or modified", "except where the law expressly permits",
            "copyleft", "unmodified and in object code only", "Canon cameras", "NOT under the GNU Affero"]),
])
def test_the_four_end_user_terms_and_the_rest_are_said_in_both_languages(lang, words):
    text = " ".join(bundle.terms(lang).split())
    for w in words:
        assert w in text, w


def test_the_terms_say_nothing_about_an_agreement_or_a_territory():
    text = bundle.licence_text().lower()
    for w in ("vereinbarung", "agreement", "deutschland", "germany", "territor", "nda"):
        assert w not in text, w


def test_the_licence_file_is_bilingual_with_bom_and_crlf(tmp_path):
    path = bundle.write_licence_file(tmp_path / "x.txt")
    raw = path.read_bytes()
    assert raw.startswith(b"\xef\xbb\xbf") and b"\r\n" in raw and b"\n\n" not in raw.replace(b"\r\n", b"")
    text = raw.decode("utf-8-sig")
    assert text.index("Kamera-SDK in dieser Fassung") < text.index("Camera SDK in this edition")


def test_the_portable_exe_finds_the_bundle_inside_itself(frozen):
    _make_bundle(frozen)
    assert bundle.sdk_dir() == frozen / "EDSDK"


def test_auto_camera_choice_picks_the_canon_sdk_when_bundled(frozen):
    from boothcore.camera import service
    assert service._edsdk_da() is False
    _make_bundle(frozen)
    assert service._edsdk_da() is True


def test_the_self_check_counts_the_bundle_as_ready(frozen, tmp_path, monkeypatch):
    from boothcore import edsdk_setup
    monkeypatch.setattr(edsdk_setup, "target_dir", lambda: tmp_path / "data" / "EDSDK")
    _make_bundle(frozen)
    ok, detail = edsdk_setup.status()
    assert ok and "dieser Fassung bei" in detail


@pytest.fixture
def unfrozen_exe(tmp_path, monkeypatch):
    """A program started from a folder, not unpacked by PyInstaller."""
    folder = tmp_path / "prog"
    folder.mkdir()
    monkeypatch.delattr(sys, "_MEIPASS", raising=False)
    monkeypatch.setattr(sys, "argv", [str(folder / "PhotoboxBooth.exe")])
    return folder


def test_a_stray_licence_and_sdk_folder_next_to_some_exe_are_not_the_edition(unfrozen_exe):
    # Someone copied an EDSDK folder and a CAMERA-SDK-LICENCE.txt next to a
    # free PhotoboxBooth.exe: that is not the edition with camera SDK.
    _make_bundle(unfrozen_exe)
    assert bundle.find() is None
    assert bundle.sdk_dir() is None


def test_the_setup_installation_is_the_edition_with_camera_sdk(unfrozen_exe):
    # {app}\EDSDK and the terms, put there by the setup, which leaves its
    # uninstaller data in the same folder.
    _make_bundle(unfrozen_exe)
    (unfrozen_exe / "unins000.dat").write_bytes(b"")
    assert bundle.find() == unfrozen_exe
    assert bundle.sdk_dir() == unfrozen_exe / "EDSDK"


def test_first_start_shows_the_terms_once_and_leaves_a_copy(frozen, tmp_path):
    _make_bundle(frozen)
    data = tmp_path / "data"
    out = io.StringIO()
    assert bundle.acknowledge_once(data, out=out) is True
    assert "Reverse Engineering" in out.getvalue() and "reverse engineered" in out.getvalue()
    # Only in the booth's data folder, never next to the program: a copy
    # there would look like the setup's licence file.
    assert (data / bundle.LICENCE_FILE).is_file()
    assert not (tmp_path / "exe" / bundle.LICENCE_FILE).exists()
    again = io.StringIO()
    assert bundle.acknowledge_once(data, out=again) is False
    assert again.getvalue() == ""


def test_the_free_edition_shows_nothing_at_first_start(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "argv", [str(tmp_path / "PhotoboxBooth.exe")])
    monkeypatch.delattr(sys, "_MEIPASS", raising=False)
    out = io.StringIO()
    assert bundle.acknowledge_once(tmp_path / "data", out=out) is False
    assert out.getvalue() == "" and not (tmp_path / "data").exists()


@pytest.fixture()
def booth(monkeypatch, tmp_path):
    monkeypatch.setenv("BOOTH_DATA", str(tmp_path / "boothdata"))
    monkeypatch.setenv("BOOTH_CAMERA", "mock")
    from boothcore.app import Booth
    b = Booth(base=tmp_path / "boothdata", start_threads=False)
    yield b
    b.mail.stop()


def test_the_terms_page_exists_only_in_the_edition_with_camera_sdk(booth, frozen):
    from fastapi.testclient import TestClient
    from boothcore.app import create_app
    client = TestClient(create_app(booth))
    url = "/static/legal/" + bundle.LICENCE_FILE
    assert client.get(url).status_code == 404
    _make_bundle(frozen)
    r = client.get(url)
    assert r.status_code == 200 and "Copyleft" in r.text and "copyleft" in r.text


def test_the_booth_loads_the_sdk_in_its_documented_order_and_remembers_which(frozen, tmp_path, monkeypatch):
    """camera/edsdk.py: configured edsdk_dll, then the data folder
    (--install-edsdk), then EDSDK next to the program, then the bundle."""
    from types import SimpleNamespace

    from boothcore.camera import edsdk
    data = tmp_path / "data"
    monkeypatch.setenv("BOOTH_DATA", str(data))
    _make_bundle(frozen)
    configured = str(tmp_path / "eigen" / "EDSDK.dll")
    in_data = str(data / "EDSDK" / "EDSDK.dll")
    next_to = str(tmp_path / "exe" / "EDSDK" / "EDSDK.dll")
    bundled = str(frozen / "EDSDK" / "EDSDK.dll")
    present = {configured, in_data, next_to, bundled}

    def fake_load(path, *args, **kwargs):
        if path in present:
            return SimpleNamespace(_name=path)
        raise OSError(path)

    monkeypatch.setattr(edsdk.ctypes, "CDLL", fake_load)
    monkeypatch.setattr(edsdk.ctypes, "WinDLL", fake_load, raising=False)
    monkeypatch.setattr(edsdk.os, "add_dll_directory", lambda d: None, raising=False)
    monkeypatch.setattr(edsdk, "LOADED_DLL", None)
    for expected in (configured, in_data, next_to, bundled):
        edsdk.EdsdkCamera(dll_path=configured if expected == configured else "")._load()
        assert edsdk.LOADED_DLL == expected
        present.discard(expected)
