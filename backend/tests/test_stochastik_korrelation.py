"""Rangkorrelation und Tagesgang — die Werkzeuge fuer die Umgebungsfragen.

Die Sonnenindizes liefen seit dem 12.09. ohne Auswertung mit. Wer sie gegen
Empfangsberichte haelt, ohne vorher den Tagesgang herauszurechnen, findet
immer einen Zusammenhang: mittags ist mehr los als nachts.
"""
from __future__ import annotations

import pytest

from ft8_appliance.analyse.stochastik import (
    bereinige_tagesgang, spearman, urteil_korrelation,
)


def test_spearman_grenzfaelle() -> None:
    assert spearman([1, 2, 3, 4], [10, 20, 30, 40]) == pytest.approx(1.0)
    assert spearman([1, 2, 3, 4], [9, 7, 5, 1]) == pytest.approx(-1.0)
    assert spearman([1, 2, 3], [5, 5, 5]) is None, "konstante Reihe"
    assert spearman([1, 2], [3, 4]) is None, "zu kurz"


def test_spearman_ist_unempfindlich_gegen_stufenabstaende() -> None:
    """K-Index 1, 2, 9 mit monoton fallenden Werten: perfekte Rangfolge,
    obwohl der Sprung auf 9 riesig ist."""
    assert spearman([1, 2, 9], [300, 200, 190]) == pytest.approx(-1.0)


def test_spearman_mittelt_bindungen() -> None:
    r = spearman([1, 1, 2, 2, 3, 3], [1, 2, 3, 4, 5, 6])
    assert 0.9 < r < 1.0


def test_urteil_haengt_an_der_datenmenge() -> None:
    assert urteil_korrelation(0.9, 5) == "zu wenig"
    assert urteil_korrelation(0.9, 20) == "deutlich"
    # r=0,5 bei n=20: t=2,45 — ein Hinweis, kein Befund
    assert urteil_korrelation(0.5, 20) == "Hinweis"
    assert urteil_korrelation(0.1, 40) == "keiner erkennbar"
    assert urteil_korrelation(None, 40) == "keine Streuung"


def test_tagesgang_wird_herausgerechnet() -> None:
    """Mittags zehnmal so viele Berichte wie nachts — an beiden Tagen. Nach
    der Bereinigung bleibt nur der Unterschied zwischen den Tagen: Tag 2 lag
    um die Haelfte hoeher, zu jeder Uhrzeit."""
    werte = {
        "2026-09-15 02": 10, "2026-09-15 12": 100,
        "2026-09-16 02": 15, "2026-09-16 12": 150,
    }
    rein = bereinige_tagesgang(werte)
    assert rein["2026-09-15 02"] == pytest.approx(rein["2026-09-15 12"])
    assert rein["2026-09-16 12"] / rein["2026-09-15 12"] == pytest.approx(1.5)


def test_einmalige_uhrzeiten_fallen_heraus() -> None:
    rein = bereinige_tagesgang({"2026-09-15 02": 10, "2026-09-16 02": 12,
                                "2026-09-16 13": 99})
    assert "2026-09-16 13" not in rein


def test_mantel_haenszel_gleiche_arme() -> None:
    from ft8_appliance.analyse.stochastik import mantel_haenszel
    quote, z, p = mantel_haenszel([(20, 100, 20, 100), (5, 50, 5, 50)])
    assert quote == pytest.approx(1.0) and z == pytest.approx(0.0) and p == pytest.approx(1.0)


def test_mantel_haenszel_erkennt_scheinbaren_vorsprung_aus_der_schichtung() -> None:
    """Arm a hat viele starke Ziele (schliessen oft ab), Arm b viele schwache.
    Roh sieht a besser aus; innerhalb jeder SNR-Klasse sind beide gleich."""
    from ft8_appliance.analyse.stochastik import mantel_haenszel
    stark, schwach = (60, 200, 15, 50), (5, 50, 20, 200)
    roh_a, roh_b = (60 + 5) / 250, (15 + 20) / 250
    assert roh_a > roh_b * 1.5
    quote, z, p = mantel_haenszel([stark, schwach])
    assert quote == pytest.approx(1.0) and abs(z) < 0.01


def test_mantel_haenszel_echter_unterschied() -> None:
    from ft8_appliance.analyse.stochastik import mantel_haenszel
    quote, z, p = mantel_haenszel([(40, 200, 20, 200), (30, 150, 15, 150)])
    assert quote > 1.5 and z > 2.5 and p < 0.05
    assert mantel_haenszel([]) == (None, None, None)
