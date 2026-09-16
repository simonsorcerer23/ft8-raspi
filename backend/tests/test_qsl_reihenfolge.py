"""In welcher Reihenfolge die Kartenbilder nachgeholt werden.

Die Galerie zeigt je Station eine Kachel. Rein chronologisch holt der
Lauf unterwegs staendig die achte Karte einer Station, deren Kachel
laengst dasteht — jede davon ist eine eigene Bestaetigung, aber am
Bildschirm aendert sie nichts. Frisch Eingetroffenes behaelt trotzdem
Vorrang, sonst landet die Karte von heute hinter viertausend alten.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from ft8_appliance.config import OperatorConfig
from ft8_appliance.db import models as m
from ft8_appliance.db.session import init_engine, session_scope
from ft8_appliance.runtime.orchestrator import Orchestrator

HEUTE = datetime.now(UTC)


def _tag(vor_tagen: int) -> str:
    return (HEUTE - timedelta(days=vor_tagen)).strftime("%Y%m%d")


def _karte(call: str, eingang: str, datei: str | None = None) -> m.QslKarte:
    return m.QslKarte(
        schluessel=f"{call}_{eingang}_{datei or 'offen'}", call=call,
        qso_date=eingang, time_on="1200", band="20M", mode="FT8",
        user_callsign="DK9XR", empfangen_am=eingang, datei=datei, versuche=0,
    )


async def _db(karten: list[m.QslKarte]) -> None:
    eng = init_engine(None)
    async with eng.begin() as c:
        await c.run_sync(m.Base.metadata.create_all)
    async with session_scope() as s:
        for k in karten:
            s.add(k)


def _orch(tmp_path, monkeypatch, je_runde: int):
    """Orchestrator-Attrappe; hole_karte merkt sich nur die Reihenfolge."""
    reihenfolge: list[str] = []

    async def _hole(user, pw, eintrag, **kw):
        reihenfolge.append(eintrag.call)
        return b"\xff\xd8bild", "image/jpeg"

    import ft8_appliance.integrations.eqsl_inbox as mod
    monkeypatch.setattr(mod, "hole_karte", _hole)
    monkeypatch.setattr(mod, "MINDESTABSTAND_S", 0.0)  # kein Warten im Test

    o = SimpleNamespace(
        config=SimpleNamespace(operators=[OperatorConfig(
            callsign="DK9XR", eqsl_user="u", eqsl_password="p")]),
        _QSL_MAX_VERSUCHE=Orchestrator._QSL_MAX_VERSUCHE,
        _QSL_FRISCH_TAGE=Orchestrator._QSL_FRISCH_TAGE,
        _QSL_JE_RUNDE=je_runde,
        qsl_verzeichnis=lambda: tmp_path,
    )
    o.reihenfolge = reihenfolge
    return o


@pytest.mark.asyncio
async def test_im_altbestand_kommen_unsichtbare_stationen_zuerst(
        tmp_path, monkeypatch) -> None:
    """EC3A hat mehrere Karten und haengt laengst in der Galerie. Ein
    weiterer Abruf macht sie nicht voller — eine noch gar nicht gezeigte
    Station schon. Also UA9SY zuerst, obwohl die EC3A-Dublette juenger ist."""
    await _db([
        _karte("EC3A", _tag(40), datei="2026/EC3A_a.jpg"),   # schon sichtbar
        _karte("EC3A", _tag(30)),                            # Dublette, offen
        _karte("UA9SY", _tag(35)),                           # noch unsichtbar
    ])
    o = _orch(tmp_path, monkeypatch, je_runde=2)
    await Orchestrator._qsl_bilder_nachholen(o)

    assert o.reihenfolge == ["UA9SY", "EC3A"], (
        "Die Dublette einer sichtbaren Station wurde vorgezogen")


@pytest.mark.asyncio
async def test_frisch_eingetroffenes_schlaegt_die_neue_station(
        tmp_path, monkeypatch) -> None:
    """Sonst landet die Karte von heute hinter viertausend alten. Die
    Vorrangregel fuer den Altbestand darf das nicht aushebeln."""
    await _db([
        _karte("EC3A", _tag(40), datei="2026/EC3A_a.jpg"),   # sichtbar
        _karte("EC3A", _tag(0)),                             # HEUTE, Dublette
        _karte("UA9SY", _tag(35)),                           # alt, unsichtbar
    ])
    o = _orch(tmp_path, monkeypatch, je_runde=2)
    await Orchestrator._qsl_bilder_nachholen(o)

    assert o.reihenfolge[0] == "EC3A", (
        "Die heute eingetroffene Karte kam nicht zuerst")
    assert o.reihenfolge == ["EC3A", "UA9SY"]


@pytest.mark.asyncio
async def test_unter_gleichen_bleibt_das_neueste_vorn(
        tmp_path, monkeypatch) -> None:
    """Wo keine Station bevorzugt ist, gilt weiterhin: das Juengste zuerst."""
    await _db([
        _karte("ALT", _tag(90)),
        _karte("NEU", _tag(20)),
        _karte("MITTE", _tag(50)),
    ])
    o = _orch(tmp_path, monkeypatch, je_runde=3)
    await Orchestrator._qsl_bilder_nachholen(o)

    assert o.reihenfolge == ["NEU", "MITTE", "ALT"]
