"""Deutsche Amateurfunk-Klassenregelung — Band-Erlaubnis + Power-Caps.

Stand: BNetzA AFuV nach Reform Juni 2024. Drei Klassen:

* **A** (volle Berechtigung): alle Amateurbänder, 750W PEP allgemein,
  Sonderfall 60m (15W EIRP, sekundär).
* **E** (Einsteigerklasse): 160m, 80m, 15m, 10m, 2m, 70cm; 100W PEP auf
  HF, 75W auf VHF/UHF. Kein 60m/40m/30m/20m/17m/12m.
  160m ist segmentabhaengig (AFuV Anlage 1, Fassung 24.06.2024, Zeilen
  3-5): 1810-1850 kHz A 750 / E 100 W, 1850-1890 kHz 75 W fuer beide,
  1890-2000 kHz 10 W fuer beide. Der FT8-Dial 1840 liegt im ersten
  Segment; ``max_power_for`` nimmt die Dial-Frequenz entgegen.
* **N** (Newcomerklasse, neu seit Juni 2024): 10m (laut Code-Historie
  eingeschränkt auf 29.510–29.700 MHz, was die übliche FT8-Freq 28.074
  ausschließt → 10m hier deshalb nicht freigegeben), 2m, 70cm; 10W.
  KEIN 160m: die Anlage 1 (Fassung 24.06.2024) fuehrt Klasse N auf allen
  drei 160-m-Segmenten mit "–". Stand bis 2026-09-06 faelschlich drin
  (Audit C1) — die erlaubende Richtung, darum korrigiert.

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
    # 160m fehlte bis 2026-09-06 (Audit C1) — die Anlage 1 erlaubt es
    # Klasse E mit 100 W im FT8-Segment.
    "E": frozenset({"160m", "80m", "15m", "10m", "2m", "70cm"}),
    # Klasse N darf 10m nur 29.510–29.700 MHz — die typische FT8-Freq
    # 28.074 MHz liegt außerhalb. Wir lassen 10m für N deshalb weg
    # (sicherer Default; wenn ein N-OP wirklich mal 29.500+ FT8 will,
    # muss er das Band manuell zur LICENSE_BANDS-Liste hinzufügen und
    # die Freq selbst auf 29.6xx MHz konfigurieren).
    "N": frozenset({"2m", "70cm"}),
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


# Segmentabhaengige Caps innerhalb eines Bands, in kHz: (lo, hi, {Klasse: W}).
# Quelle: AFuV Anlage 1 (BGBl. 2024 I Nr. 175), Zeilen 3-5. Gilt fuer A UND
# E — auch Klasse A darf oberhalb 1850 kHz nur 75 bzw. 10 W. Ohne Frequenz
# faellt ``max_power_for`` auf den Klassen-Default zurueck, was fuer den
# ueblichen FT8-Dial 1840 kHz das richtige Segment ist.
_SEGMENT_MAX_POWER_W: dict[str, tuple[tuple[int, int, dict[LicenseClass, int]], ...]] = {
    "160m": (
        (1810, 1850, {"A": 750, "E": 100}),
        (1850, 1890, {"A": 75, "E": 75}),
        (1890, 2000, {"A": 10, "E": 10}),
    ),
}


def is_band_allowed(license_class: LicenseClass, band: str) -> bool:
    """Darf eine Station mit *license_class* auf *band* senden?"""
    return band in LICENSE_BANDS[license_class]


def max_power_for(
    license_class: LicenseClass, band: str, freq_khz: float | None = None,
) -> int | None:
    """Maximale erlaubte TX-Leistung in W PEP für (Klasse, Band[, Dial]).

    ``freq_khz`` ist die Dial-Frequenz; sie entscheidet auf Baendern mit
    Segment-Caps (160m). Ohne Angabe gilt der Klassen-Default.

    Returns:
        Watt-Wert wenn erlaubt, ``None`` wenn das Band für die Klasse
        nicht freigegeben ist (Caller darf dann gar nicht erst senden).
    """
    if not is_band_allowed(license_class, band):
        return None
    if freq_khz is not None:
        for lo, hi, caps in _SEGMENT_MAX_POWER_W.get(band, ()):
            if lo <= freq_khz < hi and license_class in caps:
                return caps[license_class]
    specific = _BAND_SPECIFIC_MAX_POWER_W.get((license_class, band))
    if specific is not None:
        return specific
    return _DEFAULT_MAX_POWER_W[license_class]
