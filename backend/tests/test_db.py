"""Tests for the DB layer using an in-memory SQLite."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from ft8_appliance.db import (
    create_all,
    init_engine,
    repository,
    session_scope,
)
from ft8_appliance.db.models import Decode, Qso


@pytest.fixture(autouse=True)
async def fresh_db():
    init_engine(":memory:")
    await create_all()
    yield


async def test_insert_and_query_decodes() -> None:
    async with session_scope() as s:
        await repository.insert_decode(
            s,
            ts=datetime.now(UTC),
            call_from="W1AW",
            call_to="DK9XR",
            grid="FN31",
            message="W1AW DK9XR FN31",
            snr_db=-12,
            dt_s=0.4,
            freq_offset_hz=1850,
            band="20m",
        )
    async with session_scope() as s:
        rows = await repository.latest_decodes(s, limit=10)
    assert len(rows) == 1
    assert rows[0].call_from == "W1AW"


async def test_insert_qso_and_roundtrip() -> None:
    start = datetime.now(UTC)
    end = start + timedelta(seconds=90)
    async with session_scope() as s:
        await repository.insert_qso(
            s,
            call="W1AW",
            band="20m",
            freq_hz=14_074_000,
            mode="FT8",
            rst_sent=-10,
            rst_rcvd=-12,
            grid_rcvd="FN31",
            qso_start=start,
            qso_end=end,
            my_grid="JN58td",
            my_power_w=10,
        )
    async with session_scope() as s:
        rows = await repository.latest_qsos(s, limit=10)
    assert len(rows) == 1
    assert rows[0].call == "W1AW"
    assert rows[0].rst_rcvd == -12


async def test_upsert_heard_tracks_best_snr() -> None:
    async with session_scope() as s:
        await repository.upsert_heard(s, "W1AW", "FN31", -15)
        await repository.upsert_heard(s, "W1AW", "FN31", -8)  # better
        await repository.upsert_heard(s, "W1AW", "FN31", -20)  # worse
    async with session_scope() as s:
        from sqlalchemy import select

        from ft8_appliance.db.models import Heard

        row = (await s.execute(select(Heard).where(Heard.call == "W1AW"))).scalar_one()
    assert row.count == 3
    assert row.best_snr == -8


async def test_heard_in_last_window() -> None:
    now = datetime.now(UTC)
    async with session_scope() as s:
        await repository.upsert_heard(s, "A1A", "AA00", -10, now=now)
        await repository.upsert_heard(
            s, "B2B", "BB00", -10, now=now - timedelta(hours=3)
        )
    async with session_scope() as s:
        recent = list(await repository.heard_in_last(s, minutes=60))
    calls = {h.call for h in recent}
    assert calls == {"A1A"}
