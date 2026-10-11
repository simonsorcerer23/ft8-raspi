"""Antworten im laufenden QSO, die zu spaet im Slot faellig werden.

Findet erst die jt9-Stufe den Rapport des Partners, kam unsere Antwort
mitten im Slot — und entfiel ersatzlos (28.09.–11.10.2026: 379 Verwuerfe).
Jetzt geht sie zur naechsten eigenen Slot-Grenze hinaus.
"""
from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from ft8_appliance.runtime.orchestrator import Orchestrator
from ft8_appliance.statemachine.states import State


def _stub(*, partner="OZ1A", zustand=State.QSO_REPORT, aktiv=True):
    o = SimpleNamespace(
        config=SimpleNamespace(operating=SimpleNamespace(mode="FT8")),
        state_machine=SimpleNamespace(
            state=zustand,
            qso=SimpleNamespace(their_call=partner) if partner else None),
        _tx_aufgeschoben_nr=0, _AUFSCHIEBBAR=Orchestrator._AUFSCHIEBBAR,
        _station_aktiv=lambda: aktiv, _do_tx_message=AsyncMock(), gestartet=[],
    )

    def _spawn(coro, name=None):
        o.gestartet.append(coro)
    o._spawn = _spawn
    o._sende_aufgeschoben = lambda *a: Orchestrator._sende_aufgeschoben(o, *a)
    return o


def _r_report():
    return {"message": "OZ1A DK9XR R-09", "kind": "r_report", "freq_offset_hz": 1500}


def test_nur_qso_antworten_werden_aufgeschoben_und_nur_einmal() -> None:
    o = _stub()
    for kind in ("cq", "respond_grid"):
        Orchestrator._schiebe_tx_auf(o, {"message": "x", "kind": kind})
    Orchestrator._schiebe_tx_auf(o, {**_r_report(), "_aufgeschoben": True})
    assert o.gestartet == []
    for kind in ("respond_report", "r_report", "rr73", "ack73"):
        Orchestrator._schiebe_tx_auf(o, {"message": "x", "kind": kind})
    assert len(o.gestartet) == 4
    for c in o.gestartet:
        c.close()


def test_faellig_ist_die_uebernaechste_slot_grenze(monkeypatch) -> None:
    o = _stub()
    monkeypatch.setattr(time, "time", lambda: 1_791_700_011.5)   # 6,5 s im Slot (…005 ist eine Grenze)
    Orchestrator._schiebe_tx_auf(o, _r_report())
    coro = o.gestartet[0]
    faellig = coro.cr_frame.f_locals["faellig"]
    coro.close()
    assert faellig % 15 == 0
    assert faellig - 1_791_700_011.5 == pytest.approx(23.5, abs=0.01)


@pytest.mark.asyncio
async def test_aufgeschobene_antwort_geht_hinaus() -> None:
    o = _stub()
    Orchestrator._schiebe_tx_auf(o, _r_report())
    o.gestartet[0].close()
    await Orchestrator._sende_aufgeschoben(o, _r_report(), o._tx_aufgeschoben_nr,
                                           time.time(), "OZ1A")
    o._do_tx_message.assert_awaited_once()
    assert o._do_tx_message.await_args.args[0]["_aufgeschoben"] is True


@pytest.mark.asyncio
async def test_aufgeschobenes_entfaellt_wenn_es_ueberholt_ist() -> None:
    # neue Entscheidung der State-Machine
    o = _stub()
    await Orchestrator._sende_aufgeschoben(o, _r_report(), 5, time.time(), "OZ1A")
    o._do_tx_message.assert_not_called()
    # QSO mit ihm ist vorbei
    for kw in ({"partner": "EA1XYZ"}, {"partner": None}, {"aktiv": False},
               {"zustand": State.TX_LOCKED}):
        o = _stub(**kw)
        await Orchestrator._sende_aufgeschoben(o, _r_report(), 0, time.time(), "OZ1A")
        o._do_tx_message.assert_not_called()
    # das RR73 geht auch nach dem Loggen noch hinaus
    o = _stub(partner=None, zustand=State.IDLE)
    await Orchestrator._sende_aufgeschoben(
        o, {"message": "OZ1A DK9XR RR73", "kind": "rr73"}, 0, time.time(), "OZ1A")
    o._do_tx_message.assert_awaited_once()
