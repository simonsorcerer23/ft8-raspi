"""Der Signifikanz-Helfer der Bilanz.

Er entscheidet mit, ob wir auf einen Unterschied reagieren. Beim
Bandrand-Filter sahen 11,9 % gegen 17,0 % nach einem Befund aus und waren
keiner — ohne diese Rechnung optimiert man irgendwann Rauschen.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_pfad = Path(__file__).resolve().parents[2] / "scripts" / "qso_bilanz.py"
_spec = importlib.util.spec_from_file_location("qso_bilanz", _pfad)
qb = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(qb)


def test_bandrand_ist_rauschen():
    """Der reale Fall vom 2026-09-12: 7 von 59 gegen 202 von 1168."""
    assert "Rauschen" in qb.urteil(7, 59, 202, 1168)


def test_klarer_unterschied_wird_erkannt():
    """10 % gegen 40 % bei je 100 — das muss durchkommen, sonst ist der
    Test blind fuer echte Effekte."""
    ergebnis = qb.urteil(10, 100, 40, 100)
    assert "sicher" in ergebnis and "Rauschen" not in ergebnis


def test_knapper_fall_landet_zwischen_den_stuehlen():
    """Ein Unterschied knapp unter 5 % Irrtumswahrscheinlichkeit heisst
    'echt', nicht 'sicher' — die Abstufung darf nicht zusammenfallen."""
    stufen = {qb.urteil(k, 200, 40, 200).split()[-1] for k in (40, 25, 10)}
    assert stufen == {"Rauschen", "echt", "sicher"}, stufen


@pytest.mark.parametrize("args", [(0, 0, 5, 10), (5, 10, 0, 0), (0, 0, 0, 0)])
def test_leere_gruppen_behaupten_nichts(args):
    assert qb.urteil(*args) == "zu wenig"


def test_identische_quoten_sind_rauschen():
    assert "Rauschen" in qb.urteil(20, 100, 40, 200)


def test_n_fuer_nachweis_waechst_wenn_der_unterschied_kleiner_wird():
    gross = qb.n_fuer_nachweis(0.05, 0.170)
    klein = qb.n_fuer_nachweis(0.160, 0.170)
    assert gross is not None and klein is not None
    assert klein > gross * 10


def test_n_fuer_nachweis_ohne_unterschied():
    assert qb.n_fuer_nachweis(0.17, 0.17) is None


def test_zu_kleine_zellen_liefern_kein_urteil() -> None:
    """Der z-Test naehert die Binomial- durch die Normalverteilung.

    Das traegt erst ab rund fuenf erwarteten Faellen je Zelle. Darunter
    rechnet die Formel weiter und liefert eine Zahl, die nichts bedeutet:
    Am 12.09. stand an einem frisch gestarteten A/B "z=-2,05 echt" — bei
    sechs von dreizehn gegen drei von einundzwanzig Anrufen. Genau der
    Scheinbefund, den diese Spalte verhindern soll.
    """
    assert qb.urteil(6, 13, 3, 21) == "zu wenig"
    assert qb.urteil(0, 25, 4, 50) == "zu wenig"
    assert qb.urteil(1, 8, 1, 9) == "zu wenig"


def test_grosse_stichproben_werden_weiter_beurteilt() -> None:
    """Die Schranke darf echte Befunde nicht mitnehmen."""
    assert "z=" in qb.urteil(200, 1000, 150, 1000)
    assert "z=" in qb.urteil(80, 381, 60, 389)
