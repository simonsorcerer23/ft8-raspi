"""Nach einem FT8/FT4- oder Bandwechsel rufen alte Decodes niemanden mehr an.

MK2 am 23.11.2026: Der Autopilot schaltete um 10:05:15 auf FT4, eine
Sekunde spaeter lieferte die zweite Decoder-Stufe noch Funde aus dem
letzten FT8-Slot, und die Station rief EA4ZM — eine FT8-Station — auf
21,140 MHz im FT4-Takt.
"""

from __future__ import annotations

import datetime as _dt
import time
from unittest.mock import patch

import pytest

from ft8_appliance.runtime.slot_clock import SlotTick
from ft8_appliance.statemachine import State
from tests.test_late_tx_guard import _msg, _orch


def _tick(posix: float, slot_s: float = 15.0) -> SlotTick:
    return SlotTick(index=3, posix=posix,
                    utc_start=_dt.datetime.fromtimestamp(posix, tz=_dt.UTC),
                    slot_seconds=slot_s)


def _hunting(o) -> None:
    o.state_machine.ctx.auto_answer = True
    o.state_machine.ctx.auto_cq = False


@pytest.mark.asyncio
async def test_spaete_decodes_von_vor_dem_wechsel_rufen_nicht_an() -> None:
    o = _orch()
    _hunting(o)
    grenze = time.time() - 0.3          # der FT8-Slot endete eben
    o._betrieb_gewechselt()             # danach: Wechsel auf FT4
    gerufen = []
    with patch.object(type(o.state_machine), "on_decodes",
                      lambda self, hw, d: gerufen.append(d)):
        await o._ingest_late_decodes([_msg("CQ EA4ZM IN80", "EA4ZM", None)], _tick(grenze))
    assert gerufen == []
    assert o.state_machine.state is State.IDLE
    assert o.state_machine.last_decodes == []


@pytest.mark.asyncio
async def test_spaete_decodes_nach_dem_wechsel_zaehlen() -> None:
    o = _orch()
    _hunting(o)
    o._betrieb_gewechselt()
    grenze = time.time() + 7.5          # erster FT4-Slot nach dem Wechsel
    gerufen = []
    with patch.object(type(o.state_machine), "on_decodes",
                      lambda self, hw, d: gerufen.append(d)):
        await o._ingest_late_decodes([_msg("CQ EA4ZM IN80", "EA4ZM", None)], _tick(grenze, 7.5))
    assert len(gerufen) == 1


@pytest.mark.asyncio
async def test_moduswechsel_und_autopilot_dial_merken_den_wechsel() -> None:
    o = _orch()
    assert o._betrieb_gewechselt_at == 0.0
    vorher = time.time()
    assert await o._sync_digital_mode_runtime("FT8", "FT4", "test") is True
    assert o._betrieb_gewechselt_at >= vorher
    o._betrieb_gewechselt_at = 0.0
    assert await o._sync_digital_mode_runtime("FT4", "FT4", "test") is False
    assert o._betrieb_gewechselt_at == 0.0     # ohne Wechsel nichts verwerfen
