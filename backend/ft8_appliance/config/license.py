"""Deutsche Amateurfunk-Klassenregelung — Band-Erlaubnis + Power-Caps.

Stand: BNetzA AFuV nach Reform Juni 2024. Drei Klassen:

* **A** (volle Berechtigung): alle Amateurbänder, 750W PEP allgemein,
  Sonderfall 60m (15W EIRP, sekundär).
* **E** (Einsteigerklasse): nur 80m, 15m, 10m, 2m, 70cm; 100W PEP auf
  HF, 75W auf VHF/UHF. Kein 40m/30m/20m/17m/12m.
* **N** (Newcomerklasse, neu seit Juni 2024): nur 160m, 10m
  (eingeschränkter Bereich 29.510–29.700 MHz, was die übliche FT8-Freq
  28.074 ausschließt → 10m hier deshalb nicht freigegeben), 2m, 70cm;
  10W PEP.

Die Tabellen sind nur Konstanten — die Anwendung über `max_power_for()`
liefert eine eindeutige Antwort pro (Klasse, Band)-Paar.

Band-Namen müssen exakt mit ``BandConfig.name`` übereinstimmen, sonst
greift der Lookup nicht. Schreibweise: ``"160m"``, ``"80m"``, ...,
``"70cm"``.
"""

from __future__ import annotations

from typing import Literal

LicenseClass = Literal["A", "E", "N"]

# Erlaubte Bänder pro Klasse. Sets weil "drin oder nicht drin" reicht.
LICENSE_BANDS: dict[LicenseClass, frozenset[str]] = {
    "A": frozenset({
        "160m", "80m", "60m", "40m", "30m", "20m",
        "17m", "15m", "12m", "10m", "6m", "2m", "70cm",
    }),
    "E": frozenset({"80m", "15m", "10m", "2m", "70cm"}),
    # Klasse N darf 10m nur 29.510–29.700 MHz — die typische FT8-Freq
    # 28.074 MHz liegt außerhalb. Wir lassen 10m für N deshalb weg
    # (sicherer Default; wenn ein N-OP wirklich mal 29.500+ FT8 will,
    # muss er das Band manuell zur LICENSE_BANDS-Liste hinzufügen und
    # die Freq selbst auf 29.6xx MHz konfigurieren).
    "N": frozenset({"160m", "2m", "70cm"}),
}

# Klassen-weite Default-Caps in Watt PEP.
_DEFAULT_MAX_POWER_W: dict[LicenseClass, int] = {
    "A": 750,
    "E": 100,
    "N": 10,
}

# Band-spezifische Ausnahmen (überschreiben den Klassen-Default).
_BAND_SPECIFIC_MAX_POWER_W: dict[tuple[LicenseClass, str], int] = {
    # Klasse A: 60m ist sekundär, 15W EIRP — bei 0dBd-Antenne ≈ 15W PEP
    # an der Buchse; bei Dipol/Vertical mit ~2dBi praktisch ~10W PEP.
    # Wir nehmen 15 als hart cap und Operator-Verantwortung für den
    # Rest (richtige Antennenkonfig im UI).
    ("A", "60m"): 15,
    # Klasse E: VHF/UHF auf 75W gedeckelt (HF bleibt bei 100 wie Default).
    ("E", "2m"): 75,
    ("E", "70cm"): 75,
}


def is_band_allowed(license_class: LicenseClass, band: str) -> bool:
    """Darf eine Station mit *license_class* auf *band* senden?"""
    return band in LICENSE_BANDS[license_class]


def max_power_for(license_class: LicenseClass, band: str) -> int | None:
    """Maximale erlaubte TX-Leistung in W PEP für (Klasse, Band).

    Returns:
        Watt-Wert wenn erlaubt, ``None`` wenn das Band für die Klasse
        nicht freigegeben ist (Caller darf dann gar nicht erst senden).
    """
    if not is_band_allowed(license_class, band):
        return None
    specific = _BAND_SPECIFIC_MAX_POWER_W.get((license_class, band))
    if specific is not None:
        return specific
    return _DEFAULT_MAX_POWER_W[license_class]
