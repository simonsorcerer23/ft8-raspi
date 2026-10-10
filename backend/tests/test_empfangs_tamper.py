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
from ft8_appliance.runtime.orchestrator import _RIG_REGELN, Orchestrator, _rig_abweichungen


MITTE = 128 / 255


def _snap(**kw):
    """Ein Rig im FT8-Soll."""
    basis = dict(nr_on=False, nb_on=False, anf_on=False, att_db=0, mn_on=False,
                 rit_on=False, xit_on=False, split_on=False, rf_gain=1.0,
                 pbt_in=MITTE, pbt_out=MITTE, tuner_on=True, comp_on=False,
                 vox_on=False, usb_af=0.502, sql=0.0)
    return SimpleNamespace(**{**basis, **kw})


def test_erkennt_was_fuer_ft8_schadet() -> None:
    assert _rig_abweichungen(_snap()) == []
    assert _rig_abweichungen(_snap(nr_on=True, nb_on=True)) == ["NR", "NB"]
    assert _rig_abweichungen(_snap(anf_on=True, att_db=20)) == ["ANF", "ATT"]
    assert _rig_abweichungen(_snap(rf_gain=0.4)) == ["RF"]
    assert _rig_abweichungen(_snap(pbt_in=0.7)) == ["PBT"]
    assert _rig_abweichungen(_snap(xit_on=True, split_on=True)) == ["XIT", "SPLIT"]
    assert _rig_abweichungen(_snap(tuner_on=False, vox_on=True)) == ["TUNER", "VOX"]
    # Rig kennt die Funktion nicht → None → kein Alarm
    leer = SimpleNamespace(**{k: None for k in _snap().__dict__})
    assert _rig_abweichungen(leer) == []


def test_lautstaerke_und_mithoerton_fasst_die_station_nie_an() -> None:
    """Der Lautsprecher steht bei Raymond; ein falscher Wert waere laut."""
    for schluessel, _anzeige, _pruef, korrektur in _RIG_REGELN:
        if korrektur is None:
            continue
        assert not any(str(x).upper() in ("AF", "MONITOR_GAIN", "MON") for x in korrektur), schluessel


@pytest.mark.asyncio
async def test_zurueckstellen_nutzt_den_richtigen_weg() -> None:
    o = _stub(_snap(rf_gain=0.3, pbt_out=0.8, split_on=True, mn_on=True))
    o.rig.set_level = AsyncMock()
    o.rig.set_split_aus = AsyncMock()
    Orchestrator._pruefe_empfang(o, "PKTUSB", "PKTUSB")
    await asyncio.gather(*o.gestartet)
    levels = {c.args for c in o.rig.set_level.await_args_list}
    assert ("RF", 1.0) in levels
    assert ("PBT_IN", MITTE) in levels and ("PBT_OUT", MITTE) in levels
    o.rig.set_split_aus.assert_awaited_once()
    assert ("MN", False) in {c.args for c in o.rig.set_func.await_args_list}


@pytest.mark.asyncio
async def test_auch_daempfung_kompressor_und_usb_pegel_werden_zurueckgestellt() -> None:
    """Bis 10.10.2026 nur gemeldet. Sebastian: Solange die Station laeuft,
    soll alles auf dem FT8-Soll stehen."""
    o = _stub(_snap(comp_on=True, att_db=20, usb_af=0.9))
    o.rig.set_level = AsyncMock()
    Orchestrator._pruefe_empfang(o, "PKTUSB", "PKTUSB")
    await asyncio.gather(*o.gestartet)
    await asyncio.sleep(0)
    assert {c.args for c in o.rig.set_func.await_args_list} == {("COMP", False)}
    assert {c.args for c in o.rig.set_level.await_args_list} == {("ATT", 0), ("USB_AF", 0.5)}
    assert o._notify_empfang_tamper.call_args.kwargs["abgeschaltet"] is True


def test_jede_regel_hat_eine_korrektur() -> None:
    """Keine Regel mehr, die nur meldet — wer eine neue anlegt, sagt auch,
    wie die Station sie zurueckstellt."""
    assert [r[0] for r in _RIG_REGELN if r[3] is None] == []


