"""Das Dauerlauf-Werkzeug muss ohne Hardware laufen und Fehler klar benennen."""
import pytest

from boothcore.tools import cameratest


def test_runs_against_the_mock_camera(capsys, tmp_path, monkeypatch):
    monkeypatch.setenv("BOOTH_DATA", str(tmp_path))
    assert cameratest.main(["--backend", "mock", "--shots", "2"]) == 0
    out = capsys.readouterr().out
    assert "2 erfolgreich, 0 fehlgeschlagen" in out
    assert "Zeit je Aufnahme" in out


def test_keeps_the_pictures_only_when_asked(tmp_path, monkeypatch):
    monkeypatch.setenv("BOOTH_DATA", str(tmp_path))
    cameratest.main(["--backend", "mock", "--shots", "1"])
    assert not (tmp_path / "cameratest").exists()
    cameratest.main(["--backend", "mock", "--shots", "1", "--keep"])
    assert list((tmp_path / "cameratest").glob("*.jpg"))


def test_a_failing_camera_ends_with_an_error_code(capsys, tmp_path, monkeypatch):
    monkeypatch.setenv("BOOTH_DATA", str(tmp_path))
    from boothcore.camera.base import CameraError
    from boothcore.camera.mock import MockCamera

    def boom(self, target):
        raise CameraError("Kamera hat nicht ausgelöst")

    monkeypatch.setattr(MockCamera, "capture", boom)
    assert cameratest.main(["--backend", "mock", "--shots", "2"]) == 1
    out = capsys.readouterr().out
    assert "Kamera hat nicht ausgelöst" in out
    assert "Keine einzige Aufnahme" in out


def test_missing_sdk_points_at_the_setup_command(capsys, tmp_path, monkeypatch):
    """Unter Windows fehlt die Datei — dann gehört der Einrichtungsbefehl daneben."""
    monkeypatch.setenv("BOOTH_DATA", str(tmp_path))
    from boothcore import edsdk_setup
    monkeypatch.setattr(edsdk_setup.sys, "platform", "win32")
    assert cameratest.main(["--backend", "edsdk", "--shots", "1"]) == 1
    out = capsys.readouterr().out
    assert "--install-edsdk" in out
    assert "SDK-Prüfung" in out


def test_on_linux_it_names_gphoto2_instead_of_a_windows_command(capsys, tmp_path, monkeypatch):
    monkeypatch.setenv("BOOTH_DATA", str(tmp_path))
    from boothcore import edsdk_setup
    monkeypatch.setattr(edsdk_setup.sys, "platform", "linux")
    assert cameratest.main(["--backend", "edsdk", "--shots", "1"]) == 1
    out = capsys.readouterr().out
    assert "gphoto2" in out and "--install-edsdk" not in out


def test_unknown_backend_stops_instead_of_guessing(tmp_path, monkeypatch):
    monkeypatch.setenv("BOOTH_DATA", str(tmp_path))
    with pytest.raises(SystemExit):
        cameratest.main(["--backend", "polaroid", "--shots", "1"])
