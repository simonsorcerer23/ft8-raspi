"""Die Wunschliste muss den Picker erreichen.

Bis 2026-09-10 tat sie zwei Dinge — Alarm aufs Handy und Rufzeichen an den
Hint-Decoder — aber in der Auswahl, wen die Box anruft, kam sie nicht vor.
``ctx.watchlist_calls`` wurde befuellt und von niemandem gelesen.

Schlimmer: Pile-Up-Stationen fliegen hart aus der Kandidatenliste, bevor die
Priorisierung ueberhaupt laeuft. Seltenes DX hat per Definition Pile-Up — die
Liste war damit fuer genau die Stationen wirkungslos, fuer die man sie
anlegt. Gemessen ueber sieben Tage: Z68PX (Kosovo) rief 16-mal CQ, kein
einziger Anrufversuch; V51WH (Namibia) 13 Decodes, nie versucht.

Trennlinie seit v0.87.0: Gates, die fragen "lohnt sich das?" (Pile-Up,
SNR-Floor, Kontinent-Quote, Schwach-Gate) werden fuer die Wunschliste
uebersteuert. Gates, die sagen "geht technisch nicht" (DT ausserhalb des
Empfangsfensters, gleiche Slot-Paritaet) und die Soft-Blacklist bleiben.
"""

from __future__ import annotations

from datetime import UTC, datetime

from ft8_appliance.statemachine.guards import HardwareState
from ft8_appliance.statemachine.machine import StateMachine, _in_watchlist
from ft8_appliance.statemachine.states import DecodedMsg, MachineContext, State


def _hw_ok() -> HardwareState:
    return HardwareState(
        gps_fix_mode=3, time_offset_s=0.05, swr=1.2, alc_pct=0,
        battery_v=12.0, cpu_temp_c=45.0, audio_drift_samples=0,
        antenna_covers_band=True, chrony_synced=True,
    )


def _cq(call: str, snr: int = -15, dt: float = 0.2) -> DecodedMsg:
    return DecodedMsg(
        ts=datetime.now(UTC), call_from=call, call_to=None, grid="JN11",
        message=f"CQ {call} JN11", snr_db=snr, dt_s=dt,
        freq_offset_hz=1500, band="20m",
    )


def _sm(**ctx_kw) -> StateMachine:
    sm = StateMachine(ctx=MachineContext(callsign="DK9XR", my_grid="JN58", **ctx_kw))
    sm.ctx.auto_answer = True
    sm.state = State.IDLE
    return sm


# ------------------------------------------------------------- _in_watchlist
def test_in_watchlist_erkennt_volles_rufzeichen_und_praefix():
    liste = {"Z68PX", "KH8", "VP5"}
    assert _in_watchlist("Z68PX", liste)
    assert _in_watchlist("z68px", liste), "Gross-/Kleinschreibung egal"
    assert _in_watchlist("KH8WW", liste), "Praefix aus dem DXpeditions-Kalender"
    assert _in_watchlist("VP5DX", liste)
    assert not _in_watchlist("DL1ABC", liste)
    assert not _in_watchlist(None, liste)
    assert not _in_watchlist("Z68PX", set())


# ------------------------------------------------------------------ Pile-Up
def test_wunschliste_ueberwindet_pile_up():
    """Der Fall Z68PX: 16 CQ-Rufe, nie versucht."""
    sm = _sm()
    sm.ctx.watchlist_calls = {"Z68PX"}
    sm.ctx.pile_up_calls = {"Z68PX"}

    sm.on_decodes(_hw_ok(), [_cq("Z68PX", snr=-8)])

    assert sm.state is State.QSO_RESPOND, "die Wunschstation muss angerufen werden"
    assert sm.qso is not None and sm.qso.their_call == "Z68PX"


def test_pile_up_filtert_alle_anderen_weiter():
    sm = _sm()
    sm.ctx.watchlist_calls = {"Z68PX"}
    sm.ctx.pile_up_calls = {"DL1ABC"}

    sm.on_decodes(_hw_ok(), [_cq("DL1ABC", snr=-5)])

    assert sm.state is State.IDLE, "gewoehnliche Pile-Up-Stationen bleiben gefiltert"


# --------------------------------------------------------------- SNR-Gates
def test_wunschliste_ueberwindet_snr_floor_und_schwach_gate():
    """Seltenes DX ist fast immer schwach — sonst waere es nicht selten."""
    sm = _sm()
    sm.ctx.watchlist_calls = {"V51WH"}
    sm.ctx.hunt_snr_floor_db = -18
    sm.ctx.hunt_weak_requires_psk = True
    sm.ctx.hunt_weak_snr_db = -13

    sm.on_decodes(_hw_ok(), [_cq("V51WH", snr=-21)])

    assert sm.state is State.QSO_RESPOND


def test_schwaches_signal_ohne_wunschliste_bleibt_gefiltert():
    sm = _sm()
    sm.ctx.watchlist_calls = {"V51WH"}
    sm.ctx.hunt_weak_requires_psk = True
    sm.ctx.hunt_weak_snr_db = -13

    sm.on_decodes(_hw_ok(), [_cq("DL1ABC", snr=-20)])

    assert sm.state is State.IDLE


# ------------------------------------------------- technische Gates bleiben
def test_dt_filter_gilt_auch_fuer_die_wunschliste():
    """Kein "lohnt sich"-Gate: bei dt 3,1 s hoert uns die Station nicht mehr."""
    sm = _sm()
    sm.ctx.watchlist_calls = {"Z68PX"}

    sm.on_decodes(_hw_ok(), [_cq("Z68PX", snr=-5, dt=3.1)])

    assert sm.state is State.IDLE, "physikalisch unerreichbar bleibt unerreichbar"


def test_soft_blacklist_schlaegt_die_wunschliste():
    """Eine ausdrueckliche Sperre wiegt schwerer als der Wunsch."""
    sm = _sm()
    sm.ctx.watchlist_calls = {"Z68PX"}
    sm.ctx.soft_blacklist = {"Z68PX"}

    sm.on_decodes(_hw_ok(), [_cq("Z68PX", snr=-5)])

    assert sm.state is State.IDLE
