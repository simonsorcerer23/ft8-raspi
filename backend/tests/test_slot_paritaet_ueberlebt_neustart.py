"""Gelernte Sende-Paritaeten muessen einen Neustart ueberstehen.

``_op_slot_parity`` entsteht nur im laufenden Betrieb: pro Station werden
die Slots gezaehlt, in denen sie sendet, und ab drei Decodes mit mindestens
70 Prozent auf einer Seite gilt die Paritaet als bekannt. Nach einem
Neustart begann diese Tabelle leer — und das Self-Update prueft alle zehn
Minuten, startet den Dienst also regelmaessig neu.

In dieser Zeit lief das Slot-Paritaets-Gate leer: Es soll Stationen meiden,
die im selben Durchgang senden wie wir, kann das ohne gelernte Paritaet
aber fuer niemanden entscheiden. Am 2026-09-11 stand der Fuellstand nach
einem Update auf null.

``_restore_slot_parity`` holt den Zustand aus der decode-Tabelle zurueck.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import pytest

from ft8_appliance.runtime import orchestrator as orch_mod


@dataclass
class _Decode:
    call_from: str
    ts: datetime


def _slot_zeit(paritaet: str, minuten_zurueck: float = 5.0) -> datetime:
    """Ein Zeitstempel, der auf einer Slot-Grenze der gewuenschten Haelfte liegt."""
    jetzt = datetime.now(UTC) - timedelta(minutes=minuten_zurueck)
    n = round(jetzt.timestamp() / 15.0)
    if (n % 2 == 0) != (paritaet == "even"):
        n += 1
    return datetime.fromtimestamp(n * 15.0, tz=UTC)


def _orch(monkeypatch, decodes):
    class _Op:
        callsign = "DK9XR"

    class _Cfg:
        operator = _Op()

    class _O:
        config = _Cfg()
        _op_slot_parity: dict = {}
        _op_slot_parity_votes: dict = {}
        _restore_slot_parity = orch_mod.Orchestrator._restore_slot_parity

    class _Scope:
        async def __aenter__(self): return object()
        async def __aexit__(self, *a): return False

    monkeypatch.setattr(orch_mod, "session_scope", lambda: _Scope())

    async def _latest(session, limit=4000):
        return list(decodes)

    monkeypatch.setattr(orch_mod.repository, "latest_decodes", _latest)
    o = _O()
    o._op_slot_parity = {}
    o._op_slot_parity_votes = {}
    return o


@pytest.mark.asyncio
async def test_eindeutige_paritaet_wird_uebernommen(monkeypatch):
    o = _orch(monkeypatch, [
        _Decode("MI0JZZ", _slot_zeit("even", m)) for m in (5, 6, 7, 8)
    ])

    await o._restore_slot_parity()

    assert o._op_slot_parity.get("MI0JZZ") == "even"


@pytest.mark.asyncio
async def test_zu_wenige_decodes_ergeben_kein_urteil(monkeypatch):
    o = _orch(monkeypatch, [
        _Decode("DL1ABC", _slot_zeit("even", 5)),
        _Decode("DL1ABC", _slot_zeit("even", 6)),
    ])

    await o._restore_slot_parity()

    assert "DL1ABC" not in o._op_slot_parity


@pytest.mark.asyncio
async def test_gemischte_station_bleibt_unbestimmt(monkeypatch):
    """Wer in beiden Haelften sendet, bekommt keine Paritaet — wie im Betrieb."""
    o = _orch(monkeypatch, [
        _Decode("EA5MIX", _slot_zeit("even", 5)),
        _Decode("EA5MIX", _slot_zeit("odd", 6)),
        _Decode("EA5MIX", _slot_zeit("even", 7)),
        _Decode("EA5MIX", _slot_zeit("odd", 8)),
    ])

    await o._restore_slot_parity()

    assert "EA5MIX" not in o._op_slot_parity


@pytest.mark.asyncio
async def test_alte_decodes_zaehlen_nicht(monkeypatch):
    """Nach Stunden kann die Station laengst die Seite gewechselt haben."""
    o = _orch(monkeypatch, [
        _Decode("OH2XO", _slot_zeit("odd", 60 * 5 + m)) for m in (0, 1, 2, 3)
    ])

    await o._restore_slot_parity()

    assert o._op_slot_parity == {}


@pytest.mark.asyncio
async def test_eigenes_rufzeichen_wird_uebersprungen(monkeypatch):
    o = _orch(monkeypatch, [
        _Decode("DK9XR", _slot_zeit("even", m)) for m in (5, 6, 7)
    ])

    await o._restore_slot_parity()

    assert "DK9XR" not in o._op_slot_parity


@pytest.mark.asyncio
async def test_stimmen_werden_mitgenommen(monkeypatch):
    """Damit das Live-Lernen nahtlos weiterzaehlt statt bei null zu beginnen."""
    o = _orch(monkeypatch, [
        _Decode("RA3XYZ", _slot_zeit("odd", m)) for m in (5, 6, 7)
    ])

    await o._restore_slot_parity()

    assert o._op_slot_parity_votes["RA3XYZ"] == {"even": 0, "odd": 3}


@pytest.mark.asyncio
async def test_defekte_datenbank_bricht_den_start_nicht(monkeypatch):
    o = _orch(monkeypatch, [])

    async def _kaputt(session, limit=4000):
        raise RuntimeError("DB weg")

    monkeypatch.setattr(orch_mod.repository, "latest_decodes", _kaputt)

    await o._restore_slot_parity()

    assert o._op_slot_parity == {}


# --------------------------------------------------- die stille Kopplung
def test_wiederherstellung_rechnet_wie_das_live_lernen():
    """Beide Seiten muessen dieselbe Parität ergeben, sonst lernt die Box
    nach jedem Neustart das Gegenteil dessen, was sie beobachtet hat — und
    eine falsche Parität ist schlimmer als gar keine: Sie lenkt die
    Sendeentscheidung aktiv in die Irre.

    Die Kopplung ist still und liegt an drei Stellen: ``_compute_slot_parity``
    rechnet aus ``tick.posix``, die Wiederherstellung aus ``decode.ts`` — und
    dass beide denselben Wert tragen, entscheidet allein die Pipeline mit
    ``ts=datetime.fromtimestamp(tick.posix)``.
    """
    import inspect
    from datetime import UTC, datetime

    from ft8_appliance.decode import pipeline as pl
    from ft8_appliance.runtime import orchestrator as orch_mod

    # 1. Die Pipeline stempelt den Decode mit der Slot-Grenze, nicht mit
    #    der Uhrzeit der Verarbeitung.
    assert "ts=datetime.fromtimestamp(tick.posix, tz=UTC)" in inspect.getsource(pl)

    # 2. Beide Formeln liefern fuer denselben Zeitpunkt dasselbe Urteil.
    quelle = inspect.getsource(orch_mod.Orchestrator._restore_slot_parity)
    assert 'round(ts.timestamp() / 15.0) % 2 == 0' in quelle

    o = object.__new__(orch_mod.Orchestrator)
    for posix in (1757577600.0, 1757577615.0, 1757577630.0, 1757577645.0):
        tick = type("T", (), {"posix": posix, "slot_seconds": 15.0})()
        live = orch_mod.Orchestrator._compute_slot_parity(o, tick)
        ts = datetime.fromtimestamp(posix, tz=UTC)
        wiederhergestellt = (
            "even" if round(ts.timestamp() / 15.0) % 2 == 0 else "odd"
        )
        assert live == wiederhergestellt, f"Parität weicht ab bei {posix}"
