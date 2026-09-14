"""Signifikanz ohne scipy. Verschoben aus scripts/qso_bilanz.py am 2026-09-14."""
from __future__ import annotations

import math
import statistics


def urteil(k_a: int, n_a: int, k_b: int, n_b: int) -> str:
    """Ist der Unterschied zweier Quoten echt oder Rauschen?

    Zweiseitiger z-Test auf zwei Anteile, ohne scipy (auf dem Pi nicht
    installiert). Rueckgabe ist eine kurze Klartextspalte fuer die Tabellen.

    Warum das hier steht: Wir haben mehrfach Quoten verglichen und ueber
    Unterschiede von fuenf Prozentpunkten geredet, ohne nachzurechnen, ob sie
    ueberhaupt vom Zufall zu unterscheiden sind. Beim Bandrand-Filter waren es
    11,9 % gegen 17,0 % bei n=59 — das sieht nach einem Befund aus und ist
    keiner (z = -1,05). Ohne diese Spalte optimiert man irgendwann Rauschen.
    """
    if n_a < 1 or n_b < 1:
        return "zu wenig"
    p_a, p_b = k_a / n_a, k_b / n_b
    p_gesamt = (k_a + k_b) / (n_a + n_b)
    # Der z-Test naehert die Binomialverteilung durch die Normalverteilung.
    # Das traegt erst, wenn in jeder Zelle rund fuenf Faelle erwartet werden
    # (Faustregel n*p >= 5 und n*(1-p) >= 5). Darunter liefert die Formel
    # zwar eine Zahl, aber keine Aussage: Am 12.09. stand an einem A/B mit
    # 6 von 13 gegen 3 von 21 ein "z=-2,05 echt" — bei drei Erfolgen im
    # zweiten Arm. Genau der Scheinbefund, den diese Spalte verhindern soll.
    for _n in (n_a, n_b):
        if _n * p_gesamt < 5 or _n * (1 - p_gesamt) < 5:
            return "zu wenig"
    nenner = p_gesamt * (1 - p_gesamt) * (1 / n_a + 1 / n_b)
    if nenner <= 0:
        return "zu wenig"
    z = (p_a - p_b) / math.sqrt(nenner)
    # NormalDist statt scipy: zweiseitiger p-Wert aus der Standardnormalen.
    p_wert = 2 * (1 - statistics.NormalDist().cdf(abs(z)))
    if p_wert < 0.01:
        return f"z={z:+.2f} sicher"
    if p_wert < 0.05:
        return f"z={z:+.2f} echt"
    return f"z={z:+.2f} Rauschen"


def urteil_rate(k_a: int, t_a: float, k_b: int, t_b: float) -> str:
    """Zwei Poisson-Raten (Ereignisse je Zeit) vergleichen.

    Fuer QSOs je Stunde in zwei Armen. z aus der Differenz der Raten und
    der Summe ihrer Varianzen (k/t^2). Unter fuenf Ereignissen je Arm
    keine Aussage — die Normalnaeherung traegt dort nicht.
    """
    if min(k_a, k_b) < 5 or t_a <= 0 or t_b <= 0:
        return "zu wenig"
    r_a, r_b = k_a / t_a, k_b / t_b
    se = math.sqrt(k_a / t_a ** 2 + k_b / t_b ** 2)
    if se == 0:
        return "?"
    z = (r_a - r_b) / se
    if abs(z) >= 2.58:
        return f"echt (z={z:+.1f})"
    if abs(z) >= 1.96:
        return f"wahrscheinlich (z={z:+.1f})"
    return f"Rauschen (z={z:+.1f})"


def n_fuer_nachweis(p_erwartet: float, p_referenz: float) -> int | None:
    """Wie viele Beobachtungen braeuchte es, damit dieser Unterschied
    nachweisbar waere? Beantwortet die Frage "noch warten oder nie?"."""
    if not 0 < p_erwartet < 1 or not 0 < p_referenz < 1:
        return None
    unterschied = abs(p_erwartet - p_referenz)
    if unterschied < 1e-9:
        return None
    # z=1,96 fuer 5 %; Referenzquote als Streuungsschaetzer.
    return int(math.ceil(
        (1.96 ** 2) * p_referenz * (1 - p_referenz) / unterschied ** 2))
