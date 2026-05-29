"""CEPT-Lizenz-Compliance + DX-Country-Detection.

═══════════════════════════════════════════════════════════════════════
QUELLE (PRIMAERQUELLE, 2026-05-29 haarfein verifiziert von Claude+Sebastian):
  DARC "Countries with CEPT Licence", zusammengestellt von Hans Schwarz
  DK5JI (dk5ji@darc.de), Stand 2026-05-16, CC BY-NC-ND 4.0.
  https://www.darc.de/.../cept-laenderliste/
  Diese Liste wird selbst aus den offiziellen ECC/CEPT-Dokumenten +
  nationalen Verordnungen gepflegt:
    * T/R 61-01  (CEPT Licence / Volllizenz)   → docdb.cept.org/download/4541
    * ECC/REC (05)06 (CEPT Novice Licence)      → docdb.cept.org/download/4413
  Jede Zeile unten ist gegen die jeweilige Laender-Seite im DARC-PDF
  geprueft (Spalte oben rechts: "Short-term WITHOUT guest licence",
  Felder Full / Novice = x/-). NICHTS ist hier aus dem Bauch geschaetzt.

Deutsche Lizenzklassen (seit AFuV-Novelle 24.06.2024):
  * Klasse N = Einsteigerklasse. International NICHT anerkannt (es gibt
    KEINE ECC-Empfehlung zur gegenseitigen Anerkennung von Entry-Class;
    ECC Report 89 beschreibt nur das Syllabus) → Auslandsbetrieb gesperrt.
  * Klasse E = Novice-Class = CEPT-Novice via ECC/REC (05)06 → Gastbetrieb
    NUR in Laendern die (05)06 OHNE Gastlizenz umsetzen (Spalte "Novice"=x).
  * Klasse A = Volllizenz = CEPT-1 / T/R 61-01 → Gastbetrieb in Laendern
    die T/R 61-01 OHNE Gastlizenz umsetzen (Spalte "Full"=x).

WICHTIG — drei Stati pro Land (alle aus dem DARC-PDF, Spalte
"Short-term w/o guest licence"):
  1. cept_class_a_allowed  = Full=x   → Klasse A darf als Gast (kein Papier)
  2. cept_class_e_allowed  = Novice=x → Klasse E darf als Gast (kein Papier)
  3. cept_suspended        = ** im PDF → CEPT-Mitgliedschaft AUSGESETZT
     (aktuell Belarus + Russland) → Gastbetrieb derzeit NICHT moeglich,
     egal was die Recommendation-Spalten sagen. HART blocken.

Laender bei denen BEIDE Spalten "-" sind (Gastbetrieb nur MIT vorher
beantragter Gastlizenz, z.B. Albanien, Andorra, Aserbaidschan, San Marino,
Vatikan) sind hier bewusst NICHT gelistet — sie sind keine CEPT-Drop-in-
Ziele. Wer dorthin will braucht eine individuelle Gastlizenz; das ist
nichts was die Box automatisch freischalten darf.

USA (Sebastian reist haeufig hin): im DARC-PDF als "United States of
America – ITU Region 2" gefuehrt (umfasst conterminous states + Alaska
+ HAWAII). Short-term w/o guest: Full=x UND Novice=x → sowohl Klasse A
als auch Klasse E duerfen. Rechtsgrundlage 47 CFR §97.107: die
Privilegien sind auf die Heimat-Lizenz-Bedingungen (deutsche Klasse-E-
Baender, max 100W) ∩ US-Zuteilungen begrenzt — license.py erzwingt das
ohnehin. FT8 auf 15m (21.074) ist abgedeckt.

cept_class_a_max_w = dokumentierte Full-Leistung auf 15m (21 MHz) aus dem
DARC-PDF (unsere FT8-Hauptband-Allokation). KEINE Schaetzung — direkt aus
der jeweiligen Band-Tabelle abgelesen. Bei IC-705/IC-7300 bindet ohnehin
der Rig-Hardware-Cap (10/100W), die Laender-Caps sind fuer dieses Setup
praktisch nie limitierend; sie dienen v.a. der Dokumentation/Korrektheit.

Bounding-Boxes sind EXPLIZIT grobe rechteckige Approximationen der
Landesgrenzen — nur fuer GPS-Detection (Vorschlag, ~100km). KEIN
Auto-Switch; bei Grenz-Ambiguitaet bestaetigt der Operator manuell.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class CountryInfo:
    """CEPT-Compliance-Daten fuer ein Land (Quelle: DARC-PDF 2026-05-16).

    code:           ITU-Praefix-Kurzform (fuer config) — z.B. "9A", "F", "OE"
    prefix:         Was beim DX-Suffix vor "/" steht (typisch == code)
    name:           Anzeigename
    cept_class_a_allowed:
                    Spalte "Short-term w/o guest licence / Full" == x.
                    Darf deutsche Klasse A (CEPT-1 / T/R 61-01) hier ohne
                    vorab beantragte Gastlizenz als Gast funken?
    cept_class_e_allowed:
                    Spalte "Short-term w/o guest licence / Novice" == x.
                    Darf deutsche Klasse E (CEPT-Novice / ECC/REC (05)06)
                    hier ohne Gastlizenz als Gast funken?
    cept_suspended: ** im DARC-PDF: CEPT-Mitgliedschaft ausgesetzt
                    (Belarus, Russland). Dann ist Gastbetrieb derzeit
                    NICHT moeglich — hart blocken, unabhaengig der Spalten.
    cept_class_a_max_w:
                    Dokumentierte Full-Leistung (PEP) auf 15m laut DARC-PDF.
                    Aus der Band-Tabelle abgelesen, nicht geschaetzt.
    bbox:           (lat_min, lat_max, lon_min, lon_max) GROBE Bounding-Box
                    fuer GPS-Detection. WGS84 Dezimalgrad. Approximation!
    """
    code: str
    prefix: str
    name: str
    cept_class_a_allowed: bool
    cept_class_e_allowed: bool
    cept_suspended: bool
    cept_class_a_max_w: int | None
    bbox: tuple[float, float, float, float]


def _c(code, prefix, name, a, e, w, bbox, suspended=False) -> CountryInfo:
    """Kurz-Konstruktor — haelt die Tabelle unten lesbar."""
    return CountryInfo(
        code=code, prefix=prefix, name=name,
        cept_class_a_allowed=a, cept_class_e_allowed=e,
        cept_suspended=suspended, cept_class_a_max_w=w, bbox=bbox,
    )


# Heimatland — kein Suffix nötig, voller Lizenz-Umfang
HOME_COUNTRY_DL = _c("DL", "DL", "Deutschland", True, True, 750,
                     (47.27, 55.06, 5.87, 15.04))


# ─────────────────────────────────────────────────────────────────────
# VOLLSTAENDIGE CEPT-Drop-in-Laenderliste — jede Zeile gegen die DARC-PDF-
# Laenderseite (Stand 2026-05-16) geprueft. Spalte "Short-term w/o guest
# licence": Full → cept_class_a_allowed, Novice → cept_class_e_allowed.
# Power = dokumentierte 15m-Full-Leistung aus der jeweiligen Band-Tabelle.
#
#   E+A (Full=x, Novice=x): OE ON E7 9A OK OZ OH 4L HA TF YL HB0 LY LX ER
#                           4O PA Z3 SP CT YO OM S5 HB9 UR W
#   A-only (Full=x, Novice=-): LZ 5B ES F SV EI I 9H 3A LA YU EA SM TA G
#   SUSPENDED (**, Gastbetrieb derzeit gesperrt): EW (Belarus) UA (Russland)
#
# NICHT gelistet (Full=- → Gastlizenz Pflicht, kein Drop-in): Albanien,
# Andorra, Aserbaidschan, San Marino, Vatikan.
#
# GPS-Detection-Reihenfolge = Prioritaet bei Box-Overlap: spezifische /
# haeufige Reiseziele (Kroatien vor Bosnien!) zuerst, grosse Boxen
# (Russland) ganz am Ende. Bei echter Ambiguitaet bestaetigt der Operator.
# ─────────────────────────────────────────────────────────────────────
COUNTRIES: dict[str, CountryInfo] = {
    "DL": HOME_COUNTRY_DL,

    # ==== Klasse E + A erlaubt (CEPT-Novice-Laender) ====
    # Kroatien zuerst — Adria-Reiseziel, GPS-Vorrang vor Bosnien-bbox.
    "9A":  _c("9A", "9A", "Kroatien", True, True, 1500,
              (42.39, 46.55, 13.49, 19.45)),
    "OE":  _c("OE", "OE", "Österreich", True, True, 200,
              (46.37, 49.02, 9.53, 17.16)),
    "ON":  _c("ON", "ON", "Belgien", True, True, 1500,
              (49.50, 51.51, 2.55, 6.41)),
    "E7":  _c("E7", "E7", "Bosnien-Herzegowina", True, True, 1500,
              (42.56, 45.28, 16.20, 19.62)),
    "OK":  _c("OK", "OK", "Tschechien", True, True, 750,
              (48.55, 51.05, 12.09, 18.86)),
    "OZ":  _c("OZ", "OZ", "Dänemark", True, True, 1000,
              (54.56, 57.75, 8.07, 15.20)),
    "OH":  _c("OH", "OH", "Finnland", True, True, 1500,
              (59.81, 70.10, 20.59, 31.59)),
    "4L":  _c("4L", "4L", "Georgien", True, True, 1600,
              (41.05, 43.59, 39.96, 46.74)),
    "HA":  _c("HA", "HA", "Ungarn", True, True, 1500,
              (45.74, 48.58, 16.11, 22.90)),
    "TF":  _c("TF", "TF", "Island", True, True, 1000,
              (63.30, 66.57, -24.55, -13.50)),
    "YL":  _c("YL", "YL", "Lettland", True, True, 1000,
              (55.67, 58.09, 20.97, 28.24)),
    "HB0": _c("HB0", "HB0", "Liechtenstein", True, True, 1000,
              (47.05, 47.27, 9.47, 9.64)),
    "LY":  _c("LY", "LY", "Litauen", True, True, 1000,
              (53.90, 56.45, 20.95, 26.84)),
    "LX":  _c("LX", "LX", "Luxemburg", True, True, 100,
              (49.45, 50.18, 5.73, 6.53)),
    "ER":  _c("ER", "ER", "Moldau", True, True, 100,
              (45.47, 48.49, 26.62, 30.13)),
    "4O":  _c("4O", "4O", "Montenegro", True, True, 1500,
              (41.85, 43.57, 18.43, 20.36)),
    "PA":  _c("PA", "PA", "Niederlande", True, True, 400,
              (50.75, 53.55, 3.36, 7.23)),
    "Z3":  _c("Z3", "Z3", "Nordmazedonien", True, True, 1500,
              (40.85, 42.37, 20.45, 23.04)),
    "SP":  _c("SP", "SP", "Polen", True, True, 500,
              (49.00, 54.84, 14.12, 24.15)),
    "CT":  _c("CT", "CT", "Portugal", True, True, 1500,
              (36.96, 42.15, -9.50, -6.19)),
    "YO":  _c("YO", "YO", "Rumänien", True, True, 200,
              (43.62, 48.27, 20.26, 29.69)),
    "OM":  _c("OM", "OM", "Slowakei", True, True, 750,
              (47.73, 49.61, 16.83, 22.57)),
    "S5":  _c("S5", "S5", "Slowenien", True, True, 1500,
              (45.42, 46.88, 13.38, 16.61)),
    "HB9": _c("HB9", "HB9", "Schweiz", True, True, 1000,
              (45.81, 47.81, 5.95, 10.49)),
    "UR":  _c("UR", "UR", "Ukraine", True, True, 200,
              (44.39, 52.38, 22.14, 40.23)),

    # ==== NUR Klasse A (T/R 61-01; Klasse E hier NICHT als Gast) ====
    "LZ":  _c("LZ", "LZ", "Bulgarien", True, False, 350,
              (41.24, 44.22, 22.36, 28.61)),
    "5B":  _c("5B", "5B", "Zypern", True, False, 400,
              (34.56, 35.70, 32.27, 34.60)),
    "ES":  _c("ES", "ES", "Estland", True, False, 1000,
              (57.51, 59.68, 21.76, 28.21)),
    "F":   _c("F", "F", "Frankreich", True, False, 500,
              (41.30, 51.10, -5.14, 9.56)),
    "SV":  _c("SV", "SV", "Griechenland", True, False, 500,
              (34.80, 41.75, 19.37, 29.65)),
    "EI":  _c("EI", "EI", "Irland", True, False, 400,
              (51.42, 55.39, -10.48, -5.99)),
    "I":   _c("I", "I", "Italien", True, False, 500,
              (36.62, 47.10, 6.62, 18.52)),
    "9H":  _c("9H", "9H", "Malta", True, False, 400,
              (35.78, 36.08, 14.18, 14.58)),
    "3A":  _c("3A", "3A", "Monaco", True, False, 100,
              (43.72, 43.76, 7.40, 7.44)),
    "LA":  _c("LA", "LA", "Norwegen", True, False, 1000,
              (57.96, 71.19, 4.65, 31.06)),
    "YU":  _c("YU", "YU", "Serbien", True, False, 1500,
              (42.23, 46.19, 18.82, 23.01)),
    "EA":  _c("EA", "EA", "Spanien", True, False, 1000,
              (35.95, 43.79, -9.30, 4.33)),
    "SM":  _c("SM", "SM", "Schweden", True, False, 200,
              (55.34, 69.06, 11.11, 24.16)),
    "TA":  _c("TA", "TA", "Türkei", True, False, 400,
              (35.82, 42.10, 25.67, 44.83)),
    "G":   _c("G", "G", "Großbritannien", True, False, 1000,
              (49.91, 58.70, -8.65, 1.76)),

    # ==== USA (DARC-PDF: USA ITU Region 2, inkl. Alaska + Hawaii) ====
    # Short-term w/o guest: Full=x UND Novice=x → A und E duerfen.
    # Hawaii (KH6) ist im PDF Teil derselben Region-2-Seite; eigener
    # Eintrag nur fuer GPS-Detection + Prefix-Anzeige.
    "W":   _c("W", "W", "USA", True, True, 1500,
              (24.50, 49.38, -125.00, -66.95)),
    "KH6": _c("KH6", "KH6", "Hawaii", True, True, 1500,
              (18.91, 22.24, -160.25, -154.81)),

    # ==== CEPT-Mitgliedschaft AUSGESETZT (**) — Gastbetrieb gesperrt ====
    # Recommendation-Spalten waeren x/x, aber Mitgliedschaft suspendiert
    # → hart blocken. Eintrag bleibt fuer GPS-Detection (zeigt Sperre).
    # Belarus zuerst (spezifischer), Russland mit grosser bbox ganz am Ende.
    "EW":  _c("EW", "EW", "Belarus", True, True, 100,
              (51.26, 56.17, 23.18, 32.77), suspended=True),
    "UA":  _c("UA", "UA", "Russland", True, True, 1000,
              (43.00, 68.00, 19.64, 60.00), suspended=True),
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
    es als erster Eintrag im Dict steht; grosse Boxen (Russland)
    stehen am Ende.
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
    Klartext-Grund fuer die UI / den Lock-Banner. Datengrundlage:
    DARC-PDF 2026-05-16, Spalte "Short-term w/o guest licence".
    """
    if not country_code or country_code == home_country:
        # Keine DX-Aktivität oder Heimat → keine CEPT-Pruefung noetig
        return (True, None)
    info = COUNTRIES.get(country_code)
    if info is None:
        # Unbekanntes Land → defensiv blocken
        return (False, f"Land {country_code} nicht in CEPT-DB — manuell prüfen")
    if info.cept_suspended:
        return (
            False,
            f"{info.name}: CEPT-Mitgliedschaft ausgesetzt (Stand 2026-05-16) "
            f"— kein Gastbetrieb möglich",
        )
    if license_class == "A":
        if info.cept_class_a_allowed:
            return (True, None)
        return (
            False,
            f"{info.name}: kurzfristiger Gastbetrieb braucht eine Gast-Lizenz "
            f"(kein CEPT-Drop-in für Klasse A)",
        )
    if license_class == "E":
        if info.cept_class_e_allowed:
            return (True, None)
        return (
            False,
            f"Klasse E (CEPT-Novice) ist in {info.name} nicht für Gastbetrieb "
            f"zugelassen — dort darf nur Klasse A (T/R 61-01)",
        )
    # Klasse N (Einsteiger) — international nicht anerkannt
    return (
        False,
        f"Klasse {license_class} ist international nicht anerkannt — "
        f"kein Auslandsbetrieb",
    )


def cept_power_cap(country_code: str | None, home_country: str) -> int | None:
    """Max-Power-Cap (15m Full, DARC-PDF) fuer CEPT-Gastbetrieb im
    operating-country. None = kein Cap (Heimat oder Land nicht in DB)."""
    if not country_code or country_code == home_country:
        return None
    info = COUNTRIES.get(country_code)
    if info is None:
        return None
    return info.cept_class_a_max_w
