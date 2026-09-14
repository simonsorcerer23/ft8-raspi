"""Monatsbericht der Auswahllogik — reine Textfunktion, testbar ohne DB.

Der Orchestrator holt die Zahlen und schickt den Text per ntfy; die
Bilanz zeigt dieselben Groessen ausfuehrlicher. Sprache: die der Bilanz.
Nur die Zeilenueberschriften laufen ueber die Uebersetzung.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date

from .regelregister import Regel, ueberfaellige
from .stochastik import urteil_rate


@dataclass(frozen=True, slots=True)
class Monatszahlen:
    tage: int
    std_regel: float
    qsos_regel: int
    std_kontrolle: float
    qsos_kontrolle: int
    std_gesamt: float          # ohne ARM_-Zeilen
    qsos_gesamt: int
    std_leerlauf: float
    std_ew: float = 0.0
    qsos_ew: int = 0


def baue_monatsbericht(z: Monatszahlen, heute: date | None = None,
                       t: Callable[[str], str] = lambda k: k) -> str:
    """Drei Bloecke: Kontrollarm, ueberfaellige Regeln, Ausbeute je Stunde."""
    zeilen: list[str] = []
    zeilen.append(t("push.monatspruefung_kontrolle"))
    if z.std_regel >= 1.0 and z.std_kontrolle >= 1.0:
        zeilen.append(f"  Regel: {z.qsos_regel} QSOs / {z.std_regel:.0f} h = "
                      f"{z.qsos_regel / z.std_regel:.2f}/h")
        zeilen.append(f"  Kontrolle: {z.qsos_kontrolle} QSOs / {z.std_kontrolle:.0f} h = "
                      f"{z.qsos_kontrolle / z.std_kontrolle:.2f}/h")
        zeilen.append("  " + urteil_rate(z.qsos_regel, z.std_regel, z.qsos_kontrolle, z.std_kontrolle))
    if z.std_ew >= 1.0 and z.std_regel >= 1.0:
        zeilen.append(f"  EW-Modell: {z.qsos_ew} QSOs / {z.std_ew:.0f} h = "
                      f"{z.qsos_ew / z.std_ew:.2f}/h — gegen Regel: "
                      + urteil_rate(z.qsos_ew, z.std_ew, z.qsos_regel, z.std_regel))
    else:
        zeilen.append("  noch keine Stunden je Arm")
    faellig: list[Regel] = ueberfaellige(heute)
    zeilen.append(t("push.monatspruefung_regeln") + f" ({len(faellig)})")
    for r in faellig:
        alter = "nie belegt" if r.beleg_datum is None else f"Beleg vom {r.beleg_datum.isoformat()}"
        zeilen.append(f"  {r.stufe}: {alter}")
    zeilen.append(t("push.monatspruefung_zeit"))
    if z.std_gesamt >= 1.0:
        zeilen.append(f"  {z.qsos_gesamt} QSOs in {z.std_gesamt:.0f} h = "
                      f"{z.qsos_gesamt / z.std_gesamt:.2f}/h, "
                      f"Leerlauf {100.0 * z.std_leerlauf / z.std_gesamt:.0f} %")
    else:
        zeilen.append("  noch kein Zeitprotokoll")
    return "\n".join(zeilen)
