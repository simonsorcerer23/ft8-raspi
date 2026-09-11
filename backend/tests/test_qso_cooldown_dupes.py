"""Nach einem QSO bleibt die Station auf diesem Band eine Weile gesperrt.

Der Erfolgs-Cooldown ist seit v0.32.0 (Call, Band)-spezifisch. Trotzdem
wurde er fuer "Routine-Ops" (rarity < 20, kein neues DXCC, kein neues Grid)
auf ein Drittel gekuerzt — mit der Begruendung, andere Baender sollten
schnell wieder pickbar sein. Genau das leistet der Band-Schluessel aber
schon; auf *demselben* Band blieb nur die Nebenwirkung.

Ergebnis am 2026-09-10, dem ersten Abend im Jagdbetrieb: EA5QS dreimal in
30 Minuten auf 20 m gearbeitet (20:06, 20:19, 20:35), EA1FUB zweimal in
14 Minuten. Drei von 23 QSOs waren Doppel-Eintraege.

Seit v0.90.0 gilt fuer alle ausser seltenem DX der volle ``qso_cooldown_min``.
"""

from __future__ import annotations

import pytest

from ft8_appliance.runtime.orchestrator import qso_cooldown_minuten


@pytest.mark.parametrize("rarity", [0, 5, 19, 20, 45, 69])
def test_regelfall_ist_der_volle_cooldown(rarity: int):
    """Auch gewoehnliche Stationen bleiben die volle Zeit gesperrt.

    Bei rarity < 20 galt frueher ein Drittel — daher die Doppel-QSOs.
    """
    minuten, art = qso_cooldown_minuten(30, rarity)
    assert minuten == 30
    assert art == "default"


@pytest.mark.parametrize("rarity", [70, 85, 100])
def test_seltenes_dx_bleibt_laenger_gesperrt(rarity: int):
    """Dort will man den anderen den Vortritt lassen."""
    minuten, art = qso_cooldown_minuten(30, rarity)
    assert minuten == 120
    assert art == "rare-DXCC"


def test_die_beobachteten_dupes_waeren_verhindert():
    """EA5QS kam nach 13 und 16 Minuten wieder dran (Spanien, rarity niedrig)."""
    minuten, _ = qso_cooldown_minuten(30, rarity=0)
    assert minuten > 16, "13- und 16-Minuten-Abstaende muessen gesperrt sein"


def test_folgt_der_eingestellten_dauer():
    assert qso_cooldown_minuten(60, 0) == (60, "default")
    assert qso_cooldown_minuten(60, 90) == (240, "rare-DXCC")
    assert qso_cooldown_minuten(0, 0) == (0, "default")
