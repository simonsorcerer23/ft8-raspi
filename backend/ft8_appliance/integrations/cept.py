"""CEPT-Lizenz-Compliance + DX-Country-Detection.

Deutsche Lizenzklassen (seit AFuV-Novelle 24.06.2024):
  * Klasse N = Einsteigerklasse (10W, 10m/2m/70cm). NICHT international
    anerkannt (ECC-Report 89) → Auslandsbetrieb generell gesperrt.
  * Klasse E = Novice-Class (KW + mehr). Ist CEPT-Novice-anerkannt via
    ECC/REC (05)06 → Gast-Betrieb in den Laendern die diese Empfehlung
    umsetzen (Stand 2016: 24 Laender, s.u.).
  * Klasse A = Volllizenz (CEPT-1 / T/R 61-01) → Gast-Betrieb in fast
    allen CEPT-Laendern.

WICHTIG (Korrektur 2026-05-29, Sebastian): frueher stand hier faelschlich
"Klasse E ist nicht CEPT-anerkannt" — das war falsch und sperrte DO3XR
unnoetig vom Auslandsbetrieb. Klasse E IST CEPT-Novice (ECC/REC 05-06).
Sie ist aber NUR in den Laendern erlaubt die explizit CEPT-Novice
umsetzen — das ist eine ECHTE TEILMENGE der CEPT-1-Laender. Z.B.
Frankreich, Italien, Spanien, Griechenland, UK setzen CEPT-1 um, aber
NICHT CEPT-Novice → dort darf nur Klasse A (Dad), nicht Klasse E.

CEPT-Novice-Teilnehmer (ECC/REC (05)06, Stand 16.09.2016, Quelle CEPT
docdb 1855): Oesterreich, Belgien, Belarus, Bosnien-Herzegowina,
Kroatien, Tschechien, Daenemark (+Groenland/Faeroeer), Finnland,
Deutschland, Ungarn, Island, Liechtenstein, Litauen, Luxemburg, Moldau,
Niederlande, Polen, Portugal, Rumaenien, Russland, Slowakei, Slowenien,
Schweiz. → cept_class_e_allowed=True nur fuer diese.

USA: FCC DA-16-1048 erkennt CEPT-Novice an, ABER mit US-Frequenz-
Sonderregeln die wir nicht modellieren → konservativ Klasse-E-geblockt.

Power-Caps (cept_class_a_max_w) sind fuer Klasse A. Fuer Klasse E greift
ohnehin der nationale 100W-Cap (license.py) — und bei IC-705/IC-7300
ist der Rig-Hardware-Cap (10/100W) eh die bindende Grenze, die Laender-
Caps sind fuer dieses Setup praktisch nie limitierend.

Bounding-Boxes sind grobe rechteckige Approximation der Landesgrenzen —
ausreichend fuer GPS-Detection auf 100km-Genauigkeit. Bei Grenz-
Ambiguitaet muss der Operator manuell bestaetigen (kein Auto-Switch).

Quelle: ECC/REC (05)06 + CEPT T/R 61-01 + nationale Verordnungen.
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


# ─────────────────────────────────────────────────────────────────────
# VOLLSTAENDIGE CEPT-Laenderliste (Sebastian 2026-05-29, haarfeine
# Recherche). Quelle: ARRL "Information for US Amateurs Traveling Abroad"
# (Mai 2023), konsolidiert aus CEPT T/R 61-01 + ECC/REC (05)06.
#
# cept_class_e_allowed=True NUR fuer die 24 ECC/REC-(05)06-Teilnehmer
# (CEPT Novice). Alle uebrigen T/R-61-01-Laender sind Klasse-A-only.
#
# A=darf-Klasse-A (T/R 61-01), E=darf-zusaetzlich-Klasse-E (05-06):
#   E+A: AT BE BY BA HR CZ DK FI DE HU IS LV LI LT LU MD NL PL PT RO RU SK SI CH
#   A-only: AL BG CY EE FR GR IE IT MK MC ME NO RS ES SE TR UA GB
#
# Power-Caps sind fuer Klasse A; bei IC-705/IC-7300 bindet ohnehin der
# Rig-Hardware-Cap (10/100W), die Laender-Caps sind hier nie limitierend
# (konservativ-plausible Werte, dienen v.a. der Dokumentation).
# ─────────────────────────────────────────────────────────────────────
#
# GPS-Detection-Hinweis: Rechteck-bboxes ineinander verschachtelter
# Balkan-Laender (Kroatien C-Form um Bosnien) ueberlappen unvermeidlich.
# Reihenfolge = Prioritaet bei Mehrdeutigkeit: haeufige Reiseziele
# (Kroatien!) zuerst, damit z.B. Split → 9A statt E7 matcht. Bei echter
# Grenz-Ambiguitaet bestaetigt der Operator eh manuell (kein Auto-Switch).
COUNTRIES: dict[str, CountryInfo] = {
    "DL": HOME_COUNTRY_DL,
    # ==== CEPT-Novice-Laender (Klasse E + A erlaubt) ====
    # Kroatien zuerst — Adria-Reiseziel, GPS-Vorrang vor Bosnien-bbox.
    "9A": CountryInfo("9A", "9A", "Kroatien",
                     cept_class_a_max_w=1000, cept_class_e_allowed=True,
                     bbox=(42.39, 46.55, 13.49, 19.45)),
    "OE": CountryInfo("OE", "OE", "Österreich",
                     cept_class_a_max_w=400, cept_class_e_allowed=True,
                     bbox=(46.37, 49.02, 9.53, 17.16)),
    "ON": CountryInfo("ON", "ON", "Belgien",
                     cept_class_a_max_w=1500, cept_class_e_allowed=True,
                     bbox=(49.50, 51.51, 2.55, 6.41)),
    "EW": CountryInfo("EW", "EW", "Belarus",
                     cept_class_a_max_w=1000, cept_class_e_allowed=True,
                     bbox=(51.26, 56.17, 23.18, 32.77)),
    "E7": CountryInfo("E7", "E7", "Bosnien-Herzegowina",
                     cept_class_a_max_w=1000, cept_class_e_allowed=True,
                     bbox=(42.56, 45.28, 16.20, 19.62)),
    "OK": CountryInfo("OK", "OK", "Tschechien",
                     cept_class_a_max_w=1000, cept_class_e_allowed=True,
                     bbox=(48.55, 51.05, 12.09, 18.86)),
    "OZ": CountryInfo("OZ", "OZ", "Dänemark",
                     cept_class_a_max_w=1000, cept_class_e_allowed=True,
                     bbox=(54.56, 57.75, 8.07, 15.20)),
    "OH": CountryInfo("OH", "OH", "Finnland",
                     cept_class_a_max_w=1500, cept_class_e_allowed=True,
                     bbox=(59.81, 70.10, 20.59, 31.59)),
    "HA": CountryInfo("HA", "HA", "Ungarn",
                     cept_class_a_max_w=1500, cept_class_e_allowed=True,
                     bbox=(45.74, 48.58, 16.11, 22.90)),
    "TF": CountryInfo("TF", "TF", "Island",
                     cept_class_a_max_w=1000, cept_class_e_allowed=True,
                     bbox=(63.30, 66.57, -24.55, -13.50)),
    "YL": CountryInfo("YL", "YL", "Lettland",
                     cept_class_a_max_w=1000, cept_class_e_allowed=True,
                     bbox=(55.67, 58.09, 20.97, 28.24)),
    "HB0": CountryInfo("HB0", "HB0", "Liechtenstein",
                     cept_class_a_max_w=1000, cept_class_e_allowed=True,
                     bbox=(47.05, 47.27, 9.47, 9.64)),
    "LY": CountryInfo("LY", "LY", "Litauen",
                     cept_class_a_max_w=1000, cept_class_e_allowed=True,
                     bbox=(53.90, 56.45, 20.95, 26.84)),
    "LX": CountryInfo("LX", "LX", "Luxemburg",
                     cept_class_a_max_w=1000, cept_class_e_allowed=True,
                     bbox=(49.45, 50.18, 5.73, 6.53)),
    "ER": CountryInfo("ER", "ER", "Moldau",
                     cept_class_a_max_w=1000, cept_class_e_allowed=True,
                     bbox=(45.47, 48.49, 26.62, 30.13)),
    "PA": CountryInfo("PA", "PA", "Niederlande",
                     cept_class_a_max_w=400, cept_class_e_allowed=True,
                     bbox=(50.75, 53.55, 3.36, 7.23)),
    "SP": CountryInfo("SP", "SP", "Polen",
                     cept_class_a_max_w=750, cept_class_e_allowed=True,
                     bbox=(49.00, 54.84, 14.12, 24.15)),
    "CT": CountryInfo("CT", "CT", "Portugal",
                     cept_class_a_max_w=1500, cept_class_e_allowed=True,
                     bbox=(36.96, 42.15, -9.50, -6.19)),
    "YO": CountryInfo("YO", "YO", "Rumänien",
                     cept_class_a_max_w=1500, cept_class_e_allowed=True,
                     bbox=(43.62, 48.27, 20.26, 29.69)),
    "S5": CountryInfo("S5", "S5", "Slowenien",
                     cept_class_a_max_w=1000, cept_class_e_allowed=True,
                     bbox=(45.42, 46.88, 13.38, 16.61)),
    "OM": CountryInfo("OM", "OM", "Slowakei",
                     cept_class_a_max_w=1500, cept_class_e_allowed=True,
                     bbox=(47.73, 49.61, 16.83, 22.57)),
    "HB9": CountryInfo("HB9", "HB9", "Schweiz",
                     cept_class_a_max_w=1000, cept_class_e_allowed=True,
                     bbox=(45.81, 47.81, 5.95, 10.49)),
    # ==== CEPT-1-only (NUR Klasse A — Klasse E hier GESPERRT) ====
    "ZA": CountryInfo("ZA", "ZA", "Albanien",
                     cept_class_a_max_w=1000, cept_class_e_allowed=False,
                     bbox=(39.64, 42.66, 19.26, 21.06)),
    "LZ": CountryInfo("LZ", "LZ", "Bulgarien",
                     cept_class_a_max_w=1500, cept_class_e_allowed=False,
                     bbox=(41.24, 44.22, 22.36, 28.61)),
    "5B": CountryInfo("5B", "5B", "Zypern",
                     cept_class_a_max_w=1000, cept_class_e_allowed=False,
                     bbox=(34.56, 35.70, 32.27, 34.60)),
    "ES": CountryInfo("ES", "ES", "Estland",
                     cept_class_a_max_w=1000, cept_class_e_allowed=False,
                     bbox=(57.51, 59.68, 21.76, 28.21)),
    "F":  CountryInfo("F", "F", "Frankreich",
                     cept_class_a_max_w=500, cept_class_e_allowed=False,
                     bbox=(41.30, 51.10, -5.14, 9.56)),
    "SV": CountryInfo("SV", "SV", "Griechenland",
                     cept_class_a_max_w=1000, cept_class_e_allowed=False,
                     bbox=(34.80, 41.75, 19.37, 29.65)),
    "EI": CountryInfo("EI", "EI", "Irland",
                     cept_class_a_max_w=400, cept_class_e_allowed=False,
                     bbox=(51.42, 55.39, -10.48, -5.99)),
    "I":  CountryInfo("I", "I", "Italien",
                     cept_class_a_max_w=500, cept_class_e_allowed=False,
                     bbox=(36.62, 47.10, 6.62, 18.52)),
    "Z3": CountryInfo("Z3", "Z3", "Nordmazedonien",
                     cept_class_a_max_w=1000, cept_class_e_allowed=False,
                     bbox=(40.85, 42.37, 20.45, 23.04)),
    "3A": CountryInfo("3A", "3A", "Monaco",
                     cept_class_a_max_w=500, cept_class_e_allowed=False,
                     bbox=(43.72, 43.76, 7.40, 7.44)),
    "4O": CountryInfo("4O", "4O", "Montenegro",
                     cept_class_a_max_w=1000, cept_class_e_allowed=False,
                     bbox=(41.85, 43.57, 18.43, 20.36)),
    "LA": CountryInfo("LA", "LA", "Norwegen",
                     cept_class_a_max_w=1000, cept_class_e_allowed=False,
                     bbox=(57.96, 71.19, 4.65, 31.06)),
    "YU": CountryInfo("YU", "YU", "Serbien",
                     cept_class_a_max_w=1000, cept_class_e_allowed=False,
                     bbox=(42.23, 46.19, 18.82, 23.01)),
    "EA": CountryInfo("EA", "EA", "Spanien",
                     cept_class_a_max_w=1500, cept_class_e_allowed=False,
                     bbox=(35.95, 43.79, -9.30, 4.33)),
    "SM": CountryInfo("SM", "SM", "Schweden",
                     cept_class_a_max_w=1000, cept_class_e_allowed=False,
                     bbox=(55.34, 69.06, 11.11, 24.16)),
    "TA": CountryInfo("TA", "TA", "Türkei",
                     cept_class_a_max_w=1000, cept_class_e_allowed=False,
                     bbox=(35.82, 42.10, 25.67, 44.83)),
    "UR": CountryInfo("UR", "UR", "Ukraine",
                     cept_class_a_max_w=1000, cept_class_e_allowed=False,
                     bbox=(44.39, 52.38, 22.14, 40.23)),
    "G":  CountryInfo("G", "G", "Großbritannien",
                     cept_class_a_max_w=400, cept_class_e_allowed=False,
                     bbox=(49.91, 58.70, -8.65, 1.76)),
    # ==== Übersee (CEPT-anerkannt fuer Klasse A; Klasse E konservativ
    #      gesperrt — FCC-CEPT-Novice hat US-Frequenz-Sonderregeln die
    #      wir nicht modellieren) ====
    "W":  CountryInfo("W", "W", "USA",
                     cept_class_a_max_w=1500, cept_class_e_allowed=False,
                     bbox=(24.50, 49.38, -125.00, -66.95)),
    "KH6": CountryInfo("KH6", "KH6", "Hawaii",
                     cept_class_a_max_w=1500, cept_class_e_allowed=False,
                     bbox=(18.91, 22.24, -160.25, -154.81)),
    # ==== Grosse bbox ans Ende (GPS-Detection: spezifischere Laender
    #      matchen zuerst, Russland nur wenn nichts anderes passt) ====
    "UA": CountryInfo("UA", "UA", "Russland",
                     cept_class_a_max_w=1000, cept_class_e_allowed=True,
                     bbox=(43.00, 68.00, 19.64, 60.00)),
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
