"""Der Strict-Modus konnte strukturell nie feuern.

Er soll nach einer schlechten Serie vorsichtiger werden: Bleiben in
``hunt_poor_run_window`` Versuchen weniger als
``hunt_poor_run_min_successes`` erfolgreich, verlangt er fuer einige
Minuten mehr Evidenz.

Die Liste der Ergebnisse lebte aber nur im Arbeitsspeicher, und der Code
steigt vor der Auswertung aus, solange das Fenster nicht voll ist. Nach
jedem Neustart begann die Zaehlung damit bei null.

Gemessen ueber 48 Stunden: **40 Neustarts gegen 494 Anrufe**, im Mittel
12,3 je Intervall — bei einem Fenster von zwanzig. Es wurde so gut wie nie
voll, und der Strict-Modus hat in zwei Tagen kein einziges Mal gegriffen.

Vierter Fall derselben Art nach Erfolgs-Cooldown, Slot-Paritaeten und
PSK-Liste. Beim Aufraeumen am 2026-09-11 uebersehen, weil damals nur
geprueft wurde, was *sichtbar* falsch lief — nicht, was gar nichts tat.
"""

from __future__ import annotations

import inspect
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select

from ft8_appliance.db import repository, session_scope
from ft8_appliance.db.models import PickAttempt
from ft8_appliance.db.session import create_all, init_engine
from ft8_appliance.runtime import orchestrator as orch_mod


@pytest.fixture
async def db(tmp_path):
    init_engine(tmp_path / "qso.sqlite")
    await create_all()
    yield


async def _seed(ausgaenge: list[str]) -> None:
    basis = datetime.now(UTC) - timedelta(hours=1)
    async with session_scope() as s:
        for i, a in enumerate(ausgaenge):
            s.add(PickAttempt(
                ts=basis + timedelta(minutes=i), target_call=f"T{i}",
                pick_kind="cq", outcome=a,
            ))


def test_wiederherstellung_laeuft_beim_start():
    q = inspect.getsource(orch_mod.Orchestrator.start)
    assert "_restore_hunt_outcomes" in q


@pytest.mark.asyncio
async def test_ergebnisse_kommen_zurueck(db):
    await _seed(["bailed"] * 8 + ["completed"] + ["bailed"] * 3)

    async with session_scope() as s:
        werte = await repository.letzte_anruf_ergebnisse(s, limit=20)

    assert len(werte) == 12
    assert sum(werte) == 1


@pytest.mark.asyncio
async def test_neueste_zuerst(db):
    """Die Reihenfolge entscheidet, welche Versuche im Fenster landen."""
    await _seed(["completed"] + ["bailed"] * 5)

    async with session_scope() as s:
        werte = await repository.letzte_anruf_ergebnisse(s, limit=3)

    assert werte == [False, False, False], "die drei juengsten, nicht die aeltesten"


@pytest.mark.asyncio
async def test_nur_eigene_anrufe(db):
    """Eingehende Anrufe gehoeren nicht in die Serie — sie sagen nichts
    ueber die Qualitaet unserer Zielauswahl."""
    basis = datetime.now(UTC)
    async with session_scope() as s:
        s.add(PickAttempt(ts=basis - timedelta(minutes=1), target_call="A",
                          pick_kind="cq", outcome="bailed"))
        s.add(PickAttempt(ts=basis, target_call="B", pick_kind="inbound_grid",
                          outcome="completed"))

    async with session_scope() as s:
        werte = await repository.letzte_anruf_ergebnisse(s, limit=20)

    assert werte == [False]


@pytest.mark.asyncio
async def test_abbruch_zaehlt_als_misserfolg(db):
    """``outcome`` ist im Schema NOT NULL — eine Zeile entsteht erst beim
    Ausgang. Ein Abbruch ist deshalb kein offener Fall, sondern ein
    gezaehlter Misserfolg."""
    basis = datetime.now(UTC)
    async with session_scope() as s:
        s.add(PickAttempt(ts=basis - timedelta(minutes=1), target_call="A",
                          pick_kind="cq", outcome="bailed"))
        s.add(PickAttempt(ts=basis, target_call="B", pick_kind="cq",
                          outcome="completed"))

    async with session_scope() as s:
        werte = await repository.letzte_anruf_ergebnisse(s, limit=20)

    assert werte == [True, False], "neueste zuerst"


def test_aelteste_zuerst_in_den_kontext():
    """Die Liste wird hinten angehaengt — kaeme sie verkehrt herum, wuerde
    das Fenster die falschen Versuche abschneiden."""
    q = inspect.getsource(orch_mod.Orchestrator._restore_hunt_outcomes)
    assert "reversed(zeilen)" in q


def test_defekte_datenbank_bricht_den_start_nicht():
    q = inspect.getsource(orch_mod.Orchestrator._restore_hunt_outcomes)
    assert "except Exception" in q
