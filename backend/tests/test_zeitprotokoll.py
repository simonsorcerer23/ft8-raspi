"""Zeitprotokoll: Sekunden je Zustand als Tageszeile.

Seit 2026-09-14. Ohne diese Reihe war "QSOs je Stunde" nicht messbar,
nur "QSOs je Anruf" — und die steigt zwangslaeufig, wenn ein Filter
Anrufe verhindert. Die Reihe muss zwei Dinge sicher koennen: Zeit dem
richtigen Zustand zuschreiben, und nach einem Neustart am selben Tag
nichts ueberschreiben.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest
from sqlalchemy import select

from ft8_appliance.db import models as m
from ft8_appliance.db.session import init_engine, session_scope
from ft8_appliance.runtime.orchestrator import Orchestrator
from ft8_appliance.statemachine.states import State


def _o(zustand: State = State.IDLE, db: bool = False) -> SimpleNamespace:
    return SimpleNamespace(
        state_machine=SimpleNamespace(state=zustand, ctx=SimpleNamespace(kontroll_arm=False)),
        db_enabled=db,
        _zeit_je_zustand={}, _zeit_zuletzt=0.0, _zeit_tag="",
        _zeit_letzte_sicherung=0.0, _zeit_gesichert={},
    )


def _tick(o: SimpleNamespace, monkeypatch, t: float, tag: str = "2026-09-14") -> None:
    import ft8_appliance.runtime.orchestrator as mod
    monkeypatch.setattr(mod.time, "monotonic", lambda: t)

    class _Now:
        @staticmethod
        def now(_tz=None):
            return SimpleNamespace(strftime=lambda _f: tag)
    monkeypatch.setattr(mod, "datetime", _Now)
    Orchestrator._zeitprotokoll_tick(o)


def test_erster_tick_bucht_nichts(monkeypatch) -> None:
    o = _o()
    _tick(o, monkeypatch, 100.0)
    assert o._zeit_je_zustand == {}


def test_zeit_geht_an_den_aktuellen_zustand(monkeypatch) -> None:
    o = _o(State.IDLE)
    _tick(o, monkeypatch, 100.0)
    _tick(o, monkeypatch, 115.0)
    o.state_machine.state = State.QSO_RESPOND
    _tick(o, monkeypatch, 130.0)
    _tick(o, monkeypatch, 145.0)
    assert o._zeit_je_zustand == {"IDLE": 15.0, "QSO_RESPOND": 30.0, "ARM_REGEL": 45.0}


def test_aussetzer_wird_nicht_verbucht(monkeypatch) -> None:
    """Fuenf Minuten Decoder-Haenger sind keine fuenf Minuten IDLE."""
    o = _o()
    _tick(o, monkeypatch, 100.0)
    _tick(o, monkeypatch, 100.0 + 301.0)
    assert o._zeit_je_zustand == {}
    _tick(o, monkeypatch, 100.0 + 301.0 + 15.0)
    assert o._zeit_je_zustand == {"IDLE": 15.0, "ARM_REGEL": 15.0}


def test_datumswechsel_faengt_bei_null_an(monkeypatch) -> None:
    o = _o()
    _tick(o, monkeypatch, 100.0, tag="2026-09-14")
    _tick(o, monkeypatch, 115.0, tag="2026-09-14")
    o._zeit_gesichert = {"IDLE": 15.0}
    _tick(o, monkeypatch, 130.0, tag="2026-09-15")
    assert o._zeit_je_zustand == {} and o._zeit_gesichert == {}


def test_es_wird_nur_die_differenz_geschrieben(monkeypatch) -> None:
    """Der Stand faengt nach einem Neustart bei null an. Wer den Stand
    schriebe, ueberschriebe die Tageszeile mit einem kleineren Wert."""
    import ft8_appliance.runtime.orchestrator as mod
    geschrieben: list[dict] = []

    async def _fang(_self_tag, delta):
        geschrieben.append(delta)
    o = _o(State.IDLE, db=True)
    o._persist_zeitprotokoll = lambda tag, delta: _fang(tag, delta)
    monkeypatch.setattr(mod.asyncio, "create_task", lambda coro: coro.close())
    _tick(o, monkeypatch, 0.0)
    _tick(o, monkeypatch, 15.0)      # 15 s IDLE, Sicherung (>=60 s seit 0)
    _tick(o, monkeypatch, 30.0)
    _tick(o, monkeypatch, 90.0)      # +75 s, naechste Sicherung
    # Zweite Sicherung darf nur den Zuwachs seit der ersten enthalten.
    assert o._zeit_gesichert == {"IDLE": 90.0, "ARM_REGEL": 90.0}
    assert o._zeit_je_zustand == {"IDLE": 90.0, "ARM_REGEL": 90.0}


@pytest.mark.asyncio
async def test_persistenz_addiert_statt_zu_setzen() -> None:
    eng = init_engine(None)
    async with eng.begin() as c:
        await c.run_sync(m.Base.metadata.create_all)
    o = SimpleNamespace()
    await Orchestrator._persist_zeitprotokoll(o, "2026-09-14", {"IDLE": 40.0, "CQ_CALLING": 5.0})
    await Orchestrator._persist_zeitprotokoll(o, "2026-09-14", {"IDLE": 20.0})
    async with session_scope() as s:
        rows = {r.zustand: r.sekunden for r in (await s.execute(select(m.StateTimeDaily))).scalars()}
    assert rows == {"IDLE": 60.0, "CQ_CALLING": 5.0}


def test_zeit_je_arm_wird_mitgebucht(monkeypatch) -> None:
    o = _o(State.IDLE)
    o.state_machine.ctx.kontroll_arm = True
    _tick(o, monkeypatch, 0.0)
    _tick(o, monkeypatch, 15.0)
    assert o._zeit_je_zustand == {"IDLE": 15.0, "ARM_KONTROLLE": 15.0}
