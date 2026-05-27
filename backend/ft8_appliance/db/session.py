"""Async SQLAlchemy session wiring.

A single global engine + sessionmaker. Created lazily so tests can spin
up their own in-memory DB without touching the real one.
"""

from __future__ import annotations

import contextlib
from collections.abc import AsyncIterator
from pathlib import Path

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from .models import Base

_engine: AsyncEngine | None = None
_sessionmaker: async_sessionmaker[AsyncSession] | None = None


def init_engine(db_path: Path | str | None = None) -> AsyncEngine:
    """Create (or replace) the global engine.

    *db_path* of ``None`` or ``":memory:"`` creates an in-memory DB —
    used by tests. A real Path uses ``aiosqlite`` against that file.
    """
    global _engine, _sessionmaker
    if db_path is None or str(db_path) == ":memory:":
        url = "sqlite+aiosqlite:///:memory:"
    else:
        url = f"sqlite+aiosqlite:///{Path(db_path).absolute()}"
    _engine = create_async_engine(url, future=True)
    _sessionmaker = async_sessionmaker(_engine, expire_on_commit=False)
    return _engine


def get_engine() -> AsyncEngine:
    if _engine is None:
        raise RuntimeError("db engine not initialised — call init_engine() first")
    return _engine


def get_sessionmaker() -> async_sessionmaker[AsyncSession]:
    if _sessionmaker is None:
        raise RuntimeError("db sessionmaker not initialised — call init_engine() first")
    return _sessionmaker


async def create_all(default_user_callsign: str | None = None) -> None:
    """Create all tables, then run lightweight in-place column migrations.

    No Alembic — the appliance ships with a single-file SQLite that lives
    its whole life on one Pi, and the migrations we need so far are pure
    additive (`ALTER TABLE ADD COLUMN`). We do them here so an existing
    DB picked up after a deploy gains the new fields without manual
    intervention.

    *default_user_callsign* wird beim Multi-Operator-Backfill verwendet
    (Sebastian 2026-05-23): alle bestehenden QSO/Blacklist/Heard-Rows
    ohne user_callsign bekommen den aktuellen Single-Operator zugewiesen,
    damit das Multi-User-Filtering nahtlos uebernehmen kann.
    """
    engine = get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await _migrate_qrz_columns(conn)
        await _migrate_user_callsign_columns(conn, default_user_callsign)
        await _migrate_mf_columns(conn)
        await _migrate_dxped_source_column(conn)
        await _migrate_watchlist_source_column(conn)


async def _migrate_qrz_columns(conn) -> None:
    """Add QRZ-logbook tracking columns to an existing qso table."""
    res = await conn.exec_driver_sql("PRAGMA table_info(qso)")
    existing = {row[1] for row in res.fetchall()}
    additions = [
        ("qrz_uploaded",         "BOOLEAN NOT NULL DEFAULT 0"),
        ("qrz_logbook_id",       "TEXT"),
        ("qrz_upload_attempts",  "INTEGER NOT NULL DEFAULT 0"),
        ("qrz_last_attempt_at",  "DATETIME"),
    ]
    for name, ddl in additions:
        if name not in existing:
            await conn.exec_driver_sql(f"ALTER TABLE qso ADD COLUMN {name} {ddl}")


async def _migrate_watchlist_source_column(conn) -> None:
    """v0.19.2 — Watchlist.source-Spalte ergaenzen.

    Bestehende Rows bekommen source='manual'. Auto-Adds vom
    DXpedition-Schedule-Loop setzen ab v0.19.2 source='ng3k_auto'.
    """
    res = await conn.exec_driver_sql("PRAGMA table_info(watchlist)")
    existing = {row[1] for row in res.fetchall()}
    if "source" not in existing:
        await conn.exec_driver_sql(
            "ALTER TABLE watchlist ADD COLUMN source TEXT DEFAULT 'manual'"
        )
        await conn.exec_driver_sql(
            "UPDATE watchlist SET source='manual' WHERE source IS NULL"
        )


