"""Der Canon-Treiber selbst — Ablauf, Referenzen, die unangenehmen Fälle.

Alles hier gegen ein nachgebautes SDK (``fake_edsdk``). Die echte Kamera
ersetzt das nicht; die Liste in der Wiki-Seite „Canon-SDK" sagt, was an der
Hardware noch offen ist. Was es ersetzt, ist die Sorte Fehler, die man an der
Hardware erst bemerkt, wenn der Abend gelaufen ist: ein vertauschtes Foto,
eine hängende Kamera, ein Absturz ohne Traceback.
"""
from pathlib import Path

import pytest

from fake_edsdk import BUSY, AF_NG, FakeEdsdk, bereitstellen


@pytest.fixture()
def kamera(monkeypatch, tmp_path):
    from boothcore.camera.edsdk import EdsdkCamera
    sdk = FakeEdsdk()
    bereitstellen(monkeypatch, sdk)
    cam = EdsdkCamera()
    cam.open()
    yield cam, sdk, tmp_path
    try:
        cam.close()
    except Exception:
        pass


# ── Das Schlimmste zuerst: das vertauschte Foto ───────────────────────

def test_a_late_shot_never_lands_in_the_next_session(kamera):
    """Nach einem Timeout bekam die nächste Gruppe das Foto der vorigen.

    Der Ablauf: Aufnahme 1 läuft in den Timeout. Das Bild trifft eine
    Sekunde später ein, findet kein Ziel — und wurde bisher stillschweigend
    in das Ziel geschrieben, das Aufnahme 2 gerade gesetzt hatte.
    """
    cam, sdk, tmp = kamera
    sdk.liefert = False                       # Aufnahme 1 liefert nichts
    import boothcore.camera.edsdk as m
    monkey = pytest.MonkeyPatch()
    monkey.setattr(m.time, "monotonic", _uhr())
    eins = tmp / "eins.jpg"
    with pytest.raises(Exception) as e:
        cam.capture(eins)
    monkey.undo()
    assert "Timeout" in str(e.value)
    assert not eins.exists()

    # Jetzt kommt das Bild von Aufnahme 1 doch noch — mitten in Aufnahme 2.
    sdk.spaet_liefern()
    sdk.liefert = True
    zwei = tmp / "zwei.jpg"
    cam.capture(zwei)

    assert zwei.exists(), "Aufnahme 2 hat kein Bild bekommen"
    assert not eins.exists(), "Das verspätete Bild hat ein fremdes Ziel gefunden"
    assert sdk.abgesagt, "Das verspätete Bild wurde nicht abgesagt"


def test_an_unclaimed_transfer_is_cancelled(kamera):
    """Eine Auslösung am Gehäuse darf die Kamera nicht stehen lassen.

    Wer eine Transferanfrage nicht abholt, muss sie absagen. Sonst wartet
    die Kamera weiter und quittiert alles Weitere mit DEVICE_BUSY.
    """
    cam, sdk, _tmp = kamera
    ref = sdk.spaet_liefern()
    sdk.EdsGetEvent()
    assert ref in sdk.abgesagt, "Die Anfrage blieb unbeantwortet stehen"


def test_a_normal_shot_still_works(kamera):
    """Die Gegenprobe zu allem oben."""
    cam, sdk, tmp = kamera
    ziel = tmp / "gut.jpg"
    assert cam.capture(ziel) == ziel
    assert ziel.exists() and ziel.read_bytes().startswith(b"\xff\xd8")
    assert not sdk.abgesagt, "Ein gutes Bild wurde abgesagt"


# ── Auslösen: beschäftigt, kein Fokus ─────────────────────────────────

def test_a_busy_camera_is_retried_and_then_reported(kamera):
    """Nach drei Versuchen fiel der Fehler durch und die Box wartete 15 s."""
    cam, sdk, tmp = kamera
    sdk.busy_mal = 99
    with pytest.raises(Exception) as e:
        cam.capture(tmp / "x.jpg")
    assert "00000081" in str(e.value), f"Falscher Grund genannt: {e.value}"
    assert "Timeout" not in str(e.value)


def test_busy_once_is_just_a_retry(kamera):
    cam, sdk, tmp = kamera
    sdk.busy_mal = 1
    ziel = tmp / "nach-warten.jpg"
    cam.capture(ziel)
    assert ziel.exists()


