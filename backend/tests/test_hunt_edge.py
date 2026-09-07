"""Hunting-Kanten (2026-09-06): gerichtete CQs, ruhige Antwortfrequenz,
Stufe-2-LDPC-Faktor.

Live-Beobachtung am Nachmittag: sechs Picks in 30 min, alle ohne Antwort
("went silent" / "picked another"). Zwei der Ursachen sind steuerbar —
wen wir anrufen (nicht "CQ NA" aus Europa) und wo wir antworten (nicht
im Pile-up direkt auf der Frequenz des Rufers).
"""

from __future__ import annotations

import datetime as _dt

import pytest

from ft8_appliance.decode.pipeline import parse_message
from ft8_appliance.statemachine import DecodedMsg
from ft8_appliance.statemachine.guards import HardwareState
from ft8_appliance.statemachine.machine import StateMachine, _directed_cq_excludes_us
from ft8_appliance.statemachine.states import MachineContext, State


def _d(msg: str, frm: str, hz: int = 1500, snr: int = -8, directed: str | None = None) -> DecodedMsg:
    return DecodedMsg(ts=_dt.datetime.now(_dt.UTC), call_from=frm, call_to=None, grid="JN58",
                      message=msg, snr_db=snr, dt_s=0.1, freq_offset_hz=hz, band="15m",
                      cq_directed=directed)


def _sm(**ctx) -> StateMachine:
    ctx.setdefault("hunt_reply_ab_test", False)   # 2026-09-07: A/B nur im eigenen Test
    c = MachineContext(callsign="DK9XR", my_grid="JN58ch", auto_answer=True, my_continent="EU", **ctx)
    sm = StateMachine(ctx=c)
    return sm


# ================================================================== Parser


@pytest.mark.parametrize("text,directed,call", [
    ("CQ DL1AAA JN58", None, "DL1AAA"),
    ("CQ DX JA1ABC PM95", "DX", "JA1ABC"),
    ("CQ NA W1AW FN31", "NA", "W1AW"),
    ("CQ POTA DL1AAA JN58", "POTA", "DL1AAA"),
    ("CQ JA VK2ABC QF56", "JA", "VK2ABC"),
])
def test_parser_exposes_the_directed_token(text, directed, call) -> None:
    p = parse_message(text)
    assert p.is_cq and p.call_from == call and p.cq_directed == directed


# ================================================================== Directed-CQ-Filter


@pytest.mark.parametrize("token,their,mine,excluded", [
    (None, "EU", "EU", False),
    ("NA", "NA", "EU", True),      # CQ NA aus Europa: nicht wir
    ("EU", "NA", "EU", False),     # CQ EU von W1AW: wir!
    ("DX", "EU", "EU", True),      # CQ DX von DL: wir sind kein DX
    ("DX", "AS", "EU", False),     # CQ DX von JA: wir sind DX
    ("DX", None, "EU", False),     # Kontinent unbekannt: nicht ausschliessen
    ("JA", "AS", "EU", True),
    ("POTA", "EU", "EU", False),   # Award, kein Region-Token
    ("TEST", "EU", "EU", False),
    ("NA", "NA", None, False),     # eigener Kontinent unbekannt: nichts filtern
])
def test_directed_cq_rule(token, their, mine, excluded) -> None:
    assert _directed_cq_excludes_us(token, their, mine) is excluded


def test_picker_skips_cq_na_but_takes_cq_eu() -> None:
    sm = _sm()
    sm.ctx.call_to_continent = {"W1AW": "NA", "K1ABC": "NA"}
    decodes = [
        _d("CQ NA W1AW FN31", "W1AW", hz=1000, snr=-3, directed="NA"),
        _d("CQ EU K1ABC FN42", "K1ABC", hz=1800, snr=-12, directed="EU"),
    ]
    best = sm._pick_hunt_target(decodes)
    assert best is not None and best.call_from == "K1ABC"


