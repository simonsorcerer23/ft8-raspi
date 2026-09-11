"""Wer uns gehoert hat, wird aufgeschrieben — nicht nur gezaehlt.

Der Abruf bei pskreporter.info laeuft ohnehin alle fuenfzehn Minuten. Bis
2026-09-11 landete davon aber nur die Menge der Rufzeichen im
Arbeitsspeicher und war nach dem naechsten Durchgang weg.

Die Tabelle ``psk_reporter_in`` gab es seit jeher, beschrieben hat sie
niemand: Die Empfaenger-Ansicht *liest* aus ihr, gefuellt wurde sie
ausschliesslich vom Demo-Seed. Ein Lesepfad ohne Schreibpfad — auf der
Station standen dort nach Monaten Betrieb null Zeilen.

Wozu die Historie gebraucht wird: Rund zwei Drittel der eigenen Anrufe
bekommen nie eine Antwort. Wer uns tatsaechlich hoert, ist die einzige
direkte Evidenz dazu, und erst mit einer Historie laesst sich fragen, ob
wir die Richtigen angerufen haben.
"""

from __future__ import annotations

import inspect
from datetime import UTC, datetime, timedelta

import pytest

from sqlalchemy import func, select

from ft8_appliance.db import repository, session_scope
from ft8_appliance.db.models import PskReporterIn
from ft8_appliance.db.session import create_all, init_engine
from ft8_appliance.runtime import orchestrator as orch_mod


@pytest.fixture
async def db(tmp_path):
    init_engine(tmp_path / "qso.sqlite")
    await create_all()
    yield


def test_der_abruf_schreibt_in_die_datenbank():
    """Sonst bleibt die Tabelle leer, so wie ueber Monate hinweg."""
    q = inspect.getsource(orch_mod.Orchestrator._psk_reciprocity_refresh_loop)
    assert "_persist_heard_reports" in q


def test_schreibfehler_kostet_nicht_den_abruf():
    q = inspect.getsource(orch_mod.Orchestrator._persist_heard_reports)
    assert "except Exception" in q


@pytest.mark.asyncio
async def test_bericht_wird_abgelegt(db):
    jetzt = datetime.now(UTC)
    async with session_scope() as s:
        await repository.merge_heard_report(
            s, ts=jetzt, rx_call="W1AW", rx_grid="FN31", snr_db=-14, band="20m",
        )
    async with session_scope() as s:
        zeile = await s.get(PskReporterIn, (jetzt, "W1AW"))
        assert zeile is not None
        assert zeile.rx_grid == "FN31"
        assert zeile.snr_db == -14


@pytest.mark.asyncio
async def test_derselbe_bericht_wird_nicht_verdoppelt(db):
    """Der Abruf liefert ein rollendes Fenster — dieselbe Station steht in
    aufeinanderfolgenden Durchgaengen mehrfach darin."""
    jetzt = datetime.now(UTC)
    for _ in range(3):
        async with session_scope() as s:
            await repository.merge_heard_report(
                s, ts=jetzt, rx_call="W1AW", snr_db=-14, band="20m",
            )
    async with session_scope() as s:
        n = (await s.execute(select(func.count()).select_from(PskReporterIn))).scalar()
    assert n == 1


@pytest.mark.asyncio
async def test_fehlende_angaben_werden_nachgetragen(db):
    """Der erste Bericht kann ohne Locator kommen, ein spaeterer mit."""
    jetzt = datetime.now(UTC)
    async with session_scope() as s:
        await repository.merge_heard_report(s, ts=jetzt, rx_call="W1AW")
    async with session_scope() as s:
        await repository.merge_heard_report(
            s, ts=jetzt, rx_call="W1AW", rx_grid="FN31", snr_db=-9,
        )
    async with session_scope() as s:
        zeile = await s.get(PskReporterIn, (jetzt, "W1AW"))
        assert zeile.rx_grid == "FN31"
        assert zeile.snr_db == -9


@pytest.mark.asyncio
async def test_verschiedene_zeitpunkte_bleiben_getrennt(db):
    """Sonst waere keine Ausbreitungs-Historie moeglich."""
    jetzt = datetime.now(UTC)
    async with session_scope() as s:
        await repository.merge_heard_report(s, ts=jetzt, rx_call="W1AW")
        await repository.merge_heard_report(
            s, ts=jetzt - timedelta(hours=1), rx_call="W1AW",
        )
    async with session_scope() as s:
        n = (await s.execute(select(func.count()).select_from(PskReporterIn))).scalar()
    assert n == 2
