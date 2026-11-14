"""Audit vom 14.11.2026, Teil Sende-Sicherheit: je behobenem Befund ein Test.

Die Befunde stehen in docs/audit_2026_11_14.md. Jeder Test beschreibt das
Verhalten NACH der Behebung; der Kommentar darueber, was vorher geschah.
"""

from __future__ import annotations

import asyncio
import time
from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest

from ft8_appliance.config import (
    AntennaConfig,
    AppConfig,
    BandConfig,
    OperatingConfig,
    OperatorConfig,
)
from ft8_appliance.config.models import RigConfig
from ft8_appliance.rig.rigctld_client import RigSnapshot
from ft8_appliance.runtime import FakeSlotClock, Orchestrator, SlotTick
from ft8_appliance.runtime import orchestrator as orch_mod
from ft8_appliance.statemachine import DecodedMsg
from ft8_appliance.statemachine.guards import GuardLimits, HardwareState
from ft8_appliance.statemachine.machine import StateMachine
from ft8_appliance.statemachine.states import MachineContext, QsoContext, State


@pytest.fixture(autouse=True)
def _synced_clock(monkeypatch):
    from ft8_appliance.util.system_health import ChronyStatus

    async def _synced():
        return ChronyStatus(offset_s=0.001, stratum=3, ref_id_name="test-ntp")

    monkeypatch.setattr(orch_mod, "read_chrony_tracking", _synced)


def _cfg(*, license_class="A", bands=("20m",), default_power_w=50, **op_kw) -> AppConfig:
    freqs = {"20m": 14074, "15m": 21074, "60m": 5357, "10m": 28074}
    return AppConfig(
        operator=OperatorConfig(
            callsign="DK9XR", default_locator="JN58td",
            default_power_w=default_power_w, license_class=license_class,
        ),
        rig=RigConfig(model="ic7300"),
        bands=[BandConfig(name=b, freq_khz=freqs[b], antenna="ant") for b in bands],
        antennas=[AntennaConfig(name="ant", bands=list(bands))],
        operating=OperatingConfig(**op_kw),
    )


def _orch(cfg: AppConfig, decode_source=None, freq_hz=14_074_000) -> Orchestrator:
    rig = AsyncMock()
    rig.snapshot = AsyncMock(return_value=RigSnapshot(freq_hz=freq_hz))
    gps = AsyncMock()
    gps.snapshot = type("S", (), {"mode": 3, "lat": None, "lon": None, "ts": None,
                                  "lock_for_min": None, "satellites_used": None})()

    async def _none(tick):
        return []

    o = Orchestrator(config=cfg, rig=rig, gps=gps,
                     decode_source=decode_source or _none,
                     slot_clock=FakeSlotClock(count=0), db_enabled=False)
    o._last_rig = RigSnapshot(freq_hz=freq_hz)
    o._last_rig_at = time.monotonic()
    o._active_antenna = "ant"
    o._rx_audio_dbfs_peak = None          # sonst setzt das erst start()
    o._rx_audio_dbfs_peak_ts = 0.0
    return o


def _d(call_from, call_to, message, grid=None, snr=-5, band="20m") -> DecodedMsg:
    return DecodedMsg(ts=datetime.now(UTC), call_from=call_from, call_to=call_to,
                      grid=grid, message=message, snr_db=snr, dt_s=0.2,
                      freq_offset_hz=1500, band=band)


def _tick(index: int, posix: float) -> SlotTick:
    return SlotTick(index=index, posix=posix,
                    utc_start=datetime.fromtimestamp(posix, tz=UTC), slot_seconds=15.0)


def _tx_log(o: Orchestrator) -> list[str]:
    return [a.payload.get("kind") for a in o._action_log if a.kind == "TX_MESSAGE"]


# ---------------------------------------------------------------------------
# SWR-Notabschaltung: der Sperr-Waechter gibt sie nicht selbst wieder frei

