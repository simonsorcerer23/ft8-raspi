"""Der Erfolgs-Cooldown darf einen Neustart nicht vergessen.

``worked_until`` lebte nur im Arbeitsspeicher. Neustarts sind aber Alltag —
das Self-Update prueft alle zehn Minuten und startet den Dienst bei jeder
neuen Version neu. Danach war jede gerade gearbeitete Station sofort wieder
freigegeben.

Live am 2026-09-11: RC6OD um 06:57 gearbeitet, um 07:22:56 Neustart durch
das Update auf v0.91.0, um 07:26:05 erneut angerufen und ein zweites Mal
geloggt — 29 Minuten nach dem ersten QSO, bei 30 Minuten Cooldown.

``_restore_qso_cooldowns`` holt den Zustand beim Start aus dem Logbuch
zurueck.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import pytest

from ft8_appliance.runtime import orchestrator as orch_mod


@dataclass
class _QsoZeile:
    call: str
    band: str
    qso_start: datetime


class _Sitzung:
    pass


def _orchestrator_attrappe(monkeypatch, qsos, cd_min=30):
    """Nur so viel Orchestrator, wie die Methode anfasst."""
    class _Ctx:
        worked_until: dict = {}

    class _Sm:
        ctx = _Ctx()

    class _Op:
        qso_cooldown_min = cd_min

    class _Cfg:
        operating = _Op()

    class _Orch:
        config = _Cfg()
        state_machine = _Sm()
        _restore_qso_cooldowns = orch_mod.Orchestrator._restore_qso_cooldowns

    class _Scope:
        async def __aenter__(self): return _Sitzung()
        async def __aexit__(self, *a): return False

    monkeypatch.setattr(orch_mod, "session_scope", lambda: _Scope())

    async def _latest(session, limit=200):
        return list(qsos)

    monkeypatch.setattr(orch_mod.repository, "latest_qsos", _latest)
    o = _Orch()
    o.state_machine.ctx.worked_until = {}
    return o


@pytest.mark.asyncio
async def test_frisches_qso_sperrt_wieder(monkeypatch):
    """Der Fall RC6OD: QSO vor 5 Minuten, Cooldown 30 → weiter gesperrt."""
    o = _orchestrator_attrappe(monkeypatch, [
        _QsoZeile("RC6OD", "20m", datetime.now(UTC) - timedelta(minutes=5)),
    ])

    await o._restore_qso_cooldowns()

    assert ("RC6OD", "20m") in o.state_machine.ctx.worked_until


@pytest.mark.asyncio
async def test_abgelaufenes_qso_sperrt_nicht(monkeypatch):
    o = _orchestrator_attrappe(monkeypatch, [
        _QsoZeile("DL1ALT", "20m", datetime.now(UTC) - timedelta(hours=6)),
    ])

    await o._restore_qso_cooldowns()

    assert o.state_machine.ctx.worked_until == {}


@pytest.mark.asyncio
async def test_sperre_gilt_nur_fuer_das_gearbeitete_band(monkeypatch):
    o = _orchestrator_attrappe(monkeypatch, [
        _QsoZeile("EA5QS", "20m", datetime.now(UTC) - timedelta(minutes=3)),
    ])

    await o._restore_qso_cooldowns()

    assert ("EA5QS", "20m") in o.state_machine.ctx.worked_until
    assert ("EA5QS", "40m") not in o.state_machine.ctx.worked_until


@pytest.mark.asyncio
async def test_abgeschalteter_cooldown_bleibt_abgeschaltet(monkeypatch):
    o = _orchestrator_attrappe(monkeypatch, [
        _QsoZeile("RC6OD", "20m", datetime.now(UTC) - timedelta(minutes=1)),
    ], cd_min=0)

    await o._restore_qso_cooldowns()

    assert o.state_machine.ctx.worked_until == {}


@pytest.mark.asyncio
async def test_defekte_datenbank_bricht_den_start_nicht(monkeypatch):
    o = _orchestrator_attrappe(monkeypatch, [])

    async def _kaputt(session, limit=200):
        raise RuntimeError("DB weg")

    monkeypatch.setattr(orch_mod.repository, "latest_qsos", _kaputt)

    await o._restore_qso_cooldowns()          # darf nicht werfen

    assert o.state_machine.ctx.worked_until == {}
