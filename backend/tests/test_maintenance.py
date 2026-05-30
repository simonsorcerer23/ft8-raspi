"""DATA-M1 (Retention) + DATA-C3 (Backup), Audit 2026-05-30."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select

from ft8_appliance.db import repository, session_scope
from ft8_appliance.db.models import Decode, Heard, Qso
from ft8_appliance.db.session import backup_database, create_all, init_engine


@pytest.mark.asyncio
async def test_prune_telemetry_keeps_qso_and_recent(tmp_path) -> None:
    init_engine(tmp_path / "qso.sqlite")
    await create_all(default_user_callsign="DO3XR")
    now = datetime.now(UTC)
    old = now - timedelta(days=200)
    async with session_scope() as s:
        s.add(Decode(ts=old, message="CQ OLD"))
        s.add(Decode(ts=now, message="CQ NEW"))
        s.add(Heard(last_seen=old, call="OLD"))
        s.add(Heard(last_seen=now, call="NEW"))
        # ein QSO mit altem Zeitstempel — darf NIE geprunt werden
        s.add(Qso(call="W1AW", band="20m", freq_hz=14_074_000,
                  qso_start=old, qso_end=old, my_grid="JN58"))

    async with session_scope() as s:
        counts = await repository.prune_telemetry(s, now - timedelta(days=90))

    assert counts["decode"] == 1
    assert counts["heard"] == 1
    assert "qso" not in counts  # qso wird gar nicht angefasst
    async with session_scope() as s:
        decodes = (await s.execute(select(Decode.message))).scalars().all()
        qsos = (await s.execute(select(func.count()).select_from(Qso))).scalar()
    assert decodes == ["CQ NEW"]
    assert qsos == 1, "QSO darf NICHT geprunt werden"


@pytest.mark.asyncio
async def test_backup_database_creates_and_rotates(tmp_path) -> None:
    init_engine(tmp_path / "qso.sqlite")
    await create_all(default_user_callsign="DO3XR")
    dest = None
    for _ in range(3):
        dest = await backup_database(keep=2)
    assert dest is not None and dest.exists()
    backups = sorted((tmp_path / "backups").glob("qso-*.sqlite"))
    assert len(backups) <= 2, "Rotation auf keep=2"