def test_picker_skips_cq_dx_from_own_continent() -> None:
    sm = _sm()
    sm.ctx.call_to_continent = {"DL9ZZZ": "EU"}
    assert sm._pick_hunt_target([_d("CQ DX DL9ZZZ JO31", "DL9ZZZ", directed="DX")]) is None


def test_directed_filter_can_be_switched_off() -> None:
    sm = _sm(hunt_respect_directed_cq=False)
    sm.ctx.call_to_continent = {"W1AW": "NA"}
    best = sm._pick_hunt_target([_d("CQ NA W1AW FN31", "W1AW", snr=-3, directed="NA")])
    assert best is not None


# ================================================================== Ruhige Antwortfrequenz


def _run_pick(sm: StateMachine, decodes: list[DecodedMsg]):
    sm.state = State.IDLE
    sm.on_decodes(HardwareState(), decodes)
    return sm.drain_actions()


def test_reply_goes_to_a_quiet_bin_not_onto_the_caller() -> None:
    sm = _sm()
    # Rufer bei 1500 Hz, drum herum dicht belegt; 800 Hz ist frei.
    crowd = [_d(f"CQ DL{i}AAA JN58", f"DL{i}AAA", hz=1500 + 30 * i) for i in range(1, 6)]
    target = _d("CQ K1ABC FN42", "K1ABC", hz=1500, snr=-5)
    actions = _run_pick(sm, crowd + [target])
    tx = [a for a in actions if a.kind == "TX_MESSAGE"]
    assert tx and sm.qso is not None and sm.qso.their_call == "K1ABC"
    assert sm.qso.freq_offset_hz != 1500  # nicht auf den Rufer
    assert 300 <= sm.qso.freq_offset_hz <= 2400


def test_reply_on_caller_frequency_when_switched_off() -> None:
    sm = _sm(hunt_reply_quiet_freq=False)
    target = _d("CQ K1ABC FN42", "K1ABC", hz=1500, snr=-5)
    _run_pick(sm, [target, _d("CQ DL1AAA JN58", "DL1AAA", hz=1520)])
    assert sm.qso is not None and sm.qso.freq_offset_hz == 1500


# ================================================================== Stufe-2-LDPC


@pytest.mark.asyncio
async def test_late_pass_uses_its_own_ldpc_factor_and_restores(monkeypatch) -> None:
    from unittest.mock import AsyncMock

    from ft8_appliance.audio.slot_sync import SlotBuffer
    from ft8_appliance.decode import ft8_native as _nat
    from ft8_appliance.decode import pipeline as _pipe
    from ft8_appliance.decode.ft8_native import SAMPLES_PER_SLOT
    from ft8_appliance.decode.pipeline import DecodePipeline
    from ft8_appliance.runtime.slot_clock import SlotTick

    calls: list[int] = []
    seen_during: list[int] = []

    class _Lib:
        def ft8_shim_set_ldpc_factor(self, pct: int) -> None:
            calls.append(int(pct))

    monkeypatch.setattr(_nat, "lib", _Lib())
    monkeypatch.setattr(_pipe, "decode_slot", lambda pcm: [])

    def v2(pcm, mode="standard"):
        seen_during.append(calls[-1])
        return []

    monkeypatch.setattr(_pipe, "decode_slot_v2", v2)
    buf = SlotBuffer(); buf.feed(b"\x00\x00" * SAMPLES_PER_SLOT, posix_start=1_700_000_000.0)
    pl = DecodePipeline(slot_buffer=buf, band_hint="15m")
    pl.decoder_mode = "extreme"; pl.extract_delay_s = 0.0
    pl.late_pass_sink = AsyncMock(); pl.late_ldpc_pct = 300; pl.ldpc_factor = 1.0
    posix = 1_700_000_015.0
    await pl(SlotTick(index=0, posix=posix, utc_start=_dt.datetime.fromtimestamp(posix, tz=_dt.UTC)))
    await pl._late_task
    assert seen_during == [300]          # Stufe 2 lief mit 300 %
    assert calls[-1] == 100              # danach zurueck auf den Stufe-1-Wert
