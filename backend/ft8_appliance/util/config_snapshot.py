"""Konfigurations-Schnappschuss fuer die Aenderungshistorie — ohne Geheimnisse.

Die Tabelle ``config_history`` war seit ihrer Anlage leer: definiert, aber
nie beschrieben. Damit liess sich nachtraeglich nicht sagen, seit wann eine
Einstellung gilt — bei einem A/B-Test ueber mehrere Tage verschiebt eine
Aenderung die Grundlinie, ohne dass es in den Daten auftaucht.

Der Schnappschuss darf allerdings nicht sein, was in der ``config.yaml``
steht: Dort liegen QRZ- und ClubLog-Zugangsdaten, die API-Token der
Oberflaeche und der Hotspot-Schluessel. Die Datenbank wird gesichert,
kopiert und ausgewertet; Geheimnisse haetten dort nichts zu suchen und
waeren aus alten Sicherungen nicht mehr zurueckzuholen.

Deshalb maskiert dieses Modul, bevor irgendetwas gespeichert wird — und
zwar nach Namensmuster statt nach fester Liste, damit ein spaeter
hinzugefuegtes Feld nicht durchrutscht.
"""

from __future__ import annotations

import re
from typing import Any

MASKE = "***"

_GEHEIM = re.compile(
    r"passw|password|pwd|api_key|apikey|_key$|^key$|token|secret|psk|credential",
    re.IGNORECASE,
)
"""Namensmuster fuer Felder, die nie im Schnappschuss landen duerfen.

Absichtlich grosszuegig: Ein faelschlich maskiertes Feld kostet nur
Aussagekraft in der Historie, ein uebersehenes schreibt ein Geheimnis in
jede Sicherung. ``_key$`` trifft ``qrz_logbook_api_key`` ebenso wie ein
kuenftiges ``foo_key``; Felder wie ``hunt_priority`` bleiben unberuehrt.
"""


def ist_geheim(name: str) -> bool:
    return bool(_GEHEIM.search(name or ""))


def _zu_maskieren(name: str, wert: Any) -> bool:
    """Nur Zeichenketten koennen Geheimnisse sein.

    Ohne diese Typpruefung verschluckt das Namensmuster auch Schalter:
    ``hunt_weak_requires_psk`` ist ein bool und traf auf ``psk``. Genau
    solche Schalter will man in der Historie aber sehen — sie sind der
    Grund, warum es sie gibt. Ein Passwort ist nie True oder eine Zahl.
    """
    return isinstance(wert, str) and wert != "" and ist_geheim(name)


_GEHEIME_ABBILDUNGEN = frozenset({"qrz_logbooks"})
"""Abbildungen, deren *Werte* Geheimnisse sind und deren Schluessel keine
Feldnamen. ``qrz_logbooks`` bildet Rufzeichen auf API-Schluessel ab; das
Namensmuster sah nur "DK9XR/AM" und liess den Schluessel durch. Bis
2026-11-14 standen diese Schluessel im Klartext in config_history."""


def maskiere(wert: Any, _alles: bool = False) -> Any:
    """Tiefe Kopie mit maskierten Geheimnissen.

    Listen und verschachtelte Abbildungen werden mitgenommen — die
    Operatoren stehen als Liste von Abbildungen in der Konfiguration, und
    genau dort liegen die Zugangsdaten.
    """
    if isinstance(wert, dict):
        if _alles:
            return {k: (MASKE if isinstance(v, str) and v else maskiere(v, True))
                    for k, v in wert.items()}
        return {
            k: (MASKE if _zu_maskieren(str(k), v)
                else maskiere(v, str(k) in _GEHEIME_ABBILDUNGEN))
            for k, v in wert.items()
        }
    if isinstance(wert, (list, tuple)):
        return [maskiere(v, _alles) for v in wert]
    return wert
