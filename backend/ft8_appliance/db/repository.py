"""Thin repository helpers around the ORM models.

Kept intentionally small — most queries throughout the app go directly
through ``session.execute(select(...))``. This module only collects the
handful of helpers that show up in more than one place.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, desc, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from .models import (BandNoise, Decode, Heard, PathPrediction, PickAttempt,
                     PskReporterIn, Qso, SolarLog, SwrLog)


# DATA-M1 (Audit 2026-05-30): Telemetrie-Tabellen wachsen sonst unbegrenzt
# → SD voll → DB-Korruption → QSO-Verlust. (Tabelle, Zeit-Spalte). Die
# qso-Tabelle ist BEWUSST NICHT dabei — Logdaten werden NIE geprunt.
_TELEMETRY_TABLES = (
    (Decode, Decode.ts),
    (PickAttempt, PickAttempt.ts),
    (Heard, Heard.last_seen),
    (SwrLog, SwrLog.ts),
    (PskReporterIn, PskReporterIn.ts),
    (SolarLog, SolarLog.ts),
    (BandNoise, BandNoise.ts),
    (PathPrediction, PathPrediction.ts),
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


async def letzte_anruf_ergebnisse(
    session: AsyncSession, *, limit: int = 20,
) -> list[bool]:
    """Die Ausgaenge der letzten eigenen Anrufe, neueste zuerst.

    Fuer den Strict-Modus, dessen Fenster sonst jeden Neustart neu gefuellt
    werden muesste — bei 40 Neustarts auf 494 Anrufe wird es nie voll.
    """
    res = await session.execute(
        select(PickAttempt.outcome)
        .where(PickAttempt.pick_kind == "cq")
        .where(PickAttempt.outcome.isnot(None))
        .order_by(PickAttempt.ts.desc())
        .limit(limit)
    )
    return [row[0] == "completed" for row in res.all()]


async def fehlversuche_je_call(
    session: AsyncSession, *, stunden: int = 6,
) -> dict[str, tuple[int, datetime]]:
    """Fehlgeschlagene Anrufe je Rufzeichen seit dem letzten Erfolg.

    Fuer die Eskalation des Fehlschlag-Cooldowns, die sonst jeden Neustart
    bei null beginnt. Nur Versuche nach dem letzten erfolgreichen QSO mit
    derselben Station zaehlen — ein gegluecktes QSO loescht die Historie,
    genau wie im laufenden Betrieb.

    Rueckgabe: ``{Rufzeichen: (Anzahl, letzter Versuch)}``.
    """
    grenze = datetime.now(UTC) - timedelta(hours=stunden)
    res = await session.execute(
        select(PickAttempt.target_call, PickAttempt.outcome, PickAttempt.ts)
        .where(PickAttempt.pick_kind == "cq")
        .where(PickAttempt.ts >= grenze)
        .order_by(PickAttempt.ts.asc())
    )
    stand: dict[str, tuple[int, datetime]] = {}
    for call, ausgang, ts in res.all():
        if not call:
            continue
        key = call.upper()
        if ausgang == "completed":
            stand.pop(key, None)          # Erfolg loescht die Serie
            continue
        n = stand.get(key, (0, ts))[0]
        stand[key] = (n + 1, ts)
    return stand


async def merge_heard_report(
    session: AsyncSession, *, ts, rx_call: str,
    rx_grid: str | None = None, snr_db: int | None = None,
    band: str | None = None,
) -> None:
    """Einen Empfangsbericht ablegen — wer hat uns gehoert.

    Der Abruf liefert alle fuenfzehn Minuten ein rollendes Fenster; dieselbe
    Station steht darin mehrfach. Schluessel ist deshalb (Zeitpunkt,
    Rufzeichen), und ein bereits vorhandener Eintrag wird nur ergaenzt statt
    verdoppelt.
    """
    vorhanden = await session.get(PskReporterIn, (ts, rx_call))
    if vorhanden is not None:
        if rx_grid and not vorhanden.rx_grid:
            vorhanden.rx_grid = rx_grid
        if snr_db is not None and vorhanden.snr_db is None:
            vorhanden.snr_db = snr_db
        if band and not vorhanden.band:
            vorhanden.band = band
        return
    session.add(PskReporterIn(
        ts=ts, rx_call=rx_call, rx_grid=rx_grid, snr_db=snr_db, band=band,
    ))


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