def _waechter_objekt(swr_cut: bool):
    class _SM:
        def __init__(self):
            self.state = State.TX_LOCKED
            self.limits = GuardLimits(swr_max=2.0)
            self.entsperrt = 0
            self.ctx = type("C", (), {"last_lock_reason": "swr_guard: SWR 3.20 ueber Limit 2.00"})()

        def on_user_reset_lock(self):
            self.entsperrt += 1
            self.state = State.IDLE

    o = type("O", (), {})()
    o.state_machine = _SM()
    # Im Empfang liest das Rig wieder 1,0: fuer die Guards ist alles in Ordnung.
    o._hardware_state = HardwareState(swr=1.0, rig_link_age_s=1.0, dial_on_configured_freq=True)
    o._swr_cut_gesperrt = swr_cut
    o._lock_seit = None
    o._lock_heilungen = []
    o._lock_push_at = 0.0
    o._lock_log_at = 0.0
    o._LOCK_GEDULD_S = 0.0
    o._LOCK_HEILUNG_MAX_PRO_H = orch_mod.Orchestrator._LOCK_HEILUNG_MAX_PRO_H
    o._rig_am_usb = lambda: True
    o.gemeldet = 0

    async def _melde(*a, **k):
        o.gemeldet += 1
    o._melde_haengende_sperre = _melde
    return o


async def _waechter_laufen_lassen(o, monkeypatch, runden: int = 3) -> None:
    n = {"i": 0}

    async def _schlaf(_s):
        n["i"] += 1
        if n["i"] > runden:
            raise asyncio.CancelledError
    monkeypatch.setattr(orch_mod.asyncio, "sleep", _schlaf)
    with pytest.raises(asyncio.CancelledError):
        await orch_mod.Orchestrator._sperr_waechter_loop(o)


async def test_swr_notabschaltung_bleibt_gesperrt_bis_jemand_quittiert(monkeypatch):
    """Bis 2026-11-14 gab der Waechter nach zehn Minuten frei, bis zu dreimal je Stunde."""
    o = _waechter_objekt(swr_cut=True)
    await _waechter_laufen_lassen(o, monkeypatch)
    assert o.state_machine.entsperrt == 0
    assert o.state_machine.state is State.TX_LOCKED
    assert o.gemeldet >= 1                       # aber er sagt Bescheid


async def test_andere_sperren_heilt_der_waechter_weiter(monkeypatch):
    o = _waechter_objekt(swr_cut=False)
    await _waechter_laufen_lassen(o, monkeypatch)
    assert o.state_machine.entsperrt == 1


async def test_entsperren_nimmt_die_swr_sperre_zurueck():
    o = _orch(_cfg())
    o._swr_cut_gesperrt = True
    await o.handle_reset_lock()
    assert o._swr_cut_gesperrt is False


# ---------------------------------------------------------------------------
# ALC-Sperre laesst sich quittieren

async def test_alc_sperre_laesst_sich_quittieren():
    """Der ALC-Wert wird nur im Burst erneuert; die Sperre kam sofort wieder."""
    async def src(tick):
        return [_d("EA1ABC", None, "CQ EA1ABC IN73", grid="IN73")]

    o = _orch(_cfg(), src)
    o.state_machine.ctx.auto_answer = True
    o._last_alc_pct = o.state_machine.limits.alc_max + 12
    await o.process_slot(_tick(0, 1_700_000_000.0))
    assert o.state_machine.state is State.TX_LOCKED
    await o.handle_reset_lock()
    assert o.state_machine.state is State.IDLE
    await o.process_slot(_tick(1, 1_700_000_015.0))
    assert o.state_machine.state is not State.TX_LOCKED


# ---------------------------------------------------------------------------
# Leistung: neues Band und neuer Operator erreichen das Geraet vor dem Senden

async def test_leistungsgrenze_gilt_schon_fuer_den_ersten_burst_auf_dem_neuen_band():
    """70 W auf 20 m, Wechsel auf 60 m (15 W): der erste Burst lief mit 70 W."""
    async def src(tick):
        return [_d("EA1ABC", None, "CQ EA1ABC IN73", grid="IN73", band="60m")]

    o = _orch(_cfg(bands=("20m", "60m")), src, freq_hz=14_074_000)
    o._last_active_band = "20m"
    o._tx_power_w = 70
    o._tx_power_from_state = False
    assert o.config.effective_max_power_w("60m") == 15
    o._last_rig = RigSnapshot(freq_hz=5_357_000)       # der Poll hat die neue Frequenz
    o.state_machine.ctx.auto_answer = True
    beim_senden: list[tuple[int, int]] = []

    async def _tx(payload):
        beim_senden.append((o._tx_power_w, o.rig.set_rfpower.await_count))
    o._action_handlers["TX_MESSAGE"] = _tx
    await o.process_slot(_tick(0, 1_700_000_000.0))
    assert beim_senden, "es sollte gesendet werden"
    watt, befehle = beim_senden[0]
    assert watt <= 15 and befehle == 1


