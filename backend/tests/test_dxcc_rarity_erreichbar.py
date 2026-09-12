"""Die seltensten DXCC waren fuer den Picker unsichtbar.

Die Rarity-Tabelle benutzt cty-Entitaetskuerzel ("FT/w" fuer Crozet) —
sie stammt aus derselben Quelle wie cty.dat. ``rarity_for`` durchsucht sie
aber mit Rufzeichen-Praefixen, und die beiden Namensraeume decken sich
nicht: Crozet funkt als FT8WW, ein Praefix "FT/w" kommt in keinem
Rufzeichen vor. Fuenf der hoechstbewerteten Eintraege lieferten deshalb 0 —
denselben Wert wie eine Nachbarstation aus Deutschland.

Gefunden am 2026-09-12, weil der Pile-Up-Filter (Schwelle 70) in vierzehn
Tagen kein einziges Mal ausgeloest hatte.
"""

from __future__ import annotations

import pytest

from ft8_appliance.integrations import dxcc_rarity as dr
from ft8_appliance.integrations.dxcc_rarity import rarity_for, rarity_for_entity


@pytest.mark.parametrize(("kuerzel", "erwartet"), [
    ("FT/w", 97),   # Crozet — funkt als FT8WW
    ("FT/g", 90),   # Glorioso — FT4GL
    ("FT/j", 89),   # Juan de Nova — FT4JA
    ("FT/t", 88),   # Tromelin — FT4TA
    ("3Y/B", 99),   # Bouvet
])
def test_kuerzel_mit_schraegstrich_sind_erreichbar(kuerzel, erwartet):
    assert rarity_for_entity(kuerzel) == erwartet


def test_gross_und_kleinschreibung_egal():
    """cty.dat schreibt die Kuerzel klein ("FT/w"), die Tabelle gross."""
    assert rarity_for_entity("ft/w") == rarity_for_entity("FT/W") == 97


@pytest.mark.parametrize("wert", [None, "", "   ", "gibt-es-nicht"])
def test_unbekanntes_gibt_null(wert):
    assert rarity_for_entity(wert) == 0


def test_der_praefixweg_bleibt_noetig():
    """BS7H fuehrt cty.dat nicht als eigene Entitaet — ueber das Kuerzel
    waere der Eintrag verloren. Deshalb ersetzt der neue Weg den alten
    nicht, sondern ergaenzt ihn."""
    assert rarity_for("BS7H") == 98
    assert rarity_for_entity("BS7H") == 98  # steht als Praefix in der Tabelle


def test_haeufige_laender_bleiben_bei_null():
    for call in ("DL1ABC", "W1AW", "RA3GZ", "EC5M"):
        assert rarity_for(call) == 0, call


def test_schraegstrich_eintraege_sind_ueber_praefixe_nicht_zu_finden():
    """Der eigentliche Befund, als Test festgehalten: solange nur der
    Praefixweg existierte, war fuer diese Entitaeten nichts zu holen."""
    for call in ("FT8WW", "FT4GL", "FT4JA", "FT4TA"):
        assert rarity_for(call) == 0, (
            f"{call} liefert ueber den Praefixweg ploetzlich einen Wert — "
            "dann ist die Tabelle umgebaut worden und dieser Test veraltet"
        )


def test_die_pile_up_schwelle_ist_erreichbar():
    """Der Filter verlangt >= 70. Ueber die Kuerzel kommen jetzt Eintraege
    dorthin, die vorher unerreichbar waren."""
    erreichbar = [k for k, v in dr._RARITY_TABLE.items()
                  if v >= 70 and rarity_for_entity(k) >= 70]
    assert len(erreichbar) >= 31, len(erreichbar)
