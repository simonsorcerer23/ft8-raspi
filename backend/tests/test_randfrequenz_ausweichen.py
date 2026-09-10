"""Stationen am Rand des Audio-Fensters bleiben erreichbar.

Der Filter ``hunt_audio_freq_min_hz`` / ``_max_hz`` warf Ziele aus der
Kandidatenliste, deren Traeger zu tief oder zu hoch lag. Sein Grund war
immer das Senden: ein Reply auf 262 Hz lief 2026-05-22 in den Rig-Bandpass
und riss den Pi in einen PWR-Spike.

Seit 2026-09-06 antwortet die Station aber auf dem ruhigsten Bin — der liegt
per Konstruktion zwischen 300 und 2400 Hz, also immer im sicheren Bereich —
und der Rufer dekodiert ohnehin das ganze Passband. Der Filter hielt damit
Stationen zurueck, deren Frequenz fuer unsere Sendung keine Rolle mehr
spielt: ueber fuenf Tage 1018 CQ-Rufe von 161 Stationen, darunter J38DX
(Grenada, 51-mal), AA3B, 4L7T und SV8/F6BLP — kein einziger Anrufversuch.

Seit v0.88.0 verwirft der Picker sie nicht mehr, sondern erzwingt fuer sie
den ruhigen Bin (``reply_kind = "quiet_edge"``). Nur wenn beide
Ausweich-Schalter aus sind, bleibt der alte Filter die Bremse.
"""

from __future__ import annotations

from datetime import UTC, datetime

from ft8_appliance.statemachine.guards import HardwareState
from ft8_appliance.statemachine.machine import StateMachine
from ft8_appliance.statemachine.states import DecodedMsg, MachineContext, State


def _hw_ok() -> HardwareState:
    return HardwareState(
        gps_fix_mode=3, time_offset_s=0.05, swr=1.2, alc_pct=0,
        battery_v=12.0, cpu_temp_c=45.0, audio_drift_samples=0,
        antenna_covers_band=True, chrony_synced=True,
    )


def _cq(call: str, hz: int, snr: int = -10) -> DecodedMsg:
    return DecodedMsg(
        ts=datetime.now(UTC), call_from=call, call_to=None, grid="FK92",
        message=f"CQ {call} FK92", snr_db=snr, dt_s=0.1,
        freq_offset_hz=hz, band="20m",
    )


def _sm(quiet: bool = True, ab: bool = False) -> StateMachine:
    sm = StateMachine(ctx=MachineContext(callsign="DK9XR", my_grid="JN58"))
    sm.ctx.auto_answer = True
    sm.ctx.hunt_audio_freq_min_hz = 400
    sm.ctx.hunt_audio_freq_max_hz = 2600
    sm.ctx.hunt_reply_quiet_freq = quiet
    sm.ctx.hunt_reply_ab_test = ab
    sm.state = State.IDLE
    return sm


def _reply_hz(sm: StateMachine) -> int | None:
    return sm.qso.freq_offset_hz if sm.qso else None


# ------------------------------------------------------- oberer Rand (J38DX)
def test_station_ueber_dem_fenster_wird_angerufen():
    """Der Fall J38DX: 51 CQ-Rufe auf 2921 Hz, nie versucht."""
    sm = _sm()

    sm.on_decodes(_hw_ok(), [_cq("J38DX", hz=2921, snr=-16)])

    assert sm.state is State.QSO_RESPOND, "Grenada darf nicht am Filter haengen"
    assert sm.qso is not None and sm.qso.their_call == "J38DX"


def test_antwort_liegt_im_sicheren_bereich():
    """Gesendet wird nie am Rand — sonst waere der PWR-Spike zurueck."""
    sm = _sm()

    sm.on_decodes(_hw_ok(), [_cq("J38DX", hz=2921)])

    hz = _reply_hz(sm)
    assert hz is not None
    assert 300 <= hz <= 2400, f"Antwort auf {hz} Hz liegt im gedaempften Rand"
    assert hz != 2921, "keinesfalls auf seiner Frequenz"


# ------------------------------------------------------- unterer Rand (LB7YK)
def test_station_unter_dem_fenster_wird_angerufen():
    sm = _sm()

    sm.on_decodes(_hw_ok(), [_cq("LB7YK", hz=209, snr=-14)])

    assert sm.state is State.QSO_RESPOND
    hz = _reply_hz(sm)
    assert hz is not None and 300 <= hz <= 2400


# ------------------------------------------------------------ Telemetrie
def test_randfall_ist_in_der_telemetrie_unterscheidbar():
    """Eigener reply_kind, damit das A/B nicht verwaessert wird."""
    sm = _sm()

    sm.on_decodes(_hw_ok(), [_cq("AA3B", hz=2721)])

    meta = sm.ctx.hunt_attempt_meta.get("AA3B")
    assert meta is not None
    assert meta["reply_kind"] == "quiet_edge"


def test_ziel_im_fenster_behaelt_die_alte_kennzeichnung():
    sm = _sm()

    sm.on_decodes(_hw_ok(), [_cq("DL1ABC", hz=1200)])

    meta = sm.ctx.hunt_attempt_meta.get("DL1ABC")
    assert meta is not None and meta["reply_kind"] == "quiet"


# --------------------------------------------------- A/B bleibt ausgesetzt
def test_ab_test_wird_am_rand_nicht_gewuerfelt():
    """Auch wenn das A/B 'on_freq' zoege — am Rand nicht."""
    sm = _sm(quiet=False, ab=True)
    sm.ctx.reply_ab_counter = 0     # naechster Wurf waere ungerade -> on_freq

    sm.on_decodes(_hw_ok(), [_cq("4L7T", hz=2710)])

    assert sm.state is State.QSO_RESPOND
    hz = _reply_hz(sm)
    assert hz is not None and hz != 2710, "on_freq am Rand waere gedaempft"


# ------------------------------------------------- ohne Ausweichmoeglichkeit
def test_ohne_ausweichschalter_bleibt_der_alte_filter():
    """Wer fest auf der Rufer-Frequenz antwortet, muss den Rand meiden."""
    sm = _sm(quiet=False, ab=False)

    sm.on_decodes(_hw_ok(), [_cq("J38DX", hz=2921)])

    assert sm.state is State.IDLE, "ohne ruhigen Bin bleibt der Filter noetig"


def test_normale_ziele_bleiben_unveraendert():
    sm = _sm(quiet=False, ab=False)

    sm.on_decodes(_hw_ok(), [_cq("DL1ABC", hz=1500)])

    assert sm.state is State.QSO_RESPOND
    assert _reply_hz(sm) == 1500, "im Fenster wird auf seiner Frequenz geantwortet"
