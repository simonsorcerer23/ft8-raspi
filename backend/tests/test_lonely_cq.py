"""lonely_cq-Tier (2026-09-06).

Pick-Telemetrie am Nachmittag: 55 Picks, 6 vollendet, 31 "went silent"
(56 %). Der Rufer hatte meist laengst einen anderen Partner. Wer im Slot
davor schon CQ gerufen hat und dazwischen von niemandem angerufen wurde,
hat keinen Pile-Up und antwortet eher.
"""

from __future__ import annotations

import datetime as _dt

from ft8_appliance.config import OperatingConfig
from ft8_appliance.runtime.orchestrator import detect_lonely_cqs
from ft8_appliance.statemachine import DecodedMsg
from ft8_appliance.statemachine.machine import HUNT_TIERS, StateMachine
from ft8_appliance.statemachine.states import MachineContext


def _d(msg: str, frm: str, to: str | None = None, hz: int = 1500, snr: int = -8) -> DecodedMsg:
    return DecodedMsg(ts=_dt.datetime.now(_dt.UTC), call_from=frm, call_to=to, grid="JN58",
                      message=msg, snr_db=snr, dt_s=0.1, freq_offset_hz=hz, band="15m")


def test_detects_a_caller_who_cqs_twice_without_an_answer() -> None:
    prev2 = [_d("CQ K1ABC FN42", "K1ABC"), _d("CQ W1AW FN31", "W1AW")]
    prev1 = [_d("W1AW DL1AAA JN58", "DL1AAA", to="W1AW")]        # W1AW wurde angerufen
    now = [_d("CQ K1ABC FN42", "K1ABC"), _d("CQ W1AW FN31", "W1AW"), _d("CQ VE3AAA FN03", "VE3AAA")]
    assert detect_lonely_cqs(now, prev1, prev2) == {"K1ABC"}   # VE3AAA: erst einmal gesehen


def test_no_history_means_nobody_is_lonely() -> None:
    assert detect_lonely_cqs([_d("CQ K1ABC FN42", "K1ABC")], None, None) == set()
    assert detect_lonely_cqs([_d("CQ K1ABC FN42", "K1ABC")], [], None) == set()


def test_tier_is_registered_and_defaulted_before_psk_and_snr() -> None:
    assert "lonely_cq" in HUNT_TIERS
    prio = OperatingConfig().hunt_priority
    assert prio.index("lonely_cq") < prio.index("psk_heard_us") < prio.index("snr")
    # Migration alter Listen ergaenzt den Tier vor snr
    migrated = OperatingConfig(hunt_priority=["marine", "snr"]).hunt_priority
    assert "lonely_cq" in migrated and migrated.index("lonely_cq") < migrated.index("snr")


def test_picker_prefers_the_lonely_caller_over_a_louder_one() -> None:
    ctx = MachineContext(callsign="DK9XR", my_grid="JN58ch", auto_answer=True,
                         hunt_priority=["lonely_cq", "snr"])
    sm = StateMachine(ctx=ctx)
    sm.ctx.lonely_cq_calls = {"K1ABC"}
    best = sm._pick_hunt_target([_d("CQ W1AW FN31", "W1AW", snr=0), _d("CQ K1ABC FN42", "K1ABC", snr=-12)])
    assert best is not None and best.call_from == "K1ABC"
    sm.ctx.lonely_cq_calls = set()
    best = sm._pick_hunt_target([_d("CQ W1AW FN31", "W1AW", snr=0), _d("CQ K1ABC FN42", "K1ABC", snr=-12)])
    assert best is not None and best.call_from == "W1AW"