def test_without_focus_it_shoots_anyway_even_on_the_last_try(kamera):
    """Der Ausweg „ohne Autofokus" galt nur für die ersten beiden Versuche.

    Erreichbar war das nur, wenn die Kamera vorher zweimal beschäftigt war —
    zwei kurze Verzögerungen und dann kein Fokus ist in einem dunklen Raum
    nach einer Blitzaufnahme aber genau der Normalfall.
    """
    cam, sdk, tmp = kamera
    sdk.busy_mal = 2                          # Versuch 0 und 1 gehen so weg
    sdk.af_ng_mal = 99                        # im letzten Versuch: kein Fokus
    ziel = tmp / "dunkel.jpg"
    cam.capture(ziel)
    assert ziel.exists(), "Im letzten Versuch blieb der Ausweg ungenutzt"
    assert sdk.nonaf_versuche >= 1


def test_without_focus_on_the_first_try_it_also_works(kamera):
    """Die Gegenprobe: Der Ausweg im ersten Versuch funktionierte schon vorher."""
    cam, sdk, tmp = kamera
    sdk.af_ng_mal = 1
    ziel = tmp / "dunkel1.jpg"
    cam.capture(ziel)
    assert ziel.exists() and sdk.nonaf_versuche == 1


# ── Live-Bild ─────────────────────────────────────────────────────────

def test_a_failed_pointer_does_not_crash_the_process(kamera):
    """``string_at`` auf einen NULL-Zeiger ist ein Absturz ohne Traceback."""
    cam, sdk, _tmp = kamera
    sdk.evf_zeiger_null = True
    with pytest.raises(Exception):
        cam.preview_jpeg()


def test_a_working_preview_still_returns_a_picture(kamera):
    cam, sdk, _tmp = kamera
    assert (cam.preview_jpeg() or b"").startswith(b"\xff\xd8")


# ── Lebenszyklus ──────────────────────────────────────────────────────

def test_the_camera_list_is_released_when_choosing_the_camera_fails(monkeypatch):
    """Auf diesem Fehlerweg blieb pro Versuch eine Referenz liegen.

    Versucht wird alle ein bis acht Sekunden — über einen Abend summiert
    sich das.
    """
    from boothcore.camera.edsdk import EdsdkCamera
    sdk = FakeEdsdk()
    sdk.EdsGetChildAtIndex = lambda *a: 0x11          # scheitert
    bereitstellen(monkeypatch, sdk)
    cam = EdsdkCamera()
    with pytest.raises(Exception):
        cam.open()
    assert sdk.freigegeben, "Die Kameraliste blieb liegen"


def test_no_camera_is_a_readable_sentence(monkeypatch):
    from boothcore.camera.edsdk import EdsdkCamera
    sdk = FakeEdsdk(); sdk.kameras = 0
    bereitstellen(monkeypatch, sdk)
    with pytest.raises(Exception) as e:
        EdsdkCamera().open()
    assert "Kabel" in str(e.value)


def test_a_failed_handler_is_reported_not_swallowed(monkeypatch):
    """Sonst startet die Box sauber und läuft bei jeder Auslösung in den Timeout."""
    from boothcore.camera.edsdk import EdsdkCamera
    sdk = FakeEdsdk(); sdk.handler_fehler = 0x42
    bereitstellen(monkeypatch, sdk)
    with pytest.raises(Exception) as e:
        EdsdkCamera().open()
    assert "Ereignisse" in str(e.value)


def test_a_failed_init_does_not_terminate_a_sdk_that_never_started(monkeypatch):
    from boothcore.camera.edsdk import EdsdkCamera
    sdk = FakeEdsdk()
    sdk.EdsInitializeSDK = lambda: 0x7
    bereitstellen(monkeypatch, sdk)
    cam = EdsdkCamera()
    with pytest.raises(Exception):
        cam.open()
    cam.close()                                # darf nichts anfassen
    assert sdk.beendet == 0, "EdsTerminateSDK auf einem nie gestarteten SDK"


def test_closing_hands_the_camera_back(kamera):
    cam, sdk, _tmp = kamera
    cam.close()
    # The session is handed back; the SDK itself stays up for the process
    # (see test_the_sdk_is_started_once_per_process).
    assert sdk.sitzung_zu == 1 and sdk.beendet == 0


def _uhr():
    """Eine Uhr, die nach zwei Blicken weit voraus ist und dann weiterläuft."""
    zustand = {"n": 0}

    def jetzt():
        zustand["n"] += 1
        return 0.0 if zustand["n"] <= 2 else 100.0 + zustand["n"]
    return jetzt