async def _migrate_dxped_source_column(conn) -> None:
    """v0.19.1 — DxpeditionSchedule.source-Spalte ergaenzen.

    Bestehende Rows (alle vom User manuell eingegeben) bekommen
    source='manual'. Neue Auto-Importe vom NG3K-Loop werden mit
    source='ng3k' eingetragen.
    """
    res = await conn.exec_driver_sql("PRAGMA table_info(dxpedition_schedule)")
    existing = {row[1] for row in res.fetchall()}
    if "source" not in existing:
        await conn.exec_driver_sql(
            "ALTER TABLE dxpedition_schedule ADD COLUMN source TEXT DEFAULT 'manual'"
        )
        await conn.exec_driver_sql(
            "UPDATE dxpedition_schedule SET source='manual' WHERE source IS NULL"
        )


async def _migrate_mf_columns(conn) -> None:
    """Add Marinefunker mf_mfnr snapshot column + backfill existing rows.

    Sebastian 2026-05-26 v0.9.0: spiegelt qso.mf_mfnr Snapshot — beim
    Migrieren gehen wir durch alle existierenden QSOs, machen einen
    Live-Lookup gegen die aktuelle marinefunker.json und setzen mf_mfnr
    auf die aktuelle MFNr falls Match. Backfill ist einmalig pro DB —
    spaetere PDF-Updates aendern nichts mehr an den bereits gelogten
    Rows (Snapshot-Semantik).
    """
    res = await conn.exec_driver_sql("PRAGMA table_info(qso)")
    existing = {row[1] for row in res.fetchall()}
    if "mf_mfnr" in existing:
        return
    await conn.exec_driver_sql("ALTER TABLE qso ADD COLUMN mf_mfnr INTEGER")
    await conn.exec_driver_sql(
        "CREATE INDEX IF NOT EXISTS ix_qso_mf_mfnr ON qso(mf_mfnr)"
    )
    # Backfill: alle existierenden QSOs gegen die aktuelle MF-Liste
    # matchen. Single-shot, idempotent.
    from ..integrations.mf_lookup import get_mf_lookup
    mf = get_mf_lookup()
    if len(mf) == 0:
        return
    res = await conn.exec_driver_sql("SELECT id, call FROM qso WHERE mf_mfnr IS NULL")
    matched = 0
    for row in res.fetchall():
        qso_id, call = row[0], row[1]
        if not call:
            continue
        member = mf.lookup(call)
        if member:
            await conn.exec_driver_sql(
                "UPDATE qso SET mf_mfnr = ? WHERE id = ?",
                (member.mfnr, qso_id),
            )
            matched += 1
    if matched > 0:
        print(f"[mf-backfill] markiert {matched} historische QSOs als Marinefunker")


async def _migrate_user_callsign_columns(conn, default_user_callsign: str | None) -> None:
    """Add user_callsign column to qso/blacklist/heard for Multi-Operator-
    Isolation (Sebastian 2026-05-23). Backfillt bestehende Rows mit dem
    default_user_callsign — bei single-operator-Bestand ist das genau
    der bisherige Operator, alle alten Daten bleiben sichtbar."""
    targets = ["qso", "blacklist", "heard"]
    for tbl in targets:
        res = await conn.exec_driver_sql(f"PRAGMA table_info({tbl})")
        existing = {row[1] for row in res.fetchall()}
        if "user_callsign" not in existing:
            await conn.exec_driver_sql(
                f"ALTER TABLE {tbl} ADD COLUMN user_callsign TEXT"
            )
            await conn.exec_driver_sql(
                f"CREATE INDEX IF NOT EXISTS ix_{tbl}_user_callsign "
                f"ON {tbl}(user_callsign)"
            )
        # Backfill: alle Rows ohne user_callsign bekommen den default
        if default_user_callsign:
            await conn.exec_driver_sql(
                f"UPDATE {tbl} SET user_callsign = ? WHERE user_callsign IS NULL",
                (default_user_callsign,),
            )


@contextlib.asynccontextmanager
async def session_scope() -> AsyncIterator[AsyncSession]:
    """Context manager yielding a new session, committed on success."""
    maker = get_sessionmaker()
    async with maker() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
