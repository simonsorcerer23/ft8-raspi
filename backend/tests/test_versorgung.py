"""Versorgungsspannung und Endstufenstrom des Rigs mitschreiben.

Am 02.10.2026 verschwand das IC-7300 eine Sekunde nach dem Ende einer
Aussendung vom USB und kam nicht wieder. Raymond nannte das Netzteil (24 statt
12 V) als Ursache — belegen liess sich das nicht, weil die Spannung nirgends
aufgezeichnet war.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from ft8_appliance.config.models import OperatingConfig
from ft8_appliance.rig.rigctld_client import RigctldClient, RigSnapshot
from ft8_appliance.runtime.orchestrator import Orchestrator


def _stub(vd, *, ptt=False, id_a=None, db=True, ptt_on_at=0.0):
    o = SimpleNamespace(
        _last_rig=SimpleNamespace(vd_v=vd, id_a=id_a, ptt=ptt),
        _vd_log={}, _vd_warn_at=0.0, _ptt_on_at=ptt_on_at, db_enabled=db,
        config=SimpleNamespace(operating=OperatingConfig()),
        _persist_versorgung=AsyncMock(), _notify_versorgung=AsyncMock(),
        gestartet=[],
    )
    for name in ("_VD_SCHRITT_V", "_VD_GRUNDLINIE_S", "_VD_WARN_ABSTAND_S"):
        setattr(o, name, getattr(Orchestrator, name))
    o._spawn = lambda coro, name=None: o.gestartet.append(asyncio.ensure_future(coro))
    return o


async def _lauf(o):
    Orchestrator._buche_versorgung(o)
    await asyncio.gather(*o.gestartet)
    await asyncio.sleep(0)


@pytest.mark.asyncio
async def test_empfang_und_senden_werden_getrennt_gebucht() -> None:
    o = _stub(13.8)
    await _lauf(o)
    o._last_rig = SimpleNamespace(vd_v=13.1, id_a=17.5, ptt=True)
    await _lauf(o)
    aufrufe = [c.args for c in o._persist_versorgung.await_args_list]
    assert aufrufe == [(False, 13.8, None), (True, 13.1, 17.5)]
    o._notify_versorgung.assert_not_called()


@pytest.mark.asyncio
async def test_gleicher_wert_wird_nicht_jede_sekunde_geschrieben() -> None:
    o = _stub(13.8)
    for _ in range(5):
        await _lauf(o)
    assert o._persist_versorgung.await_count == 1
    # merkliche Aenderung → neuer Eintrag
    o._last_rig.vd_v = 13.5
    await _lauf(o)
    assert o._persist_versorgung.await_count == 2


@pytest.mark.asyncio
async def test_ueberspannung_meldet_einmal_und_bucht() -> None:
    o = _stub(16.0)
    await _lauf(o)
    await _lauf(o)
    o._notify_versorgung.assert_called_once()
    assert o._notify_versorgung.call_args.kwargs["hoch"] is True
    assert o._persist_versorgung.await_count == 1


@pytest.mark.asyncio
async def test_unterspannung_meldet_auch_ohne_datenbank() -> None:
    o = _stub(11.2, db=False)
    await _lauf(o)
    assert o._notify_versorgung.call_args.kwargs["hoch"] is False
    o._persist_versorgung.assert_not_called()


@pytest.mark.asyncio
async def test_rig_ohne_spannungsmesser_bleibt_still() -> None:
    for wert in (None, 0.0):
        o = _stub(wert)
        await _lauf(o)
        o._persist_versorgung.assert_not_called()
        o._notify_versorgung.assert_not_called()


@pytest.mark.asyncio
async def test_direkt_nach_dem_tasten_wird_nicht_gemessen() -> None:
    import time
    o = _stub(13.0, ptt=True, ptt_on_at=time.monotonic())
    await _lauf(o)
    o._persist_versorgung.assert_not_called()


@pytest.mark.asyncio
async def test_snapshot_fragt_spannung_und_strom_ab() -> None:
    c = RigctldClient.__new__(RigctldClient)
    werte = {"VD_METER": 13.72, "ID_METER": 16.4}

    async def level(name):
        if name in werte:
            return werte[name]
        raise RuntimeError("kennt das Rig nicht")

    c.get_level = level
    for name in ("get_freq", "get_mode", "get_ptt", "get_func", "get_agc_mode",
                 "get_vfo", "get_split", "get_battery_v"):
        setattr(c, name, AsyncMock(side_effect=RuntimeError))
    snap = await c.snapshot()
    assert isinstance(snap, RigSnapshot)
    assert snap.vd_v == 13.72 and snap.id_a == 16.4


def test_grenzen_liegen_um_die_nennspannung() -> None:
    op = OperatingConfig()
    assert op.rig_vd_min_v < 13.8 < op.rig_vd_max_v
