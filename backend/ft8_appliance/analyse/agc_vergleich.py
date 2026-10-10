"""AGC-Vergleich: schnell, langsam oder aus? (ab 2026-10-10)

Das WSJT-X-Handbuch (Abschnitt "Transceiver Setup") raet, die AGC
abzuschalten oder die HF-Verstaerkung zurueckzunehmen, damit sie moeglichst
wenig regelt; Gary Hinson ZL2IFB ("FT8 Operating Guide", 10.15) nennt die
langsame AGC als einfache Loesung. Die Station lief bis dahin mit schneller
AGC und voller HF-Verstaerkung — weder gelesen noch gemessen.

Der Arm haengt allein an der Uhr: Jeder 15-Minuten-Block gehoert fest zu
einem der drei Arme. So laesst sich jeder Decode nachtraeglich zuordnen, ohne
eine Spalte dafuer, und Station und Auswertung rechnen mit derselben Funktion.
"""
from __future__ import annotations

import hashlib
import math

BLOCK_S = 900
ARME = ("FAST", "SLOW", "OFF")
# hamlib ``enum agc_level_e`` (rig.h): OFF=0, SUPERFAST=1, FAST=2, SLOW=3,
# USER=4, MEDIUM=5, AUTO=6.
HAMLIB_AGC = {"OFF": 0, "FAST": 2, "SLOW": 3, "MEDIUM": 5}
ANZEIGE = {"FAST": "schnell", "SLOW": "langsam", "OFF": "aus"}
# Wie ein Arm am IC-7300 eingestellt wird: (AGC-Stufe, Zeitkonstante in s).
# "Aus" gibt es dort nicht als Stufe — ``L AGC 0`` lehnt das Geraet ab
# ("Command rejected", 10.10.2026). Abgeschaltet wird ueber die Zeitkonstante
# der Stufe: 0 = OFF. Am Ersatzgeraet stand die Stufe FAST bereits so.
EINSTELLUNG: dict[str, tuple[int, float]] = {
    "FAST": (2, 0.3),
    "SLOW": (3, 6.0),
    "OFF": (2, 0.0),
}


def arm_von_rig(agc_mode: str | None, agc_zeit_s: float | None) -> str | None:
    """Welchem Arm entspricht, was das Rig meldet? None = keinem / unbekannt."""
    if agc_mode is None or agc_zeit_s is None:
        return None
    if agc_mode == "SLOW" and agc_zeit_s > 0:
        return "SLOW"
    if agc_mode == "FAST":
        return "OFF" if agc_zeit_s == 0 else "FAST"
    return None
_SALZ = "agc"


def block_von(epoch_s: float) -> int:
    return int(epoch_s // BLOCK_S)


def arm_von_block(block: int) -> str:
    ziffer = hashlib.sha256((_SALZ + str(block)).encode("ascii")).digest()[0]
    return ARME[ziffer % len(ARME)]


def arm_zu(epoch_s: float) -> str:
    return arm_von_block(block_von(epoch_s))


def mittel_und_fehler(werte: list[float]) -> tuple[float, float]:
    """Mittelwert und Standardfehler; (0, inf) bei weniger als zwei Werten."""
    n = len(werte)
    if n == 0:
        return 0.0, math.inf
    m = sum(werte) / n
    if n < 2:
        return m, math.inf
    var = sum((w - m) ** 2 for w in werte) / (n - 1)
    return m, math.sqrt(var / n)


def z_wert(a: list[float], b: list[float]) -> float | None:
    """Welch-z fuer den Unterschied zweier Mittelwerte (Bloecke als Einheit)."""
    ma, sa = mittel_und_fehler(a)
    mb, sb = mittel_und_fehler(b)
    nenner = math.hypot(sa, sb)
    if not math.isfinite(nenner) or nenner == 0:
        return None
    return (ma - mb) / nenner
