"""Rig vom USB verschwunden: eine ruhige Meldung statt einer Flut.

Am 10.10.2026 schaltete Raymond das IC-7300 abends aus. Aufs Handy kamen
vier TX-Lock-Meldungen in hoechster Dringlichkeit (eine je Dienstneustart)
und 'Sendesperre haengt' — ununterscheidbar von einem Ausfall wie am 02.10.
"""
from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from ft8_appliance.config.models import OperatingConfig
from ft8_appliance.runtime.orchestrator import Orchestrator


def _stub(tmp_path, *, am_usb: bool, tx_gerade=False, vd=14.0, bedient=False, weg_seit=0.0):
    geraet = tmp_path / "ttyUSB0"
    if am_usb:
        geraet.write_text("")
    jetzt = time.monotonic()
    ntfy = SimpleNamespace(enabled=True, notify=AsyncMock())
    o = SimpleNamespace(
        config=SimpleNamespace(demo_mode=False, rig=SimpleNamespace(serial_device=str(geraet)),
                               operating=OperatingConfig()),
        integrations=SimpleNamespace(ntfy=ntfy),
        _rig_weg_seit=weg_seit, _last_rig_at=jetzt - 65.0,
        _ptt_on_at=(jetzt - 70.0) if tx_gerade else (jetzt - 600.0),
        _vd_log={False: (vd, jetzt - 100.0)},
        _rig_bedient_at=(jetzt - 120.0) if bedient else 0.0,
        _lock_push_at=0.0,
        _maybe_persist_runtime_state=lambda force=False: None,
    )
    for name in ("_rig_am_usb", "_rig_weg_indizien", "_melde_rig_weg", "_melde_rig_da"):
        setattr(o, name, getattr(Orchestrator, name).__get__(o))
    return o, ntfy


GRUND = "rig_link_guard: Seit 65 s keine Messwerte vom Rig (max 60 s)"


@pytest.mark.asyncio
async def test_ausschalten_gibt_eine_ruhige_meldung(tmp_path) -> None:
    o, ntfy = _stub(tmp_path, am_usb=False, bedient=True)
    await Orchestrator._do_tx_locked(o, {"reason": GRUND})
    ntfy.notify.assert_awaited_once()
    text = ntfy.notify.await_args.args[0]
    assert ntfy.notify.await_args.kwargs["priority"] == "default"
    assert "ausgeschaltet" in text and "14.0 V" in text and "bedient" in text
    assert o._rig_weg_seit > 0

    # Dienstneustart, Rig noch aus: nichts mehr
    await Orchestrator._do_tx_locked(o, {"reason": GRUND})
    await Orchestrator._melde_haengende_sperre(o, 10.0, GRUND, pendelt=False)
    assert ntfy.notify.await_count == 1


@pytest.mark.asyncio
async def test_ausfall_nach_aussendung_bleibt_dringend(tmp_path) -> None:
    """02.10.2026: weg eine Sekunde nach dem Ende einer Aussendung."""
    o, ntfy = _stub(tmp_path, am_usb=False, tx_gerade=True)
    await Orchestrator._do_tx_locked(o, {"reason": GRUND})
    assert ntfy.notify.await_args.kwargs["priority"] == "urgent"
    assert "NICHT" in ntfy.notify.await_args.args[0]

    o, ntfy = _stub(tmp_path, am_usb=False, vd=16.0)
    await Orchestrator._do_tx_locked(o, {"reason": GRUND})
    assert ntfy.notify.await_args.kwargs["priority"] == "urgent"


@pytest.mark.asyncio
async def test_rig_am_usb_aber_stumm_ist_weiter_ein_alarm(tmp_path) -> None:
    o, ntfy = _stub(tmp_path, am_usb=True)
    await Orchestrator._do_tx_locked(o, {"reason": GRUND})
    assert ntfy.notify.await_args.kwargs["priority"] == "urgent"
    assert o._rig_weg_seit == 0

    # und andere Sperren (SWR) sowieso
    o, ntfy = _stub(tmp_path, am_usb=False)
    await Orchestrator._do_tx_locked(o, {"reason": "swr_guard: SWR 3.2"})
    assert ntfy.notify.await_args.kwargs["priority"] == "urgent"


@pytest.mark.asyncio
async def test_wiedereinschalten_wird_gemeldet(tmp_path) -> None:
    o, ntfy = _stub(tmp_path, am_usb=True, weg_seit=time.time() - 11 * 3600)
    await o._melde_rig_da()
    assert o._rig_weg_seit == 0
    assert "11.0 h" in ntfy.notify.await_args.args[0]
    await o._melde_rig_da()
    assert ntfy.notify.await_count == 1


@pytest.mark.asyncio
async def test_verstell_meldung_hoechstens_alle_zehn_minuten() -> None:
    """Am 10.10.2026 kamen neun Meldungen in fuenf Minuten, weil jemand am
    RF/SQL-Knopf drehte und die Station jedes Mal zurueckstellte."""
    from tests.test_empfangs_tamper import _snap, _stub as tamper_stub
    o = tamper_stub(_snap(rf_gain=0.4))
    o.rig.set_level = AsyncMock()
    for _ in range(4):
        o._last_rig = _snap(rf_gain=0.4)
        Orchestrator._pruefe_empfang(o, "PKTUSB", "PKTUSB")
        o._last_rig = _snap()                      # zurueckgestellt
        Orchestrator._pruefe_empfang(o, "PKTUSB", "PKTUSB")
        await asyncio.sleep(0)
    assert o._notify_empfang_tamper.call_count == 1
    assert o._rig_bedient_at > 0