async def test_frequenzbefehl_setzt_die_leistungsgrenze_sofort():
    o = _orch(_cfg(bands=("20m", "60m")), freq_hz=14_074_000)
    o._last_active_band = "20m"
    o._tx_power_w = 70
    o._tx_power_from_state = False
    await o.handle_set_freq(5_357_000)
    assert o._tx_power_w <= 15
    assert o.rig.set_rfpower.await_count == 1


async def test_operatorwechsel_stellt_die_leistung_auch_am_geraet():
    """DK9XR faehrt 100 W, DO3XR hat 50 W Vorgabe: das Rig blieb auf 100 W."""
    cfg = AppConfig(
        operators=[
            OperatorConfig(callsign="DK9XR", default_locator="JN58td",
                           default_power_w=100, license_class="A"),
            OperatorConfig(callsign="DO3XR", default_locator="JN58td",
                           default_power_w=50, license_class="E"),
        ],
        active_callsign="DK9XR",
        rig=RigConfig(model="ic7300"),
        bands=[BandConfig(name="15m", freq_khz=21074, antenna="ant")],
        antennas=[AntennaConfig(name="ant", bands=["15m"])],
        operating=OperatingConfig(),
    )
    o = _orch(cfg, freq_hz=21_074_000)
    o._last_active_band = "15m"
    o._tx_power_w = 100

    async def _nix(*a, **k):
        return None
    o._preflight_warn = _nix
    o._init_integrations = lambda: None
    await o.switch_operator("DO3XR")
    await asyncio.sleep(0)
    assert o._tx_power_w == 50
    o.rig.set_rfpower.assert_awaited_once()
    assert o.rig.set_rfpower.await_args.args[0] == pytest.approx(0.5)


# ---------------------------------------------------------------------------
# Nach Stop wird nicht mehr gesendet

def test_nach_stop_kein_rr73_nachklang():
    """Der letzte Partner wiederholt seinen Report: bis zu dreimal ging noch ein RR73 hinaus."""
    sm = StateMachine(ctx=MachineContext(callsign="DK9XR", my_grid="JN58", auto_answer=True))
    hw = HardwareState()
    sm.state = State.QSO_REPORT
    sm.qso = QsoContext(their_call="W1AW", band="20m", their_snr=-10,
                        our_snr_received=-12, partner_hat_uns_gerufen=True)
    sm.on_decodes(hw, [_d("W1AW", "DK9XR", "DK9XR W1AW RR73")])
    sm.drain_actions()
    assert sm.ctx.recent_logged                       # der Nachklang ist vorgemerkt
    sm.on_user_stop()
    sm.drain_actions()
    sm.on_decodes(hw, [_d("W1AW", "DK9XR", "DK9XR W1AW R-12")])
    assert not [a for a in sm.drain_actions() if a.kind == "TX_MESSAGE"]


async def test_stop_haelt_eine_schon_abgeholte_aussendung_auf(monkeypatch):
    """Das CQ nach dem RR73 wartete auf die Slot-Grenze und ging trotz Stop hinaus."""
    async def src(tick):
        return [_d("W1AW", "DK9XR", "DK9XR W1AW RR73")]

    o = _orch(_cfg(), src)
    sm = o.state_machine
    sm.ctx.auto_cq = True
    sm.state = State.QSO_REPORT
    sm.qso = QsoContext(their_call="W1AW", their_grid="FN31", band="20m",
                        their_snr=-10, our_snr_received=-12,
                        partner_hat_uns_gerufen=True)
    gespielt = []

    class _Play:
        def play(self, pcm):
            gespielt.append(sm.state.name)
            if len(gespielt) == 1:               # Stop mitten im RR73-Burst
                o._stop_nr += 1
                sm.on_user_stop()
    o.playback = _Play()
    phasen = iter([0.6, 0.6, 13.3, 0.02, 0.02, 0.02])
    monkeypatch.setattr(o, "_slot_phase_s", lambda: next(phasen, 0.02))
    echt = asyncio.sleep

    async def _schlaf(s):
        await echt(0)
    monkeypatch.setattr(orch_mod.asyncio, "sleep", _schlaf)
    await o.process_slot(_tick(0, 1_700_000_015.0))
    assert not sm.ctx.auto_cq and sm.state is State.IDLE
    assert len(gespielt) == 1                                 # nur das RR73
    assert len([c for c in o.rig.set_ptt.await_args_list if c.args == (True,)]) == 1


