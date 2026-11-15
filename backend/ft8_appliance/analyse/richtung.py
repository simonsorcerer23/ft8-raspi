"""Richtungswahl fuer die Rotor-Nachfuehrung (seit 2026-11-15).

Die Frage ist nicht "wohin liegt die Station, die wir gerade anrufen" —
der Rotor braucht fuer eine halbe Umdrehung laenger als ein FT8-QSO,
und jedes QSO in eine andere Richtung zu drehen, waere Verschleiss ohne
Nutzen. Gefragt ist: In welcher Richtung ist das Band gerade offen und
viel los? Die Antenne bleibt dann eine Weile dort.

Die Quelle dafuer ist PSK Reporter, aber nicht das, was uns hoert oder
was wir hoeren: Beides haengt davon ab, wohin unsere Antenne gerade zeigt,
und wuerde die eingestellte Richtung immer bestaetigen. Gezaehlt werden
die Sender, die andere Empfaenger in unserem Grossfeld (4 Stellen, etwa
JN58) gerade hoeren — deren Antennen zeigen irgendwohin, im Mittel ueber
viele Stationen ist das unabhaengig von unserer.

Am MK2 stimmte die Quelle am 15.11. mit der eigenen Erfahrung ueberein:
Um 10:38 UTC hoerten Empfaenger in JN58 vor allem Asien (60 Grad) und
Suedamerika/Afrika (240 Grad), dieselbe Verteilung wie unsere eigenen
Decodes der Vortage um 10 UTC. Morgens liegt der Schwerpunkt bei 30-60
Grad (Japan, Asien), ab Mittag bei 300 Grad (Nordamerika).

Alles hier ist reine Rechnung ohne Ein-/Ausgabe, damit die Tests sie ohne
Rotor und Netz pruefen koennen.
"""
from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from ..util.maidenhead import great_circle, locator_to_latlon

RASTER_GRAD = 10


def _gueltiger_locator(g: str | None) -> bool:
    if not g or len(g) < 4:
        return False
    g = g.upper()
    if g[:4] == "RR73":     # FT8-Gruss, kein Standort (formal gueltig: Suedpazifik)
        return False
    return (
        "A" <= g[0] <= "R" and "A" <= g[1] <= "R"
        and g[2].isdigit() and g[3].isdigit()
    )


def peilung(von: str, nach: str) -> tuple[float, float] | None:
    """(Azimut in Grad, Entfernung in km) oder None bei kaputtem Locator."""
    if not (_gueltiger_locator(von) and _gueltiger_locator(nach)):
        return None
    try:
        la1, lo1 = locator_to_latlon(von)
        la2, lo2 = locator_to_latlon(nach)
    except (ValueError, IndexError):
        return None
    km, az = great_circle(la1, lo1, la2, lo2)
    return az, km


def winkelabstand(a: float, b: float) -> float:
    """Kleinster Abstand zweier Richtungen, 0..180 Grad."""
    d = abs((a - b) % 360.0)
    return min(d, 360.0 - d)


def zaehle_sender(
    sender: Iterable[tuple[str, str]], eigenes_grid: str, *, min_km: float
) -> dict[int, int]:
    """Verschiedene Sender je 10-Grad-Fach, ab ``min_km`` Entfernung.

    ``sender`` sind Paare (Rufzeichen, Locator). Ein Rufzeichen zaehlt nur
    einmal, auch wenn es von vielen Empfaengern gemeldet wurde — sonst
    gewaenne die Richtung mit den meisten Empfaengern, nicht die mit den
    meisten Stationen. Nahe Stationen bleiben draussen: Bis rund 1000 km
    ist die Richtung der Antenne fuer FT8 zweitrangig.
    """
    gesehen: dict[str, int] = {}
    for call, grid in sender:
        call = (call or "").upper()
        if not call or call in gesehen:
            continue
        p = peilung(eigenes_grid, grid)
        if p is None:
            continue
        az, km = p
        if km < min_km:
            continue
        gesehen[call] = int(round(az / RASTER_GRAD)) * RASTER_GRAD % 360
    faecher: dict[int, int] = {}
    for fach in gesehen.values():
        faecher[fach] = faecher.get(fach, 0) + 1
    return faecher


