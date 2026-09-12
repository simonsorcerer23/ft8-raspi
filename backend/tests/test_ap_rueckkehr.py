"""Der AP-Modus war eine Sackgasse.

Bis 2026-09-12 verliess die Station den Hotspot nur, wenn jemand von Hand
stoppte oder neu startete. Das faellt niemandem auf: im AP-Betrieb gibt es
kein Internet, also auch keine ntfy-Meldung. Wer vom Feldeinsatz heimkommt
und durchlaufen laesst, findet sie still im eigenen Hotspot wieder — ohne
Uploads, ohne Updates, ohne Tailscale.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from ft8_appliance.runtime.orchestrator import Orchestrator


def _stub(*, ap_aktiv: bool, upstream: list[bool]) -> SimpleNamespace:
    """upstream: Antworten von _has_upstream_connection der Reihe nach;
    der letzte Wert gilt danach weiter."""
    zustand = {"ap": ap_aktiv, "upstream": list(upstream)}
    geschaltet: list[bool] = []

    async def ap_aktiv_f():
        return zustand["ap"]

    async def upstream_f():
        return zustand["upstream"].pop(0) if len(zustand["upstream"]) > 1 \
            else zustand["upstream"][0]

    async def set_ap(an: bool):
        geschaltet.append(an)
        zustand["ap"] = an

    stub = SimpleNamespace(
        _AP_RUECKKEHR_NACH_S=Orchestrator._AP_RUECKKEHR_NACH_S,
        # Im Test gekuerzt: die 90 s der echten Konstante sind eine
        # Einstellung, geprueft wird der Ablauf.
        _AP_WLAN_GNADENFRIST_S=0.05,
        _AP_RUECKKEHR_MAX_S=Orchestrator._AP_RUECKKEHR_MAX_S,
        _ap_aktiv_seit=None,
        _ap_rueckkehr_fehlversuche=0,
        _ap_fallback_offline_since=None,
        ap_fallback_is_active=ap_aktiv_f,
        _has_upstream_connection=upstream_f,
        set_ap_fallback=set_ap,
        config=SimpleNamespace(network=SimpleNamespace(fallback_delay_s=60)),
    )
    stub._geschaltet = geschaltet
    stub._pruefe_ap_rueckkehr = lambda now: Orchestrator._pruefe_ap_rueckkehr(stub, now)
    stub._ap_fallback_tick = lambda: Orchestrator._ap_fallback_tick(stub)
    return stub


@pytest.fixture
def kein_warten(monkeypatch):
    """asyncio.sleep ueberspringen, damit die Gnadenfrist nicht real vergeht."""
    echt = asyncio.sleep

    async def fake(s, *a, **kw):
        return await echt(0)

    monkeypatch.setattr(asyncio, "sleep", fake)


@pytest.mark.asyncio
async def test_erster_tick_startet_die_uhr(kein_warten):
    """Laeuft der AP aus einer frueheren Sitzung weiter, beginnt die Frist
    mit dem ersten Tick — "nach Neustart 15 Minuten AP"."""
    stub = _stub(ap_aktiv=True, upstream=[False])
    await stub._pruefe_ap_rueckkehr(1000.0)
    assert stub._ap_aktiv_seit == 1000.0
    assert stub._geschaltet == []


@pytest.mark.asyncio
async def test_vor_ablauf_passiert_nichts(kein_warten):
    stub = _stub(ap_aktiv=True, upstream=[False])
    stub._ap_aktiv_seit = 1000.0
    await stub._pruefe_ap_rueckkehr(1000.0 + 899.0)
    assert stub._geschaltet == []


@pytest.mark.asyncio
async def test_nach_15_minuten_kommt_die_station_zurueck(kein_warten):
    """Der Kern: Hotspot aus, Netz da, Hotspot bleibt aus."""
    stub = _stub(ap_aktiv=True, upstream=[False, True])
    stub._ap_aktiv_seit = 0.0
    await stub._pruefe_ap_rueckkehr(1000.0)
    assert stub._geschaltet == [False], stub._geschaltet
    assert stub._ap_aktiv_seit is None
    assert stub._ap_rueckkehr_fehlversuche == 0


@pytest.mark.asyncio
async def test_ohne_netz_geht_der_hotspot_sofort_wieder_an(kein_warten):
    """Die Station darf nicht ohne jeden Zugang dastehen."""
    stub = _stub(ap_aktiv=True, upstream=[False])
    stub._ap_aktiv_seit = 0.0
    await stub._pruefe_ap_rueckkehr(1000.0)
    assert stub._geschaltet == [False, True], stub._geschaltet
    assert stub._ap_rueckkehr_fehlversuche == 1


@pytest.mark.asyncio
async def test_wartezeit_verdoppelt_sich_bei_misserfolg(kein_warten):
    """Sonst verschwindet der Hotspot im Feld alle 15 Minuten."""
    stub = _stub(ap_aktiv=True, upstream=[False])
    stub._ap_aktiv_seit = 0.0
    await stub._pruefe_ap_rueckkehr(1000.0)          # 1. Fehlversuch
    stub._geschaltet.clear()
    # Jetzt gilt die doppelte Frist: nach weiteren 15 min passiert nichts.
    start = stub._ap_aktiv_seit
    await stub._pruefe_ap_rueckkehr(start + 901.0)
    assert stub._geschaltet == [], "zu frueh — die Frist muss sich verdoppelt haben"
    await stub._pruefe_ap_rueckkehr(start + 1801.0)
    assert stub._geschaltet == [False, True]
    assert stub._ap_rueckkehr_fehlversuche == 2


@pytest.mark.asyncio
async def test_wartezeit_hat_eine_obergrenze(kein_warten):
    stub = _stub(ap_aktiv=True, upstream=[False])
    stub._ap_rueckkehr_fehlversuche = 99
    stub._ap_aktiv_seit = 0.0
    await stub._pruefe_ap_rueckkehr(Orchestrator._AP_RUECKKEHR_MAX_S + 1.0)
    assert stub._geschaltet == [False, True]


@pytest.mark.asyncio
async def test_netz_wieder_da_setzt_den_pendelschutz_zurueck(kein_warten):
    """Ein Tick mit Upstream loescht die Fehlversuche — sonst bliebe die
    lange Frist fuer immer stehen."""
    stub = _stub(ap_aktiv=False, upstream=[True])
    stub._ap_rueckkehr_fehlversuche = 4
    stub._ap_aktiv_seit = 500.0
    await stub._ap_fallback_tick()
    assert stub._ap_rueckkehr_fehlversuche == 0
    assert stub._ap_aktiv_seit is None