def _stub(snap, *, schuetzen=True, burst=False, aktiv=True):
    o = SimpleNamespace(
        _last_rig=snap, _tamper_armed=True, _last_empfang_alert=None,
        _empfang_restore_last_at=0.0, _tx_burst_active=burst,
        _rig_bedient_at=0.0, _empfang_push_at={},
        config=SimpleNamespace(operating=SimpleNamespace(rig_empfang_schuetzen=schuetzen)),
        rig=SimpleNamespace(set_func=AsyncMock()),
        _notify_empfang_tamper=AsyncMock(),
        gestartet=[],
        _station_aktiv=lambda: aktiv,
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
    assert '("xit_on",    "XIT")' in quelle and '"PBT_IN"' in quelle


def test_status_reicht_die_werte_durch() -> None:
    """Sonst sieht niemand in der Oberflaeche, dass NR an ist."""
    from ft8_appliance.web.routes.status import RigSnapshotOut
    for feld in ("nr_on", "anf_on", "att_db", "mn_on", "rit_on", "xit_on", "tuner_on",
                 "comp_on", "vox_on", "pbt_in", "pbt_out", "usb_af"):
        assert feld in RigSnapshotOut.model_fields, feld


@pytest.mark.asyncio
async def test_nach_stop_gehoert_das_rig_raymond() -> None:
    """Nach "Stop" darf Raymond am Geraet machen, was er will — keine Meldung,
    kein Zurueckstellen. Beim naechsten Start wird wieder geprueft."""
    o = _stub(_snap(nr_on=True, split_on=True), aktiv=False)
    Orchestrator._pruefe_empfang(o, "PKTUSB", "PKTUSB")
    await asyncio.sleep(0)
    o.rig.set_func.assert_not_called()
    o._notify_empfang_tamper.assert_not_called()
    # wieder gestartet: jetzt greift es
    o._station_aktiv = lambda: True
    Orchestrator._pruefe_empfang(o, "PKTUSB", "PKTUSB")
    await asyncio.gather(*o.gestartet)
    o._notify_empfang_tamper.assert_called_once()
    assert ("NR", False) in {c.args for c in o.rig.set_func.await_args_list}


def test_station_aktiv_heisst_hunt_cq_oder_laufendes_qso() -> None:
    from ft8_appliance.statemachine.states import State
    def o(answer=False, cq=False, state=State.IDLE):
        return SimpleNamespace(state_machine=SimpleNamespace(
            ctx=SimpleNamespace(auto_answer=answer, auto_cq=cq), state=state))
    assert not Orchestrator._station_aktiv(o())                     # nach Stop
    assert Orchestrator._station_aktiv(o(answer=True))              # Hunt
    assert Orchestrator._station_aktiv(o(cq=True))                  # CQ
    assert Orchestrator._station_aktiv(o(state=State.QSO_REPORT))   # QSO laeuft aus


def test_auch_die_alten_meldungen_schweigen_nach_stop() -> None:
    """Leistung, Betriebsart, Filter und Frequenz meldeten bisher auch nach
    Stop — Raymond am Geraet haette eine Push-Serie ausgeloest."""
    import inspect
    quelle = inspect.getsource(Orchestrator._rig_poll_loop)
    # Leistung, Betriebsart, Filter, Frequenz
    assert quelle.count("self._station_aktiv()") >= 4


@pytest.mark.asyncio
async def test_ausgeschalteter_tuner_wird_wieder_eingeschaltet() -> None:
    """Am 10.10.2026 stand am Ersatzgeraet der Tuner aus; die Station meldete
    es nur. Jetzt schaltet sie ihn ein — ausser es ist abgewaehlt."""
    o = _stub(_snap(tuner_on=False, vox_on=True))
    o.config.operating.rig_tuner_einschalten = True
    Orchestrator._pruefe_empfang(o, "PKTUSB", "PKTUSB")
    await asyncio.gather(*o.gestartet)
    await asyncio.sleep(0)
    assert {c.args for c in o.rig.set_func.await_args_list} == {("TUNER", True), ("VOX", False)}

    o = _stub(_snap(tuner_on=False))
    o.config.operating.rig_tuner_einschalten = False
    Orchestrator._pruefe_empfang(o, "PKTUSB", "PKTUSB")
    await asyncio.gather(*o.gestartet)
    await asyncio.sleep(0)
    o.rig.set_func.assert_not_called()


@pytest.mark.asyncio
async def test_rauschsperre_wird_geoeffnet() -> None:
    """Am Ersatzgeraet stand sie am 10.10.2026 auf 39 %."""
    assert _rig_abweichungen(_snap(sql=0.39)) == ["SQL"]
    o = _stub(_snap(sql=0.39))
    o.rig.set_level = AsyncMock()
    Orchestrator._pruefe_empfang(o, "PKTUSB", "PKTUSB")
    await asyncio.gather(*o.gestartet)
    await asyncio.sleep(0)
    assert {c.args for c in o.rig.set_level.await_args_list} == {("SQL", 0.0)}
