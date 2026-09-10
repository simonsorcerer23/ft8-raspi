"""Wann darf die Empfangsabfrage (wer hoert uns) pausieren?

2026-09-10: "auto_cq aktiv" war ein Pausengrund — sinnvoll, solange CQ und
Hunting sich ausschlossen: Wer nur CQ ruft, pickt niemanden und braucht die
Daten nicht. Seit boot_mode "cq+hunt" laufen beide gleichzeitig, und das ist
der Normalbetrieb der Station. Die Abfrage pausierte damit dauerhaft:

  * der Picker sah nie, wer uns hoert — das Schwach-Gate liess schwache
    Ziele grundsaetzlich durchfallen, weil ihm die Bestaetigung fehlte
    (7 Tage Telemetrie: 520 Versuche ohne Bestaetigung, 115 mit)
  * der Rueckfall der Empfaenger-Ansicht bei 503 vom Server lief ins Leere,
    weil der Cache leer blieb

Zugleich extern geprueft: PSK Reporter kannte in zwei Stunden 226
Empfangsberichte fuer DK9XR, bis in die USA. Die Information war da, die
Box hat sie nur nicht abgeholt.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

from ft8_appliance.config import (
    AntennaConfig, AppConfig, BandConfig, OperatingConfig, OperatorConfig,
)
from ft8_appliance.rig.rigctld_client import RigSnapshot
from ft8_appliance.runtime import FakeSlotClock, Orchestrator


def _orch() -> Orchestrator:
    async def _no_decodes(_tick):
        return []

    o = Orchestrator(
        config=AppConfig(
            operator=OperatorConfig(callsign="DK9XR", default_locator="JN58td"),
            bands=[BandConfig(name="20m", freq_khz=14074, antenna="endfed")],
            antennas=[AntennaConfig(name="endfed", bands=["20m"])],
            operating=OperatingConfig(),
        ),
        rig=AsyncMock(), gps=AsyncMock(),
        decode_source=_no_decodes, slot_clock=FakeSlotClock(),
    )
    o._last_rig = RigSnapshot(freq_hz=14_074_000)
    # Die Pausenpruefung verlangt eine verdrahtete RX-Kette; die Test-Attrappe
    # oben ist eine blosse Funktion ohne metrics.
    o.decode_source = SimpleNamespace(metrics=SimpleNamespace())
    return o


def test_hunting_mit_gleichzeitigem_cq_fragt_ab():
    """Der Normalbetrieb der Station: boot_mode cq+hunt."""
    o = _orch()
    o.state_machine.ctx.auto_answer = True
    o.state_machine.ctx.auto_cq = True

    assert o._psk_reciprocity_pause_reason() is None, (
        "wer jagt, braucht die Empfangsdaten — auch wenn nebenbei CQ laeuft"
    )


def test_ohne_hunting_bleibt_pausiert():
    """Ohne Picker gibt es niemanden, der die Daten nutzt."""
    o = _orch()
    o.state_machine.ctx.auto_answer = False
    o.state_machine.ctx.auto_cq = True

    assert o._psk_reciprocity_pause_reason() == "hunt inactive"


def test_ohne_rig_bleibt_pausiert():
    o = _orch()
    o.state_machine.ctx.auto_answer = True
    o.state_machine.ctx.auto_cq = True
    o._last_rig = RigSnapshot(freq_hz=None)

    assert o._psk_reciprocity_pause_reason() == "rig not ready"


def test_ohne_rx_kette_bleibt_pausiert():
    o = _orch()
    o.state_machine.ctx.auto_answer = True
    o.decode_source = SimpleNamespace()   # keine metrics

    assert o._psk_reciprocity_pause_reason() == "rx audio not wired"
