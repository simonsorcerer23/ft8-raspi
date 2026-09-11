"""Gebucht wird erst, wenn die Aussendung wirklich stattfindet.

Zwei Buchungen standen vor den Verwerfungspunkten von ``_do_tx_message``:
der Frequenz-Versuch der Reputation und ``_last_tx_message_at``. Beide
liefen also auch dann, wenn die Aussendung gleich darauf entfiel — durch
die B4-Regel (Burst mitten im Slot) oder weil noch ein Burst lief.

Die Folgen: Der Nenner der Frequenz-Reputation war zu gross, die
Erfolgsquote je Frequenzbin dadurch zu klein. Und ``_last_tx_message_at``
ist genau die Marke, an der der Vorab-Decode erkennt, ob der Slot taub
ist — eine verworfene Aussendung liess ihn einen Empfangsslot
ueberspringen.

Aufgefallen beim Nachpruefen des Vorab-Decodes: Seit v0.105.0 sendet auch
der Vorab-Pfad, und die zweite Aussendung desselben Slots wird regelmaessig
verworfen.
"""

from __future__ import annotations

import inspect

from ft8_appliance.runtime import orchestrator as orch_mod


def _quelle() -> str:
    return inspect.getsource(orch_mod.Orchestrator._do_tx_message)


def _position(nadel: str) -> int:
    q = _quelle()
    i = q.find(nadel)
    assert i >= 0, f"nicht gefunden: {nadel}"
    return i


def test_reputation_erst_nach_der_b4_pruefung():
    assert _position("_record_tx_start_offset") < _position("_freq_reputation[key]")


def test_reputation_erst_nach_der_burst_pruefung():
    assert _position("if self._tx_burst_active:") < _position("_freq_reputation[key]")


def test_sendemarke_erst_nach_den_pruefungen():
    """Sonst ueberspringt der Vorab-Decode einen Empfangsslot."""
    assert _position("_record_tx_start_offset") < _position("_last_tx_message_at = ")


def test_burst_pruefung_bleibt_auch_hinter_dem_synth():
    """Dort liegt ein await dazwischen — der Zustand kann sich aendern."""
    assert _quelle().count("if self._tx_burst_active:") == 2


def test_logzeile_erst_wenn_wirklich_gesendet_wird():
    """Die TX_MESSAGE-Zeile ist die Spur, an der sich im Betrieb ablesen
    laesst, was tatsaechlich rausging. Vor den Verwerfungspunkten
    protokollierte sie auch Aussendungen, die nie stattfanden."""
    assert _position("_record_tx_start_offset") < _position('log.info("TX_MESSAGE')
