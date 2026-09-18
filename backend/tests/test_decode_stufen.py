"""Welche Decoder-Stufe hat einen Decode geliefert — und wann kam er an?

Vom 13. bis 17.09. fielen 549 Aussendungen aus, und jede einzelne folgte
binnen einer Sekunde auf einen Decode der Stufe 3 (jt9). Die kommt 3 bis 7
Sekunden nach der Slotgrenze, zu spaet fuer eine Antwort im selben Slot.
Ob Stufe 3 unterm Strich QSOs bringt oder kostet, war trotzdem nicht zu
messen: ``decode.ts`` ist der Slotbeginn, nicht der Eingang, und die Stufe
stand nirgends.
"""
from __future__ import annotations

import datetime as _dt
import sqlite3
import time

import pytest

from ft8_appliance.decode.ft8_native import ShimDecode
from ft8_appliance.decode.pipeline import _to_decoded_msg
from ft8_appliance.runtime.slot_clock import SlotTick
from ft8_appliance.statemachine import DecodedMsg
from ft8_appliance.statemachine.guards import HardwareState
from ft8_appliance.statemachine.machine import StateMachine
from ft8_appliance.statemachine.states import MachineContext


def _tick(vor_s: float) -> SlotTick:
    posix = time.time() - vor_s
    return SlotTick(index=298, posix=posix,
                    utc_start=_dt.datetime.fromtimestamp(posix, tz=_dt.UTC))


def _d(msg: str, frm: str, *, stufe: int, to: str | None = None) -> DecodedMsg:
    return DecodedMsg(ts=_dt.datetime.now(_dt.UTC), call_from=frm, call_to=to,
                      grid="FN42", message=msg, snr_db=-5, dt_s=0.1,
                      freq_offset_hz=1500, band="20m", stufe=stufe, late=stufe > 1)


def test_pipeline_stempelt_stufe_und_eingang() -> None:
    roh = ShimDecode(message="CQ K1ABC FN42", snr_db_est=-12, dt_s=0.2,
                     freq_hz=1500.0, score=40)
    schnell = _to_decoded_msg(roh, _tick(0.4), "20m")
    spaet = _to_decoded_msg(roh, _tick(4.9), "20m", late=True, stufe=3)
    assert schnell.stufe == 1 and 0.3 <= schnell.eingang_s <= 0.6
    assert spaet.stufe == 3 and 4.8 <= spaet.eingang_s <= 5.1


def test_anrufversuch_merkt_sich_die_stufe_seines_ziels() -> None:
    sm = StateMachine(ctx=MachineContext(callsign="DK9XR", my_grid="JN58ch",
                                         auto_answer=True, my_continent="EU",
                                         hunt_skip_late_finds=False))
    sm.on_decodes(HardwareState(), [_d("CQ K1ABC FN42", "K1ABC", stufe=3)])
    sm.drain_actions()
    assert sm.ctx.hunt_attempt_meta["K1ABC"]["ziel_stufe"] == 3


def test_eingehender_anruf_merkt_sich_die_stufe() -> None:
    sm = StateMachine(ctx=MachineContext(callsign="DK9XR", my_grid="JN58ch"))
    d = _d("DK9XR K1ABC FN42", "K1ABC", stufe=3, to="DK9XR")
    sm._note_inbound_attempt(d, [d], "inbound_grid")
    assert sm.ctx.hunt_attempt_meta["K1ABC"]["ziel_stufe"] == 3


@pytest.mark.asyncio
async def test_alte_datenbank_bekommt_die_spalten(tmp_path) -> None:
    from ft8_appliance.db.session import create_all, init_engine

    pfad = tmp_path / "alt.sqlite"
    init_engine(pfad)
    await create_all("DK9XR")
    con = sqlite3.connect(pfad)
    con.execute("alter table decode drop column stufe")
    con.execute("alter table decode drop column eingang_s")
    con.execute("alter table pick_attempt drop column ziel_stufe")
    con.commit(); con.close()

    init_engine(pfad)
    await create_all("DK9XR")
    con = sqlite3.connect(pfad)
    assert {"stufe", "eingang_s"} <= {r[1] for r in con.execute("pragma table_info(decode)")}
    assert "ziel_stufe" in {r[1] for r in con.execute("pragma table_info(pick_attempt)")}


def _jaeger() -> StateMachine:
    return StateMachine(ctx=MachineContext(callsign="DK9XR", my_grid="JN58ch",
                                           auto_answer=True, my_continent="EU"))


def test_jt9_fund_wird_nicht_neu_angerufen() -> None:
    """18.09.: 50 Anrufe an jt9-Ziele, kein QSO — und jeder liess einen Burst ausfallen."""
    sm = _jaeger()
    assert sm._pick_hunt_target([_d("CQ K1ABC FN42", "K1ABC", stufe=3)]) is None
    assert sm.filter_drops.get("spaeter_fund") == 1
    assert sm._pick_hunt_target([_d("CQ K1ABC FN42", "K1ABC", stufe=1)]) is not None


def test_jt9_fund_von_der_wunschliste_wird_angerufen() -> None:
    sm = _jaeger()
    sm.ctx.watchlist_calls = {"K1ABC"}
    assert sm._pick_hunt_target([_d("CQ K1ABC FN42", "K1ABC", stufe=3)]) is not None


def test_jt9_bleibt_im_laufenden_qso_wirksam() -> None:
    """Die Antwort der Gegenstation, die nur jt9 empfing, fuehrt das QSO weiter."""
    sm = _jaeger()
    sm.on_decodes(HardwareState(), [_d("CQ K1ABC FN42", "K1ABC", stufe=1)])
    sm.drain_actions()
    assert sm.qso is not None and sm.qso.their_call == "K1ABC"
    zustand = sm.state
    sm.on_decodes(HardwareState(), [_d("DK9XR K1ABC -12", "K1ABC", stufe=3, to="DK9XR")])
    assert sm.state is not zustand
