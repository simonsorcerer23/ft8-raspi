"""Keine Sperr-Meldung aufs Handy fuer die Startphase.

18.11.2026: Nach jedem Hochfahren kam "TX gesperrt: Zeit-Offset +99 s".
Das ist der Platzhalter vor der ersten Messung; die Sperre hebt die Station
danach selbst auf (_loese_startsperre). Echte Gruende meldet der Sperr-Waechter.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from ft8_appliance.config.models import OperatingConfig
from ft8_appliance.runtime.orchestrator import Orchestrator

GRUND = "time_guard: Zeit-Offset +99.000 s > 0.5 s erlaubt"


def _stub(*, startphase: bool):
    ntfy = SimpleNamespace(enabled=True, notify=AsyncMock())
    o = SimpleNamespace(
        config=SimpleNamespace(operating=OperatingConfig()),
        integrations=SimpleNamespace(ntfy=ntfy),
        _erste_hw_messung_offen=startphase,
        _rig_am_usb=lambda: True,
    )
    return o, ntfy


@pytest.mark.asyncio
async def test_sperre_vor_der_ersten_messung_schickt_keinen_push() -> None:
    o, ntfy = _stub(startphase=True)
    await Orchestrator._do_tx_locked(o, {"reason": GRUND})
    ntfy.notify.assert_not_awaited()


@pytest.mark.asyncio
async def test_sperre_im_betrieb_meldet_weiter_dringend() -> None:
    o, ntfy = _stub(startphase=False)
    await Orchestrator._do_tx_locked(o, {"reason": GRUND})
    ntfy.notify.assert_awaited_once()
    assert ntfy.notify.await_args.kwargs["priority"] == "urgent"
