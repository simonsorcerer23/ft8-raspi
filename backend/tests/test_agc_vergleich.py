"""AGC-Vergleich: Arm aus der Uhr, Station stellt die AGC danach."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from ft8_appliance.analyse import agc_vergleich as agc
from ft8_appliance.config.models import _RIG_TABLE
from ft8_appliance.rig.rigctld_client import RigctldClient
from ft8_appliance.runtime.orchestrator import Orchestrator


def test_arme_sind_ueber_die_zeit_gleich_verteilt_und_stabil() -> None:
    zaehler = {a: 0 for a in agc.ARME}
    for b in range(2_000_000, 2_003_000):
        zaehler[agc.arm_von_block(b)] += 1
    assert all(900 < n < 1100 for n in zaehler.values()), zaehler
    # derselbe Block liefert immer denselben Arm, auch ueber die Zeit im Block
    anfang = 1_990_000 * agc.BLOCK_S
    assert agc.arm_zu(anfang) == agc.arm_zu(anfang + agc.BLOCK_S - 1)
    assert agc.block_von(anfang + agc.BLOCK_S) == agc.block_von(anfang) + 1


def test_z_wert_erkennt_einen_klaren_unterschied() -> None:
    a = [100.0 + i % 7 for i in range(80)]
    b = [80.0 + i % 7 for i in range(80)]
    assert agc.z_wert(a, b) > 10
    assert abs(agc.z_wert(a, list(a))) < 1e-9
    assert agc.z_wert([1.0], [2.0]) is None


@pytest.mark.asyncio
async def test_agc_namen_folgen_hamlib() -> None:
    """Bis 10.10.2026 standen 3=MEDIUM und 4=SLOW im Abbild — vertauscht."""
    c = RigctldClient.__new__(RigctldClient)
    c._lock = asyncio.Lock()
    for wert, name in ((0, "OFF"), (2, "FAST"), (3, "SLOW"), (5, "MEDIUM")):
        c._send = AsyncMock(return_value=str(wert))
        assert await c.get_agc_mode() == name
        assert agc.HAMLIB_AGC[name] == wert
    c._send = AsyncMock(return_value="RPRT 0")
    await c.set_agc(3)
    c._send.assert_awaited_with("L AGC 3")


def test_aus_heisst_zeitkonstante_null() -> None:
    """Das IC-7300 lehnt ``L AGC 0`` ab; abgeschaltet wird ueber die
    Zeitkonstante der Stufe (am 10.10.2026 am Geraet erprobt)."""
    assert agc.EINSTELLUNG["OFF"] == (2, 0.0)
    assert agc.arm_von_rig("FAST", 0.0) == "OFF"
    assert agc.arm_von_rig("FAST", 0.3) == "FAST"
    assert agc.arm_von_rig("SLOW", 6.0) == "SLOW"
    assert agc.arm_von_rig("MEDIUM", 2.0) is None
    assert agc.arm_von_rig(None, None) is None


def _stub(arm: str | None, *, an=True, aktiv=True, burst=False):
    stufe, zeit = {"FAST": ("FAST", 0.3), "SLOW": ("SLOW", 6.0), "OFF": ("FAST", 0.0),
                   None: ("MEDIUM", 2.0)}[arm]
    o = SimpleNamespace(
        config=SimpleNamespace(operating=SimpleNamespace(rig_agc_ab=an)),
        _tamper_armed=True, _tx_burst_active=burst, _agc_gesetzt_at=0.0,
        _last_rig=SimpleNamespace(agc_mode=stufe, agc_zeit_s=zeit),
        rig=SimpleNamespace(set_agc=AsyncMock(), set_level=AsyncMock()),
        _station_aktiv=lambda: aktiv,
    )
    o._agc_setzen = lambda s, z: Orchestrator._agc_setzen(o, s, z)
    return o


@pytest.mark.asyncio
async def test_station_stellt_die_agc_auf_den_arm() -> None:
    import time
    soll = agc.arm_zu(time.time())
    falsch = next(a for a in agc.ARME if a != soll)
    for ist in (falsch, None):          # anderer Arm oder eine Stufe ausserhalb des Vergleichs
        o = _stub(ist)
        Orchestrator._agc_vergleich(o, "PKTUSB", "PKTUSB")
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        stufe, zeit = agc.EINSTELLUNG[soll]
        o.rig.set_agc.assert_awaited_once_with(stufe)
        o.rig.set_level.assert_awaited_once_with("AGC_TIME", zeit)

    for kw, modus in (({"an": False}, "PKTUSB"), ({"aktiv": False}, "PKTUSB"),
                      ({"burst": True}, "PKTUSB"), ({}, "USB")):
        o = _stub(falsch, **kw)
        Orchestrator._agc_vergleich(o, modus, "PKTUSB")
        await asyncio.sleep(0)
        o.rig.set_agc.assert_not_called()

    o = _stub(soll)
    Orchestrator._agc_vergleich(o, "PKTUSB", "PKTUSB")
    await asyncio.sleep(0)
    o.rig.set_agc.assert_not_called()


def test_icom_profile_verlangen_den_breitesten_filter() -> None:
    for name, profil in _RIG_TABLE.items():
        if name.startswith("ic"):
            assert profil.mode_width_hz == 3600, name
