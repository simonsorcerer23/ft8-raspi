"""Welle 4 des Fix-Plans zum Tiefenaudit 2026-09-06: Upload-Pfad.

* K4  _operator_for_call fiel auf den AKTIVEN Operator zurueck — QSOs
      eines geloeschten Profils waeren unter fremdem Konto hochgeladen worden.
* H2  QRZ-Sync-Loop las den Key einmal vor der Schleife, retried
      dauerhafte Auth-Fehler endlos und haette nach einem Operator-Wechsel
      die Worked-Sets des neuen mit dem Logbuch des alten gefuellt.
* H3  ClubLog-Bulk am Attempt-Ceiling: ein Push pro QSO.
* M2  delete_operator(force) verwaist QSO-Zeilen still.

Gegen echte SQLite wie test_drain_loops.py; nur HTTP ist gestubbt.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from functools import partial
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from ft8_appliance.db import session_scope
from ft8_appliance.db.models import Qso
from ft8_appliance.db.session import create_all, init_engine
from ft8_appliance.runtime.orchestrator import Orchestrator


class _StopLoop(Exception):
    pass


@pytest.fixture
def one_sweep(monkeypatch):
    real_sleep = asyncio.sleep

    async def fake_sleep(delay: float, *a, **kw):
        if delay >= 60:
            raise _StopLoop
        return await real_sleep(0)

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)

    async def run(coro) -> None:
        with pytest.raises(_StopLoop):
            await coro

    return run


def _operator(callsign: str, *, qrz_key: str | None = None, clublog: bool = False):
    return SimpleNamespace(
        callsign=callsign,
        qrz_logbook_api_key=qrz_key,
        qrz_key_for=lambda _station, _k=qrz_key: _k,
        clublog_email=f"{callsign.lower()}@example.com" if clublog else None,
        clublog_app_password="pw" if clublog else None,
        clublog_api_key=f"key-{callsign}" if clublog else None,
    )


def _stub(operators: list, active: int = 0) -> SimpleNamespace:
    stub = SimpleNamespace(
        config=SimpleNamespace(
            demo_mode=False, operators=operators, operator=operators[active],
        ),
        _UPLOAD_MAX_ATTEMPTS=Orchestrator._UPLOAD_MAX_ATTEMPTS,
        _upload_reject_is_hard=Orchestrator._upload_reject_is_hard,
        _alert_upload_giveup=lambda *a, **kw: None,
        _alert_upload_giveup_many=lambda *a, **kw: None,
        _orphan_upload_warned=set(),
        # Der QRZ-Abgleich haelt seit 2026-09-12 einen Tagesabstand ein,
        # dessen Zeitpunkt den Neustart ueberlebt. Ohne gesicherten Wert
        # (0.0) laeuft er sofort — genau wie bei einer frischen Anlage.
        _qrz_sync_abstand_s=Orchestrator._qrz_sync_abstand_s,
        _qrz_sync_at=0.0,
        _maybe_persist_runtime_state=lambda *a, **kw: None,
    )
    # Echte Operator-Zuordnung (K4), nicht der alte Test-Fallback auf ops[0].
    stub._operator_for_call = partial(Orchestrator._operator_for_call, stub)
    stub._operator_for_qso = partial(Orchestrator._operator_for_qso, stub)

    async def note(service: str, exc: Exception | None) -> None:
        if exc is not None:
            raise AssertionError(f"{service}-Drain warf: {exc!r}") from exc

    stub._note_drain_outcome = note
    stub._clublog_sweep_for_operator = partial(
        Orchestrator._clublog_sweep_for_operator, stub,
    )
    return stub


async def _seed(qsos: list[dict]) -> None:
    base = datetime(2026, 9, 6, 8, 0, tzinfo=UTC)
    async with session_scope() as s:
        for i, extra in enumerate(qsos):
            start = base + timedelta(minutes=i)
            s.add(Qso(
                call=extra.pop("call", f"W1AW{i}"),
                band="20m", freq_hz=14_074_000, mode="FT8",
                rst_sent=-12, rst_rcvd=-7,
                qso_start=start, qso_end=start, my_grid="JN58ch",
                **extra,
            ))


# ================================================================== K4


def test_operator_lookup_has_no_active_fallback() -> None:
    stub = _stub([_operator("DO3XR"), _operator("DK9XR")], active=0)
    assert stub._operator_for_call("DK9XR").callsign == "DK9XR"
    assert stub._operator_for_call("DK9XR/P").callsign == "DK9XR"  # per Person
    assert stub._operator_for_call("DL1ORPHAN") is None
    assert stub._operator_for_call("") is None


@pytest.mark.asyncio
async def test_qrz_drain_leaves_orphaned_qsos_alone(tmp_path, monkeypatch, one_sweep) -> None:
    """Das QSO eines geloeschten Profils darf nicht unter dem aktiven Konto
    hochgeladen werden — es bleibt liegen, unversucht."""
    init_engine(tmp_path / "qso.sqlite")
    await create_all(default_user_callsign="DO3XR")
    await _seed([
        {"call": "K1MINE", "user_callsign": "DO3XR"},
        {"call": "K1ORPHAN", "user_callsign": "DL1GONE"},
    ])
    uploaded: list[tuple[str, str]] = []

    async def fake_upload(key, my_call, qso):
        uploaded.append((my_call, qso.call))
        return SimpleNamespace(logbook_id="1")

    from ft8_appliance.integrations import qrz_logbook
    monkeypatch.setattr(qrz_logbook, "upload_qso", fake_upload)

    ops = [_operator("DO3XR", qrz_key="KEY-DO3XR")]
    await one_sweep(Orchestrator._qrz_logbook_drain_loop(_stub(ops)))

    assert uploaded == [("DO3XR", "K1MINE")]
    async with session_scope() as s:
        orphan = (await s.execute(select(Qso).where(Qso.call == "K1ORPHAN"))).scalar_one()
    assert orphan.qrz_uploaded is False
    assert orphan.qrz_upload_attempts == 0


# ================================================================== H3


@pytest.mark.asyncio
async def test_clublog_bulk_ceiling_sends_one_aggregated_push(tmp_path, monkeypatch) -> None:
    init_engine(tmp_path / "qso.sqlite")
    await create_all(default_user_callsign="DK9XR")
    max_att = Orchestrator._UPLOAD_MAX_ATTEMPTS
    await _seed([
        {"call": f"K{i}CEIL", "user_callsign": "DK9XR",
         "clublog_upload_attempts": max_att - 1}
        for i in range(6)
    ])

    from ft8_appliance.integrations import clublog

    async def failing_bulk(*a, **kw):
        raise clublog.ClubLogError("could not reach login server")  # nicht hart

    monkeypatch.setattr(clublog, "bulk_upload", failing_bulk)
    stub = _stub([_operator("DK9XR", clublog=True)])
    single: list[str] = []
    many: list[list[str]] = []
    stub._alert_upload_giveup = lambda service, call: single.append(call)
    stub._alert_upload_giveup_many = lambda service, calls: many.append(list(calls))

    await stub._clublog_sweep_for_operator(
        "dk9xr@example.com", "pw", "key-DK9XR", "DK9XR", bulk_threshold=5,
    )

    assert single == []
    assert len(many) == 1 and sorted(many[0]) == sorted(f"K{i}CEIL" for i in range(6))
    async with session_scope() as s:
        done = list((await s.execute(
            select(Qso.call).where(Qso.clublog_uploaded == True)  # noqa: E712
        )).scalars())
    assert len(done) == 6  # aufgegeben = markiert, wie vorher


class _Ntfy:
    enabled = True

    def __init__(self) -> None:
        self.sent: list[dict] = []

    async def notify(self, msg: str, **kw) -> None:
        self.sent.append({"msg": msg, **kw})


@pytest.mark.asyncio
async def test_giveup_many_is_one_push_with_the_list() -> None:
    ntfy = _Ntfy()
    stub = SimpleNamespace(
        integrations=SimpleNamespace(ntfy=ntfy),
        _UPLOAD_MAX_ATTEMPTS=Orchestrator._UPLOAD_MAX_ATTEMPTS,
    )
    stub._alert_upload_giveup = partial(Orchestrator._alert_upload_giveup, stub)
    Orchestrator._alert_upload_giveup_many(stub, "ClubLog", ["A1AA", "B2BB", "C3CC"])
    for _ in range(3):
        await asyncio.sleep(0)
    assert len(ntfy.sent) == 1
    assert "3" in ntfy.sent[0]["msg"] and "A1AA" in ntfy.sent[0]["msg"]


@pytest.mark.asyncio
async def test_giveup_many_with_one_call_uses_the_single_push() -> None:
    ntfy = _Ntfy()
    stub = SimpleNamespace(
        integrations=SimpleNamespace(ntfy=ntfy),
        _UPLOAD_MAX_ATTEMPTS=Orchestrator._UPLOAD_MAX_ATTEMPTS,
    )
    stub._alert_upload_giveup = partial(Orchestrator._alert_upload_giveup, stub)
    Orchestrator._alert_upload_giveup_many(stub, "QRZ", ["A1AA"])
    for _ in range(3):
        await asyncio.sleep(0)
    from ft8_appliance import i18n as _i18n

    assert len(ntfy.sent) == 1
    # Der EINZEL-Text ("fuer QSO A1AA"), nicht die Listenform ("fuer 1 QSOs").
    assert ntfy.sent[0]["msg"] == _i18n.translate(
        "push.upload_giveup_msg", None, service="QRZ", call="A1AA",
        attempts=Orchestrator._UPLOAD_MAX_ATTEMPTS,
    )


# ================================================================== H2


def _sync_stub(key: str | None) -> SimpleNamespace:
    stub = SimpleNamespace(
        config=SimpleNamespace(integrations=SimpleNamespace(
            qrz=SimpleNamespace(logbook_api_key=key),
        )),
        integrations=SimpleNamespace(cty=None),
        _worked_calls=set(), _worked_dxccs=set(), _worked_dxcc_band=set(),
        _worked_grids=set(), _worked_grid_band=set(),
        notes=[],
        # Seit 2026-09-12 haelt der Abgleich einen Tagesabstand ein, dessen
        # Zeitpunkt den Neustart ueberlebt. 0.0 = noch nie gelaufen, also
        # sofort holen — wie bei einer frischen Anlage.
        _qrz_sync_abstand_s=Orchestrator._qrz_sync_abstand_s,
        _qrz_sync_at=0.0,
        _maybe_persist_runtime_state=lambda *a, **kw: None,
    )

    async def note(service, exc):
        stub.notes.append((service, exc))

    stub._note_drain_outcome = note
    return stub


def _sleep_recorder(monkeypatch, stop_after: int):
    """asyncio.sleep sofort durchlassen, Dauern mitschreiben, nach N stoppen."""
    real_sleep = asyncio.sleep
    seen: list[float] = []

    async def fake_sleep(delay: float, *a, **kw):
        seen.append(delay)
        if len(seen) >= stop_after:
            raise _StopLoop
        return await real_sleep(0)

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)
    return seen


@pytest.mark.asyncio
async def test_sync_loop_reads_the_key_every_cycle(monkeypatch) -> None:
    from ft8_appliance.integrations import qrz_logbook

    stub = _sync_stub("KEY-A")
    keys: list[str] = []

    async def fake_fetch(api_key, timeout=0.0):
        keys.append(api_key)
        stub.config.integrations.qrz.logbook_api_key = "KEY-B"  # Operator-Wechsel danach
        return []

    monkeypatch.setattr(qrz_logbook, "fetch_log_adif", fake_fetch)
    seen = _sleep_recorder(monkeypatch, stop_after=2)  # 60, 86400 <stop>
    with pytest.raises(_StopLoop):
        await Orchestrator._qrz_logbook_sync_loop(stub)
    # Zyklus 1 mit A; der Key-Wechsel waehrend des Fetches verwirft das
    # Ergebnis, Zyklus 2 laeuft sofort mit B, dann erst der 24-h-Schlaf.
    assert keys == ["KEY-A", "KEY-B"]
    assert seen == [60.0, 86400.0]


@pytest.mark.asyncio
async def test_sync_loop_discards_results_when_key_changed_mid_fetch(monkeypatch) -> None:
    from ft8_appliance.integrations import qrz_logbook

    stub = _sync_stub("KEY-A")

    async def fake_fetch(api_key, timeout=0.0):
        if api_key == "KEY-A":
            stub.config.integrations.qrz.logbook_api_key = "KEY-B"
            return [{"call": "K1OLDOP", "band": "20m", "gridsquare": "FN31"}]
        return [{"call": "K1NEWOP", "band": "20m", "gridsquare": "FN31"}]

    monkeypatch.setattr(qrz_logbook, "fetch_log_adif", fake_fetch)
    _sleep_recorder(monkeypatch, stop_after=3)
    with pytest.raises(_StopLoop):
        await Orchestrator._qrz_logbook_sync_loop(stub)
    assert "K1OLDOP" not in stub._worked_calls  # Logbuch des alten Operators
    assert "K1NEWOP" in stub._worked_calls


@pytest.mark.asyncio
async def test_sync_loop_backs_off_after_repeated_rejects(monkeypatch) -> None:
    from ft8_appliance.integrations import qrz_logbook

    stub = _sync_stub("KEY-BAD")

    async def rejecting(api_key, timeout=0.0):
        raise qrz_logbook.QrzLogbookError("invalid api key")

    monkeypatch.setattr(qrz_logbook, "fetch_log_adif", rejecting)
    seen = _sleep_recorder(monkeypatch, stop_after=5)  # 60, 300, 300, 3600, <stop>
    with pytest.raises(_StopLoop):
        await Orchestrator._qrz_logbook_sync_loop(stub)
    assert seen[:4] == [60.0, 300.0, 300.0, 3600.0]
    assert len(stub.notes) == 1 and stub.notes[0][0] == "QRZ-Sync"


@pytest.mark.asyncio
async def test_sync_loop_without_key_waits_instead_of_exiting(monkeypatch) -> None:
    """Vorher: `return` — ein spaeter eingetragener Key haette den Loop nie
    mehr erreicht."""
    from ft8_appliance.integrations import qrz_logbook

    stub = _sync_stub(None)
    called: list[str] = []

    async def fake_fetch(api_key, timeout=0.0):
        called.append(api_key)
        return []

    monkeypatch.setattr(qrz_logbook, "fetch_log_adif", fake_fetch)
    seen = _sleep_recorder(monkeypatch, stop_after=3)  # 60, 300, <stop>
    with pytest.raises(_StopLoop):
        await Orchestrator._qrz_logbook_sync_loop(stub)
    assert called == []
    assert seen[1] == 300.0


# ================================================================== M2


@pytest.mark.asyncio
async def test_delete_operator_reports_orphaned_qsos(tmp_path) -> None:
    from unittest.mock import AsyncMock

    from fastapi import HTTPException

    from ft8_appliance.web.routes.operators import delete_operator

    init_engine(tmp_path / "qso.sqlite")
    await create_all(default_user_callsign="DO3XR")
    await _seed([{"call": "K1A", "user_callsign": "DK9XR"},
                 {"call": "K1B", "user_callsign": "DK9XR"}])
    orch = SimpleNamespace(
        config=SimpleNamespace(
            active_callsign="DO3XR",
            operators=[SimpleNamespace(callsign="DO3XR"), SimpleNamespace(callsign="DK9XR")],
        ),
        persist_config=AsyncMock(),
    )
    with pytest.raises(HTTPException) as exc:
        await delete_operator("DK9XR", force=False, orch=orch)
    assert exc.value.status_code == 409 and "2 QSOs" in exc.value.detail

    resp = await delete_operator("DK9XR", force=True, orch=orch)
    assert resp.ok and resp.orphaned_qsos == 2
    assert [op.callsign for op in orch.config.operators] == ["DO3XR"]
    async with session_scope() as s:
        left = list((await s.execute(select(Qso.call))).scalars())
    assert sorted(left) == ["K1A", "K1B"]  # Zeilen bleiben erhalten