async def test_aufgeschobene_aussendung_prueft_die_guards_neu(monkeypatch):
    """Zwischen Aufschub und Sendung dreht jemand den VFO auf 14,200 MHz."""
    o = _orch(_cfg())
    sm = o.state_machine
    sm.ctx.auto_answer = True
    sm.state = State.QSO_REPORT
    sm.qso = QsoContext(their_call="W1AW", band="20m", their_snr=-10,
                        our_snr_received=-12, partner_hat_uns_gerufen=True)

    class _Play:
        def play(self, pcm):
            return None
    o.playback = _Play()
    monkeypatch.setattr(o, "_slot_phase_s", lambda: 0.02)
    o._last_rig = RigSnapshot(freq_hz=14_200_000)
    payload = {"message": "W1AW DK9XR R-10", "kind": "r_report",
               "state": "QSO_REPORT", "freq_offset_hz": 1500}
    await o._sende_aufgeschoben(payload, o._tx_aufgeschoben_nr, time.time(), "W1AW")
    assert not [c for c in o.rig.set_ptt.await_args_list if c.args == (True,)]
    assert sm.state is State.TX_LOCKED


async def test_aufgeschobene_aussendung_geht_bei_gruenen_guards_hinaus(monkeypatch):
    o = _orch(_cfg())
    sm = o.state_machine
    sm.ctx.auto_answer = True
    sm.state = State.QSO_REPORT
    sm.qso = QsoContext(their_call="W1AW", band="20m", their_snr=-10,
                        our_snr_received=-12, partner_hat_uns_gerufen=True)

    class _Play:
        def play(self, pcm):
            return None
    o.playback = _Play()
    monkeypatch.setattr(o, "_slot_phase_s", lambda: 0.02)
    payload = {"message": "W1AW DK9XR R-10", "kind": "r_report",
               "state": "QSO_REPORT", "freq_offset_hz": 1500}
    await o._sende_aufgeschoben(payload, o._tx_aufgeschoben_nr, time.time(), "W1AW")
    assert len([c for c in o.rig.set_ptt.await_args_list if c.args == (True,)]) == 1


# ---------------------------------------------------------------------------
# Betriebsart und Dial gehoeren zusammen (Vorfall beim Ausrollen am 14.11.)

def _cfg_15m() -> AppConfig:
    return AppConfig(
        operator=OperatorConfig(callsign="DO3XR", default_locator="JN58ch",
                                default_power_w=10, license_class="E"),
        rig=RigConfig(model="ic7300"),
        bands=[BandConfig(name="15m", freq_khz=21074, freq_khz_ft4=21140, antenna="ant")],
        antennas=[AntennaConfig(name="ant", bands=["15m"], auto_band_switch=True)],
        operating=OperatingConfig(autopilot_enabled=True, autopilot_allowed_bands=["15m"]),
    )


async def test_autopilot_setzt_den_dial_auch_bei_veraltetem_schnappschuss():
    """Neustart im FT4-Betrieb: Dial auf FT8 gestellt, eine Sekunde spaeter zurueck
    auf FT4. Der Schnappschuss zeigte noch den FT4-Dial, das Rig blieb auf FT8 stehen."""
    o = _orch(_cfg_15m(), freq_hz=21_140_000)       # der Schnappschuss ist alt
    o._active_antenna = "ant"
    await o._apply_autopilot_target("15m", "FT4", "probe")
    o.rig.set_freq.assert_awaited_once_with(21_140_000)
    assert o.config.operating.mode == "FT4"


async def test_dial_guard_verlangt_den_dial_der_betriebsart():
    o = _orch(_cfg_15m(), freq_hz=21_074_000)
    o.config.operating.mode = "FT8"
    assert o._dial_on_configured_freq() is True
    o.config.operating.mode = "FT4"                # FT4 auf dem FT8-Dial
    assert o._dial_on_configured_freq() is False
    o._last_rig = RigSnapshot(freq_hz=21_140_000)
    assert o._dial_on_configured_freq() is True


async def test_nach_eigenem_frequenzbefehl_gilt_der_befohlene_wert():
    """Ein Poll von vor dem Befehl bringt die alte Frequenz zurueck."""
    o = _orch(_cfg_15m(), freq_hz=21_074_000)
    o.config.operating.mode = "FT4"
    await o.handle_set_freq(21_140_000)
    assert o._last_rig.freq_hz == 21_140_000       # sofort mitgezogen
    o._last_rig = RigSnapshot(freq_hz=21_074_000)  # der verspaetete Poll
    assert o._dial_on_configured_freq() is True
    o._dial_set_at -= 10.0                          # spaeter zaehlt wieder das Rig
    assert o._dial_on_configured_freq() is False
