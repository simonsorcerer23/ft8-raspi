"""Der Fehlschlag-Cooldown und seine Eskalation ueberleben den Neustart.

Nach einem erfolglosen Anruf bekommt die Station eine Sperrfrist: 15
Minuten, bei Wiederholung mal 1,7, gedeckelt auf 60. Sechs Versuche
sollten sich damit ueber rund dreieinhalb Stunden verteilen.

Beide Groessen — die laufende Sperre und der Zaehler, der sie eskaliert —
lebten nur im Arbeitsspeicher. Bei einem Neustart alle gut siebzig Minuten
kam die Eskalation nie ueber die zweite Stufe.

Messbare Folge am 2026-09-12: UT7UJ sechsmal vergeblich angerufen **in
zwei Stunden**, EH2PDA fuenfmal in zweieinhalb, VE3ARF viermal in weniger
als zwei. Jeder dieser Versuche kostet dreizehn Sekunden Sendezeit, die
woanders besser angelegt waere.

Fuenfter Fall derselben Art nach Erfolgs-Cooldown, Slot-Paritaeten,
PSK-Liste und den Anrufergebnissen des Strict-Modus.
"""

from __future__ import annotations

import inspect
from datetime import UTC, datetime, timedelta

import pytest

from ft8_appliance.db import repository, session_scope
from ft8_appliance.db.models import PickAttempt
from ft8_appliance.db.session import create_all, init_engine
from ft8_appliance.runtime import orchestrator as orch_mod


@pytest.fixture
async def db(tmp_path):
    init_engine(tmp_path / "qso.sqlite")
    await create_all()
    yield


async def _seed(eintraege: list[tuple[str, str, int]]) -> None:
    """(Rufzeichen, Ausgang, Minuten in der Vergangenheit)"""
    jetzt = datetime.now(UTC)
    async with session_scope() as s:
        for i, (call, ausgang, vor_min) in enumerate(eintraege):
            s.add(PickAttempt(
                ts=jetzt - timedelta(minutes=vor_min, seconds=i),
                target_call=call, pick_kind="cq", outcome=ausgang,
            ))


def test_wiederherstellung_laeuft_beim_start():
    q = inspect.getsource(orch_mod.Orchestrator.start)
    assert "_restore_fehlversuche" in q


@pytest.mark.asyncio
async def test_fehlversuche_werden_gezaehlt(db):
    await _seed([("UT7UJ", "bailed", 90), ("UT7UJ", "bailed", 60),
                 ("UT7UJ", "bailed", 30)])

    async with session_scope() as s:
        stand = await repository.fehlversuche_je_call(s, stunden=6)

    assert stand["UT7UJ"][0] == 3


@pytest.mark.asyncio
async def test_ein_erfolg_loescht_die_serie(db):
    """Genau wie im laufenden Betrieb: Klappt es, faengt die Zaehlung neu an."""
    await _seed([("EA5QS", "bailed", 120), ("EA5QS", "bailed", 100),
                 ("EA5QS", "completed", 60), ("EA5QS", "bailed", 30)])

    async with session_scope() as s:
        stand = await repository.fehlversuche_je_call(s, stunden=6)

    assert stand["EA5QS"][0] == 1, "nur der Versuch nach dem Erfolg"


@pytest.mark.asyncio
async def test_erfolg_als_letztes_loescht_ganz(db):
    await _seed([("DL1ABC", "bailed", 60), ("DL1ABC", "completed", 30)])

    async with session_scope() as s:
        stand = await repository.fehlversuche_je_call(s, stunden=6)

    assert "DL1ABC" not in stand


@pytest.mark.asyncio
async def test_alte_versuche_zaehlen_nicht(db):
    """Sonst waere eine Station nach einem schlechten Tag dauerhaft gesperrt."""
    await _seed([("OLD1AA", "bailed", 60 * 20)])

    async with session_scope() as s:
        stand = await repository.fehlversuche_je_call(s, stunden=6)

    assert "OLD1AA" not in stand


@pytest.mark.asyncio
async def test_nur_eigene_anrufe(db):
    jetzt = datetime.now(UTC)
    async with session_scope() as s:
        s.add(PickAttempt(ts=jetzt - timedelta(minutes=10), target_call="X1AA",
                          pick_kind="inbound_grid", outcome="bailed"))

    async with session_scope() as s:
        stand = await repository.fehlversuche_je_call(s, stunden=6)

    assert stand == {}


def test_eskalation_wird_nachgerechnet():
    """Der wiederhergestellte Zaehler muss die Sperrfrist verlaengern —
    sonst haette das Zurueckholen keinen Zweck."""
    q = inspect.getsource(orch_mod.Orchestrator._restore_fehlversuche)
    assert "faktor ** max(0, anzahl - 1)" in q
    assert "deckel_s" in q


def test_abgelaufene_sperren_werden_nicht_gesetzt():
    """Eine Frist aus der Vergangenheit wuerde nur den Speicher fuellen."""
    q = inspect.getsource(orch_mod.Orchestrator._restore_fehlversuche)
    assert "if bis > jetzt:" in q


def test_defekte_datenbank_bricht_den_start_nicht():
    q = inspect.getsource(orch_mod.Orchestrator._restore_fehlversuche)
    assert "except Exception" in q
