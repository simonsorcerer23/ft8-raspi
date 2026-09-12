"""Die "taegliche" Wartung lief in Wahrheit nach jedem Neustart.

Gemessen am 2026-09-12: 49 Laeufe in sieben Tagen statt sieben. Das kostete
Schreiblast, vor allem aber machte es _DB_BACKUP_KEEP sinnlos — die sieben
aufbewahrten Sicherungen deckten zusammen knapp drei Stunden ab, alle vom
selben Vormittag, statt der gemeinten Woche.
"""

from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace

import pytest

from ft8_appliance.runtime.orchestrator import Orchestrator


class _Abbruch(Exception):
    pass


def _stub(maintenance_at: float) -> SimpleNamespace:
    stub = SimpleNamespace(
        _MAINTENANCE_INTERVAL_S=Orchestrator._MAINTENANCE_INTERVAL_S,
        _TELEMETRY_RETENTION_DAYS=Orchestrator._TELEMETRY_RETENTION_DAYS,
        _DB_BACKUP_KEEP=Orchestrator._DB_BACKUP_KEEP,
        _maintenance_at=maintenance_at,
        _maybe_persist_runtime_state=lambda *a, **kw: None,
    )
    return stub


async def _lauf(stub, monkeypatch, *, erlaube_schlaf: int = 3) -> list[float]:
    """Laesst den Loop ein paar Runden drehen und gibt die Schlafzeiten zurueck."""
    geschlafen: list[float] = []
    laeufe: list[str] = []

    async def fake_sleep(s: float, *a, **kw):
        geschlafen.append(s)
        if len(geschlafen) >= erlaube_schlaf:
            raise _Abbruch
        return None

    async def fake_prune(*a, **kw):
        laeufe.append("prune")
        return {}

    async def fake_backup(*a, **kw):
        laeufe.append("backup")
        return "/tmp/qso-test.sqlite"

    from ft8_appliance.db import repository, session_scope  # noqa: F401
    from ft8_appliance.db import session as db_session

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)
    monkeypatch.setattr(repository, "prune_telemetry", fake_prune)
    monkeypatch.setattr(db_session, "backup_database", fake_backup)

    class _S:
        async def __aenter__(self): return None
        async def __aexit__(self, *a): return False
    monkeypatch.setattr("ft8_appliance.db.session_scope", lambda: _S())

    with pytest.raises(_Abbruch):
        await Orchestrator._maintenance_loop(stub)
    stub._laeufe = laeufe
    return geschlafen


@pytest.mark.asyncio
async def test_frischer_lauf_wartet_nicht(monkeypatch):
    """Ohne gesicherten Zeitpunkt (erste Inbetriebnahme) laeuft sie sofort."""
    stub = _stub(0.0)
    await _lauf(stub, monkeypatch)
    assert "backup" in stub._laeufe


@pytest.mark.asyncio
async def test_nach_neustart_laeuft_sie_nicht_erneut(monkeypatch):
    """Der eigentliche Fehler: vor einer Stunde gelaufen, Dienst neu
    gestartet — sie darf NICHT wieder sichern."""
    stub = _stub(time.time() - 3600.0)
    await _lauf(stub, monkeypatch)
    assert stub._laeufe == [], stub._laeufe


@pytest.mark.asyncio
async def test_nach_einem_tag_laeuft_sie_wieder(monkeypatch):
    stub = _stub(time.time() - 86400.0 - 60.0)
    await _lauf(stub, monkeypatch)
    assert "backup" in stub._laeufe


@pytest.mark.asyncio
async def test_zeitpunkt_wird_gemerkt(monkeypatch):
    stub = _stub(0.0)
    gesichert: list[bool] = []
    stub._maybe_persist_runtime_state = lambda *a, **kw: gesichert.append(True)
    await _lauf(stub, monkeypatch)
    assert stub._maintenance_at > 0
    assert gesichert, "der Zeitpunkt muss auf die Platte, sonst war alles umsonst"
