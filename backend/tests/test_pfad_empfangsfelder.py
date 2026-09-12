"""Die Vorhersage soll dorthin zeigen, wo uns jemand hoert.

Acht feste Referenzrichtungen decken die Kontinente ab — gut fuer die
Frage, wohin wir ueberhaupt durchkommen. Fuer die Frage, wie weit FT8
ueber die vorhergesagte MUF traegt, taugen sie schlecht: Am 2026-09-12
liessen sich nur 636 von 6026 Empfangsberichten einer Vorhersage
zuordnen, weil die uebrigen aus Feldern kamen, die dort nicht vorkommen.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from ft8_appliance.db import session_scope
from ft8_appliance.db.models import PskReporterIn
from ft8_appliance.db.session import create_all, init_engine
from ft8_appliance.runtime.orchestrator import Orchestrator


def _stub():
    stub = SimpleNamespace(
        _PFAD_EMPFANGSFELDER_MAX=Orchestrator._PFAD_EMPFANGSFELDER_MAX,
    )
    stub._haeufigste_empfangsfelder = (
        lambda: Orchestrator._haeufigste_empfangsfelder(stub))
    return stub


async def _seed(eintraege: list[tuple[str, str, int]]) -> None:
    from datetime import UTC, datetime, timedelta
    jetzt = datetime.now(UTC)
    async with session_scope() as s:
        i = 0
        for grid, call, anzahl in eintraege:
            for _ in range(anzahl):
                i += 1
                s.add(PskReporterIn(
                    ts=jetzt - timedelta(seconds=i), rx_call=f"{call}{i}",
                    rx_grid=grid, snr_db=-15, band="20m",
                ))


@pytest.mark.asyncio
async def test_haeufigste_felder_kommen_zuerst(tmp_path):
    init_engine(tmp_path / "qso.sqlite")
    await create_all(default_user_callsign="DK9XR")
    await _seed([("JN55AA", "A", 50), ("JO31BB", "B", 30),
                 ("KO85CC", "C", 20), ("FN20DD", "D", 5)])
    felder = await _stub()._haeufigste_empfangsfelder()
    assert felder[:3] == ["JN55", "JO31", "KO85"], felder


@pytest.mark.asyncio
async def test_je_feld_nur_ein_ziel(tmp_path):
    """Sonst holt man fuenf Vorhersagen fuer dasselbe Gebiet und laesst
    andere Felder aus."""
    init_engine(tmp_path / "qso.sqlite")
    await create_all(default_user_callsign="DK9XR")
    await _seed([("JN55AA", "A", 40), ("JN48BB", "B", 30),
                 ("JN99CC", "C", 20), ("JO31DD", "D", 10)])
    felder = await _stub()._haeufigste_empfangsfelder()
    assert len(felder) == 2, felder
    assert felder[0].startswith("JN") and felder[1].startswith("JO")


@pytest.mark.asyncio
async def test_obergrenze_wird_eingehalten(tmp_path):
    """Gegenueber einem kostenlos betriebenen Dienst."""
    init_engine(tmp_path / "qso.sqlite")
    await create_all(default_user_callsign="DK9XR")
    felder_ein = [(f"{a}{b}11XX", f"C{a}{b}", 30 - i)
                  for i, (a, b) in enumerate(
                      [("J", "N"), ("J", "O"), ("K", "O"), ("K", "P"),
                       ("K", "N"), ("F", "N"), ("I", "O"), ("I", "M"),
                       ("I", "N"), ("J", "P"), ("K", "M"), ("E", "M")])]
    await _seed(felder_ein)
    felder = await _stub()._haeufigste_empfangsfelder()
    assert len(felder) <= Orchestrator._PFAD_EMPFANGSFELDER_MAX, len(felder)


@pytest.mark.asyncio
async def test_unbrauchbare_locator_werden_uebersprungen(tmp_path):
    """Ein Feld allein ("JN") reicht dem Vorhersagedienst nicht."""
    init_engine(tmp_path / "qso.sqlite")
    await create_all(default_user_callsign="DK9XR")
    await _seed([("JN", "A", 40), ("JO31BB", "B", 10)])
    felder = await _stub()._haeufigste_empfangsfelder()
    assert felder == ["JO31"], felder


@pytest.mark.asyncio
async def test_ohne_berichte_leere_liste(tmp_path):
    """Eine frische Anlage hat noch keine — dann bleiben die festen
    Referenzrichtungen allein, statt dass etwas krachend fehlschlaegt."""
    init_engine(tmp_path / "qso.sqlite")
    await create_all(default_user_callsign="DK9XR")
    assert await _stub()._haeufigste_empfangsfelder() == []
