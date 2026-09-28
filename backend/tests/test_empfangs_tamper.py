"""Rauschunterdrueckung, Stoeraustaster, Auto-Notch und Daempfungsglied am Rig.

Am 28.09.2026 schaltete jemand am IC-7300 NR und NB ein. Die Decodes fielen
eine Stunde lang auf ein Zehntel — bei unveraendertem Audiopegel, und uns
hoerten die Gegenstationen normal. Keine Anzeige schlug an: Die Tamper-
Erkennung pruefte nur Betriebsart und Filterbreite, und der Status las von
NR nur die Staerke, nicht ob sie an war.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from ft8_appliance.runtime import orchestrator as orch_mod
from ft8_appliance.runtime.orchestrator import Orchestrator, _empfangs_stoerer


def _snap(**kw):
    basis = dict(nr_on=False, nb_on=False, anf_on=False, att_db=0)
    return SimpleNamespace(**{**basis, **kw})


def test_erkennt_was_fuer_ft8_schadet() -> None:
    assert _empfangs_stoerer(_snap()) == []
    assert _empfangs_stoerer(_snap(nr_on=True, nb_on=True)) == ["NR", "NB"]
    assert _empfangs_stoerer(_snap(anf_on=True, att_db=20)) == ["ANF", "ATT"]
    # Rig kennt die Funktion nicht → None → kein Alarm
    assert _empfangs_stoerer(_snap(nr_on=None, att_db=None)) == []


def _stub(snap, *, schuetzen=True, burst=False):
    o = SimpleNamespace(
        _last_rig=snap, _tamper_armed=True, _last_empfang_alert=None,
        _empfang_restore_last_at=0.0, _tx_burst_active=burst,
        config=SimpleNamespace(operating=SimpleNamespace(rig_empfang_schuetzen=schuetzen)),
        rig=SimpleNamespace(set_func=AsyncMock()),
        _notify_empfang_tamper=AsyncMock(),
        gestartet=[],
    )
    o._spawn = lambda coro, name=None: o.gestartet.append(asyncio.ensure_future(coro))
    o._schedule_empfang_restore = lambda n: Orchestrator._schedule_empfang_restore(o, n)
    o._empfang_aus = lambda n: Orchestrator._empfang_aus(o, n)
    return o


@pytest.mark.asyncio
async def test_im_datenbetrieb_werden_nr_und_nb_abgeschaltet() -> None:
    o = _stub(_snap(nr_on=True, nb_on=True))
    Orchestrator._pruefe_empfang(o, "PKTUSB", "PKTUSB")
    await asyncio.gather(*o.gestartet)
    await asyncio.sleep(0)
    geschaltet = {c.args for c in o.rig.set_func.await_args_list}
    assert geschaltet == {("NR", False), ("NB", False)}
    o._notify_empfang_tamper.assert_called_once()
    assert o._notify_empfang_tamper.call_args.kwargs["abgeschaltet"] is True


@pytest.mark.asyncio
async def test_wer_auf_ssb_mithoert_wird_nicht_umgestellt() -> None:
    o = _stub(_snap(nr_on=True))
    Orchestrator._pruefe_empfang(o, "USB", "PKTUSB")
    await asyncio.sleep(0)
    o.rig.set_func.assert_not_called()
    assert o._notify_empfang_tamper.call_args.kwargs["abgeschaltet"] is False


@pytest.mark.asyncio
async def test_daempfungsglied_wird_nur_gemeldet() -> None:
    o = _stub(_snap(att_db=20))
    Orchestrator._pruefe_empfang(o, "PKTUSB", "PKTUSB")
    await asyncio.sleep(0)
    o.rig.set_func.assert_not_called()
    o._notify_empfang_tamper.assert_called_once()


@pytest.mark.asyncio
async def test_nur_eine_meldung_je_zustand_und_nie_waehrend_eines_bursts() -> None:
    o = _stub(_snap(nr_on=True), burst=True)
    for _ in range(5):
        Orchestrator._pruefe_empfang(o, "PKTUSB", "PKTUSB")
    await asyncio.sleep(0)
    assert o._notify_empfang_tamper.call_count == 1
    o.rig.set_func.assert_not_called()          # Burst laeuft


@pytest.mark.asyncio
async def test_abschaltbar() -> None:
    o = _stub(_snap(nr_on=True), schuetzen=False)
    Orchestrator._pruefe_empfang(o, "PKTUSB", "PKTUSB")
    await asyncio.sleep(0)
    o.rig.set_func.assert_not_called()


def test_status_liest_ob_nr_an_ist() -> None:
    """Die Staerke allein sagte nichts: Sie steht auch bei ausgeschalteter NR."""
    import inspect

    from ft8_appliance.rig.rigctld_client import RigctldClient
    quelle = inspect.getsource(RigctldClient.snapshot)
    assert '("nr_on",     "NR")' in quelle and '("anf_on",    "ANF")' in quelle
