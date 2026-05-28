"""CEPT-Lizenz-Compliance + DX-Country-Detection.

CEPT T/R 61-01 erlaubt deutschen Klasse-A-Funkern (= CEPT-1) Gast-Betrieb
in den meisten europaeischen Laendern ohne Extra-Lizenz, in Form
``<host-country-prefix>/<home-call>`` (z.B. 9A/DK9XR in Kroatien).

CEPT-Novice T/R 61-02 (= aequivalent zu deutscher Klasse-A einsteiger-
beschraenkt) ist eine SEPARATE Empfehlung. Deutsche Klasse E ist
NICHT als CEPT-Novice anerkannt — Klasse-E-Operatoren brauchen fuer
Auslandsbetrieb eine bilaterale Gast-Genehmigung der Ziellandes-
Behoerde.

Power-Caps variieren pro Land (Frankreich 500W, Oesterreich 400W,
Niederlande 400W usw.). Wir clampen den slider-max entsprechend.

Bounding-Boxes sind grobe rechteckige Approximation der Landesgrenzen —
ausreichend fuer GPS-Detection auf 100km-Genauigkeit. Bei Grenz-
Ambiguitaet (z.B. CH/DE/AT-Dreieck) muss der Operator manuell
bestaetigen — wir machen keine Auto-Switches ohne User-Klick.

Quelle: IARU R1 Reciprocal Operating Permits + nationale Verordnungen.
Daten Stand 2026, Snapshot — bei Reise sicherheitshalber prüfen.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class CountryInfo:
    """CEPT-Compliance-Daten fuer ein DXCC-Land.

    code:           ITU-Country-Code (kurz, fuer config) — z.B. "9A", "F", "OE"
    prefix:         Was beim DX-Suffix vor "/" steht (typisch == code)
    name:           Anzeigename
    cept_class_a_max_w:
                    Max-Power-Watts fuer CEPT-1-Gast (deutsche Klasse A).
                    None = unbekannt → Pi laesst Rig-Max greifen mit Warnung.
    cept_class_e_allowed:
                    Ist deutsche Klasse E (NICHT CEPT-Novice) hier ohne
                    bilaterale Genehmigung erlaubt? In >99% der Faelle False —
                    Klasse E ist deutsche Sonderklasse, nicht CEPT.
    bbox:           (lat_min, lat_max, lon_min, lon_max) grobe Bounding-Box
                    fuer GPS-Auto-Detection. WGS84 Dezimalgrad.
    """
    code: str
    prefix: str
    name: str
    cept_class_a_max_w: int | None
    cept_class_e_allowed: bool
    bbox: tuple[float, float, float, float]


# Heimatland — kein Suffix nötig, voller Lizenz-Umfang
HOME_COUNTRY_DL = CountryInfo(
    code="DL", prefix="DL", name="Deutschland",
    cept_class_a_max_w=750,   # Klasse A national
    cept_class_e_allowed=True,  # Klasse E zuhause OK
    bbox=(47.27, 55.06, 5.87, 15.04),
)


# Reise-Laender. Schwerpunkt: EU + Mittelmeer-Klassiker, plus
# Schweiz/UK als Nicht-EU-EU-Nachbarn. USA als trans-atlantischer
# Bonus (mit US-Reziprozitaetsregelung, eigene FCC-Lizenz noetig
# fuer Aufenthalte > 6 Monate, fuer Urlaubsbetrieb CEPT-Reziprozitaet
# ueber FCC Public Notice DA 16-1356).
COUNTRIES: dict[str, CountryInfo] = {
    "DL": HOME_COUNTRY_DL,
    # ---- EU-Nachbarn ----
    "9A": CountryInfo("9A", "9A", "Kroatien",
                     cept_class_a_max_w=1000, cept_class_e_allowed=False,
                     bbox=(42.39, 46.55, 13.49, 19.45)),
    "SV": CountryInfo("SV", "SV", "Griechenland",
                     cept_class_a_max_w=1000, cept_class_e_allowed=False,
                     bbox=(34.80, 41.75, 19.37, 29.65)),
    "I":  CountryInfo("I", "I", "Italien",
                     cept_class_a_max_w=500, cept_class_e_allowed=False,
                     bbox=(36.62, 47.10, 6.62, 18.52)),
    "EA": CountryInfo("EA", "EA", "Spanien",
                     cept_class_a_max_w=1500, cept_class_e_allowed=False,
                     bbox=(35.95, 43.79, -9.30, 4.33)),
    "F":  CountryInfo("F", "F", "Frankreich",
                     cept_class_a_max_w=500, cept_class_e_allowed=False,
                     bbox=(41.30, 51.10, -5.14, 9.56)),
    "OE": CountryInfo("OE", "OE", "Österreich",
                     cept_class_a_max_w=400, cept_class_e_allowed=False,
                     bbox=(46.37, 49.02, 9.53, 17.16)),
    "HB9": CountryInfo("HB9", "HB9", "Schweiz",
                     cept_class_a_max_w=1000, cept_class_e_allowed=False,
                     bbox=(45.81, 47.81, 5.95, 10.49)),
    "PA": CountryInfo("PA", "PA", "Niederlande",
                     cept_class_a_max_w=400, cept_class_e_allowed=False,
                     bbox=(50.75, 53.55, 3.36, 7.23)),
    "ON": CountryInfo("ON", "ON", "Belgien",
                     cept_class_a_max_w=1500, cept_class_e_allowed=False,
                     bbox=(49.50, 51.51, 2.55, 6.41)),
    "CT": CountryInfo("CT", "CT", "Portugal",
                     cept_class_a_max_w=1500, cept_class_e_allowed=False,
                     bbox=(36.96, 42.15, -9.50, -6.19)),
    "S5": CountryInfo("S5", "S5", "Slowenien",
                     cept_class_a_max_w=1000, cept_class_e_allowed=False,
                     bbox=(45.42, 46.88, 13.38, 16.61)),
    "OK": CountryInfo("OK", "OK", "Tschechien",
                     cept_class_a_max_w=1000, cept_class_e_allowed=False,
                     bbox=(48.55, 51.05, 12.09, 18.86)),
    "SP": CountryInfo("SP", "SP", "Polen",
                     cept_class_a_max_w=750, cept_class_e_allowed=False,
                     bbox=(49.00, 54.84, 14.12, 24.15)),
    "OZ": CountryInfo("OZ", "OZ", "Dänemark",
                     cept_class_a_max_w=1000, cept_class_e_allowed=False,
                     bbox=(54.56, 57.75, 8.07, 15.20)),
    # ---- Skandinavien ----
    "SM": CountryInfo("SM", "SM", "Schweden",
                     cept_class_a_max_w=1000, cept_class_e_allowed=False,
                     bbox=(55.34, 69.06, 11.11, 24.16)),
    "LA": CountryInfo("LA", "LA", "Norwegen",
                     cept_class_a_max_w=1000, cept_class_e_allowed=False,
                     bbox=(57.96, 71.19, 4.65, 31.06)),
    "OH": CountryInfo("OH", "OH", "Finnland",
                     cept_class_a_max_w=1500, cept_class_e_allowed=False,
                     bbox=(59.81, 70.10, 20.59, 31.59)),
    # ---- UK + Inseln ----
    "G":  CountryInfo("G", "G", "England",
                     cept_class_a_max_w=400, cept_class_e_allowed=False,
                     bbox=(49.91, 55.81, -6.42, 1.76)),
    "EI": CountryInfo("EI", "EI", "Irland",
                     cept_class_a_max_w=400, cept_class_e_allowed=False,
                     bbox=(51.42, 55.39, -10.48, -5.99)),
    # ---- Aussereuropäisch (CEPT-Reziprozität) ----
    "W":  CountryInfo("W", "W", "USA",
                     cept_class_a_max_w=1500, cept_class_e_allowed=False,
                     bbox=(24.50, 49.38, -125.00, -66.95)),
    # Hawaii separat — eigene Bounding-Box, gleiche FCC-Regeln
    "KH6": CountryInfo("KH6", "KH6", "Hawaii",
                     cept_class_a_max_w=1500, cept_class_e_allowed=False,
                     bbox=(18.91, 22.24, -160.25, -154.81)),
    # Türkei — bilaterale Vereinbarung, Klasse-A OK ueber CEPT
    "TA": CountryInfo("TA", "TA", "Türkei",
                     cept_class_a_max_w=1000, cept_class_e_allowed=False,
                     bbox=(35.82, 42.10, 25.67, 44.83)),
}


def country(code: str | None) -> CountryInfo | None:
    """Lookup eines CountryInfo via Code. None bei unbekanntem Code."""
    if not code:
        return None
    return COUNTRIES.get(code.upper())


def detect_from_gps(lat: float | None, lon: float | None) -> str | None:
    """GPS → wahrscheinliches Country-Code via Bounding-Box.

    Returns None wenn keine GPS-Position oder kein Land matched.
    Falls mehrere Boxes overlappen (Grenz-Region), wird das ERSTE
    Match aus iteration-order zurueckgegeben — DL hat Vorrang weil
    es als erster Eintrag im Dict steht.
    """
    if lat is None or lon is None:
        return None
    for code, info in COUNTRIES.items():
        lat_min, lat_max, lon_min, lon_max = info.bbox
        if lat_min <= lat <= lat_max and lon_min <= lon <= lon_max:
            return code
    return None


def cept_compliance(
    country_code: str | None,
    home_country: str,
    license_class: str,
) -> tuple[bool, str | None]:
    """Pruef ob TX im operating-country erlaubt ist.

    Returns (allowed, reason). reason ist None bei erlaubt, sonst ein
    Klartext-Grund fuer die UI / den Lock-Banner.
    """
    if not country_code or country_code == home_country:
        # Keine DX-Aktivität oder Heimat → keine CEPT-Pruefung noetig
        return (True, None)
    info = COUNTRIES.get(country_code)
    if info is None:
        # Unbekanntes Land → defensiv blocken
        return (False, f"Land {country_code} nicht in CEPT-DB — manuell prüfen")
    if license_class == "A":
        return (True, None)  # CEPT-1 ueberall erlaubt (in den Laendern die wir tracken)
    if license_class == "E":
        if info.cept_class_e_allowed:
            return (True, None)
        return (
            False,
            f"Klasse E ist nicht CEPT-anerkannt — TX in {info.name} "
            f"braucht bilaterale Gast-Genehmigung",
        )
    # Klasse N (Newcomer) — noch restriktiver als E, eh nicht CEPT
    return (False, f"Klasse {license_class} ist nicht CEPT-anerkannt")


def cept_power_cap(country_code: str | None, home_country: str) -> int | None:
    """Max-Power-Cap fuer CEPT-1 im operating-country. None = kein Cap
    (Heimat oder Land nicht in DB)."""
    if not country_code or country_code == home_country:
        return None
    info = COUNTRIES.get(country_code)
    if info is None:
        return None
    return info.cept_class_a_max_w
