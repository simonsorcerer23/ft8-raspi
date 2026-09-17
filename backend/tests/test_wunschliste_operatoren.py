"""Wer sieht welchen Wunschlisten-Eintrag?

Am 17.09. standen Nepal (9N) und Amerikanisch-Samoa (KH8WW) auf der Liste
von DO3XR — der automatische DXpedition-Import ordnet dem Operator zu, der
gerade aktiv ist. Die Station sendete aber als DK9XR, und der Picker sucht
nur in dessen Liste.
"""
from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from ft8_appliance.db import models as m
from ft8_appliance.db.repository import wunschliste_sichtbar
from ft8_appliance.db.session import init_engine, session_scope


async def _db() -> None:
    eng = init_engine(None)
    async with eng.begin() as c:
        await c.run_sync(m.Base.metadata.create_all)
    jetzt = datetime.now(UTC)
    async with session_scope() as s:
        s.add_all([
            m.Watchlist(call="V51WH", user_callsign="DK9XR", added=jetzt, source="ng3k_auto"),
            m.Watchlist(call="KH8WW", user_callsign="DO3XR", added=jetzt, source="ng3k_auto"),
            m.Watchlist(call="9N", user_callsign="DO3XR", added=jetzt, source="ng3k_auto"),
            m.Watchlist(call="DL1ABC", user_callsign="DO3XR", added=jetzt, source="manual"),
            m.Watchlist(call="OH3OJ", user_callsign="DK9XR", added=jetzt, source="manual"),
        ])


async def _sichtbar(operatoren) -> set[str]:
    async with session_scope() as s:
        return set((await s.execute(
            select(m.Watchlist.call).where(wunschliste_sichtbar(operatoren))
        )).scalars())


@pytest.mark.asyncio
async def test_dxpeditionen_gelten_fuer_jeden_operator() -> None:
    await _db()
    assert await _sichtbar(["DK9XR"]) == {"V51WH", "KH8WW", "9N", "OH3OJ"}
    assert await _sichtbar(["DO3XR"]) == {"V51WH", "KH8WW", "9N", "DL1ABC"}


@pytest.mark.asyncio
async def test_selbst_eingetragene_bleiben_beim_operator() -> None:
    """DL1ABC hat DO3XR eingetragen — das bleibt seine Sache."""
    await _db()
    assert "DL1ABC" not in await _sichtbar(["DK9XR"])
    assert "OH3OJ" not in await _sichtbar(["DO3XR"])


@pytest.mark.asyncio
async def test_oberflaeche_zeigt_dieselbe_liste_wie_der_picker() -> None:
    """Sonst sucht der Picker eine DXpedition, die in der Liste fehlt."""
    await _db()
    from ft8_appliance.web.routes.log import get_watchlist
    orch = SimpleNamespace(config=SimpleNamespace(operator=SimpleNamespace(callsign="DK9XR")))
    antwort = await get_watchlist(orch=orch)
    assert {e.call for e in antwort.entries} == {"V51WH", "KH8WW", "9N", "OH3OJ"}
