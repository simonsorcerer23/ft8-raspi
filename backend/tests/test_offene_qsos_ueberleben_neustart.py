"""Abgebrochene QSOs bleiben ueber einen Neustart fortsetzbar.

Meldet sich ein Partner nach einem Abbruch doch noch — weil er unsere
Bestaetigung nicht gehoert hat und seinen Rapport wiederholt —, nimmt die
Station das QSO wieder auf. Der dafuer noetige Kontext lag nur im
Arbeitsspeicher.

Gemessen ueber drei Tage: 37 Abbrueche mit erhaltenem Rapport, und in
**36 Faellen** meldete sich der Partner binnen zehn Minuten zurueck —
typischerweise mit einer Zeile wie ``DK9XR W1KOK -12``, also seinem
Rapport ohne R. Wieder aufgenommen wurden drei.

Ein Teil der Luecke geht auf Neustarts: Die Station startet im Mittel alle
gut siebzig Minuten neu, und jeder Neustart innerhalb des
Zehn-Minuten-Fensters loescht die Erinnerung. Siebter Fall derselben
Klasse.

Nur Abbrueche mit erhaltenem Rapport kommen in Frage — ohne ihn fehlte dem
Logeintrag die Angabe, und es waere kein gueltiges QSO.
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


async def _abbruch(call: str, *, vor_min: int, rapport: int | None = -12,
                   ausgang: str = "report_never_closed") -> None:
    async with session_scope() as s:
        s.add(PickAttempt(
            ts=datetime.now(UTC) - timedelta(minutes=vor_min),
            target_call=call, target_grid="JO31", pick_kind="cq",
            outcome="bailed", bail_reason=ausgang, snr_db=-14,
            our_snr_received=rapport, band="20m", freq_offset_hz=1500,
        ))


def test_wiederherstellung_laeuft_beim_start():
    q = inspect.getsource(orch_mod.Orchestrator.start)
    assert "_restore_offene_qsos" in q


@pytest.mark.asyncio
async def test_frischer_abbruch_ist_fortsetzbar(db):
    await _abbruch("W1KOK", vor_min=3)

    async with session_scope() as s:
        offen = await repository.offene_qsos(s, minuten=10)

    assert len(offen) == 1
    assert offen[0]["their_call"] == "W1KOK"
    assert offen[0]["our_snr_received"] == -12


@pytest.mark.asyncio
async def test_ohne_rapport_nicht_fortsetzbar(db):
    """Ohne erhaltenen Rapport fehlte dem Logeintrag die Angabe — das waere
    kein gueltiges QSO."""
    await _abbruch("DL9ZZZ", vor_min=3, rapport=None)

    async with session_scope() as s:
        offen = await repository.offene_qsos(s, minuten=10)

    assert offen == []


@pytest.mark.asyncio
async def test_alter_abbruch_zaehlt_nicht(db):
    """Nach zehn Minuten bricht die Rueckmeldequote scharf ein — gemessen
    55 Rueckmeldungen bis 10 min, danach nur noch 6."""
    await _abbruch("OLD1AA", vor_min=45)

    async with session_scope() as s:
        offen = await repository.offene_qsos(s, minuten=10)

    assert offen == []


@pytest.mark.asyncio
async def test_abgeschlossene_qsos_nicht(db):
    async with session_scope() as s:
        s.add(PickAttempt(
            ts=datetime.now(UTC) - timedelta(minutes=2), target_call="EA1OK",
            pick_kind="cq", outcome="completed", our_snr_received=-9,
        ))

    async with session_scope() as s:
        offen = await repository.offene_qsos(s, minuten=10)

    assert offen == []


def test_ablauf_rechnet_ab_dem_abbruch():
    """Nicht ab dem Neustart — sonst laebe eine alte Erinnerung nach jedem
    Start zehn weitere Minuten."""
    q = inspect.getsource(orch_mod.Orchestrator._restore_offene_qsos)
    assert "abbruch.timestamp() + self.state_machine.RECENT_QSO_TTL_S" in q
    assert "if ablauf <= jetzt:" in q


def test_defekte_datenbank_bricht_den_start_nicht():
    q = inspect.getsource(orch_mod.Orchestrator._restore_offene_qsos)
    assert "except Exception" in q