# ── Echte M50, 14.11.2026: Die Liste ist direkt nach dem Start manchmal leer ──

def test_a_camera_that_shows_up_a_moment_late_is_still_found(monkeypatch):
    """Right after EdsInitializeSDK the camera list can still be empty.

    Seen on the real EOS M50: the same cable, the same camera, found on one
    start and missing on the next. Giving up at once meant a full
    terminate/initialise round per retry, which on macOS never converged.
    """
    from boothcore.camera.edsdk import EdsdkCamera
    sdk = FakeEdsdk(); sdk.kameras_spaet = 3
    bereitstellen(monkeypatch, sdk)
    cam = EdsdkCamera()
    cam.open()
    assert sdk.sitzung_offen == 1
    assert sdk.initialisiert == 1, "Kein zweiter SDK-Start nur wegen einer späten Liste"
    cam.close()


def test_every_empty_camera_list_is_released_while_waiting(monkeypatch):
    from boothcore.camera.edsdk import EdsdkCamera
    sdk = FakeEdsdk(); sdk.kameras_spaet = 3
    bereitstellen(monkeypatch, sdk)
    cam = EdsdkCamera()
    cam.open()
    assert len(sdk.freigegeben) >= 4, "Jede abgefragte Liste muss wieder freigegeben werden"
    cam.close()


def test_no_camera_still_gives_up_within_a_few_seconds(monkeypatch):
    import time
    from boothcore.camera.edsdk import EdsdkCamera
    sdk = FakeEdsdk(); sdk.kameras = 0
    bereitstellen(monkeypatch, sdk)
    start = time.monotonic()
    with pytest.raises(Exception):
        EdsdkCamera().open()
    assert time.monotonic() - start < 5


def test_the_live_view_does_not_ask_for_events_on_every_frame(kamera):
    """EdsGetEvent took 65 ms per call on the real M50 (macOS, SDK 13.21.10).

    Asked before every frame it halved the live view: 4.6 frames a second
    instead of 11.8. The live view only needs events now and then — to cancel
    a picture somebody took at the camera body — not twenty times a second.
    """
    cam, sdk, _ = kamera
    vorher = sdk.ereignis_abfragen
    for _ in range(20):
        assert cam.preview_jpeg()
    assert sdk.ereignis_abfragen - vorher <= 2


def test_the_live_view_still_asks_for_events_from_time_to_time(kamera, monkeypatch):
    import boothcore.camera.edsdk as modul
    cam, sdk, _ = kamera
    jetzt = [1000.0]
    monkeypatch.setattr(modul.time, "monotonic", lambda: jetzt[0])
    vorher = sdk.ereignis_abfragen
    for _ in range(5):
        jetzt[0] += 1.0
        cam.preview_jpeg()
    assert sdk.ereignis_abfragen - vorher == 5


# ── Echte M50, 14.11.2026: nach einem Fehler fand die Box die Kamera nie wieder ──

def test_the_sdk_is_started_once_per_process(monkeypatch):
    """Terminate and initialise again, and the SDK lists no camera any more.

    Seen on the real EOS M50 (macOS, EDSDK 13.21.10): one capture ran into the
    timeout, the booth reconnected the way it always did — close, terminate,
    initialise, open — and from then on every attempt said "no camera found"
    although the camera sat on the cable. A fresh process found it at once.
    Canon's own samples start the SDK once and end it once; so does the booth.
    """
    from boothcore.camera.edsdk import EdsdkCamera
    sdk = FakeEdsdk()
    bereitstellen(monkeypatch, sdk)
    for _ in range(3):                    # three reconnects in one process
        cam = EdsdkCamera()
        cam.open()
        cam.close()
    assert sdk.initialisiert == 1
    assert sdk.beendet == 0
    assert sdk.sitzung_offen == 3 and sdk.sitzung_zu == 3


def test_a_failed_start_is_tried_again_next_time(monkeypatch):
    from boothcore.camera.edsdk import EdsdkCamera
    sdk = FakeEdsdk()
    bereitstellen(monkeypatch, sdk)
    echt = sdk.EdsInitializeSDK
    sdk.EdsInitializeSDK = lambda: 0x00000002          # fails once
    with pytest.raises(Exception):
        EdsdkCamera().open()
    sdk.EdsInitializeSDK = echt
    cam = EdsdkCamera()
    cam.open()
    assert sdk.initialisiert == 1
    cam.close()
