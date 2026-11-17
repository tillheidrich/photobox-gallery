"""Welche Kamera über welchen Weg — und was die Box selbst findet.

Canon hat ein SDK, Nikon und Sony nicht (jedenfalls keines, das wir einbinden
dürften). Genau dafür gibt es digiCamControl unter Windows und gphoto2 unter
Linux. Die Frage beim Aufbau ist nie „welches Backend", sondern „ich habe eine
Sony A7" — die Box soll den Rest wissen.
"""
from __future__ import annotations

import os

from boothcore.camera import models
from boothcore.camera.service import create_backend


def test_the_brands_people_actually_bring_are_covered():
    marken = {m["marke"] for m in models.MARKEN}
    assert {"Canon", "Nikon", "Sony", "Fujifilm"} <= marken


def test_sony_and_nikon_have_a_way_on_both_systems():
    for mid in ("sony", "nikon", "fujifilm"):
        assert models.empfehlung(mid, windows=True) == "digicamcontrol"
        assert models.empfehlung(mid, windows=False) == "gphoto2"


def test_canon_uses_its_own_sdk_where_it_exists():
    assert models.empfehlung("canon-dslr", windows=True) == "edsdk"
    assert models.empfehlung("canon-ccapi", windows=True) == "ccapi"


def test_an_unknown_camera_still_has_a_way_in():
    """Notausgang: Jede Tethering-Software, die in einen Ordner schreibt."""
    assert models.empfehlung("gibtsnicht") == "folder"


def test_every_entry_says_what_to_watch_out_for():
    for m in models.MARKEN:
        assert m["hinweis"].strip(), m["id"]
        assert m["windows"] in models.WEGE and m["linux"] in models.WEGE


def test_the_selection_follows_the_machine_it_runs_on():
    windows = {k["id"]: k["backend"] for k in models.auswahl(windows=True)}
    linux = {k["id"]: k["backend"] for k in models.auswahl(windows=False)}
    assert windows["nikon"] != linux["nikon"]


def test_a_test_image_is_the_fallback_not_a_crash():
    """Ohne Kamera läuft die Box weiter — sonst kann niemand etwas einrichten."""
    assert create_backend({"backend": "gibtsnicht"}).name == "mock"


def test_the_environment_variable_wins_for_tests():
    alt = os.environ.get("BOOTH_CAMERA")
    os.environ["BOOTH_CAMERA"] = "mock"
    try:
        assert create_backend({"backend": "auto"}).name == "mock"
    finally:
        if alt is None:
            os.environ.pop("BOOTH_CAMERA", None)
        else:
            os.environ["BOOTH_CAMERA"] = alt


# ── Funde der Durchsicht vom 19.09.2026 ───────────────────────────────

def test_stopping_hands_the_camera_back(tmp_path):
    """Die Kamera wurde beim Beenden einfach stehen gelassen.

    Ohne Schließen bleibt sie mit Live-Bild auf den Rechner geschaltet
    zurück, das Display schwarz — und der nächste Start findet sie belegt.
    """
    import time
    from boothcore.camera.service import CameraService
    from boothcore.camera.mock import MockCamera

    class Merkende(MockCamera):
        def __init__(self):
            super().__init__()
            self.zu = 0

        def close(self):
            self.zu += 1
            return super().close()

    hier = Merkende()
    dienst = CameraService(lambda: hier)
    dienst.start()
    for _ in range(50):
        if dienst.state == "ready":
            break
        time.sleep(0.05)
    assert dienst.state == "ready"
    dienst.stop()
    assert hier.zu >= 1, "Die Kamera wurde nicht geschlossen"


def test_a_question_to_the_camera_runs_on_the_camera_thread(tmp_path):
    """Das EDSDK verlangt alle Aufrufe auf einem Thread.

    Die Selbstdiagnose rief ``backend.shooting_mode()`` direkt — aus dem
    HTTP-Thread, aus dem Auftragsweg und aus ``--doctor`` vom Hauptthread,
    während der Kamera-Thread das Live-Bild holte.
    """
    import threading
    import time
    from boothcore.camera.service import CameraService
    from boothcore.camera.mock import MockCamera

    class Neugierig(MockCamera):
        def __init__(self):
            super().__init__()
            self.gefragt_von = None

        def shooting_mode(self):
            self.gefragt_von = threading.current_thread().name
            return "M"

    hier = Neugierig()
    dienst = CameraService(lambda: hier)
    dienst.start()
    for _ in range(50):
        if dienst.state == "ready":
            break
        time.sleep(0.05)
    try:
        assert dienst.frage("shooting_mode") == "M"
        assert hier.gefragt_von == "camera", \
            f"Die Frage lief auf {hier.gefragt_von!r} statt auf dem Kamera-Thread"
    finally:
        dienst.stop()


def test_the_doctor_asks_through_the_service_not_past_it():
    """Der Weg muss im Quelltext stehen, nicht nur im Vorsatz."""
    from pathlib import Path
    quelle = (Path(__file__).resolve().parents[1] / "boothcore" / "doctor.py").read_text()
    assert "camera.frage(" in quelle
    assert "camera.backend.shooting_mode()" not in quelle
