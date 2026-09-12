"""Regressionstests fuer die Upload-Drain-Loops (Audit 2026-07-30).

Beide Loops liefen bis hierher komplett ohne Test — genau die Ecke, in der
sich stiller Upload-Verlust am laengsten haelt, weil ein nicht
stattgefundener Upload-Versuch keinen Fehler erzeugt und darum auch keinen
Alarm ausloest.

Getestet wird gegen eine echte SQLite-DB (wie test_qso_spill.py); nur die
HTTP-Aufrufe sind gestubbt.
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
    """Sentinel, um die ``while True``-Loops nach einem Sweep zu verlassen."""


@pytest.fixture
def one_sweep(monkeypatch):
    """asyncio.sleep so patchen, dass genau ein Sweep laeuft.

    Der Intervall-Sleep am Schleifenende (>= 60 s) bricht ab, die kurzen
    Throttle-Sleeps innerhalb eines Sweeps laufen sofort durch.
    """
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
            demo_mode=False,
            operators=operators,
            operator=operators[active],
        ),
        _UPLOAD_MAX_ATTEMPTS=Orchestrator._UPLOAD_MAX_ATTEMPTS,
        _upload_reject_is_hard=Orchestrator._upload_reject_is_hard,
        _upload_reject_ist_dupe=Orchestrator._upload_reject_ist_dupe,
        _BULK_DUPE_NACHFAHREN_MAX=Orchestrator._BULK_DUPE_NACHFAHREN_MAX,
        _alert_upload_giveup=lambda *a, **kw: None,
        _operator_for_qso=lambda qso: next(
            (o for o in operators if o.callsign == qso.user_callsign), operators[0]
        ),
    )
    # Beide Loops fangen jede Exception ab und melden sie nur ueber
    # _note_drain_outcome. Ein Tippfehler im Stub saehe im Test sonst aus wie
    # "nichts hochzuladen" — deshalb hier hart durchreichen.
    async def note(service: str, exc: Exception | None) -> None:
        if exc is not None:
            raise AssertionError(f"{service}-Drain warf: {exc!r}") from exc

    stub._note_drain_outcome = note
    stub._clublog_sweep_for_operator = partial(
        Orchestrator._clublog_sweep_for_operator, stub,
    )
    stub._clublog_einzelsweep = partial(Orchestrator._clublog_einzelsweep, stub)
    return stub


async def _seed(qsos: list[dict]) -> None:
    base = datetime(2026, 7, 30, 8, 0, tzinfo=UTC)
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


# ---------------------------------------------------------------------------
# 1) ClubLog-Drain sweepte nur den AKTIVEN Operator: Credentials kamen aus
#    self.config.operator und die Query filterte zusaetzlich auf dessen
#    user_callsign. Die offenen QSOs des inaktiven Operators fielen damit aus
#    der Ergebnismenge — nie hochgeladen, nie ein Versuch, nie ein Alarm.


@pytest.mark.asyncio
async def test_clublog_drain_uploads_qsos_of_inactive_operator(
    tmp_path, monkeypatch, one_sweep,
) -> None:
    init_engine(tmp_path / "qso.sqlite")
    await create_all(default_user_callsign="DO3XR")
    await _seed([
        {"call": "K1ACT", "user_callsign": "DO3XR"},
        {"call": "K1IDLE", "user_callsign": "DK9XR"},
    ])

    sent: list[tuple[str, str]] = []

    async def fake_upload(email, app_pw, api_key, my_call, qso):
        sent.append((my_call, qso.call))

    from ft8_appliance.integrations import clublog
    monkeypatch.setattr(clublog, "upload_qso", fake_upload)

    ops = [_operator("DO3XR", clublog=True), _operator("DK9XR", clublog=True)]
    await one_sweep(Orchestrator._clublog_drain_loop(_stub(ops, active=0)))

    # Der Kern: das QSO des INAKTIVEN Operators ging mit DESSEN Call raus.
    assert ("DK9XR", "K1IDLE") in sent
    assert ("DO3XR", "K1ACT") in sent
    async with session_scope() as s:
        done = set((await s.execute(
            select(Qso.call).where(Qso.clublog_uploaded == True)  # noqa: E712
        )).scalars())
    assert done == {"K1ACT", "K1IDLE"}


@pytest.mark.asyncio
async def test_clublog_drain_uses_per_operator_credentials(
    tmp_path, monkeypatch, one_sweep,
) -> None:
    """Vaters QSOs duerfen nicht mit Sebastians API-Key hochgeladen werden —
    sie landen sonst im falschen ClubLog-Account."""
    init_engine(tmp_path / "qso.sqlite")
    await create_all(default_user_callsign="DO3XR")
    await _seed([{"call": "K1IDLE", "user_callsign": "DK9XR"}])

    seen: list[str] = []

    async def fake_upload(email, app_pw, api_key, my_call, qso):
        seen.append(api_key)

    from ft8_appliance.integrations import clublog
    monkeypatch.setattr(clublog, "upload_qso", fake_upload)

    ops = [_operator("DO3XR", clublog=True), _operator("DK9XR", clublog=True)]
    await one_sweep(Orchestrator._clublog_drain_loop(_stub(ops, active=0)))

    assert seen == ["key-DK9XR"]


@pytest.mark.asyncio
async def test_clublog_drain_skips_operator_without_credentials(
    tmp_path, monkeypatch, one_sweep,
) -> None:
    """Ohne vollstaendige Credentials kein Versuch — und vor allem kein
    Upload mit den Credentials eines anderen Operators."""
    init_engine(tmp_path / "qso.sqlite")
    await create_all(default_user_callsign="DO3XR")
    await _seed([{"call": "K1IDLE", "user_callsign": "DK9XR"}])

    async def fake_upload(email, app_pw, api_key, my_call, qso):
        raise AssertionError("darf nicht aufgerufen werden")

    from ft8_appliance.integrations import clublog
    monkeypatch.setattr(clublog, "upload_qso", fake_upload)

    ops = [_operator("DO3XR", clublog=True), _operator("DK9XR", clublog=False)]
    await one_sweep(Orchestrator._clublog_drain_loop(_stub(ops, active=0)))

    async with session_scope() as s:
        row = (await s.execute(select(Qso).where(Qso.call == "K1IDLE"))).scalar_one()
        assert row.clublog_uploaded is False
        assert row.clublog_upload_attempts == 0


# ---------------------------------------------------------------------------
# 2) QRZ-Drain: Head-of-Line-Blocking. Die Query war auf .limit(20) gedeckelt,
#    gefiltert wurde erst danach. QSOs ohne passenden Logbook-Key werden nur
#    uebersprungen (ohne Attempt-Zaehler), blieben also dauerhaft in den
#    ersten 20 Zeilen und blockierten alle nachfolgenden QSOs permanent.


@pytest.mark.asyncio
async def test_qrz_drain_not_blocked_by_keyless_qsos(
    tmp_path, monkeypatch, one_sweep,
) -> None:
    init_engine(tmp_path / "qso.sqlite")
    await create_all(default_user_callsign="DO3XR")
    # 22 aeltere QSOs ohne Logbook-Key (mehr als das alte .limit(20)) ...
    seed = [{"call": f"KEYLESS{i}", "user_callsign": "DK9XR"} for i in range(22)]
    # ... und danach drei mit Key.
    seed += [{"call": f"KEYED{i}", "user_callsign": "DO3XR"} for i in range(3)]
    await _seed(seed)

    uploaded: list[str] = []

    async def fake_upload(key, my_call, qso):
        uploaded.append(qso.call)
        return SimpleNamespace(logbook_id="4711")

    from ft8_appliance.integrations import qrz_logbook
    monkeypatch.setattr(qrz_logbook, "upload_qso", fake_upload)

    ops = [_operator("DO3XR", qrz_key="KEY-DO3XR"), _operator("DK9XR", qrz_key=None)]
    await one_sweep(Orchestrator._qrz_logbook_drain_loop(_stub(ops, active=0)))

    assert sorted(uploaded) == ["KEYED0", "KEYED1", "KEYED2"]
    async with session_scope() as s:
        rows = list((await s.execute(select(Qso))).scalars())
    by_call = {r.call: r for r in rows}
    assert all(by_call[f"KEYED{i}"].qrz_uploaded for i in range(3))
    # Keyless bleibt pending UND unversucht — sonst laeuft der Backoff hoch
    # und der Attempt-Ceiling gibt ein QSO auf, das nie gesendet wurde.
    assert by_call["KEYLESS0"].qrz_uploaded is False
    assert by_call["KEYLESS0"].qrz_upload_attempts == 0


@pytest.mark.asyncio
async def test_qrz_drain_caps_attempts_per_sweep(
    tmp_path, monkeypatch, one_sweep,
) -> None:
    """Der Deckel von 20 Versuchen pro Sweep muss erhalten bleiben — er
    haelt uns bei einem grossen Rueckstau von einem QRZ-Request-Sturm ab."""
    init_engine(tmp_path / "qso.sqlite")
    await create_all(default_user_callsign="DO3XR")
    await _seed([{"call": f"W{i}", "user_callsign": "DO3XR"} for i in range(25)])

    uploaded: list[str] = []

    async def fake_upload(key, my_call, qso):
        uploaded.append(qso.call)
        return SimpleNamespace(logbook_id="4711")

    from ft8_appliance.integrations import qrz_logbook
    monkeypatch.setattr(qrz_logbook, "upload_qso", fake_upload)

    ops = [_operator("DO3XR", qrz_key="KEY-DO3XR")]
    await one_sweep(Orchestrator._qrz_logbook_drain_loop(_stub(ops)))

    assert len(uploaded) == 20