def punkte_bei(faecher: dict[int, int], az: float, oeffnung_grad: float) -> int:
    """Sender innerhalb des Oeffnungswinkels um ``az``."""
    halb = oeffnung_grad / 2.0
    return sum(n for fach, n in faecher.items() if winkelabstand(fach, az) <= halb)


def _schwerpunkt(faecher: dict[int, int], az: float, oeffnung_grad: float) -> float:
    """Sender im Fenster, zur Mitte hin gewichtet (Dreieck, Rand = 0)."""
    halb = oeffnung_grad / 2.0
    return sum(n * (1.0 - winkelabstand(f, az) / halb)
               for f, n in faecher.items() if winkelabstand(f, az) <= halb)


def beste_richtung(
    faecher: dict[int, int], oeffnung_grad: float
) -> tuple[int, int]:
    """(Azimut, Punkte) der Richtung mit den meisten Sendern im Fenster.

    Viele Richtungen fassen dieselben Sender, sobald sie innerhalb des
    halben Oeffnungswinkels liegen. Bei Gleichstand entscheidet darum, wo
    die Sender sich sammeln (zur Mitte gewichtet), dann die kleinere
    Gradzahl — damit zeigt die Antenne auf die Mitte, nicht auf den Rand,
    und das Ergebnis ist fuer dieselben Daten immer dasselbe.
    """
    bestes: tuple[int, int, float] = (0, -1, -1.0)
    for az in range(0, 360, RASTER_GRAD):
        p = punkte_bei(faecher, az, oeffnung_grad)
        w = _schwerpunkt(faecher, az, oeffnung_grad)
        if p > bestes[1] or (p == bestes[1] and w > bestes[2] + 1e-9):
            bestes = (az, p, w)
    return bestes[0], max(bestes[1], 0)


def drehweg(von: float, nach: float, endanschlag: float) -> float:
    """Wie weit der Rotor tatsaechlich dreht, ohne den Endanschlag zu kreuzen.

    Ein Rotor wie der Create RC5B-3 hat 360 Grad Weg mit dem Anschlag bei
    Sueden ("END" auf der Skala). Von 240 nach 120 Grad geht es darum nicht
    die 120 Grad ueber Sueden, sondern 240 Grad ueber Norden.
    """
    u_von = (von - endanschlag) % 360.0
    u_nach = (nach - endanschlag) % 360.0
    return abs(u_nach - u_von)


@dataclass(frozen=True, slots=True)
class Entscheidung:
    drehen: bool
    ziel_grad: int
    punkte_ziel: int
    punkte_ist: int
    grund: str


def entscheide(
    ist_grad: float | None,
    faecher: dict[int, int],
    *,
    oeffnung_grad: float,
    min_schritt_grad: float,
    min_sender: int,
    min_vorteil: float,
) -> Entscheidung:
    """Soll gedreht werden, und wohin?

    Gedreht wird nur, wenn

    * genug Sender gemeldet sind, um ueberhaupt etwas zu sagen,
    * das Ziel mindestens ``min_schritt_grad`` von der jetzigen Richtung
      weg liegt (kleine Korrekturen bringen bei 60 Grad Oeffnung nichts),
    * im Ziel deutlich mehr los ist als in der jetzigen Richtung
      (``min_vorteil`` = 0,25 heisst 25 % mehr Sender).
    """
    gesamt = sum(faecher.values())
    ziel, p_ziel = beste_richtung(faecher, oeffnung_grad)
    if ist_grad is None:
        return Entscheidung(False, ziel, p_ziel, 0, "position_unbekannt")
    p_ist = punkte_bei(faecher, ist_grad, oeffnung_grad)
    if gesamt < min_sender:
        return Entscheidung(False, ziel, p_ziel, p_ist, "zu_wenig_meldungen")
    if winkelabstand(ziel, ist_grad) < min_schritt_grad:
        return Entscheidung(False, ziel, p_ziel, p_ist, "steht_richtig")
    if p_ziel < p_ist * (1.0 + min_vorteil) or p_ziel - p_ist < 3:
        return Entscheidung(False, ziel, p_ziel, p_ist, "vorteil_zu_klein")
    return Entscheidung(True, ziel, p_ziel, p_ist, "besser")
