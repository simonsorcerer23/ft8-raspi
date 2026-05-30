"""DATA-C1 (Audit 2026-05-30): ein abgeschlossenes QSO darf bei DB-Fehler
nicht still verloren gehen — es wird in eine Spill-Datei gesichert und beim
naechsten erfolgreichen Write / beim Start nachgetragen."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from sqlalchemy import func, select

from ft8_appliance.db import session_scope
from ft8_appliance.db.models import Qso
from ft8_appliance.db.session import create_all, get_db_path, init_engine
from ft8_appliance.runtime.orchestrator import Orchestrator


def _qso_kwargs(call: str = "W1AW") -> dict:
    now = datetime(2026, 5, 30, 12, 0, tzinfo=UTC)
    return dict(
        call=call, band="20m", freq_hz=14_074_000, mode="FT8",
        rst_sent=-12, rst_rcvd=-7, grid_rcvd="FN31",
        qso_start=now, qso_end=now, my_grid="JN58td",
        my_power_w=10, swr_avg=1.2, my_lat=48.1, my_lon=11.5,
        user_callsign="DO3XR", station_callsign=None, mf_mfnr=None,
    )


def _stub() -> SimpleNamespace:
    # _spill_qso/_drain_spilled_qsos nutzen nur self.db_enabled +
    # self.integrations.ntfy + die @staticmethods → Minimal-Stub genuegt
    # (staticmethods explizit anhaengen, da SimpleNamespace nicht erbt).
    return SimpleNamespace(
        db_enabled=True,
        integrations=SimpleNamespace(ntfy=None),
        _spill_path=Orchestrator._spill_path,
        _qso_to_jsonable=Orchestrator._qso_to_jsonable,
        _qso_from_jsonable=Orchestrator._qso_from_jsonable,
    )


def test_qso_jsonable_roundtrip() -> None:
    kw = _qso_kwargs()
    rt = Orchestrator._qso_from_jsonable(Orchestrator._qso_to_jsonable(kw))
    assert rt["qso_start"] == kw["qso_start"]
    assert rt["call"] == "W1AW"
    assert rt["freq_hz"] == 14_074_000


@pytest.mark.asyncio
async def test_spill_then_drain_persists_qso(tmp_path) -> None:
    init_engine(tmp_path / "qso.sqlite")
    await create_all(default_user_callsign="DO3XR")
    stub = _stub()
    spill = get_db_path().parent / "unlogged_qsos.jsonl"

    # 1. Spill zwei QSOs (DB-Write war "gescheitert")
    await Orchestrator._spill_qso(stub, _qso_kwargs("W1AW"))
    await Orchestrator._spill_qso(stub, _qso_kwargs("K1ABC"))
    assert spill.exists()
    assert len(spill.read_text().splitlines()) == 2

    # 2. Drain → landen in der DB, Datei verschwindet
    await Orchestrator._drain_spilled_qsos(stub)
    async with session_scope() as s:
        n = (await s.execute(select(func.count()).select_from(Qso))).scalar()
        calls = set((await s.execute(select(Qso.call))).scalars().all())
    assert n == 2
    assert calls == {"W1AW", "K1ABC"}
    assert not spill.exists()


@pytest.mark.asyncio
async def test_drain_noop_without_spill_file(tmp_path) -> None:
    init_engine(tmp_path / "qso.sqlite")
    await create_all(default_user_callsign="DO3XR")
    # darf nicht crashen wenn keine Spill-Datei da ist
    await Orchestrator._drain_spilled_qsos(_stub())
