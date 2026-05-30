"""Thin repository helpers around the ORM models.

Kept intentionally small — most queries throughout the app go directly
through ``session.execute(select(...))``. This module only collects the
handful of helpers that show up in more than one place.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, datetime

from sqlalchemy import delete, desc, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from .models import Decode, Heard, PickAttempt, PskReporterIn, Qso, SwrLog


# DATA-M1 (Audit 2026-05-30): Telemetrie-Tabellen wachsen sonst unbegrenzt
# → SD voll → DB-Korruption → QSO-Verlust. (Tabelle, Zeit-Spalte). Die
# qso-Tabelle ist BEWUSST NICHT dabei — Logdaten werden NIE geprunt.
_TELEMETRY_TABLES = (
    (Decode, Decode.ts),
    (PickAttempt, PickAttempt.ts),
    (Heard, Heard.last_seen),
    (SwrLog, SwrLog.ts),
    (PskReporterIn, PskReporterIn.ts),
)


async def prune_telemetry(session: AsyncSession, older_than: datetime) -> dict[str, int]:
    """Loesche Telemetrie-Zeilen aelter als *older_than*. Niemals qso.
    Returns {tabelle: geloeschte_zeilen}."""
    counts: dict[str, int] = {}
    for model, tscol in _TELEMETRY_TABLES:
        res = await session.execute(delete(model).where(tscol < older_than))
        counts[model.__tablename__] = res.rowcount or 0
    return counts


async def insert_decode(session: AsyncSession, **fields: object) -> Decode:
    """Insert one decoded message row."""
    row = Decode(**fields)
    session.add(row)
    await session.flush()
    return row


async def insert_pick_attempt(session: AsyncSession, **fields: object) -> PickAttempt:
    """Insert one hunt-pick-attempt telemetry row (psk_heard_us A/B)."""
    row = PickAttempt(**fields)
    session.add(row)
    await session.flush()
    return row


async def insert_qso(session: AsyncSession, **fields: object) -> Qso:
    row = Qso(**fields)
    session.add(row)
    await session.flush()
    return row


async def latest_decodes(session: AsyncSession, limit: int = 50) -> list[Decode]:
    stmt = select(Decode).order_by(desc(Decode.ts)).limit(limit)
    return list((await session.execute(stmt)).scalars())


async def latest_qsos(session: AsyncSession, limit: int = 50) -> list[Qso]:
    stmt = select(Qso).order_by(desc(Qso.qso_start)).limit(limit)
    return list((await session.execute(stmt)).scalars())


async def upsert_heard(
    session: AsyncSession,
    call: str,
    grid: str | None,
    snr_db: int | None,
    now: datetime | None = None,
    user_callsign: str | None = None,
) -> None:
    """Bump the heard table for *call*, tracking last_seen and best SNR.

    Multi-Operator (Sebastian 2026-05-23): *user_callsign* trennt die
    Heard-Geschichten der einzelnen Operatoren. Die DB-PK bleibt
    single-column ``call`` (SQLite-Migration-Constraint), aber bei
    Upsert filtern wir auf user_callsign — wenn beide Operatoren
    den gleichen Call gehoert haben, kann das technisch nur ein Row
    sein, der "letzten Hoerer" gewinnt. Pragmatisch akzeptabel — die
    Filterung beim READ tut den Rest.
    """
    now = now or datetime.now(UTC)
    existing = await session.get(Heard, call)
    if existing is None:
        session.add(
            Heard(
                call=call,
                last_seen=now,
                count=1,
                grid=grid,
                best_snr=snr_db,
                user_callsign=user_callsign,
            )
        )
        return
    best = existing.best_snr if existing.best_snr is not None else -999
    new_best = snr_db if snr_db is not None and snr_db > best else existing.best_snr
    await session.execute(
        update(Heard)
        .where(Heard.call == call)
        .values(
            last_seen=now,
            count=existing.count + 1,
            grid=grid or existing.grid,
            best_snr=new_best,
            user_callsign=user_callsign or existing.user_callsign,
        )
    )


async def heard_in_last(
    session: AsyncSession, minutes: int, limit: int = 500,
    user_callsign: str | None = None,
) -> Iterable[Heard]:
    """Heard-Stationen der letzten *minutes* Minuten, optional gefiltert
    auf *user_callsign* fuer Multi-Operator-Trennung."""
    cutoff = datetime.now(UTC).timestamp() - minutes * 60
    cutoff_dt = datetime.fromtimestamp(cutoff, tz=UTC)
    stmt = (
        select(Heard)
        .where(Heard.last_seen >= cutoff_dt)
        .order_by(desc(Heard.last_seen))
        .limit(limit)
    )
    if user_callsign:
        stmt = stmt.where(Heard.user_callsign == user_callsign)
    return list((await session.execute(stmt)).scalars())
