"""Die Watchlist hat in der Rangfolge Vorrang (seit 2026-11-18).

Vorher hob sie nur die Sperren auf; ein neues DXCC oder ein Marinefunker im
selben Slot gewann gegen KJ4YKG, mit dem ein Kontakt zustande kommen soll.
"""
from __future__ import annotations

from datetime import datetime, timezone

from ft8_appliance.config.models import OperatingConfig
from ft8_appliance.statemachine.machine import _compute_tier_score
from ft8_appliance.statemachine.states import DecodedMsg, MachineContext


def _decode(call, snr=-10):
    return DecodedMsg(ts=datetime.now(timezone.utc), call_from=call, call_to=None,
                      grid=None, message=f"CQ {call} FM16", snr_db=snr, dt_s=0.1,
                      freq_offset_hz=1500, band="15m")


def _ctx(**kw):
    ctx = MachineContext(callsign="DK9XR", my_grid="JN58", band="15m")
    ctx.hunt_priority = list(OperatingConfig().hunt_priority)
    for k, v in kw.items():
        setattr(ctx, k, v)
    return ctx


def test_watchlist_schlaegt_neues_dxcc_und_marinefunker():
    ctx = _ctx(watchlist_calls={"KJ4YKG"}, new_dxcc_calls={"3Y0J"}, marine_calls={"DL3QR"})
    freund = _compute_tier_score(_decode("KJ4YKG", snr=-18), ctx)
    assert freund > _compute_tier_score(_decode("3Y0J", snr=0), ctx)
    assert freund > _compute_tier_score(_decode("DL3QR", snr=0), ctx)


def test_bestehende_liste_bekommt_watchlist_hinter_den_filtern():
    alt = ["not_bad_reputation", "not_his_tx_slot", "not_in_pileup", "marine", "new_dxcc", "snr"]
    neu = OperatingConfig(hunt_priority=alt).hunt_priority
    assert neu[:3] == ["not_bad_reputation", "not_his_tx_slot", "watchlist"]


def test_vorhandene_position_bleibt_wie_eingestellt():
    eigene = ["snr", "watchlist", "not_bad_reputation"]
    neu = OperatingConfig(hunt_priority=eigene).hunt_priority
    assert neu.index("watchlist") == neu.index("snr") + 1
