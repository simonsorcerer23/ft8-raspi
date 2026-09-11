"""Gebucht wird erst, wenn die Aussendung wirklich stattfindet.

Drei Buchungen standen vor den Verwerfungspunkten von ``_do_tx_message``:
der Frequenz-Versuch der Reputation, ``_last_tx_message_at`` und die
``TX_MESSAGE``-Logzeile. Alle drei liefen also auch dann, wenn die
Aussendung gleich darauf entfiel — durch die B4-Regel (Burst mitten im
Slot) oder weil noch ein Burst lief.

Die Folgen: Der Nenner der Frequenz-Reputation war zu gross, die
Erfolgsquote je Frequenzbin dadurch zu klein. ``_last_tx_message_at`` ist
genau die Marke, an der der Vorab-Decode erkennt, ob ein Slot taub ist —
eine verworfene Aussendung liess ihn einen Empfangsslot ueberspringen. Und
die Logzeile meldete Aussendungen, die nie stattfanden.

Aufgefallen beim Nachpruefen des Vorab-Decodes: Seit v0.105.0 sendet auch
der Vorab-Pfad, und die zweite Aussendung desselben Slots wird regelmaessig
verworfen.
"""

from __future__ import annotations

import logging

import pytest

from .test_wave2_timing import _orch, _tx


class _Mitschnitt(logging.Handler):
    """Direkt am Modul-Logger — caplog haengt am Root, und dessen
    Weiterleitung ist hier nicht verlaesslich."""

    def __init__(self):
        super().__init__(level=logging.INFO)
        self.zeilen: list[str] = []

    def emit(self, record):
        self.zeilen.append(record.getMessage())


def _mitschnitt():
    log = logging.getLogger("ft8_appliance.runtime.orchestrator")
    h = _Mitschnitt()
    log.addHandler(h)
    altes_level, log.level = log.level, logging.INFO
    return log, h, altes_level


@pytest.mark.asyncio
async def test_b4_verwurf_bucht_keinen_frequenz_versuch():
    """Mitten im Slot entfaellt die Aussendung — dann ist es kein Versuch."""
    o = _orch()

    await _tx(o, phase_s=8.0, in_slot=False)

    assert o._freq_reputation == {}


@pytest.mark.asyncio
async def test_b4_verwurf_setzt_keine_sendemarke():
    """Sonst ueberspringt der Vorab-Decode den naechsten Empfangsslot."""
    o = _orch()

    await _tx(o, phase_s=8.0, in_slot=False)

    assert o._last_tx_message_at == 0.0


@pytest.mark.asyncio
async def test_echte_aussendung_bucht_beides():
    """Die Gegenprobe — sonst wuerde der Test oben auch bei kaputtem
    Buchungspfad bestehen."""
    o = _orch()

    await _tx(o, phase_s=0.4, in_slot=True)

    assert list(o._freq_reputation.values()) == [(1, 0)]
    assert o._last_tx_message_at > 0.0


@pytest.mark.asyncio
async def test_laufender_burst_bucht_nichts():
    """Die zweite Aussendung desselben Slots wird verworfen — seit dem
    Vorab-Decode ein Alltagsfall."""
    o = _orch()
    o._tx_burst_active = True

    await _tx(o, phase_s=0.4, in_slot=True)

    assert o._freq_reputation == {}
    assert o._last_tx_message_at == 0.0


@pytest.mark.asyncio
async def test_verworfene_aussendung_wird_nicht_protokolliert():
    """Die TX_MESSAGE-Zeile ist die Spur, an der sich im Betrieb ablesen
    laesst, was tatsaechlich rausging."""
    o = _orch()
    log, h, level = _mitschnitt()
    try:
        await _tx(o, phase_s=8.0, in_slot=False)
    finally:
        log.removeHandler(h); log.level = level

    assert not [z for z in h.zeilen if "TX_MESSAGE" in z]


@pytest.mark.asyncio
async def test_echte_aussendung_wird_protokolliert():
    """Die Gegenprobe — sonst bestuende der Test oben auch bei einer
    Logzeile, die nie feuert."""
    o = _orch()
    log, h, level = _mitschnitt()
    try:
        await _tx(o, phase_s=0.4, in_slot=True)
    finally:
        log.removeHandler(h); log.level = level

    assert [z for z in h.zeilen if "TX_MESSAGE" in z]
