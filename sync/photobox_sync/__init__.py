"""Photobox Sync — die gemeinsame Synchronisation des Fotobox-Ökosystems.

Zwei Nutzer:

* **Photobox Sync (eigenständig)** — läuft neben dslrBooth, LumaBooth oder jeder
  anderen Aufnahme-Software, beobachtet Ordner und nimmt Trigger entgegen.
* **Photobox Booth** — die eigene Aufnahme-Software; bindet dieses Paket direkt ein.

Beide sprechen dieselbe Galerie-API v2, nutzen dieselbe Outbox und werden in der
Galerie gleich verwaltet (Status, Profil, Aufträge).
"""
__version__ = "0.1.0"
